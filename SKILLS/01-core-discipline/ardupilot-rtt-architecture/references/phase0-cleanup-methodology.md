# Phase 0 代码架构违规清理方法论

**会话日期**: 2026-05-09  
**分支**: `staging/pogo-rtt-clean`  
**基线**: origin/master (pogo fork) @ `131318a6ff`  
**清理范围**: 15 个通用库文件，19 个违规提交

## 问题

`staging/pogo-rtt` 分支在 `libraries/` 通用代码中嵌入了大量 RTT 专有修改，违反了 ArduPilot 的 HAL 隔离架构。

- 违规文件：`AP_InertialSensor/`、`AP_Baro/`、`GCS_MAVLink/`、`AP_Vehicle/`、`AP_Logger/`、`AP_IOMCU/`、`AP_Scheduler/`、`AP_Filesystem/`
- 违规模式：`#ifdef HAL_RTT`、`rt_kprintf` 调用、ChibiOS→RTT API 硬替换、`struct stat` 布局重写

## 清理策略

### 主策略：文件级目标还原

替代 `git revert`（因混合提交无法干净分离），采用直接目标还原：

```bash
git checkout origin/master -- libraries/AP_InertialSensor/AP_InertialSensor.cpp
```

**何时可用**: 当 `origin/master` 版本与当前分支共享相同的内部 API（Device 接口、mavlink 类型）时。

**何时不可用**: origin/master 版本使用旧 API（如 `Device&` vs `OwnPtr<Device>`，或缺少 `mavlink_channel_mask_t` 类型）。此时回退到手术方案。

### 备用方案：手术式补丁

```bash
# 恢复到原版（包含违规代码）
git checkout HEAD~1 -- <file>

# 用 python 精确删除违规行（避免 patch 工具的 \n 字面量问题）
python3 -c "
with open('<file>', 'r') as f:
    lines = f.readlines()
# 删除 RTT 专有块
del lines[START:END]  # 精确行号
with open('<file>', 'w') as f:
    f.writelines(lines)
"
```

**删除目标**:
- `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` ... `#endif` 整块
- `#ifdef HAL_RTT` ... `#endif` 整块
- `rt_kprintf(...)` 单行调用
- `rtt_dbg_*` 变量定义

### 混合文件检测

识别同时包含"干净 RTT 修改"和"违规代码"的文件：

```bash
git log --oneline staging/pogo-rtt --not origin/master -- <file>  # 检查 RTT 提交
git diff origin/master -- <file> | wc -l                           # 偏离程度
```

对于混合文件，先手术删除违规代码，再验证干净修改完好：
```bash
git diff origin/master HEAD~1 -- libraries/AP_HAL_RTT/<file>  # 确认干净修改还在
```

## 常见陷阱及处理

### 陷阱 1: origin/master 太旧，API 不兼容

**症状**: 编译错误 — `Device&` vs `OwnPtr<Device>` 签名不匹配

**处理**: 回退到 `HEAD~1`，手术式删除 RTT ifdef

### 陷阱 2: mavlink 类型缺失

**症状**: `mavlink_channel_mask_t`、`MAV_PARAM_ERROR` 未声明

**根因**: origin/master 版本的 GCS.h 使用新版 mavlink 类型，但 mavlink 子模块指针未同步

**处理**: 见陷阱1 — 回退到 HEAD~1

### 陷阱 3: AP_Filesystem.h dirent 定义

**症状**: `'dirent'` 不完整类型

**根因**: 从 origin/master 恢复的 `AP_Filesystem.h` 使用 `struct dirent de;` 成员，但缺少 `dirent` 类型定义（RTT 无 POSIX dirent）

**正确方案**（Phase 0 暂缓）：
1. 在 `AP_HAL_RTT/include/` 下提供 `dirent` 定义
2. 或把 `DT_REG`/`DT_DIR`/`dirent struct` 并入 ChibiOS 条件块

### 陷阱 4: POSIX stat.h 冲突

**症状**: RTT DFS 的 `struct stat` 与新 lib 统计布局冲突，导致堆栈损坏

**正确方案**：
- 在 `AP_Filesystem.h` 中保留 `#include "../../modules/rt-thread/.../extension/sys/stat.h"` 路径（通过相对路径编译时工作）
- 不创建 `AP_HAL_RTT/include/sys/stat.h` 覆盖系统头文件（会破坏 RTT 内核编译）
- 更好的长期方案：在 AP_HAL_RTT 中只定义 RTT 需要的 stat 结构，不依赖相对路径

## 验证方法

```bash
# 1. 检查违规引用
git diff origin/master -- libraries/ -- libraries/AP_HAL_RTT | grep "HAL_RTT\|rt_kprintf\|rt_thread\|rtt_" | wc -l
# 期望输出: 0

# 2. 编译验证
rm -rf build && scons --v=ArduCopter --target=cuav_v5 -j$(nproc) 2>&1 | tail -5
# 期望: "done building targets"

# 3. 资源占用
grep "ROM\|RAM" <(scons ...) 
# 期望: ROM < 85%, RAM < 60%
```

## AP_Filesystem.h 最终状态（Phase 0 后）

```
Line 31: #if (CHIBIOS) || (ESP32) || (RTT)     → DT_REG/DT_DIR/DT_LNK
Line 37: #if (CHIBIOS) || (RTT && !POSIX)      → FATFS + dirent
Line 50: #endif // HAL_BOARD_CHIBIOS || RTT_NOPOSIX
Line 52: #if (RTT && POSIX)                    → #include "../../modules/.../sys/stat.h"
Line 64: #if (LINUX) || (SITL) || (QURT) || (RTT && POSIX) → AP_Filesystem_posix.h
```

RTT 专有块从原来的 3 个减少到 1 个（POSIX stat include，因 DFS 统计布局差异必须如此）。
