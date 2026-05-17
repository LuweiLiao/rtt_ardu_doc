#!/usr/bin/env python3
"""
Loop rate diagnostics via OpenOCD.
Usage: python3 scripts/diag-loop-rate.py [duration=60] [interval=5]

Measures actual main loop rate by reading rtt_dbg_main_loop_iterations
every N seconds via OpenOCD halt-read-resume cycle.

This is the standard diagnostic for loop overrun issues on RTT ArduPilot.
"""
import socket, time, re, sys

HOST = 'localhost'
PORT = 4444

def cmd(c, timeout=3):
    s = socket.socket(); s.settimeout(timeout)
    s.connect((HOST, PORT)); s.recv(4096)
    s.sendall((c+'\n').encode()); time.sleep(0.25)
    d = s.recv(4096).decode('utf-8', errors='replace'); s.close()
    return d

def read_iter_data():
    """Halt, read loop timing vars, resume. Returns dict or None."""
    cmd('halt'); time.sleep(0.15)
    r = cmd('mdw 0x20019974 8')
    cmd('resume')
    m = re.search(r'0x[0-9a-fA-F]+:\s+((?:[0-9a-fA-F]{8}\s*)+)', r)
    if not m:
        return None
    vals = m.group(1).strip().split()
    if len(vals) < 8:
        return None
    return {
        'fast_loop': int(vals[0], 16),
        'loop_max':  int(vals[1], 16),
        'loop_us':   int(vals[2], 16),
        'iters':     int(vals[3], 16),
        'overrun':   int(vals[4], 16),
        'work_max':  int(vals[5], 16),
        'work_us':   int(vals[6], 16),
    }

def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    # One-time reset to get a clean baseline
    print("Resetting chip...")
    cmd('reset')

    prev = {'iters': 0, 'time': time.time()}
    start = time.time()

    print(f"{'Time':>8s} {'iters':>7s} {'loop_us':>8s} {'work_us':>8s} "
          f"{'overrun':>7s} {'rate':>8s}")
    print("-" * 50)

    first_data = False

    while time.time() - start < duration:
        time.sleep(interval)
        d = read_iter_data()
        if d is None:
            continue

        now = time.time()

        if d['iters'] > 0 and not first_data:
            first_data = True
            print(f"  +++ Main loop started after {now-start:.1f}s +++")
            prev = {'iters': d['iters'], 'time': now}
            continue

        if first_data and d['iters'] > prev['iters']:
            dt = now - prev['time']
            di = d['iters'] - prev['iters']
            rate = di / dt if dt > 0 else 0

            print(f"{now-start:7.1f}s {d['iters']:7d} {d['loop_us']:8d} "
                  f"{d['work_us']:8d} {d['overrun']:7d} {rate:7.1f}Hz")

            prev = {'iters': d['iters'], 'time': now}

            if d['iters'] > 2000:
                print("\n=== Sufficient data collected ===")
                break

    print(f"\nSummary from {now-start:.1f}s of runtime:")
    if d:
        print(f"  Final loop_time: {d['loop_us']}us = {d['loop_us']/1000:.1f}ms")
        print(f"  Final work_time: {d['work_us']}us = {d['work_us']/1000:.1f}ms")
        print(f"  Overrun rate:    {d['overrun']}/{d['iters']} = {100*d['overrun']/d['iters']:.0f}%")
        print(f"  Fast loops:      {d['fast_loop']}")

if __name__ == '__main__':
    main()
