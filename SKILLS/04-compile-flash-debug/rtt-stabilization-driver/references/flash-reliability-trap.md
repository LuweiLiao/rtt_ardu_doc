# 烧录不可靠陷阱 — 优先验证烧录成功再诊断代码

**2026-05-14 关键教训（多次踩坑，浪费近半天）**：当烧录后系统不启动（CDC 无心跳/OpenOCD halt 在异常位置），**第一步永远是验证烧录是否真正成功**，而不是直接诊断代码。

## 验证方法

```bash
# 第一件事：读向量表确认固件真的在 flash 中
echo "mdw 0x08008000 4" | nc -q 2 localhost 4444
# 期望: 0x2000xxxx 0x080eexxx ...（非 0xFFFFFFFF）
```

## 两种烧录方式的可信度对比

| 方式 | 可靠度 | 验证 |
|------|--------|------|
| `openocd -c "program ... verify" -c "reset run" -c "shutdown"` | ✅ **最可靠** | 内置 verify，失败直接报错 |
| telnet → `flash write_image ...` | ⚠️ **静默失败风险** | 必须额外 `mdw` 验证 |
| telnet → `flash write_bank ...` | ❌ 偏移量语法易错 | bank-相对 vs 绝对地址混淆 |

## 诊断流程

```
系统不启动
  ├─ mdw 0x08008000 4 → 0xFFFFFFFF?
  │   → ❌ 烧录失败/未写入 → 改用 `program ... verify` 命令重烧
  ├─ mdw 0x08008000 4 → 有效向量表但启动崩溃?
  │   → ❌ 代码 bug → 开始正常诊断
  └─ OpenOCD 无法连接?
      → 检查僵死 OpenOCD 进程 (pkill -9 openocd)
      → 检查 ST-Link USB (lsusb | grep 0483:3748)
```

## 根因分析

`flash write_image` 通过 telnet 可能因为连接超时/nc 断开等原因**静默失败**——返回状态码表示"wrote N bytes"但实际 flash 内容未被修改。而 `program ... verify` 命令是 OpenOCD 的原子操作，**会做写入后自动校验**，失败立即报错。

## 金标准命令组合

已验证 OK（2026-05-14）：
```bash
openocd -f Tools/debug/openocd-f7.cfg \
  -c "program /path/to/rtthread.bin 0x08008000 verify" \
  -c "reset run" \
  -c "shutdown"
```

## 启示

凡是通过 telnet 发送 flash 写入命令的，如果后续出现"烧录后不启动", 必须先验证写入是否真的成功了，再开始诊断代码。
