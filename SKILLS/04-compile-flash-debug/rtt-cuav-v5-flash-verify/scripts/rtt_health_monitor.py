#!/usr/bin/env python3
"""RTT ArduPilot firmware health monitor — 5-min auto-detect + auto-fix.
Invoked by cron job bdd86609b340 every 5 minutes.

Detection chain: MCU alive → CDC ports → MAVLink heartbeat → BARO/IMU data → sensors health mask
Fix escalation: restart OpenOCD → reset MCU → reflash firmware

State tracking in /tmp/rtt_health_state.json prevents over-repair.
"""

import subprocess, time, sys, os, json

WORKDIR = "/data/firmare/pogo-apm"
STATE_FILE = "/tmp/rtt_health_state.json"

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {"baro_fail_count": 0, "last_fix_ts": 0, "last_hb_ts": 0}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "(timeout)", -1

def check_mcu_alive():
    out, rc = run("echo 'halt' | nc -q1 localhost 4444 2>/dev/null")
    return "halted" in out

def check_cdc():
    out, rc = run("ls /dev/ttyACM* 2>/dev/null")
    return [p for p in out.strip().split("\n") if p] if out.strip() else []

def check_mavlink(timeout=20):
    script_file = "/tmp/_mavcheck.py"
    with open(script_file, "w") as f:
        f.write('''import pymavlink.mavutil as m, time, sys\nc = m.mavlink_connection("/dev/ttyACM1", baud=921600)\nh = c.wait_heartbeat(timeout=10)\nif h is None:\n    print("HB:NONE")\n    sys.exit(1)\nprint("HB:status=%d" % h.system_status)\nt0 = time.time()\nbaro = 0\nimu_ok = 0\nwhile time.time() - t0 < 10:\n    msg = c.recv_match(blocking=False, timeout=0.3)\n    if msg is None: continue\n    mt = msg.get_type()\n    if mt == "SCALED_PRESSURE": baro += 1\n    elif mt == "RAW_IMU" and msg.zacc > -500: imu_ok += 1\n    elif mt == "SYS_STATUS":\n        hval = msg.onboard_control_sensors_health\n        print("HLTH:%d" % hval)\n        if hval & 0x08: print("BARO_OK:1")\nprint("SUM:baro=%d imu_ok=%d" % (baro, imu_ok))\n''')
    return run("timeout %d python3 %s" % (timeout, script_file))

def reset_mcu():
    run("echo 'reset' | nc -q2 localhost 4444 2>/dev/null")

def reflash():
    run("scons --v=ArduCopter --target=cuav_v5 -j$(nproc)", timeout=300, workdir=WORKDIR)
    run("arm-none-eabi-gdb -batch "
        "-ex 'target extended-remote :3333' "
        "-ex 'monitor reset halt' "
        "-ex 'monitor flash write_image erase build/rtt_cuav_v5/rtthread.bin 0x08008000' "
        "-ex 'monitor reset init' 2>&1", timeout=60, workdir=WORKDIR)
    reset_mcu()

def main():
    report = {"ts": time.time(), "alive": False, "cdc": [], "hb": False, "baro": False, "imu": False, "fixes": []}
    state = load_state()

    if not check_mcu_alive():
        run("pkill -9 -f openocd 2>/dev/null; sleep 1; "
            "openocd -f interface/stlink.cfg -f target/stm32f7x.cfg &", timeout=5)
        time.sleep(3)
        if not check_mcu_alive():
            report["fixes"].append("openocd_failed")
            save_state(state)
            print(json.dumps(report))
            return

    cdc = check_cdc()
    report["cdc"] = cdc
    if len(cdc) < 2:
        reset_mcu()
        time.sleep(20)
        cdc = check_cdc()
        report["cdc"] = cdc
        report["fixes"].append("mcu_reset_cdc")

    if len(cdc) >= 2:
        mav_out, _ = check_mavlink()
        report["hb"] = "HB:" in mav_out
        report["baro"] = "BARO_OK:1" in mav_out

        if not report["hb"]:
            reset_mcu()
            time.sleep(20)
            mav_out2, _ = check_mavlink(timeout=25)
            report["hb"] = "HB:" in mav_out2
            report["baro"] = "BARO_OK:1" in mav_out2
            if report["hb"]: report["fixes"].append("mcu_reset_hb")

        if report["baro"]:
            state["baro_fail_count"] = 0
        else:
            state["baro_fail_count"] = state.get("baro_fail_count", 0) + 1

        if state["baro_fail_count"] >= 6 and time.time() - state.get("last_fix_ts", 0) > 600:
            reflash()
            report["fixes"].append("reflash_baro")
            state["baro_fail_count"] = 0
            state["last_fix_ts"] = time.time()

    save_state(state)
    print(json.dumps(report))

if __name__ == "__main__":
    main()
