# CherryUSB CDC TX Stall 诊断与修复

## 背景

RTT ArduPilot 使用 CherryUSB 作为 USB CDC 设备驱动（替代 F1 USB 栈）。
USB CDC 数据流经以下路径：

```
MAVLink comm_send() → UARTDriver::_write() → _writebuf(8KB ringbuffer)
  → _drain_writebuf_to_dev() → rt_device_write()
    → usbd_serial_write() → rt_ringbuffer_put() → CherryUSB ringbuffer(32KB)
      → usbd_serial_kick_tx() → DWC2 EP1 IN FIFO(128B)
        → USB FS Bulk IN(64B/帧@1ms)
```

## 已知问题：UART 线程优先级饥饿导致 3-5 秒静默

### 根因

RT-Thread 线程优先级数字越小越高。RTT ArduPilot 的线程优先级配置：

| 线程 | 优先级 | 备注 |
|------|--------|------|
| Main | 5 | 主循环 340Hz，从不 yield（仅 DWT 50us busy-wait） |
| UART | 14 → **6** | 修复后：从 14 提升到 6 |
| IO | 18 | 最低级 |

**当 UART 优先级 = 14 时**：主线程（优先级 5）从不 yield（`delay_microseconds(50)` 是 DWT busy-wait），UART 线程永远得不到 CPU 时间。`_timer_tick()` 从未被调用 → `_drain_writebuf_to_dev()` 只由 `_write()` 触发（在 50Hz GCS update 中）。

**后果**：
1. CherryUSB ringbuffer 被 ISR 耗尽后，无新数据填充
2. `_writebuf` 满 → `txspace()=0` → `queued_param_send()` 停止
3. 数据静默 3-5 秒，直到某个巧合的再次触发

### 诊断方法：字节级时序分析 + 调试计数器

**Step 1**：测量原始字节流的到达间隔，区分固件静默 vs 主机缓冲

```python
import time, serial
s = serial.Serial('/dev/ttyACM1', 921600, timeout=0.01)
time.sleep(2)
t0 = time.time()
last_data = t0
total = 0
burst_count = 0
burst_start = 0
while time.time() - t0 < 20:
    d = s.read(2000)
    now = time.time()
    if d:
        total += len(d)
        if burst_start == 0 or (now - last_data) > 0.2:
            if burst_start > 0:
                burst_dur = now - burst_start
                print(f"  Burst{burst_count}: {bytes_in_burst}B in {burst_dur:.1f}s → {bytes_in_burst/burst_dur:.0f} B/s")
            burst_count += 1
            burst_start = now
            bytes_in_burst = len(d)
        else:
            bytes_in_burst += len(d)
        last_data = now
    else:
        if last_data > 0 and (now - last_data) >= 0.5:
            print(f"  GAP {now-last_data:.1f}s (burst had {bytes_in_burst}B)")
            last_data = now + 0.5  # suppress repeated alerts
```

**输出示例**（UART 优先级 = 14 时）：
```
  Burst1: 1408B in 0.2s → 7040 B/s
  GAP 3.4s (burst had 1408B)
  Burst2: 8778B in 0.2s → 43890 B/s
  GAP 4.9s (burst had 8778B)
  ...
Total: 20KB in 20s, effective 1KB/s
```

**Step 2**：用 volatile 调试计数器确认线程运行状态

在 UARTDriver.cpp 顶部加入：
```cpp
volatile uint32_t rtt_uart_dbg_tick_calls = 0;
```

在 `_timer_tick()` 首行：
```cpp
rtt_uart_dbg_tick_calls++;
```

编译后用 GDB 读取：
```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p rtt_uart_dbg_tick_calls" \
  -ex "monitor resume" \
  build/rtt_deploy/cuav_v5/rt-thread.elf
```

**正常值**：`rtt_uart_dbg_tick_calls` 应在运行数秒后达到数万（1kHz tick）。
**异常值**：`= 0` → UART 线程从未运行。

### 修复

**提升 UART 线程优先级**（在 `Scheduler.h` 中）：

```cpp
// ❌ 原值：优先级 14 — 被主线程(5)完全饥饿
#define APM_RTT_UART_PRIORITY     14
// ✅ 修复值：优先级 6 — 可在主线程 yield 时运行
#define APM_RTT_UART_PRIORITY     6
```

优先级 6 在 RCIN(6) 同级，低于 Main(5)。当主线程在 `wait_for_sample()` 中调用
`delay_microseconds_boost()` → `rt_thread_delay(1)` 时，UART 线程获得运行机会。

### 残余问题

即使 UART 优先级提升，CherryUSB 的 TX 管道仍有深度架构问题：

- **DWC2 TX FIFO 仅 128 字节**（`CONFIG_USB_DWC2_TX1_FIFO_SIZE`）— 只能缓冲 2 个 USB FS 包
- **单缓冲设计**：`tx_active` 原子锁保护，主线程和 UART 线程双写入竞争时效率反降
- **dbg_serial_write_timeout 计数**：1286/1503 次写入在 ringbuffer 满时触发超时恢复

未来优化方向：增大 DWC2 TX FIFO（128→512 字节），或使用多缓冲 TX 模式。

## CherryUSB 内部架构速查

### usbd_serial 结构体

```c
struct usbd_serial {
    struct rt_ringbuffer tx_rb;               // 发送环形缓冲管理
    rt_uint8_t tx_rb_buffer[CONFIG_USBDEV_SERIAL_TX_BUFSIZE]; // = 32768
    struct rt_ringbuffer rx_rb;               // 接收环形缓冲管理
    rt_uint8_t rx_rb_buffer[CONFIG_USBDEV_SERIAL_RX_BUFSIZE]; // = 4096
    volatile uint8_t tx_active;               // IN 传输进行中标志
    volatile uint8_t tx_need_kick;            // DTR 设置后需 kick TX
    struct usbd_endpoint *in_ep;              // CDC IN 端点
    struct usbd_endpoint *out_ep;             // CDC OUT 端点
    // ...
};
// sizeof = 0x9120 = 37152 字节（含 32KB TX + 4KB RX 缓冲）
```

### TX 数据流

```
UARTDriver :: 写入(每次最多 512 字节)
  → usbd_serial_write()
    → rt_ringbuffer_put() — 放入环形缓冲（非阻塞）
    → usbd_serial_kick_tx() — 尝试启动 IN 传输
      → 检查 usb_device_is_configured() — 未配置则 reset ringbuffer!
      → 检查 tx_active — 已有传输在进行则返回
      → 原子设置 tx_active=1
      → 从 ringbuffer 取出 ≤64 字节
      → usbd_ep_start_write() → DWC2 DIEPCTL
  → ISR: usbd_cdc_acm_bulk_in()
    → tx_active=0
    → usbd_serial_kick_tx() — 取下一批数据
```

### 关键陷阱

1. **`usb_device_is_configured()` 返回 false 时会 reset ringbuffer**（第 259 行）
   - 全部待发送数据丢失！
   - 在 USB 重枚举/配置期间发生

2. **`tx_active` 自修复**：如果硬件 EP 空闲但 `tx_active=1`（异常状态），kick_tx 会检测并清除

3. **DTR set 回调 reset ringbuffer**（第 494 行）：
   - QGC 等上位机连接时触发
   - 清空等待发送的数据（不影响后续数据）

4. **定时器 tick 从未运行 = 数据只通过 _write() 的 drain 路径发送**
   - `_write()` 的 drain 仅在 `_writebuf.space() < size` 时触发
   - 初始 8KB 充满后，drain 不触发 → 数据不发送 → 静默

### 验证 struct 大小确认 TX 缓冲

```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p /x sizeof(struct usbd_serial)" \
  -ex "monitor resume" \
  build/rtt_deploy/cuav_v5/rt-thread.elf
# sizeof = 0x9120 → TX_BUFSIZE=32768（因为 ≈ 32768 + 4096 + 开销）
```

## 2026-05-10 会话验证数据

### 最终修复组合

所有修复经过验证，逐步叠加：

| 修复 | 效果 |
|------|------|
| UART 优先级 14→6 | `rtt_uart_dbg_tick_calls` 从 0 → 211K+，线程开始运行 |
| DWC2 TX FIFO 256→512 字节 | 第一批 393 params 在 4.8s（之前 27 params）|
| _writebuf set_size_best 安全降级 | 防止堆碎片导致分配失败 |

### 字节间隙改善

| 指标 | 修复前(UART prio=14) | 修复后(UART prio=6+FIFO) |
|------|---------------------|-------------------------|
| 最大无数据静默 | 3.0-5.2s | **0.6-0.7s** |
| 原始流吞吐 | 0.8 KB/s | 0.9 KB/s |
| 参数速率 | 16.9-33.6 params/s | 15.6-25.6 params/s |
| QGC 断连风险 | **高**（>3s 超时） | **低**（<1s，不触发） |

### 未解决：堆碎片化

`calloc(1,8192)` 运行时验证成功，但 `_writebuf` 实际上仍为 512 字节（`set_size_best` 可能未被正确调用，或 `_begin()` 的多次调用路径有问题）。**UTEST 未能达到预期 8KB**。

要彻底解决堆碎片/缓冲大小问题，需：
1. 追踪 `_begin()` 所有调用路径（deferred + non-deferred + serial manager override）
2. 或改用 RT-Thread memheap（独立堆区域）分配
3. 或预先在启动早期（堆未碎片时）分配好所有缓冲
