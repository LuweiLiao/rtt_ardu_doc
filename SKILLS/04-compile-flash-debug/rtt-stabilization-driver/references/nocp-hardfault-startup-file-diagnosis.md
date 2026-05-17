# NOCP HardFault — 启动汇编文件诊断（2026-05-13 发现）

## 诊断流程

遇到 Reset 后立即 NOCP HardFault（CFSR 中 UFSR.NOCP=1）时，按以下流程排查。

### 第一步：确认故障类型

```bash
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0xE000ED28 2" | nc -q 1 localhost 4444  # CFSR + HFSR
echo "mdw 0xE000ED88 1" | nc -q 1 localhost 4444  # CPACR
```

- **NOCP** (UFSR bit 3 in CFSR byte 2): 协处理器指令在不启用时执行
- **PRECISERR** (BFSR bit 1 in CFSR byte 1): 精确数据总线错误（通常二次故障）
- **CPACR = 0x00000000** → FPU 被禁用  
- **CPACR = 0x00F00000** → FPU 已使能，问题不在 CPACR

### 第二步：确认 CPACR 是否可通过 OpenOCD 写入

```bash
echo "mww 0xE000ED88 0x00F00000" | nc -q 1 localhost 4444
echo "mdw 0xE000ED88 1" | nc -q 1 localhost 4444
```

若 OpenOCD 能写入（读回 0x00F00000）但代码写入不生效 → 检查启动汇编文件是否被正确编译。

### 第三步：追踪启动文件来源

```bash
# 1. 检查被编译的 object 文件
arm-none-eabi-objdump -d build/rtt_deploy/<target>/board/startup_rtt_override.o | grep -c "vmsr\|CPACR\|mcr.*p15.*cr7"
# 输出 > 0 → 含 FPU/CPACR 代码
# 输出 = 0 → 不含！（或含在另一个 object 中）

# 2. 无 CPACR 代码时的候选源文件
ls -la libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S
ls -la modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/startup_rtt_override.S

# 3. 手动汇编验证
arm-none-eabi-gcc -x assembler-with-cpp -c \
  libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S \
  -o /tmp/test.S.o -mcpu=cortex-m7 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard
arm-none-eabi-objdump -d /tmp/test.S.o | grep -c "vmsr"
# 如果手动汇编有但构建产物无 → 编译系统用了不同文件

# 4. 确认哪份文件被构建系统使用
diff libraries/AP_HAL_RTT/.../startup_rtt_override.S \
     build/rtt_deploy/<target>/board/startup_rtt_override.S
# 无差异 → 确认使用了 AP_HAL_RTT 版
```

### 第四步：检查正确的 VTOR 值

```bash
# 检查 link.lds 生成的 vflash_start
arm-none-eabi-nm build/rtt_deploy/<target>/rt-thread.elf | grep vflash_start
# 应等于 ORIGIN(ROM) 的值
```

## RT-Thread ArduPilot 构建系统的文件来源总结

| 文件类型 | 被构建系统使用（AP_HAL_RTT） | 不被使用（submodule） |
|---------|---------------------------|---------------------|
| 启动汇编 | `libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S` | `modules/rt-thread/bsp/.../board/startup_rtt_override.S` |
| 链接脚本模板 | `libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds` | `modules/rt-thread/bsp/.../board/linker_scripts/link.lds` |
| .config | `libraries/AP_HAL_RTT/hwdef/common/.config` | `modules/rt-thread/bsp/.../.config` |
| hwdef.dat | `libraries/AP_HAL_RTT/hwdef/cuav_v5/hwdef.dat` | （无对应文件） |

## 2026-05-13 session 完整复盘

**现象**：Clean rebuild + bypass bootloader (ROM @ 0x08000000) 后 HardFault。
**CFSR**: 0x00088200 (NOCP + PRECISERR)
**CPACR**: 0x00000000（启动代码写入未生效）
**根因**: 
1. submodule 版 `startup_rtt_override.S` 已被添加 D-Cache/CPACR 代码（126行）
2. 但构建系统实际编译的是 AP_HAL_RTT 版（旧版，71行，无 CPACR 代码）
3. VTOR 用 `vflash_start` 符号正确（AP_HAL_RTT 版已使用），但缺少 CPACR/FPU init
**修复**: 将 D-Cache disable + CPACR write + FPU init + I-Cache invalidate 添加到 AP_HAL_RTT 版的 startup 文件。
