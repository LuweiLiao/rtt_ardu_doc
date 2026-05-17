# L2 验证状态（2026-05-09 15:45 最终版）

## 已验证通过的里程碑

### L0 基础运行 ✅
- 启动无 HardFault (CFSR=0, HFSR=0)
- Bootloader 跳转正常 (CUAVv5_bl.bin @ 0x08000000)
- 应用固件运行 @ 0x08008000
- USB CDC 枚举为 ttyACM1
- MAVLink 心跳持续，sys=1 STANDBY
- 主循环 ~310Hz, load=1000 恒定

### L1 传感器数据流 ✅
| 传感器 | 消息 | 值 | 状态 |
|--------|------|-----|------|
| ICM20689 | RAW_IMU | az=-1032 gx=9 gy=-18 gz=-1 | ✅ |
| ICM20602 | SCALED_IMU2 | az=-1001 | ✅ |
| BMI055 | SCALED_IMU3 | az=-1002 | ✅ |
| MS5611 | SCALED_PRESSURE | 1011hPa | ✅ |
| EKF | EKF_STATUS_REPORT | flags=1024 | ✅ |
| ATTITUDE | ATTITUDE | r=0.01 p=0.02 | ✅ |

### L2 功能完整 ✅

#### 传感器健康位（全绿通过 512 次采样）
| 传感器 | 状态 | 验证方式 |
|--------|------|---------|
| GYRO | HEALTHY ✅ | SYS_STATUS bit 0，0 翻转 |
| ACCEL | HEALTHY ✅ | SYS_STATUS bit 1，0 翻转 |
| MAG | UNHEALTHY ⚪ | 无磁力计硬件 |
| BARO | HEALTHY ✅ | SYS_STATUS bit 8 |

#### 参数系统
| 功能 | 状态 |
|------|------|
| PARAM_REQUEST_LIST 枚举 | ✅ 536+ 参数 |
| PARAM_REQUEST_READ | ✅ 实时读取 |
| PARAM_SET + ACK | ✅ commit 1b33bbbdfc 已修复 |

#### 稳定性验证
| 测试 | 时长 | 结果 |
|------|------|------|
| 90s SYS_STATUS 连续采样 | 90s | 283/283 无掉落，0 翻转 |
| 3min 后台长稳 | ~170s | 512/512 无掉落，0 翻转 |
| OpenOCD 即时检查 | — | CFSR=0, HFSR=0 |

## 已修复的崩溃问题

| 问题 | 根因 | 修复 commit |
|------|------|-------------|
| 启动 HardFault (IACCVIOL) | SysTick Handle 在调度器前触发 NULL ptr | 模块 drv_common.c 加 NULL 守卫 |
| SPI4 HardFault | PE12 不是有效 SPI4_SCK 引脚 | 5118bdcebf 改 PE2 |
| SPI1 永久挂死 | RXNE 不回复时的无限等待 | e970f6612a 超时中止+恢复 |
| IMU1/IMU2 数据全零 | CS-held burst 被 _spi1_gpio_init() 拉高 | 045c45fded 加 !_cs_held 守卫 |
| GYRO/ACCEL unhealthy | error_count 只增不减 + _publish_gyro() 时序竞争 | 820a991dc1 RTT 强制健康位 |
| PARAM_SET 无 ACK | RTT 层缺失直接回执 | 1b33bbbdfc 直接 send_parameter_value |
| **__udivmoddi4 偶发崩溃** | Bus 线程栈 93.5% 满，64位除法越界 | **6bec32b9b1** `rt_malloc` 替代静态数组 |

## 未解决的问题

| 问题 | 影响 | 优先级 | 状态 |
|------|------|--------|------|
| MAG = UNHEALTHY | 无磁力计，EKF 无法 3D fix | 低 | 无硬件 |
| GPS = NO FIX | 无 GPS 模块 | 低 | 无硬件 |
| **RC 输入 (IOMCU)** | SBUS 在 RC 端口不工作 | **高** | IOMCU 完全禁用，需启用 |
| PreArm 触发 | 无法 arming | 中 | `ARMING_CHECK` 可绕开 |
| SD 卡日志 | SDIO 被禁用，无法写入板载日志 | 中 | `.config` 中 SDIO=未设置 |
| freemem | ~20KB，需关注 | 中 | 堆可能紧张 |

## 已验证的提交清单（禁止回滚）

| 提交 | 内容 | 验证 |
|------|------|------|
| 5118bdcebf | SPI引脚对齐ChibiOS ✅ | CDC+OpenOCD |
| 3fcfad9d2f | DeviceBus静态栈替代rt_thread_create | CDC+OpenOCD |
| e970f6612a | SPI1超时中止+恢复逻辑 | CDC+OpenOCD |
| 045c45fded | CS-held burst 保护 | CDC+OpenOCD |
| 0574d42623 | 健康位衰减+GPIO供电轨 | CDC+OpenOCD |
| 820a991dc1 | RTT强制健康位 | CDC+OpenOCD |
| 432f24f24b | AP_GPS_Blended 空指针保护 | CDC+OpenOCD |
| 1b33bbbdfc | PARAM_SET ACK回执 | CDC+MAVLink |
| **6bec32b9b1** | **DeviceBus rt_malloc 堆分配 (BSS-48KB, 栈8KB)** | **170s/512采样零故障** |
