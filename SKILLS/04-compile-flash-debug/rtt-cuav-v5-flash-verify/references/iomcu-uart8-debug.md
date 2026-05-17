# IOMCU UART8 调试记录（2026-05-15）

## 症状

- CDC 枚举到 `/dev/ttyACM1`（最新时间戳）
- `ser.read()` 返回 0 字节
- `rtt_dbg_main_loop_entry_called = 0x12345678`
- `rtt_dbg_main_loop_iterations = 0`
- `rtt_dbg_setup_stage = 662`（卡在 `ins.init()`）

## 根因定位

### 1. 检查启动阶段变量

```bash
# 先读取 debug 变量地址
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep -E "setup_stage|hal_run|loop_iter|main_loop"

# halt MCU 读取
(echo "halt";
 echo "mdw 0x<setup_stage_addr> 1";
 echo "mdw 0x<loop_iter_addr> 1";
 echo "mdw 0x<hal_run_addr> 1";
 sleep 1;
 echo "resume") | nc -q6 localhost 4444 | strings | grep "0x200"
```

### 2. 多线程采样（区分阻塞线程 vs 正常睡眠）

```bash
for i in 1 2 3; do
  arm-none-eabi-gdb -batch \
    -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
    -ex "target extended-remote :3333" \
    -ex "set remotetimeout 2" \
    -ex "monitor halt" \
    -ex "bt 5" \
    -ex "monitor resume" \
    -ex "quit" 2>&1 | grep "^#"
done
```

### 3. IOMCU 阻塞判定

如果出现以下调用链 → IOMCU UART 超时：

```
#?  AP_IOMCU::read_registers (AP_IOMCU.cpp:680)
#?  AP_IOMCU::read_status (AP_IOMCU.cpp:506)
#?  AP_IOMCU::thread_main (AP_IOMCU.cpp:345)
    →  RTT::UARTDriver::wait_timeout (UARTDriver.cpp:399)
    →  rt_sem_take(timeout=2)
```

### 4. IOMCU AP_IOMCU 初始化流程

```
init()
├─ uart.begin(1500*1000, 128, 128)    // 1.5Mbps
├─ check_crc()
│  ├─ read_registers(PAGE_SETUP, CRC)  // 首次 UART 通信
│  │  └─ wait_timeout() → rt_sem_take() → 超时
│  ├─ write_registers(REBOOT_BL_MAGIC) // 复位到 bootloader
│  ├─ uart.begin(115200)               // 切换 bootloader baud
│  └─ upload_fw()
│     ├─ sync()  // 150ms 超时找 bootloader
│     ├─ get_info() → 超时失败 → return false
│     └─ "IOMCU fw upload failed"
└─ thread_create(IOMCU thread)        // 仍然创建线程
   └─ thread_main() → 循环 read_status → 持续超时
```

## UART8 配置验证清单

### GPIO 配置（CubeMX HAL_MspInit）

```c
// PE0 = UART8_RX, PE1 = UART8_TX (AF8)
GPIO_InitStruct.Pin = GPIO_PIN_0 | GPIO_PIN_1;
GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
GPIO_InitStruct.Pull = GPIO_PULLUP;
GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
GPIO_InitStruct.Alternate = GPIO_AF8_UART8;
HAL_GPIO_Init(GPIOE, &GPIO_InitStruct);
```

### 时钟

```c
__HAL_RCC_UART8_CLK_ENABLE();  // APB1 clock
```

### 设备注册

```c
// drv_usart.c: 由 BSP_USING_UART8 控制条件编译
UART8_IRQHandler(void)  // 驱动入口
```

### 串口设备名与索引

从 hwdef.h 生成的 `HAL_RTT_UART_DEVICE_LIST`：
```
"usb-acm0", "uart2", "uart3", "uart1", "uart4", "uart6", "uart7", "usb-acm0", "uart8"
```
IOMCU UART index = 8（第9个元素）= "uart8"

## 硬件可能原因（与软件配置无关）

1. **IOMCU 载板未连接** — CUAV V5 的 IOMCU 在独立 STM32F0 载板上，通过排针连接。桌面调试时可能未接
2. **IOMCU 未供电** — 需检查 PE3 VDD_3V3_SENSORS_EN 是否输出 3.3V
3. **IOMCU flash 为空** — 从未烧录 bootloader，或之前被全片擦除
4. **USART6_TX 干扰** — PG14 如果未按 ChibiOS 方式注释掉，其输出可能干扰 IOMCU SBUS 输入线

## 验证方法（示波器/逻辑分析仪）

- **PE1 (UART8_TX)**：启动后应发送 bootloader 同步序列（115200 baud, 0x00/0xFF 交替）
- **PE0 (UART8_RX)**：如果 IOMCU 在听，应回传 bootloader 应答
- **PE3**：确认 3.3V 输出（传感器 + IOMCU 供电）

## 临时绕过

```bash
# 1. 在 hwdef.dat 注释掉 IOMCU
# IOMCU_UART UART8

# 2. 重建
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)

# 3. 烧录验证
openocd -f Tools/debug/openocd-f7.cfg \
  -c "program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify; reset run; exit"
```

## 相关文件

- `libraries/AP_IOMCU/AP_IOMCU.cpp` — IOMCU 主逻辑
- `libraries/AP_IOMCU/fw_uploader.cpp` — 固件上传
- `libraries/AP_HAL_RTT/UARTDriver.cpp` — wait_timeout() 实现
- `libraries/AP_HAL_RTT/HAL_RTT_Class.cpp` — ioUartDriver 实例化
- `modules/rt-thread/bsp/stm32/libraries/HAL_Drivers/drivers/drv_usart.c` — UART8_IRQHandler
- `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/CubeMX_Config/Src/stm32f7xx_hal_msp.c` — UART8 MSP
