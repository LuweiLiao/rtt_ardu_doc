# RTT ArduPilot 构建系统陷阱

## 问题：修改代码后编译，但二进制没有变化

### 根因

`rt_board_init.c` 在项目中有三份副本，**只有主仓库通用模板被编译**：

| 文件 | 用途 | 是否编译 |
|------|------|---------|
| `libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c` | **通用模板，实际被编译** | ✅ |
| `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/rt_board_init.c` | 子模块专属板级配置 | ❌ |
| `build/rtt_deploy/cuav_v5/board/rt_board_init.c` | 构建缓存（从主仓库复制而来） | ⚠️ 仅当源码变更时才重编 |

### 涉及的 SConscript

```
libraries/AP_HAL_RTT/hwdef/common/board/SConscript
→ src += Glob('rt_board_init.c')
→ 编译主仓库的通用模板
```

### 构建缓存污染

1. `git checkout -- .` 恢复子模块后，`build/rtt_deploy/` 中仍残留旧版文件
2. SCons 只检查时间戳：如果 `.o` 文件新于 `.c`，跳过重编译
3. 即使使用 `rm -f *.o` + `scons`，构建系统检测到 `.o` 存在且新于源文件，仍跳过编译

### 完整调用链（2026-05-09 分析）

```
Reset_Handler (0x080EE920)
  → SystemInit()
  → entry() (0x080FA71A)
    → rtthread_startup()
      → push {r3, lr}         ← 保存 LR = entry+6 = 0x080FA720
      → rt_hw_interrupt_disable()
      → rt_hw_board_init()
        → push {r3, lr}       ← 保存 LR = rtthread_startup+10 = 0x080FA6F6
          ↓↓↓↓↓ 栈布局 ↓↓↓↓↓
          [initial_SP - 8]: R3 (随便)
          [initial_SP - 4]: LR (= 0x080FA6F6) ← 关键保护区域
        → _mpu_config()
        → _fpu_context_init()
        → SCB_EnableICache()
        → SystemClock_Config()
        → rt_hw_systick_init()
        → rt_hw_pin_init()
        → rt_hw_usart_init()
        → rt_system_heap_init()
        → rt_console_set_device()
        → _spi_lld_board_init()
        → rt_components_board_init()  ← 可通过 INIT_BOARD_EXPORT 注释掉
        → ...
        → ldmia sp!, {r3, lr} ← LR=0xFFFFFFFF 已被破坏！
        → b.w rt_kprintf      ← 尾部调用正常执行
        → rt_kprintf 返回 → PC=0xFFFFFFFF → IACCVIOL!
```

### HardFault 案例分析：IACCVIOL 且 LR=0xFFFFFFFF

#### 症状
- CFSR=0x00010000 (IACCVIOL), HFSR=0x40000000 (FORCED)
- 异常帧 LR=0xFFFFFFFF
- 故障 PC 在 `rt_hw_board_init` 函数的文字池（literal pool）中
- 使用 `vhadd.s8` 等 FPU 指令反汇编 → 数据被当做代码执行

#### 诊断路径

1. **确认编译的文件**：
   ```bash
   arm-none-eabi-gdb -batch \
     -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
     -ex "disassemble rt_hw_board_init" 2>&1 | grep "rt_components"
   ```
   如果有 `bl rt_components_board_init` → 你的修改未生效

2. **确认栈大小**（系统栈 vs 实际使用）：
   ```bash
   # 查初始 MSP 和实际 SP 差值
   # 从向量表读初始 MSP
   xxd -l 4 build/rtt_cuav_v5/rtthread.bin  # 小端
   # OpenOCD halt 后查当前 SP
   echo "halt" | nc -q2 localhost 4444
   echo "reg sp" | nc -q1 localhost 4444
   ```
   初始 MSP - 当前 SP = 栈使用量。如果接近栈大小，考虑栈溢出。

3. **隔离 init 函数**：逐步注释掉 `rt_hw_board_init` 中的函数调用，定位哪一步破坏了 LR

4. **逐步注释顺序**：
   ```
   1. 注释 rt_components_board_init() → 还在崩溃 → 问题不在 SPI/I2C init
   2. 注释 _spi_lld_board_init() → 还在崩溃 → 问题不在 SPI LLD
   3. 注释 rt_console_set_device() → 还在崩溃 → 问题在更前面
   4. ...
   ```

#### 注意
- SP 指向的位置可能是 **旧栈底**（如从 8KB 增加到 16KB 后，SP=0x20003428 是旧 8KB 栈底）
- 即使栈大小足够，只要 LR 保存位置被 `rt_malloc`、DMA 或某 init 函数直接覆盖，同样会崩溃
- BSS 和 Stack 在 DTCM 中相邻（`__bss_start == _estack`），堆在 SRAM1（`HEAP_BEGIN = _end`），堆栈理论不重叠

## Python import re 作用域陷阱（2026-05-09 发现）

`rtt_hwdef.py` 的 `write_rtconfig_h()` 函数在 `if existing:` 分支内使用 `import re`，但函数体在到达该分支前使用了 `re.search()`。Python 的作用域规则说：如果函数内任何地方有 `import re`（即使在不可达分支），`re` 在整个函数中被视为局部变量。结果：在 `import` 执行之前使用 `re` → `UnboundLocalError`。

### 修复
```python
# ❌ 错误
def write_rtconfig_h(self, outdir):
    # ... 使用 re.search() → UnboundLocalError
    if existing:
        import re  # 这使 re 成为函数级局部变量
        existing = re.sub(...)

# ✅ 正确
def write_rtconfig_h(self, outdir):
    import re  # 移到函数顶部
    # ... 使用 re.search() → OK
    if existing:
        existing = re.sub(...)
```

## `RT_MAIN_THREAD_STACK_SIZE` 陷阱（2026-05-17 发现）

### 问题

改了 `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/.config` 中的 `CONFIG_RT_MAIN_THREAD_STACK_SIZE`，但编译后无效。

### 根因

RTT 的 `.config` 来源不是 BSP 子模块，而是**通用模板**：

| 文件 | 用途 | 是否生效 |
|------|------|---------|
| `libraries/AP_HAL_RTT/hwdef/common/.config` | ✅ **RTT 部署时复制到 build 目录的原始配置** | ✅ 编译器从这里读 |
| `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/.config` | ❌ BSP 子模块配置，仅供参考 | ❌ 不会被编译 |
| `build/rtt_deploy/cuav_v5/.config` | ⚠️ 部署副本，每次 `scons` 时从 common 模板覆盖 | ⚠️ 会被覆盖 |

### 数据流

```
libraries/AP_HAL_RTT/hwdef/common/.config  ← 改这里！
  ↓ rtt_bsp_deploy.py::_deploy_hwdef()
build/rtt_deploy/cuav_v5/.config
  ↓ rtt_bsp_deploy.py::_generate_rtconfig() → _simple_config_to_header()
build/rtt_deploy/cuav_v5/rtconfig.h
  ↓ 编译时被 BSP 和 kernel 源码 #include
rt-thread.elf  /  rtthread.bin
```

### 验证修改是否生效的方法

```bash
# 1. 检查二进制是否包含预期值
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \
  --start-address=0x8105734 --stop-address=0x810574a | grep "mov.*#"
# 输出: mov.w r3, #4096 ; 0x1000  ← 确认编译进去了

# 2. 检查 rtconfig.h 内容
grep "MAIN_THREAD_STACK" build/rtt_deploy/cuav_v5/rtconfig.h

# 3. 验证 app_descriptor CRC（确保烧录的是最新固件）
arm-none-eabi-gdb ... -ex "p app_descriptor"  # 对比 scons 输出的 CRC
```

### 教训

**改 build 系统参数时，先追数据流，别直接改第一个看到的同名文件。**
`grep -rn "keyword"` 搜到多个同名文件时，必须追踪哪个被编译链路使用。

## 正确修改文件的方法

```bash
# 正确：修改主仓库的通用模板（以下两类以此类推）
vim libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c        # 板级 C 代码
vim libraries/AP_HAL_RTT/hwdef/common/.config                       # Kconfig 配置 （陷阱高发）
vim libraries/AP_HAL_RTT/hwdef/scripts/rtt_hwdef.py                 # hwdef 生成器

# 强制清理构建缓存，重新编译
rm -rf build/rtt_deploy/ build/rtt_cuav_v5/
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)

# 验证修改生效
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "disassemble rt_hw_board_init" 2>&1 | grep -c "rt_components"
# 输出 0 = 修改生效
```
