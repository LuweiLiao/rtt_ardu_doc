# 堆/栈资源预算陷阱 — 64KB 主线程栈耗尽堆导致 serial RX FIFO 分配失败

> 发现日期：2026-05-17
> 涉及板型：CUAV V5 (STM32F767)

## 症状

- USB CDC 枚举成功（`/dev/ttyACM1`）
- 但 0 字节数据
- GDB 检查：PC 停在 `rt_assert_handler`（`kservice.c:1284`）
- 调用栈：`rt_serial_open` → `dev_serial.c:676` → `RT_ASSERT(rx_fifo != RT_NULL)`
- 根因：`rt_malloc()` 返回 NULL → 堆耗尽

## 根因链

```
SRAM1 = 384KB (@0x20020000)
  - .data + .bss = ~306KB
  - 剩余堆 ≈ 84KB
  - RT_MAIN_THREAD_STACK_SIZE = 65536 (64KB !!!)
  - 堆剩余 ≈ 84KB - 64KB = 20KB
  - serial RX FIFO 需要一点堆 → malloc → NULL → ASSERT
```

## ChibiOS 对照

| 项目 | ChibiOS | RTT (修复前) | RTT (修复后) |
|------|---------|-------------|-------------|
| 主线程栈大小 | **1KB** (0x400) | **64KB** (65536) | **4KB** (4096) |
| 分配方式 | **静态** (linker script `.pstack`) | **堆分配** (rt_thread_create) | 堆分配 |
| 参考行号 | `chibios_board.mk:82` (`USE_PROCESS_STACKSIZE=0x400`) | `modules/.../stm32f765-cuav-v5/.config` | 同左 |
| 剩余堆 | DTCM(128KB) + 全SRAM | ~20KB | ~80KB |
| serial FIFO 分配 | ✅ | ❌ assert | ✅ |

ChibiOS 主线程栈来源于 `rules_stacks.ld` 中的 `.pstack` 段（静态分配），不占用堆。RTT 的 `rt_thread_create("main", ...)` 从堆分配主线程栈。

## Build 系统陷阱

**⚠️ 关键发现**：CUAV V5 使用 hwdef 构建模式，`.config` 的源头不是 BSP 目录！

`.config` 来源链（hwdef 模式）：

```
libraries/AP_HAL_RTT/hwdef/common/.config  ← 真正源头！
  → 部署时 shutil.copytree() 复制到 deploy dir
    → build/rtt_deploy/cuav_v5/.config
      → _simple_config_to_header()
        → build/rtt_deploy/cuav_v5/rtconfig.h
```

**陷阱**：修改 `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/.config` **无效**——BSP 目录的 `.config` 在 hwdef 模式下不参与构建。正确做法是改 `libraries/AP_HAL_RTT/hwdef/common/.config`。

另，修改 deploy dir 的 `.config` 但不重建 deploy 目录 → 增量编译使用旧的 deploy dir `.config` → 修改不生效！

**正确的修改方式**：
1. ⭐ 最佳：改源头 `libraries/AP_HAL_RTT/hwdef/common/.config`（永久生效）
2. 同时修改 deploy dir 的 `.config` + rtconfig.h
3. 或者改源头后删除 deploy 目录，强制全新部署

## 构建系统文件链

```
.config (deploy dir)
  → _simple_config_to_header()  [rtt_bsp_deploy.py:314-339]
    → rtconfig.h (deploy dir)
      → write_rtconfig_h() [rtt_hwdef.py:1555] — 追加外设使能
        → rtconfig.h (deploy dir, 有 Auto-enabled 块)
          → SConscript 复制到 build/rtt_cuav_v5/rtconfig.h
```

## Scheduler.cpp 线程栈预算陷阱 (2026-05-17 新发现)

> **这是 2026-05-17 P0 诊断的第二层根因**。主线程栈修复后堆仍有剩，但串口 #2 的 `rt_malloc(76)` 仍失败。debug 后发现剩余线程栈（ap_timer=16384、ap_io=8192、storage=8192）继续耗尽堆。

### RTT 线程栈分配架构差异

**所有 RTT 线程栈都从堆分配**（`rt_thread_create` → `RT_KERNEL_MALLOC`），与 ChibiOS 的**静态分配**（linker script BSS 段）完全相反。这是移植中最隐蔽的内存资源陷阱。

### Scheduler.cpp 线程栈清单与建议值

| 线程名 | 原始值 | 修复值 | ChibiOS 等效 | 说明 |
|--------|--------|--------|-------------|------|
| `ap_timer` | **16384** (16KB) | **4096** (4KB) | <1KB (static) | 关键：ChibiOS `_timer_thread` 静态栈 ≤ 1KB，RTT 16KB 纯属过度预留 |
| `ap_uart` | **8192** (8KB) | **8192** (保持) | 320+768 (static TX+RX) | UART 线程处理所有串口 timer_tick，8KB 合理暂不缩减 |
| `ap_io` | **8192** (8KB) | **4096** (4KB) | ~2KB (static) | IO 回调中不执行高栈递归操作 |
| `storage` | **8192** (8KB) | **4096** (4KB) | ~2KB (static) | 参数存储 I2C/Flash 操作栈浅 |
| `ap_rcout` | **2048** (2KB) | **2048** (保持) | ~1KB (static) | PWM 写入无递归 |
| `ap_rcin` | **2048** (2KB) | **2048** (保持) | ~1KB (static) | RC 协议解析栈浅 |
| `ap_mon` | **2048** (2KB) | **2048** (保持) | ~1KB (static) | 监控线程 |

**修改文件**：`libraries/AP_HAL_RTT/Scheduler.cpp`（三个修改点，lines ~367-397）

```diff
- ap_timer: 16384 → 4096
- ap_io:    8192 → 4096  
- storage:  8192 → 4096
```

### 总节省

```
节省 = (16384-4096) + (8192-4096) + (8192-4096) = 12288 + 4096 + 4096 = 20,480 bytes (20KB)
```

### 诊断方法（本会话验证）

使用 GDB 跟踪每个 serial open 前后的堆状态：

```bash
# 1. 在 serial rx_fifo malloc 返回处设断
break *rt_serial_open+126  # cbnz 检查处
commands
  printf "used=%d/%d r0=0x%x\n", *(int*)((char*)system_heap+36), *(int*)((char*)system_heap+32), $r0
  continue
end
continue

# 2. 当 `used > total` 且 `r0=0` → 堆耗尽
# 观察 used 跳变时间点，回溯到 boot 期间的线程创建
```

关键识别：如果 `used > total`，但差值很小（16-64 bytes），且断点命中前所有 `r0` 非零 → 问题不在 serial FIFO，而在**之前的线程栈分配**。

### 堆预算速查表

| 阶段 | C 文件 | 消费量 | 累计 |
|------|--------|--------|------|
| RTT 内核(timer/idle/shell) | `rtconfig.h` | ~7KB (2K+0.25K+4K) | 7KB |
| main 线程栈 | `scheduler/init` | 4KB | 11KB |
| ap_timer 线程 | Scheduler.cpp:367 | 4KB (原16KB) | **15KB** |
| ap_rcout 线程 | Scheduler.cpp:373 | 2KB | 17KB |
| ap_rcin 线程 | Scheduler.cpp:379 | 2KB | 19KB |
| ap_uart 线程 | Scheduler.cpp:385 | 8KB | 27KB |
| ap_io 线程 | Scheduler.cpp:391 | 4KB (原8KB) | **31KB** |
| storage 线程 | Scheduler.cpp:397 | 4KB (原8KB) | 35KB |
| kernel objects(12×160) | rt_object_allocate | ~2KB | 37KB |
| serial 8× rx_fifo(76) | dev_serial.c:674 | ~0.6KB | ~38KB |

> 修复后总堆消费 ≈ **38KB**，86KB 堆剩余 **~48KB**（55%余量）。修复前总消费约 **58KB**，剩余仅 **28KB** → 串口 #2 的 76B 分配碰壁。

## 其他 RTT 板型的默认值对比

| 板型 | 芯片 | RT_MAIN_THREAD_STACK_SIZE |
|------|------|--------------------------|
| fmuv2 | STM32F427 | **2048** (2KB) |
| pixhawk6c_mini | STM32H743 | **2048** (2KB) |
| CUAV V5 (修复前) | STM32F767 | **65536** (64KB) — 异常大 |
| CUAV V5 (修复后) | STM32F767 | **4096** (4KB) — 够用 |

## 诊断命令

```bash
# 检查 .config 值
grep "MAIN_THREAD_STACK" build/rtt_deploy/cuav_v5/.config
# 检查 rtconfig.h (编译产物)
grep "MAIN_THREAD_STACK" build/rtt_deploy/cuav_v5/rtconfig.h
# 验证编译时生效
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rt_application_init
# 检查链接后的 RAM 布局
arm-none-eabi-nm -n build/rtt_deploy/cuav_v5/rt-thread.elf | grep -E "_end|_estack|_ebss|_edata|_sdata|_sidata|_etext|_stext" | head -10
# 完整的 rtconfig.h 生成路径
grep -n "rtconfig\\|\\.config\\|mk_rtconfig\\|_simple_config" Tools/scripts/rtt_bsp_deploy.py | head -10
```

## 堆耗尽 debug 速查

### GDB 堆状态检查

```bash
# halt 后检查堆统计
arm-none-eabi-gdb -batch -q -iex "set auto-load safe-path /" \\
  build/rtt_deploy/cuav_v5/rt-thread.elf \\
  -ex "target extended-remote :3333" \\
  -ex "monitor halt" \\
  -ex "p/x *system_heap" \\
  -ex "p/x system_heap.used" \\
  -ex "p/x system_heap.total" \\
  -ex "monitor resume"
```

### system_heap 结构解读

```c
struct rt_memory {
    struct rt_object parent;     // obj name, type, flags, list
    const char *algorithm;       // allocator name string
    rt_ubase_t address;          // heap data start address
    rt_size_t total;             // total heap data size (bytes)
    rt_size_t used;              // used heap data size (bytes)
    rt_size_t max;               // max used (peak)
};
```

### 状态判据

| `system_heap` 值 | 含义 | 下一步 |
|------------------|------|--------|
| `used ≈ 0` | 堆刚初始化，无分配 | 正常初态 |
| `used ~ 10-20KB` | 已有 main/timer/shell 线程分配 | 正常 |
| `used = total` (或 `used > total`) | **堆耗尽或堆统计溢出** | 查找大分配或内存踩踏 |
| `used > total` | 堆元数据损坏或统计跑飞 | 检查 buffer overflow / 双重释放 |

### 堆耗尽常见原因

| 原因 | 特征 | 修复 |
|------|------|------|
| `RT_MAIN_THREAD_STACK_SIZE` 过大 | `used ≈ total` 但不超过太多 | 改 `.config` 源头 |
| 多次 `rt_malloc` 不释放 | `used` 持续增长 | 检查泄漏 |
| Heap 元数据损坏 | `used > total` 或链表断裂 | 查 buffer overflow |
| 预编译库（librtthread.a）有旧值 | 二进制中 `mov.w r3, #0x10000` | 确认链接的是重新编译的 .o，非 .a |

### 验证二进制编译值的多种方法

```bash
# 方法1: 反汇编 rt_application_init — 最可靠
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \\
  --start-address=0x$(arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep "T rt_application_init" | awk '{print $1}') \\
  --stop-address=+0x20 | grep 'mov'
# 输出: mov.w r3, #4096 ; 0x1000  ✅
# 输出: mov.w r3, #65536 ; 0x10000 ❌

# 方法2: 检查 CRCs (确认 flash 与 ELF 一致)
# 从构建日志取 CRC
# 从 gdb 读 app_descriptor CRC
arm-none-eabi-gdb ... -ex "p app_descriptor"
# 比较 image_crc1, image_crc2 与构建输出

# 方法3: 直接验证 rt_serial 的 rt_malloc 调用
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \\
  --start-address=0x$(... dev_serial.c line 674-675) | head -5
```
