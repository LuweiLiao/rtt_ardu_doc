# MAVLink 无输出 — 7层 Tracing 方法论

当 USB CDC 已枚举（`/dev/ttyACM1` 存在）但 pymavlink 收不到任何心跳时，
按以下 **7 层** 从外到内逐层排查。

---

## 第 0 层（现象确认）

```bash
# 1. 打开端口持续读取 15 秒
python3 -c "
import os, time
fd = os.open('/dev/ttyACM1', os.O_RDWR | os.O_NONBLOCK)
data = bytearray()
t0 = time.time()
while time.time() - t0 < 15:
    try:
        d = os.read(fd, 4096)
        if d: data.extend(d)
    except BlockingIOError: time.sleep(0.01)
os.close(fd)
print(f'Total bytes: {len(data)}')
print(f'MAVLink v2 magic (0xfd): {data.count(b\"\\xfd\")}')
print(f'MAVLink v1 magic (0xfe): {data.count(b\"\\xfe\")}')
"

# 2. 用 pymavlink 解析
from pymavlink.dialects.v20 import ardupilotmega as m
parser = m.MAVLink(None)
for b in open('/tmp/capture.bin', 'rb').read():
    msg = parser.parse_char(bytes([b]))
    if msg: print(msg.get_type())
```

**预期**：收到 HEARTBEAT(1/s) + SYS_STATUS(1/s) + 其他流数据。

---

## 第 1 层：GCS_MAVLINK 后端状态

```bash
# 检查通道 0 后端
gdb -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x ((GCS_MAVLINK*)gcs()._chan[0])->send_packet_count" \
  -ex "p/x ((GCS_MAVLINK*)gcs()._chan[0])->last_heartbeat_time" \
  -ex "p/x *((GCS_MAVLINK*)gcs()._chan[0])->_channel_status.current_tx_seq" \
  -ex "p/x _ZN11GCS_MAVLINK14mavlink_activeE" \
  -ex "p/x ((GCS*)0x200098a0)->update_send_has_been_called" \
  -ex "p/x ((GCS*)0x200098a0)->_num_gcs" \
  -ex "monitor resume" \
  rt-thread.elf
```

**关键指标**：
- `send_packet_count` > 0 → MAVLink 正在生成包
- `last_heartbeat_time` 是否在增长 → 心跳定期发送
- `_num_gcs` > 0 → GCS 后端已创建
- `mavlink_active` bit 0 = 1 → 通道 0 有活跃连接

**如果 `send_packet_count` 停止增长** → 心跳发送被阻塞（检查 txspace、out_of_time 等）

---

## 第 2 层：AP_HAL UART 写缓冲区

```bash
# 检查 Console UART 的写缓冲区
gdb -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x ((RTT::UARTDriver*)0x20019084)->_writebuf.available()" \
  -ex "p rtt_uart_dbg_drain_calls" \
  -ex "p rtt_uart_dbg_drain_bytes" \
  -ex "p rtt_uart_dbg_drain_zero" \
  -ex "monitor resume" \
  rt-thread.elf
```

**关键指标**：
- `_writebuf.available()` → 待发送数据量
- `rtt_uart_dbg_drain_calls` 是否递增 → `_timer_tick()` 在运行
- `rtt_uart_dbg_drain_bytes` → 已排入 CDC 的总字节数

---

## 第 3 层：CherryUSB CDC 串行驱动

```bash
# 检查 CDC 驱动 debug 计数器
gdb -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x dbg_serial_write_calls" \
  -ex "p/x dbg_serial_write_ok" \
  -ex "p/x dbg_serial_write_timeout" \
  -ex "p/x dbg_serial_tx_kick" \
  -ex "p/x dbg_serial_tx_kick_fail" \
  -ex "p/x dbg_serial_bulkin_cnt" \
  -ex "p/x ((struct usbd_serial*)0x20018e98)->tx_active" \
  -ex "p/x ((struct usbd_serial*)0x20018e98)->tx_need_kick" \
  -ex "monitor resume" \
  rt-thread.elf
```

**关键指标**：
- `dbg_serial_write_calls` vs `dbg_serial_tx_kick` → **比值过大 = kick 被 tx_active 频繁拦截**
- `dbg_serial_tx_kick` vs `dbg_serial_bulkin_cnt` → kick:completion 比，远大于 1 = ISR 链断裂
- `tx_active=0, tx_need_kick=0` 但有数据在 tx_rb → kick 没有被触发

**典型故障模式**：
- `write_calls=166, tx_kick=3, bulkin_cnt=2` → 99% 数据在 tx_rb 积压，USB IN 端点几乎不传输
- 根因可能是 USB 主机不轮询 IN 端点，或 DWC2 EPENA 卡死

---

## 第 4 层：DWC2 USB 端点

```bash
# 检查 IN 端点状态
gdb -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x DWC2_INEP(1)->DIEPCTL" \
  -ex "p/x DWC2_INEP(1)->DIEPTSIZ" \
  -ex "p/x DWC2_INEP(1)->DTXFSTS" \
  -ex "p/x *(uint32_t*)0x50000E10"  # DWC2_OTG_GINTSTS — 全局中断状态
  -ex "monitor resume" \
  rt-thread.elf
```

**关键指标**：
- **DIEPCTL bit 31 = EPENA** → 端点是否使能
- **DIEPTSIZ.XFRSIZ** → 待传输大小
- **DIEPTSIZ.PKTCNT** → 待传输包数

---

## 第 5 层：主循环状态（最常见的根因）

```bash
gdb -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x rtt_dbg_main_loop_entry_called" \
  -ex "p/x rtt_dbg_main_loop_iterations" \
  -ex "p/x rtt_dbg_hal_run_called" \
  -ex "p/x AP_HAL::millis()" \
  -ex "monitor resume" \
  rt-thread.elf
```

**关键指标**：
- `rtt_dbg_main_loop_entry_called = 0x12345678` → `_main_loop_entry` 已进入
- `rtt_dbg_main_loop_iterations = 0` → **setup() 未完成！这是最典型的根因**
- `rtt_dbg_hal_run_called = 0xBBBBBBBB` → 在 run() 中 serial init 完成但 setup() 未返回
- `rtt_dbg_hal_run_called = 0x11111111` → setup() 已完成，主循环在运行

**如果 `main_loop_iterations = 0`** → 所有 MAVLink 心跳都不会发送。因为 `gcs().update_send()` 只在主循环中调用。

---

## 第 6 层：RT-Thread 线程状态

```bash
# 检查主线程状态
gdb -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x ((RTT::Scheduler*)0x20018f2c)->_main_thread_id->stat" \
  -ex "p/x ((RTT::Scheduler*)0x20018f2c)->_main_thread_id->error" \
  -ex "p/x ((RTT::Scheduler*)0x20018f2c)->_main_thread_id->sched_thread_ctx.remaining_tick" \
  -ex "monitor resume" \
  rt-thread.elf
```

**关键指标**：
- `stat = 4` → `RT_THREAD_SUSPEND`（挂起/休眠中）— 正常，delay() 期间
- `stat = 0` → `RT_THREAD_INIT`（未启动）
- `error = -4` → `RT_ETIMEOUT`（超时）或 `RT_EINTR`（中断）
- `remaining_tick` 递减 → 线程在推进

---

## 根因模式总结

| 模式 | 特征 | 根因 |
|------|------|------|
| **A: setup() 阻塞** | main_loop_iterations=0, hal_run_called=0xBBBBBBBB, millis 持续递增 | setup() 中 delay() 过多、某个模块初始化卡住 |
| **B: CDC TX 路径阻塞** | send_packet_count>0 且递增, 但 CDC 无数据 | CherryUSB tx_active 卡死、EPENA 粘滞、主机不轮询 |
| **C: 无 GCS 后端** | _num_gcs=0, send_packet_count=0 | SerialManager 未配置 MAVLink 协议、GCS init 未执行 |
| **D: USB 未枚举** | /dev/ttyACM1 不存在, usb_device_is_configured=0 | DWC2 初始化失败、USB 硬件问题 |

---

## Reference

- CDC debug counters defined in: `modules/rt-thread/components/drivers/usb/cherryusb/platform/rtthread/usbd_serial.c`
- UART driver: `libraries/AP_HAL_RTT/UARTDriver.cpp`
- GCS MAVLink: `libraries/GCS_MAVLink/GCS_Common.cpp`
- HAL entry: `libraries/AP_HAL_RTT/HAL_RTT_Class.cpp`
