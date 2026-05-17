#!/home/llw/venv-ardupilot/bin/python3
"""
CUAV V5 RTT 固件参数获取性能基准测试

测量 PARAM_REQUEST_LIST 的端到端吞吐量，诊断 USB CDC TX 瓶颈。
输出"突发-停顿"模式分析，帮助判断 CherryUSB 缓冲/DWC2 FIFO 是否饱和。

用法:
  python3 param_fetch_bench.py [port=/dev/ttyACM1] [baud=921600]
"""

import sys
import time
import traceback

# --- 配置 ---
DEFAULT_PORT = "/dev/ttyACM1"
DEFAULT_BAUD = 921600
TEST_TIMEOUT = 25  # 秒
STALL_THRESHOLD = 3.0  # 连续 x 秒无新参数视为停顿

# --- 导入 pymavlink ---
sys.path.insert(0, "/home/llw/venv-ardupilot/lib/python3.12/site-packages")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_BAUD

    import pymavlink.mavutil as mavutil

    # 连接
    print(f"Connecting to {port} @ {baud}...")
    c = mavutil.mavlink_connection(port, baud=baud)
    h = c.wait_heartbeat(timeout=10)
    if not h:
        print("FAIL: No heartbeat received")
        sys.exit(1)
    print(f"HB: status={h.system_status}")

    # 请求参数列表
    print("Requesting PARAM_REQUEST_LIST...")
    c.mav.param_request_list_send(c.target_system, c.target_component)

    # 采集
    t0 = time.time()
    params_received = 0
    last_count = 0
    last_arrival_ts = t0
    stalls = []
    total_wait = 0.0
    burst_start = 0.0
    burst_params = 0
    in_burst = False
    bursts = []

    while time.time() - t0 < TEST_TIMEOUT:
        m = c.recv_match(blocking=True, timeout=0.5)
        if m is None:
            continue
        now = time.time()
        if m.get_type() == "PARAM_VALUE":
            params_received += 1
            elapsed = now - t0

            # 检测停顿
            gap = now - last_arrival_ts
            if gap > STALL_THRESHOLD and last_arrival_ts > t0 + 0.5:
                stalls.append((last_arrival_ts - t0, gap))
                if in_burst:
                    bursts.append((burst_start, now - burst_start, burst_params))
                    in_burst = False

            # 检测突发开始
            if not in_burst and gap < 0.05:
                in_burst = True
                burst_start = now
                burst_params = 1
            elif in_burst:
                burst_params += 1

            # 进度报告
            if (params_received % 25 == 0) or (m.param_index is not None and m.param_index % 100 == 0):
                print(
                    f"  [{elapsed:5.1f}s] params={params_received} idx={m.param_index}/{m.param_count}"
                )

            last_arrival_ts = now

    # 结束最后一个突发
    if in_burst:
        bursts.append((burst_start, time.time() - burst_start, burst_params))

    total_elapsed = time.time() - t0

    # --- 报告 ---
    print(f"\n{'='*50}")
    print(f"Result: {params_received} parameters in {total_elapsed:.1f}s")
    print(f"Rate: {params_received/total_elapsed:.1f} params/s")
    print(f"Stalls detected: {len(stalls)}")

    for i, (ts, gap) in enumerate(stalls):
        print(f"  Stall #{i+1}: at {ts:.1f}s, duration={gap:.1f}s")

    if bursts:
        print(f"\nBursts: {len(bursts)}")
        for i, (start, dur, count) in enumerate(bursts):
            rate = count / dur if dur > 0 else 0
            print(f"  Burst #{i+1}: {count} params in {dur:.1f}s ({rate:.0f} params/s)")

    # 诊断
    print(f"\n{'='*50}")
    print("DIAGNOSIS:")
    if len(stalls) == 0 and params_received >= 530:
        print("  ✅ USB CDC throughput is good")
        print("  No stalls detected, all parameters received")
    elif len(stalls) >= 3:
        print("  ⚠️  Multiple stalls detected ('burst-and-stall' pattern)")
        print("  → CherryUSB CDC TX ring buffer fills up, then drains slowly")
        print("  → Common in virtual/remote USB environments")
        print("  → DWC2 TX FIFO for EP1 IN is only 128 bytes (bottleneck)")
        print("  → Suggested: Increase CONFIG_USB_DWC2_TX1_FIFO_SIZE in usb_config.h")
    elif params_received < 530:
        print(f"  ⚠️  Only {params_received}/{530} parameters received in {TEST_TIMEOUT}s")
        print("  → Parameter enumeration may be stalled or incomplete")
        print("  → Check queued_param_send() in GCS_Param.cpp")
    elif params_received == 0:
        print("  ❌ No parameters received at all!")
        print("  → handle_param_request_list() may not be called")
        print("  → Check GCS::update_send() is being called at 50Hz")

    c.close()


if __name__ == "__main__":
    main()
