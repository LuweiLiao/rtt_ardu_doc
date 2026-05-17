# ChibiOS vs CherryUSB CDC 架构对比分析（2026-05-11）

## 为什么 MAVLink 10 Hz 流在 ChibiOS 上能工作但在 RTT 上不能？

### 根本架构差异

| 层级 | ChibiOS | RTT (CherryUSB) |
|------|---------|-----------------|
| 数据源 | `_writebuf` ByteBuffer (8KB) | `_writebuf` ByteBuffer (8KB) |
| 中间层 | 无 — 直接 USB 端点 | `usbd_serial_write()` → CherryUSB `tx_rb` ringbuffer |
| 硬件写 | `usbStartTransmitI(bulk_in, buf, n)` | `usbd_ep_start_write()` → `dwc2_ep_write()` → FIFO |
| 拷贝次数 | **1 次** (`_writebuf` → DWC2 FIFO) | **3 次** (`_writebuf` → bounce → ringbuffer → FIFO) |
| 调度 | 每端口独立 TX 线程 (`uart_thread`) | 单 `ap_uart` 线程轮询 10 端口 |
| TX 频率 | 1kHz（per-port） | ~100Hz（被 `_check_usb_connected` 门控衰减） |
| USB 检查 | `usbp->state != USB_ACTIVE`（稳定可靠） | `usb_device_is_configured()`（~90% 返回 false）|

### ChibiOS TX 路径详解

```
_writebuf (ByteBuffer)
  → write_pending_bytes_NODMA()
    → chnWriteTimeout(SerialUSBDriver*, data, len, TIME_IMMEDIATE)
      → _writet() → obqWriteTimeout(&obqueue, data, len, TIME_IMMEDIATE)
        → obqWriteTimeout 写输出缓冲区队列（管理 USB 端点 DMA 缓冲）
        → obnotify() 回调：
          → usbStartTransmitI(usbp, bulk_in_ep, buf, n)
          → 启动 USB IN 传输，DWC2 直接从 buf 发数据
```

**关键特征**：
- `chnWriteTimeout(TIME_IMMEDIATE)`：非阻塞，返回实际写入字节数（0=端点忙）
- `obqWriteTimeout`：写输出缓冲区队列，**非 ringbuffer**——数据直接从 `_writebuf` 提交给 USB 端点
- `obnotify`：数据提交到队列后的回调——如果没有正在进行的 IN 传输，立即启动新的
- 数据只在 `_writebuf` 中等待；一旦硬件有空，立刻发送
- **无中间 ringbuffer → 无 drain 黑窗期**

### CherryUSB TX 路径详解（当前 RTT）

```
_writebuf (ByteBuffer)
  → _drain_writebuf_to_dev()
    → peekbytes(_tx_bounce, 512) → 拷贝数据到 bounce buffer [拷贝 #1]
    → rt_device_write(usb-acm0, tx_bounce, n)
      → usbd_serial_write(data, len)
        → rt_ringbuffer_put(&tx_rb, data, len) → [拷贝 #2]
        → kick_tx()
          → 如果 tx_active=false：
            → rt_ringbuffer_get(&tx_rb, tx_pkt, 64) → [拷贝 #3]
            → usbd_ep_start_write(busid, 0x81, tx_pkt, len)
              → dwc2_ep_write() → USB_OTG_FIFO(ep) = data [硬件写入]
```

**问题**：
1. **3 次拷贝** — bounce buffer → ringbuffer → DMA buffer
2. **ringbuffer 满了 → drain 阻塞** — USB ISR 只能以 64 字节/ms 排出，但 UART 线程以 512 字节/tick 写入。8ms 后 ringbuffer 满 → 64ms drain 黑窗
3. **64ms drain 黑窗** — 期间 writebuf 不排空 → MAVLink HAVE_PAYLOAD_SPACE=false → 流率骤降

### 关键发现：`_check_usb_connected()` 是隐式速率限制器

最初认为它是 bug（过滤 90% drain 调用）。但对照实验证明它是**必要**的：

| 测试 | RAW_IMU | ATTITUDE |
|------|---------|----------|
| 基线 gate ON | 2.6 Hz | 6.5 Hz |
| 去掉 gate | 0.8 Hz ❌ | 0.9 Hz ❌ |

**原因**：gate 将 drain 调用从 1kHz 衰减到 ~100Hz，使 drain 速率（~51KB/s）匹配 USB ISR 排出速率（64KB/s）。去掉 gate 后 1kHz × 512B = 512KB/s 写入 CherryUSB，远超 USB 63KB/s 能力 → ringbuffer 每周 64ms 满一次 → 更差。

### CherryUSB 已有绕过 ringbuffer 的 API

`usbd_ep_start_write(busid, ep, data, len)` — 见 `core/usbd_core.h`：
- 直接启动 USB IN 传输，数据从 `data` 指针直通 `dwc2_ep_write()` → DWC2 FIFO
- **不经过 tx_rb ringbuffer**
- 4 字节对齐要求（release 版本无断言，debug 有）
- 端点忙时返回 -3 (EPENA 仍置位)
- 端点检查 API: `usbd_ep_check_busy(busid, ep)`

### 改造方向

在 `_drain_writebuf_to_dev()` 中为 USB 端口 (port 0) 实现直接 EP 写入路径：

```cpp
// 替代 rt_device_write() 的 USB 直接写入路径
if (port_is_usb) {
    const auto n = _writebuf.peekbytes(_tx_bounce, sizeof(_tx_bounce));
    if (n == 0) return;
    
    int ret = usbd_ep_start_write(0, 0x81, _tx_bounce, n);
    if (ret == 0) {
        _writebuf.advance(n);
    }
    // ret == -3 → 端点忙，下次 tick 再试
    return;
}
```

需要：CDC IN 端点号 0x81、4 字节对齐的 tx_bounce（已对齐）、完成回调管理。

### 基线性能（commit a632415295，最优状态）

| 消息 | 观察率 | 配置率 | 占比 |
|------|--------|--------|------|
| ATTITUDE | 6.5 Hz | 10 Hz | 65% |
| RAW_IMU | 2.6 Hz | 10 Hz | 26% |
| AHRS | 1.4 Hz | 2 Hz | 70% |
| HEARTBEAT | 1.4 Hz | 1 Hz | 140% |

系统运行中 writebuf 平均只有 55 字节（`drain_bytes / drain_calls ≈ 36 bytes/write`），说明 writebuf 非瓶颈。瓶颈在 MAVLink `update_send()` 的 5ms 预算循环 + 轮询调度。

### ChibiOS `bw_in_bytes_per_second()` 返回 200 KB/s

在 `AP_HAL_ChibiOS/UARTDriver.h:124`：
```cpp
uint32_t bw_in_bytes_per_second() const override {
    if (sdef.is_usb) {
        return 200*1024;  // 200 KB/s = USB HS
    }
    return _baudrate/10;
}
```

ChibiOS 使用 **USB HS**（高速模式 + 外部 PHY），而 RTT CherryUSB 配置为 **USB FS**（全速 64 KB/s）。这是 3x 吞吐量差距。如果 CUAV V5 支持 HS，后续可迁移。
