# main() 未调度诊断 (2026-05-14 发现，2026-05-14 扩展)

## 现象

MCU 运行固件代码（PC 在 0x08xxxxxx），但 ArduPilot `main()` 从未被 RT-Thread 调度：

| 诊断变量 | 值 | 含义 |
|---------|-----|------|
| `rtt_dbg_hal_run_called` | 0xDEADBEEF | `HAL_RTT::run()` 未执行 |
| `rtt_dbg_main_loop_entry_called` | 0xCAFEBABE | `_main_loop_entry()` 未执行 |
| 所有 SPI/CDC 诊断变量 | 0 | 外设未初始化 |

## 硬件断点链诊断（2026-05-14 新增）

```bash
# 查出当前构建的正确符号地址
arm-none-eabi-nm build/rt-thread.elf | grep -E " main$|main_thread_entry|rt_system_scheduler_start"

# 设置三级断点（分别对应调度器启动、main线程入口、main函数）
echo "bp <sched_start_addr> 2 hw" | nc -q 1 localhost 4444
echo "bp <main_thread_entry> 2 hw" | nc -q 1 localhost 4444
echo "bp <main_addr>        2 hw" | nc -q 1 localhost 4444

echo "reset run" | nc -q 1 localhost 4444
sleep 8
echo "halt" | nc -q 1 localhost 4444
echo "bp" | nc -q 1 localhost 4444  # 查哪个断点触发
echo "reg pc" | nc -q 1 localhost 4444
```

### 解读表

| 触发断点 | 未触发断点 | 结论 |
|---------|-----------|------|
| `rt_system_scheduler_start` | `main_thread_entry`, `main` | **调度器已启动，main 线程从未被调度** |
| `main_thread_entry` | `main` | **main 线程已调度但 `rt_components_init()` 阻塞** |
| 无断点触发 | 全部 | **CPU 卡在更早启动阶段** |
| 三个全部触发 | — | **系统完全正常启动** |

## 根因方向

### 可能的原因（按概率排序）

| # | 原因 | 诊断 | 修复 |
|---|------|------|------|
| 1 | **SD 卡挂载阻塞 `rt_components_init()`** — `sdcard_port.c` 的 `sdcard_mount()` 以 `INIT_APP_EXPORT` 注册，在 `main()` 前执行 `dfs_mount("sd0")`。无 SD 卡时无限阻塞。**2026-05-15 硬件断点链验证为最常阻塞点。** | `nm | grep __rt_init_ | sort` 查 `sdcard_mount` 地址 | 注释 `sdcard_port.c:77` 的 `INIT_APP_EXPORT(sdcard_mount)` |
| 2 | **`rt_thread_create()` 返回 NULL** — main 线程需 64KB 连续堆。堆碎片或不足 → 线程创建静默失败。**2026-05-15 实测：64KB→4KB 后 `main_thread_entry` 断点命中✅** | 检查 nm | grep rt_application_init → 反汇编看 rt_thread_create 返回值 | 减 `RT_MAIN_THREAD_STACK_SIZE` 到 4096（测试值，后续需增大） |
| 3 | **USB CDC init 阻塞** — `cherryusb.c` 的 INIT_COMPONENT_EXPORT。**2026-05-15 验证不是主因**但可能叠加 | 注释 `cherryusb.c:133` 试烧 | 恢复 CDC 到 main() 后异步初始化 |
| 4 | **堆不足/位置错误** — 修改 `.config` 后堆区域不足 | 检查 heap 起始与结束地址 | 调整 SRAM 布局 |

### `rt_thread_create` NULL 返回诊断

```bash
# 检查当前配置
grep RT_MAIN_THREAD_STACK_SIZE build/rtt_deploy/cuav_v5/rtconfig.h

# 临时减到 4096 验证
# 编辑 libraries/AP_HAL_RTT/hwdef/common/.config
# CONFIG_RT_MAIN_THREAD_STACK_SIZE=65536 → 4096
```

参考：CUAV V5 SRAM 512KB, BSS~276KB + data~5KB + stack(48KB) = ~329KB, 剩余~183KB 给堆。64KB 单次分配应足够，但碎片化可能导致失败。
