#!/usr/bin/env python3
"""
Long-duration stability test for RTT ArduPilot CUAV V5.
Connects via MAVLink CDC, samples SYS_STATUS for N seconds,
reports: total, drops, health flips, min load, IMU data.

Usage:
    python3 scripts/long-stability-test.py [--time 120] [--port /dev/ttyACM1]
"""

import pymavlink.mavutil as m
import time, sys

def main():
    duration = 120  # default 2 minutes
    port = '/dev/ttyACM1'

    for i, arg in enumerate(sys.argv[1:]):
        if arg == '--time' and i+2 < len(sys.argv):
            duration = int(sys.argv[i+2])
        elif arg == '--port' and i+2 < len(sys.argv):
            port = sys.argv[i+2]

    print(f'Connecting to {port}...', flush=True)
    c = m.mavlink_connection(port, baud=921600, timeout=5)
    c.wait_heartbeat(timeout=15)
    print(f'HB OK sys={c.target_system}', flush=True)

    start = time.time()
    total = 0
    ok = 0
    fail = 0
    min_load = 1000
    last_h = 0
    flips = 0
    min_az = 0
    max_az = 0
    az_count = 0

    print(f'Running {duration}s stability test...', flush=True)

    while time.time() - start < duration:
        try:
            ss = c.recv_match(type='SYS_STATUS', blocking=True, timeout=3)
            if ss:
                total += 1
                ok += 1
                h = ss.onboard_control_sensors_health
                if h != last_h and last_h != 0:
                    flips += 1
                last_h = h
                if ss.load < min_load:
                    min_load = ss.load

                # Sample IMU data every 5th message
                if total % 5 == 0:
                    raw = c.recv_match(type='RAW_IMU', blocking=True, timeout=1)
                    if raw:
                        if az_count == 0:
                            min_az = max_az = raw.zacc
                        min_az = min(min_az, raw.zacc)
                        max_az = max(max_az, raw.zacc)
                        az_count += 1

                if total % 30 == 0:
                    t = int(time.time() - start)
                    print(f'  [{t:4d}s] #{total} GYRO={"H" if h&1 else "U"}'
                          f' ACCEL={"H" if h&2 else "U"} load={ss.load}', flush=True)
            else:
                fail += 1
                print(f'  ⚠ timeout at t={int(time.time()-start)}s', flush=True)
        except Exception as e:
            fail += 1
            print(f'  ⚠ err at t={int(time.time()-start)}s: {e}', flush=True)
            time.sleep(3)

    elapsed = int(time.time() - start)
    print(f'\n--- {elapsed}s RESULTS ---', flush=True)
    print(f'SYS_STATUS: {ok}/{total} ok ({(100*ok)//max(1,total)}%)', flush=True)
    print(f'Drops/timeouts: {fail}', flush=True)
    print(f'Health flips: {flips}', flush=True)
    print(f'Min load: {min_load}', flush=True)
    print(f'IMU az range: {min_az} ~ {max_az} ({az_count} samples)', flush=True)
    print(f'Final: GYRO={"H" if last_h&1 else "U"} ACCEL={"H" if last_h&2 else "U"}'
          f' BARO={"H" if last_h&8 else "U"}', flush=True)

    # EKF
    ekf = c.recv_match(type='EKF_STATUS_REPORT', blocking=True, timeout=2)
    if ekf:
        print(f'EKF flags={ekf.flags}', flush=True)

    if fail == 0 and flips == 0 and last_h & 3 == 3:
        print(f'\n🏆 {elapsed}s STABILITY TEST PASSED ✅', flush=True)
    else:
        print(f'\n⚠️  Issues: fail={fail} flips={flips} final_h=0x{last_h:x}', flush=True)

if __name__ == '__main__':
    main()
