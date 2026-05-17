# RTT 移植常见陷阱（2026-05-08 更新）

## 1. rt_kprintf 调试残留污染 CDC

**位置**: `libraries/AP_HAL_RTT/` 下各模块中的 `rt_kprintf` 调用

**症状**: CDC 持续输出文本（"ADC STATUS"、"ADC FIRST" 等），MAVLink 帧被污染，GCS 无法识别。

**根因**: 每 ~10 秒通过 `rt_kprintf` 打印的周期性调试消息在飞行中完全不需要。修复 `UARTDriver::_begin()` 中 `rt_console_output_set_enabled(RT_FALSE)` 可能不生效（条件 `dev == console_dev` 不匹配或定时器在 UART 初始化前已启动）。

**修复**: 直接删除周期性的 `rt_kprintf` 调用，保留错误/初始化一次性消息。调试信息通过 GDB 检查变量获取。

**经验**: `rt_kprintf` 在 CDC 上的调试输出需特殊管理。一次性消息 OK，周期性打印必须在生产构建中删除或条件编译。

**检查清单**:
```
grep -n 'rt_kprintf(' libraries/AP_HAL_RTT/*.cpp | grep -v '//' | grep -v 'panic\|FAILE'
```
每条结果都需确认是否为周期性打印（在循环/定时器中）。

## 2. DeviceBus 线程栈大小不当导致创建失败

**位置**: `libraries/AP_HAL_RTT/DeviceBus.cpp` — `rt_thread_create(..., STACK_SIZE, ...)`

**症状**: CDC 输出 "DeviceBus: FAILED to create thread!" — 传感器初始化失败（BMI088 未识别等）。

**根因**: 32KB 线程栈太大。RT-Thread 主线程 48KB，定时器 16KB。3-4 个 SPI 总线线程各 32KB → 堆耗尽。

**修复方案A（推荐 — 静态分配，彻底避免堆耗尽）**:
```cpp
// 静态数组代替动态创建
static struct rt_thread _bus_thread_objs[MAX_BUSES];
static char _bus_thread_stacks[MAX_BUSES][8192]; // 8KB per bus

// 用 rt_thread_init 代替 rt_thread_create
rt_thread_init(&_bus_thread_objs[slot], name,
               _bus_thread_entry, this,
               _bus_thread_stacks[slot], sizeof(_bus_thread_stacks[slot]),
               prio, 20);
rt_thread_startup(&_bus_thread_objs[slot]);
```

**修复方案B（最小改动 — 仅减栈）**: 32KB → **8192**。DeviceBus 线程只轮询回调列表 + rt_thread_delay，8KB 足够。

**经验**: 移植 ChibiOS 到 RT-Thread 注意栈大小。ChibiOS 默认 512-2048 字节，RT-Thread 类似。`rt_thread_create` 从堆分配栈+控制块，堆耗尽时不报错只返回 NULL。

## 3. 堆耗尽导致 AP_HAL::panic（Logger IO 线程创建失败）

**位置**: `libraries/AP_Logger/AP_Logger.cpp:1480` — `AP_HAL::panic("Failed to start Logger IO thread")`

**症状**: CDC 输出 "AP_HAL::panic: Failed to start Logger IO thread"，系统硬夯死（`__disable_irq(); while(1){}`）。CDC 后续无数据。

**根因**: `Scheduler::init()` 中 8 个 `rt_thread_create`（ap_mon 2048、ap_timer 16384、ap_rcout 2048、ap_rcin 2048、ap_uart 2048、ap_io 2048、storage 2048 + 各 Task 线程）+ DeviceBus 线程（32KB）→ 堆耗尽。

**检测**:
```bash
# 查看 CDC 输出中是否有 "AP_HAL::panic:"
timeout 30 cat /dev/ttyACM0 2>/dev/null | strings | grep -i "panic\|Failed"
```

**修复**: 将所有 `rt_thread_create` 改为 `rt_thread_init` + 静态栈（模式同 DeviceBus 修复方案A）。

**已知触发路径**（已修复 DeviceBus，但仍可能触发）:
1. Logger::init() → 创建 IO thread → rt_thread_create 耗尽堆 → panic

## 4. GDB 检测栈溢出（0x23232323 模式）

**方法**: 当 MCU 卡死或异常时，用 GDB 检查栈底：

```bash
arm-none-eabi-gdb -batch -nx \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "info threads" \
  -ex "bt 10" \
  -ex "monitor resume" 2>&1
```

**判断**: RT-Thread 用 `0x23232323` 作为栈填充/哨兵值（'#' × 4）。如果 backtrace 帧中出现 `0x23232323` 或 `0x23232322`，或 backtrace 异常截断（"previous frame identical to this frame (corrupt stack?)"），几乎可以确认栈溢出。

**更深层检查**:
```bash
# 查看线程实际栈使用
arm-none-eabi-gdb -batch -nx \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "thread apply all bt" \
  -ex "monitor resume" 2>&1
```

**典型案例**: DeviceBus 线程 2KB 栈不够 → get_micros64 → _notify_new_gyro_raw_sample → _accumulate_sensor_rate_sampling → _read_fifo → DeviceBus::_bus_thread_entry → 6 帧深度导致 2KB 溢出。

**经验**: IMU 传感器回调链可能很深（6+ 帧），至少需要 4-8KB 栈。

## 5. MCU 被 GDB halt 后未恢复

**位置**: CC/用户通过 `arm-none-eabi-gdb -ex "monitor halt"` 调试后

**症状**: 固件似乎卡死、USB CDC 无数据、CC 认为 MCU 在运行但实际 halted。

**根因**: `gdb -batch -ex "monitor halt" -ex "..."` 执行完后 GDB 退出但 MCU 仍 halt。后续所有操作看起来像固件死机。

**恢复**: 
```bash
echo "poll" | nc -q1 localhost 4444 | grep -i "halt"
# 若有 "halted" → 立即 resume
echo "resume" | nc -q1 localhost 4444
```

**自动化**: 每次监控循环的第一操作。

**CDC 恢复时间**: resume 后需等待 bootloader 5s + USB 枚举 15-17s 才有数据。

## 6. `reset init` 破坏 USB CDC 连接

**位置**: 任何使用 `monitor reset init` 或 `echo "reset init" | nc -q1 localhost 4444` 后

**症状**: `cat /dev/ttyACM0` 返回空或二进制垃圾。`ls -la /dev/ttyACM*` 仍显示设备存在，
但无任何 MCU 文本输出。

**根因**: STM32F7 的 USB DWC2 OTG 外设在 hot reset 后不重建端点。
MCU 的 USB 栈从复位向量重新初始化，但 USB 主机（PC）侧端口状态未更新，
导致 CDC ACM 处于半连接状态。部分 USB 描述符/配置请求从双方不匹配。

**修复**:
- **永久方案**: 修复 RTT CherryUSB 栈的 `reset` 恢复路径（`usbd_core.c` 中 `usbd_reset()` 应清空端点状态并触发 re-enumeration）。
- **临时方案**:
  1. 用 `resume` 代替 `reset init` 保留 MCU 运行状态
  2. 直接 GDB 设置 `$pc` 和 `$sp` 跳转到 app 而不 reset MCU：
     ```
     echo "halt" | nc -q1 localhost 4444
     echo 'reg sp 0x200682ec' | nc -q1 localhost 4444  # 从 vector table 读取
     echo 'reg pc 0x0806fef8' | nc -q1 localhost 4444  # reset_handler 地址
     echo "resume" | nc -q1 localhost 4444
     ```
  3. 物理重连 USB（仅调试用，用户禁止）
- **检测**: `echo "mdw 0x08008000 4" | nc -q1 localhost 4444` 验证 app 的 vector table 完好

## 7. CDC 捕获时序陷阱

**症状**: `timeout N cat /dev/ttyACM0 2>/dev/null` 返回空。

**原因**: CDC 数据在特定窗口才有时序敏感：
- 复位后前 5s: bootloader 阶段，无 CDC
- 5-20s: 固件启动 + USB 枚举
- 20-35s: 启动日志输出窗口
- 35s+: 仅 MAVLink 数据流（二进制）

**正确捕获**:
```bash
# 重启 → 等 25s → 捕获
echo "reset" | nc -q1 localhost 4444 2>/dev/null
sleep 25
timeout 20 cat /dev/ttyACM0 2>/dev/null | strings | sort -u
# 捕获 MAVLink 原始数据用 xxd
timeout 10 cat /dev/ttyACM0 2>/dev/null | xxd | head -20
```

**注意**: `cat /dev/ttyACM0` 可能在数据到达时阻塞返回，但 timeout 可能提前结束。用较长 timeout (15-30s) 保证捕获完整。

## 7. DeviceBus 共享线程串行化陷阱

**位置**: 尝试将 DeviceBus 从每总线一线程改为单共享线程

**症状**: 启动后 BMI088 传感器芯片 ID 随机变化（0x01/0x00/0x10/0x11/0x20，期望 0x1E），传感器探测失败。

**根因**: 共享线程按固定优先级（5）依次轮询所有 SPI 总线的回调。IMU 传感器（SPI4/SPI1）的 DMA 回调被挤在同线程中，芯片通信时序被破坏。

**教训**: 每个 SPI 总线的回调应各自独立线程运行。同优先级轮询会串行化不同总线事务，破坏 SPI 从设备的时序要求。**必须保持每总线一线程设计。**

## 8. 符号表 ELF 选择陷阱

**位置**: `build/rtt_cuav_v5/rt-thread.elf` vs `build/rtt_deploy/cuav_v5/rt-thread.elf`

**症状**: `arm-none-eabi-nm build/rtt_cuav_v5/rt-thread.elf` 返回 **0 个符号**。GDB `bt` 只显示地址无函数名。

**根因**: scons 构建流程中 LINK 后 objcopy -O binary, 生成目录的 ELF 符号表可能不完整。deploy 目录的 ~37MB ELF 是完整副本。

**正确做法**:
```bash
# 符号查找 → 用 deploy 目录的 ~37MB ELF
arm-none-eabi-nm -C build/rtt_deploy/cuav_v5/rt-thread.elf | grep "Invensense::probe"
arm-none-eabi-nm -n build/rtt_deploy/cuav_v5/rt-thread.elf | grep -i "ins\|sensor" | head -10

# GDB 调试 → 加载 deploy ELF 获得符号
arm-none-eabi-gdb \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" -ex "bt 10" -ex "monitor resume"
```

**检测**: `nm build/rtt_cuav_v5/rt-thread.elf | wc -l` → 0 则换路径。

## 9. OpenOCD 进程管理

**症状**: 新 OpenOCD 连接失败（`claim interface failed`）、GDB 无法连接。

**根因**: 旧 OpenOCD 后台进程占用 ST-Link USB。

**安全重启**:
```bash
pgrep -x openocd | xargs -r kill -9 2>/dev/null
sleep 1
# Hermes terminal(background=true) 启动新进程
```

**禁止**: `pkill -9 -f openocd` — 会误杀 Hermes 自身。
**禁止**: `fuser -k` — 误杀所有持有资源者。

## 11. SPI1 GPIO 引脚诊断路线（INS 初始化失败根因追踪）

### 步骤1：确认 MCU 运行状态
```bash
echo "poll" | nc -q1 localhost 4444 | grep -i "halt\|run"
# → running （非 halted）
ls -la /dev/ttyACM*
# → 两个 CDC 设备存在
```

### 步骤2：捕获 CDC 输出定位错误
```bash
timeout 10 cat /dev/ttyACM0 2>/dev/null | strings | grep -i "error\|fail\|panic\|unable\|config"
# → "Config Error: INS: unable to initialise driver"
```

### 步骤3：定位源代码中的错误打印
```bash
grep -rn "unable to initialise driver" libraries/AP_InertialSensor/
# → AP_InertialSensor.cpp:1318 — `_backend_count == 0` 时触发
```

### 步骤4：检查 hwdef 生成的 SPI 设备表
```bash
cat build/rtt_cuav_v5/hwdef.h | grep "IMU\|SPI_DEVICE\|INS_PROBE"
# → HAL_INS_PROBE1~4 全部存在，设备表正确
```

### 步骤5：追踪 SPI 传输路径
```cpp
// SPIDevice.cpp 中 bus==1 的设备走 _dev==nullptr 路径（寄存器级轮询）
// 而非 RTT SPI 框架路径
```
关键函数: `SPIDevice::transfer()` → 当 `_dev==nullptr` → 调用 `spi1_poll_transfer()`

### 步骤6：对比 ChibiOS GPIO 配置
```bash
# 对比 ChibiOS HAL_MspInit
cat board/CubeMX_Config/Src/stm32f7xx_hal_msp.c | grep -A20 "Instance == SPI1"
# → PG11=SCK, PA6=MISO, PD7=MOSI

# 对比 ChibiOS hwdef
cat libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat | grep "SPI1\|spi1"
```

### 步骤7：验证目标板的 flash 内容
```bash
# 检查 app 的 vector table
echo "mdw 0x08008000 4" | nc -q1 localhost 4444
# 检查 bootloader vector table
echo "mdw 0x08000000 4" | nc -q1 localhost 4444
# bootloader 存在→PC=0x08000200 正常
# app 存在→0x08008000 有有效 vector
```

### 步骤8：验证修复后 IMU 通信
```bash
# GDB 设置断点在 IMU probe 的 WHO_AM_I 读取处
# 或直接检查 SPIDevice 的诊断变量
echo 'rtt_spi1_rt.last_recv_0' | nc -q1 localhost 4444
# 期望: ICM20689 WHO_AM_I = 0x98
```

### 关键教训
- `_spi1_gpio_init()` 中的 GPIO 引脚表**必须**对照 ChibiOS hwdef/HAL_MspInit 验证，不能依赖注释
- 寄存器级轮询路径完全独立于 RTT SPI 框架，不受 drv_spi.c/LLD 影响
- INS 所有 IMU 都失败（而非只有特定型号失败）→ 很可能是总线级问题（引脚/时钟/电源），而非 IMU 驱动问题
- `reset init` 后 CDC 不重建 → 用 vector table GDB 跳转法避免全复位

## 12. SPI4 GPIO 惰性初始化导致 MCU 静默（即新 pitfall）

**症状**: 在 `SPIDevice.cpp` 中添加 `_spi4_gpio_init()` 并在 `transfer()` 中惰性调用后，MCU 启动后 CDC 无任何输出（无 boot log、无 MAVLink），仅 USB 枚举存在。

**类比**: 与 SPI1 的 `_spi1_gpio_init()` 完全相同的模式，但 SPI1 在 IMU probe 之前就被调用，而 SPI4 在 `SPIDevice::transfer()` 中才首次调用。区别在于：
- SPI1 GPIO init 在主线程 `AP_InertialSensor::init()` 期间触发（RTT 框架已就绪）
- SPI4 GPIO init 在 `AP_Baro_MS56XX::_init()` 的第一次 `_dev->transfer()` 中触发

**可能根因**:
1. **外设时钟使能顺序** — SPI4 时钟在 `RCC_APB2ENR_SPI4EN`（APB2 位 12）可能与其他 APB2 外设（SPI1）的时钟使能产生时序竞争
2. **GPIO 寄存器被 RTT 框架覆盖** — `rt_hw_spi_bus_init()` 可能在其他 INIT_BOARD_EXPORT 函数中配置了 GPIOE 但未启用其时钟，导致 `_spi4_gpio_init()` 写入 GPIOE 寄存器时发生总线错误
3. **惰性初始化在 ISR 上下文中触发** — 如果 MS5611 的 `register_periodic_callback()` 比 `_init()` 中的第一次 transfer 更早触发 `_timer()`（含 SPI 传输），可能触发死锁

**经验教训**: 
- **不在 SPIDevice.cpp 的 `transfer()` 内部做新总线的 GPIO 惰性初始化**
- 新的总线 GPIO init 应在板级初始化阶段（`rt_board_init.c::_spi_lld_board_init()`）完成，确保在 ArduPilot HAL 代码运行前引脚已配置
- 对于 SPI4，正确的修改位置是 `rt_board_init.c` 第 172-204 行的 `_spi_lld_board_init()` 函数，添加纯 GPIO init（无 DMA）代码

## 13. SPI 引脚配置追错方法（通用模式）

当 SPI 外设通信失败时（全零数据、WHO_AM_I 读 0xFF 等），系统化排查步骤：

### 步骤 A — 确认谁在配置这个 SPI
```bash
# 检查 CubeMX 自动生成
grep -B5 -A20 "Instance == SPI{N}" board/CubeMX_Config/Src/stm32f7xx_hal_msp.c
# 检查 LL 驱动
grep -B5 "spi{N}_ll_cfg =" board/drivers_ll/drv_spi_ll.c
# 检查 SPIDevice 寄存器级路径
grep "bus == {N}" libraries/AP_HAL_RTT/SPIDevice.cpp
```

### 步骤 B — 对比引脚表（三重验证）
```bash
# 1. ChibiOS 参考（已知正确）
grep "SPI{N}" libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat
# 2. RTT hwdef（已检查）
grep "SPI{N}" libraries/AP_HAL_RTT/hwdef/cuav_v5/hwdef.dat
# 3. LL 驱动配置（可能出错！）
sed -n '/spi{N}_ll_cfg/,/^};/p' modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/drivers_ll/drv_spi_ll.c
```

### 步骤 C — 检查总线注册条件
```bash
# 确认 LL 总线被编译
grep -A5 "BSP_SPI{N}_RX_USING_DMA\|BSP_SPI{N}_TX_USING_DMA" rtconfig.h
# 确认 attach 表包含
grep "spi{N}" build/rtt_cuav_v5/hwdef.h | grep ATTACH
```

## 10. 固件验证完整流程

| 模式 | 症状 | 纠正 |
|------|------|------|
| GDB 过度调试 | CC 连续多轮 GDB halt/step/bt，日志全为 GDB 输出 | 恢复 MCU，检查固件是否已 boot，引导 CC 推进而非深挖 |
| 调查已解决的问题 | 查 Semaphores 递归 mutex（已实现）、查 SysTick（正常） | 用 `git log --oneline` 确认已有提交，跳过 |
| 根因误判 | 认为是 USB driver 问题 → 实际是调试打印问题 | 检查 CDC 原始数据（`xxd`），确认是文本还是二进制污染 |
| 栈溢出当死锁 | 固件在 `_delay_microseconds_dwt` 循环，CC 判断为死锁 | 用 bt 检查栈帧，确认是否 `0x23232323` 溢出模式 |

## 10. 固件验证完整流程

1. **编译**: `python3 -m SCons --target=cuav-v5 -j$(nproc)` → 检查错误
2. **烧录**: GDB load → `monitor reset run`
3. **等待**: 25s（5s bootloader + 20s 固件启动）
4. **CDC 检查**: `timeout 10 cat /dev/ttyACM0 2>/dev/null | strings` — 应看到启动日志
5. **MAVLink 检查**: 用 `xxd` 查看原始二进制中是否有 `0xFD`（MAVLink v2 STX）
6. **无 FAILED/panic**: grep 输出中无 "FAILED"、"panic"、"error"（不区分大小写）
7. **MCU 运行中**: `echo "poll" | nc -q1 localhost 4444` 显示 running（无 "halted"）
8. **CC 是否活跃**: 如果 CC 在运行，检查其日志是否在最近 10 分钟内更新
