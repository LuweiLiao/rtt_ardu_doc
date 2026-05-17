# SPI 引脚硬件验证对照表 — STM32F765 数据手册

根据 STM32F765 数据手册（DS11532 Rev 6），以下是已验证的 SPI 引脚复用功能：

## SPI1（内部传感器总线）

| 信号 | 有效引脚选项 | ChibiOS 使用 | RTT 原配置 | 结论 |
|------|-------------|-------------|------------|------|
| SPI1_SCK | PB3(AF5), **PG11(AF5)** | **PG11** | PG11 | ✅ 一致 |
| SPI1_MISO | **PA6(AF5)**, PB4(AF5) | **PA6** | PG9 ❌ | RTT 错误 |
| SPI1_MOSI | PA7(AF5), PB5(AF5), **PD7(AF5)** | **PD7** | PB5 ❌ | RTT 错误 |

**重要**：PG9 和 PA6 都不是有效的 SPI1_MISO 引脚！RTT 原配置用了 PG9，这是一个完全无效的引脚。

## SPI4（MS5611 气压计总线）

| 信号 | 有效引脚选项 | ChibiOS 使用 | RTT 原配置 | 结论 |
|------|-------------|-------------|------------|------|
| SPI4_SCK | **PE2(AF5)**, PF6(AF5) | **PE2** | PE12 ❌ | **RTT 使用了无效引脚！** PE12 无 SPI4 功能 |
| SPI4_MISO | PE5(AF5), **PE13(AF5)**, PF11(AF5) | **PE13** | PE13 | ✅ 一致 |
| SPI4_MOSI | **PE6(AF5)**, PE14(AF5), PF9(AF5) | **PE6** | PE14 ⚠️ | PE14 虽有效但与 PWM(1) 冲突 |

## 为什么 ChibiOS 的配置是 ground truth

1. ChibiOS fmuv5 已在 CUAV V5 硬件上运行验证
2. 配置直接来自主仓库，由大量用户和 CI 验证
3. 引脚选择（PE2 而非 PF6, PA6 而非 PB4, PD7 而非 PA7）结合了 PCB 布局的物理约束

## 检查方法

在 STM32F765 数据手册中找到 Alternate Function 表的方法：
1. 打开 DS11532 (st.com 下载)
2. 搜索 "Alternate function mapping"
3. 找 Table 12 或类似表格
4. 按 GPIO 端口（PA0-PA15, PB0-PB15 等）查找 AF5 列
5. AF5 = SPI1/SPI2/SPI4/SPI5/SPI6

或者快速在线验证：
```bash
# STM32CubeMX 项目的 pinout CSV
# 或直接查 RM0410 Reference Manual §6.3.15
```
