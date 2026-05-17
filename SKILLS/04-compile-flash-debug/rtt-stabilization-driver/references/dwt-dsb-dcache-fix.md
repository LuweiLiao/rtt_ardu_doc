# DWT _delay_microseconds_dwt DSB 修复 + D-Cache 问题

## 现象（2026-05-11 首次发现）

系统运行后，PC 始终在 `Scheduler::_delay_microseconds_dwt(Scheduler.cpp:72)` 的 while 循环中，
setup_stage 不推进。DWT_CYCCNT 寄存器正常递增（通过 OpenOCD 多次 halt 验证），
但 while 循环的条件 `(DWT_CYCCNT_REG - start) < cycles` 永不满足。

三次连续 halt（间隔1秒）全部显示相同 PC 和 PSP，说明 CPU 真正卡在同一指令。

## 反汇编验证（Scheduler.cpp:72 → 0x0806ecc0）

```asm
806ec98: mov.w r1, #0xE000E000          ; SCB base
806ec9c: ldr r3, [pc, #44]              ; r3 = &SystemCoreClock (lit:0x20000c08)
806ec9e: ldr.w r2, [r1, #3580]          ; r2 = SCB_DEMCR (0xE000EDFC)
806eca2: orr.w r2, r2, #0x1000000       ; TRCENA bit
806eca6: str.w r2, [r1, #3580]          ; 使能DWT跟踪
806ecaa: ldr r2, [pc, #36]              ; r2 = DWT_BASE (lit:0xE0001000)
806ecac: ldr r1, [r2, #0]               ; r1 = DWT_CTRL
806ecae: orr.w r1, r1, #1               ; CYCCNTENA
806ecb2: str r1, [r2, #0]               ; 使能CYCCNT
806ecb4: ldr r1, [pc, #28]              ; r1 = 1000000 (lit:0xF4240)
806ecb6: ldr r3, [r3, #0]               ; r3 = SystemCoreClock = 216000000
806ecb8: udiv r3, r3, r1                ; r3 = 216 (MHz)
806ecbc: ldr r1, [r2, #4]               ; r1 = DWT_CYCCNT (= start)
806ecbe: muls r0, r3                     ; r0 = us * 216 (= cycles)
806ecc0: ldr r3, [r2, #4]               ; r3 = DWT_CYCCNT (重读)
806ecc2: subs r3, r3, r1                ; r3 = current - start
806ecc4: cmp r3, r0                     ; 比较
806ecc6: bcc.n 806ecc0                  ; if (< cycles) 循环
```

逻辑完全正确。`DWT_CYCCNT` 是 volatile 内存映射寄存器，理论上应从硬件取新值。

## 根因：D-Cache 导致 DWT 寄存器读值被缓存

### SCB_CCR（0xE000ED14）= 0x00060200

- Bit 9 (DC) = 1 → **D-Cache 启用**
- Bit 10 (IC) = 1 → I-Cache 启用
- Bit 17 (BP) = 1 → 分支预测启用

### MPU 配置（MPU_CTRL = 0x05 → ENABLE=1, PRIVDEFENA=1）

| Region | Base | Size | Attr | Cache | 用途 |
|--------|------|------|------|-------|------|
| R0 | 0x20000000 | 1MB | 0x03020027 | WT Cacheable | SRAM1+SRAM2 |
| R1 | 0x40000000 | 512MB | 0x13050039 | Device(uncacheable) | 外设 |
| R2 | 0x20020000 | 16KB | 0x0308C71B | 部分SRD禁用 | SRAM3或定制 |
| R3-R7 | — | — | 0x00000000 | 禁用 | — |

**关键分析**:

DWT 寄存器位于 0xE0001000(System Control Block 空间 0xE0000000-0xE00FFFFF)。
该地址未被任何 MPU region 覆盖。PRIVDEFENA=1 时，未被覆盖区域使用 ARMv7-M 默认内存映射。

默认映射中，System Control Block 空间属于"Device"类型 → **理论上 uncacheable**。

**但实际硬件行为表明 D-Cache 仍然缓存了读取**。可能原因：

1. **Cortex-M7 实现差异**：PRIVDEFENA 的默认映射对不同地址空间的 Device/Strongly-Ordered
   属性可能在硬件实现中不一致
2. **指令预取流**：编译器可能将 `ldr r3, [r2, #4]` 与外部 `str` 配对，pipeline 重排导致
   读值实际上来自写缓冲区
3. **普通内存映射冲突**：如果 reset 后某个 bootloader 或早期代码意外将 0xE0000000 区域
   配置为 Normal（Cacheable），该属性可能 persist 到应用启动

**建议的终极修复**：显式添加 MPU Region 覆盖 System Control Block 为 Device/Strongly-Ordered：

```c
/* 在 rt_hw_board_init() 中或 RT-Thread MPU 初始化后添加 */
#define MPU_REGION_SYSCTRL_BASE  0xE0000000
#define MPU_REGION_SYSCTRL_SIZE  0x00100000  /* 1MB, covers 0xE0000000-0xE00FFFFF */
/* RASR: ENABLE=1, SIZE=19(1MB), AP=3(Full), TEX=0,S=0,C=0,B=0=Strongly-Ordered, XN=1 */
#define MPU_RASR_SYSCTRL         0x0300003B  /* TEX=0, S=0, C=0, B=0 → Device nGnRnE */
```

## 修复方案A：DSB 内存屏障（已验证，已实施）

```cpp
void Scheduler::_delay_microseconds_dwt(uint16_t us)
{
    SCB_DEMCR_REG |= (1U << 24);
    DWT_CTRL_REG |= 1U;

    const uint32_t cycles = us * (SystemCoreClock / 1000000U);
    const uint32_t start = DWT_CYCCNT_REG;
    while ((DWT_CYCCNT_REG - start) < cycles) {
        /* spin */
        asm volatile("dsb" ::: "memory");   // ← 强制内存屏障
    }
}
```

### 效果

| 指标 | DSB 前 | DSB 后 |
|------|--------|--------|
| setup_stage 推进 | 0 / 无限期 | 630→681 在 30 秒内 |
| PC 多样性 | 始终同一地址 | 在不同线程间切换 |
| HardFault | CFSR=0 | CFSR=0（无变化）|

### 代价

DSB 在 Cortex-M7 上 stall pipeline 约 11 cycles。每次 delay 循环迭代从 ~3 周期
变为 ~14 周期（~367% 开销）。对于 250µs delay（54000 循环迭代），额外 594000 周期
≈ 2.75ms 额外延迟。

## 修复方案B：显式 MPU Region（待验证，更优雅）

在 `rt_hw_board_init()` 中添加 MPU region 覆盖 System Control Block 为 Strongly-Ordered。
如果成功，理论上 DSB 不再需要。但需验证。

## 与 ChibiOS 的对比

ChibiOS 的 `delay_microseconds()`（Scheduler.cpp 第162-175行）直接用 `chThdSleep()` 线程睡眠：

```cpp
void Scheduler::delay_microseconds(uint16_t usec) {
    if (usec == 0) return;
    uint32_t ticks = chTimeUS2I(usec);
    if (ticks == 0) ticks = 1;
    ticks = MIN(TIME_MAX_INTERVAL, ticks);
    chThdSleep(MAX(ticks, CH_CFG_ST_TIMEDELTA));
}
```

**Key differences**:
- ChibiOS 不使用 DWT 自旋循环
- ChibiOS 使用系统 timer（通常 STM32F7 上 1MHz 或 100KHz tick）进行线程睡眠
- ChibiOS 保证最小延迟大于 `CH_CFG_ST_TIMEDELTA`（通常 1）
- RTT 对 ≥1tick 已用 `rt_thread_delay()`，对 <1tick 才用 DWT

## 后续操作

1. 短期：保留 DSB 修复，已验证可工作
2. 中期：尝试方案B（显式 MPU Region），看能否移除 DSB 恢复性能
3. 长期：参考 ChibiOS，考虑将 <1ms 延迟也从 DWT 自旋改为基于 SysTick 的微睡眠
