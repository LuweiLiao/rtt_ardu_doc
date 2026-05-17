# I2C 位爆炸（Soft Bitbang）阻塞诊断与修复 — RTT CUAV V5

## 发现时间
2026-05-11，Phase 0/1A/1B pipeline 跑完后，新固件烧录验证时发现。

## 现象

| 特征 | 值 |
|------|-----|
| USB CDC | ✅ ttyACM0/1 已枚举 |
| MAVLink 心跳 | ❌ 无输出 |
| CFSR/HFSR | 0 / 0（无 HardFault，正常执行） |
| PC | `stm32_set_sda()` @ `drv_soft_i2c.c:80` |
| 调用链 | `rt_i2c_transfer` → `i2c_send_bytes` → `stm32_set_sda` |
| 再次 halt(5s后) | PC 仍在 I2C 操作区域（推进缓慢） |

## 根因分析

### 1. RT-Thread 使用 GPIO 位爆炸（非硬件 I2C）

CUAV V5 板级配置 `rtconfig.h:178-179`：`BSP_I2C3_SCL_PIN=119(PH7)`, `BSP_I2C3_SDA_PIN=120(PH8)`。

每个 I2C bit：SET_SDA → udelay → SCL_H(while!GET_SCL轮询) → udelay2。每 byte 9 个 SCL 周期，资源探测耗时数百毫秒。

### 2. 对比 ChibiOS（硬件 I2C）

ChibiOS fmuv5 用硬件 I2C3 外设（AF4），400kHz，每 byte ~28µs。

### 3. 阻塞本质

IST8310 磁力计探测同步阻塞，AP_HAL 主循环初始化被延迟。

## 已实施的修复（Phase 1C, 2026-05-11）

通过 kanban R→E→V→O 管线修复了 `I2CDevice.cpp` 的 3 个架构缺陷：

### B1: 移除 rt_i2c_bus_lock/unlock 双重锁 🟢
- 问题：transfer() 手动锁 bus->lock 后 rt_i2c_master_send() 内部再锁一次
- 修复：切到单次 rt_i2c_transfer()，其内部已持有 bus->lock

### B2: check_owner() 消除 _sem 递归获取 🟢
- 问题：IST8310: take_blocking()→write_register()→transfer()→_sem.take() 递归
- 修复：transfer() 入口用 check_owner() 检测，仅未持有时才 take/give

### B3: rt_i2c_msg 数组实现 RESTART 组合传输 🔑
- 问题：read_registers() 用分离 send+recv，STOP 打断传感器流
- 修复：msgs[0]=WR, msgs[1]=RD → 一次 rt_i2c_transfer()，bitbang 自动插 RESTART

## 🐛 额外发现：I2C 总线映射错误 （🔴 高）

hwdef.dat 中 I2C_ORDER 不被 rtt_hwdef.py 解析。默认 HAL_RTT_I2C_BUS_NAMES = "i2c1","i2c2","i2c3","i2c4" 使 bus 0=i2c1(PB8/PB9)≠I2C3(PH7/PH8)。IST8310 探测失败。

修复：hwdef.dat 加 `define HAL_RTT_I2C_BUS_NAMES "i2c3"`（基线版有但生产版缺失）。

## 修复后状态

| 检查项 | 结果 |
|--------|------|
| I2C 卡死 | ✅ 消除，PC 在 idle_thread_entry |
| HardFault | ✅ 无 |
| 调度器 | ✅ 正常运行 |
| USB CDC | ✅ ttyACM0/1 已枚举 |
| MAVLink | ❌ 无心跳 → AP_HAL 主循环未启动（Phase 1D） |

## 参考文件
- RTT I2CDevice: `libraries/AP_HAL_RTT/I2CDevice.cpp`
- 软 I2C 驱动: `modules/rt-thread/bsp/stm32/libraries/HAL_Drivers/drivers/drv_soft_i2c.c`
- I2C bit ops: `modules/rt-thread/components/drivers/i2c/dev_i2c_bit_ops.c`
- ChibiOS I2CDevice: `libraries/AP_HAL_ChibiOS/I2CDevice.cpp`
- ChibiOS fmuv5 hwdef: `libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat`
- Kanban: t_dadf4084, t_80f42ab2, t_9ecdee8a, t_2e5e727b
