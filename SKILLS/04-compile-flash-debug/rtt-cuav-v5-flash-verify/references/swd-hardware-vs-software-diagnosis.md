# SWD 连接失败：硬件 vs 软件诊断

当 ST-LINK 能检测到目标电压 (~3.24V) 但 `init mode failed (unable to connect to the target)` 时，先判断是硬件问题还是软件问题。

## 诊断流程图

```
[ST-LINK 报 "init mode failed"]
    │
    ├── 检查 ST-LINK USB 存在: lsusb | grep 0483:3748
    │   ├── ❌ 不存在 → USB 总线问题
    │   │   ├── 1. USBDEVFS_RESET (ioctl, 不须 sudo)
    │   │   ├── 2. sudo authorized toggle
    │   │   └── 3. xhci_hcd PCI unbind/bind (最后手段)
    │   │
    │   └── ✅ 存在 → 进入 SWD 诊断
    │
    ├── 尝试最低速连接: adapter speed 5
    │   ├── ✅ 连接成功 → 之前速度过高
    │   └── ❌ 仍失败 → 深入诊断
    │
    ├── 尝试不带 SRST 配置
    │   ├── ✅ 连接成功 → SRST 线未连接, 移除 reset_config
    │   └── ❌ 仍失败 → 深入诊断
    │
    ├── power cycle ST-LINK USB (见下方恢复方法)
    │   ├── ✅ 连接成功 → ST-LINK 内部状态卡死
    │   └── ❌ 仍失败 → 深入诊断
    │
    └── 关键判断: 目标 MCU 串行输出?
        ├── ✅ MAVLink/USB CDC 有输出
        │   └── ⚠️ 高概率 = SWD 排线物理松脱
        │       目标在正常运行 (CDC枚举+MAVLink心跳)
        │       但 ST-LINK 无法建立 SWD 协议层通信
        │       → 需要物理检查 SWD 排线
        │
        └── ❌ CDC 无输出, MCU 无声
            └── 可能原因:
                1. MCU 在 STOP/STANDBY (DBGMCU 未使能 debug)
                2. GPIO PA13/PA14 被配置为 OUTPUT 驱动冲突
                3. flash 损坏 (之前中断写入)
                4. option bytes 设置了 RDP Level 1
                → 按 rtt-cuav-v5-flash-verify 软件恢复流程
```

## 关键判别信号

### 软件可恢复的 SWD 锁死
- MCU 无 CDC/MAVLink 输出 (USB 设备甚至不枚举)
- 或 flash write 过程中 `kill -9` 了 OpenOCD
- 或 MCU 进入了低功耗模式且 DBGMCU 未使能
- **恢复**: bootloader 上传法 / DFU / mass erase

### 硬件级 SWD 断开
- MCU 正常运行 (CDC 枚举 + MAVLink 心跳)
- ST-LINK 检测到目标电压 (~3.24V)
- 所有频率 (5~200kHz) 均连接失败
- 各种 USB 复位手段全部无效
- **恢复**: 仅需重新插紧 SWD 排线, 无需任何软件操作

## ST-LINK USB 重置方法

### 1. USBDEVFS_RESET (ioctl, 不须 sudo)
```python
import fcntl, os
USBDEVFS_RESET = 0x5514
for root, dirs, files in os.walk('/dev/bus/usb'):
    for f in files:
        path = os.path.join(root, f)
        try:
            fd = os.open(path, os.O_RDWR)
            buf = os.read(fd, 18)
            idVendor = buf[8] | (buf[9] << 8)
            idProduct = buf[10] | (buf[11] << 8)
            if idVendor == 0x0483 and idProduct == 0x3748:
                fcntl.ioctl(fd, USBDEVFS_RESET)
                print(f'Reset ST-Link at {path}')
            os.close(fd)
        except: pass
```

### 2. USB authorized toggle (须 sudo, 但设备路径已知)
```bash
# 先查路径
for dev in /sys/bus/usb/devices/*; do
    vid=$(cat $dev/idVendor 2>/dev/null)
    pid=$(cat $dev/idProduct 2>/dev/null)
    [ "$vid" = "0483" ] && [ "$pid" = "3748" ] && echo "路径: $(basename $dev)"
done
# 切换
echo "0" | sudo tee /sys/bus/usb/devices/1-2.1/authorized  # 卸载
sleep 3
echo "1" | sudo tee /sys/bus/usb/devices/1-2.1/authorized  # 重新枚举
```

### 3. xhci_hcd PCI unbind/bind (最后手段, 须 sudo, 影响全部 USB)
```bash
sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/unbind'
sleep 2
sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/bind'
sleep 5
# 会杀掉所有 USB 进程 (含 OpenOCD), 恢复后需:
pkill -9 openocd 2>/dev/null; sleep 3
openocd -f /data/firmare/pogo-apm/Tools/debug/openocd-f7.cfg &
sleep 5
ss -tlnp | grep 4444  # 确认 OpenOCD 就绪
```

### 4. USB 设备 remove + PCI rescan (可彻底移除设备, 但 rescan 不一定恢复)
```bash
echo "1" | sudo tee /sys/bus/usb/devices/1-2.1/remove  # 从 USB 树移除
sleep 2
lsusb | grep 0483  # 确认已消失
sudo sh -c 'echo "1" > /sys/bus/pci/rescan'  # 尝试重新发现 (可能无效!)
sleep 5
lsusb | grep 0483  # 应重新出现
```

⚠️ **2026-05-16 实测**: `remove` + `pci rescan` 在 x86_64 USB 控制器上**不总能恢复**被软件移除的设备。PCI rescan 触发了总线扫描但 USB hub 未重新探测。可靠方案是 **USB hub authorized 切换** (方法 2)。

### 5. USB root hub authorized toggle (最后手段 — 2026-05-16 验证最可靠)
当以上方法均无效时 (特别是 `remove` 后 PCI rescan 不恢复), 切换整个 USB root hub 的 authorized 状态:
```bash
# 先找到 USB root hub
ls -la /sys/bus/usb/devices/usb1/  # 查看 1-0:1.0

# 切换 authorized 状态 — 这会强制 USB 总线复位枚举所有设备
echo "0" | sudo tee /sys/bus/usb/devices/usb1/authorized  # 停用 root hub
sleep 2
echo "1" | sudo tee /sys/bus/usb/devices/usb1/authorized  # 重新启用, 全部重新枚举
sleep 5
lsusb | grep 0483  # 应重新出现
```

**注意**: 这会断开**所有** USB 设备 (键盘/鼠标/UVC摄像头等) 然后重新连接, 但不会影响已挂载的文件系统。

## 纯软件 MAVLink 重启 (需 MCU 仍在运行)

当 MCU 仍在发送 MAVLink 数据但 SWD 无法连接, 可以:
- 用 pymavlink 发送 `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN`
- param1=1.0 (reboot), param7=1.713 (magic number)
- 重启后也许 SWD 恢复 (但如果是排线松动则无效)
