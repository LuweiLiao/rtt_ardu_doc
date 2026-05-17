# RTT ArduPilot — 移植文档仓库

> **目标**: 将 ArduPilot 从 ChibiOS 移植到 RT-Thread (RTT)，目标 MCU STM32F767 (CUAV V5)
> **文档状态**: 持续更新 | **当前 Phase**: 0B (L0 可通信基线) 🔴 阻塞
> **最后验证**: 2026-05-17

---

## 📋 目录结构

```
rtt_ardu_doc/
├── RTT_PORT_STATUS.md          # 🔴 单一真相面板（状态入口）
├── PHASE_PLAN.md               # 分阶段移植计划（0A/0B/1-4）
├── MEMORY_MAP.md               # STM32F767 内存布局 + 分配规则
├── DMA_CACHE_RULES.md          # DMA/Cache 一致性规则
├── HAL_POLLUTION.md            # libraries/ RTT 污染追踪表
├── SKILL_LIFECYCLE.md          # 所有 RTT 技能的 active/deprecated 状态
├── README.md                   # 本文档
├── ADR/
│   └── ADR-001-architecture-deviations.md  # 架构偏离记录
├── SKILLS/                     # 技能分类（镜像 skill 体系）
├── checker/                    # 自动化验证脚本
│   ├── heap_canary.py          # heap canary 插入+验证
│   ├── malloc_hook.py          # malloc/free 分配轨迹追踪
│   └── memory_map_check.py     # 内存映射一致性检查
└── EVIDENCE/                   # 验证证据（按阶段归档）
```

---

## 🎯 当前 Phase 0B 阻塞项

| # | 问题 | 状态 | 优先级 |
|---|------|------|--------|
| P0 | Heap metadata corruption (`used=86224 > total=86208`) | 🔴 | **立即** |
| P1 | CDC ACM DTR 响应 + TX 数据流 | 🟡 | 阻塞 MAVLink |
| P2 | MAVLink HEARTBEAT | ⚪ | 依赖 P0/P1 |
| P3 | 基础传感器 probe (SPI/I2C) | ⚪ | 依赖 P0 |
| P4 | 主循环率 >= 100Hz | ⚪ | 依赖 P0-P3 |

---

## 🔗 关键源文件路径

| 组件 | 路径 |
|------|------|
| RTT Port | `/data/firmare/pogo-apm/libraries/AP_HAL_RTT/` |
| hwdef.dat | `/data/firmare/pogo-apm/libraries/AP_HAL_RTT/hwdef/cuav_v5/hwdef.dat` |
| Linker script | `.../hwdef/common/board/linker_scripts/link.lds` |
| board.h | `.../hwdef/common/board/board.h` |
| deploy script | `Tools/scripts/rtt_bsp_deploy.py` |
| ChibiOS reference | `/data/firmare/pogo-apm/libraries/AP_HAL_ChibiOS/` |

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

- **Active (16)**: 当前有效的知识和规则
- **Historical (3)**: 已解决的问题记录
- **Reference (2)**: ChibiOS 对照参考（只读）
- **Hypothesis (4)**: 待验证的假设
- **Deprecated (1)**: SKILL.md 未找到

详见 [SKILL_LIFECYCLE.md](./SKILL_LIFECYCLE.md)

---

> **维护者**: 廖博士 / Hermes Agent (deepseek-v4-flash)
> **仓库**: `git@github.com:LuweiLiao/rtt_ardu_doc.git`
