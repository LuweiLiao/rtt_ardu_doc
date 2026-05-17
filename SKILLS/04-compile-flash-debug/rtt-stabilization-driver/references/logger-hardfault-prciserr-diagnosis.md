# Logger HardFault (PRECISERR) 诊断指南 — 栈溢出模式

## 背景

AP_Logger_Backend::Write() 在格式化日志条目时触发 PRECISERR。
这是一个**与 SDIO 无关的预先存在的 bug**，在 SDIO 启用和禁用的固件中都存在。

## 现象特征

```
CFSR:     0x00008200 (PRECISERR + BFARVALID)
HFSR:     0x40000000 (FORCED)
故障 PC:  strnlen 内 (ldrb r4, [r3])
BFAR:     0x027bf8c3 (或 0xfd0c621c 等无效地址 — 每次运行可能不同)
调用链:   AP_Logger_Backend::Write → strnlen(损坏指针)
```

## 诊断步骤

### Step 1: 确认 HardFault 类型

```bash
cd /data/firmare/pogo-apm
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p/x *(uint32_t*)0xE000ED28" \
  -ex "p/x *(uint32_t*)0xE000ED2C" \
  -ex "p/x *(uint32_t*)0xE000ED38" \
  build/rtt_deploy/cuav_v5/rt-thread.elf
```

- CFSR=0x00008200 → bit15=PRECISERR, bit9=BFARVALID
- HFSR=0x40000000 → bit30=FORCED (可配置异常升级)
- BFAR=0x027bf8c3 → **故障访问地址**（无效内存范围）

### Step 2: 读取异常帧定位故障来源

在 RT-Thread 中，线程模式下的 HardFault 使用 PSP 栈指针：

```bash
# 先读 PSP 值
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "info registers psp msp" \
  build/rtt_deploy/cuav_v5/rt-thread.elf

# 然后读取 PSP 处的异常帧 (8 words)
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "x/8xw 0x<PSP值>" \
  build/rtt_deploy/cuav_v5/rt-thread.elf
```

异常帧布局：
```
SP+0  = R0       (函数第1参数 / va_arg读取值)
SP+4  = R1       (函数第2参数)
SP+8  = R2       (函数第3参数)
SP+12 = R3       (函数第4参数)
SP+16 = R12
SP+20 = LR       (返回地址)
SP+24 = PC_fault (故障PC — 出错的指令地址)
SP+28 = xPSR
```

### Step 3: 定位故障源代码行

```bash
cd /data/firmare/pogo-apm
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "info line *0x<PC_fault>" \
  -ex "disassemble 0x<PC_fault-16>, 0x<PC_fault+16>"

# 同样检查 LR 处的调用者
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "info line *0x<LR-1>"           # LR 含 Thumb bit, 减1得到实际地址
```

## 栈溢出识别模式

### ✅ 黄金信号：异常帧中所有寄存器 = 相同垃圾值

这是 **main thread 栈溢出** 的典型特征：

```
异常帧 (PSP=0x2004a3b0):
  R0  = 0x027bf8c3  ← 所有寄存器都是同一个垃圾值
  R1  = 0x027bf8d3  ← 轻微偏移（连续内存读取的边界）
  R2  = 0x027bf8c3  ← 完全相同
  R3  = 0x027bf8c3  ← 完全相同
  R12 = 0xffffffff  ← 唯一不同的值
  LR  = 0x08079e89  ← AP_Logger_Backend::Write() 返回地址 ✅
  PC  = 0x080ee440  ← strnlen+12 (ldrb r4, [r3]) ✅
  xPSR= 0x810f0200  ← Thread mode, T bit=1 ✅
```

**为什么所有寄存器相同？** 因为 `va_arg(arg_list, char*)` 是从 `va_list` 中读取
栈上的变参列表。当主线程栈被深层调用链**越界覆盖**后，保存变参的栈区域被
后续函数调用的栈帧重写。`va_arg` 从被污染的栈位置读取 → 返回垃圾指针 →
后续所有寄存器操作都基于这个垃圾值。

### ⚠️ 与设备总线栈溢出的区别

| 症状 | 设备总线栈溢出 (旧) | 主线程栈溢出 (新) |
|------|--------------------|-------------------|
| 故障 PC | `__udivmoddi4` 等 libgcc | `strnlen` 或 `memcpy` |
| LR | 无效地址 (如 `0x23`) | 有效函数调用地址 |
| 寄存器 | 部分有效，部分垃圾 | **全部**相同垃圾值 |
| 触发场景 | SPI 传输中 64bit 除法 | Logger 日志格式化 |
| 线程 | bus 线程 (6144B → 8192B) | **main 线程** (16KB) |

## 根因分析

### 数据流

```
AP_Logger::Write_xxx(...)                    ← 调用者 (主循环)
  └─ AP_Logger_Backend::Write(msg_type, arg_list, ...)
       ├─ buffer[msg_len] (VLA! 栈上分配 ~128字节)
       ├─ for each fmt char:
       │    switch(fmt[i]):
       │      case 'N': charlen=16; break;
       │      case 'Z': charlen=64; break;
       │      ...
       ├─ if (charlen != 0):
       │    char *tmp = va_arg(arg_list, char*);    ← 从栈上变参列表读取
       │    uint8_t len = strnlen(tmp, charlen);     ← tmp=垃圾值 → 崩溃!
       └─ WritePrioritisedBlock(buffer, msg_len, ...)
```

### 主线程栈为什么溢出

| 因素 | 说明 |
|------|------|
| 主线程栈容量 | `CONFIG_RT_MAIN_THREAD_STACK_SIZE=16384` (16KB) |
| VLA buffer | `buffer[msg_len]` — 在栈上分配，随消息类型不同可达 200+ 字节 |
| 深层调用链 | 主循环 340Hz → Scheduler → 各模块 → Logger → Write → strnlen → (ISR) |
| 变参列表 | `va_list` 在栈上，被深层调用和 ISR 嵌套覆盖 |
| MSP 与 PSP | 不重叠（MSP 在 DTCM，PSP 在 SRAM1），但 ISR 嵌套可能加重栈压力 |

### 修复方案

**增大主线程栈**：`.config` 中 `CONFIG_RT_MAIN_THREAD_STACK_SIZE` 从 16384 改为 32768 (32KB)

当前 RAM 占用 ~55%，增大 16KB 后约 ~58%（512KB 总 RAM），完全可接受。

**验证方法**：
1. 修改 `.config` → 重新编译 → 烧录
2. 确认系统持续运行 60 秒无 HardFault
3. 确认 MAVLink 心跳、SYS_STATUS、RAW_IMU 等所有消息正常

## 预存在的 Logger HardFault — 关键事实

- **与 SDIO 无关**：在 SDIO 启用和禁用的固件中都存在
- **与 Phase 0/1.x 架构清理无关**：在架构清理前后都存在
- **与 UART 线程优先级无关**：即使 UART 优先级修复后仍存在
- **根因最可能**：main thread 16KB 栈被 Logger 深层调用链溢出
- **每次运行 BFAR 不同**：0xfd0c621c → 0x027bf8c3 — 随机垃圾值，取决于栈布局

## 相关文件

| 文件 | 用途 |
|------|------|
| `libraries/AP_Logger/AP_Logger_Backend.cpp:197-328` | Write() 函数 — 变参格式化逻辑 |
| `libraries/AP_Logger/AP_Logger_Backend.cpp:43-44` | HAL_LOGGING_STACK_SIZE=1580 (IO线程) |
| `libraries/AP_Logger/AP_Logger.cpp:1486` | IO 线程创建 |
| `libraries/AP_HAL_RTT/hwdef/common/.config` | CONFIG_RT_MAIN_THREAD_STACK_SIZE |
