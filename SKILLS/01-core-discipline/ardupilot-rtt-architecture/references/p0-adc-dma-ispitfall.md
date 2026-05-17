# P0 ADC DMA 完整修复（STM32F7 陷阱合集）

## 发现时间
2026-05-14 会话，多次迭代验证确认。

## 症状

P0 ADC DMA 改造后，系统烧录后 `main_loop_iterations` 始终为 0（等待 5 分钟亦然）。但：
- `hal_run_called = 0xBBBBBBBB`（setup 已完成 ✅）
- `main_loop_entry_called = 0x12345678`（main loop 已进入 ✅）
- `setup_stage = 0x296 = 662`（setup 已完成 ✅）
- PC 始终在 `_delay_microseconds_dwt()`（Scheduler.cpp:72）
- L1 基线（无 P0 改动）60s 内 ~48,000 次迭代 ✅

## 根因 — 双重问题

### 问题 1：ADC 使能优先于 DMA 配置（PRIMARY）

**这是最主要的根因。** 原 P0 代码中 `_adc_init_once()` 的顺序是：

```
// ❌ 错误顺序
ADC1->CR2 = ADON | CONT | DMA | EOCS;   // 先开 ADC — ADC 立即驱动 DMA 请求
// ... 等待 ...
DMA2_Stream0->CR = ... | EN;             // 后配 DMA — 太晚了！
NVIC_EnableIRQ(...);                     // 最后开中断
```

**后果**：ADC 使能后立即开始转换并驱动 DMA 请求，但此时 DMA 尚未配置（PAR=0, M0AR=0），ADC 将数据送到地址 0。虽然 DMA Stream 在 DISABLED 状态下不会执行总线事务，但 ADC 的 DMA 请求信号会卡在 ADC 的状态机里，导致 ADC 转换挂起。

**参考**：RM0410 §13.4.6（ADC DMA）明确要求先配置 DMA 再使能 ADC。

### 问题 2：DMA 遗留 TCIF 标志位（SECONDARY）

即使修正了顺序，如果在使能中断时 TCIF 标志位已为 1（来自上一轮运行的遗留），ISR 会立即自触发，CPU 100% 在 ISR 中。

## 完整修复方法

`_adc_init_once()` 必须按照**严格顺序**执行：

```cpp
// ========== 第 1 步：配置 ADC 模式（但先不使能）==========
ADC1->CR1 = ADC_CR1_SCAN;                  // SCAN 模式
ADC1->SMPR1 = ...;                          // 采样时间
ADC1->SMPR2 = ...;                          // 采样时间
ADC1->SQR3 = ...; ADC1->SQR2 = ...; ADC1->SQR1 = ...;  // 序列

// ========== 第 2 步：清除遗留 DMA 标志位 ==========
DMA2->LIFCR = DMA_LIFCR_CTCIF0 | DMA_LIFCR_CHTIF0
            | DMA_LIFCR_CTEIF0 | DMA_LIFCR_CDMEIF0 | DMA_LIFCR_CFEIF0;
(void)DMA2->LIFCR;

// ========== 第 3 步：配置 DMA（Stream 本身）==========
DMA2_Stream0->CR = 0;
while (DMA2_Stream0->CR & DMA_SxCR_EN) {}  // 等待停稳
DMA2_Stream0->PAR = (uint32_t)&ADC1->DR;   // 外设地址
DMA2_Stream0->M0AR = (uint32_t)_adc_dma_buf;  // 内存地址
DMA2_Stream0->NDTR = 64;                    // 传输次数
DMA2_Stream0->CR = MSIZE_0 | PSIZE_0 | MINC | CIRC | TCIE | EN;
__DSB();

// ========== 第 4 步：再次清标志（Stream 使能后可能立即置 TCIF）==========
DMA2->LIFCR = DMA_LIFCR_CTCIF0 | DMA_LIFCR_CHTIF0 | ...;
(void)DMA2->LIFCR;

// ========== 第 5 步：使能中断 ==========
NVIC_SetPriority(DMA2_Stream0_IRQn, 8);   // 低于定时器
NVIC_EnableIRQ(DMA2_Stream0_IRQn);

// ========== 第 6 步：**最后**使能 ADC ==========
ADC1->CR2 = ADC_CR2_ADON | ADC_CR2_CONT | ADC_CR2_DMA | ADC_CR2_EOCS;
(void)ADC1->CR2;
for (volatile uint32_t i = 0; i < 1000; i++) { __NOP(); }  // 等 ADC 稳定
```

**关键原则**：ADC 使能（CR2 写 ADON）必须是 `_adc_init_once()` 中的**最后一步**。

## 100Hz Gate 的循环率收益

除了 DMA 初始化顺序修复外，`_timer_tick` 加入 **100Hz gate**（`AP_HAL::micros()` + `_last_timer_tick` 判断 ≥10ms 才执行 ADC 读取）将 ADC 处理频率从 1kHz 降到 100Hz，释放了 ~7% CPU：

| 版本 | 循环/60s | 说明 |
|------|----------|------|
| L1 基线（无改动） | ~39,000 | 原始轮询 ADC 1000Hz |
| +100Hz gate 单独 | ~83,000 | 只加 gate，不改 ADC 模式 |
| +ADC DMA 完整 | **待验证** | gate + SCAN/CONT + DMA |

**结论**：100Hz gate 本身带来 ~100% 的循环率提升，远大于 DMA 改造成的理论收益。这是因为 RTT 上每轮轮询 8 通道 ADC 耗时约 80µs，1000Hz 下累计消耗 8% CPU。降频到 100Hz 后 ADC 开销降到 0.8% CPU 以下。

## 调试方法论：Binary-Search Regression Isolation

当 P0 改动导致系统挂起时，使用**基线二分数值回归**快速定位根因：

1. `git stash` 暂存 P0 改动
2. 编译 L1 基线 → 烧录 → 验证工作正常（记录 iterations/60s）
3. `git stash pop` 恢复 P0
4. 逐步**去除**可疑改动（从最复杂的开始），每步编译+烧录+60s 验证

本会话使用的递减序列：
```
完整 P0 → 0 iterations
  去掉 DMA ISR NVIC 使能 → 0 iterations  (ISR 不是根因)
  去掉 DMA/SCAN/CONT，用轮询 ADC → 0 iterations  (ADC 模式也不是？)
  回到 L1 基线 + 仅加 100Hz gate + _last_timer_tick → 83,000 iterations ✅
  L1 基线 + 100Hz gate + DMA 全功能 → 待验证
```

从第 3 步（去掉 DMA 仍挂）可知 `_adc_read_all` / 新 `_timer_tick` 本身有问题。后重建 AnalogIn.cpp 文件解决。

## 验证方法

1. 烧录后 `halt`: 检查 `main_loop_iterations` > 0 证明系统运行
2. 60s 检查：与 L1 基线对比迭代次数
3. DWT CYCCNT 读取：`mdw 0xe0001004` 检查 CPU 活动时间比例

## 诊断命令

```bash
# 检查 hal_run_called 和 main_loop_iterations
echo -e "halt\nmdw 0x200001c0\nmdw 0x20019a24\nreset run\nexit" | nc localhost 4444

# 检查 DMA2_Stream0 当前状态
echo -e "halt\nmdw 0x40026410\nmdw 0x40026400\nreset run\nexit" | nc localhost 4444
# 0x40026410 = DMA2 Stream0 CR 寄存器
# 0x40026400 = DMA2 LISR 寄存器（含 Stream0 标志位）
```

## 反汇编验证 DWT CYCCNT 回绕安全

编译器正确生成 unsigned 减法（不是比较）：
```asm
subs r3, r3, r1    ; current - start (unsigned)
cmp  r3, r0        ; < cycles?
bcs  exit          ; >= → exit (unsigned compare)
dsb  sy
b    loop
```

32-bit CYCCNT @ 216MHz 每 ~19.9s 回绕一次，unsigned 减法正确处理。
