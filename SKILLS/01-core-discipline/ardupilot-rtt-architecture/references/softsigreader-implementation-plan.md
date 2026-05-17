# SoftSigReader — RC Input Capture 实现方案

来源：C1-Research (2026-05-16)
ChibiOS 参考：SoftSigReader.cpp(122L), SoftSigReaderInt.cpp(137L), RCInput.cpp(163L)

## 架构发现

CUAV V5 FMU **无直接 RCIN 引脚** — 所有 RC 输入经 IOMCU 路由：
- PPM: PA8 TIM1_CH1（IOMCU 直连）
- SBUS: USART3_RX PB11
- DSM: USART1_RX PA10

## 3 阶段实施策略

### Phase 1: 使能 IOMCU 路径（低复杂度，~20 行）
- 在 hwdef.dat 中取消 IOMCU_UART UART8 的注释
- 现有 RCInput._timer_tick 已能消费 IOMCU 数据

### Phase 2: SoftSigReaderInt（ISR 版，~200 行，中复杂度）
- 当 IOMCU 不可用时（非 CUAV V5 板）作为 fallback
- TIM 输入捕获中断 → 测量脉宽 → 解码 PPM/SBUS
- 关键寄存器：TIM_CCMR1(CCxS=01), TIM_CCER(CCxP), TIM_DIER(CCxDE)

### Phase 3: DMA SoftSigReader（~260 行，高复杂度）
- TIM 突发读 CCR1+CCR2 via DMAR（DBA=0x0D, DBL=1）
- DMA 中断批量处理捕获值
- 适用于需要最小 CPU 开销的场景

## ChibiOS 关键行号

| 函数 | ChibiOS 行 | 作用 |
|------|-----------|------|
| SoftSigReader_DMA_init | 29 | DMA 初始化 |
| DMA_burst_setup | 83 | DMAR 突发配置 |
| DMA_IRQ_handler | 101 | DMA 中断处理 |
| SoftSigReaderInt_init | 57 | ISR 初始化 |
| SoftSigReaderInt_IRQ | 99 | 中断处理 |
| RCInput_init | 33 | RCInput 初始化 |
| RCInput_timer_tick_DMA | 128 | DMA 路径定时驱动 |
| RCInput_timer_tick_INT | 139 | ISR 路径定时驱动 |
