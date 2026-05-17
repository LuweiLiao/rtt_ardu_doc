# ChibiOS SDU (Serial USB Driver) 架构详解

> 2026-05-12 深入学习记录。来源：`modules/ChibiOS/os/hal/src/hal_serial_usb.c`
> 对照 RTT CherryUSB 实现：`modules/rt-thread/components/drivers/usb/cherryusb/platform/rtthread/usbd_serial.c`

## 一、文件结构与关键行号

### ChibiOS SDU (`hal_serial_usb.c`) — 558 行

| 行号 | 符号 | 功能 |
|------|------|------|
| 53-79 | `sdu_start_receive()` | 拉起 USB OUT 端点接收 |
| 85-89 | `_write()` | 写入 obqueue（`TIME_INFINITE` 阻塞） |
| 117-121 | `_writet()` | 写入 obqueue（可指定 timeout） |
| 156-161 | `vmt` | SerialUSBDriverVMT 虚拟方法表 |
| 168-171 | `ibnotify()` | IN buffer 释放后触发，重启接收 |
| **178-197** | **`obnotify()`** | ⭐ OUT buffer 入队后触发——TX 核心 |
| 199-260 | `sduStart()` | 初始化 obqueue/ibqueue，注册回调 |
| 407-432 | **`sduSOFHookI()`** | ⭐ SOF 中断恢复机制——TX 安全网 |
| 442-457 | **`sduDataTransmitted()`** | ⭐ XFRC ISR 回调——TX 完成处理 |

### RTT CherryUSB (`usbd_serial.c`) — 531 行

| 行号 | 符号 | 功能 |
|------|------|------|
| 27-44 | `struct usbd_serial` | CDC 实例结构体 |
| 188-254 | `usbd_serial_write()` | 写入 tx_rb ringbuffer → kick_tx |
| **255-329** | **`usbd_serial_kick_tx()`** | ⭐ TX 核心（问题所在） |
| 419-445 | `usbd_cdc_acm_bulk_in()` | XFRC ISR → 清 tx_active → kick_tx |
| 447-475 | `usbd_cdc_acm_serial_init()` | 初始化，注册端点回调 |

## 二、obqueue 机制详解

### obqueue 是什么

ChibiOS 的 `output_buffers_queue_t`（obqueue）是**基于缓冲池的环形输出队列**：

```
            ┌─────┬─────┬─────┬─────┐
ob_buf[]:   │ buf0│ buf1│ buf2│ buf3│  ← 4 个 buffer
            └──┬──┴──┬──┴──┬──┴──┬──┘
               │     │     │     │
         get_full()  │   put_empty()
              ↑      │        ↑
              │      │        │
       正在传输的   空闲待填充   已清空的
       buffer      的buffer     buffer
```

### obqueue 回调触发条件

```c
// obqueue 有三种事件触发 obnotify：
// 1. obqPostFullBufferI() — 数据写入者填入完整 buffer
// 2. obqFlushI() — 强制刷新部分填充的 buffer（由 sduSOFHookI 调用）
// 3. obqReleaseFullBufferI() — ISR 释放已传输的 buffer
```

## 三、典型 TX 场景对比

### 场景 A：持续大量数据（MAVLink 心跳 + 姿态 10Hz）

**ChibiOS**：
```
t=0:  UART thread → chnWriteTimeout → obqueue.full
      → obnotify: !usbGetTransmitStatusI → usbStartTransmitI(buf0, 64)
t=1:  SOF interrupt → sduSOFHookI: usbGetTransmitStatusI=YES → return
t=2:  XFRC ISR → sduDataTransmitted → obqReleaseFullBufferI
      → obnotify: !usbGetTransmitStatusI → usbStartTransmitI(buf1, 64)
t=3:  循环...
```

**RTT CherryUSB**：
```
t=0:  kick_tx: tx_active=0 → tx_active=1 → usbd_ep_start_write(data, 64)
t=1:  XFRC ISR → bulk_in: tx_active=0 → kick_tx
      → kick_tx: tx_active=0 → tx_active=1 → usbd_ep_start_write(data, 64)
t=2:  如果 ISR 在 kick_tx 设置 tx_active=1 之前触发（race）→ tx_active 卡死
t=3:  后续所有 write 数据积压在 ringbuffer 但永远不发送
```

### 场景 B：ISR 丢失

**ChibiOS**：SOF 中断（1kHz）无条件尝试恢复 → 下一次肯定会发出

**RTT CherryUSB**：没有定期恢复机制 → 永远卡死

## 四、sduSOFHookI 被调用的路径

```c
// 路径 1：USB SOF 中断（1kHz）
// USB 驱动层 OTGv2 每帧（1ms）触发一次 SOF 中断
// → usb_lld_serve_sof_interrupt() → usb_events_callback → sduSOFHookI

// 路径 2：ArduPilot _flush() 显式调用
// UARTDriver::_flush() {
//     sduSOFHookI((SerialUSBDriver*)sdef.serial);
// }
```

## 五、RTT CherryUSB 修复关键文件

```bash
# 修改目标：CherryUSB CDC TX
modules/rt-thread/components/drivers/usb/cherryusb/platform/rtthread/usbd_serial.c

# ChibiOS 参考（对照用）
modules/ChibiOS/os/hal/src/hal_serial_usb.c

# RTT UARTDriver（调用链顶层）
libraries/AP_HAL_RTT/UARTDriver.cpp
```

## 六、修复原则

1. **先学 ChibiOS 再动手** — 这是廖博士的最高优先级工作流规则
2. **不要在 CherryUSB 中复制 ChibiOS 的全部架构** — obqueue 不适合 CherryUSB 的 ringbuffer 设计
3. **最小修改原则**：加一个 RT-Thread 软定时器做 SOF 级恢复，而不是重写整个 TX 路径
4. **保留调试计数器**：`dbg_serial_unstick_cnt` 已在代码中，用于验证修复效果
