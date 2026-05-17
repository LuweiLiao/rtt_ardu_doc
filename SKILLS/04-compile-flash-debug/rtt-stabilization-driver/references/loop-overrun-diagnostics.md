# Loop Overrun Diagnostics (RTT ArduPilot)

## Problem Signature
- `rtt_dbg_main_loop_iterations` grows at 7-12 Hz (target 400 Hz)
- `rtt_dbg_work_time_us ≈ rtt_dbg_loop_time_us ≈ 82ms`
- `rtt_dbg_overrun_count` → 90%+ of all iterations
- `rtt_dbg_work_time_max_us` → 540ms+ (single loop spike)

## Diagnostic Checklist

### 1. Confirm the symptom via MAVLink
```bash
python3 -c "
from pymavlink import mavutil
m = mavutil.mavlink_connection('/dev/ttyACM1', baud=115200)
m.wait_heartbeat(timeout=5)
for i in range(10):
    msg = m.recv_match(type='HEARTBEAT', blocking=True, timeout=2)
    if msg:
        print(f'{i}s: HEARTBEAT state={msg.system_status}')
"
```

### 2. Read loop timing from OpenOCD (halt-read-resume)
Use the Python monitor script pattern:

```python
import socket, time, re

def halt_read_mdw(addr, count=1):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5); s.connect(('localhost', 4444)); time.sleep(0.2)
    s.recv(4096)  # banner
    s.sendall(f'halt\n'.encode()); time.sleep(0.3)
    s.sendall(f'mdw {addr} {count}\n'.encode()); time.sleep(0.3)
    data = s.recv(16384).decode('utf-8', errors='replace')
    s.sendall(f'resume\n'.encode())
    s.close()
    return data
```

Key debug variable addresses:
```
rtt_dbg_loop_time_us        0x2001997c  — last loop cycle (incl sleep)
rtt_dbg_loop_time_max_us    0x20019978
rtt_dbg_work_time_us        0x2001998c  — loop() + call_delay_cb()
rtt_dbg_work_time_max_us    0x20019988
rtt_dbg_main_loop_iterations 0x20019980
rtt_dbg_overrun_count       0x20019984
rtt_dbg_fast_loop_count     0x20019974  — loops < 1.5ms
rtt_dbg_boost_calls_per_loop 0x2001996c
```

### 3. Check `_sample_period_usec` (INS object)
```
INS singleton ptr: 0x20019a64 (read pointer value)
_sample_period_usec offset: +0x5c4 from INS object base
Expected: 2500 (for 400Hz loop rate)
```
If `_sample_period_usec` is > 2500, the INS loop rate is set too low.

### 4. Check SystemCoreClock
```
Address: 0x20000c08
Expected: 0x0cdfe600 = 216,000,000 (216 MHz)
```

### 5. Core analysis: work_time ≈ loop_time
If `work_time_us ≈ loop_time_us`, the extra time is inside `loop() + call_delay_cb()`, NOT in the 50µs delay at the end of the loop.

Inside `AP_Scheduler::loop()`:
- `wait_for_sample()` → sleeps for `_sample_period_usec` (2500µs at 400Hz)
- `run()` → executes scheduler tasks with microsecond budgets

If `work_time >> _sample_period_usec`, a scheduler task is blocking.

### 6. Prime suspects (in priority order)
1. **GCS::update_send** (400Hz, 550µs budget) — CDC TX can block if USB buffer full or DTR not set
2. **AP_Logger::periodic_tasks** (400Hz, 300µs) — SD card writes blocking
3. **GCS::update_receive** (400Hz, 180µs) — CDC RX handling

### 7. Debugging blocked tasks
To find which task is taking too long, either:
- Add per-task timing instrumentation (wrap individual task calls with micros())
- Reduce non-essential task rates to isolate the culprit
- Check `hal.util->persistent_data.scheduler_task` via OpenOCD to see which task was last running

## Common Fixes

### CDC TX blocking (most likely)
- Check `g_dtr_active` / `dbg_dtr_set_cnt` to distinguish "firmware bug" vs "host not listening"
- Add TX timeout in `UARTDriver::write()` path
- Reduce GCS update_send rate from 400Hz to 200Hz temporarily

### Priority boost leak
- In `delay_microseconds_boost()`, `_priority_boosted` is set to true on first call and NEVER reset during normal loop operation
- The main thread stays at boosted priority (3) forever
- This doesn't directly cause 82ms loops but masks timer thread starvation
- Fix: call `boost_end()` when `check_called_boost()` returns false at end of main loop
