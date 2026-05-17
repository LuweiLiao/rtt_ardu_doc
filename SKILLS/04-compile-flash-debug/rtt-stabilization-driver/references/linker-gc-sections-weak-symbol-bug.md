# GNU LD --gc-sections 弱符号地址错误导致向量表 HardFault

## 发现时间
2026-05-13 (session: CUAV V5 RTT gyro init debug)

## 背景
CUAV V5 RTT 固件 clean rebuild 后立即 HardFault。之前的增量编译版本正常启动。

## 症状
- 烧录后 MCU 立即进入 HardFault（INVSTATE）
- PC = `0x080083ca` (`hardfault_hang()`)
- CFSR = `0x00020000` (INVSTATE — bit 17)
- HFSR = `0x40000000` (FORCED)

## 诊断过程

### 步骤 1：检查向量表与符号表不一致

```bash
# 读向量表
/opt/gcc-arm-none-eabi-10-2020-q4-major/bin/arm-none-eabi-objdump -s \
  --start-address=0x08008000 --stop-address=0x08008010 \
  build/rtt_deploy/cuav_v5/rt-thread.elf

# 检查符号表
/opt/gcc-arm-none-eabi-10-2020-q4-major/bin/arm-none-eabi-nm \
  build/rtt_deploy/cuav_v5/rt-thread.elf | grep -E "Reset_Handler|Default_Handler|NMI_Handler|HardFault_Handler"
```

### 发现

| 符号 | 符号表地址 | 向量表写入值 | 应该值 |
|------|-----------|-------------|-------|
| `Reset_Handler` | **`0x080f0050`** | **`0x08000f51`** ❌ | `0x080f0051` |
| `NMI_Handler` | `0x080f00b4` (alias Default_Handler) | **`0x08000fb5`** ❌ | `0x080f00b5` |
| `HardFault_Handler` | **`0x08008364`** (strong from context_gcc.o) | `0x08008365` ✅ | `0x08008365` |

### 模式分析

**正确的符号**：HardFault_Handler — 来自 `build/kernel/libcpu/arm/cortex-m7/context_gcc.o`（RT-Thread 内核），是**不同对象文件的强定义**

**错误的符号**：Reset_Handler — 来自 `board/startup_rtt_override.o`（我们的强覆盖），但向量表给了错误地址
NMI_Handler — WEAK alias to Default_Handler 在 CMSIS 文件内，向量表给了错误地址

### 根因

GNU LD 10.2.1 的 `--gc-sections` + 同对象弱符号存在 bug：

1. CMSIS startup 文件 (`startup_stm32f767xx.s`) 定义 `.word Reset_Handler` 在 `.isr_vector` 段
2. 同文件有一 WEAK `Reset_Handler` 定义在 `.text.Reset_Handler` 段（即弱缺省实现）
3. 我们的 `startup_rtt_override.S` 定义了 STRONG `Reset_Handler` 在 `.text.Reset_Handler`
4. `--gc-sections` 将 CMSIS 文件的 `.text.Reset_Handler` 段 GC 掉（因为它弱可覆盖）
5. 但链接器对 `.isr_vector` 中 `.word Reset_Handler` 的 **R_ARM_ABS32** 重定位使用了 GC'd 段的占位地址（`0x08000f50`）而非全局强符号地址（`0x080f0050`）

**为什么 HardFault_Handler 正确**：
RT-Thread 内核的 `context_gcc.o` 提供了强硬的 `HardFault_Handler`，且 **不在同一个对象文件** 中被 `.word HardFault_Handler` 引用——重定位发生在 `startup_stm32f767xx.o` 的 `.isr_vector` 段，但符号在 `context_gcc.o`，不同对象 → 正确解析。

**触发条件**：
- WEAK 符号的定义与引用在 **同一对象文件** 内
- WEAK 符号所在的 section 被 `--gc-sections` 移除
- 有同名的 STRONG 覆盖在 **不同对象文件** 中

### 修复方案

**方案 A（推荐 — 一行改 link.lds）**：

在 link.lds 的 `.text` 输出段内，`.isr_vector` 后添加：
```ld
KEEP(*(.text.Reset_Handler))
KEEP(*(.text.Default_Handler))
```

这些 KEEP 阻止 `--gc-sections` 删除弱符号 section，确保链接器能正确解析它们的地址。

定位：`modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/linker_scripts/link.lds`

修改点（`.text` 段内，`KEEP(*(.isr_vector))` 之后）：
```ld
SECTIONS
{
    .text :
    {
        . = ALIGN(4);
        _stext = .;
        KEEP(*(.isr_vector))            /* Startup code — 已有 */

        . = ALIGN(4);
        KEEP(*(.text.Reset_Handler))    /* ← 新增：防止 GC 弱符号 */
        KEEP(*(.text.Default_Handler))   /* ← 新增 */

        . = ALIGN(4);
        *(.text)                        /* remaining code */
        ...
```

**方案 B（在启动汇编中加自定义向量表）**：

在 `startup_rtt_override.S` 中定义一个新的 `.isr_vector_custom` 段，完全绕过 CMSIS 的 `.isr_vector`。改量稍大但更彻底。

### 验证修复

修复后：
```bash
# 确认向量表正确
/opt/gcc-arm-none-eabi-10-2020-q4-major/bin/arm-none-eabi-objdump -s \
  --start-address=0x08008000 --stop-address=0x08008010 \
  build/rtt_deploy/cuav_v5/rt-thread.elf

# 期望：0x080f0051 (Reset_Handler) 和 0x080f00b5 (NMI_Handler)
# 不再是错误的 0x08000f51 和 0x08000fb5

# 烧录后验证无 HardFault
echo "reset run" | nc -q 1 localhost 4444
sleep 5
echo "halt" | nc -q 2 localhost 4444 | grep "HardFault"
# 期望：不输出 HardFault，PC 在正常代码中
```

### 影响范围

所有使用 `--gc-sections` + CMSIS startup 文件且定义了自定义 Reset_Handler 覆盖的 RT-Thread 项目。任何 WEAK→STRONG 覆盖且在向量表中有 `.word` 引用的 handler 都可能受影响。

### 相关文件

- CMSIS 启动文件：`modules/rt-thread/bsp/stm32/packages/stm32f7_cmsis_driver-latest/Source/Templates/gcc/startup_stm32f767xx.s`
- RTT 启动覆盖：`libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S`
- BSP 启动覆盖：`modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/startup_rtt_override.S`
- 链接脚本：`modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/linker_scripts/link.lds`
- 构建产物符号表：`build/rtt_deploy/cuav_v5/rtthread.map`

## 预防措施

1. 每次 clean rebuild 后检查向量表的前 4 个条目与符号表是否一致
2. 链接脚本中 `.isr_vector` 后 KEEP 所有可能被 GC 的启动 handler 弱符号 section
3. 如怀疑向量表问题，优先检查 HardFault_Handler（如果它正确而 Reset_Handler 错误，大概率是此 bug）
