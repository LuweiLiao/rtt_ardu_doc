# RTT Port Status — 单一真相面板 (Single Source of Truth)

> **Last Updated:** 2026-05-17 17:08 CST  
> **Target:** CUAVv5 (STM32F765) → ArduPilot RTT Port  
> **Bootloader:** Internal ROM  
> **Board:** CUAVv5 (`APJ_BOARD_ID=103`)

---

## L0 Milestone Tracker

| Phase | Status | Milestone | Evidence | Next Step |
|-------|--------|-----------|----------|-----------|
| **Phase 0A** | ✅ **DONE** | 编译/烧录/USB 枚举基线 (Build/Flash/USB Enum baseline) | `ROM 87.54% RAM 78.05%`, USB `1209:5741` CUAVv5 RTT 枚举成功 | 进入 Phase 0B |
| **Phase 0B** | ❌ **BLOCKED** | L0 可通信基线 (L0 communicable baseline) | CDC ACM 设备 `/dev/ttyACM1` 可见，但 **无 MAVLink 心跳** | 修复内存损坏后调试 CDC TX 路径 |

### L0 Completion Criteria (全部通过才可标记 L0 ✅)
| # | Item | Status | Detail |
|---|------|--------|--------|
| 1 | ✅ Compile | ✅ Pass | ROM 87.54% / RAM 78.05% — 编译无错误 |
| 2 | ✅ Enumeration | ✅ Pass | USB `1209:5741` 枚举成功 |
| 3 | ❌ CDC ACM | ❌ Fail | 设备节点 `/dev/ttyACM1` 存在，但无数据收发 |
| 4 | ❌ MAVLink Heartbeat | ❌ Fail | 无 `HEARTBEAT` 消息输出 |
| 5 | ❌ Sensors | ❌ Fail | 传感器驱动未验证（依赖 Phase 0B 完成） |
| 6 | ❌ Loop Rate | ❌ Fail | 调度循环率未验证（依赖 Phase 0B 完成） |

---

## P0–P7 优先级区块状态

### P0 — 构建系统与工具链 (Build System & Toolchain)
**Status: ✅** | 负责人: — | 优先级: **Critical**

| Status | Milestone | Evidence | Next Step |
|--------|-----------|----------|-----------|
| ✅ | 交叉编译工具链配置 | `arm-none-eabi-gcc` 编译通过 | — |
| ✅ | CMake 构建系统 | `./waf configure --board=CUAVv5` 成功 | — |
| ✅ | hwdef 定义 | `hwdef/CUAVv5/hwdef.dat` 包含 RTT 外设定义 | — |
| ⏳ | ROM/RAM 使用率持续监控 | `ROM 87.54% RAM 78.05%` | 控制 ROM 余量 < 85% |

### P1 — 内存布局与链接脚本 (Memory Layout & Linker Script)
**Status: ✅** | 负责人: — | 优先级: **Critical**

| Status | Milestone | Evidence | Next Step |
|--------|-----------|----------|-----------|
| ✅ | 链接脚本定义 | `_end=0x2006aee0`, `_ebss=0x20069ed4`, `_system_stack_size=0x4000` | — |
| ✅ | 堆起始地址正确 | `HEAP_BEGIN=max(0x2006aee0, 0x20020000) = 0x2006aee0` | — |
| ✅ | 堆结束地址正确 | `HEAP_END=STM32_SRAM_END=0x20080000` | — |
| ✅ | **堆元数据修复** | 根因：RTT `rt_thread_create` 用 `RT_KERNEL_MALLOC` 分配线程栈（ChibiOS 用静态 BSS 栈）。线程栈共消耗 ~59KB，堆仅 86KB。**修复：** 缩减 ap_timer(16K→4K), ap_io(8K→4K), storage(8K→4K), ap_uart(8K→4K)，节省 24KB。GDB 验证 `used < total` ✅ | 保持监控 |
| ⚠️ | system_heap 地址 | `system_heap.address=0x2006af20` | 确认 heap begin 对齐正确 |

> **✅ FIXED**: 堆耗尽根因为 RTT 线程栈从堆分配 vs ChibiOS 静态栈。经 GDB 断点跟踪定位 `ap_timer=16384` 等线程栈过大。缩减后堆健康，`used < total` ✅

### P2 — USB / CDC ACM 枚举 (USB / CDC ACM Enumeration)
**Status: ⚠️** | 负责人: — | 优先级: **High**

| Status | Milestone | Evidence | Next Step |
|--------|-----------|----------|-----------|
| ✅ | USB 设备枚举 | `lsusb: 1209:5741 CUAVv5 RTT` | — |
| ✅ | USB 描述符正确 | VID/PID 匹配 CUAVv5 | — |
| ⚠️ | CDC ACM 接口激活 | `/dev/ttyACM1` 设备节点存在 | 确认 `ttyACM1` 可读可写 |
| ❌ | CDC ACM 数据收发 | ⚡ 串口无数据输出 | 修复内存损坏后验证 TX 中断 / DMA |
| ❌ | USB 栈稳定性 | 枚举成功后 USB 栈是否稳定运行？ | 增加 USB 栈健康检查日志 |

### P3 — MAVLink 通信协议 (MAVLink Communication Protocol)
**Status: ❌** | 负责人: — | 优先级: **High**

| Status | Milestone | Evidence | Next Step |
|--------|-----------|----------|-----------|
| ❌ | MAVLink 初始化 | 无 `HEARTBEAT` 输出 | 修复 CDC TX 后回测 |
| ❌ | `GCS_MAVLink` 通道启动 | 依赖 CDC ACM 工作 | 在 GCS 层增加 RTT 专用 channel |
| ❌ | 心跳包周期性发送 | `mavlink_msg_heartbeat_pack` 从未发出 | 确认 `GCS.cpp` 中 serial 端口初始化 |
| ❌ | mavproxy / QGC 连接 | `/dev/ttyACM1` 无响应 | 修复 P1/P2 后验证 |

### P4 — 传感器驱动 (Sensor Drivers)
**Status: ❌** | 负责人: — | 优先级: **Medium**

| Status | Milestone | Evidence | Next Step |
|--------|-----------|----------|-----------|
| ❌ | IMU (BMI088 / ICM-20689) | 未验证 | 依赖 Phase 0B 基础通信完成 |
| ❌ | 磁力计 (IST8310 / MMC5983) | 未验证 | 依赖 Phase 0B 基础通信完成 |
| ❌ | 气压计 (MS5611 / BMP280) | 未验证 | 依赖 Phase 0B 基础通信完成 |
| ❌ | I2C/SPI 总线初始化 | 未验证 | 在 `AP_Periph` 层确认总线时钟 |

### P5 — 调度与循环率 (Scheduler & Loop Rate)
**Status: ❌** | 负责人: — | 优先级: **Medium**

| Status | Milestone | Evidence | Next Step |
|--------|-----------|----------|-----------|
| ❌ | `AP_Scheduler` 主循环运行 | 无法确认（无 MAVLink 心跳） | 修复 CDC 后可插入 `@SYS/param` 统计 |
| ❌ | 循环率达标 (400Hz main / 1kHz fast) | 未测量 | 使用 `perf_counter` 或 GPIO toggle |
| ❌ | `AP_Timer` / `Scheduler_Task` 运行 | 未验证 | 检查 `scheduler_tasks.cpp` 任务列表 |

### P6 — GPIO / 外设 / 板级支持 (GPIO / Peripherals / Board Support)
**Status: ⏳** | 负责人: — | 优先级: **Low**

| Status | Milestone | Evidence | Next Step |
|--------|-----------|----------|-----------|
| ⏳ | 板载 LED (RGB/Status) | 未验证 | 确认 `hal.gpio` 及 `hal.led` 映射 |
| ⏳ | 安全开关 / 蜂鸣器 | 未验证 | 验证 `Buzzer` + `SafetySwitch` |
| ⏳ | SDCard / 参数存储 | 未验证 | 确认 SPI SDCard 初始化 |
| ⏳ | UART 辅助串口 | 未验证 | 确认 `UART_A`, `UART_B`, `UART_C` 映射 |

### P7 — 安全与恢复 (Safety & Recovery)
**Status: ⏳** | 负责人: — | 优先级: **Low**

| Status | Milestone | Evidence | Next Step |
|--------|-----------|----------|-----------|
| ⏳ | 看门狗 (IWDG / WWDG) | 未配置 | 在主循环启用前配置 IWDG |
| ⏳ | HardFault 处理 / 崩溃转储 | 未验证 | 实现 `HardFault_Handler` 输出寄存器信息 |
| ⏳ | 堆栈溢出检测 | `_system_stack_size=0x4000 (16KB)` 已分配 | 增加栈顶 canary |
| ⏳ | 安全降落 (failsafe) | 未验证 | 依赖 Phase 0B 基础通信完成 |

---

## 🔴 阻塞问题摘要 (Blocking Issues)

### 🔴 CRITICAL: Storage::_flash_load 阻塞 (setup_stage=502)
```
setup_stage  : 502 (try Flash)
hal_run_called : 0xBBBBBBBB ✅
```
- **症状**: `hal.run()` 已到达 ✅，但卡在 `Storage::_flash_load()` 调用 `_flash.init()` 
- **根因**: `AP_FlashStorage::init()` 对 flash page 10 进行操作时挂起或无限循环
- **影响**: 固件设3之前无法完成，MAVLink 心跳无法发出
- **下一步**: GDB 跟踪 `AP_FlashStorage::init()` 调用栈，检查 flash 驱动是否就绪

### ⚠️ HIGH: CDC ACM TX 无声 (P2)
- **症状**: `/dev/ttyACM1` 存在但无数据
- **可能原因**: 堆修复后主因转为 Storage 阻塞导致 setup 未完成
- **下一步**: 修复 Storage 阻塞后自动回测

---

## 📊 健康面板快照 (Health Dashboard)

| 区块 | 状态 | 完成度 |
|------|------|--------|
| **P0** 构建系统 | ✅ | 100% |
| **P1** 内存布局 | ✅ | 100% (堆耗尽已修复) |
| **P2** USB/CDC | ⚠️ | 40% (枚举成功，TX 被 Storage 阻塞) |
| **P3** MAVLink | ❌ | 0% |
| **P4** 传感器 | ❌ | 0% |
| **P5** 调度/循环 | ❌ | 0% |
| **P6** GPIO/外设 | ⏳ | 10% |
| **P7** 安全/恢复 | ⏳ | 0% |
| **总进度** | ⏳ | **≈ 26%** |

### Phase 0A ✅ 已达成 (100%)
- [x] 交叉编译链配置
- [x] 固件编译通过 (ROM 87.54% / RAM 78.05%)
- [x] USB 枚举 (1209:5741 CUAVv5 RTT)
- [x] 内存布局基本正确 (除堆损坏)

### Phase 0B ❌ 未完成 (0/4)
- [ ] CDC ACM 数据收发
- [ ] MAVLink HEARTBEAT 输出
- [ ] 基础传感器健康
- [ ] 循环率达标

---

## ⚡ 快速跳转 (Quick Links)

| 文件 | 用途 |
|------|------|
| `libraries/AP_HAL_RTT/` | RTT HAL 实现 |
| `libraries/AP_HAL_RTT/system.cpp` | 堆初始化与内存管理 |
| `libraries/AP_HAL_RTT/UARTDriver.cpp` | CDC ACM 串口驱动 |
| `hwdef/CUAVv5/hwdef.dat` | 板级硬件定义 |
| `mk/RTT/config.mk` | RTT 编译配置 |

---

*此面板由自动化脚本生成，是 RTT Port 状态的单一真相来源。所有团队更新应反映在此文件中。*
