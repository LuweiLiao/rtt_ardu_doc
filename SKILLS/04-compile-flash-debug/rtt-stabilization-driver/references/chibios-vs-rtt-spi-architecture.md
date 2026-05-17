# ChibiOS vs RTT SPI Architecture Comparison

## Overview

SPI architecture is the **#1 source of INS init hangs** on RTT. ChibiOS and RTT take fundamentally different approaches to SPI transfers, which explains why the same IMU probe code works on ChibiOS but hangs on RTT (stage 662).

## Critical Difference: Transfer Mode

| Aspect | ChibiOS | RTT (CUAV V5) |
|--------|---------|---------------|
| Transfer mode | **DMA / interrupt-driven** | **Register-level polling** (bus 1 & 4) |
| CPU utilization | Releases CPU during transfer (`chThdSleep`) | **Tight `__NOP()` spin** per byte |
| Thread model | Dedicated SPI bus threads | Main thread directly performs SPI ops |
| CS control | Managed by bus thread + semaphore | Register-level GPIO BSRR writes |
| SPI clock config | Set by ChibiOS HAL at bus init | Reconfigured EVERY transaction (CR1 write) |

## SPI Transfer Code Comparison

### ChibiOS (typical DMA path)

```c
// ChibiOS SPI driver (DMA mode):
// 1. Acquire bus semaphore → may block (yields CPU)
// 2. Configure CS via GPIO
// 3. Start DMA transfer
// 4. chSemWait(&transfer_done) → BLOCKS (yields CPU!)
// 5. DMA completion ISR signals semaphore
// 6. Release CS
// 7. Release bus semaphore
```

### RTT (register-level polling path, SPIDevice.cpp:173-291)

```c
// RTT SPI1/4 polling driver:
// 1. Take SPIDevice semaphore (_sem.take) → may block (yields CPU)
// 2. Reconfigure SPI CR1/CR2 registers (EVERY transfer)
// 3. For EACH byte: (TIGHT LOOPS!)
//    while (!(SPI_SR & TXE) && timeout--) { __NOP(); }  ← NO YIELD
//    SPI_DR = data;
//    while (!(SPI_SR & RXNE) && timeout--) { __NOP(); }  ← NO YIELD
//    data = SPI_DR;
// 4. Wait for BSY=0
//    while ((SPI_SR & BSY) && timeout--) { __NOP(); }  ← NO YIELD
// 5. Release CS
// 6. Release semaphore
```

## SPI Lock Model: The Final Piece (2026-05-16)

**RTT had a TWO-level SPI lock while ChibiOS has only ONE.** This was the last structural misalignment fixed in Phase 1.

### The Bug: Two Different Locks

```cpp
// RTT SPIDevice had:
Semaphore _sem;              // (1) Per-device private lock
DeviceBus *_bus;             // (2) Per-bus lock (via get_semaphore())

// get_semaphore() returned:
return &_bus->semaphore;  // ← Bus-level lock

// BUT transfer() used:
_sem.take(BLOCK_FOREVER);  // ← Device-level lock (DIFFERENT!)
```

**Consequence**: `WITH_SEMAPHORE(dev->get_semaphore())` (used by Invensense driver for register writes) took the **bus** semaphore, but `transfer()` internally took the **device** semaphore. Two independent locks → no mutual exclusion between callers.

### The Fix

All `_sem.take()/give()` references removed from `transfer()`, `transfer_fullduplex()`, and `set_chip_select()`. Replaced with:

```cpp
// ChibiOS SPIDevice.cpp:292-aligned ownership check
_bus->semaphore.assert_owner();

// Then do the transfer (caller is trusted to hold bus semaphore)
spi1_poll_transfer(...);
```

**Files changed** (Phase 1.4, 2026-05-16):
- `libraries/AP_HAL_RTT/SPIDevice.cpp`: 7 `_sem` references → all removed

### Verification

No `_sem.take` or `_sem.give` should remain in SPIDevice.cpp:
```bash
grep -n '_sem\.(take|give)' libraries/AP_HAL_RTT/SPIDevice.cpp
# → (empty)
```

### What Led to This

1. Earlier fix (2026-05-13): `get_semaphore()` changed from `&_sem` to `&_bus->semaphore`
2. Earlier fix (2026-05-13): Bus thread `take(10)` → `take(BLOCK_FOREVER)`
3. **This session**: Remove the now-redundant `_sem` from transfer paths + add `assert_owner()` guard

### ChibiOS confirmation

ChibiOS `SPIDevice.cpp:292`:
```cpp
if (!bus.semaphore.check_owner()) {
    return false;  // Caller doesn't hold bus lock → reject
}
```

RTT now matches this pattern (using `assert_owner()` which panics on mismatch).

## Why RTT's approach blocks the system

### Problem 1: No yield during SPI transfer

Each SPI byte transfer can take up to `timeout=100000` NOP iterations (~460µs at 216MHz). During this spin:

| RTT behavior | ChibiOS behavior |
|---|---|
| Main thread **spins** — no context switch | Main thread **sleeps** — scheduler runs |
| **No other thread runs** (main has highest priority) | Other threads (UART, timer) run freely |
| Timer thread cannot pat IWDG | Timer thread feeds IWDG normally |
| All HAL threads stuck — `tick_calls=0` | Everything continues normally |

### Problem 2: SPI reconfiguration per transfer

RTT's `spi1_poll_transfer()` reconfigures SPI registers on EVERY transaction (line 216-223):
```c
if (cs_take) {
    CLEAR_BIT(spi->CR1, SPI_CR1_SPE);  // Disable SPI
    spi->CR1 = ...;                      // Full reconfig
    spi->CR2 = ...
    SET_BIT(spi->CR1, SPI_CR1_SPE);     // Enable SPI
    // Flush stale FIFO
}
```

ChibiOS configures SPI once during bus init. This is faster and avoids glitches.

### Problem 3: CS pin handling

RTT uses hardcoded GPIO register writes for CS:
```c
volatile uint32_t *bsrr = (volatile uint32_t *)(0x40020000U + port_idx * 0x400U + 0x18U);
*bsrr = 1U << (pin + 16);  // CS LOW (assert)
```

ChibiOS uses the SPI hardware's NSS pin or a managed GPIO framework — no register-level hardcoding.

## Impact on INS Initialization (Setup Hang at Stage 662)

### Sequence of events during `ins.init()` → `_start_backends()` → `detect_backends()`:

```
1. MC21E: probe(icm20689)
   → SPIDevice created (bus=1, _dev=nullptr → polling path)
   → _spi1_gpio_init() configures GPIO pins + CS
   → transfer() → _sem.take() → spi1_poll_transfer()
     → Per-byte NOP spin → no yield
     → If IMU doesn't respond → timeout → next probe
   → EACH PROBE TAKES ~500µs TO RESPOND
   → 4 IMUs × ~5-10 registers × retries = could be 20+ SPI transfers

2. During this entire sequence:
   - Main thread (prio 5) is running
   - **No other thread runs** (monitor at prio 2, timer at prio 4 are blocked)
   - UART thread (prio 6): tick_calls=0 forever
   - Timer thread can't pat IWDG → but IWDG wasn't started yet (setup not complete)
```

### Why ChibiOS doesn't have this problem

```
1. MC21E: probe(icm20689)
   → SPIDevice uses ChibiOS SPI driver (DMA/IRQ)
   → transfer() → semaphore wait → **SLEEP** (chSemWait)
   → **During sleep, other threads run!**
     → UART thread ticks → USB CDC data flows
     → Timer thread pats watchdog
     → Main thread resumes when DMA completes
   
2. Result: system stays responsive during INS init
   - CDC enumeration completes before setup finishes
   - Watchdog is fed by timer thread
   - System enters main loop normally (no hang)
```

## Verification

```bash
# Check which SPI path is in use
grep -n "bus == 1\|bus == 4" libraries/AP_HAL_RTT/SPIDevice.cpp
# → Line ~316: if (_desc.bus == 1 || _desc.bus == 4) { _dev = nullptr; }

# Check if IMU SPI calls are busy-waiting
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep spi1_poll_transfer
# Should exist if bus 1/4 polling path is compiled

# Count SPI transfers over time (to confirm progress)
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rtt_spi1_rt
```

## Known Workarounds

1. **Add periodic yield in SPI polling loops** — insert `rt_thread_yield()` every N iterations of the TXE/RXNE wait
2. **Use DMA/IRQ mode for SPI** — requires proper RTT SPI framework setup for bus 1/4
3. **Pre-init IMU before SPI probe** — let the IMU stabilize before attempting probe
4. **Add timeout guard in ins.init()** — skip IMU probe after N seconds of spinning (see `references/stage-662-invensense-whoami-ok-but-stuck.md`)

## Related ChibiOS Files

| File | Purpose |
|------|---------|
| `modules/ChibiOS/os/hal/ports/STM32/LLD/SPIv1/hal_spi_lld.c` | ChibiOS SPI LL driver (DMA+IRQ) |
| `modules/ChibiOS/os/hal/include/spi.h` | SPI driver API |
| `libraries/AP_HAL_ChibiOS/SPIDevice.cpp` | ArduPilot SPI device for ChibiOS |
