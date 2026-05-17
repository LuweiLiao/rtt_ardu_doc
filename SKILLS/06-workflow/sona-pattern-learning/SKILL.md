---
name: "sona-pattern-learning"
description: "SONA 风格模式学习钩子 — 自动从完成的 kanban 调试任务中提取模式并更新相关 skill"
---

# SONA 模式学习钩子

移植自 ruflo 的 SONA (Self-Optimizing Neural Architecture) 模式学习概念，适配 Hermes Agent 的 kanban + skill 体系。

## 核心流程

```
kanban task done ──→ 提取模式 ──→ 更新 skill ──→ 记忆持久化
                          │
                          ▼
                  下次类似 bug 时自动加载
```

## 使用方式

### 手动触发

```bash
python3 scripts/learn_pattern.py kanban --task-id <TASK_ID>
```

### 自动钩子（cronjob）

注册一个每分钟检查 kanban 最近完成任务的 cronjob：

```bash
# 创建 cron (由 Hermes Agent 执行)
cronjob action=create \\
  name="sona-pattern-learner" \\
  schedule="*/5 * * * *" \\
  script=learn_pattern.py \\
  prompt="检查 kanban 最近 5 分钟完成的任务，提取调试模式并更新相关 skill"
```

## 模式提取规则

### 提取内容

每个调试闭环提取以下字段：

```yaml
pattern:
  symptom: "USB CDC 不枚举"           # 现象
  diagnosis: "CFSR=0x10000 IACCVIOL"  # 诊断发现
  root_cause: "USB PCD_MspInit 访问无效外设地址"  # 根因
  fix: "hwdef.dat 移除冲突的 OTG1_ID pin"        # 修复
  verification: "CDC枚举+心跳1Hz"      # 验证方法
  skill_target: "rtt-stabilization-driver"  # 应更新的 skill
```

### 存储位置

- **短期**（5 条）：存储在 `~/.hermes/skills/embedded/sona-pattern-learning/patterns/` 下
- **长期**（全历史）：存储在 mem0 中，namespace `rtt-patterns`

### skill 自动更新

当同一个模式的 **fix 出现 2 次以上**，自动 patch 到对应 skill 的陷阱列表中：

```python
# 模式: "IACCVIOL 在 USB 初始化中"
# 在 rtt-stabilization-driver SKILL.md 中追加
- "如果在 HAL_PCD_MspInit 处出 IACCVIOL → 检查 hwdef.dat 中 OTG_ID pin 和 USB 引脚冲突"
```

## 脚本

`scripts/learn_pattern.py` 实现了完整的模式提取+存储+skill 更新管道。

## 集成

- **goap-debug-planner**: SONA 学习到的模式可作为 GOAP 规划器的额外启发信息（降低已知模式的搜索代价）
- **rtt-stabilization-driver**: SONA 自动向此 skill 追加已验证的陷阱
