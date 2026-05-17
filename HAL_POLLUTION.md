# HAL_POLLUTION 追踪表

> **用途**: 追踪 `libraries/` 中所有 `CONFIG_HAL_BOARD == HAL_BOARD_RTT` 污染点，记录移除策略和优先级。

---

## 污染点总览

| # | File | Line | Code | Purpose | Removal Strategy | Priority |
|---|------|------|------|---------|------------------|----------|
| 1 | GCS_FTP.cpp | — | `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` (×19+) | RTT 平台 FTP 协议适配 | 提取为 `GCS_FTP_RTT.cpp` 独立编译单元，通过 HAL 抽象层注册 | P1 |
| 2 | GCS_Common.cpp | — | `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` (×5) | RTT 特定 GCS 行为（串口/心跳） | 提取 `GCS_Common_RTT.cpp`，或通过 weak symbol 覆盖 | P1 |
| 3 | GCS_Param.cpp | — | `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` (×1) | 参数系统 RTT 适配 | 移至 HAL 抽象层回调 | P2 |
| 4 | GCS_MAVLink_Parameters.cpp | — | `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` (×1) | MAVLink 参数 RTT 适配 | 移至 HAL 抽象层回调 | P2 |
| 5 | AP_Vehicle.cpp | — | `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` (×1) | 车辆初始化 RTT 适配 | 通过 `AP_HAL::Util` 虚拟方法分发 | P2 |
| 6 | AP_CANManager.cpp | — | `#if CONFIG_HAL_BOARD != HAL_BOARD_RTT` (×2) | CAN 管理器 RTT 排除逻辑 | 反向条件，改为 HAL 特性宏 `HAL_WITH_CAN` | P1 |
| 7 | AP_BoardConfig/board_drivers.cpp | — | `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` (×1) | 板级驱动选择 RTT 分支 | 移至 `boards/rtt` 专用驱动目录 | P2 |
| 8 | AP_Filesystem/AP_Filesystem.h | — | `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` (×4) | 文件系统后端 RTT 适配（FATFS/DFS） | HAL 文件系统后端注册表，RTT 注册独立后端 | P1 |
| 9 | AP_CheckFirmware_defines | — | `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` (×1) | 固件检查宏定义 RTT 分支 | 改为编译时特性检测 | P2 |
| 10 | AP_BoardConfig.h | — | `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` (×1) | 板级配置头文件 RTT 分支 | 提取 `AP_BoardConfig_RTT.h` 独立头文件 | P2 |

---

## 优先级说明

| 优先级 | 含义 | 示例 |
|--------|------|------|
| **P1** | 高 — 代码量大（≥4处），逻辑复杂，需优先解耦 | GCS_FTP.cpp（19+处）、GCS_Common.cpp（5处）、AP_Filesystem（4处）、AP_CANManager |
| **P2** | 中 — 代码量少（1~2处），逻辑简单，可在后期清理 | GCS_Param、AP_Vehicle、AP_BoardConfig 等 |

---

## 移除策略模板

### 通用策略
1. **HAL 特性宏**: 定义 `HAL_HAVE_FTP`, `HAL_HAVE_CAN` 等特性宏，替代直接板级判断。
2. **HAL 抽象层**: 在 `AP_HAL` 中增加纯虚函数或回调，RTT 在 `AP_HAL_RTT` 中实现。
3. **独立编译单元**: 将 RTT 特定代码抽出为 `*_RTT.cpp`，由 `cmake` 条件编译选择。
4. **Weak Symbol / Override**: 对少量适配点使用 `__attribute__((weak))` 默认实现，RTT 提供覆盖。

### 各文件具体策略

- **GCS_FTP.cpp**: 提取 `AP_GCS_FTP_RTT` 子类，通过工厂方法注册。
- **AP_CANManager.cpp**: 替换 `#if != RTT` 为 `#if HAL_WITH_CAN`。
- **AP_Filesystem.h**: 注册 RTT DFS 后端到 `AP_Filesystem` 后端表。
- **GCS_Common.cpp**: 分散适配点逐个映射为 HAL 回调。
