# IMU RAW_IMU 全零 — SPI 锁修复后仍然零数据

## 验证基线

| 修复 | 状态 | 说明 |
|------|------|------|
| Fix #1: `get_semaphore()` 返回总线信号量 | ✅ 已部署 | SPIDevice transfer() 使用 `_bus->semaphore` |
| Fix #2: DeviceBus `take(10)` → `take_blocking()` | ✅ 已验证通过 | 无 HardFault，CDC 正常 |
| 主线程优先级 | 默认 (10) | 初始化速度慢但能推进 |
| `sensor_power_init()` (PE3) | 已验证工作 | GPIOE MODER=output, ODR=HIGH |

## 症状

- MAVLink HEARTBEAT: STANDBY @ 3-15s
- RAW_IMU: 持续全零（a=0,0,0 g=0,0,0）
- 42 条样本全零（超过 10 秒采样）
- CFSR=0，无任何 HardFault
- WHO_AM_I 在 probe 阶段通过（否则不会进入 STANDBY）

## 已排除的根因

| 方向 | 排除理由 |
|------|---------|
| SPI 信号量竞争 | Fix #1 确保 transfer() 获取正确的总线锁 |
| Bus 线程 callback 跳过 | Fix #2 将 `take(10)`→`take_blocking()`，callback 不再被跳过 |
| GPIO AFR 寄存器错误 | OpenOCD 已确认 PA6=AF5, PD7=AF5, PG11=AF5 ✅ |
| 传感器供电 | PE3 MODER=output, ODR bit3=1 ✅ |
| WHO_AM_I 不匹配 | probe 阶段已匹配（否则 ins.init 失败） |

## 仍可能的根因

### 1. SPI 轮询时序问题

`spi1_poll_transfer()` 使用寄存器级轮询。检查点：

```c
// SPIDevice.cpp spi1_poll_transfer()
// 1. CS 拉低
// 2. while (SPI1->SR & SPI_FLAG_TXE);   // 等待 TX 缓冲空
// 3. SPI1->DR = send[i];                  // 写入发送数据
// 4. while (!(SPI1->SR & SPI_FLAG_RXNE)); // 等待接收数据
// 5. recv[i] = SPI1->DR;                  // 读取接收数据
// 6. CS 拉高
```

可能的时序问题：
- **TXE 标志处理**：连续读取可能因 SPI 速度太快导致 TXE 未及时置位
- **RXNE 超时**：如果 SPI 设备返回数据慢，RXNE 循环可能提前退出
- **CS 释放时序**：CS 在最后字节传输完成前释放

### 2. IMU 在 power-down / sleep 模式

ICM20689 的 PWR_MGMT_1 寄存器 (`0x6B`)：
- 初始值 after reset: `0x40` (SLEEP=1, device reset)
- 正常值: `0x01` (auto-select best clock, SLEEP=0)
- 如果 firmware read back 为 `0x40` → register write 未生效

检查方法：
```bash
# 在 SPIDevice 构造函数中加诊断读
uint8_t tx[2] = { 0x6B | 0x80, 0 };  // PWR_MGMT_1 read
uint8_t rx[2] = { 0, 0 };
spi1_poll_transfer(nullptr, tx, 2, rx, 2, true, true, SPI1, pf2_cs);
rt_kprintf("PWR_MGMT_1: 0x%02x\n", rx[1]);  // 0x01=OK, 0x40=sleep, 0x85=sleep+accel_cycle
```

### 3. IMU FIFO 未使能

ICM20689 默认 FIFO 可能关闭。USER_CTRL (`0x6A`) 的 bit 6 (FIFO_EN) 必须为 1，否则 FIFO 模式读取总是返回 0。

Invensense 驱动在 `_hardware_init()` 中设置 `MPUREG_USER_CTRL = 0x40`（FIFO_EN），对应 ICM20689 的 USER_CTRL bit 6。

### 4. SPI4 干扰 SPI1 时序

CUAV V5 上 SPI1 和 SPI4 共用同一 SPI 控制器时钟域。如果 `_spi4_gpio_init()` 对 SPI4 的 CR1 配置影响了 SPI1 的时钟分频，可能导致 SPI1 时序偏移。

## 诊断步骤（建议顺序）

1. **读 WHO_AM_I 运行时值** — 在 SPIDevice 构造器中用 `rt_kprintf` 输出
2. **读 PWR_MGMT_1** — 确认 IMU 是否在 sleep 模式
3. **读 USER_CTRL** — 确认 FIFO 是否使能
4. **读 FIFO_COUNT (0x72-0x73)** — 确认是否有数据在 FIFO 中
5. **验证 spi1_poll_transfer 返回值和超时** — 检查 RXNE 是否在预期时间收到数据
6. **比较 ChibiOS 的 SPI 初始化序列** — ChibiOS 对 ICM20689 的 SPI 配置与 RTT 的不同点

## 参考

- ChibiOS SPI 初始化: `libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat`
- Invensense 驱动: `libraries/AP_InertialSensor/AP_InertialSensor_Invensense.cpp`
- RTT SPI 轮询: `libraries/AP_HAL_RTT/SPIDevice.cpp` — `spi1_poll_transfer()`
- 2026-05-13 session 记录: `references/imu-spi-diagnosis-2026-05-13.md`
