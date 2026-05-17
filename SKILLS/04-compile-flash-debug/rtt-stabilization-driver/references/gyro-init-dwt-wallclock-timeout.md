# Gyro Init DWT CYCCNT 壁钟超时

> 2026-05-13 发现：`AP_HAL::millis()` 在 RTT DWT 忙等上下文中不推进，导致 `_init_gyro()` 循环内 35s 超时条件永不满足。

## 问题

`AP_InertialSensor::_init_gyro()` 中 35s 壁钟超时使用 `AP_HAL::millis()`：

```cpp
const uint32_t gyro_init_start_ms = AP_HAL::millis();
for (int16_t j = 0; j <= 30*4 && num_converged < num_gyros; j++) {
    if (AP_HAL::millis() - gyro_init_start_ms > 35000U) {
        break;  // <-- 从未触发
    }
    // ...
    hal.scheduler->delay(5);
}
```

在 RTT 上，`delay(5)` 内部调用 `_delay_microseconds_dwt(1000)` 然后 `call_delay_cb()`。但 RTT 主线程的 `rt_tick_get()` 只在上下文切换时更新，如果高优先级线程（timer/uart）未运行，tick 值不推进。

## 修复：DWT CYCCNT 替代

```cpp
// 在 _init_gyro() for 循环前
const uint32_t gyro_init_start_cycles = DWT->CYCCNT;

// 在 for 循环体内第一行
if ((DWT->CYCCNT - gyro_init_start_cycles) > 35UL * SystemCoreClock) {
    DEV_PRINTF("gyro_init: 35s timeout (CYCCNT), using best estimate\\n");
    break;
}
```

## 前提条件

- DWT CYCCNT 必须在启动时已使能（在 `startup_rtt_override.S` 中 `CoreDebug->DEMCR |= TRCENA`）
- `SystemCoreClock` 变量必须已初始化（`SystemCoreClockUpdate()` 已调用）
- DWT CYCCNT 是自由运行计数器，在系统时钟频率下递增，不依赖 SysTick 中断

## DWT CYCCNT 使能代码

```asm
/* 在 startup_rtt_override.S Reset_Handler 中 */
ldr r0, =0xE000EDFC      /* CoreDebug->DEMCR */
ldr r1, [r0]
orr r1, r1, #0x01000000  /* bit 24: TRCENA */
str r1, [r0]
dsb
isb

ldr r0, =0xE0001004      /* DWT->CYCCNT */
ldr r1, =0x40000000      /* bit 30: CYCCNTENA */
str r1, [r0]
dsb
isb
```

## 验证方法

烧录后通过 OpenOCD 读 DWT CYCCNT 确认递增：

```bash
echo -e "halt
mdw 0xE0001004 1    # DWT->CYCCNT
resume" | timeout 10 nc localhost 4444
# 每次 halt 值应不同 → 在递增
```
