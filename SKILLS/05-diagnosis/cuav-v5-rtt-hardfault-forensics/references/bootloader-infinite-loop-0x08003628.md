# Bootloader Infinite Loop at 0x08003628

## 现象

Halt 后 PC 停在 `0x08003628`，反汇编显示 `e7fe` = `b #0x8003628`，这是一个自旋死循环（`B .` = branch to self）。

```
0x08003628  e7fe    b   #0x8003628    ← 无限循环！
```

**不是 idle thread** — idle thread 在 0x080FF746，这是 bootloader 区域内（sector 0: 0x08000000-0x08007FFF）。

## 诊断

### 第一步：确认 PC 在 bootloader 区域

```bash
# CUAV V5 bootloader 在 sector 0 (0x08000000-0x08007FFF)
# App 固件在 sector 1 (0x08008000+)
# 如果 PC 在 0x0800xxxx 且 0x0800xxxx < 0x08008000 → bootloader
```

### 第二步：检查 bootloader 向量表确认版本

```bash
printf 'halt\nmdw 0x08000000 4\nexit\n' | nc -w 5 localhost 4444
# 正常: 0x20020400 (MSP), 0x08000102 (Reset_Handler)
```

### 第三步：检查 RCC_CSR 复位标志

```bash
# 在正常运行时读 RCC_CSR
printf 'halt\nmdw 0x40023874\nexit\n' | nc -w 5 localhost 4444
# bit29=IWDGRSTF, bit28=WWDGRSTF, bit27=PORRSTF
# bit26=SFTRSTF, bit25=BORRSTF, bit24=PINRSTF
```

如果 IWDGRSTF 置位 → 看门狗复位 → app 在运行期间触发了 IWDG 复位，bootloader 复位后进入错误处理。

## 常见触发原因

| 原因 | 识别 | 修复 |
|------|------|------|
| **App CRC 校验失败** | Bootloader 检查 app 的 CRC 不匹配 | 重新烧录 app，使用 `program ... 0x08008000 verify` 确保写入完整 |
| **App 向量表无效** | Bootloader 读 0x08008000 发现 SP 或 Reset 地址在 bootloader 范围内 | 检查 FLASH_ORIGIN 是否为 0x08008000 |
| **IWDG 超时复位** | RCC_CSR 中 IWDGRSTF 置位 | 检查 timer 线程是否规律喂狗，或 app 主循环是否阻塞 |
| **App 返回跳转** | 无新复位标志但 PC 在 bootloader | app 中某条路径跳回到 bootloader 区域 |

## 实际案例（2026-05-12）

**症状**：系统烧录后启动，输出 "Init ArduPilot" + "Calibrating barometer"，60 秒后 halt 发现 PC=0x08003628。

**诊断过程**：
1. 确认 PC 在 bootloader 区域（0x08003628 < 0x08008000）
2. 指令 `e7fe` = `b #0x8003628` = 自旋死循环
3. PSP=0x20023930，Thread mode
4. **不是 watchdog 复位**（系统连续运行 196 秒无 HardFault）
5. Bootloader CRC handler 被触发，但不确定触发条件
6. 最终 root cause 未完全确定，但系统启动后 CDC TX 停止 + bootloader 循环表明 app 初始化途中发生了某种软复位

**经验**：如果系统先正常启动（有 serial 输出），然后进入 bootloader 无限循环，可能是：
- App 在运行途中触发了某种系统复位（不一定是 IWDG）
- Bootloader 在复位后检查 app 时认为无效
- 也可能是 bootloader 本身的 bug（在特定条件下进入错误路径）
