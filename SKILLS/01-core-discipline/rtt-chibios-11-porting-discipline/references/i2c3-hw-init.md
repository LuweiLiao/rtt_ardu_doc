# I2C3 硬件初始化 + RT-Thread 总线注册

## 架构

I2C3 是 CUAV V5 的内部磁力计总线（IST8310 @ 0x0E）。RT-Thread BSP 没有 `drv_i2c.c`，
因此 `rt_i2c_bus_device_find("i2c3")` 在原始状态下返回 NULL。

**解决方案**：从 `I2CDevice.cpp` 内直接使用 CMSIS 寄存器初始化 I2C3，
并注册到 RT-Thread I2C 框架。

## 代码结构

```
I2CDevice.cpp
├── #include <stm32f7xx.h>
├── I2C3_TIMINGR_100KHZ macro
├── _i2c3_hw_init()            — 时钟/GPIO/TIMINGR/PE
├── _i2c3_master_xfer()        — CMSIS master 收发
├── static ops + bus_dev       — RT-Thread 框架注册
├── _i2c3_register()           — hw_init → register
└── I2DDevice constructor      — bus==0 → _i2c3_register()
```

## 关键寄存器值

### GPIO AF4 配置（PH7 SCL, PH8 SDA）

```c
GPIOH->MODER = (GPIOH->MODER & ~((3U << 14) | (3U << 16))) |
                ((2U << 14) | (2U << 16));          // AF mode
GPIOH->AFR[0] = (GPIOH->AFR[0] & ~(0xFU << 28)) | (4U << 28);   // PH7 AF4
GPIOH->AFR[1] = (GPIOH->AFR[1] & ~(0xFU << 0))  | (4U << 0);    // PH8 AF4
GPIOH->OTYPER |= (1U << 7) | (1U << 8);                          // open-drain
GPIOH->PUPDR   = (GPIOH->PUPDR & ~((3U << 14) | (3U << 16))) |
                 ((1U << 14) | (1U << 16));                       // pull-up
```

### I2C TIMINGR（PCLK1=54MHz, 100kHz）

```
PRESC=3  → tI2CCLK = (3+1) * 18.5ns ≈ 74ns
SCLL=67  → 68 * 74ns ≈ 5.03us  (SCL low)
SCLH=66  → 67 * 74ns ≈ 4.96us  (SCL high)
→ Period ≈ 10us → 100kHz ✓
SDADEL=2, SCLDEL=3  (per RM0410 Table 137-138)
```

```c
#define I2C3_TIMINGR_100KHZ  ((3U << 24) | (3U << 20) | (2U << 16) | (66U << 8) | 67U)
```

## Master 传输状态机（RM0410 §30.4.3）

### 发送（写）流程：

```
1. 等待 BUSY=0
2. 清除 ICR 中所有错误标志
3. CR2 = (addr<<1)<<1 | 0 | NBYTES | START | AUTOEND
4. 循环 (remaining>0):
     a. 等待 TXIS=1
     b. 写 TXDR = *buf++
     c. 检查 NACKF
5. 等待 TC=1（Transfer Complete）
6. 等待 STOPF=1（AUTOEND 自动产生 STOP）
7. 清除 STOPCF
```

### 接收（读）流程：

```
1. 等待 BUSY=0
2. 清除 ICR 中所有错误标志
3. CR2 = (addr<<1)<<1 | RD_WRN | NBYTES | START | AUTOEND
4. 循环 (remaining>0):
     a. 等待 RXNE=1
     b. *buf++ = RXDR
     c. 检查 NACKF
5. 等待 STOPF=1
6. 清除 STOPCF
```

## 陷阱和注意事项

1. **`I2C_ICR_BUSYCF` 不存在** — BUSY 是 ISR 只读位。不能通过 ICR 写入清除。只能等 BUSY 自然释放
2. **CR2 写入即启动传输** — `START` 和 `AUTOEND` 必须与 SADD/NBYTES 一起设。不要先写 CR2 再设 START
3. **`I2C_CR2_RELOAD` 必须为 0** — 写 1 进入 reload 模式（用于 NBYTES>255 分段传输）
4. **双锁** — `I2CDevice::transfer()` 调用 `rt_i2c_bus_lock()`，`rt_i2c_transfer()` 内部又取 `bus->lock`。RT-Thread mutex 支持递归 take（同线程重复取 +1 hold count），但增加开销
5. **SADD 格式** — CR2 的 SADD[7:1] 在 bit[8:1]。`msg->addr` 是 7 位未移位地址。最终 CR2 值为 `((msg->addr << 1) << 1) = msg->addr << 2`
