---
name: ap-chibios-bootloader-reference
description: 参考 AP_HAL_ChibiOS 和 Tools/AP_Bootloader 代码为 RTT 移植提供权威参考。只读不修改。
alwaysApply: false
---

# AP HAL ChibiOS & Bootloader 参考指南

> ⚠️ 严格限制：`libraries/AP_HAL_ChibiOS/` 和 `Tools/AP_Bootloader/` 属于**只读参考代码**。任何时候都不允许修改、添加注释或改动这些文件。RTT 移植的代码修改只允许在 `libraries/AP_HAL_RTT/` 目录和 `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/` 中。

## 一、怎么看这两个参考

### AP_HAL_ChibiOS（主参考源）

```bash
# ChibiOS HAL 的核心参考文件
libraries/AP_HAL_ChibiOS/hwdef/common/watchdog.c   # IWDG 实现
libraries/AP_HAL_ChibiOS/hwdef/common/watchdog.h   # IWDG 接口声明
libraries/AP_HAL_ChibiOS/hwdef/common/board.c      # __early_init / __late_init 启动序列
libraries/AP_HAL_ChibiOS/hwdef/common/stm32_util.c # 实用函数
libraries/AP_HAL_ChibiOS/hwdef/common/common.ld    # 链接脚本
libraries/AP_HAL_ChibiOS/HAL_ChibiOS_Class.cpp     # init() / setup() / main loop
libraries/AP_HAL_ChibiOS/Scheduler.cpp              # watchdog_pat / 线程管理
libraries/AP_HAL_ChibiOS/UARTDriver.cpp             # UART + USB CDC 驱动（重点参考）
libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat     # CUAV V5 引脚定义原型
libraries/AP_HAL_ChibiOS/hwdef/CUAVv5/hwdef.dat    # CUAV V5 专有配置
```

### ChibiOS 内核源（USB CDC/SDU 核心实现）

```bash
# SDU 驱动 — USB CDC 的完整实现
modules/ChibiOS/os/hal/src/hal_serial_usb.c         # ⭐ SDU 核心：obqueue/obnotify/SOF hook
modules/ChibiOS/os/hal/ports/STM32/LLD/OTGv2/       # ⭐ DWC2 硬件驱动层
```

### Tools/AP_Bootloader（次要参考）

```bash
Tools/AP_Bootloader/AP_Bootloader.cpp   # bootloader main() 和跳转逻辑
Tools/AP_Bootloader/support.cpp         # flash 操作 / jump_to_app
Tools/AP_Bootloader/support.h            # 接口声明
Tools/AP_Bootloader/bl_protocol.h        # jump_to_app 声明
Tools/AP_Bootloader/mcu_f7.h             # STM32F7 MCU 信息表
Tools/bootloaders/CUAVv5_bl.elf          # 预编译 CUAV V5 bootloader（反汇编用）
Tools/bootloaders/CUAVv5_bl.bin          # 预编译二进制
```

---

## 二、Boot Sequence 对照

### 1. ChibiOS（已验证生产环境）

```
Reset_Handler
  ├── __early_init()           # board.c
  │   ├── stm32_gpio_init()   # 所有 GPIO 端口复位+配置
  │   ├── stm32_clock_init()  # 系统时钟 216MHz
  │   └── cache/MPU 配置
  ├── .data / .bss 初始化      # crt0
  ├── __libc_init_array()     # C++ 构造函数
  ├── main() / __late_init()
  │   ├── halInit(), chSysInit()
  │   ├── stm32_watchdog_save_reason()   # 保存复位原因
  │   ├── stm32_watchdog_clear_reason()  # 清除粘滞标志
  │   ├── malloc_init()
  │   └── setup_usb_strings()
  ├── 主线程创建 → main_thread()
  │   ├── callbacks->setup()   # ArduPilot init_ardupilot()
  │   │   ├── ... 各种 HAL 初始化 ...
  │   │   ├── AP_BoardConfig::watchdog_enabled() ? stm32_watchdog_init()  # ⭐ 这里才启动 IWDG
  │   │   ├── schedulerInstance.watchdog_pat()  # 第一次喂狗
  │   │   └── set_system_initialized()
  │   └── while(1) { loop(); schedulerInstance.watchdog_pat(); }
```

### 2. Bootloader

```
Reset_Handler
  ├── __early_init()          # ChibiOS 相同
  ├── .data / .bss
  ├── main()
  │   ├── flash_init()
  │   ├── check_ecc_errors()  # H7 专用
  │   ├── 快速启动逻辑
  │   │   ├── stm32_was_watchdog_reset()  # 检查是否是看门狗复位
  │   │   └── 若 watchdog 复位 + 之前固件 OK → 立即跳转
  │   ├── 若 try_boot: jump_to_app()
  │   │   ├── 验证 app 大小和 CRC
  │   │   ├── 关闭所有 NVIC 中断 (ICER/ICPR)
  │   │   ├── 设置 VTOR = 0x08008000 (或 flash_base)
  │   │   └── 跳转到 app 入口 (取向量表 SP/PC)
  │   └── 否则: bootloader 循环等待刷写
```

### 3. RTT（当前移植 — 应参考以上两者）

```
Reset from bootloader (0x08008000)
  ├── RT-Thread startup
  │   ├── Reset_Handler -> 系统时钟/HAL 初始化
  │   └── rt_hw_board_init()
  │       ├── MPU 配置 (memory region 0/1/2)
  │       ├── FPU 配置 (FPCCR ASPEN/LSPEN)
  │       ├── GPIO/UART/SPI/SDIO 板级初始化
  │       ├── rt_system_timer_init() / rt_system_scheduler_init()
  │       └── rt_application_init() -> 创建 main 线程
  ├── main 线程 -> _main_loop_entry()
  │   ├── callbacks->setup()   # ArduPilot init_ardupilot()
  │   │   └── ... HAL init ...
  │   ├── set_system_initialized()  # ⭐ 此处应启动 IWDG（当前未做）
  │   └── while(1) { loop(); watchdog_pat(); }
```

---

## 三、Watchdog 实现参考 (ChibiOS → RTT)

### ChibiOS 实现（已验证可用）

```c
// watchdog.c — 完全可靠的参考实现
#define STM32_WDG_TIMEOUT_MS 2048   // 默认 2 秒超时

static bool watchdog_enabled;       // 静态标志，启动后设为 true

void stm32_watchdog_init(void)
{
    IWDGD.KR = 0x5555;              // 解锁 PR/RLR
    IWDGD.PR = 3;                   // 预分频 /32 (固定值，改了会改变超时计算)
    IWDGD.RLR = STM32_WDG_TIMEOUT_MS - 1;  // 重装载值 = 超时毫秒 - 1
    IWDGD.KR = 0xCCCC;             // 启动 IWDG
    watchdog_enabled = true;        // 标记已启用
}

void stm32_watchdog_pat(void)
{
    if (watchdog_enabled) {         // 只有启动后才喂狗
        IWDGD.KR = 0xAAAA;
    }
}
```

**关键要点（与之前失败的 RTT 实现对比）：**

| 方面 | ChibiOS ✅ | 以前 RTT ❌ |
|------|-----------|------------|
| PR 设置 | PR=3 (/32)，不等待 PVU/RVU | PR=6 (/256)，死等 PVU/RVU |
| 调用时机 | `setup()` 中，线程和中断就绪后 | 在 `rt_board_init.c` 早期，USB 初始化前 |
| 条件喂狗 | `watchdog_enabled` 标志 | 无用条件 + `#if 0` 禁用 |
| 超时 | 2048ms（默认），可配置 | 10s（固定） |
| 重配 IWDG | 每次 init 都重配 PR/RLR（KR=0x5555 后） | 同上，但死等 PVU/RVU |

**ChibiOS 不等待 PVU/RVU 的原因：**
- PVU (Prescaler Update) 和 RVU (Reload Update) 是 IWDG 内部同步标志
- 当 IWDG 已经由 bootloader 启用后，重配 PR/RLR 需要等内部同步
- **但 ChibiOS 每次都从头 init（KR=0x5555 → 写 PR → 写 RLR → 0xCCCC），它在 bootloader 没启动 IWDG 的前提下写，所以不需要等 PVU/RVU**
- 如果 bootloader 提前启动了 IWDG（H7 的某些 bootloader 可能这样做），重配 PR/RLR 就需要等同步
- 对于 CUAV V5 (F7)：**bootloader 不启动 IWDG**，所以不需要等 PVU/RVU

### RTT 应采用的实现

```cpp
// 在 Scheduler.cpp 的 set_system_initialized() 中调用
// 或者在 HAL_RTT_Class.cpp 的 setup() 返回后调用
void Scheduler::_start_watchdog(void)
{
    // 完全照搬 ChibiOS 风格
    *(volatile uint32_t *)0x40003000 = 0x5555;  // 解锁
    *(volatile uint32_t *)0x40003004 = 3;        // PR=/32
    *(volatile uint32_t *)0x40003008 = 2047;     // RLR=2047 → ~2s
    *(volatile uint32_t *)0x40003000 = 0xCCCC;   // 启动
    _iwdg_started = true;
}
```

---

## 四、Bootloader 关键参考信息

### 4.1 Bootloader 不启动 IWDG（已验证）

```bash
arm-none-eabi-objdump -d Tools/bootloaders/CUAVv5_bl.elf | grep "0xCCCC\|0x5555"
# → 只有 0x55555555（用于 GPIO 等），没有 0xCCCC（IWDG 启动密钥）
```

- Bootloader 调用 `stm32_was_watchdog_reset()` 读取 RCC_CSR 检查复位原因
- Bootloader 调用 `stm32_watchdog_save_reason()` 保存复位原因
- Bootloader **不**调用 `stm32_watchdog_init()` 或 `stm32_watchdog_pat()`
- 所以 **CUAV V5 的 IWDG 从未被 bootloader 启用过**

### 4.2 Bootloader 的跳转前准备

`jump_to_app()` 在跳转到应用前执行：
1. **向量表有效性检查**：扫描 flash 范围寻找非擦除字，验证 Reset_Handler 在 (bootloader_size < addr < flash_end) 范围内
   - ⚠️ 不检查 app_descriptor CRC/image_size — 仅检查向量表有效即可跳转
   - app_descriptor 仅被 serial upload 协议验证，不影响 `jump_to_app()`
2. 关闭所有中断：写 NVIC ICER 和 ICPR 清除所有挂起中断
3. 设置 VTOR 为应用入口地址（CUAV V5: 0x08008000）
4. 取向量表的前两个 word（SP 和 PC）
5. 设置 MSP 为应用 SP，然后跳转到应用 PC

### 4.3 Bootloader 的快速启动策略（重要！）

```cpp
// AP_Bootloader.cpp main()
bool try_boot = false;
uint32_t timeout = HAL_BOOTLOADER_TIMEOUT;  // 默认 5000ms

// 如果之前是 watchdog 复位，立即跳转不等待
if (stm32_was_watchdog_reset()) {
    try_boot = true;
    timeout = 0;
}
```

这意味着：当系统因 IWDG 复位后重新启动，bootloader 会立即跳转到应用（延迟为 0）。这在快速恢复循环中很重要。

---

## 五、CUAV V5 硬件引脚对照 (ChibiOS → RTT)

### SPI1 — 内部传感器（ICM20689, ICM20602, BMI055）
| 信号 | ChibiOS fmuv5 | RTT (正确) |
|------|---------------|------------|
| SCK  | PG11 (AF5)    | PG11 (AF5) |
| MISO | PA6 (AF5)     | PA6 (AF5)  |
| MOSI | PD7 (AF5)     | PD7 (AF5)  |

### SPI4 — 气压计（MS5611）
| 信号 | ChibiOS fmuv5 | RTT (正确，已修正) |
|------|---------------|-------------------|
| SCK  | PE2 (AF5)     | PE2 (AF5)         |
| MISO | PE13 (AF5)    | PE13 (AF5)        |
| MOSI | PE6 (AF5)     | PE6 (AF5)         |

### SPI2 — FRAM
| 信号 | ChibiOS fmuv5 | RTT |
|------|---------------|-----|
| SCK  | PI1 (AF5)     | PI1 (AF5) |
| MISO | PI2 (AF5)     | PI2 (AF5) |
| MOSI | PI3 (AF5)     | PI3 (AF5) |

### 传感器电源控制
| 信号 | ChibiOS fmuv5 | RTT |
|------|---------------|-----|
| VDD_3V3_SENSORS_EN | PG2 (GPIO OUTPUT HIGH) | PG2 (GPIO OUTPUT HIGH) |
| VDD_3V3_SD_CARD_EN | PG7 (GPIO OUTPUT HIGH) | PG7 (GPIO OUTPUT HIGH) |

### USB OTG1
| 信号 | ChibiOS fmuv5 | RTT |
|------|---------------|-----|
| DM   | PA11 (AF10)   | PA11 (AF10) |
| DP   | PA12 (AF10)   | PA12 (AF10) |
| VBUS | PA9 (AF10)    | PA9 (AF10)  |
| ID   | PA10 (AF10)   | PA10 (AF10) |

### UART 映射
| 端口 | ChibiOS fmuv5 | RTT |
|------|---------------|-----|
| SERIAL0 (USB) | OTG1 | OTG1 |
| SERIAL1 (TELEM1) | USART2 (PD5 TX, PD6 RX) | USART2 (PD5 TX, PD6 RX) |
| SERIAL2 (TELEM2) | USART3 (PD8 TX, PD9 RX) | USART3 (PD8 TX, PD9 RX) |
| SERIAL3 (GPS1) | USART1 (PB6 TX, PB7 RX) | USART1 (PB6 TX, PB7 RX) |
| SERIAL4 (GPS2) | UART4 (PD1 TX, PD0 RX) | UART4 (PD1 TX, PD0 RX) |
| SERIAL5 (TELEM3) | USART6 (PG14 TX, PG9 RX) | USART6 (PG14 TX, PG9 RX) |
| SERIAL6 (DEBUG) | UART7 (PE8 TX, PF6 RX) | UART7 (PE8 TX, PF6 RX) |

---

## 六、复位原因诊断

```cpp
// 如何判断复位原因（参考 ChibiOS watchdog.c + stm32_util.c）
uint32_t csr = RCC->CSR;
bool is_iwdg = (csr & (1U << 29)) != 0;  // IWDGRSTF
bool is_wwdg = (csr & (1U << 28)) != 0;  // WWDGRSTF
bool is_sft  = (csr & (1U << 28)) != 0;  // SFTRSTF (注意和 WWDG 同一位！)
bool is_pin  = (csr & (1U << 26)) != 0;  // PINRSTF
// 注意：这些标志位是粘滞的，不清除的话会一直保持
// ChibiOS 在 __late_init() 中用 stm32_watchdog_clear_reason() 清除
// RCC->CSR |= (1U << 24);  // RMVF — 复位标志清除位
```

**⚠️ 粘滞位陷阱**：RCC_CSR 的 WDGRSTF 不清除就不变。如果一次启动中测到 "WDGRSTF=1"，不一定说明本次是看门狗复位——可能只是上次的残留。必须在启动早期清除该标志，然后等复位后再读。

正确做法（抄 ChibiOS）：
1. 启动早期（相当于 ChibiOS 的 `__late_init()`）→ `stm32_watchdog_save_reason()` + `stm32_watchdog_clear_reason()`
2. RTT 中应在 `rt_hw_board_init()` 末尾或 `HAL_RTT_Class::run()` 开头做这件事

---

## 七、常见问题和排查方向

### 7.1 HardFault 排查
Bootloader 中 pre-built 的 `jump_to_app()` 会清除 NVIC 中断，但不会清除 MPU、FPU 等配置。如果应用在启动时 HardFault：
1. 检查 VTOR 是否设置正确（应为 0x08008000）
2. 检查 SP 值（向量表第一个 4 字节）是否在 SRAM 范围内
3. 如果 CFSR=IACCVIOL — 说明 PC 指针跳到了数据区，通常是栈被踩
4. 如果 CFSR=PRECISERR — 总线错误，通常是访问了外设的不支持的地址或 MPU 禁止区域
5. 对比 ChibiOS 的 `__early_init()` — RTT 的 `_mpu_config()` 是否覆盖了必要的外设空间？

### 7.2 线程栈大小参考
ChibiOS 主线程栈 = 默认 `main` 线程的栈（约 256 words = 1KB），通过 ChibiOS conf 配置。
RTT 定时器线程：16KB/8KB/2KB 等不同大小。
RTT 主线程栈：当前 32KB（未修改基础）→ 64KB（含 Logger workaround）。

### 7.3 IWDG 喂狗频率
- ChibiOS 主循环：每 loop() 一次调一次 `stm32_watchdog_pat()`
- ChibiOS 定时器线程：在 `in_expected_delay()` 时不喂（因为主循环正在运行）
- RTT 当前：主循环中喂狗，定时器线程中只有在 `in_expected_delay()` 时才喂
- 建议：**无条件喂狗**（忽略 `in_expected_delay()` 条件），因为 IWDG 的超时很短（2s），万一调度延迟可能超时

### 7.4 CDC TX 无数据 — 分层调试速查

**现象**：USB 枚举成功（`/dev/ttyACM1` 出现），但 `cat` 或 Python serial 读不到数据。

**三步定位法**：

1. **Layer 1 — CherryUSB**: 读 `dbg_serial_write_calls`（GDB）是否为 0？
   - =0 → 数据没到 CDC 层 → 看 Layer 2
   - >0 → CDC 层收到数据但没发出 → 检查 SOF hook / tx_active 自愈

2. **Layer 2 — UARTDriver**: 读 `rtt_uart_dbg_tick_calls` 是否为 0？
   - =0 → UART 线程没跑 → 看 Layer 3
   - >0 但 `drain_zero` 很大 → CherryUSB ringbuffer 写不进去

3. **Layer 3 — RT-Thread**: GDB `bt` 是否在 `rt_defunct_execute`？
   - 是 → idle 线程清理已终止的线程 → UART 线程已死
   - 否 → 检查 `_hal_initialized` 是否被设置

**详细参考**：见 `references/rtt-cdc-tx-debug-methodology.md`

### 7.5 工作流原则：先学 ChibiOS 再动手

⚠️ **廖博士明确要求**（2026-05-12）：修改 RTT USB CDC TX 前，必须先深入理解 ChibiOS SDU 架构。不允许不经学习就直接调试/修改 CherryUSB 代码。

正确做法：
1. 读 `modules/ChibiOS/os/hal/src/hal_serial_usb.c` — SDU 源码
2. 对照 `modules/rt-thread/components/drivers/usb/cherryusb/platform/rtthread/usbd_serial.c`
3. 找出关键差异（SOF hook, obqueue callback, hardware status query）
4. 基于 ChibiOS 的设计原理来设计 RTT 的修复方案

---

## 八、参考时的问题清单

修改 RTT 代码前，先用这个清单对照 ChibiOS：

1. **这个功能的 ChibiOS 实现在哪？**
2. **ChibiOS 的实现在哪个阶段被调用？（__early_init / __late_init / setup() / loop()）**
3. **有没有同步/等待操作（如 PVU/RVU 等待）？ChibiOS 怎么处理的？**
4. **全局状态变量怎么保护？（static bool watchdog_enabled 模式）**
5. **中断环境：这个代码在中断中还是线程中运行？**
6. **ChibiOS 的 hwdef.dat 怎么配这个外设的引脚？**
7. **bootloader 之外，这个代码依赖什么前置条件？**

---

## 九、Bootloader jump_to_app() 验证逻辑（反汇编分析 2026-05-10，2026-05-14 修正）

**⚠️ 2026-05-14 关键修正：bootloader 不需要 app_descriptor 即可跳转！**
向量表有效性检查（SP 在 SRAM 范围内、Reset_Handler 在 flash 范围内）即满足跳转条件。app_descriptor 的 CRC/image_size 仅由 serial upload 协议使用，不影响 `jump_to_app()` 的跳转决策。

### 9.1 验证流程（源自 `CUAVv5_bl.elf` 反汇编）

```asm
_Z11jump_to_appv:
  r3 = *(pc+276)    ; app_base_ptr (flash addr of vector table)
  r1 = *(pc+280)    ; flash_end (scan upper boundary)

scan_loop:
  r3 += 4             ; advance 1 word
  r2 = *r3            ; read word from vector table
  r2 += 1             ; check: was it 0xFFFFFFFF (erased)?
  if r2 != 0 → found  ; non-erased word found → proceed to validation
  return               ; all erased → no firmware → don't jump
  
  cmp r3, r1
  bne scan_loop        ; keep scanning until r3 == flash_end

  ; Scan complete, found non-erased word. Validate Reset_Handler:
  r2 = *(app_descriptor_addr)  ; Reset_Handler value saved during scan
  if r2 <= BOOTLOADER_LIMIT → FAIL   ; Reset_Handler in bootloader area
  r3 = *(flash_geometry + 8)  ; image_size or total_bytes  
  r3 += FLASH_BASE             ; absolute address
  r3 += BOOTLOADER_SIZE        ; +32KB (bootloader reserve)
  if r2 >= r3 → FAIL           ; Reset_Handler past flash end

  ; All checks PASS → jump:
  set VTOR = app_base
  disable MPU (write 0 to MPU_CTRL)
  disable all NVIC interrupts (ICER/ICPR)
  invalidate all MPU regions
  set MSP = *(app_base)        ; SP from vector table
  set CONTROL = 0             ; Privileged thread mode, MSP
  dsb ; isb
  bx Reset_Handler              ; ← 跳转到应用
```

### 9.2 关键发现

| 发现 | 证据 |
|------|------|
| **app_descriptor 非必需** | 跳转前仅检查向量表(SP+Reset_Handler)的范围有效性 |
| **Bootloader 不启用 D-Cache** | `__core_init` 写 0 到 SCB_CCR(0xE000EF50)，初始化后 SCTLR bit 2=0 |
| **Bootloader 不启用 I-Cache** | 同上，SCB_CCR=0 禁用所有 cache |
| **跳转前有 ~5s 超时** | `bootloader()` 的 timeout=5000ms，到期后才调用 `jump_to_app()` |
| **扫描范围从 flash_start+4 开始** | 首字 (SP) 不从 flash_start 读，从 +4 开始扫描到 flash_end |

### 9.3 App Descriptor 格式

```c
struct app_descriptor_unsigned {
    uint8_t sig[8] = {0x40, 0xa2, 0xe4, 0xf1, 0x64, 0x68, 0x91, 0x06};
    uint32_t image_crc1;    // CRC from firmware start to image_crc1
    uint32_t image_crc2;    // CRC from version_major to end
    uint32_t image_size;    // total firmware size in bytes
    uint32_t git_hash;
    uint8_t  version_major;
    uint8_t  version_minor;
    uint16_t board_id;      // APJ_BOARD_ID
    uint8_t reserved[8];
};  // total: 36 bytes
```

ChibiOS 链接脚本通过 `.app_descriptor` section 将其放置在 `.text` 段的开头（紧接在 `constructors` 和 `destructors` 之后，在代码之前）。

### 9.3 RTT 构建缺少描述符的修复方向

**方法 A（最简）**：使用 `python3 Tools/scripts/uploader.py --port /dev/ttyACM1 build/rtt_deploy/cuav_v5/arducopter.apj`
   - Bootloader 的 serial update 协议会处理描述符
   - 需要先构建 `.apj` 文件（包含 PX4 JSON 头部）

**方法 B（正确修复）**：在 RTT 链接脚本（`ldscript.ld`）中添加：
```ld
SECTIONS {
    .text : {
        KEEP(*(.apsec_data));       /* 可选：安全数据 */
        KEEP(*(.app_descriptor));   /* ⭐ 添加这一行 */
        *(.text)
        *(.text.*)
    }
}
```
同时在 `AP_HAL_RTT/hwdef/cuav_v5/ldscript.ld` 中引用。

**方法 C（调试用）**：OpenOCD 直接烧录到 0x08000000（覆盖 bootloader）
   - `program build/rtt_cuav_v5/rtthread.bin 0x08000000`
   - ⚠️ 仅测试用，不可恢复

## 十、已验证的关键事实

1. **CUAV V5 的 bootloader 在 0x08000000，应用在 0x08008000**（FLASH_RESERVE_START_KB=32）
2. **Bootloader 不启动 IWDG** — 这不是复位原因
3. **RCC_CSR 的 WDGRSTF 是粘滞位** — 不清除会一直显示 True
4. **ChibiOS 在 __late_init() 中清除复位标志** → RTT 也应在 `rt_hw_board_init()` 或 `HAL_RTT_Class::run()` 早期清除
5. **ChibiOS 的 watchdog 调用在 `HAL_ChibiOS_Class.cpp` 的 `setup()` 中**，不在 board_init 阶段

---

## 十一、USB CDC / SDU 架构参考（2026-05-12 新增）

> ⚠️ 这是 RTT CherryUSB CDC TX 修复的核心参考。ChibiOS SDU（Serial USB Driver）是经过百万架次验证的生产级 USB CDC 实现。在修改 RTT 的 CDC 代码前，**必须先深入理解本章内容。**

### 11.1 架构总览

ChibiOS CDC 使用三层架构：

```
┌──────────────────────────────────────────┐
│  ArduPilot HAL (AP_HAL_ChibiOS)          │
│  UARTDriver::_tx_timer_tick()             │
│    → write_pending_bytes_NODMA()          │
│      → chnWriteTimeout(sdu, buf, TIME_IMMEDIATE)  │
├──────────────────────────────────────────┤
│  ChibiOS HAL                             │
│  hal_serial_usb.c (SDU)                  │
│    → _write() → obqWriteTimeout()        │
│    → obnotify() → usbStartTransmitI()    │
│    → sduDataTransmitted() — XFRC ISR     │
│    → sduSOFHookI() — SOF 恢复            │
├──────────────────────────────────────────┤
│  USB Driver Layer (LLD)                  │
│  OTGv2/hal_usb_lld.c                     │
│    → usbStartTransmitI() — DWC2 IN       │
│    → usbStartReceiveI() — DWC2 OUT       │
│    → 中断处理: XFRC, SOF, USB_RESET      │
└──────────────────────────────────────────┘
```

### 11.2 SDU 核心数据结构

```c
// 文件: modules/ChibiOS/os/hal/src/hal_serial_usb.c

struct SerialUSBDriver {
    const struct SerialUSBDriverVMT *vmt;       // 虚拟方法表
    struct SerialUSBConfig *config;              // 配置（USB 总线、端点等）
    input_buffers_queue_t ibqueue;               // 输入缓存队列（OUT 端点接收）
    output_buffers_queue_t obqueue;              // 输出缓存队列（IN 端点发送）
    ioblock_queue_t iobqueue;                    // I/O 阻塞队列（同步写入）
    uint8_t *ib_buf[SERIAL_USB_BUFFERS_POOL];    // 输入缓冲池
    uint8_t *ob_buf[SERIAL_USB_BUFFERS_POOL];    // 输出缓冲池
    sdu_state_t state;                           // 状态机
};
```

### 11.3 TX 数据流详解

#### 步骤 1：应用层写入

```c
// UARTDriver.cpp:991 — ArduPilot UARTDriver 的 NODMA 写路径
// 调用链: _tx_timer_tick() → write_pending_bytes() → write_pending_bytes_NODMA()
int ret = chnWriteTimeout((SerialUSBDriver*)sdef.serial, vec[i].data, vec[i].len, TIME_IMMEDIATE);
```

`chnWriteTimeout` 使用 `TIME_IMMEDIATE`（非阻塞），写入到 SDU 的 obqueue（输出缓冲队列）中。

#### 步骤 2：obqueue 回调触发硬件发送

```c
// hal_serial_usb.c:178 — obnotify() 回调
// ⭐ 每当有 buffer 插入 obqueue 时自动触发
static void obnotify(io_buffers_queue_t *bqp) {
    SerialUSBDriver *sdup = bqGetLinkX(bqp);

    // 安全检查：USB 未激活或不处于 READY 状态则跳过
    if ((usbGetDriverStateI(sdup->config->usbp) != USB_ACTIVE) ||
        (sdup->state != SDU_READY)) {
        return;
    }

    // ⭐ 流控核心：如果 IN 端点已有传输进行中，跳过
    // 当 XFRC ISR 完成后会再次触发 obnotify
    if (!usbGetTransmitStatusI(sdup->config->usbp, sdup->config->bulk_in)) {
        uint8_t *buf = obqGetFullBufferI(&sdup->obqueue, &n);
        usbStartTransmitI(sdup->config->usbp, sdup->config->bulk_in, buf, n);
    }
}
```

**关键设计**：
- `obnotify()` 是 obqueue 的**回调函数**。只要有 buffer 入队，它就被调用
- 使用 **硬件状态查询** `usbGetTransmitStatusI()` 而非软件标志来判断是否可发送
- 如果 IN 端点忙，**什么都不做**——XFRC ISR 完成后会重新触发 obnotify

#### 步骤 3：XFRC ISR — 传输完成回调

```c
// hal_serial_usb.c:442 — sduDataTransmitted()
// ⭐ DWC2 IN 端点传输完成后调用
void sduDataTransmitted(USBDriver *usbp, usbep_t ep) {
    SerialUSBDriver *sdup = usbp->in_params[ep - 1U];

    // ⭐ 释放已传输的 buffer 回 obqueue
    // 这会触发 obnotify() 检查下一个 pending buffer
    obqReleaseFullBufferI(&sdup->obqueue, n);
}
```

**关键设计**：
- `obqReleaseFullBufferI()` 释放已传输 buffer 回池
- buffer 释放**自动触发** `obnotify()`，检查是否有待发数据
- 如果有待发数据且 IN 端点空闲 → 立即开始下一个传输

#### 步骤 4：sduSOFHookI() — SOF 恢复机制（⭐ 核心）

```c
// hal_serial_usb.c:407 — sduSOFHookI()
// ⭐ 从 SOF (Start Of Frame) 中断中调用，1kHz
// ⭐ 同时从 ArduPilot UARTDriver::_flush() 中显式调用
void sduSOFHookI(SerialUSBDriver *sdup) {
    if ((usbGetDriverStateI(sdup->config->usbp) != USB_ACTIVE) ||
        (sdup->state != SDU_READY)) {
        return;
    }

    // 如果 IN 端点正在传输 → 跳过（等 XFRC ISR 完成）
    if (usbGetTransmitStatusI(sdup->config->usbp, sdup->config->bulk_in)) {
        return;
    }

    // ⭐ 尝试 flush obqueue 中未完整填满的 buffer
    if (obqTryFlushI(&sdup->obqueue)) {
        size_t n;
        uint8_t *buf = obqGetFullBufferI(&sdup->obqueue, &n);
        usbStartTransmitI(sdup->config->usbp, sdup->config->bulk_in, buf, n);
    }
}
```

**这是 ChibiOS CDC 永不卡死的原因**：即使 XFRC ISR 丢失了，SOF 中断（1kHz）会：
1. 检查 IN 端点是否空闲
2. 检查 obqueue 中是否有完整或部分填充的 buffer
3. 如果有，强制 flush 并开始传输

### 11.4 SDU 初始化与注册

```c
// hal_serial_usb.c:199-260 — sduStart()
void sduStart(SerialUSBDriver *sdup, const SerialUSBConfig *config) {
    // 初始化输出缓冲队列，注册 obnotify 回调
    obqObjectInit(&sdup->obqueue, obnotify, ...);
    
    // 初始化输入缓冲队列，注册 ibnotify 回调
    ibqObjectInit(&sdup->ibqueue, ibnotify, ...);
    
    // 注册 IN 端点完成回调 = sduDataTransmitted
    usbRegisterCallback(sdup->config->usbp, sduDataTransmitted, ...);
    
    // 注册 SOF 钩子（在 USB 配置中声明）
    // SOF 中断 → usbEventSOF 时自动调用 sduSOFHookI
}
```

SOF 钩子在 `usbcfg.c` 或 `usbcfg.h` 中注册（CUAV V5 的配置文件在 AP_HAL_ChibiOS 下）：

```c
// hwdef/CUAVv5 或 fmuv5 的 usbcfg 文件中：
static USBConfig usbcfg = {
    .usb_event = usb_event,     // USB 事件回调
    .sof_hook = sduSOFHookI,    // ⭐ SOF 中断钩子 = sduSOFHookI
    .sof_cb = NULL,             // SOF 回调（另一种形式）
};
```

### 11.5 初始化时机

USB CDC 在 ArduPilot 启动过程中被初始化：

```c
// AP_HAL_ChibiOS/hwdef/common/board.c — __late_init()
void __late_init(void) {
    ...
    setup_usb_strings();  // 配置 USB 描述符字符串
    ...
}

// AP_HAL_ChibiOS/UARTDriver.cpp — _begin()
void UARTDriver::_begin(uint32_t b, uint16_t rxS, uint16_t txS) {
    if (sdef.is_usb) {
        // 启动 SDU 驱动
        sduStart((SerialUSBDriver*)sdef.serial, ...);
    }
}
```

### 11.6 ChibiOS vs RTT CherryUSB CDC 对照表

| 维度 | ChibiOS SDU ✅ | RTT CherryUSB ❌ |
|------|---------------|------------------|
| 触发机制 | obqueue 回调 + SOF 中断双重保险 | `kick_tx()` 单一路径 |
| 流控检查 | `usbGetTransmitStatusI()` 读硬件寄存器 | `tx_active` 软件标志 + EPENA 寄存器检查 |
| 恢复机制 | `sduSOFHookI()` — 1kHz SOF 中断无条件恢复 | `_tx_stalled_bytes` 超时计数（不完整） |
| 数据所有权 | buffer 交给 USB 驱动，ISR 释放后回调通知 | `tx_pkt` 栈缓冲，紧耦合 |
| 链式传输 | XFRC ISR → obnotify → 下一 buffer | ISR → kick_tx → 设置 tx_active → ep_start_write |
| 初始化 | `sduStart()` 注册 obnotify/ibnotify/SOF | `usbd_cdc_acm_serial_init()` 注册端点回调 |
| 线程模型 | UART 线程 1kHz + SOF 中断 1kHz | UART 线程 1kHz（无中断级恢复） |
| 中断优先级 | SOF 最高优先级，XFRC 次高 | XFRC 回调（优先级待确认） |

### 11.7 RTT CherryUSB 缺少的核心机制

**CherryUSB 没有 `obqueue` + `obnotify` 的回调模式**。在 CherryUSB 中：

```c
// RTT CherryUSB flow:
// 1. write() → 放到 ringbuffer → kick_tx()
// 2. kick_tx() → 检查 tx_active → 设置 tx_active → usbd_ep_start_write()
// 3. ISR → bulk_in → 清 tx_active → kick_tx() (尝试下一个)
```

**缺少的要素**：
1. **SOF 恢复**：没有 1kHz 的 SOF 钩子来救回卡死的 tx_active
2. **硬件流控查询**：用软件 `tx_active` 替代 `usbGetTransmitStatusI()` 硬件查询
3. **buffer 所有权转移**：CherryUSB 的 `tx_pkt` 在栈上，不能等待 ISR 回调后才释放

### 11.8 修复方向

**方向 A（推荐）**：给 CherryUSB CDC 加 SOF 级恢复
```
1. 用 RT-Thread 软定时器（1ms）注册周期性回调
2. 回调中检查：tx_active && DWC2 IN 端点空闲
3. 条件满足 → 强制清除 tx_active → 调用 kick_tx()
```

**方向 B**：整改 `kick_tx()` 使用硬件状态查询
```
1. 移除 `tx_active` 软件标志
2. 改用直接读 DWC2 DIEPCTL 的 EPENA 位
3. 如果 EPENA=0 → 发送；EPENA=1 → 等下次
```

**方向 C**：在 UARTDriver 中加 `_tx_flush()` 等效
```
1. UARTDriver::_timer_tick() 中对于 USB 设备
2. 若 writebuf 有数据 pending → 直接调 usbd_ep_start_write
3. 绕过 CherryUSB 的 kick_tx 保护
```

---

## 十二、ChibiOS UARTDriver 线程模型参考（2026-05-12 新增）

### 12.1 TX 线程

```cpp
// AP_HAL_ChibiOS/UARTDriver.cpp — _tx_timer_tick()
// 从 uart_thread 中 1kHz 调用
void UARTDriver::_tx_timer_tick(void) {
    // 1. 检查 USB 状态（未连接则跳过）
    if (sdef.is_usb) {
        if (((SerialUSBDriver*)sdef.serial)->config->usbp->state != USB_ACTIVE) {
            return;
        }
    }
    
    // 2. 加锁，写入 pending 数据
    WITH_SEMAPHORE(tx_sem);
    write_pending_bytes();  // → 最终调用 chnWriteTimeout(sdu, buf, TIME_IMMEDIATE)
}
```

### 12.2 _flush() 显式推送

```cpp
// AP_HAL_ChibiOS/UARTDriver.cpp:665 — _flush()
// ⭐ USB CDC 专用：强制 SOF 钩子，刷新 pending 数据
void UARTDriver::_flush() {
    if (sdef.is_usb) {
        sduSOFHookI((SerialUSBDriver*)sdef.serial);
    } else {
        chEvtSignal(uart_thread_ctx, EVT_TRANSMIT_DATA_READY);
    }
}
```

### 12.3 USB 连接检测

```cpp
// AP_HAL_ChibiOS/UARTDriver.cpp:1260-1271
// 每次 _tx_timer_tick 都检查 USB 状态
if (sdef.is_usb) {
    if (((SerialUSBDriver*)sdef.serial)->config->usbp->state != USB_ACTIVE) {
        return;  // USB 未连接 → 不发送
    }
}
// USB 已连接 → 设置全局标志
((GPIO *)hal.gpio)->set_usb_connected();
```

---

## 十四、系统"卡在空闲线程"的诊断方法论（2026-05-14 新增）

> 现象：USB CDC 枚举（ttyACM1 出现）、无 MAVLink 数据、OpenOCD halt 显示 PC 在 `idle.c:134`（RT-Thread idle 线程）
> 与 HardFault 不同：系统在运行，但应用的主循环从未启动。

### 14.1 诊断链路（由外到内）

```
[Step 1] Bootloader 跳转了吗？
   halt 后读 PC。若 PC > 0x08008000 → 已跳转到应用 ✅
   若 PC < 0x08008000 → bootloader 未跳转 → 检查向量表有效性

[Step 2] 应用 init 到哪一步了？
   读 rtt_dbg_hal_run_called：看 run() 是否进入
   读 rtt_dbg_setup_stage：看 setup() 进度
   读 rtt_dbg_main_loop_iterations：看主循环是否跑过

[Step 3] CTOR 完成了吗？
   读 rtt_dbg_ctor_phase：4=完成
   读 rtt_dbg_ctor_index / rtt_dbg_ctor_total

[Step 4] RT-Thread 组件事先到哪了？
   读 rtt_sd_mount_stage / rtt_sd_mount_result
   读其他组件级 INIT_XXX_EXPORT 变量

[Step 5] 检查线程状态
   读 rt_tick 看系统时间
   找 main_thread TCB 检查其 state/status
```

### 14.2 rtt_dbg 变量速查表

```bash
# 先用 arm-none-eabi-nm 找实际地址（重点：每次编译地址可能变化！）
arm-none-eabi-nm build/...rt-thread.elf | grep rtt_dbg

# 或直接查：
arm-none-eabi-nm ... | grep -E "rtt_dbg|rtt_sd_mount|ctor_phase"
```

**魔法值流程**（`HAL_RTT_Class.cpp`）：

| 值 | 含义 | 设置时机 |
|------|------|------|
| `0xDEADBEEF` | run() 未进入（初始值） | 静态初始化 |
| `0xAAAAAAAA` | run() 刚进入 | `run()` 第1行 |
| `0x11111111` | setup() 完成 | setup 返回后 |
| `0xBBBBBBBB` | run() 完成（进入loop） | run() 返回前 |

**ctor_phase 值**（`syscalls.c`）：

| 值 | 含义 |
|----|------|
| 0 | 初始值（未开始构造函数） |
| 1 | 即将运行构造函数 |
| 2 | 正在运行构造函数 i（before） |
| 3 | 构造函数 i 返回后 |
| 4 | 全部构造函数完成 ✅ |

### 14.3 标准诊断脚本

```python
# 连接到 OpenOCD，读全部 rtt_dbg 变量
import socket, time, subprocess

nm_result = subprocess.run([
    'arm-none-eabi-nm', 'build/rtt_deploy/cuav_v5/rt-thread.elf'
], capture_output=True, text=True)

# 解析 nm 输出获取正确地址
symbols = {}
for line in nm_result.stdout.split('\n'):
    parts = line.strip().split()
    if len(parts) >= 3:
        addr = int(parts[0], 16)
        symbols[parts[2]] = addr

# 关心的变量
watch = ['rtt_dbg_hal_run_called', 'rtt_dbg_setup_stage',
         'rtt_dbg_main_loop_entry_called', 'rtt_dbg_main_loop_iterations',
         'rtt_dbg_ctor_phase', 'rtt_dbg_ctor_index', 'rtt_dbg_ctor_total',
         'rtt_sd_mount_stage', 'rtt_sd_mount_result', 'rt_tick']

s = socket.socket()
s.settimeout(5)
s.connect(('localhost', 4444))
s.recv(4096)
s.sendall(b'halt\n')
time.sleep(0.3)
s.recv(4096)

for name in watch:
    if name not in symbols: continue
    addr = symbols[name]
    s.sendall(f'mdw {hex(addr)}\n'.encode())
    time.sleep(0.2)
    resp = s.recv(4096).decode('latin-1', errors='replace')
    # 从 OpenOCD 响应中提取值
    import re
    m = re.search(rf'{hex(addr)[2:]}:\s+(0x[0-9a-f]+)', resp)
    if m:
        print(f'{name:40s} = {m.group(1)} ({int(m.group(1), 16)})')
s.close()
```

### 14.4 常见诊断结果解读

| 诊断结果 | 推断 | 下一步 |
|---------|------|--------|
| `hal_run_called=0xDEADBEEF`, `ctor_phase=4`, sd_mount=10 | init 完成但 `main()` 未执行 | 检查 main_thread TCB 状态、是否有高优先级线程抢占 |
| `hal_run_called=0xAAAAAAAA`, `setup_stage=0` | `run()` 进入但 setup 未推进 | `run()` 中在 setup 前有阻塞操作 |
| `hal_run_called=0xAAAAAAAA`, `setup_stage≥600` | setup 正常推进，但主循环未启动 | 检查 setup 后 run() 的 loop 入口条件 |
| `hal_run_called=0x11111111` | setup 完成 + loop 开始 | 检查 UART/CDC 初始化 |

### 14.5 BIOS 症状 vs APP 症状鉴别

遇到 USB CDC 无数据时，先区分这是 bootloader 的 CDC 还是 app 的 CDC：

```bash
# 方法 1：检查 USB 描述符（lsusb -v）
lsusb -d 0483:3748 -v 2>/dev/null | grep -i "iProduct\|iSerial"
# Bootloader CDC: 产品字符串 short
# App CDC: "ArduPilot" 或产品全名

# 方法 2：OpenOCD halt 后读 PC
# bootloader 区: PC < 0x08008000
# app 区: PC >= 0x08008000

# 方法 3：发送串口数据看响应
# Bootloader 的 serial update 协议有 ACK
# App 的 MAVLink 有心跳帧头 0xFD
```

## 十五、参考文件索引

| 文件 | 内容 | 用途 |
|------|------|------|
| `references/chibios-sdu-architecture.md` | ChibiOS SDU 完整源码分析 | CDC TX 修复时参考 |
| `references/rtt-cdc-tx-debug-methodology.md` | CDC TX 数据流分层调试方法论 | 追踪 CDC 无数据根因 |
| `references/system-bringup-diagnostics.md` | 空转（idle thread）时系统启动诊断方法论 | boot 成功后 stuck in idle 排查 |
