# INS-init 内部 setup_stage 分段标记方案

## 用途

原 `setup_stage=662` 只标记 `ins.init()` 入口，无法区分内部子步骤。

添加以下标记（2026-05-16 实施，已验证通过）：

```
AP_InertialSensor::init()
  665  → _start_backends() 前
  666  → _start_backends() 返回
  680  → init_gyro() 前
  681  → init_gyro() 后

AP_InertialSensor::_start_backends()
  670  → entry
  671  → detect_backends() done
  672  → backend[0]->start() 前 (ICM20689)
  673  → backend[0]->start() 后
  674  → backend[1]->start() 前 (ICM20602)
  675  → backend[1]->start() 后
  679  → all backends started
```

## 修改方法

**文件**: `libraries/AP_InertialSensor/AP_InertialSensor.cpp`

1. 在 includes 后添加 extern:
```cpp
// RTT diagnostic: fine-grained INS init hang marker
extern volatile uint32_t rtt_dbg_setup_stage;
```

2. 在 `_start_backends()` 中每个 backend start 前后加标记
3. 在 `init()` 中 `_start_backends()` 和 `init_gyro()` 前后加标记

**注意**：这是 RTT 专用诊断标记，不影响功能逻辑。`rtt_dbg_setup_stage` 在 `libraries/AP_Vehicle/AP_Vehicle.cpp` 定义。
