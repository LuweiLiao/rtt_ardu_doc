#!/usr/bin/env python3
"""
L0 Monitor — OpenOCD halt-read-resume via Python telnet.
Monitors rtt_dbg_setup_stage, hal_run, loop_entry, loop_iter.
Usage: python3 l0_monitor.py [duration_sec] [interval_sec]
"""
import socket
import time
import sys
import re

HOST = 'localhost'
PORT = 4444

ADDR_NAMES = {
    0x2001bc84: 'stage',
    0x200001c0: 'hal_run',
    0x200001c8: 'loop_entry',
    0x20019980: 'loop_iter',
    0x20019984: 'overrun',
    0x20019988: 'work_max_us',
}

# Magic number meanings
MAGIC = {
    0x200001c0: { 
        0xDEADBEEF: 'BEFORE_RUN', 
        0xAAAAAAAA: 'HAL_RUN_START', 
        0x11111111: 'AFTER_SETUP' 
    },
    0x200001c8: { 
        0xCAFEBABE: 'BEFORE_ENTRY', 
        0x12345678: 'ENTRY_REACHED' 
    },
}


def openocd_batch(commands, timeout=5):
    """Send batch of OpenOCD commands, return response text."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((HOST, PORT))
    time.sleep(0.2)
    s.recv(4096)
    cmd_str = '\n'.join(commands) + '\n'
    s.sendall(cmd_str.encode())
    time.sleep(0.5)
    data = s.recv(16384).decode('utf-8', errors='replace')
    s.close()
    return data


def halt_read(addrs):
    """Halt, read multiple addresses in one mdw call, resume.
    Returns {addr: int_value}.
    """
    first_addr = min(addrs)
    count = max(addrs) - first_addr + 4
    resp = openocd_batch(['halt', f'mdw {hex(first_addr)} {count//4}', 'resume'])
    result = {}
    for line in resp.split('\n'):
        m = re.match(r'(0x[0-9a-fA-F]+):\s+((?:0x[0-9a-fA-F]+\s*)+)', line)
        if m:
            base = int(m.group(1), 16)
            vals = m.group(2).strip().split()
            for i, v in enumerate(vals):
                try:
                    result[base + i * 4] = int(v, 16)
                except ValueError:
                    pass
    return result


def format_val(addr, val):
    """Format a value with magic decoding if applicable."""
    s = f'{val:#010x}'
    if addr in MAGIC and val in MAGIC[addr]:
        s += f' ({MAGIC[addr][val]})'
    return s


def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    addrs = sorted(ADDR_NAMES.keys())

    # One-time reset (firmware must already be flashed)
    print("One-time reset...")
    openocd_batch(['reset', 'resume'], timeout=2)
    time.sleep(3)

    print(f"=== L0 Monitor {time.strftime('%H:%M:%S')} ===")
    print(f"Poll every {interval}s for {duration}s\n")

    prev_stage = -1
    stall_count = 0
    start = time.time()
    loop_started = False

    while time.time() - start < duration:
        tick = time.time()
        data = halt_read(addrs)
        ts = time.strftime('%H:%M:%S')

        if not data:
            print(f'[{ts}] ⚠️ No data (target not halted?)')
            time.sleep(interval)
            continue

        stage = data.get(0x2001bc84, -1)
        loop_iter = data.get(0x20019980, -1)
        hal_run = data.get(0x200001c0, -1)
        loop_entry = data.get(0x200001c8, -1)
        overrun = data.get(0x20019984, -1)
        work_max = data.get(0x20019988, -1)

        # Check main loop
        if loop_iter > 0 and not loop_started:
            loop_started = True
            print(f'[{ts}] 🎉 MAIN LOOP ACTIVE! iter={loop_iter} '
                  f'run={format_val(0x200001c0, hal_run)} '
                  f'entry={format_val(0x200001c8, loop_entry)}')
        
        if loop_started:
            pct = (overrun / loop_iter * 100) if loop_iter > 0 else 0
            print(f'[{ts}] iter={loop_iter} overrun={overrun} ({pct:.0f}%) '
                  f'work_max={work_max} stage={stage}')
            continue

        # Stage tracking
        if stage == prev_stage and stage > 0:
            stall_count += 1
        else:
            stall_count = 0
            prev_stage = stage

        stall_warn = ''
        if stall_count >= 4:
            stall_warn = f' ⚠️ Stalled at {stage} for {stall_count*interval}s'

        print(f'[{ts}] stage={stage} '
              f'run={format_val(0x200001c0, hal_run)} '
              f'entry={format_val(0x200001c8, loop_entry)}'
              f'{stall_warn}')

        elapsed = time.time() - tick
        if elapsed < interval:
            time.sleep(interval - elapsed)

    print(f'\n=== Monitor ended {time.strftime("%H:%M:%S")} ===')
    if loop_started:
        print('Result: ✅ L0 PASS — main loop active')
    else:
        print(f'Result: ❌ L0 FAIL — stage={stage} '
              f'run={format_val(0x200001c0, hal_run)} '
              f'entry={format_val(0x200001c8, loop_entry)}')


if __name__ == '__main__':
    main()
