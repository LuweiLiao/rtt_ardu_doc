# RTT 多Agent闭环管道 — 完整任务规格 (P0-P8)

> 创建日期: 2026-05-16
> 管道: 8 个功能块 × 2 (implement + review) = 16 张 kanban 卡
> Profile: orchestrator/developer/reviewer, config.yaml 已配

## P0: hwdef/补齐 ✅ (已完成, Review 进行中)

**开发者**: Orchestrator 自己完成
**需改文件**:
- `libraries/AP_HAL_RTT/hwdef/scripts/rtt_hwdef.py` — VAL_GPIO + dma_resolver + ldscript + env
- `libraries/AP_HAL_RTT/hwdef/scripts/STM32F767xx.py` — 新 MCU 定义
- `libraries/AP_HAL_RTT/hwdef/scripts/dma_resolver.py` — 移植版约束求解器
- `libraries/AP_HAL_RTT/hwdef/scripts/dma_parse.py` — DMA 表解析器

**验证**: 运行 `cd hwdef && python3 scripts/rtt_hwdef.py -D /tmp/out cuav_v5/hwdef.dat`

## P1: UART 独立 TX 线程

**需改文件**:
- `libraries/AP_HAL_RTT/UARTDriver.cpp` — TX 线程 + unbuffered_writes + DMA bounce
- `libraries/AP_HAL_RTT/UARTDriver.h` — 新成员变量

**ChibiOS 参考**: `libraries/AP_HAL_ChibiOS/UARTDriver.cpp` (1836L)

**关键实现**:
1. 创建独立 RT-Thread TX 线程，ringbuffer 接收
2. unbuffered_writes(): 绕过缓冲区直接 DMA
3. DMA bounce buffer: 当 buffer 不在 DMA-safe 区域时中转

## P2: Flow Control + set_options

**需改文件**:
- `libraries/AP_HAL_RTT/UARTDriver.cpp`

**ChibiOS 参考**: UARTDriver.cpp `set_options()`

**关键实现**:
1. RTS/CTS 硬件流控引脚 GPIO 配置
2. set_options() 支持: DATA_BITS/STOP_BITS/PARITY/FLOW_CONTROL

**依赖**: gated on P1-review (需要 UART TX 线程改造完成)

## P3: Flash 边界 + HSI + UART parity

**需改文件**:
- `libraries/AP_HAL_RTT/Flash.cpp` — 边界检查 + HSI
- `libraries/AP_HAL_RTT/UARTDriver.cpp` — parity/stop_bits

**关键实现**:
1. Flash::write() 检查地址是否在 bootloader 保护区
2. HSI 振荡器使能/校准检测
3. UART parity/stop_bits 参数生效而非仅存值

## P4: PWM 组配置文件映射

**需改文件**:
- `libraries/AP_HAL_RTT/RCOutput.cpp` — 使用 HAL_RTT_PWM_MAP
- `libraries/AP_HAL_RTT/hwdef/scripts/rtt_hwdef.py` — _write_pwm_map() 已生成

**参考**: rtt_hwdef.py 已生成 `HAL_RTT_PWM_MAP { {"pwm1", 1}, ... }`

## P5: HAL_Storage 闪存驱动

**需改文件**:
- `libraries/AP_HAL_RTT/Storage.cpp`
- `libraries/AP_HAL_RTT/Storage.h`

**ChibiOS 参考**: `AP_HAL_ChibiOS/Storage.cpp` (504L)

**关键实现**:
1. read_block()/write_block()
2. 使用 drv_flash_blkdev.c 或 drv_flash_ll.c 作为后端
3. EEPROM 模拟（flash 扇区 + 磨损均衡）

## P6: HAL_GPIO 封装层 + HAL_Util

**需改文件**:
- `libraries/AP_HAL_RTT/GPIO.cpp` — 完整封装
- `libraries/AP_HAL_RTT/Util.cpp` — 补齐

**关键实现**:
1. GPIO: init()/pinMode()/read()/write()/toggle()/attach_interrupt()
2. Util: trap()/dump_stack_trace() 调试支持

## P7: 多板型支持 + 构建系统

**需改文件**:
- 新建 `hwdef/pixhawk6c_mini/` (已有 hwdef.dat)
- `hwdef/rtt_fmu_mini/` 配置完善
- `hwdef/common/SConstruct` — 多目标支持

## P8: HIL 测试流水线

**需改文件**:
- 新建 `Tools/ardupilot_rtt/test/` 测试目录
- OpenOCD 自动化脚本

**工具链**: pymavlink + OpenOCD + Python
