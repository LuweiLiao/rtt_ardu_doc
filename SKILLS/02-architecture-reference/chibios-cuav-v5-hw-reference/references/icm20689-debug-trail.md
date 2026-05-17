# ICM20689/ICM20602 SPI 数据读取失败 — 诊断记录

## 现象（2026-05-09）
- RAW_IMU (IMU1, ICM20689): accel=0,0,0 gyro=0,0,0
- SCALED_IMU2 (IMU2, ICM20602): accel=0,0,0 gyro=0,0,0
- SCALED_IMU3 (IMU3, BMI055): accel=13,-12,-997 (工作正常!)
- SCALED_PRESSURE (MS5611, SPI4): 1014hPa (工作正常!)
- 三者共用 SPI1，BMI055 工作说明总线本身没问题
- sensors_present 中有 IMU (backend_count=3)，但 GYRO/ACCEL health bit=0

## 排除的根因
1. ❌ SPI 传输永久挂死 → 已修复（增加超时中止逻辑）
2. ❌ SPI 引脚错误 → 已对齐 ChibiOS PA6/PD7/PG11
3. ❌ CS 引脚问题 → BMI055 工作证明 CS 正常
4. ❌ SPI4 影响 SPI1 → 独立总线

## 可能的根因
1. **SPI 频率不匹配**：spi1_poll_transfer() 硬编码 BR_0|BR_1 = /16 = 6.75MHz
   - ICM20689 低速为 2MHz
   - **读写 WHO_AM_I 可在 6.75MHz 工作**（短传输）
   - **但后续寄存器配置/FIFO 读可能对时序更敏感**
   - 修复方向：在 spi1_poll_transfer() 中根据 desc.lowspeed 动态设置 BR 分频

2. **Invensense 寄存器访问协议差异**
   - ICM20689 使用 MSb=1 的多字节读协议
   - 需要确认 AP_InertialSensor_Invensense 驱动是否兼容寄存器级 polling

3. **ICM20689 内部状态机**
   - 复位后需要等待一定时间
   - PWR_MGMT_1 配置可能有误
   - 需要 GDB 追踪 ICM20689 初始化序列

## 下一步调试方向
1. 在 spi1_poll_transfer() 中实现动态波特率：
   ```c
   // 使用传入的 dev/lld 结构体获取目标频率
   // BR = log2(PCLK / target_hz)
   // ICM20689: 低速 2MHz → BR ~ 5 (/32 = 3.375MHz)
   ```
2. 用 GDB 断点跟踪 ICM20689::_init() 完整执行流
3. 检查 `AP_InertialSensor_Invensense::_init()` 中是否有 SPI 特定的 `set_chip_select` 模式使用不当
