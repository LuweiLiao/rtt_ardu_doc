# CAN Bus Driver — bxCAN 实现方案

来源：C3-Research (2026-05-16)
ChibiOS 参考：bxcan.hpp(301L), CANIface.h(251L), CanIface.cpp(1096L)

## 架构发现

CUAV V5 使用 bxCAN（非 CAN FD）：
- CAN1: PI9 RX, PH13 TX, PH2 silent
- CAN2: PB12 RX, PB13 TX, PH3 silent
- 收发器: TJA1051T/3 (3.3V)
- PCLK1=54MHz（216MHz HCLK / 4 APB1）

## 1Mbps 位定时

| 参数 | 值 | 说明 |
|------|-----|------|
| BRP | 5 | tCAN = 2 x tPCLK1 x (BRP+1) = 2/54MHz x 6 = 222ns |
| BS1 | 6 | 6 x tCAN = 6.25 x 222ns = 1.39us |
| BS2 | 0 | 1 x tCAN = 222ns |
| SJW | 0 | 重新同步跳转宽度 |
| 采样点 | 88.9% | (1+BS1) / (1+BS1+BS2) = 7/8 |

## 实施策略：Option B（直接寄存器访问，ChibiOS 风格）

RTT CAN 设备框架（dev_can.h/drv_can.c）与 AP_HAL::CANIface API 语义不匹配，
推荐直接寄存器访问（与 RCOutput/ADC/I2C 风格一致）。

### 文件结构（~1700 行，高复杂度）

```
CANDriver.h     (~300L)  — CANIface 类、filter 配置、msg 队列
CANDriver.cpp   (~1100L) — bxCAN 初始化/收发/ISR/总线恢复
CANManager.h    (~200L)  — 单例管理器（CAN1+CAN2）
CANManager.cpp  (~100L)  — 编排
```

### 关键寄存器

| 寄存器 | 作用 | 配置 |
|--------|------|------|
| CAN_MCR | 主控制 | INRQ=1(初始化), NART=0, T1EN=0 |
| CAN_BTR | 位定时 | BRP=5, TS1=6, TS2=0, SJW=0 |
| CAN_TIxR | 发送ID | STID |
| CAN_TDTxR | 发送长度 | DLC |
| CAN_TDLxR | 发送数据 | 低32位数据 |

### 风险

1. 位定时不匹配 → 使用 ChibiOS 已验证算法
2. NVIC 优先级冲突 → 通过 HAL 配置
3. BSP CAN 未使能 → 纯绿色字段实现
