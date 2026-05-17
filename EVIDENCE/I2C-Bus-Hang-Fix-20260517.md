# I2C Bus Hang Fix — 2026-05-17

## Summary

The CUAV V5 board was hanging at `setup_stage≈500` during `ins.init()`. Root cause: **2 independent bugs** in the I2C bus layer.

## Root Cause Analysis

### Bug 1: Bus Name Order Mismatch

- `I2C_ORDER` in `hwdef.dat` defines: `I2C3, I2C1, I2C2, I2C4` → Bus 0 = I2C3
- `I2CDevice.cpp` `_i2c_bus_names[]` was: `["i2c1", "i2c2", "i2c3", "i2c4"]`
- ArduPilot requests Bus 0 → resolves to `"i2c1"` → **soft bitbang I2C1 (PB8/PB9)** instead of **hardware I2C3 (PH7/PH8)**

### Bug 2: Soft Bitbang I2C Preemption

- `rtt_hwdef.py` generated `BSP_USING_I2C3` macro → `drv_soft_i2c.c` compiled and registered a soft bitbang I2C3 bus
- This soft I2C3 registered under the name `"i2c3"`, **stealing** the bus name from the CMSIS hardware I2C3 driver in `I2CDevice.cpp`
- Soft bitbang I2C uses GPIO polling — hangs on 1kHz interrupt context

### Fix

| File | Change |
|------|--------|
| `libraries/AP_HAL_RTT/I2CDevice.cpp:22` | `_i2c_bus_names[]` reordered from `"i2c1","i2c2","i2c3","i2c4"` → `"i2c3","i2c1","i2c2","i2c4"` to match `I2C_ORDER` |
| `libraries/AP_HAL_RTT/hwdef/scripts/rtt_hwdef.py:1593-1602` | Removed generation of `BSP_USING_I2Cx` / `RT_USING_I2C_BITOPS` macros → prevents `drv_soft_i2c.c` from compiling |

## Verification

| Metric | Before | After |
|--------|--------|-------|
| `hal_run_called` | 0xBBBBBBBB (not reached main loop) | 0x11111111 (setup complete) |
| `fast_loop_count` | 0 | **138+** (main loop running!) |
| `setup_stage` | stuck at ~500 (ins.init) | **0x28b = 651** (setup complete) |
| `rt_tick` | N/A | **132s** running |
| USB enumeration | ❌ | ✅ `1209:5741 Generic CUAVv5 RTT` |
| I2C3 bus | hang | CMSIS hardware I2C3 active |

## ChibiOS Reference

- `libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat` — I2C3_PH7/PH8, I2C_ORDER=I2C3 I2C1 I2C2 I2C4
- `libraries/AP_HAL_ChibiOS/hwdef/CUAVv5/hwdef.dat` — IST8310 MAG on I2C bus 0
- `libraries/AP_HAL_ChibiOS/I2CDevice.cpp` — Bus index mapping via `I2C_ORDER`

## Commit

```
f82bd0fb02 rtt: fix I2C bus hang — correct bus name ordering + disable soft bitbang I2C
```

## Remaining Issue

- **CDC TX not sending**: `bulkin_cnt=0`, `bulkout_cnt=0` — CherryUSB IN endpoint never triggers
- System runs in main loop (fast_loop_count=138) but **no MAVLink HEARTBEAT** output
