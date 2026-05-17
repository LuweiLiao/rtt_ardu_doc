# CDC TX EPENA 卡死诊断与壁钟超时修复

> 2026-05-13 会话发现。首次 `usbd_ep_start_write()` 的 XFRC ISR 丢失后，DWC2 IN 端点 EPENA 永久=1，所有后续数据在 ringbuffer 中但永不发送。

## 诊断变量（须在 usbd_serial.c 中定义）

```c
volatile uint32_t dbg_serial_write_calls = 0;   // write() 被调用次数
volatile uint32_t dbg_serial_write_ok = 0;       // write() 成功写入 ringbuffer 次数
volatile uint32_t dbg_serial_tx_kick = 0;        // kick_tx() 被调用次数
volatile uint32_t dbg_serial_bulkin_cnt = 0;     // bulk_in XFRC ISR 触发次数
volatile uint32_t dbg_serial_unstick_cnt = 0;    // 壁钟自愈触发次数
```

在关键位置递增：
- `write()` 入口 → `write_calls++`
- `write()` 中 `_tx_buffer.write()` 成功 → `write_ok++`
- `kick_tx()` 入口 → `tx_kick++`
- `bulk_in()` ISR 入口 → `bulkin_cnt++`
- 自愈代码路径 → `unstick_cnt++`

## CDC TX Five-Counter Reading

```bash
echo -e "halt
mdw 0x2001e2a0 1    # write_calls
mdw 0x2001e29c 1    # write_ok
mdw 0x2001e294 1    # tx_kick
mdw 0x2001e26c 1    # bulkin_cnt
mdw 0x2001e278 1    # unstick_cnt
resume\nexit" | timeout 10 nc localhost 4444 2>&1 | grep "^0x"
```

## 实际诊断记录 (2026-05-13)

```
write_calls  = 311  (0x00000137)  — 311 次写入尝试
write_ok     = 311  (0x00000137)  — 全部写入 ringbuffer 成功
tx_kick      = 1    (0x00000001)  — 仅第一次写了 cs_enter
bulkin_cnt   = 0    (0x00000000)  — ISR 从未触发！
unstick_cnt  = 0    (0x00000000)  — 自愈未触发
```

**解读**：
1. 311 次 `write()` 全部成功进入 ringbuffer
2. `kick_tx()` 在第一次 write 时被调用 → `tx_kick=1`
3. 第一次 `usbd_ep_start_write()` 启动后，bulk_in ISR **从未触发** → `bulkin_cnt=0`
4. `tx_active` 永远 = 1（ISR 未清）
5. 自愈仅检查 `!EPENA` 路径 → 未触发 → `unstick_cnt=0`
6. DWC2 DIEPCTL 寄存器中 EPENA=1（端点认为在传输）

## 壁钟超时修复（已部署至 usbd_serial.c）

```c
// 在 write() 中，调用 usbd_ep_start_write() 之后
if (serial->tx_active) {
    uint32_t start_tick = rt_tick_get();
    while (serial->tx_active && (rt_tick_get() - start_tick) < RT_TICK_MS(50)) {
        /* 忙等 50ms 让 ISR 有机会完成 */
    }
    if (serial->tx_active) {
        uint8_t ep_idx = serial->in_ep & 0x7F;
        DWC2_OTG_INEndPoint *inep = DWC2_INEP(ep_idx);
        if (inep->DIEPCTL & USB_OTG_DIEPCTL_EPENA) {
            /* EPENA=1: 强制恢复 */
            inep->DIEPCTL |= USB_OTG_DIEPCTL_SNAK;
            /* 等待 CNAK 清零 */
            inep->DIEPCTL &= ~USB_OTG_DIEPCTL_EPENA;
            serial->tx_active = 0;
        } else {
            /* EPENA=0: 传输已完成但 ISR 丢失 */
            serial->tx_active = 0;
        }
        dbg_serial_unstick_cnt++;
    }
}
```

## 相关文件

- `modules/rt-thread/components/drivers/usb/cherryusb/platform/rtthread/usbd_serial.c` — CDC 串口驱动
- `modules/rt-thread/components/drivers/usb/cherryusb/core/usbd_core.c` — 端点操作（`usbd_ep_recover_stuck` 等）
