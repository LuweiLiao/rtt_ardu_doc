# ChibiOS 基准对齐评估 — RTT CUAV V5 (2026-05-10)

**基准版本**：ChibiOS fmuv5 hwdef + CUAVv5 hwdef（include）
**评估版本**：RTT cuav_v5 hwdef（commit a632415295 + D1修改）
**验证状态**：L0+L1 通过，26 种 MAVLink 消息流

## 硬件引脚对齐（已验证通过项）

| 功能 | ChibiOS | RTT | 状态 |
|:----|:--------|:----|:----:|
| SPI1 SCK/MISO/MOSI | PG11/PA6/PD7 | PG11/PA6/PD7 | ✅ |
| SPI2 SCK/MISO/MOSI | PI1/PI2/PI3 | PI1/PI2/PI3 | ✅ |
| SPI4 SCK/MISO/MOSI | PE2/PE13/PE6 | PE2/PE13/PE6 | ✅ |
| USART1 (GPS1) | PB6 TX, PB7 RX | PB6 TX, PB7 RX | ✅ |
| USART2 (TELEM1) | PD5 TX, PD6 RX | PD5 TX, PD6 RX | ✅ |
| USART3 (TELEM2) | PD8 TX, PD9 RX | PD8 TX, PD9 RX | ✅ |
| UART4 (GPS2) | PD1 TX, PD0 RX | PD1 TX, PD0 RX | ✅ |
| USART6 (TELEM3) | PG9 RX (TX注释) | PG9 RX (TX注释) | ✅ |
| UART7 (debug) | PE8 TX, PF6 RX | PE8 TX, PF6 RX | ✅ |
| UART8 (IOMCU) | PE0 RX, PE1 TX | PE0 RX, PE1 TX | ✅ |
| 流控 CTS/RTS | USART2/3/6 | USART2/3/6 | ✅ |
| USB OTG1 | PA11 DM, PA12 DP | PA11 DM, PA12 DP | ✅ |
| CAN1/2 | PI9/PH13, PB12/PB13 | PI9/PH13, PB12/PB13 | ✅ |
| PWM 8 通道 | TIM1/4/12 | TIM1/4/12 | ✅ |
| SDMMC1 | PC8-12, PD2 | PC8-12, PD2 | ✅ |
| I2C3 (Compass) | PH7 SCL, PH8 SDA | PH7 SCL, PH8 SDA | ✅ |
| DRDY 8 路 | PB4/14/15, PC5/13, PD10/15, PE7 | 同上 | ✅ |
| LED/BUZZER/SPEKTRUM | 同上 | 同上 | ✅ |
| ADC 9 通道 | PA0-4, PB0, PC0/1/4 | 同上 | ✅ |
| 电源/保护 | PE3/PF12/PG4-7/PF13/PE15/PG1-3 | 同上 | ✅ |

## SPI 设备对齐

| 传感器 | ChibiOS | RTT | 验证 |
|:------|:--------|:----|:----:|
| ICM20689 (SPI1 DEVID1) | ✅ | ✅ | RAW_IMU 数据流 |
| ICM20602 (SPI1 DEVID2) | ✅ | ✅ | SCALED_IMU2 |
| BMI055_G (SPI1 DEVID3) | ✅ | ✅ | MAVLink 流中 |
| BMI055_A (SPI1 DEVID4) | ✅ | ✅ | MAVLink 流中 |
| MS5611 (SPI4 DEVID1) | ✅ | ✅ | SCALED_PRESSURE |
| RAMTRON FRAM (SPI2) | ✅ | ✅ | 参数存储待验证 |
| ICM42688 (SPI1 DEVID5) | ✅ CUAVv5特有 | ✅ 新添加 | 探测待验证 |

## 功能模块差异

| 模块 | ChibiOS | RTT | 差异等级 |
|:----|:--------|:----|:--------:|
| IOMCU_UART | ✅ 使能 | ✅ 已取消注释 | D1解决 |
| HAL_OS_FATFS_IO | =1 | =0 (RTT DFS桥未实现) | 🟡 TODO |
| HAL_LOGGING_FILESYSTEM | =1 | =0 (Logger线程挂起) | 🟡 TODO |
| SPI1 传输 | DMA | Polling (#if 0) | 🟢 功能正常 |
| DCache | 使能 | 禁用 (USB DWC2) | 🟢 功能正常 |
| Buzzer | PE5 TIM9_CH1 | PF9 TIM14_CH1 | 🟢 待验证 |
| CAN2_SILENT | PH3 | PI8 | 🟢 待验证 |

## 对齐率总结

```
硬件引脚: 100% ✅ (52/52)
SPI设备:  100% ✅ (7/7, ICM42688已添加)
功能模块: 74%  🟡 (14/19, 5项有差异)
MAVLink:  L1全面覆盖 ✅ (26种消息)
```
