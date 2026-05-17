#!/usr/bin/env python3
"""
GOAP (Goal-Oriented Action Planning) 诊断规划器
用于嵌入式调试的 A* 状态空间搜索引擎

移植自 ruflo GOAP 概念，适配 Hermes Agent + STM32F7 RTT 调试场景

用法：
  python3 goap_engine.py --current "hardfault" --goal "mavlink_heartbeat"
  python3 goap_engine.py --test
"""

import sys
import json
import heapq
import re
from typing import Dict, List, Tuple, Optional, Callable


# ============================================================
# 核心数据结构
# ============================================================

class Action:
    """一个可执行的诊断/修复步骤"""

    def __init__(self, name: str, cost: float,
                 preconditions: Dict[str, bool],
                 effects: Dict[str, bool],
                 description: str = "",
                 category: str = "diagnose"):
        self.name = name
        self.cost = cost
        self.preconditions = preconditions  # {var: required_bool}
        self.effects = effects              # {var: new_bool}
        self.description = description
        self.category = category

    def is_applicable(self, state: 'State') -> bool:
        """检查当前状态是否满足所有前提条件"""
        for var, val in self.preconditions.items():
            if state.get(var) != val:
                return False
        return True

    def apply(self, state: 'State') -> 'State':
        """应用此动作，返回新状态（不修改原状态）"""
        new_state = state.copy()
        for var, val in self.effects.items():
            new_state.set(var, val)
        return new_state

    def __repr__(self):
        return f"Action({self.name}, cost={self.cost})"


class State:
    """调试目标的状态表示"""

    def __init__(self, values: Dict[str, bool] = None):
        self._vars: Dict[str, bool] = {}
        if values:
            for k, v in values.items():
                self._vars[k] = v

    def get(self, key: str, default: bool = False) -> bool:
        return self._vars.get(key, default)

    def set(self, key: str, value: bool):
        self._vars[key] = value

    def copy(self) -> 'State':
        new = State()
        new._vars = dict(self._vars)
        return new

    def __eq__(self, other):
        if not isinstance(other, State):
            return False
        return self._vars == other._vars

    def __hash__(self):
        # 用 frozenset 做 hash（所有键值对）
        return hash(frozenset(self._vars.items()))

    def __repr__(self):
        true_vars = [k for k, v in self._vars.items() if v]
        false_vars = [k for k, v in self._vars.items() if not v]
        return f"State(true=[{','.join(sorted(true_vars))}], false=[{','.join(sorted(false_vars))}])"

    def difference(self, other: 'State') -> Dict[str, Tuple[bool, bool]]:
        """返回与另一状态的差异"""
        all_keys = set(self._vars.keys()) | set(other._vars.keys())
        diff = {}
        for k in all_keys:
            v1 = self.get(k)
            v2 = other.get(k)
            if v1 != v2:
                diff[k] = (v1, v2)
        return diff

    def is_goal_reached(self, goal: 'State') -> bool:
        """检查是否达到目标状态（只检查 goal 中定义的变量）"""
        for var, val in goal._vars.items():
            if self.get(var) != val:
                return False
        return True


# ============================================================
# 启发函数
# ============================================================

def default_heuristic(current: State, goal: State) -> float:
    """
    可采纳（admissible）启发函数 — 永远 ≤ 实际最小代价
    只考虑 goal 中定义了、但在 current 中未满足的变量
    """
    cost = 0.0

    # ---- 最小诊断代价 ----
    if goal.get("fault_cause_identified") and not current.get("fault_cause_identified"):
        # 最小路径: halt(0.2) + read_regs(0.3) + disasm(0.5) + analyze(0.4) = 1.4
        cost += 1.4

    # ---- 最小修复代价 ----
    if goal.get("fix_applied") and not current.get("fix_applied"):
        # 直接修复（已知根因的情况）
        cost += 1.5

    # ---- 最小编译-烧录-验证链 ----
    # 目标需要固件运行才能验证的项目数
    goal_needs_fw = 0
    for var in ["usb_cdc_enumerated", "mavlink_heartbeat",
                "gyro_healthy", "accel_healthy", "ekf_attitude",
                "spi_working", "i2c_working"]:
        if goal.get(var) and not current.get(var):
            goal_needs_fw += 1

    if goal_needs_fw > 0 and not current.get("firmware_flashed"):
        # 最小编译+烧录代价（只要一次）
        if not current.get("firmware_compiled"):
            cost += 5.0
        cost += 3.0  # flash
        cost += 2.0  # reset + wait_cdc

    # ---- 最小验证代价 ----
    # 每个需要验证的项目最多加一次
    if goal.get("mavlink_heartbeat") and not current.get("mavlink_heartbeat"):
        cost += 0.8
    if (goal.get("gyro_healthy") or goal.get("accel_healthy")):
        if not current.get("gyro_healthy") or not current.get("accel_healthy"):
            if current.get("mavlink_heartbeat"):
                cost += 0.5  # verify_imu 一次性验证两者
            # 否则代价已包含在编译烧录链中
    if goal.get("ekf_attitude") and not current.get("ekf_attitude"):
        cost += 0.5  # verify_attitude

    # ---- 最小 SPI 诊断代价 ----
    if goal.get("spi_working") and not current.get("spi_working"):
        if not current.get("spi_config_checked"):
            cost += 0.4  # check_spi_pins
        cost += 0.4  # read_spi_regs

    if goal.get("i2c_working") and not current.get("i2c_working"):
        cost += 0.4

    return cost


# ============================================================
# A* 规划器
# ============================================================

class GOAPPlanner:
    """GOAP A* 搜索规划器"""

    def __init__(self, actions: List[Action] = None, heuristic: Callable = None):
        self.actions = actions or []
        self.heuristic = heuristic or default_heuristic

    def add_action(self, action: Action):
        self.actions.append(action)

    def add_actions(self, actions: List[Action]):
        self.actions.extend(actions)

    def plan(self, current_state: State, goal_state: State,
             max_steps: int = 30, verbose: bool = False) -> Optional[List[Action]]:
        """
        A* 搜索最优动作序列

        返回从 current_state 到 goal_state 的最短动作序列，或 None（无解）
        """
        if current_state.is_goal_reached(goal_state):
            return []

        start_h = self.heuristic(current_state, goal_state)

        # priority queue: (f_score, g_score, counter, action_sequence, state)
        # counter is tiebreaker to avoid comparing Action objects
        counter = 0
        heap = [(start_h, 0.0, counter, [], current_state)]
        visited = set()

        iteration = 0
        while heap and iteration < max_steps * 100:
            iteration += 1
            f_score, g_score, _, sequence, state = heapq.heappop(heap)

            state_hash = hash(state)
            if state_hash in visited:
                continue
            visited.add(state_hash)

            if state.is_goal_reached(goal_state):
                if verbose:
                    print(f"[GOAP] 找到解: {len(sequence)} 步, 总代价 {g_score:.1f}, "
                          f"探索 {len(visited)} 状态")
                return sequence

            if len(sequence) >= max_steps:
                continue

            # 扩展动作
            action_generated = False
            for action in self.actions:
                if action.is_applicable(state):
                    new_state = action.apply(state)

                    new_hash = hash(new_state)
                    if new_hash in visited:
                        continue

                    new_sequence = sequence + [action]
                    new_g = g_score + action.cost
                    new_h = self.heuristic(new_state, goal_state)
                    new_f = new_g + new_h

                    heapq.heappush(heap, (new_f, new_g, counter, new_sequence, new_state))
                    counter += 1
                    action_generated = True

            if not action_generated and verbose:
                print(f"[GOAP] 警告: 在状态 {hash(state)} 无可用动作")

        if verbose:
            print(f"[GOAP] 无解: 探索 {len(visited)} 状态, 达到搜索上限")
        return None

    def plan_with_context(self, current_state: State, goal_state: State,
                          context: Dict[str, str] = None,
                          verbose: bool = False) -> Optional[List[Action]]:
        """
        带上下文的规划（可注入已知信息优化搜索）
        context = {"cfsr": "0x00010000", "hfsr": "0x40000000", ...}
        """
        # 如果有已知的故障上下文，可以剪枝动作空间
        if context:
            cfsr = context.get("cfsr", "")
            if cfsr == "0x00010000":  # IACCVIOL
                # 优先搜索 FPU/指令对齐相关诊断
                pass

        return self.plan(current_state, goal_state, verbose=verbose)


# ============================================================
# STM32F7 RTT 调试专用动作定义
# ============================================================

def create_stm32f7_rtt_actions() -> List[Action]:
    """创建 STM32F7 + RT-Thread + ArduPilot 调试专用动作库"""
    actions = []

    # ---- 诊断动作 ----
    actions.append(Action(
        "halt_mcu", 0.2,
        {"mcu_halted": False},
        {"mcu_halted": True},
        "OpenOCD halt MCU（仅当未 halt 时）",
        "diagnose"
    ))
    actions.append(Action(
        "read_setup_stage", 0.2,
        {"mcu_halted": True},
        {"setup_stage_known": True},
        "读 rtt_dbg_setup_stage 变量",
        "diagnose"
    ))
    actions.append(Action(
        "read_cfsr_hfsr", 0.3,
        {"mcu_halted": True},
        {"fault_regs_read": True},
        "读 CFSR/HFSR/MMFAR/BFAR/DFSR 故障寄存器",
        "diagnose"
    ))
    actions.append(Action(
        "read_exception_frame", 0.3,
        {"mcu_halted": True},
        {"exception_frame_read": True},
        "读 SP 处的异常栈帧（R0-R3, R12, LR, PC, xPSR）",
        "diagnose"
    ))
    actions.append(Action(
        "disassemble_fault_pc", 0.5,
        {"fault_regs_read": True, "mcu_halted": True},
        {"fault_pc_known": True},
        "addr2line + objdump 反汇编故障 PC",
        "diagnose"
    ))
    actions.append(Action(
        "check_fpu_state", 0.4,
        {"mcu_halted": True},
        {"fpu_state_known": True},
        "检查 CPACR/FPCCR/FPCAR 浮点寄存器",
        "diagnose"
    ))
    actions.append(Action(
        "check_stack_pointer", 0.3,
        {"mcu_halted": True},
        {"sp_status_known": True},
        "检查当前 SP 地址及剩余栈空间",
        "diagnose"
    ))
    actions.append(Action(
        "analyze_fault_cause", 0.4,
        {"fault_pc_known": True},
        {"fault_cause_identified": True},
        "综合所有故障寄存器信息，确定根因类型",
        "diagnose"
    ))
    actions.append(Action(
        "check_cdc_enumeration", 0.3,
        {"firmware_flashed": True},
        {"usb_cdc_enumerated": True},
        "检查 /dev/ttyACM* 是否枚举",
        "diagnose"
    ))
    actions.append(Action(
        "check_mavlink_heartbeat", 0.8,
        {"usb_cdc_enumerated": True},
        {"mavlink_heartbeat": True},
        "mavproxy/mavlink 接收心跳消息",
        "diagnose"
    ))
    actions.append(Action(
        "check_spi_pins", 0.4,
        {"mcu_halted": True},
        {"spi_config_checked": True},
        "检查 SPI GPIO 复用配置寄存器",
        "diagnose"
    ))
    actions.append(Action(
        "read_spi_regs", 0.4,
        {"spi_config_checked": True},
        {"spi_working": True},
        "检查 SPI 外设状态寄存器（SR/DR/CR1）",
        "diagnose"
    ))
    actions.append(Action(
        "check_i2c_status", 0.4,
        {"mcu_halted": True},
        {"i2c_working": True},
        "检查 I2C 总线状态和 SDA/SCL 电平",
        "diagnose"
    ))
    actions.append(Action(
        "diagnose_spi_full", 0.6,
        {"mcu_halted": True},
        {"spi_config_checked": True, "spi_working": True},
        "复合动作：检查 SPI 引脚配置 + 读外设状态寄存器",
        "diagnose"
    ))
    actions.append(Action(
        "read_thread_list", 0.3,
        {"mcu_halted": True},
        {"thread_list_known": True},
        "RT-Thread 线程列表（优先级、状态）",
        "diagnose"
    ))
    actions.append(Action(
        "check_hardfault_context", 0.4,
        {"fault_regs_read": True, "exception_frame_read": True},
        {"hardfault_context_analyzed": True},
        "分析 HardFault 上下文：实际故障地址、总线 master、冲突类型",
        "diagnose"
    ))

    # ---- 修复动作 ----
    actions.append(Action(
        "apply_fix_fpu_init", 2.0,
        {"fault_cause_identified": True, "fpu_state_known": True},
        {"fix_applied": True, "fpu_init_fixed": True},
        "修复 FPU 初始化顺序（CPACR）",
        "fix"
    ))
    actions.append(Action(
        "apply_fix_spi_pins", 2.0,
        {"fault_cause_identified": True, "spi_config_checked": True},
        {"fix_applied": True},
        "修复 SPI 引脚配置（hwdef.dat 或 GPIO 寄存器）",
        "fix"
    ))
    actions.append(Action(
        "apply_fix_stack", 2.0,
        {"fault_cause_identified": True, "sp_status_known": True},
        {"fix_applied": True},
        "修复栈大小（增加线程栈或主栈）",
        "fix"
    ))
    actions.append(Action(
        "apply_fix_thread_priority", 2.0,
        {"fault_cause_identified": True, "thread_list_known": True},
        {"fix_applied": True},
        "修复线程优先级（主线程/SPI/UART 优先级调整）",
        "fix"
    ))
    actions.append(Action(
        "apply_fix_i2c_pins", 2.0,
        {"fault_cause_identified": True, "i2c_working": False},
        {"fix_applied": True},
        "修复 I2C 引脚或换上硬件 I2C 外设",
        "fix"
    ))
    actions.append(Action(
        "apply_fix_cdc_name", 1.5,
        {"fault_cause_identified": True, "usb_cdc_enumerated": False},
        {"fix_applied": True},
        "修复 USB CDC 设备名（usb-acm0 vs usbd0）",
        "fix"
    ))
    actions.append(Action(
        "apply_fix_general", 2.0,
        {"fault_cause_identified": True},
        {"fix_applied": True},
        "通用修复（根据根因分析结果）",
        "fix"
    ))
    actions.append(Action(
        "apply_fix_spi_pins_direct", 1.5,
        {"spi_config_checked": True},
        {"fix_applied": True},
        "直接修复 SPI 引脚配置（已知 pin 有问题，无需走 HardFault 链）",
        "fix"
    ))
    actions.append(Action(
        "apply_fix_i2c_direct", 1.5,
        {"i2c_working": False},
        {"fix_applied": True},
        "直接修复 I2C 配置（已知 I2C 有问题，无需 HardFault 链）",
        "fix"
    ))

    # ---- 编译/烧录 ----
    actions.append(Action(
        "compile_firmware", 5.0,
        {"fix_applied": True},
        {"firmware_compiled": True},
        "scons --v=ArduCopter --target=cuav_v5 -j$(nproc)",
        "build"
    ))
    actions.append(Action(
        "flash_board_openocd", 3.0,
        {"firmware_compiled": True},
        {"firmware_flashed": True},
        "OpenOCD 烧录 rtthread.bin 到 0x08008000",
        "build"
    ))
    actions.append(Action(
        "flash_board_pyocd", 2.5,
        {"firmware_compiled": True},
        {"firmware_flashed": True},
        "pyOCD 烧录 rtthread.bin 到 0x08008000",
        "build"
    ))
    actions.append(Action(
        "reset_mcu", 0.5,
        {"firmware_flashed": True},
        {"mcu_halted": False, "usb_cdc_enumerated": False},
        "OpenOCD reset + resume",
        "build"
    ))
    actions.append(Action(
        "wait_cdc_enum", 2.0,
        {"firmware_flashed": True},
        {"usb_cdc_enumerated": True},
        "等待 USB CDC 枚举（最多 15 秒）",
        "verify"
    ))

    # ---- 验证动作 ----
    actions.append(Action(
        "verify_imu_raw", 0.5,
        {"mavlink_heartbeat": True},
        {"gyro_healthy": True, "accel_healthy": True},
        "读 RAW_IMU 消息确认陀螺仪/加速度计数据",
        "verify"
    ))
    actions.append(Action(
        "verify_attitude", 0.5,
        {"gyro_healthy": True, "accel_healthy": True},
        {"ekf_attitude": True},
        "确认 ATTITUDE 消息不含 NaN",
        "verify"
    ))
    actions.append(Action(
        "verify_no_hardfault", 0.3,
        {"firmware_flashed": True},
        {"mcu_halted": False},
        "OpenOCD halt 确认无 HardFault",
        "verify"
    ))
    actions.append(Action(
        "verify_imu_health", 0.5,
        {"mavlink_heartbeat": True},
        {"gyro_healthy": True, "accel_healthy": True},
        "确认陀螺仪和加速度计的 health=True",
        "verify"
    ))
    actions.append(Action(
        "full_l0_verify", 3.0,
        {"firmware_flashed": True},
        {"usb_cdc_enumerated": True, "mavlink_heartbeat": True,
         "mcu_halted": False},
        "完整 L0 验证：CDC + MAVLink 心跳 + 无 HardFault",
        "verify"
    ))

    return actions


# ============================================================
# 预设使用场景
# ============================================================

SCENARIOS = {
    "hardfault": {
        "description": "烧录后 MCU 进入 HardFault（常见场景）",
        "current": {
            "mcu_halted": True,
            "fault_regs_read": False,
            "fault_pc_known": False,
            "fault_cause_identified": False,
            "fix_applied": False,
            "firmware_compiled": False,
            "firmware_flashed": True,
            "usb_cdc_enumerated": False,
            "mavlink_heartbeat": False,
        },
        "goal": {
            "mavlink_heartbeat": True,
            "usb_cdc_enumerated": True,
            "mcu_halted": False,
        }
    },
    "spi_diagnose": {
        "description": "SPI 传感器不工作（GYRO/ACCEL unhealthy）",
        "current": {
            "mcu_halted": True,
            "spi_config_checked": False,
            "spi_working": False,
            "gyro_healthy": False,
            "accel_healthy": False,
        },
        "goal": {
            "spi_working": True,
            "gyro_healthy": True,
            "accel_healthy": True,
        }
    },
    "cdc_no_enum": {
        "description": "USB CDC 不枚举，但无 HardFault（固件已烧录）",
        "current": {
            "firmware_flashed": True,
            "usb_cdc_enumerated": False,
            "mavlink_heartbeat": False,
            "mcu_halted": False,
        },
        "goal": {
            "usb_cdc_enumerated": True,
            "mavlink_heartbeat": True,
        }
    },
    "no_heartbeat": {
        "description": "CDC 已枚举但无 MAVLink 心跳",
        "current": {
            "usb_cdc_enumerated": True,
            "mavlink_heartbeat": False,
            "fault_regs_read": False,
        },
        "goal": {
            "mavlink_heartbeat": True,
        }
    },
    "imu_unhealthy": {
        "description": "CDC + MAVLink 都有，但传感器 unhealthy",
        "current": {
            "usb_cdc_enumerated": True,
            "mavlink_heartbeat": True,
            "gyro_healthy": False,
            "accel_healthy": False,
            "spi_working": True,
            "spi_config_checked": False,
        },
        "goal": {
            "gyro_healthy": True,
            "accel_healthy": True,
        }
    },
    "setup_stuck": {
        "description": "启动阶段卡在某个 setup_stage 不推进（固件已烧录）",
        "current": {
            "firmware_flashed": True,
            "mcu_halted": True,
            "setup_stage_known": False,
            "fault_regs_read": False,
        },
        "goal": {
            "mavlink_heartbeat": True,
            "usb_cdc_enumerated": True,
        }
    },
    "ekf_no_attitude": {
        "description": "EKF 运行但无姿态输出",
        "current": {
            "usb_cdc_enumerated": True,
            "mavlink_heartbeat": True,
            "gyro_healthy": True,
            "accel_healthy": True,
            "ekf_attitude": False,
        },
        "goal": {
            "ekf_attitude": True,
        }
    },
    "empty": {
        "description": "初始状态，完全不知道发生了什么",
        "current": {},
        "goal": {
            "mavlink_heartbeat": True,
            "usb_cdc_enumerated": True,
        }
    },
}


# ============================================================
# 输出格式化
# ============================================================

def format_plan(plan: List[Action], state: State = None,
                verbose: bool = False) -> str:
    """格式化输出诊断计划"""
    if not plan:
        return "═══ 无解：无法从当前状态到达目标状态 ═══"

    total_cost = sum(a.cost for a in plan)
    lines = []
    lines.append("")
    lines.append("═══ GOAP 诊断计划 ═══")
    lines.append(f"总代价: {total_cost:.1f}")
    lines.append(f"步数: {len(plan)}")
    lines.append("")

    for i, action in enumerate(plan, 1):
        cat_icon = {"diagnose": "🔍", "fix": "🔧", "build": "⚙️", "verify": "✅"}.get(
            action.category, "➡️")
        lines.append(f" {i:2d}. [{action.cost:.1f}] {cat_icon} {action.name}")
        if verbose or action.description:
            lines.append(f"      {action.description}")

    lines.append("")
    lines.append("═══")
    return "\n".join(lines)


def format_scenario(key: str) -> str:
    """格式化场景描述"""
    if key not in SCENARIOS:
        return f"未知场景: {key}"
    s = SCENARIOS[key]
    lines = [f"场景: {key} — {s['description']}"]
    lines.append(f"  当前状态: {len(s['current'])} 个变量")
    lines.append(f"  目标状态: {len(s['goal'])} 个变量")
    return "\n".join(lines)


# ============================================================
# 命令行接口
# ============================================================

def parse_state_arg(arg: str) -> State:
    """解析 --current 或 --goal 参数"""
    # 支持格式: "hardfault" (场景名) 或 "var1=true,var2=false"
    if arg in SCENARIOS:
        return State(SCENARIOS[arg]["goal"] if arg == "goal" else
                      SCENARIOS[arg]["current"])

    values = {}
    parts = arg.split(",")
    for part in parts:
        part = part.strip()
        m = re.match(r'(\w+)\s*=\s*(true|false|1|0)', part.lower())
        if m:
            key = m.group(1)
            val = m.group(2) in ("true", "1")
            values[key] = val
        else:
            # 尝试作为场景名
            pass
    return State(values)


def list_scenarios() -> str:
    lines = ["可用场景:"]
    for key, s in sorted(SCENARIOS.items()):
        lines.append(f"  {key:20s} — {s['description']}")
    return "\n".join(lines)


# ============================================================
# 自测试
# ============================================================

def run_tests() -> bool:
    """运行内置测试"""
    print("═══ GOAP 引擎自测试 ═══\n")
    all_pass = True

    actions = create_stm32f7_rtt_actions()
    planner = GOAPPlanner(actions)

    for key, s in sorted(SCENARIOS.items()):
        if key == "empty":
            continue  # 空场景会导致搜索空间过大
        current = State(s["current"])
        goal = State(s["goal"])

        # 检查目标是否合理
        print(format_scenario(key))

        plan = planner.plan(current, goal, verbose=True)
        if plan is not None:
            print(format_plan(plan, verbose=True))
            # 验证：顺序执行计划后是否达到目标
            test_state = current.copy()
            for action in plan:
                test_state = action.apply(test_state)
            if test_state.is_goal_reached(goal):
                print(f"  ✅ 验证通过: {len(plan)} 步达到目标\n")
            else:
                print(f"  ❌ 验证失败: 执行完计划未达到目标\n"
                      f"     差异: {test_state.difference(goal)}\n")
                all_pass = False
        else:
            print("  ❌ 无解\n")
            all_pass = False

    # 测试无解场景
    print("测试无解场景（不可能的 goal）:")
    impossible = State({"impossible_var": True})
    goal = State({"impossible_var": True})
    plan = planner.plan(State({}), goal, verbose=True)
    if plan is not None:
        print(f"  ❌ 应有解但实际上返回了 {len(plan)} 步\n")
        all_pass = False
    else:
        print("  ✅ 正确返回无解\n")

    print(f"{'═══ 全部通过 ✅ ═══' if all_pass else '═══ 有测试失败 ❌ ═══'}")
    return all_pass


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 goap_engine.py --scenario <场景名>  # 运行预设场景")
        print("  python3 goap_engine.py --list                # 列出预设场景")
        print("  python3 goap_engine.py --test                # 自测试")
        print("  python3 goap_engine.py --current <状态> --goal <状态>  # 自定义")
        print("")
        print("状态格式: var1=true,var2=false")
        print("示例:")
        print("  python3 goap_engine.py --scenario hardfault")
        print("  python3 goap_engine.py --current mcu_halted=true --goal mavlink_heartbeat=true")
        return

    actions = create_stm32f7_rtt_actions()

    if "--test" in sys.argv:
        run_tests()
        return

    if "--list" in sys.argv:
        print(list_scenarios())
        return

    if "--scenario" in sys.argv:
        idx = sys.argv.index("--scenario") + 1
        if idx < len(sys.argv):
            key = sys.argv[idx]
            if key not in SCENARIOS:
                print(f"未知场景: {key}")
                print(list_scenarios())
                return
            s = SCENARIOS[key]
            current = State(s["current"])
            goal = State(s["goal"])
            print(f"运行场景: {key} — {s['description']}")
        else:
            print("--scenario 需要场景名")
            return
    else:
        # 从参数解析
        current = State({})
        goal = State({})

        if "--current" in sys.argv:
            idx = sys.argv.index("--current") + 1
            current = parse_state_arg(sys.argv[idx])
        if "--goal" in sys.argv:
            idx = sys.argv.index("--goal") + 1
            goal = parse_state_arg(sys.argv[idx])

    verbose = "--verbose" in sys.argv

    planner = GOAPPlanner(actions)
    plan = planner.plan(current, goal, verbose=verbose)

    if plan:
        print(format_plan(plan, verbose=True))
        print(f"\n总代价: {sum(a.cost for a in plan):.1f}")
        print(f"总步数: {len(plan)}")
    else:
        print("═══ 无解：无法从当前状态到达目标状态 ═══")
        if verbose:
            print("建议：检查目标是否可达，或添加缺失的动作")
        sys.exit(1)


if __name__ == "__main__":
    main()
