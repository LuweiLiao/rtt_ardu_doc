# 慢推进 vs 真卡死诊断（2026-05-16 新增）

> **核心理念**：setup_stage 停在一个值上 ≠ 系统真卡死了。RTT ArduPilot 在 CUAV V5 上启动较慢（~40s），需区分卡死和慢推进。
> **适用场景**：任何遇到 `setup_stage=502/620/662/680` 且无心跳的场景。

## 两步诊断法

```bash
# Step 1: 首次 halt + 读 stage
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p/x *(uint32_t*)0x2001f35c" \
  -ex "bt 3" \
  -ex "monitor resume" -ex "quit"

# Step 2: 等 10-15s 再读
sleep 15
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p/x *(uint32_t*)0x2001f35c" \
  -ex "bt 3" \
  -ex "monitor resume" -ex "quit"
```

## 解读矩阵

| 两次读到的 stage | PC 变化 | 诊断 |
|:---:|:---:|------|
| **相同**（如 502→502） | 同一行 | ✅ **真卡死** → 按 setup_hang 标准诊断流程 |
| **不同**（如 502→620→680） | 各线程切换 | ✅ **慢推进** → 系统在前进，只是慢 |
| 相同但 PC 在 `_delay_microseconds_dwt` | 每次相同 | ⚠️ **高优线程饿死** → DWT 忙等被更高优先级线程持续抢占 |

## 已知慢推进瓶颈（CUAV V5）

| 阶段 | 耗时 | 瓶颈 | 根因 |
|------|------|------|------|
| 502→503 | ~10-15s | Flash 256KB sector erase | `Flash::erasepage()` 的 `_wait_bsy()` 和 `while(FLASH->SR & BSY)` 循环 + `rt_thread_yield()` |
| 630→660 | ~10-15s | Compass/GPS init | I2C 软 bitbang `stm32_set_scl()` 被外部设备拉低 SDA，位爆炸缓慢 |
| 662→665 | ~5s | IOMCU UART 超时 | UART8 无 IOMCU 回应，`read_registers` 反复超时重试 |
| 666→680 | ~5-10s | init 中间步骤 | Notch filter init、sample rate 计算 |
| 680→681 | ~10-15s | init_gyro 采集样本 | 等 ACCEL/GYRO 样本累积；SPI 总线频率可能影响采集速率 |

## 2026-05-16 实证记录

```bash
# CUAV V5, STM32F767, 新烧录固件（app_descriptor 已打补丁）
# 从 power-on 开始计时

T+0s:  烧录完成 + reset run
T+15s: setup_stage=0x276=630   (GPS init)     PC: _delay_microseconds_dwt 通过 AP_Logger
T+30s: setup_stage=0x2A8=680   (init_gyro 前)  PC: Invensense _accumulate… 
T+45s: setup_stage=0x28B=651   (startup完成)   PC: Invensense _accumulate…
T+50s: CDC HEARTBEAT ACTIVE ✓  RAW_IMU 数据流 ✓
```

**总启动时间 ~45s**。主要瓶颈：I2C 软 bitbang → 等待硬件 I2C 化（Phase 2 计划）。

## 对比：为什么之前在 ChibiOS 上快？

| 对比项 | ChibiOS (STM32F767) | RTT (当前) |
|--------|-------------------|------------|
| I2C | 硬件 I2C3 AF4（400kHz） | 软 bitbang（~1-10kHz） |
| Flash erase | HAL 层处理 | 寄存器直接操作 + `rt_thread_yield()` |
| IOMCU | 硬件 UART8 + 完整固件 | UART8 硬件正常但 IOMCU 固件上传可能延迟 |
| SPI speed | 动态 BR（低速探测+高速读取） | 当前已实现动态 BR |
| 总启动时间 | ~5-10s | ~40-50s |
