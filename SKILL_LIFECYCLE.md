# SKILL_LIFECYCLE — RTT 技能状态表

> 所有 RTT 相关技能的完整生命周期追踪。
> Last updated: 2026-05-17

---

## 状态定义

| Status | 含义 |
|--------|------|
| **active** | 活跃技能，当前正在被使用或阶段性已验证 |
| **historical** | 历史记录，问题已解决，内容已合并到其他技能 |
| **reference** | 纯参考文档，不驱动开发决策 |
| **hypothesis** | 探索性假设，尚未被验证或落地 |

---

## 技能状态表

| # | Name | Status | Applies To | Last Verified | Verification Artifact | Supersedes | Superseded By |
|---|------|--------|------------|---------------|----------------------|------------|---------------|
| 1 | `rtt-chibios-11-porting-discipline` | active | ChibiOS→RTT 移植规范 | 2026-05 | RTT 移植方法论文档 | — | — |
| 2 | `ardupilot-rtt-architecture` | active | ArduPilot on RTT 架构设计 | 2026-05 | 架构文档 + hwdef 生成器 | — | — |
| 3 | `rtt-vs-chibios-reference` | active | RTT vs ChibiOS 对比参考 | 2026-05 | 对照表 | — | — |
| 4 | `rtt-multi-agent-closed-loop` | active | 多 Agent 闭环协作流程 | 2026-05 | Agent workflow 文档 | — | — |
| 5 | `rtt-porting-phase-plan` | active | 移植阶段计划 | 2026-05 | PHASE_PLAN.md | — | — |
| 6 | `rtt-cuav-v5-flash-verify` | active | CUAVv5 flash 烧录验证 | 2026-05 | flash 边界检查修复 (sidata+sdata) | — | — |
| 7 | `cuav-v5-rtt-hardfault-forensics` | active | CUAVv5 hardfault 根因分析 | 2026-05 | hardfault 排查记录 | — | — |
| 8 | `rtt-l0-verification-plan` | active | Phase 0 验证规范 | 2026-05 | L0 验证检查清单 | — | — |
| 9 | `rtt-l1-sensor-pipeline` | active | Phase 1 传感器流水线 | 2026-05 | 传感器初始化/数据流设计 | — | — |
| 10 | `rtt-phase1a-setup-hang-fix` | historical | Phase 1a 启动挂死修复 | 2026-04 | 问题已解决，根因归档 | — | `rtt-cdc-in-timeout-recovery` |
| 11 | `rtt-stabilization-driver` | active | 驱动稳定化（大技能） | 2026-05 | 2877 行综合调试记录 | — | ⚠️ 建议拆分 |
| 12 | `rtt-cuav-v5-cdc-tx-fix` | historical | CDC TX 修复 | 2026-04 | 已合并到 timeout recovery skill | — | `rtt-cdc-in-timeout-recovery` |
| 13 | `rtt-cdc-in-timeout-recovery` | active | CDC ACM timeout 恢复 | 2026-05 | CDC DTR/GCCFG 修复记录 | `rtt-cuav-v5-cdc-tx-fix`, `rtt-phase1a-setup-hang-fix` | — |
| 14 | `rtt-cuav-v5-spi-fix-record` | historical | SPI 修复记录 | 2026-04 | 已合并到 SPI 综合技能 | — | `rtt-stabilization-driver` |
| 15 | `rtt-cuav-v5-adc-spi-conflict` | active | ADC/SPI DMA 冲突 | 2026-05 | ADC-SPI 冲突分析+修复 | — | — |
| 16 | `rtt-i2c3-hardware-fix` | active | I2C3 硬件修复 | 2026-05 | I2C3 时序/电平修复记录 | — | — |
| 17 | `rtt-stm32-adc-channel-deadlock` | active | STM32 ADC 通道死锁 | 2026-05 | ADC 死锁分析报告 | — | — |
| 18 | `ap-chibios-bootloader-reference` | reference | ChibiOS bootloader 参考 | 2026-04 | ChibiOS bootloader 文档 | — | — |
| 19 | `chibios-cuav-v5-hw-reference` | reference | CUAVv5 ChibiOS 硬件参考 | 2026-04 | ChibiOS hwdef 对照 | — | — |
| 20 | `rtt-chibios-api-adaptation` | hypothesis | ChibiOS→RTT API 适配 | 2026-05 (barely started) | 未完成的概念验证 | — | — |
| 21 | `goap-debug-planner` | active | GOAP 调试规划器 | 2026-05 | GOAP debug 决策跟踪 | — | — |
| 22 | `anti-drift-verification-gate` | active | 反漂移验证门 | 2026-05 | 验证门标准文档 | — | — |
| 23 | `sona-pattern-learning` | hypothesis | Sona 模式学习 | 2026-05 (not yet proven) | 未验证的假设 | — | — |
| 24 | `cybernetics-informed-coding` | hypothesis | 控制论指导编码 | 2026-05 (not yet proven) | 未验证的假设 | — | — |
| 25 | `cybernetics-self-introspection` | hypothesis | 控制论自省机制 | 2026-05 (not yet proven) | 未验证的假设 | — | — |
| 26 | `openclaw-cc-sigkill-debug` | active | OpenClaw CC SIGKILL 调试 | 2026-05 | SIGKILL 根因分析 | — | — |

---

## 状态分布统计

| Status | Count | Notes |
|--------|-------|-------|
| **active** | 16 | 核心工作技能 |
| **historical** | 3 | 问题已解决/已合并 |
| **reference** | 2 | 纯参考不驱动决策 |
| **hypothesis** | 4 | 未验证假设 |
| **total** | **26** | |

---

## 技能合并关系 (Supersedes / Superseded By)

```
rtt-phase1a-setup-hang-fix ──→ rtt-cdc-in-timeout-recovery
rtt-cuav-v5-cdc-tx-fix     ──→ rtt-cdc-in-timeout-recovery
rtt-cuav-v5-spi-fix-record  ──→ rtt-stabilization-driver

rtt-stabilization-driver ──→ [建议拆分]
```

---

## 需关注项

1. **`rtt-porting-phase-plan`** — 需同步更新 PHASE_PLAN.md (本文件即是更新结果)
2. **`rtt-l0-verification-plan`** — 需更新 Phase 0A/0B 验证条目
3. **`rtt-stabilization-driver`** — 2877 行过大，建议拆分为子技能
4. **`rtt-l1-sensor-pipeline`** — 当前阻塞于 Phase 0B 完成
5. **hypothesis 技能** — 需评估是否继续投入或归档
