# Stage 662: Invensense WHO_AM_I 通过但初始化不完成

## 现象

| 诊断指标 | 值 | 结论 |
|---------|-----|------|
| Setup stage | 662（`ins.init()` 中） | ⛔ 阻塞 |
| `rtt_dbg_inv_init_step` | 5（WHO_AM_I OK） | ICM20689 SPI 通信正常 |
| CFSR/HFSR | 0 | 无异常 |
| PC | `_delay_microseconds_dwt` | 正常执行延时 |
| USB CDC | 枚举但无数据 | 初始化未完成，主循环未启动 |

## 诊断思路

当单个组件（Invensense WHO_AM_I 通过、ADC 修复生效）看似工作正常，但系统整体卡在 stage 662 时，根因通常不是「某个组件完全坏掉」，而是**多个串行依赖中的一个子步骤阻塞**。

### 检查清单

#### 1. 后台 _backend_count（确认 IMU 探测是否成功）

```bash
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p AP::ins()" \
  -ex "p/x AP::ins()._backend_count" \
  -ex "p/x AP::ins()._gyro_count" \
  -ex "p/x AP::ins()._accel_count" \
  -ex "monitor resume"
```

- `_backend_count == 0` → 所有 IMU 探针失败（SPI 总线或 semaphore 问题）
- `_backend_count > 0` 但 `_gyro_count == 0` → 探针成功但 gyro 校准阻塞

#### 2. Invensense _hardware_init() 的完整状态

安装诊断变量到 `AP_InertialSensor_Invensense.cpp`（用 `#if CONFIG_HAL_BOARD == HAL_BOARD_RTT` 守卫）：

```cpp
#if CONFIG_HAL_BOARD == HAL_BOARD_RTT
static volatile uint32_t rtt_dbg_inv_init_step;  // 1-9 不等
static volatile uint32_t rtt_dbg_inv_init_whoami;
static volatile uint32_t rtt_dbg_inv_init_reg;
#endif
```

然后通过 OpenOCD 监视。Step 值含义：
| Step | 含义 |
|------|------|
| 1 | BEFORE semaphore take |
| 2 | AFTER semaphore taken |
| 3 | Speed set + setup done |
| 4 | WHO_AM_I FAILED |
| 5 | WHO_AM_I OK |
| 6-8 | 芯片复位/寄存器配置中 |
| 9 | _init_done = true，返回成功 |

如果卡在 step 5-8 之间（PC 永远在 `_delay_microseconds_dwt`）：
- 芯片复位循环卡住：`_register_write(MPUREG_PWR_MGMT_1, BIT_H_RESET)` 后 `delay(10)` 等待复位完成
- 复位命令写入了但芯片不响应 → SPI 超时→每次 `delay(10)` 后检查寄存器发现 BIT_H_RESET 仍未清除
- semaphore 超时（100ms）后 transfer 返回 false → 但上层（Invensense 驱动）不检查返回值 → 继续调用 delay

#### 3. Semaphore 超时 vs FOREVER 的差异

| 模式 | 行为 | 诊断 |
|------|------|------|
| `HAL_SEMAPHORE_BLOCK_FOREVER` | 如果 semaphore 被占，永远阻塞 | PC 在 `_rt_mutex_take` 中，非 DWT delay |
| `rt_mutex_take(..., 100ms)` | 超时后返回 false，transfer 失败 | PC 在 DWT delay（上层代码的延时循环中） |

如果设为 100ms 后 PC 仍在 DWT delay，说明 semaphore 正常获取但 SPI 传输本身超时（设备不响应）。

#### 4. Phase 0 清理后的补偿需求

Phase 0 清理 (`062a16fb8d`) 从 `libraries/AP_InertialSensor/` 移除了以下 RTT 兼容代码：

| 移除内容 | 原位置 | 影响 |
|---------|--------|------|
| Gyro 校准跳过 | `AP_InertialSensor.cpp init()` | 恢复 `init_gyro()` 8 秒校准，期间 SPI 必须稳定 |
| 健康位强制 | `AP_InertialSensor.cpp` | 不直接影响 init 阻塞 |
| SPI 超时容错 | `AP_InertialSensor_Invensense.cpp` | 不直接影响 init 阻塞 |

**关键**：`init_gyro()` 在被恢复后需要连续 SPI 通信 8 秒。如果 SPI 有任何间歇性超时，gyro 校准会失败/重试，延长阻塞时间。

### 常见阻塞场景

```
Invensense ICM20689 probe 开始
  → WHO_AM_I 通过 (step 5) ✅
  → 芯片复位 (write BIT_H_RESET, delay 10ms, read back)
    → 可能: 复位命令未正确写入 → 等待超时 → 重试 → 耗时 50-500ms
  → 配置寄存器 (sample rate, DLPF, FIFO)
    → 可能: 某个 register write 因 SPI 超时而失败 → 无返回值检查 → 继续
  → _init_done = true → 返回 true
→ ICM20602 probe 开始 (同类型驱动，但地址不同)
  → WHO_AM_I 可能失败 (不同芯片) → 快速跳过
→ BMI055 probe 开始 → ...
→ 所有 IMU 探测完成 → _start_backends() → init_gyro() 开始
  → 8 秒校准 → 可能因为 SPI 间歇性超时而阻塞
```

## 修复方向

### 在 AP_HAL_RTT 层（不修改 libraries/ 通用代码）

1. **SPI semaphore 超时保护** — 所有 `HAL_SEMAPHORE_BLOCK_FOREVER` 改为 100ms，`_lock_bus()` 的 `RT_WAITING_FOREVER` 也加超时
2. **Gyro 校准加速** — 在 `AP_HAL_RTT/Scheduler.cpp` 提供加速校准的机制（如缩短采样间隔）
3. **IMU 驱动探针的 SPI 重试** — 在 `SPIDevice.cpp` 的 register-level polling 路径中增加失败重试
4. **Phase 0 清理后遗症补偿** — 被清理的 RTT 兼容代码功能需在 AP_HAL_RTT 层以合规方式重新实现
