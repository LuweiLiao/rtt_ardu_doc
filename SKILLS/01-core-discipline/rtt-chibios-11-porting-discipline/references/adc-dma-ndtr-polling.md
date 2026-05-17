# ADC DMA 循环缓冲 + NDTR 安全读取

## 架构

ADC1 通过 DMA2 Stream0 进行连续循环采样。8 通道序列自动扫描，
DMA 填充 SRAM1 缓冲区。`_timer_tick()` 在 100Hz 下通过 NDTR 判定
安全读取半区，累加样本值。

## 初始化顺序（关键！RM0410 §13.4.6）

```
1. 使能 GPIO + ADC1 + DMA2 时钟
2. 配置 ADC GPIO 引脚为模拟模式
3. 配置 ADC 预分频器 + TSVREFE
4. 配置 DMA2 Stream0 （DISABLE → NDTR → PAR → M0AR → CR）
5. 清 LIFCR 所有 Stream0 错误标志
6. 使能 DMA2 Stream0
7. 配置 ADC1 CR1（SCAN=1, 序列长度）
8. 配置 ADC1 SQR1-3（通道序列）
9. 配置 ADC1 SMPR1-2（采样时间）
10. 第一写 ADON（唤醒稳压器）
11. 等待 1000 NOP
12. 清 ADC SR
13. 第二写 ADON | DMA | DDS | CONT
14. SWSTART 开始转换
```

**必须按此顺序**！先启 DMA 再启 ADC 转换，否则 DMA 请求可能错过。

## DMA 配置

| Stream | 外设 | 方向 | 数据宽度 | 模式 |
|--------|------|------|---------|------|
| DMA2 Stream0 | ADC1 (CH=0) | P→M | 16-bit | CIRCULAR |

```c
DMA2_Stream0->CR =
    (0U << DMA_SxCR_CHSEL_Pos) |   // Channel 0
    (1U << DMA_SxCR_MSIZE_Pos) |   // Memory 16-bit
    (1U << DMA_SxCR_PSIZE_Pos) |   // Peripheral 16-bit
    DMA_SxCR_MINC |                 // Memory increment
    DMA_SxCR_CIRC |                 // Circular
    DMA_SxCR_EN;                    // Enable
```

## 缓冲区

- **必须在 SRAM1（0x20020000+）** — DTCM（0x20000000-0x2001FFFF）不可 DMA 访问
- 使用 `__attribute__((section(".sram1")))` 指定段
- 对齐到 32 字节（cache line）

```cpp
static uint16_t __attribute__((section(".sram1"), aligned(32)))
    _adc_dma_buf[ADC_NUM_CHANNELS * ADC_DMA_BUF_DEPTH];
```

## NDTR 安全半缓冲读取（无中断方案）

DMA NDTR 从 `ADC_DMA_BUF_SIZE` 递减到 0，循环模式自动重置。
`_timer_tick`（100Hz）使用 NDTR 判断哪半区安全读取：

```
half_size = ADC_DMA_BUF_SIZE / 2     // 16 (8ch × 4depth / 2)
done = ADC_DMA_BUF_SIZE - NDTR        // DMA 已写入的样本数
safe_half = (done / half_size) & 1   // 0=DMA 写前半, 1=DMA 写后半
read_start = safe_half==0 ? half_size : 0
read_end   = safe_half==0 ? ADC_DMA_BUF_SIZE : half_size

for i in read_start..read_end:
    ch = i % ADC_NUM_CHANNELS
    accum[ch] += buf[i] & 0xFFF
    count[ch]++
```

**优点**：无需中断，无 ISR 延迟风险。100Hz 下每个 10ms 窗口内 DMA 完成
32 个样本（8ch × 4depth），半区 16 个样本安全可读。

## D-Cache 处理

STM32F7 的 D-Cache 在 DMA 写入 SRAM1 时可能包含陈旧数据：

```cpp
SCB_InvalidateDCache_by_Addr((uint32_t *)_adc_dma_buf, sizeof(_adc_dma_buf));
```

必须在 `_adc_dma_process()` 的首部调用，确保读取真实 DMA 数据。

## 性能

- 每 10ms 读取 16 个样本（半区）
- 每通道 100Hz 下约 2 个样本/周期
- 累加后平均 → 平滑读取值
- CPU 占用几乎为 0（无轮询等待）
