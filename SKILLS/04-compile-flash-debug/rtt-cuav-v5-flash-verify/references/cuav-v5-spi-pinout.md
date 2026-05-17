# CUAV V5 SPI 引脚总表与 SPI4 调试记录

## CUAV V5 SPI 引脚映射

| SPI 总线 | 信号 | GPIO | 外设 | AF |
|----------|------|------|------|----|
| **SPI1** | SCK | PG11 | ICM20602, ICM20689, BMI055_GYRO | AF5 |
| SPI1 | MISO | PA6 | — | AF5 |
| SPI1 | MOSI | PD7 | — | AF5 |
| SPI1 | CS(ICM20689) | PF2 | IMU1 | GPIO OUT |
| SPI1 | CS(ICM20602) | PF3 | IMU2 | GPIO OUT |
| SPI1 | CS(BMI055_G) | PF4 | IMU3 (gyro) | GPIO OUT |
| SPI1 | CS(BMI055_A) | PG10 | IMU3 (accel) | GPIO OUT |
| **SPI2** | SCK | — | (未用) | — |
| SPI2 | CS(BMI055_A?) | PF5 | — | — |
| **SPI4** | **SCK** | **PE2** | **MS5611** 气压计 (ChibiOS fmuv5 参考) | **AF5** |
| **SPI4** | **MISO** | **PE13** | MS5611 MISO | **AF5** |
| **SPI4** | **MOSI** | **PE6** | MS5611 MOSI | **AF5** |
| SPI4 | CS(MS5611) | PF10 | BARO_CS | GPIO OUT |

## SPI4 MOSI 引脚错误诊断过程

### 症状
- `SCALED_PRESSURE: abs=0.00hPa temp=-142.6°C` — ADC 读全零
- SYS_STATUS 中 PRESSURE "present" 但 "unhealthy"
- 气压计 backend 已注册（PROM 读了？但后续 ADC 全零）

### 排查路线

**第 1 层** — 总线是否存在:
- `CONFIG_BSP_USING_SPI4=y` ✅ — SPI4 在 .config 中启用
- SPI4 DMA 宏被注释 → `s_spi4_lld` 不编译 → SPI4 总线未注册到 RTT 框架
- → `rt_device_find("spi41")` 返回 nullptr → SPIDevice 有了但 `_dev==nullptr`

**第 2 层** — 引脚配置:
- CubeMX MSP init (`stm32f7xx_hal_msp.c:533`) 用 PE6=MOSI("SPI4: PE2=SCK(AF5), PE13=MISO(AF5), PE6=MOSI(AF5)")
- `drv_spi_ll.c:30` 的 `spi4_ll_cfg.mosi_pin_no = 6`（PE6）— 与 CubeMX 一致，但都错！
- **CUAV V5 原理图**: PE14 = SPI4_MOSI（PE6 未连接任何 SPI 外设）
- ChibiOS CUAVv5 hwdef 验证: `libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat` → `PE14 SPI4_MOSI SPI4 AF5`

**第 3 层** — 从机选择:
- PF10 = MS5611_CS 已在 `hwdef.dat` 中定义
- `rt_board_init.c` 的 `_spi_attach_table` 包含 `{"spi4", "spi41", GET_PIN(F, 10)}`
- SPIDevice.cpp CS 查找表: `{"spi41", 90}`（90 = PFx, x=10 → PF10 ✅）

### 修复方案

见 `embedding/rtt-cuav-v5-flash-verify` SKILL.md 的 "SPI4 不工作" 章节。

**建议**: 用寄存器级轮询（方案A），不用 DMA（方案B），因 DMA 有 IRQ 不触发问题。

## drv_spi_ll.c SPI4 LL 配置源码参考

```c
// modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/drivers_ll/drv_spi_ll.c:24
const spi_ll_config_t spi4_ll_cfg = {
    .Instance       = SPI4,
    .sck_port_idx   = 4,  /* PE2 */
    .sck_pin_no     = 2,
    .miso_port_idx  = 4,  /* PE13 */
    .miso_pin_no    = 13,
    .mosi_port_idx  = 4,  /* PE14 (⚠️ 曾误写为 PE6) */
    .mosi_pin_no    = 14, /* ← 2026-05-08 修正 PE6→PE14 */
    .af             = 5,
    .mode           = 3,  /* CPOL=1, CPHA=1 */
    .prescaler      = 3,  /* /16 → APB2 108MHz/16 = 6.75MHz */
};
```

## SPIDevice.cpp 寄存器级 CS 引脚表

```cpp
// libraries/AP_HAL_RTT/SPIDevice.cpp:71
static const struct spi_cs_entry _spi_cs_table[] = {
    {"spi11", 82},   // PF2  = ICM20689_CS
    {"spi12", 83},   // PF3  = ICM20602_CS
    {"spi13", 84},   // PF4  = BMI055_GYRO_CS
    {"spi14", 106},  // PG10 = BMI055_ACCEL_CS
    {"spi21", 85},   // PF5  = 备用
    {"spi41", 90},   // PF10 = MS5611_CS
};
```

计算: `90 = port(5) * 16 + pin(10) = PF10`（端口 F=5, 引脚 10）

## SPI4 DMA 问题记录

| 问题 | 状态 |
|------|------|
| DMA completion IRQ 不触发 | 未修复 — 改用寄存器级轮询 |
| NVIC 不使能（board init 中注释为避免 HardFault） | 设计如此 — 待 stm32_spi_init 使能 |
| SPI1/SPI4 DMA 流冲突 | 已通过 board.h 重映射 SPI1 到 Stream2/3 解决 |
