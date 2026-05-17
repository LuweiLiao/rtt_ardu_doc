---
name: "anti-drift-verification-gate"
description: "Anti-Drift 验证门控 — 在 kanban 四段链（R→E→V→O）每段 handoff 前自动验证输出质量，防止质量漂移"
---

# Anti-Drift 验证门控

移植自 ruflo 的 anti-drift 概念，防止开发过程中质量逐渐下降。

## 核心思想

```
Researcher → [Gate R] → Engineer → [Gate E] → Reviewer → [Gate V] → Ops → [Gate O] → Done
                ↑            ↑            ↑            ↑
            验证报告质量   验证修改安全   验证审查通过  验证烧录成功
```

每个 Gate 在 handoff 之前自动检查交付物质量。未通过则回退到对应阶段，不继续推进。

## 门控定义

### Gate R（Researcher → Engineer）

**检查项：**
- ✅ 引用 ChibiOS 对应代码的行号
- ✅ 根因分析报告 > 100 字
- ✅ 分析了至少 2 种可能原因并排除
- ✅ 对 ChibiOS 和 RTT 的差异做了对比

**回退：** Researcher 补充分析

### Gate E（Engineer → Reviewer）

**检查项：**
- ✅ 修改仅限于 `AP_HAL_RTT/` 目录（除非是通用 bug）
- ✅ 提供了代码 diff（`git diff`）
- ✅ 编译通过（scons exit=0）
- ✅ 没有回滚已验证的修复

**回退：** Engineer 修正

### Gate V（Reviewer → Ops）

**检查项：**
- ✅ 对照 ChibiOS 逐行审查（不是自娱自乐的审查）
- ✅ 批准/拒绝结论明确
- ✅ 代码未引入已知回退
- ✅ diff 不超过 500 行（合理范围）

**回退：** Reviewer 补充审查 / Engineer 修改

### Gate O（Ops → Done）

**检查项：**
- ✅ OpenOCD halt 确认无 HardFault（CFSR=0, HFSR=0）
- ✅ USB CDC 已枚举（/dev/ttyACM* 存在）
- ✅ MAVLink 心跳 1Hz
- ✅ 双重验证完成（CDC + OpenOCD 都通过）

**回退：** 根据故障类型回退到对应阶段

## 集成方式

### kanban-worker skill 集成

在 Researcher→Eng→Rev→Ops 四段链中，每个角色完成时调用验证脚本：

```bash
python3 /home/llw/.hermes/skills/embedded/anti-drift-verification-gate/scripts/verify_gate.py --gate R --task-id <TASK_ID>
```

### 自动积分

每个任务有一个"漂移分数"。通过所有 Gate 的 +10 分，回退一次的 -5 分。
分数 < 0 的 profile 触发警报机制。

## 验证脚本

`scripts/verify_gate.py` 实现了各门控的自动化检查。
