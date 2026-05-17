# ChibiOS 固件对照实验法

> 当不确定硬件是否正常时，用 ChibiOS 标准固件作为对照。

## 适用场景

- RTT 固件某功能不工作（RC、传感器、GPS），怀疑硬件问题
- 需要区分"RTT 移植 bug" vs "硬件故障/接线问题"

## 三步法

### Step 1: 获取 ChibiOS 标准固件

**方法 A：从 firmware.ardupilot.org 下载（推荐）**
```bash
# 下载最新固件（APJ 格式）
wget "https://firmware.ardupilot.org/Copter/latest/CUAVv5/arducopter.apj" -O /tmp/cuavv5_latest.apj

# 解压 APJ（zlib 压缩）
python3 -c '
import json, base64, zlib
with open("/tmp/cuavv5_latest.apj") as f:
    data = json.load(f)
fw = zlib.decompress(base64.b64decode(data["image"]))
with open("/tmp/cuavv5_latest.bin", "wb") as f:
    f.write(fw)
print(f"Decompressed: {len(fw)} bytes")
'
```

**方法 B：本地 waf 编译（RTT 修改仓库，注意修复 rt_kprintf 冲突）**
```bash
cd /data/firmare/pogo-apm
./waf configure --board=CUAVv5
./waf copter --board=CUAVv5 -j$(nproc)
```

### Step 2: 烧录 ChibiOS 固件

```bash
pkill -9 openocd 2>/dev/null; sleep 2

openocd -f Tools/debug/openocd-f7.cfg \
  -c "program /tmp/cuavv5_latest.bin 0x08008000" \
  -c "reset run" -c "shutdown"
```

### Step 3: 对比验证

```bash
sleep 15 && python3 -c '
import pymavlink.mavutil as m
mav = m.mavlink_connection("/dev/ttyACM1", baud=921600)
h = mav.wait_heartbeat(timeout=15)
print("HB: status=%d" % (h.system_status if h else -1))
for i in range(40):
    msg = mav.recv_match(blocking=True, timeout=0.5)
    if msg and msg.get_type() == "RC_CHANNELS":
        print("RC: ch1=%d ch2=%d rssi=%d" % (msg.chan1_raw, msg.chan2_raw, msg.rssi))
        break
'
```

## 结论矩阵

| ChibiOS 结果 | RTT 结果 | 结论 |
|-------------|----------|------|
| ✅ 正常 | ❌ 不正常 | RTT 移植有 bug |
| ❌ 不正常 | ❌ 不正常 | **硬件问题** |
| ✅ 正常 | ✅ 正常 | 都正常 |

## 已知陷阱

### APJ 格式压缩
APJ 的 `image` 字段是 **zlib 压缩**的 data，直接 base64 解码后写入 flash 会得到无效向量表（SP 异常）。必须 zlib.decompress() 后再烧录。

### rt_kprintf 冲突
RTT 分支在 `libraries/AP_Baro_MS5611.cpp`、`AP_InertialSensor_BMI088.cpp`、`AP_IOMCU.cpp` 等中添加了 `rt_kprintf()`。ChibiOS 无 `rtthread.h` 导致编译失败。修复：受影响的 .cpp 顶部加 `#ifndef rt_kprintf\n#define rt_kprintf(...) do {} while(0)\n#endif`

### OpenOCD 超时
ChibiOS 固件更大（~1.5MB），用 `reset halt` 后再 `flash write_image erase` 可避免超时。
