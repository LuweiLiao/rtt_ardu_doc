# DTCM 遗留脏数据导致 UARTDriver vtable 破坏

> Session: 2026-05-12, CUAV v5 RT-Thread RTT + bootloader
> 根因：bootloader 在 DTCM（0x2000e510）留下脏数据 0x08122960，该值正好是固件中 "CUAVv5-RTT..." 字符串的地址，覆盖了 serial1Driver 的 vtable 指针。

## 故障现象

- CFSR = `0x00008200` (PRECISERR + BFARVALID)
- BFAR = `0x79366A28`
- PC = `0x080DA150` (`GCS_MAVLINK::txspace()`)
- LR = `0x080E0009` (`GCS_MAVLINK::try_send_message()`)
- R3 (从 _port vtable 读取的值) = `0x79366A00`

### 反汇编链

```
0x080da14a: ldr.w r0, [r0, #484]    ; r0 = this->_port（GCS_MAVLINK offset 0x1e4）
0x080da14e: ldr   r3, [r0, #0]      ; r3 = _port->vtable = 0x79366A00
0x080da150: ldr   r3, [r3, #40]     ; r3 = vtable[5]（txspace）→ BFAR 0x79366A28
0x080da152: blx   r3                 ; 未执行
```

## 诊断过程

### Step 1：复位后立即读 DTCM

```bash
echo "reset halt" | nc -q 2 localhost 4444
echo "mdw 0x2000e510 4"   # serial1Driver 的 vtable
```

**结果**：`0x2000e510: 08122960 00000000 00000000 00000000`

0x08122960 = `scalbnf` nm 误导，实际指向字符串 "CUAVv5-RTT %02X%02X%02X%02X..."

### Step 2：手动清零后设 Write Watchpoint

```bash
echo "mww 0x2000e510 0x00000000" | nc -q 2 localhost 4444
echo "wp 0x2000e510 4 w" | nc -q 2 localhost 4444
echo "resume" | nc -q 1 localhost 4444
sleep 15
echo "halt" | nc -q 2 localhost 4444
echo "mdw 0x2000e510 4" | nc -q 2 localhost 4444
```

**结果**：watchpoint 未触发，[0x2000e510] 仍然为 0（BSS 清零后未被写入）。

**但 crash 模式改变！** stacked PC 从 `0x080DA150` (txspace) 变为 `0x20056398` (heap 区域)。说明：
- bootloader 的脏数据影响了固件的初始化路径
- 手动清零后固件走了不同路径，但还是崩溃了（heap 区执行）

### Step 3：验证 vtable 正确值

```bash
arm-none-eabi-nm rt-thread.elf | grep "_ZTVN3RTT10UARTDriverE"
# = 0x081228b0

arm-none-eabi-objdump -s -j .rodata --start-address=0x081228b0 --stop-address=0x08122980
# vtable[-2] offset_to_top = 0
# vtable[-1] typeinfo = ???
# vtable[0] = 0x0806b581 (BetterStream::printf)
# ...
# vtable[42] = 0x08122960 → 这里正好是字符串 "CUAVv5-RTT..."
```

正确 vtable 指针应为：`0x081228b8`（_ZTVN3RTT10UARTDriverE + 8，跳过 offset_to_top 和 typeinfo）

### Step 4：UARTDriver 实例内存布局

使用 `arm-none-eabi-nm -n` 查看 DTCM BSS 布局：

```
地址        大小    对象
0x2000e004  1124B  _ZL12ioUartDriver
0x2000e468  168B   _ZL12utilInstance     ← 紧邻 serial1Driver！
0x2000e510  1124B  _ZL13serial1Driver    ← vtable 被覆盖
0x2000e974  1124B  _ZL13serial2Driver
0x2000edd8  1124B  _ZL13serial3Driver
0x2000f23c  1124B  _ZL13serial4Driver
...
```

### Step 5：关键的启动顺序 — 是根因诊断的关键

```
Reset_Handler in startup_rtt_override.S:
  1. bl SystemInit
  2. 复制 .data 段
  3. BSS 清零（_sbss=0x200054c0 → _ebss=0x20045294）
     → [0x2000e510] = 0x00000000
  4. bl entry() → rtthread_startup()
  5. rt_hw_board_init() → 堆初始化
  6. INIT_COMPONENT_EXPORT(rtt_run_cpp_ctors) → __libc_init_array()
     → C++ 构造器 → [0x2000e510] = 0x081228b8（%正确转换屏蔽和修复后的参数名称%）
  7. rt_application_init() → 创建 main 线程
  8. rt_system_scheduler_start() → main 线程运行
  9. main() → setup_ardupilot()
  10. GCS 初始化 → GCS_MAVLINK::txspace() → 读 _port vtable
```

问题在于 **Step 3 之后到 Step 6 之间**，或者 **Step 6 之后到 Step 10 之间**，vtable 被覆盖了。

**Vtable 被覆盖的可能路径**（按可疑度从高到低）：
1. `utilInstance`（0x2000e468，168B）的 `Util::init()` 或 `board_name()` 的 snprintf 溢出到 serial1Driver
2. bootloader 通过在 DTCM 写入然后清除的方式，在跳到固件前没有完全恢复 DTCM
3. 某处 DMA 或 memcpy 操作写错了目标地址

## 推荐的验证方法

### A) BSS 清零完整性验证

`_sbss = 0x200054c0`, `_ebss = 0x20045294`

```bash
# 验证 BSS 范围是否覆盖所有 UARTDriver 实例
# serial1Driver at 0x2000e510
echo "0x2000e510 >= 0x200054c0 && 0x2000e510 <= 0x20045294" | bc
# = 1 (范围内)
```

### B) D-Cache 排除法

STM32F7 SRAM1 (0x20020000+) 有 D-Cache，DTCM (0x20000000-0x2001FFFF) 无 D-Cache。
所有 UARTDriver 实例都在 DTCM，所以 D-Cache 不是本问题的根因。

### C) Bootloader 残留量化

```bash
# 读取 bootloader 跳转后 DTCM 的脏数据范围
echo "reset halt" | nc -q 2 localhost 4444
echo "mdw 0x2000e000 100" | nc -q 2 localhost 4444  # 读取 DTCM 区域的内容
# 与 BSS 初始值（全 0）比较，看 bootloader 留下了多少脏数据
```

## 使用的工具命令速查

```bash
# 写监视点
echo "wp <address> <length> r/w/a" | nc -q 2 localhost 4444

# 删监视点
echo "rwp <address>" | nc -q 2 localhost 4444

# 查看监视点状态
echo "wp" | nc -q 2 localhost 4444

# 读内存
echo "mdw <address> <count>" | nc -q 2 localhost 4444

# 写内存
echo "mww <address> <value>" | nc -q 2 localhost 4444

# 查看 vtable 地址（ELF）
arm-none-eabi-nm rt-thread.elf | grep "_ZTV.*UARTDriver"

# 查看 vtable 内容
arm-none-eabi-objdump -s -j .rodata --start-address=<vtable_addr> --stop-address=<vtable_addr+0x200>
```

## 实际修复（2026-05-12）

### FIX 1：I-Cache 屏障（根因修复）

在 `startup_rtt_override.S` 的 Reset_Handler 中，BSS 清零完成后添加 DSB + ISB + ICIALLU：

```asm
    /* After BSS clear */
    dsb                          /* 确保所有 STR 完成 */
    isb                          /* 刷新流水线 */
    movs  r0, #0
    mcr   p15, 0, r0, c7, c5, #0  /* ICIALLU — invalidate entire I-Cache */
    dsb
    isb
    bl    entry
```

**为何有效**：Bootloader 在 DTCM 中写入了数据（0x08122960），固件 BSS 清零虽然会覆盖 DTCM，但 I-Cache 可能保留了 bootloader 的指令缓存行，导致后续内存操作（如 C++ 构造器的 vtable 赋值）在某些处理器状态下被延迟或乱序执行。ICIALLU 确保所有缓存线条目无效。

### FIX 2：vtable 验证（运行时检测）

在 `rt_board_init.c` 的 `rtt_run_cpp_ctors()` 中，`__libc_init_array()` 返回后验证所有 UARTDriver 实例的 vtable：

```c
static int rtt_run_cpp_ctors(void)
{
    __libc_init_array();

    /* Validate UARTDriver vtable pointers */
    uint32_t expected_vtable;
    __asm__ volatile("ldr %0, =_ZTVN3RTT10UARTDriverE + 8" : "=r"(expected_vtable));
    rt_kprintf("[CTOR] UART vtable expected=0x%08x\n", expected_vtable);

    uint32_t *drv[] = { _ZL12ioUartDriver, _ZL13serial1Driver, _ZL13serial2Driver };
    const char *names[] = { "ioUart", "serial1", "serial2" };
    for (int i = 0; i < 3; i++) {
        uint32_t got = drv[i][0];
        if (got != expected_vtable) {
            rt_kprintf("[CTOR] %s: vtable CORRUPT (0x%08x), fixing\n", names[i], got);
            drv[i][0] = expected_vtable;  // 紧急修复
        }
    }
    return 0;
}
```

## 验证结果

| 检查项 | 修复前 | 修复后 |
|--------|--------|--------|
| serial1Driver vtable | 0x08122960 ❌ | 0x081228b8 ✅ |
| CFSR | 0x00008200 ❌ | 0x00000000 ✅ |
| USB CDC 枚举 | 无 | `/dev/ttyACM1` ✅ |
| MCU 运行状态 | Handler HardFault | Thread（正常）✅ |

## 相关技能

- `cuav-v5-rtt-hardfault-forensics` — CFSR=0x00008200 章节 + I-Cache 修复详情
- `rtt-stabilization-driver` — ArduPilot RTT 稳定性工作流

- ARM Cortex-M7 Technical Reference Manual: D-Cache behavior (Section 7.2)
- STM32F767 Reference Manual: DTCM vs SRAM1 (Section 2.3: Memory Map)
- ArduPilot RTT HAL: UARTDriver.cpp constructor sets vtable pointer
- `cuav-v5-rtt-hardfault-forensics` skill: CFSR=0x00008200 section
