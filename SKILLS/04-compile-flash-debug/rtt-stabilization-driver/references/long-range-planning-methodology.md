# 规划铁律：方案必须覆盖完整路线图

> 2026-05-14 廖博士反复纠正："方案太短，不够长远"。之前写的 L1 推进方案（3 Phase 短期）被否决，改为 6 Phase 完整路线图。

## 原则

每次写 RTT 移植方案时，**必须站在完整移植角度规划**，从当前状态一直到可发布稳定版本。不能只写"下一步要做的事"。

## 完整路线图模板

参考 `.hermes/plans/rtt-complete-roadmap.md`（13233 字节，6 Phase）：

```
Phase 1: L1 传感器数据流（短期，当前阶段）
    目标：GYRO/ACCEL/IMU stable、MS5611 BARO 工作、EKF+ATTITUDE 输出
    待解决问题：[DeviceBus take(10)→BLOCK_FOREVER, SPIDevice get_semaphore→总线锁, MS5611 probe]
    验证标准：RAW_IMU zacc≈-1000、SCALED_PRESSURE>900hPa、EKF flags=167

Phase 2: L2 核心功能（中期）
    目标：IOMCU RC 输入、GPS、SD卡日志、参数存取
    待解决问题：[缺失RCOutput_iofirmware.cpp, 缺失sdcard.cpp, GPS UART]

Phase 3: 外设完善（中期）
    目标：CAN 总线、ADC DMA、USB CDC TX 稳定性

Phase 4-6: 性能→长期稳定→其他硬件
```

## 每个 Phase 必须包含

1. **目标** — 可测量的里程碑
2. **待解决问题** — 每条一个，列优先级
3. **验证标准** — 具体 MAVLink 消息字段值
4. **依赖项** — 前面的 Phase 或外部依赖

## 计划的存放位置

- 短期执行计划：`.hermes/plans/rtt-<name>-plan.md`
- 写入实现步骤到 skill，而非记忆（`skill_manage(action='create')`）
- 每条修改标注 ChibiOS 参考文件+行号

## 廖博士确认流程

计划写好 → 向廖博士汇报（分析+路线图+时间预估）→ 等确认 → 按计划执行 → 逐条验证。
