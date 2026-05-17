# MCU 温度/Vrefint 监测 — ADC3 轮询实现

> 创建：2026-05-16 | 参考：ChibiOS AnalogIn.cpp L743-768

## 原理

STM32F767 内置温度传感器连接 ADC1_IN18 和 ADC3_IN18。VREFINT 连接 ADC1_IN17 和 ADC3_IN17。使用 ADC3（未做他用的空闲 ADC）做 20Hz 轮询读取。

## 启用方式

1. `hwdef.dat` 添加：`define HAL_WITH_MCU_MONITORING 1`
2. 编译时，`AP_HAL/AnalogIn.h` 中 `#if HAL_WITH_MCU_MONITORING` 块会暴露 `mcu_temperature()`、`mcu_voltage()` 等虚函数
3. RTT `AnalogIn.h` 覆写这些虚函数，添加累加器成员

## ADC3 初始化

```c
// 1. 使能时钟
RCC->APB2ENR |= RCC_APB2ENR_ADC3EN;

// 2. ADC common 配置由 ADC1 init 已做（prescaler + TSVREFE）

// 3. 单次转换模式
ADC3->CR2 = ADC_CR2_ADON;                    // 第一次 ADON（从掉电中唤醒）
for (volatile uint32_t i = 0; i < 1000; i++); // 等待稳定
ADC3->SMPR1 = (7 << (3*5)) | (7 << (3*4));   // ch18+ch17 480 周期采样
ADC3->CR2 = ADC_CR2_ADON;                    // 第二次 ADON（完整启动，RM0410 §13.4.5）
```

## 单次读取

```c
uint16_t read_ch(uint8_t ch) {
    ADC3->SQR1 = ch;                          // SQ1 = ch
    ADC3->CR2 |= ADC_CR2_SWSTART;             // 启动转换
    for (int t = 10000; t > 0; t--) {
        if (ADC3->SR & ADC_SR_EOC) {
            return ADC3->DR & 0xFFF;          // 12-bit
        }
    }
    return 0;                                 // 超时
}
```

## 20Hz 计算（在 _timer_tick 中）

100Hz gate 循环中，每 50ms 读一次 ADC3，每 5 次（250ms/20Hz）计算一次：

```c
// 累加
_mcu_monitor_temperature_accum += read_ch(18);  // VSENSE
_mcu_monitor_voltage_accum += read_ch(17);      // VREFINT
_mcu_monitor_sample_count++;

// 每 5 次计算
if (_mcu_monitor_sample_count >= 5) {
    float TS_CAL1 = *(uint16_t*)0x1FF1E820;  // 30°C 校准
    float TS_CAL2 = *(uint16_t*)0x1FF1E840;  // 110°C 校准
    float VREFINT_CAL = *(uint16_t*)0x1FF1E860; // 3.3V 校准

    float temp_raw = _mcu_monitor_temperature_accum / (float)_mcu_monitor_sample_count;
    float vref_raw = _mcu_monitor_voltage_accum / (float)_mcu_monitor_sample_count;

    _mcu_temperature = ((110.0f - 30.0f) / (TS_CAL2 - TS_CAL1)) * (temp_raw - TS_CAL1) + 30.0f;
    _mcu_voltage = 3.3f * VREFINT_CAL / (vref_raw + 0.001f);
    _mcu_voltage_min = 3.3f * VREFINT_CAL / (float)(_mcu_vrefint_max + 0.001f); // inverted!
    _mcu_voltage_max = 3.3f * VREFINT_CAL / (float)(_mcu_vrefint_min + 0.001f);

    // 清零累加器
}
```

## 工厂校准地址（STM32F767）

| 参数 | 地址 | 说明 |
|------|------|------|
| TS_CAL1 | 0x1FF1E820 | 30°C 时的 ADC 原始值 |
| TS_CAL2 | 0x1FF1E840 | 110°C 时的 ADC 原始值 |
| VREFINT_CAL | 0x1FF1E860 | 3.3V 时的 VREFINT 原始值 |

## 验证

```bash
# 查看 MCU 温度（通过 GDB）
openocd -c "mdw 0x20019448"  # _mcu_temperature (float, 4 bytes)
# 或用 pymavlink 读 SYS_STATUS.mcu_temperature 字段
```
