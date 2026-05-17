# USB CDC TX 缓冲区对 QGC 连接稳定性的影响

## 症状

QGroundControl 连接后，参数获取（PARAM_REQUEST_LIST）卡顿、频繁断连重连。

## 根因分析

### 数据流

```
ArduPilot GCS 线程
  → UARTDriver::_write() → _writebuf (8KB)
    → _drain_writebuf_to_dev() [1kHz timer tick]
      → rt_device_write() → CherryUSB ring buffer (原 4KB)
        → DWC2 TX FIFO EP1 (128 bytes)
          → USB FS Bulk IN (64-byte packets)
            → QGC (宿主机 USB 接收)
```

### 瓶颈

1. **CherryUSB 环形缓冲 4KB** — 参数列表突发 ~537 × ~20B = ~10KB 远超 4KB
2. **`rt_device_write()` 返回 0** — 环形缓冲满时拒绝写入
3. **`_last_drain_wrote = false`** — 写入失败计数器 +1
4. **阈值 500 tick (500ms)** — 超过后清空整个 `_writebuf` → 参数数据丢失
5. QGC 收不到所有 PARAM_VALUE → 超时 → 断连重连

### 验证方法

GDB 检查 USB CDC 调试计数器：
```gdb
p dbg_iepint_calls     # IEP interrupt 调用次数
p dbg_txfe_ep1_calls   # TXFE (TX FIFO empty) 调用次数
p dbg_txfe_ep1_wrote   # TXFE 实际写入次数
p dbg_ep_busy_cnt      # 端点繁忙计数
p dbg_ep_recover_cnt   # 端点恢复计数
```

若 `dbg_ep_busy_cnt` 快速增长，说明 USB 端点在参数突发时频繁饱和。

## 修复方案

### 修复 1：增加 CherryUSB TX 缓冲大小

**文件**: `libraries/AP_HAL_RTT/hwdef/common/board/ports/cherryusb/usb_config.h`

```diff
- #define CONFIG_USBDEV_SERIAL_TX_BUFSIZE 4096
+ #define CONFIG_USBDEV_SERIAL_TX_BUFSIZE 32768
```

32KB 足够容纳完整参数列表突发（~10KB），并留有余量给同时进行的传感器流数据。

### 修复 2：增加写入失败清除阈值

**文件**: `libraries/AP_HAL_RTT/UARTDriver.cpp`

```diff
- if (_usb_write_fail_count > 500) {    // 500 ticks = 500ms
+ if (_usb_write_fail_count > 5000) {   // 5000 ticks = 5s
```

提高阈值避免在宿主短暂滞后时错误清空缓冲。5 秒的容错期足够 USB 端点恢复。

### 相关代码路径

**UARTDriver::_drain_writebuf_to_dev()** (line 340-385):
```cpp
for (;;) {
    uint32_t n = _writebuf.peekbytes(_tx_bounce, sizeof(_tx_bounce));
    if (n == 0) { _last_drain_wrote = true; return; }
    rt_size_t w = rt_device_write(_dev, 0, _tx_bounce, n);
    if (w > 0) {
        _writebuf.advance(w);
        _last_drain_wrote = true;
        if (is_usb) continue;  // USB 需要持续灌数据
        return;
    } else {
        _last_drain_wrote = false;
        return;  // 设备缓冲满
    }
}
```

**UARTDriver::_timer_tick()** (line 546-564):
```cpp
if (_is_usb) {
    if (_writebuf.available() == 0) {
        _usb_write_fail_count = 0;
    } else if (!_last_drain_wrote) {
        _usb_write_fail_count++;
        if (_usb_write_fail_count > 5000) {   // 修改后
            _writebuf.clear();                // 真的死锁时才清空
            _usb_write_fail_count = 0;
        }
    } else {
        _usb_write_fail_count = 0;
    }
}
```

## 定量基准测试脚本

`scripts/param_fetch_bench.py` 自动化参数获取性能测试，输出：

- 参数获取速率 (params/s)
- "突发-停顿"模式检测（停顿 ≥3 秒标记）
- 每次突发包含的参数数量和速率
- 自动化断诊断建议

用法：
```bash
python3 /home/llw/.hermes/skills/embedded/rtt-cuav-v5-flash-verify/scripts/param_fetch_bench.py [port=/dev/ttyACM1]
```

## "突发-停顿"诊断模式（2026-05-09）

当参数以"突发几百个→停顿3-4秒→再突发几百个"的模式到达时，说明：

```
CherryUSB TX 缓冲 (32KB) 有容量，但 USB IN 端点不被宿主及时轮询
  → DWC2 FIFO EP1 IN (仅 128 字节) 填满
    → USB 硬件缓冲满
      → CherryUSB 环形缓冲不能再写入
        → rt_device_write() 返回 0
          → txspace() 返回 0
            → queued_param_send() 停止发送
              → 3-4 秒后才恢复
```

**关键区分**：

| 模式 | 诊断 | 修复方向 |
|------|------|---------|
| 参数持续到达但慢 | USB 带宽不足（USB FS = 12Mbps） | 增大 DWC2 FIFO |
| 突发→停顿→突发 | 宿主不轮询 IN 端点（远程/虚拟 USB） | 改变连接方式 |
| 偶尔停顿一次 | CherryUSB 缓冲瞬态满 | 已经够用 |
| 0 参数到达 | handle_param_request_list 未触发 | 检查 GCS::update_send |

### 根因层级（2026-05-09 更新）

之前认为 CherryUSB TX 缓冲区 4KB 是唯一瓶颈（已修复到 32KB）。
但即使缓冲足够大，**DWC2 TX FIFO for EP1 IN 只有 128 字节**（32 words）：
```
#define CONFIG_USB_DWC2_TX1_FIFO_SIZE (128 / 4)
```
DWC2 的 FIFO 是硬件缓冲，USB FS 一帧（1ms）只能发 64 字节 bulk。
若宿主因虚拟机/远程桌面等原因延迟轮询 IN 端点，128 字节的 FIFO 迅速填满→
CherryUSB 环形缓冲的写入被 DWC2 拒绝→软件层阻塞→参数枚举卡死。

增大 `CONFIG_USB_DWC2_TX1_FIFO_SIZE` 到 (512/4) 可让硬件缓冲更多数据，
减少回压概率。但需要注意总 FIFO 320 words 的限制。

### 测试方法

1. 烧录后，用 QGC 连接，检查参数列表是否完整获取（不卡顿、不断连）
2. 用 `param_fetch_bench.py` 定量测试：
   ```bash
   python3 scripts/param_fetch_bench.py /dev/ttyACM1
   ```
   期望：≥530 参数，无 ≥3 秒的停顿
3. 或用 pymavlink 简易验证：
   ```python
   import pymavlink.mavutil as mavutil
   c = mavutil.mavlink_connection('/dev/ttyACM1', baud=921600)
   c.wait_heartbeat()
   c.mav.param_request_list_send(c.target_system, c.target_component)
   count = 0
   for _ in range(600):
       m = c.recv_match(type='PARAM_VALUE', blocking=True, timeout=1)
       if m: count += 1
   print(f'Got {count} parameters')  # 应 ≥530
   ```

## 相关文件

- `libraries/AP_HAL_RTT/hwdef/common/board/ports/cherryusb/usb_config.h` — CherryUSB 配置
- `libraries/AP_HAL_RTT/UARTDriver.cpp` — UART 驱动（写入缓冲、定时器 drain）
