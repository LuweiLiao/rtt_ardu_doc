# STM32F7 ADC SR：Write-1-to-Clear 陷阱

## 核心事实
**STM32F7 的 ADC SR 寄存器是 Write-1-to-Clear（W1C），与 F4 系列不同。**

- F4：`ADC1->SR = 0;` 清除所有标志 ✅
- F7：`ADC1->SR = 0;` 什么都不做 ❌

## 根因链
1. 首次 ADC 转换后硬件置位 STRT (bit 4)
2. `ADC1->SR = 0` 在 F7 上无效 → STRT 永远为 1
3. `_adc_read()` 检查到 STRT=1 → 关 ADON 再开（ADC 掉电重启）
4. 每次读都掉电→启动→掉电... 永远等不到 EOC
5. 定时器线程永久卡在 EOC 轮询 → 饿死主线程

## 正确修复
```cpp
ADC1->SR = ADC_SR_STRT;              // W1C clear STRT
if (ADC1->SR & ADC_SR_EOC)           // drain stale EOC
    (void)ADC1->DR;
ADC1->CR2 |= ADC_CR2_SWSTART;        // start conversion
for (volatile uint32_t t = 0; t < 10000; t++) {
    if (ADC1->SR & ADC_SR_EOC)
        return ADC1->DR & 0xFFF;
}
```

## 诊断
halt 后读 `ADC1->CR2`：预期 `0x401`（ADON=1, EOCS=1）；异常值（如 0x7000）→ ADON 被异常清除。

## 教训
**不要假定 STM32 系列间外设寄存器行为一致。**
