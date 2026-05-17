# IMU WHO_AM_I Probe via `rtt_spi1_rt` Struct

2026-05-13 session 验证的诊断技术。

## 背景

IMU RAW_IMU 全零时，需要快速区分是 SPI 物理层故障（引脚配置、供电）还是 IMU 寄存器级配置问题。最好的方法是直接读 WHO_AM_I 寄存器。

## 添加诊断字段到 `rtt_spi1_rt`

```c
/* SPIDevice.cpp line 101-111 */
volatile struct {
    uint32_t spi1_xfer_calls;
    uint32_t spi1_tx_bytes;
    uint32_t spi1_rx_bytes;
    uint32_t last_recv_0;
    uint32_t last_recv_1;
    uint32_t icm20689_whoami;     // ← 新增
    uint32_t icm20602_whoami;     // ← 新增
    uint32_t spi1_gpio_status;    // ← 新增
} rtt_spi1_rt = {};
```

## WHO_AM_I Probe（关键！必须用 send_len=1）

```c
// 在 SPIDevice 构造函数中，bus==1 的 IMU probe 处：
if (strstr(_desc.name, "icm20689")) {
    uint8_t addr = 0x75 | 0x80;     // WHO_AM_I 寄存器地址 + 读标志
    uint8_t val = 0;
    spi1_poll_transfer(nullptr, &addr, 1, &val, 1,  // ← send_len=1, recv_len=1！
                       true, true, bus_to_spi(1), _cs_pin);
    rtt_spi1_rt.icm20689_whoami = val;
}
```

### ⚠️ 陷阱：为什么不能用 send_len=2, recv_len=2

`spi1_poll_transfer()` 对非全双工模式的接收数据采用 `memcpy(recv, buf + send_len, recv_len)` 返回。

如果用 `tx[2]={0xF5, 0x00}, rx[2]={0,0}` + `send_len=2, recv_len=2`：
- total_len = 4（非全双工：send_len + recv_len）
- buf 初始化：[0xF5, 0x00, 0x00, 0x00]
- 循环后 buf：[dummy, WHO_AM_I(0x98), garbage, garbage]
- recv = buf[2..3] = [garbage, garbage] ← **值被吃掉了！**
- WHO_AM_I 真正在 buf[1]，但函数返回 buf[2] 起的数据

**必须用 send_len=1, recv_len=1**，让 buf[0]=address, buf[1]=response。

## GPIO MODER 位域编码

GPIO status 打包在 `spi1_gpio_status`（8位有效）：

```
Bits [1:0]   = PA6 MODER[13:12]   → 0b10 (AF) = SPI1_MISO
Bits [3:2]   = PD7 MODER[15:14]   → 0b10 (AF) = SPI1_MOSI
Bits [5:4]   = PG11 MODER[23:22]  → 0b10 (AF) = SPI1_SCK
```

正确的 capture 代码：

```c
rtt_spi1_rt.spi1_gpio_status = ((GPIOA->MODER >> 12) & 3) |       // PA6 @ [1:0]
                                (((GPIOD->MODER >> 14) & 3) << 2) | // PD7 @ [3:2]
                                (((GPIOG->MODER >> 22) & 3) << 4);  // PG11 @ [5:4]
```

期望值：`0x2A`（所有引脚 AF 模式）。如果看到 `0x00` 说明 capture 代码的位偏移有 bug，不是 GPIO 真的没配置。

## 读取方法

编译烧录后，系统在 STANDBY，通过 OpenOCD telnet 读取：

```bash
# 查符号地址
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rtt_spi1_rt
# → 20019998 b rtt_spi1_rt

# halt 后读 8 个字段（32 字节）
echo -e "mdw 0x20019998 8\nresume\nexit" | nc -w 5 localhost 4444
```

输出解析：

| 偏移 | 字段 | 例子 | 含义 |
|------|------|------|------|
| +0 | spi1_xfer_calls | 0x5f2=1522 | SPI 传输次数 |
| +4 | spi1_tx_bytes | 0x5f2 | 发送字节数 |
| +8 | spi1_rx_bytes | 0x8ec=2284 | 接收字节数 |
| +12 | last_recv_0 | 0x85 | 最后接收的第 1 字节 |
| +16 | last_recv_1 | 0x90 | 最后接收的第 2 字节 |
| +20 | icm20689_whoami | **0x00** 或 **0x98** | ICM20689 存活标志 |
| +24 | icm20602_whoami | 0x00 或 0x12 | ICM20602 存活标志 |
| +28 | spi1_gpio_status | 0x2A | GPIO MODER 状态 |

## 结果解读

| icm20689_whoami | 含义 |
|-----------------|------|
| **0x98** | IMU 在 SPI 总线上存活 ✅ |
| **0x00** | IMU 不响应 SPI 寄存器读 ❌ |
| **0x00** + spi1_xfer_calls > 0 | SPI 硬件在工作但 IMU 未正确回应 |
| **0xFF** | SPI 总线浮空（MISO 上拉） |
| **0x00** + spi1_xfer_calls = 0 | SPI 传输从未发生 |

## 本会话验证结果

2026-05-13 CUAV V5 (STM32F767) + RTT ArduPilot:

```
spi1_xfer_calls   = 0x5f2 (1522)  ← SPI 硬件正常工作
spi1_tx_bytes     = 0x5f2
spi1_rx_bytes     = 0x8ec (2284)
last_recv_0       = 0x85
last_recv_1       = 0x90
icm20689_whoami   = 0x00  ← IMU 不响应！
icm20602_whoami   = 0x00
spi1_gpio_status  = 0x00  ← capture 代码 bug（不是真值）
```

**结论**：SPI 硬件正常（1522次传输），GPIO 配置正确（OpenOCD 直读寄存器确认），但 IMU 芯片不返回有效数据 → 根因在**IMU 供电或复位**，不是 SPI 协议/引脚问题。
