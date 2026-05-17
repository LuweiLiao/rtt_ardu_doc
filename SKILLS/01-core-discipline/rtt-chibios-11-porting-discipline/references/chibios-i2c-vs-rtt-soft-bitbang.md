# ChibiOS I2Cv2 vs RTT Soft Bitbang 对比分析

> 创建：2026-05-16 会话诊断
> 根因：系统启动缓慢（~45s），setup_stage 推进慢，"假装卡死"在 662

## 顶层架构差异

| 维度 | ChibiOS (fmuv5) | RTT (cuav_v5 当前) |
|------|----------------|-------------------|
| **I2C 方案** | 硬件 I2Cv2 外设（`hal_i2c_lld.c`） | 软件 GPIO 位爆炸（`drv_soft_i2c.c`） |
| **中断模型** | 事件中断 + 错误中断（双 IRQ） | 无中断，纯轮询 |
| **DMA 支持** | 可选（`STM32_I2C_USE_DMA`） | 无 |
| **时序产生** | 硬件 SCL（TIMINGR 寄存器） | `stm32_set_scl()` GPIO 延时循环 |
| **错误检测** | NACKF / BUS_ERROR / ARBITRATION_LOST / TIMEOUT / OVERRUN | ❌ 无 |
| **超时保护** | `STM32_I2C_BUSY_TIMEOUT` 阈值 + `MSG_TIMEOUT` 返回 | ❌ 无 — 无限 spin |
| **总线恢复** | 超时后必须 stop+restart（文档强制） | ❌ 无恢复机制 |
| **引脚声明** | `PH7 I2C3_SCL I2C3 AF4` → 硬件外设自动初始化 | 相同声明但 BSP 无 `drv_i2c.c`，回退 bitbang |

## STM32F7 I2Cv2 硬件特性（RM0410 §38）

硬件 I2Cv2 相比软 bitbang 的核心优势：

1. **SCL 硬件时钟** — TIMINGR 寄存器预设频率，无需 GPIO 延时代码
2. **START/STOP 硬件产生** — CR2.START + CR2.STOP 位，硬件产生时序
3. **NACK 自动处理** — 从设备不响应时硬件置位 NACKF，产生中断
4. **BUSY 检测** — ISR.BUSY 只读位，+ 超时阈值避免死等
5. **错误中断** — BUS_ERROR（总线冲突）、ARBITRATION_LOST、TIMEOUT 全部走中断

## RTT 软 bitbang 卡死机制

```c
// drv_soft_i2c.c:stm32_set_scl() / stm32_set_sda()
static void stm32_set_sda(void *data, rt_int32_t state)
{
    struct stm32_soft_i2c_config *cfg = (struct stm32_soft_i2c_config *)data;
    rt_pin_write(cfg->sda, state);           // ← GPIO 写入
    // 如果 SDA 被外部设备拉低（如 IST8310 未正确释放总线），
    // 下一次读取时状态与预期不符，导致上层协议逻辑混乱
}
```

**卡死条件**：IST8310 magnetometer 在 probe 阶段发送 `0x0E` 地址 + READ 位后，IST8310 将 SDA 拉低（ACK 或数据响应），但软件 bitbang 在 STOP 条件生成时未正确处理 SDA 释放时序，导致 `stm32_set_sda()` 或 `dev_i2c_bit_ops.c` 中的 `while` 循环永远等不到预期状态。

## RTT I2CDevice.cpp 的已有硬件 I2C3 驱动

I2CDevice.cpp 中已经实现了 CMSIS 寄存器级的硬件 I2C3 驱动：

```
_i2c3_hw_init():
  RCC->APB1ENR |= RCC_APB1ENR_I2C3EN
  GPIO AF4 配置 (PH7 SCL, PH8 SDA)
  I2C3->TIMINGR = 100kHz 预设
  I2C3->CR1 |= I2C_CR1_PE  // 使能外设

_i2c3_master_xfer():
  CR2.SADD | RD_WRN | NBYTES | START | AUTOEND → 发送
  轮询 TXIS/RXNE → 写 TXDR / 读 RXDR
  等待 TC / STOPF → 清除
```

**但软 bitbang 抢先注册了 "i2c3" 总线**，`rt_i2c_bus_device_find("i2c3")` 返回软 bitbang 的 bus 对象，硬件驱动从未被调用。

## 修复方向

### 方案 A（推荐——跟 ChibiOS 一致）
1. 从 `drv_soft_i2c.c` 的 `soft_i2c_config[]` 中移除 I2C3 (PH7/PH8) 引脚
2. 或者将 `CONFIG_RT_USING_I2C_BITOPS` 改为 `n`
3. 确保 I2CDevice.cpp 的硬件 I2C3 驱动在 `_i2c3_register()` 中能注册成功

### 方案 B（临时工作绕过）
不在 BSP 层面解决，改为在 I2C 总线注册后由 AP_HAL_RTT 代码 deregister 软 bitbang 的 i2c3：
```c
// 在 _i2c3_register() 中先找已注册的 "i2c3"
// 如果找到且是 bitbang 类型 → rt_i2c_bus_device_unregister()
// 再注册硬件版本
```

## 验证测试

烧录后运行 `list_device` 命令确认 i2c3 总线类型。硬件 I2C 驱动下，IST8310 探测不应导致 GPIO 循环卡死。
