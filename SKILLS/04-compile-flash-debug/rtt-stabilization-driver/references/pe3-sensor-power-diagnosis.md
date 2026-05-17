# PE3 (VDD_3V3_SENSORS_EN) 传感器电源诊断

**发现日期**: 2026-05-12
**芯片**: CUAV V5 (STM32F767)
**症状**: setup_stage 卡在 620, 传感器全部无响应

## 症状模式

| 现象 | 说明 |
|------|------|
| setup_stage | 620（`init_rc_in()`后, `ins.init()`前） |
| GPIOE MODER | 0x00000000 → PE3 bits[7:6]=00 (INPUT!) |
| GPIOE ODR | 0x00000000 → PE3 bit 3 = LOW |
| USB CDC | 可能有输出("IOMCU startup"等) 但停滞 |
| 无 HardFault | CFSR=0, HFSR=0 |

## 快速诊断（1 分钟）

```bash
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0x40001800 1"     # GPIOE MODER
echo "mdw 0x40001814 1"     # GPIOE ODR
echo "resume" | nc -q 1 localhost 4444
```

**期望**: MODER bits[7:6]=01 (output), ODR bit 3=1 (HIGH)
**实际（故障时）**: MODER bits[7:6]=00 (input), ODR bit 3=0

## 根因分析

### 电源引脚布线

```
PE3 → VDD_3V3_SENSORS_EN → 3.3V 传感器供电轨
                              ├── ICM20689 (SPI1)
                              ├── ICM20602 (SPI1)
                              ├── MS5611 (SPI4)
                              ├── IST8310 (I2C3)
                              └── 其他传感器
```

PE3 必须为 OUTPUT HIGH 才能给所有传感器供电。

### 为什么 `_sensor_power_init()` 不工作

初始化链：

```
rt_hw_board_init()
  ├── ... 
  ├── rt_components_board_init()   ← 运行所有 INIT_*_EXPORT
  │   ├── INIT_BOARD_EXPORT        ← SPI4 HAL_GPIO_Init 在此运行
  │   │   └── HAL_SPI_MspInit()    ← 对 GPIOE 做 read-modify-write
  │   │                               → PE3 被清零为 INPUT
  │   └── INIT_PREV_EXPORT         ← _sensor_power_init() 在此运行
  │       └── rt_pin_mode()        ← 试图写 PE3=OUTPUT
  │            + rt_pin_write()    ← D-Cache 可能缓存此写入
  │                                   → GPIOE MODER 物理寄存器未更新
  └── rtt_run_cpp_ctors()          ← C++ 构造器（最后一步）
```

**根因**：D-Cache 干扰了 `rt_pin_mode()` 对 GPIOE MODER 的 read-modify-write。虽然 0x40000000 外设区域理论上应为 Device(non-cacheable)，但 MPU 配置不完善时，D-Cache 仍可介入。

### 修复方案

在 `rtt_run_cpp_ctors()` 末尾添加直接寄存器写：

```c
/* Force PE3 (VDD_3V3_SENSORS_EN) to OUTPUT HIGH — D-Cache-safe direct write */
#define GPIOE_MODER  (*(volatile uint32_t *)(0x40021000UL + 0x00))
#define GPIOE_BSRR   (*(volatile uint32_t *)(0x40021000UL + 0x18))

GPIOE_MODER |= (1UL << 6);     // bits[7:6] = 01 (output)
__DSB();
GPIOE_BSRR = (1UL << 3);       // BS3 = set bit 3 HIGH
__DSB();
```

**为什么在 `rtt_run_cpp_ctors()` 末尾**：
- 这是 `rt_hw_board_init()` 的最后一步
- 所有 SPI/GPIO 初始化已完成
- 再晚的步骤（如主线程启动后）可能被其他线程抢占

## 验证修复

```bash
# 烧录后 halt + 检查
echo "halt" | nc -q 1 localhost 4444
echo "mdw 0x40001800 1"     # 期望: bits[7:6]=01
echo "mdw 0x40001814 1"     # 期望: bit 3=1
echo "resume" | nc -q 1 localhost 4444

# 等待系统初始化后检查 setup_stage
sleep 30
echo "halt" | nc -q 1 localhost 4444
echo "mdw <setup_stage_addr> 1" | nc -q 1 localhost 4444
# 期望: > 620 (如 630+, 表示已越过传感器电源依赖阶段)
echo "resume" | nc -q 1 localhost 4444
```

## 相关参考

- `ardupilot-rtt-architecture` skill §5 "D-Cache 与 GPIO MODER/ODR 写入冲突"
- `ardupilot-rtt-architecture/references/stm32f7-gpio-dcache-interaction.md`
- `rtt-stabilization-driver` skill "第十一步：Stage 610-630 早期挂起 — 传感器电源"
