# RTT CUAV V5 文件系统日志架构

## hwdef.dat 关键配置

| 配置项 | 值 | 含义 |
|--------|-----|------|
| `HAL_OS_FATFS_IO` | **0** | 不使用 ArduPilot 原生 FATFS 后端 |
| `AP_FILESYSTEM_POSIX_ENABLED` | **1** | 使用 POSIX 文件系统后端 |
| `HAL_LOGGING_FILESYSTEM_ENABLED` | **1** | 文件系统日志已启用 |
| `HAL_LOGGING_MAVLINK_ENABLED` | **1** | MAVLink 日志已启用 |
| `HAL_BOARD_LOG_DIRECTORY` | `"/logs"` | 日志目录 |
| `HAL_BOARD_STORAGE_DIRECTORY` | `"/APM/STORAGE"` | 存储目录 |
| `HAL_BOARD_TERRAIN_DIRECTORY` | `"/APM/TERRAIN"` | 地形目录 |

## 文件系统后端选择链路

```
AP_FILESYSTEM_FATFS_ENABLED = HAL_OS_FATFS_IO = 0  ← 关闭
AP_FILESYSTEM_POSIX_ENABLED = 1                      ← 开启
                    ↓
AP_Filesystem.cpp L38-40 实例化: static AP_Filesystem_Posix fs_local
                    ↓
AP_Filesystem_Posix.cpp → 直接调用 RT-Thread DFS 的 open/close/read/write/stat
```

RT-Thread DFS（Device File System）内部已包含 FatFS，对外提供标准 POSIX 接口。
ArduPilot 通过 `AP_Filesystem_Posix` 调用这些接口，**不需要** ArduPilot 原生的 `AP_Filesystem_FATFS`（ChibiOS 专用）。

### `HAL_OS_FATFS_IO` 的实际作用

**仅控制** `AP_FILESYSTEM_FATFS_ENABLED`（AP_Filesystem_config.h:16）。
启用它会导致编译 `AP_Filesystem_FATFS.cpp`，该文件硬依赖 ChibiOS 头文件，**在 RTT 板上会编译失败**。
RTT 板完全不需要这个文件——POSIX 后端已覆盖所有文件操作。

**⚠️ 绝不能将 `HAL_OS_FATFS_IO` 设为 1。**

### AP_Filesystem_FATFS.cpp 的 ChibiOS 硬依赖

```
L15: #include <AP_HAL_ChibiOS/sdcard.h>        ← RTT 板无此头文件
L17: #include <AP_HAL_ChibiOS/hwdef/common/stm32_util.h>  ← RTT 板无此头文件
L200-204: 调用 sdcard_stop() / sdcard_retry()   ← RTT 板无这些函数
```

### RTT POSIX 适配层

RTT 板有专用的 POSIX 兼容层：
- `AP_Filesystem_posix.cpp` — 含 `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` 分支
- `AP_Filesystem_posix_rtt_compat.c` — 桥接 RT-Thread `struct stat` 到 POSIX `struct stat`

关键差异：
- `open()` 不加 `O_CLOEXEC`（RTT 无此概念）
- `stat()` 通过 `ap_rtt_posix_stat()` 兼容层转换（`ap_rtt_stat_compat` → `struct stat`）
- `disk_free()` / `disk_space()` 通过 `statfs()` 实现（`AP_FILESYSTEM_POSIX_HAVE_STATFS=1`）
- `set_mtime()` 不可用（`AP_FILESYSTEM_POSIX_HAVE_UTIME=0`）

## PreArm 日志检查链路

```
AP_Arming::logging_checks() (AP_Arming.cpp:390-411)
  ├─ check_enabled(Check::LOGGING)
  ├─ logging_present() → _next_backend != 0（有后端即 true）
  ├─ logging_failed() → 遍历所有后端，任一返回 true 即失败
  │   ├─ AP_Logger_File::logging_failed() (AP_Logger_File.cpp:1086-1107)
  │   │   ├─ !_initialised → true（缓冲区分配失败时）
  │   │   ├─ recent_open_error() + RTT + !_have_ever_opened → false ← RTT 特殊处理
  │   │   ├─ recent_open_error() + RTT + _have_ever_opened → true
  │   │   ├─ !io_thread_alive() → true
  │   │   └─ _last_write_failed → true
  │   └─ AP_Logger_MAVLink::logging_failed()
  │       └─ !_sending_to_client → true（无 GCS 客户端时）
  ├─ CardInserted() → 遍历所有后端，任一返回 true 即通过
  │   ├─ AP_Logger_File::CardInserted() → _initialised && !recent_open_error()
  │   └─ AP_Logger_MAVLink::CardInserted() → true（始终）
  └─ in_log_download() → 下载中阻止解锁
```

### RTT 特殊处理（AP_Logger_File.cpp:1092-1095）

```cpp
#if CONFIG_HAL_BOARD == HAL_BOARD_RTT
    if (!_have_ever_opened) {
        return false;  // 首次打开失败不算 logging_failed
    }
#endif
```

确保 SD 卡未就绪时首次不报 "Logging failed"（但 "No SD card" 仍可能触发）。

## SDIO / SD 卡挂载状态

**当前构建（2026-05-09 检查）：SD 卡功能完全未启用。**

| 检查项 | 结果 |
|--------|------|
| `CONFIG_BSP_USING_SDIO` | **未设置**（.config 中 `is not set`，.config.baseline 中为 `y`） |
| `CONFIG_RT_USING_DFS_ELMFAT` | **未设置**（FatFS 未编译） |
| `rtt_sd_mount_result` | `-99`（初始值，从未被修改） |
| `filesystem_table[0]` | `/dev`（devfs）✅ 正常 |
| `filesystem_table[1-3]` | 空（path=NULL） |

SD 卡初始化代码在 `rt_board_init.c:343` 被 `#ifdef BSP_USING_SDIO` 保护，编译时完全排除。

**GDB 检查 DFS 挂载状态**：
```gdb
(gdb) p rtt_sd_mount_result    # -99=从未尝试, 0=成功, -4=重试耗尽
(gdb) p rtt_sd_mount_stage     # 0=未开始, 1=开始, 2=SDIO初始化, 3=dfs_mount尝试, 5=创建目录, 10=完成, -4=失败
(gdb) p filesystem_table       # 查看 4 个槽位的 dev_id/path/ops
```

要启用 SD 卡：将 `.config` 中 `CONFIG_BSP_USING_SDIO` 改为 `y` 并启用 `CONFIG_RT_USING_DFS_ELMFAT`。

## 默认 Logger 后端选择

`HAL_LOGGING_BACKENDS_DEFAULT` 逻辑（AP_Logger.cpp:75-87）：
```
FILESYSTEM_ENABLED && SITL → FILESYSTEM
DATAFLASH_ENABLED → BLOCK
FILESYSTEM_ENABLED → FILESYSTEM  ← RTT 走这里（bit 0）
MAVLINK_ENABLED → MAVLINK        ← 需手动启用（bit 1）
```

RTT 板默认只启用 FILESYSTEM 后端。MAVLink 后端需要通过参数 `LOG_BACKEND_TYPE` 手动开启。

### PreArm 风险

| 场景 | 结果 | 原因 |
|------|------|------|
| 无 SD 卡，默认后端 | **PreArm 失败 "No SD card"** | `CardInserted()=false` |
| 有 SD 卡，文件系统正常 | ✅ 通过 | |
| 启用 MAVLink 后端，无 GCS | **PreArm 失败 "Logging failed"** | MAVLink `logging_failed()=true` |
| 启用 MAVLink 后端，有 GCS | ✅ 通过 | MAVLink `CardInserted()=true` |

### AP_Logger_File::Init() 关键流程

```cpp
Init() {
    _writebuf.set_size(bufsize);  // 分配写缓冲
    _initialised = true;          // ← 只要缓冲分配成功就算初始化完成
    find_last_log();              // 扫描日志目录
    Prep_MinSpace();              // 准备最小空间检查
}
```

`_initialised = true` 不依赖文件系统是否实际可用，只依赖内存分配。

## 关键文件索引

| 文件 | 作用 |
|------|------|
| `libraries/AP_Filesystem/AP_Filesystem_config.h` | FS 后端启用宏定义 |
| `libraries/AP_Filesystem/AP_Filesystem.cpp` | fs_local 后端实例化选择 |
| `libraries/AP_Filesystem/AP_Filesystem_Posix.cpp` | POSIX FS 实现（含 RTT 分支） |
| `libraries/AP_Filesystem/AP_Filesystem_posix_rtt_compat.c` | RT-Thread stat 兼容层 |
| `libraries/AP_Filesystem/AP_Filesystem_FATFS.cpp` | ChibiOS 专用 FATFS（RTT 不可用） |
| `libraries/AP_Logger/AP_Logger.cpp` | 后端 probe 顺序 + backend_types 参数 |
| `libraries/AP_Logger/AP_Logger_config.h` | 日志后端启用宏 |
| `libraries/AP_Logger/AP_Logger_File.cpp` | 文件日志后端（含 RTT workaround） |
| `libraries/AP_Logger/AP_Logger_File.h` | `_have_ever_opened` 声明 |
| `libraries/AP_Logger/AP_Logger_MAVLink.h` | `CardInserted()=true` 始终通过 |
| `libraries/AP_Arming/AP_Arming.cpp:390-411` | PreArm logging_checks() |
| `libraries/AP_HAL_RTT/hwdef/cuav_v5/hwdef.dat` | 板级 FS/LOGGING 配置 |
| `libraries/AP_HAL/board/rtt.h` | RTT 板 HAL_OS_FATFS_IO 默认值 |
