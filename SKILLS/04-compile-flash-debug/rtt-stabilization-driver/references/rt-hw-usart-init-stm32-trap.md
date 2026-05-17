# rt_hw_usart_init STM32 陷阱

## 发现时间
2026-05-09

## 症状

在 `rt_hw_board_init()` 中取消注释 `rt_hw_usart_init()` 后，固件烧录启动后立即 HardFault（MCU 不响应 halt 请求，telnet 超时）。

## 根因

`rt_hw_usart_init()` 在 STM32 HAL Drivers 中 **没有实现**。该函数声明为 extern：
```c
extern int rt_hw_usart_init(void);
```

但 `modules/rt-thread/bsp/stm32/libraries/HAL_Drivers/drv_usart.c` 中并没有定义此函数。在其他 MCU 系列中（AT32、GD32、NXP 等）有各自的实现，但 STM32 没有。

链接器表现：函数被解析为弱符号（0 地址弱引用）。调用 `rt_hw_usart_init()` 时，PC 跳到地址 0 附近 → **IACCVIOL HardFault**（因为地址 0 不可执行）。

## 验证方法

查看 ELF 符号表确认函数是否已定义：
```bash
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep "usart_init"
# 若输出为空 → 函数未定义
```

## 修复方向

需要为 STM32 实现 `rt_hw_usart_init()`。有两种方案：

### 方案 A（推荐）：在主仓库的 rt_board_init.c 中直接初始化 UART

在 `libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c` 的 `rt_hw_board_init()` 中，用寄存器级别的 UART 初始化替代 `rt_hw_usart_init()` 调用。CUAV V5 使用的 UART 端口：
- USART2 (TELEM1)
- USART3 (TELEM2)
- USART1 (GPS1)
- UART4 (GPS2)
- USART6 (TELEM3)
- UART7 (Debug)

### 方案 B：在 drv_usart.c 中添加 rt_hw_usart_init 实现

参考其他 BSP 的实现（如 at32 的 `drv_usart.c` 第 977 行），为 STM32 添加对应的实现。需要遍历 UART 配置表并调用 `rt_hw_usart_init()`。

## 当前状态

在 `rt_board_init.c` 中注释掉 `rt_hw_usart_init()` 调用。USART 功能缺失但不影响 L0 验证（USB CDC 作为主通信通道，不需要 UART）。
