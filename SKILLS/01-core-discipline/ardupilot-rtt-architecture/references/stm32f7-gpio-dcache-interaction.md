# STM32F7 D-Cache 与 GPIO 寄存器写入冲突

## 发现背景

2026-05-12 CUAV V5 RTT bring-up 调试中，ICM20689 IMU 始终不回 WHO_AM_I (返回 0xFF)，排除 SPI 引脚配置、时钟等后，发现 IMU 传感器供电引脚 PE3 (VDD_3V3_SENSORS_EN) 处于 AF 模式而非 Output 模式。

## 根因

### 硬件层面

STM32F7 SCB_CCR.DC (bit 17) = 1 → D-Cache 默认启用。外设地址空间 (0x40000000-0x5FFFFFFF) 的 MPU 默认属性被配置为 **Normal Memory, Non-shareable, Write-Through, Read-Allocate, No Write-Allocate**（RT-Thread startup 中 `SCB_EnableDCache()` 的默认 MPU 配置）。

在此配置下，D-Cache 介入读-改-写路径：
1. 读 `GPIOE->MODER` → D-Cache 未命中 → AXI 总线读取外设 → 写入 D-Cache ✅
2. 别的代码路径写到 `GPIOE->MODER` → D-Cache 写直达 → 外设已更新 ✅
3. 后续读-改-写 → **使用 D-Cache 中的陈旧值** → 覆盖步骤 2 的写入 ❌

### 软件层面

`rt_pin_mode()` 内部通过 `rt_pin_ops` 回调执行 GPIO 模式的 RMW。中间层代码（如 `drv_gpio.c`）使用 `__IO` 限定的 MODER 指针，但读取到的值可能来自 D-Cache 而非实际外设。

`HAL_GPIO_Init()`（ST 标准库）使用相同的 `__IO` 限定的 MODER 指针，读取逻辑类似。

### 触发场景

CUAV V5 上 SPI4 (MS5611 气压计) 使用 GPIOE 引脚 (PE12=SCK, PE13=MISO, PE14=MOSI)。SPI4 初始化时，`HAL_SPI_MspInit()` 调用 `HAL_GPIO_Init(GPIOE, ...)` 做 RMW 设置这些引脚为 AF 模式。此 RMW 读取到的 MODER 可能来自 D-Cache，覆盖了此前 `_sensor_power_init()` 对 PE3 的 output 配置。

## 诊断流程

### 1. 确认 PE3 状态

```bash
# GPIOE MODER (0x40021000)
echo "mdw 0x40021000" | nc -q 2 localhost 4444
# PE3 bits 7:6 = (value >> 6) & 3
# 01 = output (期望) | 10 = AF (被覆盖) | 00 = input (默认)

# GPIOE ODR (0x40021014)
echo "mdw 0x40021014" | nc -q 2 localhost 4444
# PE3 bit 3 = (value >> 3) & 1
```

### 2. 确认 D-Cache 状态

```bash
# SCB_CCR (0xE000ED14)
echo "mdw 0xE000ED14" | nc -q 2 localhost 4444
# Bit 16 = IC (I-Cache enable)
# Bit 17 = DC (D-Cache enable)
```

CUAV V5 上的典型值：`0x00060200` (DC=1, IC=0, UNALIGN_TRAP=1, STKALIGN=1)

### 3. 隔离测试 — 手动强制设 PE3

通过 OpenOCD 直接写 GPIO 寄存器验证是否是 cache 问题：

```bash
echo "halt" | nc -q 2 localhost 4444
# PE3 output mode: clear 7:6, set 01
echo "mww 0x40021000 0x0802214a" | nc -q 1 localhost 4444
# PE3 HIGH
echo "mww 0x40021014 0x0000ffff" | nc -q 1 localhost 4444
echo "resume" | nc -q 1 localhost 4444
```

### 4. SPI WHO_AM_I 验证

强制供电后检查 IMU 通信：

```bash
# 拉低 CS (PF2)
echo "mww 0x40021418 0x0004" | nc -q 1 localhost 4444
# 发送 WHO_AM_I 0x75 | 0x80 = 0xF5
echo "mww 0x4001300c 0x000000F5" | nc -q 1 localhost 4444
sleep 0.2
echo "mdw 0x4001300c" | nc -q 1 localhost 4444  # 丢弃第一个字节
# 发送 dummy byte
echo "mww 0x4001300c 0x00000000" | nc -q 1 localhost 4444
sleep 0.2
echo "mdw 0x4001300c" | nc -q 1 localhost 4444  # 读取 WHO_AM_I 响应
# 预期: 0x98 (ICM20689)
# 拉高 CS
echo "mww 0x40021418 0x00040000" | nc -q 1 localhost 4444
```

## 修复方案

### 方案 A（已采纳）：直接 volatile 寄存器写 + DSB

替换 `rt_pin_mode()/rt_pin_write()` 为直接寄存器操作：

```c
#define _GPIO_REG(port, offset) \
    (*(volatile uint32_t *)(0x40020000UL + (port) * 0x400UL + (offset)))

_GPIO_REG(4, 0x00) = (_GPIO_REG(4, 0x00) & ~(3UL << 6)) | (1UL << 6);  // PE3 output
__DSB();
_GPIO_REG(4, 0x14) |= (1UL << 3);  // PE3 HIGH
__DSB();
```

`volatile` 关键字强制每次访问穿过到 AXI 总线上，`__DSB()` 等待写入完成后再继续后续代码。

### 方案 B（备选）：运行时周期性地重设 PE3

在 `GPIO::init()`（INIT_APP_EXPORT level，远晚于 SPI 初始化）中再次设 PE3 = output HIGH。但此方案不能解决 SPI device init 阶段传感器已掉电的问题。

### 方案 C（最后手段）：禁用 D-Cache

在 `rt_hw_board_init()` 中注释掉 `SCB_EnableDCache()`。但 D-Cache 是 F7 性能的关键特性，禁用后 USB CDC 吞吐量可能受影响（虽然 F7 的 USB 使用 D-Cache 不友好是已知问题）。

## 对照 ChibiOS

ChibiOS 的 STM32F7 移植**同样禁用 D-Cache**（通过 `HAL_USE_STM32_DMACACHE` 配置），原因与 RTT 相同：USB DWC2 DMA 不兼容 D-Cache。但 ChibiOS 在 HAL 初始化中 `HAL_Init()` 调用 `__HAL_RCC_GPIOE_CLK_ENABLE()` **之后**立即初始化 power 引脚 GPIO，而此时外设尚未被 SPI init 触碰，因此不存在 RTT 这种"先 SPI init 后 power init"的时序倒挂。

## 通用教训

| 教训 | 说明 |
|------|------|
| D-Cache 开启时，`__IO` 不能保证外设寄存器一致性 | `__IO` = `volatile` 阻止编译器优化，但不阻止 D-Cache 介入。需要 DSB 确保 AXI 总线完成 |
| GPIO MODER 的 RMW 在多代码路径竞争时脆弱 | 无论使用 HAL 库还是 RTOS API，RM-W 都不安全——第三方路径可能在 W 和 R 之间修改了其他位 |
| 外设 power init 必须在共享 GPIO 端口的其他初始化**之前**执行 | 否则 RMW 会丢失 power pin 的配置 |

## 相关文件

- CUAV V5 传感器供电引脚定义：`hwdef.h:121` → `HAL_GPIO_VDD_3V3_SENSORS_EN_PIN GET_PIN(E, 3)`
- 供电初始化代码：`libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c:313-330`（直接寄存器写版本）
- SPI4 初始化（破坏 PE3）：CubeMX HAL `stm32f7xx_hal_msp.c` 中的 `HAL_SPI_MspInit()`
- SCB_CCR 定义：`core_cm7.h` → `SCB->CCR`
