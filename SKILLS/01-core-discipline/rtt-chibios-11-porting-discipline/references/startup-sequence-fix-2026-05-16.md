# RTT 启动顺序对齐 ChibiOS — 原子化实施记录

> 会话日期：2026-05-16
> 修复目标：对齐 ChibiOS `HAL_ChibiOS_Class.cpp` 的 5 步启动顺序
> 涉及文件：3 个原子化修改

## 诊断过程

### 症状
- RTT 编译运行后，INS calibrate 阶段挂死（setup_stage=662）
- `setup_stage` 停在 662（`ins.wait_for_sample()`）
- `main_loop_iterations = 0`（一次都没进主循环）
- `hal_run_called = 0xBBBBBBBB`（setup 已完成）
- 无 HardFault，线程调度正常

### 设备调试记录

| 调试点 | 发现 | 结论 |
|--------|------|------|
| OpenOCD halt | PC in `_timer_tick()` / DWT busy-wait | 系统在跑，不是死锁 |
| `main_loop_iterations` | = 0 | 主循环未进入 |
| `setup_stage` | = 662 (INS wait_for_sample) | INS 等待样本超时 |
| `_hal_initialized` | lookup in Scheduler.cpp | 发现设时机不当 |

## ChibiOS 参考代码分析

### HAL_ChibiOS_Class.cpp 主循环（L240-380）

```cpp
void main_loop() {
    chThdSetPriority(APM_MAIN_PRIORITY);      // L237 — Step 1

    hal_chibios_set_priority(APM_STARTUP_PRIORITY);  // L265 — Step 2 ↓

    schedulerInstance.hal_initialized();       // L273 — Step 3 ⭐

    g_callbacks->setup();                      // L275 — Step 4

    chThdSetPriority(APM_MAIN_PRIORITY);       // L317 — Step 5 ↑

    while (true) { g_callbacks->loop(); }      // main loop
}
```

### ChibiOS hal_initialized() 的作用

```cpp
void hal_initialized() {
    _initialized = true;          // ← 注意是 _initialized，不是 _hal_initialized！
    chSysLock();
    _hal_initialized = true;       // 通知 timer 线程可以开始运行
    chSysUnlock();
}
```

ChibiOS 有两个标志：
- `_initialized`：由 `set_system_initialized()` 在 setup 完成后设 true
- `_hal_initialized`：由 `hal_initialized()` 在 setup 前设 true，控制 timer 线程的启动

## RTT 的原始问题

### 错误设计

```cpp
// Scheduler::init() 末尾 — 原代码
void Scheduler::init(...) {
    ...
    _hal_initialized = true;   // ❌ 太早！init 在 run() 中间调用
    ...
}
```

**影响链**：`run()` → `scheduler->init()` → 设置 `_hal_initialized = true` → `_main_loop_entry()` 还未执行 → 但 timer/SPI 线程已经看到 `_hal_initialized` 为 true 并开始运行！

此时：
- 主线程还在 run() 中执行 `_register_periodic_callback()` 等初始化
- timer 线程已经以 1kHz 频率运行 `_timer_tick()`
- SPI 总线线程已经在执行回调
- 而传感器的 `.init()` 还没调用 → SPI 回调访问未初始化的设备

### 修正设计

```
修正目标：
  - init() 中只做线程创建、回调注册等基础设施
  - _hal_initialized 由 hal_initialized() 在 _main_loop_entry 中 setup 前设 true
  - 对齐 ChibiOS 的 5 步流程
```

## 实施验证

### 编译验证

```bash
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
```
各批次编译均通过 ✅

### 烧录验证

```bash
# Batch 3（全部修改）后完整烧录
openocd -f Tools/debug/openocd-f7.cfg \
  -c "program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify" \
  -c "reset run" -c "wait_halt 5000" \
  -c "mdw 0xE000ED28 2" \
  -c "resume" -c "shutdown"
```

## 避免重复工作的经验

在写原子清单时发现：
1. `DeviceBus.cpp` 的 `take(10)` → `BLOCK_FOREVER` **此前已修复** ✅
2. `SPIDevice.cpp` 的 `get_semaphore()` 返回总线锁 **此前已修复** ✅

如果按"凭感觉"改，可能会：
- 重改 DeviceBus.cpp 和 SPIDevice.cpp（重复工作）
- 或者认为"改太麻烦"而绕过它们的修复

**原子清单帮我们精确知道哪些已完成、哪些待完成。**
