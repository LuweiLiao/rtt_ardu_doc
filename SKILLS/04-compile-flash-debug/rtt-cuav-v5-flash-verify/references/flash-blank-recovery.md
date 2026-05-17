# Flash Blank / Bootloader Missing Recovery (2026-05-08)

## 场景

OpenOCD `flash erase_sector 0 0 last` 擦除了整个2MB flash（含bootloader区），但后续只将 `rtthread.bin` 写到了 `0x08008000`（而非正确的 `0x08000000`）。结果：

- 复位后 MCU 从 `0x00000000`（alias到 `0x08000000`）加载初始SP → 读出0xFFFFFFFF
- MSP = 0xFFFFFFFC, PC = 0xFFFFFFFE
- MCU跳转到一个非法地址 → 彻底卡死
- OpenOCD 仍能连接 SWD 但所有上下文都无效

## 诊断

```
reset halt 后:
  PC = 0xFFFFFFFE
  SP = 0xFFFFFFFC
  xPSR = 0x01000000
```

✅ 这是 flash 空白的**确定性特征**，不是代码bug也不是硬件故障。

## 恢复步骤

### 方法一：完整擦除+写全量BIN到0x08000000（推荐）

```bash
# 1. 启动OpenOCD（后台）
cd /data/firmare/pogo-apm && openocd -f Tools/debug/openocd-f7.cfg &

# 2. 完整擦除
echo "reset halt" | nc -q2 localhost 4444
echo "flash erase_sector 0 0 last" | nc -q20 localhost 4444

# 3. 写全量BIN到0x08000000
echo "flash write_image build/rtt_cuav_v5/rtthread.bin 0x08000000" | nc -q30 localhost 4444

# 4. 复位运行
echo "reset run" | nc -q1 localhost 4444
```

### 方法二：手动PC跳转（应急，仅验证固件是否可用）

当bootloader区被擦除但固件已正确写入（方法一的第三步已完成），可用此方法验证固件：

```bash
# 读向量表
python3 -c "
import struct
with open('build/rtt_cuav_v5/rtthread.bin', 'rb') as f:
    data = f.read()
print(f'MSP=0x{struct.unpack(\"<I\", data[0:4])[0]:08X}')
print(f'Reset=0x{struct.unpack(\"<I\", data[4:8])[0]:08X}')
'

# 手动跳转
echo "reset halt" | nc -q2 localhost 4444
echo "reg pc 0x<ResetHandler>" | nc -q1 localhost 4444
echo "reg sp 0x<InitialMSP>" | nc -q1 localhost 4444
echo "resume" | nc -q1 localhost 4444
```

验证：`lsusb | grep "1209:5741"` 应显示 "Generic CUAVv5 RTT"

## 为什么PC=0xFFFFFFFE？

STM32F7复位流程：
1. 从地址 `0x00000000` 读取初始MSP
2. 从地址 `0x00000004` 读取复位向量
3. 跳转到复位向量

当flash被擦除后，`0x00000000`（alias到 `0x08000000`）读出0xFFFFFFFF：
- MSP = 0xFFFFFFFC（0xFFFFFFFF - 3 = 四字节对齐）
- PC = 0xFFFFFFFE（0xFFFFFFFF - 1 = 半字对齐且LSB=1表示Thumb状态）
- 跳转到0xFFFFFFFE → 无意义代码 → 卡死

## 预防

- **永远不要**在 `flash write_image` 执行中 `kill -9` OpenOCD
- 擦除前确认要写什么地址
- RTT构建的 `rtthread.bin` 是**单一镜像从0x08000000开始**，不是split布局
- 验证：写入后用 `echo "mdw 0x08000000 4" | nc -q1 localhost 4444` 确认向量表存在
