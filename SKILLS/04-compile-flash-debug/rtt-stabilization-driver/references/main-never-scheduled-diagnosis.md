# main() 未调度诊断 (2026-05-14 发现)

## 现象

MCU 运行固件代码（PC 在 0x08xxxxxx），但 ArduPilot `main()` 从未被 RT-Thread 调度：

| 诊断变量 | 值 | 含义 |
|---------|-----|------|
| `rtt_dbg_hal_run_called` (0x200001c0) | 0xDEADBEEF | `HAL_RTT::run()` 未执行 |
| `rtt_dbg_main_loop_entry_called` (0x200001c8) | 0xCAFEBABE | `_main_loop_entry()` 未执行 |
| `rtt_dbg_main_loop_iterations` (0x20019980) | 0 | 主循环未启动 |
| 所有 SPI/CDC 诊断变量 | 0 | 外设未初始化 |

## 诊断方法

### 1. 确认 ELF 中 `main` 符号

```bash
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep " T main$"
# → 0801243c T main
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf -f 0x0801243c
# → main
# → /data/firmare/pogo-apm/ArduCopter/Copter.cpp:983  (AP_HAL_MAIN_CALLBACKS)
```

### 2. 确认 `HAL_RTT::run()` 反汇编正常

```bash
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep HAL_RTT.*run
# → 0806d024 T _ZNK7HAL_RTT3runEiPKPcPN6AP_HAL3HAL9CallbacksE
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf | sed -n '/0806d024.*HAL_RTT.*run/,/^$/p'
# 应包含: mov.w r3, #2863311530  (0xAAAAAAAA) — 写入 hal_run_called
#         movs r3, #20            — 优先级降至 20 (ChibiOS 式 setup 降级)
#         blx r3                  — 调用 setup()
#         movs r3, #5             — 优先级恢复
```

### 3. 确认 Reset_Handler 正确

```bash
# 检查 startup 代码
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf | sed -n '/080f01e0.*Reset_Handler/,/^$/p' | tail -10
# 应包含: bl 80ff87e <entry>  — 调用 RT-Thread 入口
Arm-none-eabi-addr2line -e ... -f 0x080ff87e
# → entry
# → modules/rt-thread/src/components.c:166  — rtthread_startup()
```

### 4. 读取向量表验证固件完整性

```bash
echo "mdw 0x08008000 4" | nc -q 2 localhost 4444
# 第一个字 = SP 初始值（如 0x200054bc）
# 第二个字 = Reset_Handler 地址（Thumb 模式，末位=1）
```

### 5. 多种 halt 尝试

```bash
# 方法 A: reset halt → 检查 bootloader 阶段
echo "reset halt" | nc -q 2 localhost 4444
echo "reg pc" | nc -q 2 localhost 4444
# → PC=0x08000200 (bootloader) 正常

# 方法 B: 跑几秒后 halt → 检查阶段
echo "reset run" | nc -q 1 localhost 4444
sleep 6
echo "halt" | nc -q 1 localhost 4444
echo "reg pc" | nc -q 2 localhost 4444
# → 如果 PC 在 0x08xxxxxx 但所有 diag var 为初始值 → main() 未调度
arm-none-eabi-addr2line -e rt-thread.elf -f <PC>
# → rt_list_remove / rt_schedule / idle 等 RT-Thread 内核函数
```

## 根因方向

### 可能的原因（按概率排序）

| # | 原因 | 诊断 | 修复 |
|---|------|------|------|
| 1 | **rt_components_init() 中 auto-init 函数阻塞** — 即使 submodule 完全干净、仅保留8个基线文件，auto-init 函数表中的某函数挂起，main_thread_entry() 永不抵达 main() | 见下方 Auto-init 阻塞诊断 | 定位并按需禁用阻塞的 auto-init 函数 |
| 2 | **submodule 修改导致 rt_components_init 挂起** — modules/rt-thread 中的 CDC 自愈补丁 (usbd_serial.c) 在 INIT_DEVICE_EXPORT 阶段阻塞 | git diff modules/rt-thread 查看 | cd modules/rt-thread && git checkout -- . |
| 3 | **RT-Thread 优先级反转** — main 线程优先级低于 timer/UART 线程 | 检查 RT_MAIN_THREAD_PRIORITY 配置 | 提高 main 线程优先级 |
| 4 | **链接顺序错误** — BSP 的 LED blinker applications/main.c 被优先链接 | nm ... | grep " T main$" 检查来源 | 确保 AP_HAL_MAIN_CALLBACKS 的 main 被链接 |

### Auto-init 阻塞诊断（2026-05-14 新增 — 2026-05-15 扩展：SD 卡挂载是主因）

**前提**：submodule 完全干净（最佳版本 `d8e850724e`），只有必需的8个基线文件被修改，但 `main()` 仍不被调度。PC 在 `idle_thread_entry` 或 `rt_list_remove`。

**调用链**：
```
Reset_Handler → entry() → rtthread_startup() → rt_system_scheduler_start()
  ↓ 调度器启动 → 选择 main 线程（hw bp ✅ → main_thread_entry）
main_thread_entry() → rt_components_init() → [auto-init 函数表] → ...
  ↓ 如果 auto-init 表中有函数挂起 → main() 永不执行（hw bp ❌ → main）
main() → hal.run() → HAL_RTT::run() → _main_loop_entry()
```

**关键**：`rt_components_init()` 遍历 `__rt_init_rti_board_end` 到 `__rt_init_rti_end` 之间的函数指针，按 `INIT_` 级别顺序逐一调用。**任何一个挂起都会阻塞后续所有函数和 `main()`。**

---

#### ⚡ 首要阻塞点：SD 卡挂载（2026-05-15 硬件断点链确认）

**2026-05-15 三级硬件断点链验证**：
1. `main_thread_entry` 断点 ✅ 命中（主线程被调度）
2. `main()` 断点 ❌ 不命中（被 `rt_components_init()` 阻塞）
3. `sdcard_mount` 和 `sdcard_remount` 使用 `INIT_APP_EXPORT`（级别6），在 `INIT_COMPONENT_EXPORT` 之后

**实际阻塞机制**：
- `sdcard_port.c` 中的 `sdcard_mount()` 调用 `dfs_mount("sd0", "/", "elm")`
- 当无 SD 卡插入时，`dfs_mount` 内部等待 SDIO 检测完成，**无限阻塞**
- 因 `INIT_APP_EXPORT`（级别 6）排在 `INIT_COMPONENT_EXPORT`（级别 4，含 `rtt_run_cpp_ctors`）之后 → 在 C++ 构造器后、`main()` 前执行

**诊断方法**：

```bash
# 列出 auto-init 函数（按执行顺序 = 地址升序）
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep __rt_init_ | sort
# 输出示例：
# 0814a0e8 T __rt_init_rti_board_end
# 0814a0ec T __rt_init__sensor_power_init
# 0814a0f0 T __rt_init_dfs_init
# 0814a0f4 T __rt_init_rt_mmcsd_core_init
# 0814a0f8 T __rt_init_rt_hw_sdio_init
# 0814a0fc T __rt_init_spi_device_board_init
# 0814a100 T __rt_init_rtt_run_cpp_ctors
# ...
# 0814a120 T __rt_init_sd_card_mount_sync        # ⚠️ INIT_APP_EXPORT 级别！
# 0814a124 T __rt_init_sdcard_remount            # ⚠️ 另一个阻塞点！
# 0814a128 T __rt_init_sdcard_mount              # ⚠️ dfs_mount("sd0") 无限阻塞！
# 0814a12c T __rt_init__cpu_idle_monitor_init
# 0814a130 T __rt_init_finsh_system_init
# 0814a134 T __rt_init_rti_end
```

**关键诊断发现**：`sdcard_mount` 在函数表中的位置在 `rtt_run_cpp_ctors` **之后**，但在 `main()` **之前**。`INIT_APP_EXPORT` 确实在 `INIT_COMPONENT_EXPORT` 之后执行。

#### 阻塞点优先级诊断步骤（2026-05-15）

**步骤 A：硬件断点链**（首选 — 不依赖诊断变量）

```bash
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep -E "main_thread_entry| main$|rt_system_scheduler_start"
# 例：rt_system_scheduler_start=0x080ff738, main_thread_entry=0x080ff6e4, main=0x080fadfd

echo -e "reset halt\nbp 0x080ff6e4 2 hw\nbp 0x080fadfd 2 hw\nresume" | nc -q 2 localhost 4444 | strings
sleep 10
echo -e "halt\nbp\nreg pc" | timeout 20 nc localhost 4444 2>&1 | strings
# ✅ main_thread_entry 命中（第1断点）
# ❌ main 未命中（第2断点）
# → 结论：main() 被 rt_components_init() 中的 auto-init 函数阻塞
```

**步骤 B：依次禁用 auto-init 函数定位阻塞点**

从最可疑的开始，每次禁用一个，编译烧录验证 `main()` 是否命中。重要：**sdcard_port.c 在 submodule 中**！

```bash
# 文件路径（submodule 内）：
# modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/ports/sdcard_port.c

# 禁用的行（使用行号注释）：
# line 77:  INIT_APP_EXPORT(sdcard_mount);           → 主要阻塞点
# line 96:  INIT_APP_EXPORT(sdcard_remount);          → 次要阻塞点
# line 81:  INIT_APP_EXPORT(sd_card_mount_sync);      → 可能也需注释

# 注意：这些修改在 submodule 工作区，不会被 git add 跟踪（主仓库只跟踪 submodule 的 commit 指针）
```

**2026-05-15 实测结果**：

| 禁用组合 | main() 断点 | 结论 |
|---------|------------|------|
| 仅禁用 USB CDC init (cherryusb.c) | ❌ 仍不命中 | USB CDC 不是主因 |
| 禁用 SD card mount (sdcard_port.c lines 77,96) | ✅ **命中！** | **SD 卡挂载是主要阻塞点** |
| 只禁用 sdcard_mount（保留 sdcard_remount） | ✅ 命中 | `sdcard_mount` 是具体阻塞的函数 |

> **注意**：2026-05-14 session 中错误推断 USB CDC init 是主因——因`INIT_COMPONENT_EXPORT`（级别 4）的优先级比`INIT_APP_EXPORT`（级别 6）高，其实际在 auto-init 表中排在 `rtt_run_cpp_ctors`(级别4) 之后。而 `sdcard_mount` 是 `INIT_APP_EXPORT`(级别6)，排在最后。**两次排列顺序虽然不同，但影响相同：都在 `main()` 之前执行。**

**步骤 C：验证 `main()` 进入后优先级降低策略是否生效**

```bash
# 确认 hal_run_called 和 main_loop_entry
echo -e "halt\nmdw <rtt_dbg_hal_run_called> 1\nmdw <rtt_dbg_main_loop_entry_called> 1\nresume" | nc -q 2 localhost 4444
# rtt_dbg_hal_run_called=0xBBBBBBBB ✅  → HAL_RTT::run() 进入了
# rtt_dbg_main_loop_entry_called=0x12345678 ✅ → _main_loop_entry() 进入了

# 如果 hal_run_called 已设置但 main_loop_entry 初始 → setup() 极慢
# 如果两个都已设置 → setup() 执行中或已完成
```

#### 后续诊断：setup() 极慢（2026-05-15 新发现）

SD 卡阻塞解决后，`main()` 和 `_main_loop_entry()` 成功进入，但 `setup()` 极慢（2+ 分钟仍在 `_delay_microseconds_dwt` 忙等中）：

- `rtt_dbg_hal_run_called=0xBBBBBBBB` ✅
- `rtt_dbg_main_loop_entry_called=0x12345678` ✅
- `rtt_dbg_fast_loop_count=0`（setup 未完成）
- `rtt_dbg_main_loop_iterations=0`（setup 未完成）
- PC 反复在 `_delay_microseconds_dwt` (DWT 忙等)

**根因**：优先级降低策略（setup_priority=20）·降低了 main 线程优先级，每次 `delay(5)` 的 DWT 忙等被高优先级线程（UART=6, Timer=4）频繁打断，wall-clock 耗时远超 5µs。

**关键发现**：DWT CYCCNT 忙等循环的 while 条件 `(DWT_CYCCNT - start) < cycles` 中的 DWT_CYCCNT 在 RT-Thread 调度下可能因中断/上下文切换而意外推进。但 **DSB 屏障保证了每个循环迭代的 CYCCNT 读值是最新的**，所以实际忙等不会无限期卡住——但 wall-clock 时间膨胀是优先级调度的结果。

#### 诊断步骤（通用补充）

**步骤 0：检查所有 auto-init 函数列表**

```bash
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep __rt_init_ | sort
```

**步骤 1：检查强可疑函数**

| 函数 | 级别 | 文件 | 阻塞风险 | 原因 |
|------|------|------|---------|------|
| `sd_card_mount_sync` / `sdcard_mount` | APP (6) | `sdcard_port.c` | **极高** ✅ 已验证 | `dfs_mount("sd0")` 无 SD 卡时无限阻塞 |
| `sdcard_remount` | APP (6) | `sdcard_port.c` | 高 | 同上模式 |
| `rt_hw_cherryusb_cdc_init` | COMPONENT (4) | `cherryusb.c` | 中 | DWC2 AHBIDL 等待 + chardev init，但**已验证非主因** |
| `rtt_run_cpp_ctors` | COMPONENT (4) | `rt_board_init.c` | 中 | C++ 构造器可能含 malloc |
| `sdio_enable` | DEVICE (3) | `stm32f7xx_hal_msp.c` | 低 | 仅使能 SDIO 时钟 |

#### 修复方向（按优先级）

| # | 方案 | 描述 | 工作 | 代价 |
|---|------|------|-----|------|
| 1 | ✅ **禁用 SD card auto-init** | 在 `sdcard_port.c:77` 和 `:96` 注释 `INIT_APP_EXPORT` 行 | ✓ 已验证，main() 进入 | 文件系统不可用，SD 卡日志无法记录 |
| 2 | 禁用 USB CDC auto-init（可选） | 在 `cherryusb.c:133` 注释 `INIT_COMPONENT_EXPORT` | ✓ 已验证非必需 | CDC 需在 main() 后手动初始化 |
| 3 | SD card mount 加超时 | 重构 `sdcard_mount()` 使用 DWT CYCCNT 超时 | 中等 | 超时后需优雅降级 |
| 4 | 移到 main() 后异步挂载 | 用 `rt_work_submit` 在 main() 初始完成后挂载 | 较大改造 | 需要额外工作队列 |

**当前方案（已验证）**：方案 1 + 2 = main() 进入 + _main_loop_entry() 运行但 setup() 极慢。代价是启动阶段无 USB CDC 输出、SD 卡不可用，可在 system 稳定后逐步恢复。后续需解决 setup() 优先级导致的 DWT 忙等问题。

### 排除检查清单

- [ ] main 符号在 ELF 中存在且来自 Copter.cpp
- [ ] 向量表正确（SP + Reset_Handler 有效）
- [ ] HAL_RTT::run() 反汇编包含预期的写操作
- [ ] submodule 无脏修改（git diff modules/rt-thread）
- [ ] CFSR=0, HFSR=0（无 HardFault）
- [ ] PC 在固件代码区域（0x08xxxxxx），不在 idle_thread_entry 或 rt_list_remove
- [ ] reset run 后多等 6s 再 halt（给 RT-Thread 启动足够时间）
- [ ] 如果 PC 持续在 idle 线程且 submodule 干净 → 检查 auto-init 阻塞
