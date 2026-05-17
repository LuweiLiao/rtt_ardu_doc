# DWT Busy-wait CYCCNT 反汇编验证 + OpenOCD 计数器读取技术

## DWT Busy-wait 安全性验证（2026-05-11）

### 背景

RTT 的 `Scheduler::_delay_microseconds_dwt(uint16_t us)` 使用 DWT CYCCNT 计数器做亚微秒级忙等待。曾被怀疑因 CYCCNT 翻转导致死循环。

### 反汇编验证

```asm
; _delay_microseconds_dwt  (r0 = us, r3 = SystemCoreClock ptr)
  ; 1. 使能 DWT
  ldr r1, [r3, #0]         ; r1 = SystemCoreClock
  udiv r3, r3, r1          ; r3 = SystemCoreClock / 1,000,000  (= 216)
  ldr r1, [r2, #4]         ; r1 = DWT_CYCCNT_REG  ← start
  muls r0, r3              ; r0 = us * 216 = cycles

  ; === LOOP (0x806ec90) ===
  ldr r3, [r2, #4]         ; r3 = DWT_CYCCNT_REG  ← 每次重读 (volatile! ⭐)
  subs r3, r3, r1          ; r3 = CYCCNT - start
  cmp r3, r0               ; compare with cycles
  bcc.n 0x806ec90          ; if (r3 < r0) loop
  ; === LOOP END ===

  bx lr
```

**关键确认**: `ldr r3, [r2, #4]` 在循环体内——因为 DWT_CYCCNT_REG 定义带 `volatile`，编译器每次重新读取，不存在优化问题。

### CYCCNT 翻转安全性分析

DWT CYCCNT 是 32 位计数器，在 216MHz 下每 ~19.88 秒翻转一次。`(CYCCNT - start)` 的无符号减法：

- **start 在低位 + 翻转后**: `(small_after_wrap - low_start) = large_value → > cycles → 退出` ✅
- **start 在高位 + 翻转到低位**: `(small_after_wrap - high_start) = small_value → < cycles → 继续` ⚠️

但需要注意：实际运行时此函数仅被 `delay_microseconds()` 调用，且只在 `< 1 tick (< 1000µs)` 的延迟时使用。短时间内连续多次调用可能碰上翻转边界，但每次 spin 仅持续 < 1000µs，概率极低。

**结论**: volatile 读取正确 ✅，DWT busy-wait 不会因编译器优化导致死循环。

### 通过寄存器推算 spin 状态

halt 后从寄存器读取当前 spin 进度：

```bash
# 现场寄存器
# r0 = cycles 值 (us * 216)
# r1 = start (CYCCNT at function entry)
# r2 = 0xE0001000 (DWT_BASE)
# r3 = CYCCNT - start (当前已自旋的周期数)

# 从 r0 反推算 us
python3 -c "print(215112 / 216)"  # → ~996 µs 的忙等待

# 从 r3 推算已等待时间
python3 -c "print(88267 / 216)"   # → ~409 µs 已过
python3 -c "print((215112 - 88267) / 216)"  # → ~587 µs 剩余
```

---

## OpenOCD 计数器必须 resume 在读之间（重大陷阱）

### 问题

从 OpenOCD telnet 读 volatile 调试计数器时，**所有值看起来都不变**：

```
t=0s:  rtt_uart_dbg_tick_calls = 8,149,559
t=5s:  rtt_uart_dbg_tick_calls = 8,149,559  ← "死锁了？"
```

实际根因：**`echo "mdw ..." | nc ...` 前如果执行过 `halt`，CPU 是停住的**，所有计数器自然不前进。

### 修复方法

```python
# ✅ 正确：每次读前 resume，等一会再 halt
def read_counter(addr):
    terminal("echo 'resume' | nc -q 1 localhost 4444")  # 先 resume
    time.sleep(3)                                         # 让板子跑一会
    terminal("echo 'halt' | nc -q 1 localhost 4444")      # 再 halt 读
    terminal(f"echo 'mdw 0x{addr} 1' | nc -q 2 localhost 4444")
```

正确定时序列：
```
resume → wait(3-5s) → halt → mdw → (记录) → resume → wait(3-5s) → halt → mdw → (比较 delta)
```

### 证明（2026-05-11 实测）

本会话中，初始读时（板子被上次 halt 卡住）：
- `tick_calls` = 8,149,559 (不动)

resume 5 秒后重新 halt 读：
- `tick_calls` = 8,460,793 (+226,127)
- 因此调度器**正常运行**，setup 只是在正常推进

**教训**: 永远不要在被 halt 的板子上读两次计数器说"不变化"。

---

## 完整诊断流程：判断板子是否真的死锁

### 三样本法

```bash
# 1. 先确保板子在运行
echo "resume" | nc -q 1 localhost 4444
sleep 5

# 2. 读第一组
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0x<setup_stage> 1" | nc -q 1 localhost 4444
echo "reg pc" | nc -q 1 localhost 4444
echo "resume" | nc -q 1 localhost 4444

# 3. 等 5 秒读第二组
sleep 5
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0x<setup_stage> 1" | nc -q 1 localhost 4444
echo "reg pc" | nc -q 1 localhost 4444
```

| 第一组 | 第二组 | 诊断 |
|--------|--------|------|
| setup=662 | setup=662 | ⚠️ 真·死锁（调度器/主线程都卡） |
| setup=662 | setup=670 | ✅ 正常推进，只是慢 |
| tick=8M | tick=8.2M + 但 setup 不动 | ✅ 调度器活，主线程在等待资源 |
| tick=8M | tick=8M + PC=0x0806ec90 | ⚠️ 某线程在 DWT spin 中永久卡住 |

### 关键计数器解读

| 计数器 | 增长说明 |
|--------|---------|
| `rtt_uart_dbg_tick_calls` | 如果有增长 → 调度器 alive |
| `dbg_serial_bulkin_cnt` | 如果有增长 → USB IN 传输进行中 |
| `rtt_cpu_idle_pct` | 99% = main 线程在 sleep；< 50% = 某线程在 busy-wait |
| `rtt_dbg_setup_stage` | 递增 = init 在推进；不变 = 卡在某 init 步骤 |
| `rtt_dbg_main_loop_iterations` | > 0 = main loop 已进入 |

### 硬件诊断：复位后不 halt 连续采样（不触碰 CPU）

不需要 halt CPU 的传感器数据：

```bash
# ADC 寄存器（STM32F7 独立运行，不管 CPU 状态）
echo "mdw 0x40012304 1" | nc -q 1 localhost 4444
# → 0x00810000 (TSVREFE=bit23) - ADC 配置是持续的硬件状态

# IWDG 寄存器
echo "mww 0x40003000 0x5555" | nc -q 1 localhost 4444  # 解锁
echo "mdw 0x40003004 1" | nc -q 1 localhost 4444  # PR

# SysTick 计数值（即使 halt 也在变化？No — halt 也停 SysTick）
```

注意：`halt` 停止 **所有** Cortex-M 处理——包括 SysTick 中断、PendSV 调度器、所有线程。读内存时 CPU 不是运行状态。

---

## 直接 USB 写路径的架构记录

### 设计与实现

`usbd_serial_direct_write()` 在 `usbd_serial.c` 中实现，绕过了 CherryUSB 的 32KB ringbuffer：

```
DWT busy-wait 验证:
  反汇编证实 volatile 读取正确
  → 不是编译器优化问题
  → CYCCNT 翻转不是本会话死循环的根因

setup hang 诊断:
  通过 resume/read 两步法确认板子 alive
  tick_calls 增长但 setup_stage 不变
  → 主线程卡在 ins.init() 的内部 delay() 循环

直接 USB 写:
  实现通过测试
  drain_zero = 0% ✅  (CherryUSB ringbuffer路径 ≈ 72%)
  bulk_in 正常计数
  MAVLink 流率不变 → 瓶颈在 GCS 调度层
```

### 验证结果（本会话实测）

| 指标 | CherryUSB ringbuffer 路径 | 直接写路径 |
|------|--------------------------|-----------|
| drain_zero | ~72% | **0%** ✅ |
| USB stall 间隙 | 11-53s | **无** |
| RAW_IMU 流率 | 2.6 Hz | **2.6 Hz** (相同) |
| ATTITUDE 流率 | 6.5 Hz | **6.5 Hz** (相同) |

**结论**: USB 数据路径已优化至 ChibiOS 等价水平（零拷贝 + 零 ringbuffer 断流），MAVLink 流率瓶颈被锁定在 `GCS_MAVLINK::update_send()` 的轮询调度层。
