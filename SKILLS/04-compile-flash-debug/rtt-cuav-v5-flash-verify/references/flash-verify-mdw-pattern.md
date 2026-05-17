# 烧录验证的 mdw 模式（2026-05-09）

## 问题背景

多轮烧录中反复出现：OpenOCD 输出 "wrote 1291416 bytes" 但实际 flash 为 0xFFFFFFFF。
根因是 stale OpenOCD 进程（使用不同 config 文件）占用了 ST-Link，导致新 openocd 的
`flash write_image` 命令被静默忽略——输出说写入成功但实际未执行。

## 可信验证模式

每次烧录后必须做三层验证才能信任写入结果：

### 第 1 层：向量表验证（最快、最可信）

```bash
echo "mdw 0x08008000 4" | timeout 5 nc localhost 4444 2>&1 | tr -cd '[:print:]\n\r'
```

**期望输出**：`0x08008000: 2000xxxx 080eexxx ...`（4 个有效字）
**失败输出**：`0x08008000: ffffffff ffffffff ffffffff ffffffff` → 写入未生效

第一个字 `2000xxxx` 是初始 MSP（应在 0x20000000–0x20080000 范围内）。
第二个字 `080eexxx` 是 Reset_Handler 地址（应在 0x08008000–0x08200000 范围内）。
所有字不为 0 且不为 0xFFFFFFFF。

### 第 2 层：openocd verify_image（耗时较长）

```bash
openocd -f ... -c init -c "reset halt" -c "verify_image build/rtt_cuav_v5/rtthread.bin 0x08008000"
```

输出 `verified N bytes in ...` 表示通过。
输出 `diff 0 address ...` 表示有差异，flash 内容不一致。

⚠️ 注意：verify_image 在写入命令序列的末尾执行才可靠。
如果在写入命令后立即执行 verify，OpenOCD 可能仍在处理写入缓冲区。

### 第 3 层：MCU 运行状态验证

```bash
# 复位后让 MCU 运行 8-10 秒
echo "reset run" | timeout 10 nc localhost 4444

# 然后 halt 检查
echo "halt" | timeout 5 nc localhost 4444 2>&1 | grep -E "halted|pc:"
```

**期望**：
- `pc: 0x080e....` 且 `psp: 0x2000....` → 应用代码执行中（调度器运行）
- `pc: 0x080083ca` → HardFault 循环（代码无误但运行时崩溃）
- `pc: 0x08000200` 或 `pc: 0x08003628` → 仍在 bootloader 中（未跳转或跳转失败）

## 写入失败恢复流程

```bash
# 1. 检查并清理 stale OpenOCD
ps aux | grep -v grep | grep openocd
pkill -9 openocd 2>/dev/null; sleep 3

# 2. 确认 ST-Link 可用
ss -tlnp | grep -E "4444|6666" || echo "ports free"

# 3. 重新烧录（带 verify）
openocd -f Tools/debug/openocd-f7.cfg \
  -c init -c "reset halt" \
  -c "flash erase_sector 0 1 11" \
  -c "flash write_image build/rtt_cuav_v5/rtthread.bin 0x08008000" \
  -c "verify_image build/rtt_cuav_v5/rtthread.bin 0x08008000"

# 4. 立即验证向量表
# （启动新的 openocd 后台进程，然后用 telnet mdw）
openocd -f Tools/debug/openocd-f7.cfg &
sleep 5
echo "mdw 0x08008000 4" | timeout 5 nc localhost 4444 2>&1
```

## 快速健康检查模板

```bash
# 烧录后完整检查序列
(
  echo "reset run"; sleep 1  # 需要先 reset
) | timeout 3 nc -q 2 localhost 4444

pkill -9 openocd 2>/dev/null; sleep 3
openocd -f Tools/debug/openocd-f7.cfg &
sleep 8

# 检查
echo "halt" | timeout 5 nc localhost 4444 2>&1 | grep "pc:" && echo "✅ booted"
echo "mdw 0xE000ED28 2" | timeout 5 nc localhost 4444 2>&1 | grep "0x.*00000000" && echo "✅ no fault"
```
