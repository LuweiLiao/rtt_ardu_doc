# CUAV V5 RTT IOMCU 状态

> **发现时间**: 2026-05-09
> **最后更新**: 2026-05-10（通信确认 flags_rc_ok=false）
> **适用场景**: RC 输入 (SBUS) 不工作，RC_CHANNELS 全部为 0

## 当前状态（2026-05-10）

| 维度 | 状态 | 证据 |
|------|------|------|
| IOMCU 通信 | ✅ **正常** | 内存变量: step=10 "ioevent_init_ok", mcuid=0x10016420 |
| UART8 设备 | ✅ 已注册 | drv_usart.c + rt_hw_usart_init() |
| IOMCU 固件 | ✅ 已嵌入 | io_firmware_lowpolh.bin 在 ROMFS |
| RC_CHANNELS | ❌ ch1-4=0, rssi=255 | 无 RC 信号输入 |
| 系统状态 | ⚠️ STANDBY(3) / CRITICAL(5) | 取决于是否预飞检查 |

## 硬件架构

```
SBUS 接收机 → DSM/SBUS 端口 → IOMCU(STM32F100) → UART8(PE0/PE1) → STM32F767
```

## RC 数据流

```
IOMCU 侧 (STM32F100，运行 iofirmware_lowpolh.bin)：
  USART1(SD1, 115200) ← DSM 信号 (Spektrum卫星)
  USART3(SD3, 100000) ← SBUS 信号 (反转+8E2)
    │
  iofirmware/rc.cpp:rcin_serial_update()
    → rc.process_byte() → 协议检测 (SBUS/DSM/FPORT)
    → flags_rc_ok=true (有效数据) → 存储到 page_rc_input 寄存器

主 CPU 侧 (STM32F767)：
  AP_IOMCU::read_rc_input()          ← 50Hz 读 PAGE_RAW_RCIN
    → rc_last_input_ms = now         (仅 flags_rc_ok && !failsafe 时)
  AP_RCProtocol_IOMCU::update()      ← 通过 AP::RC().update() 调用
    → iomcu.check_rcinput()          → last_frame_us 比对
    → add_input()  → AP_RCProtocol
  RTT::RCInput::_timer_tick()        ← 通过 scheduler 调用
    → AP::RC().update() → new_input() → 读 _rc_values
```

## IOMCU 固件 RC 检测机制

IOMCU 固件 (`libraries/AP_IOMCU/iofirmware/rc.cpp`) 的检测逻辑：

```c
rc_state = RC_SEARCHING;       // 初始状态

// DSM 端口 (USART1, 115200)
if (chnReadTimeout(&SD1, buf, 16, 0)) {
    rc.process_byte(b, 115200);
    if (!rc.should_search(now)) rc_state = RC_DSM_PORT;
}

// SBUS 端口 (USART3, 100000, 8E2)
if (chnReadTimeout(&SD3, buf, 16, 0)) {
    rc.process_byte(b, serial_rcin_config==0 ? 100000 : 115200);
    if (!rc.should_search(now)) rc_state = RC_SBUS_PORT;
}
```

`flags_rc_ok` 在 `add_input()` 中被设为 true，
该函数由 `AP_RCProtocol::process_byte()` → 协议 decode → `add_input()` 调用。

## 诊断内存变量法

### 变量定义

```cpp
volatile uint32_t diag_step = 0;
volatile uint32_t diag_val1 = 0;
volatile uint32_t diag_val2 = 0;
volatile const char *diag_label = "init";

#define IOMCU_DIAG(label, v1, v2) do { \
    diag_step++; diag_val1 = (uint32_t)(v1); \
    diag_val2 = (uint32_t)(v2); diag_label = (label); \
} while(0)
```

### 诊断点覆盖

```
step 1  init_entered
step 2  uart_begin_done
step 3  calling_check_crc / skipped_check_crc
step 4-35 check_crc_try(32次, read_ok结果)
step 36 crc_match(io_crc, fw_crc) / crc_mismatch(io_crc, fw_crc)
step 37 sending_reboot_bl
step 38 calling_upload_fw
step 39 upload_fw_succeeded / upload_fw_failed
step 40 check_crc_done(crc_is_ok)
step N+1 thread_main_entered
step N+2 ioevent_init_start
step N+3 ioevent_init_ok(mcuid, protocol_version) / ioevent_init_fail(fail_count)
```

### 执行命令

```bash
# 1. 编译后找符号地址
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep diag

# 2. 烧录运行后 halt 读取
echo "halt" | nc -q 2 localhost 4444
echo "mdw 0x<diag_step_addr> 3" | nc -q 1 localhost 4444  # step, val1, val2
echo "mdw 0x<diag_label_addr> 1" | nc -q 1 localhost 4444  # label 指针
echo "mdw <label_pointer> 4" | nc -q 1 localhost 4444      # 读字符串
echo "resume" | nc -q 1 localhost 4444

# 字符串解码示例（Little-Endian ASCII）：
# 0x566f6965 = "eVoi" → "ioeV" → 反转："ioev" (其实是 "ioevent_init_ok")
```

## 已知问题

### 1. IOMCU 通信正常但 RC_CHANNELS=0
IOMCU 通过 UART8 双向通信已确认工作，但 `flags_rc_ok=false`：
- **可能原因**：SBUS 接收机未上电、未对频、未发送数据
- **可能原因**：CUAV V5 的 IOMCU 引脚映射与标准 `iofirmware_lowpolh.bin` 预期不符
- **可能原因**：SBUS 信号反转不匹配（IOMCU 固件默认不设 RXINV，需硬件电平反转）

### 2. rt_kprintf 在启动阶段不可见
USB CDC 控制台在 MAVLink 启动后调用 `rt_console_output_set_enabled(RT_FALSE)` 抑制输出。早期 rt_kprintf 发出的数据因 USB 主机未 polling IN 端点而被丢弃。

**替代方案**：全局 volatile 变量 + OpenOCD 读取。

## 引脚配置

```
# UART8 (IOMCU←→主CPU)
PE0  UART8_RX  UART8 AF8   ← IOMCU TX
PE1  UART8_TX  UART8 AF8   → IOMCU RX

# SPEKTRUM 电源
PE4  SPEKTRUM_PWR  OUTPUT HIGH  GPIO(73)

# 5V 外设电源
PG4  nVDD_5V_PERIPH_EN  OUTPUT HIGH
PF12 nVDD_5V_HIPOWER_EN OUTPUT HIGH
```

## 历史

| 时间 | 事件 |
|------|------|
| 2026-05-09 16:25 | ad7ac796d2: 首次启用 IOMCU_UART UART8 |
| 2026-05-09 16:30 | 60d28fffd6: Revert（缺少 UART8 设备注册）|
| 2026-05-09 17:01 | b8a92f78f0: 正确启用 IOMCU（+设备注册+IRQ）|
| 2026-05-10 | 内存变量法确认 IOMCU 通信正常，flags_rc_ok=false |
