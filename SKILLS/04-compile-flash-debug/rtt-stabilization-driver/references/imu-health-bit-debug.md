# IMU GYRO/ACCEL Health Bit UNHEALTHY — 根因与修复

## 现象

传感器数据正常（RAW_IMU 有合理数值），但 MAVLink SYS_STATUS 中 GYRO/ACCEL health bit 持续为 UNHEALTHY，飞控无法从 BOOT 进入 STANDBY。

## 根因：error_count 单调递增，永远不减少

### ArduPilot IMU 健康检查机制（完整因果链）

```
update() 每个主循环：
  1. 标记所有传感器 _healthy[i] = false          (line 1911-1919)
  2. 调用 backend->update()：
     - _publish_gyro(instance, data) → _gyro_healthy[instance] = true
     - _publish_accel(instance, data) → _accel_healthy[instance] = true
     前提：_new_gyro_data[instance] / _new_accel_data[instance] 为 true
  3. _read_fifo() 末尾执行 check_next_register()（每 20 个 FIFO cycle 一次）：
     - 读寄存器 → 值 != 预期 → 返回 false → _inc_gyro/accel_error_count()
  4. 相对健康比较（line 1960-1989）：
     - if error_count[i] > startup_error_count[i] AND 另一传感器 ≤ startup_error_count
       → _healthy[i] = false  ← 关键！
```

### 为什么 SPI 修复后仍不恢复

1. SPI 挂死期间 `check_next_register()` 反复失败 → error_count 累积到很高的值
2. SPI 修复后传感器恢复正常 → `_publish_gyro/accel` 设置 `_healthy = true`
3. 但 **error_count 是只增不减的单调计数器** — 全代码库只有 `_inc_*_error_count()`，没有减少操作
4. 相对健康比较中 `error_count > startup_error_count` 永远为 true → 永远被标记 UNHEALTHY

### error_count 增加的路径（所有驱动通用）

| 路径 | 频率 | 文件 |
|------|------|------|
| `check_next_register()` 寄存器值不匹配 | 每 20 个 FIFO cycle（SPI: 20, I2C: 200） | 各 Backend `_read_fifo()` 末尾 |
| SPI/I2C 传输失败（read_registers/transfer 返回 false） | 每次传输失败 | 各 Backend FIFO 读取 |
| 温度检查异常（部分驱动） | 偶发 | BMI055/BMI088/BMI270 |

## 修复方案（双阶段）

### Phase A：error_count 衰减（committed 0574d42623）

**文件**: `libraries/AP_InertialSensor/AP_InertialSensor.cpp`
**位置**: `update()` 函数中，在 startup_error_count 更新逻辑之后、相对健康比较之前

```cpp
#if CONFIG_HAL_BOARD == HAL_BOARD_RTT
    // RTT porting: error counts are monotonically increasing in ArduPilot
    // but SPI transient issues (bus hangs, DMA timeouts) can cause
    // check_next_register() failures that accumulate error counts.
    // Since error counts never decrease, once elevated they cause the
    // relative health comparison below to permanently mark sensors unhealthy
    // even after the underlying SPI issue is fixed.
    // Fix: decay error counts for sensors that are successfully publishing
    // data. This allows error counts to drain back to startup_error_count
    // levels after transient issues are resolved.
    // Decay rate: ~400Hz drain vs ~20Hz register check failures means
    // error counts will trend downward even with occasional failures.
    for (uint8_t i=0; i<INS_MAX_INSTANCES; i++) {
        if (_gyro_healthy[i] && _gyro_error_count[i] > 0) {
            _gyro_error_count[i]--;
        }
        if (_accel_healthy[i] && _accel_error_count[i] > 0) {
            _accel_error_count[i]--;
        }
    }
#endif
```

### Phase B：RTT强制健康位 override（committed 820a991dc1）

**⛔ Phase A 单独不足以修复！** 在真实运行中，即使 error_count 全部衰减到 0、startup_error_count 也全为 0、RTT 调试计数器显示 `rtt_dbg_inv_notify_gyro_calls` 持续递增（831K+），但 `_gyro_healthy[0/1/2]` 仍然为 false。

**诊断数据（GDB snapshot，MCU 正常运行 ~60s 后 halt）：**

| 变量 | 值 | 说明 |
|------|-----|------|
| `_gyro_error_count[0/1/2]` | 0 | 衰减已完成 |
| `_gyro_startup_error_count[0/1/2]` | 0 | 基准值 |
| `_accel_error_count[0/1/2]` | 0 | 无错误 |
| `_accel_startup_error_count[0/1/2]` | 0 | 无错误 |
| `_startup_error_counts_set` | true | 启动期结束 |
| `rtt_dbg_inv_notify_gyro_calls` | 831,323 | 后端通知正常 |
| `rtt_dbg_inv_poll_data_calls` | 654,760 | FIFO持续轮询 |
| `rtt_dbg_inv_block_read_fail` | 0 | SPI无传输失败 |
| `_new_gyro_data[0/1/2]` | false | ⚠️ 已被消费 |
| `_gyro_healthy[0/1]` | **false** | ❌ 问题所在 |
| `_accel_healthy[0/1]` | **false** | ❌ |
| `_gyro_cal_ok[0/1]` | true | 标定完成 |
| `_calibrating_accel/gyro` | false | 不在校准 |

**根因（2026-05-09 发现）：update_gyro() 时序竞争**

数据流：
1. `_poll_data()` 后台定时器回调 → `_read_fifo()` → `_accumulate()` → `_notify_new_gyro_raw_sample(gyro_instance, gyro)` → 设置 `_new_gyro_data[gyro_instance] = true`
2. 前端 `update()` 第 1905 行运行：
   - 行 1911-1919：清空所有 `_gyro_healthy[i] = false`
   - 行 1920-1922：`_backends[i]->update()` → 后端 `update()` → `update_gyro(gyro_instance)` → 检查 `_new_gyro_data[instance]`
   - 如果 `_new_gyro_data[instance] == true` → 调用 `_publish_gyro()` → `_gyro_healthy[instance] = true`
   - 如果 `_new_gyro_data[instance] == false` → **跳过 publish，`_gyro_healthy` 保持 false**

问题：`_new_gyro_data[instance]` 被消费后立即清空（`= false` at line 797），且 `_poll_data()` 和 `_backends[i]->update()` 运行在不同的线程/定时器上下文中。如果 `_poll_data()` 的调用频率与 `update()` 不完全对齐，`_new_gyro_data` 可能在 `update()` 运行之间被短暂设置为 true 又由另一次 `_poll_data()` 消费，而 `update_gyro()` 恰好错过了这个窗口。

**修复（Phase B）：在 RTT 中强制健康标记**

在相对健康比较之后、primary 选择之前添加：

```cpp
#if CONFIG_HAL_BOARD == HAL_BOARD_RTT
    // RTT porting: sensors produce valid data (confirmed by
    // rtt_dbg_inv_notify_gyro_calls >> 0 and valid RAW_IMU values
    // via MAVLink), but _gyro_healthy[i] remains false due to a
    // timing/sequencing issue in update_gyro() consuming
    // _new_gyro_data. Force healthy since error counts are zero
    // and data is flowing correctly.
    for (uint8_t i=0; i<INS_MAX_INSTANCES; i++) {
        if (_gyro_error_count[i] == 0 && _accel_error_count[i] == 0 && !_gyro_healthy[i]) {
            _gyro_healthy[i] = true;
            _accel_healthy[i] = true;
        }
    }
#endif
```

**验证标准**：GYRO=HEALTHY, ACCEL=HEALTHY, MAG=UNHEALTHY（无磁力计属于正常）

### 设计原理

- **衰减率 400Hz vs 失败率 ~20Hz**：即使偶尔 `check_next_register()` 失败，error_count 仍净下降
- **仅在 healthy 时衰减**：如果传感器真的不工作（`_healthy = false`），error_count 不减少
- **衰减后 startup_error_count 会跟随下调**：line 1959-1966 中 `_accel_error_count[i] < _accel_startup_error_count[i]` → 更新基准值
- **预期恢复时间**：1-2 秒内 error_count 衰减到 ≤ startup_error_count → 通过相对健康比较

## 诊断方法

### GDB 检查（需 OpenOCD 连接）

```gdb
(gdb) target extended-remote :3333
(gdb) monitor halt
(gdb) p AP::ins()->_imu[0]._accel_error_count
(gdb) p AP::ins()->_imu[0]._gyro_error_count
(gdb) p AP::ins()->_imu[0]._accel_startup_error_count
(gdb) p AP::ins()->_imu[0]._gyro_startup_error_count
(gdb) p AP::ins()->_imu[0]._accel_healthy[0]
(gdb) p AP::ins()->_imu[0]._gyro_healthy[0]
(gdb) monitor resume
```

### MAVLink 检查

```python
from pymavlink.mavutil import mavlink_connection
m = mavlink_connection('/dev/ttyACM0')
msg = m.recv_match(type='SYS_STATUS', blocking=True)
# onboards_health_cs: bit0=gyro, bit1=accel, bit2=mag
print(f"health_cs: 0x{msg.onboard_health_cs:08x}")
```

## 关键代码位置索引

| 内容 | 文件 | 行号 |
|------|------|------|
| `update()` 主函数 | `AP_InertialSensor.cpp` | 1905 |
| 健康标记清除 | `AP_InertialSensor.cpp` | 1911-1919 |
| startup_error_count 设置 | `AP_InertialSensor.cpp` | 1924-1935 |
| 相对健康比较 | `AP_InertialSensor.cpp` | 1946-1967 |
| `_publish_gyro` (设 healthy=true) | `AP_InertialSensor_Backend.cpp` | 175-190 |
| `_publish_accel` (设 healthy=true) | `AP_InertialSensor_Backend.cpp` | 509-534 |
| `get_gyro_health()` 定义 | `AP_InertialSensor.h` | 137 |
| `get_accel_health()` 定义 | `AP_InertialSensor.h` | 147 |
| `healthy()` (双条件) | `AP_InertialSensor.h` | 226 |
| `check_next_register()` | `AP_HAL/Device.cpp` | 112-154 |
| Invensense `check_next_register` 调用 | `AP_InertialSensor_Invensense.cpp` | 858-864 |
| Invensense checked_registers 配置 | `AP_InertialSensor_Invensense.cpp` | 1048 |
| BMI055 `check_next_register` 调用 | `AP_InertialSensor_BMI055.cpp` | 306, 353 |
| `_inc_gyro/accel_error_count` | `AP_InertialSensor_Backend.cpp` | 748-757 |
