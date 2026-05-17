# SPI Register-Level Debugging via OpenOCD Telnet

适用于 STM32F7 (CUAV V5) SPI1/SPI4 寄存器级诊断。

## SPI1 寄存器映射 (STM32F7, base=0x40013000)

| 偏移 | 寄存器 | 用途 | 示例值 |
|------|--------|------|--------|
| 0x00 | CR1 | 控制寄存器1 | `0x0000035f` |
| 0x04 | CR2 | 控制寄存器2 | `0x00001700` |
| 0x08 | SR | 状态寄存器 | `0x00000203` |
| 0x0C | DR | 数据寄存器 | `0x00000000` |
| 0x10 | CRCPR | CRC 多项式 | `0x00000007` |
| 0x14 | RXCRCR | RX CRC | `0x00000000` |
| 0x18 | TXCRCR | TX CRC | `0x00000000` |
| 0x1C | I2SCFGR | I2S 配置 | `0x00000000` |
| 0x20 | I2SPR | I2S 预分频 | `0x00000002` |

## 读取寄存器

```bash
# 一次读10个字 (CR1 到 I2SPR)
{ echo "halt"; sleep 0.3; echo "mdw 0x40013000 10"; sleep 0.3; echo "resume"; } | nc -q1 localhost 4444 2>&1 | strings
```

## CR1 位解码

`0x35f` = `0000 0011 0101 1111`

| 位 | 域 | 值 | 含义 |
|----|----|----|------|
| 15 | BIDIMODE | 0 | 双线 SPI |
| 14 | BIDIOE | 0 | - |
| 13 | CRCEN | 0 | CRC 禁用 |
| 12 | CRCNEXT | 0 | - |
| 11 | DFF | 0 | 8 位数据 |
| 10 | RXONLY | 0 | 全双工 |
| 9 | SSM | 1 | **软件 NSS** ✅ |
| 8 | SSI | 1 | **内部 NSS 选通** ✅ |
| 7 | LSBFIRST | 0 | MSB 优先 |
| 6 | SPE | 1 | **SPI 启用** ✅ |
| 5 | BR2 | 0 | BR=011 |
| 4 | BR1 | 1 | **fPCLK/16 = 6.75MHz** ✅ |
| 3 | BR0 | 1 | (ICM20689 最高 8MHz) |
| 2 | MSTR | 1 | **主机模式** ✅ |
| 1 | CPOL | 1 | CLK 空闲高 (MODE3) ✅ |
| 0 | CPHA | 1 | 第二边沿采样 (MODE3) ✅ |

## SR 位解码

`0x203` = `0000 0010 0000 0011`

| 位 | 域 | 值 | 含义 |
|----|----|----|------|
| 7 | BSY | 0 | **不忙** ✅ (传输完成) |
| 6 | OVR | 0 | 无溢出 |
| 5 | MODF | 0 | 无模式错误 |
| 4 | CRCERR | 0 | 无 CRC 错误 |
| 3 | UDR | 0 | - |
| 2 | CHSIDE | 0 | - |
| 1 | TXE | 1 | **TX 空** 可发送下一字节 |
| 0 | RXNE | 1 | **RX 非空** 有数据可读 |

## SPI4 寄存器 (base=0x40013400)

```bash
{ echo "halt"; sleep 0.3; echo "mdw 0x40013400 10"; sleep 0.3; echo "resume"; } | nc -q1 localhost 4444 2>&1 | strings
```

SPI4 的时钟源是 PCLK1 (54MHz)，所以:
- BR=011(fPCLK/16) = 54MHz/16 = **3.375MHz** (MS5611 最高 20MHz, OK)

## 诊断流程

1. **MCU 卡在 SPI 传输**: 检查 `bt` 是否显示 `spi1_poll_transfer` 在第181行
2. **读 CR1**: 确认 SPE=1, MSTR=1, 波特率正确
3. **读 SR**: 确认 BSY=0 不卡住, RXNE=1 有数据
4. **检查 cs_pin**: backtrace 中的 `cs_pin` 参数标识哪个设备被选中
5. **检查 send_len/recv_len**: 112字节全双工=ICM20689批量读, 小传输=WHOAMI探针

## 注意事项

- OpenOCD halt 时 SPI 外设仍在运行，SR 值可能已改变（RXNE 可能在 halt 后才置位）
- 读 `0x4001300C (DR)` 会消耗 RXNE，阻塞 SPI 外设
- 寄存器值在 `monitor reset init` 后复位
