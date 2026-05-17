# RTT ArduPilot — 移植文档仓库

> **目标**: 将 ArduPilot 从 ChibiOS 移植到 RT-Thread (RTT)，目标 MCU STM32F767 (CUAV V5)
> **文档状态**: 持续更新 | **当前 Phase**: 0B (L0 可通信基线) 🔴 阻塞于 Storage
> **最后验证**: 2026-05-17 (P0 heap fix) ~ **最近推送**: `1187b16`

---

## 📋 目录结构

```
rtt_ardu_doc/
├── RTT_PORT_STATUS.md              # 🔴 单一真相面板（状态入口）
├── PHASE_PLAN.md                   # 分阶段移植计划（0A/0B/1-4）
├── MEMORY_MAP.md                   # STM32F767 内存布局 + 分配规则
├── DMA_CACHE_RULES.md              # DMA/Cache 一致性规则
├── HAL_POLLUTION.md                # libraries/ RTT 污染追踪表
├── SKILL_LIFECYCLE.md              # 所有 RTT 技能的 active/deprecated 状态
├── README.md                       # 本文档
│
├── ADR/
│   └── ADR-001-architecture-deviations.md    # 架构偏离记录（5项，含线程栈分配）
│
├── SKILLS/                         # 26 个 RTT 技能（镜像 Hermes skill 体系）
│   ├── 01-core-discipline/         # 移植铁律 + 架构规范
│   ├── 02-architecture-reference/  # ChibiOS 对照参考（只读）
│   ├── 03-phase-plan/              # 分阶段计划
│   ├── 04-compile-flash-debug/     # 编译/烧录/调试工作流
│   ├── 05-diagnosis/               # 诊断子技能（adc/cdc/i2c/hardfault/…）
│   └── 06-workflow/                # 多Agent协作 + 自学习流程
│
├── checker/                        # 自动化验证脚本
│   ├── heap_canary.py              # heap canary 插入+验证
│   ├── malloc_hook.py              # malloc/free 分配轨迹追踪
│   └── memory_map_check.py         # 内存映射一致性检查
│
└── EVIDENCE/                       # 验证证据（按阶段归档）
    └── P0-Heap-Exhaustion-20260517.md       # P0 堆耗尽完整调试记录
    └── Phase0B-Storage-Block-20260517.md    # Storage 阻塞跟踪文档
```

---

## 🎯 当前阻塞项（P0 堆耗尽 ✅ 已修复）

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| ✅ | **P0 堆耗尽** | **FIXED** | 根因：RTT rt_thread_create 用堆分配线程栈（ChibiOS 用静态 BSS）。缩减 ap_timer(16K→4K), ap_io(8K→4K), storage(8K→4K), ap_uart(8K→4K)，节省 24KB。HEAP 恢复健康 `used < total` ✅ |
| 🔴 | **Storage::_flash_load 阻塞** | **ACTIVE** | setup_stage=502，`AP_FlashStorage::init()` 挂起。USB枚举 ✅ 但 MAVLink 心跳被阻塞 |
| 🟡 | CDC ACM TX | PENDING | 堆修复后 TX 被 Storage 阻塞连带，需先解 Storage |
| ⚪ | MAVLink HEARTBEAT | DEPENDS | 依赖 Storage 阻塞解除 |
| ⚪ | 传感器 probe | DEPENDS | 依赖 Phase 0B 完成 |

---

## 🔗 关键源文件路径

| 组件 | 路径 |
|------|------|
| RTT Port | `/data/firmare/pogo-apm/libraries/AP_HAL_RTT/` |
| hwdef.dat | `/data/firmare/pogo-apm/libraries/AP_HAL_RTT/hwdef/cuav_v5/hwdef.dat` |
| Scheduler (线程栈) | `/data/firmare/pogo-apm/libraries/AP_HAL_RTT/Scheduler.cpp` |
| Storage (当前阻塞) | `/data/firmare/pogo-apm/libraries/AP_HAL_RTT/Storage.cpp` |
| Linker script | `.../hwdef/common/board/linker_scripts/link.lds` |
| board.h | `.../hwdef/common/board/board.h` |
| deploy script | `Tools/scripts/rtt_bsp_deploy.py` |
| ChibiOS reference | `/data/firmare/pogo-apm/libraries/AP_HAL_ChibiOS/` |
| RTT thread_create | `modules/rt-thread/src/thread.c:568` |
| Heap allocator | `modules/rt-thread/src/mem.c:275` |

---

## ⚙️ 验证流程

每个修复必须包含以下证据格式：

```
Commit: <SHA>
Build command: scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
Binary SHA256: <hash>
app_descriptor dump: <hex>
Flash command: openocd -f ... -c "program ... verify"
Board: CUAV V5 / STM32F767
Boot log: <GDB/OpenOCD transcript>
MAVLink evidence: <pymavlink log>
Regression checklist: [items]
```

---

## 📚 全部 26 个技能状态速览

SKILLS/ 目录镜像实际 Hermes Agent 的 embedded 类技能，按 6 个主题组织：

| 分类 | 技能数 | 内容 |
|------|--------|------|
| 01-core-discipline | 2 | 移植铁律 + 架构规范（含18份参考文档） |
| 02-architecture-reference | 3 | ChibiOS 对照参考（bootloader/hwdef/behavior delta） |
| 03-phase-plan | 4 | 分阶段计划（Phase 0A→1） |
| 04-compile-flash-debug | 3 | 烧录/调试/稳定化工作流（含60+份诊断参考 + 脚本） |
| 05-diagnosis | 8 | 子技能：adc/cdc/i2c/hardfault/GOAP/openclaw |
| 06-workflow | 6 | 多Agent协作 + 自学习 + 控制论 |
| **总计** | **26** | 182 files（含 SKILL.md + references/ + scripts/） |

详见 [SKILL_LIFECYCLE.md](./SKILL_LIFECYCLE.md)

---

## 📝 最近 Commit 历史

| 日期 | Commit | 内容 |
|------|--------|------|
| 2026-05-17 | `1187b16` | 同步全部 26 个 RTT 技能到 SKILLS/ 目录 |
| 2026-05-17 | `591cfe0` | P0 堆耗尽调试记录 + ADR-001 Deviation 5 + Storage 阻塞跟踪 |
| 2026-05-17 | `eb00fbd` | 更新 RTT_PORT_STATUS：P0 堆耗尽已修复 |
| 2026-05-17 | `f9e9d85` | RTT 移植文档仓库初始化 |

---

> **维护者**: 廖博士 / Hermes Agent (deepseek-v4-flash)
> **仓库**: `git@github.com:LuweiLiao/rtt_ardu_doc.git`
