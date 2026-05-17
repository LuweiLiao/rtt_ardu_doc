# I2C3 CMSIS Driver + RT-Thread Framework Registration

## Problem
RT-Thread BSP for STM32F7 compiles the I2C core (`dev_i2c_core.o`, `dev_i2c_dev.o`) but **no board-level `drv_i2c.c` exists** — no I2C bus device is registered. `rt_i2c_bus_device_find("i2c3")` returns NULL.

The config shows `CONFIG_RT_USING_I2C=y` but `# CONFIG_RT_USING_SOFT_I2C is not set`, and the STM32 HAL I2C driver is compiled (`stm32f7xx_hal_i2c.c`) but not wired to the RT-Thread framework.

## Solution Pattern
Write a CMSIS register-level driver that:
1. Initializes I2C3 hardware (clocks, GPIO AF4, timing)
2. Implements `master_xfer` callback (RT-Thread ops contract)
3. Registers via `rt_i2c_bus_device_register()`

### Files Modified
Add code to `libraries/AP_HAL_RTT/I2CDevice.cpp` (no new build artifacts needed).

### Hardware Init (I2C3 on STM32F767)
```
Pins:   PH7 = SCL (AF4), PH8 = SDA (AF4)
Clock:  RCC_APB1ENR_I2C3EN, RCC_AHB1ENR_GPIOHEN
Reset:  RCC_APB1RSTR_I2C3RST
Timing: PCLK1=54MHz → TIMINGR for 100kHz Standard Mode
```

### TIMINGR Calculation (PCLK1=54MHz, 100kHz)
```
PRESC=3  → tI2CCLK = 4 × 18.5ns ≈ 74ns
SCLL=67  → 68 × 74ns ≈ 5.03us
SCLH=66  → 67 × 74ns ≈ 4.96us
SDADEL=2, SCLDEL=3

I2C3_TIMINGR = (3<<24) | (3<<20) | (2<<16) | (66<<8) | 67
```

### Master Transfer Flow (RM0410 §30.4.3)
1. Wait BUSY=0
2. Clear all sticky error flags in ICR (`NACKCF | STOPCF | BERRCF | ARLOCF | OVRCF`)
3. Write CR2: SADD[7:1], RD_WRN, NBYTES, START, AUTOEND
4. For each byte:
   - TX: wait TXIS → write TXDR
   - RX: wait RXNE → read RXDR
5. Check NACKF each iteration
6. Wait STOPF → clear STOPCF

### Registration
```cpp
struct rt_i2c_bus_device _i2c3_bus_dev = { .ops = &_i2c3_ops };
rt_i2c_bus_device_register(&_i2c3_bus_dev, "i2c3");
```

Call from `I2CDevice` constructor when bus==0.

### Known Issues
- STM32F7 I2C peripheral is timing-sensitive; timing values may need tuning for different PCLK1 frequencies
- `I2C_ICR_BUSYCF` does NOT exist — BUSY is read-only in ISR. Only cleared by hardware after STOP condition completes
- Double-lock: `rt_i2c_bus_lock()` + `rt_i2c_transfer()` internal lock both take `bus->lock` mutex. RT-Thread mutex IS recursive (`hold` counter), so it's safe but wasteful

### Verification
- GDB: check `I2C3->CR1 & I2C_CR1_PE` → 1 (enabled)
- GDB: check `I2C3->TIMINGR` → matches programmed value
- After `rt_i2c_bus_device_find("i2c3")` → non-NULL
- Logic analyzer: SCL ≈ 100kHz square wave on PH7
