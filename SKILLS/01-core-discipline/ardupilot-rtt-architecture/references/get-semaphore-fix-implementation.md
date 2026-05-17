# get_semaphore() 修复实施记录

## 发现的差异（2026-05-13）

**ChibiOS** (`libraries/AP_HAL_ChibiOS/SPIDevice.cpp:338`):
```cpp
AP_HAL::Semaphore *SPIDevice::get_semaphore()
{
    return &bus.semaphore;  // 总线级信号量
}
```

**RTT（修复前）** (`libraries/AP_HAL_RTT/SPIDevice.cpp:649`):
```cpp
AP_HAL::Semaphore *SPIDevice::get_semaphore()
{
    return &_sem;  // 私有 per-device 信号量
}
```

## 影响分析

`WITH_SEMAPHORE(_dev->get_semaphore())` 在 Invensense/ICM20689 驱动中被大量使用。
Invensense 驱动的 `_hardware_init()` 执行顺序：

```cpp
WITH_SEMAPHORE(_dev->get_semaphore()) {
    _register_write(MPUREG_PWR_MGMT_1, BIT_PWR_MGMT_1_DEVICE_RESET);  // reset IMU
    hal.scheduler->delay(100);  // 等待复位完成
    _register_write(MPUREG_PWR_MGMT_1, BIT_PWR_MGMT_1_CLK_XGYRO);     // 选择时钟源
    _register_write(MPUREG_SIGNAL_PATH_RESET, 0x01);                   // 信号路径复位
    _register_write(MPUREG_USER_CTRL, BIT_USER_CTRL_I2C_IF_DIS);      // 禁用I2C
    // ... 更多配置
}
```

每个 `_register_write()` 最终调用 `_dev->transfer()`。ChibiOS 中 `WITH_SEMAPHORE` 锁住整个总线，`transfer()` 无需额外锁。RTT 中 `WITH_SEMAPHORE` 只锁私有 `_sem`，`transfer()` 内部再获取总线锁 `_bus->semaphore` — **两把锁之间其他设备可插入操作**。

## 修复方案

### 1. `get_semaphore()` 返回值
```cpp
// 修复前：
return &_sem;
// 修复后：
return &_bus->semaphore;
```

### 2. STM32F7 polling path (`_dev == nullptr`)
```cpp
// 修复前：使用 _sem（私有锁）
if (need_sem && !_sem.take(HAL_SEMAPHORE_BLOCK_FOREVER)) return false;
// ... transfer ...
if (!_cs_held && need_sem) _sem.give();

// 修复后：使用 _bus->semaphore（总线锁）
if (need_sem && !_bus->semaphore.take(HAL_SEMAPHORE_BLOCK_FOREVER)) return false;
// ... transfer ...
if (!_cs_held && need_sem) _bus->semaphore.give();
```

### 3. RT-Thread framework path (`_dev != nullptr`)
```cpp
// 修复前：取 _sem（私有） + _lock_bus（总线框架锁）
bool need_sem = !_cs_held;
if (need_sem && !_sem.take(HAL_SEMAPHORE_BLOCK_FOREVER)) return false;
if (!_cs_held && !_lock_bus()) {
    if (need_sem) { _sem.give(); }
    return false;
}
// ... transfer via rt_spi_transfer_message ...
if (!_cs_held) { _unlock_bus(); }
if (need_sem) { _sem.give(); }

// 修复后：移除 _sem，保留 _lock_bus()（上层已持总线锁）
if (!_cs_held && !_lock_bus()) {
    return false;
}
// ... transfer via rt_spi_transfer_message ...
if (!_cs_held) { _unlock_bus(); }
// 不再需要 _sem.give()
```

**关键理解**：`_lock_bus()` 获取的是 RT-Thread SPI 框架锁 `_dev->bus->lock`，而 `WITH_SEMAPHORE(get_semaphore())` 现在获取的是 `_bus->semaphore`（DeviceBus 总线锁）。这是**两个不同的锁**，分别保护不同层级：
- `_bus->semaphore`：串行化 DeviceBus 线程 callback 分派与上层 SPI 操作
- `_dev->bus->lock`：串行化 RT-Thread SPI 框架的底层配置

### 4. `set_chip_select()` 框架路径
```cpp
// 修复前：
if (!_sem.take(HAL_SEMAPHORE_BLOCK_FOREVER)) return false;
// ... assert CS ...
_sem.give();

// 修复后：
if (!_bus->semaphore.take(HAL_SEMAPHORE_BLOCK_FOREVER)) return false;
// ... assert CS ...
_bus->semaphore.give();
```

### 5. `transfer_fullduplex()` 框架路径
与 `transfer()` 框架路径相同：移除 `_sem.take/give`，保留 `_lock_bus()/_unlock_bus()`。

## 验证结果

**编译**：`scons --v=ArduCopter --target=cuav_v5 -j$(nproc)` ✅ 通过

**烧录**：OpenOCD `program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify` ✅ 通过

**运行时**：
- ✅ MAVLink HEARTBEAT state=5 (STANDBY)
- ✅ RAW_IMU 消息流存在（但数据全零）
- ✅ AHRS 消息存在
- ✅ GLOBAL_POSITION_INT 消息存在

**注意事项**：
- bootloader 可能因缺少 `.app_descriptor` 不跳转（需在链接脚本中添加）
- RAW_IMU 数据为零表明 fix #1 正确但 IMU 寄存器读取仍需进一步调试

## 相关文件
- `libraries/AP_HAL_RTT/SPIDevice.cpp` — get_semaphore(), transfer(), set_chip_select(), transfer_fullduplex()
- `libraries/AP_HAL_ChibiOS/SPIDevice.cpp` — ChibiOS 参考实现
