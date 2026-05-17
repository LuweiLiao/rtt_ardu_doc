# I2CDevice Semaphore 对齐 ChibiOS — 2026-05-15 修复记录

## 发现

系统对比 ChibiOS vs RTT 时发现 I2CDevice::get_semaphore() 返回私有 `&_sem`
而非总线级 `&bus.semaphore`，与 Phase 1 SPIDevice 的同一错误模式。

## ChibiOS 参考

```
I2CBus : DeviceBus (I2CDevice.h:36)
I2CBus::get_semaphore() { return &bus.semaphore; }  // I2CDevice.h:91-93
```

`I2CBus` 继承 `DeviceBus`，`bus.semaphore` 是 `DeviceBus` 的成员。
ChibiOS 中 `WITH_SEMAPHORE(dev->get_semaphore())` 锁的是整条 I2C 总线。

## RTT 修复（3 处修改）

### 文件 1: I2CDevice.cpp

```diff
-    if (!_sem.take(HAL_SEMAPHORE_BLOCK_FOREVER)) return false;
+    if (_bus_dev == nullptr) return false;
+    if (!_bus_dev->semaphore.take(HAL_SEMAPHORE_BLOCK_FOREVER)) return false;

-    _sem.give();
+    if (_bus_dev != nullptr) {
+        _bus_dev->semaphore.give();
+    }

-    return &_sem;
+    return &_bus_dev->semaphore;
```

### 文件 2: I2CDevice.h

```diff
-    Semaphore _sem;
     DeviceBus *_bus_dev;
```

### 文件 3: SPIDevice.h（连带清理）

```diff
-    Semaphore _sem;
     DeviceBus *_bus;
```

## 验证

- 编译: `scons --v=ArduCopter --target=cuav_v5 -j$(nproc)` → ROM 85.18%, RAM 54.55%, 零错误
- Git: `commit bfe648f60c`

## ChibiOS I2CBus 继承关系

```
class DeviceBus {           // Device.h
    Semaphore semaphore;
    callback_info *_callbacks;
    ...
};

class I2CBus : public DeviceBus {   // I2CDevice.h:36
    AP_HAL::Semaphore* get_semaphore() override {
        return &bus.semaphore;       // 继承自 DeviceBus
    }
    I2CInfo info;
    i2c_bus_state state;
};
```

I2CBus 的 `bus` 是一个静态数组 `I2CD[]`，每个 `I2CBus` 实例包含一个
`DeviceBus` 子对象，其 `semaphore` 成员就是总线锁。I2CDevice 通过
`_bus` 指针（指向 `I2CD[idx]`）访问 `semaphore`。
