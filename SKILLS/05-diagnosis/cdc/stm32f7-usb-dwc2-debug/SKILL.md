---
name: stm32f7-usb-dwc2-debug
description: STM32F7 USB OTG DWC2 debugging — clock domains, RCC reset, CSRST deadlock diagnosis
---

# STM32F7 USB OTG DWC2 调试指南

## ✅ 最终解决方案（2026-04-18 验证）

**热复位死锁的根因和修复**：`usb_glue_st.c` 的 `usb_dc_low_level_init()` 是实际链接的版本（board override 被 linker 忽略），它只调用了 `HAL_PCD_MspInit()`（GPIO/时钟/NVIC），**没有做 RCC force-reset**。热复位后 DWC2 AHB 总线 stuck（AHBIDL=0），导致 USB 永久死锁。

**修复**：在 `HAL_PCD_MspInit()` 之后插入 `__HAL_RCC_USB_OTG_FS_FORCE_RESET()` + AHBIDL 等待：

```c
// usb_glue_st.c → usb_dc_low_level_init()
HAL_PCD_MspInit(hpcd);

/* 热复位恢复：RCC force-reset 清除 DWC2 AHB bus 残留状态 */
__HAL_RCC_USB_OTG_FS_FORCE_RESET();
rtt_hw_us_delay(1000U);  /* 1ms */
__HAL_RCC_USB_OTG_FS_RELEASE_RESET();
rtt_hw_us_delay(1000U);

/* 等待 AHB idle */
volatile uint32_t _timeout = 100000U;
while (((*(volatile uint32_t *)0x5000000CU) & (1UL << 31)) == 0U) {
    if (--_timeout == 0U) break;
}
```

**冷启动时 AHBIDL=1 所以无害（仅增加 ~1ms）。ROM 变化 -0.11%，RAM 无变化。**

### ⚡ 2026-05-10 状态确认：修复已在编译产物中

当前 `staging/pogo-rtt` 分支的子模块已包含此修复：
- 文件：`modules/rt-thread/components/drivers/usb/cherryusb/port/dwc2/usb_glue_st.c`
- 函数：`usb_dc_low_level_init()` 第 650-666 行
- 编译产物：`arm-none-eabi-nm` 确认 `usb_dc_low_level_init @ 0x080ee2f0` 包含 RCC force-reset 代码

**如果 USB 仍然死锁，不要重新排查 DWC2 修复本身 — 先检查其他原因**（如 setup 未完成导致 USB 驱动从未被调用）。

**验证结果**：
- 冷启动：✅ MAVLink 正常（237 msgs, 16 types）
- 热复位 ×3：✅ 全部正常（CFSR=0, HFSR=0, VTOR=0x08008000）

### 关键教训

1. **Board override via strong symbol 不一定生效** — linker 可能选择 `usb_glue_st.c` 版本（即使 board override 的 `.o` 也被编译了）。用 `arm-none-eabi-nm` 确认实际链接的符号地址。本案例通过反汇编 `usb_dc_low_level_init`（0x080ec8f8，仅 68 字节）确认是 `usb_glue_st.c` 版本。

2. **热复位测试等待时间 ≥45 秒** — `mon reset init` 后 bootloader（0x08000200）等 5-10 秒才 jump_to_app。25-30 秒可能不够。之前 "5/5 失败" 是假阴性：halt 时 PC 在 bootloader 中，ttyACM 是 bootloader 阶段的残留枚举。

3. **复杂恢复机制不必要** — 之前尝试的 clock power cycle、PCGCCTL.STOPCLK 条件门控、dwc2_reset() 内部分级恢复等都过度工程化了。RCC force-reset 在 `usb_dc_low_level_init()` 入口处就足够，因为它在 `HAL_PCD_MspInit()` 重新配置时钟之后执行。

4. **`usb_glue_st.c` 在 git submodule 中** — 修改在磁盘上但不会被 `git add` 提交到主仓库。需要 `git submodule` 操作或手动提交 submodule 变更。

---

## 关键陷阱：AHB2 vs AHB1

**USB OTG FS 在 STM32F7 上挂在 AHB2 总线，不是 AHB1！**

| 操作 | 寄存器 | 地址 | Bit |
|------|--------|------|-----|
| 时钟使能 | `RCC->AHB2ENR` (OTGFSEN) | 0x40023820 | bit7 |
| 时钟禁用 | `RCC->AHB2ENR &= ~OTGFSEN` | 0x40023820 | bit7 |
| 外设复位 | `RCC->AHB2RSTR` (OTGFSRST) | 0x40023830 | bit7 |
| HAL 宏 | `__HAL_RCC_USB_OTG_FS_CLK_ENABLE()` 等 | — | — |

**如果操作 AHB1ENR bit29，reset 是静默空操作 — 不会报错但完全无效！**

## USB OTG FS 寄存器地址（DWC2, base=0x50000000）

| 偏移 | 寄存器 | 用途 |
|------|--------|------|
| 0x000 | GOTGINT | OTG 中断 |
| 0x004 | GAHBCFG | AHB 配置（bit0=GINT 全局中断） |
| 0x008 | GUSBCFG | USB 配置（bit6=PHYSEL） |
| 0x00C | **GRSTCTL** | **复位控制**（bit30=CSRST, bit10=AHBIDL） |
| 0x010 | GINTSTS | 中断状态 |
| 0x014 | GINTMSK | 中断掩码 |
| 0x040 | GOTGCTL | OTG 控制 |
| 0x04C | GSNPSID | 版本 ID（0x4F54420A=ST DWC2） |
| 0x800 | DCTL | 设备控制（bit1=SDIS 软断开） |
| 0xE00 | PCGCCTL | PHY 时钟门控 |

## DWC2 CSRST 死锁特征

- **GRSTCTL=0x40001847 / 0x40001447**: CSRST=1(bit30+bit0), AHBIDL=0(bit11) — 死锁
- 热复位后进入 `cherryusb_cdc_init` 时常见此状态（上一次运行的残留）
- CSRST=1 时，GDB `set *(uint32_t*)addr` 写入**可能不生效**（SWD 行为异常）
- 但 CPU 运行时代码写入 GRSTCTL **是有效的**

### 恢复方法（经验证状态）

1. **❌ 写 GRSTCTL=0 — AHBIDL=0 时单独写入无效**（AHB bus 死锁，写操作丢失）
2. **❌ RCC AHB2RSTR reset 无法清除 CSRST 死锁**（已用 monitor mww 反复验证）
3. **❌ Clock power cycle（关时钟→RCC reset→重开）— 在 cherryusb.c 中做无效**（被 HAL_PCD_MspInit 覆盖）
4. **❌ PCGCCTL PHY gate 无效**（CSRST=1 时 PCGCCTL 写入不生效）
5. **✅ 物理断电能恢复**，但飞控调试中禁止
6. **✅ 方案E：在 dwc2_reset() 内部做分级恢复**（2026-04-17 验证 5/5 成功！）

**✅ 方案E — 已验证有效（2026-04-17）**：
在 `dwc2_reset()` 内部（usb_dc_dwc2.c），当检测到 AHBIDL=0 时：
1. GRSTCTL=0 清除残留 reset bits → 等 100ms AHBIDL
2. 如果仍卡死 → RCC clock power cycle（AHB2 直接寄存器操作，不依赖 HAL）→ 200ms 关断 + 50ms 恢复
3. 如果还卡死 → 重新 SDIS + GRSTCTL=0 + 再等
4. 全部失败 → 返回 -1（CONFIG_USB_ASSERT_DISABLE 防止死循环）

**时序关键**：恢复逻辑在 `HAL_PCD_MspInit` + SDIS 50ms 之后执行（usb_dc_init 调用链保证），
不会被后续 init 覆盖。之前 cherryusb.c 中的 recovery 失败就是因为被 cdc_acm_chardev_init 内部
的 HAL_PCD_MspInit 覆盖。

## GDB 非侵入式诊断命令

### ⚠️ 进程管理关键：用 setsid 隔离 OpenOCD

**不要直接 `openocd ... &` 然后在同一 shell 里跑 GDB！**
如果后续 `pkill -9 -f openocd` 会杀掉整个进程组（包括当前 shell 和 GDB）。
**必须用 `setsid` + `disown` 隔离 OpenOCD 进程组**：

```bash
# ✅ 正确：setsid 隔离，disown 脱离 shell
setsid openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c "init" \
  </dev/null >/tmp/ocd.log 2>&1 &
disown
sleep 4
pgrep -a openocd  # 确认在运行

# 然后用 GDB 读取寄存器（GDB 自带 halt，不需要 mon halt）
/opt/gcc-arm-none-eabi-10-2020-q4-major/bin/arm-none-eabi-gdb -batch -q \
  -ex "target remote :3333" \
  -ex "x/1xw 0x5000000C" \
  -ex "x/1xw 0x50000010" \
  -ex "x/1xw 0x50000014" \
  -ex "x/1xw 0x50000004" \
  -ex "x/1xw 0x50000800" \
  -ex "x/1xw 0x40023820" \
  -ex "x/1xw 0x5000004C" \
  -ex "monitor resume" \
  2>&1

# 用完后杀掉
pkill -9 -f openocd
```

### 寄存器速查表

| 地址 | 寄存器 | 含义 |
|------|--------|------|
| 0x5000000C | GRSTCTL | bit30=CSRST, bit31=AHBIDL, 死锁=0x40001847 |
| 0x50000010 | GINTSTS | 中断状态 |
| 0x50000014 | GINTMSK | 中断掩码 |
| 0x50000004 | GAHBCFG | bit0=GINT 全局中断 |
| 0x50000800 | DCTL | bit1=SDIS |
| 0x40023820 | RCC_AHB2ENR | bit7=OTGFSEN（时钟使能） |
| 0x40023830 | RCC_AHB2RSTR | bit7=OTGFSRST |
| 0x5000004C | GSNPSID | 0x4F54420A=正常, 0x0200d1e8=AHB不通 |

### ⚠️ OpenOCD mdw/mdb 输出问题

`openocd -c "init; halt; mdw 0x5000000C 1; resume; exit"` 的 mdw 输出
**不会显示在终端**（被 OpenOCD 内部日志系统吞掉）。必须用 GDB 的 `x/1xw` 来读取。

### ❌ 不要用的模式

```bash
# ❌ GDB halt 前的 mon halt — 不需要，GDB connect 自动 halt
# ❌ openocd -c "init; halt; mdw ..." — mdw 输出不可见
# ❌ openocd ... & 后不加 setsid/disown — pkill 会杀掉整个 shell
# ❌ 管道重定向 openocd stdout — 可能导致输出丢失
```

### ⚠️ GDB `set` vs `monitor mww` — 关键区别

- **`set *(uint32_t*)addr = val`** — GDB 通过 SWD 写入，CPU halt 时对 RCC 等系统寄存器**可能不生效**
- **`monitor mww addr val`** — OpenOCD 直接操作 AHB/APB 总线，**更可靠**，对 RCC 和 DWC2 寄存器都有效
- **读取**：两者都可靠（`x/1xw` 或 `monitor mdw`）
- **最佳实践**：写 RCC/DWC2 寄存器时始终用 `monitor mww`

```bash
# ✅ 正确：用 monitor mww 写入
arm-none-eabi-gdb -batch \
  -ex "target remote :3333" \
  -ex "mon halt" \
  -ex "mon mww 0x5000000C 0x00000000" \  # 清除 CSRST 死锁
  -ex "monitor sleep 1" \
  -ex "mon mdw 0x5000000C 1" \            # 验证
  -ex "mon resume"

# ❌ 可能失败：GDB set 写入（halt 时 SWD 行为不确定）
arm-none-eabi-gdb -batch \
  -ex "set *(uint32_t*)0x40023820 = 0x80" \  # 可能不生效！
```

## usb_dc_init 关键时序

```
1. memset g_dwc2_udc        — 清零内部状态
2. usb_dc_low_level_init    — HAL_PCD_MspInit（enable AHB2 时钟, GPIO, NVIC）
3. 读 CID/GSNPSID/GHWCFG    — 需要 AHB2 时钟
4. GAHBCFG &= ~GINT         — 禁用全局中断
5. DCTL |= SDIS             — 软断开（D+ pull-down）
6. ⚠️ 50ms 延迟             — 让 USB 主机检测断开并停止发包
7. dwc2_core_init → dwc2_reset — CSRST（需要 AHB 总线 idle）
8. dwc2_set_mode            — 强制设备模式
9. FIFO/endpoint 配置
10. GAHBCFG |= GINT         — 启用全局中断
```

**步骤 5→6 的延迟至关重要**：如果 SDIS 后不等待，USB 主机仍在发包，PHY 仍在接收，
AHB 总线不会 idle，CSRST 无法完成 → 永久死锁。

## cherryusb.c 热复位模板（当前最佳实践 2026-04-16）

```c
// 关键：先开时钟再读任何 DWC2 寄存器！
__HAL_RCC_USB_OTG_FS_CLK_ENABLE();
__DSB();
rtt_hw_us_delay(10000U);  // 10ms

// 先做 RCC reset 确保基础状态干净
__HAL_RCC_USB_OTG_FS_FORCE_RESET();
rtt_hw_us_delay(1000U);
__HAL_RCC_USB_OTG_FS_RELEASE_RESET();
__HAL_RCC_USB_OTG_FS_CLK_ENABLE();
rtt_hw_us_delay(50000U);  // 50ms

// 读取状态（此时时钟已开，寄存器可读）
uint32_t grstctl = *(volatile uint32_t *)0x5000000CU;
uint32_t gsnpsid = *(volatile uint32_t *)0x5000004C;
bool stuck = (grstctl & 0x7) != 0;       // CSRST|HSRST|FCRST
bool ahb_idle = (grstctl & (1UL<<31)) != 0;
bool gsnpsid_ok = (gsnpsid == 0x4F54420A);

if (stuck || !ahb_idle || !gsnpsid_ok) {
    // Recovery: SDIS + clock power cycle
    *(volatile uint32_t *)0x50000800U |= (1UL << 1);  // SDIS
    rtt_hw_us_delay(5000U);
    __HAL_RCC_USB_OTG_FS_CLK_DISABLE();
    __HAL_RCC_USB_OTG_FS_FORCE_RESET();
    rtt_hw_us_delay(100000U);  // 100ms
    __HAL_RCC_USB_OTG_FS_RELEASE_RESET();
    __HAL_RCC_USB_OTG_FS_CLK_ENABLE();
    rtt_hw_us_delay(50000U);
    // 清除 SDIS
    *(volatile uint32_t *)0x50000800U &= ~(1UL << 1);
}

// 注意：cdc_acm_chardev_init 内部的 HAL_PCD_MspInit 可能重新配置时钟
// 更好的方案是将 recovery 移入 dwc2_reset() 内部（待实现）
cdc_acm_chardev_init(0, USB_OTG_FS_PERIPH_BASE);
```

## dwc2_reset() 热复位修复（关键 — 需要更新）

```c
static inline int dwc2_reset(uint8_t busid)
{
    /*
     * 热复位修复：检测 AHBIDL=0 时先写 GRSTCTL=0 清除残留 CSRST 死锁。
     * 上一次运行可能留下 CSRST=1+AHBIDL=0，AHB 永远不会 idle。
     *
     * ⚠️ 2026-04-16 更新：AHBIDL=0 时 GRSTCTL=0 写入可能不生效（AHB bus 死锁）。
     * 如果写入无效，此处的 fallback 不够 — 需要在更上层做 clock power cycle。
     * 最佳方案是将 clock cycle 移入此函数内部。
     */
    if ((USB_OTG_GLB->GRSTCTL & USB_OTG_GRSTCTL_AHBIDL) == 0U) {
        USB_OTG_GLB->GRSTCTL = 0x00000000U;
        for (count = 0; count < 500000U; count++) { asm volatile("nop"); }
    }

    /* 正常流程：等待 AHBIDL → 设置 CSRST → 等待完成 */
    // ...
}
```

**注意**：
- 仅写 GRSTCTL=0 + NOP 延迟**不足以**让 AHBIDL 恢复为 1（AHB 死锁时写入无效）
- 必须先 SDIS 断开 PHY 或做 clock power cycle
- **推荐**：将 clock power cycle 逻辑移入 `dwc2_reset()` 内部（在 `HAL_PCD_MspInit` 之后执行），
  这样时序完全正确

## 2026-04-17 更新：GRSTCTL=0 清除验证 + SDIS 前置要求

### 关键发现：GRSTCTL=0 清除 CSRST 但 AHBIDL 仍为 0

通过 `monitor mww 0x5000000C 0` 验证：
- 写入前 GRSTCTL=0x40001847 (CSRST=1+AHBIDL=0)
- 写入后 GRSTCTL=0x00000040 (只保留 FRMCNT, **AHBIDL 仍为 0**)
- 写 GRSTCTL=1 后变为 0x00000041 (重新设置 CSRST)
- **结论**：GRSTCTL=0 能清除 CSRST/HSRST/FCRST，但 AHB master 仍然 busy！

### 根因：PHY 仍在接收数据导致 AHB 不 idle

GRSTCTL=0 清除死锁后 AHBIDL (bit31) 仍为 0，是因为 PHY 仍在接收 USB 主机的数据包，
AHB master 持续有 DMA 活动。**必须先 SDIS (soft disconnect) 断开 PHY**。

### 正确的热复位恢复流程

```
1. __HAL_RCC_USB_OTG_FS_CLK_ENABLE()   // enable AHB2 时钟
2. DCTL |= SDIS (0x50000800 bit1)       // 软断开 PHY
3. usbd_dwc2_delay_ms(100)              // 等 PHY 停止接收
4. GRSTCTL = 0                          // 清除残留死锁
5. 等待 AHBIDL=1 (bit31)               // 此时 PHY 已断开，AHB 应能 idle
6. GRSTCTL |= CSRST (写 1)             // 触发正常 reset
7. 等 CSRST 自清除 (bit30→0)
8. 然后调 cdc_acm_chardev_init()
```

### ⚠️ USB_ASSERT_MSG 致命陷阱

**CONFIG_USB_ASSERT_DISABLE 未定义时，USB_ASSERT_MSG 是 while(1) 死循环！**

故障链路：dwc2_core_init 失败 → 继续执行 → USB_ASSERT_MSG(GRXFSIZ >= rx_fifo_size)
→ GRXFSIZ=0（DWC2 未初始化）→ assert 触发 → while(1) → USB init 线程永久卡死。
main_loop 在另一线程所以 app 看起来仍在运行，但 USB CDC 永远不枚举。

**保底措施**：在 usb_dc_dwc2.c 顶部 `#define CONFIG_USB_ASSERT_DISABLE`，或在
cherryusb.c 中确保 dwc2_reset 不会失败后再继续。

### ⚠️ cron 任务自动化测试的假阳性问题

cron 任务自主测试报告"5/5 通过"，但手动验证发现热复位仍失败。
原因：测试脚本可能在错误的设备上连接（ttyACM0=CH340 vs ttyACM1=STM32），
或在 bootloader 5s 等待窗口内连接到了上一 session 的残留端口。
**教训**：自动化测试结果必须手动交叉验证，尤其 USB 热复位场景。

## 2026-04-16 深度更新：AHB 总线死锁的真正根因 + Clock Power Cycle 无效

### 🔴 核心发现：AHB master 死锁时写操作不可靠

之前的假设"GRSTCTL=0 写入可以清除死锁位"是**错误的**。实测证明：

**当 AHBIDL=0（AHB master 死锁）时，通过 AHB 总线对 DWC2 的写操作不可靠或直接丢失。**

证据链（2026-04-16 实测）：
1. 热复位后 GRSTCTL=0x40001847（CSRST=1, HSRST=1, FCRST=1, AHBIDL=0）
2. 执行完整 clock power cycle（关时钟→RCC reset→100ms→开时钟→50ms）
3. **GRSTCTL 仍然是 0x40001847** — 写操作完全无效！
4. **GSNPSID=0x200d1e8**（应为 0x4F54420A）— AHB 总线完全不通
5. 固件未卡死（GDB backtrace 显示正常运行在 AP_RCProtocol 循环中）

### 🔴 Clock Power Cycle 对嵌入式 PHY 无效

STM32F765 USB OTG FS 使用**嵌入式全速 PHY**。RCC 时钟门控可能只切断了数字逻辑时钟，
**PHY 模拟部分可能由独立电源轨供电**，其内部状态机不受时钟门控影响。

需要**真正的电源断电**才能复位 PHY（物理断电可恢复，但飞控调试中禁止）。

### 🟡 USB 枚举成功但 CDC 数据不通

热复位后 Linux 能看到 USB 设备（ttyACM1 在 1.5s 内出现），但 MAVLink 不工作。
原因：DWC2 的 USB 枚举层（PHY 硬件自动处理）能工作，但**端点/FIFO 配置层因死锁状态损坏**，
导致 CDC 数据通道不通。GDB 确认固件未卡死，正常运行在 ArduPilot 主循环中。

### 🟡 cherryusb.c 中 clock power cycle 的时序陷阱

**关键教训**：`cdc_acm_chardev_init()` 内部调用链：
```
cdc_acm_chardev_init → usbd_init → usb_dc_init → usb_dc_low_level_init (HAL_PCD_MspInit)
                                                    → dwc2_core_init → dwc2_reset()
```

**`HAL_PCD_MspInit()` 可能会重新配置 GPIO 和时钟**，覆盖在 cherryusb.c 中做的 recovery。
因此 clock power cycle 应放在 `dwc2_reset()` 内部，而不是外层 cherryusb.c。

### 🟡 冷启动路径检查时机错误

在 `INIT_COMPONENT_EXPORT` 中，如果**先检查 GRSTCTL 状态再开时钟**，
会读到垃圾值（AHB2ENR=0 时寄存器不可读），导致冷启动也走入热复位 recovery 路径，
从而破坏冷启动。

**正确顺序**：先 `__HAL_RCC_USB_OTG_FS_CLK_ENABLE()` → 等 10ms → 再读 GRSTCTL/GSNPSID。

### 🟢 GSNPSID 校验是判断 DWC2 状态的最佳方法

| GSNPSID 值 | 含义 |
|-----------|------|
| 0x4F54420A | 正常 — DWC2 可通过 AHB 访问 |
| 0x200d1e8 | 异常 — AHB 总线不通，DWC2 不可访问 |
| 0x00000000 | 时钟未开启或 DWC2 完全复位 |

**建议**：在 cherryusb.c 中同时检查 `stuck bits`、`AHBIDL` 和 `GSNPSID` 三个条件。

### 🟢 动态 ttyACM 设备号

热复位后 Linux 可能分配不同的 ttyACM 编号（如 ttyACM2 而非 ttyACM1）。
自动化测试脚本应使用 `glob.glob("/dev/ttyACM*")` 匹配，排除 ttyACM0（ST-Link）。

### 下一步方向（待验证）

1. **方案E（推荐）**：将 clock power cycle 移入 `dwc2_reset()` 内部，在检测到 AHBIDL=0 时执行。
   这样时序在 `HAL_PCD_MspInit()` 之后、FIFO 配置之前，完全正确。
2. **方案F（硬件）**：通过 GPIO 控制 USB PHY 电源引脚，模拟真正断电。
3. **方案G（软件规避）**：检测 USB 故障后自动重启 USB 子系统。

## 2026-04-17 更新：PCGCCTL.STOPCLK 条件门控 + 冷启动陷阱

### 🔴 关键教训：无条件 PCGCCTL.STOPCLK 破坏冷启动

在 `dwc2_reset()` 中无条件在 CSRST 前写 `PCGCCTL=USB_OTG_PCGCCTL_STOPCLK` 会导致**冷启动也失败**：

- GDB 读到的状态：GRSTCTL=0x40001847（死锁）、AHB2ENR=0x00000000（所有 AHB2 时钟关闭）、AHB2RSTR=0x007007ff（全在复位）、GSNPSID=0x0200d1e8（异常）
- USB 设备节点 ttyACM1 出现但无数据流（枚举了但端点配置失败）

**根因**：冷启动时 PHY 状态正常，AHBIDL=1，此时门控 PHY 时钟会干扰 DWC2 正常的 CSRST 完成时序。

### ✅ 正确方案：need_phy_gate 条件标志

```c
static inline int dwc2_reset(uint8_t busid)
{
    volatile uint32_t count = 0U;
    volatile uint8_t need_phy_gate = 0U;

    // 仅在热复位（AHBIDL=0）时标记需要 PHY 门控
    if ((USB_OTG_GLB->GRSTCTL & USB_OTG_GRSTCTL_AHBIDL) == 0U) {
        need_phy_gate = 1U;
        // ... recovery logic (GRSTCTL=0, clock power cycle, etc.) ...
    }

    // 正常 AHBIDL 等待
    do { ... } while ((GRSTCTL & AHBIDL) == 0);

    // 条件 PHY 时钟门控（仅热复位）
    if (need_phy_gate) {
        USB_OTG_PCGCCTL = USB_OTG_PCGCCTL_STOPCLK;
        __DSB();
        for (i = 0; i < 500000U; i++) { asm volatile("nop"); }  // ~2ms
    }

    // CSRST
    USB_OTG_GLB->GRSTCTL |= USB_OTG_GRSTCTL_CSRST;
    // ... 等待 CSRST 完成 ...

    // 条件恢复 PHY 时钟
    if (need_phy_gate) {
        USB_OTG_PCGCCTL = 0U;
        __DSB();
    }
    return 0;
}
```

### PCGCCTL.STOPCLK 与之前结论的关系

之前验证"PCGCCTL PHY gate 在 CSRST=1 时不生效"是正确的——但那是在 CSRST **已经死锁后**才尝试门控。
新方法是在 CSRST **之前**门控 PHY 时钟，此时 AHB 总线仍可访问 PCGCCTL 寄存器，门控能生效。

## DWC2 TX FIFO 优化（2026-05-10 新增）

### 问题：STM32F7 OTG_FS 总 FIFO 仅 320 words（1280 字节）

STM32F765 的 USB_OTG_FS 外设（不是 OTG_HS）的 DWC2 FIFO RAM 是 **320 words 硬件限制**，无法增加。必须在这个预算内分配所有端点的 TX FIFO + RX FIFO。

### 端点→FIFO 索引映射

CDC ACM 使用以下端点（见 `cdc_acm_rttchardev_template.c`）：

| 端点地址 | 方向 | FIFO 索引 | 用途 |
|---------|------|-----------|------|
| EP0 | IN/OUT | [0] | 控制传输（必须保留） |
| EP1 (0x81) | **IN** | [1] | CDC 数据上传 — 瓶颈 |
| EP2 (0x02) | OUT | [2] | CDC 数据下发 |
| EP3 (0x83) | **IN** | [3] | CDC 中断状态通知 |

**关键洞察**：TX FIFO 仅用于 **IN 端点**。OUT 端点不需要分配 TX FIFO，因为 OUT 方向的数据流是反向的（RX FIFO 处理）。

### FIFO 分配公式

```
total_fifo_size = device_rx_fifo_size + sum(device_tx_fifo_size[0..15])
```

优化前分配（`usb_glue_st.c` 中 STM32F7 的 `param_pa11_pa12`）：
```c
.device_rx_fifo_size = (320 - 16 - 64 - 16 - 16),  // RX=208 words
.device_tx_fifo_size = { [0]=16, [1]=64, [2]=16, [3]=16, ... }
// EP1(CDC IN)=64 words=256B — 瓶颈！
// EP2(OUT)浪费16 words — OUT 不需要 TX FIFO
```

优化后：
```c
.device_rx_fifo_size = (320 - 16 - 128 - 0 - 16),  // RX=160 words
.device_tx_fifo_size = { [0]=16, [1]=128, [2]=0, [3]=16, ... }
// EP1(CDC IN)=128 words=512B — 翻倍！
// EP2(OUT)=0 — 节省出来给 EP1
```

### 效果

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| CDC IN TX FIFO | 64 words (256B) | 128 words (512B) |
| RX FIFO | 208 words (832B) | 160 words (640B) |
| 3-5 秒静默 | ✅ 存在 | ✅ 消除 |
| 首批参数吞吐(4.8s内) | ~50 params | ~393 params |

### 修改位置

文件（submodule）：`modules/rt-thread/components/drivers/usb/cherryusb/port/dwc2/usb_glue_st.c`

STM32F7 的 `param_pa11_pa12` 结构体（PA11/PA12=OTG_FS 引脚）。注意 CUAV V5 使用 PA11/PA12（OTG_FS），不是 PB14/PB15（OTG_HS）。

### 验证方法

编译后检查 `sizeof(struct usbd_serial)` 确认缓冲大小：
```bash
arm-none-eabi-gdb -batch \\
  -ex "target extended-remote :3333" \\
  -ex "monitor halt" \\
  -ex "p /x sizeof(struct usbd_serial)" \\
  -ex "monitor resume" \\
  build/rtt_deploy/cuav_v5/rt-thread.elf
# sizeof = 0x9120 → TX_BUFSIZE=32768（32KB） + RX_BUFSIZE=4096 + 开销
```

### 相关调试计数器

在 CherryUSB `usbd_serial.c` 中添加的 volatile 计数器：
- `dbg_serial_write_calls` — 总写入次数
- `dbg_serial_write_ok` — 成功写入（ringbuffer 空闲）
- `dbg_serial_write_timeout` — ringbuffer 满导致失败
- `dbg_serial_timeout_tx_active` — 同时 tx_active=1 导致超时

高 `write_timeout` 值（如 >80%）→ 写入被 CherryUSB ringbuffer 阻塞 → 需要增大 FIFO 或提高 UART 线程优先级以加速 drain。

### USB 流吞吐天花板

即使 FIFO 优化，USB FS (12Mbps) 的理论极限：
- 64 字节/帧 × 1000 帧/s = 64 KB/s
- 参数枚举理论极限 ~1800 params/s（不计流数据）
- 实际当前 15-25 params/s 瓶颈在 UART 线程调度频率（仅每 3ms 运行 1ms）

### 未来方向

- STM32F765 **OTG_HS** 外设使用内部 FS PHY 时有 4096 words FIFO（但 CUAV V5 仅焊接 OTG_FS 引脚）
- 增大 `_writebuf`（当前 8KB → 32KB）减少 txspace=0 概率
- 修改 CherryUSB 为双缓冲 TX 模式（当前单缓冲 tx_active 限制）

### 症状
- pymavlink 120s 测试：心跳最大间隔 8-15s（正常应 ~1s）
- 120 秒中约 60-80 个 1 秒轮询周期无任何数据（50-65% 空转）
- MAVROS 默认 5s 心跳超时频繁断连

### 根因：thread 和 ISR 的 EPENA 竞争

`usbd_serial_kick_tx()` 可从两个上下文调用：
1. **线程侧**：`usbd_serial_write()` → PRIMASK 区内 → `kick_tx` → `usbd_ep_start_write()`
2. **ISR 侧**：`usbd_cdc_acm_bulk_in()` (XFRC 回调) → `kick_tx` → `usbd_ep_start_write()`

当两者同时进入 `usbd_ep_start_write()` 时：
- 第一个调用设置 EPENA=1，启动 DMA 传输
- 第二个调用检测到 EPENA=1，执行 `dwc2_flush_txfifo()`（PRIMASK 关中断下 200K 次 NOP 忙等）
- 关中断下 flush 阻塞 USB ISR → XFRC 无法触发 → 端点永久卡死
- 最终靠 `_usb_write_fail_count` 超时（5s）触发恢复 → gap 约 5-14s

### 尝试的修复方案及结果（共10种）

| # | 方案 | max gap | 结果 | 回归 |
|---|------|---------|------|------|
| 0 | 原始代码（PRIMASK 内调 kick_tx） | 14.23s | baseline | 无 |
| 1 | ✅ kick_tx 移出 PRIMASK（should_kick 模式） | 7.83-11s | **较好** | 无 |
| 2 | TX buffer 2048→4096 单独 | 15.87s | 更差 | 无 |
| 3 | 跳过 dwc2_flush_txfifo（EPENA=1 时 return 0） | 61.6s | 更差 | 否但退化 |
| 4 | PRIMASK 内预占位 tx_active=1 in write() | — | TX 永久卡死 | **是** |
| 5 | LDREX/STREX 原子 test-and-set | — | USB 完全失效 | **是** |
| 6 | ISR 内 kick_tx 加 PRIMASK save/restore | — | TX 永久卡死 | **是** |
| 7 | ISR 只清 tx_active（不调 kick_tx） | — | 1心跳/120s | **是** |
| 8 | ❌ PRIMASK 扩展到 usbd_ep_start_write | **77.94s** | **最差** | **是** |
| 9 | ❌ EPENA=1 return -3 + 数据放回 ringbuffer | **67.71s** | TX 前67.7s完全死亡 | **是** |
| 10 | ❌ ISR 延迟 kick_tx（tx_need_kick 标志位） | **0-110B/25s** | **TX完全死亡** | **是** |
| 11 | ✅ Option C: ISR kick_tx + EPENA预检 + 非阻塞return -3 | **待验证** | 待验证 | 无 |

### ❌ 失败方案的教训（详细）

**方案4: PRIMASK 预占位**（`tx_active=1` 在 PRIMASK 区内提前设置）：
- ISR 侧被 `if (tx_active) return` 门控挡住，永远不再调 `kick_tx`
- `tx_active` 只在 `kick_tx` 内部 avail==0 时才清零
- 形成**死锁**：线程预占位 → ISR 不触发 → XFRC 不发生 → tx_active 永不清零

**方案5: LDREX/STREX 原子操作**：
- Cortex-M 的 Local Monitor 在 ISR 抢占线程的独占访问时会清除标记
- ISR 中 STREXB 可能反复失败 → do-while 死循环 → USB 永久不初始化
- **ISR 上下文中不应使用 LDREX/STREX 等待循环**

**方案6: ISR 内 kick_tx 加 PRIMASK save/restore**：
- ISR 执行时 PRIMASK 已经是 1（Cortex-M 进 ISR 自动关中断）
- `__get_PRIMASK()` 读到 1 → save → `cpsid i`（已经是 1，无效果）→ kick_tx → restore 1
- **整个 save/restore 序列是 no-op**，对 ISR 路径没有任何保护效果

**方案7: ISR 只清 tx_active（不调 kick_tx）**：
- ISR 清除 tx_active 后，没有调用 kick_tx 重新 arm 端点
- 120 秒只有 1 个心跳 → TX 链完全断裂
- **ISR 必须调用 kick_tx 来重新 arm 端点**，否则 XFRC 完成后端点永远不会再次启动

**方案8: PRIMASK 扩展到 usbd_ep_start_write（最差）**：
- 从线程侧将 PRIMASK=1 保持到 usbd_ep_start_write 完成才恢复
- usbd_ep_start_write 含多步 DWC2 寄存器读写 + 可能的 dwc2_flush_txfifo
- 关中断时间过长 → **错过 USB SOF 和其他端点中断** → 控制器状态损坏
- 结果 77.94s max gap，TX 在初始突发后完全死亡
- **PRIMASK 绝对不能覆盖 usbd_ep_start_write**

**方案9: EPENA=1 return -3 + 数据放回 ringbuffer（Option A）**：
- 在 `usbd_ep_start_write` 中 EPENA=1 时直接 return -3（busy）
- `kick_tx` 处理 -3 时：保持 tx_active=1，用 `rt_ringbuffer_put` 把数据放回
- 期望：ISR 的 XFRC 完成中断会清除 tx_active 并重新 kick
- **失败原因**：如果 DWC2 端点真正 stuck（主机未 ACK、USB suspend），XFRC 永远不触发
  → tx_active 永远为 1 → write() 被 `if (tx_active) return` 挡住
  → tx_stuck_counter 只在 ringbuffer 满（写入失败）时递增，正常 5Hz 心跳不一定会让 ringbuffer 满满 100 次
- 结果：前 67.7 秒完全无数据，之后恢复但不稳定（3-8s gap）
- **与方案3的区别**：方案3 返回 0（假装成功），数据被消费但未发送；方案9 返回 -3 + 数据放回，但死锁风险更严重
- **教训：任何依赖 XFRC 来解锁的方案都有死锁风险**，必须有线程侧主动恢复路径

**方案10: ISR 延迟 kick_tx via tx_need_kick 标志位（Option B）**：
- ISR `usbd_cdc_acm_bulk_in()` 只做 `tx_active=0; tx_need_kick=1;`，不调用 kick_tx
- 线程 `usbd_serial_write()` 在 ringbuffer put 成功后检查 tx_need_kick，清除标志并调用 kick_tx
- `usbd_ep_start_write` 中 EPENA=1 时恢复原始 flush 路径（SNAK+EPDIS+wait+flush）
  → **安全**，因为 kick_tx 只在线程上下文执行（PRIMASK=0），200K nop 虽然慢但不会死锁
- **与方案7的关键区别**：方案7 只清 tx_active 没有 tx_need_kick 标志，write() 中
  `if (!tx_active) kick_tx()` 会在 XFRC 发生在两次 write 之间时错过事件。
  方案10 每次 write() 都无条件调用 kick_tx（kick_tx 内部有 tx_active 保护），并清除 tx_need_kick 防止状态泄漏
- **设计原则**：ISR 只设标志，所有耗时操作（flush、端点配置）在线程上下文执行
- **❌ 实测失败**：TX 几乎完全死亡（0-110B/25s）。ISR 不调 kick_tx 导致端点在 XFRC 后永不重新 arm。
  与方案7本质相同——即使有 tx_need_kick 标志，write() 调用频率（5Hz 心跳）远不够维持端点活跃。

### 核心设计约束（11次实验验证）

1. **ISR 必须直接调用 kick_tx** — 仅设标志位不够，write() 调用频率（5Hz）远不够维持端点活跃（方案7、10证明）
2. **PRIMASK 不能覆盖 usbd_ep_start_write** — 关中断太久导致控制器损坏（方案8证明，77.94s）
3. **PRIMASK 仅保护 tx_active 标志是正确设计** — 短临界区（<1μs），ISR 正常触发（方案1证明）
4. **ISR 内 PRIMASK save/restore 是 no-op** — ISR 执行时 PRIMASK 已为 1（方案6证明）
5. **ISR 内不应使用 LDREX/STREX 等待循环** — Local Monitor 被 ISR 抢占时清除标记（方案5证明）
6. **PRIMASK 预占位 tx_active=1 导致死锁** — ISR 被门控挡住，tx_active 永不清零（方案4证明）
7. **跳过 flush (return 0) 比原始更差** — 端点状态不一致，61.6s gap（方案3证明）
8. **仅扩 TX buffer 无效** — 竞态窗口不受 buffer 大小影响（方案2证明）
9. **依赖 XFRC 解锁的方案有死锁风险** — 端点真正 stuck 时 XFRC 永不来（方案9证明）
10. **EPENA=1 时消费 ringbuffer 数据再 return -3 会丢数据** — 方案9 证明数据放回不可靠
11. **EPENA 预检必须在消费 ringbuffer 数据之前** — 否则 EPENA=1 return 导致数据永久丢失（Option C 设计原则）

### ❌ Option B 失败分析（2026-04-18 实测）

**设计**：ISR 只设标志位（`tx_need_kick=1`），线程 write() 中安全执行 kick_tx。

**实测结果**：TX 几乎完全死亡（0-110B/25s，应为 ~30KB/s）。pymavlink "No initial heartbeat"。

**失败根因**：
1. **ISR 不调 kick_tx → XFRC 后端点永不重新 arm**（与方案7相同的根本问题）
2. **write() 依赖自身被调用来消费 flag**：ArduPilot 5Hz 心跳时 write() 调用频率太低，端点长时间 idle
3. **self-heal 逻辑有盲区**：`tx_stuck_counter` 只在 `written==0`（ringbuffer 满）时递增，正常心跳流量不会让 ringbuffer 满满 100 次 → recovery 永远不触发
4. **commit `d8e850724e` 的 message 写的是 "revert to baseline" 但实际代码是 Option B** — 导致误判板上固件版本

**教训**：
- ISR 必须直接或间接触发 kick_tx，不能仅依赖 write() 轮询
- self-heal 逻辑必须覆盖"tx_active=1 但 write() 持续成功"的场景
- commit message 必须与实际代码一致，否则排障时间浪费

### ✅ 当前最佳方案 — Option C: ISR 恢复 kick_tx + EPENA 预检 + 非阻塞 return（待验证）

**三处修改**：

**1. ISR 恢复调用 kick_tx**（像 baseline，保证端点在 XFRC 后立即重新 arm）：
```c
void usbd_cdc_acm_bulk_in(uint8_t busid, uint8_t ep, uint32_t nbytes)
{
    // ... XFRC 处理 ...
    serial->tx_active = 0;
    usbd_serial_kick_tx(serial);  // ISR 直接调用（PRIMASK=1，安全）
    return;
}
```

**2. kick_tx 加 EPENA 预检**（在消费 ringbuffer 数据之前！）：
```c
// 在从 ringbuffer 取数据之前，预检 endpoint 状态
{
    uint8_t ep_idx = serial->in_ep & 0x7F;
    if (ep_idx && (USB_OTG_INEP(ep_idx)->DIEPCTL & USB_OTG_DIEPCTL_EPENA)) {
        serial->tx_active = 0;
        return;  // 不消费数据，下次 XFRC 或 write() 会重试
    }
}
```

**3. DWC2 EPENA=1 路径改为非阻塞 return -3**（消除 200K nop 死锁）：
```c
if (ep_idx && (USB_OTG_INEP(ep_idx)->DIEPCTL & USB_OTG_DIEPCTL_EPENA)) {
    return -3;  // 立即返回，不阻塞，不 flush
}
```

**与之前所有方案的关键区别**：
- vs baseline (#0)：ISR 仍调 kick_tx（吞吐量有保障），但 EPENA=1 不再阻塞 200K nop
- vs Option A (#9)：EPENA=1 return -3 但**数据已从 ringbuffer 取出**会丢失；Option C 在取数据**之前**预检，不丢数据
- vs Option B (#10)：ISR 不调 kick_tx 依赖 write() 轮询 → TX 几乎死亡；Option C 恢复 ISR kick_tx

**需要 `#include "usb_dc_dwc2.h"`** 才能访问 `USB_OTG_INEP`/`USB_OTG_DIEPCTL_EPENA` 宏。

**待验证**（代码已写，构建因缺少头文件失败，加 include 后未完成构建+烧录）

### 之前的方案（should_kick 模式）— commit: submodule `a9f9ff83bf`, main `134774162c`

```c
// kick_tx() — PRIMASK 仅保护 tx_active 标志检查/设置
static void usbd_serial_kick_tx(struct usbd_serial *serial)
{
    uint32_t primask = __get_PRIMASK();
    __asm volatile("cpsid i" ::: "memory");

    if (serial->tx_active) {
        __asm volatile("msr primask, %0" :: "r"(primask) : "memory");
        return;
    }
    serial->tx_active = 1;
    __asm volatile("msr primask, %0" :: "r"(primask) : "memory");

    // ... usbd_ep_start_write 在开中断下执行 ...
}

// ISR — 直接调用 kick_tx（PRIMASK 已为 1，save/restore 是 no-op 但无害）
void usbd_cdc_acm_bulk_in(uint8_t busid, uint8_t ep)
{
    // ... XFRC 处理 ...
    serial->tx_active = 0;
    usbd_serial_kick_tx(serial);  // 重新 arm 端点
}
```

**效果**：4416 msgs/120s, 24 types, max gap 11.01s, 62/120 空秒。

### 待尝试方向（Option C 验证后的 fallback）

**Option D**: RT-Thread software interrupt（pendSV）延迟 kick_tx — 比 write() 检查更及时。
**Option E**: kick_tx 内检测 EPENA=1 时调 `usbd_ep_recover_stuck()` 而非直接 return — 主动恢复端点。

### 辅助优化

```c
// UARTDriver.cpp — 降低写失败恢复阈值
// 原来：_usb_write_fail_count > 5000  (5秒)
// 现在：_usb_write_fail_count > 500   (500ms)
```

### MAVROS 配置建议
```bash
# conn_heartbeat ≥ 15s 以容忍偶发传输中断
ros2 launch mavros apm.launch fcu_url:=... conn_heartbeat:=15.0
```

### 提交记录
- Submodule: `a9f9ff83bf` — `fix(usb): CDC TX kick_tx 移出 PRIMASK 区 + TX buffer 扩容至 4096`
- Main repo: `134774162c` — `fix(usb): 减少 USB CDC 写失败恢复延迟 + 更新 submodule`

---

## ⚠️ 热复位验证测试注意事项（2026-04-18）

**等待时间规则**：
- `mon reset init` → bootloader（PC=0x08000200）→ 等 5-10 秒 → jump_to_app → app init ~20-30 秒
- **至少等待 45 秒**再 halt 检查或测试 MAVLink
- 30 秒等待 = 假阴性（bootloader 可能还没跳转）
- ttyACM 在 bootloader 阶段就可能枚举（残留），不代表 app USB 已就绪

**验证方法**：
```bash
# 热复位
arm-none-eabi-gdb -batch -ex "target remote :3333" -ex "mon reset init" -ex "quit"
sleep 45  # 必须 45+ 秒

# 检查 app 是否运行（VTOR=0x08008000 = app，CFSR/HFSR=0 = 无异常）
arm-none-eabi-gdb -batch \
  -ex "target remote :3333" -ex "mon halt" \
  -ex "p/x *(unsigned int*)0xE000ED08" \
  -ex "p/x *(unsigned int*)0xE000ED28" \
  -ex "p/x *(unsigned int*)0xE000ED2C" \
  -ex "mon resume" -ex "quit"
```

**判据**：VTOR=0x08008000 + CFSR=0 + HFSR=0 = app 正常运行。

---

## 注意事项

- INIT_COMPONENT_EXPORT 在 scheduler 启动前运行，不能用 rt_thread_mdelay
- `usbd_dwc2_delay_ms()` 用 NOP 循环实现，在 scheduler 前可用
- `rt_kprintf()` 直接写 UART，在 scheduler 前可用（适合诊断）
- GDB halt 时 PC 可能不准（bootloader 阶段需多次读取）
- PCGCCTL PHY gate 在 CSRST=1 时不生效（已验证），不要依赖此策略
- ⚠️ **PCGCCTL.STOPCLK 在 CSRST 之前有效，但必须条件执行**（2026-04-17 实测验证）：
  - 在 CSRST 之前写 PCGCCTL=STOPCLK 能让 PHY 停止，AHB 恢复 idle → CSRST 可完成
  - **但冷启动路径不能加 STOPCLK！** 会在正常初始化时序中破坏 DWC2，导致 GRSTCTL 死锁、AHB2ENR=0、GSNPSID 异常
  - **必须用 `need_phy_gate` 标志条件执行**：仅当 AHBIDL=0（热复位特征）时才 gate PHY
- **热复位后进入 cherryusb_cdc_init 时的典型状态**：GRSTCTL=0x40001847, GSNPSID=0x0200d1e8, AHB2ENR=0
- **GDB 通过 stlink 读取外设寄存器不受 AHB2ENR 影响**（stlink 有独立 SWD 总线），但 CPU 代码访问需要时钟 enable
- `usb_dc_init` 中 `memset g_dwc2_udc` 后 `snpsid=0`，直到 `dwc2_get_hwparams` 重新读取
- **AHBIDL 在 CherryUSB 头文件中定义为 bit31** (`USB_OTG_GRSTCTL_AHBIDL`)，与 RM0431 一致
- **Bootloader PC=0x08000200**（reset handler），app PC=0x08008000+。bootloader 有 5s 超时
- **USB_ASSERT_MSG 默认是 while(1) 死循环**，dwc2_core_init 失败后会导致 USB 线程永久卡死
- **RCC AHB2 reset 无效但可保留**：虽然不能清除 CSRST 死锁，但它能复位其他 AHB2 外设状态
- **GRSTCTL=0 后只保留 FRMCNT (bit6)**，其他可写 bits 全部清除
- **`__HAL_RCC_USB_OTG_FS_CLK_ENABLE()` 映射到 `__HAL_RCC_USB2_OTG_FS_CLK_ENABLE()`** — 设置 RCC_AHB2ENR_OTGFSEN (AHB2ENR bit7)
- **AHBIDL=0 时 GRSTCTL=0 写入无效** — 必须先通过 clock gating 或 SDIS 让 AHB 恢复
