# Bootloader 时序检查陷阱

## 现象

烧录后 `reset run` + 立即 `halt` 显示 PC 在 0x08003628（bootloader 主循环），而非 0x080exxx（应用代码）。容易出现误判认为"bootloader 没跳转"。

## 根因

`reset run` 后，MCU 从 bootloader 启动（向量表 0x08000000），bootloader 初始化后跳转到应用（0x08008000）。但 `halt` 命令发送时如果 bootloader 正在执行，就会停在 bootloader 中。

正确的时序是：

```
reset run (t=0)
  → bootloader startup (t=0~10ms, PC=0x08000200)
  → bootloader main loop (t=10~50ms, PC=0x08003628)  
  → bootloader 跳转到应用向量表 (t=50~100ms, PC=0x0800xxxx)
  → 应用启动 → SystemInit → entry → main (t=100~200ms)
  → 调度器启动 (t=200~500ms, PC=0x080exxx, PSP模式)
```

## 正确诊断方法

```bash
# 方法 A（推荐）：reset init + resume + 等待 + halt
# OpenOCD 单命令行（不依赖 telnet 持久连接）
openocd -f Tools/debug/openocd-f7.cfg \
  -c init \
  -c "reset init" \
  -c resume \
  -c "sleep 6000" \
  -c halt \
  -c "reg pc" \
  -c "reg xPSR" \
  -c "mdw 0xE000ED28 2"

# 方法 B：telnet 持久连接（注意超时）
(echo "reset init"; sleep 2;
 echo "flash erase_sector 0 1 11"; sleep 20;
 echo "flash write_image build/rtt_cuav_v5/rtthread.bin 0x08008000"; sleep 15;
 echo "resume"; sleep 10;
 echo "halt"; sleep 2) | nc -q 52 localhost 4444
```

## 验证结果解析

| halt 后显示 | 含义 |
|-----------|------|
| `pc: 0x08000200, msp: 0x20020400` | Bootloader 刚启动（SWD 刚连接时暂停于此） |
| `pc: 0x08003628, psp: 0x20023930` | Bootloader 主循环中，等待跳转 |
| `pc: 0x080fxxxx, psp: 0x2000532c` | **应用正在运行** ✅ CFSR=0 时表示正常运行 |
| `pc: 0x080083ca` | HardFault_Handler 死循环 ❌ |
