# CDC TX Historical Attempts (Archived Reference)

> **Status**: Historical — superseded by `rtt-cdc-in-timeout-recovery`
> **Original skill**: `rtt-cuav-v5-cdc-tx-fix` (deleted 2026-05-17)
> **Reason**: This content documents 9 failed approaches and 3 distinct CDC bugs that were later traced to the CherryUSB init + GCCFG root cause. Retained as reference for counter addresses and DTR diagnostics.

## 9 Failed EPENA Race Approaches (2026-04-18)

[Snip — 9 attempts with PRIMASK, EPENA, LDREX/STREX, ISR-only, etc.]

## DTR Ringbuffer Reset Bug

When host opens/closes /dev/ttyACM*, `usbd_cdc_acm_set_dtr()` resets TX ringbuffer → data lost.

**Fix**: Remove `rt_ringbuffer_reset(&serial->tx_rb)` from both DTR=1 and DTR=0 branches.

## IN-Endpoint ISR Stall

`tx_active` stuck at 1 → XFRC never fires → no USB IN transfers.

## Memory Map — CDC Debug Counters

| Address | Name | Meaning |
|---------|------|---------|
| `0x2001e280` | `dbg_serial_write_calls` | Total write() calls |
| `0x2001e284` | `dbg_serial_write_notcfg` | Writes skipped |
| `0x2001e288` | `dbg_serial_write_ok` | Successful writes |
| `0x2001e28c` | `dbg_serial_write_timeout` | Write timeouts |
| `0x2001e274` | `dbg_serial_tx_kick` | TX endpoint kick count |
| `0x2001e278` | `dbg_serial_tx_kick_fail` | TX kick failures |
| `0x200199f0` | `rtt_uart_dbg_drain_bytes` | Total bytes drained |
| `0x200199f4` | `rtt_uart_dbg_drain_calls` | Drain function calls |
| `0x200199f8` | `rtt_uart_dbg_drain_writes` | Drain loops wrote data |
| `0x200199fc` | `rtt_uart_dbg_drain_zero` | Drain loops wrote 0 |

## DTR Diagnosis Quick-Reference

| g_dtr_active | dbg_dtr_set_cnt | dbg_serial_write_ok | dbg_iepint_ep1_xfrc | Conclusion |
|-------------|----------------|-------------------|--------------------|------|
| 0 | 0 | X | 0 | No process opened port |
| 1 | >0 | >0 | >0 | DTR ON, TX running |
| 1 | >0 | >0 | 0 | IN transfer never completes |
| 1 | >0 | 0 | 0 | Wrong CDC device name |
| 1 | >0 | >0 | =tx_kick | Perfect TX |
