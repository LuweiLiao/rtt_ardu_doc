# DTCM Bootloader 残留 + I-Cache Barrier 修复

## 背景

2026-05-12 调试中发现：Bootloader 跳转到固件后，DTCM 中 `serial1Driver` 的 vtable
被 bootloader 遗留下的值 0x08122960（"CUAVv5-RTT..."板名字符串地址）污染。

固件的 Reset_Handler 虽然做了 BSS 清零（_sbss→_ebss，覆盖 0x2000e510），
但 I-Cache 保留了 bootloader 的指令缓存行。在 BSS 清零后，C++ 构造器设 vtable 为
正确值 0x081228b8，随后初始化过程中 I-Cache 把 bootloader 的残留数据重新加载到 DTCM，
覆盖了 vtable。

## 诊断方法

### 1. 确认症状

```bash
# 复位后立即 halt（还在 bootloader 中），读 serial1Driver vtable
echo "reset halt" | nc -q 2 localhost 4444
echo "mdw 0x2000e510 4" | nc -q 2 localhost 4444
# → 0x08122960  （错误值！正确应为 0x081228b8）

# 确认 vtable 正确值
arm-none-eabi-nm rt-thread.elf | grep "_ZTVN3RTT10UARTDriverE"
# 期望 vtable = 上面值 + 8（跳过 offset-to-top 和 typeinfo）
```

### 2. 关键内存布局

```
0x2000e004: ioUartDriver (1124B)
0x2000e468: utilInstance (168B) ← 紧邻 serial1Driver！
0x2000e510: serial1Driver     ← vtable 被覆盖
```

### 3. 追踪写入者

```bash
# 手动清零
echo "mww 0x2000e510 0x00000000" | nc -q 2 localhost 4444
# 设写监视点
echo "wp 0x2000e510 4 w" | nc -q 2 localhost 4444
echo "resume" | nc -q 1 localhost 4444
sleep 15
echo "halt"
echo "mdw 0x2000e510 4"
```

## 修复方法

### 位置：`startup_rtt_override.S` 的 Reset_Handler

在 BSS 清零完成后，entry() 调用之前添加：

```asm
    ldr   r2, =_sbss
    ldr   r4, =_ebss
    movs  r3, #0
    b     .L_LoopFillBss
.L_FillBss:
    str   r3, [r2]
    adds  r2, r2, #4
.L_LoopFillBss:
    cmp   r2, r4
    bcc   .L_FillBss

    /* === I-Cache 屏障：防御 bootloader DTCM 残留 === */
    dsb                              /* 确保 ALL STR 已完成内存写入 */
    isb                              /* 刷新前端流水线 */
    movs  r0, #0
    mcr   p15, 0, r0, c7, c5, #0    /* ICIALLU — invalidate entire I-Cache */
    dsb
    isb

    bl    entry
```

### 备选：vtable 运行时验证（C 代码）

在 `rt_board_init.c` 的 `rtt_run_cpp_ctors()` 中：

```c
static int rtt_run_cpp_ctors(void)
{
    __libc_init_array();

    /* 验证 UARTDriver vtable */
    uint32_t expected_vtable;
    __asm__ volatile("ldr %0, =_ZTVN3RTT10UARTDriverE + 8" : "=r"(expected_vtable));
    extern uint32_t _ZL13serial1Driver[];
    uint32_t got = _ZL13serial1Driver[0];
    if (got != expected_vtable) {
        rt_kprintf("[CTOR] serial1 vtable CORRUPT: got 0x%08x expected 0x%08x\n",
            got, expected_vtable);
        _ZL13serial1Driver[0] = expected_vtable;  // 紧急修复
    }
    return 0;
}
```

## 验证

```bash
# 烧录后复位，等待 5 秒（bootloader 阶段），再等 10 秒
sleep 15
echo "halt" | nc -q 2 localhost 4444

# 验证无 HardFault
echo "mdw 0xE000ED28 1" | nc -q 2 localhost 4444
# → 0x00000000

# 验证 vtable 正确
echo "mdw 0x2000e510 4" | nc -q 2 localhost 4444
# → 0x081228b8  （_ZTVN3RTT10UARTDriverE + 8）
```
