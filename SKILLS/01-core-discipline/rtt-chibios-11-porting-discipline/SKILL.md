---
name: "rtt-chibios-11-porting-discipline"
description: "RTT ArduPilot 移植的铁律 — 1:1 复刻规则（廖博士2026-05-14建立）。每行代码改动必须有 ChibiOS 对应参考（文件:行号），禁止无依据修改。"
---

# RTT → ChibiOS 1:1 复刻移植规则

> **设立者**：廖博士（2026-05-14）\n> **核心原则**：你改的每一行代码都要有原因，要和 ChibiOS 对得上。这样才能算移植。而不是无缘无故乱改代码。\n> **适用范围**：所有涉及 `libraries/AP_HAL_RTT/` 的修改、新增、重构\n> **例外机制**：当因 RTOS 架构差异无法字面 1:1 时，必须写 ADR（Architecture Decision Record）说明 ChibiOS 行为、RTT 差异、替代实现、风险、验证证据\n\n## 🆕 ADR 例外机制 — 1:1 规则的增强而非削弱\n\n**背景（2026-05-17 廖博士审阅）**：已有多个模块不是字面 1:1：USB(SDU vs CherryUSB)、ADC(DMA ring vs CMSIS reg)、SPI(LLD vs RTT device)、Scheduler(chThdSleep vs idlehook)。不加例外机制会导致两种坏结果：机械照抄导致错，或为修问题绕开规则但不留下架构解释。\n\n### 何时必须写 ADR\n\n当以下任一条件成立时，必须写 ADR：\n\n| 条件 | 例子 |\n|------|------|\n| RTT 使用完全不同架构的组件替代 ChibiOS 组件 | USB: SDU → CherryUSB |\n| RTT 缺少 ChibiOS 使用的硬件外设抽象层 | ADC: DMA ring → CMSIS 直寄存器 |\n| RTT 用不同模式实现相同功能 | SPI: LLD poll → RTT SPI device |\n| ChibiOS 的 RTOS 特性在 RTT 中无直接等价 | Scheduler: chThdSleep + WFI → DWT 忙等 + idlehook |\n\n### ADR 模板（必须包含的字段）\n\n```markdown\n# ADR-NNN: 标题\n\n- **Status**: Accepted / Proposed / Deprecated\n- **Date**: 2026-05-17\n- **ChibiOS Reference**: `libraries/AP_HAL_ChibiOS/<file>:<line>`\n- **RTT Implementation**: `libraries/AP_HAL_RTT/<file>:<line>`\n\n## Context\n为什么无法 1:1 复刻？RTOS 架构差异是什么？\n\n## Decision\nRTT 采用什么替代方案？为什么选这个方案？\n\n## Consequences\n- 性能影响：\n- 内存影响：\n- 验证方法：\n- 回退方案（如果 RTT 新增了等价 API）：\n\n## Verification\n如何证明替代方案功能等价？\n```\n\n### 已有 ADR 清单（详见 repo `ADR/` 目录）\n\n| # | 模块 | 偏离 | Status |\n|---|------|------|--------|\n| 001 | USB | ChibiOS SDU → CherryUSB CDC ACM | Accepted |\n| 002 | ADC | ChibiOS DMA 环形缓冲 → CMSIS 直接寄存器 | Accepted |\n| 003 | SPI | ChibiOS SPI LL Driver → RTT SPI Device 框架 | Accepted |\n| 004 | Scheduler | chThdSleep + WFI → rt_thread_mdelay + idlehook | Accepted |

## 🚨 铁律 ZERO：先读 ChibiOS，再动一行代码（2026-05-16 廖博士强制命令）

> **任何涉及 `AP_HAL_RTT` 或 `hwdef/` 的修改，必须在动代码前先读 ChibiOS 对应文件。读完之前一个字都不许写。**

### 执行流程（不可跳过）

```
┌──────────────────────────────────────────────────────────────┐
│  收到修改任务 → 立刻 STOP                                    │
│  ↓                                                          │
│  第 1 步：找到 ChibiOS 对应文件                              │
│    libraries/AP_HAL_ChibiOS/<对应文件> 或                     │
│    modules/ChibiOS/os/hal/... 或                             │
│    hwdef/fmuv5/hwdef.dat / hwdef/CUAVv5/hwdef.dat           │
│  ↓                                                          │
│  第 2 步：精读关键函数（不跳行，不 skim）                      │
│    构造函数 / init / probe / transfer / start / stop          │
│    错误处理 / 超时 / 恢复机制                                 │
│  ↓                                                          │
│  第 3 步：逐行对比 ChibiOS vs RTT 差异                        │
│    做好差异表（ChibiOS行号 | ChibiOS做法 | RTT做法 | 差距）   │
│  ↓                                                          │
│  第 4 步：确定修改方案（标注每个修改的 ChibiOS 参考行号）      │
│  ↓                                                          │
│  第 5 步：**向廖博士汇报分析 + 方案**，确认后再执行            │
│  ↓                                                          │
│  最后：执行修改 → 编译 → 烧录 → 验证                          │
└──────────────────────────────────────────────────────────────┘
```

### 违规惩罚

| 行为 | 后果 |
|------|------|
| 没读 ChibiOS 就改代码 | ❌ **立即停止**，git restore，读 ChibiOS 后重来 |
| 改了但与 ChibiOS 行为不一致 | ❌ **视为 bug**，回退重做 |
| 写 plan 但不标注 ChibiOS 参考行号 | ❌ **plan 无效**，补上行号 |
| 连续 3 次违反本规则 | ⚠️ 廖博士将亲自监督所有修改 |

---

## 一、AP_HAL_ChibiOS 完整目录结构（参考索引）

```
libraries/AP_HAL_ChibiOS/
├── AnalogIn.cpp/h          # 模拟量输入（ADC）
├── CANFDIface.cpp/h        # CAN FD 接口
├── CanIface.cpp/h          # CAN 接口
├── Device.cpp/h            # 设备抽象层（总线注册/回调框架）
├── DSP.cpp/h               # 数字信号处理
├── Flash.h                 # Flash 存储抽象
├── GPIO.cpp/h              # GPIO 控制
├── HAL_ChibiOS_Class.cpp/h # HAL 入口（main_loop_entry 核心）
├── I2CDevice.cpp/h         # I2C 设备驱动
├── LogStructure.h          # 日志结构
├── RCInput.cpp/h           # RC 输入
├── RCOutput.cpp/h          # RC 输出
├── RCOutput_bdshot.cpp     # 双向射击 RC
├── RCOutput_iofirmware.cpp # IOMCU RC 输出
├── RCOutput_serial.cpp     # 串行 RC 输出
├── Scheduler.cpp/h         # 调度器（delay/micros/任务调度）
├── Semaphores.cpp/h        # 信号量封装
├── shared_dma.cpp/h        # DMA 共享管理
├── SoftSigReader.cpp/h     # 软信号读取器
├── SoftSigReaderInt.cpp/h  # 中断版软信号读取器
├── SPIDevice.cpp/h         # SPI 设备驱动
├── stdio.cpp               # 标准 I/O 重定向
├── Storage.cpp/h           # 参数存储（FRAM/Flash）
├── system.cpp              # 系统功能（reboot/panic）
├── UARTDriver.cpp/h        # UART 驱动（含 USB CDC）
├── Util.cpp/h              # 工具函数
├── WSPIDevice.cpp/h        # WSPI 设备驱动
├── sdcard.cpp/h            # SD 卡支持
├── bxcan.hpp               # CAN 总线头文件
└── hwdef/
    ├── common/             # 共享 hwdef（启动文件、链接脚本）
    ├── fmuv5/              # CUAV V5 专用 hwdef
    ├── CUAVv5/             # CUAV V5 板级（include fmuv5 + 覆写）
    └── scripts/            # hwdef 处理脚本
```

### AP_HAL_RTT 当前实现对照

```
libraries/AP_HAL_RTT/
├── AnalogIn.cpp/h          ← ChibiOS: AnalogIn.cpp/h
├── DeviceBus.cpp/h          → 无直接对应（RTT 自定义总线条）
├── Flash.cpp/h             ← ChibiOS: Flash.h
├── GPIO.cpp/h              ← ChibiOS: GPIO.cpp/h
├── HAL_RTT_Class.cpp/h     ← ChibiOS: HAL_ChibiOS_Class.cpp/h
├── I2CDevice.cpp/h         ← ChibiOS: I2CDevice.cpp/h
├── RCInput.cpp/h           ← ChibiOS: RCInput.cpp/h
├── RCOutput.cpp/h          ← ChibiOS: RCOutput.cpp/h
├── Scheduler.cpp/h         ← ChibiOS: Scheduler.cpp/h
├── Semaphores.cpp/h        ← ChibiOS: Semaphores.cpp/h
├── SPIDevice.cpp/h         ← ChibiOS: SPIDevice.cpp/h
├── SPIDeviceManager.cpp/h   → 无直接对应（RTT 特有）
├── Storage.cpp/h           ← ChibiOS: Storage.cpp/h
├── UARTDriver.cpp/h        ← ChibiOS: UARTDriver.cpp/h
├── Util.cpp/h              ← ChibiOS: Util.cpp/h
├── system.cpp              ← ChibiOS: system.cpp
├── hwdef/
│   ├── common/             ← ChibiOS: hwdef/common/
│   ├── cuav_v5/            ← ChibiOS: hwdef/CUAVv5/ + hwdef/fmuv5/
│   ├── scripts/            ← ChibiOS: hwdef/scripts/
│   └── fmuv2/              (遗留)
├── include/                 (RTT 特有头文件)
├── scripts/                 (RTT 特有脚本)
├── ch.h                    (ChibiOS 兼容头 — 近似替代)
├── AP_HAL_RTT.h            ← ChibiOS: AP_HAL_ChibiOS.h
└── HAL_RTT_Namespace.h     ← ChibiOS: AP_HAL_ChibiOS_Namespace.h
```

---

## 二、文件级 1:1 映射表

### 核心源文件映射

| AP_HAL_RTT 文件 | ChibiOS 参考文件 | 关键参考行 | 备注 |
|----------------|-----------------|-----------|------|
| `HAL_RTT_Class.cpp` | `HAL_ChibiOS_Class.cpp` | `main_loop()` 入口 (≈L240-380) | 启动顺序、调度模型、setup 优先级 |
| `Scheduler.cpp` | `Scheduler.cpp` | `delay()` (≈L60-110), `micros64()` (≈L120-200) | delay 实现、时间基准 |
| `SPIDevice.cpp` | `SPIDevice.cpp` | `get_semaphore()` (L337-340), `register_periodic_callback()` | 总线信号量模型 |
| `DeviceBus.cpp` | `Device.cpp` | `get_bus()` (L55-70), `register_periodic_callback()` (L130-180) | 总线线程回调注册 |
| `UARTDriver.cpp` | `UARTDriver.cpp` | `_begin()` (L360-420), `_write()` (L500-550) | 串口/USB CDC 配置 |
| `AnalogIn.cpp` | `AnalogIn.cpp` | `_timer_tick()` (L350-400), ADC 配置 | ADCs 采集时序 |
| `GPIO.cpp` | `GPIO.cpp` | `pin_mode()`, `write()` (L50-100) | GPIO 控制 |
| `I2CDevice.cpp` | `I2CDevice.cpp` + `I2CDevice.h` | `get_semaphore()` (I2CBus L91-93), `transfer()` (L180-250) | I2C 收发 + **get_semaphore 必须返回总线级锁** `&bus.semaphore`（非私有 `_sem`） |
| `RCOutput.cpp/h` | `RCOutput.cpp/h` | `init()`, `write()`, `cork()` (L100-300) | PWM 输出。**关键差异**：RTT 使用直接 TIM 寄存器访问替代 ChibiOS 的 `pwmStart()`/`pwmStop()` API，因为 CUAV V5 BSP 无 board.h → drv_pwm.c 未被编译。TIM1/TIM4/TIM12 全部用 CMSIS 寄存器直接配置。 |
| `RCInput.cpp` | `RCInput.cpp`
| `Semaphores.cpp` | `Semaphores.cpp` | 所有函数 (L20-80) | 信号量封装 API |
| `Util.cpp` | `Util.cpp` | `get_system_clock()` (L170-200), `get_hw_cycle_count()` | 工具函数 |
| `Storage.cpp` | `Storage.cpp` | `init()`, `read_byte()`, `write_byte()` (L50-150) | 参数存储 |
| `Flash.cpp` | `Flash.h` | `erase_sector()`, `write_word()` (L40-100) | Flash 擦写 |
| `system.cpp` | `system.cpp` | `reboot()`, `panic()` (L30-60) | 系统控制 |

### hwdef 映射

| RTT hwdef | ChibiOS 参考 | 关键定义 | 说明 |
|-----------|-------------|---------|------|
| `hwdef/cuav_v5/hwdef.dat` | `hwdef/fmuv5/hwdef.dat` | SPI1 引脚, USART, I2C, IMU | 最关键的硬件定义参考 |
| `hwdef/cuav_v5/hwdef.dat` | `hwdef/CUAVv5/hwdef.dat` (overlays) | LED, IMU 覆写, 板级特定 | CUAV V5 特有的覆写 |
| `hwdef/common/board/startup_rtt_override.S` | `modules/ChibiOS/os/common/startup/ARMCMx/compilers/GCC/crt0_v7m.S` | 向量表, Reset_Handler, FPU 初始化 | 启动汇编 |
| `hwdef/common/board/linker_scripts/link.lds` | `modules/ChibiOS/os/common/startup/ARMCMx/compilers/GCC/ld/STM32F765xI.ld` | 内存布局, 段定义 | 链接脚本 |
| `hwdef/common/.config` | N/A (RTT 特有) | RT-Thread 内核配置 | RTT 配置参数 |

### 启动文件映射（关键！）

| RTT 组件 | ChibiOS 对应作用 | 行号参考 |
|---------|-----------------|---------|
| `startup_rtt_override.S` Reset_Handler | `crt0_v7m.S` `_crt0_entry` | crt0_v7m.S:L90-160 |
| CPACR 设置 (内存映射 STR) | `crt0_v7m.S` `_crt0_init_fpu` | crt0_v7m.S:L120-140 |
| BSS 清零循环 | `crt0_v7m.S` BSS fill | crt0_v7m.S:L160-190 |
| VTOR 设置 (使用 vflash_start) | `crt0_v7m.S` `_crt0_setup_vtor` | crt0_v7m.S:L75-85 |
| I-Cache 使能 (SCTLR bit 12) | `crt0_v7m.c` `__cpu_init()` | crt0_common_v7m.S:L30-50 |
| D-Cache 禁用 | N/A — RTT 为 DMA 兼容禁用 | (CUAV V5 特有) |
| `HAL_ChibiOS_Class.cpp` setup 前降优先级 | `HAL_ChibiOS_Class.cpp` `main_loop()` | L280-300 |

---

## 三、修改前 8 问检查清单

> 修改 **任何** `AP_HAL_RTT/` 下的文件前，逐条回答以下 8 个问题。
> 回答必须写在 kanban 任务 body 或 plan 中。

### ☐ 第 1 问：ChibiOS 对应文件路径是什么？

```
ChibiOS: libraries/AP_HAL_ChibiOS/<文件名>
```
如果 RTT 修改涉及的功能在 ChibiOS 中没有直接对应文件 → 作为**架构差异**记录，说明为什么 RTT 需要独立的实现。

### ☐ 第 2 问：ChibiOS 中对应的精确行号是什么？

```bash
# 查找目标功能的精确行号
grep -n "功能名\|函数名\|寄存器" libraries/AP_HAL_ChibiOS/<文件> | head -10
```
记录精确行号（如 `SPIDevice.cpp:337`），并简述该行的作用。

### ☐ 第 3 问：ChibiOS 的寄存器值/时序/优先级与 RTT 现有值有何差异？

| 项目 | ChibiOS 值 | RTT 当前值 | 差异分析 |
|------|-----------|-----------|---------|
| SPI 分频 | BR=5(低速), BR=3(高速) | BR=3(固定) | RTT 缺少动态分频切换 |
| 总线线程优先级 | 181 (ChibiOS 数值语义) | 默认 RT-Thread 优先级 | 需查 RT-Thread 优先级对齐 |
| 信号量超时 | BLOCK_FOREVER | 10ms | RTT 会丢失样本 |
| 栈大小 | 动态分配或编译期 | 4KB (最小) | 不够时需要增大 |

### ☐ 第 4 问：这个修改是否在 `AP_HAL_RTT/` 目录内？

```
修改路径: libraries/AP_HAL_RTT/<文件>
```
- **是** → ✅ 可以，但需回答完 8 问
- **不是** → ⛔ 立即停止！修改 `AP_HAL_RTT/` 外文件需要：
  1. 证明 ChibiOS 在相同位置也做了相同修改
  2. 或者这是一个真正的跨平台 bug（非 RTT 专属 hack）
  3. 例外：`hwdef/common/` 中的链接脚本、启动文件、.config 可以在 RTT 项目中修改

### ☐ 第 5 问：功能是否必须用与 ChibiOS 完全相同的方式实现？

| 条件 | 判断 |
|------|------|
| ChibiOS 用硬件外设（硬件 I2C, DMA SPI） | ✅ 必须尽可能用相同方式，除非 RT-Thread 不提供 |
| ChibiOS 用特定寄存器配置 | ✅ 必须相同（STM32 RM 唯一确定） |
| ChibiOS 用独立线程架构 | ⚠️ 尽可能接近，最少功能等价 |
| ChibiOS 用 ChibiOS 特有 API（chThd, chEvt） | ❌ 用 RT-Thread 等价 API 实现相同行为 |

**功能等价** 比 API 等价更重要。例如 ChibiOS 的 `chEvtSignal(thread, mask)` 用 RTT 的 `rt_event_send(&event_obj, mask)` 替代，行为等价即可。

### ☐ 第 6 问：这个修改会影响其他外设的 GPIO/时钟/中断配置吗？

检查清单：
- ☐ GPIO MODER 是否会被其他端口操作覆盖？（D-Cache RMW 陷阱）
- ☐ 时钟使能是否与其他外设共享总线？
- ☐ 中断优先级是否与现有线程优先级冲突？
- ☐ 改变了哪个端口的哪个引脚？

**STM32F7 特有陷阱**：GPIO MODER 的 read-modify-write 在 D-Cache 或写缓冲区使能时可能导致修改被吞没 → 使用 DSB 屏障。

### ☐ 第 7 问：不修改是否可以接受临时 workaround？

如果当前功能不 critical（如外部磁力计、SD 卡日志），考虑：
- 在 hwdef.dat 中注释掉（如 `#SPIDEV ...`）
- 在编译时定义 `HAL_xxx_DISABLED`
- 用 `if 0` 或 `#if 0` 跳过阻塞代码段

**禁止** 的 workaround：
- 跳过 IMU 初始化（导致 EKF 不工作）
- 注释掉信号量获取（导致 SPI 竞争）
- 修改 RT-Thread 内核代码（submodule 改动会丢失）

### ☐ 第 8 问：验证标准是什么？（具体、可测量）

```markdown
- 编译: scons --v=ArduCopter --target=cuav_v5 -j$(nproc) → exit 0
- OpenOCD: halt 后 CFSR=0, HFSR=0
- CDC: /dev/ttyACM1 枚举, pymavlink 收到 HEARTBEAT
- RAW_IMU: xacc/yacc/zacc 至少一个非零
- 功能验证: 对照 ChibiOS 行为，具体到消息类型+频率
```

---

## 四、修改工作流（强制顺序）

### Step 1: 确定要改什么

```bash
# 问题: <症状或需求>
# 可能涉及: <文件列表>
```

### Step 2: 查 ChibiOS 参考文件

```bash
# 方法 A: 按文件名查
vim libraries/AP_HAL_ChibiOS/<对应文件>

# 方法 B: 按功能查
grep -rn "关键函数名" libraries/AP_HAL_ChibiOS/ | head -10

# 方法 C: 按 hwdef 查（引脚配置）
grep -rn "引脚号\|外设名" libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat

# 方法 D: 按启动顺序查
grep -rn "启动阶段" modules/ChibiOS/os/common/startup/ARMCMx/compilers/GCC/crt0_v7m.S
```

### Step 3: 记录对应关系

```markdown
修改: libraries/AP_HAL_RTT/<文件>:<行号>
参考: libraries/AP_HAL_ChibiOS/<对应文件>:<行号>
ChibiOS 代码:
```cpp
// ChibiOS 的参考代码
```
RTT 修改:
```cpp
// RTT 中的对应修改
```
差异原因: <解释>
```

### Step 4: 逐行回答 8 问（写入 skill 或 task body）

```markdown
1. ChibiOS 文件: libraries/AP_HAL_ChibiOS/<文件>
2. 行号: <行号>
3. 差异: ...
...
8. 验证: ...
```

### Step 5: 修改 → 编译 → 烧录 → 验证

每次只改 **1 个文件** 或 **1 个逻辑功能块**，改完立即编译测试。

### Step 6: 汇报

用表格对比 ChibiOS vs RTT 行为：

| 指标 | ChibiOS | RTT (修改前) | RTT (修改后) |
|------|---------|-------------|-------------|
| CFSR | 0 | 0 | 0 |
| CDC 枚举 | 2-5s | N/A | 3-7s ✅ |
| HEARTBEAT | 1Hz | 0Hz | 1Hz ✅ |
| RAW_IMU | 18.7Hz | 0Hz | 2.7Hz ⚠️ (待优化) |

---

## 五、禁止清单

| # | 禁止行为 | 原因 | 正确做法 |
|---|---------|------|---------|
| 1 | ❌ 修改 `modules/rt-thread/` 中的内核代码 | submodule 改动易丢失、引入版本不兼容 | 通过 BSP 配置或 port 文件绕过 |
| 2 | ❌ 修改 `libraries/` 中非 `AP_HAL_RTT/` 的通用代码 | (除非 ChibiOS 也改了相同位置) 属于跨平台 bug proof | 先在 `AP_HAL_RTT/` 代理层实现 |
| 3 | ❌ 无 ChibiOS 参考行号的寄存器值猜测 | 寄存器值必须来自 RM 或 ChibiOS 验证值 | 查 STM32F765 RM + ChibiOS 代码确认 |
| 4 | ❌ 修改 `Scheduler.h` 或 `Scheduler.cpp` 的 include 链 | 历史教训：导致 IBUSERR HardFault | 保持 include 链不变，功能在本地实现 |
| 5 | ❌ 批量回滚已验证的修复（git restore 多个文件） | 破坏 L0 基线 | 逐个文件 git checkout 恢复 |
| 6 | ❌ 在一次 commit 中修改 3 个以上不相关文件 | 无法定位回归根因 | 每次 1 个逻辑功能块 |
| 7 | ❌ 声称"修复完成"但未通过 OpenOCD + CDC 双重验证 | 口头说得再好也没用 | 必须 CFSR=0 + HEARTBEAT |
| 8 | ❌ 跳过问题诊断直接修 | 用户反复纠正的铁律 | 先诊断根因再出方案 |
| **9** | **❌ CC 委托时 prompt 不限定文件范围** | CC 自主越界修改 10+ 文件，含 `.config` 致命改动的历史教训 | 委托 prompt 开头写明“只修改以下文件：...。禁止修改其他任何文件” |
| **10** | **❌ 修改 SWD 引脚定义或添加 PA13/PA14 的 GPIO 配置** | 破坏调试接口，导致无法烧录/调试（2026-05-16 实际发生） | PA13(SWDIO)/PA14(SWCLK) 必须保持默认 SWD 功能，hwdef.dat 中只能用标签标注（`GPIO_SWDIO`/`GPIO_SWCLK`），**绝不可加 OUTPUT/INPUT/AF 等 GPIO 初始化** |

## 六、允许清单

| # | 允许行为 | 条件 |
|---|---------|------|
| 1 | ✅ 在 `AP_HAL_RTT/` 中添加适配代码 | 有 ChibiOS 参考行号 |
| 2 | ✅ 实现 ChibiOS 功能的 RT-Thread 等价物 | API 不同但行为相同（如 event→rt_event） |
| 3 | ✅ 修改 `hwdef/common/` 中的链接脚本/启动文件 | STM32F7 特有初始化需要 |
| 4 | ✅ 修改 `hwdef/cuav_v5/hwdef.dat` | 直接对应 ChibiOS fmuv5/CUAVv5 |
| 5 | ✅ 在 submodule BSP 目录中添加 port 文件 | 通过 `INIT_APP_EXPORT` 注册 |
| 6 | ✅ 添加 GDB 调试辅助变量（volatile dbg_xxx） | 不改变功能逻辑 |
| 7 | ✅ 在 `.trae/rules/` 或技能中编写辅助文档/参考 | 不改变代码 |

## 七、违规判定与处理

| 表现 | 判定 | 纠正 |
|------|------|------|
| 修改了 `AP_HAL_RTT/` 外部的文件 | ❌ 违规 | `git restore` + 在 `AP_HAL_RTT/` 内实现 |
| 修改无 ChibiOS 行号引用 | ❌ 违规 | 立即暂停修改，查出 ChibiOS 对应行号后继续 |
| 修改有 ChibiOS 行号引用但值不同 | ⚠️ 需论证 | 在 plan 中写清差异原因（RM 版本、RTT 限制等） |
| 修改了 RT-Thread 内核 | ❌ 违规 | 保持只读，通过 BSP 配置或 port 文件绕过 |
| 修改了 `modules/ChibiOS/` | ❌ 违规 | 只读参考，永不修改 |
| 一次性改了 5+ 个文件后编译 | ⚠️ 高风险 | 回滚到基线，每次 1 个逻辑块重新提交 |
| **CC 执行越界** — 委托 CC 修改时，CC 自主修改了plan范围外的多个文件（含致命改动如 `.config` 栈大小 64KB→4KB） | ❌ 违规 | 复查 git diff HEAD 确认仅 plan 指定文件被改，回滚所有越界文件。在委托 prompt 中明确写“只改 XX 文件，禁止改其他文件” |

## 八、CUAV V5 专用参考

### 关键硬件映射

| 功能 | RTT 引脚 | ChibiOS fmuv5 引脚 | 寄存器 | 验证方法 |
|------|---------|-------------------|--------|---------|
| SPI1_SCK | PG11 | PG11 | GPIOG AFR[1] bits[15:12]=AF5 | `mdw 0x40021824` → bit15-12=0101 |
| SPI1_MISO | PA6 | PA6 | GPIOA AFR[0] bits[27:24]=AF5 | `mdw 0x40020020` → bit27-24=0101 |
| SPI1_MOSI | PD7 | PD7 | GPIOD AFR[0] bits[31:28]=AF5 | `mdw 0x40020C20` → bit31-28=0101 |
| SPI4_SCK | PE2 | PE2 | GPIOE AFR[0] bits[11:8]=AF5 | `mdw 0x40021020` → bit11-8=0101 |
| SPI4_MISO | PE13 | PE13 | GPIOE AFR[1] bits[23:20]=AF5 | `mdw 0x40021024` → bit23-20=0101 |
| SPI4_MOSI | PE6 | PE6 | GPIOE AFR[0] bits[27:24]=AF5 | `mdw 0x40021020` → bit27-24=0101 |
| SENSOR_EN | PE3 | PE3 (PAL) | GPIOE MODER bits[7:6]=01(output), ODR bit3=1 | `mdw 0x40021000` (MODER), `mdw 0x40021014` (ODR) |
| ICM20689 CS | PF2 | PF2 | GPIOF MODER bits[5:4]=01(output) | `mdw 0x40021400` |
| ICM20602 CS | PF3 | PF3 | GPIOF MODER bits[7:6]=01(output) | `mdw 0x40021400` |
| USART2 TX/RX | PD5/PD6 | PD5/PD6 | GPIOD AFR[0] | `mdw 0x40020C20` |

### 关键外设配置（ChibiOS fmuv5）

```bash
# SPI1 配置 (fmuv5/hwdef.dat:PG11-KEYS)
SPIDEV icm20689     SPI1 DEVID1  ICM20689_CS MODE3  2*MHZ  8*MHZ
SPIDEV icm20602     SPI1 DEVID2  ICM20602_CS MODE3  2*MHZ  8*MHZ
SPIDEV ms5611       SPI4 DEVID1  MS5611_CS   MODE3  1*MHZ  8*MHZ

# IMU 配置
IMU Invensense SPI:icm20689 ROTATION_NONE
IMU Invensense SPI:icm20602 ROTATION_NONE

# 气压计
BARO MS5611 SPI:ms5611

# 磁力计
MAG IST8310 I2C:0x0C
```

### 编译+验证命令

```bash
# 编译
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)

# 烧录（用 program 命令，不搞 flash write_bank）
openocd -f Tools/debug/openocd-f7.cfg \
  -c "program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify" \
  -c "reset run" -c "shutdown"

# 验证 1: OpenOCD（验证 CFRS/HFSR）
echo -e "reset halt\nmdw 0xE000ED28 2\nresume" | nc -q 3 localhost 4444

# 验证 2: MAVLink 心跳
python3 -c "
from pymavlink import mavutil
m = mavutil.mavlink_connection('/dev/ttyACM1')
m.wait_heartbeat(timeout=10)
print(f'HEARTBEAT: type={m.type}, status={m.status}')
"
```

---

## 十一、系统性 ChibiOS 对比方法论（2026-05-16 新增）

> **目的**：当被要求"透彻读 ChibiOS 找差距"时，按照本流程系统性地逐个模块对比。
> **原则**：不靠猜，不跳步，每个模块走完 5 步流程才能说"已对比"。

### 11.1 5 步对比流程

```
Step 1: 选模块
  ↓  从模块映射表（§二）选择"未对比"模块
Step 2: 读 ChibiOS
  ↓  精读 ChibiOS 源码的关键函数：
     · constructor / init / _begin
     · _write / _read / transfer（数据路径）
     · _timer_tick（1kHz 定时驱动）
     · set_xx() 配置函数
Step 3: 读 RTT
  ↓  精读 RTT 对应文件的关键函数
Step 4: 逐功能对比
  ↓  对每个功能点：
     · 记录 ChibiOS 的做法（含行号）
     · 记录 RTT 的做法（含行号）
     · 判断：等价？差距？架构差异？
Step 5: 记录结论
  ↓  如果无差距 → 写入已验证清单
     · 如果发现差距 → 按铁律修复流程（§四）
     · 如果架构差异 → 说明 RTOS 框架原因
```

### 11.2 对比检查清单

每对比一个模块，逐项回答：

| 号 | 检查项 | 完成 |
|----|--------|------|
| □ | **数据路径**：_write/transfer 是否等价？ | |
| □ | **定时驱动**：_timer_tick 是否在 1kHz 正确驱动？ | |
| □ | **中断/ISR**：DMA 或外设中断是否等价？ | |
| □ | **信号量/锁**：同步机制是否等价？ | |
| □ | **边界条件**：缓冲区满/空、超时、失败路径？ | |
| □ | **初始化**：init/_begin 是否覆盖相同寄存器？ | |
| □ | **错误处理**：panic/assert 是否等价？ | |
| □ | **USB 状态检查**：连接/断开检测是否匹配？ | |

### 11.3 典型对比节奏

- 每模块约需 **15-30 分钟**（读 ChibiOS + 读 RTT + 对比）
- 复杂模块（UARTDriver 1836行）约 30-40 分钟
- 简单模块（Storage 504行）约 10-15 分钟
- **每次对比至少 2 个模块**后才汇报结果

### 11.4 对比完成后的记录

将对比结果写入：
1. **主 skill 的映射表**（§二）更新验证状态
2. **`references/comparison-verification-YYYY-MM-DD.md`** 详细对比表
3. **memory** 中记录"已验证模块清单"（避免后续重复对比）

### 11.5 已对比模块汇总（2026-05-16 Phase 1 完成时全面更新）

| 模块 | 对比日期 | 结论 | 参考文件 |
|------|---------|------|---------|
| SPIDevice | 2026-05-15 | ✅ 功能等价 | `references/comparison-verification-2026-05-16.md` |
| I2CDevice | 2026-05-15 | ✅ 功能等价（含 fix） | `references/i2cdevice-semaphore-alignment-2026-05-15.md` |
| DeviceBus | 2026-05-15 | ✅ 功能等价 | — |
| Semaphores | 2026-05-15 | ✅ 功能等价 | — |
| HAL_RTT_Class | 2026-05-16 | ✅ 功能等价 | `references/startup-sequence-fix-2026-05-16.md` |
| UARTDriver | 2026-05-16 | ✅ 核心功能等价 | `references/phase1-gap-analysis-2026-05-16.md` |
| AnalogIn | 2026-05-16 | ✅ 功能等价 | `references/comparison-verification-2026-05-16.md` |
| Scheduler | 2026-05-16 | ✅ 功能等价 | 同上 |
| Storage | 2026-05-16 | ✅ 功能等价 | 同上 |
| GPIO | 2026-05-16 | ✅ 功能等价 | — |
| RCInput | 2026-05-16 | ✅ 功能等价 | — |
| RCOutput | 2026-05-16 | ⚠️ 架构差异 | `references/rcoutput-direct-register-access.md` |
| Util | 2026-05-16 | ✅ 功能等价 | — |
| system.cpp | 2026-05-16 | ✅ 功能等价 | — |
| Flash | 2026-05-16 | ✅ 功能等价（2 个 low gap） | `references/phase1-gap-analysis-2026-05-16.md` |

### 11.6 对比常见陷阱

| 陷阱 | 说明 | 避免方法 |
|------|------|---------|
| ❌ 只读 RTT 不读 ChibiOS | 没有参考基线，看不出差距 | 先读 ChibiOS 再读 RTT |
| ❌ 只看函数名不看实现 | 同名函数可能语义不同 | 逐行比较关键逻辑 |
| ❌ 忽略 RTOS 框架差异 | ChibiOS 线程 vs RTT 回调，功能等价即 pass | 标注"架构差异—功能等价" |
| ❌ 对比后不记录 | 下次重复对比浪费时间 | 写入 reference 文件 + memory |
| ❌ 只对比一个模块就汇报 | 效率低且容易遗漏关联影响 | 至少 2 个模块一轮 |

---

### 🆕 I2C 总线注册模式 — 从 AP_HAL_RTT 内注册 RT-Thread 总线设备（2026-05-15 新增）

> **问题**：CUAV V5 的 RT-Thread BSP 没有 `drv_i2c.c`（STM32 I2C HAL 桥接文件），导致 `rt_i2c_bus_device_find("i2c3")` 返回 NULL。
> **方案**：从 `I2CDevice.cpp` 内直接注册 I2C3 总线设备，绕过缺失的 BSP 驱动层。

**实现模式**（`I2CDevice.cpp` 已验证）：

```
I2CDevice 构造函数(bus=0)
  → _i2c3_register()
    → _i2c3_hw_init()          // CMSIS 寄存器：时钟、GPIO AF4、TIMINGR、PE
    → rt_i2c_bus_device_register(&_i2c3_bus_dev, "i2c3")
  → rt_i2c_bus_device_find("i2c3")  // 现在可以找到了
```

**关键要素**：

1. **静态 ops 结构体** — 实现 `master_xfer` 回调，通过 RT-Thread `dev_i2c_core.c` 被 `rt_i2c_master_send/recv` 调用
2. **前向声明** — `_i2c3_master_xfer()` 需要前向声明，因为 ops 结构体在实现函数之前
3. **注册时机** — 必须在 RT-Thread 调度器启动后调用（因为 `rt_i2c_bus_device_register()` 初始化 mutex）
4. **延迟注册** — 在 `I2CDevice` 构造函数中触发（`bus==0` 时），确保调度器已运行

**STM32F7 I2C TIMINGR 计算**（RM0410 §30.4.2，参考文件 `references/i2c3-hw-init.md`）：

| 参数 | 100kHz (PCLK1=54MHz) | 公式 |
|------|----------------------|------|
| PRESC | 3 | tI2CCLK = (PRESC+1) × tPCLK1 |
| SCLL | 67 | tSCLL = (SCLL+1) × tI2CCLK |
| SCLH | 66 | tSCLH = (SCLH+1) × tI2CCLK |
| SDADEL | 2 | RM0410 Table 137 |
| SCLDEL | 3 | RM0410 Table 138 |

**Master 传输流程**（RM0410 §30.4.3）：
```
CR2: SADD[7:1] | RD_WRN | NBYTES | START | AUTOEND
→ 等待 TXIS（发）或 RXNE（收）
→ 写 TXDR 或读 RXDR
→ 等待 TC（发完最后字节时）
→ 等待 STOPF（AUTOEND 自动产生 Stop）
→ 清除 STOPCF
```

**陷阱**：\n- `I2C_ICR_BUSYCF` **不存在** — BUSY 是 ISR 只读位，不能通过 ICR 清除。只能等待释放\n- CR2 写入一次性启动传输（START + AUTOEND 同时设）\n- `I2C_CR2_RELOAD` 必须为 0（写 1 进入 reload 模式）\n- 双锁问题：`rt_i2c_bus_lock()` 取 `bus->lock`，`rt_i2c_transfer()` 内部又取一次 → RT-Thread mutex **支持递归 take**（`mutex->owner==thread → hold++`），安全但浪费\n- **⚠️ `get_semaphore()` 必须返回总线级锁**（`&_bus_dev->semaphore`），与 ChibiOS `I2CBus::get_semaphore()` 返回 `&bus.semaphore` 对齐。如果返回 per-device 私有 `_sem`，`WITH_SEMAPHORE` 块内不持有总线锁，同总线上其他 I2C 设备可插入操作（commit `bfe648f60c` 修复）

**参考文件**：`references/i2c3-hw-init.md` — I2C3 硬件初始化细节
**参考文件**：`references/i2cdevice-semaphore-alignment-2026-05-15.md` — I2CDevice get_semaphore() 总线锁对齐修复记录

---

### 🆕 ADC DMA 循环缓冲区 — NDTR 安全半缓冲读取（2026-05-15 新增）

> **核心原则**：STM32F7 ADC DMA 缓冲区必须在 **SRAM1**（0x20020000+），**DTCM**（0x20000000-0x2001FFFF）不可 DMA 写入。
> **初始化顺序**：先配 DMA → 清 LIFCR → 使能中断（可选）→ 最后使能 ADC CR2（RM0410 §13.4.6 + STM32F7 erratum 2.1.12）

**DMA 配置**：

| 参数 | 值 | 说明 |
|------|-----|------|
| Stream | DMA2 Stream0 | ADC1 专用 |
| Channel | 0 | RM0430 Table 49 |
| DIR | 外设→内存 | `DMA_SxCR_DIR=0` |
| MSIZE/PSIZE | 16-bit | ADC DR 是 16 位 |
| CIRC | 1 | 循环模式 |
| MINC | 1 | 内存地址递增 |

**NDTR 安全读取模式**（无中断方案 — 已验证）：

DMA NDTR 从 `ADC_DMA_BUF_SIZE` 递减到 0，判断 DMA 当前写位置：

```
half_size = ADC_DMA_BUF_SIZE / 2
done = ADC_DMA_BUF_SIZE - NDTR
safe_half = (done / half_size) & 1
  if safe_half==0: DMA 在写入前半 → 读取后半
  if safe_half==1: DMA 在写入后半 → 读取前半
```

这样保证不会读到 DMA 正在写入的缓冲区半区。100Hz `_timer_tick` 频率下每个半区 (~16 样本) 数据完整。

**缓冲区声明**（SRAM1 段属性）：
```cpp
static uint16_t __attribute__((section(".sram1"), aligned(32)))
    _adc_dma_buf[ADC_NUM_CHANNELS * ADC_DMA_BUF_DEPTH];
```

**D-Cache 处理**：
```cpp
SCB_InvalidateDCache_by_Addr((uint32_t *)_adc_dma_buf, sizeof(_adc_dma_buf));
```
必须在读取 DMA 缓冲区前调用，确保 cache 内容与 SRAM1 一致。

**参考文件**：`references/adc-dma-ndtr-polling.md`

---

### 🆕 SPI 动态 BR — 寄存器级速度切换（2026-05-15 新增）

在寄存器级 SPI 路径（`spi1_poll_transfer()`）中，BR 分频在每次 CS 拉起时设定（`cs_take==true`）：

```cpp
spi->CR1 = SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI |
           SPI_CR1_CPOL | SPI_CR1_CPHA |
           (high_speed ? SPI_CR1_BR_0 : (SPI_CR1_BR_0 | SPI_CR1_BR_1));
           // HIGH=/8(13.5MHz), LOW=/16(6.75MHz)
```

速度通过 `SPIDevice::_speed_high` 成员变量传递：
1. `set_speed()` 保存值 → `_speed_high = (speed == SPEED_HIGH)`
2. `transfer()` / `transfer_fullduplex()` 传 `_speed_high` 给 `spi1_poll_transfer()`
3. `spi1_poll_transfer()` 选 BR

**速度对照**（STM32F767, APB2=108MHz）：

| 模式 | BR 值 | 分频 | 频率 | 用途 |
|------|-------|------|------|------|
| SPEED_LOW | BR=3 | /16 | 6.75MHz | IMU 探测/初始化 |
| SPEED_HIGH | BR=2 | /8 | 13.5MHz | IMU 正常数据采集 |

**陷阱**：BR 只在 `cs_take==true` 时重配。CS 保持（burst 读取）期间速度不变。

---

### ⚠️ DMA 资源分配检查（2026-05-14 新增）

在 RTT 上使能任何新的 DMA 功能前，必须检查 STM32 DMA Stream 分配冲突：

```bash
# 1. 检查 board.h 中已分配的 DMA Stream
grep "DMA.*STREAM\|DMA.*Stream\|DMA.*INSTANCE" modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/board.h

# 2. 检查 .config 中 DMA 是否实际编入
grep "BSP_.*DMA\|.*USING_DMA" libraries/AP_HAL_RTT/hwdef/common/.config

# 3. 对照 RM0430 Table 49 (DMA request mapping):
#    ADC1  = DMA2 Stream0 Channel 0
#    SPI1_RX = DMA2 Stream2 Channel 3 (board.h 已重映射到 Stream2)
#    SPI1_TX = DMA2 Stream5 Channel 3 (board.h 重映射)
#    SPI4_RX = DMA2 Stream0 Channel 4 (board.h 定义但. config 若 BSP_SPI4_RX_USING_DMA 未设置则空闲)
#    SPI4_TX = DMA2 Stream1 Channel 4

# 4. 验证目标 Stream 是否已被其他外设占用
#    Stream 在同一时间只能服务一个外设。SPI4_RX 和 ADC1 都映射到 DMA2 Stream0
#    → 除非 SPI4_RX_DMA 在 .config 中未启用，否则不可同时使用
```

**典型情景**：CUAV V5 上 SPI4_RX 编译时定义了 `DMA2_Stream0` (board.h:68) 但 `.config` 中 `BSP_SPI4_RX_USING_DMA` 未设置 → Stream0 空闲，可安全分配给 ADC1 DMA。

---

### ⚠️ ChibiOS ADC DMA 累计清零模式（2026-05-14 新增）

ChibiOS 的 `read_adc()` (AnalogIn.cpp:625-659) 在每次读取所有通道后**原子化清零**累计数组：

```c
chSysLock();
for (uint8_t i = 0; i < num_grp_channels; i++) {
    val[i] = sample_sum[index][i] / sample_count[index];
}
memset(sample_sum[index], 0, sizeof(uint32_t) * num_grp_channels);
sample_count[index] = 0;
chSysUnlock();
```

RTT 实现必须**在关中断下**批量读取+清零，否则：
- 不清零 → `_sample_sum` 约 150s 后 32 位溢出
- 逐个清零 → DMA ISR 可能在读取过程中修改数据

**正确模式**（AnalogIn.cpp 参考实现）：
```cpp
static void _adc_read_all(uint32_t *vals)
{
    rt_base_t level = rt_hw_interrupt_disable();
    uint32_t cnt = _sample_count;
    for (uint8_t i = 0; i < ADC_NUM_CHANNELS; i++) {
        vals[i] = _sample_sum[i] / cnt;
    }
    memset((void *)_sample_sum, 0, sizeof(uint32_t) * ADC_NUM_CHANNELS);
    _sample_count = 0;
    rt_hw_interrupt_enable(level);
}
```

---

### ⚠️ ChibiOS Device.cpp 同款 `thread_started` 陷阱（2026-05-16 发现）

**ChibiOS `Device.cpp:107` 实际上使用了与 RTT 相同的模式**——`thread_started` 在 `rt_malloc` 之前设置：

```cpp
// ChibiOS Device.cpp:106-137
if (!thread_started) {
    thread_started = true;      // ← 同样过早设置！
    ...
    thread_ctx = thread_create_alloc(...);
    if (thread_ctx == nullptr) {
        AP_HAL::panic("Failed to create bus thread %s", name);  // ← 但用 panic 兜底！
    }
}
```

**关键差异**：ChibiOS 对线程创建失败调用 `AP_HAL::panic()`（立即 halt），所以 `thread_started` 提前设置不会导致静默失败。RTT 之前没有 panic 保护，导致 `rt_malloc(8192)` 失败后 `_thread_started` 永久锁定且无错误告警。

**RTT 修复记录**（2026-05-16 会话）：
1. `BUS_STACK_SIZE 8192 → 2048` — RT-Thread 堆最大连续块 < 8KB，2048 够用
2. `_thread_started = true` 移至 `rt_thread_startup()` 成功后 — 允许失败重试

**教训**：ChibiOS 的代码不一定在所有细节上都更正确——它只是在特定条件下（有 panic）隐藏了同款 bug。RTT 的修复（延迟设置 flag）是更健壮的方案。

### ⚠️ app_descriptor 基础设施 — 根因：scons 缺少 set_app_descriptor() 后处理（2026-05-16 更新）

> **2026-05-16 重大发现（A1-Research）**：此前的"hwdef.dat 加 2 个 define"指导是**不完整的**。实际根因是 scons 构建缺少 `set_app_descriptor()` Python 后处理脚本——二进制中有 app_descriptor 签名（sig ✅、board_id=50 ✅），但 `image_crc1=0`、`image_crc2=0`、`image_size=0`、`git_hash=0`。bootloader 的 `check_good_firmware_unsigned()` 验证失败因为 `len1+desc_len > image_size(0)`，拒绝跳转。

正确的完整修复涉及 **4 层**，缺一不可：

| 层级 | 文件 | 修改 | 作用 |
|------|------|------|------|
| 1. 编译 | `hwdef/cuav_v5/hwdef.dat` | 加 `define AP_CHECK_FIRMWARE_ENABLED 1` 和 `APJ_BOARD_ID TARGET_HW_PX4_FMU_V5` | 使 app_descriptor 结构体被编译且 board_id 正确 |
| 2. 链接 | `hwdef/common/board/linker_scripts/link.lds` | `KEEP(*(.isr_vector))` 后加 `KEEP(*(.apsec_data)); KEEP(*(.app_descriptor));` | 确保链接器不丢弃 app_descriptor section |
| 3. Section | `AP_CheckFirmwareDefine.h` | `#if CONFIG_HAL_BOARD == HAL_BOARD_CHIBIOS` 后加 `\|\| CONFIG_HAL_BOARD == HAL_BOARD_RTT` | 使 app_descriptor 使用 `.app_descriptor` section 属性 |
| **4. 后处理** | **scons 构建脚本** | **添加 `set_app_descriptor()` Python 函数**，复制 `Tools/ardupilotwaf/chibios.py:266-328` | **填充 image_crc、image_size、git_hash 到二进制文件** |

**层级 4（后处理）是修复 bootloader 跳转的关键步骤**。没有它，descriptor 字段全为 0，bootloader 校验失败。

#### 后处理脚本参考（ChibiOS waf → RTT scons）

在 `Tools/ardupilotwaf/chibios.py:266-328` 中，`set_app_descriptor` 类：
1. 使用 `arm-none-eabi-objcopy -O binary` 生成 `.bin`
2. 打开 `.bin` 文件，搜索 8 字节签名 `{0x40, 0xa2, 0xe4, 0xf1, 0x64, 0x68, 0x91, 0x06}`
3. 计算：`image_crc1 = crc32(bin_data[0:offset])`、`image_crc2 = crc32(bin_data)`、`image_size = len(bin_data)`、`git_hash = GIT_VERSION`
4. 将计算值写入二进制文件对应偏移

**RTT scons 集成方式**：在 `libraries/AP_HAL_RTT/hwdef/scripts/` 下创建 `set_app_descriptor.py`，在 `.bin` 生成后由 scons 构建规则调用。参考 `libraries/AP_HAL_RTT/hwdef/scripts/rtt_hwdef.py` 的 scons 集成模式。

#### 根因链（修复后验证）

```bash
# 1. 验证 app_descriptor 符号存在
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep app_descriptor
# 应输出: 080xxxxx T app_descriptor

# 2. 验证签名存在 + 字段非零
python3 -c "
import struct, zlib
with open('build/rtt_cuav_v5/rtthread.bin', 'rb') as f:
    d = f.read()
sig = bytes([0x40, 0xa2, 0xe4, 0xf1, 0x64, 0x68, 0x91, 0x06])
pos = d.find(sig)
assert pos >= 0, '❌ SIGNATURE NOT FOUND'
offset = pos + 8
board_id, _, img_crc1, img_crc2 = struct.unpack_from('<IIII', d, offset)
img_size, git_hash_lo, git_hash_hi = struct.unpack_from('<III', d, offset + 16)
print(f'✅ SIGNATURE at 0x{pos:x}')
print(f'  board_id={board_id} expect=50 {\"✅\" if board_id==50 else \"❌\"}')
print(f'  image_size={img_size} (len={len(d)}) {\"✅\" if img_size==len(d) else \"❌\"}')
print(f'  image_crc1=0x{img_crc1:08x} {\"✅\" if img_crc1!=0 else \"❌\"}')
print(f'  image_crc2=0x{img_crc2:08x} {\"✅\" if img_crc2!=0 else \"❌\"}')
print(f'  git_hash=0x{git_hash_lo:08x}{git_hash_hi:08x} {\"✅\" if git_hash_lo!=0 else \"❌\"}')
"

# 3. 烧录后验证 bootloader 跳转
openocd -f Tools/debug/openocd-f7.cfg \\
  -c "program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify" \\
  -c "reset run" -c "shutdown"
sleep 15
ls /dev/ttyACM*   # 应有 ACM1
python3 -c "from pymavlink import mavutil; m=mavutil.mavlink_connection('/dev/ttyACM1'); m.wait_heartbeat(timeout=10); print(f'HEARTBEAT: status={m.status}')"

# 4. 独立 reset 持久性验证
echo -e "reset run" | nc -w2 localhost 4444 2>/dev/null
sleep 15
ls /dev/ttyACM*   # 应仍有 ACM1
```

**诊断区分：签名不存在 vs 签名字段全零**

| 症状 | 原因 | 修复 |
|------|------|------|
| 在 bin 中用 python3 搜索 8 字节签名找不到 | 层级 1 或 3 未做（app_descriptor 未编译或被丢弃） | 加 hwdef.dat define + AP_CheckFirmwareDefine.h 的 ifdef |
| 签名找到但字段全 0（image_size=0, crc=0, git_hash=0） | **层级 4 未做** — 缺少后处理脚本 | 写 Python set_app_descriptor() 并集成到 scons |
| 签名存在、字段非零、但 bootloader 仍不跳转 | IWDG 复位或 CRC 校验失败 | 检查 RCC_CSR IWDGRSTF、确认 CRC 算法一致 |

---

## 九、快速参考：ArduPilot RTT 启动线程优先级模型

> ChibiOS 的优先级数字**越小越优先**（1=最高）。
> RT-Thread 的优先级数字**越小越优先**（0=最高）。
> 两者语义相同，但数值范围不同（ChibiOS: 0-255, RTT: 0-255 可配）。

| 线程/任务 | ChibiOS 优先级 | RTT 优先级 | 说明 |
|-----------|---------------|-----------|------|
| Timer 线程 | 182 | 4 | 1kHz 定时器 |
| SPI 总线线程 | 181 | 5 | SPI 设备回调 |
| UART 线程 | 180 (或 config) | 6 | 串口 drain |
| **主线程 setup** | **APM_STARTUP_PRIORITY (ChibiOS: 极低)** | **8 (setup 降优先级)** | setup 期间让出 CPU |
| **主线程 main loop** | **APM_MAIN_PRIORITY (ChibiOS: 180)** | **5 (恢复)** | 主循环高优先级 |
| IO 回调 | 179 | 18 | 低优先级后台任务 |

### ✅ 已验证修复（2026-05-16 会话实施）

**问题根因**：原 RTT 实现缺少 ChibiOS 的 5 步启动顺序。具体来说：

1. `_hal_initialized = true` 在 `scheduler->init()` 末尾（太早！）→ timer/SPI 线程在 init 返回后立即开始运行
2. 主线程不降优先级 → setup 期间 timer 线程已跑了几十到几百毫秒
3. INS 校准挂死另有其因（SPI 数据路径问题），但时序偏差让其他线程行为不确定

**修复内容**：3 个文件原子化修改

#### 1. `HAL_RTT_Class.cpp` — 对齐 ChibiOS 5 步启动顺序

```cpp
_main_loop_entry() {
    // Step 1: Set MAIN priority (prio 5 = APM_RTT_MAIN_PRIORITY)
    rt_uint8_t main_prio = APM_RTT_MAIN_PRIORITY;
    rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &main_prio);

    // Step 2: Drop to STARTUP priority (prio 15 = APM_RTT_STARTUP_PRIORITY)
    rt_uint8_t startup_prio = APM_RTT_STARTUP_PRIORITY;
    rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &startup_prio);

    // Step 3: Signal timer/SPI/UART threads to start — ChibiOS: HAL_ChibiOS_Class.cpp:273
    a->sched->hal_initialized();

    // Step 4: setup() runs at low priority (timer/SPI can preempt freely)
    a->callbacks->setup();

    // Step 5: Restore MAIN priority — ChibiOS: HAL_ChibiOS_Class.cpp:317
    rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &main_prio);
}
```

#### 2. `Scheduler.cpp` — `_hal_initialized` 移到 hal_initialized()

```diff
-    _hal_initialized = true;  // 原来在 init() 末尾
+    // _hal_initialized 由 hal_initialized() 在 _main_loop_entry 的 Step 3 设置
```

#### 3. `Scheduler.h` — 定义 STARTUP priority

```c
#define APM_RTT_STARTUP_PRIORITY  15
#define APM_RTT_MAIN_PRIORITY      5
#define APM_RTT_TIMER_PRIORITY     4
#define APM_RTT_IO_PRIORITY       18
```

### 优先级体系对照

| 线程/任务 | ChibiOS 优先级 | RTT 优先级 | 说明 |
|-----------|---------------|-----------|------|
| Timer 线程 | 182 | **4** | 1kHz 定时器 |
| SPI 总线线程 | 181 | **4** | SPI 设备回调 |
| UART 线程 | 60 | **6** | 串口 drain |
| **主线程 main loop** | **180 (APM_MAIN_PRIORITY)** | **5 (APM_RTT_MAIN_PRIORITY)** | 主循环高优先级 |
| **主线程 setup 期间** | **10 (APM_STARTUP_PRIORITY)** | **15 (APM_RTT_STARTUP_PRIORITY)** | setup 降级让 timer/SPI 抢占 |
| IO 回调 | 58 | **18** | 低优先级后台任务 |

**为什么 APM_RTT_STARTUP_PRIORITY = 15？**
- 低于 timer(4) / SPI(4) / UART(6) → 这些线程可自由抢占 setup
- 高于 IO(18) / storage(16) → IO 回调不会在 setup 期间干扰
- ChibiOS startup=10 在 RTT 语义中等价于 15

### 为什么 prio=8 不够？

之前的方案 prio=8 仅实现了"降优先级"，但缺少两步：
1. **`hal_initialized()` 放在 setup 之前（Step 3 → Step 4）** — 原来在 init() 末尾设 `_hal_initialized = true`，timer 线程过早启动
2. **STARTUP priority 数字正确** — prio=15 确保 timer(4)/SPI(4) 比主线程优先级高，但不会让 UART(6) 在主线程 yield 时独占 CPU

---

### ⚠️ delay_microseconds_boost() 架构对照：ChibiOS chThdSleep vs RTT DWT 忙等（2026-05-14 发现）

这是本次会话中发现的最关键架构差异。直接决定了 263Hz 还是 7Hz 的循环率，以及 setup 是否会死锁。

#### ChibiOS 的实现（`Scheduler.cpp:162-175`）

```cpp
void Scheduler::delay_microseconds(uint16_t usec)
{
    uint32_t ticks;
    ticks = chTimeUS2I(usec);          // µs → ticks（CH_CFG_ST_FREQUENCY=1000）
    if (ticks == 0) ticks = 1;         // 最小 1 tick = 1ms
    ticks = MIN(TIME_MAX_INTERVAL, ticks);
    chThdSleep(MAX(ticks, CH_CFG_ST_TIMEDELTA));  // **始终 yield CPU**
}
```

**关键性质**：`chThdSleep()` **始终 yield CPU**。即使在 `delay_microseconds(200)` 这种 sub-tick 调用中，也 sleep 至少 1ms 并让出 CPU。这意味着：
- Timer 线程（prio 182 ChibiOS）可以在主线程 sleep 期间运行
- 即使主线程处于 boost 优先级，sleep 期间 CPU 自动让给低优先级线程
- Timer 线程永远不会饿死

#### RTT 的原始问题（`Scheduler.cpp` 修改前）

```cpp
void Scheduler::delay_microseconds(uint16_t us)
{
    // 原始代码：所有延迟都使用 rt_thread_delay()
    rt_thread_delay(us < tick_us ? 1 : us / tick_us);  // 最小 1ms
}
```

但 `delay_microseconds_boost()` 中的调用链导致：
1. `delay_microseconds_boost(200)` → boost prio 3 → `rt_thread_delay(1)` → yield 1ms
2. `_next_sample_usec` 漂移累积 +800µs/次
3. 多次后漂移达 80ms → `wait_for_sample()` 需要等 80ms → 7-8Hz

#### RTT DWT 修复后的新问题（本会话发现）

**修复**：sub-tick 延迟改用 DWT 忙等替代 `rt_thread_delay(1)`，消除漂移。

```cpp
void Scheduler::_delay_microseconds_dwt(uint16_t us)
{
    // DWT CYCCNT 精确忙等 — 不 yield CPU！
    const uint32_t cycles = us * (SystemCoreClock / 1000000U);
    const uint32_t start = DWT_CYCCNT_REG;
    while ((DWT_CYCCNT_REG - start) < cycles) {
        asm volatile("dsb" ::: "memory");
    }
}
```

**新问题**：DWT 忙等**不会 yield CPU**。如果主线程在此期间处于 boost 优先级（prio 3 > timer prio 4），timer 线程完全被饿死。导致：

| 场景 | boost_end 存在？ | 现象 |
|------|-----------------|------|
| setup 期间 `ins.init()` 调用 `delay_microseconds_boost` | ❌ 无 boost_end | Timer 饿死 → ADC 不工作 → INS 永远收不到样本 → **死锁** |
| setup 期间同上 | ✅ 有 boost_end | DWT 后恢复 prio 5 → timer 可运行 → ADC 工作 → setup 正常完成 |
| 主循环 400Hz 运行 | ❌ 无 boost_end | 每次循环 timer 无法运行 → 计时器溢出+EKF 异常 |
| 主循环 400Hz 运行 | ✅ 有 boost_end | Timer 在任务执行期间抢占 → 循环率从 400Hz 降到 263Hz |

#### 解决方案：三层 boost 架构（本次会话实现）

```
循环开始: main_loop_pre_boost() → prio 3（timer 不能抢占）
  ├── delay_microseconds_boost(DWT) → _priority_boosted=true → 跳过 boost/restore
  ├── 任务执行 at prio 3（timer 不能抢占 → 263→400Hz 的关键！）
  └── loop 结尾: boost_end() → prio 5（timer 可在间隙运行）
下次循环 → main_loop_pre_boost() → 重新 boost 到 3
```

**要点**：
1. **`delay_microseconds_boost()` 内**：DWT 忙等 + 结尾 `boost_end()`（释放 boost 让 timer 在 setup 期间运行）
2. **主循环入口**：`main_loop_pre_boost()` — 在 `callbacks->loop()` 前 boost 到 3
3. **主循环出口**：`boost_end()` — 在 `watchdog_pat()` 前恢复 prio 5
4. 这样 `delay_microseconds_boost()` 内部看到 `_priority_boosted=true` → `should_boost=false` → 跳过 boost 和 restore → 保持 prio 3 整个循环

**ChibiOS 对照**：ChibiOS 的 `delay_microseconds_boost()` 从不 reset `_priority_boosted`，也不在 `boost_end()` 外 restore 优先级。boost 从第一个 `wait_for_sample()` 调用起持续整个主循环生命周期。

#### RTT 特有 DWT 忙等 yield 铁律

```
DWT 忙等期间，相当于在当前优先级上 spin。
必须在 spin 后恢复优先级到一个低于 timer（4）的值，
否则 timer 线程被饿死。

恢复优先级 = 5（APM_RTT_MAIN_PRIORITY），
低于 timer(4) 但高于 UART(6)/IO(18)/Storage(16)。
```

---

### ⚠️ IWDG 启动时机与 timer 线程的依赖关系

IWDG（独立看门狗）在 CUAV V5 上被**我们的代码**启动（而非 bootloader），启动时机在 `set_system_initialized()`（`Scheduler.cpp:642-657`）：

```cpp
void Scheduler::set_system_initialized() {
    _initialized = true;
    // 启动 IWDG: PR=3(/32), RLR=2047 → ~2s 超时
    IWDG_KR = 0x5555;
    IWDG_PR = 3;
    IWDG_RLR = 2047;
    IWDG_KR = 0xCCCC;   // 启动
}
```

**关键依赖**：IWDG 启动后，必须在 2s 内被 timer 线程 pat（`watchdog_pat()`）。timer 线程在 `_timer_thread_entry` 中间隔 1ms pat 一次。但如果 `_hal_initialized` 没有被及早设置，timer 线程会阻塞在 `while (!sched->_hal_initialized)` 中，无法 pat。

**原始问题**：`_hal_initialized = true` 只在 `scheduler->init()` 末尾设置（line 401），`scheduler->init()` 在 `run()` 中间调用。主线程 `setup()` 需 ~25s，IWDG 在 setup 完成后启动，但如果在 setup 后才设置 `_hal_initialized`，timer 线程在 setup 期间不运行，IWDG 启动后 timer 立即开始 pat，2s 窗口内问题不大。

**但！** 如果观察到的复位（~30s 周期）与 IWDG 无关，检查其他原因：
- SRAM 持续检测（warm reset 后 stage 变量保留旧值）
- 检查 `RCC_CSR` 复位标志（被 RMVF 清除后不可读）
- `ins.init()` 内部的软件复位或 panic

---

## 十、每个 RTT 组件对应的 ChibiOS 参考文件速查表

| 要修改/理解 | 先看这个 ChibiOS 文件 | 再看这个 STM32 RM 章节 |
|-----------|---------------------|---------------------|
| SPI 驱动 | `AP_HAL_ChibiOS/SPIDevice.cpp` + `hwdef/fmuv5/hwdef.dat` | RM0430 §37 SPI |
| I2C 驱动 | `AP_HAL_ChibiOS/I2CDevice.cpp` | RM0430 §38 I2C |
| UART/USB CDC | `AP_HAL_ChibiOS/UARTDriver.cpp` + `modules/ChibiOS/os/hal/src/hal_serial_usb.c` | RM0430 §44 USART + §45 USB OTG |
| ADC | `AP_HAL_ChibiOS/AnalogIn.cpp` | RM0430 §19 ADC |
| GPIO | `AP_HAL_ChibiOS/GPIO.cpp` | RM0430 §10 GPIO |
| RC 输入 | `AP_HAL_ChibiOS/RCInput.cpp` | — |
| PWM 输出 | `AP_HAL_ChibiOS/RCOutput.cpp` | RM0430 §32 TIM |
| 存储 | `AP_HAL_ChibiOS/Storage.cpp` | — |
| 调度器 | `AP_HAL_ChibiOS/Scheduler.cpp` | — |
| 启动 | `modules/ChibiOS/os/common/startup/ARMCMx/compilers/GCC/crt0_v7m.S` | RM0430 §6 Boot |
| Flash 操作 | `modules/ChibiOS/os/hal/ports/STM32/STM32F7xx/hal_flash_lld.c` | RM0430 §7 Flash |
| 系统控制 | `AP_HAL_ChibiOS/system.cpp` | RM0430 §6 SCB |
| CAN | `AP_HAL_RTT/CANFDIface.cpp` | RM0430 §48 bxCAN |

### USB CDC 对比参考（2026-05-16 新增）

当调试 USB CDC TX 问题时，必须同时参考 **ChibiOS** 和 **CherryUSB** 实现：

| 对比维度 | ChibiOS (hal_usb_lld.c + sdu) | CherryUSB (usb_dc_dwc2.c + usbd_serial.c) |
|---------|-------------------------------|-------------------------------------------|
| TX 架构 | SDU 输出队列 + SOF 钩子自愈 | 回调驱动的 bulk-in + tx_active 互斥锁 |
| SOF Watchdog | `sduSOFHookI()` 每 SOF 检查 `obqueue` | **缺失** — 无 SOF 级恢复 |
| GCCFG (F7) | stepping 2: 仅 PWRDWN (0x10000) | stepping 2: 仅 PWRDWN (0x10000) |
| B-session valid | `GOTGCTL.BVALOEN\|BVALOVAL` | `b_session_valid_override = true` |
| 自愈机制 | `obnotify` → 重试输出 | 无 → 需 `DIEPMSK.TOM` 使能 TOC 中断 |

详细对比 → `rtt-cdc-in-timeout-recovery` skill 的 `references/stm32f7-gccfg-config.md`

---

## 十一、原子化修改工作流（2026-05-16 新增 — 廖博士纠正）

> **廖博士纠正**：不能再稀里糊涂的搞了。必须先学习 ChibiOS，把任务拆分成一个一个文件的原子任务。

### 修改前必做的 3 件事

每次修改代码前，必须按以下顺序执行：

#### Step 1: 精读 ChibiOS 参考代码

```bash
cat libraries/AP_HAL_ChibiOS/<对应文件> | head -400  # 精读完整上下文
```

#### Step 2: 创建原子任务清单

```
目标：<要修复的功能>
涉及文件：
  1. libraries/AP_HAL_RTT/<文件> — ChibiOS 参考: <对应文件>:<行号>
  2. libraries/AP_HAL_RTT/<文件> — ChibiOS 参考: <对应文件>:<行号>
...
每次只改 1 个文件，编译验证通过后再改下一个。
```

#### Step 3: 逐行记录差异与修改

```markdown
### 文件: libraries/AP_HAL_RTT/<文件名>

ChibiOS 参考: libraries/AP_HAL_ChibiOS/<对应文件>:<行号>
ChibiOS 做了什么:
  <ChibiOS 的关键代码片段>
RTT 当前做了什么:
  <RTT 的代码>
差异:
  <关键差异>
修改后:
  <修改后的 RTT 代码>
验证:
  <编译通过 / 烧录验证>
```

### 多文件修改的纪律

| 状态 | 规则 |
|------|------|
| 修改 ≤ 3 个文件且逻辑同一 | ✅ 一次改完 → 编译 → 烧录 → 验证 |
| 修改 > 3 个文件 | ❌ 必须拆分批次，每批 ≤ 3 个文件 |
| 修改涉及不同模块（如 Scheduler + SPIDevice + UARTDriver） | ❌ 每个模块独立一批 |
| 修改 `HAL_RTT_Class.cpp` | ⚠️ 必须在批次的**第一组** — 影响全局启动顺序 |
| 修改后编译失败 | ❌ 回退整批次，找出哪个文件导致的 |

### 3 文件原子化修复范例（已验证）

#### 修复目标：对齐 ChibiOS 启动顺序

| 批次 | 文件 | ChibiOS 参考行 | 验证结果 |
|------|------|---------------|---------|
| 1 | `HAL_RTT_Class.cpp` | `main_loop()` L240-380 | 编译通过 ✅ |
| 2 | `Scheduler.cpp` | 无直接对应（hal_initialized 语义） | 编译通过 ✅ |
| 3 | `Scheduler.h` | `APM_STARTUP_PRIORITY` 定义 | 编译通过 ✅ |

**关键发现**：写原子清单时发现 DeviceBus.cpp 的 `take(10)` → `BLOCK_FOREVER` 和 SPIDevice.cpp 的 `get_semaphore()` 返回总线锁这两个修复此前就已到位。如果不先做原子化清单，就会重复做已经完成的工作。

> **最后提醒**：每次动手改代码前，先问自己一句——"ChibiOS 里是怎么做的？" 找到对应行号再动手。这就是 1:1 复刻移植的铁律。
>
**参考文件**：`references/openocd-diagnostic-workflow.md` — 包含复位诊断、setup 进度监测、循环率测量、stage 编号速查等 OpenOCD 调试工作流。
**参考文件**：`references/heap-budget-stack-trap-2026-05-17.md` — 堆/栈资源预算陷阱：ChibiOS 主线程栈静态 1KB vs RTT 堆分配 64KB 导致 serial RX FIFO 分配失败。包含 `.config` → `rtconfig.h` 构建系统追踪方法。
**参考文件**：`references/phase1-gap-analysis-2026-05-16.md` — Phase 1 完成时全模块对比差距分析（5 个 gap）
**参考文件**：`references/startup-sequence-fix-2026-05-16.md` — HAL_RTT_Class.cpp 5 步启动顺序对齐 ChibiOS 的完整实施记录（3 文件原子化修改）。
**参考文件**：`references/rcoutput-direct-register-access.md` — RCOutput 直接 TIM 寄存器实现（CUAV V5 BSP 无 board.h 导致 drv_pwm.c 不编译，用 CMSIS 寄存器替代 rt_pwm_set()）。
**参考文件**：`references/hwdef-infrastructure-valgpio-dma-resolver.md` — hwdef 基础设施：VAL_GPIO 寄存器宏生成、dma_resolver 约束求解器、ldscript.ld/common.ld/env.py 生成（P0 改造 2026-05-16）

---

## 十二、Phase 完成时系统性回顾检查（2026-05-16 新增）

> **原则**：每个 Phase 做完核心修改后，做一次全模块 ChibiOS 对比回顾，主动发现遗留差距。

### 12.1 回顾触发条件

- ✅ 一个 Phase 的所有原子任务全部完成并验证通过
- ✅ 或在用户要求"看看还有什么问题"时

### 12.2 回顾范围

| 范围 | 说明 |
|------|------|
| Phase 新修改的文件 | 必须逐行对比 ChibiOS 验证正确性 |
| 所有已对比模块 | 快速检查是否有新修改影响已验证模块 |
| 未对比模块 | 按 §11 的方法论补对比 |

### 12.3 回顾输出

每轮回顾产生 `references/phaseN-gap-analysis-YYYY-MM-DD.md`，含：
1. 差距清单表（文件、ChibiOS 行号、影响评级）
2. 模块状态表
3. 修复优先级建议

### 12.4 已知差距（Phase 1 回顾）

详见 `references/phase1-gap-analysis-2026-05-16.md`。

| # | 差距 | 模块 | 评级 | 计划 |
|---|------|------|------|------|
| 1 | Flash::write() 缺边界检查 | Flash | ⬇️低 | Phase 3 随修 |
| 2 | Flash::write() 缺 HSI 检查 | Flash | ⬇️极低 | Phase 3 随修 |
| **3** | **UART::set_options() 存值不执行** | UART | **⬆️中** | **有外设需求再修** |
| 4 | parity/stop_bits/RTS/CTS 空函数 | UART | ⬇️低 | 默认 8N1 够用 |
| 5 | _begin() 缺 baudrate 缓冲计算 | UART | ⬇️低 | 固定 512 字节够用 |

---

## 十三、堆/栈资源预算 — 移植中的关键架构差异

> **核心发现（2026-05-17）**：ChibiOS 主线程栈是**静态分配**（linker script `.pstack` 段），而 RTT 从**堆分配**。RTT 配置的大栈值直接吃掉可用堆。
>
> 详细调试记录 → `references/heap-budget-stack-trap-2026-05-17.md`\n> **2026-05-17 更新**：新增 `Scheduler.cpp 线程栈预算陷阱` 小节 — ap_timer(16384→4096)、ap_io(8192→4096)、storage(8192→4096)。所有 RTT 线程栈从堆分配（vs ChibiOS 静态链接），这是堆耗尽的最隐蔽根因。

### 13.1 ChibiOS 栈参考值

ChibiOS 栈通过 `rules_stacks.ld` 定义，默认值来自 `chibios_board.mk:82`：

| 参数 | 默认值 | 来源 | 
|------|--------|------|
| `USE_PROCESS_STACKSIZE` | **0x400 (1KB)** | `chibios_board.mk:82` |
| `__main_stack_size__` | **0x400 (1KB)** | `rules.mk:50-59` |

两个栈都在 linker script 中静态分配，不消耗堆。

### 13.2 RTT 构建系统 `.config` → `rtconfig.h` 陷阱

**⚠️ 最关键陷阱**：`.config` 来源不是 BSP 目录，而是 **hwdef/common/.config**！

CUAV V5 使用 hwdef 构建模式，`rtt_bsp_deploy.py` 的 `_deploy_hwdef()` 调用 `shutil.copytree(common_template_dir, deploy_dir)` 来部署整个模板目录（含 `.config`）。**BSP 目录的 `.config` 完全不参与构建**。如果修错地方，`rm -rf build/` 后重新编译会再次从源头复制旧值，修改丢失。

构建系统文件链：

```
hwdef/common/.config  (libraries/AP_HAL_RTT/hwdef/common/.config)  ← 真正源头！
  │  rtt_bsp_deploy.py:_deploy_hwdef(): shutil.copytree("libraries/AP_HAL_RTT/hwdef/common", deploy_dir)
  ↓
Deploy dir .config  (build/rtt_deploy/cuav_v5/.config)  ← 副本
  │  _generate_rtconfig() → _simple_config_to_header() [rtt_bsp_deploy.py:296-339]
  ↓
rtconfig.h  (build/rtt_deploy/cuav_v5/rtconfig.h)
  │  write_rtconfig_h() [rtt_hwdef.py:1555] 追加外设使能
  ↓
最终编译 (build/rtt_cuav_v5/rtconfig.h)
```

**正确修改方式**（四选一）：
1. ⭐ **最佳**：直接改源头 `libraries/AP_HAL_RTT/hwdef/common/.config` — 永久生效，clean build 也不丢
2. 直接改 deploy dir 的 `.config` + `rtconfig.h` 并重建（临时方案）
3. `rm -rf build/rtt_deploy/` 后重建（强制重新部署，前提是源头已改正）
4. `sed -i 's/65536/4096/' build/rtt_deploy/cuav_v5/.config`（临时）

**验证生效的方法**（编译后做）：
```bash
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \
  --start-address=0x8105734 --stop-address=0x810574a | grep 'mov.*#'
# 应输出: mov.w r3, #4096  ; 0x1000
# 若输出 mov.w r3, #65536 ; 0x10000 → 修改未生效，重新检查源头
```

**legacy 模式例外**（pixhawk6c_mini）：使用 `bsp_src_rel` 走 `_deploy_legacy()`，此时 `.config` 确实来自 BSP 目录的完整复制。

### 13.3 各板型主线程栈大小参考

| 板型 | 芯片 | RT_MAIN_THREAD_STACK_SIZE | 来源 |
|------|------|--------------------------|------|
| fmuv2 | STM32F427 | **2048** (2KB) | `rtt_bsp_fmuv2/rtconfig.h:120` |
| pixhawk6c_mini | STM32H743 | **2048** (2KB) | `rtt_bsp_pixhawk6c_mini/rtconfig.h:121` |
| CUAV V5 (错误值) | STM32F767 | **65536** (64KB) | 原始 `.config` |
| CUAV V5 (修复值) | STM32F767 | **4096** (4KB) | 当前修复值 |

### 13.4 堆耗尽诊断速查

当 USB 枚举但 0 数据时：

```bash
# 1. GDB halt 检查
arm-none-eabi-gdb -batch -q -ex "target extended-remote :3333" \
  -ex "monitor halt" -ex "p/x \$pc" -ex "info registers"

# PC=0x081071ca + rt_assert_handler → 堆耗尽
# PC=0x080f5f9e + flash_check.c 死循环 → Flash 边界计算错

# 2. 查堆预算
arm-none-eabi-nm -n rt-thread.elf | grep -E "_end|_ebss|_edata|_sdata"
# 堆 = 384KB (SRAM1/2) - (_end - SRAM_BASE) - RT_MAIN_THREAD_STACK_SIZE

# 3. 验证 rtconfig.h 生效
grep "MAIN_THREAD_STACK" build/rtt_deploy/cuav_v5/rtconfig.h
grep "MAIN_THREAD_STACK" build/rtt_deploy/cuav_v5/.config
```

### 13.5 PC 断点特征速查

| PC | 函数 | 症状 | 根因 |
|----|------|------|------|
| `0x081071ca` | `rt_assert_handler` | USB 枚举但无数据 | 堆耗尽 → serial malloc 失败 |
| `0x080f5f9e` | `flash_check.c` | 无 USB 枚举 | Flash 边界使用 SRAM VMA |
| `0x080f5xxx` | USB init | 不枚举 | CherryUSB 初始化失败 |

---

## 十四、ChibiOS vs RTT 全模块架构差异（2026-05-17 逐行读代码后总结）

> **廖博士纠正（2026-05-17）**：不要猜，逐行读 ChibiOS 对应代码，找出真正的架构差异。

### 14.1 线程模型差异

| 维度 | ChibiOS | RTT (RT-Thread) | 影响 |
|------|---------|----------------|------|
| **主线程创建方式** | 初始执行上下文，无显式创建 | `rt_application_init()` → `rt_thread_create("main", ..., stack_size)` | RTT 主线程栈来自堆 |
| **主线程栈来源** | `.pstack` 段（链接脚本静态分配） | `rt_malloc(stack_size)` 堆分配 | RTT 主线程栈消耗堆内存 |
| **主线程栈大小** | `__process_stack_size__ = 0x400` (1KB) | `RT_MAIN_THREAD_STACK_SIZE` (原64KB→修复4KB) | ChibiOS 1KB 静态；RTT 必须谨慎配置 |
| **中断/系统栈** | `.mstack` 1KB | `.stack` **16KB** | RTT 中断栈大 16 倍，多占 ~15KB SRAM |
| **空闲线程** | ChibiOS 空闲钩子（无限循环） | `rt_thread_create()` → 256B 栈 | RTT 空闲线程从堆分配 |
| **定时器线程** | ChibiOS 内部 `vtimer` 线程 | `rt_thread_create()` → 2048B 栈 | RTT 定时器线程从堆分配 |
| **Shell 线程** | 无（ArduPilot 模式下禁用） | `rt_thread_create()` → 4096B 栈（如 FINSH 使能） | RTT 额外消耗 |
| **UART TX 线程** | **1 个线程管所有 UART** (`uart_thread`), 320B 栈 | UARTDriver::_tx_init 创建 per-UART 线程 | RTT 每个 UART 额外消耗堆 |
| **UART RX 线程** | **1 个线程管所有 UART** (`uart_rx_thread`), 768B 栈 | 同 TX（每个 UART 一个线程） | RTT 每 UART 额外消耗堆 |

**核心结论**：RTT 所有线程栈都来自堆（堆分配），ChibiOS 线程栈大多静态分配。RTT 变成本如下：

```
RTT 堆最小消耗 ≈ 主线程(4K) + 定时器(2K) + 空闲(0.25K) + Shell(4K) + 线程结构体(0.2K×4)
               ≈ 11KB 起步
每增加一个 UART 线程 ≈ 栈(320+768) + 结构体(0.2K) ≈ 1.3KB
```

### 14.2 UART 驱动架构差异（关键！）

| 维度 | ChibiOS (UARTDriver.cpp) | RTT (UARTDriver.cpp) | 影响 |
|------|------------------------|---------------------|------|
| **底层驱动** | `BaseSequentialStream*` — 指向 ChibiOS `SerialDriver` SD1/SD2/... | `rt_device_t dev` — 指向 RT-Thread 串口设备 | API 不同，行为需等价 |
| **底层缓冲区** | ChibiOS SerialDriver 结构体内**静态分配**（SDx_BUFSIZE 编译期恒定） | RT-Thread `rt_serial_open` 内部 **`rt_malloc()` 动态分配** RX FIFO | RTT 每开一个串口多一次堆分配 |
| **TX 路径** | `sdWrite()` + DMA（可选）→ ISR 回调 | `rt_device_write()` → `dev_serial.c` TX FIFO/DMA | 功能等价 |
| **RX 路径** | `sdRead()` + `_readbuf` (ByteBuffer) 双缓冲，ISR→_rx_timer_tick→_readbuf | `rt_device_read()` + `_readbuf` (ByteBuffer) | 数据路径等价 |
| **`_readbuf` 分配** | `_begin()` 中 `set_size_best(rxS)` | 同左 — `set_size(txS/rxS)` | 等价（堆分配） |
| **`_writebuf` 分配** | `_begin()` 中 `set_size_best(txS)` | 同左 | 等价 |
| **TX 缓冲区默认** | `HAL_UART_MIN_TX_SIZE = 512` | 无默认（从参数传入） | RTT 需补默认值 |
| **RX 缓冲区默认** | `HAL_UART_MIN_RX_SIZE = 512` | 同 TX | 同 |
| **TX 线程栈** | `HAL_UART_STACK_SIZE = 320` | 无默认定义，使用 rt_thread_create 参数 | RTT 需显式设栈大小 |
| **RX 线程栈** | `HAL_UART_RX_STACK_SIZE = 768` | 同上 | 同上 |
| **UART 数量** | 共享 1 个 RX 线程 + 每个 UART 1 个 TX 线程 | 每个 UART 独立 TX/RX 线程 | RTT 多线程消耗更多堆 |
| **DMA TX 首次尝试** | 硬件自动尝试 DMA | 同左 — `RT_DEVICE_FLAG_DMA_TX` 优先 | 功能等价 |
| **DMA TX 回退** | 尝试 DMA → 失败回落 INT_RX | 同左 — 先尝试 DMA_TX，失败后 INT_RX | 等价 |

**ChibiOS 串口驱动结构体**（`hal_serial_lld.c` 中）：
```c
// SD1 静态缓冲区示例
static uint8_t sd1_tx_buffer[SD1_TX_BUFSIZE];  // 静态分配
static uint8_t sd1_rx_buffer[SD1_RX_BUFSIZE];
static SerialDriver SD1;  // 完整驱动结构体（含缓冲区指针）

// 打开时只用配置寄存器，不用 malloc
sdStart(&SDD1, &sercfg);
```

**RTT 串口打开路径**（`dev_serial.c:670-676`）：
```c
rx_fifo = (struct rt_serial_rx_fifo*) rt_malloc(     // ← 堆分配！
    sizeof(struct rt_serial_rx_fifo) + serial->config.bufsz);
RT_ASSERT(rx_fifo != RT_NULL);  // 此处若失败就是堆耗尽
```

### 14.3 内存布局差异

| 区域 | ChibiOS (fmuv5) | RTT (cuav_v5) | 备注 |
|------|----------------|--------------|------|
| DTCM (0x20000000-0x2001FFFF) | 可用作堆或独立内存 | **未使用** — 只有 `.stack` 段在此区域 | 128KB 浪费 |
| SRAM1 (0x20020000-0x20080000) | .data + .bss + heap (≈ 384KB) | .data + .bss + .stack + heap (384KB) | ChibiOS 不在这里放.stack |
| 总映射范围 | 由芯片 RM 定义，灵活 | `RAM: 0x20000000, LENGTH = 512k` | 512KB 含 DTCM |
| 系统栈 | `.mstack` 1KB (MAIN_STACK_RAM) | `.stack` **16KB** (`_system_stack_size=0x4000`, RAM 段) | RTT 大 16 倍 |
| 主线程栈 | `.pstack` 1KB (PROCESS_STACK_RAM) | **堆内分配** | ChibiOS 不占堆 |
| 堆 | `.bss` 后剩余 RAM | `.bss` + `.sram1_bss` 后剩余 RAM | 语义等价，但 RTT 堆更少 |

**ChibiOS 链接脚本 RAM 段分配**：
```
.data          → DATA_RAM     (SRAM1, 0x20020000+)
.bss           → BSS_RAM      (SRAM1)
.mstack        → MAIN_STACK_RAM   ← 1KB
.pstack        → PROCESS_STACK_RAM ← 1KB
heap           → 剩余 SRAM1 + DTCM
```

**RTT 链接脚本 RAM 段分配**：
```
.data          → RAM (0x20000000+, 含 DTCM)
.stack         → RAM (16KB 在 data 后)
.bss           → RAM (.stack 后)
.sram1_bss     → RAM (.bss 后，强制在 >=0x20020000)
_end           → 此后为堆
```

### 14.4 堆耗尽 debug 流程

当 MCU 启动后 USB 枚举但无数据且 assert 在 `rt_serial_open:676` 时：

```
Step 1: 确认是否是堆耗尽
  GDB: p/x *system_heap
  → used >= total → 堆耗尽

Step 2: 查 main_thread_stack 编译值
  arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \
    --start-address=<rt_application_init> | grep 'mov.*#'

Step 3: 若值为 4096 但仍耗尽 → 非栈问题，查其他大分配
  查 serial0 begin 时 CHERRYUSB 分配
  查是否有 mempool / lwip / DFS 预分配
  查 _end -> system_heap.address 间元数据是否被踩

Step 4: 若值为 65536 → 修改未生效
  查源头: hwdef/common/.config
  查生成链: .config → rtconfig.h 路径是否正确
  清 build/ 后重编

Step 5: 若 used > total (统计异常)
  可能原因:
  · buffer overflow 踩了堆元数据
  · 双重释放导致统计计数错误
  · flash 烧录的仍是旧固件（CRC 验证不一致）
```

### 14.5 验证二进制编译值的多种方法

```bash
# 方法1: 反汇编 rt_application_init — 最可靠
FUNC=$(arm-none-eabi-nm build/.../rt-thread.elf | grep "T rt_application_init" | awk '{print $1}')
arm-none-eabi-objdump -d build/.../rt-thread.elf \
  --start-address=0x$FUNC --stop-address=+0x20 | grep 'mov'
# 输出: mov.w r3, #4096 ; 0x1000  ✅
# 输出: mov.w r3, #65536 ; 0x10000 ❌

# 方法2: 检查 app_descriptor CRC 确认 flash 与 ELF 一致
arm-none-eabi-gdb ... -ex "p app_descriptor"
# 比较 image_crc1, image_crc2 与构建输出

# 方法3: 直接看整个函数
arm-none-eabi-objdump -d build/.../rt-thread.elf \
  --start-address=0x$FUNC --stop-address=+0x30
```
