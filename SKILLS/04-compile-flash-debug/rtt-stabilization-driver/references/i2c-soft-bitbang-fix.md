# I2C Soft Bitbang Hang — Root Cause & Fix

## 根因

RTT CUAV V5 BSP 只有 `drv_soft_i2c.c`（软 bitbang），没有 `drv_i2c.c`（硬件 I2C）。
I2C3 用于 IST8310 磁力计探测。当 SDA 被外部设备拉低时，bitbang 的 `while` 循环
永远无法退出，导致 CPU 卡死在 GPIO 位操作中。

**ChibiOS 做法**：使用硬件 I2Cv2 外设（`hal_i2c_lld.c`），中断驱动 + 超时检测 + 错误恢复。

## 修复

**目标**：禁用软 bitbang 的 I2C3，让 `I2CDevice.cpp` 中已有的 CMSIS 硬件 I2C3 驱动接管。

**修改**（仅 1 行）：

```
文件: modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/rtconfig.h:177
- #define BSP_USING_I2C3
+ // #define BSP_USING_I2C3   // disabled: hardware I2C3 in I2CDevice.cpp replaces soft bitbang
```

**效果**：
1. `drv_soft_i2c.c` 不再编译 I2C3 的 bitbang 条目（line 15 条件 `BSP_USING_I2C1||...||BSP_USING_I2C3||...` 失败，整个文件跳过编译）
2. `I2CDevice.cpp` 首次 `rt_i2c_bus_device_find("i2c3")` 返回 NULL
3. 触发 `_i2c3_register()` → `_i2c3_hw_init()`（CMSIS 寄存器级 GPIO PH7/PH8 AF4 + I2C3 TIMINGR + PE）
4. 硬件 I2C3 正式接管

**已在 I2CDevice.cpp 中的硬件驱动**：
- `_i2c3_hw_init()`：GPIOH MODER AF4、时钟 RCC、I2C3 TIMINGR 100kHz
- `_i2c3_master_xfer()`：标准 I2C 主模式收发（CR2 START+AUTOEND、轮询 TXIS/RXNE/TC/STOPF）

## ChibiOS 对比

| 维度 | ChibiOS | RTT 修复前 | RTT 修复后 |
|------|---------|-----------|-----------|
| 驱动层 | I2Cv2 LLD (中断+DMA) | drv_soft_i2c.c (GPIO bitbang) | I2CDevice.cpp (CMSIS寄存器) |
| 时序 | 硬件I2C外设自动产生SCL | GPIO toggling | 硬件I2C外设自动产生SCL |
| 错误 | NACK/BUSY/TIMEOUT中断 | 无超时保护 → 死循环 | 轮询ISR_NACKF |
| 总线恢复 | stop+restart | 无 | CR1 PE toggle |
| 卡死风险 | 无 | SDA被拉低→永久卡死 | 无（硬件控制SCL/SDA时序） |

## 关联陷阱

**Mass erase 会擦掉参数存储区**：`STORAGE_FLASH_PAGE 10` 对应最后 256KB 扇区。
Mass erase（`stm32f7x mass_erase 0`）会清空这些扇区。下次启动时 `AP_FlashStorage::init()`
读空Flash → 触发 `erase_all()` → 数据全空 → `load_parameters()` 中 `g2_conversions` 
在未初始化内存上访问 → **HardFault**（PRECISERR, BFAR=0x4000）。

**修复**：不要 mass erase。只 `flash write_image` app 区域即可。
