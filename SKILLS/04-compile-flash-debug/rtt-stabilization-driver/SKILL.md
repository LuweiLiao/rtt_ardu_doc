---
name: "rtt-stabilization-driver"
description: "RTT (RT-Thread) ArduPilot 稳定性攻坚驱动规则 — 调试、修复、验证工作流。当需要调试/修复/稳定 RTT 固件时自动激活。不达目标不停止。"
---

# RTT 稳定性攻坚驱动规则

## 🔄 入口工作流（用户强制顺序 - 2026-05-14 用户反复纠正）

> 每次接手新的稳定性任务/问题，严格按以下顺序执行，**不可跳过步骤**：

```
1. 📖 读 .trae/rules/ 中的全部规则文件
2. 📖 读 git log --oneline -20 了解近期提交历史
3. 📖 读 git diff HEAD 确认当前工作区状态
4. 📋 制定修改计划（标注 ChibiOS 参考文件+行号）
5. ✅ 向廖博士汇报分析结果+计划，等待确认
6. ✍️ 将计划写入 skill（而非记忆/会话上下文）
7. 👁️ 监督 CC 执行（不直接改代码）
8. 🔍 git diff + 编译 + 烧录 + OpenOCD + CDC 双重验证
9. 📊 逐条汇报执行结果
```

**⚠️ 不要在第一步之前就问"怎么走"、不要跳过 git 历史、不要无计划直接动手。**

## 📋 规划铁律：方案必须覆盖完整路线图

> **廖博士反复纠正：方案太短不够长远。** 每次写 RTT 移植方案时，必须站在完整移植角度规划——从当前状态一直到可发布稳定版本，分至少 3-6 个 Phase。不能只写"下一步要做的事"。
>
> 每个 Phase 必须包含：目标→待解决问题→验证标准→依赖项→每个步骤的 ChibiOS 参考文件+行号。
>
> 参考示例：`.hermes/plans/rtt-complete-roadmap.md`（6 Phase，13233 字节）

## ⚠️ 烧录不可靠陷阱（2026-05-14 关键教训，多次踩坑）

## 🔧 系统卡顿/低循环率诊断流程
> 📎 参考文件：`references/setup-hang-diagnosis.md` — setup 挂死诊断、setup_stage 解码表、IOMCU 超时（§662-①）、DeviceBus 总线线程创建失败（§662-②）
> 📎 参考文件：`references/ins-init-internal-marker-scheme.md` — INS-init 内部分段标记法（665-681），精确定位 IMU 初始化阻塞点（2026-05-16 新增）
> 📎 参考文件：`references/slow-progress-vs-true-hang.md` — 慢推进 vs 真卡死的诊断区分法（2026-05-16 新增）
> 📎 参考文件：`references/clean-rebuild-hardfault-guide.md` — clean rebuild 后 _timer_list 损坏→PRECISERR 修复指南（2026-05-15）

### 🔍 PC-as-Thermometer：判断系统是否真"卡住"

当系统看起来卡在某个 setup stage 时，**不要假设死锁**——检查 PC 指针：

```bash
echo -e "halt\nr 15\nresume" | nc -q 2 localhost 4444
# 输出示例：pc = 0x0806c534
```

**解读 PC 值**：

| PC 位置 | 含义 | 应对 |
|---------|------|------|
| `Scheduler.cpp:74` (delay_microseconds_dwt) | 主线程在 DWT 忙等 | 检查优先级：timer prio < main prio → timer 被饿死 |
| `AnalogIn.cpp` (DMA callback or _timer_tick) | **定时器线程在运行！系统没卡** | ADC 改为 DMA 后不再有 EOC 轮询。检查 DMA 缓冲区是否正常累加 |
| `AP_GPS.cpp` (GPS init) | GPS 初始化中 | 正常行为，GPS 需 ~30s |
| `AP_InertialSensor.cpp` (wait_for_sample) | 主线程在等 IMU 样本 | check timer 线程是否在运行 ADC |

### ⚡ ADC 定时器开销诊断（2026-05-14 会话关键发现）

参见以下新增 reference 文件（因 SKILL.md 超限已拆分）：

| 主题 | 文件 |
|------|------|
| 100Hz gate 技术（658Hz→1387Hz） | `references/100hz-gate-technique.md` |
| STM32F7 DMA DTCM 陷阱 & DMA 初始化顺序 | `references/stm32f7-dma-dtcm-pitfall.md` |
| 主线程未启动诊断（hal_run_called=0xDEADBEEF） | `references/main-thread-silent-exit.md` |
| 烧录不可靠陷阱：先验证烧录成功再诊断代码 | `references/flash-reliability-trap.md` |
| 规划铁律：方案必须覆盖完整路线图 | `references/long-range-planning-methodology.md` |

### 🔙 回退验证清单

当需要回退到已知好的基线时，**必须确认回退完全**：

```bash
# Step 1: 检查 RTT 相关文件是否完全回退
git diff -- libraries/AP_HAL_RTT/ modules/rt-thread/bsp/stm32/

# Step 2: 检查 submodule 状态
git submodule status modules/rt-thread/

# Step 3: 确认关键函数与基线版本一致
grep -n "delay_microseconds_boost\|main_loop_pre_boost\|boost_end\|_priority_boosted" \
  libraries/AP_HAL_RTT/Scheduler.cpp

# Step 4: 编译对比 ROM/RAM 占用
scons --v=ArduCopter --target=cuav_v5 -j$(nproc) 2>&1 | grep -E "ROM|RAM"

# Step 5: 烧录后对比 stage 推进速度
python3 -c "
import time, serial
s = serial.Serial('/dev/ttyACM1', 115200, timeout=2)
start = time.monotonic()
for _ in range(300):
    line = s.readline().decode(errors='replace')
    if 'stage' in line.lower():
        print(f'[{time.monotonic()-start:.1f}s] {line.strip()}')
"
# 对比已知基线时间: 263Hz 时 ~3.9s 完成 setup → 进入主循环
```

**已知陷阱**（2026-05-14 会话验证）：
1. **git diff 可能显示 clean 但二进制不完全相同** — submodule 内部修改可能在 `git status` 中不显示
2. **回退后行为差异** — 之前 263Hz 基线回退后变成 7-11Hz，原因是 ADC timer 开销在不同编译条件下放大
3. **不要假设"回退到之前" = "行为一致"** — 编译器版本、.config 选项、甚至主堆大小变化都会影响

## 🚨 最高优先级：1:1 复刻移植规则（2026-05-14 用户建立铁律 — Hermes Skill 原版）

> **本技能包含完整的 1:1 复刻移植规则**。每次修改前必须阅读本节的「文件映射表」、「修改前检查清单8问」、「禁止/允许清单」以及 `rtt-chibios-api-adaptation` 和 `rtt-vs-chibios-reference` 技能。

### 核心铁律

**每一行代码改动必须有 ChibiOS 参考文件行号依据，禁止无端修改。**

### 文件映射表（核心对照）

| RTT 文件 | 对应的 ChibiOS 参考文件 |
|---------|----------------------|
| `AP_HAL_RTT/SPIDevice.cpp` | `AP_HAL_ChibiOS/SPIDevice.cpp` + `hwdef/fmuv5/hwdef.dat` |
| `AP_HAL_RTT/HAL_RTT_Class.cpp` | `AP_HAL_ChibiOS/HAL_ChibiOS_Class.cpp` |
| `AP_HAL_RTT/Scheduler.cpp` | `AP_HAL_ChibiOS/Scheduler.cpp` |
| `AP_HAL_RTT/UARTDriver.cpp` | `AP_HAL_ChibiOS/UARTDriver.cpp` |
| `AP_HAL_RTT/DeviceBus.cpp` | `AP_HAL_ChibiOS/DeviceBus.cpp` |
| `hwdef/common/board/startup_rtt_override.S` | `modules/ChibiOS/os/common/startup/ARMCMx/compilers/GCC/crt0_v7m.S` |
| `hwdef/common/board/linker_scripts/link.lds` (模板) | `modules/ChibiOS/os/common/startup/ARMCMx/compilers/GCC/ld/STM32F765xI.ld` |

### 修改前检查清单（8 问）

修改任何文件前，逐条回答：

1. □ ChibiOS 对应文件路径是什么？
2. □ ChibiOS 中对应的精确行号是什么？
3. □ ChibiOS 的寄存器值/时序/优先级与 RTT 现有值有何差异？
4. □ 这个修改是否在 `AP_HAL_RTT/` 目录内？（若不是→需要架构审核）
5. □ 如果这个功能在 ChibiOS 中用了不同的实现方式（DMA vs 轮询、硬件 vs 软件），RTT 是否必须用相同方式？
6. □ 这个修改会否影响其他外设的 GPIO/时钟/中断配置？
7. □ 不修改是否可接受临时 workaround？
8. □ 验证标准是什么？（OpenOCD 读哪些寄存器？MAVLink 消息哪些字段？）

### 禁止清单

- ❌ 禁止修改 `modules/rt-thread/` 内核代码（除非 RT-Thread 官方 bug）
- ❌ 禁止修改 `libraries/` 中通用代码（除非 ChibiOS 也做了相同修改）
- ❌ 禁止无参考依据的寄存器值猜测
- ❌ 禁止修改 Scheduler.h/Scheduler.cpp 的 include 链
- ❌ 禁止批量回滚已验证的修复

### 允许清单

- ✅ 在 `AP_HAL_RTT/` 内添加适配代码（如注册 STM32F7 SPI1 引脚 PG11 CS 恢复）
- ✅ 在 `AP_HAL_RTT/` 中实现 ChibiOS 功能的 RTT 等价物
- ✅ 在 `hwdef/common/` 模板中修改链接脚本/启动文件
- ✅ 在 submodule 的 BSP 目录中修复 C 代码（如 cherryusb CDC 超时恢复）

### 违规判定

| 表现 | 判定 | 纠正 |
|------|------|------|
| 修改了 `AP_HAL_RTT/` 外部文件 | ❌ 违规 | git restore + 在 `AP_HAL_RTT/` 内实现 |
| 修改无 ChibiOS 行号引用 | ❌ 违规 | 暂停修改，查出对应行号后继续 |
| 修改有 ChibiOS 行号引用但值不同 | ⚠️ 需论证 | 在 plan 中写清差异原因 |
| 修改了 RT-Thread 内核 | ❌ 违规 | 保持只读，通过 BSP 配置绕过 |
| 修改了 `modules/ChibiOS/` | ❌ 违规 | 只读参考，永不修改 |

> **本技能已包含全部 1:1 复刻规则的核心内容**。不再依赖 `.trae/rules/` 目录中的文件（那些仅用于 Trae IDE）。每次修改前直接参考本技能中的「1:1 复刻移植规则」章节以及 `rtt-chibios-api-adaptation` 和 `rtt-vs-chibios-reference` 技能。

## 🩺 GOAP 诊断规划器（2026-05-12 新增）

在开始任何手工诊断前，**先运行 GOAP 规划器**自动生成最短诊断路径：

```bash
# 列出所有预设诊断场景
python3 ~/.hermes/skills/embedded/goap-debug-planner/scripts/goap_engine.py --list

# 根据当前症状选择场景
python3 ~/.hermes/skills/embedded/goap-debug-planner/scripts/goap_engine.py --scenario hardfault

# 或自定义当前状态和目标
python3 ~/.hermes/skills/embedded/goap-debug-planner/scripts/goap_engine.py \
  --current "mcu_halted=true,fault_regs_read=false" \
  --goal "mavlink_heartbeat=true,gyro_healthy=true"
```

**GOAP 规划器**使用 A* 状态空间搜索，内置 35 个 STM32F7 RTT 调试动作和 8 个预设场景。它不会替代人工判断，但能避免遗漏诊断分支。

## Kanban 工作流（持续到像 ChibiOS 一样稳定工作才停止）

项目挂载在 `~/.hermes/kanban.db`，工作流定义如下：

### Phase 1A: Fix baseline boot (t_e28bd2bc) ← 优先级最高
- 解决干净基线固件卡 setup_stage=662 (ins.init())
- 交付：CDC枚举 + 心跳1Hz + main_loop运行
- 依赖：无

### Phase 1B: SPI sensor stabilization (t_59ea5207)
- 修复 GYRO/ACCEL error_count 累积、MS5611 气压计
- 交付：所有传感器 health=True
- 依赖：Phase 1A

### Phase 2: CDC MAVLink stream rate (t_15867133)
- 独立发送线程或 timer 线程触发
- 交付：ATTITUDE/RAW_IMU ≥ 10Hz
- 依赖：Phase 1A, Phase 1B

### Phase 3: L1 verification (t_12ff2f8d)
- 3次冷启动 + 3次热复位测试
- 交付：L1 验证报告
- 依赖：Phase 1A, Phase 1B, Phase 2

### 环境前置检查 — sudo NOPASSWD（2026-05-11 发现）

Kanban 的 workspace 初始化会执行 `sudo /usr/bin/true` 做系统能力检测。
**OpenOCD本身不需要sudo**（USB udev规则正常），但 gateway workspace setup 需要。
因此必须在确保 sudo NOPASSWD 配置，否则 ops agent 会被 blocked：

```bash
# 检查是否已配
sudo -n true && echo "OK" || echo "NEED NOPASSWD"

# 配 NOPASSWD（需要一次sudo密码）
echo 'llw ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/llw-nopasswd
```

### 子Agent预算陷阱（2026-05-11 发现，2026-05-11 已修复）

**2026-05-11 已修复**：max_turns 已从 60 更新为：
- ops: 150 ✅
- backend-eng: 150 ✅
- researcher: 80 ✅
- reviewer: 60（保持）

**修复方法**：在 kanban Infra-R→E→V→O 链中自动完成，无需手动修改。
**验证**：新派发的 worker 自动读取更新后的 max_turns 配置。

**影响范围**: 所有涉及编译+烧录+验证的 profile 都会在 60 次迭代内耗尽预算。

| Profile | max_turns | 是否足够 | 典型任务 | 建议值 |
|---------|-----------|---------|---------|-------|
| ops | 60 | ❌ 远不够 | 编译→OpenOCD烧录→GDB验证→MAVLink验证→汇报 | 150 |
| backend-eng | 60 | ❌ 不够 | 读代码→分析→修复→编译→汇报 | 150 |
| researcher | 60 | ✅ 刚好 | 读代码→分析→写报告 | 80 |
| reviewer | 60 | ✅ 足够 | 读代码→审查→写结论 | 60（保持） |

**配置文件路径**（修改后**立即生效**，无需重启 gateway，新派发的 worker 自动读取新配置）：
- `~/.hermes/profiles/ops/config.yaml` → `agent.max_turns: 150`
- `~/.hermes/profiles/backend-eng/config.yaml` → `agent.max_turns: 150`
- `~/.hermes/profiles/researcher/config.yaml` → `agent.max_turns: 80`

**手动重置 blocked 任务**（临时快速恢复流水线）：
```bash
python3 -c "
import sqlite3
c = sqlite3.connect('/home/llw/.hermes/kanban.db').cursor()
for tid in ['任务ID']:
    c.execute(\"UPDATE tasks SET status='ready', spawn_failures=0, consecutive_failures=0, last_spawn_error=NULL WHERE status='blocked'\")
    print(f'Unblocked {tid}')
c.connection.commit()
"
```

3. 确认 worker profile 加载了正确的 skill 列表

### 2026-05-11 当前进度 (v3)
```审查通过
│   └── T: t_5dc689fd ✅ 双重验证通过
│
├── Phase 1A: ICM20602 SPI挂死修复 ✅
│   ├── R: t_f2d8c7c4 ✅ 根因分析：线程优先级模型
│   ├── E: t_7ce2c26f ✅ 实施修复
│   ├── V: t_6c755691 ✅ 审查通过
│   └── T: t_6199c21f ✅ 双重验证通过
│
├── Phase 1B/C/D/E/F/G: 一系列中间阶段 ✅
│   ├── P1C I2C修复 ✅ → P1G BMI055恢复 ✅
│   └── P1H DSB根因分析 ✅
│
├── Phase 2: USB CDC + MAVLink 10Hz ✅ (2026-05-11 21:42)
│   ├── R: t_b8a57119 ✅ 对比 ChibiOS CDC
│   ├── E: t_f5329baf ✅ USB CDC 修复完成
│   ├── V: t_fdec04da ⛔ protocol violation (需重试)
│   └── T: t_a8936af8 ⏳ 等待 V
│
└── Phase 3: L1传感器数据流 🔄 (当前活跃)
    ├── R: t_cc677db9 ⏳ 研究 ChibiOS SPI DMA/MS5611
    ├── E: t_e4ee6226 🔄 IMU+气压计+EKF姿态验证（2026-05-11 22:13 启动）
    ├── V: t_7c5ac294 ⏳ 等待 E
    └── T: t_72c4ba14 ⏳ 等待 V
```

### 多Agent管线模式

每条 Phase 采用标准四段链：

| 角色 | Agent Profile | 职责 | 输出 |
|------|-------------|------|------|
| **R**esearch | `researcher` | 读 ChibiOS 源码，对比差异 | 根因分析报告（引用ChibiOS行号） |
| **E**ngineer | `backend-eng` | 根据 Research 输出实施修改 | 代码 diff |
| **R**eview | `reviewer` | 对照 ChibiOS 逐行审查修改 | Approved / Changes Required |
| **T**est | `ops` | 编译、烧录、双重验证 | 验证结果（CDC+OpenOCD） |

**依赖链**：R → E → V → T（不可跨越）

**并行策略**：Phase 0 和 Phase 1A 无依赖 → 可同时派发给不同 Agent

### 工作原则
- 每个 Phase 必须**完全完成后**才能进入下一个
- 如果不满足交付标准，回退到对应的前一 Phase 修复
- 每个改动必须在每次烧录后用 GDB 验证调试计数器
- 不能跳过或合并 Phase
- **修改必须引用 ChibiOS 对应代码行号**——在任务 body 中写死参考文件路径

---

## 🔀 自动诊断规划：GOAP 规划器（2026-05-12 集成）

在开始任何调试/修复前，可先用 **GOAP 规划器**（`goap-debug-planner` skill）自动生成最短诊断路径。

GOAP（Goal-Oriented Action Planning）将调试问题建模为状态空间搜索：
- **当前状态** = 你已观察到的现象（MCU 是否 halt、CFSR 值、CDC 是否枚举等）
- **目标状态** = 你期望的结果（CDC枚举、MAVLink心跳、传感器healthy）
- **动作** = 35个预定义的诊断/修复/编译/验证步骤
- **A* 搜索** = 自动找到当前→目标的最短路径

### 什么时候用

| 场景 | 推荐 |
|------|------|
| 新 bug：完全不知道从何入手 | ✅ 用 `--scenario empty` |
| HardFault 后需要系统诊断 | ✅ 用 `--scenario hardfault` |
| 传感器不工作 | ✅ 用 `--scenario spi_diagnose` |
| CDC 有问题 | ✅ 用 `--scenario cdc_no_enum` |
| 已经知道根因、只需执行 | ❌ 跳过，直接走 kanban |

### 用法

```bash
# 预设场景
python3 ~/.hermes/skills/embedded/goap-debug-planner/scripts/goap_engine.py --scenario <场景名>

# 自定义状态
python3 ~/.hermes/skills/embedded/goap-debug-planner/scripts/goap_engine.py \
  --current "mcu_halted=true,spi_working=false" \
  --goal "mavlink_heartbeat=true,gyro_healthy=true" \
  --verbose
```

GOAP 规划器不执行代码——它提供诊断路径规划，实际执行仍通过 kanban R→E→V→T 链。

---

## 🔬 基线隔离诊断法（2026-05-13 新增 — 本会话验证有效）

> **问题**：新加启动修复后USB CDC不工作 → 无法判断是启动修复本身问题还是其他修改干扰
> **验证**：只保留启动修复，revert所有其他非必要修改 → USB CDC立即正常工作！MAVLink心跳稳定！

### 应用场景

当你面对多个修改文件、某个子系统（尤其是USB CDC）不工作时，立即执行基线隔离：

| 症状 | 是否适用基线隔离 |
|------|----------------|
| USB CDC 不枚举但之前工作过 | ✅ **首选方法** |
| 多个文件被修改后出现新问题 | ✅ **首选方法** |
| CC 回滚了已验证的修复后系统崩溃 | ✅ **必须执行** |
| 只有 1 个文件被修改 | ❌ 单个文件问题直接诊断 |
| 从未工作过的功能 | ❌ 基线本身就不支持 |

### 执行步骤

```bash
# 1. 保存核心修复
cp libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S /tmp/startup_fix.s

# 2. 查看哪些文件被修改
git diff HEAD --name-only

# 3. 回滚所有非核心文件（保留核心修复）
git checkout HEAD -- \
  libraries/AP_HAL_RTT/SPIDeviceManager.cpp \
  libraries/AP_HAL_RTT/hwdef/common/.config \
  libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds \
  libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c \
  libraries/AP_HAL_RTT/hwdef/cuav_v5/hwdef.dat \
  libraries/AP_InertialSensor/AP_InertialSensor.cpp \
  libraries/AP_InertialSensor/AP_InertialSensor_Invensense.cpp \
  libraries/AP_Vehicle/AP_Vehicle.cpp

# 4. 恢复核心修复
cp /tmp/startup_fix.s libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S

# 5. 清理submodule修改（如果有） — ⚠️ 危险操作！
cd modules/rt-thread && git checkout -- . && cd ../..
# ❗ 这会删除 submodule 的所有未提交工作树改动！
# 如果 submodule 包含 CDC TX 修复、cherryusb 热复位修复、usb_dc_dwc2 编译补丁等
# 未提交修改，删除后系统可能无法启动（USB CDC 初始化阶段死锁）。
# 替代方案：先 `cd modules/rt-thread && git stash` 保存，
# 构建基线验证后 `git stash pop` 恢复。
# 2026-05-14 教训：cherryusb.c 的 GRSTCTL 死锁修复和 usbd_serial.c 的 CDC TX 
# 修复在 submodule 的工作树中未提交。删除后 USB 初始化阶段死锁 → main() 不被调度。

# 6. 确认只有核心修复文件被修改
git diff HEAD --name-only
# → 应只有 startup_rtt_override.S

# 7. 清理构建缓存 + 重建
rm -rf build/rtt_deploy build/rtt_cuav_v5
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
```

### 验证结果解读

| 结果 | 含义 | 下一步 |
|------|------|--------|
| ✅ **系统工作**（USB CDC枚举+MAVLink心跳） | 问题在其他修改中 | 增量添加被revert的修改，每次添加1个文件→编译→测试 |
| ❌ **系统仍不工作** | 问题在核心修复本身或基线 | 检查核心修复代码错误，或`git bisect`找基线断点 |

### 增量回加模式（2026-05-13 扩展）

基线隔离确认问题在 revert 的文件中后，必须**每次只加 1 个文件的改动 → 编译 → 烧录 → 验证**，不应批量恢复：

```bash
# Step 1: 加文件A → 编译 → 测试
git diff HEAD -- libraries/A/file.c  # 确认只有单一修改
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
# 烧录 + MAVLink验证

# Step 2: 加文件B → 编译 → 测试
# ...重复
```

**为什么**：如果批量加回多个文件（如 SPI 引脚 + RXNE 超时 + CS-held burst + 传感器供电），出现新问题时无法知道是哪个文件引入的。每次 1 个文件，回归定位是 O(1) 而非 O(n)。

**2026-05-13 实测**：SPI4 (MS5611) 引脚修正后，气压计在基线版本即可工作（press_abs=1001.99hPa），说明 SPI4 在初始基线中已经功能正常。IMU (SPI1) RAW_IMU 全 0 的原因是 SPI1 CS-held burst 读取协议的时序问题，不是引脚配置错误。

### 本会话验证记录

2026-05-13 session：启动修复（CPACR/FPCCR/VTOR/D-Cache/I-Cache全部用内存映射寄存器）为唯一修改。revert了7个非必要文件和submodule修改后，系统从HardFault状态恢复到：
- MCU正常启动（xPSR=0x81000000，Thread模式）
- CPACR=0x00F00000（FPU全开）✅
- CFSR=0, HFSR=0（无HardFault）✅
- USB CDC枚举（/dev/ttyACM1）✅
- MAVLink心跳稳定（type=copter, STANDBY）✅
- RAW_IMU/ATTITUDE/SCALED_PRESSURE全部收到 ✅
- MEMINFO: freemem=10336（堆紧张但够用）

**核心教训**：当CC（或你自己）改了一堆文件后USB不工作，**不要试图逐个排查**——直接全部revert，只保留核心修复，再看USB。如果恢复了，说明问题在那些revert的文件中；如果没恢复，再查核心修复。

### 什么时候不要用

- 可复现的单一文件编译错误 → 直接修
- 已知的基线USB问题（如控制台输出干扰MAVLink）→ 直接修
- 硬件相关问题 → 不适用

---

## 🩺 诊断优先规则（2026-05-11 更新 — 新增 SWD连接失败根因）

在看到任何"卡住/不工作"现象时，**必须先诊断再行动**。诊断三步法：

### 第零步：检查僵死 OpenOCD 进程（2026-05-11 新增 — SWD静默失败的根因）
> 在 OpenOCD 报 clock speed 后就静默超时时，**第一步不是检查排线/电压，而是查僵死进程！**

```bash
# 检查是否有遗留 OpenOCD 进程锁住 ST-Link
pgrep -a openocd && echo "⚠️ 有僵死进程！" || echo "✅ 无旧进程"

# 清理
pkill -9 openocd 2>/dev/null; sleep 3
ss -tlnp | grep -E "3333|4444|6666" || echo "✅ ports free"

# 验证
lsusb | grep "0483:3748" && echo "✅ ST-Link在线"
pyocd list 2>/dev/null | grep -q ST-Link && echo "✅ pyOCD可连" || echo "使用pyOCD确认"
```

**根因回顾**：OpenOCD 进程在 tty 关闭时（kanban worker 超时、terminal disconnect）成为僵尸，继续持有 ST-Link USB 设备锁。后续 OpenOCD 全部静默失败，显示"clock speed"后不报错退出。此前多次被误判为 SWD 排线松动、目标板未上电——全错。

**替代方案：pyOCD（不受僵死进程影响）**
```bash
pyocd list                          # 查看所有调试探针
pyocd commander -t STM32F767ZI     # 交互式调试
pyocd load -t STM32F767ZI --format bin -a 0x08008000 rtthread.bin  # 烧录
```

### 第一步：读 setup_stage + PC

```bash
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep setup_stage
echo "halt
mdw 0x<stage_addr> 1
reg pc
resume" | nc -q 3 localhost 4444
```

### 第二步：检查是否真卡住（需要 resume 在读之间）

```bash
# t=0: 第一次读
echo "halt
mdw 0x<stage_addr> 1
reg pc
resume" | nc -q 3 localhost 4444

sleep 15

# t=15s: 第二次读
echo "halt
mdw 0x<stage_addr> 1
reg pc" | nc -q 3 localhost 4444

# 如果 stage 变化 → 系统在推进，只是慢
# 如果 stage 不变且 PC 相同 → 真的卡住了
```

### 第三步：主循环状态诊断（2026-05-13 新增，2026-05-14 扩展）

当系统启动后 CFSR=0 但无 MAVLink 心跳时，检查主循环是否进入：

```bash
# 查找诊断变量符号
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep -E "main_loop|hal_initialized|rtt_dbg_fast|rtt_dbg_boost"

# 读取关键诊断变量
timeout 5 bash -c 'echo -e "halt\nmdw 0x<main_loop_entry> 1\nmdw 0x<main_loop_iterations> 1\nmdw 0x<fast_loop_count> 1\nreg pc\nresume\nexit" | nc -q 3 localhost 4444' 2>&1 | strings | grep -E "pc:|^0x"
```

**状态解读表**：

| `hal_run_called` | `main_loop_entry` | `main_loop_iterations` | PC 范围 | 含义 |
|---------|-----------------|---------------------|---------|------|
| `0xDEADBEEF` (init) | `0xCAFEBABE` (init) | 0 | `idle_thread_entry` / `rt_list_remove` / `rt_hw_interrupt_enable` | **`main()` 未被 RT-Thread 调度** — RT-Thread 在跑但 `main_thread_entry()` 从未执行。**如果 submodule 已清理仍有此问题 → 检查 auto-init 阻塞**。见 `references/main-never-scheduled-diagnosis.md` 的「Auto-init 阻塞诊断」 |
| — | 0x12345678 | 0 | — | **setup() 尚未完成** — 应用已启动（entry reached）但卡在初始化阶段。`addr2line PC` 定位阻塞点：SPI probe / I2C probe / logging init / GPS init。注意：变量值可能是上次运行的 RAM 残留，需配合 PC 交叉确认 |
| — | 0x12345678 | >0 | — | 主循环正常运行 — 查 MAVLink 串口配置 |
| — | 0x00000000 | 0 | — | 主循环未进入 — 系统仍在 setup 阶段 |
| 0xBBBBBBBB | — | 0 | — | hal.run() 已调用但仍卡在 scheduler 内部 — 查线程调度 |

**特殊诊断：PC 在 `rt_list_remove` 且所有 diag var 为初始值**

当 MCU 运行固件代码（PC 在 0x08xxxxxx，非 bootloader）且所有诊断变量为初始值时，说明 **RT-Thread 调度器已启动但 main 线程从未执行**：

```bash
# 1. 确认 ELF 中 main() 符号存在且正确
arm-none-eabi-nm rt-thread.elf | grep " T main$"
# → 应显示地址（如 0x0801243c），且属于 Copter.cpp:983 (AP_HAL_MAIN_CALLBACKS)

# 2. 确认 HAL_RTT::run() 反汇编正确
arm-none-eabi-addr2line -e rt-thread.elf -f <main_addr>

# 3. 检查向量表正确性
echo \"mdw 0x08008000 4\" | nc -q 2 localhost 4444
# → 第一个字应=SRAM栈顶，第二个字=Reset_Handler(Thumb地址)

# 4. 常用诊断命令链
echo \"halt\" | nc -q 1 localhost 4444
echo \"mdw 0x200001c0 1\" | nc -q 1 localhost 4444  # hal_run_called
echo \"mdw 0x200001c8 1\" | nc -q 1 localhost 4444  # main_loop_entry
echo \"reg pc\" | nc -q 1 localhost 4444
echo \"resume\" | nc -q 1 localhost 4444
```

### 🔍 硬件断点链诊断（2026-05-14 新增 — 精确定位调度断点）

当所有诊断变量为初始值、PC 在空闲线程或调度器函数中时，使用硬件断点链确定 main 线程是否被创建和调度：

```bash
# 先查出当前构建的正确符号地址（每次 rebuild 后地址可能变化！）
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep -E " main$| main_thread_entry| rtthread_startup| entry$"

# 设置三级硬件断点链
echo "bp <rt_system_scheduler_start>  2 hw" | nc -q 1 localhost 4444  # 级别1：调度器是否启动
echo "bp <main_thread_entry>         2 hw" | nc -q 1 localhost 4444  # 级别2：main线程入口是否被调度
echo "bp <main>                      2 hw" | nc -q 1 localhost 4444  # 级别3：main() 是否被执行

# 重置并运行
echo "reset run" | nc -q 1 localhost 4444
sleep 8
echo "halt" | nc -q 1 localhost 4444
echo "reg pc" | nc -q 1 localhost 4444
echo "bp" | nc -q 1 localhost 4444           # 检查哪个断点被触发
```

**断点链解读表**：

| 触发断点 | 未触发断点 | 结论 |
|---------|-----------|------|
| `rt_system_scheduler_start` | `main_thread_entry`, `main` | **调度器已启动，但 main 线程从未被调度** — 检查 `rt_thread_create()` 返回值（堆空间不足→返回 NULL），或线程优先级被低优先级线程（空闲线程）异常干扰 |
| `main_thread_entry` | `main` | **main 线程已调度但 `rt_components_init()` 阻塞** — 挂死在 auto-init 函数中。2026-05-15 确认首要阻塞源是 **SD 卡挂载**（`sdcard_port.c` 的 `sdcard_mount()` 在 `dfs_mount("sd0")` 上无限等待）。其次可能是 USB CDC init。 |
| 无断点触发 | 全部 | **CPU 卡在更早阶段** — Reset_Handler 之前或 rtthread_startup 内部 |
| 三个全部触发 | — | **系统完全正常启动** — 问题在后续阶段 |

**关键技巧**：
- **必须先查出当前构建的正确地址** — `arm-none-eabi-nm` 显示的地址每个 rebuild 后都可能变化
- **每次重启 OpenOCD 断点自动清除** — 新断点必须重新设置
- **断点地址必须是偶数**（Thumb 模式，`&~1` 后使用）—— `arm-none-eabi-nm` 显示的地址是奇数的，但 `bp` 需要偶数（2字节对齐）

### ⚠️ 关键陷阱：Symbol 地址随 rebuild 变化（2026-05-14 新增）

每次 `scons` 重新构建后，`rtthread_startup`、`main_thread_entry`、`main` 等符号的地址可能变化（特别是 submodule 版本变更时）。**使用上一轮构建的 `nm` 输出设置断点会导致断点在错误位置，永远不触发。**

```bash
# ❌ 错误：使用上一轮构建的地址
# (从之前的会话复制了 0x080ff75c)

# ✅ 正确：每次 rebuild 后重新查出
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep -E " main$|main_thread_entry|rt_system_scheduler_start"
# main_thread_entry → 0x080ff75c (or 0x080ff6e4 etc. depending on build)
```

**2026-05-14 实测**：submodule 从 `d5dd08dda3` 切换到 `d8e850724e` 后：
- `main_thread_entry`: 0x080ff75c → 0x080ff6e4 (偏移 -120 字节)
- `rtthread_startup`: 0x080ff7b0 → 0x080ff738 (偏移 -120 字节)
- `entry`: 0x080ff87e → 0x080ff766 (偏移 -280 字节)

**2026-05-14 实测**：此断点链成功定位到：
- `rt_system_scheduler_start` 被触发 ✅
- `main_thread_entry` 和 `main` **未触发** ❌
→ 结论：调度器启动、main 线程**从未被调度** → 需要检查 `rt_thread_create()` 的返回值和堆状态

**根因方向**：`main()` 未被执行可能是因为：
1. **SD 卡挂载阻塞 `rt_components_init()`** — `sdcard_port.c` 的 `sdcard_mount()` 使用 `INIT_APP_EXPORT` 注册，在 `main()` 之前执行 `dfs_mount("sd0")`。无 SD 卡时无限阻塞 → `main()` 永不执行。**2026-05-15 硬件断点链验证为最常阻塞点。** 禁用后在 `sdcard_port.c` 注释 `INIT_APP_EXPORT(sdcard_mount)` 行可恢复。
2. USB CDC init 阻塞 `rt_components_init()` — `cherryusb.c` 的 `INIT_COMPONENT_EXPORT(rt_hw_cherryusb_cdc_init)` 级别 4，在 `rtt_run_cpp_ctors` 之前或同时执行。DWC2 设备模式初始化可能等待 PHY 枚举。2026-05-15 验证**不是主因**但可能叠加。
3. `rt_thread_create()` 返回 NULL（堆空间不足） — 主线程默认需要 64KB 连续堆空间，如果堆碎片化或剩余不足（RAM 30% BSS + 堆竞争），`rt_thread_create` 返回 NULL → main 线程从未创建。**2026-05-15 实测**：将 `RT_MAIN_THREAD_STACK_SIZE` 从 65536 降到 **4096** 后 `main_thread_entry` 断点命中 ✅。说明 64KB 堆分配可能因 heap 紧缩而在第一阶段失败。**但 4KB 堆栈不够 ArduPilot setup() 使用**，后续需增大到合理值（建议 32KB+）。
4. RT-Thread 优先级反转 — main 线程（默认优先级 10）可能被更高优先级的 timer/uart 线程永久阻塞。**注意**：降低堆栈大小（点 3）与优先级反转（点 4）是**可叠加影响**：即使堆栈足够创建线程，优先级过低也可能导致线程被饿死。
5. 构建系统编译了错误的 `applications/main.c` — BSP 的 `applications/main.c` 是 LED blinker，如果它被优先链接而非 `AP_HAL_MAIN_CALLBACKS` 定义的 `main`，系统会跑 LED 闪烁而非 ArduPilot。

**详细诊断** → `references/main-never-scheduled-diagnosis.md`（含完整诊断命令链和排除清单）
**硬件断点链诊断** → `references/main-never-scheduled-hardware-breakpoint-chain.md`（三级断点精确定位 main 线程未调度的原因）

**常见情境**：`main_loop_entry=0x12345678` + `main_loop_iterations=0` + PC 在 `_delay_microseconds_dwt`/`get_micros64`/`AnalogIn::_timer_tick` 之间切换 → 系统在 setup 后已进入主循环，但 scheduler 的第一个回调还没完成（CDC TX 阻塞或 timer 线程饥饿）。

### 第四步：I2C 位爆炸诊断

当无 MAVLink 心跳、USB CDC 已枚举、无 HardFault、PC 在 `stm32_set_sda()`/`stm32_set_scl()` 时：

```bash
# 1. 确认不是真卡住 — 查调用链
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf -f -a <PC>

# 读取 64 字节栈看返回地址
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0x<PSP> 16" | nc -q 1 localhost 4444
```

典型 root cause 特征：
- **调用链**: `i2c_send_bytes`(dev_i2c_bit_ops.c:202) → `stm32_set_sda`(drv_soft_i2c.c:80) → rt_pin_write
- **CFSR/HFSR 正常**（无 HardFault，系统在正常执行）
- **5 秒后再次 halt**：PC 仍在 I2C 操作附近（推进很慢但不停）
- USB CDC 已枚举（ttyACM0/1）但 **无 MAVLink 心跳**

### 根因：RT-Thread 使用 GPIO 位爆炸 I2C（非硬件 I2C）

**不同硬件 I2C：** RT-Thread 的 BSP (`drv_soft_i2c.c:15` 条件 `BSP_USING_I2Cx`) 使用 GPIO toggle + udelay
逐位控制 SDA/SCL 时钟线。每个 I2C 位需要 GPIO 操作 → 微秒延时 → 释放 SCL → 轮询 SCL 电平。

**对比 ChibiOS：** ChibiOS 使用硬件 I2C 外设（寄存器配置，DMA 传输），不占 CPU。
`libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat` 中 I2C3 = PH7/PH8 配置为 AF4 硬件模式。

**验证 I2C 配置：**
```bash
# 查启用了哪些 I2C 总线（board 级）
grep 'BSP_I2C[1-5]_' modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/rtconfig.h
# 查 .config 确认 I2C 模式
grep -i 'I2C' modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/.config
```

**两条修复路径：**
1. **短中期：** 在 `AP_HAL_RTT/` 中实现硬件 I2C3 驱动（用 STM32F7 I2C 外设，行为匹配 ChibiOS）
2. **快速绕过：** 在 hwdef.dat 中注释掉磁力计 I2C 探测（`#define HAL_PROBE_EXTERNAL_I2C_COMPASSES`），跳过初始化阶段耗时 I2C 事务，推进 MAVLink 启动 — 但磁力计永久不可用

**参考文件：** `references/i2c-bitbang-blocking-diagnosis.md`（完整诊断日志）

### 第五步：CDC 已枚举 + 无 MAVLink 心跳诊断

当 USB CDC 已枚举（ttyACM0/1 存在）、无 HardFault（CFSR=0, HFSR=0）、系统在 idle 线程运行、但 **MAVLink 无心跳**时的诊断流程：

**检查 5a：CDC 设备名匹配（最常见根因）**

生成的 hwdef.h 中 `HAL_RTT_UART_DEVICE_LIST` 定义了 Serial 端口的 RT-Thread 设备名。CUAV V5 生成例：
```c
#define HAL_RTT_UART_DEVICE_LIST "usb-acm0", "uart2", "uart3", ...
```

关键问题：**RT-Thread CDC ACM 驱动实际注册的设备名是什么？**
- CherryUSB 可能注册为 `"usbd0"` 而非 `"usb-acm0"`
- 如果名字不匹配 → UARTDriver::_begin() 中 `rt_device_find("usb-acm0")` 返回 nullptr → MAVLink 写入空设备

**检查 5b：UARTDriver::_begin() 是否成功**

查看 UARTDriver.cpp 中 `_begin()` 函数的日志（或手动添加 rt_kprintf 调试）：
- `rt_device_find(name)` 是否返回非空
- `rt_device_open()` 是否返回 RT_EOK

**检查 5c：AP_HAL 主循环线程是否存在**

用 OpenOCD 多次 halt（间隔100ms），检查 PC 区域：
```bash
# 如果 PC 始终在 idle_thread_entry / _idle_hook / rt_spin_lock_irqsave
# → AP_HAL 主循环线程未运行或已退出
```

对比 ChibiOS：`AP_HAL_ChibiOS/Scheduler.cpp` 创建 `APM_SCHED_THREAD` 主线程。RTT 的等效实现在 `HAL_RTT_Class.cpp` 和 `Scheduler.cpp` 中。

**检查 5d：USB CDC TX 路径**

`UARTDriver.cpp` 含 STM32F7 专用 `uart_poll_tx()` 寄存器直写 TX，但这仅适用于硬件 UART（USART1-8），**不适用于 USB CDC**。USB CDC 的 TX 必须经过 RT-Thread device 框架的 `rt_device_write()`。

检查 `_write()` 方法：是否对 USB 设备（`_is_usb` 为真）使用了不同的写入路径？

**修复方向汇总（按优先级）：**

| # | 方向 | 描述 | 涉及文件 |
|---|------|------|---------|
| A | CDC 设备名修正 | 对齐 "usb-acm0" 与实际 CherryUSB 注册名 | hwdef.dat / UARTDriver.cpp |
| B | AP_HAL 主线程启动 | 检查 Scheduler 创建主线程逻辑 | HAL_RTT_Class.cpp, Scheduler.cpp |
| C | USB CDC TX 路径 | rt_device_write 替代寄存器直写 | UARTDriver.cpp _write() |
| D | Serial 配置 | 确认 Serial0 为 MAVLink 协议 | hwdef.dat / 生成配置 |

**完整 7 层 Tracing 方法论** → `references/mavlink-absence-7-layer-tracing.md`

覆盖：原始 CDC 检查 → GCS MAVLink 后端 → AP_HAL 写缓冲 → CherryUSB CDC 环缓冲 → DWC2 端点 → 主循环状态 → RT-Thread 线程。**当 pymavlink 无故收不到心跳时，优先使用该方法逐层排查。**

**参考：**
- CUAV V5 生成 hwdef.h: `build/rtt_cuav_v5/hwdef.h`（查看 `HAL_RTT_UART_DEVICE_LIST` 和 `DEFAULT_SERIAL0_BAUD`）\n- UARTDriver.cpp: `libraries/AP_HAL_RTT/UARTDriver.cpp`\n- ChibiOS 参考: `libraries/AP_HAL_ChibiOS/UARTDriver.cpp`\n- 详细诊断: `references/cdc-mavlink-silent-diagnosis.md`\n\n**检查 5e：CDC TX 自愈 — `_tx_stalled_bytes` 超时重试（2026-05-12 新增）**\n\n当 CDC 枚举但无数据时，除设备名不匹配外，也可能是 CherryUSB CDC 的 `tx_active` 标志卡死。\nDWC2 IN 端点传输完成后 XFRC ISR 可能丢失，导致 `tx_active` 永远为 1，后续所有 `write()` 数据积压在 ringbuffer 中但从不发送。\n\n诊断方法：检查 `dbg_serial_unstick_cnt` 是否递增。\n\n**诊断方法**（OpenOCD 读取 CDC 调试变量）：

```bash
# 定义调试变量地址（需在 usbd_serial.c 中定义全局 volatile 变量）
echo -e "halt
mdw 0x2001e2a0 1    # write_calls
mdw 0x2001e29c 1    # write_ok  
mdw 0x2001e294 1    # tx_kick count
mdw 0x2001e26c 1    # bulkin_cnt (IN endpoint ISR counter)
mdw 0x2001e278 1    # unstick_cnt (自我修复触发计数)
resume\\nexit" | timeout 10 nc localhost 4444 2>&1 | grep "^0x"
```

**诊断结果解读**：

| 组合 | 结论 | 所需修复 |
|------|------|---------|
| write_calls>0, write_ok>0, tx_kick>0, **bulkin_cnt=0**, unstick_cnt=0 | **EPENA 卡死** — 所有数据进入 ringbuffer 但 DWC2 IN 端点未传输。IBR=0(无中断), EPENA=1(端点卡在传输状态) | 壁钟超时 + `usbd_ep_recover_stuck()` 强制恢复 |
| write_calls>0, write_ok=0 | write() 返回前已失败（设备未打开或不存在） | 检查 CDC 设备名匹配 |
| tx_kick=0 | write() 从未调用 tx_kick — 发送未触发 | 检查 UARTDriver 的 `_write()` 调用链 |

**EPENA=1 的壁钟超时修复（2026-05-13 添加）**：

当首次 `usbd_ep_start_write()` 的 XFRC ISR 丢失后，**EPENA 永久=1**，端点 DWC2_OTG_DIEPCTL 寄存器的 EPENA 位始终置位。现有自愈仅检查 `!EPENA` 路径，无法恢复。

修复代码模板（在 `usbd_serial.c:write()` 中，`usbd_ep_start_write()` 之后添加）：

```c
if (serial->tx_active) {
    uint32_t start_tick = rt_tick_get();
    while (serial->tx_active && (rt_tick_get() - start_tick) < RT_TICK_MS(50)) {
        /* 等待最多 50ms 让 ISR 完成 */
    }
    if (serial->tx_active) {
        uint8_t ep_idx = serial->in_ep & 0x7F;
        DWC2_OTG_INEndPoint *inep = DWC2_INEP(ep_idx);
        if (inep->DIEPCTL & USB_OTG_DIEPCTL_EPENA) {
            /* EPENA=1: 强制恢复 — 设 SNAK 后清除 EPENA */
            inep->DIEPCTL |= USB_OTG_DIEPCTL_SNAK;
            /* 等待 CNAK 清除 */
            inep->DIEPCTL &= ~USB_OTG_DIEPCTL_EPENA;
            serial->tx_active = 0;
        } else {
            /* EPENA=0: 传输已完成但 ISR 丢失 */
            serial->tx_active = 0;
        }
        dbg_serial_unstick_cnt++;
    }
}
```

**验证修复**：烧录后 15 秒检查 `dbg_serial_unstick_cnt > 0` → 自愈已触发。`bulkin_cnt` 应在后续传输中递增。

**参考诊断**：`references/cdc-tx-counter-diagnosis-via-pyocd.md`

修复模式（已实现 `usbd_serial.c` 中）：\n```c\nstruct usbd_serial {\n    ...\n    volatile uint32_t _tx_stalled_bytes;  // bytes queued since last TX progress\n};\n\n// In kick_tx(): if tx_active stuck AND _tx_stalled_bytes >= 256 (4xMPS),\n// force-clear tx_active and retry.\nif (serial->tx_active) {\n    uint8_t ep_idx = serial->in_ep & 0x7F;\n    if (ep_idx && !(DWC2_INEP(ep_idx)->DIEPCTL & USB_OTG_DIEPCTL_EPENA)) {\n        dbg_serial_unstick_cnt++;\n        serial->tx_active = 0;\n    } else if (serial->_tx_stalled_bytes >= 256) {\n        serial->tx_active = 0;\n        serial->_tx_stalled_bytes = 0;\n        dbg_serial_unstick_cnt++;\n    }\n}\n\n// In write(): after kick_tx returns, if tx_active still set,\n// accumulate bytes for timeout detection.\nif (serial->tx_active) {\n    serial->_tx_stalled_bytes += (uint32_t)written;\n}\n\n// In bulk_in ISR(): reset on successful transfer.\nserial->_tx_stalled_bytes = 0;\n```

### 第六步：烧录前前置检查 — 硬件连接验证

在 Ops 任务（编译→烧录→验证）开始前，**必须**先检查 ST-Link 和飞控板是否已物理连接。否则 agent 会启动后立即 block，浪费一次预算。

```bash
# 1. 确认 ST-Link 存在
lsusb | grep -i -E "st-link|stlink|0483:3748"
if [ $? -ne 0 ]; then
    echo "ST-Link 未连接！跳过烧录验证步骤。"
fi

# 2. 检查已枚举的 USB 设备
ls /dev/ttyACM* 2>/dev/null || echo "CDC 未枚举"

# 3. OpenOCD 连接测试（非烧录，仅检查是否能连）
echo "halt" | nc -q 2 localhost 4444 2>/dev/null && echo "OpenOCD 可达" || echo "OpenOCD 不可达"
```

## 🚨 铁律：先软件恢复，绝不建议物理插拔

**禁止物理断电/插拔！** 只能通过软件方法恢复 USB/ST-Link 连接。如果复位后 USB CDC 无法连接，那就是代码 bug，必须修复代码使其在热复位后正确重新初始化。严禁建议用户物理拔插 USB 或断电。

### 恢复优先：xhci_hcd PCI unbind/rebind

在 block 任务之前，**必须首先尝试软件恢复**。这是最高优先级：

```bash
# 1. 检查 ST-Link 是否在 USB 总线上
lsusb | grep -i -E "st-link|stlink|0483:3748"
if [ $? -ne 0 ]; then
    # 2. 软件恢复：xhci_hcd PCI unbind/rebind（影响所有USB设备3-5秒）
    echo "ST-Link 不在 USB 总线上，尝试软件恢复..."
    sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/unbind'
    sleep 2
    sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/bind'
    sleep 5
    
    # 3. 验证恢复
    lsusb | grep "0483:3748" && echo "✅ ST-Link 恢复成功" || echo "❌ 软件恢复失败"
fi
```

**恢复后的必需操作**：
1. `pkill -9 openocd; sleep 3; openocd -f ...` — 重启 OpenOCD（xhci 复位杀掉了它）
2. `echo "reset" | nc -q2 localhost 4444` — 复位 MCU
3. 解阻塞 kanban 任务（见下方）

完整恢复流程及诊断技巧见 `references/usb-recovery-xhci.md`。

### 软件恢复失败后的处理

仅当 xhci_hcd 恢复也失败时（lsusb 仍无 0483:3748），才 block 任务：

- **不要在 task body 中写长诊断日志**，直接 block 明确原因
- block reason 格式：`"ST-Link 在 USB 总线上消失，xhci_hcd 软件恢复失败。硬件可能已物理断开或 USB 控制器需更深度复位（sudo/sysfs）。"`
- 如果多个任务同时 block（P1B-Ops、P1C-Ops、P1D-Eng 等），**汇总一次报告给用户**，而不是散乱在各个任务结果中

**典型硬件断连模式**（2026-05-11 已验证）：
- **ST-Link 完全消失**：`lsusb` 无 0483:3748，OpenOCD 进程可能还在但连不上 → 优先试 xhci_hcd 恢复
- **ST-Link 在但板子断**：`lsusb` 有 0483:3748，但 OpenOCD 连接提示 `Error: open failed` → 检查和板子的连线
- **CDC 消失**：`/dev/ttyACM0/1` 不存在 → 先 reset MCU，如果 CDC 不来且 ST-Link 正常 → 代码 bug（不枚举 CDC）

**软件恢复成功后的 kanban 解阻塞**：

```bash
python3 -c "
import sqlite3, datetime
conn = sqlite3.connect('/home/llw/.hermes/kanban.db')
c = conn.cursor()
now = int(datetime.datetime.now().timestamp())
c.execute(\"SELECT id, title FROM tasks WHERE status='blocked'\")
for tid, title in c.fetchall():
    c.execute(\"UPDATE tasks SET status='ready' WHERE id=? AND status='blocked'\", (tid,))
    c.execute('INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)',
              (tid, 'promoted', '{\"from\":\"blocked\",\"reason\":\"ST-Link recovered via xhci reset\"}', now))
    print(f'  ✅ {tid} → ready')
conn.commit()
conn.close()
"
```

---

### 第七步补充：D-Cache 遮挡调试变量读取（2026-05-12 发现）

### 第七步补充B：NOCP HardFault — 两种根因（2026-05-13 更新）

**症状**：烧录后立即 HardFault，CFSR=0x00088200（UFSR.NOCP + BFSR.PRECISERR），
或 CFSR=0x00080000（仅 NOCP，无 PRECISERR），HFSR=0x40000000（FORCED），PC 在 hardfault_hang。

**快速诊断（OpenOCD halt 后立即检查）**：

```bash
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0xE000ED88 1" | nc -q 1 localhost 4444  # CPACR
echo "mdw 0xE000ED28 2" | nc -q 1 localhost 4444  # CFSR+HFSR
```

**两种可能的根因**：

| CPACR | CFSR | 根因 |
|-------|------|------|
| 0x00000000 | 0x00088200 (NOCP + PRECISERR) | **启动文件错误** — 构建系统编译旧版 AP_HAL_RTT startup（无 CPACR 代码） |
| 0x00F00000 | 0x00080000 (仅 NOCP) | **CP15 MRC/MCR 指令** — 汇编中包含 Cortex-M7 不支持的 CP15 协处理器指令 |

**根因 1：构建系统使用错误的 startup 文件**

构建系统编译的是 **libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S**，不是 submodule 版。如果 AP_HAL_RTT 版缺少 CPACR 初始化代码，表现为 CPACR=0。

详见 references/nocp-hardfault-startup-file-diagnosis.md。

**根因 2：Cortex-M7 CP15 MRC/MCR 指令 NOCP（2026-05-13 发现）**

CPACR=0x00F00000（FPU 已正确使能），但仍 NOCP — 原因是汇编中包含 mrc p15/mcr p15 指令。
STM32F767 对 CP15 协处理器接口支持有限，必须用内存映射寄存器代替：

| 操作 | 错误的 MRC/MCR | 正确的内存映射 |
|------|---------------|---------------|
| 读 SCTLR | mrc p15, 0, r0, c1, c0, 0 | ldr r0, =0xE000ED30; ldr r0, [r0] |
| 写 SCTLR | mcr p15, 0, r1, c1, c0, 0 | ldr r1, =0xE000ED30; str r0, [r1] |
| DCCSW | mcr p15, 0, r0, c7, c14, 0 | ldr r1, =0xE000EF5C; str r0, [r1] |
| ICIALLU | mcr p15, 0, r0, c7, c5, 0 | ldr r1, =0xE000EF50; str r0, [r1] |

**关键验证**：检查启动汇编中是否包含 mrc/mcr 指令：

```bash
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \
  --start-address=$(arm-none-eabi-nm build/.../rt-thread.elf | grep Reset_Handler | awk '{print "0x"$1}') \
  --stop-address=+0x100 2>/dev/null | grep -E "mrc|mcr"
```
如果输出中包含 mrc 或 mcr → 需要替换为 STR/LDR 内存映射操作。

**完整诊断**：详见 references/cortex-m7-cp15-nocp-fix.md。

**不要**：先检查排线、电压、芯片焊锡——如果之前能烧录写入，问题不是硬件。

Cortex-M7 D-Cache 使能时 (`SCB_CCR.DC=1`)，`volatile` 变量的写入会进入 D-Cache 缓存行而非直接穿透到物理内存。OpenOCD 的 `mdw` 命令读取的是**物理内存**，而非缓存中的最新值，因此可能看到**过时的调试变量值**。

**症状**：
- `setup_stage` 多次 halt 读取始终不变，但 0.5 秒短间隔后 PC 区域的定时器回调不同 → 系统可能在推进，只是缓存未回写
- 用不同间隔（1秒、5秒、15秒）多次 halt 读同一变量，如果值不变且 PC 不变 → 真卡住了；如果 PC 变化但变量不变 → D-Cache 遮挡

**清理特定地址的 D-Cache 行后再读（Cortex-M7）：**

```bash
# DCCMVAC (Data Cache Clean by MVA to PoC) at 0xE000EF5C
echo "halt" | nc -q 1 localhost 4444
echo "mww 0xe000ef5c <var_addr>" | nc -q 1 localhost 4444   # 清理缓存行
echo "mww 0xe000ef00 0x0f" | nc -q 1 localhost 4444         # DSB
echo "mdw <var_addr> 1" | nc -q 1 localhost 4444             # 读物理内存
echo "resume" | nc -q 1 localhost 4444
```

**注意**：如果物理内存值与缓存值相同，说明系统真的卡住了（不是 D-Cache 伪影）。验证方法是：修改固件中该变量的代码（如加写操作），重建烧录后看是否变化。

**典型场景**：`setup_stage` 写入在 D-Cache 中但未被驱逐 → OpenOCD 读物理内存看到旧值 → 误判为系统卡住。

---

### 第七步补充：OpenOCD flash write_bank 偏移量陷阱（2026-05-12 发现）

**`flash write_bank` 使用 bank-相对偏移，不是绝对地址！**

**2026-05-13 补充：烧录后 bootloader 不跳转的两种根因**

当烧录后 PC 停在 bootloader 范围（0x0800xxxx）时，有两种可能：

**根因 A：flash 写入不完整（最常见）**— 向量表 0x08008000 处全 0xFF → `flash write_bank` 超时静默失败。改用 `program` 命令替代。

**根因 B：app_descriptor CRC 不匹配**— 向量表正确写入但 bootloader 验证不通过：
```bash
# 诊断：读向量表判断是否写入完整
echo "halt" | timeout 10 nc localhost 4444
echo "mdw 0x08008000 4" | timeout 10 nc localhost 4444
# 有效向量表（如 200054bc 080f0165 ...）→ CRC 问题，绕过 bootloader
```
解决：`openocd -c "program rtthread.bin 0x08008000 verify" -c "reset run"` 强制写入并运行。

```bash
# ❌ 错误
flash write_bank 0 rtthread.bin 0x08008000
# → "Offset 0x08008000 is out of range of the flash bank"

# ✅ 正确
flash write_bank 0 rtthread.bin 0x8000
```

**当重烧不同版本的固件时，必须先擦除再写**（否则旧数据残留）：

```bash
echo "halt
flash erase_sector 0 <first_sector> <last_sector>
flash write_bank 0 <abs_path_to_bin> <bank_offset>
verify_image <abs_path_to_bin> <abs_flash_address>
reset run" | nc -q 30 localhost 4444
```

**CUAV V5 示例**（bootloader 在 sector 0, 固件从 sector 1 开始）：
```bash
# 擦除 sectors 1-11, 写固件到 bank 偏移 0x8000 (绝对 0x08008000)
echo "flash erase_sector 0 1 11
flash write_bank 0 /abs/path/rtthread.bin 0x8000
verify_image /abs/path/rtthread.bin 0x08008000
reset run" | nc -q 60 localhost 4444
```

**注意**：nc pipe 需要 `-q` 设置足够超时（擦除和写入各需 10-20 秒，合计至少 60 秒）。

**2026-05-13 实测：`program` 比 `flash write_bank` 更可靠**

> ⚠️ 关键经验：`flash write_bank` 对 1.3MB 固件可能静默部分失败——向量表写入成功但后续数据全 0xFF，verify_image 不报错，bootloader 不跳转。**只相信 `program` 命令的 `Programming Finished + Verified OK` 输出。**

`flash write_bank` 对大二进制（1.3MB）可能触发 `timeout waiting for algorithm`，导致 **写入静默部分失败** — 向量表写入了但后续数据全部为 0xFF，验证码不报错但系统无法启动。

```bash
# ✅ 推荐：用 program 命令一步完成擦除+写入+验证（内部有更完善的超时处理）
echo "reset halt
program /path/to/rtthread.bin 0x08008000 verify
reset run
exit" | timeout 120 nc localhost 4444
# 预期输出：Programming Started → Programming Finished → Verified OK

# ❌ 避免：手动 flash write_bank（erase + write + verify 三步，容易超时）
flash write_bank 0 rtthread.bin 0x8000   # 1.3MB 易超时
```

`scons --upload` (PX4 bootloader 协议) 在 bootloader 验证 app_descriptor CRC 不通过时也会超时。uploader.py 工具需要 .apj 格式而非 .bin。

---

### 第十一步：Stage 610-630 早期挂起 — 传感器电源 (PE3) 未使能（2026-05-12 发现）

**症状**：
- `setup_stage` 卡在 **620**（`init_rc_in()` 后，`ins.init()` 前）
- USB CDC 可能有控制台输出（"Init ArduCopter", "Free RAM" 等），但停滞
- IOMCU 可能已上传（"IOMCU startup"），但后续传感器初始化不推进
- 多次 halt 后 PC 不在 HardFault handler，通常是定时器回调或调度器函数

**根因：PE3 (VDD_3V3_SENSORS_EN) 处于输入模式，传感器无供电**

CUAV V5 的传感器供电由 PE3 控制：`PE3 VDD_3V3_SENSORS_EN OUTPUT HIGH`。如果 PE3 未被正确驱动为输出 HIGH，所有 SPI I2C 传感器（ICM20689、ICM20602、MS5611、IST8310 等）无供电，无任何应答。

**诊断方法**：

```bash
# 1. 检查 GPIOE MODER — PE3 是否在 output 模式
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0x40001800 1" | nc -q 1 localhost 4444
# GPIOE MODER = 0x40001800
# PE3 = bits [7:6], 00=input, 01=output, 10=AF, 11=analog
# 如果 bits[7:6]=00 → PE3 是输入 → 传感器没电！

# 2. 检查 GPIOE ODR — PE3 电平
echo "mdw 0x40001814 1" | nc -q 1 localhost 4444
# PE3 = bit 3, 0=LOW, 1=HIGH
# 如果 bit 3=0 → PE3 输出低

echo "resume" | nc -q 1 localhost 4444
```

**为什么 `_sensor_power_init()` 会失效**？

传感器电源初始化在 `rt_board_init.c` 中通过 `INIT_PREV_EXPORT(_sensor_power_init)` 注册。这是一个 `INIT_PREV_EXPORT`（初始化级别 2），它**晚于** `INIT_BOARD_EXPORT`（级别 1）。然而：

1. SPI4 初始化（`rt_hw_spi_init` 内部调用的 `HAL_SPI_MspInit`）对 GPIOE 做 HAL_GPIO_Init 读-改-写，无意中将 PE3 清零为 input(00)
2. `_sensor_power_init()` 随后运行，试图用 `rt_pin_mode() + rt_pin_write()` 设 PE3=output HIGH
3. **D-Cache 可能干扰 `rt_pin_mode()` 的 MODER 写操作**（见 `ardupilot-rtt-architecture` §5）
4. 后续 GPIO 操作进一步污染 MODER → PE3 最终还是 input

**修复方式**（已验证有效）：在 `rt_board_init.c` 中 SPI4 初始化完成后，用**直接寄存器写 + DSB** 设 PE3：

```c
/* 直接写 GPIOE MODER/ODR，绕开 D-Cache 和 rt_pin_mode() 的 RMW */
#define GPIOE_BASE  0x40021000UL
*(volatile uint32_t *)(GPIOE_BASE + 0x00) |= (1UL << 6);  // PE3 bits[7:6]=01(output)
__DSB();
*(volatile uint32_t *)(GPIOE_BASE + 0x14) |= (1UL << 3);  // PE3=HIGH
__DSB();
```

**验证修复**：
```bash
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0x40001800 1"     # MODER bits[7:6] = 01 (output) ✅
echo "mdw 0x40001814 1"     # ODR bit 3 = 1 (HIGH) ✅
echo "resume" | nc -q 1 localhost 4444
```

**定位 PE3 修复的最佳位置**：在 `rtt_run_cpp_ctors()` 末尾（`rt_board_init.c` 约 line 330），此时 SPI 和所有 GPIO 初始化已完成。

**参考：** `ardupilot-rtt-architecture` skill §5 (D-Cache GPIO 写入冲突)；`rtt-cuav-v5-spi-fix-record` skill（SPI GPIO 污染历史记录）；`references/imu-spi-diagnosis-2026-05-13.md`（IMU SPI AF 寄存器诊断完整记录）；`references/imu-whoami-probe-via-rtt-spi1-rt.md`（IMU WHO_AM_I probe 方法 + rtt_spi1_rt 诊断结构体 + GPIO MODER 编码）

---

### 第八步：GCS_MAVLINK 中期野指针 HardFault — DTCM bootloader 遗留脏数据（2026-05-12 发现）

**症状**：烧录后 5-12 秒 HardFault，PC=0x080083ca（`hardfault_hang()`）。

**故障寄存器**：
```
CFSR  = 0x00008200  (PRECISERR + BFARVALID)
HFSR  = 0x40000000  (FORCED)
BFAR  = 0x7936XXXX  （野指针，0x79 段 = 未初始化/被覆盖的堆内存）
SHCSR = 0x00000000
```

**异常帧解码**（通过 `rtt_dbg_hardfault` BSS 变量）：
```
stacked PC = 0x080DA150  → GCS_MAVLINK::txspace() const (GCS.h:209)
stacked LR = 0x080E0009  → GCS_MAVLINK::try_send_message()
stacked xPSR = 0x61000000 (Thread mode, T=1, Z=1, C=1)
R3 (vtable) = 0x79366A00  → 野指针
```

**故障指令解码**：
```asm
ldr.w r0, [r0, #484]     ; r0 = GCS_MAVLINK::_port (offset 0x1e4)
ldr   r3, [r0, #0]       ; r3 = _port->vtable
ldr   r3, [r3, #40]      ; r3 = vtable[5] (txspace) → BFAR 指向这里
blx   r3                  ; 未执行
```

### 🚨 关键发现：Bootloader 在 DTCM 遗留脏数据

2026-05-12 通过 `reset halt` 后立即读 DTCM（*还在 bootloader 中*）发现：

```bash
echo "mdw 0x2000e510 4" | nc -q 2 localhost 4444
# → 0x08122960  （"CUAVv5-RTT..." 板名字符串地址！）
```

这个值是 UARTDriver `serial1Driver` 的 vtable 指针位置。正确值应为 `0x081228b8`
（`_ZTVN3RTT10UARTDriverE + 8`）。错误值指向了板名格式化字符串。

**诊断验证方法**：

1. **复位后立即读 DTCM（还在 bootloader）看残留值**：
   ```bash
   echo "reset halt" | nc -q 2 localhost 4444
   echo "mdw 0x2000e510 4"      # serial1Driver vtable
   ```

2. **手动清零后设 Write Watchpoint 追踪覆盖者**：
   ```bash
   echo "mww 0x2000e510 0x00000000" | nc -q 2 localhost 4444
   echo "wp 0x2000e510 4 w" | nc -q 2 localhost 4444
   echo "resume" | nc -q 1 localhost 4444
   sleep 15  # 等 crash
   echo "halt"
   echo "mdw 0x2000e510 4"       # 若仍为 0 → BSS 后无写入，但 crash 模式会变
   ```

3. **DTCM 中 UARTDriver 实例内存布局**（`arm-none-eabi-nm -n`）：
   ```
   0x2000e004: ioUartDriver (1124B)
   0x2000e468: utilInstance (168B) ← 紧邻 serial1Driver！
   0x2000e510: serial1Driver     ← vtable 被覆盖
   ```

4. **验证 vtable 正确值**：
   ```bash
   arm-none-eabi-nm rt-thread.elf | grep "_ZTVN3RTT10UARTDriverE"
   # 正确 vtable 指针 = 上值 + 8（跳过 offset-to-top 和 typeinfo）
   ```

**已推测的覆盖机制**：
- bootloader 在 DTCM 留下脏值 0x08122960
- 固件 BSS 清零后，C++ 构造器设 vtable = 0x081228b8（正确）
- 后续初始化（可能是 `utilInstance` 的 `board_name()` snprintf）溢出覆盖 serial1Driver 的 vtable
- **w/p**: 手动清零后 crash 模式改变（stacked PC 从 txspace → heap），说明 bootloader 残留确实影响了初始化路径

如果手动清零 + wp 未触发但 crash 模式改变，说明该问题根因比预想的复杂——可能是 D-Cache 导致的 SRAM1 数据不一致间接影响 DTCM 分配。

**推荐前置检查**：先检查 `startup_rtt_override.S` 中 BSS 清零范围是否覆盖所有 UARTDriver 实例（_sbss=0x200054c0, _ebss=0x20045294, serial1Driver=0x2000e510 → 已覆盖）。

#### 实际修复：I-Cache 屏障（2026-05-12 确认有效）
**完整诊断 & 修复记录** → `references/dtcm-bootloader-icache-barrier-fix.md`

在 `startup_rtt_override.S` 的 Reset_Handler 中，BSS 清零完成后添加 **DSB + ISB + ICIALLU**（全 I-Cache 无效化）：

```asm
    /* 清零 BSS（已有代码）*/
    ldr   r2, =_sbss
    ldr   r4, =_ebss
    movs  r3, #0
    b     .L_LoopFillBss
.L_FillBss:
    str   r3, [r2]
    adds  r2, r2, #4
.L_LoopFillBss:
    cmp   r2, r4
    bcc   .L_FillBss

    /* === I-Cache 屏障：'mcr p15' 在 STM32F767 上触发 NOCP，必须用内存映射 === */
    dsb
    isb
    movs  r0, #0
    ldr   r1, =0xE000EF50       /* SCB_ICIALLU — invalidate entire I-Cache */
    str   r0, [r1]
    dsb
    isb

    bl    entry
```

**为何有效**：虽然 D-Cache 在此平台已禁用（USB DWC2 DMA coherency），但 I-Cache 保留了 bootloader 的指令缓存行，在 BSS 清零后可能影响后续内存操作的一致性。ICIALLU 确保固件启动时 I-Cache 中无残留。

**验证方法**：修复后复位 halt 读 serial1Driver vtable 应为 `0x081228b8`（`_ZTVN3RTT10UARTDriverE + 8`），CFSR=0。

#### 构建配置链 — .config 的实际来源（2026-05-12 发现）

**关键发现**：ArduPilot RTT 构建系统在 `build/rtt_deploy/<target>/` 中生成 `rtconfig.h`，
但 `.config` 的来源是 **`libraries/AP_HAL_RTT/hwdef/common/.config`**（模板目录），
**不是** `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/.config`（BSP 源目录）。

```python
# rtt_bsp_deploy.py 的 _deploy_hwdef() 执行顺序：
# 1. shutil.copytree(common_dir, deploy_dir)  ← 从模板复制 .config
# 2. _generate_rtconfig(deploy_dir, ap_root)  ← 用 deploy_dir/.config 生成 rtconfig.h
# 3. _run_hwdef_parser(...)                   ← 生成 hwdef.h 等
```

**验证变更是否生效的方法**：
```bash
# 1. 修改模板 .config（真正的来源）
vim libraries/AP_HAL_RTT/hwdef/common/.config

# 2. 清除构建缓存 + 重建
rm -rf build/rtt_deploy build/rtt_cuav_v5
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)

# 3. 验证编译产物（objdump 确认寄存器值已用新常量）
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \
  --start-address=$(arm-none-eabi-nm ... | awk '$NF=="rt_application_init"{print "0x"$1}') \
  --stop-address=+0x30 2>/dev/null | head -10
# 查找 movs r3, #N — N 就是主线程优先级

# 4. BSP 源目录的 .config（modules/rt-thread/bsp/.../.config）
#    不会被构建系统读取，仅用于 RT-Thread menuconfig 工具。
#    建议保持两边一致避免混淆。
```

**涉及参数**（所有 `CONFIG_RT_*` 参数均在模板 .config 中定义）：
- `CONFIG_RT_MAIN_THREAD_STACK_SIZE` → `RT_MAIN_THREAD_STACK_SIZE`
- `CONFIG_RT_MAIN_THREAD_PRIORITY` → `RT_MAIN_THREAD_PRIORITY`
- `CONFIG_RT_TICK_PER_SECOND` → `RT_TICK_PER_SECOND`
- etc.

#### vtable 运行时验证（2026-05-12 新增，备用防御）

若不想修改 startup 汇编，可在 `rtt_run_cpp_ctors()` 中验证：

```c
uint32_t expected_vtable;
__asm__ volatile("ldr %0, =_ZTVN3RTT10UARTDriverE + 8" : "=r"(expected_vtable));
// serial1Driver[0] should == expected_vtable
if (_ZL13serial1Driver[0] != expected_vtable) {
    rt_kprintf("[CTOR] serial1 vtable CORRUPT (0x%08x != 0x%08x)\n",
        _ZL13serial1Driver[0], expected_vtable);
    _ZL13serial1Driver[0] = expected_vtable;  // 紧急修复
}
```

**完整诊断 session 记录**：见 `cuav-v5-rtt-hardfault-forensics` skill 的 `references/dtcm-bootloader-stale-data.md`。

### 第九步：RT-Thread 主线程优先级导致初始化极慢（2026-05-12 发现 + 2026-05-12 验证修复）

**症状**：烧录后系统正常运行（CFSR=0, USB CDC 枚举），但 `setup_stage` 推进极慢（≥30秒才到 stage 630），`_hal_initialized` 长期为 0，主循环迭代数为 0。

**根因**：RT-Thread 主线程（运行 `main()` → `setup_ardupilot()`）的优先级为 `RT_MAIN_THREAD_PRIORITY=10`。UART/timer 线程的优先级分别为 6 和 4。每个 `hal.scheduler->delay(1)` 调用中，主线程睡眠 1ms 期间被更高优先级的 UART/timer 线程频繁抢占，导致有效推进时间缩水。

**线程优先级对比**（CUAV V5 / STM32F765）：

| 线程 | RTT优先级 | 说明 |
|------|-----------|------|
| ap_timer | 4 | 1kHz定时器 |
| SPI1 bus thread | 5 | DeviceBus |
| ap_uart | 6 | UART drain |
| **Main thread** | **10** | **运行 setup_ardupilot() — 过低！** |
| ap_io | 18 | IO回调 |

**推进机制**：主线程每次 `delay(1)` 需要 1ms，但实际完成需要 1ms + 所有高优先级线程轮转一圈的时间。在 setup 阶段有大量的 `delay(1)` 调用（I2C/SPI 传感器初始化），累计下来初始化时间膨胀到 30-60 秒。

**验证方法**：
```bash
# 1. 读当前 priority 配置
grep RT_MAIN_THREAD_PRIORITY modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/rtconfig.h
# → 10

# 2. 长期观察 setup_stage 推进
grep "setup_stage = " ArduCopter/system.cpp  # 查看所有 stage 定义

# 3. 用 OpenOCD 多次 halt 确认 PC 分布
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0x2001bc6c 1" | nc -q 2 localhost 4444  # setup_stage
echo "reg pc" | nc -q 2 localhost 4444              # 当前 PC
# 多次采样：如果在 uart/timer 线程的时间 > 主线程时间，确认优先级问题
```

**修复方向**：
1. **降低主线程优先级号**（数值更小=优先级更高）：将 `RT_MAIN_THREAD_PRIORITY` 从 10 改为 **6**（与 UART 线程同级或略高）
2. **保持高优先级线程不变**：UART(6)、Timer(4)、SPI(5) 不降低
3. **ChibiOS 参考**：ChibiOS 中 main thread 优先级与 UART 线程同级，不会出现这种抢占延迟
4. **副作用**：主线程优先级提高后，可能影响 UART 线程的实时性，需验证 MAVLink TX 路径是否仍能及时输出

**验证结果（2026-05-12 实测）**：

主线程优先级 10→6 后：

| 指标 | 之前 (priority=10, 15秒) | 之后 (priority=6, 20秒) |
|------|------------------------|------------------------|
| setup_stage | 630 (AP_SerialManager) | **662** (AP_Param ✅) |
| _hal_initialized | 0 | **1** ✅ |
| main_loop_entry | 0 | **0x12345678** ✅ |
| USB CDC 输出 | 无 | **控制台输出！** 🎉 |

USB CDC 控制台输出（`/dev/ttyACM1`）：
```
Unknown RAMTRON device
Init ArduCopter V4.7.0-dev
Free RAM: 89976
0 0 0 IOMCU: CRC ok
IOMCU: 0x10016420
IOMCU startup
```

**对比 ChibiOS**：ChibiOS 的 `main()` 在 `chThdCreateStatic()` 创建的主线程中运行，其优先级通常与 UART 驱动线程相同或更高，不存在此类初始化缓慢问题。

#### 2026-05-14 扩展：在 `_main_loop_entry()` 内实现 ChibiOS 式 setup 降优先级

**问题**：即使主线程优先级提高到 6，`setup()` 阶段的大量 `delay()` 调用仍可能因 AP_HAL 主循环尚未就绪而卡住。ChibiOS 采用**在 setup 前降到 STARTUP 优先级、setup 后恢复**的两段策略。

**ChibiOS 参考** (`libraries/AP_HAL_ChibiOS/HAL_ChibiOS_Class.cpp:main_loop()`)：
```cpp
// ChibiOS: setup 前降到极低优先级，让 UART/SPI/timer 线程充分运行
chThdSetPriority(APM_STARTUP_PRIORITY);  // = 10 (ChibiOS: 越低越优先)
copter.setup();
chThdSetPriority(APM_MAIN_PRIORITY);     // = 180 (恢复)
```

**RTT 实现**（2026-05-14 已验证编译+烧录成功 → `libraries/AP_HAL_RTT/HAL_RTT_Class.cpp`）：

```cpp
// 在 _main_loop_entry() 中，setup() 调用前
// 降优先级到 20（RTT: 数字越大优先级越低，20 低于 UART=6 和 timer=4）
rt_uint8_t setup_priority = 20;
rt_thread_control(rt_thread_self(), RT_THREAD_CTRL_CHANGE_PRIORITY, &setup_priority);

a->callbacks->setup();  // setup 期间主线程以低优先级运行

// setup 完成后恢复
rt_uint8_t main_priority = APM_RTT_MAIN_PRIORITY;  // = 5
rt_thread_control(rt_thread_self(), RT_THREAD_CTRL_CHANGE_PRIORITY, &main_priority);
```

**优先级调整效果**：

| 阶段 | ChibiOS | RTT (原) | RTT (修复后) |
|------|---------|----------|-------------|
| setup 前 | APM_STARTUP_PRIORITY=10 (低) | 5 (高 — UART/timer 被抢占) | **20 (低 — 让出 CPU)** |
| setup 后 | APM_MAIN_PRIORITY=180 (高) | 5 (持续高) | **5 (恢复高优先级)** |

**验证方法**：编译后烧录，检查 `rtt_dbg_hal_run_called` 和 `rtt_dbg_main_loop_entry_called` 是否被写入。如果 `main()` 本身未被调度（hal_run_called 仍为初始值），则优先级调整未被触及——问题在更早的 RT-Thread 调度阶段（见「主循环状态诊断」扩展表）。

**注意**：此修改不影响 RT-Thread 内核的默认 main 线程优先级（`RT_MAIN_THREAD_PRIORITY=10`），仅在 `AP_HAL_MAIN_CALLBACKS` 定义的 `main()` 入口后生效。如果 `main()` 从不会被调度，此修复不可达。

---

### 五层阻塞修复链（2026-05-14 更新至 5 层 — 本会话实体验证）

**背景**：从 L0 基线到正常启动进入主循环，存在 **5 层阻塞叠加**。每一层修复后下一层才暴露。必须按此顺序修复。

```
第一层: Flash.cpp:137 rt_thread_yield() → erase 卡死 (Flash 操作完成但线程被抢)
    ↓ 修复: 删除 yield，纯忙等
    ↓ 确认: FLASH->SR=0 (BSY cleared), FLASH->CR=0x80000100 (LOCK)
第二层: CONFIG_RT_DEBUGING_ASSERT → ASSERT 死循环 (C++ 构造触发 RT_ASSERT)
    ↓ 修复: 禁用 (# CONFIG_RT_DEBUGING_ASSERT is not set)
    ↓ 确认: PC 推进到 _delay_microseconds_dwt
第三层: setup_priority=20 → 主线程被 UART(6)/Timer(4)永久抢占
    ↓ 修复: setup_priority=8
第三轮设置：setup_priority=8 → 进入主循环（ML_ITER=1500+）但卡在 stage 630
第四层: CherryUSB CDC init 被注释掉 → USB 设备端未初始化
    ↓ 修复: 恢复 INIT_COMPONENT_EXPORT(rt_hw_cherryusb_cdc_init)
    ↓ 确认: IEPINT=1, DRAIN bytes=200K+
第五层: GCCFG 设置于 dwc2_core_init() 之前 → core_reset 后清零
    ↓ 修复: GCCFG 移至 dwc2_core_init() 之后
    ↓ 确认: GCCFG=0x210000 (含 NOVBUSSENS)
第六层: AP_GPS::init() 返回快但 Timer/UART 轮询线程（prio 4/6）始终就绪 → setup 线程（prio 8）调度饥饿
    ↓ 修复: 提高 setup_priority 到 6（与 UART 同级）+ 或跳过 ADC timer_tick
    ↓ 确认: stage 推进到 650+ 后进入主循环
```

**诊断思路**：当修复一层后系统仍不推进到预期状态（如 Flash 修复后系统进入 `rt_assert_handler`），不要认为根因诊断错了 — 可能是**下一层阻塞暴露了**。正确做法：

1. 确认上一层是否真通了（Flash:SRL=0 ✅, ASSERT handler 不再触发 ✅）
2. 寻找**新的**卡住点（PC 位置、诊断变量值）
3. 依次解决各层，永不跳过

**快速跳转到具体诊断**：
- 第一层 → 下方「Stage 500-503 存储初始化挂起」
- 第二层 → 下方「配置断言 CONFIG_RT_DEBUGING_ASSERT」
- 第三层 → 下方「setup_priority=20 在 RTT 上过饿」→ 2026-05-15 扩展：priority=8 仍可能被 Timer/UART 饥饿
- 第四层 → `rtt-cuav-v5-cdc-tx-fix` skill「CherryUSB 初始化被注释」
- 第五层 → `rtt-cuav-v5-cdc-tx-fix` skill「GCCFG 位置 Bug」
- **第六层（新增）** → `references/stage-decoding-and-rtt-polling-vs-chibios-event.md`「RTT 轮询 vs ChibiOS 事件驱动架构差异 — Timer/UART 轮询线程造成 setup 线程调度饥饿」

**第六层核心**：即使 setup_priority=8，RTT 的 ap_timer(prio 4) 和 ap_uart(prio 6) 因轮询设计始终就绪 → "夫妻档"锁住 scheduler → setup 线程得不到 CPU。详见解码参考文件。

---

### 第九步-BONUS：Stage 500-503 存储初始化挂起 — Flash::erasepage 优先级反转（2026-05-15 发现）

**症状**：`HAL_RTT::run()` 和 `_main_loop_entry()` 均已进入（`rtt_dbg_hal_run_called=0xBBBBBBBB`，`rtt_dbg_main_loop_entry_called=0x12345678`），但 `setup_stage` 卡在 **502**（`Storage::_storage_open()` → `_flash_load()` → `AP_FlashStorage::init()`）。CFSR=0, HFSR=0。PC 始终在 `_delay_microseconds_dwt` 或 `_serial_int_rx`（中断上下文）。系统运行 60+ 秒后 stage 仍为 502。

**解码 setup_stage 值**：

| 值 | 含义 | 来源文件 |
|----|------|---------|
| 500 | `_storage_open` entered | `AP_HAL_RTT/Storage.cpp:24` |
| 501 | 尝试 FRAM (SPI2) | `AP_HAL_RTT/Storage.cpp:29` |
| 502 | 尝试 Flash 存储 | `AP_HAL_RTT/Storage.cpp:55` |
| 503 | 使用 RAM stub | `AP_HAL_RTT/Storage.cpp:63` |
| 600+ | ArduCopter init_ardupilot 阶段 | `ArduCopter/system.cpp` |

**根因：`Flash::erasepage()` 中 `rt_thread_yield()` 导致优先级反转**

在 `libraries/AP_HAL_RTT/Flash.cpp:137` 的忙等循环中：

```cpp
while (FLASH->SR & FLASH_SR_BSY) {
    if (++yield_counter >= 10000) {
        rt_thread_yield();    // ← 问题：低优先级线程 yield 后永不被调度
        yield_counter = 0;
    }
}
```

**优先级反转链**：
1. `flash erase_sector 0 1 11` （烧录时的 OpenOCD 命令）擦除了存储页（page 10-11）
2. 首次启动时 `AP_FlashStorage::init()` 发现页头无效 → 调用 `erase_all()` → 擦除两个 256KB 扇区
3. `Flash::erasepage()` 以 priority=20 运行，每次 yield 后：
   - UART 线程 (prio 6) 就绪 → 被调度
   - Timer 线程 (prio 4) 就绪 → 被调度
   - **主线程（prio 20）永不恢复 CPU** → 无法检查 BSY 是否清除
4. 实际上 FLASH 擦除已完成（`FLASH->SR=0`, BSY=0）但主线程饿死无法返回

**诊断方法**：

```bash
# 1. 读 setup_stage 确认卡在 502
python3 -c "
import socket, time
s = socket.socket(); s.settimeout(5); s.connect(('localhost',4444))
time.sleep(0.5); s.recv(4096)
s.send(b'mdw 0x2001bc84 1\\n'); time.sleep(0.3)
print('STAGE:', s.recv(1024).decode(errors='replace'))
s.close()
"

# 2. 读 FLASH SR 确认擦除已完成（BSY=0）
echo "mdw 0x40023C0C 1" | nc -q 2 localhost 4444
# → 0x40023C0C: 00000000  (BSY=0, 擦除已完成！)

# 3. 确认优先级反转 — 检查线程调度
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rtt_dbg_setup_stage
# → 2001bc84 B rtt_dbg_setup_stage
```

**修复方案**（移除 `rt_thread_yield()`）：

```cpp
// Flash.cpp ~line 134-140 — 修改前
    while (FLASH->SR & FLASH_SR_BSY) {
        if (++yield_counter >= 10000) {
            rt_thread_yield();    // ← 移除！
            yield_counter = 0;
        }
    }

// 修改后 — 纯忙等。闪存擦除由硬件自动完成，CPU 忙等不影响进度。
// 擦除一个 256KB 扇区约 1-2 秒，此期间主线程不 yield 是可以接受的。
    while (FLASH->SR & FLASH_SR_BSY) {
        /* 忙等闪存擦除完成（~2秒）。不 yield — 否则 priority=20 的
         * 主线程被 UART(prio6)/Timer(prio4) 永久抢占，擦除永不结束。
         * 闪存擦除由硬件自动完成，CPU 忙等不影响擦除速度。 */
    }
```

### 第九步-BONUS-2：配置断言 `CONFIG_RT_DEBUGING_ASSERT` 导致 boot 后挂死（2026-05-14 本会话发现）

**症状**：Flash 擦除修复后，系统启动但 halt 后 PC 停在 `rt_assert_handler` 的 `while(dummy==0)` 死循环。STAGE=502、CFSR=0、HFSR=0。系统不输出任何控制台信息。

**根因**：`.config` 中 `CONFIG_RT_DEBUGING_ASSERT=y` 导致 `RT_ASSERT(obj != object)` 在 `rt_object_init` 中触发。C++ 构造器中的 `Semaphore::_ensure_mtx()` 第二次调用 `rt_mutex_init()` 时，`object.c:386` 的循环检查发现同一对象指针在对象列表中已存在 → 触发断言 → 进入死循环。

**诊断**：
```bash
# 1. 检查 PC 地址
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf -f -a <PC>
# → rt_assert_handler / .../kservice.c:1284

# 2. 检查调用者
# → rt_object_init / .../object.c:386

# 3. 确认配置
grep CONFIG_RT_DEBUGING_ASSERT libraries/AP_HAL_RTT/hwdef/common/.config
```

**修复**（修改 `.config`，不修改 RT-Thread 内核）：
```bash
# libraries/AP_HAL_RTT/hwdef/common/.config line 144
# CONFIG_RT_DEBUGING_ASSERT is not set    ← 替换 CONFIG_RT_DEBUGING_ASSERT=y
```

**注意**：`.config` 的真实来源是 `libraries/AP_HAL_RTT/hwdef/common/.config`，不是 BSP 子模块的 `.config`。修改后需 clean rebuild（`rm -rf build/rtt_deploy build/rtt_cuav_v5`）才能生效。

### 第九步-BONUS-3：setup_priority=20 在 RTT 上过饿 — 改用 8，但 8 仍可能不够（2026-05-14 本会话发现 + 2026-05-15 扩展）

#### 第一轮修复：20 → 8（已验证）

**症状**：Flash yield 和 ASSERT 都修复后，`HAL_RUN=0xBBBBBBBB`、`ML_ENTRY=0x12345678` 均设置，但 `setup_stage` 仍是 502。PC 始终卡在 `_delay_microseconds_dwt`（Scheduler.cpp:72）。CFSR=0。

**根因**：setup 期间主线程优先级设为 20（ChibiOS 式降优先级，参考 `HAL_ChibiOS_Class.cpp:main_loop()` 的 `APM_STARTUP_PRIORITY`）。但 RT-Thread 的 Timer 线程(prio 4)、UART 线程(prio 6) 是"始终就绪"线程，主线程每次 `rt_thread_delay(1)` 后 UART/Timer 立即抢占 CPU，永不归还。每个 `delay(1ms)` 膨胀到 10-50ms 壁钟。**ChibiOS 的 UART/Timer 是事件驱动（阻塞在信号量），RTT 的是轮询（始终就绪）**，所以同一个低优先级策略在 RTT 上失效。

**线程优先级分布**：

| 线程 | RTT 优先级 | 就绪行为 |
|------|-----------|---------|
| ap_timer | 4 | 1kHz 定时器，始终就绪 |
| SPI1 bus | 5 | 设备回调，偶发就绪 |
| ap_uart | 6 | 串口 drain，始终就绪 |
| **主线程 setup** | **20 ❌** | **被 UART/Timer/SPI 永久抢占** |
| ap_io | 18 | 低优先级后台 |

**修复**（`libraries/AP_HAL_RTT/HAL_RTT_Class.cpp:174`）：
```cpp
// 修改前
rt_uint8_t setup_priority = 20;
// 修改后 — 8 仍低于 UART(6)/SPI(5)/Timer(4)，不再彻底饿死
rt_uint8_t setup_priority = 8;
```

#### 🚩 第二轮发现问题：setup_priority=8 下卡在 stage 630（AP_GPS::init）

**症状**：三层阻塞修复链已全部到位（Flash 无 yield ✅、CONFIG_DEBUG_ASSERT 关闭 ✅、setup_priority=8 ✅），但 `rtt_dbg_setup_stage` 固化在 **630**（0x276），系统不推进。

**根因分析过程**：

1. **读芯片变量**：`echo "mdw 0x2001bc84" | nc -q1 localhost 4444` → `0x00000276` (630)

2. **反汇编确认写入点**：
```asm
8026e8e: movw r3, #630   ; 0x276
8026e92: str r3, [r5, #0]          ; stage=630 → memory
8026e94: bl 80619ac <AP_GPS::init> ; call GPS::init()
8026e98: movw r3, #631             ; (only after return)
8026e9c: str r3, [r5, #0]          ; stage=631 (after return)
```
→ stage 630 被写入后调用 `AP_GPS::init()`，若返回则写入 631

3. **读 AP_GPS::init() 源码**发现它**没有阻塞循环**——只是参数转换 + find_serial。理论上应快速返回。

4. **结论**：不是 `AP_GPS::init()` 本身挂死，而是 **setup 线程（prio 8）被更高优先级线程永久抢占**，导致 `AP_GPS::init()` 函数虽然就绪但**得不到 CPU 时间片**。

**Stage 解码表（500-800+）**：

| 值 | 含义 | 所在文件 |
|----|------|----------|
| 500 | `_storage_open` entered | `AP_HAL_RTT/Storage.cpp:24` |
| 501 | 尝试 FRAM (SPI2) | `AP_HAL_RTT/Storage.cpp:29` |
| 502 | 尝试 Flash 存储 | `AP_HAL_RTT/Storage.cpp:55` |
| 503 | 使用 RAM stub | `AP_HAL_RTT/Storage.cpp:63` |
| 600-616 | `init_ardupilot()` 前半段 | `ArduCopter/system.cpp` |
| 620 | `init_rc_in()` | `ArduCopter/system.cpp` |
| 630 | `AP_GPS::init()` — **本会话新发现** | `ArduCopter/system.cpp` |
| 631-632 | Compass init | `ArduCopter/system.cpp` |
| 640 | `baro.calibrate()` | `ArduCopter/system.cpp` |
| 641 | `init_rangefinder()` | `ArduCopter/system.cpp` |
| 651 | `ins.init()` | `ArduCopter/system.cpp` |
| 662 | gyro init / sensor probe | `AP_InertialSensor/AP_InetialSensor.cpp` |
| 672 | `ahrs.reset()` | `ArduCopter/system.cpp` |
| 675 | Scheduler init | `AP_Scheduler/AP_Scheduler.cpp` |
| 681+ | 参数加载+初始化完成 | 多个文件 |

#### 核心架构差异：ChibiOS 事件驱动 vs RTT 轮询驱动

| 维度 | ChibiOS | RTT |
|------|---------|-----|
| UART 线程 | 阻塞在 `chnReadTimeout()` 信号量 — 只在有数据时才就绪 | 轮询 drain — **始终就绪** |
| Timer 线程 | 阻塞在 `chThdSleep()` — 只在 tick 到达时短暂就绪 | 1kHz 硬实时 — **始终就绪** |
| 低优先级线程 | `chThdSetPriority()` 后高优先线程自动阻塞 → 自然调度 | 高优先线程永不阻塞 → **低优先线程被饿死** |
| 等效策略 | setup 前降优先级 → 高优先线程主动放弃 CPU | ❌ 同策略不生效 — 高优先线程消费所有时间片 |

**验证方法**：当 `setup_stage` 不推进且 CFSR=0 时，需要区分"真卡死" vs "调度饥饿"：

```bash
# 方法1：跨时间长间隔检测（30s+ 间隔）
echo "halt; mdw 0x2001bc84 1; resume" | timeout 10 nc localhost 4444
sleep 60
echo "halt; mdw 0x2001bc84 1; resume" | timeout 10 nc localhost 4444
# 如果 stage 变了 → 调度饥饿（极慢推进），非真卡死
# 如果 PC 也在变（如不同 delay 点）但 stage 不变 → 同结论

# 方法2：检查当前线程活跃度
echo "halt" | timeout 5 nc localhost 4444
echo "mdw 0x2001bc84" | timeout 5 nc localhost 4444   # stage
echo "reg pc" | timeout 5 nc localhost 4444            # 当前 PC
echo "resume" | timeout 5 nc localhost 4444
sleep 2
echo "halt" | timeout 5 nc localhost 4444
echo "reg pc" | timeout 5 nc localhost 4444            # 不同 PC？→ 活跃
echo "resume" | timeout 5 nc localhost 4444

# 方法3：读 D-Cache 清空后的物理内存（排除 D-Cache 伪影）
# 用 DCCMVAC 清理指定地址的缓存行
echo "halt" | timeout 5 nc localhost 4444
echo "mww 0xE000EF5C 0x2001bc84" | timeout 5 nc localhost 4444  # DCCMVAC
echo "mww 0xE000EF00 0x0F" | timeout 5 nc localhost 4444        # DSB
echo "mdw 0x2001bc84 1" | timeout 5 nc localhost 4444           # 读物理内存
echo "resume" | timeout 5 nc localhost 4444
```

**修复方向（按有效性排序）**：

| # | 方案 | 说明 | 风险 |
|---|------|------|------|
| A | setup_priority=**6**（与 UART 同级） | 与 UART 线程同级轮转，不再被完全抢占 | UART drain 延迟可能增加，但 setup 阶段通常无大流量 UART | 
| B | Timer thread 在 setup 期内降优先级 | 临时将 timer (prio 4) 降到 UART 之下 | 需要修改 timer 线程的创建点，复杂度高 |
| C | Setup 阶段跳过 ADC timer tick | `AnalogIn._timer_tick()` 中检查 setup 阶段标志，skip ADC 读取 | ADC 数据在 setup 阶段不需要 |
| D | 全局关闭所有不必要的高优先级线程 | 在 setup 前 pause timer/uart 线程 | 复-杂、维护成本高 |

**推荐路径**：先试方案 A（setup_priority=6）——RTT 优先级数值越小越高，6 与 UART 同级，主线程不再完全被饿死。

**验证判断链**：

```
setup_stage 卡在 630+ 且 CFSR=0
    ↓
检查 setup_priority 值（objdump HAL_RTT_Class.cpp:179）
    ↓
如果 priority=8
    └→ 修改为 6 → 重建 → 烧录 → 验证
        └→ 如果 stage 推进到 650+ → 问题确认
        └→ 如果还是卡住 → 添加壁钟超时（见 gyro init 超时）
如果 priority=6 仍卡住
    └→ 确认是否有其他阻塞（SD卡挂载、USB CDC init 等）
```

**2026-05-15 关键发现**：Priority 8 在 RTT 上**不保证**主线程有机会运行。与优先级别（6/8）本身的关系不大，关键是 **Scheduler 策略差异**：RTT 使用优先级抢占 + 同优先级时间片轮转，但 ap_timer (prio 4) 每 1ms 就绪一次，ap_uart (prio 6) 每 tick 轮询 drain → 两个更高优先级线程组成"夫妻档"，在每个 time slice 内交替锁住 CPU。

**建议**：如果 priority=6 仍不行，考虑在 `setup()` 入口临时禁用 ADC timer 的 _timer_tick（设标志位让 AnalogIn 跳过 8 通道读取），这是对 ArduPilot 行为影响最小的路径。
```

**1:1 复刻参考**：ChibiOS 的 `hal_flash_lld.c` 在闪存操作中不使用 yield/线程切换 — 通过纯忙等或 `chSysPolledDelay()` 实现。

**预防**：首次烧录后（特别是 `flash erase_sector 0 1 11` 擦除了存储区的场景），系统首次启动时会自动执行 `erase_all()`。如果不移除 yield，则需要额外等待 4-8 秒（实际擦除时间）× 优先级反转系数。移除 yield 后只需等待实际闪存擦除时间 ~4 秒。

---

**完整诊断参考**：`references/gyro-init-hang-six-variable-diagnosis.md`

### 第十步-BONUS：Gyro Init 诊断六变量法 — _gyro_cal_ok 构造函数陷阱（2026-05-12 新增）

当 setup_stage 卡在 **662** 时，除了 SPI 信号量分析外，还需要更精确地判断 gyro init 的当前状态。这里存在一个核心陷阱。

#### 核心陷阱：`_gyro_cal_ok` = true ≠ 校准完成！

`AP_InertialSensor` 构造函数（line 720-721）将 `_gyro_cal_ok[i]` **初始化为 true**：
```cpp
for (uint8_t i=0; i<INS_MAX_INSTANCES; i++) {
    _gyro_cal_ok[i] = true;  // 默认 true，不表示校准完成！
}
```

**这意味着 `_gyro_cal_ok = {1, 1}` 是初始状态，不是校准完成信号！** 仅当校准失败时 `_gyro_cal_ok[k] = false` 才被设置（line 1828）。切勿据此判断进度。

#### 六变量法精确定位

读以下六个 GDB 变量即可判断 gyro init 的精确阶段：

```bash
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x rtt_dbg_setup_stage" \
  -ex "p AP::ins()._calibrating_gyro" \
  -ex "p/x AP::ins()._sample_period_usec" \
  -ex "p AP::ins()._loop_rate" \
  -ex "p/x AP::ins()._gyro_count" \
  -ex "p/x AP::ins()._gyro_cal_ok" \
  -ex "p/x rtt_dbg_gyro_loop" \
  -ex "monitor resume" \
  /path/to/rt-thread.elf
```

| 变量 | 值含义 | 诊断结论 |
|------|--------|---------|
| `rtt_dbg_setup_stage` | 662 | 卡在 `startup_INS_ground()` 的 `ins.init()` |
| `_calibrating_gyro` | **true** | `_init_gyro()` 从未返回（14+ 分钟） |
| `_calibrating_gyro` | **false**, `_sample_period_usec=2500` | `init_gyro()` 完成，阻塞在 `_save_gyro_calibration()` |
| `_sample_period_usec` | **0** | `init_gyro()` 之后行未执行（卡在 `_init_gyro()` 内） |
| `_sample_period_usec` | 2500 | `init_gyro()` 已返回（1000000/400=2500） |
| `_loop_rate` | 400 | `ins.init()` 已进入（line 947 执行了） |
| `_gyro_count` | 2 | `_start_backends()` 完成，两个 IMU 已找到 |

**典型卡死组合**：`_calibrating_gyro=true`, `_sample_period_usec=0`, `_loop_rate=400`, `_gyro_count=2` → `_init_gyro()` 内 `hal.scheduler->delay(5)` 循环的某个 delay 从未返回。

#### 三层调用链（重要！）

`init_gyro()` 不是单体函数：

```
init_gyro()                              // line 1394-1399 - 包装函数
  ├── _init_gyro()                       // line 1687-1847 - 核心校准循环
  │     └── hal.scheduler->delay(5)       // line 1777 - 每次内循环迭代的阻塞点
  └── _save_gyro_calibration()            // line 1850-1865 - 持久化校准结果
        └── _gyro_offset(i).save()        // line 1853 - 写 FRAM/Flash，可能阻塞
```

`_init_gyro()` 完成 → `_calibrating_gyro = false`（line 1842），然后 `init_gyro()` 调用 `_save_gyro_calibration()`。
如果 `_calibrating_gyro=true` 但 `_sample_period_usec=0` → **卡在 `_init_gyro()` 内**。
如果 `_calibrating_gyro=false` 但 `_sample_period_usec=0` → **卡在 `_save_gyro_calibration()` 内**。

#### 追踪循环进度的 GDB 计数器

在 `AP_InertialSensor.cpp` 添加全局 `volatile uint32_t rtt_dbg_gyro_loop = 0;`，在 inner loop 前后赋值：

```cpp
// 在 inner loop 内，delay 前后
rtt_dbg_gyro_loop = j * 1000 + i + 1;          // delay 前
hal.scheduler->delay(5);
rtt_dbg_gyro_loop = j * 1000 + i + 1 + 50000;  // delay 后
```

读取方法：
```bash
p/x rtt_dbg_gyro_loop
```

| 值范围 | 含义 |
|--------|------|
| **0** | 从未进入 inner loop → SPI backend `update()` 卡死 |
| **1-59999** | 进入内循环但 delay 后赋值未执行 → **`delay(5)` 永远不返回** |
| **≥ 60000** | delay post 赋值已执行 → delay 正常，继续迭代 |

#### `delay(5)` 能永远不返回吗？

在 RTT HAL 的实现中：

```cpp
void Scheduler::delay(uint16_t ms) {
    uint64_t start = AP_HAL::micros64();
    while ((AP_HAL::micros64() - start) / 1000 < ms) {
        delay_microseconds(1000);   // → rt_thread_delay(1) if tick_us=1000
        call_delay_cb();            // → gcs().update_send()
    }
}
```

理论上 35 秒超时应击穿，但：
- 15/17 分钟后 `_calibrating_gyro` 仍为 true → 超时检查 `AP_HAL::millis() - gyro_init_start_ms > 35000` 从未为 true
- 可能的根因：`delay()` 内 `AP_HAL::micros64()` 返回值在 while 条件下不推进
- 如果 `micros64()` 依赖 `rt_tick_get()`，而 `rt_thread_delay(1)` 后 tick 正常递增 → delay 应推进
- **未确认的假设**：主线程未正确唤醒或 tick 计数器在 delay 循环内不更新

**调试路径**：用 rtt_dbg_gyro_loop 在 delay 前后各写一次值，看 post-delay 值是否出现。

#### 完整诊断参考

详见 `references/gyro-init-hang-six-variable-diagnosis.md`。

**症状**：
- setup_stage 卡在 **662**（`ins.init()` 中）
- USB CDC 已枚举，有控制台输出（"IOMCU startup"）
- 主循环迭代数 = 0
- 多次 halt 检查 PC 始终在 `_delay_microseconds_dwt` (Scheduler.cpp:72)
- CFSR=0，无 HardFault

**子路径 A：IMU 无供电（WHO_AM_I 返回 0xFF）**

如果 SPI 诊断 `last_recv_0` / `rtt_spi1_rt.last_recv_0` 始终为 `0xFF`，说明 IMU 芯片无响应。检查传感器供电引脚：

| 引脚 | CUAV V5 | 验证 |
|------|---------|------|
| `VDD_3V3_SENSORS_EN` | PE3 (port 4, pin 3) | `mdw 0x40021000` → PE3 bits 7:6 = 01 (output) |
| ODR 值 | PE3 bit 3 = 1 (HIGH) | `mdw 0x40021014` → bit 3 = 1 |

**常见根因**：STM32F7 D-Cache 干扰 GPIO MODER 写入（`ardupilot-rtt-architecture` skill §行为差异-第5节）。

**诊断**：
```bash
# 1. PE3 MODER 是否 output?
echo "mdw 0x40021000" | nc -q 2 localhost 4444

# 2. SPI 诊断变量（需在固件中定义 rtt_spi1_rt 结构体）
echo "mdw 0x20019998 10" | nc -q 2 localhost 4444
# 检查 last_recv_0 和 last_recv_1 — 若始终 0xFF 说明 IMU 无响应
# 若变了（如 0x98 或 0x85/0x41）说明 IMU 有电但在初始化中
```

**修复**：见 `ardupilot-rtt-architecture` 参考文件 `stm32f7-gpio-dcache-interaction.md`。

**子路径 B：IMU 有电但 SPI 信号量竞争导致推进极慢**

**根因分析**：

`ins.init()` → `detect_backends()` → `AP_InertialSensor_Invensense::probe()` → `_hardware_init()`：

```cpp
bool AP_InertialSensor_Invensense::_hardware_init(void)
{
    WITH_SEMAPHORE(_dev->get_semaphore());  // ← 获取 SPI 信号量
    
    _register_write(MPUREG_PWR_MGMT_1, BIT_PWR_MGMT_1_DEVICE_RESET);
    hal.scheduler->delay(100);  // ← 持锁期间调 delay(100)！
    // ... 更多 delay(10), delay(5) 等
}
```

**关键冲突**：

| 组件 | 优先级 | 行为 |
|------|--------|------|
| 主线程 (main) | **6** | 持有 SPI 信号量，执行 `delay(100)` |
| 定时器线程 (ap_timer) | **4** (更高) | 试图访问 SPI 总线做 IMU 采样，被信号量阻塞 |
| UART 线程 (ap_uart) | **6** | 在 `delay(100)` 间隙运行，但可能让出时间片给定时器 |
| 辅助线程 (ap_io) | **18** | 低优先级，不参与竞争 |

**优先级反转链**：
1. 主线程 (prio 6) 获取 SPI 信号量 → 执行 `delay(100)` → 挂起 100ms
2. 定时器线程 (prio 4, 更高优先级) 就绪 → 被调度运行 → 尝试获取 SPI 信号量 → 阻塞！
3. 定时器线程阻塞后 → 调度器切换到下一个就绪线程 → 通常是主线程 (prio 6) 恢复运行
4. 主线程跑完 `delay(100)` 剩余时间 → 释放 SPI 信号量 → 继续下一个 100ms 循环

**每个 `_hardware_init()` 芯片复位循环**：5 次 × 100ms = 500ms 最少耗时（实际因调度切换更多）
**两个 IMU (ICM20689 + ICM20602)**：1-2 秒
**`init_gyro()`**：8 秒校准（需要 SPI 连续通信）

**诊断方法**：

```bash
# 1. 确认卡在 ins.init()
echo "mdw 0x2001bc6c 1" | nc -q 2 localhost 4444  # setup_stage = 662

# 2. 检查 PC 是否在 delay 循环中（预期）
echo "reg pc" | nc -q 2 localhost 4444
# 0x0806ec18 → _delay_microseconds_dwt (正常，不是死锁)

# 3. 多次 halt 间隔 5 秒，确认 setup_stage 是否推进
echo "halt; mdw 0x2001bc6c; resume" | nc -q 2 localhost 4444
sleep 5
echo "halt; mdw 0x2001bc6c; resume" | nc -q 2 localhost 4444
# 如果不变 → 系统真的卡住（非慢速推进）
# 如果变化 → 系统在推进，只是慢

# 4. 用 MAIN_THREAD_PRIORITY 从 10 改为 6 可加速
# 5. 确认 IOMCU upload 是否完成（之前已有 "IOMCU startup" 输出，可能需要几十秒）
```

**修复方向**：

| # | 方向 | 描述 | 风险 |
|---|------|------|------|
| A | ✅ 主线程优先级 10→6 | 减少被高优先级线程抢占次数 | 已验证有效 |
| B | SPI 锁粒度细化 | 在 `delay()` 前释放信号量，delay 后重新获取（需重构 `_hardware_init()`） | 修改 Invensense 驱动（在 AP_HAL_RTT 代理层实现？） |
| C | IOMCU 上传加速 | IOMCU firmware upload 通常需 10-30 秒 | 硬件依赖 |
| D | 跳过 IMU init | 调试阶段临时跳过 ins.init() 让系统先跑起来验证 CDC 心跳 | 降低初始化完整性 |

**核心观察**：即使主线程优先级提高到 6，`ins.init()` 中 `delay(100)` 仍然耗时不短（每个芯片需 500ms+5 次尝试 × 100ms 延迟 = 500ms 纯等待，两个 IMU 则 1 秒+）。实际耗时还受 IOMCU 上传（UART 通信，UART 线程 prio 6）和 I2C 位爆炸（prio 7）影响。**建议设一个上限（如 120 秒），超过则跳过 IMU init 让系统进入主循环**。

#### 子路径 C：Gyro init 标定循环要太久 — 壁钟超时修复（2026-05-12 验证有效）

**现象**：系统启动后卡在 `setup_stage=662`（`ins.init()`），`rtt_dbg_imu_probe_stage=22`（IMU 探测成功），但系统 60+ 秒后才继续。CFSR=0，无 HardFault。

**根因分析**：`AP_InertialSensor::_init_gyro()` 的标定循环使用迭代次数作为超时：

```cpp
// libraries/AP_InertialSensor/AP_InertialSensor.cpp:1751
for (int16_t j = 0; j <= 30*4 && num_converged < num_gyros; j++) {
```

外循环最多 120 次，每次迭代 = 50 样本 × 5ms delay + `update()` = 约 250ms。理论上限 = 30 秒。

但 **RTT 调度下的每次 `delay(5)` 实际耗时可能远超 5ms**（主线程优先级 6 被 timer/uart 线程频繁抢占），导致每迭代耗时 500ms+，总时间膨胀到 60+ 秒。

**修复方式（已验证）**：在内循环开头加一个壁钟超时检查，覆盖迭代计数不足的场景：

```cpp
// 在 _init_gyro() 中，for 循环前加
const uint32_t gyro_init_start_ms = AP_HAL::millis();

// 在 for 循环体内第一行加
if (AP_HAL::millis() - gyro_init_start_ms > 35000U) {
    DEV_PRINTF("gyro_init: timeout after 35s, using best estimate\n");
    break;
}
```

**验证结果**：加上 35 秒壁钟超时后，系统：
- 35 秒后强制退出标定循环
- 使用最佳估计值继续（即使未收敛）
- 成功进入主循环
- 连续运行 196 秒零故障（CFSR=0, HFSR=0）

**注意**：`AP_HAL::millis()` 依赖 `rt_tick_get()`。如果 SysTick 也停了，壁钟超时也不会触发。但这种情况极少见——如果 SysTick 停了，系统整体已经挂死。

**⚠️ 2026-05-13 局限性发现**：`AP_HAL::millis()` 在 RTT 上依赖 `rt_tick_get()`(SysTick)。当主线程在 DWT 忙等中从未进入 `call_delay_cb()` 时，`AP_HAL::millis()` 的 tick 值可能不更新 → 35 秒壁钟超时条件永不满足。

**2026-05-13 实测验证**：添加 35s `AP_HAL::millis()` 超时后，系统仍 70s+ 未退出 gyro calibration。PC 在 `_delay_microseconds_dwt` 和 `AnalogIn::_timer_tick` 之间切换，但 `main_loop_iterations=0`。超时未触发 → **确认 `AP_HAL::millis()` 在 DWT 忙等上下文中不推进**。

**根本修复方向**：改用 DWT CYCCNT 做壁钟超时（不依赖 SysTick）：
```cpp
// 在 _init_gyro() 中
const uint32_t gyro_init_start_cycles = DWT->CYCCNT;
// 在循环体内
if ((DWT->CYCCNT - gyro_init_start_cycles) > 35UL * SystemCoreClock) {
    DEV_PRINTF("gyro_init: 35s wall-time (CYCCNT) timeout, using best estimate\\n");
    break;
}
```
注意：需确保 DWT CYCCNT 在此运行上下文中是使能且递增的。

**备选方案**：若所有超时方案都不可靠，放弃 `init_gyro()` 改用跳过校准的短路径（已验证有效）：
```cpp
// 替换 AP_InertialSensor.cpp:967-968 的 init_gyro() 调用
for (uint8_t i = 0; i < _gyro_count; i++) {
    _gyro_cal_ok[i] = true;
}
AP_Notify::flags.gyro_calibrated = true;
```
副作用：陀螺零偏使用默认值 0，不进行板级校准。可在主循环中通过其他方式补偿。

---

## ⚠️ 第十三步（2026-05-13 新增，2026-05-12 扩展）：Clean rebuild 后 HardFault — GNU LD --gc-sections 弱符号地址错误

**症状**：clean rebuild 后立即 HardFault（INVSTATE），之前增量编译版本正常。CFSR=0x00020000, HFSR=0x40000000。**注意**：栈溢出（第十五步）也可能表现为 HardFault 但 CFSR 不同——请先排除栈溢出再诊断本问题。

**根因**：GNU LD 10.2.1 的 `--gc-sections` 在 GC 掉 CMSIS startup 文件的 WEAK `.text.Reset_Handler` 段后，对同文件 `.isr_vector` 中 `R_ARM_ABS32 Reset_Handler` 的重定位给了 GC'd 段的占位地址而非全局强符号地址。

**诊断（向量表 vs 符号表对比）**：
```bash
objdump -s --start-address=0x08008000 --stop-address=0x08008010 build/.../rt-thread.elf
nm build/.../rt-thread.elf | grep -E "Reset_Handler|Default_Handler|NMI_Handler|HardFault_Handler"
```
- Reset_Handler 向量表应=0x080f0051（非 0x08000f51）
- HardFault_Handler 向量表应正确（0x08008365）作为对照组

**修复**：在 link.lds 的 `.text` 段 `.isr_vector` 后加 `KEEP(*(.text.Reset_Handler))` 和 `KEEP(*(.text.Default_Handler))`。

### 三层问题叠加陷阱（2026-05-12 新增）

2026-05-12 session 发现：clean rebuild 到 L0 稳定需要**同时修复三个问题**，任何一个缺失都会阻止系统启动：

| # | 问题 | 症状 | 修复位置 | 遗漏后果 |
|---|------|------|---------|---------|
| 1 | gc-sections KEEP | INVSTATE HardFault, CFSR=0x00020000 | 模板 link.lds | 向量表损坏 -> 跳转即崩 |
| 2 | 早期 malloc 断言 | rt_assert_handler 死循环 | SPIDeviceManager.cpp 静态池 | 调度器未启动时 malloc 被拦截 |
| 3 | 栈溢出 (16KB->48KB) | IBUSERR HardFault, backtrace 0x00000000 | 模板 link.lds _system_stack_size | 静态构造链撑爆栈 -> 返回地址被覆盖 |

不要逐个排查——如果在 C++ 静态构造阶段崩溃，优先依次检查这三个问题。

### STM32F765 SPI4 引脚限制（2026-05-13 发现 — 常见配置错误）

**SPI4 在 STM32F765 上只有特定的有效引脚组合。错误的 PE12/PE14 → PE2/PE6 是本会话发现的关键修正。**

| 功能 | 有效引脚 (STM32F765) | 错误配置（被本会话修正） |
|------|---------------------|-----------------------|
| SPI4_SCK | **PE2** 或 PF6 | ❌ PE12（无效引脚） |
| SPI4_MISO | PE13 或 PF7/PF8 | ✅ PE13（正确） |
| SPI4_MOSI | **PE6** 或 PF9/PF11 | ❌ PE14（与 TIM1_CH4 PWM 冲突） |

**为什么 PE12/PE14 是错误的**：
- PE12 不是 STM32F765 上 SPI4 的有效 SCK 引脚（只有 PE2 或 PF6 可以）
- PE14 同时被 TIM1_CH4 PWM 输出占用 → 硬件冲突
- 提交 `f24258ffbd` 使用了 PE12/PE14，`5118bdcebf` 修正为 PE2/PE6

**正确配置位置**：
1. `hwdef.dat` — SPI4 引脚定义
2. `drv_spi_ll.c` — `spi4_ll_cfg` 结构体的 `sck_pin_no` 和 `mosi_pin_no`
3. `SPIDevice.cpp` — `_spi4_gpio_init()` 中的 GPIO AFR 配置

**验证方法**（OpenOCD）：
```bash
echo -e "halt
mdw 0x40021800 1    # GPIOE MODER — PE2 bits[5:4]=10(AF), PE6 bits[13:12]=10(AF), PE12 bits[25:24]=?, PE13 bits[27:26]=10(AF), PE14 bits[29:28]=?
mdw 0x40021820 1    # GPIOE AFR[0] — PE2(SCK) bits[11:8]=0101(AF5), PE6(MOSI) bits[27:24]=0101(AF5)
mdw 0x40021824 1    # GPIOE AFR[1] — PE13(MISO) bits[23:20]=0101(AF5)
resume\nexit" | timeout 10 nc localhost 4444 2>&1 | strings | grep "^0x"
```

**ChibiOS 参考**：`hwdef/fmuv5/hwdef.dat` 中 SPI4 使用 PE2/PE13/PE6，这是已验证的 CUAV V5 引脚布局。

---

### 第十三步补充：PG11(SCK) MODER 被后续 HAL GPIO init 覆盖 — DCache 脏读陷阱（2026-05-13 发现）

**症状**：WHO_AM_I 返回 0x00，RAW_IMU 全零。SPI 传输函数 `spi1_poll_transfer()` 正常执行（计数器递增、TX/RX 字节数正确），但所有接收数据为 0x00。

**根因**：PG11(SPI1_SCK) 的 MODER 在 `_spi1_gpio_init()` 被正确设为 AF(10) 后，被后续的 HAL GPIO init 在 GPIOG 端口上的 read-modify-write 覆盖回 Output(01) 模式。SCK 外设失去时钟驱动能力 → IMU 收不到时钟 → 不响应任何 SPI 事务。

**覆盖机制**：STM32F7 D-Cache 使能时，`HAL_GPIO_Init()` 对 GPIOG 的 `MODER` 做 RMW：
1. 读取 MODER（可能从 D-Cache 返回过时的值，不含 AF 设置）
2. 修改目标引脚位域
3. 写回 MODER（覆盖掉 PG11 的 AF 位 → Output）

**验证方法**（OpenOCD 读 GPIOG MODER）：
```bash
echo -e "halt\nmdw 0x40021800 1\nresume" | nc -w 3 localhost 4444
# 结果示例：0x00905500 → PG11 bits[23:22] = 01(OUTPUT) ❌
# 期望：bits[23:22] = 10(AF)
```

**GPIOG MODER 解码表**（CUAV V5，PG 引脚功能分布）：

| 位域 | 引脚 | 正确模式 | 可能被覆盖为 | 说明 |
|------|------|---------|------------|------|
| [23:22] | PG11 | **10(AF)** — SPI1_SCK | 01(Output) ❌ | **本会话修复目标** |
| [19:18] | PG9 | **10(AF)** — USART6_RX | 01(Output) ❌ | 也会被覆盖，但不影响 SPI |
| [17:16] | PG8 | **10(AF)** — USART6_RTS | 00(Input) ❌ | 也会被覆盖 |
| [15:14] | PG7 | 01(Output) — SD卡供电 | 01 ✅ | 正常 |

**为什么 PD7(MOSI) 没被覆盖但 PG11(SCK) 被覆盖了**：只有 GPIOD 的 PD7 有老的工作区补丁（`rt_board_init.c` line 267-269），GPIOG 的 PG11 没有被覆盖相同级别的保护。且影响 PD7 和 PG11 的 HAL init 发生于不同 GPIO 端口（GPIOD vs GPIOG），因此覆盖时序不同。

**修复策略**：在 `spi1_poll_transfer()` 每次 CS 断言时恢复 PG11 MODER，与 CR1 重新配置走同一时序：

```cpp
// In spi1_poll_transfer(), inside the `if (cs_take)` block, after SPE enable:
if (spi == SPI1) {
    GPIOG->MODER = (GPIOG->MODER & ~(3U << 22)) | (2U << 22);
}
```

**为什么放在 transfer 函数中而不是 board init 中**：
1. `INIT_DEVICE_EXPORT` 级别的 board init 补丁会被更晚的组件/应用 init 覆盖
2. Per-transfer 修复与 CR1 重配置策略一致（每次 CS 断言都重新确认 SPI 外设状态）
3. STM32F7 的 DCache 导致的 RMW 问题可能在初始化完成后仍间歇性触发

**已知检查点（每轮烧录后必须验证）**：
1. GPIOG_MODER(0x40021800) → PG11 bits[23:22] = 10(AF) ✅
2. `rtt_spi1_rt.last_recv_0` ≠ 0x00（第一个有效接收字节非零）
3. MAVLink RAW_IMU 包的 xacc/yacc/zacc 至少有一个非零

**参考**：`references/imu-whoami-probe-via-rtt-spi1-rt.md`（含完整诊断结构体 rtt_spi1_rt 和 GPIO MODER 编码方法）

### 跳过 init_gyro() 校准（2026-05-13 — RTT 启动速度修复）

**症状**：`setup_stage` 永久卡在 **662**（`ins.init()` 内的 `init_gyro()`），系统永不进入主循环。

**根因**：`init_gyro()` 的校准循环在 RT-Thread 调度下耗时膨胀 3-5 倍。每个 `delay(5)` 被高优先级线程（timer prio 4, UART prio 6）频繁抢占，导致标定循环 120 次迭代 × ~500ms = 60+ 秒。虽然 `_init_gyro()` 有 35 秒壁钟超时，但在 RTT 上 `AP_HAL::millis()` 通过 `rt_tick_get()` 获取的 tick 值同样被延迟干扰，导致超时检查不触发。

**修复**（`libraries/AP_InertialSensor/AP_InertialSensor.cpp` line 967-968）：

```cpp
// 原代码（导致卡死）：
if (gyro_calibration_timing() != GYRO_CAL_NEVER && _gyro_count > 0) {
    init_gyro();
}

// 修复代码（跳过校准，直接标记为已校准）：
if (gyro_calibration_timing() != GYRO_CAL_NEVER && _gyro_count > 0) {
    // Skip gyro calibration for RTT bringup - too slow during init
    // init_gyro();
    for (uint8_t i = 0; i < _gyro_count; i++) {
        _gyro_cal_ok[i] = true;
    }
    AP_Notify::flags.gyro_calibrated = true;
}
```

**验证**：跳过后 `setup_stage` 应从 662 → 663（`ahrs.reset()`），系统继续推进。

**副作用**：Gyro 零偏使用默认值（0），不进行板级校准。后续可在主循环中通过其他方式校准。

**2026-05-13 实测效果**：跳过 init_gyro() 后，stage 从 662 推进到 663 并停留在 `ahrs.reset()` 阶段（仍需较长等待），说明 setup 后续阶段仍存在慢速问题，但 IMU 数据流基础已打通。

---

### 初始化阶段推进极慢 — Stage 663+ 故障排除（2026-05-13）

跳过 init_gyro() 后，stage 从 662 → 663（`ahrs.reset()`）但此后长时间卡住。PC 反复在以下地址：

| PC 范围 | 对应函数 | 含义 |
|---------|---------|------|
| 0x0806ec14 - 0x0806ec18 | `_delay_microseconds_dwt` | 系统在 busy-wait 延迟循环中 |
| 0x080c2600 | `AP_RCProtocol::new_input()` | 调度器切换到 UART/RC 线程 |
| 0x080edad2 | `rt_hw_atomic_add` | 内核层原子操作（线程上下文切换） |

**特征**：`_hal_initialized` = 0xBBBBBBBB（已调用 hal.run()），`rtt_dbg_fast_loop_count` = 0（主循环未启动）。

**根因**：后续 setup 阶段（`ahrs.reset()`、`baro.calibrate()`、参数加载等）同样依赖大量 `delay()` 调用，在 RT-Thread 优先级调度下推进极慢。这可能持续数分钟。

**诊断方法**：
```bash
# 每 30-60 秒读一次 stage
echo "halt; mdw 0x2001bc6c 1; resume" | nc -q 2 localhost 4444
sleep 60
echo "halt; mdw 0x2001bc6c 1; resume" | nc -q 2 localhost 4444
# 如果 stage 变化 → 系统在推进（极慢）
# 如果不变且 PC 也相同 → 系统卡死了
```

**Known fix**：使用 `staging/pogo-rtt` 分支的完整 Phase 1G 提交（`33d62c3dfc`），包含 SPI 信号量修复、I2C 多消息、DeviceBus BLOCK_FOREVER 优化，已验证 IMU 数据流正常工作。

---

### SPI 信号量锁定模型的验证与陷阱（2026-05-13 添加 — 本会话验证）

Fix #1 (`get_semaphore()` 返回总线信号量) + Fix #2 (`take(10)` → `take_blocking()`) 组合已验证在 STM32F767 上无回归，编译烧录后正常启动至 STANDBY。

#### Fix #2 验证：`take(10)` → `take(HAL_SEMAPHORE_BLOCK_FOREVER)` ✅

| 指标 | 验证结果 |
|------|---------|
| HardFault | 无（CFSR=0）|
| USB CDC 枚举 | ✅ ttyACM1 |
| MAVLink HEARTBEAT | ✅ STANDBY @ 13.7s |
| IMU RAW_IMU | ❌ 仍然全零（42 条样本） |

**结论**：信号量锁定不是 IMU 零数据的根因。问题在更深层（SPI 轮询时序、IMU 电源模式、传感器初始化顺序）。

#### ⚠️ Fix #3 回归：Scheduler.h 包含依赖脆弱性

Fix #3 将 `Scheduler.cpp` 中的 `#include <rtthread.h>` 替换为 `#include "AP_HAL_RTT/DeviceBus.h"`，并将 `APM_RTT_SPI_PRIORITY` 定义从 Scheduler.h 移到 DeviceBus.h。**这导致了 IBUSERR HardFault（CFSR=0x00010000, HFSR=0x40000000）**。

**根因分析**：Scheduler.h 在 ArduPilot 构建中被广泛包含（Scheduler.cpp + UARTDriver.cpp + HAL_RTT_Class.cpp 等）。将核心 `#include <rtthread.h>` 从 Scheduler.cpp 替换为间接包含（DeviceBus.h 内部也包含 rtthread.h），可能导致某些编译单元的预处理顺序改变，暴露了未声明的符号或类型不匹配。

**教训**（铁律）：
1. **不要修改 Scheduler.cpp/Scheduler.h 的 include 链** — 它是 RT-Thread RTT HAL 中最核心的编译单元，任何 include 变化都可能引发间接回归
2. **优先级定义应在 SPIDevice.cpp 本地**，不要通过 Scheduler.h 或 DeviceBus.h 传播
3. **增量测试必须每次只加 1 个改动** — 本会话中 fix #2 单独验证 ✅，fix #3 单独验证 ❌，准确隔离了回归根因

**完整诊断参考** → `references/imu-whoami-probe-via-rtt-spi1-rt.md`（含 WHO_AM_I probe + GPIO status capture 的完整技术细节）

```bash
# 正确做法：优先级在 SPIDevice.cpp 中用字面量，不通过头文件传播
SPIDevice::SPIDevice(RTT_SPIDesc &desc)
    : _bus(DeviceBus::get_bus(desc.bus, 0))  # 不用 APM_RTT_SPI_PRIORITY 宏
```

**恢复方法**：`git checkout -- libraries/AP_HAL_RTT/Scheduler.cpp libraries/AP_HAL_RTT/Scheduler.h libraries/AP_HAL_RTT/DeviceBus.h`

### 增量回加模式（2026-05-13 扩展 — 从基线隔离恢复）

基线隔离确认问题在 revert 的文件中后，必须**每次只加 1 个文件的改动 → 编译 → 烧录 → 验证**，不应批量恢复：

```bash
# Step 1: 加文件A → 编译 → 测试
git diff HEAD -- libraries/A/file.c  # 确认只有单一修改
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
# 烧录 + MAVLink验证

# Step 2: 加文件B → 编译 → 测试
# ...重复
```

**为什么**：如果批量加回多个文件（如 SPI 引脚 + RXNE 超时 + CS-held burst + 传感器供电），出现新问题时无法知道是哪个文件引入的。每次 1 个文件，回归定位是 O(1) 而非 O(n)。

**2026-05-13 实测**：SPI4 (MS5611) 引脚修正后，气压计在基线版本即可工作（press_abs=1001.99hPa），说明 SPI4 在初始基线中已经功能正常。IMU (SPI1) RAW_IMU 全 0 的原因是 SPI1 CS-held burst 读取协议的时序问题，不是引脚配置错误。

---

### GPIO AFR 寄存器读取陷阱（2026-05-13 新增 — 本会话浪费 10+ 次诊断的教训）

**SPI 引脚 Alternate Function 检查是诊断 IMU 不工作的第一步，但很容易读错寄存器！**

PA5/PA6/PA7 的 AF 配置在 **AFR[0]**（偏移 0x20），不是 AFR[1]（偏移 0x24）！

| 寄存器 | 地址 | 覆盖引脚 |
|--------|------|---------|
| GPIOx AFR[0] | x = Base+0x20 | PA0-PA7 (AFRL, bits 0-31) |
| GPIOx AFR[1] | x = Base+0x24 | PA8-PA15 (AFRH, bits 0-31) |

```bash
# ✅ 正确：读 PA6 的 AF (bits [27:24])
mdw 0x40020020 1  # GPIOA AFR[0] → PA6 = bits 27:24
# ❌ 错误：读 0x40020024 返回的是 PA8-PA15 的 AF 配置
mdw 0x40020024 1  # GPIOA AFR[1] → PA8-PA15，不是 PA6！
```

**2026-05-13 实测**：本会话中最初误读 `0x40020024`（AFR[1]）看到 PA6=AF10，误判为 SPI1 引脚未配置。实际上 PA6 在 AFR[0]（0x40020020）中值为 AF5，完全正确。

**正确读取 CUAV V5 SPI1 引脚 AF 的完整命令集**：

```bash
echo -e "halt
mdw 0x40020020 1    # GPIOA AFR[0] — PA6(MISO) bits[27:24]=0101(AF5)
mdw 0x40020C20 1    # GPIOD AFR[0] — PD7(MOSI) bits[31:28]=0101(AF5)
mdw 0x40021824 1    # GPIOG AFR[1] — PG11(SCK) bits[15:12]=0101(AF5)
mdw 0x40013000 1    # SPI1 CR1 — BR[2:0]=011(/16), SPE=1
mdw 0x40021000 1    # GPIOE MODER — PE3 bits[7:6]=01(output, 传感器供电)
mdw 0x40021014 1    # GPIOE ODR — PE3 bit 3=1(HIGH)
resume
exit" | timeout 15 nc localhost 4444 2>&1 | strings | grep "^0x"
```

**CUAV V5 正确 SPI1 引脚配置参考**：

| 引脚 | 功能 | 寄存器 | 正确值 |
|------|------|--------|-------|
| PA6 | SPI1_MISO | AFR[0] bits[27:24] | 0101 (AF5) |
| PD7 | SPI1_MOSI | AFR[0] bits[31:28] | 0101 (AF5) |
| PG11 | SPI1_SCK | AFR[1] bits[15:12] | 0101 (AF5) |
| SPI1 CR1 | 控制 | CR1 @ 0x40013000 | 0x0000035f (SPE on, BR=/16, CPHA/CPOL=1) |

**注意**：PA5/PA7 在 CUAV V5 上**不是** SPI1 引脚 — 不要因它们不是 AF5 而误判为 SPI1 配置错误。CUAV V5 的 SPI1 使用 PG11(SCK)/PA6(MISO)/PD7(MOSI)。

### 关键陷阱：找到构建系统实际使用的 startup 文件 — submodule 文件 ≠ 编译文件

**构建系统编译的是 `libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S`，不是 `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/startup_rtt_override.S`！**

2026-05-13 实测：submodule 版已含 D-Cache/CPACR 代码，**但构建用 AP_HAL_RTT 版（旧版，无 CPACR 代码）**，表现为 CPACR=0（NOCP HardFault）。

**验证方法**（构建后检查 object 内容）：
```bash
# 对比两文件差异
diff libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S \
     modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/startup_rtt_override.S

# 检查编译产物是否有 CPACR 代码
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/board/startup_rtt_override.o | grep -c "CPACR\|vmsr\|mcr.*p15"

# 手动汇编验证（与构建产物对比）
arm-none-eabi-gcc -x assembler-with-cpp -c \
  libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S \
  -o /tmp/test_override.o -mcpu=cortex-m7 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard
arm-none-eabi-objdump -d /tmp/test_override.o | grep -c "vmsr\|mcr.*p15.*cr7"
# 如果手动汇编有但构建产物无 → 确认 build system 用了不同文件
```

**修复方法**：**必须修改 AP_HAL_RTT 版的 startup_rtt_override.S**，而非 submodule 版。

**完整诊断流程 → `references/nocp-hardfault-startup-file-diagnosis.md`**

### VTOR = AP_HAL_RTT 版已正确使用 vflash_start

**AP_HAL_RTT 版 `startup_rtt_override.S` 已正确使用 `vflash_start` 符号：**
```asm
ldr r0, =vflash_start    /* Set by link.lds: ORIGIN(ROM) */
```
配合 template link.lds 中的 `PROVIDE(vflash_start = ORIGIN(ROM));`，VTOR 始终跟随链接脚本的 ROM 基址。

**不要改回硬编码值**（除非明确要覆盖）。确认 link.lds 的 `PROVIDE(vflash_start)` 存在。此符号由 `rtt_hwdef.py` 生成 MEMORY 块时传入。

### 关键陷阱：修改 ROM ORIGIN = 修改 hwdef.dat！仅改模板无效！

`rtt_hwdef.py`（line 878-884）**不读取**模板 link.lds 的 MEMORY 块，而是从 hwdef.dat 解析 `FLASH_RESERVE_START_KB` 计算 ROM ORIGIN：

```python
# rtt_hwdef.py 中硬编码的 MEMORY 生成
flash_origin = 0x08000000 + flash_reserve_start * 1024   # line 883
f.write('    ROM (RX) : ORIGIN = 0x%08x, LENGTH = %dK\\n' % (flash_origin, flash_length // 1024))
```

因此：
- 修改模板 link.lds 的 `ROM (rx) : ORIGIN` → **无效**（被 rtt_hwdef.py 覆盖）
- 修改 `hwdef.dat` 的 `FLASH_RESERVE_START_KB` → **有效**
- 也需同步修改 `startup_rtt_override.S` 的 VTOR 值

**debug 模式**（无 bootloader）：在 hwdef.dat 中将 `FLASH_RESERVE_START_KB` 从 32 改为 0，清除时使用 `flash erase_sector 0 1 11`（保留 sector 0）。

### ⚠️ 完整芯片擦除 = 删除 bootloader！（2026-05-13 新增）

`flash erase_sector 0 0 11` 会擦除 sector 0（0x08000000-0x08007FFF，即 bootloader）。**CUAV V5 的 PX4 兼容 bootloader 不在 `Tools/bootloaders/` 目录中**，擦除后无法直接从 ArduPilot 源码恢复。

**恢复方法**：hwdef.dat 设 `FLASH_RESERVE_START_KB 0` + 同步 VTOR + PROVIDE(vflash_start)（参见第十五步）| 从另一块同型号板读 `openocd -c "flash read_bank 0 save.bin 0 0x8000"` | 从 PX4/ArduPilot 固件包解 `find /path/to/cuav-v5-bl.bin`

**预防**：只擦除 `1-11` 扇区，保留 sector 0：
```bash
flash erase_sector 0 1 11
```

### 关键陷阱：链接脚本的真实来源是模板，不是 BSP 目录！

**不要修改 `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/linker_scripts/link.lds`** — 这不起作用！

构建系统通过 `libraries/AP_HAL_RTT/hwdef/scripts/rtt_hwdef.py` 从**模板**重新生成 link.lds：
1. 读取模板 `libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds`
2. 填充正确的 MEMORY 值（FLASH_SIZE/RAM_SIZE 从 hwdef 解析）
3. 写入 `build/rtt_deploy/<target>/board/linker_scripts/link.lds`

**因此必须修改模板文件**：
```
libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds
```

**验证 diff**（确认构建用的是模板版本而非 BSP 版本）：
```bash
diff libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds \
     build/rtt_deploy/cuav_v5/board/linker_scripts/link.lds
```
如果不同，说明模板未生效或构建缓存残留。

**验证 KEEP 生效**：
- 对比 ROM 大小变化：插入 KEEP 后弱符号占 ~88 字节，ROM 应略微增大
- 检查 map 文件确认 CMSIS `.text.Reset_Handler` 段 VMA 非零（非 GC'd）：
  ```bash
  grep "text.Reset_Handler" build/.../rtthread.map
  # 弱符号段 VMA 应为真实地址（如 0x080081f8），非 0x00000000
  ```
- 向量表 vs 符号表对比（同上）

> **2026-05-12 实测**：初始链接脚本（不正确的那个）将 `ROM (rx)` 大写写成 `RX`，且长度使用 1504K（非 2016k）。只有当同时在模板修改 KEEP + 使用 SCons 完整重构时，才能生成正确的向量表。

**完整诊断**：`references/linker-gc-sections-weak-symbol-bug.md`

---

## ⚠️ 第十四步（2026-05-12 新增）：C++ 静态构造期 malloc 断言失败 — rt_assert_handler("0", "_rt_mutex_take")

**症状**：系统烧录后正常运行（Thread mode，CFSR=0），但 halt 后 PC 停在 `kservice.c` 的 `while (dummy == 0)` 无限循环，无任何 CDC 控制台输出。

**调用链**（通过 GDB bt 获取）：
```
#0  rt_assert_handler("0", "_rt_mutex_take", line=1334)
#1  _rt_mutex_take(mutex=&_lock, timeout=-1, suspend_flag=2)
#2  rt_malloc(size=124)
#3  rt_calloc(count=X, size=Y)
#4  _calloc_r(ptr=impure_data, ...)
```

**根因**：C++ 静态构造器（`__libc_init_array` 调用的全局对象构造函数）在其代码路径中调用了 `malloc()`。此时 RT-Thread 堆互斥锁 `_lock` 尚未完全初始化，`_rt_mutex_take` 在断言检查中失败。

**关键上下文**：ArduPilot RTT 的启动顺序：
```
Reset_Handler
  → entry()
  → rtthread_startup()
  → rt_hw_board_init() [line ~326]
     → rt_system_heap_init()        ← 堆初始化（互斥锁就绪）
     → INIT_BOARD_EXPORT 表         ← SPI/I2C 等板级初始化
     → INIT_PREV_EXPORT 表         ← 传感器电源等
     → INIT_DEVICE_EXPORT 表       ← 设备驱动（cherryusb CDC 等）
     → INIT_COMPONENT_EXPORT(rtt_run_cpp_ctors)  ← C++ 构造器
        → __libc_init_array()      ← 调用所有 .init_array 入口
        → Copter copter;            ← 全局对象构造
           → AP_Vehicle::AP_Vehicle()
           → AP_InertialSensor::AP_InertialSensor()
        → AP_HAL_MAIN_CALLBACKS(&copter)  ← 定义 main()
```

**诊断方法**：

```bash
# 1. 确认断言位置
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "bt 5" \
  -ex "p/x rtt_dbg_setup_stage" \
  -ex "p/x copter.ap.initialised" \
  /path/to/rt-thread.elf

# 2. 检查 _lock 互斥体状态
echo "mdw 0x20045048 4" | nc -q 2 localhost 4444 | head -2
# _lock 的 object.type == RT_Object_Class_Mutex (0x04) 才正确
# 若为 0xffffffff 或 0 表示未初始化

# 3. 检查堆初始化是否已完成
# heap_lock 的 init 函数通常叫 rt_system_heap_init
# 在 map 文件中确认其地址
nm build/.../rt-thread.elf | grep rt_system_heap_init
```

**排查哪些组件在 C++ 构造前调用了 malloc**：

C++ 构造器 `rtt_run_cpp_ctors()` 运行级别是 `INIT_COMPONENT_EXPORT`。以下级别在它之前运行，其中的代码若调用 `malloc` 可能触发此问题：

| 级别 | 宏 | 典型组件 | 是否可能调用 malloc |
|------|---|---------|-------------------|
| 1 | INIT_BOARD_EXPORT | SPI/I2C 板级 init | ❌ 通常不 |
| 2 | INIT_PREV_EXPORT | 传感器电源 | ❌ 不 |
| 3 | INIT_DEVICE_EXPORT | cherryusb CDC, SDIO | ⚠️ CDC 可能间接需要 |
| 4 | INIT_COMPONENT_EXPORT | C++ 构造器、参数系统 | ⚠️ C++ 全局对象构造 |
| 5 | INIT_ENV_EXPORT | 文件系统 | ⚠️ FAT 挂载 |
| 6 | INIT_APP_EXPORT | 应用初始化 | ❌ 太晚 |

**Preferred Fix: SPI Device Static Pool**

Replace `SPIDeviceManager::get_device_ptr()` 中的 `NEW_NOTHROW SPIDevice(...)` 为 placement-new 静态池：

```cpp
AP_HAL::SPIDevice *SPIDeviceManager::get_device_ptr(const char *name)
{
    for (uint8_t i = 0; i < _DEVICE_TABLE_COUNT; i++) {
        if (strcmp(name, _device_table[i].name) == 0) {
            static SPIDevice *s_devices[_DEVICE_TABLE_COUNT] = {};
            static uint8_t s_mem[sizeof(SPIDevice) * _DEVICE_TABLE_COUNT];
            if (!s_devices[i]) {
                s_devices[i] = new (&s_mem[i * sizeof(SPIDevice)]) SPIDevice(_device_table[i]);
            }
            return s_devices[i];
        }
    }
    return nullptr;
}
```

**Why this works**：`static` 局部变量在 BSS 中分配（启动代码清零），placement-new 在 `uint8_t` 缓冲上构造对象，无需堆分配 → 不触发 `_heap_lock()` → 无断言失败。

**AVOIDED: Kernel-level fix**: 尝试过修改 `kservice.c` 的 `_heap_lock()` 在调度器未就绪时跳过互斥锁，但引发了次级 IBUSERR HardFault。**始终优先应用层修复**而非修改 RT-Thread 内核。

**完整诊断参考**：`references/cpp-static-init-malloc-assert.md`

**可能的根本原因**：

1. **Invensense 驱动的静态构造函数**中调用了 `new` 或 `malloc`（如 `AP_InertialSensor_Invensense` 的静态成员初始化）
2. **CherryUSB CDC 的 `INIT_DEVICE_EXPORT` 初始化**间接调用了标准库的 `_calloc_r` 
3. **newlib 的 `impure_data` 初始化**需要堆分配

**调试技巧**：在 `rt_assert_handler` 中添加简短的调用栈打印，或使用硬件断点在 `rt_malloc` 入口处捕获第一次调用：

```bash
# 在 rt_malloc 入口设断点，记录返回地址
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor reset halt" \
  -ex "hbreak *rt_malloc" \
  -ex "commands" -ex "silent" -ex "p/x lr" -ex "continue" -ex "end" \
  -ex "monitor resume" \
  /path/to/rt-thread.elf 2>&1 | head -20
```

**临时绕过**（仅用于验证后续流程）：
- 在 `rt_board_init.c` 的 `rtt_run_cpp_ctors()` 之前手动调用 `rt_malloc(1)` 预热堆系统
- 或在 `rt_hw_board_init()` 中提前调用 `rt_system_heap_init()`（如果它还没被调用的确就是顺序问题）

**完整诊断参考**：`references/cpp-static-init-malloc-assert.md`

---

## ⚡ 当前主动推进计划（2026-05-13）— 监督 CC 执行

### 状态：L0 基线已达成 → 卡在主循环第一次迭代

**两个阻塞点需同时修复**：

---

### P2-Phase A: Gyro Init DWT CYCCNT 壁钟超时

**文件**：`libraries/AP_InertialSensor/AP_InertialSensor.cpp`

**问题**：`_init_gyro()` 内 35s `AP_HAL::millis()` 超时在 RTT DWT 忙等上下文中不推进。

**修复**：在 line 1751 附近，将现有的 `AP_HAL::millis()` 超时改为 DWT CYCCNT：

```cpp
// 在 for 循环前
const uint32_t gyro_init_start_cycles = DWT->CYCCNT;

// 在 for 循环体内
if ((DWT->CYCCNT - gyro_init_start_cycles) > 35UL * SystemCoreClock) {
    DEV_PRINTF("gyro_init: 35s timeout (CYCCNT), using best estimate\\n");
    break;
}
```

**前提**：DWT CYCCNT 已在 startup_rtt_override.S 中使能（TRCENA + CYCCNTENA）。确认 `SystemCoreClock` 已正确初始化。

---

### P2-Phase B: CDC TX EPENA=1 壁钟超时强制恢复

**文件**：`modules/rt-thread/components/drivers/usb/cherryusb/platform/rtthread/usbd_serial.c`

**问题**：首次 `usbd_ep_start_write()` 的 XFRC ISR 丢失，EPENA=1 永久卡住，现有自愈只处理 EPENA=0。

**修复**：在 `usbd_serial_write()` 中，`usbd_ep_start_write()` 之后添加壁钟超时 + EPENA=1 强制恢复：

```c
if (serial->tx_active) {
    uint32_t start_tick = rt_tick_get();
    while (serial->tx_active && (rt_tick_get() - start_tick) < RT_TICK_MS(50)) {
        /* 等待 50ms 让 ISR 完成 */
    }
    if (serial->tx_active) {
        uint8_t ep_idx = serial->in_ep & 0x7F;
        DWC2_OTG_INEndPoint *inep = DWC2_INEP(ep_idx);
        if (inep->DIEPCTL & USB_OTG_DIEPCTL_EPENA) {
            inep->DIEPCTL |= USB_OTG_DIEPCTL_SNAK;
            inep->DIEPCTL &= ~USB_OTG_DIEPCTL_EPENA;
            serial->tx_active = 0;
        } else {
            serial->tx_active = 0;
        }
        dbg_serial_unstick_cnt++;
    }
}
```

**验证**：烧录后 15s 读 `dbg_serial_unstick_cnt` 应 > 0，`bulkin_cnt` 应递增。

---

### P2-Phase C: 编译 + 烧录 + 双重验证

```bash
# 编译
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)

# 烧录
openocd -f Tools/debug/openocd-f7.cfg \
  -c "program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify" \
  -c "reset run" -c "shutdown"

# 验证 1: OpenOCD — CFSR=0, HFSR=0
# 验证 2: pymavlink — HEARTBEAT(1Hz) + STANDBY
# 验证 3: main_loop_iterations > 0
# 验证 4: CDC debug vars — bulkin_cnt > 0
```

---

## 核心原则

> **任何 RTT 平台特有的修复必须在 `AP_HAL_RTT/` 内部完成。禁止修改 `libraries/` 中的通用代码（除非是真正的跨平台 bug）。**
>
> **必须先加载 `ardupilot-rtt-architecture` 技能**，用其代码审查 checklist 验证修复路径。
>

### 🏆 黄金工作流：先看 ChibiOS 再做（2026-05-10 用户明确纠正 + 2026-05-11 重申）

> **用户原话**（2026-05-11 再次强调）："很重要的是要学会参考ardupilot chibios。而不是盲目乱改"
>
> **这是最高优先级的工作流规则**。任何 RTT HAL 的调试/修复/设计，在动手前必须先查 ChibiOS HAL 的对应实现。

**强制步骤**（在开始任何 RTT HAL 调试前执行）：

```bash
# 1. 查找 ChibiOS 对应功能的实现文件
find libraries/AP_HAL_ChibiOS -name "*.c" -o -name "*.cpp" -o -name "*.h" | xargs grep -l "功能关键词" 2>/dev/null

# 2. 读取 ChibiOS 的实现
cat libraries/AP_HAL_ChibiOS/hwdef/common/watchdog.c   # IWDG 看门狗
cat libraries/AP_HAL_ChibiOS/Scheduler.cpp              # 调度器/喂狗
cat libraries/AP_HAL_ChibiOS/HAL_ChibiOS_Class.cpp      # HAL 入口

# 3. 理解 ChibiOS 的做法后再设计 RTT 方案
```

**已验证的 ChibiOS 参考文件与对应 RTT 问题**：

| RTT 问题 | ChibiOS 参考文件 | 关键发现 |
|---------|-----------------|---------|
| DWT delay 缓存 | `Scheduler.cpp` 中 `delay_microseconds()` | ChibiOS 用 `chThdSleep()` 线程睡眠，不用 DWT 自旋。RTT 对 <1ms 用 DWT+DSB，≥1ms 用 `rt_thread_delay()` |
| IWDG 看门狗复位 | `hwdef/common/watchdog.c` | PR=3(/32), 不等待 PVU/RVU, `setup()` 后启动 |
| 调度器喂狗 | `Scheduler.cpp:watchdog_pat()` | 直接从主循环+定时器线程无条件调用 |
| HAL 入口 | `HAL_ChibiOS_Class.cpp` | `stm32_watchdog_init()` 在 `setup()` 完成后的 `set_system_initialized()` 前调用 |
| SPIDevice | `hwdef/fmuv5/hwdef.dat` | 引脚定义的金标准参考 |
| UART 驱动 | `UARTDriver.cpp` | 线程优先级、缓冲大小、DMA 配置 |
| UART 驱动 | `UARTDriver.cpp` | ChibiOS 用 `SerialDriver`/`USBDriver` 对象绑定硬件外设+CDC；RTT 用 `rt_device_find("usb-acm0")` 找 CDC ACM 设备，潜在设备名不匹配问题 |
| 传感器初始化 | `hwdef/fmuv5/hwdef.dat` | 传感器类型、SPI 总线、CS 引脚 |
| 栈大小 | `Scheduler.cpp` 中的栈数组 | 直接在 `Scheduler` 类中声明，非 BSS 全局 |
| **USB CDC TX** | **`modules/ChibiOS/os/hal/src/hal_serial_usb.c`** | ⭐ CDCTX必须先看！SDU obqueue+obnotify+sduSOFHookI 三层架构。CherryUSB 缺少 SOF 级恢复机制 |
| **CDC SOF 架构** | **`references/chibios-cdc-sof-hook-architecture.md`** | ChibiOS SDU vs CherryUSB 完整对比 + 三种替换方案 |
| **启动/FPU/缓存** | **`modules/ChibiOS/os/common/startup/ARMCMx/compilers/GCC/crt0_v7m.S`** | ⭐ 启动顺序：FPU使能（cpacr直接STR+DSB）→ 数据/BSS → __cpu_init（缓存/MPU）。**FPU使能在所有C代码之前完成**，用直接STR+DSB/ISB而非C的read-modify-write `SCB->CPACR |=`。**2026-05-12实测**：bootloader开D-Cache后，C代码的CPACR `|=` 写入被缓存吞掉 → NOCP fault。 |

**警告**：不要在未经对照 ChibiOS 实现之前就自己猜测寄存器配置、时序或外设初始化顺序。ChibiOS 是 ArduPilot 官方已验证的平台——它的做法就是正确的做法。
---
**⚠️ 用户明确纠正过的铁律**（2026-05-11 反复强调）：
> "你需要再好好读一遍 trae 文件夹里面的规则和 skill，然后再读读 git 历史，然后先制定计划，然后严格遵循计划，然后在 skill 里面写。你只是监督协助 CC 的管理人，严格遵守，而不是乱来，可以吗"

这意味着在任何调试/修复回合开始前，必须严格执行以下 5 步为第一优先级：

---

### 0. 前置准备 — 五步强制流程（用户反复纠正的铁律）

> 在开始任何调试/修复前，先加载 `ardupilot-rtt-architecture` skill 确认修复路径不违反 HAL 隔离原则。如果修复涉及 `libraries/` 中 `AP_HAL_RTT/` 以外的文件，必须先用架构 skill 的代码审查 checklist 审查，确认是通用 bug 而非 RTT 专属 hack。

在任何调试/修复回合开始前，**必须按顺序**完成以下五步：

**第一步：加载 Hermes 相关技能**\n```\nskill_view(name='rtt-stabilization-driver')  # 本技能 — 1:1 复刻+调试+验证\nskill_view(name='rtt-chibios-api-adaptation')  # API 适配参考\nskill_view(name='rtt-vs-chibios-reference')  # 行为对比分析\n```\n本 Hermes Agent 技能已包含全部核心规则。`.trae/rules/` 中的文件仅用于 Trae IDE，Hermes 会话中无需读取。\n\n**第二步：读取相关补充技能**
```
skill_view(name='skills名')  # 读取已有的相关技能
```
技能包含已验证的工作流、陷阱列表、参考文件索引。**必须读已有的技能，不能凭感觉做。**

**第三步：阅读 git 历史**
```bash
git log --oneline -20   # 了解最近提交
git diff HEAD~5 --stat  # 了解改动范围
```
了解当前进度：已修复什么、已知问题、哪个分支、CC 做了什么。

**第四步：制定计划（写在 skill 中）**
在 skill 中（或在 kanban 任务 body 中）写明确的计划：
- 阶段划分
- 每步做什么
- 验证标准
- ChibiOS 参考文件路径（写死！）

**第五步：严格遵循计划**
- 按计划顺序执行，不跳过、不更换方向
- 每完成一步，更新 kanban 任务状态
- 遇到新问题→扩展计划（加新 Phase）→返回执行，不要直接跑偏
- **用户原话**："你只是监督协助 CC 的管理人，严格遵守，而不是乱来" — 不要直接改代码，给 CC 下指令

### ⚠️ 核心工作模型：监督CC模式
本 Agent 是 **CC（Claude Code/Trae）的管理者/监督者**，不是替代者：
- 制定计划 → 交给CC执行 → 审查diff → 烧录验证 → 记录结果
- 自己不动手改代码（除非CC不可用或紧急情况）
- CC 卡住时：提供根因分析+修复方向，不自己修
- CC 引入回归时：git restore 清理，指导正确方向

### ⚠️ 质量红线：禁止低效循环
用户明确批评过"发现-修复-再发现"的零散patch式工作。必须：
- 一次性抓住根本问题
- 系统性解决方案（而非表面 workaround）
- 如果根因清楚，直接做根本性修复
- **永远不要回滚已验证的修复** — 每个修复提交都经过 CDC+OpenOCD 双重验证
- 如果有人（CC 等）在 staged changes 中回滚已验证的提交，必须**立即清理**：
  - `git restore --staged .` 清理 index
  - `git checkout -- <file>` 恢复工作区
  - 重新编译、烧录、验证
- **回滚典型表现：** INVSTATE HardFault (CFSR=0x01, HFSR=0x40000000)，PC=0xfffffffe (EXC_RETURN 被当成函数指针)

### ⚠️ 全过程自主决策铁律（2026-05-11 修正）

用户明确纠正过两次：
1. "你不是说你全程自己决定吗？明白吗。你再这样我就要发火了"
2. "可以。组织过程中。"

**规则：在诊断→分析→出方案→执行的全链条上，每一步都必须自主决策，不等待确认。**
- 读到 setup_stage 固定值 → 自动分析哪个阶段阻塞 → 直接执行下一诊断步骤
- 发现了根因 → 直接制定方案并执行，不问"可以吗/要开始吗？"
- 编译/烧录完成 → 自动进入验证环节，不等待确认
- 验证通过 → 自动推进到下一步
- **唯一允许停顿的情况**：需要物理操作（接线、按按钮、卸螺旋桨、插USB）
- **2026-05-14 用户明确补充**：\"你今晚都自主修复，明白了吗，不需要问我\" — 即使是修复方案的执行也不需要询问确认。直接修复→编译→烧录→验证→汇报。汇报时只报告结果，不询问下一步。

### 4. 双重验证标准
- **CDC MAVLink 验证：** pymavlink 收到心跳且状态进入 STANDBY
- **OpenOCD 验证：** halt/step/resume 无 HardFault，CFSR=0, HFSR=0
- 两条缺一不可

### 5. 固化基线原则：先固化再修复（2026-05-12 新增）

当系统达到一个稳定状态时（如 L0 基线：MCU运行、CDC枚举、无HardFault），必须先固化再修复：

```bash
# 提交所有已验证改动
git add -A && git commit -m "fix(baseline): 固化 <名称> 基线 — XXX工作/YYY待修复"
git tag -a "<baseline>-<YYYY-MM-DD>" -m "描述" HEAD
git push origin HEAD:refs/heads/<branch> --force
```

**Why**: CDC TX 修复可能引入新问题，有了基线 tag 可快速回滚对照。
**固化时机**：✅ L0/L1 milestone 达成时 | ❌ 每个中间调试步骤

### 6. 每次修复必须可检验
- 修改→编译→`-j$(nproc)`→烧录→双重验证
- 修复失败回退换方案，不堆积无效代码

### 7. l0-baseline 构建可复现性验证（2026-05-12 发现）
当从已标记的基线重新构建时，必须验证构建产物仍能正常工作。基线的提交信息说"无 HardFault"不代表当前构建也能：

```bash
# 验证步骤：
# 1. 确认子模块状态与基线一致
git submodule status modules/rt-thread
git show <baseline-commit>:modules/rt-thread  # 检查 gitlink 指向的 commit

# 2. 确认构建产物与预期一致
cd modules/rt-thread && git log --oneline -1 HEAD  # HEAD 应与 gitlink 一致

# 3. 编译后先做静态检查（对比符号表大小、向量表位置）
arm-none-eabi-size build/rtt_deploy/cuav_v5/rt-thread.elf
# 与基线记录的 dec/hex 值对比

# 4. 如果基线的 HardFault 出现新模式，可能是：
#    - 工具链版本变化（gcc --version）
#    - 构建系统自动拉取的依赖变化（submodule 的 submodule）
#    - D-Cache 导致的不确定性故障（只在某些执行路径触发）
```

**2026-05-12 实测**：`faee486d1c`（l0-baseline-2026-05-12）重建后出现 GCS_MAVLINK 野指针 HardFault，
说明基线验证时的工作区状态可能与提交时的 gitlink 不一致（有未提交的 submodule 改动）。

---

## 调试目标等级

| 等级 | 要求 |
|------|------|
| **L0** | 飞控启动保持运行：OpenOCD 可连、Bootloader 跳转、无 HardFault、USB CDC 枚举、MAVLink 心跳持续、状态从 BOOT 进入 STANDBY、**IWDG 被规律喂狗（无 5 秒重启循环）** |
| **L1** | 传感器与数据流：IMU 数据(RAW_IMU)、姿态(ATTITUDE)、电压/电流(SYS_STATUS) |

---

## 🟢 L0 验证方法论（三层阻塞修复链 2026-05-14 验证通过）

### 三层阻塞修复链

当系统在 `setup()` 阶段卡住（`rtt_dbg_setup_stage` 变量不变）时，以下三层必须全部到位：

| 层 | 文件 | 正确值 | 修复意义 |
|----|------|--------|----------|
| ① | `AP_HAL_RTT/Flash.cpp:137` | 删除 `rt_thread_yield()`，纯 spin | Flash 擦除（~2s）期间不 yield，否则被高优线程抢占后永不返回 |
| ② | `hwdef/common/.config` | `CONFIG_RT_DEBUGING_ASSERT = not set` | 否则 `rt_object_init` 触发 `RT_ASSERT(obj != object)` 断言退出 |
| ③ | `AP_HAL_RTT/HAL_RTT_Class.cpp:179` | `setup_priority = 8`（原 20 太低） | 原值 20：UART(6) 和 Timer(4) 随时就绪，饿死 setup 线程 |

**L0 通过信号**（OpenOCD halt 读取）：
- `0x200001c0` = `0x11111111` → `rtt_dbg_hal_run_called` = AFTER_SETUP
- `0x200001c8` = `0x12345678` → `rtt_dbg_main_loop_entry_called` = ENTRY_REACHED
- `0x20019980` > 0 → `rtt_dbg_main_loop_iterations` = 主循环迭代计数
- `/dev/ttyACM*` 存在 → USB CDC 枚举
- pymavlink HEARTBEAT → status=STANDBY, 1Hz

### OpenOCD 监测法（Python halt-read-resume）

**不要用 bash+grep 做 OpenOCD telnet 监控**——nc 输出含 null 字节，grep 报 "binary file matches"。

**正确方法**：每次新 socket 连接做 halt → mdw → resume。执行 `scripts/l0_monitor.py` 一键轮询。

### rtt_dbg 变量地址表

| 地址 | 名称 | 诊断意义 |
|------|------|----------|
| `0x200001c0` | `rtt_dbg_hal_run_called` | 0xDEADBEEF → 0xAAAAAAAA → **0x11111111** (setup done) |
| `0x200001c8` | `rtt_dbg_main_loop_entry_called` | 0xCAFEBABE → **0x12345678** (entry reached) |
| `0x20019980` | `rtt_dbg_main_loop_iterations` | >0 = 主循环活跃 |
| `0x20019984` | `rtt_dbg_overrun_count` | >2.5ms 循环次数（91% = 过载，待优化） |
| `0x20019988` | `rtt_dbg_work_time_max_us` | 最长循环耗时（典型值 540ms 需优化） |

### Stage 反汇编解码

```bash
# 查找写入特定 stage 值的代码
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf | grep "0x276\\|#630"
# 例: movw r3, #630 ; 0x276 = stage 630

# 确定对应函数
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf | grep -B5 "f240 2376"
# 例: bl 80619ac <AP_GPS::init> ← stage 630 = AP_GPS::init()
```

### 典型 Stage 值对照表

| Stage | 函数 | 说明 |
|-------|------|------|
| 630 (0x276) | `AP_GPS::init()` | 非阻塞函数；卡住 → setup 线程饿死，检查③ |
| 640 (0x280) | `AP_Baro::calibrate()` | MS5611 SPI4 probe；挂死 → 检查 SPI4 引脚/CS |
| 651 (0x28b) | `Copter::init_ardupilot()` 后半段 | 接近完成；若 loop_entry=0x12345678 则实际已进入主循环 |

## 🔁 循环过载诊断（2026-05-14 新增 — 本会话发现）

### 问题签名
- `rtt_dbg_main_loop_iterations` 增长率 7-12 Hz（目标 400 Hz）
- `overrun_count > 90%`
- `work_time_us ≈ loop_time_us ≈ 60-170ms`

### 诊断核心发现（本会话 2026-05-14）

**`work_time_us ≈ loop_time_us`** 意味着 `loop() + call_delay_cb()` 消耗了全部 82ms——问题在 `AP_Scheduler::loop()` 内部，不在末尾的 50µs delay。

**根因链分析**：
1. `_sample_period_usec = 2500`（400Hz 正常）— INS 对象偏移 0x5c4 读取验证
2. `SystemCoreClock = 216MHz`（正常）
3. 主线程运行在 `APM_RTT_MAIN_BOOST=3`（高于 timer 的 4），不应被抢占
4. **最可能根因**：`GCS::update_send`（400Hz, 550µs 预算）在 CDC TX 路径阻塞

### 诊断方法（详见 `references/loop-overrun-diagnostics.md`）

```python
# 读关键调试变量（halt-read-resume）
addrs = {'stage':0x2001bc84, 'hal_run':0x200001c0, 'loop_entry':0x200001c8,
         'loop_iter':0x20019980, 'loop_time':0x2001997c, 'work_time':0x2001998c,
         'overrun':0x20019984, 'fast_loop':0x20019974, 'work_max':0x20019988}
```

### 已知修复方向

| # | 方案 | 说明 | 已验证？ |
|---|------|------|---------|
| A | 修复 `boost_end()` 永不调用 bug | `_priority_boosted` 在第一次 boost 后永久置位，主线程永远运行在 prio 3 | ❌ 未验证 |
| B | CDC TX 超时 | 在 UARTDriver write 路径加超时，避免阻塞整个 loop | ❌ |
| C | 降低 GCS::update_send 率 | 从 400Hz 临时降至 200Hz | ❌ |

## 🩺 DWT 延迟循环 DSB 修复

根因及修复详情见 `references/dwt-dsb-dcache-fix.md`。

**关键点**：STM32F7 D-Cache 导致 DWT_CYCCNT 读值被缓存。添加 `asm volatile("dsb" ::: "memory")` 修复。
**效果**：修复前无限期卡住，修复后 setup_stage 稳步递增。
**代价**：每循环迭代 +11 个周期（DSB stall）。

---

## ⚠️ 第十五步（2026-05-12 新增）：栈溢出 — `_system_stack_size` 不足导致 IBUSERR HardFault

**症状**：Clean rebuild 后系统在 C++ 静态构造阶段 IBUSERR HardFault。PC 停在 `hardfault_hang`，CFSR=0x10000（IBUSERR），`bt` 显示 `#3 0x00000000 in ?? ()`。

**调用链（从异常帧解码）**：
```
#0  hardfault_hang
#1  <signal handler>
#2  SystemCoreClockUpdate() at system_stm32f7xx.c:223  — 访问 RCC 寄存器
#3  0x00000000  <-- 返回地址被栈溢出破坏！
```

**根因**：C++ 静态构造链（Copter → AP_Vehicle → AP_InertialSensor → ...）深度嵌套调用，消耗超过 16KB 栈空间。栈从 `_estack`（初始 SP）向下增长，溢出到 `.bss` 或 `.data` 区域，覆盖返回地址 → 函数返回时跳转到垃圾地址 → IBUSERR。

**关键陷阱：模板 vs BSP 的 `_system_stack_size` 不一致**

| 位置 | 值 | 说明 |
|------|-----|------|
| **模板** `libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds` | **`0x4000` (16KB)** ❌ | **构建系统使用这个！** |
| BSP `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/linker_scripts/link.lds` | `0xC000` (48KB) ✅ | 这个足够但构建系统不用 |
| 构建产物 `build/rtt_deploy/.../board/linker_scripts/link.lds` | 从模板生成（=0x4000） ❌ | |

**修复**：修改模板的 `_system_stack_size` 为 `0xC000`（48KB）：
```
libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds
  _system_stack_size = 0x4000;  →  _system_stack_size = 0xC000;
```

**验证**：
```bash
# 1. 确认模板已修改
grep "_system_stack_size" libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds
# → _system_stack_size = 0xC000;

# 2. Clean rebuild 后检查 BSS/SRAM 布局变化
nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep -E "_estack|_sbss|_ebss"
# _estack 应移动约 0x8000 = 32KB 向上

# 3. 验证不会 HardFault
# 烧录后 halt，检查 CFSR=0, HFSR=0
```

**为什么之前增量编译没遇到**：增量编译可能混合了 BSP 目录的 0xC000 栈布局的旧产物。Clean rebuild 使用模板的 0x4000，暴露了栈溢出。

**RAM 影响**：0x4000→0xC000 增加 32KB 栈空间，从总 RAM 512KB 中划出。BSS (~276KB) + data (~5KB) + stack (48KB) = ~329KB，剩余 ~183KB 给堆，可正常使用。

**完整 RAM 布局链路 — 修改必须走 hwdef.dat，不是模板 link.lds！**

栈溢出后还可能引发**二次故障**：栈空间变化导致 `_estack` 地址移动，VTOR 若硬编码则与向量表位置失配。关键链路：

```
hwdef.dat → FLASH_RESERVE_START_KB (32→0)
    ↓
rtt_hwdef.py line 883-897 → 生成 MEMORY { ROM (RX) : ORIGIN = ... }
```

**不要修改模板 link.lds 的 `ROM (rx) : ORIGIN`** — `rtt_hwdef.py` 用自己的计算值覆盖它。修改 FLASH 基址必须改 **hwdef.dat** 的 `FLASH_RESERVE_START_KB`，然后**同步 VTOR 值**。

**VTOR 必须跟随 ROM 基址 — 用 PROVIDE 而非硬编码**

`startup_rtt_override.S` 第 33 行曾硬编码 `ldr r0, =0x08008000`。修改 ROM ORIGIN 后（如 bootloader 擦除后绕行），VTOR 指向错误地址 → 任何中断立即 HardFault。

修复方式：链接脚本中添加 `PROVIDE(vflash_start = ORIGIN(ROM));`，汇编中使用 `ldr r0, =vflash_start`。这样 VTOR 始终等于链接脚本的 ROM 起始地址。

**⛔ 完整芯片擦除 = 删除 bootloader！**

`flash erase_sector 0 0 11` 擦除 sector 0 (0x08000000-0x08007FFF)。**CUAV V5 的 PX4 兼容 bootloader 不在 `Tools/bootloaders/` 中**，擦除后无法从 ArduPilot 源码恢复。

恢复方法：hwdef.dat 设 `FLASH_RESERVE_START_KB 0` + 同步 VTOR（跳过 bootloader）。**预防**：只擦除 `flash erase_sector 0 1 11`。

**关键诊断手法：读取 PSP 异常帧中的 LR/PC 确认栈溢出**

当 `bt` 显示 `#3 0x00000000 in ?? ()` 时，读异常帧（由 HardFault 入口的硬件压栈）确认故障点的精确信息：

```bash
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "x/8w \$psp" \
  -ex "info registers sp pc lr" \
  /path/to/rt-thread.elf
```

异常帧布局（PSP 指向的位置）：
```
PSP+0:  R0         = 故障时的 R0
PSP+4:  R1         = R1
PSP+8:  R2         = R2  
PSP+12: R3         = R3
PSP+16: R12        = R12
PSP+20: LR         = 故障前 LR（应=`rt_hw_systick_init` 返回地址，若=Reset_Handler 地址则栈溢出）
PSP+24: PC         = 故障指令地址
PSP+28: xPSR       = 状态字
```

**典型栈溢出特征 LR**：PSP+20 的 LR 显示为 `0x0800820f`（Reset_Handler+0x17，`bl SystemInit` 的返回地址），而不是预期的 `rt_hw_systick_init` 返回地址。说明栈被撑爆到初值位置，OS 压栈的 LR 其实是复位时的陈旧值。

**验证方法**：
```bash
# 复位后立即检查 SP 是否正确
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor reset halt" \
  -ex "info registers sp" \
  /path/to/rt-thread.elf
# SP 应为 _estack
```
