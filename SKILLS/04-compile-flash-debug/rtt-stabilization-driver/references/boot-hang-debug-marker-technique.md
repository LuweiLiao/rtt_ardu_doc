# Boot Hang Debug Marker Technique (2026-05-11)

## Motivation

GDB/C++ class member spelunking for boot hangs is slow and fragile — BSS addresses shift per build, DWARF class offsets vary, and thread-list traversal through OpenOCD is painful.

## The Technique

Add fine-grained `rtt_dbg_setup_stage` markers at every critical boundary in the init path. One extra marker per nested call. Recompile. Read via simple `mdw`.

### Implementation

**In `ArduCopter/system.cpp`** — mark before and after the problematic init call:
```cpp
rtt_dbg_setup_stage = 670;   // ← NEW: just before ins.init()
ins.init(scheduler.get_loop_rate_hz());
rtt_dbg_setup_stage = 663;   // existing
```

**In `AP_InertialSensor.cpp`** — mark inside `_start_backends()`:
```cpp
void AP_InertialSensor::_start_backends()
{
    rtt_dbg_setup_stage = 671;       // ← before detect_backends()
    detect_backends();
    rtt_dbg_setup_stage = 672;       // ← after detect_backends()

    for (uint8_t i = 0; i < _backend_count; i++) {
        rtt_dbg_setup_stage = 680 + i;  // ← before _backends[i]->start()
        _backends[i]->start();
    }
    ...
}
```

### Stage Decoding

| Value | Meaning |
|-------|---------|
| 670   | Before ins.init() entered (but not yet in _start_backends) |
| 671   | Inside _start_backends(), before detect_backends() |
| 672   | detect_backends() returned; about to iterate start() |
| 680   | In _backends[0]->start() (first backend) |
| 681   | In _backends[1]->start() (second backend) |
| ...   | ... |
| 689   | In _backends[9]->start() (last possible backend) |

### Reading

```bash
# After ~30s wait following reset:
echo "halt
mdw 0x2001bf34 1
exit" | nc -q 2 localhost 4444
# Value: 662 → still in init (no markers reached)
# Value: 670 → ins.init() called but not yet _start_backends
# Value: 671 → stuck inside detect_backends()
# Value: 672 → detect_backends passed, stuck in some start()
# Value: 680 → stuck in first IMU's start()
```

### Requirements

- `extern volatile uint32_t rtt_dbg_setup_stage;` declaration in the target .cpp file
- The variable is already declared in `system.cpp` and `Storage.cpp` — for other files, add `extern volatile uint32_t rtt_dbg_setup_stage;` at file scope
- Recompile after adding markers; BSS address may shift

### Cleanup

Remove the debug markers after diagnosis. They inflate code size and clutter `_start_backends()`.

## Session History

This technique was created during the 2026-05-11 RTT CUAV V5 Phase 1A session to diagnose why `ins.init()` never returned (setup_stage stuck at 662). The markers were added, compiled, and flashed before a flash corruption issue was discovered and resolved.
