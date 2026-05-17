# USB DWC2 GCCFG 寄存器偏移陷阱（2026-05-14 本会话发现）

## 核心发现

**STM32F7 OTG_FS 的 GCCFG 寄存器在 `USB_OTG_GlobalTypeDef` 结构体中的偏移是 +0x038，而不是参考手册中某些表格暗示的 0xC00！**

## 根因

STM32 标准外设库的 `USB_OTG_GlobalTypeDef` 结构体定义（`stm32f7xx.h`）：

```c
typedef struct {
  __IO uint32_t GOTGCTL;      // 0x000
  __IO uint32_t GOTGINT;      // 0x004
  __IO uint32_t GAHBCFG;      // 0x008
  __IO uint32_t GUSBCFG;      // 0x00C
  __IO uint32_t GRSTCTL;      // 0x010
  __IO uint32_t GINTSTS;      // 0x014
  __IO uint32_t GINTMSK;      // 0x018
  __IO uint32_t GRXSTSR;      // 0x01C
  __IO uint32_t GRXSTSP;      // 0x020
  __IO uint32_t GRXFSIZ;      // 0x024
  __IO uint32_t DIEPTXF0_HNPTXFSIZ; // 0x028
  __IO uint32_t HNPTXSTS;     // 0x02C
  uint32_t RESERVED0[2];      // 0x030-0x034
  __IO uint32_t GCCFG;        // *** 0x038 *** ← 这里！
  __IO uint32_t CID;          // 0x03C
  // ... (后面到 0x800+)
} USB_OTG_GlobalTypeDef;
```

**偏移 0xC00 是 OTG_FS 的全局寄存器区的**设备配置**部分（DIEP/DOEP 等），不是 GCCFG！**

## 错误实践（浪费大量调试时间）

在 OpenOCD 中：

```bash
# ❌ 错误：0xC00 不是 GCCFG！
mdw 0x50000C00 1    # USB_OTG_FS 基址 0x50000000 + 0xC00
# → 读到了 DIEP 或 DOEP 寄存器，不是 GCCFG

# ✅ 正确：偏移 0x038
mdw 0x50000038 1    # USB_OTG_FS 基址 0x50000000 + 0x038
# → 这才是真正的 GCCFG
```

## 验证方法

正确读 GCCFG（CUAV V5 / STM32F767）：

```bash
echo -e "halt
mdw 0x50000038 1    # OTG_FS GCCFG @ +0x038
mdw 0x50000C00 1    # 对比：DIEP/DOEP区域（应看不到 GCCFG 相关位）
resume\nexit" | timeout 10 nc localhost 4444 2>&1 | grep "^0x"
```

## 正确值

| 位域 | 含义 | 正确值 | 
|------|------|--------|
| bit 15 (TXVBUS) | TX 驱动 VBUS | 1 (device mode) |
| bit 13 (VBDEN) | VBUS 检测使能 | 1 |
| bit 21 (NOVBUSSENS) | 无 VBUS 检测模式 | 1 (可选) |
| GCCFG 完整值 | | 0x0000A000 或 0x2000A000 |

## 什么时候 GCCFG=0（空）

**`dwc2_core_init()` 中的 `core_reset` 会清除所有非核心寄存器，包括 GCCFG！**

```c
// usb_dc_dwc2.c 中的 core_reset:
// 1. GRSTCTL = 0x80000000  → core soft reset
// 2. 等待 2-3 次循环 → reset 完成
// 3. GRSTCTL=0  → 此时 GCCFG 已恢复到复位值 0！
```

所以必须在 `dwc2_core_init()` **之后**再写 GCCFG，不能提前。

## 修复代码模式

```c
// 在 cherryusb 的 usb_dc_dwc2.c: dwc2_core_init() 后
// 或 dwc2_otg_core_init() 中 core_reset 完成之后

DWC2_OTG_GlobalTypeDef *dwc2_reg = USB_OTG_FS;
dwc2_reg->GCCFG |= DWC2_GCCFG_VBDEN | DWC2_GCCFG_TXVUSBEN;  // VBUS 检测
// 如果要用 self-powered / 无 VBUS 检测：
dwc2_reg->GCCFG |= USB_OTG_GCCFG_NOVBUSSENS;  // 0x00200000
```

## 诊断步骤：恢复 CDC 的完整检查清单

当 USB CDC 不枚举时，按此顺序检查：

1. **检查硬件：** USB DP/DM 信号？VBUS 5V 存在？
2. **检查 OTG_FS 时钟：** `RCC->AHB1ENR |= RCC_AHB1ENR_OTGFSEN` (bit 25)
3. **检查 GCCFG：** `mdw 0x50000038` → 应为 0xA000 / 0x20A000 / 0x2000A000，非 0
4. **检查核心初始化顺序：** GCCFG 在 `dwc2_core_init()` 后才设置
5. **检查 EPENA 状态：** `mdw OTG_FS_BASE+0x900` (DIEPCTL0) → EPENA=1 正常
6. **检查中断：** `mdw OTG_FS_BASE+0x014` (GINTSTS) → IEPINT/SOF 出现
7. **检查 CherryUSB init：** `INIT_COMPONENT_EXPORT(rt_hw_cherryusb_cdc_init)` 是否被注释
