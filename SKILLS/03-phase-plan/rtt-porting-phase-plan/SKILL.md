---
name: "rtt-porting-phase-plan"
description: "RTT ArduPilot 移植完整分阶段计划 — 廖博士 2026-05-16 确认。从当前状态到可发布稳定版本，分 4 个 Phase，每步标注 ChibiOS 参考。"
triggers:
  - "rtt 下一步"
  - "rtt 计划"
  - "rtt 路线图"
  - "rtt roadmap"
  - "rtt phase"
  - "rtt 移植下一步"
  - "对照 chibios"
  - "chibios 对比"
---

# RTT ArduPilot 移植 Phase 计划

> **确认日期**：2026-05-17 廖博士审阅修正  \n> **当前位置**：**Phase 0B（L0 可通信基线）— 阻塞中 🔴**  \n> **阻塞原因**：堆元数据破坏（`system_heap.used=86224 > total=86208`，metadata corruption，非堆耗尽）  \n> **前序**：Phase 0A（编译/枚举基线）已完成 ✅

---

## ✅ Phase 0A — 编译+烧录 baseline（已完成）\n\n**Entry**: 无（初始状态）\n**Exit**: 编译通过 + USB 枚举 + 三层阻塞修复链确认 + hwdef 基础设施全部到位\n\n| 修复 | 文件 | 状态 |\n|------|------|------|\n| hwdef 生成器（VAL_GPIO/DMA resolver/ldscript） | `rtt_hwdef.py` + `STM32F767xx.py` + `dma_resolver.py` | ✅ 已验证 |\n| app_descriptor 后处理（4层修复：hwdef→linker→ifdef→scons脚本） | `AP_CheckFirmwareDefine.h` + `link.lds` + `hwdef.dat` | ✅ 已验证 |\n| flash 边界检查（_sidata+(_edata-_sdata)修复） | `flash_check.c` | ✅ 已验证 |\n| 编译通过（ROM 87.54%, RAM 78.05%） | 全部文件 | ✅ 已验证 |\n| USB 枚举（1209:5741 CUAVv5 RTT） | `CherryUSB` + `board.h` | ✅ 已验证 |\n| CDC ACM 可见（/dev/ttyACM1） | `USB driver` | ✅ 已验证 |\n| 三层阻塞修复链 | Flash yield + Debug Assert + setup_priority | ✅ 已验证 |\n| DeviceBus 栈 8192→2048 + _thread_started 标志修复 | `DeviceBus.cpp` | ✅ 已验证 |\n| 5-step 启动优先级对齐（降 prio→hal_init→setup→恢复） | `HAL_RTT_Class.cpp` | ✅ 已验证 |\n| DWT boost + 100Hz ADC gate（循环率 263→1387Hz） | `Scheduler.cpp` | ✅ 已验证 |\n| `get_semaphore()` 返回 `&_bus->semaphore` | `SPIDevice.cpp:649` | ✅ 已对齐 |\n| Bus 线程 `take(BLOCK_FOREVER)` | `DeviceBus.cpp:49` | ✅ 已对齐 |\n| UART priority（4→5，低于 timer） | `Scheduler.h` | ✅ 已验证 |\n| Semaphore `take_blocking()` 覆写删除 | `Semaphores.cpp/h` | ✅ 已验证 |\n\n## 🔴 Phase 0B — L0 可通信基线（阻塞中）\n\n**Entry**: Phase 0A exit criteria met\n**Exit**: CDC ACM MAVLink HEARTBEAT + loop_rate >= 100Hz + 基础传感器健康（全 6 项 L0 里程碑）\n\n### P0（最高优先级）：堆元数据破坏定位+修复\n\n**根因**：`system_heap.used(86224) > total(86208)` — 这不是堆耗尽，是**堆元数据被踩**。`used` 超出 `total` 仅 16 字节，不可能来自任何合理分配。\n\n诊断方案：\n- canary 围栏插入（0xDEADBEEF 在 heap 前后）\n- `rt_malloc_sethook`/`rt_free_sethook` 分配轨迹\n- DWT watchpoint 监视被踩字段\n- 二分定位触发路径\n\n详见 checker/ 脚本 + RTT_PORT_STATUS.md\n\n### P1：CDC ACM DTR 正确响应\n- GCCFG 修复验证\n- DTR ringbuffer reset + drain-skip 确认\n- `g_dtr_active`/`dbg_dtr_set_cnt` 诊断\n\n### P2：MAVLink 心跳\n- `rt_device_write` 验证\n- pymavlink 双向验证\n\n### P3：基础传感器健康\n- SPI1 ICM20689/20602 probe through\n- I2C3 IST8310 probe through（硬件 I2C，禁用软 bitbang）\n\n### P4：主循环率 >= 100Hz\n\n---\n\n## 🚧 Phase 1 — 核心传感器+执行器对齐（Phase 0B 完成后启动）\n\n> 目标：与 ChibiOS 在锁模型、优先级、延迟行为上功能等价\n\n### 1.1 L1 — 传感器数据流完整对齐\n- SPI 动态 BR 分频\n- I2C 硬件化（禁用软 bitbang）\n- ADC DMA 改造（替代 CMSIS 轮询）\n- INS 完整数据流验证\n- EKF/ATTITUDE 验证\n\n### 1.2 L2 — RC 闭环（从原 L3 提前）\n> **廖博士审阅调整**：RCIN/RCOUT 不应等到 L3，因为actuator安全边界（arming/failsafe/PWM输出）依赖RC。\n- RCOutput: bdshot, serial, iofirmware\n- RCInput: SoftSignalReader\n- actuator 安全边界验证\n\n### 1.3 L3 — GPS/磁力计完整\n- I2C IST8310 磁力计\n- GPS UART 完整数据流

> 目标：与 ChibiOS 在锁模型、优先级、延迟行为上功能等价

### 1.1 Bus 线程优先级对齐 ✅

| 属性 | ChibiOS | RTT（当前） | RTT（修改后） |
|------|---------|------------|-------------|
| 参考行号 | `SPIDevice.cpp:78` `DeviceBus(APM_SPI_PRIORITY)` | `Scheduler.h:42` `APM_RTT_SPI_PRIORITY=4` | **5**（低于 timer 4, 等于主线程 main prio） |
| 优先级 | APM_SPI_PRIORITY=181 (ChibiOS 高=优先) | `4`（与 timer 同级，不合理） | **5**（2026-05-16 从 4 降为 5） |
| 修改文件 | — | `Scheduler.h` | `APM_RTT_SPI_PRIORITY 4 → 5` |
| 验证结果 | — | — | ✅ 编译通过 + 烧录验证成功 + CDC MAVLink 心跳输出 |

**效果**：Timer(4) > SPI(5) = Main(5) — 匹配 ChibiOS Timer(182) > SPI(181) > Main(180)

**烧录验证注意事项**：此项修改本身不影响启动，但烧录时必须**先擦除 bootloader 扇区**再写入。详见 `rtt-cuav-v5-flash-verify` 技能的 `🔴 致命陷阱：Bootloader 扇区未擦除就写入`。

**效果**：Timer(4) > SPI(5) = Main(5) — 匹配 ChibiOS Timer(182) > SPI(181) > Main(180)

**⚠️ 注意**：修改仅改宏定义值，SPIDevice.cpp 已使用 `APM_RTT_SPI_PRIORITY` 传入 `get_bus()`，无需额外改传参。

### 1.2 Semaphore `take_blocking()` 覆写删除 ✅

| 属性 | ChibiOS | RTT（当前） | RTT（修改后） |
|------|---------|------------|-------------|
| 参考行号 | 基类 `Semaphore.cpp` — 无覆写 | `Semaphores.cpp` 覆写 `take_blocking()`（含 owner-check + hold bump 递归死锁规避） | **删除整个覆写，使用基类默认 `take(HAL_SEMAPHORE_BLOCK_FOREVER)`** |
| 行为 | `chMtxLock` 永久阻塞 | `rt_mutex_take(&_mtx_obj, RT_WAITING_FOREVER)` + 自定义 owner 递归处理 | 基类 `take(HAL_SEMAPHORE_BLOCK_FOREVER)` → `rt_mutex_take(RT_WAITING_FOREVER)` — RTT 的 `rt_mutex_take` 原生支持递归锁定 |
| 验证 | — | — | ✅ 编译通过 + 烧录验证成功 + CDC MAVLink 2 心跳输出 |

**文件**：`libraries/AP_HAL_RTT/Semaphores.h`（删除声明） + `Semaphores.cpp`（删除 ~30 行实现）

**修改说明**：ChibiOS 的 `chMtxLock()` 原生支持递归锁定。RT-Thread 的 `rt_mutex_take()` 也支持（`hold` 字段）。原 RTT 覆写中的 owner-check + hold bump 是重复实现的递归逻辑，删除后由 `rt_mutex_take` 原生处理。基类 `take_blocking()` 定义在 `AP_HAL/Semaphores.h:23`：`virtual void take_blocking() { take(HAL_SEMAPHORE_BLOCK_FOREVER); }`。

### 1.3 `delay()` 中 setup 阶段跳过 `call_delay_cb()`

| 属性 | ChibiOS | RTT（当前） |
|------|---------|------------|
| 参考行号 | `Scheduler.cpp` 中 delay() 纯 `chThdSleep()` | `Scheduler.cpp` 循环 `delay_microseconds(1000)` + 每 tick 调用 `call_delay_cb()` |
| 行为 | 纯睡眠 | setup 期间触发 GCS/Logger 线程 |
| 影响 | 无 | 未初始化资源被访问 |

**文件**：`libraries/AP_HAL_RTT/Scheduler.cpp`

### 1.4 `transfer()` 添加 `check_owner()` 断言

| 属性 | ChibiOS | RTT（当前） |
|------|---------|------------|
| 参考行号 | `SPIDevice.cpp:292` `bus.semaphore.check_owner()` | 缺失 |
| 行为 | 无锁调用 transfer 返回 false | 无保护 |
| 影响 | 检测到的编程错误 | 隐蔽的数据竞争 |

**文件**：`libraries/AP_HAL_RTT/SPIDevice.cpp`

---

## 📋 Phase 2 (P2) — 驱动增强

> 目标：从寄存器轮询升级到 DMA/硬件加速，消除性能瓶颈

### 2.1 ADC DMA 改造

| 属性 | ChibiOS | RTT（当前） |
|------|---------|------------|
| 参考行号 | `AnalogIn.cpp` `_timer_tick` → DMA 读 `adcgrp->samples[]` | `AnalogIn.cpp:78` 寄存器轮询 1000 次/通道 |
| 每 8 通道耗时 | ~8µs（DMA 后台） | ~3-5ms（轮询） |
| 对主循环影响 | <1% | 可达 50%+ |

### 2.2 硬件 I2C3 AF4 替代 GPIO 位爆炸

| 属性 | ChibiOS | RTT（当前） |
|------|---------|------------|
| 参考行号 | `hwdef/fmuv5/hwdef.dat` I2C3 AF4 | `drv_soft_i2c.c` GPIO 位爆炸 |
| 速率 | 400kHz | ~1-10kHz |
| 影响 | 磁力计探测 <1ms | 可达 100ms+ |

### 2.3 SPI 动态 BR 分频

| 属性 | ChibiOS | RTT（当前） |
|------|---------|------------|
| 参考行号 | `SPIDevice.cpp:266-280` `derive_freq_flag()` | `spi1_poll_transfer()` 硬编码 BR |
| 低速 | BR=5 (~1.68MHz) | 固定高速 BR=3 / BR=2 |
| 高速 | BR=3 (~6.75MHz) / BR=2 (~13.5MHz) | 同上固定 |

---

## 📋 Phase 3 (P3) — 架构补齐

> 目标：补全 ChibiOS 中存在但目前 RTT 缺失的 HAL 模块

| 序号 | 模块 | ChibiOS 参考 | 依赖 |
|------|------|-------------|------|
| 3.1 | `SoftSigReader.cpp/h` | `AP_HAL_ChibiOS/SoftSigReader.cpp` | PWM input |
| 3.2 | `RCOutput_iofirmware.cpp` | `AP_HAL_ChibiOS/RCOutput_iofirmware.cpp` | IOMCU |
| 3.3 | `shared_dma.cpp/h` | `AP_HAL_ChibiOS/shared_dma.cpp` | DMA 共享管理 |
| 3.4 | `CANFDIface.cpp` 对齐 | `AP_HAL_ChibiOS/CANFDIface.cpp` | CAN 总线 |
| 3.5 | `DSP.cpp/h` | `AP_HAL_ChibiOS/DSP.cpp` | 数字信号处理 |

---

## 📋 Phase 4 — 性能优化

| 序号 | 优化 | ChibiOS 参考 | 预期收益 |
|------|------|-------------|---------|
| 4.1 | SPI DMA 传输 | `SPIDevice.cpp` `do_transfer()` DMA | ~4% 主循环 |
| 4.2 | Bus 线程栈 2048→1024 | `Device.cpp:30` = 1024 | 节省 8KB heap |
| 4.3 | SD 卡 DMA | `sdcard.cpp` | 日志写入不阻塞 |
| 4.4 | 定时器线程 1kHz→500Hz | 可配 | 减少上下文切换 |

---

## 当前 Phase 0B 验证基线（2026-05-17 实测）\n\n| 指标 | 当前值 | 目标 | 状态 |\n|------|--------|------|------|\n| **L0 里程碑达成** | **2/6**（编译✅ 枚举✅ CDC❌ MAVLink❌ 传感器❌ 循环率❌） | 6/6 | 🔴 |\n| setup_stage | 502（卡在 rt_serial_open:676 assert） | ≥651 | 🔴 |\n| 堆状态 | used(86224) > total(86208) — **元数据被踩** | used <= total | 🔴 |\n| 启动时间 | 未达（assert 前已卡住） | <10s | ❌ |\n| CDC 枚举 | /dev/ttyACM1 ❌ 无数据 | 1Hz HEARTBEAT | ❌ |\n| CFSR/HFSR | 0（无 HardFault） | 0 | ✅ |

---

## 🚨 已知陷阱与阻断条件

### 🔴 重复 app_descriptor 陷阱（2026-05-16 发现 — 经深入分析后确认为**红鲱鱼**）

**现象（误判）**：Bootloader + app 均烧录验证通过（verify OK），但 CPU 启动后 HardFault。同时发现二进制中有两份 app_descriptor 签名，猜测 bootloader 扫到零字段的第二份而拒绝跳转。

**实际根因**（2026-05-16 17:39 最终确认）：不是重复 descriptor 导致！**bootloader 扇区未擦除就写入，残留旧数据导致 bootloader 自身 HardFault**。修复方法见 `rtt-cuav-v5-flash-verify` 技能的「致命陷阱：Bootloader 扇区未擦除就写入」。

**技术分析**：CUAV V5 bootloader（基于 ArduPilot `AP_Bootloader`）使用 `memmem()` 从 `0x08008000`（app 起始地址）扫描 app_descriptor 签名，`memmem` 返回**第一个匹配**。RTT 二进制中第一份签名在 0x080081f8（CRC 正确已补丁），第二份在 0x0800821c（全零）。所以 bootloader 会找到第一份正确签名，不会因为第二份拒绝跳转。

**保留此陷阱作为诊断参考（2026-05-16 验证结论）**：
- 二进制中确实有两份 app_descriptor（RTT 链接器产生）
- 但 bootloader 扫描到的是第一份（CRC 正确） → 跳转正常
- 烧录后系统不启动时先排查 bootloader 扇区擦除问题，而非此陷阱

### 🔴 Flash 写入速度导致的算法超时

OpenOCD `adapter speed 200` 下 `flash write_image` 会报 `timeout waiting for algorithm` 错误。需要在烧写时提升速度：

```bash
openocd ... \
  -c "adapter speed 200" \    # halt/reset 用低速
  -c "halt" \
  -c "adapter speed 1800" \   # flash 写入用高速（自动协商到 1800kHz）
  -c "flash write_image ..." \
  -c "adapter speed 200" \    # reset 恢复低速
  -c "reset run"
```

1. **每步先读 ChibiOS 参考** — 找到精确行号再动手
2. **每次只改 1-3 个文件** — 编译通过后再改下一批
3. **每修改完一批 → 编译 → 烧录 → 验证 setup_stage + HEARTBEAT + RAW_IMU**
4. **不得修改 `libraries/` 中 `AP_HAL_RTT/` 外的通用代码**（除非通用 bug）
5. **不得引入 `#ifdef HAL_RTT`**
6. **所有改动必须在 plan 中标注 ChibiOS 参考行号**
