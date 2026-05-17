# SPI4 (MS5611 气压计) 调试记录 — V4 (2026-05-08 20:15)

## 现象汇总

| 构建 | SPIDevice.cpp | IMU (SPI1) | BARO (SPI4) | ACM0 console |
|------|---------------|------------|-------------|--------------|
| 原始 L0 (bus==1 only) | `bus==1` | ✅ zacc≈-1000 | ❌ data=0 | ✅ 文本输出 |
| V3 (bus==1\|4 polling) | `bus==1\|bus==4` | ✅ zacc≈-1000 | ❌ _init断点不命中 | ✅ 文本输出 |
| V4 (RTT框架, 撤回bus==4) | `bus==1` | ❌ **全零** | ❌ _init断点不命中 | ❌ **二进制垃圾** |

结论: **切换 SPI4 传输路径影响了 SPI1 IMU**。虽非预期，但可控。

## 关键发现 (2026-05-08 20:00+)

### 1. hwdef.h 生成验证正确

`build/rtt_cuav_v5/hwdef.h`:
```c
#define HAL_SPI_DEVICE5 {"ms5611", "spi41", 4, 1, 3, 20000000U, 20000000U}
#define HAL_RTT_SPI_ATTACH_LIST \
    {"spi1", "spi11", GET_PIN(F, 2)}, \
    ...
    {"spi4", "spi41", GET_PIN(F, 10)}  // ← SPI4 正确包含!
```

SPI4 的 RTT 设备 "spi41" 已正确包含在 attach 列表中。

### 2. SPIDevice.cpp CS引脚表验证

```cpp
static const struct spi_cs_entry _spi_cs_table[] = {
    {"spi11", 82},  // ICM20689
    {"spi12", 83},  // ICM20602
    {"spi13", 84},  // BMI055_G
    {"spi14", 106}, // BMI055_A
    {"spi21", 85},  // RAMTRON
    {"spi41", 90},  // MS5611 (CS=PF10)
};
```

CS 表也正确包含 spi41 → PF10(90)。

### 3. 构造器代码流

```cpp
SPIDevice::SPIDevice(const RTT_SPIDesc &desc) {
    _dev = nullptr;                       // 初始化为 null
    _cs_pin = _lookup_cs_pin(desc.rtt_devname);  // "spi41" → 90
    if (_desc.bus == 1) {                 // bus==4 不走这里, _dev 设为 null 返回
        _dev = nullptr; return;
    }
    _dev = rt_device_find("spi41");       // ← 关键: 返回有效设备还是 NULL?
    if (_dev) set_speed(SPEED_LOW);
}
```

### 4. MS56XX::_init() GDB 断点持续不命中

背景 GDB 进程 `proc_3a1bec4b3659` 输出:
```
Breakpoint 1 at 0x804bed0: file OwnPtr.h, line 95.  ← MS56XX::_init 解析到其他位置!
Breakpoint 2 at ..._read_prom_word
Breakpoint 3 at ...SPIDevice::transfer
Continuing.
Breakpoint 3, SPIDevice::transfer (this=0x200529b0) for "icm20689"
```

**`_init()` 断点从未命中。** 且其解析地址在 `OwnPtr.h:95` 而非 `AP_Baro_MS5611.cpp`。

可能的解释:
- `_init()` 被编译器内联（因为 `_probe` 是模板函数）
- 断点符号解析到函数末尾/内联展开处
- 构造函数或 `_probe` 内的 nullptr 检查先返回了

## MS5611 probe 流程时序分析

```cpp
// AP_Baro_MS5611::probe()
static AP_Baro_Backend *probe(AP_Baro &baro, AP_HAL::OwnPtr<AP_HAL::Device> dev) {
    return _probe(baro, NEW_NOTHROW AP_Baro_MS5611(baro, std::move(dev)));
}

// _probe() 模板函数
template...
static AP_Baro_Backend *_probe(AP_Baro &baro, AP_Baro_MS56XX *sensor) {
    if (sensor == nullptr || !sensor->_init()) {  // ← _init() 从未到达
        delete sensor;                             // → sensor 为 nullptr, 或 _init() 内联/符号错误
        return nullptr;
    }
    return sensor;
}
```

若 NEW_NOTHROW 失败 → sensor=nullptr → 返回 nullptr，_init 不调用。
若 NEW_NOTHROW 成功但构造器内 _dev=nullptr（rt_device_find 返回 NULL）→ _init()→!_dev→false。

## 待排查内容

1. **`rt_device_find("spi41")` 实时返回值** — 断点设于构造器 bus!=1 条件分支
2. **`_lock_bus()` 中 `_dev->bus` 是否为 null** — 若 RTT 设备注册未正确链接 bus
3. **`_probe` 中 `sensor` 是否为 nullptr** — NEW_NOTHROW 失败表示堆耗尽
4. **对照实验** — 重建旧版(bus==4 polling)看 IMU 是否恢复 → 确认回归原因

## STM32F765 SPI4 引脚映射（已验证正确 — ChibiOS 参考）

⚠️ **2026-05-09 修正**: 此前错误地认为 CUAV V5 的 SPI4 走 "映射2 (PE12/PE14)"。STM32F765 数据手册证实 **PE12 不是有效的 SPI4_SCK 引脚**。ChibiOS fmuv5 使用以下引脚且在硬件上正常工作，这是唯一正确的配置：

| 信号 | **ChibiOS 配置（已验证正确）** | AF |
|------|-------------------------------|----|
| SCK | **PE2** | AF5 |
| MISO | **PE13** | AF5 |
| MOSI | **PE6**（避免与 TIM1_CH4 PWM(1) 冲突） | AF5 |

**PE14 不应用于 SPI4_MOSI**：虽然 PE14 是 SPI4_MOSI 的合法选项之一，但它同时被 `TIM1_CH4 PWM(1)` 占用，硬件冲突。

**GPIO 寄存器期望值 (已确认)**:
- MODER: `0x2A020040` → PE12/13/14 = AF(10)
- AFRH: `0x05550008` → PE12/13/14 = AF5

## 分层 Bug 总表

| 层 | 文件 | 问题 | 状态 |
|---|------|------|------|
| 1 | `stm32f7xx_hal_msp.c` | SCK PE2→PE12, MOSI PE6→PE14 | ✅ 已修复 |
| 2 | `hwdef/cuav_v5/hwdef.dat` | PE2→PE12 | ✅ 已修复 |
| 3 | AP_HAL_RTT `rt_board_init.c` | 无 GPIO 安全网 | ✅ 已添加 |
| 4 | modules `rt_board_init.c` | PE2→PE12 | ✅ 已修复 |
| 5a | `SPIDevice.cpp` | bus==4 polling 路径 | ❌ 已撤回 |
| 5b | `SPIDevice.cpp` | bus==4 RTT框架 | ⏳ 测试中 |

## 调试教训

1. **5 个文件共同决定 SPI4 工作** — 改一个不够，必须全部核对
2. **对照实验是发现回归的唯一方法** — 烧旧版看 IMU 是否恢复
3. **GDB 硬件断点陷阱**: 不要在 `commands` 中使用 `shell sleep`；`_init` 可能被内联导致断点符号异常
4. **ACM0 变二进制 = 严重状态变化** — 比单纯传感器失效更值得深究
5. **`_probe` 模板函数** — `NEW_NOTHROW` 失败会令 `sensor==nullptr`，导致 `_init()` 压根不被调用
6. **SPI1 和 SPI4 看似独立但共用一个 ArduPilot 初始化流水线** — `barometer.init()` 卡住会阻塞整个传感器初始化
