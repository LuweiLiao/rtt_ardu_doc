# IWDG 看门狗验证方法论

## 背景

在调试 CUAV V5 RTT 移植过程中，发现一种常见错误：RCC_CSR 的 WDGRSTF 标志被误读为"当前 IWDG 正在运行"的证据。
本文档总结了验证 IWDG 是否实际在运行的三种方法。

## 方法一：Bootloader ELF 反汇编（最可靠）

CUAV V5 的 bootloader 源码可能在 ArduPilot `Tools/bootloaders/` 目录下，有 prebuilt ELF 文件。

### 检查 IWDG 初始化

```bash
# 进入固件目录
cd /data/firmare/pogo-apm

# 方法 1a：查找 stm32_watchdog_init 符号
arm-none-eabi-nm Tools/bootloaders/CUAVv5_bl.elf | grep -i "watchdog\|iwdg\|wdog"
# 期望输出（仅检查复位原因，不启动）：
#   08002804 T stm32_watchdog_save_reason
#   0800281c T stm32_was_watchdog_reset
# 如果有 stm32_watchdog_init → bootloader 启动 IWDG

# 方法 1b：查找 IWDG 启动命令 0xCCCC
arm-none-eabi-objdump -d Tools/bootloaders/CUAVv5_bl.elf | grep -c "0xCCCC"
# = 0 → bootloader 从未写入 IWDG_KR 启动码

# 方法 1c：查找 IWDG 寄存器地址 0x40003000
arm-none-eabi-objdump -d Tools/bootloaders/CUAVv5_bl.elf | grep -i "0x40003000" | head -20
# 如果无输出 → bootloader 完全不操作 IWDG 寄存器

# 方法 1d：查看所有与 IWDG 相关的汇编上下文
arm-none-eabi-objdump -d Tools/bootloaders/CUAVv5_bl.elf | grep -B5 -A5 "stm32_was_watchdog_reset"
# 确认调用者
```

### 验证结果解读

| 符号 | 含义 | 是否启动 IWDG |
|------|------|--------------|
| `stm32_was_watchdog_reset` | 读取 RCC_CSR 判断上次复位原因 | ❌ 否，只读不写 |
| `stm32_watchdog_save_reason` | 保存复位原因到 RAM | ❌ 否，只读不写 |
| `stm32_watchdog_init` | 配置+启动 IWDG（写 PR/RLR + 0xCCCC） | ✅ 是 |
| `stm32_watchdog_pat` | 喂狗（写 0xAAAA） | ❌ 否，维持运行 |

### 列出所有 watchdog 相关符号

```bash
# 映射
arm-none-eabi-nm Tools/bootloaders/CUAVv5_bl.elf | sort # 查看所有 T(ext) BSS 符号
# 只关注 watchdog
arm-none-eabi-nm Tools/bootloaders/CUAVv5_bl.elf | grep -i "watchdog\|wdog\|iwdg\|0x40003000\|0xCCCC\|0xAAAA\|0x5555"
```

## 方法二：运行时寄存器检查

通过 OpenOCD telnet 读取 IWDG 寄存器，确认是否活动和配置值。

### 读取 IWDG 寄存器

```bash
# 1. halt MCU
echo "halt" | nc -q 2 localhost 4444

# 2. 解锁 IWDG 寄存器（写 0x5555 到 KR）
echo "mww 0x40003000 0x5555" | nc -q 1 localhost 4444

# 3. 读取 PR（Prescaler）和 RLR（Reload）
echo "mdw 0x40003004" | nc -q 1 localhost 4444  # PR
echo "mdw 0x40003008" | nc -q 1 localhost 4444  # RLR

# 4. 读取 SR（Status）
echo "mdw 0x4000300C" | nc -q 1 localhost 4444  # SR: PVU(bit0), RVU(bit1)

# 5. 恢复运行
echo "resume" | nc -q 1 localhost 4444
```

### 寄存器解读

| PR 值 | 分频 | RLR=4095 时的超时 | RLR=1250 时的超时 |
|-------|------|-------------------|-------------------|
| 0 | /4 | ~0.51s | ~0.16s |
| 1 | /8 | ~1.02s | ~0.31s |
| 2 | /16 | ~2.05s | ~0.63s |
| 3 | /32 | ~4.10s | ~1.25s |
| 4 | /64 | ~8.19s | ~2.50s |
| 5 | /128 | ~16.38s | ~5.00s |
| 6 | /256 | ~32.77s | ~10.00s |

超时公式（LSI≈32kHz）：`timeout = 4 * 2^PR * (RLR+1) / 32000`

### IWDG 未运行的证据

- `IWDG_KR` 读回 0x0000（默认值）— IWDG 外设未初始化
- `IWDG_PR` 读回 0x00000000 — 不是有效 PR 值
- `IWDG_RLR` 读回 0x00000FFF — 这是复位默认值，不是被设置的值
- 写 `0x5555` 到 KR 后读回应为 0x5555 或至少非零

## 方法三：RCC_CSR 粘滞标志清除测试

RCC_CSR 的复位标志（包括 IWDGRSTF）在复位后不清零。要确认本次启动是否由看门狗引起，必须：
1. 先清除 CSR 标志
2. 然后运行系统
3. 等复位后再检查

### 步骤

```bash
# Phase 1: 清除标志
echo "halt" | nc -q 1 localhost 4444
echo "mww 0x40023874 0x01000000" | nc -q 1 localhost 4444  # 写 bit 24 (RMVF) 清除所有标志
echo "mdw 0x40023874" | nc -q 1 localhost 4444
# 期望: CSRV=1 (bit 31 有效标志), 复位标志位=0

# Phase 2: 启动系统运行
echo "resume" | nc -q 1 localhost 4444
# 等待系统复位或稳定运行

# Phase 3: 检查（如果 MCU 仍然在运行）
sleep 10
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0x40023874" | nc -q 1 localhost 4444
# WDGRSTF=1 → 本次启动中发生了看门狗复位
# SFTF=1 → 软件复位（NVIC_SystemReset）
# PORRSTF=1 → 上电/掉电复位
```

### 关键陷阱

- **不要只看一次 RCC_CSR！** 旧 boot 的残留标志会误导判断
- **清除、运行、检查** 三步缺一不可
- 如果系统正在 5 秒复位循环中，Phase 2 后 MCU 已经复位过，RCC_CSR 可能显示 WDGRSTF=1
  → 这可以是旧标志！需要 Phase 1→Phase 2（短暂运行）→halt 看是否又有 WDGRSTF

## 常见结论速查

| IWDG 状态 | 排查结论 | 后续处理 |
|-----------|---------|---------|
| Bootloader 不启动 IWDG，App 也没启动 | 5 秒复位**不是 IWDG 导致** | 查其他原因（软件复位、HardFault→escalation、电源） |
| Bootloader 不启动，App 启动了 IWDG | 超时=app 配置值 | 确认 watchdog_pat() 在定时器线程+主循环都喂狗 |
| Bootloader 启动 IWDG，App 没动 | 超时=~0.5s，必须无条件喂狗 | 修复：watchdog_pat() 无条件写 0xAAAA |
| Bootloader 不启动 IWDG 但 WDGRSTF 在运行后出现 | RCC_CSR 是旧 boot 残留 | 清除后重新测试 |

## 验证架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                  IWDG 是否在运行？                              │
├─────────────────┬───────────────────┬───────────────────────────┤
│ 方法 A (最高优) │ 方法 B (中等优)    │ 方法 C (辅助)            │
├─────────────────┼───────────────────┼───────────────────────────┤
│ 反汇编 bootloader│ 运行时寄存器读取   │ RCC_CSR 清除后验证       │
│ ELF 文件         │ IWDG_KR/PR/RLR/SR │ 清除→运行→检查           │
│ 查找符号和立即数 │ 解锁+读取         │ 确认是否 IWDG 导致复位    │
└─────────────────┴───────────────────┴───────────────────────────┘
```

## 历史记录

- 2026-05-10: 初始创建（假设 bootloader 启动 IWDG，使用 PR=0 RLR=4095 理论）
- **2026-05-11: 通过 bootloader ELF 反汇编推翻假设——bootloader 不启动 IWDG**
