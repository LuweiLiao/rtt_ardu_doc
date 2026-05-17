# CDC TX 计数器读取与诊断 — pyOCD + 远/近场调试

## 场景

固件已烧录，MCU 正常运行但 MAVLink 心跳收不到。需要诊断 CDC TX 链的断裂点。

## 快速读取全部 CDC 计数器

```bash
# 1. 构建命令文件
cat > /tmp/cdc_diag.cmd << 'PYCMD'
halt
# CherryUSB 计数器
read32 0x2001e280 4    # dbg_serial_write_calls
read32 0x2001e274 4    # dbg_serial_tx_kick
read32 0x2001e278 4    # dbg_serial_tx_kick_fail
read32 0x2001e24c 4    # dbg_serial_bulkin_cnt ← 关键：USB 实际发送包数
read32 0x2001e250 4    # dbg_serial_bulkout_cnt
read32 0x2001e258 4    # dbg_serial_rb_put_bytes
# ArduPilot UART driver 计数器
read32 0x200199f0 8    # drain_bytes/drain_calls/drain_writes/drain_zero
read32 0x20019a28 4    # rtt_uart_dbg_tick_calls
reg pc
status
PYCMD

# 2. 等 ST-Link 就绪
pkill -9 openocd pyocd 2>/dev/null; sleep 3

# 3. 执行
cat /tmp/cdc_diag.cmd | timeout 12 pyocd commander -t STM32F767ZI
```

## 解释结果

### 模式 A：正常传输
```
write_calls ≈ tx_kick ≈ bulkin_cnt  (都在增长，数量级一致)
drain_bytes 持续增长
```
→ CDC TX 链完整。问题在上游（MAVLink 调度层）。

### 模式 B：`_check_usb_connected()` 门控跳过（2026-05-11 发现）
```
write_calls > 0, write_ok = write_calls
drain_bytes ≈ 0, drain_calls ≈ 0
```
→ `UARTDriver::_timer_tick()` 因 `_check_usb_connected()` 返回 false 而跳过 drain。
→ 修复：移除 gate 或加重试。

### 模式 C：IN 端点 ISR 挂起（2026-05-12 发现）
```
write_calls ≥ 200, tx_kick ≈ 3, bulkin_cnt ≈ 2, drain_bytes ≥ 40KB
```
→ 数据写入 ringbuffer、drain 成功、但 USB IN 端点 `tx_active` 卡在 1。
→ `kick_tx()` 被 `if (serial->tx_active) return;` 拦住，ISR 不再触发。
→ 修复：在 kick_tx 中加超时回退，或审计 DWC2 XFRC 中断。

## 注意

- `read32` 的 length 参数必须能被 4 整除（word-aligned）
- `read32 0x2001e280 4` 一次读 4 个 32-bit 字 = 16 字节 = 4 个计数器
- pyOCD commander 的连接是排他的 — 同时只能有一个实例
- 如果 `Resource busy`，先 `pkill -9 pyocd openocd`
