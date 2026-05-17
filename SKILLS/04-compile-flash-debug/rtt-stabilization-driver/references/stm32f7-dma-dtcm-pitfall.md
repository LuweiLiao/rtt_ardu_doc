# STM32F7 DMA DTCM 陷阱与 ADC DMA 初始化顺序

## DTCM 不可 DMA 访问（\#1 关键陷阱）

**STM32F765 内存布局**：
| 区域 | 地址范围 | 大小 | DMA 可访问？ |
|------|---------|------|------------|
| DTCM | 0x20000000–0x2001FFFF | 128KB | ❌ CPU only |
| SRAM1 | 0x20020000–0x2007FFFF | 384KB | ✅ |
| SRAM2 | 0x2007C000–0x2007FFFF | 16KB | ✅ |

**问题来源**：RTT 的 `link.lds` 通常将 `.data` 和 `.bss` 从 0x20000000 开始（DTCM 范围）。任何 `static` 全局变量的 DMA 缓冲区若在 BSS 段中，可能落在 DTCM 内。

**症状**：
- DMA 配置完全正确（中断触发、NDTR 递减、标志清除）
- 但缓冲区内容始终为 0（DMA 写入从未生效）
- 主循环挂起（`_adc_read_all()` 读到的累加值为 0）
- 即使回退轮询模式也可能不 work（因为 ADC CR2 已设 DMA 位）

**修复方案**：
```cpp
// 方案 A：显式 SRAM1 section（需 link.lds 定义 .dma_sram 段）
static volatile uint16_t _adc_dma_buf[64] 
    __attribute__((section(".dma_sram"))) __attribute__((aligned(4)));

// 方案 B：硬编码 SRAM1 地址（不优雅但可行）
static volatile uint16_t *_adc_dma_buf = (volatile uint16_t *)0x20020000;

// 方案 C：在 link.lds 中添加 SRAM1 段
// .dma_sram (NOLOAD) : { . = ALIGN(4); *(.dma_sram) } > SRAM1
```

**验证**（OpenOCD）：
```
halt
# 获取缓冲区地址
arm-none-eabi-nm rt-thread.elf | grep _adc_dma_buf
# 或用 mdw 检查指针值
mdw <buf_address> 1    # 确认 ≥ 0x20020000
```

## ADC DMA 初始化顺序铁律（RM0410 §13.4.6）

```cpp
// ❌ 错误顺序：ADC 在 DMA 未就绪时发出转换请求
ADC1->CR2 |= ADC_CR2_ADON;      // 先使能 ADC
// ... 再配 DMA（太晚！）

// ✅ 正确顺序（5 步）：
// 1. 清残留标志
DMA2->LIFCR = DMA_LIFCR_CTCIF0 | DMA_LIFCR_CHTIF0 | DMA_LIFCR_CTEIF0 
            | DMA_LIFCR_CDMEIF0 | DMA_LIFCR_CFEIF0;
(void)DMA2->LIFCR;

// 2. 配置 DMA 流
DMA2_Stream0->CR = 0;
while (DMA2_Stream0->CR & DMA_SxCR_EN) {}
DMA2_Stream0->PAR = (uint32_t)&ADC1->DR;
DMA2_Stream0->M0AR = (uint32_t)_adc_dma_buf;  // 确保 ≥ 0x20020000
DMA2_Stream0->NDTR = ADC_DMA_BUF_DEPTH * ADC_NUM_CHANNELS;
DMA2_Stream0->CR = DMA_SxCR_PL_0 | DMA_SxCR_MSIZE_0 | DMA_SxCR_PSIZE_0 
                 | DMA_SxCR_MINC | DMA_SxCR_CIRC | DMA_SxCR_TCIE | DMA_SxCR_EN;
__DSB();

// 3. 清标志（中断使能前）
DMA2->LIFCR = ...;

// 4. 使能 DMA 中断
NVIC_SetPriority(DMA2_Stream0_IRQn, 8);
NVIC_EnableIRQ(DMA2_Stream0_IRQn);

// 5. 最后使能 ADC（DMA 完全就绪后才允许 ADC 发出请求）
ADC1->CR2 = ADC_CR2_ADON | ADC_CR2_CONT | ADC_CR2_DMA | ADC_CR2_EOCS;
```

**错误顺序症状**：ADC 向地址 0 发 DMA 请求 → 总线错误/数据全 0。

## DMA 调试逐步隔离法

当 DMA 导致系统挂起时，用以下步骤隔离问题：

1. **纯基线测试**：`git stash` 回到已知 work 的版本，确认 60s 迭代数
2. **加 100Hz gate 单独**：仅加 gate（无 DMA 代码），验证不影响迭代
3. **逐步添加 DMA**：从基线出发重建 DMA 代码，每次只加一个组件
4. **检查 DMA_CLR**：开中断前必须清所有 TCIF/HTIF/TEIF 残留标志
5. **检查 NTDR**：resume 后 halt → `mdw <NDTR_addr> 1` 确认递减
6. **最后怀疑缓冲区地址**：用 `nm` 或 `mdw` 确认 ≥ 0x20020000
