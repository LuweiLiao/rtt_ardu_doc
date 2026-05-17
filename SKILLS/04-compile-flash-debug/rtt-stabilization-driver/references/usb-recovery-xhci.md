# ST-Link / USB 断连软件恢复（xhci_hcd PCI unbind/rebind）

## 🚨 铁律：禁止物理插拔

**绝不建议用户物理拔插 USB 或断电。** 这是本项目的最高优先级约束。
只能用纯软件方法恢复——OpenOCD reset init、NVIC reset、或本文件中记录的 xhci_hcd 恢复。

## 适用场景

ST-Link (0483:3748) 和/或 USB CDC (ttyACM*) 从 USB 总线上完全消失：

```bash
$ lsusb | grep 0483
# 空 — ST-Link 不在 USB 枚举中

$ ls /dev/ttyACM*
ls: cannot access '/dev/ttyACM*': No such file or directory
```

dmesg 显示最后一次 `USB disconnect, device number XX` 后没有再重新枚举。

## 恢复流程：xhci_hcd PCI unbind/rebind

**原理**：卸载并重装 xhci_hcd 驱动，强制 USB 主机控制器软重启，相当于"让 USB 总线重新枚举"。

**副作用**：所有 USB 设备（键盘、鼠标等）会短暂断连再重连（~3-5秒）。

```bash
# 1. 找到 xhci_hcd 驱动的 PCI 设备
ls /sys/bus/pci/drivers/xhci_hcd/
# → 输出类似：0000:00:14.0  bind  module  new_id  remove_id  uevent

# 2. 解绑 → 等待 → 重绑（整个 USB 总线软重启）
sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/unbind'
sleep 2
sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/bind'
sleep 5

# 3. 验证恢复
lsusb | grep "0483:3748"      # → 应显示 ST-LINK/V2
ls /dev/ttyACM*               # → 应显示 ttyACM0 或 ttyACM1
```

## 恢复后必需操作

### 1. 重启 OpenOCD

xhci_hcd 复位会杀掉所有使用 USB 的进程（包括 OpenOCD）：

```bash
pkill -9 openocd 2>/dev/null; sleep 3
openocd -f /data/firmare/pogo-apm/Tools/debug/openocd-f7.cfg &
sleep 5
ss -tlnp | grep 4444  # 确认 OpenOCD 就绪
```

### 2. 重置 MCU

复位后 MCU 可能处于不可知状态：

```bash
echo "reset" | nc -q 2 localhost 4444
sleep 3
echo "halt" | nc -q 2 localhost 4444
echo "mdw 0xE000ED28 2" | nc -q 2 localhost 4444  # 检查 CFSR=0, HFSR=0
```

### 3. 解阻塞 kanban 任务

所有因"硬件未连接"而 blocked 的 kanban 任务需要手动解封：

```python
import sqlite3, datetime
conn = sqlite3.connect('/home/llw/.hermes/kanban.db')
c = conn.cursor()
now = int(datetime.datetime.now().timestamp())

# 查所有 blocked 任务
c.execute("SELECT id, title FROM tasks WHERE status='blocked'")
blocked = c.fetchall()

for tid, title in blocked:
    c.execute("UPDATE tasks SET status='ready' WHERE id=? AND status='blocked'", (tid,))
    c.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
              (tid, 'promoted', '{"from":"blocked","reason":"ST-Link recovered via xhci reset"}', now))
    print(f'  ✅ {tid} → ready')

conn.commit()
conn.close()
```

### 4. 确认流水线恢复

```bash
# 检查 dispatcher 是否已派发
python3 -c "
import sqlite3
c = sqlite3.connect('/home/llw/.hermes/kanban.db').cursor()
c.execute(\"SELECT id, status, assignee FROM tasks WHERE status='running' ORDER BY updated_at DESC\")
for r in c.fetchall():
    print(f'{r[1][:8]:8s} {r[0][:20]:22s} {r[2]}')
"
```

## 诊断：USB 断连类型判断

| dmesg 模式 | 诊断 | 恢复方法 |
|-----------|------|---------|
| 单次 disconnect + 不再重连 | 物理断开或 USB 控制器卡死 | xhci_hcd unbind/rebind |
| 多次 disconnect/reconnect 循环后最终消失 | USB 信号不稳定 | xhci_hcd unbind/rebind |
| disconnect 后立刻 reconnect 但新设备号不同 | 正常复位（CDC 重新枚举） | 无需恢复，检查 ttyACM* |
| OpenOCD 报 `couldn't bind tcl` / `adapter drivers cannot be shared` | 多个 OpenOCD 进程争用 ST-Link | `pkill -9 openocd` 后重开 |
| ST-Link 在 lsusb 中但在 OpenOCD 中报 `unable to connect` | MCU 锁定（SWD 被固件禁用） | 走 bootloader 上传法 |

## 各恢复方法比较

| 方法 | 适用范围 | 副作用 | 成功率 |
|------|---------|--------|-------|
| xhci_hcd unbind/rebind | ST-Link 和/或 CDC 完全消失 | 所有 USB 设备短暂掉线 | ⭐⭐⭐ 最高 |
| USBDEVFS_RESET (ioctl) | USB 设备存在但通信卡死（如 OpenOCD 超时） | 仅复位目标设备 | ⭐⭐ |
| authorized toggle | USB 设备在 sysfs 中但 driver 有问题 | 仅复位目标设备 | ⭐⭐ |
| pkill -9 openocd + 重启 | OpenOCD 进程残留 | 无 | ⭐⭐⭐（针对进程残留） |

**优先顺序**：先试 pkill openocd 重连 → 如果 ST-Link 不在 lsusb 中 → xhci_hcd unbind/rebind。

## 参考

- 首次验证日期：2026-05-11（本会话）
- 适用内核：Linux 6.17.0-20-generic
- PCI 设备地址 `0000:00:14.0` 是 Intel 平台的 xhci 主控制器固定地址
