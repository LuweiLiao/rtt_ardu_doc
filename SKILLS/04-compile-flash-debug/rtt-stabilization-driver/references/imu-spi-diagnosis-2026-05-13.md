# IMU SPI 诊断记录（2026-05-13）

## 背景

L0 基线（faee486d1c + startup_rtt_override.S 启动修复）通过后，MAVLink 显示：
- RAW_IMU 全零（xacc/yacc/zacc/xgyro/ygyro/zgyro=0）
- SCALED_PRESSURE press_abs=1001.99hPa（MS5611 正常工作）
- SYS_STATUS 未显示 GYRO/ACCEL healthy

## 诊断过程

### Step 1: SPI1 引脚寄存器检查

使用 OpenOCD 读 SPI1 相关寄存器：

```
# 正确寄存器地址
GPIOA AFR[0] @ 0x40020020: 0x05000000 → PA6 bits[27:24]=0101(AF5) ✅
GPIOD AFR[0] @ 0x40020C20: 0x57707C88 → PD7 bits[31:28]=0101(AF5) ✅
GPIOG AFR[1] @ 0x40021824: 0x00005000 → PG11 bits[15:12]=0101(AF5) ✅
SPI1 CR1 @ 0x40013000: 0x0000035F → BR=/16, SPE=1, CPHA/CPOL=1 ✅
GPIOE MODER @ 0x40021000: 0x0802216A → PE3[7:6]=01(output) ✅
GPIOE ODR @ 0x40021014: 0x0000FFFF → PE3=HIGH ✅
CFSR/HFSR: 0/0 ✅
```

### Step 2: 陷阱 — AFR[0] vs AFR[1]

**大坑**：PA5/PA6/PA7 的 AF 在 GPIOx AFR[0]（偏移 0x20），但初次诊断读了 AFR[1]（偏移 0x24），错误得出 PA6=AF10 的结论。

| 正确 | 错误 |
|------|------|
| `mdw 0x40020020 1` (AFR[0]) → PA6=AF5 ✅ | `mdw 0x40020024 1` (AFR[1]) → PA6=AF10 ❌ |

### Step 3: SPI4 (MS5611) 验证

尽管 SPIDevice.cpp 使用轮询 SPI（不经过 LL driver），MS5611 仍能读取到有效的压力数据。证实：
- SPI4 引脚（PE12/PE13/PE14）在基线中已正确配置
- 从 branch 中 cherry-pick SPI4 引脚修正（drv_spi_ll.c）后编译正常，但 MS5611 在修正前已工作

### Step 4: IMU 数据全零原因分析

SPI1 引脚配置正确、传感器有供电（PE3=HIGH），但 RAW_IMU 全零。可能原因：

1. **CS-held burst 读取协议问题** — ICM20689 使用多字节 burst 读取（保持 CS LOW，先发寄存器地址再读数据）。SPIDevice.cpp 的 CS 保持逻辑可能时序不对
2. **RXNE 超时** — 首次传输后 RXNE 未置位，后续传输失败
3. **IMU 寄存器配置错误** — ICM20689 可能处于 sleep 模式或未正确配置数据速率
4. **Backend driver 标定跳过** — 标定完成后所有数据被标记为 0（需要检查 `_calibrating_gyro` 标志）

## 下一步修复方向

按优先级：

| # | 修复 | 提交（branch） | 预期效果 |
|---|------|---------------|---------|
| 1 | SPI1 RXNE 超时修复 | e970f6612a | 防止传输永久挂死 |
| 2 | CS-held burst 跳过 _spi1_gpio_init | 045c45fded | 修复 multi-byte read 时序 |
| 3 | IMU 健康位修复 | 0574d42623 | 标记传感器 healthy |
| 4 | SPI1 引脚对齐（已在基线中正确） | 5118bdcebf | 仅验证参考 |

## 参考命令

```bash
# 验证 SPI1 引脚完整配置
echo -e "halt
mdw 0x40020020 1    # GPIOA AFR[0]
mdw 0x40020C20 1    # GPIOD AFR[0]
mdw 0x40021824 1    # GPIOG AFR[1]
mdw 0x40013000 1    # SPI1 CR1
mdw 0x40013004 1    # SPI1 CR2
mdw 0x40021000 1    # GPIOE MODER
mdw 0x40021014 1    # GPIOE ODR
resume" | timeout 10 nc localhost 4444 2>&1 | strings | grep "^0x"

# 验证 MAVLink IMU 数据
timeout 30 python3 << 'EOF'
from pymavlink import mavutil
m = mavutil.mavlink_connection('/dev/ttyACM1', baud=115200, timeout=10)
while True:
    msg = m.recv_match(blocking=True, timeout=5)
    if msg:
        t = msg.get_type()
        if t == 'RAW_IMU':
            print(f'xacc={msg.xacc} yacc={msg.yacc} zacc={msg.zacc} | xgyro={msg.xgyro} ygyro={msg.ygyro} zgyro={msg.zgyro}')
        elif t == 'SYS_STATUS':
            h = msg.onboard_control_sensors_health
            for name, bit in [('GYRO',0), ('ACCEL',1), ('BARO',2), ('AHRS',10)]:
                print(f'  {name}: {"OK" if h&(1<<bit) else "BAD"}')
EOF
```

## 相关文件

- `libraries/AP_HAL_RTT/SPIDevice.cpp` — SPI1 轮询传输函数
- `libraries/AP_HAL_RTT/hwdef/common/board/drivers_ll/drv_spi_ll.c` — LL 驱动的 SPI 引脚配置
- `libraries/AP_InertialSensor/AP_InertialSensor_Invensense.cpp` — ICM20689 后端驱动
