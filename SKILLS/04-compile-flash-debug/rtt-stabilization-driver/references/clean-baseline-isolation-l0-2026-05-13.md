# Clean Baseline Isolation — L0 验证记录（2026-05-13）

## 问题背景

启动修复（CPACR/FPCCR/VTOR/D-Cache/I-Cache 全部用内存映射寄存器替代 CP15 MRC/MCR）已实现。
但烧录后 USB CDC 不枚举。同时有大量其他修改文件（SPI、IMU、传感器电源、链接脚本等）。

用户（廖博士）说：「我发现你老是卡在usb上。usb好像就没有顺畅过」，表达了强烈的挫败感。

## 执行基线隔离

只保留 `startup_rtt_override.S` 的启动修复，revert 所有其他 7 个文件 + submodule 修改：

**Reverted 文件列表**：
- `libraries/AP_HAL_RTT/SPIDeviceManager.cpp`
- `libraries/AP_HAL_RTT/hwdef/common/.config`
- `libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds`
- `libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c`
- `libraries/AP_HAL_RTT/hwdef/cuav_v5/hwdef.dat`
- `libraries/AP_InertialSensor/AP_InertialSensor.cpp`
- `libraries/AP_InertialSensor/AP_InertialSensor_Invensense.cpp`
- `modules/rt-thread`（submodule 内部修改）

**保留的唯一文件**：`libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S`

## 编译信息

```
ROM: 1318496 / 1540096 bytes (85.61%)
RAM: 287392 / 524288 bytes (54.82%)
text=1318496, data=5308, bss=282068, dec=1605872
```

## OpenOCD 验证结果

| 检查项 | 值 | 结论 |
|--------|-----|------|
| 向量表 @ 0x08008000 | `0x200054bc 0x080effa5` | ✅ SP/Reset正确 |
| SCTLR | `0x00000001` | MPU on, D-Cache off, I-Cache off |
| CPACR | `0x00F00000` | ✅ FPU全使能 |
| CPUID | `0x411fc270` | ✅ Cortex-M7 r1p0 |
| FPCCR | `0x80000000` | ASPEN=1, LSPEN=0 |
| CFSR | `0x00000000` | ✅ 无异常 |
| HFSR | `0x00000000` | ✅ 无HardFault |
| xPSR | `0x81000000` | ✅ 正常运行中 |

## MAVLink 验证结果

2 个 ACM 设备存在（ttyACM0 旧 + ttyACM1 新）。

**ttyACM0**：打开成功，10秒超时无数据（旧设备残留）
**ttyACM1**：**收到完整 MAVLink 数据流！**

消息序列（等待15秒等 boot 输出结束后）：
- ❤️ **HEARTBEAT** — type=2(copter), autopilot=3(ArduPilot), base_mode=89, custom_mode=0(STABILIZE), system_status=5(STANDBY)
- 📊 **RAW_IMU** — xacc=0, yacc=0, zacc=0（IMU SPI未通信，数据全0）
- 📡 **SCALED_PRESSURE** — press_abs=0.0（MS5611未工作）
- 🧭 **ATTITUDE** — 全0（无传感器数据）
- 📍 **GLOBAL_POSITION_INT** — 全0
- ⚙️ **SERVO_OUTPUT_RAW** — 全0
- 🔋 **MEMINFO** — freemem=10336（堆紧张）

## 关键发现

1. **USB CDC 在 clean baseline + 仅启动修复的情况下正常工作** — 不需要任何 USB 配置修改
2. **之前 USB 不枚举的原因是其他修改文件干扰了 USB 初始化路径**（最可能是 `rt_board_init.c` 的传感器电源改写、或链接脚本变化影响了内存布局）
3. **IMU(RAW_IMU) 和气压计(SCALED_PRESSURE) 数据全0** — 这是 L1 需要修复的问题，与 USB 无关
4. **堆内存仅 10KB** — `freemem=10336`，系统运行正常但堆空间紧张（IMU 和气压计 probe 可能需要更多堆）

## 下一步方向

1. ✅ L0 已达成（MCU运行+无HardFault+USB枚举+MAVLink心跳+STANDBY）
2. ⏳ L1 待推进：诊断 IMU SPI 通信（ICM20689, ICM20602）→ 诊断 MS5611 气压计 → 验证 ATTITUDE 数据流

## 核心教训

**当多个修改文件导致子系统不工作时，不要试图逐个排查——直接全部 revert，只保留核心修复，再看。如果恢复了，说明问题在那些 revert 的文件中。这是一种 O(1) 的隔离方法，比 O(n) 的逐个排除效率高一个数量级。**
