# PHASE_PLAN — ArduPilot on RT-Thread (CUAVv5) 修订版

> 按廖博士要求修订。Last updated: 2026-05-17

---

## Phase 0A (CURRENT) — 编译+烧录 baseline [已完成 ✅]

**Entry:** 无（初始状态）  
**Exit:** 编译通过 + USB 枚举 + 三层阻塞修复链确认

### 成果

| 项目 | 状态 | 备注 |
|------|------|------|
| hwdef 生成器 | ✅ | |
| app_descriptor 后处理 | ✅ | |
| flash 边界检查 | ✅ | 修复了 `_sidata+(_edata-_sdata)` 偏移 |
| 编译通过 | ✅ | ROM 87.54% RAM 78.05% |
| USB 枚举 | ✅ | `1209:5741 CUAVv5 RTT` |
| CDC ACM 可见 | ✅ | `/dev/ttyACM1` |
| 三层阻塞修复链 (Flash → assert → priority) | ✅ | |

---

## Phase 0B (NEXT) — L0 可通信基线 [阻塞中 🔴]

**Entry:** Phase 0A exit criteria met  
**Exit:** CDC ACM MAVLink HEARTBEAT + loop_rate >= 100Hz + 基础传感器健康

| Priority | 任务 | 状态 | 详情 |
|----------|------|------|------|
| **P0** | heap metadata corruption 定位+修复 | 🔴 | canary 围栏插入 → malloc/free hook 分配轨迹 → GDB/DWT watchpoint 监视被踩字段 → 二分定位触发路径 |
| **P1** | CDC ACM DTR 正确响应 | 🔴 | GCCFG 修复验证 → DTR ringbuffer reset → CDC TX drain-skip 机制确认 |
| **P2** | MAVLink 心跳 | 🔴 | `rt_device_write` 验证 → pymavlink 双向验证 |
| **P3** | 基础传感器健康 | 🔴 | SPI1 ICM20689/20602 probe through → I2C3 IST8310 probe through |
| **P4** | 主循环率 >= 100Hz | 🔴 | |

### P0 — heap metadata corruption 定位+修复

```
┌─────────────────────────────────────────────────────────┐
│ 1. canary 围栏 ← 每块 malloc 前后插入 magic pattern     │
│ 2. malloc/free hook  ← 记录分配轨迹 (callstack+size)    │
│ 3. DWT watchpoint    ← 硬件断点监视被踩 canary 字段    │
│ 4. 二分定位          ← 缩小触发路径范围                │
└─────────────────────────────────────────────────────────┘
```

### P1 — CDC ACM DTR 正确响应

```
┌─────────────────────────────────────────────────────────┐
│ 1. GCCFG 修复验证   ← 确认 GCCFG 寄存器配置生效        │
│ 2. DTR ringbuffer   ← reset toggling 后环形缓冲清理    │
│ 3. TX drain-skip    ← DTR 低时跳过 CDC TX 写入         │
└─────────────────────────────────────────────────────────┘
```

### P2 — MAVLink 心跳

```
┌─────────────────────────────────────────────────────────┐
│ 1. rt_device_write  ← 验证 RT-Thread 设备层写入        │
│ 2. pymavlink 双向   ← 宿主机 py/tools/mavlink.py 收发  │
└─────────────────────────────────────────────────────────┘
```

### P3 — 基础传感器健康

```
┌─────────────────────────────────────────────────────────┐
│ SPI1: ICM20689 + ICM20602   ← probe through 寄存器读写 │
│ I2C3: IST8310               ← probe through 寄存器读写 │
└─────────────────────────────────────────────────────────┘
```

---

## Phase 1 — 核心传感器+执行器 (L1-L3) [待启动 ⏳]

**Entry:** Phase 0B exit criteria met  
**Exit:** 全部 onboard 传感器数据流 + PWM/Servo 输出 + 姿态估计初步可用

| 层级 | 内容 | 依赖 |
|------|------|------|
| L1 | 传感器驱动完整化 (IMU+磁力计+气压计+GPS) | Phase 0B P3 |
| L2 | PWM/Servo 输出 (IOMCU 或直驱) | L1 |
| L3 | INS 姿态估计 (EKF2 初步) | L1+L2 |

---

## Phase 2 — 驱动增强 [待启动 ⏳]

**Entry:** Phase 1 exit criteria met  
**Exit:** 全部外设驱动稳定 + ADC+SDCard+双CAN

| 模块 | 内容 |
|------|------|
| ADC | 电池电压/电流监测 (rtt-stm32-adc-channel-deadlock 修复) |
| SDCard | FATFS + 日志记录 (tlog/binlog) |
| CAN1/CAN2 | DroneCAN / UBX 协议支持 |

---

## Phase 3 — 架构补齐 [待启动 ⏳]

**Entry:** Phase 2 exit criteria met  
**Exit:** RTT 调度器充分适配 ArduPilot 实时需求

| 模块 | 内容 |
|------|------|
| 调度器调优 | RTT priority ↔ APM priority 映射验证 |
| DMA/USART | 低优先级通道 DMA 卸载 |
| 缓存一致性 | DCache flush/invalidate 策略 |

---

## Phase 4 — 性能优化 [待启动 ⏳]

**Entry:** Phase 3 exit criteria met  
**Exit:** 稳态性能达标 (loop_rate >= 400Hz, 传感器 jitter < 100μs)

| 模块 | 内容 |
|------|------|
| 循环率 | 400Hz+ 主循环 |
| 中断延迟 | < 5μs 硬实时 |
| 内存 | heap fragmentation < 5% |

---

## 依赖关系总图

```
Phase 0A ──✅──→ Phase 0B ──🔴──→ Phase 1 ──⏳──→ Phase 2 ──⏳──→ Phase 3 ──⏳──→ Phase 4
                    │
                    ├─ P0: heap corruption
                    ├─ P1: CDC ACM DTR
                    ├─ P2: MAVLink heartbeat
                    ├─ P3: sensor probe
                    └─ P4: loop rate ≥ 100Hz
```

---

## 相关技能

| Skill | 角色 |
|-------|------|
| `rtt-porting-phase-plan` | 本文件的所有者，需同步更新 |
| `rtt-l0-verification-plan` | Phase 0A/0B 验证规范，需同步更新 |
| `rtt-cuav-v5-flash-verify` | Phase 0A flash 检查 |
| `cuav-v5-rtt-hardfault-forensics` | Phase 0B hardfault 排查 |
| `rtt-cdc-in-timeout-recovery` | Phase 0B CDC 修复 |
| `rtt-stabilization-driver` | Phase 0B-1 驱动稳定化（太大，建议拆分） |
| `rtt-l1-sensor-pipeline` | Phase 1 传感器流水线 |
| `rtt-i2c3-hardware-fix` | Phase 1 I2C3 硬件修复 |
| `rtt-cuav-v5-adc-spi-conflict` | Phase 2 ADC 冲突解决 |
| `rtt-stm32-adc-channel-deadlock` | Phase 2 ADC 死锁修复 |
