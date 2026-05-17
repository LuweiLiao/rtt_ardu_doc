---
name: rtt-vs-chibios-reference
description: >-
  RTT ArduPilot 移植与参考实现 ChibiOS HAL 的对比分析。
  用于指导 RTT 移植修复，确保与 ChibiOS 的行为一致。
triggers:
  - "cuav v5 对比"
  - "chibios vs rtt"
  - "RTT 移植对标"
---

# RT-Thread vs ChibiOS ArduPilot HAL 对比知识库

## 0. 系统对比方法论（2026-05-16 建立）

进行 RTT vs ChibiOS 行为级对比时，按以下步骤系统化执行：

### Step 1: 加载已有差距清单
- `skill_view('rtt-vs-chibios-reference')` — 读取已知差距表和修复优先级
- `skill_view('ardupilot-rtt-architecture')` — 读取架构对比和违规清单
- 验证每个列出的差距当前是否已修复（read_file 核对行号）

### Step 2: 模块级对比清单
对照以下结构性要点，逐模块检查：

| 检查项 | 对比方法 | ChibiOS 参考 |
|--------|---------|-------------|
| **锁框架构** | `get_semaphore()` 返回私锁还是总线锁 | `SPIDevice.cpp:338` → `&bus.semaphore` |
| **Bus 线程 take** | 永久阻塞 vs 超时静默 | Device::bus_thread → 永久阻塞 |
| **Bus 线程优先级** | 是否显式传递优先级 | `APM_SPI_PRIORITY = 181` |
| **内锁（transfer）** | 用总线锁还是私有信号量 | 总线锁统一 |
| **Setup 优先级** | 是否降级→signal→setup→恢复 | `HAL_ChibiOS_Class.cpp:265-317` |
| **delay() 行为** | 是否调用 call_delay_cb() | `chThdSleep` 纯睡眠 |
| **_run_io()** | recursion guard + sem 保护 | ChibiOS _run_io |
| **RCInput _timer_tick** | AP_RCProtocol 集成 | 一致 |
| **UART _timer_tick** | 写入/读出缓冲区刷新 | 结构等价 |
| **AnalogIn** | DMA vs 轮询 | LL 库 + DMA |

### Step 3: 验证 → 修复 → 更新 skill
1. 对每个未对齐项：确认当前源码行号 + ChibiOS 参考行号
2. 修改代码 + 编译验证
3. 更新本 skill 中的状态表
4. 提交 commit，在 skill 中记录 commit hash

### ✅ 已覆盖的模块（全部完成对比）
- ✅ SPIDevice — 锁架构、优先级、get_semaphore、动态BR
- ✅ I2CDevice — 锁架构、CMSIS硬件驱动、get_semaphore
- ✅ DeviceBus — 线程阻塞策略、栈大小
- ✅ Semaphores — take_blocking、递归锁
- ✅ Scheduler — delay()、_run_io()、优先级管理、DWT boost 三层架构
- ✅ HAL_RTT_Class — 启动顺序、setup 优先级（5步对齐）
- ✅ RCInput — AP_RCProtocol 集成
- ✅ RCOutput — 基础 PWM（高级功能待 Phase 3）
- ✅ GPIO — sensor_power init + usb_connected() 硬件直查
- ✅ AnalogIn — ADC DMA NDTR 轮询（Phase 2，功能等价于 ChibiOS DMA ISR）
- ✅ UARTDriver — 完整对比（线程模型/写路径/读路径/USB状态检查）
- ✅ Storage — 脏行写回模式逻辑一致，FRAM+Flash双后端
- ❌ SoftSigReader — Phase 3 缺失
- ❌ RCOutput_iofirmware — Phase 3 缺失（依赖 IOMCU）
- ❌ Shared_DMA — Phase 3 缺失
- ❌ CAN 驱动 — Phase 3 缺失
- 🟡 MCU温度/Vrefint监测 — 诊断功能缺失，不影响飞行（ChibiOS 20Hz读取TS_CAL1/2）

## 1. 启动顺序对比

### ChibiOS 启动链
```
复位 → __early_init() [PAL表加载PE3=HIGH] → __late_init() [halInit+chSysInit]
→ main() → main_loop() → usb_initialise() → serial begin → setup()
```

### RTT 启动链（修复后）
```
复位 → Reset_Handler [DCache关+FPU直写+VTOR] → SystemInit [CPACR]
→ entry() → rtthread_startup → rt_hw_board_init() [PE3断言]
→ scheduler start → main_thread → rt_components_init [USB+SPI设备]
→ main() → HAL_RTT::run() → serial begin → setup()
```

## 2. 启动阶段寄存器初始化（完整对比 — 2026-05-13 更新）

### Cortex-M7 启动与 CP15 NOCP 限制

**关键发现（2026-05-13）**：STM32F767 上 `mrc p15` 和 `mcr p15` 协处理器指令**触发 NOCP HardFault**，
即使 CPACR=0x00F00000。ARM Cortex-M7 对 CP15 接口支持有限，所有系统控制寄存器
必须通过内存映射（SCB 地址空间 0xE000EDxx/0xE000EFxx）读写。

### 启动时需初始化的寄存器清单

| 寄存器 | 地址 | 值 | 说明 |
|--------|------|-----|------|
| SCB_CPACR | 0xE000ED88 | 0x00F00000 | 使能 FPU (CP10+CP11) |
| SCB_FPCCR | 0xE000EF34 | 0xC0000000 | ASPEN+LSPEN，自动 FPU 上下文保存 |
| SCB_SCTLR | 0xE000ED30 | bit12=1 (I-Cache), bit9=0 (D-Cache off) | 指令缓存开，数据缓存关 |
| SCB_ICIALLU | 0xE000EF50 | 0 | 整块 I-Cache 无效化 |
| SCB_DCCSW | 0xE000EF5C | 0 | 整块 D-Cache 清除（如果 D-Cache 之前开启） |
| FPSCR | 特殊（CPU 寄存器） | 0 | 清零浮点状态寄存器（vmsr 指令） |

### ChibiOS vs RTT 启动顺序对比

ChibiOS 参考 `crt0_v7m.S` 的正确做法：

```asm
/* ChibiOS 方式：全部 STR + DSB/ISB，零 MRC/MCR */
ldr   r0, =0x00F00000
ldr   r1, =0xE000ED88    /* SCB_CPACR */
str   r0, [r1]
dsb
isb
mov   r0, #0
vmsr  FPSCR, r0          /* canary: 若 FPU 未使能，此处 NOCP */
/* 设置 CONTROL.FPCA */
mrs   r0, CONTROL
orr   r0, r0, #0x04
msr   CONTROL, r0
isb
```

**关键差异**：
- ChibiOS 在 `_crt0_entry` 中直接 STR+DSB/ISB，不依赖 C 代码
- RTT 原版依赖 `SystemInit()` 中的 `SCB->CPACR |= ...` 无 DSB/ISB
- **bootloader 开启 D-Cache 后**，CPACR 写操作被缓存吞没 → NOCP HardFault
- **修复**：Reset_Handler 中先关 D-Cache+全清，再直写 CPACR+DSB/ISB

## 3. PE3 传感器电源（已验证正确）

| 属性 | ChibiOS | RTT |
|------|---------|-----|
| **GPIOE 基址** | 0x40021000 | 0x40021000（跟 F4 不同！） |
| **使能时机** | `__early_init()` PAL 表 | `rt_hw_board_init()` 末尾 |
| **PE3 MODER** | 0b01 = OUTPUT | 0b01 = OUTPUT ✅ |
| **PE3 ODR** | 1 = HIGH | 1 = HIGH ✅ |
| **使能方法** | PAL 框架自动加载 | 直接 GPIOE->MODER/BSRR 写 |

> **注意**：之前读 0x40001800 是错误的——那是 F4 的地址。F7 的 GPIOx 在 0x4002xxxx。

## 4. SPI 总线速度差异（重要）

| 设备 | ChibiOS 低速 | ChibiOS 高速 | RTT（当前） |
|------|-------------|-------------|-------------|
| SPI1 (ICM20689) | **~1.68 MHz** (BR=5) | **~6.75 MHz** (BR=3) | 固定 **6.75 MHz** (BR=3) |
| SPI4 (MS5611) | **~1.68 MHz** (BR=5) | **~13.5 MHz** (BR=2) | 固定 **13.5 MHz** (BR=2) |

**RTT 问题**：`spi1_poll_transfer()` 和 `spi4_poll_transfer()` 硬编码了 BR 分频，忽略 hwdef.dat 的 lowspeed/highspeed 配置。IMU 初始化时应使用 1.68MHz 低速读取 WHO_AM_I，初始化完成后切换到 6.75MHz 高速。

## 5. IMU 数据采集线程模型（关键差异）

### ChibiOS 方式
```
[SPI1 总线线程] Prio=181
  └─ 持有 SPI 信号量 → _poll_data() → _read_fifo() → FIFO 批量读取 → 释放信号量

[Timer 线程] Prio=181  
  └─ 负责 1kHz 节拍、模拟量采样

[Main 线程] Prio=180
  └─ 主循环
```

### RTT 方式（当前）
```
[Timer 线程] Prio=4
  └─ 负责所有定时器回调（含 IMU 的 periodic callback）
  └─ 但 IMU callback 持有 SPI1 信号量 → 与主线程竞争

[Main 线程] Prio=6
  └─ ins.init() → init_gyro() → delay() 等待样本
```

**核心差距**：
- ChibiOS 有**独立的 SPI 总线线程**，跟 timer 线程解耦
- RTT 把所有回调塞进 timer 线程，且 timer 线程也在处理 ADC、storage 等
- 当 `init_gyro()` 调用 `delay()` 等待样本时，timer 线程需要运行来产生样本，但 RTT 的 `delay()` 实现可能没有正确让出 CPU 给 timer 线程

## 6. init_gyro() 超时机制

### `_init_gyro()` 详细逻辑 (AP_InertialSensor.cpp:1686)

| 参数 | 值 |
|------|-----|
| 收敛判据 | 连续两次 50 样本均值差 < **0.1°/s** |
| 每批样本数 | 50 |
| 每批耗时 | ~250ms（理想） |
| **最大迭代** | **120 次**（`j <= 30*4`） |
| **最大耗时** | **~30 秒**（理想情况） |
| 超时后 | 使用最佳偏置值，标记 `_gyro_cal_ok = false` |
| **不会阻塞系统启动** | 超时后继续执行 |

### ⚠️ 关键陷阱：迭代次数超时不足以应对 RTT 调度延迟

**问题**：该超时是**基于迭代次数的**（120 次），而不是基于**墙上时钟时间的**。如果每次迭代的实际耗时 > 250ms，总耗时会远超 30 秒。

```cpp
// AP_InertialSensor.cpp:1751 — 循环条件
for (int16_t j = 0; j <= 30*4 && num_converged < num_gyros; j++) {
    // 内循环：50 × update() + delay(5) — 理想 250ms
    for (i=0; i<50; i++) {
        update();
        hal.scheduler->delay(5);   // ← 在 RTT 上可能远 >5ms！
    }
}
```

**实际耗时分解**：

| 组件 | 理想 | 实际（RTT 调度延迟） |
|------|------|---------------------|
| `delay(5)` | 5ms | 5-50ms（取决于线程抢占） |
| `update()` | <1ms | 1-10ms（等待 SPI 数据） |
| **每次迭代实际** | **~250ms** | **300-3000ms** |
| **120 次总计** | **~30s** | **~36-360s** |

**为什么 `delay(5)` 可能不是 5ms**：

`Scheduler::delay(5)` 的实现：
```cpp
void Scheduler::delay(uint16_t ms) {
    uint64_t start = AP_HAL::micros64();
    while ((AP_HAL::micros64() - start) / 1000 < ms) {
        delay_microseconds(1000);  // 每次 sleep 1 tick
        if (_min_delay_cb_ms <= ms) {
            if (in_main_thread()) call_delay_cb();
        }
    }
}
```

`delay_microseconds(1000)` 对于 RT_TICK_PER_SECOND=1000：
```cpp
const uint32_t tick_us = 1000000U / RT_TICK_PER_SECOND; // = 1000
if (tick_us == 0 || us < tick_us) { ... }  // us=1000, tick_us=1000 → false
const rt_tick_t whole_ticks = us / tick_us;  // = 1 tick
rt_thread_delay(whole_ticks);                // sleep 1 tick ≈ 1ms
```

**结论**：`delay_microseconds(1000)` 调用 `rt_thread_delay(1)`，这应该只花 ~1ms。但整个 `delay(5)` 循环检查 `micros64() - start`，而 `micros64()` 使用 DWT 计数（硬实时）。所以每个 `delay(5)` 应该真正只花 ~5ms。

**但**：`update()` 调用 `_wait_for_sample()`，在 RTT 上可能等待多个 ticks 才有新数据。**这才是真正的耗时放大器。**

### 🔧 修复方案：添加墙上时钟超时（2026-05-12 计划）

在 `_init_gyro()` 的循环中添加绝对时间检测，不受迭代耗时波动影响：

```cpp
uint32_t gyro_init_start_ms = AP_HAL::millis();
for (int16_t j = 0; j <= 30*4 && num_converged < num_gyros; j++) {
    // == 新增的墙上时钟超时 ==
    if (AP_HAL::millis() - gyro_init_start_ms > 35000) {
        DEV_PRINTF("gyro init timed out after 35s\n");
        break;
    }
    // ... 原有循环体
}
```

**为什么 `AP_HAL::millis()` 不会也卡住**：`Util::get_millis()` 读的是 `rt_tick_get()`：
```cpp
uint32_t Util::get_millis() const {
    const rt_tick_t tick = rt_tick_get();
    return (uint32_t)((uint64_t)tick * 1000ULL / RT_TICK_PER_SECOND);
}
```
SysTick 中断必须运行才能推进系统 → 如果 tick 不增加，系统本身也完全卡死。所以 `millis()` 超时是可靠的保底。

## 7. USB CDC 数据流

### 写入路径 (ChibiOS vs RTT)

| 层级 | ChibiOS | RTT |
|------|---------|-----|
| hal.console→printf | UARTDriver _writebuf | UARTDriver _writebuf |
| 刷新机制 | timer tick 定时刷新 | uart thread 定时刷新 |
| USB 驱动 | SDU1 (ChibiOS USB) | CherryUSB cdc_acm |
| 未配置时 | 数据在 _writebuf 排队 | 数据在 _writebuf 排队（但 CherryUSB tx_rb 被重置） |

**关键结论**：如果 `init_gyro()` 卡住时 USB 枚举已完成，应当能看到输出。
如果看不到 TTY 输出，说明卡住发生在 USB 枚举之前或 `hal.serial(0)->begin()` 之前。

## 8. 待解决的关键问题（2026-05-16 更新）

### ⚠️ 已知但未解决的问题

1. **Phase 3 缺失模块**：SoftSigReader（PPM/ICU 脉冲捕获）、RCOutput_iofirmware（IOMCU协同处理器输出）、Shared_DMA（DMA冲突仲裁）、CANIface（CAN1/CAN2驱动）。均在 `rtt-porting-phase-plan` skill 中规划为 Phase 3，不影响当前 bench 验证。

2. **RTT 主循环率 < 400Hz（当前 ~263Hz）**：纯软件层面的权衡。定量分析见 `ardupilot-rtt-architecture` skill 的 §正确修复路径速查。SPI DMA 仅贡献 ~4% 改善（263→275Hz），真正瓶颈在调度器/定时器抢占。

3. **App descriptor 缺失**：RTT 链接脚本缺少 `.app_descriptor` section，导致 ArduPilot bootloader 从独立复位时不跳转应用。当前通过 PX4 uploader 协议烧录绕过（`scons --upload`）。

### ✅ 已解决的关键问题

| 问题 | 解决时间 | 关键动作 |
|------|---------|---------|
| init_gyro() 卡住 60s+ | Phase 1 | SPI 锁对齐 + Bus 线程永久阻塞 + setup 优先级降级 |
| SPI 低速/高速切换 | Phase 2 | `_speed_high` + 动态 BR（cs_take 时重配） |
| 独立 SPI 总线线程 | Phase 1 | `DeviceBus` 为每个 bus 创建独立线程 |
| **I2CDevice 私有锁** | **Phase 2** | **get_semaphore() 改为返回总线锁，transfer() 内锁统一** |
| **SPIDevice 无用 `_sem`** | Phase 2 | **移除了 SPIDevice.h 中残留的成员声明** |

## 9. RTT vs ChibiOS 驱动对齐修复清单（持续更新）

### 统一锁模型：所有总线设备使用总线级信号量

**机制**：
- `AP_HAL::Thread` 的 `get_semaphore()` 是外部用户（如 `WITH_SEMAPHORE`）获取总线锁的唯一途径
- **必须返回总线级**信号量（`DeviceBus.semaphore`），而非 per-device 私有信号量
- ChibiOS 中 `SPIDevice::get_semaphore()` 和 `I2CBus::get_semaphore()` 都使用 `bus.semaphore`
- 该规则适用于所有通过 DeviceBus 管理的总线设备：SPI、I2C、未来添加的总线类型

| 模块 | 修复状态 | commit | ChibiOS 参考 | RTT 当前代码 |
|------|---------|--------|-------------|-------------|
| **SPIDevice** get_semaphore | Phase 1 已修 | `878e7f1f2b` | `SPIDevice.cpp:338` → `&bus.semaphore` | `SPIDevice.cpp:643` → `&_bus->semaphore` |
| **SPIDevice.h** 清理无用 `_sem` 成员 | 2026-05-16 已修 | `bfe648f60c` | 无对应（ChibiOS 无 per-device sem） | 已移除 |
| **I2CDevice** get_semaphore | 2026-05-16 已修 | `bfe648f60c` | `I2CBus::get_semaphore()` → `&bus.semaphore` | `I2CDevice.cpp:302` → `&_bus_dev->semaphore` |
| **I2CDevice** transfer() 内锁 | 2026-05-16 已修 | `bfe648f60c` | 总线锁统一 | `_bus_dev->semaphore.take/give` |
| **I2CDevice.h** 清理无用 `_sem` 成员 | 2026-05-16 已修 | `bfe648f60c` | 无对应 | 已移除 |

**修复验证**（I2CDevice）：
```cpp
I2CDevice::get_semaphore()
{
    // Align with ChibiOS: return bus-level semaphore
    return &_bus_dev->semaphore;
}
```

**影响**（修复前）：
```cpp
WITH_SEMAPHORE(_dev->get_semaphore()) {  // 拿私有锁，总线锁未持有
    // 其他总线设备可在此间隙插入操作！
}
```
**修复后**：`WITH_SEMAPHORE` 拿到总线锁 → 同总线所有设备互斥。

### [P1] Bus 线程 `take()` 超时（Phase 1 已修）
- **RTT（修复前）**：`DeviceBus.cpp:49` — `binfo->semaphore.take(10)`（10ms 超时）
- **RTT（修复后）**：`binfo->semaphore.take(HAL_SEMAPHORE_BLOCK_FOREVER)`
- **ChibiOS**：永久阻塞
- **影响**：旧超时导致 Bus 线程**静默跳过** callback → IMU 样本丢失

### [P2] Bus 线程优先级对齐（Phase 1 已修）
- **RTT**：`DeviceBus::get_bus(desc.bus, APM_RTT_SPI_PRIORITY)` — 显式传 prio=4
- **ChibiOS**：`APM_SPI_PRIORITY = 181`（高于主线程 180）
- 已在 `SPIDevice.cpp` 创建总线时以 `APM_RTT_SPI_PRIORITY` 启动

### [P3] 其他可对比项（功能等价已验证）

| 项目 | ChibiOS | RTT | 状态 |
|------|---------|-----|------|
| SPI 传输方式 | DMA + 线程挂起 | 寄存器轮询 | ✅ 功能等价 |
| Scheduler delay() | `chThdSleep` 纯睡眠 | `rt_thread_delay` + DWT忙等 | ✅ 功能等价 |
| Bus 线程栈大小 | 静态编译期 1024 | heap 分配 2048 | ✅ 已验证通过 |
| Startup 优先级降级 | `APM_STARTUP_PRIORITY=10` | `APM_RTT_STARTUP_PRIORITY=15` | ✅ 语义一致 |
| `_run_io()` 回调执行 | chBSemWait 保护 | `_io_sem.take_blocking()` 保护 | ✅ 结构一致 |

## 10. IOMCU UART8 对比（2026-05-15 新增）

### 配置对照

| 项目 | ChibiOS fmuv5 | RTT cuav_v5 | 一致？ |
|------|---------------|-------------|--------|
| UART8 引脚 | PE0(RX)/PE1(TX) AF8 | PE0(RX)/PE1(TX) AF8 | ✅ |
| GPIO 上拉 | PULLUP | PULLUP | ✅ |
| GPIO 速度 | VERY_HIGH | VERY_HIGH | ✅ |
| UART8 时钟 | `__HAL_RCC_UART8_CLK_ENABLE()` | 同左 (HAL_MspInit) | ✅ |
| 串口设备名 | N/A (ChibiOS SDU 架构) | `"uart8"` | ✅ (已注册) |
| 波特率 | 1.5Mbps (AP_IOMCU) | 1.5Mbps (AP_IOMCU) | ✅ |
| IO firmware | `ROMFS io_firmware.bin` | 同左 | ✅ |
| DMA 优先级 | `DMA_PRIORITY SDMMC* UART8* ADC* SPI* TIM*` | 同左 | ✅ |
| IOMCU_UART | `IOMCU_UART UART8` | 同左 | ✅ |
| USART6_TX | **注释掉**——"leave as input to prevent pullup on IOMCU SBUS input" | ✅ 应同样注释 | 需确认 |

### 行为差异

**ChibiOS IOMCU 启动流程**：
```
main() → AP_IOMCU::init() → uart.begin(1.5M) → check_crc() → read_registers()
→ bootloader sync (115200) → firmware upload → reboot → 正常 IOMCU 通信
```

**RTT IOMCU 启动流程（当前问题）**：
```
main() → AP_IOMCU::init() → uart.begin(1.5M) → check_crc() → read_registers()
→ UART `rt_sem_take()` 超时 → 无应答 → upload_fw() 也失败 → 线程循环超时
```

### 多线程采样法（定位 IOMCU 阻塞）

当 CDC 枚举但无 MAVLink 心跳时，IOMCU 线程可能是阻塞根源：

```bash
for i in 1 2 3; do
  arm-none-eabi-gdb -batch \
    -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
    -ex "target extended-remote :3333" \
    -ex "set remotetimeout 2" \
    -ex "monitor halt" \
    -ex "bt 5" \
    -ex "monitor resume" \
    -ex "quit" 2>&1 | grep "^#"
done
```

如果 sample 中出现以下调用链 → IOMCU UART 超时已确认：
```
#0  rt_sched_thread_get_stat (scheduler_comm.c:109)
#1  rt_thread_suspend_to_list (thread.c:951)
#2  rt_thread_suspend_with_flag (thread.c:1050)
#3  _thread_sleep (thread.c:668)
#4  rt_thread_mdelay (thread.c:790)
#5  RTT::UARTDriver::wait_timeout (UARTDriver.cpp:399)
#6  AP_IOMCU::read_registers (AP_IOMCU.cpp:680)
#7  AP_IOMCU::read_status (AP_IOMCU.cpp:506)
#8  AP_IOMCU::thread_main (AP_IOMCU.cpp:345)
```

### 可能根因（与 ChibiOS 无关的硬件问题）

配置完全一致的情况下，IOMCU 不响应很可能是：
1. **IOMCU 协处理器未上电** — CUAV V5 的 STM32F0 在独立载板上，桌面调试时排线未接
2. **IOMCU flash 空白** — 从未烧录 bootloader，或之前被擦除
3. **USART6_TX 干扰** — 如果 USART6_TX (PG14) 未按 ChibiOS 方式注释掉，其输出可能干扰 IOMCU SBUS 输入

### 临时绕过

在 hwdef.dat 中注释掉 `IOMCU_UART UART8` → `HAL_WITH_IO_MCU` 不编译 → 系统跳过 IOMCU 初始化，快速进入主循环。

## 11. ADC AnalogIn Timer Tick 开销对比（2026-05-14 会话发现）

### 关键差异：ChibiOS 使用 ADC+DMA + 硬件触发，RTT 使用寄存器轮询

| 对比维度 | ChibiOS | RTT |
|---------|---------|-----|
| ADC 驱动方式 | LL 库 + DMA | CMSIS 寄存器轮询 |
| 采样触发 | 定时器硬件触发 | 定时器线程软件触发 |
| EOC 检测 | DMA 中断（无需轮询） | 轮询 ADC 状态寄存器（1000 次循环） |
| 每通道耗时 | <1µs（DMA 后台进行） | ~370µs（含 EOC 轮询 + HAL_Delay） |
| 8 通道总耗时 | ~8µs | ~3-5ms |
| 对主循环影响 | 基本无（<1% CPU） | 极大（定时器占 50%+ CPU） |

### RTT 当前实现分析（AnalogIn.cpp）

```cpp
// AnalogIn.cpp:78 — EOC 轮询循环
static uint16_t adc_read_channel(ADC_HandleTypeDef *hadc, uint32_t channel)
{
    // ChibiOS 使用 DMA 后台采集，这里没有直接对应代码
    ADC1->SQR3 = channel;          // 设置通道
    ADC1->CR2 |= ADC_CR2_SWSTART;  // 软件启动转换
    uint32_t timeout = 1000;
    while (!(ADC1->SR & ADC_SR_EOC) && timeout--) {
        // DWT 或寄存器读 — 不 yield CPU！
    }
    return ADC1->DR;               // 读数据
}
```

**与 ChibiOS 的对标**：
- ChibiOS 在 `AnalogIn.cpp` 的 `_timer_tick()` 中调用 `adcRead()`（LL 库），LL 库使用 DMA + 回调中断
- DMA 传输完成后自动更新 `adcgrp->samples[]`，`_timer_tick()` 直接读采样数组，不阻塞
- RTT 的寄存器轮询方式在 8 通道 × 每个通道 1000 次轮询 + HAL_Delay 稳定延迟 → 3-5ms

### 对主循环率的实证影响

| 场景 | 定时器占用 CPU | 主循环可用 CPU | 主循环率 |
|------|---------------|---------------|---------|
| ChibiOS (DMA ADC) | ~1% | ~99% | 400Hz ✅ |
| RTT (轮询 ADC, 无优化) | ~50-80% | ~20-50% | 11Hz ❌ |
| RTT (减少通道数) | ~30% | ~70% | ~100Hz ⚠️ |
| RTT (DMA ADC 改造后) | ~1% | ~99% | 400Hz 🎯 |

### 诊断方法

```bash
# 验证是否是 ADC 定时器开销导致低循环率
echo -e "halt\nr 15\nresume" | nc -q 2 localhost 4444
# 多次采样：PC 多次落在 AnalogIn.cpp:78 → 确认

# 查看定时器线程栈
echo -e "halt\nthreads\nresume" | nc -q 2 localhost 4444
# 检查 timer 线程的 PC
```

### 修复方向（优先级：P3 — 不影响功能，只影响性能）

1. **减少 EOC 轮询次数**：500 次足够（500 × ~100ns = 50µs 仍可稳定检测 EOC）
2. **ADC DMA 改造**：参考 ChibiOS stm32_hal_legacy 写法，将 ADC 改为 DMA 后台采集
3. **降低 timer 线程频率**：从 1kHz 降到 500Hz 或更低（不影响 IMU 采样频率）

### 症状

主循环运行在 **7-8 Hz**（目标 400 Hz），`rtt_dbg_loop_time_us ≈ rtt_dbg_work_time_us ≈ 82ms`，循环过载率 96%。

### Bug ①：sub-tick 延迟使用 `rt_thread_delay(1)` 导致定时漂移

**文件**：`libraries/AP_HAL_RTT/Scheduler.cpp:486`（`delay_microseconds_boost()`）

```cpp
// ❌ 修复前：us < tick_us 时执行 1ms 睡眠
if (us < tick_us) {
    rt_thread_delay(1);  // ← 最小 1ms，即使 wait_usec=100µs
    return;
}
```

**根因**：当 `wait_for_sample()` 调用 `delay_microseconds_boost(wait_usec)` 且 `wait_usec < 1000µs` 时，该函数实际睡了 **1 整 tick**（1ms），比预期多 500-900µs。每循环积累 0.5-0.9ms 的过量时间，导致 `_next_sample_usec` 不断超前于墙上时钟。最终 `_next_sample_usec - now` 超过 `_sample_period_usec`，触发「long overshoot」重设——`_next_sample_usec = now + _sample_period_usec`——但重设时`wait_usec` 又小于 1ms，又进入 1ms 睡眠，陷入恶性循环。

**ChibiOS 参考**：`delay_microseconds_boost()` 使用 `chThdSleepMicroseconds()` 或 `chThdBusySpin()`，对 <1000µs 的延迟使用忙等（不进入睡眠状态），不存在 1ms 最小睡眠。

**修复**：
```cpp
// ✅ 修复后：sub-tick 用 DWT 忙等，精确到微秒
if (us < tick_us) {
    _delay_microseconds_dwt(us);  // 精确忙等，不睡眠
}
```

### Bug ②：priority boost 永不解锁

**文件**：`libraries/AP_HAL_RTT/Scheduler.cpp:474`（`delay_microseconds_boost()`）

```cpp
// ❌ 修复前：_priority_boosted 第一次设为 true 后永不恢复
if (!_priority_boosted && in_main_thread()) {
    // ... boost priority to APM_RTT_MAIN_BOOST (=3)
    _priority_boosted = true;  // ← 永远不会变成 false
    _called_boost = true;
}
// ... do delay ...
// 没有恢复优先级的代码！
```

**根因**：`boost_end()` 只在 `expect_delay_ms()` 中被调用，而 `expect_delay_ms()` 只在 flash 写入等场景被调用——正常主循环中从未调用。导致第一次 boost 后，主线程永远运行在 prio=3（高于 timer/SPI 的 prio=4）。

**ChibiOS 参考**：ChibiOS 中 priority boost 通过 `chSysLock()`/`chSysUnlock()` 自动作用域化——boost 只在函数调用期间有效。`delay_microseconds_boost()` 返回后优先级自动恢复。

**修复**：
```cpp
// ✅ 修复后：延迟结束后立即恢复优先级
if (should_boost) {
    _priority_boosted = false;
    // restore priority to APM_RTT_MAIN_PRIORITY (=5)
}
```

注意：`_called_boost` 仍然设为 `true`，使主循环跳过 `delay_microseconds(50)`（L210 的检查），保持 loop 紧凑。

### 验证结果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| **循环率** | **7-8 Hz** | **263 Hz**（~33x 改善） |
| **循环耗时** | **82ms** | **~3.8ms** |
| **冷启动时间** | ~22-25s | **~3.9s** |
| **CDC 枚举** | ✅ | ✅ |
| **MAVLink 心跳** | ✅ | ✅ |

## 13. UARTDriver 完整对比（2026-05-16 新增）

### 架构差异

| 维度 | ChibiOS | RTT | 等价？ |
|------|---------|-----|--------|
| **TX 线程模型** | 每端口独立 uart_thread（事件驱动 + 1ms 超时轮询） | 全局 uart_thread（1kHz 轮询 10 个端口） | ✅ RTOS 框架差异 |
| **RX 线程模型** | 全局 uart_rx_thread（1kHz 轮询所有驱动） | 回调式：rt_device_set_rx_indicate + usb_rx_bridge | ✅ RT-Thread 设备框架原生 |
| **写函数** | 弹跳缓冲 iovec → chnWriteTimeout(TIME_IMMEDIATE) | 弹跳缓冲 → rt_device_write() | ✅ 行为相同 |
| **读函数** | chnReadTimeout(TIME_IMMEDIATE) → readbuf | rt_device_read() → readbuf | ✅ 行为相同 |
| **USB 连接检测** | `USB_ACTIVE` 状态检查 + `set_usb_connected()` | `usb_device_is_configured(0)` 硬件直查 | ✅ RTT 更直接 |
| **USB 写失败恢复** | `sduSOFHookI()` SOF 级重试 | CherryUSB TX timeout + 500 tick 缓冲清空 | ⚠️ 不同机制，已在 rtt-cdc skill 记录 |

### RTT GPIO 的 set_usb_connected() 缺失分析

**ChibiOS GPIO**：`set_usb_connected()` 设置 `_usb_connected = true` 缓存标志，UARTDriver 每次 `_timer_tick` 调用此函数。

**RTT GPIO**：没有 `set_usb_connected()` 方法。`usb_connected()` 直接调用 `usb_device_is_configured(0)` 查询硬件状态。

**结论**：RTT 的实现反而更好——实时查询硬件，不会出现缓存标志过期问题。无需 `set_usb_connected()`。

### ChibiOS UARTDriver 特有的 DMA 功能（RTT 无对应）

- **DMA TX**：`write_pending_bytes_DMA()` 使用 Shared_DMA 通道共享仲裁 + 碰撞自适应降速（contention_counter 机制）
- **DMA RX**：双弹跳缓冲（rx_bounce_buf[2]），IDLE 中断暂停 DMA 重读模式
- **自动流控**：`FLOW_CONTROL_AUTO` 模式通过统计 `used vs total_written` 的差值判断需要使能硬件流控
- **半双工**：`HDSEL` 位切换 + 发送完成事件监听

这些功能在 RTT 上要么因 RTOS 架构差异不需要（DMA 仲裁），要么已通过其他机制等价实现（USB 流控已写在 `_timer_tick` 中）。

## 14. AnalogIn 完整对比（2026-05-16 新增）

### 采样架构

| 对比项 | ChibiOS | RTT |
|--------|---------|-----|
| 驱动方式 | ChibiOS HAL ADC LL 库 + DMA | CMSIS 寄存器直接操作 |
| 中断 | DMA 传输完成中断（adccallback） | 无中断，_timer_tick 轮询 NDTR |
| 缓冲 | DMA 双缓冲（adcsample_t samples[]） | SRAM1 单环形缓冲区（`.sram1` 节） |
| 累计方式 | ISR 中 `sample_sum[index][j] += *buffer` | `_timer_tick` 中 `_dma_accum[ch] += buf[i]` |
| 100Hz 门控 | `delta_t < 10000` 跳过 | `now - _last_timer_tick < 10000` 跳过 |
| 通道数 | 动态（hwdef.dat 配置） | 硬编码 8 通道 |
| 电源状态 | `update_power_flags()` 硬件 GPIO 引脚读取 | ADC 电压阈值估算（无 VBUS_VALID 引脚） |

### MCU 温度/Vrefint 监测（RTT 缺失）

**ChibiOS**（AnalogIn.cpp:743-769）：
- 20Hz 从工厂校准地址读取 `TS_CAL1(0x1FF1E820)`, `TS_CAL2(0x1FF1E840)`, `VREFINT_CAL(0x1FF1E860)`
- 计算 `_mcu_temperature = ((110-30)/(TS_CAL2-TS_CAL1)) * (accum/avg - TS_CAL1) + 30`
- 计算 `_mcu_voltage = 3.3 * VREFINT_CAL / (accum/avg)`
- ADC3 的 Vrefint 通道（ch19）和温度传感器通道（ch18）由 `adcSTM32EnableVREF()` 和 `adcSTM32EnableTS()` 使能

**RTT**：完全缺失。不上报 `_mcu_temperature` 和 `_mcu_voltage`。

**影响**：仅在 `@SYS/status.txt` 和 MAVLink NAMED_VALUE_FLOAT 中作为诊断信息使用，**不影响飞行安全**。

### 功能等价性

- ✅ 100Hz 采样率门控一致
- ✅ 累计+平均算法等价（累计→读取→清零）
- ✅ Board voltage 输出一致（RTT 从通道 5 估算，ChibiOS 从 ADC 配置读取）
- ✅ D-Cache 处理一致（SCB_InvalidateDCache_by_Addr）
- 🟡 MCU 温度/Vrefint — 缺失（诊断功能，不影响飞行）

## 15. Storage 完整对比（2026-05-16 新增）

### 脏行写回逻辑对比

| 步骤 | ChibiOS | RTT | 一致？ |
|------|---------|-----|--------|
| 空检查 | `_dirty_mask.empty()` → `_last_empty_ms` | 同上 | ✅ |
| 找脏行 | `for(i=0;i<CH_STORAGE_NUM_LINES;i++)` | `for(i=0;i<RTT_STORAGE_NUM_LINES;i++)` | ✅ |
| 拷贝 | WITH_SEMAPHORE(sem) 保护下 memcpy | `_sem.take_blocking()` 保护下 memcpy | ✅ |
| 写后端 | FRAM.write / Flash_write / SDCard write | FRAM.write(读回校验) / Flash_write | ✅ |
| 清除脏标 | memcmp 检查是否重脏后 clear | 同上（memcmp后 clear） | ✅ |

### 后端支持对比

| 后端 | ChibiOS | RTT |
|------|---------|-----|
| FRAM | HAL_WITH_RAMTRON | ✅ HAL_WITH_RAMTRON（写后读回校验增强） |
| Flash | STORAGE_FLASH_PAGE | ✅ STORAGE_FLASH_PAGE |
| SDCard | USE_POSIX（posix文件fallback） | ❌ 未实现（桌面实验通常无SD卡） |
| Stub | memset(0xFF) | ✅ 同左 |

### 结论

- 核心脏行写回逻辑完全一致
- RTT 在 FRAM 后端增加了读回校验（3次尝试，失败则 fallback），比 ChibiOS 更稳健
- SDCard 后端未实现，但不影响桌面验证（CUAV V5 在 bench 上无 SD 卡）


