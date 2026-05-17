# Cortex-M7 CP15 NOCP 故障 — STM32F767 使用内存映射代替 MRC/MCR

## 症状

烧录后立即 HardFault，CFSR=0x00080000（UFSR.NOCP），HFSR=0x40000000（FORCED）。
启动代码编译正确（CPACR/FPCCR 写入均在汇编级），但任何 `mrc p15, ...` 或 `mcr p15, ...`
指令触发 NOCP。

## 根因

Cortex-M7 的 CP15 接口**不完全支持** ARMv7-A 风格的 MRC/MCR 协处理器指令。
在 STM32F767（ARM Cortex-M7 r0p1）上：

| 操作 | Cortex-A/R MRC/MCR | Cortex-M7 替代方案 |
|------|-------------------|-------------------|
| 读 SCTLR | `mrc p15, 0, r0, c1, c0, 0` | `ldr r0, [0xE000ED30]` (SCB_SCTLR) |
| 写 SCTLR | `mcr p15, 0, r1, c1, c0, 0` | `str r0, [0xE000ED30]` |
| DCCSW | `mcr p15, 0, r0, c7, c14, 0` | `str r0, [0xE000EF5C]` (SCB_DCCSW) |
| ICIALLU | `mcr p15, 0, r0, c7, c5, 0` | `str r0, [0xE000EF50]` (SCB_ICIALLU) |

**所有系统控制寄存器在 Cortex-M7 上都是内存映射的**，位于 SCB 地址空间。

## 诊断

```bash
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0xE000ED28 2" | nc -q 1 localhost 4444  # CFSR+HFSR
# CFSR=0x00080000 → UFSR.NOCP

# 检查 CPACR
echo "mdw 0xE000ED88 1" | nc -q 1 localhost 4444
# 如果 CPACR=0x00F00000 → FPU 已启用但仍是 NOCP → 是 CP15 问题
# 如果 CPACR=0x00000000 → startup 文件不含 CPACR 写入

# 检查启动代码中所有 MRC/MCR 指令
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \
  --start-address=$(arm-none-eabi-nm build/.../rt-thread.elf | grep Reset_Handler | awk '{print "0x"$1}') \
  --stop-address=+0x100 2>/dev/null | grep -E "mrc|mcr"
```

## 修复模式

**必须修改 `libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S`**

```asm
/* 内存映射读 SCTLR */
ldr   r1, =0xE000ED30
ldr   r0, [r1]
bic   r0, r0, #0x0004
str   r0, [r1]
dsb
isb

/* 内存映射 DCCSW */
movs  r0, #0
ldr   r1, =0xE000EF5C
str   r0, [r1]
dsb
isb

/* 内存映射 CPACR */
ldr   r0, =0x00F00000
ldr   r1, =0xE000ED88
str   r0, [r1]
dsb
isb

/* 内存映射 FPCCR */
ldr   r0, =0xC0000000
ldr   r1, =0xE000EF34
str   r0, [r1]
dsb
isb

/* FPSCR 清零 */
mov   r0, #0
vmsr  FPSCR, r0

/* ICIALLU */
movs  r0, #0
ldr   r1, =0xE000EF50
str   r0, [r1]
dsb
isb
```

## 验证

```bash
echo "reset" | nc -q 1 localhost 4444
sleep 5
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0xE000ED88 1"       # CPACR = 0x00F00000 ✅
echo "mdw 0xE000EF34 1"       # FPCCR = 0x80000000 ✅
echo "mdw 0xE000ED30 1"       # SCTLR = 0x00000001 (D-Cache off) ✅
echo "mdw 0xE000ED28 2"       # CFSR=0, HFSR=0 ✅
echo "reg pc"                  # 应在应用代码中 ✅
```
