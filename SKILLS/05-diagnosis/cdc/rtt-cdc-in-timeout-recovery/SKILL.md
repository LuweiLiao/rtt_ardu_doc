---
name: rtt-cdc-in-timeout-recovery
description: >
  CherryUSB CDC ACM IN 传输超时自愈方案。ChibiOS 的 SOF 处理
  器(sduSOFHookI)会被周期性调用来自动恢复 CDC 数据流，而 CherryUSB
  的 tx_active 互斥锁在 IN 传输超时(TOC)后永久死锁。本方案在 DWC2 驱
  动层使能 TOC 中断并在触发时恢复端点状态。
domain:
  - embedded
  - ardupilot
  - rtt
trigger: >
  当 USB CDC IN 端点启动后主机从未发送 IN 令牌（DTR 未设置），
  导致 XFRC=0 且 tx_active 永远 1。
---

# CherryUSB CDC IN 传输 TOC 自愈方案

## 根因分析

CherryUSB 的 `usbd_serial_kick_tx()` 使用 `tx_active` 互斥锁：
1. `kick_tx` 设置 `tx_active=1` → 调用 `usbd_ep_start_write()` → IN 端点启用
2. DWC2 等待主机 IN 令牌 → **若主机从未打开端口(DTR=0)永不发 IN 令牌**
3. DWC2 在 `GUSBCFG_TOCAL` 超时后置 DIEPINT_TOC(bit 3)
4. **但 CherryUSB 屏蔽了 TOC 中断**（DIEPMSK 只设了 XFRCM），所以无人处理
5. `tx_active` 永远为 1，后续 kick_tx 全部跳过
6. 4096 字节的 tx_rb 环缓冲区填满后所有写入失败

## ChibiOS 参考

ChibiOS 的 `sduSOFHookI()` 在每帧 SOF 中断中调用，等价于周期性 watchdog：
- 检查 IN 端点是否空闲且有数据待发 → 启动传输
- 不依赖 tx_active 锁，数据流由 SOF 和 XFRC 事件共同驱动

> 行号参考：`modules/ChibiOS/os/hal/ports/STM32/LLD/OTGv1/hal_usb_lld.c`
> L874: `GINTMSK |= GINTMSK_SOFM`（SOF 使能）
> `usbcfg.c` L393: `sof_handler` → `sduSOFHookI`

## 改动方案

### 改动 1: usb_dc_dwc2.c — USB Reset 时 DIEPMSK 增加 TOM

**文件**: `modules/rt-thread/components/drivers/usb/cherryusb/port/dwc2/usb_dc_dwc2.c`
**当前 L1364**:
```c
USB_OTG_DEV->DIEPMSK = USB_OTG_DIEPMSK_XFRCM;
```
**修改后**:
```c
USB_OTG_DEV->DIEPMSK = USB_OTG_DIEPMSK_XFRCM |
                        USB_OTG_DIEPMSK_TOM;   /* 使能 TOC 中断 */
```

### 改动 2: usb_dc_dwc2.c — IN 中断处理增加 TOC 分支

在现有 XFRC 分支之后、TXFE 分支之前（约 L1319），增加：

```c
// NEW: Timeout Condition recovery — ChibiOS-equivalent SOF watchdog
if ((epint & USB_OTG_DIEPINT_TOC) == USB_OTG_DIEPINT_TOC) {
    if (ep_idx > 0) {
        /* Abort the stuck IN transfer: disable endpoint + flush FIFO */
        USB_OTG_INEP(ep_idx)->DIEPCTL |= (USB_OTG_DIEPCTL_SNAK |
                                           USB_OTG_DIEPCTL_EPDIS);
        dwc2_flush_txfifo(busid, ep_idx);

        /* Clear transfer state so completion handler doesn't panic */
        g_dwc2_udc[busid].in_ep[ep_idx].actual_xfer_len = 0;
        g_dwc2_udc[busid].in_ep[ep_idx].xfer_len = 0;

        /* Fake IN completion with 0 bytes to trigger CDC retry.
         * This chains into usbd_cdc_acm_bulk_in → tx_active=0 → kick_tx.
         * ChibiOS' sduSOFHookI (called from SOF IRQ) does the same:
         * checks if previous transfer finished and retries. */
        usbd_event_ep_in_complete_handler(busid, ep_idx | 0x80, 0);
    }
}
```

### 改动 3: usbd_serial.c — 零字节完成时不重置 ring buffer

**无代码改动**。现有 `usbd_cdc_acm_bulk_in` 中 `tx_active=0` → `kick_tx` 逻辑已经兼容 0 字节完成。

## 验证方法

1. 编译 `scons --v=ArduCopter --target=cuav_v5 -j$(nproc)`
2. 烧录后用 OpenOCD 确认 `mdw 0x50000910` 返回值的 bit 3=1（DIEPMSK 含 TOM）
3. 运行固件后不连接任何 MAVLink 客户端，等 10s 后：
   - 观察 `dbg_serial_bulkin_cnt` 是否周期性增长
   - 观察 `dbg_iepint_calls` 是否包含 TOC 中断
4. 运行 `mavproxy.py --master /dev/ttyACM0` 确认心跳正常

## 回退方法

撤销 `usb_dc_dwc2.c` 两处改动后重新编译烧录。
