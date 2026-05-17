#!/usr/bin/env python3
"""
RTT ArduPilot MAVLink 固件健康诊断脚本
用法: dd if=/dev/ttyACM1 bs=1 count=10000 2>/dev/null | python3 mavlink_diag.py

解析 MAVLink v2 二进制流，输出关键传感器状态。
"""
import sys, struct

def parse_mavlink(data):
    i = 0
    msgs = {}
    while i < len(data) - 12:
        if data[i] != 0xFD:
            i += 1
            continue
        plen = data[i+1]
        total = 12 + plen + 2
        if i + total > len(data):
            break
        msgid = struct.unpack("<I", data[i+7:i+10] + b"\x00")[0]
        payload = data[i+12:i+12+plen]
        msgs.setdefault(msgid, {"count": 0})
        msgs[msgid]["count"] += 1
        if msgs[msgid]["count"] > 3:
            i += total
            continue
        msgs[msgid].setdefault("samples", []).append(payload)
        i += total
    return msgs

def print_summary(msgs):
    # SYS_STATUS
    if 1 in msgs:
        p = msgs[1]["samples"][0]
        if len(p) >= 17:
            pres = struct.unpack("<I", p[5:9])[0]
            hlth = struct.unpack("<I", p[13:17])[0]
            bits = [(1, 'gyro'), (2, 'accel'), (4, 'mag'),
                    (8, 'pressure'), (16, 'gyro2'), (32, 'accel2')]
            unhealthy = [n for b, n in bits if (pres & b) and not (hlth & b)]
            print(f"SYS_STATUS: present={pres:#x} health={hlth:#x}")
            print(f"  Unhealthy: {', '.join(unhealthy) if unhealthy else 'none'}")
    # SCALED_PRESSURE
    if 29 in msgs:
        p = msgs[29]["samples"][-1]
        if len(p) >= 14:
            pa = struct.unpack("<f", p[4:8])[0]
            t = struct.unpack("<h", p[12:14])[0]
            status = "OK" if pa > 900 else "BROKEN"
            print(f"BARO [{status}]: {pa:.1f}hPa {t/100:.1f}°C")
    # RAW_IMU (IMU1)
    if 27 in msgs:
        for p in msgs[27]["samples"][:2]:
            if len(p) >= 20:
                xa, ya, za = struct.unpack("<hhh", p[8:14])
                xg, yg, zg = struct.unpack("<hhh", p[14:20])
                if abs(xa) > 0 or abs(ya) > 0 or abs(za) > 0:
                    print(f"IMU1: acc=({xa},{ya},{za}) gyro=({xg},{yg},{zg})")
    # SCALED_IMU2 (IMU2)
    if 116 in msgs:
        p = msgs[116]["samples"][0]
        if len(p) >= 22:
            xa, ya, za = struct.unpack("<hhh", p[4:10])
            acc_mag = (xa*xa + ya*ya + za*za)**0.5
            print(f"IMU2: acc_mag={acc_mag:.0f}")
    # HEARTBEAT
    if 0 in msgs:
        p = msgs[0]["samples"][0]
        if len(p) >= 8:
            states = {0:'UNINIT',1:'BOOT',2:'CAL',3:'STANDBY',4:'ACTIVE'}
            print(f"HB: status={p[7]}({states.get(p[7],'?')})")
    # STATUSTEXT
    if 253 in msgs:
        for p in msgs[253]["samples"][:3]:
            if len(p) > 1:
                txt = p[1:].decode('utf-8', 'replace').rstrip('\x00')
                if txt:
                    print(f"TEXT: {txt[:100]}")
    print(f"\nFrames: {sum(v['count'] for v in msgs.values())}")
    print(f"Msg types: {len(msgs)}")

if __name__ == "__main__":
    data = sys.stdin.buffer.read()
    if not data:
        print("NO DATA — 检查 CDC 端口状态")
        sys.exit(1)
    msgs = parse_mavlink(data)
    print_summary(msgs)
