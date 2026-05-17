# Clean Rebuild 后 BSS 布局偏移导致的 HardFault 排查指南

> 发现于 2026-05-15 RTT CUAV V5 调试。`rm -rf build/` 全量重建后 _timer_list 损坏 → PRECISERR HardFault。

## 现象

系统烧录后立即进入 HardFault，timer 中断上下文：

```
PC = 0x080083ca (hardfault_hang — RT-Thread HardFault 无限循环)
CFSR  = 0x00020000 → Bit17(BFARVALID) + Bit14(PRECISERR) 精确数据总线错误
BFAR  = 0x00000000 → 试图访问地址 0x0
HFSR  = 0x40000000 → Bit30(FORCED) 可配置异常升级为 HardFault
```

Backtrace via OpenOCD GDB:
```
#0  hardfault_hang
#1  <signal handler called>
#2  0x00000000 in ?? ()
#3  _timer_check (timer.c:530)
#4  rt_timer_check (timer.c:771)
```

## 根因

`_timer_list` 链表头部 `.next` 指向 `_bus_thread_objs+260` 处的零填充 BSS 内存。
该内存被误解释为 `struct rt_timer`：
- `timeout_func = NULL (0x0)` — 从未初始化的总线线程槽位
- `init_tick = 0`
- `timeout_tick = 0`

timer 系统遍历链表时调用 `timeout_func(NULL)` → 跳转到 0x0 → PRECISERR。

### BSS 布局依赖性

仅在全量重建（`rm -rf build/`）后出现。原因：
- BSS 段地址随编译产物变化
- `_bus_thread_objs[]` (size=0x500, 8 × rt_thread) 在 BSS 中的位置改变
- 零化内存区域的相对地址与 `_timer_list` 链表指针恰好对齐 → 被误读为链表节点

增量编译不会触发 — 因为 BSS 地址不变。

## 诊断命令

```bash
# 1. 检查 HardFault 状态寄存器
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "set remotetimeout 3" \
  -ex "monitor halt" \
  -ex "p/x *(uint32_t*)0xE000ED28" \   # CFSR
  -ex "p/x *(uint32_t*)0xE000ED2C" \   # HFSR
  -ex "p/x *(uint32_t*)0xE000ED38" \   # BFAR
  -ex "monitor resume" \
  -ex "quit"

# 2. 检查 _timer_list 链表
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "set remotetimeout 3" \
  -ex "monitor halt" \
  -ex "p &_timer_list" \
  -ex "p _timer_list" \
  -ex "monitor resume" \
  -ex "quit"

# 预期输出：_timer_list.next = &_timer_list (空链表)
# 异常输出：_timer_list.next = 0x2000d95c (指向 _bus_thread_objs 区域)

# 3. 检查被损坏 timer 的内容
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "set remotetimeout 3" \
  -ex "monitor halt" \
  -ex "p *(struct rt_timer*)_timer_list.next" \
  -ex "monitor resume" \
  -ex "quit"
# timeout_func = 0x0 → 确认损坏
```

## 修复方法

复位后直接将 `_timer_list` 清空（指向自身）：

```bash
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "set remotetimeout 3" \
  -ex "monitor reset halt" \
  -ex "set {int}&_timer_list = &_timer_list" \
  -ex "set {int}(&_timer_list+1) = &_timer_list" \
  -ex "monitor resume" \
  -ex "quit"
```

修复后验证：
- `setup_stage` 应正常推进（502→630→662）
- CFSR 应为 0x00000000（无故障）

## BSS 地址偏移的补充陷阱

全量重建后**所有 BSS 段符号地址改变**。调试前必须重新查询：

```bash
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep "rtt_dbg_"
```

| 变量 | 旧地址（clean前） | 新地址（clean后） |
|------|-------------------|-------------------|
| `rtt_dbg_setup_stage` | 0x2001bc64 | 0x2001b5c0 |
| `rtt_dbg_main_loop_iterations` | 0x2001997c | 0x200192f0 |
| `rtt_dbg_hal_run_called` | 0x200001c0 | 0x200001c0 (不变 — .data 段) |
| `rtt_dbg_main_loop_entry_called` | 0x200001c8 | 0x200001c8 (不变 — .data 段) |

仅 `.bss` 段变量地址会变；`.data` 段已初始化变量通常不变。

## SPI1 总线线程与 _backend_count

系统在 IOMCU 禁用后推进至 setup_stage=662（`ins.init()` 内的陀螺仪校准阶段）。
即使 `_backend_count=2`, `_gyro_count=2`, `_accel_count=2` 表明传感器探针成功，
陀螺仪校准可能因以下原因挂死：
- SPI1 总线线程存在但未被调度（优先级问题）
- SPI1 读取 IC 的 FIFO 失败（CS 时序或 SPI 配置）
- `_samples_notifier` semaphore 从未被 post
- 原 IOMCU 线程阻塞被移除后暴露了另一条路径的阻塞

诊断：检查 `_samples_notifier.value` 和 SPI1 总线线程的运行状态。
