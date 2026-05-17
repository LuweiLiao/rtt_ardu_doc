# AP_HAL 模块对比矩阵：ChibiOS / ESP32 / RTT

> 整理日期：2026-05-09
> 来源：实战对比三个 HAL 层的 `ls` 目录结构和功能分析

## 文件级对比

| 驱动模块 | ChibiOS | ESP32 | RTT (当前) | 优先级 |
|---------|---------|-------|-----------|--------|
| **AnalogIn** | ✅ | ✅ | ✅ | 已有 |
| **CAN / DroneCAN** | ✅ `CANFDIface`, `CANIface` | ❌ | ❌ | 🔴 高 — DroneCAN 外设依赖 |
| **Device / DeviceBus** | ✅ `Device.cpp/h` | ✅ `DeviceBus` | ✅ `DeviceBus` | 功能等价，名称不同 |
| **DSP** | ✅ `DSP.cpp/h` | ❌ | ❌ | 🟢 低 — 空实现可编译 |
| **Flash** | ✅ | — | ✅ | 已有 |
| **GPIO** | ✅ | ✅ | ✅ | 已有 |
| **I2CDevice** | ✅ | ✅ (含 i2c_sw) | ✅ | 已有（无软件I2C） |
| **I2CDeviceManager** | — | — | ✅ | RTT独有 |
| **LogStructure** | ✅ | — | ❌ | 🟡 中 — 编译可能失败 |
| **OSD** | — | ✅ | ❌ | 🟢 低 |
| **RCInput** | ✅ | ✅ | ✅ (需补齐) | 有基础实现 |
| **RCOutput** | ✅ | ✅ | ✅ (需补齐) | 有基础实现 |
| **RCOutput_bdshot** | ✅ | — | ❌ | 🟢 低 — ESC遥测 |
| **RCOutput_iofirmware** | ✅ | — | ❌ | 🔴 高 — IOMCU输出依赖 |
| **RCOutput_serial** | ✅ | — | ❌ | 🟡 中 — 串口协议ESC |
| **SD 卡管理** | ✅ `sdcard.cpp/h` | ✅ `SdCard` | ❌ | 🔴 高 — 日志记录依赖 |
| **Scheduler** | ✅ | ✅ | ✅ | 已有（需优化yield策略） |
| **Semaphores** | ✅ | ✅ | ✅ | 已有 |
| **shared_dma** | ✅ `shared_dma.cpp/h` | — | ❌ | 🟡 中 — DMA共享管理 |
| **SoftSignalReader** | ✅ `SoftSigReader*` | ✅ `SoftSigReader*` | ❌ | 🔴 高 — RC PPM/PWM 输入 |
| **SPIDevice** | ✅ | ✅ | ✅ | 已有 |
| **SPIDeviceManager** | — | — | ✅ | RTT独有 |
| **stdio** | ✅ `stdio.cpp` | — | ❌ | 🟡 中 — console I/O |
| **Storage** | ✅ | ✅ | ✅ | 已有 |
| **UARTDriver** | ✅ | ✅ | ✅ | 已有（需修复USB CDC） |
| **Util** | ✅ | ✅ | ✅ | 已有 |
| **WiFi** | — | ✅ | ❌ | 🟢 低 |

## 缺失模块优先级

### 🔴 高优先级（阻塞核心功能）
1. **CAN/DroneCAN** — 支持 DroneCAN 外设（GPS、compass、rangefinder）
2. **SD 卡管理** — 日志记录（`SD_Logging` 编译时可能强依赖）
3. **SoftSignalReader** — RC PPM/PWM 输入解析
4. **RCOutput_iofirmware** — IOMCU 输出路径

### 🟡 中优先级
5. **RCOutput_serial** — 串口 ESC 协议
6. **shared_dma** — DMA 通道冲突避免
7. **LogStructure** — 编译链接可能失败
8. **stdio** — console I/O 标准化

### 🟢 低优先级（可空实现）
9. **DSP** — 空函数即可
10. **WiFi** — 飞控不需要
11. **OSD** — CUAV V5 无 OSD 芯片
12. **RCOutput_bdshot** — 高级 ESC 功能

## 已有模块健康度

| 模块 | 状态 | 已知问题 |
|------|------|---------|
| **UARTDriver** | ⚠️ 有bug | `_writebuf` 512字节（堆碎片 + GCC 编译器分支反转 bug），QGC参数~16 params/s |
| **SPIDevice** | ✅ 可用 | SPI1/SPI4引脚已修复，超时恢复逻辑、CS-held burst已修复 |
| **Scheduler** | ⚠️ 需优化 | UART优先级已修至6，但主线程yield策略待定（不再改AP_Vehicle gate） |
| **GPIO** | ✅ 可用 | 供电轨初始化已配置（commit 0574d42623中干净部分） |
| **I2CDevice** | ✅ 基础可用 | 需验证外接compass（当前MAG=U） |
| **Storage** | ✅ 可用 | FRAM SPI2已在ChibiOS引脚对齐后验证 |
| **AnalogIn** | ⚠️ 待优化 | ADC本身工作，但RT-Thread ADC channel死锁已知（rtt-stm32-adc-channel-deadlock skill） |
| **RCInput** | ⚠️ 半成品 | 只有基础UART RC模式，无SoftSignal解析 |
| **RCOutput** | ⚠️ 半成品 | 基础PWM输出，无bdshot、无IOMCU输出支持 |
| **DeviceBus** | ✅ 可用 | 已改用rt_malloc堆分配 + 8KB栈 |
| **Flash** | ✅ 可用 | STM32F7内部Flash写入/擦除 |
| **Semaphores** | ✅ 可用 | 静态mutex init，无死锁 |
| **Util** | ✅ 可用 | 基础功能（tick、時間、复位） |
| **CAN** | ❌ 缺失 | 需要从头实现 |
| **SD卡** | ❌ 缺失 | 需要从头实现 |
| **DSP** | ❌ 缺失 | 需检查编译是否强依赖 |
