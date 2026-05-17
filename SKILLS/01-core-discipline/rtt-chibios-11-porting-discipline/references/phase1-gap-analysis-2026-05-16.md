# Phase 1 完成时 ChibiOS 对比差距分析

> 日期：2026-05-16  
> 范围：Phase 1 核心对齐完成后，系统性对比 ChibiOS 全模块  
> 方法：逐模块读 ChibiOS 源码 → 读 RTT 源码 → 标注差距 → 分类影响

## 总体评价

Phase 1（核心 API 对齐：Scheduler、SPIDevice、Semaphores、启动顺序、I2C、ADC）已全部完成。剩余差距均为**非核心功能**——不影响 MAVLink 通信、不影响 IMU 数据、不影响参数存储、不影响 PWM 输出。

## 差距清单

### 1. `Flash::write()` 缺少边界检查

| 项目 | 内容 |
|------|------|
| 文件 | `libraries/AP_HAL_RTT/Flash.cpp:156-222` |
| ChibiOS 参考 | `hwdef/common/flash.c:741-743` |
| 差距 | RTT 不检查写入地址 + 长度是否在 Flash 范围内 |
| 代码 | `if ((addr+count) > STM32_FLASH_BASE+STM32_FLASH_SIZE) return false;` |
| 影响 | ⬇️ 低 — 正常使用不离奇越界 |

### 2. `Flash::write()` 缺少 HSI 时钟检查

| 项目 | 内容 |
|------|------|
| 文件 | `libraries/AP_HAL_RTT/Flash.cpp:156-222` |
| ChibiOS 参考 | `hwdef/common/flash.c:747-749` |
| 差距 | RTT 不检查 HSI 振荡器是否使能 |
| 代码 | `if (!(RCC->CR & RCC_CR_HSION)) return false;` |
| 影响 | ⬇️ 极低 — HSI 在 STM32F7 启动时默认开启，不会被关闭 |

### 3. `UARTDriver::set_options()` 存值不执行

| 项目 | 内容 |
|------|------|
| 文件 | `libraries/AP_HAL_RTT/UARTDriver.cpp:632-636` |
| ChibiOS 参考 | `UARTDriver.cpp:1539-1595` |
| 差距 | ChibiOS 根据 options 标志实际配置 GPIO 模式（pull-down、swap、half-duplex、DMA 开关等）。RTT 仅存 `_last_options` 不做任何硬件配置 |
| 影响的选项 | `HALF_DUPLEX`, `SWAP`, `PULLDOWN_RX/TX`, `RX_INV`, `TX_INV`, `NODMA_RX/TX`, `NOFIFO` |
| 影响 | ⬆️ 中 — 如果外设设置 `SERIALn_OPTIONS` 要求 half_duplex/SWAP 等会静默失效 |

### 4. `UARTDriver::configure_parity()` / `set_stop_bits()` / `set_RTS_pin()` / `set_CTS_pin()` 空函数

| 项目 | 内容 |
|------|------|
| 文件 | `libraries/AP_HAL_RTT/UARTDriver.cpp:649-669` |
| ChibiOS 参考 | ChibiOS 通过 STM32 HAL 实际配置 USARTx->CR1 奇偶校验和 STOP bits |
| 差距 | RTT 所有 set_* 函数为空或存值但无硬件操作 |
| 影响 | ⬇️ 低 — 默认 8N1 覆盖大部分场景。GPS 等外设通常用 8N1 |

### 5. UART `_begin()` 缺少 baudrate 自动最小缓冲区计算

| 项目 | 内容 |
|------|------|
| 文件 | `libraries/AP_HAL_RTT/UARTDriver.cpp:146-282` |
| ChibiOS 参考 | `UARTDriver.cpp:264-276` |
| 差距 | ChibiOS 根据 baudrate 计算 `min_rx_buffer = MAX(min_rx_buffer, b/(40*10))`，确保接收循环在 40Hz 下不丢字节。RTT 固定 512 字节最小 |
| 影响 | ⬇️ 低 — 大容量内存板（512KB SRAM）不缺这几百字节，固定 512 够用 |

## 已对比模块状态（2026-05-16 更新）

| 模块 | 结论 | 备注 |
|------|------|------|
| SPIDevice | ✅ 等价 | transfer/transfer_fullduplex 均有 assert_owner |
| I2CDevice | ✅ 等价 | 含硬件 I2C3 注册 |
| DeviceBus | ✅ 等价 | |
| Semaphores | ✅ 等价 | 已删除自定义 take_blocking |
| HAL_RTT_Class | ✅ 等价 | 5 步启动顺序对齐 ChibiOS |
| UARTDriver | ✅ 核心功能等价 | 差距 #3, #4, #5 |
| AnalogIn | ✅ 等价 | NDTR 安全半缓冲 |
| Scheduler | ✅ 等价 | DWT 忙等 + 三层 boost |
| Storage | ✅ 等价 | 依赖 Flash |
| GPIO | ✅ 等价 | init 列表不含 PA13/PA14 ✅ |
| RCInput | ✅ 等价 | |
| RCOutput | ⚠️ 架构差异 | 直接 TIM 寄存器访问（BSP 无 board.h） |
| Util | ✅ 等价 | |
| system.cpp | ✅ 等价 | |
| Flash | ✅ 功能等价 | 差距 #1, #2 |
