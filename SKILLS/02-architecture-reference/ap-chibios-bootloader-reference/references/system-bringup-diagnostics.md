# System Bringup Diagnostics: "App Running But Stuck in Idle"

## Problem Checklist

When the system boots, USB CDC enumerates, but NO MAVLink data ever appears, and OpenOCD halt shows PC in the idle thread (`idle.c:134`):

```
xPSR: 0x81000000 pc: 0x080ff788 psp: 0x200450c4
```

## Step-by-Step Diagnosis

### Q1: Did the bootloader actually jump?

**Check**: Read PC after 8+ seconds. If PC >= 0x08008000, we're in app code.

```
> halt
xPSR: 0x81000000 pc: 0x080ff788  ← app code (> 0x08008000)
```

**Failure mode**: If PC < 0x08008000 (e.g., 0x08003xxx), bootloader hasn't jumped.
- Wait longer (bootloader has ~5s timeout)
- Check vector table validity (SP in SRAM, Reset_Handler in flash range)

### Q2: Did `HAL_RTT::run()` ever get called?

**Check**: Read `rtt_dbg_hal_run_called`. First, find its actual address:

```bash
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rtt_dbg_hal_run_called
# → 200001c0 D rtt_dbg_hal_run_called
```

**Magic values** (from `HAL_RTT_Class.cpp`):

| Value | Meaning |
|-------|---------|
| `0xDEADBEEF` | **Never entered** (initial value in DATA section) |
| `0xAAAAAAAA` | Entered `run()`, before setup |
| `0x11111111` | `run() setup stage done` |
| `0xBBBBBBBB` | `run()` about to return (entering main loop) |

**0xDEADBEEF → `main()` was never reached.** Why?

### Q3: Did C++ constructors complete?

**Check**: `rtt_dbg_ctor_phase`. Values:
- `0`: Not started
- `1`: About to run constructors
- `2`: In constructor `i` (before)
- `3`: Constructor `i` returned
- `4`: **All constructors done** ✅

Also check `rtt_dbg_ctor_index` and `rtt_dbg_ctor_total` to see progress.

If ctor_phase != 4, a constructor is stuck (likely blocking on hardware init).

### Q4: Did RT-Thread component init complete?

**Check**: `rtt_sd_mount_stage` and `rtt_sd_mount_result` (from `rt_board_init.c`):

```c
rtt_sd_mount_stage = 10 → SD mounted + APM dirs created
rtt_sd_mount_result = 0  → success
```

Other `INIT_XXX_EXPORT` functions called sequentially:
```
INIT_ENV_EXPORT(sd_card_mount_sync)     → checks rtt_sd_mount_stage
INIT_ENV_EXPORT(flash_blkdev_mount)     → flash block device
INIT_APP_EXPORT(_cpu_idle_monitor_init) → CPU monitor thread
```

The `main_thread_entry()` calls `rt_components_init()` then `main()`:
```asm
main_thread_entry:
  bl rt_components_init     ; ALL INIT FUNCTIONS HERE
  b.w main                  ; ← This is a tail-call
```

If `rt_components_init()` returns and `hal_run_called` is still 0xDEADBEEF, then either:
1. `main_thread_entry` was preempted and never scheduled again
2. Another thread at higher priority is consuming all CPU
3. The main thread was killed/suspended

### Q5: Is the main thread in READY state?

RT-Thread TCB layout (struct rt_thread):
```c
struct rt_thread {
    char name[8];                // offset 0
    rt_uint8_t type;             // offset 8
    rt_uint8_t flags;            // offset 9
    rt_list_t list;              // offset 12 (prev/next pointers)
    rt_list_t tlist;             // offset 20
    void *sp;                    // offset 28 (stack pointer)
    void *entry;                 // offset 32
    void *parameter;             // offset 36
    void *stack_addr;            // offset 40
    rt_uint32_t stack_size;      // offset 44
    rt_uint8_t current_priority; // offset 48
    rt_uint8_t init_priority;    // offset 49
    rt_uint8_t number;           // offset 50
    rt_uint8_t err;              // offset 51
    rt_uint8_t stat;             // offset 52
    // ... more fields
};
```

Read `rt_current_thread` (if symbol available) or scan thread list.

## Tool: Full Diagnostic Script

Save as `diag_boot.py` in project root:

```python
#!/usr/bin/env python3
"""Diagnose "stuck in idle" state for RTT ArduPilot on CUAV V5."""
import socket, time, subprocess, re

def read_mdw(sock, addr, count=1):
    sock.sendall(f'mdw {hex(addr)} {count}\n'.encode())
    time.sleep(0.2)
    resp = sock.recv(4096).decode('latin-1', errors='replace')
    m = re.search(rf'{hex(addr)[2:]}:\s+((?:0x[0-9a-f]+\s*)+)', resp)
    if m:
        return [int(v, 16) for v in m.group(1).split()]
    return None

# Get actual symbol addresses
result = subprocess.run([
    'arm-none-eabi-nm', 'build/rtt_deploy/cuav_v5/rt-thread.elf'
], capture_output=True, text=True)
symbols = {}
for line in result.stdout.split('\n'):
    parts = line.strip().split()
    if len(parts) >= 3 and parts[1] in ('D', 'B', 'T'):
        symbols[parts[2]] = int(parts[0], 16)

# Connect to OpenOCD
s = socket.socket()
s.settimeout(5)
s.connect(('localhost', 4444))
s.recv(4096)
s.sendall(b'halt\n')
time.sleep(0.3)
s.recv(4096)

# Read registers via OpenOCD 'reg' command
s.sendall(b'reg\n')
time.sleep(0.3)
reg_resp = s.recv(4096).decode('latin-1', errors='replace')
for line in reg_resp.split('\n'):
    if 'pc:' in line:
        print(f'REG: {line.strip()}')

# Read debug variables
watchlist = ['rtt_dbg_hal_run_called', 'rtt_dbg_setup_stage',
             'rtt_dbg_main_loop_entry_called', 'rtt_dbg_main_loop_iterations',
             'rtt_dbg_ctor_phase', 'rtt_dbg_ctor_index', 'rtt_dbg_ctor_total',
             'rtt_sd_mount_stage', 'rtt_sd_mount_result', 'rt_tick']

for name in watchlist:
    if name not in symbols:
        print(f'{name:40s}= NOT FOUND in ELF')
        continue
    vals = read_mdw(s, symbols[name])
    if vals:
        print(f'{name:40s}= {hex(vals[0])} ({vals[0]})')
    else:
        print(f'{name:40s}= READ FAILED')

s.close()
```

## Key Pitfalls

### Pitfall 1: Wrong symbol addresses
Symbol addresses CHANGE between builds. Always run `arm-none-eabi-nm` fresh to get correct addresses.

### Pitfall 2: Bootloader vs App CDC
After bootloader's ~5s timeout, it jumps to the app. The app's USB CDC may take 10-30s to initialize (GPS init etc.). Don't conclude failure before 45s.

### Pitfall 3: `0x00000000` in vector table
If a vector table entry is `0x00000000`, the bootloader scan loop treats it as valid (non-erased). This is normal for unused vector slots — they don't prevent jumping.

### Pitfall 4: D-Cache NOT the issue
The CUAV V5 bootloader **disables** D-Cache (SCB_CCR=0). Any NOCP/CPACR theories about cache-coherency are provably wrong via __core_init disassembly.
