# ChibiOS Setup 优先级降低 — 精确代码分析

## 发现日期
2026-05-13（会话上下文压缩后重新发现，廖博士严厉纠正"不准猜测"后才精读到这一行）

## 根因摘要
ChibiOS 在 `setup()` 前明确将主线程优先级降到 10，`setup()` 完成后恢复为 180。
RT-Thread 不做此降级，主线程在 setup 期间维持优先级 5（较高），导致：

- UART(6) 线程饿死 → IOMCU 上传慢、CDC TX ISR 延迟
- Timer(4) 线程被主线程阻塞 → 调度延迟
- 每个 `delay(5)` 实际耗时远超 5ms
- Gyro calibration 70s+ 而非 ChibiOS 的 ~30s

## ChibiOS 精确源码引用

### HAL_ChibiOS_Class.cpp — main_loop()

```cpp
// File: libraries/AP_HAL_ChibiOS/HAL_ChibiOS_Class.cpp
// Function: void main_loop(void *parameter) — 约 line 230-342

void main_loop(void *parameter)
{
    // [line 247-250] 获取当前线程句柄
    daemon_task = chThdGetSelfX();

    // [line 253-256] 设置主线程优先级为 180（高）
    chThdSetPriority(APM_MAIN_PRIORITY);  // APM_MAIN_PRIORITY = 180

    // [line 259] hal_initialized 已经在 _init() 调用 set_priority() 前被 scheduler.init() 设 true
    schedulerInstance.hal_initialized();

    // [line 265] ★★★ 核心行：setup 前将主线程优先级降到 10 ★★★
    hal_chibios_set_priority(APM_STARTUP_PRIORITY);  // APM_STARTUP_PRIORITY = 10

    // [line 267] 标识第一阶段初始化完成
    g_callbacks->setup();

    // [line 277] ★★★ 恢复主线程优先级到 180 ★★★
    chThdSetPriority(APM_MAIN_PRIORITY);

    // [line 287+] 主循环
    while (true) {
        g_callbacks->loop();
        // ...
    }
}
```

### hal_chibios_set_priority() 实现

```cpp
// File: libraries/AP_HAL_ChibiOS/Scheduler.cpp
// Function: void hal_chibios_set_priority(uint8_t priority)
// line ~127-135
void hal_chibios_set_priority(uint8_t priority)
{
    chThdSetPriority(priority);
}
```

### Scheduler.h — 优先级常量定义

```cpp
// File: libraries/AP_HAL_ChibiOS/Scheduler.h
// 约 line 24-28
#define APM_MAIN_PRIORITY        180
#define APM_TIMER_PRIORITY       181
#define APM_SPI_PRIORITY         181
#define APM_UART_PRIORITY        60
#define APM_IO_PRIORITY          58
#define APM_STARTUP_PRIORITY     10
#define APM_MAIN_PRIORITY        180
```

### ChibiOS 优先级数值体系

在 ChibiOS/RT 中，**数字越小优先级越高**（与 RT-Thread **相同方向！**）：

| 常量 | 值 | 用途 |
|------|-----|------|
| APM_STARTUP_PRIORITY | **10** | setup 阶段的主线程（最低优先级） |
| APM_UART_PRIORITY | **60** | UART 串口线程 |
| APM_IO_PRIORITY | **58** | IO 处理线程 |
| APM_MAIN_PRIORITY | **180** | 主循环线程（高优先级） |
| APM_TIMER_PRIORITY | **181** | 定时器线程（最高） |
| APM_SPI_PRIORITY | **181** | SPI 总线线程（与定时器同级） |

**关键观察**：startup 优先级 10 远低于 UART(60)、IO(58)、几乎是最低可行值。这样 setup 中的每个 `delay()` 调用真实让出 CPU 给所有其他线程 → UART 上传流畅、传感器通信可被及时处理。

## ChibiOS vs RTT 优先级对照（等效数值）

| 线程/角色 | ChibiOS | RTT（当前） | 分析 |
|-----------|---------|-------------|------|
| Timer | **181** | **4**（极高） | RTT timer 优先级正确（比主循环高） |
| SPI Bus | **181** | **5** | RTT SPI 略低于 timer，可以接受 |
| **主循环** | **180** | **5** | RTT 主循环与 timer/SPI 差距太大 |
| UART | **60** | **6** | RTT UART 几乎与主循环同级 ❌ 应该更低 |
| IO | **58** | **18** | ✅ 合理（最低之一） |
| **setup 期间主线程** | **10** | **5（不降级！）** | ❌ **根因！RTT 不做降级** |

**RTT 问题**：主线程在 setup 期间维持优先级 5，比 UART(6) 还高 → UART 线程在 setup 中被饿死。ChibiOS 中 setup 优先级 10 远低于 UART(60)，delay() 真实让出。

## RTT 等效修复

在 `HAL_RTT_Class.cpp` 的 `_main_loop_entry()` 中：

```cpp
void _main_loop_entry(void *parameter)
{
    auto *a = (HAL_RTT *)parameter;

    // 获取当前线程
    rt_thread_t self = rt_thread_self();

    // ★ 保存主线程优先级
    rt_uint8_t main_prio = self->current_priority;

    // ★ 降级到 startup 优先级（远低于 UART）
    rt_uint8_t startup_prio = 20;  // 低于 UART(6) 和 IO(18)
    rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &startup_prio);

    // ★ run hal_initialized() + setup()
    a->sched->hal_initialized();
    a->callbacks->setup();

    // ★ 恢复主线程优先级
    rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &main_prio);

    // 进入主循环
    a->sched->set_system_initialized();
    for (;;) {
        a->callbacks->loop();
    }
}
```

## 其他发现：hal_initialized() 调用顺序差异

ChibiOS 在 setup() 前调用 `schedulerInstance.hal_initialized()`，通知 timer 线程开始运行。RTT 的 `_main_loop_entry()` 在 setup() 之后才通过 `set_system_initialized()` 启动 timer。

**影响**：ChibiOS 的 timer 在 setup 期间就已运行，可以处理定时回调。RTT 的 setup 期间无 timer 服务。如果 setup 中的某些代码依赖 timer 回调（如软件定时器、超时检查），RTT 的行为会不同。

## 参考链接

- `libraries/AP_HAL_ChibiOS/HAL_ChibiOS_Class.cpp` — line 230-342 `main_loop()`
- `libraries/AP_HAL_ChibiOS/Scheduler.h` — line 24-28 优先级常量
- `libraries/AP_HAL_RTT/HAL_RTT_Class.cpp` — line 164-205 `_main_loop_entry()`（需修改）
- `libraries/AP_HAL_RTT/Scheduler.h` — RTT 优先级常量定义
