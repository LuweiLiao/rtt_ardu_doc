# STM32F7 ADC DMA (AnalogIn) — Implementation Reference

## Overview

Replace ADC EOC polling with DMA2 Stream0 continuous scan mode. ChibiOS reference: `AP_HAL_ChibiOS/AnalogIn.cpp`.

## Architecture

```
DMA2 Stream0 (Channel 0 = ADC1, Circular, 16-bit, Mem-Inc)
  │
  ├── Source: ADC1->DR (0x4001204C)
  ├── Dest:   _adc_dma_buf[8×8] = 64 × uint16_t
  │           Layout: [scan0_ch0, scan0_ch1, ..., scan0_ch7,
  │                    scan1_ch0, ..., scan7_ch7]
  ├── Mode:   CIRC | TCIE | MINC | PSIZE_0 | MSIZE_0 | PL_0
  └── TC IRQ → _adc_dma_callback()
                  → accumulate into _sample_sum[8]
                  → _sample_count += 8
                          │
                    _timer_tick() @ 100Hz
                          │
                    _adc_read_all() → batch read + atomic clear
```

## Key Register Config (AnalogIn.cpp `_adc_init_once`)

| Register | Value | Meaning |
|----------|-------|---------|
| ADC1->CR1 | SCAN | Scan mode |
| ADC1->CR2 | ADON | CONT | DMA | EOCS | Continuous + DMA + EOC per channel |
| ADC1->SMPR1/2 | 7U per channel | 480 cycles sample time (matching ChibiOS ADC_SAMPLE_480) |
| ADC1->SQR3/2/1 | Sequence {0,1,2,3,8,10,11,14} | 8 channels, L[3:0]=7 |
| DMA2_Stream0->CR | PL_0 | MSIZE_0 | PSIZE_0 | MINC | CIRC | TCIE | EN | Circular, 16-bit, medium prio, TC interrupt |

## DMA2 Stream0 Collision Check

On CUAV V5, SPI4_RX uses DMA2_Stream0 by default in `board.h`. **Check before enabling ADC DMA**:

```bash
grep -rn "BSP_SPI4_RX_USING_DMA\|SPI4_DMA_RX" modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/
```

If `BSP_SPI4_RX_USING_DMA` is NOT set (as of 2026-05), Stream0 is free for ADC1. If set, need alternative (EOC interrupt approach).

## `_sample_sum` Overflow Trap (CRITICAL)

**Problem**: Accumulators that are never reset overflow after ~150s (8 channels × 480 cycles × 1kHz × 8 depth = ~3.8M samples/min).

**Fix**: Batch-read all channels atomically, then clear accumulators — matching ChibiOS `read_adc()` (AnalogIn.cpp L625-659):

```cpp
static void _adc_read_all(uint32_t *vals)
{
    rt_base_t level = rt_hw_interrupt_disable();
    uint32_t cnt = _sample_count;
    if (cnt == 0) {
        rt_hw_interrupt_enable(level);
        memset(vals, 0, sizeof(uint32_t) * ADC_NUM_CHANNELS);
        return;
    }
    for (uint8_t i = 0; i < ADC_NUM_CHANNELS; i++) {
        vals[i] = _sample_sum[i] / cnt;
    }
    memset((void *)_sample_sum, 0, sizeof(uint32_t) * ADC_NUM_CHANNELS);
    _sample_count = 0;
    rt_hw_interrupt_enable(level);
}
```

Do NOT clear per-channel or between reads — DMA callback (ISR) may fire and add samples mid-loop. Must clear all channels simultaneously under IRQ disable.

## ChibiOS Reference Lines

| Function | ChibiOS Line | Notes |
|----------|-------------|-------|
| `setup_adc()` DMA config | L447-616 | Uses ChibiOS ADC HAL API; RTT uses direct registers |
| `read_adc()` batch + clear | L625-659 | Must lock (`chSysLock`); RTT uses `rt_hw_interrupt_disable` |
| `adccallback()` accumulate | L341-395 | `_sample_count += ADC_DMA_BUF_DEPTH` |
| `_timer_tick()` @ 100Hz | L710-760 | Gate via `delta_t < 10000` return |
| `ADC_DMA_BUF_DEPTH` | AnalogIn.h:40 | = 8 |

## `_timer_tick()` Frequency

ChibiOS runs `_timer_tick()` at **100Hz** (gated by 10ms delta). RTT's original 1kHz was wasteful for ADC — DMA handles the actual sampling. The 100Hz gate frees ~90% of timer thread CPU for ADC work.

## DMA ISR Priority

Set NVIC priority to 8 (STM32F7: 4-bit, 0-15). DMA ISR always runs above any RT-Thread thread, so the priority value mainly matters for nesting with other interrupts.
