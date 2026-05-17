# SPI Dynamic BR — Register-Level Speed Switching

## Context
RTT's `SPIDevice.cpp` uses CMSIS register-level SPI transfers (bypassing RT-Thread SPI framework) for SPI1 and SPI4 on STM32F7. The CR1 BR field was hardcoded to `/16` (~6.75MHz at 108MHz APB2), preventing low/high speed switching used by IMU drivers.

## Pattern
1. Add `_speed_high` member to `SPIDevice` class (bool, default false)
2. In `set_speed()`: save `_speed_high = (speed == SPEED_HIGH)` for register-level path
3. Pass `bool high_speed` to `spi1_poll_transfer()` 
4. In CR1 init (cs_take path), select BR bits based on `high_speed`:

```cpp
spi->CR1 = SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI |
           SPI_CR1_CPOL | SPI_CR1_CPHA |
           (high_speed ? SPI_CR1_BR_0 : (SPI_CR1_BR_0 | SPI_CR1_BR_1));
```

## BR Values at PCLK2=108MHz
| Mode         | BR Bits             | Divider | Frequency  |
|-------------|---------------------|---------|------------|
| SPEED_LOW   | BR_0 \| BR_1 (BR=3) | /16     | 6.75 MHz   |
| SPEED_HIGH  | BR_0         (BR=1) | /4      | 27.0 MHz   |

## Callers to Update
- `transfer()` register-level path
- `transfer_fullduplex()` register-level path

## Verification
- GDB: read `SPI1->CR1` after `set_speed(SPEED_HIGH)` → BR bits = 0x1
- GDB: read `SPI1->CR1` after `set_speed(SPEED_LOW)` → BR bits = 0x3
