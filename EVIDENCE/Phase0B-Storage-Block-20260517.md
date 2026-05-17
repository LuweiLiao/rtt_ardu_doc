---
milestone: "Phase 0B Storage::_flash_load Blocking"
date: 2026-05-17
status: "ACTIVE_BLOCKER"
next_action: "GDB 跟踪 AP_FlashStorage::init() 调用栈"
---

# Phase 0B — Storage::_flash_load 阻塞

## 问题描述

P0 堆耗尽修复后，固件启动到 `HAL::run()` ✅，USB 枚举成功 ✅，
但卡在 `Storage::_flash_load()`，`setup_stage=502 (try Flash)`。

## 证据

```
(gdb) p/x rtt_dbg_hal_run_called
$1 = 0xBBBBBBBB              ✅  HAL::run() 已到达

(gdb) p/x rtt_dbg_setup_stage
$2 = 0x1f6 = 502              ❌  卡在 setup_stage=502

(gdb) p/x rtt_dbg_main_loop_iterations
$3 = 0x0                      确认主循环未进入
```

## 调用链

```
AP_Vehicle::setup()                           # AP_Vehicle.cpp
  → Storage::_flash_load()                    # Storage.cpp:175
    → _flash.init()                           # Storage.cpp:179 (调用 AP_FlashStorage)
      → AP_FlashStorage::init()               # AP_FlashStorage.cpp:47
        → ??? (挂起点)
```

## 配置

- hwdef.dat line 192: `define STORAGE_FLASH_PAGE 10`
- Storage.h: `#define STORAGE_FLASH_PAGE 10`
- 存储页 10 对应 flash 扇区

## 可能原因

1. **Flash 驱动未完全初始化** — RTT 的 flash 设备框架可能未正确注册
2. **AP_FlashStorage 内部 while 循环** — erase/write 等待操作永远不完成
3. **SPI flash 未就绪** — 如果使用外部 SPI flash 存储而不是内部 flash
4. **flash 写入时序问题** — 在 RTT 环境下 flash 擦除/写入需要重新验证 timing

## 下一步诊断

- [ ] GDB 跟踪 `AP_FlashStorage::init()` 精确栈帧
- [ ] 检查 flash 设备是否已通过 RT-Thread 设备框架注册
- [ ] 确认 `STORAGE_FLASH_PAGE=10` 映射到正确的 flash 扇区地址
- [ ] 检查 ChibiOS 参考实现中 flash 存储初始化的流程
