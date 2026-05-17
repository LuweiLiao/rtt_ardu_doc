# RTT 堆耗尽诊断流程

> 创建日期：2026-05-17
> 适用：USB 枚举但 pymavlink 读 0 字节

## 症状判断

| 症状 | 诊断 |
|------|------|
| `lsusb` 显示 `1209:5741 CUAVv5 RTT` ✅ | MCU 启动成功，USB 枚举 |
| `/dev/ttyACM1` 存在 ✅ | CDC ACM 设备注册 |
| `pymavlink` 读 0 字节 | 固件卡死在 serial init 前的某处 |
| `gdb halt` → PC=`0x081071ca` | `rt_assert_handler` → 堆耗尽 |

## 5 步诊断流程

### Step 1: GDB halt 确认状态

```bash
arm-none-eabi-gdb -batch -q -iex "set auto-load safe-path /" \
  build/rtt_deploy/cuav_v5/rt-thread.elf \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "reg pc" \
  -ex "bt 5"
```

PC 应在 `rt_assert_handler`，bt 显示调用链包含 `rt_serial_open:676` → `rx_fifo != RT_NULL`。

### Step 2: 检查堆统计

```bash
arm-none-eabi-gdb -batch -q ... -ex "p/x *system_heap"
```

输出格式：
```
address = 0x2006af20   # 堆数据区起始
total = 0x150c0        # 总大小 ~84KB
used = 0x150d0         # 已用 > total → 耗尽或统计异常
max = 0x150d0          # 峰值
```

**判据**：
- `used ≈ 0` → 堆刚初始化
- `used < 20KB` → 有正常线程分配
- `used ≈ total` → 堆耗尽
- `used > total` → 堆元数据损坏或 flash 是旧固件

### Step 3: 验证二进制编译值

```bash
# rt_application_init 的反汇编
FUNC=$(arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | \
       grep "T rt_application_init" | awk '{print $1}')
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \
  --start-address=0x$FUNC --stop-address=+0x20 | grep 'mov'
```

期望输出：`mov.w r3, #4096 ; 0x1000`（4KB 主线程栈）

如果输出 `0x10000`（65536），则修改未生效。

### Step 4: 查 .config 源头

```bash
# hwdef 模式（CUAV V5）
grep "MAIN_THREAD_STACK" libraries/AP_HAL_RTT/hwdef/common/.config
# 必须显示 4096！

# legacy 模式（pixhawk6c_mini）
grep "MAIN_THREAD_STACK" libraries/AP_HAL_RTT/rtt_bsp_pixhawk6c_mini/rtconfig.h
```

**关键陷阱**：hwdef 模式下，`.config` 源头是 `libraries/AP_HAL_RTT/hwdef/common/.config`，**不是** BSP 目录的 `.config`。`rtt_bsp_deploy.py` 的 `_deploy_hwdef()` 用 `shutil.copytree()` 复制整个 common 模板目录到 deploy_dir，BSP `.config` 不参与构建。

### Step 5: 验证 flash 内容

```bash
arm-none-eabi-gdb ... -ex "p app_descriptor"
# 核对 image_crc1, image_crc2, image_size 与构建输出一致
```

如果 CRC 不匹配 → flash 中是旧固件 → 重新烧录。

## 常见根因

| 原因 | 特征 | 修复 |
|------|------|------|
| `RT_MAIN_THREAD_STACK_SIZE=65536` | mov.w r3, #0x10000 | 改 `hwdef/common/.config` → 4096 |
| 修改未生效 | 源头改了但编译用旧值 | 检查 `.config` → `rtconfig.h` 生成链 |
| flash 仍是旧固件 | app_descriptor CRC 不匹配 | 重新 flash + verify |
| 堆元数据损坏 | `used > total` | 查 buffer overflow 或硬错误 |

## 🔴 根本原因：RTT 线程栈从堆分配（与 ChibiOS 架构差异）

这是 P0 堆耗尽的真正根因，远比 `RT_MAIN_THREAD_STACK_SIZE` 问题影响更大。

### 架构差异

| 特性 | ChibiOS | RTT |
|------|---------|-----|
| 栈分配 | `chThdCreateStatic(wa, …)` — 调用者提供 BSS 静态缓冲区 | `rt_thread_create(name, …)` — 内部 `RT_KERNEL_MALLOC(stack_size)` → `kservice.c:898` |
| 栈内存来源 | BSS 段（编译时分配，不占堆） | 动态堆（`rt_malloc` → `mem.c` 分配器） |
| 内存安全 | 每个线程栈独立 BSS 符号 | 堆中连续分配，可能碎片化或耗尽 |

### RTT 线程栈分配调用链

```
Scheduler.cpp:thread_create_wrapper()
  → rt_thread_create(name, stack_size, ...)    # modules/rt-thread/src/thread.c:550
    → RT_KERNEL_MALLOC(stack_size)              # thread.c:568
      → rt_malloc(stack_size)                   # kservice.c:898
        → rt_smem_alloc()                       # mem.c:275 (RTT 小内存管理算法)
```

**关键代码行**：`modules/rt-thread/src/thread.c:568` — 这里 `rt_thread_create` 用 `RT_KERNEL_MALLOC` 为线程栈分配堆内存。

### GDB 跟踪诊断技术

#### 技术 1：硬件观察点监控堆增长

```bash
# 先找到 system_heap.used 的地址
arm-none-eabi-gdb -batch -q \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p/x &system_heap" \
  -ex "p/x system_heap.total" \
  -ex "p/x system_heap.used"
# → system_heap.used@0x2006af04 (偏移 36 字节)

# 设置硬件观察点（4 字节写入触发）
# system_heap.used 在 struct rt_small_mem 中偏移 = sizeof(name)+type+flag+pad+list+algorithm+address+total = 12+1+1+2+8+4+4+4 = 36
arm-none-eabi-gdb -batch -q \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "watch *(int*)($(echo 'obase=16;ibase=16;2006AEE0+24' | bc | tr A-F a-f))" \
  -ex "continue"
# (需交互式运行以观测每次命中)
```

#### 技术 2：条件断点捕获大型分配

```bash
# 在 rt_smem_alloc 上设置条件断点，只捕获大于 2KB 的分配
arm-none-eabi-gdb -batch -q \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "break rt_smem_alloc if r5 > 2048" \
  -ex "commands" \
  -ex "  silent" \
  -ex "  printf \"ALLOC size=%d from \", r5" \
  -ex "  bt 5" \
  -ex "  continue" \
  -ex "end" \
  -ex "continue"
```

#### 技术 3：跟踪线程创建调用

```bash
# 在 Scheduler::init() 的 thread_create 调用处设断点
# 获取函数地址：
arm-none-eabi-nm rt-thread.elf | grep "T rt_thread_create"
# 然后在每一个 rt_thread_create 调用处捕获调用栈 + 栈大小
```

### 线程栈审计表（ArduPilot on RTT, Scheduler.cpp）

| 线程名 | 原大小 | 修复后 | ChibiOS 参考 | 说明 |
|--------|--------|--------|-------------|------|
| ap_timer | 16384 | **4096** | 静态 4KB | 定时器线程，小栈即可 |
| ap_io | 8192 | **4096** | 静态 4KB | GPIO/SPI 回调线程 |
| storage | 8192 | **4096** | 静态 4KB | 参数存储线程 |
| ap_uart | 8192 | **4096** | 静态 4KB | UART 驱动线程 |
| ap_rcout | 2048 | 2048 | 静态 2KB | — |
| ap_rcin | 2048 | 2048 | 静态 2KB | — |
| ap_mon | 2048 | 2048 | 静态 2KB | — |
| **总节省** | | **24KB** | | |

### 实际堆消耗计算

```
堆总容量: 86,208 bytes (0x150c0)
修复前线程栈总和: ~59KB (含 ap_timer=16K + ap_uart=8K + ap_io=8K + storage=8K + main=4K + 其他)
修复前可用: ~21KB → serial rx_fifo(5×~4KB) 失败 ❌
修复后线程栈总和: ~35KB (ap_timer=4K + ap_uart=4K + ap_io=4K + storage=4K + ...)
修复后可用: ~51KB → 堆健康 ✅
```

### ⚠️ 关键陷阱：clean rebuild

修改 `Scheduler.cpp` 中的线程栈大小后，**必须 `rm -rf build/rtt_deploy/ build/rtt_cuav_v5/` 再编译**。SCons 编译缓存可能提供旧的目标文件（即使源文件已修改）。验证方法：

```bash
# 检查编译出的二进制是否真的用了新值
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf \
  | grep -A5 "Scheduler::init" \
  | grep "mov.*#"
# 应显示 ap_timer=4096(0x1000), ap_io=4096(0x1000) 等
```

### 验证标准

修复后 GDB 检查：
```bash
arm-none-eabi-gdb -batch -q \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "p/x system_heap" \
  -ex "monitor resume"
# 期望: used < total
# 输出示例:
# total = 0x150c0, used = 0x36e8 (14KB, 远小于 total) ✅
```

## `.config` → `rtconfig.h` 生成链

```
hwdef/common/.config  ← 改这里！
  → rtt_bsp_deploy.py:_deploy_hwdef()
    → shutil.copytree("hwdef/common", "build/...")
      → build/rtt_deploy/cuav_v5/.config
        → _generate_rtconfig() → _simple_config_to_header()
          → build/rtt_deploy/cuav_v5/rtconfig.h
            → rtt_hwdef.py:write_rtconfig_h() 追加外设使能
              → 编译
```

完整修改：改源头 `hwdef/common/.config`，`rm -rf build/rtt_deploy/`，重新编译。
