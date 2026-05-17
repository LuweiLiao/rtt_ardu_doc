---
name: rtt-l0-verification-plan
category: embedded
description: RTT ArduPilot CUAV V5 L0 验证标准与流程 — 分拆为 Phase 0A（编译枚举基线）✅ 和 Phase 0B（通信基线）🔴。含三层阻塞修复链验证、OpenOCD halt-read-resume 监控、MAVLink 心跳确认、heap corruption 诊断。
status: active
note: "2026-05-17 廖博士审阅修正：L0 分拆为 0A(前3项完成) / 0B(后3项阻塞)。当前 P0=heap metadata corruption"
---

# RTT L0 验证计划

## Phase 0A 里程碑（已完成 ✅）\n\n| 条件 | 验证方法 | 通过信号 | 状态 |\n|------|----------|----------|------|\n| ① 无 HardFault | OpenOCD: CFSR=0, HFSR=0 | 连续轮询 5 次不出现异常 | ✅ |\n| ② 编译通过 + 烧录验证 | scons + OpenOCD verify | ROM 87.54%, RAM 78.05% | ✅ |\n| ③ USB 枚举 | `ls /dev/ttyACM*` | 1209:5741 CUAVv5 RTT | ✅ |\n\n**Exit**: 三层阻塞修复链全部到位 + hwdef 基础设施完整\n\n## Phase 0B 里程碑（阻塞中 🔴）\n\n> **当前阻塞根因**：堆元数据破坏（`system_heap.used=86224 > total=86208`），不是堆耗尽（metadata corruption 16B 超出）。\n> 不解决此问题，后续所有验证无效。\n\n| 条件 | 验证方法 | 通过信号 | 状态 |\n|------|----------|----------|------|\n| ④ CDC ACM 数据收发 | `cat /dev/ttyACM1` → 有字符输出 | 非空输出流 | 🔴 |\n| ⑤ MAVLink 心跳 | pymavlink HEARTBEAT（`wait_heartbeat(timeout=10)`） | `system_status=STANDBY`，1Hz | 🔴 |\n| ⑥ 基础传感器健康 | RAW_IMU + SYS_STATUS | 至少 1 个 IMU 健康 | 🔴 |\n| ⑦ 主循环率 | loop_time_us < 10ms | >= 100Hz | 🔴 |\n\n**Entry**: Phase 0A exit met + heap corruption fixed\n**Exit**: 全部 4 条件通过 = L0 完整\n\n## 堆元数据破坏验证（Phase 0B P0）\n\n```bash\n# GDB 检查堆头部 magic number\narm-none-eabi-gdb -batch -q -ex \"target extended-remote :3333\" \\\n  -ex \"monitor halt\" \\\n  -ex \"p/x ((struct rt_small_mem_item *)0x2006af20)->magic\"\n# 期望: 0x1ea0 (RT_SMALL_MEM_MAGIC)\n# 若 != 0x1ea0 → heap 头部已被踩踏\n\n# 检查 system_heap 统计\n(gdb) p/x *system_heap\n# 期望: used <= total\n# 当前: used=0x150D0 > total=0x150C0 → 元数据损坏\n\n# canary 验证\n(gdb) p/x *(uint32_t*)0x2006af00  # heap 前导 canary\n(gdb) p/x *(uint32_t*)(0x2006af20 + 0x150C0)  # heap 尾部 canary\n```

## 三层阻塞修复链（2026-05-14 验证通过）

当系统烧录后完全静默（无 CDC、无 MAVLink）或 stage 变量不推进时，三处必须检查：

| 层 | 检查内容 | 验证命令 |
|----|----------|----------|
| ① | `Flash.cpp` 无 `rt_thread_yield()` | `grep -n "yield" libraries/AP_HAL_RTT/Flash.cpp` → 无输出 |
| ② | `.config` 关闭 ASSERT | `grep ASSERT libraries/AP_HAL_RTT/hwdef/common/.config` → `# CONFIG_RT_DEBUGING_ASSERT is not set` |
| ③ | `setup_priority = 8` | `grep -n "setup_priority" libraries/AP_HAL_RTT/HAL_RTT_Class.cpp` → 显示 `rt_uint8_t setup_priority = 8;` |

## 验证步骤

### Step 1: 编译

```bash
cd /data/firmare/pogo-apm
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
```

输出: `build/rtt_cuav_v5/rtthread.bin` (~1.3MB, ROM ~84%)

### Step 2: 烧录

```bash
# 后台启动 OpenOCD
openocd -f Tools/debug/openocd-f7.cfg &
sleep 5

# 烧录 + 验证向量表
echo "program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify reset" \
  | timeout 120 nc localhost 4444 2>/dev/null

# 验证:
echo "mdw 0x08008000 4" | timeout 10 nc localhost 4444 2>/dev/null | grep "0x08008000"
# 期望: 0x2000xxxx 0x080eexxx ... (非 0xFFFFFFFF)
```

### Step 3: 监控启动（关键 — 用 Python 非 bash）

```bash
# 使用 rtt-stabilization-driver 中的脚本
python3 ~/.hermes/skills/embedded/rtt-stabilization-driver/scripts/l0_monitor.py 90 5
```

期望输出：
```
stage=0 run=0xDEADBEEF (BEFORE_RUN) ...
stage=630 run=0xAAAAAAAA (HAL_RUN_START) ...
stage=651 run=0x11111111 (AFTER_SETUP) entry=0x12345678 (ENTRY_REACHED)
🎉 MAIN LOOP ACTIVE! iter=1234 ...
```

**如果 stage 卡在 630+ 不推进** → 检查三层阻塞修复链（最常见问题：③ setup_priority 仍为 20）

### Step 4: 验证 CDC + MAVLink

```bash
# CDC 枚举检查
ls -la /dev/ttyACM*
# 选择最新时间戳的端口

# MAVLink 心跳
timeout 15 python3 -c "
from pymavlink import mavutil
c = mavutil.mavlink_connection('/dev/ttyACM1', baud=115200)
h = c.wait_heartbeat(timeout=10)
print(f'type={h.type} autopilot={h.autopilot} state={h.system_status}' if h else 'NO HEARTBEAT')
"
```

期望: `type=2 autopilot=3 state=5` (MAV_TYPE_QUADROTOR, MAV_AUTOPILOT_ARDUPILOTMEGA, MAV_STATE_STANDBY)

### Step 5: 传感器健康验证

```bash
timeout 15 python3 -c "
from pymavlink import mavutil
c = mavutil.mavlink_connection('/dev/ttyACM1', baud=115200)
c.wait_heartbeat(timeout=5)
c.mav.request_data_stream_send(c.target_system, c.target_component,
    mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)
import time; time.sleep(1)
for _ in range(5):
    m = c.recv_match(blocking=True, timeout=2)
    if not m: continue
    t = m.get_type()
    if t == 'SYS_STATUS':
        h = m.onboard_control_sensors_health
        bits = ['GYRO','ACCEL','MAG','BARO']
        healthy = [bits[i] for i in range(4) if h & (1 << i)]
        print(f'Healthy: {healthy}')
    elif t == 'RAW_IMU':
        print(f'IMU: acc=({m.xacc},{m.yacc},{m.zacc}) gyro=({m.xgyro},{m.ygyro},{m.zgyro})')
    elif t == 'SCALED_PRESSURE':
        print(f'Baro: {m.press_abs:.1f}hPa {m.temperature}deg')
    elif t == 'EKF_STATUS_REPORT':
        print(f'EKF: flags={m.flags:#x} vel={m.velocity_variance:.4f} pos_h={m.pos_horiz_variance:.4f}')
"
```

期望：
- `Healthy: GYRO, ACCEL, MAG, BARO` (全部健康)
- `IMU: acc=(-18,20,-1032)` (Z轴≈1G)
- `Baro: 1002.7hPa ~30deg`
- `EKF: flags=0xa7` (收敛中) 或逐步递增

## 已知 L0+ 问题（2026-05-14 更新——循环过载已修复）

| 问题 | 影响 | 优先级 |
|------|------|--------|
| EKF flags 含 UNINITIALIZED (0xa7) | 桌面环境无 GPS，自然状态 | 低 |
| Vservo=0（无伺服供电） | 桌面 USB 供电，正常 | 低 |

## 故障处理

| 现象 | 可能根因 | 修复 |
|------|----------|------|
| stage 卡在 630 (`AP_GPS::init`) | setup_priority 太低 | 检查③: 改为 8 |
| stage 卡在 662 (`startup_INS_ground` → `ins.init`) | **IOMCU UART 超时** — IOMCU 线程反复 `read_registers` 超时消耗调度器 tick | 多线程采样确认后，注释 `IOMCU_UART UART8` 或修复 UART8 通信；见 `rtt-stabilization-driver`→`references/setup-hang-diagnosis.md` §IOMCU |
| stage 卡在 682 | BMI055/BMI088 SPI probe 挂死（已在 hwdef.dat 中禁用） | 确认注释状态 `#IMU BMI055` / `#IMU BMI088` |
| CDC 不枚举（Main loop 已活跃） | USB 设备名不匹配 | 检查 `hwdef.h` 中 `HAL_RTT_UART_DEVICE_LIST` |
| CDC 枚举但无 MAVLink | UART TX 路径问题 | 检查 DTR: `grep dbg_dtr_set_cnt` |
| HardFault 循环复位 | flash 写入中断损坏 | 检查 CFSR/HFSR，重新 clean 烧录 |
| 传感器全部 unhealthy | SPI1 引脚/CS 错误 | 对比 ChibiOS hwdef.dat 引脚表 |
| 循环率 < 50Hz（loop_time_us > 20ms） | `delay_microseconds_boost()` 两处 bug | 见 `rtt-vs-chibios-reference` §10 |

### 循环率低诊断（loop_time_us > 20ms）

当系统已运行但循环率远低于 400Hz 时：

```bash
# 使用诊断脚本自动测量
python3 ~/.hermes/skills/embedded/rtt-stabilization-driver/scripts/diag-loop-rate.py 60 5
```

如果输出显示 loop_time_us ≈ work_us ≥ 50ms，根因是 `delay_microseconds_boost()` 的两个 bug：
1. **Bug ①**: sub-tick 延迟用了 `rt_thread_delay(1)`（1ms 最小睡眠），导致定时漂移累积
2. **Bug ②**: boost 优先级从不释放，主线程永久 prio 3

修复详细说明见 `rtt-vs-chibios-reference` §10。
