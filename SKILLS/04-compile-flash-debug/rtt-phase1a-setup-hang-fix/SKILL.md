---
name: "rtt-phase1a-setup-hang-fix"
description: "Phase 1A — 修复 RTT CUAV V5 baseline boot 在 setup_stage≈662 ins.init() 挂死的问题。分三阶段：①精确定位阻塞点 ②针对性修复 ③双重验证"
triggers:
  - "phase 1a"
  - "setup hang"
  - "662 hang"
  - "ins.init hang"
  - "phase1a"
---

# Phase 1A — Fix baseline boot (setup_stage≈662 ins.init() hang)

> 批准日期：2026-05-16 廖博士确认
> 当前基线：HEAD `1e42da1c6a` (已含 app_descriptor 后处理 + 启动优先级对齐 + DWT boost)
> 参考铁律：`rtt-chibios-11-porting-discipline` §四（每行修改必须有 ChibiOS 参考行号）

---

## 问题描述

烧录后：
- CDC ttyACM 枚举 ✅（A4-Ops 已验证 bootloader 跳转正常）
- MAVLink 心跳 ❌（静默）
- 原因：`ins.init()` 在 `setup()` 中永久阻塞（CPU idle=99%, PC 在 micros64(), tick_calls=0）

## 已知分析（来自 kanban 评论 t_e28bd2bc）

- `detect_backends()` 可能通过 → 进入 `_start_backends()`
- 第一个 Invensense backend `start()` 中 `register_periodic_callback()` 创建 DeviceBus 线程
- 线程启动后可能访问尚未完全初始化的 backend → 竞态
- 但**从未被精确定位过**（只有 PC 指针快照，无分段 stage 标记）

---

## Phase A1: 精确定位阻塞点

### 修改文件：`libraries/AP_HAL_RTT/HAL_RTT_Class.cpp`

在 `_main_loop_entry()` 中 setup() 调用前后插入分段标记。定义一个新的全局 `rtt_dbg_phase1a_stage` 变量。

**具体修改：**

1. 在 `rtt_dbg_hal_run_called` 定义区附近添加新变量：
```c
// Phase 1A debug — pinpoints ins.init() blockage
volatile uint32_t rtt_dbg_phase1a_stage = 0;
```

2. 在 `a->callbacks->setup()` 调用周围插入阶段标记（L216 附近）：
```c
rtt_dbg_phase1a_stage = 660;  // before setup() — callbacks->setup is where ins.init() lives
a->callbacks->setup();          // ← 这里卡住
rtt_dbg_phase1a_stage = 670;  // after setup() — only reached if setup returns
```

3. 在 `rtt_dbg_hal_run_called = 0x11111111` 后加：
```c
rtt_dbg_phase1a_stage = 680;  // main loop entry reached
```

**ChibiOS 参考**：HAL_ChibiOS_Class.cpp 无直接等价的分 stage 调试 — 这是 RTT 专用的诊断工具，不影响功能逻辑。

### 验证方法

```bash
# 1. 编译
cd /data/firmare/pogo-apm && scons --v=ArduCopter --target=cuav_v5 -j$(nproc)

# 2. 烧录（假设 OpenOCD 已运行）
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify" \
  -c "reset run" -c "shutdown"

# 3. 监控启动（等待 15-30s）
sleep 15

# 4. 检查 CDC 枚举
ls /dev/ttyACM*

# 5. 如果 CDC 存在，检查 MAVLink
timeout 10 python3 -c "
from pymavlink import mavutil
m = mavutil.mavlink_connection('/dev/ttyACM1', baud=115200)
h = m.wait_heartbeat(timeout=8)
print(f'HEARTBEAT: {h}' if h else 'NO HEARTBEAT')
"

# 6. 用 OpenOCD 读 stage 变量
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "init" -c "halt" \
  -c "mdw 0x20000000+<offset>"  # 需先编译确定 rtt_dbg_phase1a_stage 的地址
```

---

## Phase A2: 针对性修复（根据 A1 结果选择）

### 场景 A: rtt_dbg_phase1a_stage = 660 (setup() 内 ins.init() 卡死)

#### 子场景 A1: SPI probe 超时（detect_backends 卡住）

**症状：** Setup_stage 在 660，SPI 探测不返回。

**可能根因：**
- ICM20689/20602 SPI WHO_AM_I 读取超时
- 传感器供电未启用（SENSOR_EN PE3 时序）

**修复方案：**
1. 在 `HAL_RTT::run()` 的 `GPIO::init()` 后加 10ms 延迟等待传感器供电稳定
2. 或在 `_main_loop_entry()` 中 setup() 前主动拉高 SENSOR_EN

**ChibiOS 参考：**
- `hwdef/fmuv5/hwdef.dat` 中 SENSOR_EN 定义在 `GPIO` 段，由 `hal.gpio->init()` 在 `setup()` 前初始化
- RTT 当前在 `run()` 中已调用 `hal.gpio->init()` (L278)，确认时序正确

#### 子场景 A2: start() 中 DeviceBus 线程竞态

**症状：** detect_backends 完成（可通过 OpenOCD 读 `_backend_count` 确认），然后在 start() 中卡住。

**可能根因：**
- `register_periodic_callback()` 创建 DeviceBus 线程 → 线程启动后遍历 callbacks
- callbacks 链可能为空或包含未初始化条目
- 或线程的 `binfo->semaphore` 在 take(10ms) 超时后重试循环

**修复方案：**
1. 检查 SPIDevice.cpp 中 `register_periodic_callback()` 是否与 ChibiOS 行为一致
2. 确保 DeviceBus 线程构造完成后才允许运行（添加就绪标志）

**ChibiOS 参考：**
- `Device.cpp:130-180` — `register_periodic_callback()` 实现
- RTT: `DeviceBus.cpp:49` — 已对照 ChibiOS 修复了 `_thread_started` 标志位置

#### 子场景 A3: 主线程优先级导致 timer 线程饿死

**症状：** timer 线程的 ADC 采样永远跑不起来 → ins.init 等样本超时。

**可能根因：** 当前 STARTUP priority = 15(太低! )。查看优先级表：
- timer(4) > SPI(4) > UART(6) > MAIN_SETUP(15) > storage(16) > IO(18)
- 主线程 prio=15 低于 timer(4) 和 SPI(4)，所以 timer 可以抢占主线程
- 但主线程在 delay() 中 rt_thread_delay() 后，timer 能在此期间运行

但 tick_calls=0 说明 uart 线程从未运行过。这不是优先级问题，因为 uart(6) > main(15)。

**tick_calls=0 的真正含义：** UART thread 在 `!_hal_initialized` 上循环等待。如果在 setup() 执行前 `_hal_initialized` 未被设置，所有线程都会等。

当前代码：`hal_initialized()` 在 L212（setup 之前）调用，所以线程应在 setup 开始时已释放。

**关键检查：** 用 GDB 检查 `sched->_hal_initialized` 的值。

### 场景 B: rtt_dbg_phase1a_stage = 670 (setup() 完成但 MAVLink 无心跳)

**症状：** setup 完成，主循环运行，CDC 枚举，但无 MAVLink 心跳。

**可能根因：**
- GCS 串口配置问题（SERIAL0 未正确映射到 CDC）
- MAVLink 流控 (DTR) 问题

**修复：** 参考已知的 CDC TX 调试铁律 — 先查 DTR（`g_dtr_active`/`dbg_dtr_set_cnt`）

### 场景 C: 主循环运行且 MAVLink 心跳正常但传感器数据静默

**症状：** HEARTBEAT 收到，但 RAW_IMU 无数据。

**可能根因：**
- SPI 数据路径故障（引脚错误、寄存器配置差异）
- IMU 探测时配置了错误寄存器值

---

## Phase A3: 双重验证

### 验证标准

| 检查项 | 通过条件 | 命令 |
|--------|---------|------|
| OpenOCD HardFault | CFSR=0, HFSR=0 | `echo "mdw 0xE000ED28 2" \| nc localhost 4444` |
| 启动完成 | rtt_dbg_hal_run_called = 0x11111111 | GDB read addr |
| 主循环活跃 | rtt_dbg_main_loop_iterations 持续增长 | OpenOCD 多次 halt 读 |
| CDC 枚举 | /dev/ttyACM* 存在 | `ls /dev/ttyACM*` |
| MAVLink 心跳 | 1Hz, system_status=STANDBY | pymavlink wait_heartbeat |
| 热复位持久 | reset init 后 CDC 重新枚举 | OpenOCD reset + 等待 |

### 稳定性测试

```bash
# 运行 60 秒，每 10s 采样一次 stage/CFSR/MAVLink
for i in $(seq 6); do
  sleep 10
  echo "=== T+${i}0s ==="
  # OpenOCD check
  echo -e "halt\nmdw 0xE000ED28 1\nresume" | nc -w3 localhost 4444 2>/dev/null
  # MAVLink heartbeat
  timeout 3 python3 -c "
from pymavlink import mavutil
m = mavutil.mavlink_connection('/dev/ttyACM1', baud=115200)
h = m.wait_heartbeat(timeout=2)
print(f'HBT: {\"OK\" if h else \"MISS\"}'  )
" 2>/dev/null
done
```

---

## 执行纪律

1. **每次只改 ≤3 个文件** — 当前 Phase A1 只涉及 1 个文件 (HAL_RTT_Class.cpp)
2. **每步标注 ChibiOS 参考行号** — 调试变量无 ChibiOS 等价物，标注为"RTT 专用诊断"
3. **不修改 `modules/` 下的 RT-Thread 内核或 ChibiOS 代码**
4. **不引入 `#ifdef HAL_RTT`**
5. **每修改完一批 → 编译 → 烧录 → 验证**
