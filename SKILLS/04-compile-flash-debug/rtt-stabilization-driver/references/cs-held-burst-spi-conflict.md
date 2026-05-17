# CS-held Burst Read SPI 冲突 — ICM20689 数据全零的根因

## 现象
- BMI055 在 SPI1 工作正常（accel z=-1000）
- ICM20689/ICM20602 在 SPI1 被检测到（backend_count=3, EKF initialised）但数据全零
- MCU 有时在 `spi1_poll_transfer()` 的 RXNE 等待中卡死

## 根因

### 问题链
1. `SPIDevice::set_chip_select(true)` → CS 被拉低（active）
2. `SPIDevice::transfer()` → 无条件调用 `_spi1_gpio_init()`
3. `_spi1_gpio_init()` 末尾: `GPIOF->BSRR = (1U<<2)|(1U<<3)|(1U<<4)` → **所有 CS 拉高！**
4. ICM20689 检测到 CS 释放 → 中止当前 burst 事务
5. 后续字节在 CS=HIGH 状态下发送 → 从机不响应 → MISO 悬空 → 读到全零

### 为什么 BMI055 不受影响
BMI055 的 InvenSense 驱动使用不同的数据读取方式（单次寄存器读），不依赖 CS-held burst。

### 为什么 WHO_AM_I 通过
WHO_AM_I 是单次完整 SPI 事务（cs_take=true, cs_release=true），CS 在函数内部管理，不受 `_spi1_gpio_init()` 的 BSRR 写影响。

## 修复

### 文件: `SPIDevice.cpp`

分别在 `transfer()` 和 `transfer_fullduplex()` 的 register-level polling 路径中：

```cpp
// 修改前
if (_desc.bus == 4) { _spi4_gpio_init(); }
else { _spi1_gpio_init(); }

// 修改后
if (!_cs_held) {
    if (_desc.bus == 4) { _spi4_gpio_init(); }
    else { _spi1_gpio_init(); }
}
```

### 逻辑
- `_cs_held == true`（CS-held burst 中）: 完全跳过 GPIO init
- `_cs_held == false`（新事务）: 正常进行 GPIO init
- 第一次 `set_chip_select(true)` 已经完成 GPIO init，后续 burst 内无需重复

## 验证

修复后 MAVLink 消息:
```
RAW_IMU: accel=(13, -4, -1001)   ← z ≈ -1000 (gravity!)
SCALED_IMU3: accel=(13, -11, -1001)
```

## 相关代码位置

| 函数 | 文件 | 行号 |
|------|------|------|
| `_spi1_gpio_init()` | SPIDevice.cpp | ~24-51 |
| `set_chip_select()` | SPIDevice.cpp | ~482-560 |
| `transfer()` | SPIDevice.cpp | ~359-480 |
| `transfer_fullduplex()` | SPIDevice.cpp | ~530-580 |
| `spi1_poll_transfer()` | SPIDevice.cpp | ~170-263 |
