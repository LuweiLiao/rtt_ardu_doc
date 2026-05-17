# 验证证据归档

## 使用方式
每个修复/里程碑完成后，在本目录下创建 `<Phase>-<Date>-<Description>.md` 文件。

## 证据模板

```plaintext
---
milestone: "L0 / Phase 0B / P0"
date: 2026-05-17
verifier: "Hermes Agent / GDB / pymavlink"
---

## 验证对象
[一句话描述验证的内容]

## 编译证据
```bash
Build command: scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
Binary: build/rtt_deploy/cuav_v5/rtthread.bin
SHA256: $(sha256sum build/rtt_deploy/cuav_v5/rtthread.bin)
```

## 烧录证据
```bash
Flash command: openocd -f interface/cmsis-dap.cfg -f target/stm32f7x.cfg \
  -c "program Tools/AP_Bootloader/bl_cuavv5.bin 0x08000000 verify" \
  -c "program build/rtt_deploy/cuav_v5/rtthread.bin 0x08008000 verify"
Result: Verified OK
```

## app_descriptor dump
```
(gdb) x/32bx 0x08008000
```

## GDB/OpenOCD 验证
```
(gdb) p/x system_heap
(gdb) p/x _end
(gdb) p/x rtt_dbg_setup_stage
```

## MAVLink 验证
```python
from pymavlink import mavutil
m = mavutil.mavlink_connection('/dev/ttyACM1', baud=921600)
m.wait_heartbeat()
print(f"Got HEARTBEAT from system {m.target_system}")
```

## 回归检查清单
- [ ] heap canary 未触发
- [ ] used <= total
- [ ] CDC ACM /dev/ttyACM1 存在
- [ ] MAVLink HEARTBEAT 可收到
- [ ] 主循环率 > 100Hz
- [ ] 所有 SPI 传感器 probe 通过
```

## 阶段证据索引

| 日期 | 阶段 | 验证项 | 文件 |
|------|------|--------|------|
| - | - | - | - |
