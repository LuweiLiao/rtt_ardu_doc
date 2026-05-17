# RTT 参数系统 MAVLink 限制：PARAM_SET ACK 缺失

## 现象

通过 MAVLink `PARAM_SET` 发送参数写入后，飞控不返回 `PARAM_VALUE` ACK。

```
MAVLink:   PARAM_SET(COMPASS_ENABLE=1.0, type=REAL32)
FC:        [静默 — 无 PARAM_VALUE 回执]
```

**影响范围**：
- 参数值在 RAM 中实际已被修改 ✅
- 参数未持久化到 EEPROM（RTT Storage 后端无实现）
- GCS（MissionPlanner, QGC）会显示"参数写入失败"警告 ⚠️
- 部分 GCS 可能反复尝试写入导致 MAVLink 队列拥堵

## 诊断方法

### 方法 A：发送 PARAM_SET 并监听所有消息（验证无 ACK）

```python
import pymavlink.mavutil as m, time
c = m.mavlink_connection('/dev/ttyACM1', baud=921600, timeout=5)
c.wait_heartbeat(timeout=8)

# Drain
time.sleep(0.5)
while c.recv_match(blocking=True, timeout=0.3): pass

# Send PARAM_SET
c.mav.param_set_send(c.target_system, c.target_component,
                     b"COMPASS_ENABLE", 1.0, 5)  # 5=MAV_PARAM_TYPE_REAL32
print("PARAM_SET sent", flush=True)

# Listen for PARAM_VALUE
found = False
for i in range(8):
    msg = c.recv_match(blocking=True, timeout=1)
    if msg:
        t = msg.get_type()
        if t == 'PARAM_VALUE':
            pid = msg.param_id.rstrip(chr(0))
            print(f"PARAM_VALUE: {pid}={msg.param_value}", flush=True)
            found = True
        elif t == 'STATUSTEXT':
            print(f"STATUSTEXT: {msg.text}", flush=True)
    else:
        print(f"No response at {i+1}s", flush=True)
        
if not found:
    print("❌ PARAM_SET ACK missing", flush=True)
```

### 方法 B：写后读验证（确认 RAM 修改）

```python
# 1. 读当前值
c.mav.param_request_read_send(c.target_system, c.target_component, b"COMPASS_ENABLE", -1)
pr = c.recv_match(type='PARAM_VALUE', blocking=True, timeout=3)
orig = pr.param_value

# 2. 写入
c.mav.param_set_send(c.target_system, c.target_component, b"COMPASS_ENABLE", float(orig), 5)

# 3. 读回验证
time.sleep(2)
c.mav.param_request_read_send(c.target_system, c.target_component, b"COMPASS_ENABLE", -1)
pr2 = c.recv_match(type='PARAM_VALUE', blocking=True, timeout=3)
print(f"orig={orig} readback={pr2.param_value}")
```

## 根因分析

ArduPilot 的 `PARAM_SET` 处理链（`libraries/GCS_MAVLink/GCS_Param.cpp`）：

```
handle_param_set() (line 295):
  1. decode packet, find param by name (line 309)
  2. if null or NaN → return (无 ACK)                    ← line 310
  3. if !allow_set_via_mavlink → send_parameter_value()  ← line 325 (deny case有ACK)
  4. vp->set_float(value, type)                          ← line 330
  5. vp->save(force_save)                                ← line 342
  6. [日志代码]                                           ← line 344-353
  7. }  ← 函数结束！                                     ← line 354
```

**关键发现**：`handle_param_set()` 的成功路径中 **从未调用 `send_parameter_value()`**！函数在 `vp->save()` 之后直接返回，不发送 PARAM_VALUE ACK。

**正常的 ACK 机制**：依赖 `param_io_timer()` 异步 IO 管道（line 397-414），该管道从 `param_replies` 队列中取出待发送的 PARAM_VALUE 并发送。但此管道在 RTT 上已损坏（`handle_param_request_read` 的注释已说明：`// Direct send: bypass the broken async IO pipeline on AP_HAL_RTT`）。`handle_param_request_read()` 已有 RTT 直接发送绕过，但 `handle_param_set()` 之前没有。

**这个 bug 对所有平台都存在** — ChibiOS 上由于 `param_io_timer()` 正常工作，ACK 通过异步管道发出；RTT 上该管道损坏，导致 ACK 永远不会发送。

**不是 Storage 后端问题** — 参数值在 RAM 中已正确修改（`vp->set_float()` 成功），`vp->save()` 仅标记待持久化，返回值不影响 ACK 发送。

## 修复（已实施，commit 1b33bbbdfc）

### 短期修复：RTT 直接发送 ACK

在 `handle_param_set()` 末尾添加与 `handle_param_request_read()` 相同的 RTT 直接发送：

**文件**: `libraries/GCS_MAVLink/GCS_Param.cpp`
**位置**: 函数末尾（日志代码之后、闭合括号之前，行 354 附近）

```cpp
#if CONFIG_HAL_BOARD == HAL_BOARD_RTT
    // RTT: direct send — the async IO pipeline is broken on RTT
    send_parameter_value(key, var_type, vp->cast_to_float(var_type));
#endif
```

**验证结果**：
```
PARAM_SET: COMPASS_ENABLE=1.0
[+1s] PARAM_VALUE: COMPASS_ENABLE=1.0 idx=65535 ✅  → ACK 收到！
```

### 长期修复（上游兼容）
- 修复 `param_io_timer()` 的 async IO 管道（RTT Scheduler 定时器回调问题）
- 或：在 `handle_param_set()` 中无条件添加 `send_parameter_value()`（对 ChibiOS 无害，只是多一次直接发送）

## 相关代码位置

| 内容 | 文件 | 行号 |
|------|------|------|
| `handle_param_set()` | `libraries/GCS_MAVLink/GCS_Param.cpp` | 295-354 |
| `send_parameter_value()` (per-channel) | `libraries/GCS_MAVLink/GCS_Param.cpp` | 356-366 |
| `send_parameter_value()` (broadcast) | `libraries/GCS_MAVLink/GCS_Param.cpp` | 371-391 |
| `param_io_timer()` (异步管道) | `libraries/GCS_MAVLink/GCS_Param.cpp` | 397-414 |
| `handle_param_request_read()` (已有RTT修复) | `libraries/GCS_MAVLink/GCS_Param.cpp` | 231-260 |
| RTT Storage 后端 | `libraries/AP_HAL_RTT/Storage.cpp` | 全部 |
