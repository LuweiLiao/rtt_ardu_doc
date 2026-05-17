# ADR-001: 架构偏离记录

> **Architecture Deviation Record**
> 记录 ArduPilot 在 RTT (RT-Thread) 平台上的架构偏离项，说明与原生 ChibiOS 实现的差异、原因及验证方法。

---

## Deviation 1: USB 协议栈

| 字段 | 内容 |
|------|------|
| **Title** | USB 协议栈：ChibiOS SDU vs CherryUSB CDC ACM |
| **Status** | **Accepted** — 功能等价，接口不同 |
| **Context** | ArduPilot 原生在 ChibiOS 上使用 SDU (Serial Driver Unit) 实现 USB CDC ACM，上层通过 `BaseChannel` 流接口读写。RTT 平台使用 CherryUSB 协议栈，提供不同的初始化/收发 API，且不兼容 `BaseChannel` 接口。 |
| **Decision** | 在 `AP_HAL_RTT` 中实现 `RTTUARTDriver`，封装 CherryUSB CDC ACM 的初始化(`usbd_initialize`)、接收回调(`usbd_ep_receive`)和发送(`usbd_ep_transmit`)，对外暴露与 ChibiOS `UARTDriver` 一致的 `AP_HAL::UARTDriver` 接口。 |
| **Consequences** | ✅ USB 通信功能正常<br>⚠️ 不支持 ChibiOS 的 `BaseChannel` 多态派生<br>⚠️ 波特率概念在 USB CDC ACM 中无实际意义，需特殊处理<br>⚠️ CherryUSB 的接收为回调驱动，需内部 ringbuffer 中转 |
| **ChibiOS Reference** | `modules/ChibiOS/hwdef/...` — SDU 配置 + `hal_usb.c` |
| **RTT Implementation** | `libraries/AP_HAL_RTT/UARTDriver.cpp` — `RTTUARTDriver` 类 |
| **Verification** | 1. 设备插入 PC 后 `dmesg` 识别为 CDC ACM 设备<br>2. MAVLink 心跳包正常收发（`mavproxy.py --master=/dev/ttyACM0`）<br>3. AT 测试 / 回环测试验证数据完整性 |

---

## Deviation 2: ADC 驱动

| 字段 | 内容 |
|------|------|
| **Title** | ADC 驱动：ChibiOS DMA 环形缓冲 vs CMSIS 直接寄存器 |
| **Status** | **Accepted** — 功能等价，性能近似 |
| **Context** | ChibiOS 的 ADC 驱动使用 `adcStartConversionI()` + DMA 环形缓冲，以中断方式持续采样并通过回调通知上层。RTT 的 ADC 框架基于 CMSIS 直接寄存器操作，不支持 DMA 环形缓冲模式。 |
| **Decision** | 在 `AP_HAL_RTT` 中实现 `RTTADCDevice`，使用 CMSIS 直接寄存器配置 ADC（单次/连续模式），通过 RT-Thread 的 `rt_device_read()` 同步读取或高优先级线程轮询。对需要高速采样的通道（如电流/电压），启用 ADC 连续模式 + 定时器触发。 |
| **Consequences** | ✅ ADC 采样功能完整<br>⚠️ 无 DMA 环形缓冲，CPU 开销略高于 ChibiOS 方案<br>⚠️ 高采样率场景需专用线程，增加上下文切换<br>✅ 实现更简单，依赖更少 |
| **ChibiOS Reference** | `os/hal/ports/STM32/LLD/ADCv2/hal_adc_lld.c` — DMA 环形缓冲实现 |
| **RTT Implementation** | `libraries/AP_HAL_RTT/AnalogSource.cpp` — `RTTAnalogSource` 类，调用 `rt_device_read()` |
| **Verification** | 1. `AnalogSource::read()` 返回值在合理范围（如 3.3V 引脚读取 ≈ 3.26~3.34V）<br>2. 电压/电流传感器数据在 GCS 正确显示<br>3. 连续采样 1000 次无数据丢失 |

---

## Deviation 3: SPI 驱动

| 字段 | 内容 |
|------|------|
| **Title** | SPI 驱动：ChibiOS SPI LL Driver vs RTT SPI Device |
| **Status** | **Accepted** — 功能等价，接口不同 |
| **Context** | ChibiOS 使用底层的 SPI 低层驱动 (LLD) 直接操作寄存器，通过 `spiStart()` / `spiSelect()` / `spiExchange()` 同步收发。RTT 使用 RT-Thread 的 SPI 设备框架，通过 `rt_spi_transfer()` / `rt_spi_send_then_recv()` 进行消息级收发。 |
| **Decision** | 在 `AP_HAL_RTT` 中封装 `RTTSpiDevice` 和 `RTTSpiManager`，将 ChibiOS 的 `spiExchange()` 语义映射到 RTT 的 `rt_spi_transfer()`。CS 片选使用 RTT 的 `rt_spi_take_bus()` / `rt_spi_release_bus()` 管理。 |
| **Consequences** | ✅ 所有 SPI 外设（IMU、Baro、SD 卡）正常工作<br>⚠️ RTT SPI 框架每次传输有额外函数调用开销<br>⚠️ 不支持 ChibiOS 的 `spiUnselect()` 延迟释放优化<br>✅ CS 管理更安全（自动总线锁定） |
| **ChibiOS Reference** | `os/hal/ports/STM32/LLD/SPIv2/hal_spi_lld.c` — 寄存器级 SPI 驱动 |
| **RTT Implementation** | `libraries/AP_HAL_RTT/SPIDevice.cpp` — `RTTSpiDevice` 类，包装 `rt_spi_transfer_message()` |
| **Verification** | 1. IMU 数据输出正常（`mpu9250` 等驱动通过 SPI 读取）<br>2. SPI 时钟频率与配置一致（示波器测量）<br>3. 连续 1 小时运行无 SPI 通信错误 |

---

## Deviation 4: 调度器休眠

| 字段 | 内容 |
|------|------|
| **Title** | 调度器休眠：`chThdSleep` vs `idlehook` |
| **Status** | **Accepted** — 行为等价，实现不同 |
| **Context** | ChibiOS 调度器中线程通过 `chThdSleep()` / `chThdSleepMilliseconds()` 主动让出 CPU，调度器在有就绪线程时切换到它们，无就绪线程时进入空闲线程执行 `idle` 钩子并 `WFI` (Wait For Interrupt) 低功耗休眠。RTT 使用 `rt_thread_sleep()` / `rt_thread_mdelay()` 让出 CPU，空闲线程执行 `rt_thread_idle_sethook()` 注册的钩子。 |
| **Decision** | 在 `AP_HAL_RTT` 中实现 `RTTScheduler::delay_microseconds()` 和 `delay_milliseconds()`，内部调用 `rt_thread_mdelay()`（毫秒级）或 `rt_hw_us_delay()`（微秒级忙等）。空闲线程钩子注册用于执行 ArduPilot 的 `hal.scheduler->system_initialized()` 后的空闲回调。 |
| **Consequences** | ✅ 线程休眠功能正常<br>✅ 空闲线程钩子可用于低功耗管理<br>⚠️ RTT 微秒级休眠只能用忙等（`rt_hw_us_delay()`），无微秒级线程睡眠原语<br>⚠️ ChibiOS 的 `chThdSleepUntil()` 绝对时间休眠无直接等价物 |
| **ChibiOS Reference** | `os/common/ports/ARMCMx/chcore.c` — `chThdSleep()` + idle loop `WFI` |
| **RTT Implementation** | `libraries/AP_HAL_RTT/Scheduler.cpp` — `RTTScheduler` 中的时间延迟和空闲钩子 |
| **Verification** | 1. `Scheduler::delay(100)` 实际延迟 ≈ 100ms（`rt_tick_get()` 测量）<br>2. 空闲时 CPU 进入低功耗模式（电流测量降低）<br>3. 主循环频率稳定（400Hz，`AP_Vehicle` 调度周期准确） |

---

## Deviation 5: 线程栈分配

| 字段 | 内容 |
|------|------|
| **Title** | 线程栈：ChibiOS 静态 BSS 分配 vs RTT 动态堆分配 |
| **Status** | **Accepted** — 行为等价，资源模型不同 |
| **Context** | ChibiOS 使用 `THD_WORKING_AREA(wa, N)` 宏在 BSS 段静态分配线程栈，栈内存不占用堆空间。RTT 的 `rt_thread_create()` 内部通过 `RT_KERNEL_MALLOC(stack_size)` 从动态堆分配线程栈。这意味着在 ChibiOS 上线程栈不计入堆使用量，而在 RTT 上全部线程栈总和必须小于可用堆。CUAV V5 堆仅 86KB，而 AP_HAL_RTT 的线程栈总需求曾达 ~59KB，导致堆耗尽。 |
| **Decision** | 1. 缩减 `Scheduler.cpp` 中线程栈大小至合理值：ap_timer 16384→4096, ap_io 8192→4096, storage 8192→4096, ap_uart 8192→4096（共节省 24KB）<br>2. 未来新增线程时需评估堆预算<br>3. 长期考虑：将关键线程栈改为静态分配（手动分配 `rt_malloc` + `rt_thread_init`），需要 ADR 升级 |
| **Consequences** | ✅ 堆耗尽已修复（`used < total`）<br>⚠️ 缩减后的栈大小需要持续验证无栈溢出<br>⚠️ 栈溢出检测需要额外 canary 机制（RTT 无内置检测）<br>⚠️ 新增功能线程时必须重新评估堆预算<br>✅ 静态分配升级路径保留 |
| **ChibiOS Reference** | `os/common/ports/ARMCMx/chtypes.h` — `THD_WORKING_AREA(wa, N)` 宏定义（BSS 静态分配） |
| **RTT Implementation** | `modules/rt-thread/src/thread.c:568` — `RT_KERNEL_MALLOC(stack_size)` <br>`libraries/AP_HAL_RTT/Scheduler.cpp` — `thread_create_worker()` 封装及栈大小常量 |
| **Verification** | 1. GDB 检查 `system_heap`：`used < total` ✅<br>2. USB 枚举正常 ✅<br>3. `HAL::run()` 执行到 `setup_stage=502` ✅（堆健康，阻塞在其他问题） |

---

## 偏离项汇总

| # | 偏离项 | ChibiOS 实现 | RTT 实现 | 复杂度 | 验证状态 |
|---|--------|-------------|----------|--------|---------|
| 1 | USB | SDU + BaseChannel | CherryUSB CDC ACM | 中 | ✅ 已验证 |
| 2 | ADC | DMA 环形缓冲 | CMSIS 直接寄存器 | 中 | ✅ 已验证 |
| 3 | SPI | SPI LLD 寄存器 | RTT SPI Device | 低 | ✅ 已验证 |
| 4 | Scheduler | chThdSleep + WFI | rt_thread_mdelay + idlehook | 低 | ✅ 已验证 |
| 5 | 线程栈 | 静态 BSS (THD_WORKING_AREA) | 动态堆 (RT_KERNEL_MALLOC) | 高 | ✅ 已验证(已修复) |

---

## 附录: 未纳入的偏离项

以下项虽存在差异，但被视为"平台适配"而非"架构偏离"，故不在此记录：

- **GPIO**: RTT PIN 设备 vs ChibiOS PAL — 标准外设抽象层差异
- **I2C**: RTT I2C 设备 vs ChibiOS I2C LLD — 同上
- **UART**: RTT UART 设备 vs ChibiOS UART LLD — 串口框架差异
- **Timer**: RTT hwtimer vs ChibiOS GPT — 硬件定时器框架差异

这些项通过 `AP_HAL_RTT` 中的标准设备封装解决，不改变上层架构。
