# Bootloader HardFault 恢复 — 看门狗复位后

## 故障模式

**现象**：MCU 卡在 bootloader 内部 HardFault handler，PC=0x08000xxx，CDC 不枚举（只有 DFU ttyACM0）

**触发条件**：SPI 轮询超时 → 总线线程阻塞 → 主调度器饿死 → IWDG 超时复位 → bootloader 启动失败

## 诊断流程

### 1. 确认状态
```bash
echo "halt" | nc -w 2 localhost 4444
# 期望输出类似:
# [stm32f7x.cpu] halted due to debug-request, current mode: Handler HardFault
# xPSR: 0x60000003 pc: 0x08000fb6 msp: 0x20020380
```

**关键特征**：
- PC 在 `0x08000000-0x08007FFF` 范围（bootloader 空间）
- 而非 `0x08008000+`（应用固件空间）
- 模式 = Handler HardFault

### 2. 确认闪存布局完好
```bash
echo "mdw 0x08000000 4" | nc -w 2 localhost 4444  # bootloader 向量表
echo "mdw 0x08008000 4" | nc -w 2 localhost 4444  # 应用向量表
```

**正常状态**：
```
0x08000000: 20020400 08000201 08000f91 08000f95  ← bootloader
0x08008000: 20005424 080ee21d 080ee281 08008365  ← 应用固件
```

**异常状态**（需要先修复）：
```
0x08008000: ffffffff ffffffff ffffffff ffffffff  ← flash 已被擦除
```

### 3. 确认固件 flash 校验通过
```bash
echo "flash verify_image /data/firmare/pogo-apm/build/rtt_cuav_v5/rtthread.bin 0x08008000" | nc -w 10 localhost 4444
# 期望: "verified 1291936 bytes"
```

## 恢复步骤

### 方法 A：GDB 法（首选）

```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "monitor mww 0xE000ED08 0x08008000" \    # 设置 VTOR
  -ex "set \$sp = *(unsigned int*)0x08008000" \  # 从向量表加载 SP
  -ex "set \$pc = *(unsigned int*)0x08008004" \   # 从向量表加载 Reset Handler
  -ex "monitor resume" \
  -ex "detach" \
  /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rt-thread.elf
```

### 方法 B：OpenOCD telnet 法

分步骤操作：

```bash
# 1. Halt
echo "halt" | nc -w 2 localhost 4444

# 2. 设置 VTOR
echo "mww 0xE000ED08 0x08008000" | nc -w 1 localhost 4444

# 3. 验证 VTOR 设置成功
echo "mdw 0xE000ED08" | nc -w 1 localhost 4444
# 期望: 0xe000ed08: 08008000

# 4. 读取应用向量表获取 SP 和 PC
echo "mdw 0x08008000 4" | nc -w 1 localhost 4444
# 输出示例: 0x08008000: 20005424 080ee21d 080ee281 08008365
# SP = 0x20005424
# Reset = 0x080EE21D

# 5. 设置 SP 和 PC
echo "reg sp 0x20005424" | nc -w 1 localhost 4444
echo "reg pc 0x080EE21D" | nc -w 1 localhost 4444

# 6. 确认后恢复运行
echo "resume" | nc -w 1 localhost 4444
```

### 方法 C：完整系统复位（方法 A/B 失败时）

如果方法 A/B 后仍只有 ttyACM0：

```bash
# 尝试 OpenOCD reset
echo "reset run" | nc -w 1 localhost 4444
sleep 15
ls /dev/ttyACM*
```

或 **物理复位**（断点-上电或按复位键）。

## 恢复后验证

```bash
sleep 15
ls /dev/ttyACM*  # 应显示 /dev/ttyACM1

# MAVLink 心跳
timeout 15 python3 -c "
import pymavlink.mavutil as m
c=m.mavlink_connection('/dev/ttyACM1', baud=921600)
c.wait_heartbeat(timeout=10)
print('HB OK')
" 2>&1
```

## 为什么 GDB 法可能失败

| 原因 | 说明 | 解决方法 |
|------|------|----------|
| 外设锁定 | HardFault 后某些外设（RCC、DWT、NVIC）处于不一致状态 | 只有硬件复位能彻底清除 |
| VTOR 复位 | bootloader 的 HardFault 处理流程可能重设 VTOR | 在 resume 前再检查一次 VTOR |
| 闪存错误 | bootloader 或应用固件被损坏 | 重新烧录 bootloader + 应用 |
| SWD 断开 | 看门狗复位导致 ST-Link 失去连接 | 重新初始化 OpenOCD |

## 与 Flash 空白恢复的区别

| 特征 | Flash 空白 | 看门狗 → Bootloader HardFault |
|------|-----------|-------------------------------|
| PC | `0xFFFFFFFE` | `0x08000fb6` |
| Flash 0x08008000 | `0xFFFFFFFF` 开头 | 固件向量表完好 |
| CDC | 无 | ttyACM0（bootloader DFU） |
| 根因 | bootloader 擦除后未写入 | IWDG 复位后 bootloader 启动失败 |
| 恢复 | 烧录 bootloader | VTOR + SP/PC 跳转 或 reset |
