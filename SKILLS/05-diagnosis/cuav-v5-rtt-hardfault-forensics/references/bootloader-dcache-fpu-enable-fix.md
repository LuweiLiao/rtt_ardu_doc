# Bootloader D-Cache + FPU Enable Fix (2026-05-12)

## 背景

CUAV V5 (STM32F767) 的 ArduPilot bootloader（CUAVv5_bl.bin）在跳转到固件前会使能 D-Cache 和 I-Cache。**固件的 C 代码 `SCB->CPACR |= ...` 写入 CPACR 时，写操作进入 D-Cache 而非物理寄存器，导致 FPU 实际未使能。**

## 症状全览

| 检查项 | 值 | 含义 |
|--------|-----|-------|
| CFSR | 0x00008200 | NOCP + UNDEFINSTR (FPU 指令执行时协处理器未使能) |
| HFSR | 0x40000000 | FORCED (从 UsageFault 升级) |
| CPACR (phys) | 0x00000000 | FPU 物理寄存器未使能！ |
| CPACR (非 phys) | 0x00F00000 | 误读：D-Cache 返回了缓存值 |
| CCR | 0x00070200 | DC=1 (D-Cache 被 bootloader 开启) |
| VDOR | 0x08000000 | 复位时为 0 (bootloader 空间) |

## 关键诊断步骤

### 1. 验证确认 D-Cache 被 bootloader 开启

```bash
# 复位后立即检查
echo "reset halt" | nc -q 2 localhost 4444
echo "stm32f7x.cpu mdw phys 0xE000ED14 1"  # CCR = 0x00040200 (DC=0)

# 让 bootloader 运行几秒后
echo "resume" | nc -q 2 localhost 4444; sleep 2
echo "halt
stm32f7x.cpu mdw phys 0xE000ED14 1" | nc -q 2 localhost 4444  # CCR = 0x00070200 (DC=1!)
```

### 2. 用 phys 模式读 CPACR

```bash
# ❌ 普通 mdw 可能返回缓存值
echo "mdw 0xE000ED88 1"        # 可能 = 0x00F00000 (假阳性)

# ✅ stm32f7x.cpu mdw phys 读物理寄存器
echo "stm32f7x.cpu mdw phys 0xE000ED88 1"  # = 0x00000000 (真实值)
```

## ChibiOS 参考

ChibiOS `crt0_v7m.S` 的初始化顺序：

1. `_crt0_entry`: 关中断 → 设 MSP/PSP → 设 VTOR
2. **直接 STR 写 FPCCR** + DSB/ISB
3. **直接 STR 写 CPACR = 0x00F00000** + DSB/ISB
4. `vmsr FPSCR, #0` — 清 FPU 状态寄存器（同时也是 canary：如果 FPU 没使能，这里 NOCP）
5. 写 FPDSCR = 0
6. 设 CONTROL.FPCA 位
7. `bl __cpu_init`（配置缓存/MPU）
8. `bl __early_init`（SystemInit 等）
9. 数据/BSS 复制
10. 构造器 → main

**核心区别**：ChibiOS 在所有 C 代码之前、缓存配置前完成 FPU 使能。

## 修复验证结果

修复前：每次烧录必 HardFault，CPACR=0x00000000
修复后：CPACR=0x00F00000 ✅，系统正常推进到 setup_stage=662（ins.init）
