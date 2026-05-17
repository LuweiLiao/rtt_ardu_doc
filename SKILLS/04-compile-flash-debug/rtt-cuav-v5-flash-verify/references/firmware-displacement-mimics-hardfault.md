# 固件位移（Firmware Displacement）导致的 HardFault 误诊

## 根因

当 `rtthread.bin`（VMA 基址 0x08008000）被烧录到错误地址 `0x08000000`（而不是正确的 `0x08008000`），
所有代码在 flash 中位移了 32KB（0x8000 字节）。

## 症状表现

向量表在 0x08000000 是**有效的**（MSP 和 Reset_Handler 地址看起来正确），
但实际代码在错误的位置。MCU 跳转到 Reset_Handler 的 VMA 地址，却读到随机数据。

**HardFault 症状可以表现为任何位置、任何函数名的崩溃**，因为执行的是随机数据。

## 历史案例（2026-05-08）

### 如何掉坑

1. CLAUDE.md 写道"烧录地址必须是 0x08000000：rtthread.bin 是完整镜像含bootloader"
2. `flash erase_sector 0 0 last` 擦掉 bootloader
3. `flash write_image rtthread.bin 0x08000000` 把固件写到偏移地址
4. MCU 读向量表，Reset=0x080EE841，跳转
5. 实际代码在 flash[0x080E6841]，MCU 读 flash[0x080EE841]
6. 读到的随机数据 → HardFault

### 消耗的调试时间

| 步骤 | 诊断 | 实际原因 |
|------|------|---------|
| 看见 `AP_GPS_Blended::calc_state(this=0x33)` | 堆损坏 → NEW_NOTHROW 返回垃圾指针 | 错误指令导致 `this` 寄存器乱值 |
| 加 memset drivers[] | 初始化修复 | 无效 — 不是代码问题 |
| 加静态 AP_GPS_Blended 对象 | 绕过堆分配 | 无效 |
| 加 nullptr+SRAM 范围安全检查 | 防止越界调用 | 无效 — 安全检查从未执行 |
| 核实 BIN 偏移与 ELF 不一致 | 发现烧录地址错误 | 这才是根因 |

**总计浪费: ~2 小时**。如果在第一次看到 HardFault 时就检查 flash 地址，可以避免。

## 诊断流程

当你调试嵌入式固件遇到 HardFault，**第一步不是分析崩溃函数**，而是验证 flash 布局：

```bash
# 1. 检查向量表在 flash 中的位置
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "x/4xw 0x08000000" \
  -ex "x/4xw 0x08008000" \
  -ex "quit"
```

期望:
- `0x08000000`: bootloader 向量表 (SP ≈ 0x2002xxxx, Reset ≈ 0x08000201)
- `0x08008000`: 固件向量表 (SP ≈ 0x2000xxxx, Reset ≈ 0x080EExxx)

如果两者相同（都是固件向量表），或者 0x08000000 的 Reset 地址与当前执行地址矛盾，则 flash 布局错误。

### 快速判断算法

```
if (当前 PC 在固件范围内 but flash 布局检查显示 0x08000000 也包含固件向量表):
    诊断 = "固件位移 — 烧录地址错误"
    # 因为 bootloader 向量表应该占据 0x08000000
    # 如果 0x08000000 和 0x08008000 的向量表相同 → bootloader 被覆盖
```
