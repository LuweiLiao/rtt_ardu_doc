# INS Backend Start Hang (setup_stage=680+N)

## 现象

- `setup_stage` 固定在 680+N（N=0,1,2,3）
- PC 在 `_delay_microseconds_dwt`（Scheduler.cpp:72）
- CFSR=0, HFSR=0 — 无 HardFault
- DWT CYCCNT 正常递增
- 系统看似活着但 setup 不推进

## CUAV V5 IMU 探测顺序

从 `hwdef.h` 编译产物读取实际的初始化顺序：

| 索引 | setup_stage | IMU 驱动 | SPI 设备 | CS |
|------|-------------|---------|----------|-----|
| 0 | 680 | ICM-20689 (Invensense) | icm20689 | PF2 |
| 1 | 681 | ICM-20602 (Invensense) | icm20602 | PF3 |
| 2 | **682** | **BMI055** | **bmi055_a, bmi055_g** | **PG10/PF4** |
| 3 | 683 | BMI088 | bmi055_a, bmi055_g (同上) | PG10/PF4 |

## 根因分析

### 特征：BMI055/BMI088 共享 SPI 设备

BMI055 和 BMI088 的 `probe()` 使用**完全相同的 SPI 设备** (`bmi055_a`, `bmi055_g`)：

```cpp
BMI055: hal.spi->get_device("bmi055_a"), hal.spi->get_device("bmi055_g")
BMI088: hal.spi->get_device("bmi055_a"), hal.spi->get_device("bmi055_g")  // 同设备！
```

这意味着：当一个已经获取了 SPI 总线 semaphore，另一个尝试获取时会阻塞。

### 阻塞点：Semaphore::take_blocking() 使用 RT_WAITING_FOREVER

```cpp
// Semaphores.cpp:98-123
void Semaphore::take_blocking() {
    _ensure_mtx();
    if (!_mtx_inited) return;
    // 如果是当前线程重入，跳过
    if (_mtx_obj.owner == rt_thread_self()) {
        if (_mtx_obj.hold < RT_MUTEX_HOLD_MAX) _mtx_obj.hold++;
        return;
    }
    rt_mutex_take(&_mtx_obj, RT_WAITING_FOREVER);  // ← 永久阻塞！
}
```

如果一个线程持有了 SPI 总线 mutex 且未释放，另一个线程尝试 `take_blocking()` 会永久阻塞。

### 死锁场景（最可能路径）

1. Backend[0] ICM20689 probe 成功（获取/释放 SPI 总线）
2. Backend[1] ICM20602 probe 成功（获取/释放 SPI 总线）
3. Backend[2] BMI055 probe 进入 `accel_init()` → `take_blocking()` → 获取 SPI 总线
4. 某个中间步骤（`dev_accel->read_registers()` 的 SPI 传输）需要 SPI 总线
5. 但总线线程（SPI1 bus thread）也在尝试 `take_blocking()` → 互锁
6. 或者: BMI055 probe 过程中, 前一个 backend 的回调（ICM20689的 periodic callback）触发了 SPI 读取 → 争锁

### BMI055::start() 无 SPI 操作

重要观察：`BMI055::start()` 只做注册 + 回调设置，**不直接做 SPI 操作**：

```cpp
void AP_InertialSensor_BMI055::start() {
    _imu.register_accel(...);
    _imu.register_gyro(...);
    dev_accel->register_periodic_callback(...);  // 触发 bus thread 创建
    dev_gyro->register_periodic_callback(...);
}
```

如果挂死发生在 `start()` 中，可能是 `register_periodic_callback()` 触发的总线线程创建过程中，与新线程间的 mutex 竞争。

## 诊断步骤

### 1. 确认挂死不在 _delay_microseconds_dwt 本身

```bash
# 读 DWT 确认 CYCCNT 在递增
echo "halt
mdw 0xE0001004 1  # DWT_CYCCNT
mdw 0xE000ED28 1  # CFSR
mdw 0xE000ED2C 1  # HFSR
resume" | timeout 5 nc localhost 4444 2>/dev/null | tr -d '\0'
# CYCCNT 递增 ✅，CFSR=0 ✅, HFSR=0 ✅ → 非硬件异常
```

### 2. 确认 stage 固定不推进

```bash
for i in 1 2 3; do
  sleep 10
  echo "halt" | timeout 3 nc localhost 4444
  echo "mdw 0x2001bf34 1" | timeout 3 nc localhost 4444
  echo "resume" | timeout 3 nc localhost 4444
done
# 如果三次都相同 → 永久挂死
```

### 3. 读取就绪队列确认主线程状态

```bash
# 找就绪组地址
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rt_thread_priority_table

# 读就绪组
echo "mdw <ready_group_addr> 1" | timeout 3 nc localhost 4444
```

如果就绪组显示只有 idle 和 IO 线程就绪 → 所有其他线程阻塞在 semaphore 上。

### 4. 用变更隔离法确认

注释掉 `hwdef.dat` 中可疑的后端:

```diff
- IMU BMI055 SPI:bmi055_a SPI:bmi055_g ROTATION_ROLL_180_YAW_90
- IMU BMI088 SPI:bmi055_a SPI:bmi055_g ROTATION_ROLL_180_YAW_90
+ # IMU BMI055 SPI:bmi055_a SPI:bmi055_g ROTATION_ROLL_180_YAW_90
+ # IMU BMI088 SPI:bmi055_a SPI:bmi055_g ROTATION_ROLL_180_YAW_90
```

如果去掉后 system 正常推进 → 根因确认在共享 SPI 设备的死锁。

## 修复方向

### 方案 A：去除冲突的 IMU（最快）

注释掉 BMI055 和 BMI088，只保留 ICM20689 + ICM20602（两颗 IMU 足以验证 L0）。

### 方案 B：拆分为独立 SPI 设备

为 BMI055 和 BMI088 分配不同的 SPI DEVID，使它们不共享同一总线 semaphore。

### 方案 C：Semaphore 加超时保护

在 `take_blocking()` 中加入超时检测，超时后返回 false 而非永久阻塞。

```cpp
void Semaphore::take_blocking() {
    _ensure_mtx();
    if (!_mtx_inited) return;
    if (_mtx_obj.owner == rt_thread_self()) {
        if (_mtx_obj.hold < RT_MUTEX_HOLD_MAX) _mtx_obj.hold++;
        return;
    }
    // 超时 5 秒后返回 false
    if (rt_mutex_take(&_mtx_obj, rt_tick_from_millisecond(5000)) != RT_EOK) {
        DEV_PRINTF("Semaphore: take_blocking timeout!\\n");
        return;
    }
}
```
