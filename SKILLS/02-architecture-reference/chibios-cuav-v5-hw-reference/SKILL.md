---
name: chibios-cuav-v5-hw-reference
category: embedded
description: ChibiOS CUAV V5 硬件定义参考路径 — RTT 移植时对照验证引脚、外设、传感器配置的唯一可信来源
---

# ChibiOS CUAV V5 硬件参考

RTT ArduPilot 移植过程中，所有硬件引脚和传感器配置必须对照 ChibiOS 参考实现验证。

## 核心参考文件

### CUAV V5 专用配置（增量覆盖）
```
libraries/AP_HAL_ChibiOS/hwdef/CUAVv5/hwdef.dat
```

这个文件通过 `include ../fmuv5/hwdef.dat` 继承了所有基础配置，只添加了 CUAV V5 特有的差异：
- RGB LED 引脚（PH10/PH11/PH12 代替 fmuv5 的 PB1/PC6/PC7）
- 额外的 ICM42688 传感器
- 去除了 PF11（改为 ICM42688_CS）

### 基础参考（最重要！所有硬件引脚的真正定义在这里）
```
libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat
```

**这是 RTT 移植必须严格对齐的参考文件**。CUAV V5 通过 include 机制继承了这个文件中的全部定义。

## 关键引脚对齐表

| 功能 | ChibiOS fmuv5（参考） | RTT cuav_v5（对齐后） |
|------|----------------------|----------------------|
| **SPI1_SCK** | PG11 | PG11 ✅ |
| **SPI1_MISO** | **PA6** | **PA6** ✅（从 PG9 修正） |
| **SPI1_MOSI** | **PD7** | **PD7** ✅（从 PB5 修正） |
| **SPI4_SCK** | **PE2** | **PE2** ✅（从 PE12 修正！PE12 无效） |
| **SPI4_MISO** | PE13 | PE13 ✅ |
| **SPI4_MOSI** | **PE6** | **PE6** ✅（从 PE14 修正，消除 PWM 冲突） |
| **SPI2_SCK** | PI1 | PI1 ✅ |
| **SPI2_MISO** | PI2 | PI2 ✅ |
| **SPI2_MOSI** | PI3 | PI3 ✅ |
| **USART6_TX** | **注释掉**（输入防 IOMCU SBUS 干扰） | **注释掉** ✅ |
| **USART6_RX** | PG9 NODMA | PG9 ✅ |
| **PWM(1)** | PE14 TIM1_CH4 | PE14 TIM1_CH4 ✅（SPI4_MOSI 不再占用） |

## 传感器配置参考

| 传感器 | ChibiOS | RTT |
|--------|---------|-----|
| ICM20689 | SPI1 DEVID1, MODE3, 2/8MHz | 一致 ✅ |
| ICM20602 | SPI1 DEVID2, MODE3, 2/8MHz | 一致 ✅ |
| BMI055_G | SPI1 DEVID3, MODE3, 10MHz | 一致 ✅ |
| BMI055_A | SPI1 DEVID4, MODE3, 10MHz | 一致 ✅ |
| MS5611 | SPI4 DEVID1, MODE3, 20MHz | 一致 ✅ |
| RAMTRON | SPI2 DEVID1, MODE3, 8MHz | 一致 ✅ |

## IMU 探测顺序

ChibiOS fmuv5 的 IMU 探测顺序（CUAV V5 继承）：
```
IMU Invensense SPI:icm20689 ROTATION_NONE      # IMU1 (Primary)
IMU Invensense SPI:icm20602 ROTATION_NONE      # IMU2 (Secondary)
IMU BMI055 SPI:bmi055_a SPI:bmi055_g ROTATION_ROLL_180_YAW_90  # IMU3
IMU BMI088 SPI:bmi055_a SPI:bmi055_g ROTATION_ROLL_180_YAW_90  # Fallback (兼容)
```

## 串口顺序

ChibiOS fmuv5 的 SERIAL_ORDER：
```
SERIAL_ORDER OTG1 USART2 USART3 USART1 UART4 USART6 UART7 OTG2
```
注意：OTG2 没有物理引脚，仅用于 API 兼容性。

## 烧录信息

- **Bootloader 文件**: `Tools/bootloaders/CUAVv5_bl.bin`（16KB，烧录到 0x08000000）
- **固件起始地址**: `0x08008000`（bootloader 后 32KB 偏移）
- **MCU**: STM32F765VI / STM32F767
- **Flash**: 2MB, 晶振 16MHz → SYSCLK 216MHz

## 使用原则

1. **任何引脚定义修改前必须先对照此参考**
2. **STM32F765 数据手册（DS11532）是第二验证源**——某些引脚可能没有 SPI 或 UART 复用功能
3. **ChibiOS 能在 CUAV V5 上跑**，所以它的配置是已验证的 ground truth
4. **RTT hwdef.dat、drv_spi_ll.c、SPIDevice.cpp 三者的引脚定义必须一致**——它们曾经不一致导致大量调试时间浪费
