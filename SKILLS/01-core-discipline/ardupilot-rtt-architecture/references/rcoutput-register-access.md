# RCOutput 直接 TIM 寄存器访问实现

> 创建：2026-05-16 | 根因：CUAV V5 BSP 无 board.h → drv_pwm.c 不编译 → rt_pwm_set() 不可用

## 背景

CUAV V5 的 RT-Thread BSP 没有标准 `board.h`（`stm32f765-cuav-v5/board/` 中仅有 `ardupilot_force_include.h` + `drv_spi_lld.h`）。`libraries/HAL_Drivers/drivers/drv_pwm.c` 依赖 `board.h` 中的 `BSP_USING_PWMx` 宏和 `PWMx_CONFIG` 宏来注册 PWM 设备。由于 board.h 不存在，`drv_pwm.c` 不会被编译，`rt_device_find("pwm1")` 返回 NULL，所有 `rt_pwm_set()` 调用静默失败。

**PWM 从未生效——这个 bug 从 RTT 移植开始就存在。**

## CUAV V5 PWM 硬件映射

| 通道 | Timer | CH | 引脚 | 定时器类型 | 时钟 |
|------|-------|----|------|-----------|------|
| CH1  | TIM1  | 4  | PE14 | 高级定时器 | APB2=216MHz |
| CH2  | TIM1  | 3  | PA10 | 高级定时器 | APB2=216MHz |
| CH3  | TIM1  | 2  | PE11 | 高级定时器 | APB2=216MHz |
| CH4  | TIM1  | 1  | PE9  | 高级定时器 | APB2=216MHz |
| CH5  | TIM4  | 2  | PD13 | 通用定时器 | APB1=108MHz |
| CH6  | TIM4  | 3  | PD14 | 通用定时器 | APB1=108MHz |
| CH7  | TIM12 | 1  | PH6  | 通用定时器 | APB1=108MHz |
| CH8  | TIM12 | 2  | PH9  | 通用定时器 | APB1=108MHz |

## 关键寄存器

### PSC + ARR（频率控制）

```
TIMx->PSC : 预分频器（16-bit, 0~65535）
TIMx->ARR : 自动重装载（16-bit, 0~65535）
频率 = timer_clk / ((PSC+1) * (ARR+1))
周期(ns) = 1e9 / 频率
```

`drv_pwm_set()` 中的 PSC 计算：
```c
period_ticks = period_ns * tim_clock_MHz / 1000;
psc = period_ticks / MAX_PERIOD + 1;  // MAX_PERIOD=65535
period = period_ticks / psc;
```

### CCMR（PWM 模式）

在 CCMR1/CCMR2 中设置 OCxM=110（PWM mode 1，向上计数时 CNT<CCR 输出有效）：
```c
tim->CCMR1 = (tim->CCMR1 & ~TIM_CCMR1_OC1M) 
           | (0x6 << TIM_CCMR1_OC1M_Pos)  // OC1M=110=PWM mode 1
           | TIM_CCMR1_OC1PE;             // 预装载使能
```

### CCER（输出使能）

```c
tim->CCER |= TIM_CCER_CC1E << (4 * (ch - 1));  // 通道 ch 使能
tim->CCER &= ~(TIM_CCER_CC1E << (4 * (ch - 1))); // 通道 ch 禁用
```

### BDTR（高级定时器主输出）

TIM1 需要设 MOE 位才能使能输出（TIM4/TIM12 不需要）：
```c
if (tim == TIM1) {
    tim->BDTR |= TIM_BDTR_MOE;
}
```

### 影子寄存器更新

PSC/ARR 修改后，必须用 UG 事件触发影子加载：
```c
tim->EGR = TIM_EGR_UG;  // 产生更新事件
tim->SR = ~TIM_SR_UIF;  // 清除更新标志
```

## RCOutput 核心函数

### init()
- 初始化成员数组（_period_us, _pending_us, _freq_hz 等）
- 设为 50Hz 默认频率
- 注册 10Hz safety_update 定时器

### set_freq(uint32_t chmask, uint16_t freq_hz)
- 先存储 _freq_hz[]
- 然后立即重写硬件：对已激活的通道调用 _write_hw()
- 这与 ChibiOS 不同（ChibiOS 在 set_freq 中调用 pwmStart 重启定时器），但行为等价

### _write_hw(chan, period_us)
- 首次写入 == 完整初始化：`_tim_init_channel()`
  - 时钟使能
  - PSC/ARR 计算
  - PWM mode 1 配置（CCMR）
  - CCER 使能通道
  - BDTR MOE（仅 TIM1）
  - EGR UG 加载影子寄存器
  - TIMx->CR1 CEN 启动计数器
- 后续写入 == 仅更新：只设 PSC/ARR/CCR，EGR UG

### timer_tick()
- 250Hz 触发频率（每 4 个 1kHz tick 一次），仅重写有变化的通道
- 与 ChibiOS 的 250Hz safety-only trigger 匹配

## 关键差异对比

| 方面 | ChibiOS (pwmStart/pwmStop) | RTT 直接寄存器 | 评价 |
|------|---------------------------|---------------|------|
| 初始化时机 | init() 后 timer 即运行 | 首次 write 时才 init | 行为等价（无 write 前 timer 状态无关紧要） |
| 频率重配 | pwmStop → pwmStart → 刷新所有通道 | set_freq 中即时重写受影响通道 | RTT 更精细粒度的通道级控制 |
| DMA | 支持 DShot 的 DMA 突发写入 | 不支持 DShot | Phase 3 功能缺口 |
| 同步更新 | cork/push | cork/push（通过 _pending_us） | 等价 |
| 错误处理 | chSysHalt on failure | 静默跳过 ndtr 不足的情况 | 可以加强 |

## 调试方法

检查 TIM 寄存器：
```bash
# TIM1: 0x40010000, TIM4: 0x40000C00, TIM12: 0x40001800
openocd -c "mdw 0x40010000"  # CR1
openocd -c "mdw 0x40010028"  # CCR1
openocd -c "mdw 0x4001002C"  # CCR2
openocd -c "mdw 0x40010034"  # CCER
openocd -c "mdw 0x40010044"  # BDTR (TIM1 only)
```
