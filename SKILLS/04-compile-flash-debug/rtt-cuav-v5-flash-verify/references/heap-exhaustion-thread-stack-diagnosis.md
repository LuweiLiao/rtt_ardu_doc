# RTT 堆耗尽诊断全记录（2026-05-17）

## 问题概览

固件烧录后 USB CDC 枚举成功，但无 MAVLink 心跳。串行驱动打开第二个 serial 设备时 `rt_malloc(rx_fifo)` 返回 NULL，触发 `RT_ASSERT` 死循环。

## 诊断链路

```
USB 枚举成功, 但无数据
→ 怀疑固件在某处挂起
→ OpenOCD halt 发现 PC 在 rt_assert_handler
→ addr2line: dev_serial.c:676 → RT_ASSERT(rx_fifo != RT_NULL)
→ 堆耗尽，serial rx_fifo 无法分配
→ 检查堆使用 → lfree 非常接近 heap_end
→ 检查堆消费者 → 所有 RTT 线程栈从堆分配（rt_thread_create → RT_KERNEL_MALLOC）
→ ChibiOS 对照确认差异：ChibiOS 线程栈是 linker 静态分配的
```

## 精确诊断步骤

### 1. 定位阻塞点

```bash
pyocd commander -t STM32F767ZI -c \
  "read32 0x2004089c 4; read32 0x200201c8 4; read32 0x2003e10c 4; exit" 2>&1
# rtt_dbg_setup_stage=502 (Storage::_flash_load)
# rtt_dbg_hal_run_called=0xBBBBBBBB ✅
# rtt_dbg_main_loop_iterations=0
```

setup_stage=502 意味着 init 流程推进到了 Storage，但 serial 打开失败在更早阶段（serial 在 502 之前就已断言）。

### 2. 堆结构分析

堆使用 `RT_USING_SMALL_MEM` + `RT_USING_SMALL_MEM_AS_HEAP` 分配器。

`rt_small_mem` 结构（ARM 32-bit, RT_NAME_MAX=12）：
```
parent:    rt_object(name[12]+type+flag+pad+list[8])=24
           + algorithm(4)+address(4)+total(4)+used(4)+max(4) = 44 bytes
heap_ptr:  4 bytes → 堆管理的实际起始
heap_end:  4 bytes → 堆管理的结束
lfree:     4 bytes → 空闲链头部
mem_size_aligned: 4 bytes
总计: 60 bytes
```

在 RAM 中：
- `system_heap` 指针存储地址：`0x20069d54`
- smem struct 在堆起始：`0x2006aee0`
- `heap_ptr=0x2006af20`（实际可用内存开始）
- `heap_end=0x2007fff0`（实际可用内存结束）
- 总容量 = `0x2007fff0 - 0x2006af20 = 86208 (0x150c0)` 字节 ≈ 84KB
- `mem_size_aligned` 验证：`0x150c0`

**关键事实**：系统从 `0x2006af20` 到 `0x2007fff0` 共 84KB 可用堆。

### 3. GDB watchpoint 跟踪堆分配

```bash
cat > /tmp/heap_watch.gdb << 'GDBEOF'
set pagination off
file /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf
target remote localhost:3333

# used 字段在 smem struct 偏移 36 处
# 直接读 C 表达式获得精确地址
watch *(rt_size_t*)((char*)system_heap + 36)

commands
  silent
  printf "used → %u at PC=0x%08lx\n", *(rt_size_t*)((char*)system_heap + 36), $pc
  bt 3
  continue
end
continue
quit
GDBEOF

timeout 120 arm-none-eabi-gdb -batch -x /tmp/heap_watch.gdb 2>&1 | \
  tee /tmp/heap_trace.log
```

输出示例（关键帧）：
```
used → 4 at PC=0x0807af4e   ← 首次分配（main thread?）
used → 4132 at PC=0x0807af4e  ← 大量分配
...
used → 86072 at PC=0x0806fef0  ← serial open #2 耗尽
```

### 4. 跟踪 serial open 过程中的堆消耗

```bash
cat > /tmp/serial_track.gdb << 'GDBEOF'
set pagination off
file /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf
target remote localhost:3333

# 在 rt_serial_open 设置断点，每次打印堆状态
# 查找 system_heap 地址
break rt_serial_open
commands
  silent
  printf "=== rt_serial_open #%d ===\n", $bp_num
  printf "used=%u total=%u max=%u\n", \
    *(rt_size_t*)((char*)system_heap+36), \
    *(rt_size_t*)((char*)system_heap+32), \
    *(rt_size_t*)((char*)system_heap+40)
  bt 2
  continue
end
continue
quit
GDBEOF
```

**关键发现**：
- `rt_serial_open #1`：used=86072/86208 ≈ 99.8% → malloc(small) 成功（运气好）
- `rt_serial_open #2`：used=86224 > total=86208 → 超过堆上限，malloc 返回 NULL

`used=86224 > total=86208` 说明是在分配之前 used 就已经超过 total 了——之后还尝试分配 serial open #2 的 rx_fifo，自然会失败。

### 5. 线程栈大小审计

```bash
# 反编译 Scheduler::init 找到所有 rt_thread_create 调用
arm-none-eabi-objdump -d /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf \
  2>&1 | sed -n '/<.*Scheduler.*init.*>:/,/^[0-9a-f]\{8\} </p'

# 每个 create 调用的第三个参数（r2/ldr r2）是栈大小
# ap_timer:  ldr r3, [pc, #...]  → 16384 (0x4000) ← 最大问题源
# ap_io:     ldr r3, [pc, #...]  → 8192  (0x2000)
# ap_uart:   ldr r3, [pc, #...]  → 8192  (0x2000)
# storage:   ldr r3, [pc, #...]  → 8192  (0x2000)
```

## 线程栈 vs 堆分配对比

| 方面 | ChibiOS（参考） | RTT（当前） | 影响 |
|------|----------------|-------------|------|
| 线程栈来源 | `.pstack`/`.mstack` linker 段（BSS） | 堆分配 `RT_KERNEL_MALLOC` | RTT 每个线程吃掉堆空间 |
| ap_timer 栈 | 静态（不计入堆） | 16384 字节从堆 | 最大消费者 |
| 总堆压力 | 0（线程栈不占用堆） | ≈65KB（所有线程栈 + thread objects） | 84KB 堆被吃掉 ~75% |
| 堆剩余 | 整个 heap 可用 | ~19KB 仅够串行 FIFO | 极紧 |

ChibiOS 的 `chThdCreateStatic()` 使用预分配的静态缓冲区（linker 脚本中定义的节）。  
RTT 的 `rt_thread_create()` 内部调用 `RT_KERNEL_MALLOC(stack_size)`。

## 修复

在 `Scheduler.cpp` 的 `init()` 中将 4 个线程栈缩小：

| 线程 | 原大小 | 新大小 | 依据 |
|------|--------|--------|------|
| ap_timer | 16384 | 4096 | 定时器回调轻量，4KB 远大于 ChibiOS 等价 |
| ap_uart | 8192 | 4096 | UART 驱动非递归，4KB 足够 |
| ap_io | 8192 | 4096 | IO 事件循环，栈深度浅 |
| storage | 8192 | 4096 | Storage 操作同步，无深度递归 |

**总节省：24KB**，堆使用率从 99.8% 降至约 75%。

## 验证

```bash
# 1. 反编译确认新栈大小
arm-none-eabi-objdump -d /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf \
  2>&1 | grep -E "ap_(timer|uart|io|storage)" | head -4

# 2. 烧录后检查堆使用
pyocd commander -t STM32F767ZI -c \
  "read32 0x2006af00 3; read32 0x2006af14 1; exit" 2>&1
# total, used, max, lfree

# 3. USB 枚举确认
lsusb | grep "1209:5741"  # Generic CUAVv5 RTT

# 4. MAVLink 心跳（需要修复 Storage::_flash_load 后才能到达主循环）
```

## 教训

1. **RTT vs ChibiOS 线程模型差异是架构性的**，不是参数微调问题。RTT 的 `rt_thread_create` 永远从堆分配，这在终端类应用（短生命周期、栈小）中没问题，但在 ArduPilot 这种多线程嵌入式系统中会造成堆碎化和耗尽。

2. **堆耗尽不一定表现为 malloc 失败**。在小内存分配器（small_mem）中，首次 serial open 可能"借用"已满堆中的空间（`plug_holes` 合并相邻空闲块），导致 `used > total` 这种反直觉的状态。第二次 serial open 才真正触发 NULL 返回。

3. **`RT_CONFIG_HOST_THREAD_STACK_SIZE` 或 `RT_MAIN_THREAD_STACK_SIZE` 不是唯一问题**。至少 6 个辅助线程各自消耗栈空间，累计达到 ~57KB。即使 main 栈只有 4096，其他线程也会耗尽堆。

4. **长期根治**：将关键实时线程（timer, IO, UART）改为 `rt_thread_init()` + 静态栈缓冲（linker 节），模仿 ChibiOS 的 `chThdCreateStatic()` 模式。
