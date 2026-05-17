# OpenOCD 复位诊断工作流（RTT ArduPilot）

当遇到系统周期性复位、setup 卡死、或主循环不启动等问题时，使用以下诊断流程。

## 1. 基线诊断：监测 setup 进度而不干扰运行

目标：在不复位不 halt 的情况下观察 setup stage 推进。

```python
import socket, time, re
s = socket.socket()
s.settimeout(5)
s.connect(('localhost', 4444))
time.sleep(1)

def cmd(c):
    s.sendall(c.encode()+b'\n')
    time.sleep(0.3)
    try:
        s.settimeout(0.2)
        return s.recv(2048)
    except:
        return b''

cmd('reset run')
time.sleep(3)

last_stage = -1
for i in range(12):
    time.sleep(5)
    r = cmd('mdw 0x2001bc84')
    r2 = cmd('mdw 0x20019980')
    t = (r+r2).decode('utf-8','replace')
    
    m = re.search(r'2001bc84:\s+(\w+)', t)
    st = int(m.group(1),16) if m else -1
    m = re.search(r'20019980:\s+(\w+)', t)
    it = int(m.group(1),16) if m else -1
    
    note = ''
    if st==651 and it>0: note=' MAIN LOOP'
    elif st < last_stage and st > 0 and last_stage > 0: note=f' RESET! ({last_stage}->{st})'
    
    print(f'{5*(i+1):2d}s: st={st:3d} it={it:5d}{note}')
    last_stage = st
```

## 2. 识别复位原因

### 2a. 检查故障寄存器

```bash
echo "halt
mdw 0xe000ed28   # CFSR
mdw 0xe000ed2c   # HFSR
mdw 0xe000ed30   # DFSR
mdw 0xe000ed34   # MMFAR
resume" | nc localhost 4444
```

### 2b. 确认 IWDG 状态

```bash
echo "halt
mdw 0x40003000   # IWDG_KR
mdw 0x40003004   # IWDG_PR
mdw 0x40003008   # IWDG_RLR
mdw 0x4000300c   # IWDG_SR
resume" | nc localhost 4444
```

### 2c. 确认 RCC_CSR 复位原因（注意：RMVF 会清除标志）

```bash
echo "mdw 0x40023874" | nc localhost 4444
# 位 26 = IWDGRSTF, 位 27 = WWDGRSTF
# 位 24 = BORRSTF, 位 20 = PINRSTF, 位 8 = SFTRSTF
```

### 2d. 捕捉复位瞬间

复位发生后，SRAM 内容在 warm reset 后**保留**。这意味着：
- `rtt_dbg_setup_stage` 保留复位前的值（如 662）
- `rtt_dbg_main_loop_iterations` 保留旧值
- 在复位后的第一个 `run()` 调用中，`rtt_dbg_hal_run_called` 被设为 `0xAAAAAAAA`

检测方法：如果 `stage >= 651` 但 `hal_run_called = 0xAAAAAAAA`（而非 `0x11111111`），说明 SRAM 中保留的是旧 stage 值，系统实际刚复位不久。

### 2e. addr2line 快速定位 PC

```bash
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf 0x<pc_value>
```

## 3. 常见复位模式

| 模式 | 表现 | 根因 |
|------|------|------|
| 每 ~30s 复位 | stage 从 662→600，循环 | INS 校准中 DWT 忙等饿死 timer → IWDG 超时或 panic |
| 每 ~2s 复位（主循环运行时） | 主循环运行但周期性复位 | IWDG 已启动但 timer 线程未 pat（`_hal_initialized` 未设早） |
| 不休止复位循环 | 永远停在 stage 0 | bootloader 或 startup 代码问题 |
| HardFault 后复位 | CFSR≠0，PC 在非法地址 | 需检查 fault 寄存器和异常帧 |

## 4. 循环率测量（halt-resume 法）

```python
# 必须用同一个 OpenOCD 连接，复位后用连续连接监测
oc('halt')
r = oc('mdw 0x20019980')
m = re.search(rb'20019980:\s+(\w+)', r)
base = int(m.group(1), 16)
oc('resume')
time.sleep(5.0)
oc('halt')
r = oc('mdw 0x20019980')
m = re.search(rb'20019980:\s+(\w+)', r)
end = int(m.group(1), 16)
delta = end - base
rate = delta / 5.0
```

注意：`mdw` 输出格式为 `0xADDR: VALUE`（值部分**无** `0x` 前缀），正则应为 `re.search(rb'ADDR:\s+(\w+)', data)`。

## 5. Stage 编号速查

| Stage | 位置 | 事件 |
|-------|------|------|
| 0 | — | 复位后 BSS 初始值 |
| 500-503 | `Storage.cpp:24-63` | 参数存储初始化 |
| 600-651 | `ArduCopter/system.cpp:20-181` | `init_ardupilot()` 各阶段 |
| 600 | — | `init_ardupilot` 入口 |
| 620 | — | 板级初始化完成 |
| 630-633 | — | GPS 初始化 |
| 640-641 | — | 外设初始化 |
| 650 | — | 即将 `startup_INS_ground` |
| 651 | — | setup 完成 |
| 660 | — | `startup_INS_ground` 入口 |
| 661 | — | `ahrs.init()` 完成 |
| 662 | — | 即将 `ins.init()` |
| 663 | — | `ins.init()` 完成 |
| 664 | — | `ahrs.reset()` 完成 |
