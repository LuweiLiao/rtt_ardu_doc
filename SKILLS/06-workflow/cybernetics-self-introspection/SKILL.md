---
name: cybernetics-self-introspection
title: "Engineering Cybernetics Self-Introspection (Daily + Weekly)"
description: "Systematic Agent self-review using Qian Xuesen's Engineering Cybernetics — daily self-optimizing exploration (Ch.15) + weekly deep analysis (Ch.11 phase-plane, Ch.17 ultrastability, Ch.9 PSD)."
trigger: "Daily cron (09:00) for exploration, Weekly cron (Sunday 21:00) for deep review, or explicit user request"
requires:
  - ~/.hermes/notes/engineering-cybernetics.md
  - ~/.hermes/scripts/cybernetics-review.py
  - ~/.hermes/cron/output/
---

# Engineering Cybernetics Weekly Self-Introspection

> **Purpose**: Execute a systematic weekly self-review based on Qian Xuesen's *Engineering Cybernetics*, applying Ch.11 phase-plane analysis, Ch.17 ultrastability, and Ch.9 power-spectral-density error analysis to Agent behavior.
>
> **Trigger**: Weekly cron job (recommended: Sunday 21:00) or explicit user request.
> **Prerequisites**: `~/.hermes/notes/engineering-cybernetics.md` exists with prior weekly records.

---

## Daily Self-Optimizing Exploration (Ch.15 Extremum Seeking)

> **Purpose**: Execute the daily self-evolution loop based on Ch.15 "自动寻求最优运转点的控制系统".  
> **Control theory basis**: 钱老指出——"不需要精确知道系统的最优工作点，通过连续的感知和测量，系统可以在控制过程中实时发现最优".  
> **Time budget**: 10-15 minutes.  
> **Prerequisites**: `~/.hermes/scripts/self_optimizing_exploration.py` exists and is executable.

### Daily 4-Step Procedure

**Step 1: Get today's exploration direction**
```
terminal: python3 ~/.hermes/scripts/self_optimizing_exploration.py
```
- Read the script output to identify the current week's exploration category and recommended perturbations.
- Categories rotate: Tool使用探索 (Ch.15 极值搜索) → Skill优化探索 (Ch.15 峰值保持) → 工作流程探索 (Ch.17 超稳定) → 跨领域知识迁移 (Ch.12 变系数).

**Step 2: Read a random chapter from the notes**
```
# Use search_files to locate all chapter headings, then read_file on a randomly selected one
search_files: pattern="^## (第.章|Chapter)" path="~/.hermes/notes/engineering-cybernetics.md"
# Count chapters, pick one at random, read its content with read_file
```
- Focus on the "Agent进化映射" section — this is the bridge from theory to practice.
- If the selected chapter is very short, read the next one too.

**Step 3: Attempt at least one perturbation**
Choose one perturbation from the script output (or invent one) and execute it:

| Category | Example Perturbations |
|----------|----------------------|
| Tool使用探索 | Use `execute_code` to batch multiple tool calls; use `browser_vision` instead of `browser_snapshot`; try `delegate_task` for parallel subtasks |
| Skill优化探索 | Randomly pick a skill with `skills_list`, load it with `skill_view`, identify an improvement, patch it with `skill_manage` |
| 工作流程探索 | Change task analysis order (tests-first vs code-first); try a different search strategy; write plan before execution vs direct execution |
| 跨领域知识迁移 | Apply embedded debugging methodology to a web dev task; apply control-theoretic framing to a routine task |

### Step 4: Record the exploration result
Append to `~/.hermes/notes/exploration-log.json`:
```json
{
  "date": "YYYY-MM-DD",
  "category": "<Tool使用/Skill优化/工作流程/跨领域>",
  "perturbation": "<what was tried>",
  "result": "better|same|worse",
  "persisted": true|false,
  "note": "<one-paragraph justification plus key chapter insight>"
}
```

**Note on format**: The `self_optimizing_exploration.py` script manages this JSON log directly. Append to the list array in `exploration-log.json` — the file already has the opening `[` and closing `]`. Add your entry before the last `]`.

### Daily → Weekly Handoff
The weekly review (Step 1 below) reads `exploration-log.json` to aggregate daily perturbations into the Ch.9 power spectral density analysis and Ch.17 ultrastability evaluation. Daily entries are the raw signal; weekly analysis is the filtered trend.

**Pitfall**: Do not skip recording even if the perturbation "failed" — negative results are signal, not noise. Ch.15 requires measuring response in all directions to find the gradient.

---

## Step 1: Data Collection (Parallel Where Possible)

### 1.1 Read the canonical notes file
```
read_file ~/.hermes/notes/engineering-cybernetics.md
```
- Read the full file (it may be >500 lines; use offset to paginate).
- Identify the most recent `Week N` section to understand last week's baseline and unresolved P0-P3 items.

### 1.2 Run the review script
```
terminal: python3 ~/.hermes/scripts/cybernetics-review.py
```
- This produces a template report. Capture its output but **do not rely on it exclusively**—it is a guide, not data.

### 1.3 Gather error patterns from session history
**Critical learning**: Search multiple keyword groups separately and in combination. A single OR query may miss sessions.

**Required searches** (execute in parallel if possible):
1. `error OR fail OR timeout OR bug`
2. `timeout OR blocked OR failure OR crash`
3. `mem0 OR qdrant OR storage`
4. `delegate OR subagent OR retry`
5. `SILENT OR 9499 OR Bad Request OR 429 OR 418`

For each result, note:
- Session ID, date, source (cron/feishu/cli)
- Whether the session was truncated (raw preview unavailable) or summarized
- Key error signatures

### 1.4 Read cron output files for the review period
Locate cron job outputs under `~/.hermes/cron/output/`.
- Identify job IDs active during the review week.
- Read daily/heartbeat reports (`*.md` files) for that period.
- Look for files with error keywords: `ERROR`, `error`, `失败`, `超时`, `timeout`, `fail`, `429`, `418`, `blocked`.

**Pitfall**: `grep -r` across large cron output directories can timeout or be interrupted. Prefer targeted `read_file` on known files, or `tail -n 50` on specific reports.

---

## Step 2: Ch.11 Phase-Plane Analysis — Oscillation Detection

**Framework**: Treat each active task as a trajectory in the phase plane `(e, de/dt)` where:
- `e` = deviation from 100% completion (0 = done, 100 = not started)
- `de/dt` = rate of progress (negative = improving, zero = stalled, positive = regressing)

### Classification rules

| Pattern | Phase-Plane Signature | Control Theory Mapping | Example |
|---------|----------------------|------------------------|---------|
| **Limit cycle** | Closed loop around `(e≈const, de/dt≈0)` | Negative feedback missing or measurement error (false positive) | Agent repeatedly "failing" but marked as success; same outcome every cycle |
| **Damped oscillation** | Spiral converging to origin | Underdamped → critically damped; eventual convergence via trial-and-error | Network access: browser blocked → API blocked → finally Python direct API works |
| **Monotonic convergence** | Straight-line approach to origin | Well-damped or bang-bang optimal | Single fix resolves issue; daily learning with persistent small improvements |
| **Steady-state holding** | Point fixed at origin | System at equilibrium; cron acts as closed-loop monitor | Hourly heartbeat check finds no issues |

### Analysis output format
For each active project/task, produce:
```
**[Color] Pattern #N: <Task Name>**
- State trajectory: <description>
- Oscillation frequency: <time period or count per week>
- Control theory diagnosis: <which chapter/concept explains the behavior>
- Root cause: <specific mechanism>
```

---

## Step 3: Ch.17 Ultrastability Evaluation — Strategy Switching

**Framework**: Ashby's ultrastability requires four components:
1. **Target region** (desired equilibrium) — clearly defined success criteria
2. **Boundary detector** — detects when system leaves target region
3. **Random switcher** — changes parameters when boundary crossed
4. **Stable-mode lock** — retains parameters that avoid boundary crossing

### Evaluation table (produce this in output)
```
| Behavior Pattern | Switch Count | Stable Mode? | Evaluation |
|------------------|-------------|--------------|------------|
| <Task 1> | N | ✅/❌ | <reason> |
```

### Anti-pattern: False-positive locking
**Definition**: The boundary detector incorrectly reports "stable" when the system is actually failing. This stops exploration and creates a **stable limit cycle of inaction**.

**Detection heuristics**:
- Same error signature appears >2 times with no actual progress
- Progress tracking file shows completions that don't match reality
- Agent returns `Timeout` or `Error` but is marked `Success`

**This session's discovery**: False-positive locking is **isomorphic** across domains:
- Week 3: USB CDC test script reports "5/5 pass" while actual hardware still times out
- Week 4: `mark_task_completed()` marks failed agents as complete

**Chapter 9 lesson**: When measurement noise > signal, the system cannot make correct decisions.

---

## Step 4: Ch.9 Power Spectral Density — Error Frequency Analysis

**Framework**: Treat error types as signals with different frequencies. High-power, high-frequency errors are the highest priority.

### Classification table
```
| Error Type | Count | Power (1-5) | Frequency | Trend vs Last Week |
|------------|-------|-------------|-----------|-------------------|
| <Type 1>   | N     | ★★★★★       | High/Low  | New / Same / Decreased |
```

### Power assignment rules
- ★★★★★: Occurs ≥5 times or blocks a major workflow entirely
- ★★★★: Occurs 2-4 times or causes significant delay
- ★★★: Occurs once but has high impact
- ★★: Occurs once, low impact, easily recoverable
- ★: Cosmetic or informational only

### Trend analysis
Compare with prior week:
- **New**: Error did not appear last week → indicates environment/task change (Ch.12 time-varying system)
- **Same**: Error persists → indicates fundamental unresolved issue (stable error mode)
- **Decreased**: Error count dropped → previous fix effective (convergence)
- **Increased**: Error count rose → regression or new trigger

---

## Step 4a: Cross-Week Isomorphic Pattern Detection

**Purpose**: Identify whether this week's error patterns share the same control-theoretic root cause as previous weeks. A single error is a bug; the same pattern across weeks is a systemic failure mode.

**Trigger**: After Step 4 (PSD analysis), before Step 5 (Seven Principles).

**Method**: Compare current week's findings against ALL prior weekly records' error pattern tables:
1. Read all "Week N" sections in `engineering-cybernetics.md` 
2. Build a cross-week matrix: for each error pattern, check if a similar pattern appeared before
3. Classify isomorphic matches:

| Classification | Meaning | Example |
|---------------|---------|---------|
| **同构 (Isomorphic)** | Same root cause in different domains | USB CDC false-positive (Week 3) = Multi-agent false-positive (Week 5) = IOMCU API regression (Week 7) — all are "measurement chain broken" or "modifying A breaks B" |
| **延续 (Continuation)** | Same error, same domain, same project | IMU SPI sensor no-data (Week 6→Week 7) |
| **新 (New)** | First occurrence | MS5611 hardware (Week 7) |
| **已收敛 (Converged)** | Error appeared previously but no longer occurs | Register direct-write coupling (Week 3→Week 4→absent) |

**Output format**:
```
### 跨周同构模式分析
| 本周模式 | 匹配前周 | 同构类型 | 控制论根因 |
|---------|---------|---------|-----------|
| IOMCU回退 | Week 3 USB CDC | 同构 | Ch.5 修A坏B，缺乏交叉验证 |
| IMU无数据 | Week 6 IMU | 延续 | Ch.4 开环未解决 |
```

**Critical heuristic**: If ≥2 patterns are isomorphic with previous weeks, the systemic root cause (not the individual bugs) becomes P0 for next week.

---

## Step 5: Seven Evolution Principles Assessment

Assess the week against the seven core principles from the notes:

1. **Feedback is soul** — Are verification loops present and accurate?
2. **Small steps, fast iteration** — Are changes incremental and quickly validated?
3. **Lyapunov thinking** — Is there a quantified progress metric V(x) with dV/dt < 0?
4. **Adaptive strategy** — Does the system adjust to environmental changes?
5. **Extremum seeking** — Are new methods actively explored?
6. **Noise tolerance** — Can the system function despite uncertainty?
7. **Redundancy & fault tolerance** — Are there fallback paths?

For each principle, rate: ✅ (good), ⚠️ (needs work), ❌ (failing). Include one concrete improvement action per ⚠️/❌.

---

## Step 6: Update Notes File

Append a new `### Week N` section to `~/.hermes/notes/engineering-cybernetics.md` containing:
1. Ch.11 phase-plane analysis (with 🔴🟡🟢 color coding)
2. Ch.17 ultrastability evaluation table
3. Ch.9 error power spectrum table
4. Seven principles assessment
5. P0-P3 improvement plan for next week

**Pitfall**: The file may be large. Use `patch` with `mode=replace` and a unique `old_string` anchor (e.g., the last unchecked item from the previous week's P3 list).

---

## Step 7: Generate Final Report

Produce a concise but complete report containing:
- Executive summary (table of task types, counts, success rates)
- Phase-plane analysis with behavioral classifications
- Ultrastability evaluation
- Error power spectrum
- Seven principles assessment
- Persistent achievements (what was saved/updated)
- Next week's P0-P3 plan

**Delivery rule**: If invoked as cron, output the report directly (do not use `send_message`). The cron system handles delivery.

---

## Known Pitfalls

1. **Terminal grep across cron outputs hangs** — Large `.md` cron output files cause `grep -r` to timeout. Use targeted `read_file` or `tail` instead.
2. **Session search truncation** — Many cron sessions return "Raw preview — summarization unavailable". Cross-check with actual cron output files.
3. **Qdrant/mem0 concurrency errors** — `mem0_conclude` may fail with storage folder locked. Note the failure but do not let it block the analysis.
4. **False-positive locking masquerades as success** — Always verify progress-tracking files against ground truth.
5. **Week-over-week comparison requires reading prior week** — Ensure you read at least the previous week's section in the notes file before assigning trends.

---

## Related Files

- `~/.hermes/notes/engineering-cybernetics.md` — Master learning notes and weekly records
- `~/.hermes/scripts/cybernetics-review.py` — Template generator (run but don't rely on exclusively)
- `~/.hermes/scripts/self_optimizing_exploration.py` — Daily exploration direction generator
- `~/.hermes/scripts/multi_agent_cybernetics.py` — Multi-agent learning tracker (check for false-positive bugs)
- `~/.hermes/cron/output/` — Cron job outputs for detailed error inspection
