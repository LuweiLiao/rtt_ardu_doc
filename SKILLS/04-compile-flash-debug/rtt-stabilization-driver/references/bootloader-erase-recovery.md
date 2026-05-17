# Bootloader 擦除恢复过程（2026-05-13 session）

## 背景

CUAV V5 使用 PX4 兼容 bootloader，位于 flash sector 0（0x08000000-0x08007FFF）。
这个 bootloader **不在 ArduPilot 的 `Tools/bootloaders/` 目录中**。

## 误擦除场景

```bash
# ❌ 这条命令会擦除 bootloader！
flash erase_sector 0 0 11

# ✅ 只擦除固件区域，保留 bootloader
flash erase_sector 0 1 11
```

## 恢复方法

### 方法 A：固件直接放到 0x08000000（跳过 bootloader）

1. 修改 `libraries/AP_HAL_RTT/hwdef/cuav_v5/hwdef.dat`：
   `FLASH_RESERVE_START_KB 0`    （原 32）

2. 同步更新 VTOR：
   - `startup_rtt_override.S` 中 `ldr r0, =0x08008000` → 改为 `ldr r0, =vflash_start`
   - 在 link.lds 模板中添加 `PROVIDE(vflash_start = ORIGIN(ROM));`

3. Clean rebuild + 烧录到 0x08000000：
   ```bash
   printf 'reset halt\nflash write_bank 0 /path/rtthread.bin 0x0\nreset run\n' | nc ...
   ```

4. **注意事项**：
   - 跳过 bootloader = DFU/串口升级不可用（调试专用）
   - VTOR 必须指向正确的新地址

### 方法 B：从同型号板读取 bootloader

```bash
# 目标板
flash read_bank 0 cuav-v5-bl.bin 0x0 0x8000
# 恢复
program cuav-v5-bl.bin 0x08000000
```

### 方法 C：从 PX4 Firmware 仓库提取

CUAV V5 bootloader 来自 PX4 Firmware，可从 `ROMFS/px4fmu_common/` 获取。

## VTOR一致性

VTOR 必须等于链接脚本 ROM ORIGIN。建议用 PROVIDE symbol：
```asm
ldr r0, =vflash_start    ; 由 link.lds 定义
PROVIDE(vflash_start = ORIGIN(ROM));
```
