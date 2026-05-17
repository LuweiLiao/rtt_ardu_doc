# IWDG 启动时机对齐 ChibiOS 修复记录

## 问题

RTT ArduPilot 在 `reset run` + 10-15 秒后系统自动复位，`hal_run_called` 回到初始值 0xDEADBEEF。`RCC_CSR` 显示 SFTRSTF=1 （软件复位），非 IWDG 复位。

## 诊断过程

### 逐层 GDB 断点跟踪（完整路径）

```
Reset_Handler → entry() → rtthread_startup() → rt_application_init()
→ main_thread_entry → rt_components_init()
  → _sensor_power_init ✅ → dfs_init ✅ → ... → rtt_run_cpp_ctors ✅
→ main() ✅ → HAL_RTT::run() (hal_run_called=0xAAAAAAAA) ✅
  → scheduler->init() ✅ → gpio->init() ✅
  → Shared_DMA::init() ✅ → serial(0)->begin(921600) ✅
  → analogin->init() ✅ → hal_run_called=0xBBBBBBBB ✅
→ _main_loop_entry() → set_main_thread_id ✅
  → rt_thread_control(MAIN_PRIORITY) ✅
  → rt_thread_control(STARTUP_PRIORITY) ✅
  → hal_initialized() → timer/uart/io 线程启动 ✅
  → callbacks->setup() → AP_Vehicle::setup → AP_Param::setup_sketch_defaults ✅
    → ... setup 正常运行 ...
```

### 关键发现

1. **GDB 断点全部命中** — run() 完整执行、setup() 被进入
2. **`reset run` + 10s → halt → 0xDEADBEEF** — 系统复位发生在自由运行模式下
3. **`set_system_initialized()` 在 RTT 中同时做两件事**：
   - 设 `_initialized = true`（释放 rcout 等等待线程）
   - 启动 IWDG
4. **ChibiOS 的 `set_system_initialized()` 只设 `_initialized=true`**（`Scheduler.cpp:644-651`）
5. **ChibiOS 的 IWDG 启动通过 `stm32_watchdog_init()` 在 `main_loop()` 中独立调用**（`HAL_ChibiOS_Class.cpp:290-299`）

## 修复

### 修改文件（3 个）

| 文件 | 修改 | ChibiOS 参考 |
|------|------|-------------|
| `Scheduler.h` | 添加 `watchdog_init()` 公开方法 | — |
| `Scheduler.cpp` | 提取 IWDG 启动代码 → `watchdog_init()`；`set_system_initialized()` 仅设 `_initialized=true` | `watchdog.c:79-87` |
| `HAL_RTT_Class.cpp` | 在 `_main_loop_entry()` 的 `setup()` 前调用 `watchdog_init()` + `watchdog_pat()` | `HAL_ChibiOS_Class.cpp:290-307` |

### 核心逻辑变更

```diff
- _main_loop_entry → hal_initialized → setup() → set_system_initialized(含IWDG) → main loop
+ _main_loop_entry → hal_initialized → watchdog_init() + watchdog_pat() → setup() → set_system_initialized(仅flag) → main loop
```

## 验证结果

### `reset run` + 30 秒自由运行后 halt

| 变量 | 地址 | 修改前(10s) | 修改后(30s) |
|------|------|-------------|-------------|
| `rtt_dbg_hal_run_called` | 0x200001c8 | **0xDEADBEEF** ❌ | **0x11111111** ✅ |
| `rtt_dbg_main_loop_entry_called` | 0x200001d0 | **0xCAFEBABE** ❌ | **0x12345678** ✅ |
| `rtt_dbg_setup_stage` | 0x200264fc | **0** ❌ | **651** ✅ |
| `rtt_dbg_fast_loop_count` | 0x20023d60 | **0** ❌ | **144** ✅ |
| `RCC_CSR` | 0x40023874 | 0x14000003 (SFTRSTF=1) | **0x00000003** (SFTRSTF=0) ✅ |

### 60 秒连续运行

| 指标 | 结果 |
|------|------|
| USB CDC 枚举 | ✅ 1 秒内枚举 |
| 系统复位 | **无** — 稳定运行 60+ 秒 |
| setup() 完成 | ✅ (stage 651 → 670 → 680) |
| main loop | ✅ 144 次迭代 |
| 看门狗复位 | **0** (RCC_CSR IWDGRSTF=0) |

## 残留问题

只有 **CherryUSB CDC IN TX** 不通（已诊断根因：DIEPMSK 缺 TOM 位，无 ChibiOS 的 SOF 钩子恢复机制）。需应用 TOC 修复（参照 `hal_usb_lld.c:965` 加 `DIEPMSK |= TOCM` + TOC 中断处理）。

## Git 记录

- Commit: `1eaf537bcb` — "IWDG: align init timing with ChibiOS — start before setup()"
- 已推送到 `gitee-pogo:pogouav/ardupilot.git` branch `rtt-cuav-v5`
