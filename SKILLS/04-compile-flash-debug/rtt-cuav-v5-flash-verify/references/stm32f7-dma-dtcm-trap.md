# STM32F7: DMA 缓冲区不能在 DTCM 中

> 发现：2026-05-14 | 适用：所有 STM32F7 RTT 移植中的 DMA 功能

## 内存布局

| 区域 | 地址范围 | 大小 | DMA 可访问？ |
|------|---------|------|------------|
| DTCM | 0x20000000–0x2001FFFF | 128KB | ❌ **CPU 专用** |
| SRAM1 | 0x20020000–0x2007FFFF | 384KB | ✅ DMA 可访问 |
| SRAM2 | 0x2007C000–0x2007FFFF | 16KB | ✅ DMA 可访问 |

## 陷阱：静态变量默认在 DTCM

RTT 的链接脚本（`link.lds`）将 `RAM` 定义为 0x20000000-0x2007FFFF（512KB）。`.data` 和 `.bss` 段从 0x20000000 开始，因此**所有静态变量默认放在 DTCM**。

```cpp
// ❌ 错误：静态数组在 DTCM，DMA 写入静默丢弃
static volatile uint16_t _adc_dma_buf[64] __attribute__((aligned(4)));
```

STM32 总线矩阵对 DMA→DTCM 写入的处理是**静默丢弃**（bus transaction 返回 OK 但不写入）。DMA Stream 的 TEIF（传输错误中断）不会触发除非 `TEIE` 被使能。结果是：
- DMA 看起来在运行（ISR 可能触发 TCIF）
- 但缓冲区内容永远是 0
- 查不出数据问题（无 HardFault、无报错）

## 正确做法

### 方法 1：动态分配（推荐，ChibiOS 方式）

```cpp
// ChibiOS 做法：用 MEM_DMA_SAFE 分配，RTT 的 RT-Thread heap 在 SRAM1
samples[0] = (adcsample_t *)hal.util->malloc_type(
    sizeof(uint16_t) * ADC_DMA_BUF_DEPTH * num_grp_channels,
    AP_HAL::Util::MEM_DMA_SAFE
);
```

`Util::malloc_type()` 的 RTT 实现使用 `calloc()`，RT-Thread heap 起始地址在 `_ebss` 之后（通常在 SRAM1 范围内）。

### 方法 2：section 属性

在链接脚本中添加 DMA-safe section 并显式放在 SRAM1：

```ld
SECTIONS {
    .dma_buffer (NOLOAD) : {
        *(.dma_buffer)
    } > SRAM1
}
```

然后在 C 代码中：
```cpp
static volatile uint16_t _adc_dma_buf[64] __attribute__((section(".dma_buffer"), aligned(4)));
```

## 验证方法

检查 DMA 缓冲区的实际地址：
```bash
arm-none-eabi-nm rt-thread.elf | grep _adc_dma_buf
# 地址必须在 0x20020000+（SRAM1）
# 如果在 0x20000000-0x2001FFFF → 在 DTCM，不可用
```

## 附加要求：DMA2 时钟

配置 DMA2 前必须使能 AHB1 时钟，否则所有 DMA2 寄存器写入**静默忽略**：

```cpp
RCC->AHB1ENR |= RCC_AHB1ENR_DMA2EN;  // bit 22
(void)RCC->AHB1ENR;  // 读回确保生效
```
