# CDC 无心跳 Debug Counters 分析（2026-05-12）

## 场景

MCU 正常运行（CFSR=0, HFSR=0），USB CDC 已枚举（/dev/ttyACM0 存在），
但 MAVLink 心跳收不到。cat/xxd 读 ttyACM0 无数据。

## 诊断步骤

### 1. pyOCD 读调试计数器

```bash
printf "halt\n\nread32 0x2001e280 4\nread32 0x200199f0 8\nread32 0x2001e274 2\n" \
  | pyocd commander -t STM32F767ZI
```

### 2. 解读计数器

| 计数器 | 正常值 | 本会话发现值 | 含义 |
|--------|--------|-------------|------|
| `dbg_serial_write_calls` | >0 | **321** | AP_HAL 正在写入 CDC 缓冲 |
| `dbg_serial_write_ok` | = write_calls | **321** | 所有写入操作成功 |
| `dbg_serial_write_notcfg` | 0 | **0** | 无"未配置"拒绝 |
| `dbg_serial_write_timeout` | 0 | **0** | 无超时 |
| `dbg_serial_tx_kick` | >0 | — | USB TX 端点被启动次数 |
| `rtt_uart_dbg_drain_calls` | >0 | **≈0** | ⚠️ drain 几乎从未运行！ |
| `rtt_uart_dbg_drain_bytes` | >0 | **≈0** | ⚠️ 数据从未到达设备 |

### 3. 模式识别

**关键模式**：`write_calls >> 0` 但 `drain_calls ≈ 0`

这表明：
- ✅ AP_HAL 正确调用 `write()`，数据进入 `_writebuf`
- ❌ `_drain_writebuf_to_dev()` 几乎从未执行
- 数据在用户态缓冲中堆积，从未到达 USB 硬件

### 4. 根因定界

`UARTDriver::_timer_tick()` 在 `libraries/AP_HAL_RTT/UARTDriver.cpp` 中：

```cpp
void UARTDriver::_timer_tick(void) {
    // ...
    if (!_initialized) {
        // 尝试 deferred open
        if (_deferred_open && ...) { ... }
        return;  // ← 未初始化时直接返回
    }

    if (_is_usb && !_check_usb_connected()) {
        return;  // ← 跳过了 drain！
    }

    _drain_rx_to_readbuf();
    _drain_writebuf_to_dev();  // ← 从未被执行！
}
```

### 5. 验证方向

- 检查 `_initialized` 是否在 `_begin()` 中被设置
- 检查 `_deferred_open` 是否在 `_timer_tick()` 中被重试
- 检查 `g_usbd_core[0].configuration` 的值

## 参考

- `libraries/AP_HAL_RTT/UARTDriver.cpp` — `_timer_tick()` 和 `_check_usb_connected()`
- `modules/rt-thread/components/drivers/usb/cherryusb/core/usbd_core.c:1265` — `usb_device_is_configured()`
- `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/ports/cherryusb/cherryusb.c:129` — CDC初始化后验证 `rt_device_find("usb-acm0")`
