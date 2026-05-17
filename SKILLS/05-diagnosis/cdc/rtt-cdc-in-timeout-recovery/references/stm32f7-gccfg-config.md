# STM32F7 USB OTG FS GCCFG 配置对比

## ChibiOS vs CherryUSB 参数表

| 参数 | ChibiOS (OTGv1) | CherryUSB (usb_glue_st.c) | 说明 |
|------|-----------------|--------------------------|------|
| 文件路径 | `modules/ChibiOS/os/hal/ports/STM32/LLD/OTGv1/hal_usb_lld.c` | `modules/rt-thread/components/drivers/usb/cherryusb/port/dwc2/usb_glue_st.c` | |
| 条件判断 | `#if STM32_OTG_STEPPING == 1/2` | `#if __has_include("stm32f7xx_hal.h")` | |
| GCCFG stepping 1 | `GCCFG_NOVBUSSENS \| GCCFG_VBUSASEN \| GCCFG_VBUSBSEN \| GCCFG_PWRDWN` | N/A (F7 用 stepping 2) | = 0x210000 + VBUSASEN + VBUSBSEN |
| GCCFG stepping 2 | `GCCFG_PWRDWN` (仅 0x10000) | `(1 << 16)` = 0x10000 | **STM32F7: 仅 PWRDWN!** |
| NOVBUSSENS | stepping 1 用 bit 20 | F7 不使用 | stepping 2 不需要 |
| B-session valid | 在 `usb_lld_start` 中显式设置 `GOTGCTL = BVALOEN \| BVALOVAL` (L837) | 通过 `b_session_valid_override = true` 条件设置 (L652-656) | 两者都在 F7 上有效 |
| GCCFG 写入时机 | 在 `otg_core_reset()` 之前 (L842 → L856) | 在 `dwc2_core_init()` 之后 (L634) | F7 上 CSRST 不重置 GCCFG，顺序不重要 |

## 关键发现

### STM32F7 不需要 NOVBUSSENS

CherryUSB 的 `param_pa11_pa12` 在 STM32F7 上只设 PWRDWN(0x10000)：
```c
// modules/rt-thread/.../usb_glue_st.c L211-238
.device_gccfg = (1 << 16), // fs: USB_OTG_GCCFG_PWRDWN
.b_session_valid_override = true,  // 替代 NOVBUSSENS
```

ChibiOS stepping 2 也只用 PWRDWN：
```c
// modules/ChibiOS/.../hal_usb_lld.c L51-55
#define GCCFG_INIT_VALUE  GCCFG_PWRDWN  // stepping 2
```

### B-session valid override 是替代方案

对于缺少 VBUS 检测引脚的设计（如 CUAV V5 的 OTG_FS 端口），硬件无法通过 VBUS 引脚自动检测 USB 连接。NOVBUSSENS(stepping 1) 或 BVALOEN|BVALOVAL(stepping 2) 都能绕过该检测。

### 寄存器偏移确认

STM32F7 OTG_FS 寄存器基址：`0x50000000`
- GCCFG 偏移：`0x038`（在 `USB_OTG_GlobalTypeDef` 结构中）
  - 不是 `0xC00`（PCGCCTL 地址）！2026-05-14 之前因误读 0xC00 导致误解
- DCTL 偏移：`0x804`
- DIEPCTL1 偏移：`0x920`
- DIEPMSK 偏移：`0x910`
- DIEPTSIZ1 偏移：`0x93C`
- DIEPINT1 偏移：`0x928`
- GINTMSK 偏移：`0x018`

### GCCFG 位定义 (STM32F7)

| 位 | 名称 | 说明 | 典型值 |
|----|------|------|--------|
| 16 | PWRDWN | 电源关闭 | 1 = 开启 PHY |
| 17 | VBUSASEN | VBUS A-sensing 使能 | stepping 1 专用 |
| 18 | VBUSBSEN | VBUS B-sensing 使能 | stepping 1 专用 |
| 19 | SOFOUTEN | SOF 输出使能 | 调试用 |
| 20 | NOVBUSSENS | 无 VBUS 感知 | **stepping 1 专用，F7 不支持** |
| 21 | — | stepping 2 VBUS 感知辅助 | 通常不设 |
| 22 | — | 保留 | — |

## 诊断命令

```bash
# 读 GCCFG
printf "halt\nmdw 0x50000038 1\n" | nc -q 3 127.0.0.1 4444 2>&1 | strings

# 预期值：
# STM32F7 (CherryUSB): 0x0001FFF0 (PWRDWN + 其他保留位)
# STM32F7 (ChibiOS):   0x00010000 (干净 PWRDWN)
```

## 历史

- 2026-05-14: 误读 PCGCCTL(0x50000C00) 为 GCCFG，错报为 0x00000000
- 2026-05-14: 修正为 GCCFG@0x50000038=0x1FFF0，确认 F7 上 NOVBUSSENS 不需要
- 2026-05-14: 修改 CherryUSB usb_dc_dwc2.c 将 GCCFG 写入从 dwc2_core_init() 前移到后
- 2026-05-15: 确认 STM32F7 GCCFG 正确值就是 PWRDWN(0x10000)，加 b_session_valid_override
