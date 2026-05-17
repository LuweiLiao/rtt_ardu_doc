---
name: rtt-l1-sensor-pipeline
description: RTT ArduPilot CUAV V5 Phase 1 L1传感器数据流修复 —— 严格执行监督CC工作流
---

# RTT L1 传感器数据流修复 — 监督 CC 工作流

> 创建：2026-05-15 | 管理人：Hermes Agent | 执行人：CC (严格限定范围)
> 铁律：**每行改动必须有 ChibiOS 参考行**（rtt-chibios-1-1-port.mdc）
> 禁止：无 ChibiOS 参考的猜测修改、越界改无关文件

## 整体流程

每次 step = 管理人诊断→写计划→用户确认→CC执行→管理人复查→验证

```
./trae/rules/ 读规则 → skill 写计划 → 廖博士确认 → CC 执行 → git diff 复查 → 编译 → 烧录 → 双重验证
```

## Phase 1 Pre-build: 确保 app_descriptor 存在（2026-05-15 发现——阻断 L0 启动的根因！）

**如果不做这步，bootloader 拒绝跳转，PC 卡在 0x08003628，CDC 枚举但无 MAVLink。**

必须修改 hwdef.dat 添加 2 行，让二进制包含 8 字节签名字节供 bootloader 验证：

```bash
# 在 libraries/AP_HAL_RTT/hwdef/cuav_v5/hwdef.dat 中添加
# （在 'define AP_FILESYSTEM_POSIX_ENABLED 1' 后）
define AP_CHECK_FIRMWARE_ENABLED 1
APJ_BOARD_ID TARGET_HW_PX4_FMU_V5
```

**验证签名存在**（编译后立刻做，否则烧了也白烧）：
```bash
python3 -c "
with open('build/rtt_cuav_v5/rtthread.bin', 'rb') as f:
    d = f.read()
sig = bytes([0x40, 0xa2, 0xe4, 0xf1, 0x64, 0x68, 0x91, 0x06])
pos = d.find(sig)
print(f'{\"✅\" if pos>=0 else \"❌\"} SIG at 0x{pos:x}')
if pos>=0:
    bid = int.from_bytes(d[pos+8:pos+12], 'little')
    print(f'Board ID: {bid} (expect 50)' if bid==50 else f'❌ ID={bid}')
"
```

## Phase 1 Steps

### Step 1.1: DeviceBus.cpp take(10) → BLOCK_FOREVER

**参考：** ChibiOS Device.cpp:60-80 (WITH_SEMAPHORE / chSemWait)

**问题：** `binfo->semaphore.take(10)` 用 10ms 超时。若主线程持 SPI 总线锁 >10ms，bus 线程跳过采样 → GYRO error_count 累积超标。

**修改：**
- 文件：`libraries/AP_HAL_RTT/DeviceBus.cpp`
- 行49：`take(10)` → `take(HAL_SEMAPHORE_BLOCK_FOREVER)`
- 注意：Semaphores.cpp L82-84 已正确处理 `timeout_ms == 0` → `RT_WAITING_FOREVER`

**验证：** `git diff` 确认只有这一行变化。

---

### Step 1.2: SPIDevice.cpp get_semaphore() → 返回总线锁

**参考：** ChibiOS SPIDevice.cpp:337-340 (`return &bus.semaphore`)

**问题：** `get_semaphore()` 返回 `&_sem`（per-device 私有锁）。两个 SPI1 上的设备（ICM20689 + BMI055）各拿各的锁，不阻塞彼此 → 并行访问 SPI1 总线导致数据冲突。

**修改：**
- 文件：`libraries/AP_HAL_RTT/SPIDevice.cpp`
- 行649：`return &_sem` → `return &_bus->semaphore`
- 注意：`_bus` 是 `DeviceBus *`，`DeviceBus::semaphore` 是 public 的 `Semaphore` 类型。`&_bus->semaphore` 返回类型为 `AP_HAL::Semaphore *`（`RTT::Semaphore` 继承自 `AP_HAL::Semaphore`，隐式转换合法）

**验证：** `git diff` 确认只有这一行变化。

---

### Step 1.3: 编译 + 烧录 + 双重验证 RAW_IMU

**编译：**
```bash
cd /data/firmare/pogo-apm && scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
```

**烧录（必须用 program 命令）：**
```bash
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "adapter speed 2000" \
  -c "program /data/firmare/pogo-apm/build/rtt_cuav_v5/rtthread.bin 0x08008000 verify" \
  -c "reset run" \
  -c "shutdown"
```

⚠️ 禁止用 `flash write_image` telnet！静默失败！ 

**L1 验证标准：**

| 检查项 | 命令 | 通过标准 |
|--------|------|---------|
| 无 HardFault | OpenOCD halt 读 CFSR | CFSR=0, HFSR=0 |
| MAVLink 心跳 | `pymavlink --device /dev/ttyACM1 --baud 115200` | HEARTBEAT 1Hz, STANDBY |
| 主循环运行 | 读 `rtt_dbg_main_loop_iterations` | 持续递增 |
| RAW_IMU GYRO | mavlink 消息 | xgyro/ygyro/zgyro 非零 |
| RAW_IMU ACCEL | mavlink 消息 | xacc/yacc/zacc 非零（含重力） |
| GYRO healthy | 参数 | _gyro_error_count 不持续增长 |
| ACCEL healthy | 参数 | _accel_error_count 不持续增长 |

**验证脚本（pymavlink）：**
```python
from pymavlink import mavutil
m = mavutil.mavlink_connection('/dev/ttyACM1', 115200)
m.wait_heartbeat()
print(f"HEARTBEAT: {m.target_system}/{m.target_component}")
for _ in range(50):
    msg = m.recv_match(type=['RAW_IMU'], blocking=True, timeout=2)
    if msg:
        print(f"GYRO: {msg.xgyro:.2f} {msg.ygyro:.2f} {msg.zgyro:.2f}  "
              f"ACC: {msg.xacc} {msg.yacc} {msg.zacc}")
```

---

### Step 1.4: MS5611 气压计验证

**引脚验证（已确认）：**

| 信号 | 引脚 | 状态 |
|------|------|------|
| SPI4 SCK | PE12 | ✅ |
| SPI4 MISO | PE13 | ✅ |
| SPI4 MOSI | PE14 | ✅ |

**验证：** 启动后检查 SCALED_PRESSURE 消息
```python
msg = m.recv_match(type=['SCALED_PRESSURE', 'SCALED_PRESSURE2'], blocking=True, timeout=5)
if msg: print(f"Press={msg.press_abs:.2f}mbar Temp={msg.temperature:.2f}C")
```

---

### Step 1.5: EKF/ATTITUDE 验证

等待 ~30s 让 EKF 收敛后：
```python
msg = m.recv_match(type=['ATTITUDE'], blocking=True, timeout=5)
if msg: print(f"Roll={msg.roll:.2f} Pitch={msg.pitch:.2f} Yaw={msg.yaw:.2f}")
```

---

## 验证失败回退

只需修改了两个文件 DeviceBus.cpp 和 SPIDevice.cpp，最多改 2 行：
```bash
cd /data/firmare/pogo-apm
git checkout -- libraries/AP_HAL_RTT/DeviceBus.cpp
git checkout -- libraries/AP_HAL_RTT/SPIDevice.cpp
```

## Phase 1 Step 1.3 诊断记录（2026-05-15）

### 当前状态
- 编译 ✅ ROM 85.60%, RAM 54.82%
- 烧录 ✅ Verified OK
- 启动 ✅ hal_run_called=0xBBBBBBBB, entry_called=0x12345678
- **主循环未迭代** ❌ iterations=0
- **卡在 setup_stage=620** — `init_rc_in()` 完 → `allocate_motors()` 前

### PC 轨迹
1. 首次 halt: PC=0x08070130 → `Util::get_micros64()` (DWT 延迟中)
2. 30s 后 halt: PC=0x0806ec18 → `_delay_microseconds_dwt()` (DWT 忙等)
3. 5s 后 halt: PC=0x080ff6b4 → `idle_thread_entry` (主线程已退出)
4. DWT 状态检查: CYCCNT=0x47C69564 ✅, CTRL=0x40000001 ✅, DEMCR=0x01000000 ✅

### 根因分析
DWT 计数器正常运行，但主线程在 `_delay_microseconds_dwt()` 中似乎"永久等待"。可能解释：
a) **线程优先级反转**：timer/MONITOR 线程优先级高于 MAIN，主线程被持续抢占
b) **某处 delay 参数异常**：调用链中传递了异常大的 us 值
c) **setup 完成后线程退出**：主线程完成 setup 后退出，被 idle 回收

### 下一步调试方向
1. 用 GDB 设断点在 `_delay_microseconds_dwt()`，捕获传入参数值
2. 检查调用链：`bt` 看是谁调用了 delay
3. 尝试将 `_delay_microseconds_dwt()` 中的 `dsb` 改为 `nop` 看是否区别
4. 或回退到 L0 基线验证是否纯基线也卡在 stage 620

## CC 监督规则

1. 每次只委托 **1 个文件** 的修改（每步只委托 step 1.1 或 step 1.2）
2. 必须明确告诉 CC：**只改哪一行，改成什么**，禁止越界
3. CC 改完后管理员 `git diff` 复查，通过后才下一步
4. CC 任何超出范围的修改 → 全部 `git checkout --` 回滚
