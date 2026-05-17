---
name: cybernetics-informed-coding
description: Inject engineering cybernetics principles (钱学森《工程控制论》) into AI coding agent tasks. Use when delegating work to CC/Codex/OpenCode to embed control-theoretic thinking into code generation, debugging, and system design.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cybernetics, control-theory, ai-agent, self-evolution]
    related_skills: [claude-code, codex, opencode]
---

# Cybernetics-Informed Coding — 控制论驱动的编码实践

> **核心命题**：基于钱学森《工程控制论》(Engineering Cybernetics, 1954) 的18章体系，将控制论原理注入AI Agent的编码行为。

## 用法

当委派任务给CC/Codex/OpenCode时，在prompt中嵌入以下控制论指导原则：

### 方法1：CC `--append-system-prompt` 注入

```
claude -p "你的任务" --append-system-prompt-file ~/.hermes/skills/embedded/cybernetics-informed-coding/references/cybernetics-primer.md
```

### 方法2：在delegate_task的context中引用

将控制论要点作为context传递给子Agent。

### 方法3：Codex/OpenCode的prompt前缀

在exec/run的prompt开头添加控制论上下文摘要。

---

## 控制论18章 → 编码实践映射速查

### 第一层：经典控制（Ch.1-9）

| 章节 | 控制论概念 | 编码实践 |
|------|-----------|---------|
| Ch.1 | 系统建模 | 写代码前先理解系统架构，画数据流图 |
| Ch.2 | 拉氏变换/换域思考 | 复杂问题先变换到结构化空间再求解 |
| Ch.3 | 传递函数/频率响应 | 理解代码的"响应特性"——输入如何映射到输出 |
| Ch.4 | **反馈伺服** | **每个修改后必须验证（编译/测试/烧录）** |
| Ch.5 | 解耦控制 | 模块间正交设计，修A不坏B |
| Ch.6 | 振荡控制/dither | 陷入僵局时引入小幅随机扰动（换思路） |
| Ch.7 | 采样系统 | 合理的检查频率——太粗漏错误，太细浪费资源 |
| Ch.8 | 时滞系统 | 时滞大时每次只改一处，小步快跑；用Smith预估器思想——基于历史经验预估结果而不等待实际反馈；警惕时滞的destabilizing效应——等待反馈期间可能做出错误决策 |
| Ch.9 | 随机噪声 | 在噪声中提取信号，统计最优而非每次最优 |

### 第二层：现代控制（Ch.10-14）

| 章节 | 控制论概念 | 编码实践 |
|------|-----------|---------|
| Ch.10 | bang-bang控制 | 有时候果断的全做/全不做比渐进修改更有效。**诊断**：检查是否在"修A→测B→修A变体→测B"的极限环中振荡；若是，用STOP-REWRITE/FREEZE/ESCALATE三种离散开关打破循环 |
| Ch.11 | 相平面/极限环 | 检测是否在局部最优附近来回振荡。**诊断问题**：最近3-5次fix尝试是否属于同一类别？若是→已陷入极限环。**打破策略**：选择完全相反的假设先测试（bang-bang switch），而非继续微调当前方向 |
| Ch.12 | 变系数系统/冻结系数法 | **制定计划时snapshot当前工具可用性，假设中途不变，但预留检测点——工具失效时立即重评估** |
| Ch.13 | 摄动理论 | 从已知方案出发，逐步加入修正 |
| Ch.14 | **最优控制** | **定义量化性能指标J，选择使J最小的方案** |

### 第三层：智能控制（Ch.15-18）⭐

| 章节 | 控制论概念 | 编码实践 |
|------|-----------|---------|
| Ch.15 | **自寻优** | 不知道最优在哪→加扰动→测方向→逐步逼近 |
| Ch.16 | 噪声过滤 | 从历史中提取有意义模式，过滤偶然噪声 |
| Ch.17 | **超稳定系统** | 失败→随机切换策略→锁定稳定模式 |
| Ch.18 | **冗余容错** | 多次执行取最优，统计可靠性 |

---

## 七条核心编码原则

1. **反馈是灵魂**（Ch.4）— 每个操作必须有验证环节（闭环）
2. **小步快跑**（Ch.8）— 时滞大的系统中，小修改+快迭代 > 大改动+慢验证
3. **李雅普诺夫思维**（Ch.11）— 定义"进步指标"，确保每一步都在变好
4. **自适应策略**（Ch.12）— 根据环境反馈自动调整工作模式
5. **极值搜索**（Ch.15）— 主动探索最优方案，不固守已知模式
6. **噪声容忍**（Ch.9/16）— 统计最优而非每次最优
7. **冗余容错**（Ch.18）— 多策略备选、重试机制、错误恢复

---

## 控制论诊断框架

当遇到问题时，用以下框架诊断：

### 时滞大，反馈慢 → Ch.8 Smith预估器
- 症状：编译/测试时间很长，修改后要等很久才能看到结果
- 诊断：系统存在显著时滞τ，在等待期间Agent可能做错误决策（destabilizing effect）
- 策略：用Smith预估器思维——根据历史经验预估操作结果，不必等待实际反馈
  - 例如：知道某类修改通常需要某类修复，可以在编译完成前预先写好补丁
  - 但必须保留最终验证步骤（闭环不可省略）
- 执行规则：τ越大，单次修改幅度越小，但预估的修正步骤可以并行准备
- 误差 = 期望 - 实际
- 用编译错误作为反馈信号
- 修正后立即重新编译验证

### 反复改同一处 → Ch.10/11 极限环
- 检测振荡模式：是否在两个状态间来回切换？
- 打破方式：换一种方法（bang-bang切换到不同策略）

### 修A坏B → Ch.5 耦合
- 变量间存在隐耦合
- 解耦方案：独立修改，各自验证
- **⛔ 特殊陷阱：Architecture Cleanup 中的耦合风险** — 当清理子系统 A（通用代码中的 RTT 违规修改）时，**非对角项 $C_{AB}$ 将改动副作用耦合到子系统 B（IOMCU 通信）**。清理者 `git checkout origin/master` 无差别还原整个文件，丢失了 B 的 RTT 适配补丁。这是跨周同构模式（Week 3 USB CDC, Week 5 假阳性, Week 7 IOMCU）的根因。
- **修复检查**：清理后必须编译所有目标变体（IOMCU on/off），确认非对角元素 $C_{AB}=0$

### 进度停滞 → Ch.15 自寻优
- 当前策略已达局部最优
- 加扰动：尝试完全不同的方法

### 计划执行中工具突然不可用 → Ch.12 冻结系数法失效
- 诊断：Agent的"时变参数"包括工具可用性（API key/配额）、skill新鲜度、上下文窗口剩余量
- 策略：制定计划时snapshot当前能力，执行中假设不变；如果检测到参数漂移（工具报错），立即重新评估而非盲目继续
- 类比：火箭质量随燃料消耗递减 ≈ 上下文窗口随对话推进递减

### 不确定怎么做 → Ch.17 超稳定
- 随机选择一个策略
- 检测是否"穿越边界"（失败）
- 失败则切换，稳定则锁定

---

## 多Agent控制论学习协议

### 学习进度追踪
每个Agent（CC/Codex/OpenCode）的学习状态记录在：
`~/.hermes/notes/agent-cybernetics-progress.json`

### 学习任务模板
每次委派学习任务时使用以下结构：

```
你是[Agent名]，正在学习钱学森《工程控制论》。

## 本章学习目标
- 核心概念：[从速查表选取]
- 编码实践：[对应的编码原则]
- 实际应用：[在当前项目中找到的应用场景]

## 学习任务
1. 阅读[章节摘要]
2. 在当前代码中找到[概念]的实例
3. 用[概念]改进一段代码
4. 记录学到的教训

## 输出要求
将学习成果写入指定文件，格式为markdown。
```

### 章节学习顺序
建议按控制论三层架构顺序学习：
1. 先学 Ch.4（反馈）+ Ch.5（解耦）— 最直接的编码应用
2. 再学 Ch.8（时滞）+ Ch.11（稳定性）— 调试思维
3. 最后学 Ch.15（自寻优）+ Ch.17（超稳定）— 自我进化

---

## Agent指令注入机制（持久化）

三个Agent各自有不同的指令注入路径，以下是实测验证的方法：

### Claude Code (CC)
- **全局指令**: `~/.claude/CLAUDE.md` — 每次会话自动加载
- **非交互验证**: `claude -p "确认你已加载CLAUDE.md" --max-turns 1`
- **技巧**: CLAUDE.md中包含控制论原则后，CC会主动应用（如加`[Cybernetics Ch.X]`注释）
- **注意**: 不要用 `--append-system-prompt-file` 做永久注入，只用于临时覆盖

### Codex (0.121.0+)
- **全局指令**: `~/.codex/instructions.md` — 每次会话加载
- **Skill系统**: `~/.codex/skills/<name>/SKILL.md` — YAML frontmatter + markdown body
- **非交互**: `codex exec --skip-git-repo-check --full-auto "prompt"`
- **已知问题**: 如果使用自定义proxy（如Responses API转Anthropic），tool-call协议转换可能丢失function_call/function_call_output语义，纯文本任务无影响
- **Config**: `~/.codex/config.toml` 中的 `wire_api` 和 `base_url`

### OpenCode
- **项目级指令**: 项目根目录 `AGENTS.md` — 进入项目目录时加载
- **无全局指令文件**: OpenCode不支持类似CLAUDE.md的全局文件，必须逐项目放置
- **非交互**: `opencode run --format json "prompt"` — 支持非交互batch模式
- **Agent创建**: `opencode agent create --description "..." --mode all --tools "..."` 需要LLM调用，如果底层API余额不足会失败
- **Config**: `~/.config/opencode/config.json`

### Cron调度管理
- `cronjob list` 列出所有job，注意确认job_id对应正确的job名称
- ⚠️ **陷阱**: 多个job可能名称相似，更新前务必确认job_id
- 已建立的Cron jobs:
  - 每日9:00: Hermes自身控制论学习
  - 二四六10:00: 多Agent学习（驱动CC/Codex/OpenCode）
  - 周日21:00: 周度自省
  - 每月1号: 月度J₁-J₄评估

---

## CC 协同工作模式（RTT 移植场景）

当 Hermes Agent 与 Claude Code (CC) 协同进行 RTT ArduPilot 移植时，遵循以下模式：

### 工作模式

1. **CC 每解决一个问题，必须提交中文 Git commit** — 记录已修复的内容和根因
2. **“不需要我同意”** — 用户授权在正确方向上自主决策，无需逐次请示
3. **不达目的不罢休** — 持续推进直到目标达成（L0→L1→L2 稳定等级）

### CC 走偏诊断与干预

CC 在嵌入式调试中容易过度深入 GDB 循环，以下信号表示需要干预：

| 信号 | 行动 |
|------|------|
| MCU 被 GDB halt 超过 10 分钟 | 立即 `echo "resume" \| nc -q1 localhost 4444` 恢复 |
| CC 日志超过 15 分钟未更新 | 读最后几条日志诊断，若陷入 GDB 循环则直接介入 |
| 同一调试方向超过 30 分钟无进展 | 换方案（[Ch.15] 自寻优 — 加扰动切换到不同策略） |
| CC 在调查已解决的问题（如 Semaphores.cpp 已有递归绕行但仍查） | 纠正方向 |
| 固件已 boot 但 CC 仍在查定时器 | 跳过定时器问题，推进到下一模块 |

### MCU 状态自动恢复

每次 CC 用 GDB 调试后，MCU 可能被遗留在 halt 状态。应当：
1. 每次检查时先通过 OpenOCD telnet 检查 MCU 状态
2. 若 halted → `echo "resume" | nc -q1 localhost 4444`
3. 若 OpenOCD 已死 → 重启 OpenOCD 并 resume MCU
4. 然后检查 CDC 通信是否正常

### 中文 Git commit 格式

```
fix(模块): 修复问题描述 — 关键信息
<空行>
根因：详细分析。存放原因层次。
<空行>
修复：具体做了什么。
<空行>
[Cybernetics Ch.X] 控制论原理应用
```

### 验收标准

- OpenOCD GDB 可连接（MCU 在运行）
- USB CDC 可通信（`/dev/ttyACM*` 存在且有数据）
- MAVLink 心跳持续（pymavlink wait_heartbeat 成功）
- 状态进入 STANDBY 或 ACTIVE

## 引用格式

在代码注释或commit message中引用控制论章节时使用：
```
[Cybernetics Ch.X] 简要说明
```

示例：
```c
// [Cybernetics Ch.4] 闭环验证：修改后立即检查编译结果
// [Cybernetics Ch.15] 自寻优：尝试不同的SPI时钟配置
// [Cybernetics Ch.17] 超稳定：失败时切换到备选方案
```
