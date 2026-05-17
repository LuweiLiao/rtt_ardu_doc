# USB OTG_FS 寄存器诊断 — STM32F7/CUAV V5

## 当 USB CDC 不枚举时

### 诊断流

```bash
# 1. 检查 USB 核心状态寄存器
(echo "halt"; sleep 1;
 echo "mdw 0x50000000 8";      # GOTGCTL, GOTGINT, GAHBCFG, GUSBCFG, GRSTCTL, GIINTMSK, GIINTSTS, GRXSTSR
 sleep 0.5;
 echo "mdw 0x50000800 2";      # DCFG, DCTL
 sleep 0.5;
 echo "mdw 0x50001c00 1";      # GCCFG (PHY power down, VBUS sensing)
 sleep 0.5;
 echo "mdw 0x50000844 1";      # DSTS (Device Status — enumeration speed)
 sleep 0.5;
 echo "resume") | nc -q 4 localhost 4444 2>&1 | grep -E "0x5000" | head -10
```

### 关键寄存器

#### DSTS (0x50000844) — Device Status
- bits 1:0 (EnumSpd):
  - `00` = **未枚举**（或高速模式，但 FS 核不支持）
  - `01` = **Full Speed (12Mbps)**
  - `10` = **Low Speed (1.5Mbps)**
- **DSTS=0x00000000** = 设备未被主机枚举 → 检查物理层和初始化顺序

#### DCFG (0x50000800) — Device Configuration
- 包含设备地址、帧间隔、EP 不匹配计数
- 正常值示例：`0x08200163`（地址 0x163=355，但通常应在 0-127 范围）

#### DCTL (0x50000804) — Device Control
- bit 1 (SDIS): **Soft Disconnect**
  - `0` = 正常连接
  - `1` = 软断开（主机检测不到设备）
- bit 0 (RWUSIG): Remote Wakeup

#### GCCFG (0x50001C00) — Global Core Configuration
- bit 16 (PWRDWN): PHY 电源关闭
  - `0` = PHY 正常工作 ✅
  - `1` = PHY 断电 ❌
- bit 23 (NOVBUSSENS): VBUS 感应
  - `0` = 使用 Vbus_VALID（需外部 VBUS 检测）
  - `1` = 忽略 VBUS（自供电设备常用）
- bit 22 (SOFOUTEN): SOF 输出使能
- bit 24 (DECEVT): 设备事件

#### GIINTSTS (0x50000018) — Global Interrupt Status
- bit 31 (WKUPINT): 唤醒中断
- bit 21 (WkUp): 唤醒
- bit 12 (USBRST): USB 复位已检测
- bit 11 (ENUMDNE): 枚举完成
- bit 3 (EOPF): 周期性帧结束

**诊断要点**：如果 USBRST + ENUMDNE 中断已置位但 DSTS 显示枚举速度=00，说明硬件收到了复位和枚举请求但软件未正确配置端点 → 中断处理程序可能未运行。

### 典型案例：PVU/RVU 超时循环导致 USB 不枚举（2026-05-10）

**现象**：烧录后 MCU 正常运行（SysTick 正常、Thread 模式、CFSR=0），但 DSTS=0（未枚举）。主机看到 /dev/ttyACM1 时间戳**不更新**。

**根因**：`rt_board_init.c` 中 IWDG 早期代码的错误 PVU/RVU 等待（并行等待 PVU|RVU）消耗了数毫秒循环。此时代码在 `rt_hw_board_init()` 最开头执行：

```
SCB->VTOR = 0x08008000;
// → IWDG 代码：PR写→RLR写→PVU|RVU等待→喂狗
//    PVU|RVU 并行等待循环在 I-Cache/预取未使能时消耗数毫秒
//    RLR 被忽略（PVU 未清时写入）→ IWDG 仍用 0.5s 超时
//    高延迟内存访问可能影响 RCC 寄存器状态
// → USB 初始化 → 因早期 RCC/外设状态不一致 → DSTS=0
_mpu_config();
_fpu_context_init();
SCB_EnableICache();
// ...
```

**对照实验验证**：
1. 32KB 栈 + 原始代码（无 IWDG 修改）→ USB 枚举 ✅
2. 64KB 栈 + 原始代码 → USB 枚举 ✅（排除栈大小）
3. 64KB 栈 + 正确 PVU→RLU→RVU 时序（本会话修复）→ 待验证

### 寄存器重映射

CUAV V5 使用 STM32F765，USB OTG 接口：

| 外设 | 基地址 | 时钟使能 | 引脚 |
|------|--------|----------|------|
| OTG_FS (用于 CDC) | `0x50000000` | RCC_AHB2ENR OTGFSEN=1 | PA11(DP), PA12(DM) |
| OTG_HS | `0x40040000` | RCC_AHB2ENR OTGHSEN=1 | 未连接 PHY |

### 验证方法

```bash
# 1. 检查 CDC 设备是否创建
ls -la /dev/ttyACM*
# 若 ttyACM1 时间戳不更新 → USB 未枚举

# 2. 检查设备模式
echo "mdw 0x50000000 1" | nc -q 1 localhost 4444
# GOTGCTL bit 0 (BSESSION) = 1 → 设备模式（非主机）

# 3. 检查 VBUS 感知
echo "mdw 0x50001c00 1" | nc -q 1 localhost 4444
# bit 23 (NOVBUSSENS) = 1 → 忽略 VBUS（需要确认供电正常）

# 4. 检查 PHY 电源
# bit 16 (PWRDWN) = 1 → PHY 断电 → 必须为 0

# 5. 复位 USB 核心（极端情况下）
echo "mww 0x50000010 0x00000001" | nc -q 1 localhost 4444  # GRSTCTL CSGRST=1
sleep 2
echo "mww 0x50000010 0x00000000" | nc -q 1 localhost 4444  # CSGRST=0
```
