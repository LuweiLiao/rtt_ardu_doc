# SDIO/SD卡 基础设施 — CUAV V5 (STM32F767)

## 文件结构

```
RTT BSP (子模块, 修改谨慎):
  modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/
    ├── board/ports/sdcard_port.c       # 显式调用 rt_hw_sdio_init() + 挂载(与rt_board_init.c重复)
    ├── board/CubeMX_Config/Src/stm32f7xx_hal_msp.c  # HAL_SD_MspInit() — GPIO/时钟/DMA中断
    ├── packages/stm32f7_hal_driver-latest/
    │   ├── Inc/stm32f7xx_hal_sd.h      # HAL SD API
    │   ├── Inc/stm32f7xx_ll_sdmmc.h    # LL SDMMC 寄存器
    │   └── Inc/stm32f7xx_hal_mmc.h     # MMC (eMMC) 支持
    └── .config                          # BSP_USING_SDIO=y, RT_USING_DFS=y, RT_USING_DFS_ELMFAT=y

RTT HAL_Drivers (通用STM32):
  modules/rt-thread/bsp/stm32/libraries/HAL_Drivers/drivers/
    ├── drv_sdio.c                       # 主SDIO驱动 — rt_hw_sdio_init()
    ├── drv_sdio.h                       # SDCARD_INSTANCE=SDMMC1, 缓冲大小定义
    ├── config/f7/sdio_config.h          # SDIO_BUS_CONFIG — SDMMC1 + DMA2_Stream3/6
    └── SConscript                       # 编译条件: BSP_USING_SDIO

ArduPilot HAL RTT (主仓库):
  libraries/AP_HAL_RTT/hwdef/common/board/
    ├── rt_board_init.c                  # sd_card_mount_sync() — PG7供电 + 后台挂载重试
    └── board.h                          # SPI DMA 重映射定义

ArduPilot HAL RTT 板级配置:
  libraries/AP_HAL_RTT/hwdef/cuav_v5/
    └── hwdef.dat                        # PG7 VDD_3V3_SD_CARD_EN OUTPUT HIGH
```

## 依赖链

```
BSP_USING_SDIO=y
  → SConscript 编译 drv_sdio.c
  → drv_config.h 包含 f7/sdio_config.h → SDIO_BUS_CONFIG 宏可用
  → drv_sdio.c 使用 SDIO_BUS_CONFIG 初始化 SDMMC1 + DMA

RT_USING_DFS=y + RT_USING_DFS_ELMFAT=y (已在 .config)
  → DFS 和 FATFS 可用

HAL_SD_MspInit() (在 CubeMX_Config 中已实现)
  → __HAL_RCC_SDMMC1_CLK_ENABLE()
  → GPIO 配置: PC8-12(D0-D3/CLK) + PD2(CMD) — 全部 AF12
  → __HAL_RCC_DMA2_CLK_ENABLE()
  → NVIC: SDMMC1_IRQn, DMA2_Stream3_IRQn, DMA2_Stream6_IRQn

sdcard_port.c 中的显式 rt_hw_sdio_init() 调用
  → 防止 --gc-sections 丢弃 INIT_DEVICE_EXPORT 注册的驱动
```

## CUAV V5 SDMMC1 引脚

| 引脚 | 功能 | AF | DMA | 备注 |
|------|------|----|-----|------|
| PC8  | SDMMC1_D0  | AF12 | — | 4-bit data bus |
| PC9  | SDMMC1_D1  | AF12 | — | |
| PC10 | SDMMC1_D2  | AF12 | — | |
| PC11 | SDMMC1_D3  | AF12 | — | |
| PC12 | SDMMC1_CLK | AF12 | — | 时钟，最高 48MHz |
| PD2  | SDMMC1_CMD | AF12 | — | 命令线 |
| PG7  | VDD_3V3_SD_CARD_EN | GPIO | — | HIGH=供电, 引脚号103 (6*16+7) |

## DMA 分配

| 外设 | DMA 流 | 通道 | 方向 | 优先级 |
|------|--------|------|------|--------|
| SDMMC1 | DMA2_Stream3 | Ch4 | RX | 3 |
| SDMMC1 | DMA2_Stream6 | Ch4 | TX | 3 |
| SPI1 | DMA2_Stream2 | Ch3 | RX | (SPI1重映射) |
| SPI1 | DMA2_Stream5 | Ch3 | TX | (SPI1重映射) |
| SPI4 | DMA2_Stream0 | Ch4 | RX | (默认) |
| SPI4 | DMA2_Stream1 | Ch4 | TX | (默认) |

> **注意**: SPI1 被重映射到 DMA2_Stream2/5 以避开 SDMMC1 的 DMA2_Stream3/6。  
> SPI4 使用默认 Stream0/1，Channel_4 与 SDMMC1 一致但流不同，无冲突。

## 挂载路径

`rt_board_init.c` 的 `sd_card_mount_sync()` (INIT_ENV_EXPORT 级别)：
1. PG7 拉高供电 SD 卡
2. 自动重试挂载 (60 次 × 500ms = 30s)
3. 挂载到 `/` (根目录) → 创建 `/APM/`, `/APM/LOGS/`, `/APM/TERRAIN/`, `/APM/STORAGE/`
4. 无 SD 卡时不阻塞启动

`sdcard_port.c` 的 `sdcard_mount()` (INIT_APP_EXPORT 级别)：
1. 也拉高 PG7 (重复)
2. 显式调用 `rt_hw_sdio_init()` → **关键**: 防止 --gc-sections 丢弃驱动
3. 挂载到 `/sdcard` (与 `rt_board_init.c` 不同路径，建议统一)

## ⚠️ .config 源文件陷阱（2026-05-10 发现） 复制 `libraries/AP_HAL_RTT/hwdef/common/.config` 至部署目录，**不是** `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/.config`。

两个 `.config` 文件内容不同，且彼此独立维护。如果在 RTT BSP 的 `.config` 中启用了某个功能（如 SDIO），但 `hwdef/common/.config` 中未启用，**部署后的固件不会包含该功能**。

### 诊断方法

当怀疑某个 Kconfig 选项未生效时：

```bash
# 1. 检查部署后的 rtconfig.h 是否有该 define
grep 'BSP_USING_SDIO' build/rtt_deploy/cuav_v5/rtconfig.h

# 2. 检查部署后的 .config
grep 'BSP_USING_SDIO' build/rtt_deploy/cuav_v5/.config

# 3. 检查部署前的 .config（模板源）
grep 'BSP_USING_SDIO' libraries/AP_HAL_RTT/hwdef/common/.config

# 4. 对比 RTT BSP 的 .config（参考/目标）
grep 'BSP_USING_SDIO' modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/.config
```

### 根因判断

| 检查点 | 含义 |
|--------|------|
| RTT BSP `.config` **有**该选项 | 该功能在 RTT 层面上本该启用 |
| `hwdef/common/.config` **无**该选项 | ❌ **根因** — 模板源未同步 |
| 部署后 `.config` **无**该选项 | 确认部署系统未从其他来源补充配置 |
| 部署后 `rtconfig.h` **无**该选项 | 确认 `_simple_config_to_header()` 转换正确（CONFIG_前缀已剥离） |

### 修复方法

直接编辑 `libraries/AP_HAL_RTT/hwdef/common/.config`：

```bash
# 将 "is not set" 改为 "=y"
# 注意保留 CONFIG_ 前缀（转换器会自动剥离）
```

### SDIO 三件套（必须同时设置）

```kconfig
CONFIG_RT_USING_SDIO=y          # RTT SDIO 框架（dev_sdio.c, dev_mmcsd_core.c）
CONFIG_BSP_USING_SDIO=y         # STM32 BSP SDIO 驱动（drv_sdio.c, HAL_SD_MspInit）
CONFIG_RT_USING_DFS_ELMFAT=y    # ELM FAT 文件系统（dfs_mount "elm"）
```

三者缺一不可。缺少 `RT_USING_SDIO` → RTT SDIO 框架不编译 → `dev_mmcsd_core.c` 不注册 `sd0` 块设备 → `rt_device_find("sd0")` 返回 NULL → 挂载失败。

## ELF 符号验证（编译后必查）

不要假设 `#ifdef` 保护的代码已链接。用 `nm` 确认：

```bash
# 检查 SD 卡挂载函数
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep sd_card_mount
# 预期: 080xxxxx t sd_card_mount_sync

# 检查 SDIO 驱动
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rt_hw_sdio
# 预期: 080xxxxx T rt_hw_sdio_init

# 检查 DFS mount 函数
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep dfs_mount
# 预期: 080xxxxx T dfs_mount

# 如果缺失 → 检查 .config 中对应的宏是否 =y
```

**常见缺失模式**：
- `sd_card_mount_sync` 缺失 → `BSP_USING_SDIO` 未设置（`#ifdef BSP_USING_SDIO` 保护）
- `rt_hw_sdio_init` 缺失 → `BSP_USING_SDIO` 或 `RT_USING_SDIO` 未设置
- `dev_mmcsd_core` 相关函数缺失 → `RT_USING_SDIO` 未设置
- `dfs_mount` 缺失 → `RT_USING_DFS` 或 `RT_USING_DFS_ELMFAT` 未设置

## 已知问题

### ⚠️ 重复: sdcard_port.c 与 rt_board_init.c

`sdcard_port.c` 和 `rt_board_init.c` 都做：
- PG7 供电（重复，无害）
- DFS 挂载（到不同路径：`/sdcard` vs `/`）

**建议修复**：精简 `sdcard_port.c`，仅保留：
1. 显式 `rt_hw_sdio_init()` 调用（防止 --gc-sections）
2. 移除 PG7 供电（由 `rt_board_init.c` 负责）
3. 移除 dfs_mount（由 `rt_board_init.c` 负责）

### ⚠️ --gc-sections 陷阱

`drv_sdio.c` 使用 `INIT_DEVICE_EXPORT(rt_hw_sdio_init)` 自注册。  
如果没有任何代码显式调用 `rt_hw_sdio_init()`，链接器在 `--gc-sections` 下会丢弃 `drv_sdio.o`。  
→ 必须保持至少一个显式引用点。

### 烧录验证

```bash
# 确认 SDIO 驱动已编译
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rt_hw_sdio_init

# 启动后检查日志
# 期望: "[sd] mounted sd0 on / ok" (来自 rt_board_init.c)
# 期望: "/APM directories ready"
```

## 运行时验证：内存变量法（2026-05-10 新增）

当 SD 卡 `[sd]` 消息被 MAVLink 二进制数据淹没时，通过 OpenOCD 读取 `rt_board_init.c` 的全局变量确认挂载状态：

```bash
# 先找到变量地址
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep -E 'rtt_sd_mount_stage|rtt_sd_mount_result'

# 假设输出:
# 200445b8 B rtt_sd_mount_stage
# 20000bcc B rtt_sd_mount_result

# halt 后读取
echo "halt" | nc -q 2 localhost 4444
echo "mdw 0x200445b8 1" | nc -q 1 localhost 4444  # rtt_sd_mount_stage
echo "mdw 0x20000bcc 1" | nc -q 1 localhost 4444  # rtt_sd_mount_result
echo "resume" | nc -q 1 localhost 4444
```

### `rtt_sd_mount_stage` 值含义

| 值 | 含义 |
|-----|------|
| 0 | 未开始 |
| 1 | PG7 供电拉高 |
| 2 | 首次挂载尝试 |
| 3 | `rt_device_find("sd")` 成功 |
| 5 | `dfs_mount` 成功 |
| 10 | APM 目录创建完毕 (挂载完成) |
| -4 | 120 次重试后失败 (60s 超时) |

### `rtt_sd_mount_result` 值含义

| 值 | 含义 |
|-----|------|
| 0 | 挂载成功 |
| 非零 | 挂载失败 (与 errno 对应) |

### 预存在的 HardFault 陷阱（2026-05-10 发现）

SD 卡挂载成功后，系统可能在 `AP_Logger_Backend::Write` 中触发 HardFault：

```
CFSR: 0x00008200 (PRECISERR + BFARVALID)
故障 PC: 0x080ee440 (ldrb r4, [r3]) — strnlen 内
R3: 0xfd0c621c (无效指针)
调用链: AP_Logger_Backend::Write → strnlen(0xfd0c621c)
```

**这是与 SDIO 无关的预先存在的 bug** — 日志格式化时传入了损坏的字符串指针。在 SDIO 启用和禁用的固件中都存在。

**诊断时注意**：如果在 SD 卡挂载后看到此 HardFault，不要误判为 SDIO 问题。这是独立的 logger 数据指针损坏 bug。

## 参考文件

| 文件 | 用途 |
|------|------|
| `references/filesystem-logging-architecture.md` | 文件系统架构、POSIX vs FATFS、flash0 备选方案 |
