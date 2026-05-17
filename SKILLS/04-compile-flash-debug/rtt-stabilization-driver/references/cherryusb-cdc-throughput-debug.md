# CherryUSB CDC Throughput Debug

## Data Flow Path

```
MAVLink → GCS_MAVLINK::send_message() → comm_send_buffer() → 
  UARTDriver::_write() → _writebuf (8KB ringbuffer) → 
    ap_uart thread (1kHz): _timer_tick() → _drain_writebuf_to_dev() →
      rt_device_write() → usbd_serial_write() → tx_rb (32KB ringbuffer) →
        usbd_serial_kick_tx() → 64-byte USB FS Bulk IN transfer →
          DWC2 TX FIFO → USB OTG FS (PA11/PA12) → Host
```

## ⚠️ [2026-05-11 — CORRECTED] `_check_usb_connected()` Is a NECESSARY Rate Limiter

### Correction: Previous Analysis Was Wrong

Earlier analysis identified `_check_usb_connected()` as the root cause of low MAVLink throughput. **This was incorrect.** After systematic testing:

| Test | RAW_IMU | ATTITUDE | Conclusion |
|------|---------|----------|------------|
| **Baseline** (gate ON, tx_rb=32KB, writebuf=8KB) | **2.6 Hz** | **6.5 Hz** | Optimal baseline |
| Remove gate (drain every tick) + tx_rb=4KB | **0.8 Hz** ❌ | 0.9 Hz | WORSE |
| Remove gate + writebuf=32KB | **0.8 Hz** ❌ | 0.7 Hz | WORSE |

**The gate is a NECESSARY implicit rate limiter**, not a bug to fix.

### Why the Gate Is Beneficial

| Scenario | UART drain rate | USB ISR drain rate | Net | Result |
|----------|----------------|-------------------|-----|--------|
| **Gate ON** (~10% ticks pass) | 100 t/s × 512B = **51 KB/s** | 64 KB/s | ISR faster (13 KB/s surplus) | tx_rb never full → writebuf drains steadily → MAVLink ~6.5 Hz |
| **Gate OFF** (every tick) | 1000 t/s × 512B = **512 KB/s** | 64 KB/s | UART 8x faster | tx_rb fills in 8ms → drain_zero 64ms → writebuf blocks → MAVLink ~0.8 Hz |

The gate reduces drain frequency from 1kHz to ~100Hz, matching the CherryUSB ISR's throughput (64 KB/s). Without it, every drain tick writes 512B, filling the 4KB tx_rb in 8ms, then blocking for 64ms while the ISR drains at 64 B/ms — creating a drain blackout that starves MAVLink.

### Baseline Characterization (commit a632415295)

| Metric | Rate | % of configured |
|--------|------|----------------|
| **ATTITUDE** (STREAM_EXTRA1, set 10 Hz) | **6.5 Hz** | **65%** |
| **RAW_IMU** (STREAM_RAW_SENSORS, set 10 Hz) | **2.6 Hz** | **26%** |
| VFR_HUD / SCALED_PRESSURE | 2.6 Hz | 26% |
| HEARTBEAT (dual source) | 1.4 Hz | 140% |
| AHRS (STREAM_EXTRA3, set 2 Hz) | 1.4 Hz | 70% |

Rates are limited by the MAVLink scheduling architecture (`GCS_MAVLINK::update_send()`), not by buffer or drain issues.

### Verified Non-Factors

| Claimed bottleneck | Test result |
|--------------------|-------------|
| `_check_usb_connected()` filtering 90% of drain calls | **Gate is beneficial** — removal made things worse |
| tx_rb too large (32KB) causing bufferbloat | Smaller (4KB) made throughput worse |
| writebuf too small (8KB) | 32KB made zero difference (identical rates) |
| UART priority 6 too low | Priority 5 broke GCS init (race condition) |

### Actual Root Cause: MAVLink Stream Scheduling

`GCS_MAVLINK::update_send()` uses round-robin scheduling with a 5ms budget:
- Called at ~167 Hz (from `scheduler_delay_callback()` every 4ms)
- Each call sends one deferred message, then continues or breaks
- With ~100 deferred message types, each gets ~1.6 turns/sec
- After `check_payload_size()` filtering: ~25-65% of turns succeed

The 2.6-6.5 Hz for 10 Hz streams is the inherent RTT MAVLink scheduling performance. To reach 10 Hz, a dedicated GCS TX thread (matching ChibiOS) would be needed.

## Debug Counters

All counters are `volatile uint32_t` — read via OpenOCD `mdw <address>`.

### RTT UART Drain Counters (in `UARTDriver.cpp`)

| Counter | Address (build-dependent) | Meaning |
|---------|--------------------------|---------|
| `rtt_uart_dbg_drain_bytes` | nm for `rtt_uart_dbg_drain_bytes` | Total bytes written to CherryUSB tx_rb |
| `rtt_uart_dbg_drain_calls` | nm for `rtt_uart_dbg_drain_calls` | Total rt_device_write() calls |
| `rtt_uart_dbg_drain_writes` | nm for `rtt_uart_dbg_drain_writes` | Calls where write returned > 0 |
| `rtt_uart_dbg_drain_zero` | nm for `rtt_uart_dbg_drain_zero` | Calls where write returned 0 (tx_rb full) |
| `rtt_uart_dbg_tick_calls` | nm for `rtt_uart_dbg_tick_calls` | Total _timer_tick() calls (ALL ports) |
| `rtt_uart_dbg_port_ticks[0-9]` | nm for `rtt_uart_dbg_port_ticks` | Per-port tick counter — identifies _check_usb_connected filtering |

**Interpretation**:

```
throughput = drain_bytes / elapsed_seconds   [bytes/s]
success_rate = drain_writes / drain_calls     [%]
congestion_rate = drain_zero / drain_calls    [%]
# 🆕 New critical metric:
drain_efficiency = drain_calls / (port_ticks[0] * avg_writes_per_drain)
  where avg_writes_per_drain ≈ tx_rb_size / TX_BOUNCE_BUFFER_SIZE
  If < 50% → _check_usb_connected() is the bottleneck
```

A healthy system at 10Hz MAVLink streams should show:
- `drain_bytes` growing at ~5-10 KB/s
- `congestion_rate` < 50%

Our measured values: `congestion_rate = 72%` (24,261 / 87,169) indicates CherryUSB tx_rb is frequently full — but the PRIMARY issue is only 1.2% of ticks actually reaching _drain_writebuf_to_dev().

### CherryUSB CDC Counters (in `usbd_serial.c`)

| Counter | Meaning |
|---------|---------|
| `dbg_serial_bulkin_cnt` | USB IN transfer completions |
| `dbg_serial_tx_kick` | Calls to `usbd_serial_kick_tx()` |
| `dbg_serial_tx_kick_fail` | Failed `usbd_ep_start_write()` calls |
| `dbg_serial_timeout_tx_active` | Times `tx_active` was stuck when full |
| `dbg_serial_unstick_cnt` | Endpoint recovery events |
| `dbg_serial_rb_put_bytes` | Total bytes written to tx_rb ringbuffer |

## Read Counters from Running Board

```bash
# 1. Find counter addresses
arm-none-eabi-nm -n build/rtt_deploy/cuav_v5/rt-thread.elf | grep -iE \
  'rtt_uart_dbg|dbg_serial'

# 2. Halt and read (example addresses — always re-run nm!)
echo 'halt
mdw 0x20019c18   # drain_bytes (PORT0)
mdw 0x20019c1c   # drain_calls
mdw 0x20019c20   # drain_writes
mdw 0x20019c24   # drain_zero
mdw 0x20019c50   # tick_calls
mdw 0x2001e4b4   # tx_kick
mdw 0x2001e4b0   # timeout_tx_active
resume' | nc -w 2 localhost 4444
```

**⚠️ Addresses change after every rebuild.** Always run `nm` first.

## CherryUSB Write Path (usbd_serial.c)

### `usbd_serial_write()` (called from `rt_device_write()`)

```c
static rt_ssize_t usbd_serial_write(struct rt_device *dev,
                                    rt_off_t pos,
                                    const void *buffer,
                                    rt_size_t size)
{
    // 1. Check USB configured
    if (!usb_device_is_configured(serial->busid)) {
        return -RT_EPERM;
    }

    // 2. Write to tx_rb ringbuffer (NON-BLOCKING, 32KB)
    rt_size_t written = rt_ringbuffer_put(&serial->tx_rb, buffer, size);

    // 3. Kick USB transfer
    if (written > 0) {
        usbd_serial_kick_tx(serial);
    } else {
        // 4. Self-heal if endpoint stuck
        if (serial->tx_active) {
            // Check DWC2 EPENA — if idle, clear tx_active and retry
            if (ep_idx && !(diepctl & USB_OTG_DIEPCTL_EPENA)) {
                serial->tx_active = 0;
                usbd_serial_kick_tx(serial);
            } else if (++tx_stuck_counter > 100) {
                usbd_ep_recover_stuck(serial->busid, serial->in_ep);
            }
        }
    }
    return written;
}
```

**Key insight**: `rt_ringbuffer_put` returns the number of bytes actually written. If tx_rb is full, returns 0. No blocking.

### `usbd_serial_kick_tx()` — the transfer throttle

```c
static void usbd_serial_kick_tx(struct usbd_serial *serial)
{
    // Atomic tx_active guard — only ONE transfer at a time
    if (serial->tx_active) {
        return;  // ISR will re-kick when transfer completes
    }
    serial->tx_active = 1;

    // Read max 64 bytes from tx_rb
    uint16_t mps = usbd_get_ep_mps(serial->busid, serial->in_ep); // = 64 (USB FS)
    uint16_t to_send = min(avail, mps);
    rt_ringbuffer_get(&serial->tx_rb, serial->tx_pkt, to_send);

    // Start USB IN transfer
    int ret = usbd_ep_start_write(serial->busid, serial->in_ep, serial->tx_pkt, got);
    if (ret < 0) {
        serial->tx_active = 0;  // release for retry
    }
}
```

**Key limitation**: Only 64 bytes per USB IN transfer. Next transfer starts in ISR callback, 1ms later (USB FS SOF interval). Theoretical max: 64 KB/s.

### Race Condition: Missed Transfer

Between `rt_ringbuffer_put` (new data added) and `usbd_serial_kick_tx`, if the ISR completes the previous transfer and finds tx_rb empty (before new data), then calls `tx_active=0` + `kick_tx` which does nothing (no data), the new data sits without a pending IN transfer. It waits for the next `rt_ringbuffer_put` call (i.e., next `_drain_writebuf_to_dev` call in 1ms or next `_write()` call).

### `tx_active` Self-Heal Bug

The `tx_stuck_counter` is only incremented when `written == 0` in `usbd_serial_write()`. 
If `write()` always succeeds (data enters ringbuffer), the counter is always reset to 0, 
so recovery never triggers even when TX endpoint is permanently stuck.

## UART Thread Priority

```c
// Scheduler.h
#define APM_RTT_MAIN_PRIORITY     5    // RT-Thread: lower = higher priority
#define APM_RTT_UART_PRIORITY     6    // LOWER than main! Cannot preempt main!
```

In RT-Thread, UART at priority 6 has LOWER priority than main at 5. The UART thread only runs when main thread is sleeping (between main loop iterations). This is by design — UART should not preempt main during critical computation.

**Effect**: UART thread gets ~83% of CPU time (main sleeps 2.44ms out of 2.94ms loop at 340 Hz). Should be sufficient for normal operation.

**⚠️ [2026-05-11] Setting UART to priority 5 causes GCS init race condition**: See 🆕 section above for details.

## GCS update_send Timing

### RTT: Called once per main loop

```cpp
// HAL_RTT_Class.cpp — main loop
for (;;) {
    a->callbacks->loop();       // main loop work
    a->sched->call_delay_cb();  // GCS update_send() + update_receive()
}
```

- `call_delay_cb()` calls `GCS_MAVLINK::update_send()` → sends ONE message per loop
- At 340 Hz main loop → 340 messages/s → each of ~8 message types gets ~42 Hz
- Stream rate limiters (100ms = 10Hz) cap individual message rates

### ChibiOS: Called from delay() — multiple times per loop

```cpp
// ChibiOS Scheduler::delay(uint16_t ms)
while (ms--) {
    chThdSleep(1);          // 1ms sleep
    call_delay_cb();         // GCS update_send() every ms
}
```

- delay() typically runs for 1-3ms per main loop iteration
- `update_send()` called 1-3 times per main loop iteration
- At 1kHz → 1000 messages/s theoretical

**Implication**: RTT gets fewer `update_send()` calls than ChibiOS, but 340 Hz is still enough for all stream rates (≤10 Hz). The difference doesn't explain low message rates.

## MAVLink Stream Rate Initialization

RTT-specific code in `GCS_Common.cpp:7040-7061` seeds stream rates:

```cpp
#if CONFIG_HAL_BOARD == HAL_BOARD_RTT && APM_BUILD_COPTER_OR_HELI
    if (all_stream_rates_zero) {
        streamRates[STREAM_RAW_SENSORS].set(10);     // IMU, baro, mag: 10 Hz
        streamRates[STREAM_EXTENDED_STATUS].set(5);   // sys_status: 5 Hz
        streamRates[STREAM_RC_CHANNELS].set(5);       // RC: 5 Hz
        streamRates[STREAM_POSITION].set(5);          // GPS: 5 Hz
        streamRates[STREAM_EXTRA1].set(10);           // attitude: 10 Hz
        streamRates[STREAM_EXTRA2].set(5);            // VFR_HUD: 5 Hz
        streamRates[STREAM_EXTRA3].set(2);            // EKF/AHRS: 2 Hz
    }
#endif
```

**Expected output at 10 Hz**: RAW_IMU ~10 Hz, ATTITUDE ~10 Hz, SCALED_PRESSURE ~10 Hz

## Measured Throughput Analysis

From actual board counters (87s uptime):

| Metric | Value | Implication |
|--------|-------|-------------|
| `drain_bytes` | 1,340,167 | 15.4 KB/s throughput |
| `drain_writes` | 24,261 | 28% success rate |
| `drain_zero` | 62,908 | 72% congestion (tx_rb full) |
| `tx_kick` | 1,931 | USB IN transfers submitted |
| `timeout_tx_active` | 303 | Endpoint stuck, self-healed |
| `unstick_cnt` | 3 | Full recovery needed |

**Conclusion**: USB FS provides ~64 KB/s max throughput. Actual MAVLink data at 10 Hz needs ~5 KB/s. The 15 KB/s measured throughput is more than enough. Low message rate (1.4 Hz RAW_IMU) is NOT due to USB bandwidth — it's `_check_usb_connected()` filtering.

## Diagnostic Approach

### For Low Message Rate

```
1. Read counters → throughput OK? 
   YES → Problem is `_check_usb_connected()` (compare port_ticks[0] vs drain_calls)
   NO → Check CherryUSB tx_rb full or USB FS bandwidth

2. Check drain_efficiency ratio:
   drain_calls / (port_ticks[0] × 8) < 0.5 → _check_usb_connected() is the bottleneck
   
3. Read SR0_* stream rate parameters (via PARAM_REQUEST_READ)
   Unexpectedly low? → Seed code failed or parameters overridden
   At expected value? → Problem in message scheduling

4. Record update_send() calls rate (RTT ≈ main_loop_rate, ChibiOS = 1kHz)
   Low rate but stream rates fine? → Check HAVE_PAYLOAD_SPACE in MAVLink

5. Check txspace() value (read from GDB or OpenOCD)
   Always 0? → writebuf full → CherryUSB backpressure
   Always > 64? → writebuf OK → problem in GCS scheduling
```

## CherryUSB Buffer Sizing Tradeoffs

| Parameter | Current | Options |
|-----------|---------|---------|
| `CONFIG_USBDEV_SERIAL_TX_BUFSIZE` | 32768 (32KB) | **Reduce to 2048-4096** for lower latency |
| RTT `_writebuf` for USB | 8192 (8KB) | Keep — set_size_best ensures allocation |
| USB FS MPS | 64 | Hardware limit — cannot change |
| EP1 IN FIFO | 128 words (512B) | Increase from DWC2 FIFO pool (320 total) |

**Reducing tx_rb from 32KB to 4KB**: Lowers bufferbloat from 625ms to 78ms at 64 KB/s drain rate. Data flows faster through the pipeline → txspace() recovers faster after bursts → MAVLink resumes sending sooner.

## ChibiOS vs CherryUSB Key Differences

| Aspect | ChibiOS (SDU) | CherryUSB (RTT) |
|--------|---------------|-----------------|
| USB write | `chnWriteTimeout(TIME_IMMEDIATE)` | `rt_ringbuffer_put()` → async 64B IN transfer |
| Buffering | DWC2 TX FIFO only (~256B) | tx_rb (32KB) + DWC2 TX FIFO (~512B) |
| Write behavior | Returns bytes written to FIFO (may be partial) | Returns bytes put in tx_rb (always full if space) |
| TX threads | Per-port dedicated thread | Single shared thread for all 10 ports |
| Wakeup | Event-driven + 1kHz polling | 1kHz polling only |
