# Setup Hang 诊断 — rtt_dbg_setup_stage 三阶段法

## 🆕 2026-05-11 重要更新：两阶段 Boot Hang 模式 + 基线验证陷阱

### 两阶段 Boot Hang 模式

本会话（2026-05-11）发现 RTT ArduPilot 在 setup 阶段存在**两个分离的挂起点**：

| 阶段 | Stage | 挂起现象 | 行为 | 根因 |
|------|-------|---------|------|------|
| **Phase A** | 502 | Flash storage 擦除等待 | **~10 秒后自恢复** | AP_FlashStorage 全扇区擦除在 busy-wait loop 中等 IWDG 超时 |
| **Phase B** | 662 | INS init 永久挂起 | **永不恢复**，main 线程在 DWT spin | IMU 探测中芯片复位循环 5×100ms delay → `call_delay_cb()` 死锁 |

#### Phase A: Stage 502 诊断特征

```bash
# 启动后 ~3 秒读 stage
echo 'halt' | nc -q 1 localhost 4444
echo 'mdw 0x<setup_stage_addr>' | nc -q 1 localhost 4444
# → 0x1F6 = 502

# 等待 10+ 秒后再读
# → 已推进到 620+ (如果 phase B 也通过了)
```

**Stage 502 源码位置**（ArduPilot 启动路径）：
```
init_ardupilot() → init_ardupilot_core() → init() 中
AP_FlashStorage::init() 或 AP_FlashStorage::erase_part() 
→ 全扇区擦除在 IWDG 未喂狗期间卡住 → 实际是 wait 而不是 hang
```

**Phase A 不是真正的问题**：flash storage 的全扇区擦除操作本身较慢（~10 秒）。如果 IWDG 未启用，系统会自然通过。如果 IWDG 已启用（超时 0.5-2 秒），擦除完成前系统已被复位。但在开发阶段 IWDG 通常未启动 → **Phase A 自恢复**。

#### Phase B: Stage 662 永久挂起

诊断方法见下方「三阶段诊断实战」的详细内容。关键特征：

```bash
# 等待 Phase A 恢复后（~15 秒），再读 stage
echo 'halt' | nc -q 1 localhost 4444
echo 'mdw 0x<setup_stage_addr>' | nc -q 1 localhost 4444
# → 0x296 = 662 — 已通过所有前期初始化，卡在 ins.init()
```

**关键诊断信号**：如果在 Phase A 恢复后 stage 不变且持续卡在 662，说明：
- 所有前期初始化（flash、battery、baro、GPS、compass）都通过了 ✅
- 问题被精确定位到 `AP_InertialSensor::init()`

### ⚠️ 基线验证陷阱（2026-05-11 关键发现）

**灾难性场景**：被问及「是否 baseline 本身也卡住」时，自然步骤是回退到干净的 baseline 提交，重新编译烧录。

但 baseline commit 的年代（`a632415295`, 2026-05-09）的构建产物已被覆盖。重建后：

```bash
# ❌ 以为能正常工作
git checkout a632415295
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)

# 烧录后 — 同样阶段 662 挂死！
# 花了 3 小时在 direct-write 改动上找原因
```

**教训**：**baseline commit 也可能有同样的 bug** — 特别是当 baseline 本身就是在移植开发中的半成品。不要理所当然地认为「回到 baseline 就好了」。

**正确做法**：
1. **不要急于回退**。先问：这个 bug 会不会是 baseline 本身就有的？
2. 检查 baseline 的历史记录：IMU init 是否曾有改动？基线在什么时候达到过 L0？
3. **在回退 baseline 之前**，先通过 OpenOCD 读取 baseline 固件硬盘上是否还保留着旧的 `.bin` 或 `.elf` 文件
4. 如果必须重建 baseline，准备接受 baseline 也同样卡住的可能性

**具体到本会话的教训**：
- baseline commit `a632415295` 虽然标注了 L0 通过记录，但 IMU init hang (stage 662) 是**所有固件共有的**——无论是 baseline 还是我们的 direct-write 分支
- 这意味着 bug 存在于 ArduPilot RTT 移植的更早阶段（可能是 build system 变化、submodule Pin 变化等）

### Stage 映射表扩展

在原有映射表基础上补充：

| Stage | 位置 | 意义 | 自恢复？ |
|-------|------|------|---------|
| **502** | **Flash storage init** | **全扇区擦除等待** | ✅ ~10s 后自恢复 |
| ... | ... | ... | ... |
| 662 | `ins.init()` 前 | IMU 初始化 | ❌ 永久挂起 |

### Phase A 的诊断确认（区分真·死锁和自恢复等待）

当第一次读到 stage=502 时，不要立即判定为永久死锁。用**三样本法**确认：

```bash
# 样本 1: 启动后 ~5 秒
echo 'resume' | nc -q 1 localhost 4444   # 先 resume，否则读了也白读
sleep 5
echo 'halt' | nc -q 1 localhost 4444
echo 'mdw 0x<setup_stage_addr>' | nc -q 1 localhost 4444
# → 0x1F6 (502)
echo 'resume' | nc -q 1 localhost 4444

# 样本 2: + 10 秒
sleep 10
echo 'halt' | nc -q 1 localhost 4444
echo 'mdw 0x<setup_stage_addr>' | nc -q 1 localhost 4444
# → 如果 > 502 (如 620) → 是自恢复，不是真死锁
# → 如果仍是 502 → 真死锁
```

---

## 来源
2026-05-10 会话：启用 IOMCU 后系统在 sensor init 卡住，CDC 短暂数据流后静默。
强制通过"先分析再解决"原则，用 stage 变量 + GDB + hwdef.h 交叉分析定位阻塞点。

## 问题描述
- 系统启动后：ttyACM1 枚举 → 短暂 RAW_IMU/ATTITUDE/HEARTBEAT 数据流 → 静默
- OpenOCD 检查：CFSR=0, HFSR=0 (无硬件异常)
- 主线程 PC 重复在 `_delay_microseconds_dwt` (Scheduler.cpp:72)
- IWDG: PR=0, RLR=0xFFF (默认值 — set_system_initialized() 未被调用)
- 定时器线程在 `_adc_read()` (AnalogIn.cpp:90) 反复超时

## 三阶段诊断实战

### 阶段 1: 读 stage 变量

```bash
# 查找变量地址
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rtt_dbg_setup_stage
# → 0x2001bf34 B rtt_dbg_setup_stage

# halt 后读
echo 'halt' | nc -q 1 localhost 4444
echo 'mdw 0x2001bf34' | nc -q 1 localhost 4444
# → 0x00000296 = 662 十进制
```

**Stage 662 含义**：`startup_INS_ground()` 中 `ahrs.init()` + `ahrs.set_vehicle_class()` 已完成，
即将调用 `ins.init(scheduler.get_loop_rate_hz())` — line 225 of system.cpp。

### 阶段 2: GDB 断点确认函数入口

```bash
# 从 nm 找到 ins.init 地址
arm-none-eabi-nm ... | grep 'AP_InertialSensor::init'
# → 0x08073f04 T _ZN17AP_InertialSensor4initEt

# GDB 击中断点后：
arm-none-eabi-gdb build/rt-thread.elf \
  -ex "target remote localhost:3333" \
  -ex "hb *0x08073f04" \
  -ex "continue" \
  -ex "bt 20"

# Backtrace 输出:
# #0  AP_InertialSensor::init (loop_rate=400)
# #1  Copter::startup_INS_ground
# #2  Copter::init_ardupilot
# #3  AP_Vehicle::setup
# #4  HAL_RTT::run
# #5  main
```

**发现**：`ins.init()` **已成功进入**。阻塞发生在 `init()` 内部。

### 阶段 3: IMU 探测列表 + 驱动断点

```bash
# 读取实际的 IMU 探测顺序
grep 'HAL_INS_PROBE' build/rtt_deploy/cuav_v5/hwdef.h
# → HAL_INS_PROBE1: Invensense::probe(icm20689)
# → HAL_INS_PROBE2: Invensense::probe(icm20602)  
# → HAL_INS_PROBE3: BMI055::probe(bmi055_a, bmi055_g)
# → HAL_INS_PROBE4: BMI088::probe(bmi055_a, bmi055_g)
```

各驱动入口地址和断点设置：
```bash
# Invensense::_hardware_init → 0x08076d64
# BMI055::init → 0x080743cc
# BMI088::init → 0x08074ad0
```

设置硬件断点后执行 `continue`，等待 15 秒：
- **所有三个断点均未触发** → 阻塞不在 IMU 驱动具体初始化代码中
- 阻塞点在 `detect_backends()` 更上游 → SPI 设备创建/配置阶段

## 关键系统状态数据

### 定时器线程状态
GDB halt 时，`info threads` 仅显示一个 Thread（stm32f7x.cpu）。
当前 PC 在定时器线程中（`_adc_read`），主线程被切换出去了。

```bash
# 当前 PC 翻译
arm-none-eabi-addr2line -e build/rt-thread.elf -f -C 0x0806c56e
# → RTT::_adc_read (AnalogIn.cpp:90)
# → RTT::AnalogIn::_timer_tick (AnalogIn.cpp:136)
# → RTT::Scheduler::_run_timers (Scheduler.cpp:633)
```

### ADC 超时分析
```cpp
// AnalogIn.cpp:89-95 — 100K 循环超时
for (volatile uint32_t t = 0; t < 100000; t++) {
    if (ADC1->SR & ADC_SR_EOC) { ... return; }
}
```

- 每次超时 = 100K × ~5 cycles = ~0.5ms @ 216MHz
- 定时器线程每 1ms 运行一次
- 如果 ADC EOC 从未置位 → 定时器线程消耗 ~50% CPU

## 探查中的坑

### GDB "target is running" 问题
```gdb
# ❌ monitor halt + continue 失败
-ex "monitor halt"
-ex "continue"
# → Cannot execute this command while the target is running.

# ✅ 正确：直接 continue，不要先 halt
-ex "target remote localhost:3333"
-ex "hb *addr"     # 设断点
-ex "continue"     # 让目标运行，断点命中后自动 halt
```

### hardware breakpoint 数量限制
Cortex-M7 只有 6 个硬件断点（FPB）。设置过多会失败。

### OpenOCD telnet 与 GDB 的互斥
- 不要同时用 telnet 和 GDB 控制 OpenOCD — 状态可能会冲突
- 推荐流程：GDB 做断点/backtrace → telnet 做内存读取 → 交替使用

## 2026-05-10 补充：Phase 0 清理回归诊断

### 新根因：Phase 0 架构清理回退 IMU 兼容代码

当 ADC 修复（TSVREFE + SWSTART 超时恢复）已确认生效（`ADC_CCR=0x00810000`）、IOMCU 禁用后仍卡在 stage 662 时，根因可能是 **Phase 0 清理（commit `062a16fb8d`）** 回退的 IMU 探测代码。

### 排除流程

```bash
# Step 1: 确认 ADC 寄存器正常（排除 ADC 阻塞）
echo "mdw 0x40012304" | nc -q1 localhost 4444  # → 0x00810000 ✅
echo "mdw 0x4001204C" | nc -q1 localhost 4444  # → 有有效数据 ✅

# Step 2: 禁用 IOMCU（排除 IOMCU 阻塞）
# hwdef.dat: # IOMCU_UART UART8 → 注释掉
# 重建 → 烧录 → 仍卡 stage 662 → 排除 IOMCU

# Step 3: 检查 Phase 0 回退的 IMU 驱动文件
git show 062a16fb8d --stat | grep -i "InertialSensor"
# → AP_InertialSensor.cpp       | 80 ++-------
# → AP_InertialSensor_BMI055.cpp| 20 +--
# → AP_InertialSensor_BMI088.cpp| 23 +--
# → AP_InertialSensor_Invensense.cpp | 51 +-----
```

### 回退清单（被取消的 RTT 兼容代码）

| 文件 | 回退内容 | 影响 |
|------|---------|------|
| `AP_InertialSensor.cpp` | 健康位强制、SPI 兼容 | IMU 健康检查失败时无 fallback |
| `AP_InertialSensor_BMI055.cpp` | 延迟/时序适配 | SPI 时序不匹配导致 probe 失败 |
| `AP_InertialSensor_BMI088.cpp` | 延迟/时序适配 | 同上 |
| `AP_InertialSensor_Invensense.cpp` | 超时兼容、复位序列 | SPI 读 WHO_AM_I 永久超时 |
| `AP_InertialSensor_Invensensev3.cpp` | 超时兼容 | 同上 |

### 修复方向

**方案 A**：在 `AP_HAL_RTT/SPIDevice.cpp` 中增强 SPI 超时处理，使 RTT 的轮询 SPI 能兼容标准 ArduPilot IMU 探测流程。

**方案 B**：在 `AP_HAL_RTT/` 层增加 `HAL_INS_PROBE` 包装函数，在标准 probe 之前做额外初始化。

**方案 C**：回退 Phase 0 对 `AP_InertialSensor/` 的修改（恢复已验证的 RTT 兼容层）。

### 构建验证：检查时间戳

```bash
# 关键检查：构建产物时间必须晚于最后 commit 时间
stat build/rtt_deploy/cuav_v5/rtthread.bin      # 构建时间
git log --oneline -1 --format="%ad" --date=iso   # 提交时间
# 构建时间 >= 提交时间 → OK
# 构建时间 < 提交时间 → rm -rf build/ 重编
```

## 根因结论
阻塞点在 `ins.init()` → `_start_backends()` → `detect_backends()` 中，
创建 SPI 设备对象期间。具体原因可能是：
1. SPI 总线锁（`rt_spi_take_bus`）被定时器线程中的 ADC 操作间接持有
2. 或 RT-Thread SPI 设备对象创建时的内存/资源竞争
3. 定时器线程的 ADC 超时轮询消耗 ~50% CPU，加剧了主线程的滞后

修复方向：
1. 修复 ADC EOC 不置位（ADC 时钟/触发配置问题）→ 消除定时器线程 CPU 浪费
2. 或在 IMU 探测期间临时提升主线程优先级
