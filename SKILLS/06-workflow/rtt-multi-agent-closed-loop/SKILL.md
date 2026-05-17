---
name: rtt-multi-agent-closed-loop
description: >
  三Agent闭环流水线工作流：Orchestrator(指挥) → Developer(开发) → Reviewer(验收)。
  用于 RTT ArduPilot 移植的 P0-P8 全链路改造任务。
  Profiles 已在 config.yaml 中定义，dispatcher 自动调度。
  创建无限循环的自驱团队。
---

# RTT 多Agent闭环团队工作流

## 架构

```
         Orchestrator (指挥/你)
               │ kanban_create(assignee=developer)
               ▼
   ┌──────────────────────────┐
   │       Kanban Board       │
   │  implement → review 链   │
   │  gated on parents 自动流转│
   └─────┬──────────────┬─────┘
         │              │
         ▼              ▼
   Developer       Reviewer
   (150 turns)     (100 turns)
```

## Config.yaml Profile 配置

```yaml
profiles:
  orchestrator:
    model: deepseek-v4-flash
    provider: deepseek
    max_turns: 60
    toolsets: [kanban, terminal, file, skills, search]
    system_prompt: |
      Orchestrator in multi-agent closed-loop team.
      Decompose tasks, create kanban cards, assign to developer/reviewer.
  developer:
    model: deepseek-v4-flash
    provider: deepseek
    max_turns: 150
    toolsets: [terminal, file, kanban, skills, search]
    system_prompt: |
      RTT ArduPilot port Developer.
      铁律：每行代码修改前先读 ChibiOS 参考文件精确行号。
  reviewer:
    model: deepseek-v4-flash
    provider: deepseek
    max_turns: 100
    toolsets: [terminal, file, kanban, skills, search]
    system_prompt: |
      RTT ArduPilot port Reviewer.
      对照 ChibiOS 验证代码。通过 kanban_complete，不通过 kanban_comment。
```

## 任务创建模式

### implement + review 双卡模式

每个功能点创建 2 张卡：

| 步骤 | assignee | 父任务 | 效果 |
|------|----------|--------|------|
| Pn implement | developer | (空) | 就绪，dispatcher 自动派单 |
| Pn review | reviewer | [implement 的 task_id] | todo，等 implement 完成后自定变 ready |

```python
# 创建 implement 卡（ready 立即调度）
kanban_create(title="P1: xxx", assignee="developer", body="技术规格...")
# → 返回 task_id = "t_abc123"

# 创建 review 卡（gated on implement）
kanban_create(title="P1-review", assignee="reviewer", 
              parents=["t_abc123"], body="验收标准...")
```

### 依赖链

```python
# 链式 gating: P2 依赖 P1-review
kanban_link(child_id="P2-task-id", parent_id="P1-review-task-id")
```

### 无限循环推进

当用户说"继续无限循环推进"时：
1. 不要等所有任务完成再创建下一轮
2. 看当前看板 ready 状态，立即创建下一轮 implement+review 对
3. 让 dispatcher 自动拾取、自动流转
4. Orchestrator 定期检查进度，创建新轮次

## Dispatcher 行为

- **轮询间隔**: 每 60 秒（由 gateway 进程驱动）
- **拾取规则**: status=ready 且 assignee 匹配当前可用 profile 的任务
- **并发**: 同一时间一个 profile 只能处理一个任务
- **超时**: 超过 max_turns 后会终止当前运行
- **自动运行**: 需要 gateway 在前台运行（`hermes gateway` 或 `hermes tui`）

### ⚠️ Dispatcher 不可靠 — 关键实战教训

> **2026-05-16 实战**：创建了 16 张卡（P0-P8 + 对应 review），P0-review 被拾取并完成，但 P1/P3/P4/P5-P8 全部停留在 `status=ready` **从未被 dispatcher 拾取**。最后所有卡由用户要求清理。

**已知 dispatcher 无法拾取的原因**（按概率排序）：
1. **Gateway 未运行** — dispatcher 由 gateway 进程主循环驱动。如果 `hermes gateway` 或 `hermes tui` 不在前台运行，dispatcher 根本不会轮询。`config.yaml` 中 `kanban.dispatch_in_gateway: true` 只影响 gateway 启用了此功能，不启用 gateway 本身。
2. **Profile 配置与 task assignee 不匹配** — Dispatcher 通过 task 的 `assignee` 字段匹配 profiles。如果 assignee name 和 profile name 拼写不一致（如 `developer` vs `develope`），不会拾取。
3. **并发限制** — 同一 profile 同一时间只能处理一个任务。如果 P0-review（reviewer profile）正在运行，其他 reviewer 任务不会启动。
4. **任务 ID 解析失败** — dispatcher 需要正确的 task ID 格式。如果手动 `kanban_create` 后 task_id 有异常格式，可能被跳过。

**诊断 dispatcher 是否在工作**：
```bash
# 方法1：查看 gateway 日志
pgrep -a hermes  # 确认 gateway 进程存在
# 方法2：查看看板
kanban_show(task_id)  # 查看多个 ready 任务的时间戳
# 如果多个 status=ready 任务长时间（> 2min）未被拾取，说明 dispatcher 未运行
```

### 手动派遣（dispatcher 降级模式）

## 子Agent代码质量验证（2026-05-16 新增 — 实战教训）

> **实战教训**（本次会话 P1/P3/P4 手工派遣）：子Agent 达到 `max_iterations=50` 时停止，留下的代码**可能有编译错误**。本节的验证步骤不可跳过。

### ⚠️ 常见子Agent代码问题

| 问题类别 | 具体表现 | 根因 |
|---------|---------|------|
| **命名不一致** | `.h` 声明 `_tx_bounce_buf`，`.cpp` 写 `_tx_bounce` | 子Agent只看了一侧文件就修改另一侧 |
| **缺失常量** | 用了 `APM_RTT_UART_PRIORITY` 但未定义 | 子Agent假设常量已存在，未检查头文件 |
| **结构体初始化不匹配** | `pwm_channel_map {tim*, ch, clock}` 但 init 给 `{TIM1, ch, freq}` 元素数对不上 | 子Agent假设C编译器行为与想象一致 |
| **头文件依赖缺失** | 新加代码引用了 ChibiOS 宏（`STM32_DMA_STREAM_ID`）但 RTT port 中未定义 | 子Agent只在 ChibiOS 路径中看到宏，不知道 RTT 需要显式定义 |
| **API推理错误** | C++/C-ism边界错，或假定 RTOS API 语义等价于 ChibiOS | 子Agent没查参考实现，靠泛化推理 |

### 验证流程（强制）

每次 `delegate_task` 返回后，Orchestrator 必须：

```python
# Step 1: 检查 git diff 确认修改文件
git diff HEAD --stat

# Step 2: 检查子Agent声称修改的文件与实际是否一致
# 用 read_file 查看关键改动，确认命名、类型、常量对齐

# Step 3: 编译验证（缺一不可）
scons --v=ArduCopter --target=cuav_v5 -j$(nproc) 2>&1 | grep -E "error:"
# 如果有 error → 分析根因 → 修复 → 重新编译
# 如果无 error → 记录为"编译通过"

# Step 4: 记录到看板 comment
kanban_comment(task_id, "子Agent完成。编译结果: ✅/❌")
```

### 子Agent输出后常见修复模式

**命名不一致修复**（如 `_tx_bounce` → `_tx_bounce_buf`）：
```bash
# 方法：确认头文件声明，修复 .cpp 中的引用
grep -n "声明名" AP_HAL_RTT/UARTDriver.h
grep -n "引用名" AP_HAL_RTT/UARTDriver.cpp
# 统一命名后重新编译
```

**缺失常量修复**：
```bash
# 找到同类常量定义的位置，添加缺失的常量
grep -n "APM_RTT.*PRIORITY" AP_HAL_RTT/Scheduler.h
# output: #define APM_RTT_MAIN_PRIORITY 5
# output: #define APM_RTT_TIMER_PRIORITY 4
# → 在同类位置添加缺失常量
```

**ChibiOS特有宏缺失**：
```python
# 如果是 hwdef.h 中引用了 ChibiOS 宏（如 STM32_DMA_STREAM_ID），
# 必须在生成脚本的 write_dma_header() 开头添加宏定义：
f.write('#define STM32_DMA_STREAM_ID(dma, stream) ((((dma) - 1U) * 8U) + (stream))\\n')
f.write('#define STM32_DMA_STREAM_ID_ANY 255\\n\\n')
```

**优先修复顺序**：
1. 命名/声明不匹配（最易修，影响最大）
2. 缺失常量/宏定义
3. 结构体初始化格式
4. 类型/API 语义错误

### 常见陷阱

- ⚠️ 子Agent说"编译通过"不等于真的通过了——必须在 scons 中实际编译验证
- ⚠️ 子Agent修改 `rtt_hwdef.py` 后，必须重新生成 hwdef.h 再编译——生成器本身可能有语法错误
- ⚠️ 多个子Agent并行时可能修改同一份文件（如 `rtt_hwdef.py`）→ 需要 git diff 检查冲突
- ⚠️ 子Agent 50次调用限制下倾向于快速提交未完全验证的代码——Orchestrator 必须做质量把关，而不是信任子Agent的"已完成"声称
- ⚠️ **区分"预存bug"和"子Agent引入bug"**（本次会话关键教训）：编译错误可能早已存在（如 VAL_GPIO 表 `}` 语法错误），不在子Agent的修改范围内。先判断根因再分配修复责任。

### 参考文件

`references/subagent-compile-error-patterns.md` — 子Agent代码常见编译错误模式与修复方法

## Cron 监控设置
P1: ready (assignee=developer)  ← 没有 Agent 来领取
P3: ready (assignee=developer)  ← 同上
P4: ready (assignee=developer)  ← 同上
```

**诊断方法**：查看看板，如果多个 `status=ready` 的任务长时间（> 2分钟）未被拾取，说明 dispatcher 未运行。

### 手动派遣（dispatcher 降级模式）

当 dispatcher 不拾取任务时，Orchestrator 应使用 `delegate_task` 手工派遣：

```python
# 读取任务 body 获取 context
task = kanban_show(task_id)

# 手工派遣给子 Agent
result = delegate_task(
    tasks=[{
        "context": f"Working directory: {path}\nRepository: {repo}\n\n这是看板任务 {task_id} ({title})。\n{task_body}",
        "goal": "实现/完成该看板任务描述的需求",
        "toolsets": ["terminal", "file", "search"]
    }]
)
```

**规则：**
1. **先看看板确认任务状态** — 只有 `status=ready` 或 `status=todo` 的任务可以手工派遣
2. **读取原始 task body** — 子 Agent 需要完整的上下文（技术规格、参考文件路径、验收标准）
3. **传递正确的工作目录和仓库路径** — 否则子 Agent 找不到代码 → 浪费时间
4. **子 Agent 完成后** — 用 `kanban_comment` 记录进展（即使未完成也要记录部分成果），不要丢失看板链的连续性
5. **不改变看板状态** — 手动派遣不影响看板的依赖图（parents/children 链仍然有效）
6. **子 Agent 超过 max_iterations 时** — 看板 comment 记录"部分完成"，由 Orchestrator 决定继续 dispatch 还是先 review
7. **不要同时手工和自动派同一任务** — 手工派遣后如果 dispatcher 恢复，可能重复执行

## 看板清理协议（用户要求清理停滞任务时使用）

2026-05-16 实战：用户要求"无响应的要处理掉，已完成的有归档"。kanban 工具集没有 `archive/delete/cancel` 功能。

### 流程

```python
# 1. 检查所有任务状态
for tid in [所有 task_id]:
    task = kanban_show(tid)
    print(f"{tid}: {task['status']} — {task['title']}")

# 2. 分类
#    - status=done → 已完成，不改动
#    - status=ready 且从未被拾取 → 用 kanban_complete 终结
#    - status=todo（gated on 某个父任务）→ 随父任务一同终结
#    - status=running → 先等它完成或手动 kill

# 3. 终结停滞任务
kanban_complete(task_id=stalled_id, summary="停滞原因说明")

# 4. 清理监控 cron（如果之前设置了针对这些任务的 cron）
cronjob(action="remove", job_id="...")
```

### 终结 summary 格式

对每个终结的任务，summary 应包含：
1. **最终状态** — 完成比例（如"子Agent完成代码但未达编译通过"或"dispatcher 从未拾取"）
2. **已有资产** — 已存在的代码文件路径（如 `UARTDriver.cpp` 已修改）
3. **遗留问题** — 已知的编译错误或未实现功能
4. **future handoff** — 如果将来恢复此任务可以从哪里继续

**示例**（P1 终结）：
```
P1 UART TX线程 — 子Agent完成代码但遗留2个编译错误未修复。
代码已位于 UARTDriver.h/.cpp，需修复 _tx_bounce 命名和 APM_RTT_UART_PRIORITY 常量后编译验证。
```

### 分类策略

| 卡状态 | 用户\"处理掉\"含义 | 操作 |
|--------|-----------------|------|
| `done` | 已归档 | 不需要动 |
| `ready`（从未被拾取） | 无响应→清理 | `kanban_complete` + 说明原因 |
| `todo`（gated） | 依赖链断裂→清理 | `kanban_complete` + 注明父任务已停 |
| `running` | 正在运行中 | 评估是否需要 kill。如果超过预期时长且无进展，先 `kanban_heartbeat` 检查活跃性 |

## Cron 监控设置

## 看板结构（当前两轮共16张卡）

### 第一轮：P0-P4（hwdef/补齐 + 驱动改造）

| ID | 任务 | assignee | 依赖 |
|----|------|----------|------|
| P0 | hwdef 补齐: VAL_GPIO+dma_resolver+ldscript+env | **(已由 Orchestrator 完成)** | — |
| P0-review | 验收 P0 | reviewer | — |
| P1 | UART TX线程+unbuffered_writes+DMA bounce | developer | — |
| P1-review | 验收 P1 | reviewer | P1 |
| P2 | Flow control+set_options | developer | P1-review |
| P2-review | 验收 P2 | reviewer | P2 |
| P3 | Flash边界+HSI+UART parity | developer | — |
| P3-review | 验收 P3 | reviewer | P3 |
| P4 | PWM组配置文件映射 | developer | — |
| P4-review | 验收 P4 | reviewer | P4 |

### 第二轮：P5-P8（核心模块补齐 + 构建 + 测试）

| ID | 任务 | assignee | 依赖 |
|----|------|----------|------|
| P5 | HAL_Storage 闪存驱动 | developer | — |
| P5-review | 验收 P5 | reviewer | P5 |
| P6 | HAL_GPIO封装+HAL_Util补齐 | developer | — |
| P6-review | 验收 P6 | reviewer | P6 |
| P7 | 多板型支持+构建系统 | developer | — |
| P7-review | 验收 P7 | reviewer | P7 |
| P8 | HIL测试流水线+HAL验证 | developer | — |
| P8-review | 验收 P8 | reviewer | P8 |

## 工作流

### 正常流程

```
1. Orchestrator 收到需求
2. 分解为 implement+review 双卡
3. Dispatcher 拾取 implement → Developer 实现 → kanban_complete
4. Review 卡从 todo 变为 ready
5. Dispatcher 拾取 review → Reviewer 验收
   → 通过: kanban_complete → 闭环完成
   → 打回: kanban_comment(修改意见) + kanban_create 新 implement 卡
6. Orchestrator 汇总 → 创建下一轮任务
```

### 无限循环模式

```
while kanban有任务:
    dispatcher 自动拾取并执行
    cron 每N分钟报告进度
    Orchestrator (你) 定期检查：
        - 是否需要创建新轮次
        - 是否有阻塞需要干预
        - 是否有被打回的代码需要重新分配
```

## 铁律

1. Developer 必须先读 ChibiOS 参考文件精确行号再修改
2. 每次修改后必须运行 git diff 和编译验证
3. Reviewer 对照 ChibiOS 验证代码正确性
4. 不通过的代码打回 + kanban_comment 写明具体修改意见
5. Orchestrator 将进度通过 cron 定期推送到飞书
6. 不是所有任务都要等完成才创建下一轮——ready 任务越多，闭环越高效
