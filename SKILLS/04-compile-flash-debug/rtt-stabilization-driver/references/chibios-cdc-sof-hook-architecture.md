# ChibiOS CDC SOF Hook 架构 — CherryUSB 对比参考

## ChibiOS SDU 三层架构

### 层 1: 输出缓冲队列 (obqueue)
- 文件: `modules/ChibiOS/os/hal/src/hal_serial_usb.c`
- 数据结构: `io_buffers_queue_t` (obqueue)
- 写入: `obqPostFullBufferI()` / `chnWrite()` → 放入完整缓冲到 obqueue
- 特性: 缓冲池大小由 `SERIAL_USB_BUFFERS_SIZE` 和 `SERIAL_USB_BUFFERS_NUMBER` 定义

### 层 2: obnotify — 数据就绪回调
```c
static void obnotify(io_buffers_queue_t *bqp) {
    // 当新缓冲插入 obqueue 时自动调用
    // 检查 EP 是否空闲 → 如果空闲立即开始传输
    if (!usbGetTransmitStatusI(sdup->config->usbp, sdup->config->bulk_in)) {
        uint8_t *buf = obqGetFullBufferI(&sdup->obqueue, &n);
        usbStartTransmitI(sdup->config->usbp, sdup->config->bulk_in, buf, n);
    }
    // 如果 EP 忙 → 数据留在队列中，等 SOF hook 恢复
}
```

### 层 3: sduSOFHookI — USB SOF 中断恢复
```c
// 从 USB 硬件 SOF 中断（1kHz）调用
void sduSOFHookI(SerialUSBDriver *sdup) {
    // 1. 检查状态
    if (usb_state != ACTIVE || sdup->state != SDU_READY) return;
    // 2. 检查 EP 是否空闲
    if (usbGetTransmitStatusI(...)) return; // 有传输进行中
    // 3. 尝试 flush 部分填满的缓冲
    if (obqTryFlushI(&sdup->obqueue)) {
        // 4. 获取填满的缓冲并启动传输
        uint8_t *buf = obqGetFullBufferI(&sdup->obqueue, &n);
        usbStartTransmitI(sdup->config->usbp, sdup->config->bulk_in, buf, n);
    }
}
```

### SOF 中断使能
```
hal_usb_lld.c:549-556:
  if (usbp->config->sof_cb != NULL)
      cntr |= USB_CNTR_SOFM;   // 启用 SOF 中断
```
SOF 中断只在有回调时启用（避免 1kHz 空转）。

## CherryUSB 对比

### CherryUSB 当前架构
- 写入: `usbd_serial_write()` → 写入 ringbuffer + 调用 `kick_tx()`
- kick_tx: 设置 `tx_active = 1` + `usbd_ep_start_write()`
- XFRC ISR: `usbd_cdc_acm_bulk_in()` → 清除 `tx_active` + 调用 `kick_tx()`
- 自愈: 1ms RT-Thread 软定时器 → `usbd_serial_kick_tx_poll()` → 检查 EPENA

### 关键差异

| 方面 | ChibiOS SDU | CherryUSB |
|------|------------|-----------|
| 恢复触发 | **USB 硬件 SOF 中断 (1kHz ISR)** | RT-Thread 软定时器 (1ms) |
| 恢复优先级 | **最高**（USB ISR 中执行） | **低**（软定时器线程中执行） |
| 缓冲模型 | 固定数量的大缓冲（obqueue, 推模型） | 环形缓冲区（ringbuffer, 拉模型） |
| TX 完成通知 | `usbGetTransmitStatusI()` + SOF hook | `tx_active` 标志 + EPENA 检查 |
| 优先级 | 在 USB ISR 中完成，不被任何线程抢占 | 可能被主循环/UART 线程抢占 |

### ChibiOS SOF 方案的不可移植性

CherryUSB 是纯软件 USB 协议栈（无 RTOS 绑定 USB ISR 框架），不能直接移植 ChibiOS 的 `sof_cb` 机制。可行替代方案：

1. **DWC2 寄存器级 SOF 检测**（推荐）：
   - 在 DWC2 OTG_FS ISR 中：`if (GINTSTS & GINTSTS_SOF)` 时触发 kick
   - 通过 USB GINTMSK 使能 SOF 中断位
   - 需要 CherryUSB 的 busid → DWC2 Instance 映射

2. **高优先级 RT-Thread 硬定时器**（折中）：
   - 使用 `RT_TIMER_FLAG_HARD_TIMER`（在 timer ISR 中执行）
   - 优先级高于所有线程，接近 ISR
   - **风险**：硬定时器 ISR 中不能调用 `rt_ringbuffer` 等线程安全 API

3. **专用 TX 看门狗线程**（最安全）：
   - 优先级 1（仅次 ISR），阻塞在 `rt_sem_take(1ms)` 循环
   - 调用 `kick_tx_poll()`，可以使用所有 thread-safe API
   - 无定时器中断上下文限制

## 参考文件
- `modules/ChibiOS/os/hal/src/hal_serial_usb.c` — SDU 完整实现（~500行）
- `modules/ChibiOS/os/hal/ports/STM32/LLD/USBv2/hal_usb_lld.c:549-556` — SOF 中断使能逻辑
- `modules/rt-thread/components/drivers/usb/cherryusb/platform/rtthread/usbd_serial.c` — CherryUSB CDC
