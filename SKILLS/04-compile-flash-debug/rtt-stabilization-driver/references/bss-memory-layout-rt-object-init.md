# RT-Thread 内存布局敏感性: BSS 变更引发 init 死循环

> **发现时间**: 2026-05-09  
> **适用场景**: 修改 BSS 大小后（如增加全局数组、栈大小），MCU 启动时能收到 MAVLink 心跳但无后续消息流  
> **风险等级**: 🔴 高 — 不明原因会让人类误以为"固件坏了"，其实只是内存布局偏移

## 现象特征

- ✅ MAVLink **心跳正常**收到（`HB OK sys=N`）
- ❌ **无后续消息** — `SYS_STATUS`、`RAW_IMU`、`EKF_STATUS_REPORT` 全部无输出
- ❌ MCU **不是 HardFault** — CFSR=0, HFSR=0，PC 在 Thread mode 正常地址
- ❌ GDB backtrace 显示 main 线程卡在 **`rt_object_init()`** 的链表遍历中：

```
#0  rt_object_init (object=0x20009394, type=<optimized out>, name=0x20058370 "hm38")
    at modules/rt-thread/src/object.c:380
380	    node != &(information->object_list);
#1  rt_mutex_init (mutex=0x20009394, name="hm38", flag=1)
#2  RTT::Semaphore::_ensure_mtx (this=0x20009390)
```

## 因果链

```
BSS 段大小增加 16KB（如 bus 线程栈 6144→8192 × 8 总线）
    ↓
全局变量地址整体上移 16KB（BSS 在 RAM 中的位置偏移）
    ↓
之前恰好被覆盖/隐藏的的内存越界写入（在旧布局中刚好落在"安全"区域）
现在落在了 RT-Thread 内核对象链表中某个重要指针上
    ↓
rt_object_init() 在遍历 `information->object_list` 时遇到损坏的 next 指针
    ↓
链表形成循环 → 遍历永不终止 → init 线程死锁
    ↓
MAVLink 线程继续运行（能发心跳），但 main 线程卡死（无后续消息）
```

## 诊断方法

### 1. GDB backtrace 确认

```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "bt 5" \
  -ex "thread apply all bt 3" \
  /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf
```

如果 `#0` 在 `rt_object_init` 的 `node != &(information->object_list)` → **确认**。

### 2. 确认 BSS 变更量

```bash
# 比较改前改后的 RAM 占用
# 改前:
arm-none-eabi-size build/rtt_deploy/cuav_v5/rt-thread.elf
# 注意 bss 列的变化量
```

### 3. 确认不是其他原因

- 检查 MCU 不是 HardFault: `p/x *(uint32_t*)0xE000ED28`（应为 0）
- 检查心跳正常（能收 HB = USB CDC + MAVLink 线程存活）

## 修复策略

### 方案 A：回退 BSS 变更（首选，恢复工作状态）

```bash
git revert <commit-that-added-bss>
# 重新编译
rm -rf build/rtt_deploy/ build/rtt_cuav_v5/
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
```

### 方案 B：减少变更量（当必须增加时）

不要全局增加所有实例的栈大小：

```cpp
// ❌ 错误：所有 8 个总线线程都增大
static char _bus_thread_stacks[DeviceBus::MAX_BUSES][8192]; // +16KB

// ✅ 正确：仅增加需要的总线（如果预知哪个会溢出）
static char _bus_thread_stacks[DeviceBus::MAX_BUSES][6144];
// 或者用动态分配替代全局数组
```

### 方案 C：改用堆分配（彻底避免 BSS 偏移）

```cpp
// 用 rt_malloc 替代全局数组
_bus_thread_stacks[slot] = (char*)rt_malloc(8192);
```

### 方案 D：定位并修复潜在的内存越界

这是一个可选项但工作量最大。根因不是 BSS 变更本身，而是被 BSS 变更**暴露**了已有的内存越界 bug。用以下方法定位：

1. **GDB watchpoint**：在 `rt_object_init` 循环中设 watchpoint 监控 `information->object_list`
2. **RT-Thread 栈溢出检测**：开启 `CONFIG_RT_USING_OVERFLOW_CHECK=y`
3. **对照实验**：在旧 BSS 布局下检查各全局变量的地址，看谁恰好紧邻对象链表

## 关键教训

1. **BSS 大小是系统的隐式约束** — RT-Thread 内核的许多数据结构（对象链表、线程控制块）都在 BSS 中，BSS 偏移可能影响它们
2. **这次不是堆栈溢出** — 是 BSS 变更**暴露了堆栈溢出**（或其他越界写入）的副作用
3. **症状是"HB 有但无消息"而非 HardFault** — 因为 init 线程卡在无限循环而非崩溃
4. **NEVER 假设 BSS 变更安全** — 即使只是 +16KB

## 关联问题

- `__udivmoddi4` 偶发崩溃（参见 `udivmoddi4-stack-overflow-diagnosis.md`）— bus 线程栈 93.5% 使用率是切实问题，但增大栈引入了 init 死锁，需要换方案解决
