# GOAP 规划器架构笔记

本文件记录 A* 搜索在嵌入式调试领域的设计经验和陷阱，供后续改进参考。

## 核心设计原则

### 1. 状态变量是布尔值

每个变量（`mcu_halted`, `fault_regs_read`, `spi_working` 等）均为 `bool`。
变量的值是二进制 true/false，没有"部分已知"或"部分工作"的中间状态。

**影响**：如果某个状态需要更细粒度表示（如 `fault_cause = "fpu" | "spi" | "stack"`），
当前框架不支持。需要扩展为枚举或多值状态。

### 2. 动作必须有合理的 precondition

GOAP 搜索的**效率取决于 precondition 的精确性**。

✅ 正确例子：
```python
Action("halt_mcu", 0.2, {"mcu_halted": False}, {"mcu_halted": True}, ...)
```
- precondition `mcu_halted=False` 防止搜索反复 apply halt，浪费迭代

✅ 正确例子：
```python
Action("apply_fix_spi_pins_direct", 1.5,
       {"spi_config_checked": True},
       {"fix_applied": True},
       "直接修复 SPI 引脚配置，无需走 HardFault 诊断链")
```
- 提供了「绕过 HardFault 链」的直接修复路径
- 否则 SPI 诊断必须走完 halt→read_regs→disasm→analyze→fix 的完整 HardFault 链

❌ 陷阱：如果一个 fix action 的 precondition 要求 `fault_cause_identified`，
但场景没有显式的 HardFault（如 SPI 引脚配置错误），搜索就会卡住。

### 3. Combined actions 降低分支因子

搜索性能受分支因子（每个状态可 apply 的动作数）直接影响。

**STM32F7 RTT 动作集的分支因子**：
- 从 `mcu_halted=True` → 约 8-10 个动作可 apply
- 从 `mcu_halted=False` → 约 2-3 个动作可 apply

**优化策略：把连续的不相关诊断步骤合并为一个复合动作**：
```python
# 原来：check_spi_pins(0.4) + read_spi_regs(0.4) = 2个动作，中间状态多
# 合并后：
Action("diagnose_spi_full", 0.6,
       {"mcu_halted": True},
       {"spi_config_checked": True, "spi_working": True},
       "复合动作：检查 SPI 引脚配置 + 读外设状态寄存器")
```

这减少了 1 个中间状态，减少约 8-10 个无关分支。

### 4. Heuristic 必须可采纳（admissible）

可采纳 = 永远 ≤ 实际最小代价。否则 A* 可能找不到最优解。

**不可采纳的陷阱**（踩过的坑）：
```python
# ❌ 过度估计：双重计数 + 忽略依赖链
if needs_fw_running:
    cost += 5.0  # compile
    cost += 3.0  # flash
    cost += 2.0  # cdc
    cost += 0.8  # mavlink
    cost += 1.5  # gyro
    cost += 1.5  # accel     ← gyro+accel 可被同一个 verify_imu 动作一次性设置
```

✅ 可采纳的写法：
```python
# 先判断是否需要编译烧录（只计一次）
if goal_needs_fw > 0 and not current.get("firmware_flashed"):
    if not current.get("firmware_compiled"):
        cost += 5.0
    cost += 3.0   # flash
    cost += 2.0   # reset+wait_cdc

# 再单独计验证代价（verify_imu 一次设置 gyro+accel）
if current.get("mavlink_heartbeat"):
    cost += 0.5  # 一个 verify_imu 动作同时设置 gyro+accel
```

### 5. 搜索迭代上限

搜索迭代 = heap pop 次数，决定了搜索能探索的深度。

**估值方法**：
- 从初始状态到目标状态的最短路径步数 = S
- 平均分支因子 = B
- 需要的迭代次数 ≈ S × B × 1.5（安全系数）

对于 RTT 场景：
- 最短路径 ≈ 7 步（spi_diagnose）
- 分支因子 ≈ 8
- 需要的迭代 ≈ 7 × 8 × 1.5 = 84（安全值）

实际配置为 `max_steps × 100 = 3000`，留了足够余量。

### 6. 场景设计的要点

每个预设场景应包含：
- **合理的当前状态**：只包含已知的事实，不要假设未知信息
- **可达的目标状态**：从当前状态出发必须有可行的动作序列
- **firmware_flashed 标记**：如果固件已烧录但有问题，当前状态必须有 `firmware_flashed=True`

常见错误场景设计：
```
❌ cdc_no_enum 当前状态缺少 firmware_flashed=True
   → 搜索必须走完整的 编译+烧录 链才能检查 CDC
   → 但实际上 CDC 不枚举通常是因为固件已烧录但代码有 bug
✅ 修复后：firmware_flashed=True，搜索只需 check_cdc → check_mavlink
```

## 与 kanban 的集成方式

GOAP 规划器的输出定位为 **diagnostic plan**，不直接生成 kanban task。

推荐流程：
1. 遇到 bug → 运行 GOAP 规划器获得最短诊断路径
2. 将路径的前缀（诊断步骤）作为 Researcher task 的任务描述
3. Researcher 执行诊断，产出根因分析报告
4. Engineer 根据报告实施修复
5. ...

GOAP 规划器本身不操作 kanban database。

## 未来改进方向

1. **CFSR/HFSR 解析集成**：根据 CFSR 值自动剪枝动作空间
   - CFSR=0x00010000 (IACCVIOL) → 优先搜索 FPU/指令对齐诊断
   - CFSR=0x00000100 (DIVBYZERO) → 优先搜索除法溢出诊断
2. **非布尔状态变量**：支持枚举值（如 fault_cause 可取 "fpu" | "spi" | "stack" | "i2c"）
3. **概率加权动作**：某些动作有失败概率（如 check_cdc_enumeration 可能返回 False），
   应支持分支路径和回退
4. **动态新增动作**：如果搜索发现无解，允许用户描述新动作并自动加入重试
