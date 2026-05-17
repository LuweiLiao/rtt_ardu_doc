---
name: ardupilot-rtt-architecture
description: ArduPilot RT-Thread (RTT) 移植架构规范 — HAL 隔离原则、违规清单、正确修复路径、代码审查标准、模块对比矩阵、重建计划
category: embedded
---

# ArduPilot RTT 移植架构规范

## ⚠️ Agent 角色约束（廖博士 2026-05-09 强调）

**Agent = 监督协助 CC 的管理人，不是直接改代码的工程师。**

角色：
1. **诊断** → 分析问题根因、定位违规代码
2. **出方案** → 制定修复计划（不越过架构边界）
3. **监督 CC 执行** → CC 负责写代码，Agent 复查
4. **复查结果** → 验证通过后才汇报

绝对禁止：
- 直接向 `libraries/` 通用代码提交 RTT 专有修改
- 在通用代码中嵌入 `#ifdef HAL_RTT`、`rt_kprintf` 等 RTT 平台特化代码
- 将自己当作开发者随意修改代码（用户明确纠正过：「不能胡来」）

如果用户说「可以吗」→ 需要先确认再执行。如果用户说「直线」「执行」→ 立即执行。

## 🏗️ HAL 隔离架构原则（命定性规则）

ArduPilot 的跨平台架构遵循 HAL（Hardware Abstraction Layer）模式：

```
libraries/AP_HAL_ChibiOS/    ← ChibiOS 平台（52 文件）
libraries/AP_HAL_Linux/      ← Linux 平台（133 文件）
libraries/AP_HAL_ESP32/      ← ESP32 平台（45 文件）
libraries/AP_HAL_RTT/        ← RTT 平台（36 文件，已补齐的缺失见模块矩阵）
```

每个平台专属目录内部封装底层差异，向上提供统一接口。上层代码（传感器、GPS、IO 协处理器等）**不感知也不关心**底层是 ChibiOS 还是 RTT。

**这是 ArduPilot 官方设计的红线，不是风格建议。** 违背此原则的修改：
- 无法被 upstream 接收（upstream 拒绝 `#ifdef HAL_RTT`）
- 在 upstream 更新时产生合并冲突
- 破坏"一个 HAL 目录"的软件管理逻辑
- 属于「搞混了软件生态」的架构违规行为

### 具体规则

| 规则 | 违反后果 |
|------|---------|
| ❌ `libraries/`（除 `AP_HAL_RTT/`）中不能出现 `#ifdef HAL_RTT` | 回退提交 |
| ❌ `libraries/` 中不能出现 `rt_kprintf`、`rt_thread*`、`rt_event*`、`rt_mutex*` 等 RTT API 调用 | 回退提交 |
| ❌ `libraries/` 中不能出现 RTT 板级头文件包含（如 `<rtthread.h>`、`"../../modules/rt-thread/..."` 等） | 回退提交 |
| ❌ `libraries/` 中不能改动线程栈、缓冲、堆等资源参数 | 回退提交 |
| ✅ 通用 bug（空指针、数组越界等）**无条件修复，不加 ifdef** | 自动进入 upstream |
| ✅ 在 `AP_HAL/board/` 中新增 `rtt.h`（与 `chibios.h` 平级） | 架构合规 |
| ✅ 在 build 系统中新增 RTT 目标（SConscript、waf） | 架构合规 |

## 错误认知对照表

| 看起来像"正确修复"的方式 | 实际是违规 | 正确的 HAL 层做法 |
|----------------------|----------|-----------------|
| 在 AP_Baro_MS5611.cpp 中加 `#ifndef rt_kprintf` 绕开编译 | ❌ 污染通用代码 | 在 AP_HAL_RTT 中提供 rt_kprintf stub，或在 rtt.h 中 `#define rt_kprintf(fmt, ...)` |
| 在 AP_InertialSensor.cpp 中强制设 health bit | ❌ 绕过而非修复 | AP_HAL_RTT/SPIDevice.cpp 修 SPI 时序防止 error_count 累积 |
| 在 GCS_Param.cpp 中加 RTT 直接 ACK 路径 | ❌ 绕过异步 IO | AP_HAL_RTT/UARTDriver.cpp 修底层 CDC 驱动使异步路径正常工作 |
| 在 AP_IOMCU.cpp 中嵌入 volatile 诊断变量 | ❌ 调试代码入通用逻辑 | AP_HAL_RTT/debug.h 独立调试模块 + OpenOCD 内存变量读取 |
| 在 AP_Logger.cpp 中改栈大小常量 | ❌ 硬编码入通用层 | AP_HAL_RTT/hwdef/common/.config 中配置 |
| 在 AP_Vehicle.cpp 中改 scheduler gate | ❌ 改通用调度 | AP_HAL_RTT/Scheduler.cpp 中配置 yield 频率 |
| 在 AP_Filesystem.h 中添加 RTT dirent/stat 条件块 | ❌ 平台代码污染 | AP_HAL_RTT/include/ 下提供 dirent/stat 定义（但 DFS 统计 bug 导致无法移动，见 reference） |

ArduPilot 通过 HAL (Hardware Abstraction Layer) 实现跨平台。

```
libraries/ (通用代码，不感知平台)
  ├── AP_Baro/           ← 不动
  ├── AP_InertialSensor/ ← 不动
  ├── GCS_MAVLink/       ← 不动
  ├── AP_Vehicle/        ← 不动
  ├── AP_Logger/         ← 不动
  ├── AP_IOMCU/          ← 不加入RTT诊断
  ├── AP_Filesystem/     ← 不动
  ├── ...
  └── AP_HAL/            ← 板级定义 (board/rtt.h ✅)
       └── AP_HAL_RTT/   ← 只有这里可以改

AP_HAL_RTT/              ← RTT 平台专属，任意修改
  ├── UARTDriver.cpp     ✅
  ├── SPIDevice.cpp      ✅
  ├── GPIO.cpp           ✅
  ├── Scheduler.cpp      ✅
  ├── Scheduler.h        ✅
  ├── Semaphores.cpp     ✅
  ├── ...                ✅
  └── hwdef/             ✅ 板级配置
```

## 铁律

### 1. `libraries/` 通用代码原则上不动

- **动**：你发现的是所有平台都存在的**通用 bug**（如 `AP_GPS/AP_GPS.cpp` 中 drivers[] 未初始化）
- **不动**：你的问题是因为 RTT 平台特有的行为（如 GYRO/ACCEL health、USB CDC 缓冲、SPI 时序）

### 2. 永远不加 `#ifdef HAL_RTT`

`#ifdef HAL_RTT` 出现在通用代码中是架构红色警报。正确的 HAL 隔离要求在编译时通过 **不同的 .cpp 源文件** 来区分，而非在同一个文件中做条件编译。

### 3. 永远不加 RTT 专有函数调用（如 `rt_kprintf`）

**触发场景**：`AP_Baro_MS5611.cpp` 中有 `rt_kprintf` 调用导致编译失败
**正确修复**：在 `AP_HAL_RTT/system.cpp` 中提供空 stub 宏，或在 board 头文件中处理
**错误修复**：在 `AP_Baro_MS5611.cpp` 中加 `#ifndef rt_kprintf` 定义

### 4. 能修的 bug 无条件修

如果一个 bug 在所有平台都可能出现（只是 RTT 先暴露了），**无条件修复，不加 ifdef**。例如：
- 空指针解引用、未初始化变量、数组越界、资源泄漏等 C++ 通用错误
- 这些修复直接进 upstream

### 5. 堆/栈配置不走通用代码

**触发场景**：`AP_Logger.cpp` 中线程栈大小需要调整
**正确修复**：在 `AP_HAL_RTT/hwdef/common/.config` 或板级配置中定
**错误修复**：改 `AP_Logger.cpp` 中的硬编码常量

### 6. 必须先对照 ChibiOS 逐项比对，功能一致才能提交（廖博士 2026-05-13 强调）

**「必须要跟chibios核对驱动，对照着来，一项一项对比，要从功能上一致，才能提交验证版」**

修改 RTT 代码前，必须逐项比对 ChibiOS 的对应实现：

1. **找到 ChibiOS 对应文件**：`libraries/AP_HAL_ChibiOS/` 中对应的 `.cpp/.h`
2. **逐行对比函数签名和返回值**：特别是 `get_semaphore()`、`register_periodic_callback()`、`transfer()` 等 HAL 接口
3. **对比锁模型**：ChibiOS 用单层总线锁，RTT 不得引入私有锁破坏语义
4. **对比线程优先级**：ChibiOS `APM_SPI_PRIORITY=181`（高于主线程 180），RTT 必须映射为等效优先级
5. **对比超时策略**：ChibiOS 用永久阻塞，RTT 不得用短超时+静默跳过
6. **编译+烧录+双重验证**：通过后才可提交

**反馈机制**：如果发现 RTT 实现与 ChibiOS 不一致（如返回值、锁模型、超时策略差异），必须先记录为新发现的差异，再制定修复方案。禁止"先凑合用，后面再对齐"。

### 7. Architecture Cleanup 后必须编译验证 IOMCU

**经验证 3 次跨周同构事件（Week 3 USB CDC, Week 7 IOMCU）**：Phase 架构清理操作是 IOMCU RTT API 回归的最高风险源。

**触发场景**：回退 libraries/ 通用代码中的 RTT 专有修改时，误将 `AP_IOMCU.cpp/h` 中的 ChibiOS→RTT 适配补丁一并回退
**根本原因**：IOMCU 的 RTT 适配补丁分布在 `AP_IOMCU.cpp` 和 `AP_IOMCU.h` 两个文件中，与通用代码的 RTT 违规修改混在同一个文件。清理者 `git checkout origin/master` 时无差别还原整个文件，丢失了 RTT 必要的适配代码。
**必须验证**：每次 Phase 清理后，必须使用 SCons 编译 `--v=ArduCopter --target=cuav_v5`（IOMCU 启用），确认链接通过且 MAVLink 心跳输出正常
**必须恢复的 3 个补丁**：
1. `chEvt*` → `rt_event_*`（事件/IPC 适配）
2. 超时保护（`iomcu_event` + `init_fail_count`）
3. 固件上传不硬崩（`wait_for_ready()` 超时处理）

**检查命令**：`grep -n "rt_event_" libraries/AP_IOMCU/AP_IOMCU.cpp` — 应返回至少 3 处 rt_event 调用

**触发场景**：PARAM_REQUEST_READ ACK 不走 param_io_timer()
**正确修复**：修 `AP_HAL_RTT/UARTDriver.cpp` 使底层 UART/USB 驱动正确支持异步发送
**错误修复**：在 `GCS_Param.cpp` 中加入 RTT 直接发送路径

## 违规清单（历史记录，禁止新增）

### ❌ 已回退的违规修改（Phase 0 完成，2026-05-09 会话）

**状态：✅ 编译通过** — ROM 84.56%, RAM 54.07%

#### 清理方法论（本会话实践验证）

替代原始的 `git revert` 逐个提交方案（因混合提交过多导致难以执行），采用**文件级目标还原**：

**Step 1：** 针对每个违规文件，使用 `git checkout origin/master -- <file>` 还原

**Step 2：** 验证干净修改是否丢失：
```bash
git diff origin/master HEAD~1 -- <file>   # 检查AP_HAL_RTT/修改是否还在
git log --oneline origin/master..HEAD -- '<file>'  # 检查文件是否从origin/master还有差异
```

**Step 3：** 编译验证。如果通过 → 完成。如果编译失败（常见原因：origin/master 太旧，依赖的 mavlink 类型不存在、Device API 签名不匹配）→ 回退到 `HEAD~1` 版本，用 patch 手术式去除 RTT 专有代码。

#### 手术式清理方案（用于混合文件）

对于编译无法直接还原 origin/master 的文件，使用 `git checkout HEAD~1 -- <file>` 恢复原版，然后：

1. 识别文件中所有 RTT 专有 ifdef 块（`#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` 或 `#ifdef HAL_RTT`）
2. 使用 Python 脚本精确删除对应行（避免 patch 工具的 `\n` 字面量问题）
3. 去除 `rt_kprintf` 调用——替换为 `DEV_PRINTF`（通用平台可用的调试打印）
4. 验证：`git diff` 确认只删除了 RTT 专有代码，未修改其他逻辑

**⚠️ 常见编译陷阱：**

| 问题 | 根因 | 处理 |
|------|------|------|
| `mavlink_channel_mask_t` 未声明 | origin/master GCS.h 需新版 mavlink 类型（`typedef uint32_t`），但 build 用旧版 | 回退到 HEAD~1，手术式去除 RTT ifdef |
| `AP_Baro_MS56XX` 构造签名不匹配 | origin/master 的 .cpp 用 `Device&`，HEAD~1 的 .h 用 `OwnPtr<Device>`（上游改进） | 文件级还原 origin/master 失败，用手术方案 |
| `AP_Filesystem.h` — `dirent` 不完整类型 | RTT 无 POSIX dirent 头（origin/master 版本依赖 `<dirent.h>`） | 临时保留 HEAD~1 版本（含 RTT dirent 定义），Phase 1 移入 AP_HAL_RTT |
| `HAL_STORAGE_SIZE` 重定义警告 | hwdef.h 和 command-line 重复定义 | 无害警告，可忽略 |

#### 具体清理文件记录（19 个提交 → 手术或还原）

| 处理方式 | 文件 | 操作 |
|---------|------|------|
| ✅ `origin/master` 还原 | `AP_InertialSensor/*` 全部5个 | 含 BMI055/088/Invensense/Invensensev3 |
| ✅ `origin/master` 还原 | `AP_IOMCU/AP_IOMCU.cpp/h` | 去除 ChibiOS→RTT 替换 + 诊断变量 |
| ✅ `origin/master` 还原 | `AP_Logger/AP_Logger.cpp`, `AP_Logger_File.cpp` | 栈大小还原 |
| ✅ `origin/master` 还原 | `AP_Scheduler/AP_Scheduler.cpp` | 编译修补还原 |
| ✅ `origin/master` 还原 | `GCS_MAVLink/GCS.h` + `GCS_Param.cpp` + `GCS_Common.cpp` | 去除RTT ifdef + direct ACK |
| ✅ 手术去除 | `AP_Baro/AP_Baro_MS5611.cpp` | 去除 4 个 `rt_kprintf` 调用 |
| ✅ 手术去除 | `AP_Baro/AP_Baro.cpp` | 替换 rt_kprintf → DEV_PRINTF |
| ✅ 手术去除 | `AP_Vehicle/AP_Vehicle.cpp` | 去除 RTT 循环率报告块（~15行） |
| ✅ 删除文件 | `AP_Filesystem/AP_Filesystem_posix_rtt_compat.c` | 新文件直接删除 |
| ⚠️ 临时保留 | `AP_Filesystem/AP_Filesystem.h` | 含 RTT dirent/stat 定义（Phase 1 移入 AP_HAL_RTT） |
| ⚠️ 待审 | `GCS_MAVLink/GCS_FTP.cpp` | 含 `rtt_use_open_first` 命名（功能兼容，Phase 1 规范） |

涉及违规的通用代码文件：
| 文件 | 违规内容 |
|------|---------|
| `AP_InertialSensor/AP_InertialSensor.cpp` | RTT专属 health bit 绕过 |
| `GCS_MAVLink/GCS_Param.cpp` | RTT专属 param ACK 直接发送 |
| `GCS_MAVLink/GCS_Common.cpp` | RTT专属 stream 调度改 |
| `AP_Vehicle/AP_Vehicle.cpp` | `#ifdef HAL_RTT` scheduler gate 4ms |
| `AP_Logger/AP_Logger.cpp` + `AP_Logger_File.cpp` | 线程栈大小写死 |
| `AP_Filesystem/AP_Filesystem_posix.cpp` + compat | RTT POSIX 兼容混入 |
| `AP_IOMCU/AP_IOMCU.cpp/h` | ChibiOS→RTT API 替换 + 诊断变量 |
| `AP_Baro/AP_Baro_MS5611.cpp` | rt_kprintf 抑制 |
| `AP_Baro/AP_Baro.cpp` + `AP_InertialSensor/*` | RTT SPI 适配混入 |

**回退顺序提醒**：先 revert A 类（安全），再处理 B 类混合提交，最后分析 C 类。

### ✅ 允许的修改

| 文件 | 理由 |
|------|------|
| `AP_HAL/board/rtt.h` | 板级定义，与 `chibios.h`/`linux.h` 平行 |
| `AP_GPS/AP_GPS.cpp` | 通用 bug fix — BSS 段未初始化 (`drivers[] = nullptr`) |

## 模块对比与重建计划

见参考文件：
- `references/hal-comparison-matrix.md` — ChibiOS/ESP32/RTT 驱动模块完整对比，含**缺失模块分优先级清单**和**已有模块健康度评估**
- `references/cleanup-and-rebuild-plan.md` — 四阶段实施计划（Phase 0 代码清洁 → Phase 1 驱动补齐 → Phase 2 问题修复 → Phase 3 验证）

### 重建路线图速览

```
Phase 0: 代码清洁 ─── 回退所有违规提交 (当前)
  ↓
Phase 1: 驱动补齐 ─── CAN, SD卡, SoftSigReader, RCOutput_iofirmware, shared_dma, stdio
  ↓
Phase 2: 问题修复 ─── _writebuf 512B, RCInput补齐, ADC死锁, Scheduler yield策略
  ↓
Phase 3: 逐模块验证 ─ L0→L1→L2→L3 逐级通过
```

## ⚡ 关键铁律：每一步必须参考 ChibiOS（廖博士 2026-05-10 强调；2026-05-13 追加精读规则）

**「研究 ChibiOS 是怎么做的，每一步都要参考 ChibiOS，才能高效的移植，而不是毫无目的的移植」**

修改 RTT 代码前，必须：
1. **找到 ChibiOS 的对应实现**（`libraries/AP_HAL_ChibiOS/` 中对应 `.cpp/.h`）
2. **逐行精读 ChibiOS 代码**，理解完整上下文和语义（不只开头几句）
3. **对比行为差异**后，再制定修复方案
4. **验证行为等价**（同样的输入/条件 → 同样的输出/状态）

**2026-05-13 追加的精读规则（廖博士严厉纠正「不准猜测、必须精读、再规划」后的明确要求）：**

❌ **禁止做的**：
   - 读前 5 行就推出结论并动手改代码
   - 凭记忆或猜测量推算 ChibiOS 的行为
   - 用 CC 改写延时/超时参数来"绕开"而非修复根因
   - 读一个函数就下结论，不读完整调用链

✅ **必须做的**：
   1. 用 `skill_view('chibios-cuav-v5-hw-reference')` 或直接 `cat` ChibiOS 源码的完整函数
   2. 标注每个函数的关键行号（如 `HAL_ChibiOS_Class.cpp:265` `hal_chibios_set_priority(APM_STARTUP_PRIORITY)`）
   3. 写下 ChibiOS 行为 → RTT 行为的对照表
   4. 制定修复方案后才能动代码
   5. 汇报给廖博士确认后，才能让 CC 执行

**优先级对比是 RTT 移植最容易被忽略的核心差异**：ChibiOS 在 setup 前主动将主线程优先级降到最低 (10)，setup 完成后恢复高优先级 (180)。RTT 完全不做此降级 → 导致 setup 耗时膨胀 2 倍、UART 线程饿死、传感器初始化时序偏差。这个差异在 ChibiOS 源码中是明显的（`HAL_ChibiOS_Class.cpp:265`），但如果不精读到那一步就会错过。

**陷阱**：RTT 的 API（`rt_mutex_take`、`rt_thread_delay`、`rt_sem_take`）与 ChibiOS 的 API（`chMtxLock`、`chThdSleep`、`chBSemWait`）**语义可能不同**。例如 `rt_mutex_take` 是递归锁但 ChibiOS mutex 已经是递归锁；`rt_thread_delay` 只是睡眠而 ChibiOS `chThdSleep` 也类似，但 RTT 的 `delay()` 额外调用了 `call_delay_cb()` 而 ChibiOS 不调用。

## 🔬 ChibiOS vs RTT 关键行为差异

以下差异是本会话中通过反汇编 + OpenOCD + 源码对比发现的。

### 1. delay() 回调行为

| 方面 | ChibiOS ✅ | RTT ❌ |
|------|-----------|--------|
| `hal.scheduler->delay(100)` | 仅 `chThdSleep(MS2ST(100))` — 纯睡眠 | 循环调用 `delay_microseconds(1000)` + **每个 tick 调用 `call_delay_cb()`** |
| `call_delay_cb()` 触发 | 只在 `_delay_cb_task()` 定时器中 | 在 `delay()` 的 `while` 循环中每次迭代都触发 |
| 影响 | 无副作用 | **setup 阶段触发 GCS/Logger**，导致 Logger 线程试图访问未初始化的资源 |

**根因**：RTT 的 `Scheduler::delay()` 实现为循环 `while (time_elapsed < ms) { delay_microseconds(1000); call_delay_cb(); }`。ChibiOS 的等效是直接 `chThdSleep(MS2ST(ms))`，不触发任何回调。

**影响范围**：`setup()` 阶段的所有 `hal.scheduler->delay(N)` 调用。特别是 IMU 初始化中的 `_hardware_init()` 芯片复位循环（5 次 × 100ms）。

**修复方向**：setup 期间跳过 `call_delay_cb()`，或改用 `rt_thread_delay` 直接睡眠。

### 2. SPI 设备锁架构（2026-05-13 更新：三个关键差异已确认）

| 方面 | ChibiOS ✅ | RTT ❌ |
|------|-----------|--------|
| `get_semaphore()` 返回 | **DeviceBus.semaphore** (总线级唯一锁) | **SPIDevice 内部 `_sem`** (per-device 私有锁，与 DeviceBus 分离) |
| 行号验证 | SPIDevice.cpp:338 — `return &bus.semaphore;` | SPIDevice.cpp:649 — `return &_sem;` |
| `WITH_SEMAPHORE` 效果 | 直接锁住**整条 SPI 总线**，其他设备不可操作 | 只锁住**单个设备**，其他设备仍可通过同一总线操作 |
| Bus 线程锁回调 | 永久阻塞等待锁 (`take_blocking`) | `binfo->semaphore.take(10)` — **10ms 超时后静默跳过回调** |
| 锁层级 | 单层：DeviceBus semaphore | **双锁**：DeviceBus semaphore + SPIDevice `_sem`，相互独立 |
| 优先级继承 | chMtxLock 原生支持 | `rt_mutex_take` 支持（RT-Thread mutex 有 PI） |
| Bus 线程优先级 | `APM_SPI_PRIORITY=181`（高于主线程 180） | `0` → 默认 `RT_THREAD_PRIORITY_MAX/6`（未对齐） |

**根因**：RTT SPIDevice 的 `get_semaphore()` 返回 `&_sem`（私有成员）而非 DeviceBus 的 `&_bus->semaphore`。ChibiOS 返回 `&bus.semaphore`。这导致 `WITH_SEMAPHORE(_dev->get_semaphore())` 在 RTT 中获取的是 SPIDevice 私锁，而 DeviceBus 线程获取的是总线锁——**两把不同的锁，完全破坏了总线互斥语义**。

**影响**：
- Invensense 驱动 `WITH_SEMAPHORE(dev->get_semaphore()) { _register_write(); ... }` 只持私有锁
- `transfer()` 内部再拿总线锁 → 两锁分离，其他 SPI 设备可插入操作
- Bus 线程 `take(10)` 超时会**静默跳过** callback → IMU 样本丢失
- Bus 线程优先级未显式对齐 ChibiOS → 调度顺序不确定

**修复方向**（按优先级）：
1. **[P1]** 让 `get_semaphore()` 返回 DeviceBus 的 `&_bus->semaphore`（匹配 ChibiOS），移除 `_sem` 的独立锁逻辑
2. **[P1]** Bus 线程改为 `take(HAL_SEMAPHORE_BLOCK_FOREVER)` 而非 `take(10)` 超时
3. **[P2]** 显式设置 Bus 线程优先级为 ChibiOS 等效值

### 3.2 DWT CYCCNT 回绕与 micros64() 交互（2026-05-14 发现）

| 方面 | 分析 | 结论 |
|------|------|------|
| CYCCNT 回绕周期 | 32-bit @ 216MHz = ~19.9s | 回绕频繁发生 |
| `_delay_microseconds_dwt` 防回绕 | `(current - start) < cycles` 用 unsigned sub | ✅ 编译器保留为 sub（非 cmp），正确 |
| `micros64()` 中 sub_us 计算 | `(cyc / _cpu_freq_mhz) % tick_period_us` | tick_us + sub_us 整体正确 |
| delay() 循环退出条件 | `(micros64() - start_us) / 1000 < ms` | 64-bit 无回绕问题 ✅ |
| 回绕时 sub_us 跳变 | CYCCNT 0xFFFFFFFF→0 时 sub_us 从 ~295 跳到 0 | 微小抖动，不影响单调性 |

**反汇编验证**（关键发现）：编译器正确将 `(DWT_CYCCNT - start) < cycles` 编译为 unsigned 减法：
```asm
subs r3, r3, r1    ; r3 = current - start (unsigned)
cmp  r3, r0        ; compare with cycles
bcs  exit          ; if >=, exit (unsigned)
dsb  sy
b    loop
```

**微秒精度**：`micros64()` 中的 sub_us 计算使用 `cyc / _cpu_freq_mhz`（整数除法），`_cpu_freq_mhz=216`，因此精度为 `1000/216 ≈ 4.63µs`。对于 delay() 的毫秒级精度足够。

| `references/p0-adc-dma-ispitfall.md` | DMA ISR 自锁 — 2026-05-14 发现/修复 |
| `references/board-hal-limitation.md` | CUAV V5 BSP 无 board.h → HAL_Drivers PWM 不可用，需直接寄存器访问 |

## 🆕 CUAV V5 BSP 关键限制：board.h 缺失

CUAV V5 BSP 没有标准 `board.h` → `libraries/HAL_Drivers/drivers/drv_pwm.c` 不编译 → `rt_pwm_set()` 不可用。RCOutput 改用直接 TIM 寄存器访问（TIM1/TIM4/TIM12 PSC+ARR+CCR+CCER+BDTR）。I2C 和 ADC 同理——自行注册/初始化。

## 🆕 MCU 温度监测（2026-05-16 新增）

hwdef.dat 加 `define HAL_WITH_MCU_MONITORING 1`。ADC3 轮询 ch18(温度)+ch17(VREFINT) 20Hz。公式参考 ChibiOS AnalogIn.cpp L743-768。

## ⚠️ DMA ISR 自锁（Stale TCIF Flag）— 2026-05-14 发现

> **根因**：烧录/复位后 DMA2_Stream0 的 TCIF 可能遗留为 1。使能中断后 ISR 立即自触发，CPU 100% 在执行 ISR，主线程永远得不到运行时间。

**症状**：
- `hal_run_called = 0xBBBBBBBB`（setup 已完成 ✅）
- `main_loop_entry_called = 0x12345678`（main loop 已进入 ✅）
- **`main_loop_iterations = 0`**（一次都没迭代 ❌）
- PC 始终在 `_delay_microseconds_dwt()`（Scheduler.cpp:72）

**修复**：初始化 DMA 前先清所有遗留标志位，再使能中断。见 `references/p0-adc-dma-ispitfall.md`。

**区分诊断**：如果 L1 基线（无 P0 改动）正常运行而 P0 版本 main_loop_iterations=0，100% 是 DMA ISR 自锁。用 `main_loop_iterations` 对比 L1 基线可快速缩小范围。

| 方面 | ChibiOS ✅ | RTT ❌ |
|------|-----------|--------|
| 短延迟策略 | 直接 `chThdSleep`（最少 1 tick） | DWT 忙等（`_delay_microseconds_dwt`） |
| `< 1 tick` 的延迟 | 不提供（向上取整到 1 tick） | DWT 忙等，**不释放 CPU** |
| 对调度的影响 | 线程让出 CPU，其他线程可运行 | 阻塞所有优先级线程，包括定时器 |
| DWT 精度 | 不使用 | 依赖 `SystemCoreClock` — **计算错误会导致死等** |

**根因**：RTT 的 `delay_microseconds(us)` 中，如果 `us < tick_us`（例如 100µs < 1000µs），直接进入 DWT 忙等。忙等期间线程不让出 CPU。

**影响范围**：任何调用 `delay_microseconds(< 1 tick)` 的代码，包括 `set_speed()` 和 `_register_write()` 后的小延迟。如果主线程在 DWT 忙等，定时器线程（更高优先级）也无法运行。

**修复方向**：对于 `>= 200µs` 的延迟，即使 `us < tick_us`，也至少睡眠 1 tick；仅对 `<= 100µs` 保留 DWT 忙等。

### 4. take_blocking() 超时差异

| 方面 | ChibiOS ✅ | RTT ❌ |
|------|-----------|--------|
| `take(HAL_SEMAPHORE_BLOCK_FOREVER)` | `chMtxLock(mtx)` — 永久阻塞 | `rt_mutex_take(&_mtx_obj, RT_WAITING_FOREVER)` — 永久阻塞 |
| `take_blocking()` 覆写 | 不覆写，走基类 `take(0)` | **覆写**为 60s 超时的 `rt_mutex_take(&_mtx_obj, 60000)` |
| 超时后行为 | N/A | mutex **未获取但继续执行**，`WITH_SEMAPHORE` 析构时调用 `give()` 在未持有的锁上 |

**影响**：60s 超时的 `take_blocking()` 在锁竞争时会导致 `WITH_SEMAPHORE` 析构函数调用 `give()` 在没有持锁的 mutex 上，造成未定义行为。

**修复方向**：移除 `take_blocking()` 覆写，使用基类的 `take(HAL_SEMAPHORE_BLOCK_FOREVER)` 行为（永久阻塞 + 优先级继承），或确保超时后正确处理。

### 5. D-Cache 与 GPIO MODER/ODR 写入冲突（STM32F7 特有）

| 方面 | 理论 ✅ | 实测 ❌ |
|------|---------|--------|
| 外设区(0x40000000-0x5FFFFFFF) 缓存策略 | 应为 Device(non-cacheable) | D-Cache 仍介入读-改-写路径 |
| `rt_pin_mode(PIN_MODE_OUTPUT)` 效果 | MODER 应写为 output(01) | 写被**静默丢失**，MODER 保持 AF(10) |
| `HAL_GPIO_Init()` 后 GPIO 配置 | 应为正确的复用功能 | 其他引脚的 RMW 可能**意外重置本引脚** |
| DSB 后重写 | 写入应生效 | ✅ DSB后 `volatile` 直接写生效 |

**根因**：STM32F7 的 SCB_CCR.DC (bit 17) 启用 D-Cache 后，即使 MPU 将外设区域标记为 Device Memory，Cortex-M7 的 L1 D-Cache 仍可能在 read-modify-write 路径中提供**陈旧数据**。具体机制：

1. 某代码路径读取 GPIOx_MODER（D-Cache miss → 从硬件读 → 写入 D-Cache）
2. 另一代码路径写到 GPIOx_MODER（写入直接到 D-Cache，不一定穿透到硬件——`rt_pin_write` 使用 `__IO` 但中间层如 `rt_pin_mode` 的 RMW 可能绕不开 D-Cache 污染）
3. 后续读-改-写使用 D-Cache 中的陈旧值 → 上一个写入被**静默覆盖**

**观察到的症状**：
```
// GPIOE MODER 值（SENSORS_EN=PE3 应为 output=01）
// 预期：0x0802_214a (PE3=01 output)
// 实际：0x0802_216a (PE3=10 AF — 被 SPI4 init 盖回)
//                              ^^ 位7:6
```

**已验证的修复**：使用直接 `volatile` 指针写 + `__DSB()` 屏障：

```c
#define _GPIO_REG(port, offset) \
    (*(volatile uint32_t *)(0x40020000UL + (port) * 0x400UL + (offset)))

/* GPIOE PE3: set bits [7:6] = 01 (output), ODR bit 3 = 1 (HIGH) */
_GPIO_REG(4, 0x00) = (_GPIO_REG(4, 0x00) & ~(3UL << 6)) | (1UL << 6);
__DSB();
_GPIO_REG(4, 0x14) |= (1UL << 3);
__DSB();
```

**`volatile` 关键字在此至关重要**——它阻止编译器将读结果缓存在寄存器中，强制每次访问穿过到 AXI 总线。配合 `__DSB()`，确保写入在总线前端完成任何后续读之前到达外设。

**特定场景：CUAV V5 PE3 — VDD_3V3_SENSORS_EN**

| 步骤 | 代码 | 效果 |
|------|------|------|
| 1 | SPI4 `HAL_GPIO_Init(PE12/13/14)` (INIT_BOARD_EXPORT) | RMW GPIOE_MODER → PE3 被清零为 00 (input) |
| 2 | `_sensor_power_init()` (INIT_PREV_EXPORT, 级别 2) | `rt_pin_mode()` 写入可能被 D-Cache 干扰 → PE3 仍为 input |
| 3 | 后续外设读-改-写 | 使用陈旧的 MODER → PE3=01 的写入被覆盖丢失 |

**`INIT_PREV_EXPORT` 时序陷阱（2026-05-12 发现）**：

RT-Thread 初始化顺序（从早到晚）：
1. `INIT_BOARD_EARLY_EXPORT` (0)
2. `INIT_BOARD_EXPORT` (1) ← SPI4 `HAL_SPI_MspInit` 在此阶段运行
3. `INIT_PREV_EXPORT` (2) ← `_sensor_power_init()` 在此阶段运行
4. `INIT_DEVICE_EXPORT` (3)
5. ...

虽然 `_sensor_power_init()` 在 SPI4 之后运行，但它的写入通过 `rt_pin_mode()`（`__IO` 中间层）进行，可能被 D-Cache 缓存而未穿透到硬件寄存器。当后续 GPIO 操作（如 `HAL_GPIO_TogglePin`、`HAL_GPIO_WritePin`）对 GPIOE 做 RMW 时，D-Cache 提供陈旧 MODER 值，静默覆盖了 `_sensor_power_init()` 的写入。

**修复归属**：在 `rtt_run_cpp_ctors()`（`libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c` line ~330）中或 `rt_hw_board_init()` 最后用**直接 volatile 指针写 + `__DSB()`** 设 PE3：

**修复归属**：`_sensor_power_init()` 在 `libraries/AP_HAL_RTT/hwdef/common/board/rt_board_init.c` 中使用直接寄存器写 + DSB，绕开了 `rt_pin_mode()` 的 RMW 路径。

| `references/get-semaphore-fix-implementation.md` | get_semaphore() 总线锁对齐修复的完整 diff — 2026-05-13 会话实施记录 |

### 7. 线程优先级体系 — setup 阶段主线程降级策略（2026-05-13 新增）

> **这是 RTT 启动慢的核心根因。** 本会话中廖博士两次批评"不准猜测、必须精读 ChibiOS"后方才定位到此差异。

**ChibiOS 优先级体系**（数字越大优先级越高，RT-Thread 规则相反）：

| 线程 | ChibiOS | RTT（当前） | 对比结论 |
|------|---------|------------|---------|
| 主循环 | **180** | prio 5（数字小=高） | 主循环优先级正确 |
| 定时器/SPI | **181**（比主线程高1） | prio 4（比主线程高1） | ✅ 对齐 |
| UART | **60**（远低于主循环） | prio 6（比主线程低1） | ⚠️ RTT 中 UART 和主线程几乎同级，抢占频繁 |
| IO | **58** | prio 18 | ✅ 对齐（都最低） |
| **setup 期间主线程** | **10（降到最低！）** | **prio 5（不降低）** | ❌ **RTT 不降级！** |

**ChibiOS `main_loop()` 的 setup 优先级流程**（`HAL_ChibiOS_Class.cpp:230-317`）：

```cpp
void main_loop() {
    chThdSetPriority(APM_MAIN_PRIORITY);     // 设 180
    ...
    hal_chibios_set_priority(APM_STARTUP_PRIORITY);  // ↓ 降到 10 ★
    schedulerInstance.hal_initialized();              // 通知 timer 开始
    g_callbacks->setup();                             // setup 在低优先级下运行
    ...
    chThdSetPriority(APM_MAIN_PRIORITY);              // ↑ 恢复 180
    while (true) { g_callbacks->loop(); ... }         // 主循环
}
```

**RTT `_main_loop_entry()` 的缺失**（`HAL_RTT_Class.cpp:164-205`）：

```cpp
void _main_loop_entry() {
    // ★ 没有降级
    a->callbacks->setup();          // 主线程保持 prio 5 运行 setup
    a->sched->set_system_initialized();
    for (;;) { loop(); ... }
}
```

**影响分析**：
- ChibiOS startup priority = 10（极低），setup 中的每个 delay() 真实让出 CPU 给 UART(60)、timer(181) 等线程 → IOMCU 上传流畅、传感器初始化准确
- RTT 主线程保持 prio 5，高于 UART(6) → UART 在 setup 中得不到足够 CPU 时间 → IOMCU 上传变慢、每个 delay(5) 实际耗时远超 5ms
- 结果：ChibiOS gyro calibration 需 ~30s（设计值），RTT 需 70s+（膨胀 2 倍+）

### ✅ 已实施修复（2026-05-16 会话）

**修改文件**：3 个原子化修改

#### 1. `HAL_RTT_Class.cpp` — 对齐 ChibiOS 5 步启动顺序

```cpp
_main_loop_entry() {
    // Step 1: Set main thread to main priority
    rt_uint8_t main_prio = APM_RTT_MAIN_PRIORITY;  // = 5
    rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &main_prio);

    // Step 2: Drop to startup priority — ChibiOS: HAL_ChibiOS_Class.cpp:265
    rt_uint8_t startup_prio = APM_RTT_STARTUP_PRIORITY;  // = 15
    rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &startup_prio);

    // Step 3: Signal timer/SPI/UART threads — ChibiOS: HAL_ChibiOS_Class.cpp:273
    a->sched->hal_initialized();

    // Step 4: setup() runs at low priority — timer(4)/SPI(8) can preempt freely
    a->callbacks->setup();

    // Step 5: Restore main priority — ChibiOS: HAL_ChibiOS_Class.cpp:317
    rt_thread_control(self, RT_THREAD_CTRL_CHANGE_PRIORITY, &main_prio);
}
```

**`APM_RTT_STARTUP_PRIORITY = 15`**：低于 timer(4)/SPI(4)/UART(6)，高于 IO(18)/storage(16)。确保传感器采集线程在 setup 期间可抢占主线程，但 IO 回调不会干扰初始化。

#### 2. `Scheduler.cpp` — `_hal_initialized` 定时修正

```diff
-    _hal_initialized = true;  // 原来在 init() 末尾 — 线程启动太早！
+    // _hal_initialized 现在由 hal_initialized() 设 true — 在 _main_loop_entry 中 setup 前设置
```

#### 3. `Scheduler.h` — 新增 startup priority 定义

```c
#define APM_RTT_STARTUP_PRIORITY  15   // during setup — below SPI(8)/timer(4), above IO(18)
```

### 为什么 ChibiOS 降优先级有效而 RTT 之前无效

ChibiOS 优先级体系（大=高）：
```
timer(181) > main(180) > startup(10) — timer 可自由抢占 setup
```

RTT 优先级体系（小=高）：
```
timer(4) > SPI(4) > main(5) — timer 可抢占 main
```

**问题不是"优先级不够高"，而是时序**：RTT 原来在 `scheduler->init()` 末尾就设了 `_hal_initialized = true`，所有线程在 `init()` 返回时就已开始运行。setup 虽然是默认优先级(16)，timer(4) 能抢占。但 `set_system_initialized()` 在 setup 完成后才调用，所以 IWDG 和主循环之间的衔接也无问题。

**真正修复的是**：原来 setup 运行时 timer 线程已经跑了几十毫秒到几百毫秒了，正常的传感器采样循环应该已经产生数据。INS 校准挂死另有原因（SPI 数据路径问题，不是线程调度问题）。

### 原子化实施记录

| 文件 | 改动 | ChibiOS 行号 | 验证 |
|------|------|-------------|------|
| `HAL_RTT_Class.cpp` | 降 prio → hal_initialized → setup → 恢复 prio | `:265, :273, :317` | 编译通过 |
| `Scheduler.cpp` | `_hal_initialized` 从 init 移到 hal_initialized | 无直接对应 | 编译通过 |
| `Scheduler.h` | 添加 `APM_RTT_STARTUP_PRIORITY` | `Scheduler.h:35` | 编译通过 |

**完整对比参考**：`references/rtt-vs-chibios-behavior-delta.md` §优先级体系

### 8. I2C 驱动架构：位爆炸 vs 硬件 I2C

| 方面 | ChibiOS ✅ | RTT ❌ |
|------|-----------|--------|
| I2C 驱动类型 | **硬件 I2C 外设**（`I2C3 AF4`） | **GPIO 位爆炸**（`drv_soft_i2c.c` 软 I2C） |
| 实现文件 | `I2CDevice.cpp`（HAL 封装）+ `chibios_hal` I2C LL 驱动 | `drv_soft_i2c.c`（每个 bit 手动 toggle GPIO） |
| 数据速率 | 400kHz（硬件时钟） | ～1-10kHz（GPIO 升降 + udelay + SCL 轮询） |
| CPU 占用 | 极低（中断/DMA 卸载） | 极高（整个传输过程忙等） |
| 对线程调度影响 | 传输期间其他线程可运行 | 传输期间**阻塞所有线程**（位爆炸是 synchronized GPIO 操作） |
| IST8310 探测耗时 | < 1ms | 可达 100ms+（NACK 等超时更可达秒级） |
| 引脚配置 | `hwdef.dat: PH7 I2C3_SCL I2C3 AF4` | `rtconfig.h: BSP_I2C3_SCL_PIN 119` + `drv_soft_i2c.h: SCL=GPIO_PIN_xx` |
| 触发条件 | `BSP_USING_I2C3` 定义时硬件 I2C 被配置 | 同上（`BSP_USING_I2C3` 宏触发位爆炸实现） |

**根因**：ChibiOS fmuv5 的 `hwdef.dat` 声明 I2C3 为 AF4 硬件外设模式，ST 的 HAL 驱动通过 I2C 外设寄存器通信。而 RT-Thread 的 BSP 用相同的 `BSP_USING_I2C3` 宏触发 `drv_soft_i2c.c` 的编译——该文件使用 GPIO 位爆炸，而非 STM32 的硬件 I2C 外设。

**影响**：IST8310 内部磁力计探测是 AP_HAL 启动流程的一部分，完成前 AP 主循环不会启动 → MAVLink 心跳永远不会输出。

**参考**：`rtt-stabilization-driver` skill 的 `references/i2c-bitbang-blocking-diagnosis.md`

## ⚠️ Bootloader 兼容性：App Descriptor — 根因更新（2026-05-16）

> **2026-05-16 修正**：此前根因分析（`app-descriptor-fix-rtt.md`）已过时。A1-Research 发现真正的根因是 scons 缺少 set_app_descriptor() 后处理脚本。详见 `rtt-chibios-11-porting-discipline` skill 的 `⚠️ app_descriptor 基础设施` 节。

**RTT 构建的二进制文件（`rtthread.bin`）中 app_descriptor 的签名字节存在且 board_id=50 正确，但 `image_crc=0`, `image_size=0`, `git_hash=0`**。bootloader 的 `check_good_firmware_unsigned()` 验证失败因为 `len1+desc_len > image_size(0)`，拒绝跳转到 0x08008000。

### 症状
1. OpenOCD 烧录后，PC 停在 bootloader 范围（0x08003000-0x08003xxx）
2. 首次 `reset run` 后可能跳转，但**此后所有独立 reset 都不跳转**
3. CDC ACM 端口存在（bootloader 的 serial update 模式），但无 MAVLink 消息
4. 烧录器校验 `0x08008000` 的向量表正确，但 bootloader 不跳转

### 根因
`app_descriptor` 的放置涉及三层，缺一不可：

**第 1 层：符号层** — `AP_CheckFirmware/AP_CheckFirmwareDefine.h`

```cpp
// ChibiOS 编译时用 __attribute__((section(".app_descriptor")))
// RTT 编译时走 else 分支 → 没有 section 属性！
#if CONFIG_HAL_BOARD == HAL_BOARD_CHIBIOS
const app_descriptor_t app_descriptor __attribute__((section(".app_descriptor"))) = {
#else
const app_descriptor_t app_descriptor = {  // 被 gc-sections 优化掉
#endif
```

必须添加 `|| CONFIG_HAL_BOARD == HAL_BOARD_RTT`。

**第 2 层：宏开关层** — `AP_CHECK_FIRMWARE_ENABLED`

```cpp
// AP_CheckFirmware.h: 默认值是 AP_OPENDRONEID_ENABLED
// 在 RTT 构建中为 0，整个 app_descriptor 定义被 #if 排除
#define AP_CHECK_FIRMWARE_ENABLED AP_OPENDRONEID_ENABLED
```

ChibiOS 的 `chibios_hwdef.py` 强制写 `#define AP_CHECK_FIRMWARE_ENABLED 1` 到 hwdef.h。RTT 的 `rtt_hwdef.py` 必须同样处理。

**第 3 层：链接脚本层** — RTT linker script (`link.lds`)

```ld
SECTIONS {
    .text : {
        KEEP(*(.isr_vector))
        KEEP(*(.apsec_data));    // ← 缺失！
        KEEP(*(.app_descriptor)); // ← 缺失！
        *(.text)
    }
}
```

必须添加，否则链接器丢弃输入 section。

### 修复步骤（缺一不可）

| 步骤 | 文件 | 修改内容 | 验证命令 |
|------|------|---------|---------|
| 1 | `libraries/AP_CheckFirmware/AP_CheckFirmwareDefine.h` | `#if CONFIG_HAL_BOARD == HAL_BOARD_CHIBIOS` → `#if CONFIG_HAL_BOARD == HAL_BOARD_CHIBIOS \|\| CONFIG_HAL_BOARD == HAL_BOARD_RTT` | `/opt/gcc-arm-none-eabi-10-2020-q4-major/bin/arm-none-eabi-nm build/.../rt-thread.elf \| grep app_descriptor` |
| 2 | `libraries/AP_HAL_RTT/hwdef/scripts/rtt_hwdef.py` | 在 `write_hwdef_header_content()` 末尾加 `f.write('#define AP_CHECK_FIRMWARE_ENABLED 1\n')` | `grep AP_CHECK_FIRMWARE build/.../board/hwdef.h` |
| 3 | `libraries/AP_HAL_RTT/hwdef/common/board/linker_scripts/link.lds` | 在 `KEEP(*(.isr_vector))` 后加 `KEEP(*(.apsec_data)); KEEP(*(.app_descriptor));` | `strings -n 4 build/.../rtthread.bin \| grep APFW` |

### 验证方法

```bash
# 编译后检查 app_descriptor 符号
arm-none-eabi-nm rt-thread.elf | grep app_descriptor
# 输出应为: 0800xxxx T app_descriptor

# 检查 bin 中 APFW 魔数
strings -n 4 rtthread.bin | grep APFW

# 烧录后检查 bootloader 是否跳转
openocd -f ... -c "program rtthread.bin 0x08008000 verify" -c "reset run" -c "shutdown"
# 等待 15 秒后: ls /dev/ttyACM* → 应有 ttyACM1
# pymavlink 收心跳 → state=5 (STANDBY)

# 独立 reset 确认持久性
echo -e "reset run" | nc localhost 4444
# 等待 15 秒后 → 应有 ttyACM1 + HEARTBEAT
```

## 正确修复路径速查

| 问题 | 正确修复位置 | 参考 |
|------|------------|------|
| GYRO/ACCEL health 不恢复 | `AP_HAL_RTT/SPIDevice.cpp` — 修 SPI 时序 | `rtt-stabilization-driver` skill |
| USB CDC 缓冲不够 | `AP_HAL_RTT/UARTDriver.cpp` + `.config` | GCC 编译器分支反转 bug 参考 |
| 编译找不到 `rt_kprintf` | `AP_HAL_RTT/system.cpp` — 全局提供空 stub | — |
| 传感器 PROM 读取失败 | `AP_HAL_RTT/SPIDevice.cpp` — 修 SPI4 时序 | — |
| MAVLink 参数下载慢 | `AP_HAL_RTT/UARTDriver.cpp` + submodule usb_glue_st.c | DWC2 FIFO 调优 |
| IOMCU 诊断 | `AP_HAL_RTT/debug.h` — 独立调试模块 | `rtt-stabilization-driver` 的内存变量法 |
| GCS stream rate | `AP_HAL_RTT/Scheduler.cpp` — yield 策略 | 不要改 `AP_Vehicle.cpp` |
| 线程栈不够 | `AP_HAL_RTT/hwdef/common/.config` | — |
| 调试输出 | `AP_HAL_RTT/debug.h` / `system.cpp` | 内存变量法（OpenOCD 读取 volatile）|
| IMU init 卡住（setup_stage=662, loop_iterations=0） | **IOMCU 线程阻塞 + ADC 100Hz gate 竞争** — 先做多线程采样定位：IOMCU `read_registers` 超时 vs SPI 挂死 | `references/iomcu-uart8-debug.md`（skill: `rtt-cuav-v5-flash-verify`）|
| ⚠️ **架构清理导致IOMCU RTT API回退** | **每次Phase清理后必须编译验证IOMCU功能** | IOMCU的ChibiOS→RTT适配补丁（`chEvt*`→`rt_event*`、超时保护、固件上传安全）是最容易被架构清理误回退的模块。3次跨周同构事件（Week 3 USB CDC、Week 7 IOMCU）验证了此模式。|
| ⚠️ 陷阱：用 `!_initialized` 而非 `!_hal_initialized` | `_hal_initialized` 在 Scheduler::init 末尾即 true（早于 setup）；`_initialized` 由 `set_system_initialized()` 在 setup 完成后才设 true | CC 于 2026-05-10 纠正此错误 |
| SPI 锁架构不对齐 | **1. `SPIDevice.cpp`: `get_semaphore()` 返回 `&_bus->semaphore`** 2. **`DeviceBus.cpp`: `take(HAL_SEMAPHORE_BLOCK_FOREVER)` 替代 `take(10)`** 3. **`SPIDevice.cpp`: 显式设 Bus 线程优先级** | 三击齐发：锁返回值+超时策略+优先级 |
| I2C 锁架构不对齐 | **1. `I2CDevice.cpp`: `get_semaphore()` 返回 `&_bus_dev->semaphore`** 2. **`I2CDevice.cpp`: `transfer()` 内锁改用 `_bus_dev->semaphore`** 3. **`I2CDevice.h`: 移除 `Semaphore _sem`** | 与 SPI 同一模式：get_semaphore 必须返回总线锁 (commit `bfe648f60c`) |
| I2CDevice 锁架构不对齐 | **`I2CDevice.cpp`: `get_semaphore()` 返回 `&_bus_dev->semaphore`**（非 `&_sem`），`transfer()` 中使用总线锁替代私有锁 | 与 SPI 相同模式——私有锁暴露给上层 |
| SPIDevice.h 废弃 _sem 成员 | **`SPIDevice.h:53` — 删除 `Semaphore _sem;`**（get_semaphore 已改为返回总线锁） | 残留声明，无功能影响，清理即可 |
| Bootloader 不跳转 | **添加 `.app_descriptor` 到链接脚本** 或 使用 `uploader.py` | `ap-chibios-bootloader-reference` skill |
| 循环率 263Hz → 400Hz | **非 SPI DMA 能解决** — 定量分析：SPI1 DMA 仅贡献 ~4% (263→275Hz)，真正瓶颈在调度器/定时器抢占 | `references/performance-bottleneck-analysis.md` |
| ⚠️ **P0 ADC DMA 导致 main loop 不迭代** | **`AnalogIn.cpp` 中双重问题：① ADC 使能在 DMA 配置之前 → ADC 向地址 0 发请求 ② DMA ISR 因遗留 TCIF 自触发 → CPU 100% 在 ISR 中**。**修复**：先配 DMA + 清 LIFCR + 使能中断 → **最后**使能 ADC CR2。见 `references/p0-adc-dma-ispitfall.md` |
| ✅ **P0 100Hz gate 提升循环率 263→1387Hz** | `_timer_tick` 中加 `AP_HAL::micros()` gate → 仅每 10ms 处理一次 ADC，1kHz 轮询降为 100Hz | 纯软件优化，收益远超 DMA |

## 代码审查 Checklist

- [ ] 是否修改了 `libraries/` 中 `AP_HAL_RTT/` *以外* 的文件？
- [ ] 如果有，是否真的是**通用 bug**（不依赖 RTT 平台）？
- [ ] 是否引入了 `#ifdef HAL_RTT`？
- [ ] 是否引入了 RTT 专有 API 调用？
- [ ] 堆栈、缓冲、线程参数是否在 `AP_HAL_RTT/hwdef/` 中配置？
- [ ] HAL RTT 内的问题是否已在 `AP_HAL_RTT/` 内解决？
- [ ] 是否检查了 `references/hal-comparison-matrix.md` 确认模块是否已有？
- [ ] **I2CDevice/SPIDevice `get_semaphore()` 是否返回总线锁（`&_bus->semaphore` / `&_bus_dev->semaphore`）而非私有 `_sem`？**（常见遗漏）
- [ ] **DeviceBus 线程是否使用 `HAL_SEMAPHORE_BLOCK_FOREVER` 而非短超时？**（10ms 超时导致静默跳过 callback）
- [ ] **SPIDevice.h/I2CDevice.h 是否有废弃的 `_sem` 成员未被清理？**（SPI 已修复，I2C 仍残留）

## 参考文件索引

| 文件 | 场景 |
|------|------|
| `references/hal-comparison-matrix.md` | 需要了解哪个 HAL 模块 RTT 缺失/已有/损坏时 |
| `references/cleanup-and-rebuild-plan.md` | 开始代码清洁或重建工作时 |
| `references/rtt-vs-chibios-behavior-delta.md` | delay() / SPI锁 / DWT忙等 / bootloader 行为差异详细分析 |
| `references/boot-hang-debugging-methodology.md` | 启动卡死 (setup_stage=662) 调试 — mdw/addr2line/SPI寄存器/DeviceBus锁级 |
| `references/app-descriptor-fix-rtt.md` | Bootloader 不跳转时的完整三文件修复方案 |
| `references/chibios-setup-priority-analysis.md` | **ChibiOS setup 优先级降级的精确代码分析（2026-05-13 新增） — 廖博士纠正后的核心发现** |
| `references/performance-bottleneck-analysis.md` | **性能瓶颈分析（2026-05-14 新增） — 263Hz→400Hz 各优化项量化收益对比，包含 SPI DMA 收益仅 ~4% 的结论** |
| `references/mcu-monitoring-implementation.md` | **MCU 温度/Vrefint 监测实现 (2026-05-16) — ADC3 轮询 20Hz，公式参考 ChibiOS AnalogIn.cpp L743-768** |
| `references/softsigreader-implementation-plan.md` | **SoftSigReader RC 输入捕获 — C1-Research 输出 (2026-05-16)** |
| `references/shared-dma-implementation-plan.md` | **Shared_DMA DMA 流仲裁 — C2-Research 输出 (2026-05-16)** |
| `references/can-driver-implementation-plan.md` | **CAN Bus Driver bxCAN 直接寄存器 — C3-Research 输出 (2026-05-16)** |
