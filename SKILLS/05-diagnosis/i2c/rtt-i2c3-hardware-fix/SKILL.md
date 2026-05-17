---
name: "rtt-i2c3-hardware-fix"
description: "RTT CUAV V5 I2C3 硬件化修复 — 禁用软 bitbang，启用 I2CDevice.cpp 中已有的 CMSIS 硬件 I2C3 驱动"
---

# RTT I2C3 硬件化修复

## 根因

ChibiOS fmuv5 使用**硬件 I2Cv2 外设**（中断驱动 + 超时检测 + 错误恢复）。
RTT cuav_v5 BSP 只有 `drv_soft_i2c.c`（无 `drv_i2c.c`），但 I2CDevice.cpp 中
**已有 CMSIS 寄存器级硬件 I2C3 驱动**（`_i2c3_hw_init()` + `_i2c3_master_xfer()`）。

问题：`BSP_USING_I2C3` 在 `rtconfig.h` 中定义，导致软 bitbang 在 board init 时
抢先注册了 "i2c3" 总线。I2CDevice.cpp 的 `rt_i2c_bus_device_find("i2c3")` 返回
软 bitbang 设备，硬件驱动从未被调用。

## 修复方案

**涉及文件**（仅 1 个）：
- `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/rtconfig.h`

**修改内容**：注释掉 `#define BSP_USING_I2C3`

**效果**：
1. `drv_soft_i2c.c` 不再编译 I2C3 的 bitbang 条目
2. I2CDevice.cpp 首次调用 `rt_i2c_bus_device_find("i2c3")` 返回 NULL
3. 触发 `_i2c3_register()` → `_i2c3_hw_init()`（CMSIS 寄存器）→ `rt_i2c_bus_device_register()`
4. 硬件 I2C3 正式接管

**风险**：Zero — I2C1/2/4 软 bitbang 不受影响，I2C3 硬件驱动已在 I2CDevice.cpp 中独立实现

## ChibiOS 参考

| 维度 | ChibiOS (AP_HAL_ChibiOS) | RTT 修复后 |
|------|--------------------------|-----------|
| I2C 驱动 | `hal_i2c_lld.c` I2Cv2 (STM32F7) | `I2CDevice.cpp` CMSIS 寄存器 |
| 引脚 AF | `PH7 I2C3_SCL I2C3 AF4` | `_i2c3_hw_init()` 设 GPIOH AF4 |
| 时序 | TIMINGR 寄存器 | `I2C3_TIMINGR_100KHZ` |
| 错误处理 | 中断: NACK/BUS_ERROR/TIMEOUT | 轮询 `ISR_NACKF` |
| 总线恢复 | stop+restart | `CR1` PE toggle |
