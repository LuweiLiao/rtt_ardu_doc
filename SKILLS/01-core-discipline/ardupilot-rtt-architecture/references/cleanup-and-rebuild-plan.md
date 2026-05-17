# RTT 移植代码清洁与重建计划 (Phase 0–3)

> 最后更新：2026-05-10 01:20
> 状态：Phase 0 ✅ 编译通过 (ROM 85.87%, RAM 54.94%)
> Phase 1.2 (SDIO) ✅ 编译通过

## Phase 0 — 代码清洁 ✅ 完成

### 背景
在 `staging/pogo-rtt-clean` 分支上执行。从 `staging/pogo-rtt` fork 创建。

### 执行方式（替代原始 revert 方案）
原始方案试图逐个 revert 19 个提交，但因混合提交（clean HAL 修改 + 违规共存）导致难以执行。改用以下方法：

**1. 文件级目标还原** — 对于纯违规文件：`git checkout origin/master -- <file>`
**2. 验证干净修改** — `git diff origin/master HEAD~1 -- '<file>'` 检查 AP_HAL_RTT/ 修改还在
**3. 编译后修复** — 若编译失败（origin/master 太旧），回退 `git checkout HEAD~1 -- <file>` 后手术式去除 RTT ifdef

### 具体操作

#### 纯 origin/master 还原（15 个文件）
```bash
git checkout origin/master -- \
  libraries/AP_Baro/AP_Baro_MS5611.cpp \
  libraries/AP_InertialSensor/AP_InertialSensor.cpp \
  libraries/AP_InertialSensor/AP_InertialSensor_BMI055.cpp \
  libraries/AP_InertialSensor/AP_InertialSensor_BMI088.cpp \
  libraries/AP_InertialSensor/AP_InertialSensor_Invensense.cpp \
  libraries/AP_InertialSensor/AP_InertialSensor_Invensensev3.cpp \
  libraries/AP_Logger/AP_Logger.cpp \
  libraries/AP_Logger/AP_Logger_File.cpp \
  libraries/AP_IOMCU/AP_IOMCU.cpp \
  libraries/AP_IOMCU/AP_IOMCU.h \
  libraries/AP_Scheduler/AP_Scheduler.cpp \
  libraries/GCS_MAVLink/GCS_Param.cpp \
  libraries/GCS_MAVLink/GCS_Common.cpp \
  libraries/GCS_MAVLink/GCS.h \
  libraries/AP_Vehicle/AP_Vehicle.cpp
```

#### 手术式清理（3 个文件）

**AP_Baro/AP_Baro_MS5611.cpp** — 去除 4 处 `rt_kprintf` 调用：
```python
# 使用 Python 脚本精确删除行，避免 patch 工具的 \n 字面量问题
with open('libraries/AP_Vehicle/AP_Vehicle.cpp', 'r') as f:
    lines = f.readlines()
del lines[581:596]  # RTT block (0-indexed)
with open('libraries/AP_Vehicle/AP_Vehicle.cpp', 'w') as f:
    f.writelines(lines)
```

**AP_Baro/AP_Baro.cpp** — 替换 `rt_kprintf` → `DEV_PRINTF`

**AP_Vehicle/AP_Vehicle.cpp** — 去除 `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` ... `#endif` 块（循环率报告）

#### 删除文件（1 个）
```bash
git rm libraries/AP_Filesystem/AP_Filesystem_posix_rtt_compat.c
```

#### 保留的文件（暂存）

**AP_Filesystem/AP_Filesystem.h** — 含有 RTT 专有 `dirent`/`stat` 定义。因 RTT 无 POSIX dirent.h，编译必须。Phase 1 时移入 `AP_HAL_RTT/`。

#### 编译陷阱速查

| 问题 | 根因 | 解法 |
|------|------|------|
| `mavlink_channel_mask_t` 未声明 | origin/master GCS.h 需新版 mavlink | 回退 HEAD~1 + 手术 |
| `AP_Baro_MS56XX` 构造签名 | .cpp Device& vs .h OwnPtr<Device> | 回退 HEAD~1 + 手术 |
| `dirent` 不完整类型 | RTT 无 POSIX dirent.h | 临时保留 RTT dirent 定义 |
| `HAL_STORAGE_SIZE` 重定义警告 | hwdef.h + command-line 重复 | 无害 |
| `#error "Unsupported platform"` | 编译未 include CH_HAL.. | 验证 rtt.h 是否正确 include |

### 提交记录
```
062a16fb8d chore(cleanup): Phase 0 — 架构违规清理，回退所有RTT专有修改
```

---

## Phase 1 — HAL 驱动补齐

### 1.1 RTT dirent/stat 定义移入 AP_HAL_RTT（🟡 中/前置依赖）
- **当前**: `AP_Filesystem/AP_Filesystem.h` 含 `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` dirent 定义 (Phase 0 临时保留)
- **目标**: 移入 `AP_HAL_RTT/include/sys/dirent.h` 或 `AP_HAL_RTT/include/` 下
- **方法**: `AP_Filesystem.h` 中仅保留与 ChibiOS 相同的 `#if CONFIG_HAL_BOARD == HAL_BOARD_CHIBIOS` 逻辑，RTT dirent 定义改为从 HAL 层 include
- **注意**: 需确认 `include/` 目录的编译路径是否已包含

### 1.2 SD 卡管理 ✅ 编译通过（2026-05-10）
- **状态**: ✅ 编译通过, ROM 85.87%, RAM 54.94%
- **当前**: SDIO 基础设施已完整：
  - `BSP_USING_SDIO=y` 已在 `.config`
  - `drv_sdio.c` (HAL_Drivers) 可正确编译
  - `f7/sdio_config.h` 已有 CUAV V5 特定 SDMMC1 + DMA2_Stream3/6 配置
  - `HAL_SD_MspInit()` 在 `CubeMX_Config/stm32f7xx_hal_msp.c` 中已实现 GPIO/时钟/DMA 初始化
  - `rt_board_init.c` 已有 SD 卡供电(PG7) + 自动重试挂载
  - `sdcard_port.c` 已有显式 `rt_hw_sdio_init()` 调用（防 --gc-sections）
- **已知问题**:
  - ⚠️ `sdcard_port.c` 与 `rt_board_init.c` 重复 PG7 供电和挂载 → 需精简
  - ⚠️ `--gc-sections` 陷阱: 必须保持至少一个显式引用点
- **下一步**: 烧录硬件测试，确认 SD 卡检测和挂载
- **详见**: `rtt-stabilization-driver/references/sdio-infrastructure.md`

### 1.3 CAN / DroneCAN (🔴 高)
- 对照: `AP_HAL_ChibiOS/CANFDIface.cpp`, `CANIface.h`
- 实现: `AP_HAL_RTT/CANFDIface.cpp`
- 依赖: STM32F7 bxCAN 外设寄存器级驱动
- 验证: DroneCAN GPS/compass 数据接收

### 1.4 SoftSignalReader (🔴 高)
- 对照: `AP_HAL_ChibiOS/SoftSigReader.cpp/h`
- 实现: `AP_HAL_RTT/SoftSigReader.cpp`
- 依赖: TIM 捕获 + GPIO EXTI
- 验证: RC PPM/PWM 输入解析

### 1.5 RCOutput_iofirmware (🔴 高)
- 对照: `AP_HAL_ChibiOS/RCOutput_iofirmware.cpp`
- 实现: `AP_HAL_RTT/RCOutput_iofirmware.cpp`
- 依赖: IOMCU 协议（已通）

### 1.6 shared_dma (🟡 中)
- 对照: `AP_HAL_ChibiOS/shared_dma.cpp/h`
- 实现: `AP_HAL_RTT/shared_dma.cpp`
- 依赖: STM32F7 DMA 流分配

### 1.7 stdio (🟡 中)
- 对照: `AP_HAL_ChibiOS/stdio.cpp`
- 实现: `AP_HAL_RTT/stdio.cpp`
- 依赖: RT-Thread console 设备

### 1.8 GCS_FTP.cpp 规范
- **当前**: 含 `rtt_use_open_first` 局部变量名（PR 不友好）
- **修复**: 去 RTT ifdef，改为判断 DFS 特性或函数调用封装

---

## Phase 2 — 已有驱动问题修复

### 2.1 UARTDriver — _writebuf 512字节
- 根因 1: 堆碎片化（calloc(16384)失败）
- 根因 2: GCC ARM 编译器分支反转 bug（if/else 中局部变量被交换）
- 修复: _timer_tick中硬编码字面量 `set_size_best(8192)` + 反汇编验证
- 验证: GDB `p _writebuf.get_size()` → 8192, pymavlink 参数枚举 >200 params/s

### 2.2 RCInput — 补齐 SoftSignal 路径
- 对照: ChibiOS RCInput 的软信号/中断/定时器输入
- 修复: 添加 PPM/PWM 输入解析（依赖 SoftSignalReader）

### 2.3 AnalogIn — ADC channel 死锁
- 已知问题: `rt_adc_read()` hang
- 修复: 直接 CMSIS ADC 寄存器访问替代 RT-Thread ADC 框架

### 2.4 Scheduler — yield 策略
- 不再改 `AP_Vehicle.cpp` 的 gate
- 修复: 优化主循环中的 `wait_for_sample()` yield 时机
- 目标: UART 线程获得足够 CPU 时间

---

## Phase 3 — 逐模块验证

### 验证阶梯
```
L0 (启动基础):
  □ OpenOCD halt → CFSR=0, HFSR=0
  □ USB CDC 枚举 /dev/ttyACM1
  □ MAVLink 心跳 → status=STANDBY
  □ 运行 60s 无 HardFault

L1 (传感器):
  □ RAW_IMU 数据稳定（GYRO/ACCEL）
  □ ATTITUDE 消息输出
  □ SYS_STATUS 电压/电流
  □ BARO 数据（MS5611 SPI4）

L2 (功能完整):
  □ RC 输入（PPM/PWM/SBUS）
  □ GPS 数据（DroneCAN/UART）
  □ 参数读写（全部 672 参数 < 3s）
  □ SD 卡日志（写入 + 读取） ← Phase 1.2 后待硬件验证
  □ IOMCU 输出（PWM 通道）
  □ CAN 总线数据

L3 (长期稳定):
  □ 连续运行 24h 无 HardFault
  □ 日志不间断记录
  □ RC 不丢失
  □ USB CDC 不断连
```
