---
name: "goap-debug-planner"
description: "GOAP (Goal-Oriented Action Planning) 诊断规划器 — 使用 A* 状态空间搜索自动生成嵌入式调试的最短诊断路径"
---

# GOAP 诊断规划器

## 概述

将 ruflo 的 GOAP（Goal-Oriented Action Planning）思想移植到嵌入式调试领域。遇到 bug 时，用 A* 搜索自动发现**当前状态 → 目标状态**的最短诊断路径，并输出可执行的计划步骤。

## 核心思想

```
当前状态 (Current State) ──[A* 搜索]──→ 目标状态 (Goal State)
       │                                          │
       ▼                                          ▼
  MCU halted at HardFault               USB CDC + MAVLink 心跳
  CFSR=0x00010000                       GYRO/ACCEL healthy
  PC=0x080EE85C                         EKF ATTITUDE 输出
```

**动作** = 每个可执行的诊断/修复步骤，有：
- `preconditions` — 执行前必须满足的条件
- `effects` — 执行后改变的条件
- `cost` — 时间/复杂度代价

A* 搜索 = 从当前状态出发，沿着可能的动作序列，用 `cost + heuristic` 评估优先级，找到最短路径。

## 使用方式

### 方式一：Python 引擎直接调用

```python
from scripts.goap_engine import GOAPPlanner, State, Action

# 1. 定义当前状态
current = State({
    "mcu_halted": True,
    "fault_regs_read": False,
    "fault_pc_known": False,
    "fault_cause_identified": False,
    "fix_applied": False,
    "firmware_compiled": False,
    "firmware_flashed": False,
})

# 2. 定义目标状态
goal = State({
    "usb_cdc_enumerated": True,
    "mavlink_heartbeat": True,
})

# 3. 运行 GOAP
planner = GOAPPlanner()
plan = planner.plan(current, goal)

# 4. 输出诊断计划
for i, step in enumerate(plan, 1):
    print(f"{i}. [{step.cost:.1f}] {step.name} — {step.description}")
```

### 方式二：Hermes Agent 自动调用

```python
from hermes_tools import terminal

# GOAP 引擎内置在 skill 中，可直接调用
result = terminal('python3 /home/llw/.hermes/skills/embedded/goap-debug-planner/scripts/goap_engine.py --current "hardfault" --goal "mavlink_heartbeat"')
print(result['output'])
```

## 内置动作库

### 诊断动作

| 动作 | 前条件 | 效果 | 代价 | 说明 |
|------|--------|------|------|------|
| `halt_mcu` | — | mcu_halted=true | 0.2 | OpenOCD halt MCU |
| `read_cfsr_hfsr` | mcu_halted | fault_regs_read | 0.3 | 读 CFSR/HFSR/MMFAR |
| `read_exception_frame` | mcu_halted | exception_frame_read | 0.3 | 读 SP 处异常帧 |
| `disassemble_fault_pc` | fault_regs_read + mcu_halted | fault_pc_known | 0.5 | addr2line 反汇编 PC |
| `analyze_fault_cause` | fault_pc_known | fault_cause_identified | 0.4 | 分析故障类型 |
| `check_cdc_enumeration` | firmware_flashed | usb_cdc_enumerated | 0.3 | 查 /dev/ttyACM0 |
| `check_mavlink_heartbeat` | usb_cdc_enumerated | mavlink_heartbeat | 0.8 | pyMavlink 接收心跳 |
| `check_imu_health` | mavlink_heartbeat | gyro_healthy, accel_healthy | 0.5 | 读 RAW_IMU 消息 |
| `check_spi_working` | mcu_halted | spi_working | 0.4 | 读 SPI 相关寄存器 |
| `read_setup_stage` | mcu_halted | setup_stage_known | 0.2 | 读 rtt_dbg_setup_stage |

### 修复动作

| 动作 | 前条件 | 效果 | 代价 | 说明 |
|------|--------|------|------|------|
| `apply_fix_from_cause` | fault_cause_identified | fix_applied | 2.0 | 根据根因应用修复 |
| `compile_firmware` | fix_applied | firmware_compiled | 5.0 | scons 编译 |
| `flash_board` | firmware_compiled | firmware_flashed | 3.0 | OpenOCD/pyOCD 烧录 |
| `reset_board` | firmware_flashed | mcu_halted=false | 0.5 | NVIC 热复位 |

## 启发函数（Heuristic）

A* 的启发函数估计从当前状态到目标状态的最小剩余代价：

```python
def heuristic(current: State, goal: State) -> float:
    """估计最小剩余代价"""
    cost = 0.0
    if not current.get("exception_frame_read") and goal.get("exception_frame_read"):
        cost += 0.3  # 至少一次 halt + 读帧
    if not current.get("fault_pc_known") and goal.get("fault_pc_known"):
        cost += 0.5  # 反汇编
    if not current.get("fault_cause_identified") and goal.get("fault_cause_identified"):
        cost += 0.4  # 分析
    if not current.get("fix_applied") and goal.get("fix_applied"):
        cost += 5.0  # 至少一次编译
    if not current.get("firmware_compiled") and goal.get("firmware_compiled"):
        cost += 5.0  # 编译
    if not current.get("firmware_flashed") and goal.get("firmware_flashed"):
        cost += 3.0  # 烧录
    if not current.get("usb_cdc_enumerated") and goal.get("usb_cdc_enumerated"):
        cost += 0.3  # 验证
    # ... 更多
    return cost
```

## 与其他 Skill 集成

- **rtt-stabilization-driver** — GOAP 规划器生成的计划可作为 RTT 稳定性驱动的诊断入口
- **rtt-cuav-v5-flash-verify** — 编译/烧录/验证动作对应烧录验证流程
- **rtt-cuav-v5-spi-fix-record** — SPI 修复相关的动作定义

## 使用场景示例

### 场景 1: HardFault 新 bug

```bash
python3 ~/.hermes/skills/embedded/goap-debug-planner/scripts/goap_engine.py \
  --current "mcu_halted=true" \
  --goal "mavlink_heartbeat=true,usb_cdc_enumerated=true" \
  --verbose
```

输出：
```
═══ GOAP 诊断计划 ═══
路径代价: 11.7
步数: 8

 1. [0.2] halt_mcu
 2. [0.3] read_cfsr_hfsr
 3. [0.3] read_exception_frame
 4. [0.5] disassemble_fault_pc
 5. [0.4] analyze_fault_cause
 6. [5.0] compile_firmware
 7. [3.0] flash_board
 8. [0.8] check_mavlink_heartbeat
═══
```

### 场景 2: SPI 问题诊断

```bash
python3 ~/.hermes/skills/embedded/goap-debug-planner/scripts/goap_engine.py \
  --current "spi_working=false,mcu_halted=true" \
  --goal "spi_working=true,gyro_healthy=true"
```

## 验证

运行测试确认搜索逻辑正确：

```bash
python3 ~/.hermes/skills/embedded/goap-debug-planner/scripts/goap_engine.py --test
```

输出应包含：基础路径搜索、不同场景、无解处理。

## 架构笔记

详细的 A* 设计决策、分支因子优化、启发函数可采纳性分析、与 kanban 集成方式见：

📄 `references/architecture-notes.md`

## 设计来源

本项目是将 **[ruflo](https://github.com/ruvnet/ruflo)** （原名 Claude Flow）的 GOAP（Goal-Oriented Action Planning）概念移植到嵌入式调试领域。ruflo 是一个 98 agent × 32 plugin × 210 MCP tool 的多智能体编排平台，其 GOAP 规划器使用 A* 状态空间搜索进行目标分解。本 skill 移植了其核心算法思想，适配 Hermes Agent + STM32F7 RTT 调试场景。
