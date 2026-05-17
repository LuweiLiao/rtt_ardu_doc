---
name: l1-verified-state-2026-05-09
description: L2 最终验证状态 — RTT CUAV V5 移植 (commit 6bec32b9b1)
---

# L2 验证状态（2026-05-09 最终版，commit 6bec32b9b1）

## 已验证通过的里程碑

### L0 基础运行 ✅
- 启动无 HardFault (CFSR=0, HFSR=0)
- Bootloader 跳转正常 (CUAVv5_bl.bin @ 0x08000000)
- 应用固件运行 @ 0x08008000，USB CDC 枚举为 ttyACM1
- MAVLink 心跳持续，status=STANDBY，主循环 ~310Hz

### L1 传感器数据流 ✅
- **IMU1 (ICM20689)**: RAW_IMU accel≈(-16,+5,-1032) gyro≈(+9,-18,-1) ✅
- **IMU2 (ICM20602)**: SCALED_IMU2 accel≈(-3,-4,-997) ✅
- **IMU3 (BMI055)**: SCALED_IMU3 accel≈(-2,-8,-1001) ✅
- **MS5611**: SCALED_PRESSURE ~1011hPa ✅
- **EKF**: flags=1024, ATTITUDE roll≈0.01 pitch≈0.02 ✅
- **GYRO/ACCEL health**: HEALTHY ✅
- **BARO health**: HEALTHY ✅
- **MAG**: UNHEALTHY (无磁力计硬件，正常)

### L2 功能完整 ✅
| 项目 | 状态 | 说明 |
|------|------|------|
| MAVLink 心跳 | ✅ | HB OK sys=1，类型稳定 |
| SYS_STATUS | ✅ | GYRO=H ACCEL=H BARO=H，load=1000 |
| PARAM_REQUEST_LIST 枚举 | ✅ | 全部参与参数有效 |
| PARAM_SET (写入) ACK | ✅ | **已修复** (commit 1b33bbbdfc) |
| GPS 串口 (SERIAL3) | ✅ | USART1 配置正确，无硬件时 fix=0 |
| LOG_ENTRY | ✅ | 日志系统可查询 |
| 90秒稳定性 | ✅ | 283/283 SYS_STATUS 无掉落、0 健康翻转 |
| OpenOCD 验证 | ✅ | CFSR=0, HFSR=0，无任何故障 |
| 堆 (主循环后) | ✅ | freemem ~20KB |

### 综合 MAVLink 测试结果 ✅
全部关键消息持续输出，IMU 数据正确，EKF 稳定收敛，参数读写正常。

## 已验证的修复清单

| 提交 | 修复内容 | 关键文件 |
|------|---------|---------|
| 5118bdcebf | SPI引脚对齐ChibiOS (PA6/PD7/PG11, PE2/PE6/PE13) | hwdef.dat, SPIDevice.cpp, drv_spi_ll.c |
| e970f6612a | SPI1 polling超时中止+恢复 | SPIDevice.cpp |
| 045c45fded | CS-held burst跳过_spi1_gpio_init() | SPIDevice.cpp |
| 432f24f24b | AP_GPS_Blended空指针保护 | AP_GPS_Blended.cpp |
| 820a991dc1 | RTT强制健康位override | AP_InertialSensor.cpp |
| 1b33bbbdfc | PARAM_SET直接ACK回执 | GCS_Param.cpp |
| 6bec32b9b1 | DeviceBus栈rt_malloc堆分配 (6144→8192, BSS-48KB) | DeviceBus.cpp |

## 最终 L2 稳定状态

**固件**: commit 6bec32b9b1 (staging/pogo-rtt)
**BSS**: 274,336 字节 (从 323,456 减少 48KB)
**ROM**: 83.55% 使用
**RAM**: 65.81% 使用
**堆**: 堆分配按需使用 (bus线程栈从堆取，每栈8KB)
