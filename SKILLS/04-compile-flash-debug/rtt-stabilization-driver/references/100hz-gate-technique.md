# 100Hz Gate 技术详解

## 原理

`AP_HAL_RTT::AnalogIn::_timer_tick()` 默认被定时器线程以 **1kHz** 频率调用。在轮询模式下，每次 call 执行 ADC EOC 轮询循环（~1ms 忙等 × 1000tick/s = 1s/s 被浪费）。

在 DMA 模式下，`_timer_tick` 只需每 10ms 读取一次累加器（100Hz），其余 900 次调用可以 **直接 return**，不消耗任何 CPU 时间。

## 效果

| 版本 | 循环/60s | 提升 |
|------|----------|------|
| L1 基线（轮询） | 39,393 (657Hz) | — |
| +100Hz gate | **83,242 (1387Hz)** | **2.1x** |

## 实现

**AnalogIn.h**：
```cpp
class AnalogIn : public AP_HAL::AnalogIn {
    ...
private:
    uint32_t _last_timer_tick = 0;  // ← 新增
};
```

**AnalogIn.cpp**：
```cpp
void AnalogIn::_timer_tick()
{
    if (!_initialized) return;

    // 100Hz gate — 每 10ms 处理一次 ADC
    uint32_t now = AP_HAL::micros();
    if (now - _last_timer_tick < 10000) return;  // ← 核心代码
    _last_timer_tick = now;

    // ... 以下每 10ms 执行一次
    _adc_read_all(raw_vals);
    ...
}
```

**init() 中置零**：
```cpp
void AnalogIn::init() {
    _last_timer_tick = 0;  // ← 必须，否则首次 gate 无效
    ...
}
```

## 注意

1. `_last_timer_tick` 必须是 **`uint32_t`**（与 `AP_HAL::micros()` 类型匹配），`int8_t` 会溢出导致 gate 逻辑错乱
2. 仅靠 100Hz gate 就能大幅提升循环率，**即使不实现 DMA 也值得加**
3. 与 DMA 改造兼容：`_adc_read_all()` 从累加器 `_sample_sum[]` 读取、清零，不涉及寄存器轮询
