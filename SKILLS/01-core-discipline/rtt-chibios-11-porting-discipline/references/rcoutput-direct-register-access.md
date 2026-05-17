# RCOutput 直接 TIM 寄存器访问实现

> 适用平台：CUAV V5 (STM32F767)
> 背景：BSP 无 board.h → drv_pwm.c 不编译 → rt_pwm_set() 返回 NULL → PWM 从未输出

## 硬件映射

| 通道 | 引脚 | 定时器 | 通道号 |
|------|------|--------|--------|
| CH1 | PE14 | TIM1 | 4 |
| CH2 | PA10 | TIM1 | 3 |
| CH3 | PE11 | TIM1 | 2 |
| CH4 | PE9 | TIM1 | 1 |
| CH5 | PD13 | TIM4 | 2 |
| CH6 | PD14 | TIM4 | 3 |
| CH7 | PH6 | TIM12 | 1 |
| CH8 | PH9 | TIM12 | 2 |

## 定时器时钟
- TIM1: APB2 = 216MHz
- TIM4: APB1 = 108MHz  
- TIM12: APB1 = 108MHz

## PSC + ARR 计算
```
target_ticks = period_ns * tim_clk / 1e9
psc = (target_ticks + 65535) / 65536  // 确保 ARR ≤ 16-bit
arr = target_ticks / psc
```

示例: 50Hz@TIM1 → psc=66, arr=65454

## 关键寄存器

| 寄存器 | 作用 |
|--------|------|
| TIMx->PSC | 预分频器 (psc-1) |
| TIMx->ARR | 周期 (arr-1) |
| TIMx->CCRx | 脉宽 |
| TIMx->CCMRx | PWM mode 1 (OCxM=110) + preload (OCxPE=1) |
| TIMx->CCER | CCxE=1 (通道使能) |
| TIMx->BDTR | MOE=1 (TIM1 主输出使能) |
| TIMx->CR1 | CEN=1 (计数器使能) |
| TIMx->EGR | UG=1 (更新事件) |

## 初始化流程 (_tim_init_channel)
1. 使能定时器时钟 (RCC->APBxENR)
2. 设置 PSC / ARR
3. PWM mode 1 + preload (CCMRx)
4. 使能通道 (CCER)
5. 设置脉宽 (CCRx)
6. 主输出使能 (TIM1: BDTR.MOE)
7. UDIS+UG 加载影子寄存器
8. 启动 (CR1.CEN)

## 频率切换 (_tim_set_freq)
1. 保存 CCER → 停止 CR1.CEN → 设置新 PSC/ARR → UG 加载 → 恢复 CR1.CEN → 恢复 CCER

## set_freq() 修复要点
原实现: 仅存储 `_freq_hz[i]`，不编程定时器
新实现: `set_freq()` 直接重配 PSC+ARR (调用 `_write_hw`)

## ChibiOS 对比

| 功能 | ChibiOS | RTT |
|------|---------|-----|
| 初始化 | `pwmStart(group.pwm_drv, &group.pwm_cfg)` | CMSIS TIM 寄存器 |
| 频率 | `pwmStop+pwmStart` | `_tim_set_freq()` |
| 脉宽 | `pwmEnableChannel` | `TIMx->CCRx` 写入 |
| 批量更新 | cork/push + trigger_groups() | cork/push + 逐个 |
| DShot | DMA DMAR burst | Phase 3 |

## 验证方法
- OpenOCD: `mdw 0x40010000` (TIM1 CR1) → bit0=1
- 示波器/逻辑分析仪: CH1-PE14 应输出 50Hz/400Hz PWM
