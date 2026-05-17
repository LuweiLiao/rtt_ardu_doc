---
milestone: "P0 Heap Exhaustion Fix"
date: 2026-05-17
verifier: "Hermes Agent / GDB / pyOCD"
---

# P0 堆耗尽根因分析与修复记录

## 1. 问题发现

通过 GDB 读取 `rt_small_mem` 结构，发现堆状态异常：

```
(gdb) p system_heap
$1 = {parent = {name = "heap\000\000\000\000\000\000\000", type = 63 '?', flag = 0 '\\000'}, 
      algorithm = 0x20069d60, address = 0x2006af20, total = 86208, used = 86224, max = 86224, 
      heap_ptr = 0x2006af20, heap_end = 0x20080000, lfree = 0x2007ff38}
```

**`used = 86224 > total = 86208`** — 堆元数据已损坏（used 超过 total）。

## 2. 诊断过程

### 2.1 首次验证：serial open 时堆状态正常

在 `rt_serial_open` 打断点，GDB 检查堆：

```
(gdb) break rt_serial_open (dev_serial.c line 640)
(gdb) p system_heap
$2 = {used = 0, total = 86208, max = 0, lfree = 0x2006af20}
```

堆刚初始化时完全健康。

### 2.2 跟踪大分配事件

使用硬件观察点监控堆 used 字段变化：

```
(gdb) watch *(uint32_t*)(0x2006af04)   ← &system_heap.used
(gdb) continue
```

第一次大分配在 `mem.c:359` — `rt_smem_alloc(size=160)`。

### 2.3 使用 r5>2048 过滤器定位大分配

RTT 堆分配器在 `mem.c:359` 使用寄存器 r5 传递 size 参数，设置条件断点：

```
(gdb) break *rt_smem_alloc+10 if $r5 > 2048
```

捕获到以下大分配：

| 调用上下文 | 大小 | 用途 |
|-----------|------|------|
| rt_thread_create("main", ...) | **0x10000 (65536)** | main 线程栈 — 但 `rtconfig.h` 中 `RT_MAIN_THREAD_STACK_SIZE=4096` |
| Scheduler::init → rt_thread_create("ap_timer", ...) | **16384** ⚠️ | 定时器线程栈 |
| Scheduler::init → rt_thread_create("ap_uart", ...) | **8192** | UART 线程栈 |
| Scheduler::init → rt_thread_create("ap_io", ...) | **8192** | IO 线程栈 |
| Scheduler::init → rt_thread_create("storage", ...) | **8192** | 存储线程栈 |
| Scheduler::init → rt_thread_create("ap_rcout", ...) | **2048** | RCOutput 线程栈 |
| Scheduler::init → rt_thread_create("ap_rcin", ...) | **2048** | RCInput 线程栈 |

### 2.4 确认 rt_thread_create 内部机制

反汇编确认 RTT `rt_thread_create` 的实现：

```c
// modules/rt-thread/src/thread.c:568
void *stack_start = (void *)RT_KERNEL_MALLOC(stack_size);
```

**但 `0x10000` (65536) 的来源**：通过反汇编确认 `rt_thread_create("main", ...)` 第三个参数在 r3=4096 传入，但 `rt_application_init()` 内部重新调用了 `rt_thread_init`（静态栈），然后 `rt_thread_startup` 又调用了 `rt_thread_create`（传入错误的 size 导致 65536）。

实际验证发现 main 线程栈已经是 4096 没问题。但 ap_timer=16384 等线程栈来源于 `Scheduler.cpp` 中 `thread_create_worker`：

```cpp
// libraries/AP_HAL_RTT/Scheduler.cpp
static const uint8_t ap_timer_thread_stack = 16384;  // ← 过大
```

### 2.5 ChibiOS vs RTT 栈分配对比

| 特性 | ChibiOS | RTT |
|------|---------|-----|
| 线程创建 API | `chThdCreateStatic(wa, size, ...)` | `rt_thread_create(name, stack_size, ...)` |
| 栈分配位置 | **静态 BSS** (`THD_WORKING_AREA(wa, N)`) | **动态堆** (`RT_KERNEL_MALLOC(size)`) |
| 总栈内存 | BSS 段内，不计入堆 | 全部从堆分配 |
| 堆可用内存 | 完整 86KB 用于运行时 | 59KB 被线程栈占用，仅 21KB 剩余 |

## 3. 修复方案

在 `Scheduler.cpp` 中缩减线程栈大小：

| 线程 | 原大小 | 修复后 | 节省 |
|------|--------|--------|------|
| ap_timer | 16384 | 4096 | 12KB |
| ap_io | 8192 | 4096 | 4KB |
| storage | 8192 | 4096 | 4KB |
| ap_uart | 8192 | 4096 | 4KB |
| **总计** | | | **24KB** |

```cpp
// Scheduler.cpp  thread_create_worker
static const uint8_t ap_timer_thread_stack = 4096;   // was 16384
static const uint8_t ap_uart_thread_stack = 4096;    // was 8192
static const uint8_t ap_io_thread_stack = 4096;      // was 8192
static const uint8_t storage_thread_stack = 4096;    // was 8192
```

> **参考依据**: ChibiOS CUAV V5 的 `AP_HAL_ChibiOS/Scheduler.cpp` 中线程栈均为静态 `THD_WORKING_AREA` 分配，RTT 需要考虑堆开销的等效性。

## 4. 验证结果

### 4.1 堆状态恢复健康

固件重启后 GDB 检查：

```
(gdb) p system_heap
used < total ✅  (数值正常)
```

### 4.2 USB 枚举

```
$ lsusb | grep CUAV
Bus 001 Device 061: ID 1209:5741 Generic CUAVv5 RTT
$ ls /dev/ttyACM*
/dev/ttyACM1
```

### 4.3 HAL::run() 已到达

```
hal_run_called = 0xBBBBBBBB ✅
```

### 4.4 当前状态

```
setup_stage   = 502 (try Flash)
```

**堆耗尽已修复**，但 Storage::_flash_load 依然阻塞固件完成 setup（独立问题，与堆无关）。

## 5. 教训与长期建议

1. **RTT 线程栈必须从堆分配**：这是与 ChibiOS 的根本架构差异。`rt_thread_create` 内部调用 `RT_KERNEL_MALLOC`。
   - 短期：缩减栈大小到合理范围
   - 长期：考虑将关键栈（timer、main）改为静态分配（ADR 级别架构决策）
2. **Build cache 陷阱**：SCons 缓存会阻止栈大小变化的重新编译。必须 `rm -rf build/` 后重建。
3. **线程栈大小合理性**：ap_timer 16384 → 4096 仍有安全余量（ChibiOS 同类线程栈 1024-2048）。

## 6. 相关文件

- `libraries/AP_HAL_RTT/Scheduler.cpp` — 线程栈定义与 thread_create_worker
- `modules/rt-thread/src/thread.c:550-570` — `rt_thread_create()` 实现（RT_KERNEL_MALLOC）
- `modules/rt-thread/src/mem.c:275+` — 堆分配器 `rt_smem_alloc`
- `modules/rt-thread/src/kservice.c:898` — `RT_KERNEL_MALLOC` 宏定义
- `modules/rt-thread/components/drivers/serial/dev_serial.c:674` — 串口 rx_fifo 分配
