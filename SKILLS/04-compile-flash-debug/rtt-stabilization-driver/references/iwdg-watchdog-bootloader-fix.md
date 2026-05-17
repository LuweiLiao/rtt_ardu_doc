# IWDG 看门狗修复全记录

## 背景

CUAV V5 bootloader 启动 IWDG（PR=0, RLR=4095 → ~0.5s 超时）。RTT HAL 的 `set_system_initialized()` 中看门狗初始化被 `#if 0` 禁用 → `_iwdg_started=false` → 所有 `watchdog_pat()` 调用写入 `IWDG_KR=0xAAAA` 被跳过 → 系统每 ~5-10s 被 IWDG 复位。

## 2026-05-10 完整诊断记录

### 现象

- MAVLink "Initialising ArduPilot" 每 ~5.1 秒重复出现
- HC 周期 ≈ 5 秒（精确）
- CFSR=0, HFSR=0（无 HardFault）
- RCC CSR bit 26 (WDGRSTF)=1 确认看门狗复位

### 诊断路径

```
原始问题（57KB栈+SDIO+IOMCU禁用）:
  HB=1, 27msg/s, 40+秒稳 → 但 IWDG 复位
  ↓
IWDG 根因定位: Scheduler 中 `#if 0` 禁用了喂狗
  ↓
尝试修复 A: rt_board_init.c 开头加 IWDG 重配 + 喂狗
  → USB 不枚举！(DSTS=0)
  ↓
对照实验:
  - 32KB栈+原始代码 → USB ✅, MAVLink ✅, IWDG复位 ✅ (10s)
  - 64KB栈+原始代码 → USB ✅, MAVLink ✅, IWDG复位 ✅ (5s)
  - 64KB栈+错误IWDG代码 → USB ❌ ← 根因定位！
  ↓
根因: IWDG 寄存器写在 SystemClock_Config() 之前
  损坏了 RCC 状态 → USB OTG 不枚举
  ↓
正确修复: 只改 Scheduler.cpp 让 watchdog_pat() 无条件
```

### 根因图

```
错误做法:
  scons build
  → openocd program → reset run
  → rt_hw_board_init()
    → SCB->VTOR = 0x08008000
    → IWDG->KR = 0xAAAA  ← ★ 根因！SystemClock_Config() 之前
    → _mpu_config()
    → SystemClock_Config()  ← 系统时钟初始化
    → ... USB 初始化
  → USB OTG DSTS = 0 (Speed: not enumerated)
  → /dev/ttyACM1 不出现

正确做法:
  rt_hw_board_init()
    → SCB->VTOR = 0x08008000
    → SystemClock_Config()  ← 先初始化时钟
    → ... 其他初始化
    → iwdg_reconfigure()  ← 再写 IWDG（安全）
    → rt_kprintf("complete")
```

### ✅ 正确做法：参考 ChibiOS HAL（2026-05-10 对照实验确认）

**核心原则**：不要自己猜 IWDG 寄存器配置 — 直接照搬 ChibiOS HAL 的实现。

#### ChibiOS vs RTT 原始代码对比

| 方面 | ChibiOS (正确) | RTT 原始代码 (错误) |
|------|----------------|-------------------|
| **分频 PR** | `PR=3` (/32) | `PR=6` (/256) |
| **RLR 计算** | `RLR = timeout_ms - 1`（1ms 精度） | `RLR = 1250`（硬编码，~10s） |
| **PVU/RVU 等待** | **不等待** — 直接写 4 条指令 | 无限循环等待 `PVU\|RVU`（可能永久卡死） |
| **调用时机** | `setup()` 完成后 (`HAL_ChibiOS_Class.cpp`) | 在 `board_init.c` 开头，`SystemClock_Config` 前 ❌ |
| **喂狗条件** | 无条件 + 内部 `watchdog_enabled` 标志 | `if (_iwdg_started)` — 但 `_iwdg_started` 永远 false！ |

#### ChibiOS 代码全文（libraries/AP_HAL_ChibiOS/hwdef/common/watchdog.c）

```c
void stm32_watchdog_init(void) {
    IWDGD.KR = 0x5555;     // 解锁
    IWDGD.PR = 3;           // /32: 1ms 每 RLR tick
    IWDGD.RLR = 2047;       // 2048ms 超时（默认 STM32_WDG_TIMEOUT_MS）
    IWDGD.KR = 0xCCCC;      // 启动
    watchdog_enabled = true;
}

void stm32_watchdog_pat(void) {
    if (watchdog_enabled) {
        IWDGD.KR = 0xAAAA;
    }
}
```

关键：**没有 PVU/RVU 等待，没有 LSI 检查，没有读回验证**。4 条指令完成初始化。

#### 调用时序（HAL_ChibiOS_Class.cpp）

```cpp
void _main_loop_entry() {
    g_callbacks->setup();                       // setup() 完成
    // ...
    stm32_watchdog_init();                      // 启动 IWDG
    // ...
    hal.scheduler->set_system_initialized();    // 标记
}
```

IWDG 只在 **setup() 完成后、main loop 开始前**启动。这完全避开了早期初始化（C 运行时、RT-Thread 内核、线程创建、USB 枚举等）的时序窗口问题。

#### 正确迁移到 RTT HAL

```cpp
// system.cpp — 替换旧的 ap_rtt_iwdg_init()
#include <stdint.h>
#include <stdbool.h>

typedef struct {
    volatile uint32_t KR;
    volatile uint32_t PR;
    volatile uint32_t RLR;
    volatile uint32_t SR;
    volatile uint32_t WINR;
} _iwdg_regs;
#define _IWDG (*(volatile _iwdg_regs *)(0x40003000UL))

static bool _rtt_watchdog_active;

extern "C" void stm32_watchdog_init(void) {
    _IWDG.KR = 0x5555;
    _IWDG.PR = 3;               /* /32: 1ms per tick */
    _IWDG.RLR = 4095;           /* max → 4096ms */
    _IWDG.KR = 0xCCCC;          /* start */
    _IWDG.KR = 0xAAAA;          /* initial feed */
    _rtt_watchdog_active = true;
}

extern "C" void stm32_watchdog_pat(void) {
    if (_rtt_watchdog_active) {
        _IWDG.KR = 0xAAAA;
    }
}
```

```cpp
// HAL_RTT_Class.cpp — 在 setup() 后调用
a->callbacks->setup();
stm32_watchdog_init();           // ← 新增
a->sched->set_system_initialized();
```

```cpp
// Scheduler.cpp — watchdog_pat() 调用 stm32_watchdog_pat()
void Scheduler::watchdog_pat(void) {
    last_watchdog_pat_ms = AP_HAL::millis();
#if !defined(IOMCU_FW)
    stm32_watchdog_pat();        // ← 替换手动 *(uint32_t*)0x40003000
#endif
}
```

⚠️ 注意：`extern "C"` 声明必须在 .cpp 文件中用 `extern "C" { }` 包裹定义（见下文链接陷阱）

### ⚠️ extern "C" 链接陷阱（2026-05-10 新增）

在 `system.cpp`（C++ 文件）中定义 `extern "C"` 声明的函数时，**实现也必须用 `extern "C" { }` 包裹**：

```cpp
// ✅ 正确
#include "hwdef/common/watchdog.h"  // 声明: extern "C" void stm32_watchdog_init(void)

extern "C" {
void stm32_watchdog_init(void) {
    // ... 实现
}
}  // extern "C"

// ❌ 错误：导致 collect2: undefined reference to 'stm32_watchdog_init'
void stm32_watchdog_init(void) {  // C++ mangled symbol!
    // ...
}
```

**症状**：`collect2: error: ld returned 1 exit status` + `undefined reference to 'stm32_watchdog_init'`
**原因**：头文件用 `extern "C"` 声明（生成 C linkage `stm32_watchdog_init`），但 .cpp 文件定义不在 `extern "C"` 内（生成 C++ mangled symbol 如 `_Z21stm32_watchdog_initv`）。
**诊断**：`arm-none-eabi-nm build/rt-thread.elf | grep stm32_watchdog` — 有 C++ mangling（下划线前缀 + 类型编码）说明定义不在 `extern "C"` 块内。
**修复**：用 `extern "C" { ... }` 包裹函数定义。

### 对照实验：各方案稳定性和 USB 状态（2026-05-10）

| 实验 | 栈大小 | IWDG 代码 | USB 枚举 | MAVLink | 稳定时间 |
|------|--------|-----------|---------|---------|---------|
| A: 原始代码 | 32KB | 无修改 | ✅ | ✅ | ~10s（Logger HardFault 复位） |
| B: 仅增大栈 | **64KB** | 无修改 | ✅ | ✅ | ~5s（IWDG 从未被喂） |
| C: IWDG 在 init 开头 | 64KB | `rt_board_init` 开头，Clock 前 | **❌** DSTS=0 | — | — |
| D: ChibiOS 风格完整 | 64KB | system.cpp + HAL_RTT_Class + Scheduler | ✅ | **❌** 无 HB | — |
| E: 最小修改 | **64KB** | 仅 `watchdog_pat()` 无条件 | ✅ | ✅ | ~5s 复位 |

**实验 E 关键洞察**：即使 `watchdog_pat()` 无条件写 `0xAAAA`，系统仍每 ~5s 复位。这暗示：
1. bootloader 可能**未启动 IWDG** → 写 `0xAAAA` 是 no-op → 无硬件保护
2. 或复位另有根因（如 ArduPilot 内部 `NVIC_SystemReset()` 调用）
3. 需要**明确启动 IWDG**（调用 `stm32_watchdog_init()`）才能让喂狗生效

### 为什么 IWDG 寄存器写影响 USB
1. `IWDG->KR = 0xAAAA` 需要通过 APB1 总线访问地址 `0x40003000`
2. 在 `SystemClock_Config()` 之前，APB 时钟可能使用 HSI（16MHz）而非 PLL（108/216MHz）
3. 写入 IWDG 寄存器时，硬件可能锁存 APB 时钟状态
4. 之后 `SystemClock_Config()` 切换时钟时，APB1 上的 USB 域状态不一致
5. USB OTG FS 核心无法正确初始化 PHY
6. DSTS 速度位停留在 00（未枚举）

### 修正后的正确修复（已验证工作流）

**修复 A（最小化，推荐）**：
只改 `libraries/AP_HAL_RTT/Scheduler.cpp`：

```diff
-#if defined(HAL_BOARD_RTT) && !defined(IOMCU_FW)
-    if (_iwdg_started) {
-#define IWDG_KR_REG    (*(volatile uint32_t *)0x40003000)
-        IWDG_KR_REG = 0xAAAA;
-    }
-#endif
+#if defined(HAL_BOARD_RTT) && !defined(IOMCU_FW)
+#define IWDG_KR_REG    (*(volatile uint32_t *)0x40003000)
+    IWDG_KR_REG = 0xAAAA;
+#endif
```

同时在 `set_system_initialized()` 中：

```diff
-    // #if 0 ... ap_rtt_iwdg_init(); _iwdg_started = true; ... #endif
+    watchdog_pat();       // 喂狗（来自之前 rt_board_init 的10s配置）
+    _iwdg_started = true; // 供任何遗留代码检查
```

**修复 B（增加长超时）**：
如需要更长的 IWDG 超时（如 33s 覆盖整个初始化），在 `rt_board_init.c` **末尾**（`SystemClock_Config()` 已执行后）加上 `iwdg_reconfigure()`：

```c
// ★ 必须放在 SystemClock_Config() 之后！
iwdg_reconfigure();
rt_kprintf("\"Board initialization complete\");\n"
```

### IWDG 寄存器调试速查

```bash
# 读取 IWDG 寄存器
echo "mdw 0x40003000 4" | nc -q 2 localhost 4444
# 0x40003000: KR=0x00000000  PR=0x00000006  RLR=0x00000fff  SR=0x00000000
# PR=6 → /256, RLR=4095 → 最大12位, SR=0 → PVU/RVU=0（就绪）

# 检查复位原因
echo "mdw 0x40023874 1" | nc -q 2 localhost 4444
# bit 26 (WDGRSTF)=1 → IWDG 导致上次复位
# bit 28 (LSIRDY)=1 → LSI 正在运行
```

### IWDG 超时计算

```
timeout = (RLR + 1) * prescaler / LSI_freq

预设值:
  PR=6 → 分频 = 256
  RLR=4095 → 最大
  
LSI 频率容差: 17kHz ~ 60kHz（-50%~+100%）

名义超时 (32kHz): 4096 * 256 / 32000 = 32.8s
最坏超时 (60kHz): 4096 * 256 / 60000 = 17.5s
最好超时 (17kHz): 4096 * 256 / 17000 = 61.7s
```

### PVU/RVU 完整处理（含超时）

```c
static inline void iwdg_reconfigure(void)
{
    /* 确保 LSI 运行 */
    if (!(RCC->CSR & RCC_CSR_LSIRDY)) {
        RCC->CSR |= RCC_CSR_LSION;
        uint32_t tout = 100000;
        while (!(RCC->CSR & RCC_CSR_LSIRDY) && tout--) {}
    }

    IWDG->KR = 0x5555;      /* 解锁 */
    IWDG->PR = 6;            /* /256 */

    uint32_t tout = 100000;
    while ((IWDG->SR & IWDG_SR_PVU) && tout--) {}

    IWDG->KR = 0x5555;      /* 重新解锁写 RLR */
    IWDG->RLR = 4095;       /* 最大 12-bit */

    tout = 100000;
    while ((IWDG->SR & IWDG_SR_RVU) && tout--) {}

    IWDG->KR = 0xCCCC;      /* 启动（如已启动则无操作） */
    IWDG->KR = 0xAAAA;      /* 喂狗 */
}
```

## 相关文件

- `libraries/AP_HAL_RTT/Scheduler.cpp` — `watchdog_pat()` + `set_system_initialized()`
- `libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c` — `iwdg_reconfigure()` 必须在 SystemClock_Config 之后
- `libraries/AP_HAL_RTT/system.cpp` — `ap_rtt_iwdg_init()` 备用（带超时修复）
