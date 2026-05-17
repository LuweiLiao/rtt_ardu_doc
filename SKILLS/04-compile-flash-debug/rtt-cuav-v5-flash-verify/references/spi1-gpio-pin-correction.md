# SPI1 GPIO 引脚修正记录（2026-05-08 重大发现）

## 背景

CUAV V5 的 SPI1 连接 ICM20689 + ICM20602 IMU。当前 RTT 移植代码在
`drv_spi_ll.c` 和 `SPIDevice.cpp` 两个文件中各有一份 SPI1 引脚配置，
在 RTT 移植过程中两处都被错误地改成了 PG9(PB5)，导致 IMU 不工作。

## 正确的 SPI1 引脚配置（来自 hwdef.dat 和 ChibiOS 验证）

| 信号 | 正确引脚 | 错误引脚（代码中） | 
|------|---------|-------------------|
| SCK  | PG11 (正确) | PG11 |
| MISO | **PA6** | **PG9** ❌ |
| MOSI | **PD7** | **PB5** ❌ |

## 代码中的两处配置

### 1. SPIDevice.cpp — _spi1_gpio_init()
用于 register-level polling 路径（`_dev == nullptr`，`bus == 1`）。

**原始正确代码（commit 4cd3f2be06）:**
```c
static bool _spi1_gpio_init_done = false;
static void _spi1_gpio_init(void)
{
    if (_spi1_gpio_init_done) return;  // ✅ 只初始化一次
    _spi1_gpio_init_done = true;

    /* PG11 SCK, PA6 MISO, PD7 MOSI */
    GPIOA->MODER = ... PA6 AF5
    GPIOD->MODER = ... PD7 AF5
    GPIOG->MODER = ... PG11 AF5
    GPIOF->BSRR = (1<<2)|(1<<3)|(1<<4);  // ✅ CS HIGH 初始态
}
```

**被破坏后的代码:**
```c
// ❌ _spi1_gpio_init_done 被删除 — 每次transfer都重配GPIO
// ❌ PA6 → PG9, PD7 → PB5 — 引脚完全错误
// ❌ GPIOF->BSRR HIGH 初始被删除 — CS浮动
// ❌ 注释说"was PA6 (WRONG)" — 明明是正确引脚被注释说成错误的
static void _spi1_gpio_init(void)
{
    // 每次重配GPIO → 在CS保持低电平时产生引脚毛刺
    GPIOG->MODER = ... PG9 AF5  // ← 错误引脚！
    GPIOB->MODER = ... PB5 AF5  // ← 错误引脚！
}
```

### 2. drv_spi_ll.c — spi1_ll_cfg
用于 RTT 框架路径（`_dev != nullptr`，通过 `spi_ll_init()` 配置）。

```c
// 原始错误
.miso_port_idx = 6, .miso_pin_no = 9,  // PG9 ❌
.mosi_port_idx = 1, .mosi_pin_no = 5,  // PB5 ❌

// 修正后
.miso_port_idx = 0, .miso_pin_no = 6,  // PA6 ✅
.mosi_port_idx = 3, .mosi_pin_no = 7,  // PD7 ✅
```

## 三个关键修复

### 修复1: 恢复 _spi1_gpio_init_done 保护

**为什么必须要有：** 在 `transfer()` 和 `transfer_fullduplex()` 中，
`_spi1_gpio_init()` 在每次调用时无条件被调用。当 ICM20689 使用
CS-held burst 读（set_chip_select(true) → transfer(addr) → 
transfer_fullduplex(data)）时，第二次 transfer 中 GPIO 被重配，
尽管 MODER/AFR 写入相同的值，但寄存器的写入周期本身会产生短暂的
引脚电平毛刺。这个毛刺刚好在 CS 保持低电平期间发生，导致 IMU 
失同步。

### 修复2: 恢复 PA6/PD7 引脚

**验证方法：** 对照 ChibiOS hwdef:
```bash
cat libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat | grep "SPI1"
```

### 修复3: 恢复 CS HIGH 初始态

BSSR 写入 `GPIOF->BSRR = (1U<<2)|(1U<<3)|(1U<<4)` 确保 CS 在初始化
后处于高电平。不驱动 HIGH 时 CS 引脚浮空，IMU 可能误检测到片选。

## 调试过程

### 现象复现
1. 固件启动 → USB枚举 "Generic CUAVv5 RTT" → MAVLink 发几帧 → 停止
2. ACM1 端口存在，`ser.read()` 无限阻塞
3. OpenOCD halt 后 PC 在有效 flash 地址（非 0xFFFFFFFE）

### GDB 诊断
```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor reset halt" \
  -ex "info registers pc sp lr" \
  -ex "x/5i \$pc"
```
期望看到 PC 在 SPI1 传输函数附近，而不是 HardFault。

### SPI 寄存器验证
GDB 确认 SPI1 SR=0x203 (RXNE=1, TXE=1, BSY=0) — 外设状态正常。
问题不在 SPI 外设，而在 GPIO 引脚连接错误和 CS 时序。

### 烧录验证
```bash
# 正确流程
echo "reset halt" | nc -q2 localhost 4444
echo "flash erase_sector 0 0 last" | nc -q15 localhost 4444
echo "flash write_image rtthread.bin 0x08000000" | nc -q30 localhost 4444
echo "reset run" | nc -q1 localhost 4444
```

⚠️ `reset halt` 后 PC 应为 `0x08000200`（bootloader 运行中），
  而不是 `0xFFFFFFFE`（flash 空白）。

## 教训总结

1. C 代码（GPIO 配置）的**每次修改**都有把 MCU 打死的风险
2. 每次修改前必须思考：这个函数会被调用多少次？在什么上下文中被调用？
3. `_spi1_gpio_init_done` 保护不能被删除 — 理由是"GPIO可能被其他外设篡改"
   （被篡改的概率远低于每次重配导致的毛刺概率）
4. IMU 的 CS 保持低电平期间不能触碰任何 SPI/GPIO 寄存器
5. 对照实验：`git stash` 回到旧版验证是否还有问题 — 2026-05-08 确认
   旧版（`1d2dc8f68a`）在同一硬件上 IMU 也为零 → SPIDevice.cpp 的 
   SPI4 修改不可能影响 SPI1 → 根因在其他地方
