# Pogo CAN 硬件家族参考

Pogo 系列 CAN 外设基于 AP_Periph (ArduPilot Peripheral) 固件，使用 DroneCAN (UAVCAN) 协议通信。所有模块共享 STM32L431 基础平台，可通过 CAN 总线与 CUAV V5 等飞控直联。

## 模块清单

| 模块名 | 功能 | MCU | 关键特性 |
|--------|------|-----|---------|
| **Pogo-CANPWMxServo** | 1路 PWM 输出 | STM32L431 | PA2(TIM2_CH3)，单舵机控制 |
| **Pogo-CANPWMx8** | 8路 PWM 输出 | STM32L431 | 多通道舵机/电调控制，有bdshot变体 |
| **Pogo-CANESC** | 电调 | STM32L431 | DroneCAN ESC |
| **Pogo-CANFCU-Mini** | 迷你飞控 | STM32L431/H7 | 有bdshot/SimOnHardWare变体 |
| **Pogo-CANGPS** | GPS | STM32L431 | 有Cube/AIROPTIX变体 |
| **Pogo-CANMAG** | 磁力计 | STM32L431 | 有nano变体 |
| **Pogo-CANBARO** | 气压计 | STM32L431 | 独立气压计节点 |
| **Pogo-CANUART** | UART 桥接 | STM32L431 | 有um982/periph变体 |
| **Pogo-CANPWR** | 电源管理 | STM32L431 | 4in1/Base/Single 三种变体 |
| **Pogo-CANRGB** | LED 控制 | STM32L431 | 有MRFX变体 |
| **Pogo-CANWIFI** | WiFi 桥接 | STM32L431 | ESP32 + CAN |
| **Pogo-CANF10/F9P** | 高精定位 | — | RTK 定位模块 |

## Pogo-CANPWMxServo 详细配置

**hwdef:** `libraries/AP_HAL_ChibiOS/hwdef/Pogo-CANPWMxServo/`
- hwdef.dat (固件): MCU=STM32L431, FLASH_SIZE=256KB, APJ_BOARD_ID=1062
- hwdef-bl.dat (bootloader): 36KB 引导区 + 4KB 参数存储
- CAN: PA11(RX)/PA12(TX)
- PWM: **1路** PA2(TIM2_CH3) — 只能控制 1 个舵机
- 协议: DroneCAN (非原始CAN帧)，节点名 `"pogo.canpwmxservo"`
- 连接方式: CAN_H/CAN_L 并联到 CUAV V5 的 CAN1/CAN2 接口

**典型应用拓扑：**
```
CUAV V5 (飞控) ←CAN总线→ Pogo-CANPWMxServo → PWM → 单舵机
                      ←CAN总线→ Pogo-CANPWMx8 → PWM ×8 → 多舵机/电调
                      ←CAN总线→ Pogo-CANGPS → GPS数据
```

## 控制方式

### 方式A: 通过飞控 (ArduPilot)
1. CUAV V5 通过 `AP_DroneCAN` 库与 CAN 模块通信
2. 在 Mission Planner / QGC 中配置 SERVO*_FUNCTION 参数映射到 DroneCAN
3. 飞控自动发送 DroneCAN ESC/Servo 命令 → 模块输出 PWM

### 方式B: PC 直接控制
需要 USB-CAN 适配器 (如 USB2CAN、ZLG USBCAN、或 Pogo-CANUART 桥接):
```python
# 需要 pydronecan 或 libuavcan 库
# 发送 DroneCAN RawCommand 消息
# 模块响应并输出对应 PWM
```

## 固件编译

```bash
cd /data/firmare/pogo-apm
# 编译 Pogo-CANPWMxServo 固件
scons --v=AP_Periph --target=Pogo-CANPWMxServo -j$(nproc)
# 编译 Pogo-CANPWMx8 固件
scons --v=AP_Periph --target=Pogo-CANPWMx8 -j$(nproc)
```

烧录通过 SWD 或通过飞控的 CAN bootloader 进行。
