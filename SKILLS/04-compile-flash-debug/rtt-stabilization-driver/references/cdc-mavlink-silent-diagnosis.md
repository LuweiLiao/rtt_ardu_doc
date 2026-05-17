# CDC 已枚举 + 无 MAVLink 心跳诊断 — RTT CUAV V5

## 发现时间
2026-05-11，Phase 1C（I2C 位爆炸修复）完成后验证时发现。I2C 卡死解除、系统正常启动到 idle 线程，但 MAVLink 仍无输出。

## 现象特征

| 特征 | 值 |
|------|-----|
| USB CDC | ✅ ttyACM0/1 已枚举（USB 层面初始化成功） |
| HardFault | ✅ 无（CFSR=0, HFSR=0） |
| 调度器 | ✅ 正常运行（PC 在 idle_thread_entry / rt_spin_lock_irqsave） |
| MAVLink | ❌ ttyACM0 和 ttyACM1 均无数据 |
| 3次halt(100ms间隔) | PC 不在 I2C/SPI 阻塞区，系统在正常调度 |

## 潜在的根因假设

### 假设 A：UART 设备名不匹配 🏆 最高优先级

**证据**：生成的 `hwdef.h:70` 定义：
```c
#define HAL_RTT_UART_DEVICE_LIST "usb-acm0", "uart2", "uart3", ...
```

CUAV V5 Serial0 → "usb-acm0"（CDC ACM OTG1 设备）。

UARTDriver.cpp:174 调用 `rt_device_find("usb-acm0")`。

**关键问：CherryUSB CDC 驱动实际注册的 RT-Thread 设备名是什么？**
- 常见候选名：`"usbd0"`, `"cdc_acm0"`, `"usb_acm0"`, `"acm0"`
- 如果 != "usb-acm0" → `rt_device_find` 返回 nullptr → `_begin()` 失败 → MAVLink 写到空设备
- ubus 驱动也可能使用 `"usb_gadget"` 接口名

**验证方法**：在 UARTDriver.cpp 的 `_begin()` 中临时加入 `rt_kprintf("[UART%u] device_find('%s') → %p\n", ...)`。

### 假设 B：AP_HAL 主循环线程未启动

**证据**：PC 持续在 idle 线程，`AP_HAL::run()` 未输出任何数据。

**对比 ChibiOS**：`AP_HAL_ChibiOS/Scheduler.cpp` 中 `init()` 创建 `APM_SCHED_THREAD` 主线程，优先级最高。
RTT 的等效逻辑在 `HAL_RTT_Class.cpp`/`Scheduler.cpp` 中。

**检查点**：
- `HAL_RTT_Class.cpp` 的 `run_ap()` 或 `main_loop_wrapper()` 是否在正确时机被调用？
- setup_stage 最终值是多少？（完整的 init 序列是否全部执行？）
- 如果 setup_stage 已到最大值但主循环未运行 → 主线程创建逻辑缺失

### 假设 C：USB CDC TX 路径不适用

UARTDriver.cpp:76-87 定义了 `uart_poll_tx()` 寄存器直写函数，仅适用于硬件 UART（USART1-8，寄存器基地址 0x40011000 等）。

**USB CDC 不是 UART！** 它的 TX 通过 USB DWC2 控制器的 IN endpoint，经 RT-Thread device 框架写入。

**关键问**：`UARTDriver::_write()` 是否对 USB 设备做了特殊处理？检查 `_is_usb` 标志的使用。

**ChibiOS 参考**：`AP_HAL_ChibiOS/UARTDriver.cpp` 中 USB CDC 通过 `USBDriver` 结构体 + `chnWrite()` 写入数据端点。

### 假设 D：Serial 配置不为 MAVLink

hwdef.h:199 `DEFAULT_SERIAL0_BAUD 921600` → Serial0 配置为高速率。
但 AP_SerialManager 的默认协议映射可能不为 MAVLink（可能为 console 或 GPS）。

## 诊断步骤

```bash
# 1. 确认 CDC 设备名
# 检查 CherryUSB 驱动的设备注册名
grep -rn 'rt_device_register' modules/rt-thread/components/drivers/usb/usb_device/cherryusb/ 2>/dev/null

# 2. 检查 UARTDriver 设备列表定义
grep 'HAL_RTT_UART_DEVICE_LIST' build/rtt_cuav_v5/hwdef.h

# 3. 检查 AP_HAL 主循环入口
grep 'run_ap\|main_loop\|thread_create.*ap\|SCHED_THREAD' libraries/AP_HAL_RTT/HAL_RTT_Class.cpp libraries/AP_HAL_RTT/Scheduler.cpp

# 4. 对比 ChibiOS 主线程创建
grep -A 20 'thread_create\|SCHED_THREAD\|scheduler_init' libraries/AP_HAL_ChibiOS/Scheduler.cpp
```

## 参考文件
- 生成 hwdef.h: `build/rtt_cuav_v5/hwdef.h`（`HAL_RTT_UART_DEVICE_LIST`, `DEFAULT_SERIAL0_BAUD`）
- UARTDriver: `libraries/AP_HAL_RTT/UARTDriver.cpp`, `UARTDriver.h`
- HAL_RTT_Class: `libraries/AP_HAL_RTT/HAL_RTT_Class.cpp`
- Scheduler (RTT): `libraries/AP_HAL_RTT/Scheduler.cpp`
- ChibiOS UARTDriver: `libraries/AP_HAL_ChibiOS/UARTDriver.cpp`
- ChibiOS Scheduler: `libraries/AP_HAL_ChibiOS/Scheduler.cpp`
- ChibiOS HAL_ChibiOS_Class: `libraries/AP_HAL_ChibiOS/HAL_ChibiOS_Class.cpp`
- Kanban: t_078bb155 (Phase 1D Research), t_d462f9f8 (Engineer), t_9e6c11a5 (Reviewer), t_30822b22 (Ops)
