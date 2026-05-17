# MCU 温度 / Vrefint 监测 (ADC3 轮询)

> 实现: AnalogIn.cpp (`_adc3_init()`, `_adc3_read_ch()`)
> 参考: ChibiOS AnalogIn.cpp L743-768
> 触发: hwdef.dat `define HAL_WITH_MCU_MONITORING 1`

## 架构
- ADC3 独立轮询（非 DMA）
- 20Hz 采样频率（每 50ms）
- 连续 5 次采样取平均后计算

## 通道
- ADC3_IN18: 内部温度传感器 (VSENSE)
- ADC3_IN17: 内部参考电压 (VREFINT)

## 工厂校准值 (STM32F767)
| 地址 | 值 | 说明 |
|------|-----|------|
| 0x1FF1E820 | TS_CAL1 | 30°C 校准值 |
| 0x1FF1E840 | TS_CAL2 | 110°C 校准值 |
| 0x1FF1E860 | VREFINT_CAL | 3.3V 校准值 |

## 温度公式
```
T = ((110 - 30) / (TS_CAL2 - TS_CAL1)) × (raw_temp - TS_CAL1) + 30
```

## 电压公式
```
Vcc = 3.3 × VREFINT_CAL / raw_vref
Vcc_min = 3.3 × VREFINT_CAL / raw_vref_max  (反比关系)
Vcc_max = 3.3 × VREFINT_CAL / raw_vref_min
```

## ADC3 初始化
1. 使能 ADC3 时钟 (RCC->APB2ENR |= RCC_APB2ENR_ADC3EN)
2. TSVREFE (已由 ADC1 init 设置)
3. 首次 ADON 唤醒 → 设置 SMPR1 (ch17,18 = 480cyc) → 二次 ADON 保持

## ADC3 读取
1. 设置 SQR1 为单通道 (SQ1 = channel)
2. 启动 SWSTART
3. 等待 EOC (ADC_SR 轮询)
4. 返回 ADC_DR & 0xFFF

## 注意
- ADC3 clock 由 ADC common prescaler 控制 (与 ADC1 共享)
- TSVREFE 也是共享位 (ADC123_COMMON->CCR)
- 计算使用 float，非关键路径
