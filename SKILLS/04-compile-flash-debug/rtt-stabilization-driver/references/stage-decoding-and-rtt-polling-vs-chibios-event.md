# Setup Stage 解码与 RTT 轮询 vs ChibiOS 事件驱动架构差异

## 场景说明

三层阻塞修复链（Flash 无 yield ✅、CONFIG_DEBUG_ASSERT 关闭 ✅、setup_priority=8 ✅）全部到位后，系统仍卡在 stage 630+。此时**不是某个函数挂死**，而是 **RTT 的调度器特性导致 setup 线程得不到 CPU**。

---

## Stage 完全解码表（ArduCopter CUAV V5）

### Phase 1: Storage (500-503)

| 值 | 含义 | 所在文件:行号 |
|----|------|-------------|
| 500 | `_storage_open` 进入 | `AP_HAL_RTT/Storage.cpp:24` |
| 501 | 尝试 FRAM (SPI2) | `AP_HAL_RTT/Storage.cpp:29` |
| 502 | 尝试 Flash 存储 | `AP_HAL_RTT/Storage.cpp:55` |
| 503 | 使用 RAM stub | `AP_HAL_RTT/Storage.cpp:63` |

### Phase 2: init_ardupilot (600-689)

这些值来自 `ArduCopter/system.cpp` + `Copter::init_ardupilot()`。通过反汇编确认（`arm-none-eabi-objdump -d`）：

```asm
8026e8e: movw r3, #630   ; 0x276
8026e92: str r3, [r5, #0]  ; stage=630 → memory
8026e94: bl 80619ac <AP_GPS::init>  ; call GPS::init()
```

| 值 | 含义 | 关键函数 |
|----|------|---------|
| 600-615 | 前半段 init（串口管理器、参数、notify 等） | 多源 |
| 616 (0x268) | `init_rc_in()` 前 | |
| 620 (0x26C) | `init_rc_in()` 后 | |
| **630 (0x276)** | **`AP_GPS::init()` — 本节诊断焦点** | `AP_GPS::init()` |
| 631 (0x277) | Compass HIL_MODE 设置 | |
| 632 (0x278) | Compass init | `Compass::init()` |
| 633 (0x279) | PathPlanner init | `AP_OAPathPlanner::init()` |
| 634+ | OpticalFlow, Mount, Camera | |
| 640 (0x280) | Baro calibrate | `AP_Baro::calibrate()` |
| 641 (0x281) | Rangefinder init | `Copter::init_rangefinder()` |
| 651 (0x28B) | ins.init() 进入 | `AP_InertialSensor::init()` |
| 662 (0x296) | gyro calibration / IMU probe | `AP_InertialSensor::_init_gyro()` |
| 672 (0x2A0) | AHRS reset | `AP_AHRS::reset()` |
| 675+ | Scheduler init | `AP_Scheduler` |
| 681+ | 参数加载、初始化完成 | |

---

## RTT 轮询 vs ChibiOS 事件驱动：核心架构差异

### 为什么相同策略在 ChibiOS 上有效，RTT 上无效？

**ChibiOS** 的 UART 驱动和 Timer 线程是**事件驱动**的：
- `UARTDriver` 线程阻塞在信号量上（`chnReadTimeout()` / `chnWrite()`） → 无数据时不消耗 CPU
- Timer 线程在 `chThdSleep()` 间期被挂起 → 只在实际 tick 中断后短暂就绪
- 因此 setup 前降优先级后，**高优先级线程自然阻塞**，低优先级的 setup 线程自动获得 CPU

**RTT** 的 UART 驱动和 Timer 线程是**轮询驱动**的：
- `ap_uart` 线程在 `drain()` 中轮询 `rt_device_read()` → **每 tick 都就绪一次**
- `ap_timer` (1kHz) 每次 tick 都调度 `AnalogIn._timer_tick()` + 其他定时器回调 → **每 ms 都就绪**
- 两个更高优先级线程组成 "夫妻档"：timer 唤醒 → 运行 → 挂起 → UART 唤醒 → 运行 → 挂起 → 下一 tick timer 又唤醒 → ...
- **setup 线程 (prio 8 或 6) 在两个就绪线程之间永远排不上队**

### 优先级与就绪行为对照表

| 线程 | RTT 优先级 | 就绪行为 | ChibiOS 等价优先级 | ChibiOS 就绪行为 |
|------|-----------|---------|-------------------|-----------------|
| ap_timer | 4 | **始终就绪**（1kHz 轮询） | 可变 | Tick中断时短暂就绪 |
| SPI1 bus | 5 | 偶发（回调触发） | 可变 | 信号量触发的短暂就绪 |
| ap_uart | 6 | **始终就绪**（轮询 drain） | 可变 | 阻塞在信号量 → 无数据时不就绪 |
| **setup 主线程** | **8** | 等待 CPU | (自动获得CPU) | 高优先线程自然阻塞后获得CPU |
| ap_io | 18 | 后台 | — | — |
| idle | 255 | 永斥 | (always last) | (always last) |

### 根因总结

**不是任何一个函数挂死，而是调度器饥饿**：setup 线程虽然就绪，但每次 scheduler 选择下一个就绪线程时，都有 ap_timer (prio 4) 或 ap_uart (prio 6) 排在前面。这是轮询调度模型与优先级抢占模型的固有特征。

### 验证方法：区分"真卡死" vs "调度饥饿"

```bash
# 30秒读一次 stage，观察变化
for i in 0 30 60; do
    sleep $i
    echo "halt; mdw 0x2001bc84 1; resume" | timeout 10 nc localhost 4444 2>/dev/null | grep "^0x"
done
# 如果 stage 变化（哪怕很慢）→ 调度饥饿
# 如果 stage 不变且每次 PC 相同 → 真卡死
```

如果 60 秒后 stage 从 630→631 或跳到 640+，就是调度饥饿。阶段变化本身也说明 `AP_GPS::init()` 返回了（它本身没有阻塞循环）。

### 修复方向

| # | 方案 | 优先级值 | 说明 |
|---|------|---------|------|
| A | setup_priority=**6** | 6 (与UART同级) | UART(6) 同优先时间片轮转 → 主线程获得 ~1/3 CPU |
| B | 跳过 ADC _timer_tick | 不改变优先级 | `AnalogIn._timer_tick()` 内检查 `!hal.util->get_soft_armed() && !rtt_dbg_hal_run_called` → skip ADC 8通道读取 |
| C | Timer priority=8 | 临时改 timer | 复杂，需改 timer 线程创建点 |
| D | 壁钟超时跳过 | 不改优先级 | 在耗时的 `init()` 函数内加超时（见 gyro init） |

方案 A 最简单，推荐先试。如果不行，方案 B 不影响 ArduPilot 上层的执行路径。
