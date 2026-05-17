# AP_GPS_Blended HardFault (this=0x33) — 预存bug

## 症状

HardFault 发生在 `AP_GPS_Blended::calc_state()`，`this=0x33`（无效指针）。
xPSR=0x21000003（HardFault exception #3），PC 在 HardFault_Handler 的 NOP 循环中。

## 复现条件

固件初始化推进到 AP_GPS scheduler 回调时必现。
**之前被更早的SPI1挂死掩盖**——SPI1修复(PA6/PD7正确引脚)让固件推进更远，暴露了此bug。

## 根因

```cpp
// AP_GPS.cpp:286
AP_GPS::AP_GPS()
{
    // 构造函数未初始化 drivers[] 数组！
    // drivers[GPS_MAX_INSTANCES] = {nullptr} 没有写在这里
    ...
}

// AP_GPS.cpp:358
drivers[GPS_BLENDED_INSTANCE] = NEW_NOTHROW AP_GPS_Blended(...);

// AP_GPS.cpp:1110
((AP_GPS_Blended*)drivers[GPS_BLENDED_INSTANCE])->calc_state();  // ← this=0x33
```

C++ 标准要求静态/全局对象的成员数组零初始化，但 RTT 的 BSS 初始化或构造函数调用顺序可能不满足此保证。`drivers[2]` 在分配失败后保持为未初始化状态（0x33 = 某些随机残留值）。

## 修复方法

在 `AP_GPS::AP_GPS()` 构造函数开头加：

```cpp
AP_GPS::AP_GPS()
{
    memset(drivers, 0, sizeof(drivers));
    ...
}
```

或在 `calc_state()` 入口加空指针检查：

```cpp
void AP_GPS_Blended::calc_state()
{
    if (this == nullptr) return;  // 安全守卫
    ...
}
```

## 影响分析

- 不影响 ChibiOS/Linux 等端口（BSS 清零保证）
- 仅影响 RTT 端口
- 修复后 USB CDC 应能正常枚举，MAVLink 心跳可用
- 所有传感器可用性取决于 SPI probe 是否成功

## 调试命令

```bash
# 确认崩溃位置
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf -f 0x080639fe

# 检查 drivers 数组
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p &gps" \
  -ex "p &gps.drivers" \
  -ex "p gps.drivers[0]" \
  -ex "p gps.drivers[1]" \
  -ex "p gps.drivers[2]"
```
