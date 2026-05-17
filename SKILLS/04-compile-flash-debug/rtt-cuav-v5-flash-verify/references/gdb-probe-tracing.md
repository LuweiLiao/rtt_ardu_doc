# GDB Probe Tracing Technique — Sensor/Peripheral Initialization Debugging

## 适用场景

当 console 没有显示预期的 sensor probe 日志时，用 GDB 验证：
1. probe 函数是否被调用
2. init 函数是否到达
3. probe 在 init() 流程中的位置
4. 调用链（谁调了谁）

## 标准 GDB 追踪脚本

```gdb
# /tmp/trace_probe.gdb — 用 hardware BP 无需 flash 写入
file build/rtt_deploy/cuav_v5/rt-thread.elf
target extended-remote :3333
monitor reset init

# BP1: 顶层调用者
break Copter::init_ardupilot

# BP2: 目标 probe 函数
break AP_Baro_MS5611::probe

# BP3: 目标 init 函数
break AP_Baro_MS56XX::_init

monitor resume
continue          # 等待 BP1，然后 bt 打印调用链
bt 10
continue          # 等待下一个 BP
bt 10
quit
```

## 执行命令

```bash
timeout 40 arm-none-eabi-gdb -batch -x /tmp/trace_probe.gdb 2>&1
```

## 关键陷阱

### ❌ 错误做法：在 commands 中使用 shell sleep

```gdb
# 错误！shell sleep 干扰硬件断点触发
break AP_Baro_MS56XX::_init
commands
  silent
  print "=== HIT ==="
  shell sleep 20    # ← 这会导致 BP 被视为 "missed"
  continue
end
```

### ✅ 正确做法：timeout 包裹

```bash
# 在脚本中只用 continue，外层用 timeout
timeout 40 gdb -batch -x script.gdb
```

## 汇编级验证（无需运行）

```bash
# 检查 probe 是否被编译进 init() 函数
arm-none-eabi-gdb -batch -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "disassemble AP_Baro::init" | grep "bl.*probe"

# 检查所有 probe 调用
arm-none-eabi-gdb -batch -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "disassemble AP_Baro::init" | grep "bl.*MS5611\|get_device\|_add_backend"
```

输出示例：
```
<+88>:  bl 0x804c748 <AP_Baro_MS5611::probe>     ← HAL_BARO_PROBE_LIST
<+96>:  bl 0x8048e74 <AP_Baro::_add_backend>       ← 添加 backend
<+200>: bl 0x804c748 <AP_Baro_MS5611::probe>     ← 第二次（SIM后备路径）
<+208>: bl 0x8048e74 <AP_Baro::_add_backend>
```

## Console 输出捕获

```bash
# 后台捕获 (rt_kprintf 输出到 CDC ACM0 和/或 uart7)
timeout 30 cat /dev/ttyACM0 > /tmp/acm0_output.txt
grep -i "baro\|init\|error\|spi" /tmp/acm0_output.txt
```

注意：`RT_CONSOLE_DEVICE_NAME` 在 rtconfig.h 中配置（CUAV V5: `"uart7"`），
但部分 console 输出也通过 USB CDC ACM0 可见。

## 完整诊断流程（SPI/外设 probe 失败）

```
1. Console 日志 → 确认 init 阶段是否有相关打印
2. GDB BP → 确认 probe 是否被调用
3. GDB BP → 确认 _init() 是否到达
4. 汇编确认 → probe 在 init() 中的位置
5. 反汇编追 → 检查内联优化是否移除了关键调用
```
