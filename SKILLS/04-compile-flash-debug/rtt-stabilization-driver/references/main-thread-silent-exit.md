# 主线程未启动诊断

## 现象

烧录运行后：
- `rtt_dbg_hal_run_called` = **0xDEADBEEF**（初始值，从未被写入）
- `rtt_dbg_main_loop_entry_called` = **0xCAFEBABE**（初始值，从未被覆写为 0x12345678）
- `rtt_dbg_main_loop_iterations` = **0**
- `rtt_dbg_setup_stage` = **0**（setup 从未推进）
- PC 在 idle 线程（`idle_thread_entry` 或 `_thread_exit` 中）

系统跑在 idle 线程，说明 RT-Thread 调度器正常工作，但主线程从未运行或提前退出。

## 根因对照表

| 状态 | 可能根因 | 验证方式 |
|------|---------|---------|
| `hal_run_called`=0xDEADBEEF, 系统在 idle | **AP_CHECK_FIRMWARE 校验失败** | 回退 AP_CheckFirmwareDefine.h、link.lds 中 `.app_descriptor`、rtt_hwdef.py 的 `AP_CHECK_FIRMWARE_ENABLED=1` |
| `hal_run_called`=0xDEADBEEF, 系统在 idle | **BSP main.c 被编译** | 确认 build 日志中 `applications/main.c` 被 `continue` 跳过 |
| `hal_run_called`=0xBBBBBBBB, 但 iter=0, PC 在 DWT 忙等 | **定时器线程饿死主线程** | 检查优先级：timer=4, main=10（timer 更高）。此时 `setup_stage`=620（完成） |
| `hal_run_called`=0xBBBBBBBB, iter=0, PC 在 ADC DMA 相关 | **DMA 缓冲区地址在 DTCM** | `nm` 检查 `_adc_dma_buf` 地址是否 ≥ 0x20020000 |

## 详细流程：AP_CHECK_FIRMWARE 校验失败

ArduPilot 的 `AP_CheckFirmware` 模块在 `setup()` 早期运行固件签名校验。当：

1. `link.lds` 添加了 `.app_descriptor` 段
2. `rtt_hwdef.py` 设置了 `AP_CHECK_FIRMWARE_ENABLED=1`
3. `AP_CheckFirmwareDefine.h` 添加了 RTT 平台的校验定义

在校验发现 signature/CRC/board_id 不匹配时，`check_firmware_sig()` 会返回 **`FWCHECK_BOARD_MISMATCH`** 或 `FWCHECK_BAD_CRC`，导致 `setup()` 提前 return。

A 空返回的 `setup()` → `_main_loop_entry()` 继续 → 但随后卡在 `for(;;) { loop(); ... }` 之前设置的 `set_system_initialized()` 等标记处。实际结果是整个主线程静默退出。

**临时修复**：回退上述 3 个文件到已知 work 的基线版本。

**正确修复**：实现正确的固件签名生成和校验逻辑：
1. 编译时计算 `.app_descriptor` 的签名
2. 将签名写入固件头部（类似 ChibiOS 的 `apj_tool.py`）
3. 确保 bootloader 和应用层的签名一致
