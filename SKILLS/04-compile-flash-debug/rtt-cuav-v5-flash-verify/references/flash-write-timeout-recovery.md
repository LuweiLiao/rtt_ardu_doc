# Flash Write Algorithm Timeout & Recovery

## 症状

OpenOCD 报:
```
Error: timeout waiting for algorithm, a target reset is recommended
Error: error executing stm32x flash write algorithm
Error: flash write failed = 0x00000040
Error: error writing to flash at address 0x08000000 at offset 0x00008000
auto erase enabled
```

但有时即使没报错，PC=0xFFFFFFFE 也说明写入不完整。

## 根因

STM32F7 flash 编程算法在以下情况超时:
1. Flash 未完全擦除（`auto erase enabled` 自动擦除可能不够干净）
2. Flash option bytes 处于锁定状态
3. MCU 未被正确 halt（最常见！）
4. ST-Link 时钟不稳定（V2 + 长线缆）

## 恢复步骤

### Step 1: 确保 halt
```bash
echo "reset halt" | nc -q2 localhost 4444
```
确认输出: `halted due to debug-request`, 不要是 `halted due to breakpoint`

### Step 2: 完全擦除
```bash
echo "reset halt" | nc -q2 localhost 4444
echo "flash erase_sector 0 0 last" | nc -q15 localhost 4444
```
等待到 `erased sectors 0 through 11 on flash bank 0 in 18.xxx s`

### Step 3: 写入（GDB法，更可靠）
```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor reset halt" \
  -ex "set {int}0x40023C00 = 0x01" \
  -ex "monitor flash write_image /path/to/rtthread.bin 0x08000000" \
  -ex "monitor reset run" \
  -ex "quit"
```

`0x40023C00` = FLASH_OPTKEYR (解锁option byte), 写入0x01解锁.

### Step 4: 验证
```bash
echo "reset halt" | nc -q2 localhost 4444
```
- PC=0x08000200 → bootloader正常启动 ✅
- PC=0x0800xxxx → 在固件代码中 ✅
- PC=0xFFFFFFFE → flash仍空白 ❌

### Step 5: 启动并检查USB枚举
```bash
echo "reset run" | nc -q1 localhost 4444
sleep 5
lsusb | grep "1209:5741"
```
应显示 `Generic CUAVv5 RTT`

## 中断flash写入的灾难性后果

如果在 `flash write_image` 或 `load` 执行期间用 `kill -9` 中断 OpenOCD:

1. **flash损坏** — 当前写入的扇区数据不完整（partial erase/program）
2. **MCU从损坏flash启动** — 可能执行了禁用SWD的option bytes或进入低功耗模式
3. **SWD锁定** — OpenOCD报 `unable to connect to the target`，ST-Link能检测到电压但无法通信
4. **软件无法恢复** — ioctl USBDEVFS_RESET、pyOCD、st-flash全部失败
5. **唯一方案**: 按板子上的**物理复位键**（或拔插USB）

### 典型错误日志（中断后）
```
Error: init mode failed (unable to connect to the target)
Info : Target voltage: 3.249501
```
电压正常但无SWD连接。pyOCD报同样错误。

### MCU锁死后PC值含义
| halt后PC | 含义 |
|----------|------|
| 0xFFFFFFFE | flash空白（最可能） |
| 0x08000200 | bootloader区域完好但未跳转 |
| SWD不通 | option bytes锁定或低功耗模式 |

## 正确清理OpenOCD

```bash
# ✅ 正确
kill -9 $(pgrep -f openocd) 2>/dev/null; sleep 2

# ❌ 错误 — 会杀死当前terminal
kill -9 $(pgrep -f openocd) 2>/dev/null  # 先执行
fuser -k 6666/tcp 3333/tcp 4444/tcp      # 然后fuser — 此时safe
# 问题: 如果把两条写在同一行: kill+wait+fuser = 危险！
```

## 关键教训

1. **永远不要在flash写入中中断OpenOCD** — 宁可等30秒等写入完成
2. **烧录中途失败 = 物理复位** — 不要尝试10次同样的软件恢复方法
3. **向用户诚实汇报** — 如果不小心搞砸了，直接说明原因和需要用户帮忙的操作（按复位键）
4. **恢复后马上验证** — 烧录正确固件 → `reset run` → 等USB枚举 → 检查MAVLink
