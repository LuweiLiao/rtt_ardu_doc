# RTT vs ChibiOS 行为差异：延迟、锁、Bootloader

> 发现于 2026-05-10 调试会话（IMU init stage 662 卡死分析）
> 验证方法：OpenOCD halt + 反汇编 + 源码对比

## 1. delay() 行为差异（核心根因）

### ChibiOS 行为
```cpp
// ChibiOS Scheduler.cpp
void delay(uint16_t ms) {
    chThdSleep(MS2ST(ms));   // 仅睡眠，纯内核阻塞
}
```
- `chThdSleep(MS2ST(100))` → 线程从就绪队列移除 → 100ms 后重新加入
- 期间调度器自由选择下一个最高优先级线程运行
- **不触发任何回调**（GCS、Logger、timer 都不在 delay 中执行）

### RTT 行为（问题所在）
```cpp
// RTT Scheduler.cpp
void delay(uint16_t ms) {
    uint64_t start = AP_HAL::micros64();
    while ((AP_HAL::micros64() - start) / 1000 < ms) {
        delay_microseconds(1000);   // 每次睡眠 1 tick
        if (_min_delay_cb_ms <= ms) {
            if (in_main_thread()) {
                call_delay_cb();    // ← ⚠️ 每个 tick 触发回调！
            }
        }
    }
}
```
- 每 1ms 迭代一次，每次调用 `delay_microseconds(1000) + call_delay_cb()`
- `call_delay_cb()` → `gcs().update_receive()` / `gcs().update_send()` / Logger 处理
- **setup 期间触发 GCS/Logger** → Logger 试图访问未初始化的存储 → 可能触发其他延迟/逻辑

### 影响链路
```
delay(100) → 100次循环 →
  delay_microseconds(1000) → rt_thread_delay(1) ✓ 睡眠 1ms
  call_delay_cb() → gcs.update_receive() →
    可能调用 AP_Logger 操作（AP_Logger.cpp:1456 被 addr2line 验证）
    → 更多延迟 → DWT 忙等
```

### 验证方法
```bash
# 1. halt 后查 PC
echo "halt" | nc -q 2 localhost 4444
# 2. addr2line 确认 PC 位置
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf <PC>

# 3. 查 call_delay_cb 的调用者
# LR 寄存器从 OpenOCD 读取
LR 指向 Scheduler.cpp delay() 中的 call_delay_cb 之后
```

## 2. SPI 锁架构差异

### ChibiOS (单锁)
```
DeviceBus {
    Semaphore semaphore;  // 唯一锁
};

SPIDevice::get_semaphore() { return &bus.semaphore; }
SPIDevice::transfer() { check_owner(bus.semaphore); do_spi(); }
DeviceBus::bus_thread() { WITH_SEMAPHORE(semaphore) { cb(); } }
```

### RTT (双锁)
```
SPIDevice {
    Semaphore _sem;       // 私锁 — get_semaphore() 返回此
    DeviceBus *_bus;      // 总线 — 有独立的 semaphore
};

SPIDevice::get_semaphore() { return &_sem; }
SPIDevice::transfer() { _sem.take(100); _lock_bus(); do_spi(); _sem.give(); }

DeviceBus::_bus_thread_entry() {
    binfo->semaphore.take(10);  // 总线锁
    cb();                        // 内部又取 _sem
    binfo->semaphore.give();
}
```

### 验证方法
```bash
# 检查 get_semaphore 反汇编
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf | grep -A 10 "SPIDevice.*get_semaphore"
# 应返回 &_sem（位于 sp 偏移量），而非 &bus.semaphore（全局量）
```

## 3. DWT 忙等 vs 线程睡眠

### RTT delay_microseconds 策略
```cpp
void delay_microseconds(uint16_t us) {
    const uint32_t tick_us = 1000000U / RT_TICK_PER_SECOND;  // = 1000
    if (tick_us == 0 || us < tick_us) {
        _delay_microseconds_dwt(us);  // ← ⚠️ 忙等！≤ 999µs 都走此路径
        return;
    }
    rt_thread_delay(us / tick_us);     // 睡眠整 tick
    _delay_microseconds_dwt(us % tick_us);  // 忙等余数
}
```

### DWT 忙等的危害
- CPU 被独占：`while ((DWT_CYCCNT_REG - start) < cycles) { __NOP(); }`
- 更高优先级的线程（timer prio 4, Logger IO prio 18）也无法运行
- 如果 `SystemCoreClock` 不正确（不是 216MHz），cycles 计算错误可能导致延迟过长或过短

### 修复策略
```cpp
// 对于 >= 200µs 的延迟，至少睡眠 1 tick
const uint32_t tick_us = 1000000U / RT_TICK_PER_SECOND;
if (us < tick_us && us >= 200) {
    rt_thread_delay(1);         // 至少睡眠 1 tick
    us -= tick_us;              // 减去已睡眠时间
    // 余数继续 DWT — 此时远小于 1 tick，可以接受
}
```

## 4. take_blocking() 超时差异

### ChibiOS
```cpp
bool Semaphore::take(uint32_t timeout_ms) {
    if (timeout_ms == HAL_SEMAPHORE_BLOCK_FOREVER) {  // 0
        chMtxLock(mtx);  // 永久阻塞，优先级继承
        return true;
    }
}
```
- `take(HAL_SEMAPHORE_BLOCK_FOREVER)` = `take(0)` = 永久阻塞
- 优先级继承防止优先级反转

### RTT
```cpp
void Semaphore::take_blocking() {  // override
    // 覆写基类：基类调用 take(0) → RT_WAITING_FOREVER
    // RTT 覆写为 60s 超时
    rt_mutex_take(&_mtx_obj, 60000);
}
```
- 60s 超时后返回（即使 mutex 未获取！）
- `WITH_SEMAPHORE` 析构函数调用 `give()` → 在没有持锁的 mutex 上释放
- 这是 **UB（未定义行为）**

### 修复
移除 `take_blocking()` 覆写，让基类行为生效：
```cpp
// Semaphores.cpp 中删除此函数
// 基类 AP_HAL::Semaphore::take_blocking() 会调用 take(HAL_SEMAPHORE_BLOCK_FOREVER)
// 而 take(0) 在 RTT 实现中 = rt_mutex_take(&_mtx_obj, RT_WAITING_FOREVER) ✓
```
