# 启动阶段诊断：rtt_dbg 调试变量分析法

当系统烧录后无 CDC 输出或无法确认是否启动时，通过 OpenOCD 读取 `rtt_dbg_*` 调试变量，**无需串口输出**即可确定系统在哪个阶段卡住。

## 读取方法

```python
import socket, time, re, subprocess

result = subprocess.run(['arm-none-eabi-nm', 'build/rtt_deploy/cuav_v5/rt-thread.elf'],
                       capture_output=True, text=True)
symbols = {}
for line in result.stdout.split('\n'):
    parts = line.strip().split()
    if len(parts) >= 3:
        symbols[parts[2]] = int(parts[0], 16)

vars_to_check = {
    'rtt_dbg_hal_run_called': symbols.get('rtt_dbg_hal_run_called'),
    'rtt_dbg_ctor_phase': symbols.get('rtt_dbg_ctor_phase'),
    'rtt_dbg_setup_stage': symbols.get('rtt_dbg_setup_stage'),
    'rtt_dbg_main_loop_iterations': symbols.get('rtt_dbg_main_loop_iterations'),
    'rtt_sd_mount_stage': symbols.get('rtt_sd_mount_stage'),
    'rtt_sd_mount_result': symbols.get('rtt_sd_mount_result'),
}

s = socket.socket(); s.settimeout(5)
s.connect(('localhost', 4444)); time.sleep(0.3); s.recv(4096)
s.sendall(b'halt\n'); time.sleep(1)
resp = s.recv(4096).decode('latin-1', errors='replace')

for name, addr in vars_to_check.items():
    if addr is None: continue
    s.sendall(f'mdw {hex(addr)}\n'.encode()); time.sleep(0.3)
    r = s.recv(4096).decode('latin-1', errors='replace')
    m = re.search(rf'{hex(addr)[2:]}:\s+(0x[0-9a-f]+)', r)
    val = int(m.group(1), 16) if m else -1
    print(f'{name:35s} = 0x{val:08X} ({val})')
s.close()
```

## 变量含义速查

| 变量 | 初始值 | 运行值 | 含义 |
|------|--------|--------|------|
| `rtt_dbg_hal_run_called` | 0xDEADBEEF | →0xAAAAAAAA→0xBBBBBBBB | `hal->run()` 进入→完成 |
| `rtt_dbg_ctor_phase` | 0 | 4(完成) | C++ 静态构造函数阶段 |
| `rtt_dbg_setup_stage` | 0 | 1-651 | `setup()` 进度 |
| `rtt_dbg_main_loop_iterations` | 0 | >0 | 主循环已运行计数 |
| `rtt_sd_mount_stage` | 0 | 10(完成) | SD卡挂载进度 |
| `rtt_sd_mount_result` | -99 | 0(成功) | SD卡挂载结果 |

## 诊断决策树

```
hal_run_called = DEADBEEF
  ├─ ctor_phase = 4  → rt_components_init()已完成，但 main() 未执行
  │  ├─ sd_mount_stage=10, sd_mount_result=0 → 线程调度问题
  │  └─ sd_mount_stage<10 → 某 init 函数阻塞
  └─ ctor_phase < 4  → 构造函数卡住
     └─ 检查 rtt_dbg_ctor_addr + rtt_dbg_ctor_index

hal_run_called = AAAAAAAA → run()刚进入，setup()执行中
hal_run_called = BBBBBBBB → run()完成，主循环在运行
setup_stage > 600 → 主循环稳定运行 ✅
```

## ⚠️ 已知地址变更

| 符号 | 旧地址 | 新地址 | 说明 |
|------|--------|--------|------|
| rtt_dbg_setup_stage | 0x2001bc84 | 0x2001bd0c | BSS段因代码增减偏移 |
| rtt_dbg_main_loop_iterations | 0x20019980 | 0x20019a24 | 同上 |

**每次重建后必须用 `nm` 确认实际地址。**

> ⚠️ 地址变更原因：每次增删代码导致 BSS 段布局变化，`rtt_dbg_*`全局变量的虚拟地址偏移。**不完全重建（只编译部分文件）不会解决此问题**——要用 `rm -rf build/rtt_deploy/ && scons ...` 确保完全刷新。

## 对照实验法隔离回归

当怀疑某组代码改动引入启动失败时，通过选择性回退定位：

```bash
# 1. 备份改动
git stash save "work"

# 2. 在已知正常基线编译验证
git checkout <known_good_commit>
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
# 烧录+验证基线正常

# 3. 恢复改动
git stash pop

# 4. 选择性回退单个文件
git checkout HEAD -- path/to/suspected/file
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
# 烧录+验证 → 如果恢复则根因在该文件
```

**原则**：一次只回退一个文件/修改。不要同时回退多个再逐个加回。
