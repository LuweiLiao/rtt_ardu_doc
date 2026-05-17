# Gyro Init Hang — 六变量诊断法

## 适用场景

- `rtt_dbg_setup_stage` = 662（`startup_INS_ground()` 中 `ins.init()` 前）
- `copter.ap.initialised` = 0（setup() 从未完成）
- USB CDC 已枚举，有前期启动文本（"Init ArduCopter", "IOMCU startup" 等）
- CFSR=0, HFSR=0（无硬件异常）
- 重启后 17+ 分钟系统仍在运行但不输出任何新内容

## 诊断流程

### Step 1: 确认卡在 ins.init()

```bash
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x rtt_dbg_setup_stage" \
  -ex "p/x copter.ap.initialised" \
  -ex "p/x AP_HAL::millis()" \
  -ex "monitor resume" \
  /path/to/rt-thread.elf
```

结果预期：`rtt_dbg_setup_stage = 0x296`（662）、`initialised = 0`、`millis` 在 15 分钟以上。

### Step 2: 六变量读

```bash
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p AP::ins()._calibrating_gyro" \
  -ex "p AP::ins()._calibrating_accel" \
  -ex "p/x AP::ins()._sample_period_usec" \
  -ex "p AP::ins()._loop_rate" \
  -ex "p/x AP::ins()._gyro_count" \
  -ex "p/x AP::ins()._gyro_cal_ok" \
  -ex "monitor resume" \
  /path/to/rt-thread.elf
```

### Step 3: 解读结果

#### 模式 A：Gyro init 未返回（最常见）

| 变量 | 值 | 含义 |
|------|----|------|
| `_calibrating_gyro` | true | `_init_gyro()` 正在执行 |
| `_sample_period_usec` | 0 | `init_gyro()` 后的代码未执行 |
| `_loop_rate` | 400 | `ins.init()` 已进入且 `_loop_rate` 已设 |
| `_gyro_count` | 2 | `_start_backends()` 完成 |
| `_gyro_cal_ok` | {1, 1} | ⚠️ **构造函数初始值，不代表校准完成！** |

**结论**：`_init_gyro()` 内部卡住，35 秒绝对超时也未能返回。

#### 模式 B：Gyro init 完成但 save 阻塞

| 变量 | 值 | 含义 |
|------|----|------|
| `_calibrating_gyro` | **false** | `_init_gyro()` 已完成 |
| `_sample_period_usec` | 0 | 但 `init_gyro()` 包装函数的后续部分未执行 |
| `_loop_rate` | 400 | `ins.init()` 已进入 |

**结论**：卡在 `init_gyro()` → `_save_gyro_calibration()` 中，FRAM/Flash write 阻塞。

#### 模式 C：正常完成

| 变量 | 值 | 含义 |
|------|----|------|
| `_calibrating_gyro` | false | `_init_gyro()` 完成 |
| `_sample_period_usec` | 2500 | `init_gyro()` 后代码已执行 |
| `_loop_rate` | 400 | 正常 |

**结论**：setup 卡点在别处（baro/compass/GPS init 等）。

## 为什么 35 秒超时不触发？

`_init_gyro()` 有外层和内层两重循环：

```cpp
// 外层 (line 1752-1815)
for (int16_t j = 0; j <= 30*4 && num_converged < num_gyros; j++) {
    if (AP_HAL::millis() - gyro_init_start_ms > 35000U) break;  // 壁钟超时
    
    for (i=0; i<50; i++) {  // 内层 (line 1772-1778)
        update();
        gyro_sum[k] += get_gyro(k);
        hal.scheduler->delay(5);  // ← 潜在的永久阻塞点
    }
}
```

超时检查在**外层循环头部**。如果内层的某个 `delay(5)` 永远不返回，外层的超时检查也永远不执行。

`delay(5)` 的实现：
```cpp
while ((micros64() - start) / 1000 < 5) {
    delay_microseconds(1000);  // → rt_thread_delay(1)
    call_delay_cb();           // → gcs().update_send()
}
```

理论上 `delay_microseconds(1000)` 调用 `rt_thread_delay(1)` 至少等待 1 个 tick。实际证明 17 分钟仍未返回 → 主线程的 `rt_thread_delay` 唤醒出现了问题。

**未验证的根因猜测**：
1. 主线程（优先级 3）的 `rt_thread_delay(1)` 没有正确被 tick ISR 唤醒 → 可能是因为 tik ISR 中的 PendSV 没有切换到主线程
2. 主线程被更高优先级线程持续抢占（monitor 线程优先级 2 > 主线程 3）
3. RT-Thread tick 处理中的某种竞争条件

## 调试计数器法

在 `AP_InertialSensor.cpp` 中添加全局变量：

```cpp
// 在文件顶部（include 之后）
volatile uint32_t rtt_dbg_gyro_loop = 0;
```

在 inner loop 内（`_init_gyro()` 约 line 1772-1778）：

```cpp
for (i=0; i<50; i++) {
    update();
    for (uint8_t k=0; k<num_gyros; k++) {
        gyro_sum[k] += get_gyro(k);
    }
    rtt_dbg_gyro_loop = j * 1000 + i + 1;             // delay 前写入
    hal.scheduler->delay(5);
    rtt_dbg_gyro_loop = j * 1000 + i + 1 + 50000;     // delay 后写入
}
```

读取：
```bash
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x rtt_dbg_gyro_loop" \
  -ex "monitor resume" \
  /path/to/rt-thread.elf
```

| 值范围 | 含义 | 下一步 |
|--------|------|--------|
| **0** | inner loop 从未进入 | 检查 `_update_gyro()` 或 SPI backend 是否阻塞 |
| **< 50000** | pre-delay 写入执行，post-delay 未执行 | **`Scheduler::delay()` 永远不返回** → 检查 RT-Thread tick/调度 |
| **≥ 50000** | delay 正常完成 | gyro init 在推进只是慢 |
| 最终值（120次迭代后） | 循环正常结束 | 检查后续阻塞点 |

## 架构层次图

```
AP_Vehicle::setup()
  → Copter::init_ardupilot()              [stage: 600-650]
    → Copter::startup_INS_ground()        [stage: 660-664]
      → ahrs.init()                       [stage: 660→661]
      → ahrs.set_vehicle_class()          [stage: 661→662]
      → ins.init(loop_rate)               [stage: 662→663]
        → _start_backends()               [gyro_count=2, accel_count=2]
        → init_gyro()                     [wrapper]
          → _init_gyro()                  [_calibrating_gyro=true]
            → inner loop (50×delay(5))    [rtt_dbg_gyro_loop tracking]
          → _save_gyro_calibration()      [_calibrating_gyro=false]
        → _sample_period_usec = ...       [=2500 if loop_rate=400]
```

## Git 工作区状态注意事项

- 检查 `git status` 确认 AP_InertialSensor.cpp 的修改（`rtt_dbg_gyro_loop` 添加）已正确保存
- submodule `modules/rt-thread` 的 gitlink 是否与预期一致
- 编译前确认 `scons --target=cuav_v5` 而非旧目标

## 参考

- 主 skill: `rtt-stabilization-driver`
- SPI 信号量分析: 主 skill §第十步
- 本会话: 2026-05-12 深度调试，17 分钟找出 gyro_init(...)` 内 `delay(5)` 阻塞
