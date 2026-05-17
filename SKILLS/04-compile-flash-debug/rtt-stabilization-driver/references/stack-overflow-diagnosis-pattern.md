# 栈溢出诊断模式 — PRECISERR + 异常帧全零或全相同垃圾值

## 诊断信号

当系统在 `AP_Logger_Backend::Write` → `strnlen` 中崩溃时，不要止步于"预存在的 bug"。

### 关键区分

| 特征 | 栈溢出 | 孤立数据损坏 |
|------|--------|-------------|
| 异常帧 R0=R1=R2=R3 值 | **全部相同垃圾值**（如 0x027bf8c3） | 仅目标寄存器异常 |
| 故障 PC | 函数体中间（如 strnlen+12） | 函数体中间 |
| BFAR 值 | 不定（每次运行不同） | 可能固定 |
| 崩前运行时间 | 固定时序（~5-10 秒，logger 启动后） | 可能随机 |

**核心规则**：如果异常帧中多个寄存器包含相同的无效指针值，说明整个函数栈帧被覆盖 — 这是**栈溢出**而非格式字符串 bug。

## 诊断步骤

### Step 1: 确认 HardFault 类型

```bash
(echo "halt"; sleep 1; echo "mdw 0xE000ED28 2"; sleep 1; echo "reg pc"; sleep 1) | nc -q 5 localhost 4444
# CFSR=0x00008200 → PRECISERR + BFARVALID
# PC=0x080083ca → HardFault_Handler 无限循环
```

### Step 2: 读取异常帧

```bash
# PSP 地址从 GDB 获取（thread mode fault 用 PSP，handler mode 用 MSP）
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "info registers r0 r1 r2 r3 r12 lr pc sp psp msp" \
  build/rtt_deploy/cuav_v5/rt-thread.elf

# 异常帧在 PSP（thread mode）或 MSP（handler mode）处
# 手动读 8 个字
(echo "halt"; sleep 1; echo "mdw 0x<PSP_value> 8"; sleep 1) | nc -q 5 localhost 4444

# 异常帧布局：[R0, R1, R2, R3, R12, LR, PC_fault, xPSR]
# PC_fault = 字 [6]（第 7 个）
```

### Step 3: 定位故障代码

```bash
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "info line *0x<PC_fault>" \
  -ex "disassemble 0x<PC_fault-16>, 0x<PC_fault+16>"

# 检查调用者
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "info line *0x<LR & ~1>"
```

### Step 4: 读取 CFSR/HFSR/BFAR

```bash
# CFSR = 0xE000ED28, HFSR = 0xE000ED2C, BFAR = 0xE000ED38
(echo "halt"; sleep 1; echo "mdw 0xE000ED28 5"; sleep 1) | nc -q 5 localhost 4444
# 输出: CFSR, HFSR, DFSR, MMAR, BFAR
# BFAR 值即为导致 PRECISERR 的无效地址
```

### Step 5: 检查栈溢出痕迹

```bash
# 检查主线程栈使用情况（需要知道栈基址）
# 主线程栈通常由 rt_malloc 分配，在 SRAM1 区域
# PSP 接近栈顶（低地址）= 栈近满
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "thread apply all info frame" \
  -ex "monitor resume"
```

## 已知修复

### 主线程栈

```
# libraries/AP_HAL_RTT/hwdef/common/.config
CONFIG_RT_MAIN_THREAD_STACK_SIZE=16384 → 32768
```

ArduPilot 主循环（340Hz）包含：
- `AP_Logger_Backend::Write()` — VLA `uint8_t buffer[msg_len]`（最多 255B）
- `AP_InertialSensor::update()` — 多层传感器回调
- `AP_Navigation`, `AP_Motors`, `EKF` 等深度调用链

16KB 不足，32KB 已通过 55 秒长稳验证。

### 其他可能需要的栈调整

| 线程 | 当前大小 | 评估 |
|------|---------|------|
| 主线程 (main) | 32KB ✅ | 已验证 |
| DeviceBus | 8KB (堆分配) | 已验证 (6bec32b9b1) |
| I/O (log_io) | 1580 (默认) | 留意（如需启用 DataFlash 日志） |
| 定时器 | 2048 | 正常 |

## 已确认的修复记录

1. **commit 6bec32b9b1**: DeviceBus 栈 6144→8192（堆分配，消除 BSS 偏移隐患）
2. **当前会话 (2026-05-10)**: 主线程栈 16384→32768（修复 Logger HardFault）

两者都通过 1 分钟以上长稳验证。
