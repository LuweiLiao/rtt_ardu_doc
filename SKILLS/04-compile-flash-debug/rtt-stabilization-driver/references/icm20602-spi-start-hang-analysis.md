# ICM20602 SPI start() 挂死分析（v2 — 2026-05-11 更新）

## 现象
- `rtt_dbg_setup_stage = 681`（`_backends[1]->start()` = ICM20602）
- 同总线 ICM20689（backend[0]）的 `start()` 完成（680→681）
- `rtt_dbg_fast_loop_count = 0`（主循环从未运行）
- `rtt_dbg_main_loop_entry_called = 0x12345678` ✅（_main_loop_entry 已执行）
- `rtt_dbg_hal_run_called = 0xBBBBBBBB` ✅（HAL_RTT::run 已调用）
- 无 HardFault（CFSR=0, HFSR=0）
- PC 通常停在 `rt_cpu_self()` / `rt_thread_self()`（UART/idle 线程上下文）
- 系统仍在正常运作（调度器运行、USB CDC 枚举），但主循环从不执行

## 排查路线图

### 第一阶段（已排除，2026-05-11 Researcher 完成）
Phase 1D Researcher 对 MAVLink 无输出的5个假设方向全部排查：

| 方向 | 结论 |
|------|------|
| CDC设备名不匹配 | ✅ 排除 — "usb-acm0" 与 CherryUSB 注册名一致 |
| 主循环线程架构 | ✅ 排除 — 与 ChibiOS 一致（直接函数调用） |
| MAVLink Serial 配置 | ✅ 排除 — GCS 通过 call_delay_cb() 发，4ms门控250Hz |
| USB CDC TX路径 | ✅ 排除 — CDC 使用 rt_device_write() 到 CherryUSB ringbuffer |
| Debug基础设施 | ✅ 就绪 — rtt_dbg_setup_stage 等 debug 变量可用 |

### 第二阶段（2026-05-11 现场 GDB 调试完成）

**确诊：不是 CDC/MAVLink 问题，是 ICM20602 的 Invensense::start() 内部阻塞。**

#### 根因分析

##### 1. 线程优先级问题（核心发现）

RT-Thread 主线程优先级 = `RT_MAIN_THREAD_PRIORITY = 10`。

对比各线程优先级：

| 线程 | RTT优先级 | 创建方式 |
|------|-----------|---------|
| ap_monitor | 2 | Scheduler::init() |
| ap_timer | 4 | Scheduler::init() |
| ap_rcout | 4 | Scheduler::init() |
| **SPI1 bus thread** | **5** | DeviceBus::register_periodic_callback() — `prio = RT_THREAD_PRIORITY_MAX/6 = 32/6 = 5` |
| ap_uart | 6 | Scheduler::init() |
| ap_rcin | 6 | Scheduler::init() |
| **Main thread** | **10** | RT-Thread BSP 创建（`RT_MAIN_THREAD_PRIORITY=10`） |
| ap_storage | 16 | Scheduler::init() |
| ap_io | 18 | Scheduler::init() |

主线程(10) 低于 SPI1 bus thread(5)、Timer(4)、UART(6)。这是 ICM20602 start() 阻塞的关键因素。

##### 2. DeviceBus 线程架构

关键代码 `libraries/AP_HAL_RTT/DeviceBus.cpp`：

- **每个 SPI/I2C 总线有独立的 DeviceBus 线程**（通过 `register_periodic_callback()` 惰性创建）
- SPI1 bus thread 运行 `_bus_thread_entry()` 循环：
  - 每次迭代检查所有已注册的回调（ICM20689 的 `_poll_data()`）
  - 以 10ms 超时尝试 `semaphore.take(10)` 获取 bus semaphore
  - 成功则运行回调，失败则跳过一个周期
  - 线程优先级：`prio = RT_THREAD_PRIORITY_MAX / 6 = 5`
  - 线程栈：`BUS_STACK_SIZE = 8192` 字节（堆分配）
- `Semaphore::take(10)` = `rt_mutex_take(&_mtx_obj, 10)` 阻塞最多 10 ticks = 10ms

##### 3. 阻塞时间线

```
ICM20602 start():
  T+0:  WITH_SEMAPHORE(get_semaphore())  → 持有 bus sem [recursive count=1]
  T+0:  _register_write() → transfer() → take(bus sem) [recursive, count=2] → spi1_poll_transfer() → give() [count=1]
  T+0:  hal.scheduler->delay(1) → 主线程(prio=10) 睡眠 1ms
  T+0:  SPI1 bus thread(prio=5) 唤醒 → 尝试 take(10) → BUS sem 被主线程持有 → 阻塞
  
  主线程在优先级 10，SPI1 bus thread 阻塞在优先级 5。
  主线程每次只获得 ~10μs CPU 时间（在更高优先级线程轮转完毕后）。
  
  主线程执行下一条 _register_write() → delay(1) → 循环。
  
  T+10ms: SPI1 bus thread 的 take(10) 超时 → continue → sleep → 重试
  T+20ms: 第二次 take(10) 超时 → continue → sleep → ...
  
  ICM20602 start() 需要完成 ~20 条寄存器操作，每个 ~5μs SPI 工作 + 1ms delay。
  但 start() 在 WITH_SEMAPHORE 块内从未退出——因为递归 sem 需要 count=0 时才释放。
  只要 start() 尚未返回，sem 就不会释放，SPI1 bus thread 永远无法运行 ICM20689 的回调。
```

##### 4. 关键瓶颈

- **主线程优先级(10)太低** — 被所有业务线程（timer、SPI bus、UART）抢占
- **WITH_SEMAPHORE 块内持有 sem 时间过长** — 包含多次 `delay(1)`，期间 sem 不会被释放
- **递归 mutex 的 give() 只在 WITH_SEMAPHORE 析构时释放** — `transfer()` 内的 give() 只减计数，不真正释放
- **ADC EOC timeout fix 是必须的但不是充分的** — ADC 修复(T=662→681)后系统进步了，但 ICM20602 start() 的优先级问题仍然存在

## 修复方向

### 方案 A：提高主线程初始化优先级
在 `_main_loop_entry()` 中先调用 `boost_end()` 或显式将主线程优先级从10提高到3-5，完成初始化后再降回。

```cpp
// HAL_RTT_Class.cpp 的 _main_loop_entry() 开始处：
rt_thread_t self = rt_thread_self();
rt_uint8_t boost_prio = APM_RTT_MAIN_BOOST;  // = 3
rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &boost_prio);
// ... setup() ...
rt_uint8_t normal_prio = APM_RTT_MAIN_PRIORITY;  // = 5
rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &normal_prio);
```

### 方案 B：WITH_SEMAPHORE 内使用超时
在 start() 中使用 `take(100)`（100ms超时）代替 `HAL_SEMAPHORE_BLOCK_FOREVER`。如果超时则降级（跳过此传感器）。

### 方案 C：分离 ICM20602 start() 中的 sem 持有范围
将需要持有 sem 的寄存器操作与非操作（delay）分开，减少 sem 的持有时间：
```cpp
{
    WITH_SEMAPHORE(_dev->get_semaphore());
    // 所有 SPI 操作
    _register_write(..., ...);
    _register_write(..., ...);
    _fifo_reset(false);
    _set_filter_register();
    // ...
}
// 所有 delay 操作在 sem 外执行
hal.scheduler->delay(1);
```

## 验证方法

修复后验证步骤：
1. 编译烧录后检查 `rtt_dbg_setup_stage` 是否从 681 推进到 690+
2. `rtt_dbg_fast_loop_count` 是否从 0 开始递增
3. CDC MAVLink 是否开始输出 HEARTBEAT
4. CFSR=0, HFSR=0 无 HardFault

## 参考文件
- `libraries/AP_HAL_RTT/DeviceBus.cpp:49` — SPI bus thread 的 `take(10)` 超时
- `libraries/AP_HAL_RTT/DeviceBus.cpp:132-135` — 线程优先级计算（`RT_THREAD_PRIORITY_MAX/6`）
- `libraries/AP_HAL_RTT/Semaphores.cpp:86` — `take(10)` 转换为 10 tick 超时
- `libraries/AP_HAL_RTT/HAL_RTT_Class.cpp:238` — `_main_loop_entry` 直接调用
- `libraries/AP_InertialSensor/AP_InertialSensor.cpp:876` — setup_stage=681 赋值点
- `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/rtconfig.h:133` — RT_MAIN_THREAD_PRIORITY=10
- `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/rtconfig.h:71` — RT_THREAD_PRIORITY_MAX=32
