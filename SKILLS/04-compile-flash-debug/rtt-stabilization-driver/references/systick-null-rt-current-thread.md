# SysTick NULL rt_current_thread HardFault

## 发现时间
2026-05-09

## 症状
- CFSR=0x00010000 (IACCVIOL)
- HFSR=0x40000000 (FORCED)
- 故障 PC 在 `rt_hw_board_init` 函数的文字池（literal pool）中
- 异常帧 LR=0xFFFFFFFF
- 栈使用: 初始 MSP - 当前 SP ≈ 8212 字节（对于 8KB 栈是溢出，对于 16KB 栈是正常）

## 完整调用链（Boot → Crash）

```
Reset_Handler (0x080EE920)
  → SystemInit()
  → entry() (0x080FA71A)
    → rtthread_startup()
      → push {r3, lr}             ← 保存 LR = entry+6 = 0x080FA720
      → rt_hw_interrupt_disable()
      → rt_hw_board_init()
        → push {r3, lr}           ← 保存 LR = rtthread_startup+10 = 0x080FA6F6
        → _mpu_config()
        → _fpu_context_init()
        → SCB_EnableICache()
        → SystemClock_Config()
        → rt_hw_systick_init()    ← 使能 SysTick 中断，每 1ms 触发一次
          → HAL_SYSTICK_Config()  ← 设置 CTRL |= TICKINT | ENABLE
        → ... 还在 rt_hw_board_init 内执行后续函数 ...
        → ~1ms 后第一次 SysTick 触发 → SysTick_Handler()
          → rt_interrupt_enter()  ← OK
          → HAL_IncTick()         ← OK
          → rt_tick_increase()    ← CRASH!
            → rt_thread_self() = rt_current_thread → NULL (未初始化!)
            → thread->remaining_tick-- → NULL 指针解引用 → MemManage Fault
        → (后续不再执行)
```

## 为什么之前 L0 验证通过了？

之前的 L0 验证（CC 误回滚前）可能因为：
1. 编译配置不同（不同 `RT_TICK_PER_SECOND` 值、不同中断优先级屏蔽）
2. 栈大小不同（8KB 时可能因栈溢出先触发 HardFault，掩盖了 SysTick 问题）
3. 构建缓存导致并不是最新代码被编译

## 修复方法

### 方法 A（推荐）：在 SysTick_Handler 中加 NULL 守卫

修改 `modules/rt-thread/bsp/stm32/libraries/HAL_Drivers/drv_common.c` 中的 `SysTick_Handler`:

```c
void SysTick_Handler(void)
{
    rt_interrupt_enter();

    if (SysTick->CTRL & SysTick_CTRL_COUNTFLAG_Msk)
        HAL_IncTick();

    /* SAFE guard: skip rt_tick_increase() before scheduler starts
     * (rt_current_thread==NULL) to avoid NULL dereference in
     * rt_thread_self()->remaining_tick during early boot init. */
    if (rt_current_thread != RT_NULL)
        rt_tick_increase();

    rt_interrupt_leave();
}
```

### 方法 B（备选）：延迟 SysTick 使能

在 `rt_hw_board_init()` 中调用 `rt_hw_systick_init()` 后立即禁用 SysTick 中断：
```c
rt_hw_systick_init();
NVIC_DisableIRQ(SysTick_IRQn);  // 暂时禁用
```

然后在 `rt_system_scheduler_start()` 之后重新使能：
```c
NVIC_EnableIRQ(SysTick_IRQn);
```

但方法 B 需要修改内核代码（`rt_system_scheduler_start()` 在 RT-Thread 内核中），侵入性更大。

## 构建系统注意事项

`SysTick_Handler` 在 `modules/rt-thread/bsp/stm32/libraries/HAL_Drivers/drv_common.c` 中定义。
这个文件是子模块的一部分，直接从子模块编译（不经过 `build/rtt_deploy/` 拷贝）。

修改子模块文件后，必须先删除对应的 `.o` 文件再编译，否则 SCons 不会重编：
```bash
rm -f build/rtt_deploy/cuav_v5/build/libraries/HAL_Drivers/drv_common.o
scons ...
```

或彻底清理：
```bash
rm -rf build/rtt_deploy/ build/rtt_cuav_v5/
scons ...
```

## 验证方法

烧录后通过 OpenOCD 检查：
```bash
# 检查无 HardFault
echo "halt" | nc -q 2 localhost 4444
echo "reg pc" | nc -q 1 localhost 4444
echo "mdw 0xE000ED28 3" | nc -q 2 localhost 4444  # CFSR+HFSR+DFSR

# 预期结果：
# CFSR = 0x00000000（无故障）
# PC 在 0x080exxx 范围（非 0x080083ca HardFault hang）
```

## 阶梯隔离法（定位此类问题的方法论）

当 HardFault 根因不明时，使用 **binary search 逐步加回**的方式定位：

```c
// Step 0: 最小化函数 — 仅设置 VTOR
void rt_hw_board_init(void) {
    SCB->VTOR = 0x08008000U;
    rt_kprintf("minimal\n");
}

// Step 1: 加回 MPU + FPU + Cache + FLASH + NVIC
// Step 2: 加回 SystemClock_Config
// Step 3: 加回 rt_hw_systick_init
// ...以此类推

// 每步编译→烧录→验证，找到触发故障的准确函数
```

关键修改文件：`libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c`（主仓库模板，非子模块文件）

## Git 提交记录

- 修改文件: `modules/rt-thread/bsp/stm32/libraries/HAL_Drivers/drv_common.c`
- 改动: 在 `SysTick_Handler` 中添加 `rt_current_thread != RT_NULL` 守卫
- 分支: 当前工作区（尚无提交）

## 关联技能

- `rtt-stabilization-driver` — 主要的稳定性调试技能
- `rtt-l0-to-l1-plan` — L0→L1 推进计划
- `rtt-cuav-v5-flash-verify` — 烧录验证工作流
