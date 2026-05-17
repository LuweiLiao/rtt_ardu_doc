# Bus 线程栈溢出 → `__udivmoddi4` 偶发崩溃诊断与修复

> **发现时间**: 2026-05-09
> **最后更新**: 2026-05-09（commit 6bec32b9b1 验证通过）
> **适用场景**: MCU 偶发崩溃，PC 在 `__udivmoddi4`，LR 为无效地址（如 0x23）

## 现象特征

- PC 停在 `__udivmoddi4` 或其他 libgcc 运行时函数中
- **LR = 无效地址**（如 `0x23` — Thumb bit 置位但地址不在代码空间）
- 这是**栈损坏**的标志：返回地址被覆盖，不是正常的调用返回
- 发生频率：数小时测试中仅出现 1 次

## 因果链

```
Bus 线程栈接近满载 (93.5%) → 触发深层调用（SPI 传输 + 回调 + 时间戳64位除法）
→ __udivmoddi4 栈帧（~52字节）越界 → 覆盖栈上 LR
→ 函数返回时跳转到 LR=0x23 → HardFault
```

## 关键数据

### CUAV V5 栈布局
| 栈 | 大小 | 位置 |
|----|------|------|
| 系统/MSP 栈（ISR/异常） | 16KB | `_sstack` → `_estack` |
| Bus 线程栈 (×8) | **6144** 字节/个 | `_bus_thread_stacks[N][6144]` |
| 主线程栈 | 16384 字节 | CONFIG |
| 空闲线程栈 | 256 字节 | CONFIG |

### Bus 线程栈使用率实测
正常 IMU FIFO 读取（SPI1 + BMI055 + ICM20689）时可达 **93.5%**（仅剩 ~400 字节）。
`__udivmoddi4` 调用链额外需要 ~52 字节，足以越界。

### 64 位除法热路径（AP_HAL_RTT）
| 调用点 | 代码 | 频率 |
|--------|------|------|
| `Util::get_millis()` | `(uint64_t)tick * 1000ULL / RT_TICK_PER_SECOND` | ⚠️ 极高 |
| `Util::get_micros64()` | `(uint64_t)tick * 1000000ULL / RT_TICK_PER_SECOND` | ⚠️ 极高 |
| `Scheduler::delay()` | `(micros64() - start) / 1000` | ⚠️ 高频 |
| `UARTDriver::receive_time_constraint_us()` | `((uint64_t)nbytes * 1000000ULL * 10) / _baudrate` | 🟡 低频 |
| `Semaphores::take()` | `(uint64_t)us * RT_TICK_PER_SECOND / 1000000U` | 🟡 中频 |

## 失败方案：增大静态数组

**尝试**: `static char _bus_thread_stacks[8][6144]` → `[8][8192]`（+16KB BSS）
**结果**: ❌ MCU 初始化时 `rt_object_init` 死循环（BSS 偏移暴露了 RT-Thread 内核对象链表中的指针损坏）
**详见**: `references/bss-memory-layout-rt-object-init.md`

## ✅ 最终修复：堆分配替代静态数组

**commit**: `6bec32b9b1`
**修改文件**: `libraries/AP_HAL_RTT/DeviceBus.cpp`

### 改法

```cpp
// ❌ 错误：增大静态数组 → +16KB BSS 触发 rt_object_init 死循环
static char _bus_thread_stacks[DeviceBus::MAX_BUSES][8192];  // BSS +16KB

// ✅ 正确：堆分配，BSS 反而减少 48KB
static char *_bus_thread_stacks[DeviceBus::MAX_BUSES] = {nullptr};
static const unsigned BUS_STACK_SIZE = 8192;

// 在 register_periodic_callback 中惰性分配：
if (!_bus_thread_stacks[slot]) {
    _bus_thread_stacks[slot] = (char*)rt_malloc(BUS_STACK_SIZE);
    if (!_bus_thread_stacks[slot]) {
        rt_kprintf("DeviceBus: failed to allocate %u-byte stack\n", BUS_STACK_SIZE);
        return nullptr;
    }
}
rt_thread_init(&_bus_thread_objs[slot], name, _bus_thread_entry, arg,
               _bus_thread_stacks[slot], BUS_STACK_SIZE, prio, 20);
```

### 效果

| 指标 | 改前 | 改后 |
|------|------|------|
| BSS 占用 | 49,152 字节（8×6144） | 32 字节（8 个指针） |
| 单栈大小 | 6,144 字节 | **8,192 字节** |
| 栈余量 | 6.5%（~400B） | **37%**（~3KB） |
| rt_object_init 死锁 | — | ✅ 已消除（BSS不增反减） |
| 170 秒稳定性测试 | — | ✅ 512/512 采样，零翻转 |

## 验证方法

```python
# 长时间稳定性测试
import pymavlink.mavutil as m, time

c = m.mavlink_connection('/dev/ttyACM1', baud=921600, timeout=5)
c.wait_heartbeat(timeout=15)

start = time.time()
total, ok = 0, 0
while time.time() - start < 120:
    ss = c.recv_match(type='SYS_STATUS', blocking=True, timeout=2)
    if ss: ok += 1
    total += 1
    if total % 20 == 0:
        print(f't={int(time.time()-start)}s #{total}: GYRO={"H" if ss.onboard_control_sensors_health&1 else "U"}')

print(f'OK: {ok}/{total}')
```
