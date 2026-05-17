# RTT CDC TX 数据流分层调试方法论

> 2026-05-12 记录。在 RTT ArduPilot 移植中，USB CDC TX 无输出时，应按层级追踪。

## 一、层级模型

```
┌──────────────────────────────────────┐
│  Layer 0: USB 枚举                   │
│  /dev/ttyACM* | dmesg | lsusb        │
├──────────────────────────────────────┤
│  Layer 1: CherryUSB debug counters   │
│  dbg_serial_write_calls              │  ← usbd_serial_write 是否被调用
│  dbg_serial_write_ok                 │  ← 数据是否写入 ringbuffer
│  dbg_serial_tx_kick                  │  ← kick_tx 是否被调用
│  dbg_serial_tx_kick_fail             │  ← ep_start_write 是否失败
├──────────────────────────────────────┤
│  Layer 2: UARTDriver debug counters  │
│  rtt_uart_dbg_tick_calls             │  ← _timer_tick 是否运行
│  rtt_uart_dbg_port_ticks[i]          │  ← 每个端口的 tick 计数
│  rtt_uart_dbg_drain_calls            │  ← _drain_writebuf_to_dev 调用次数
│  rtt_uart_dbg_drain_writes           │  ← rt_device_write 成功次数
│  rtt_uart_dbg_drain_bytes            │  ← 写入字节数
│  rtt_uart_dbg_drain_zero             │  ← rt_device_write 返回 0 次数
├──────────────────────────────────────┤
│  Layer 3: RT-Thread 线程状态         │
│  GDB bt — 当前线程堆栈               │
│  检查: idle thread? main thread?     │
│  rt_defunct_execute → 线程已终止      │
│  rtt_uart_dbg_tick_calls==0          │
│   → UART 线程从未运行                 │
└──────────────────────────────────────┘
```

## 二、调试流程

### Step 1: 确认 USB 枚举
```bash
ls -la /dev/ttyACM*
ls -la /dev/serial/by-id/
# 期望: ArduPilot CUAVv5 RTT RTTUSB0001
# 如果枚举成功 → 跳 Layer 1
# 如果不枚举 → USB 硬件/描述符/配置问题
```

### Step 2: 读 CherryUSB debug 计数器
```bash
arm-none-eabi-gdb -batch -n -ex "target remote :3333" \
  -ex "x/w &dbg_serial_write_calls" \
  -ex "x/w &dbg_serial_tx_kick" \
  /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf
```
- 如果 `dbg_serial_write_calls > 0` → CherryUSB 收到了数据 → 问题在 kick_tx / ISR 路径
- 如果 `dbg_serial_write_calls == 0` → 数据没到达 CherryUSB → 跳 Layer 2

### Step 3: 读 UARTDriver debug 计数器
```bash
arm-none-eabi-gdb -batch -n -ex "target remote :3333" \
  -ex "x/w &rtt_uart_dbg_tick_calls" \
  -ex "x/w &rtt_uart_dbg_drain_calls" \
  -ex "x/10w &rtt_uart_dbg_port_ticks" \
  /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf
```
- 如果 `rtt_uart_dbg_tick_calls > 0` → UART 线程在跑 → _drain_writebuf 返回 0 → CherryUSB ringbuffer 满/写失败
- 如果 `rtt_uart_dbg_tick_calls == 0` → **UART 线程从未运行** → 跳 Layer 3

### Step 4: 诊断 RT-Thread 线程状态
```bash
arm-none-eabi-gdb -batch -n -ex "target remote :3333" \
  -ex "bt" \
  -ex "info registers" \
  /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf
```
关键发现模式：
- `bt` 显示 `rt_defunct_execute` → idle 线程在清理已终止的线程
- `psp` 指向 `idle_thread_stack` → 当前在 idle 线程
- 对比：正常时应指向 `ap_uart` 或 `ap_main` 线程栈

## 三、2026-05-12 实战记录

### 现象
- USB 枚举成功 (`/dev/ttyACM1`)
- 但 `cat /dev/ttyACM1` 无数据输出
- CherryUSB debug 计数器全为 0

### 诊断过程

1. 读 `dbg_serial_write_calls` → **0** → 数据未到达 CherryUSB
2. 读 `rtt_uart_dbg_tick_calls` → **0** → UART 线程从未运行
3. 读 `rtt_uart_dbg_port_ticks[0..9]` → **全 0** → 所有端口从未 tick
4. GDB `bt` → `rt_defunct_execute() → rt_thread_defunct_dequeue()`
5. 确认堆栈在 `idle_thread_stack+196` → 当前在 idle 线程

### 根因（待确认）
UART 线程入口等待 `sched->_hal_initialized` 为 true：
```cpp
while (!sched->_hal_initialized) {
    rt_thread_mdelay(1);
}
```
`_hal_initialized` 由 `set_system_initialized()` 在 `AP_Vehicle::setup()` 末尾设置。
如果主线程的 `setup()` 没完成 → UART 线程永远等待 → 所有 debug counter 为 0。

### 需验证
- [ ] `_hal_initialized` 是否被设置（GDB 读对应内存地址）
- [ ] `_uart_thread_ctx` 是否创建成功（`rt_thread_create` 返回值）
- [ ] `rt_thread_startup` 是否被调用
- [ ] 线程优先级模型是否正确（main=5, UART=6 → UART 不能抢占 main）

## 四、常见坑

### 4.1 误判 CDC 层
"CDC 枚举了但没数据" → 90% 情况下不是 CDC 的问题，而是上游（UARTDriver/线程调度）没在写。

### 4.2 Debug 计数器分布在两个文件
| 文件 | 变量 | 前缀 |
|------|------|------|
| `usbd_serial.c` | `dbg_serial_*` | CherryUSB 层 |
| `UARTDriver.cpp` | `rtt_uart_dbg_*` | AP_HAL_RTT 层 |

两个文件的计数器必须分开读取，不能只看一个。

### 4.3 GDB 符号跨编译会变地址
每次 `scons` 后都需要重新获取符号地址。如果 flash 了旧 binary 但用了新 ELF 读地址，结果无意义。
```bash
# 正确做法：用最新编译的 ELF 文件
arm-none-eabi-gdb ... /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf
```

## 五、计数器变量总表

| 变量名 | 位置 | 含义 | 正常范围 |
|--------|------|------|---------|
| `dbg_serial_write_calls` | usbd_serial.c:48 | write() 被调用次数 | >0 |
| `dbg_serial_write_ok` | usbd_serial.c | write 成功写入 ringbuffer 次数 | >0 |
| `dbg_serial_tx_kick` | usbd_serial.c | kick_tx 被调用次数 | >0 |
| `dbg_serial_tx_kick_fail` | usbd_serial.c | ep_start_write 失败次数 | 0 |
| `dbg_serial_write_timeout` | usbd_serial.c | ringbuffer 满返回 0 次数 | 小值 |
| `dbg_serial_unstick_cnt` | usbd_serial.c | tx_active 自愈触发次数 | 小值 |
| `rtt_uart_dbg_tick_calls` | UARTDriver.cpp:487 | _timer_tick 被调用次数 | >0 |
| `rtt_uart_dbg_port_ticks[i]` | UARTDriver.cpp:489 | 端口 i 的 tick 计数 | 端口 0 应有值 |
| `rtt_uart_dbg_drain_calls` | UARTDriver.cpp | _drain_writebuf_to_dev 调用 | >0 |
| `rtt_uart_dbg_drain_writes` | UARTDriver.cpp | rt_device_write 成功 | >0 |
| `rtt_uart_dbg_drain_bytes` | UARTDriver.cpp | 写入字节总数 | >0 |
| `rtt_uart_dbg_drain_zero` | UARTDriver.cpp | rt_device_write 返回 0 | 小值 |
