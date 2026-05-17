---
name: rtt-cuav-v5-adc-spi-conflict
description: RT-Thread CUAV v5 ADC与SPI冲突的调试与解决方案（含直接CMSIS ADC寄存器访问实现）
category: embedded
---
# RT-Thread CUAV v5 ADC SPI冲突调试方法

## 问题描述
在CUAV v5（STM32F767）上启用`HAL_ADC_MODULE_ENABLED`会导致INS初始化失败，表现为：
- 系统反复打印"INS: unable to initialise driver"
- 无SYS_STATUS心跳以外的MAVLink消息
- 启动时间异常长

## 根因
启用`HAL_ADC_MODULE_ENABLED`后，RTT BSP的`drv_adc.c`被编译进固件，其`HAL_ADC_Init()`调用会干扰SPI1/2/3陀螺仪的初始化（GPIO复用、时钟等资源冲突）。

## 正确方案：直接CMSIS寄存器访问ADC

**策略**：不启用HAL_ADC_MODULE_ENABLED，改用直接STM32F7 CMSIS寄存器访问ADC，完全绕过RTT的HAL ADC层。

### 引脚限制
- **PA4不能作为ADC**：该引脚同时是SPI1_NSS（陀螺仪CS），必须排除
- CUAV V5有效ADC通道：PA0, PA1, PA2, PA3, PB0, PC0, PC1, PC4
  - 逻辑通道映射：{0, 1, 2, 3, 8, 10, 11, 14}

### 关键CMSIS寄存器访问要点

#### 1. ADC Common寄存器（CCR）不能用ADC1->CCR
**错误**：`ADC1->CCR`（CCR不在ADC_TypeDef结构里）
**正确**：`ADC123_COMMON->CCR`（CCR在ADC_Common_TypeDef结构里，独立地址0x40012304）

```cpp
// 设置ADC预分频器 PCLK2/4
ADC123_COMMON->CCR = (ADC123_COMMON->CCR & ~(3U << 16)) | (1U << 16);
```

#### 2. RCC时钟直接操作比HAL宏更可靠
不用`__HAL_RCC_ADC1_CLK_ENABLE()`，直接写RCC寄存器：
```cpp
RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN;
RCC->APB2ENR |= RCC_APB2ENR_ADC1EN;
(void)RCC->APB2ENR;  // 读回确保时钟使能生效
```

#### 3. GPIO模拟模式配置
```cpp
GPIOA->MODER |= 0xFF;     // PA0-3 analog
GPIOB->MODER |= 0x3;      // PB0 analog
GPIOC->MODER |= 0x30F;    // PC0,PC1,PC4 analog
```

#### 4. ADC初始化序列
```cpp
ADC1->CR2 = 0;                              // 先关闭
ADC1->CR1 = 0;                              // 12-bit, no scan
ADC1->CR2 = ADC_CR2_ADON | ADC_CR2_EOCS;   // 使能 + EOC per conversion
// 等待稳定
for (volatile uint32_t i = 0; i < 200; i++) __NOP();
```

#### 5. 单次转换读取
```cpp
// 设采样时间
if (ch < 10) ADC1->SMPR2 = (7U << (ch * 3));      // 112 cycles
else         ADC1->SMPR1 = (7U << ((ch-10) * 3));
ADC1->SQR3 = ch;   // 序列位置1
ADC1->SQR1 = 0;    // L=0 → 1次转换
ADC1->SR = 0;
ADC1->CR2 |= ADC_CR2_SWSTART;  // bit 30, F7确认就是SWSTART
// 轮询EOC（用 volatile counter，不要用 rt_tick_get()！）
for (volatile uint32_t t = 0; t < 100000; t++) {
    if (ADC1->SR & ADC_SR_EOC) return ADC1->DR & 0xFFF;
}
(void)ADC1->DR;  // timeout 时读 DR 清标志
return 0;
```

### ⚠️ 已知问题：ADC EOC 永久不置位（2026-05-10 确认）

即使 CMSIS 寄存器 ADC 初始化序列完全正确，**ADC1 的 EOC 标志位在 CUAV V5 上始终不置位**。

**现象**：
```cpp
// AnalogIn.cpp:89-95 — 每次调用都走 timeout 路径
ADC1->SR = 0;                     // 清所有标志
ADC1->CR2 |= ADC_CR2_SWSTART;     // 开始转换
for (volatile uint32_t t = 0; t < 100000; t++) {
    if (ADC1->SR & ADC_SR_EOC) {  // ← 永远不成立！
        val = ADC1->DR; return;
    }
}
// → 每次走 timeout: rtt_adc_timeout_count++
// → SR 状态: 0 (EOC=0)
```

**但 ADC 转换可能已完成**：读 `ADC1->DR` 返回的是最后一次有效转换值
（DR 有 16 位缓冲，始终保存最后结果，不受 EOC 影响）。

**根因推测**：
1. ADC 时钟配置问题 — ADC1 时钟来自 APB2，需要正确的预分频
2. SWSTART 触发方式不被 ADC 识别 — 可能是硬件差异（STM32F767 vs 通用 F7）
3. 或 ADC 处于关闭状态（ADON=0 时 SWSTART 被忽略）

**影响**：
- 定时器线程每 1ms 调用 `_timer_tick()` → 每次 `_adc_read()` 超时消耗 ~0.5ms
- 定时器线程消耗约 **50% CPU**，严重拖慢主线程（尤其是启动阶段的 IMU 初始化）
- 在 main thread 进行 `ins.init()` → 15 秒内仅推进到 SPI 设备创建阶段

**修复方向**（重要性从高到低）：
1. **修复 ADC 时钟**：检查 `ADC123_COMMON->CCR` 的 ADCPRE 位、`RCC->APB2ENR` 的 ADC1EN
2. **改用 TIM 触发**：配置 TIM2/TIM3 输出 TRGO 触发 ADC 定时自动转换，消除轮询
3. **改为非阻塞读取**：在 `_adc_read()` 中设置转换后立即返回上次有效值 + 安排下次读取
4. **降频轮询**：只在每 10 次定时器 tick 中检查一次 ADC，减少 CPU 浪费
5. **返回缓存值**：如果 EOC 不置位，直接返回 `ADC1->DR`（可能返回真实值）

#### ⚠️ 验证陷阱：用 GDB 确认也不可靠

```bash
# ❌ 不可靠：每次 halt 后读 SR 不一定反映转换时的状态
echo 'mdw 0x40012000' | nc -q 1 localhost 4444  # ADC1_SR
# 读到的 SR=0 可能是因为 halt 这个时刻刚好不在转换中

# ✅ 可靠：检查 CONFIG 寄存器确认 ADC 时钟
echo 'mdw 0x40012304' | nc -q 1 localhost 4444  # ADC123_COMMON->CCR
# bit17:16 = ADCPRE (0=div2, 1=div4, 2=div6, 3=div8)

# 确认 ADC1 时钟使能
echo 'mdw 0x40023844' | nc -q 1 localhost 4444  # RCC->APB2ENR
# bit8 = ADC1EN (0=disabled, 1=enabled)
```
- `rt_tick_get()` 粒度 1ms，ADC 转换 <1us → 超时判断太粗
- `__DMB()` 在 EOC 轮询循环中无意义（SR 是设备寄存器，编译器不会重排序 volatile 访问）
- `_timer_tick()` 1kHz 中调用 8 次 `rt_tick_get()` 可能引起时序漂移
- 正确做法：`volatile uint32_t` 计数器循环（100000 次 ≈ 0.5ms @216MHz，远超 ADC 实际转换时间）

#### 6. F7特有位定义（已验证）
- `ADC_CR2_SWSTART` = bit 30 ✓（在stm32f767xx.h中定义）
- `ADC_CR2_ADSTART` 不存在（那是F3/F4 HAL的概念）
- `ADC_CR2_EOCS` = bit 10（每次转换后置EOC，而非序列结束）

### 编译注意事项
- `ADC_VREF`不是标准宏，用常数`3.3f`
- `MAV_POWER_STATUS_BRICK_VALID`不是宏——用`(uint16_t)PowerStatusFlag::BRICK_VALID`（enum class）
- `_add_sample(float v)` 必须在cpp中实现，不能只声明
- `#include <stm32f7xx.h>` 走CMSIS路径（stm32f7xx.h → stm32f7xx_hal.h → stm32f767xx.h）

### 当前状态（2026-04-14）
- ✅ 编译通过，INS正常，不干扰SPI
- ✅ 已提交（62824c51a3），hwdef.dat 添加了8路ADC通道+电池监控定义
- ⏳ ADC读取值待实机验证（需稳定MAVLink连接读SYS_STATUS voltage_battery）
- 可能问题：ADC读取返回0mV——需GDB验证ADC1->CR2和RCC->APB2ENR硬件状态
- 注意：pymavlink 2.4.48 有 post_message TypeError bug，用 recv_msg() 替代 recv_match()
  详见 `rtt-cuav-v5-flash-verify` skill

### 验证方法
每次改动后必须同时验证：
1. INS初始化成功（无"INS: unable to initialise driver"错误）
2. BATTERY电压有值（`voltage_battery > 0` in SYS_STATUS）
3. SYS_STATUS正常（`onboard_control_sensors_health`非零）

## 调试-bisect方法
当改动较多导致问题时，用git stash逐个测试：
```bash
# 测试单一改动
git stash && python3 -m SCons --target=cuav-v5 -j16  # 编译无改动版本
# 烧录验证INS
# 逐步pop stash的改动组合，精确定位问题文件
```

## 排查ADC=0的下一步
1. **GDB读寄存器**：在`_adc_init_once`完成后断点，检查：
   - `ADC1->CR2` 应含 `ADC_CR2_ADON` (bit 0)
   - `RCC->APB2ENR` 应含 `RCC_APB2ENR_ADC1EN`
   - `ADC123_COMMON->CCR` bit 17 应为1（DIV4）
2. **检查深度掉电**：F7 ADC没有DEEPPWD位，但ADVREGEN可能有影响
3. **硬件验证**：用万用表确认PA0/PC0上有实际电压（USB供电约5V经分压）
