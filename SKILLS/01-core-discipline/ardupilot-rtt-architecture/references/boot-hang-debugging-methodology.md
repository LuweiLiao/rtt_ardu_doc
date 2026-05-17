# ArduPilot RTT 启动卡死调试方法

## 概述

RTT ArduPilot 启动卡死在 setup 阶段（setup_stage 锁定在某值）是 L0/L1 验证中最常见的问题。本文件总结系统化的调试方法。

## 启动卡死诊断流程

### Step 1: 确认卡死位置

```bash
# OpenOCD telnet (through localhost:4444)
echo "halt" > /dev/tcp/localhost/4444
# 读取 setup_stage 变量（地址从 ELF 获取）
arm-none-eabi-objdump -t build/rtt_deploy/cuav_v5/rt-thread.elf | grep setup_stage
# 得到地址如 0x2001bf34
echo "mdw 0x2001bf34 1" | nc -q 2 localhost 4444
```

常用调试变量：
- `rtt_dbg_setup_stage` — 当前启动阶段
- `rtt_dbg_main_loop_iterations` — 主循环计数器（>0 表示 loop 运行）
- `rtt_uart_dbg_tick_calls` — UART 线程 tick 计数（>0 表示 UART 被调度）
- `rtt_cpu_idle_pct` — CPU 空闲百分比

### Step 2: PC 定位到源码行

OpenOCD halt 后读取 PC 寄存器，再用 addr2line 映射：

```bash
# halt 后看 PC
echo "halt" | nc -q 2 localhost 4444  # 输出中包含 pc: 0x080xxxxx
# 然后用 addr2line 找到源码位置
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf 0x080702ee
```

常见 PC 位置模式：
| PC 区域 | 含义 |
|---------|------|
| `0x0800xxxx` | bootloader 区域 → bootloader 未跳转应用 |
| `micros64()`/`delay_microseconds_dwt()` | 在 delay 循环中（正常，不是死锁） |
| SPI 寄存器操作 | 在 SPI 传输中等待 RXNE/TXE 超时 |
| `rt_thread_delay()` | 线程在睡眠（正常，看主循环是否增长） |

### Step 3: 确认线程状态

```bash
# 查看 RT-Thread PID 和当前线程
echo "pid" | nc -q 2 localhost 4444
```

### Step 4: SPI 传输级调试

如果 PC 在 SPI 传输路径上：

1. 读取 SPI1 SR 寄存器（0x40013008）确认 RXNE/TXE/BSY/OVR
2. 读取 SPI1 CR1（0x40013000）确认 SPE/MSTR/BR 配置
3. 确认 SPI1 时钟使能：`RCC->APB2ENR` bit 12 (SPI1EN)
4. 确认 GPIO MODER/AFR 配置正确

```bash
# 读取 SPI1 状态寄存器
echo "mdw 0x40013008 1" | nc -q 2 localhost 4444
# 读取 SPI1 控制寄存器
echo "mdw 0x40013000 1" | nc -q 2 localhost 4444
```

SPI_SR 标志位（STM32F7）：
- bit 0 (RXNE): 接收非空 → 1=有数据可读
- bit 1 (TXE): 发送为空 → 1=可写入新数据
- bit 4 (BSY): 忙 → 1=正在传输
- bit 5 (OVR): 溢出 → 1=溢出错误

### Step 5: 分析 setup_stage 卡死模式

| setup_stage | 含义 | 常见根因 |
|------------|------|---------|
| 502 | STORAGE_FLASH_PAGE 擦除（AP_FlashStorage init） | ~1-2秒自恢复，不是卡死 |
| 662 | `ins.init()` 未返回 | SPI 探测/DeviceBus 线程/锁竞争 |
| 662+但主循环>0 | ins.init 完成后卡在后续步骤 | 需看具体代码路径 |

## setup_stage=662 根因分析

`ins.init()` → `_start_backends()` → `detect_backends()` + `_backends[i]->start()`

### 探测阶段 vs 启动阶段

区分卡在 `detect_backends()` 还是 `start()`：

```cpp
// 从 GDB 或 OpenOCD 看 _backend_count
// 如果 _backend_count > 0 → detect_backends() 已完成，卡在 start()
// 如果 _backend_count == 0 → 卡在 detect_backends()
```

### detect_backends() 卡死原因

1. **SPI 寄存器级轮询超时**：`spi1_poll_transfer()` 中 TXE/RXNE 等待超时（100000 NOPs ≈ 0.5ms @216MHz）
   - 检查 SPI CR1 配置是否正确（MSTR|SSM|SSI|CPOL|CPHA|BR）
   - 检查 GPIO MODER/AFR 是否为 AF5 复用功能
   - 检查 CS 引脚 BSRR 是否正确
   - **注意**：register-level polling 路径在 `_dev == nullptr` 时走 `SPIDevice::transfer()` 分支

2. **WHO_AM_I 不匹配**：所有 IMU 的 WHO_AM_I 都失败（返回 false，不会卡死，会继续探测下一个）

### start() 卡死原因

1. **DeviceBus 线程创建竞态**：`register_periodic_callback()` 创建并启动新线程（`rt_thread_init + rt_thread_startup`），新线程立即进入 `_bus_thread_entry()` 开始调度回调。如果主线程仍在 `start()` 中执行配置，可能发生：
   - 回调试图访问未完全初始化的传感器
   - DeviceBus 线程取 `binfo->semaphore` 与主线程的 `_sem` 产生交互

2. **BMI055/BMI088 双设备 probe**：两个 IMU backend 使用相同的 SPI 设备名 `bmi055_a` 和 `bmi055_g`，通过 `OwnPtr` move 语义消费指针。注意 `get_device(name)` 在第二次调用时的行为（返回 nullptr 或相同对象）。

## SPI 寄存器级轮询路径详解（STM32F7）

RTT SPIDevice 对 SPI1/SPI4 使用直接寄存器轮询（`_dev == nullptr` 路径）：

```cpp
// SPIDevice.cpp 中的关键路径
if (_desc.bus == 1 || _desc.bus == 4) {
    _dev = nullptr;  // 绕过 RT-Thread SPI 框架
    return;
}
```

该路径的特性：
- 通过 `_spi1_gpio_init()` / `_spi4_gpio_init()` 手动初始化 GPIO
- 每字节 100000 NOP 超时（约 0.5ms @216MHz）
- SPI 时钟 = APB2 / 16 = 108MHz/16 ≈ 6.75MHz（低速探测）
- CS 控制通过 GPIO BSRR 手动实现（非 SPI NSS 硬件控制）
- **cs_take 时**：禁用 SPE → 配置 CR1/CR2 → 启用 SPE → 刷新 FIFO
- **cs_hold 模式下**：不重新配置 SPI，只切换 CS

## DeviceBus 锁架构分析

### 锁层级

```
DeviceBus (binfo->semaphore)    ← Bus 级锁，10ms 超时
    └── SPIDevice (_sem)        ← 设备级锁，FOREVER
```

### 与 ChibiOS 的差异

- **ChibiOS**：`get_semaphore()` 返回 DeviceBus 的 `&bus.semaphore`（单锁架构）
- **RTT**：`get_semaphore()` 返回 `&_sem`（SPIDevice 私锁），独立于 DeviceBus 的 `binfo->semaphore`

### 锁获取顺序

- **DeviceBus 线程**：`binfo->semaphore.take(10ms)` → `callback()` → `SPIDevice._sem.take(FOREVER)` → SPI transfer → `_sem.give()` → `binfo->semaphore.give()`
- **主线程 setup**：`SPIDevice._sem.take(FOREVER)` → SPI transfer → `_sem.give()`

**不会死锁**：主线程不取 bus sem，DeviceBus 线程按序取锁。

### DeviceBus 线程

- 线程优先级：`RT_THREAD_PRIORITY_MAX/6 ≈ 42`（当 `_thread_priority=0` 时）
- 栈大小：8KB，堆分配
- 调度周期：min 100µs sleep，max 50ms
- **只有第一个 `register_periodic_callback()` 时才创建线程**
- 创建时机：`start()` 阶段（detect_backends 之后）

## 快速调试命令备忘录

```bash
# 1. 读 setup_stage
echo "mdw 0x2001bf34 1" | nc -q 2 localhost 4444

# 2. 读 main_loop_iterations
echo "mdw 0x20019c48 1" | nc -q 2 localhost 4444

# 3. 读 SPI 状态
echo "mdw 0x40013008 1" | nc -q 2 localhost 4444  # SPI1_SR
echo "mdw 0x40013000 1" | nc -q 2 localhost 4444  # SPI1_CR1
echo "mdw 0x40013004 1" | nc -q 2 localhost 4444  # SPI1_CR2

# 4. 读 GPIO 配置
echo "mdw 0x40022000 1" | nc -q 2 localhost 4444  # GPIOG_MODER (PG11=SCK)
echo "mdw 0x40020000 1" | nc -q 2 localhost 4444  # GPIOA_MODER (PA6=MISO)
echo "mdw 0x40020C00 1" | nc -q 2 localhost 4444  # GPIOD_MODER (PD7=MOSI)

# 5. PC 定位
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf <PC_ADDR>
```
