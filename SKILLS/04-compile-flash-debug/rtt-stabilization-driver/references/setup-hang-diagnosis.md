# Setup Hang 诊断参考

## 三步诊断法

1. 读 `rtt_dbg_setup_stage`（符号地址：`arm-none-eabi-nm rt-thread.elf | grep setup_stage`）
2. 读 PC → 判断线程当前执行位置
3. 恢复运行 30s 后重新 halt → 判断是否在前进

## ⚠️ 关键新发现（2026-05-16）：慢推进 ≠ 真卡死

**setup_stage 停在一个值上，不代表系统真的卡死了。** 必须间隔 10-15s 读两次才能区分：

| 诊断 | 现象 | 根因 |
|------|------|------|
| **真卡死** | 两次 halt 读到的 setup_stage 相同，PC 在同一函数同一行 | 死锁、线程创建失败、SPI 总线线程缺失 |
| **慢推进** | setup_stage 在推进（如 502→620→680），PC 在各线程间切换 | I2C 软 bitbang 位爆炸、Flash sector erase（~2s）、IOMCU UART 超时 |

**实证**：此前认为 setup_stage=662 是 ins.init() 卡死，实际是 I2C 软 bitbang（IST8310）导致启动极慢。系统最终完整启动并发送 MAVLink HEARTBEAT。

## setup_stage 解码表

| 值域 | 含义 | 源码位置 |
|------|------|---------|
| 500-503 | Storage 初始化 | `AP_HAL_RTT/Storage.cpp` |
| 600-620 | Copter init_ardupilot 前半段 | `ArduCopter/system.cpp` |
| 660-664 | startup_INS_ground | `system.cpp:217-230` |
| **665-681** | **INS-init 内部分段标记** | `AP_InertialSensor.cpp` → `references/ins-init-internal-marker-scheme.md` |

## 相关参考

- `references/ins-init-internal-marker-scheme.md` — INS-init 分段标记方案
- `references/i2c-soft-bitbang-fix.md` — I2C 软 bitbang 卡死根因与修复

## DeviceBus 总线线程创建失败

```bash
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p (int)RTT::DeviceBus::_buses[1]->_thread_started" \
  -ex "monitor resume" -ex "quit"
```

## IOMCU UART 超时

```bash
for i in 1 2 3; do
  arm-none-eabi-gdb -batch \
    -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
    -ex "target extended-remote :3333" \
    -ex "monitor halt" \
    -ex "bt 5" \
    -ex "monitor resume" -ex "quit" 2>&1 | grep "^#"
done
```
