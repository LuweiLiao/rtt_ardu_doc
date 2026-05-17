# Shared_DMA — DMA 流仲裁实现方案

来源：C2-Research (2026-05-16)
ChibiOS 参考：shared_dma.cpp(285L), shared_dma.h(118L)

## 架构发现

RTT 当前 **零 DMA 使用** — 所有 SPI/ADC 在轮询模式运行。

### STM32F767 冲突矩阵

| Stream | 外设冲突 | ChibiOS 处理 |
|--------|---------|-------------|
| DMA2_Stream0 | ADC1(ch0) vs SPI4_RX(ch4) | Shared_DMA 互斥仲裁 |
| DMA2_Stream1 | SPI4_TX(ch4) | 无冲突 |
| DMA2_Stream2 | SPI1_RX(ch3) | 无冲突 |
| DMA2_Stream5 | SPI1_TX(ch3) | 无冲突 |

### 实施 Option A（推荐）：全 port，~400 行

直接翻译 ChibiOS shared_dma 到 RTT API：

| ChibiOS API | RTT 等价 |
|-------------|---------|
| chMutex | rt_mutex |
| chSysLock | rt_hw_interrupt_disable() |
| chSysUnlock | rt_hw_interrupt_enable() |
| dma_allocate | rt_mutex_take(&dma_mutex, RT_WAITING_FOREVER) |
| dma_deallocate | rt_mutex_release(&dma_mutex) |
| lazy-deallocation callback | 函数指针调用 |

### 为什么不选 Option B（简单方案）

Polling 模式理论上可行，但未来 IOMCU/DShot/SDMMC 都需要 DMA。Shared_DMA 是基础设施，宜尽早植入。

### 文件结构

```
shared_dma.h   (~120L) — API、stream 配置结构体
shared_dma.cpp (~280L) — 互斥仲裁实现、register/unregister
```
