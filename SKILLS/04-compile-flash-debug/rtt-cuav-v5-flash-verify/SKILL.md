---
name: rtt-cuav-v5-flash-verify
category: embedded
description: CUAV V5 RTT固件烧录、调试与验证完整工作流 — 编译→烧录→MAVLink验证→常见陷阱
related_skills:
  - rtt-cuav-v5-adc-spi-conflict
  - rtt-stm32f7-usb-dwc2-debug
  - rtt-chibios-api-adaptation
  - local-rag-kb-mcp
rules:
  - "🛑 永不在flash写入中kill -9 OpenOCD: `monitor flash write_image`或`load`执行期间绝不能用pkill或kill -9中断。中断=flash损坏→MCU SWD锁定→需要物理干预才能恢复。必须等写入自然完成"
  - "监督协助CC模式: RTT移植的代码修改、烧录、调试由CC（Claude Code）执行，本Agent只负责监督进度、在CC卡住时救助、提供决策建议。禁止自己独立修改代码→烧录→验证"
  - "不达目的不罢休: 每个问题必须找到根本原因并系统性解决，禁止打地鼠式零散patch"
  - "不请示不等待: 从不问'要不要'，自动选择最优方案继续推进"
  - "根本解决而非症状缓解: 如果根因清楚（如MOSI引脚错误），直接做根本性修复，不要做表面workaround"
  - "中文git commit: 每解决一个问题必须提交一个中文git commit，描述问题根因和解决方案"
  - "双重验证: 每条修改必须通过OpenOCD GDB + CDC MAVLink双重验证"
  - "禁止物理断电: 只能用OpenOCD reset init或软件NVIC reset，禁止要求用户拔插USB"
  - "cron自动监控: 每10分钟检查固件健康状况，卡住自动救助"
  - "问题树思维: 遇到问题先建立问题树（现象→可能根因→验证方法→修复方案），选最优路径"
  - "知识固化: 每次踩坑后把经验写入skill或memory，避免重复犯错"
  - "安全清理OpenOCD: 只用 `pgrep -f openocd && kill -9 $(pgrep -f openocd)` 清理。禁止 `fuser -k` 加在同一组进程上——fuser的SIGKILL会传播到shell自身，杀死当前terminal进程"
  - "flash写入错误 = 需要物理复位: 中断flash写入后MCU SWD会被锁定，无法通过软件恢复。唯一可靠方案是按板子上的复位键。不接受在同一个死循环里再试10次"
  - "对照实验: 怀疑回归时烧回旧版本固件验证，确认根因再修"
---

# RTT CUAV V5 固件烧录与验证工作流

## 🛫 前置检查清单（2026-05-11 更新）

### sudo NOPASSWD
Kanban gateway workspace 初始化时执行 `sudo /usr/bin/true` 做系统能力检测。
OpenOCD 本身不需要 sudo，但 gateway 需要。

```bash
sudo -n true && echo "✅ sudo NOPASSWD 已配置" || echo "❌ 需要配置"
echo 'llw ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/llw-nopasswd
```

### ⚠️ 关键：杀死僵死 OpenOCD 进程（2026-05-11 新增 - 导致SWD静默失败的根因！）
> 僵死的 OpenOCD 进程会锁定 ST-Link 设备，后续 OpenOCD 连接时显示"clock speed 2000 kHz"后静默挂死退出。
> 这不是 SWD 排线问题，不是目标板断电，只是一个遗留进程占着设备。

```bash
# 必须用 pkill -9（SIGKILL），SIGTERM 可能杀不干净
pkill -9 openocd 2>/dev/null; sleep 3
ss -tlnp | grep -E "3333|4444|6666" || echo "✅ ports free"

# 验证 ST-Link 可访问
lsusb | grep "0483:3748" && echo "✅ ST-Link 设备在线" || echo "❌ ST-Link 不在线"

# 如果 pkill 后 pyOCD/OpenOCD 仍无法连接 → 尝试 xhci_hcd USB 总线复位
sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/unbind'
sleep 2
sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/bind'
sleep 5
```

> **根因回顾**（2026-05-11）：OpenOCD 进程在 tty 关闭时（如 kanban worker 超时退出）成为僵尸进程，继续持有 ST-Link USB 设备的锁定。新 OpenOCD 进程会静默失败，仅显示"clock speed"后不报错退出。如果不清理进程，你会认为是 SWD 硬件问题、排线松动、目标板未上电——全都是误导方向。**第一步永远是检测并清理旧进程。**

### ST-Link 设备检测
```bash
lsusb | grep "0483:3748" && echo "✅ ST-Link/V2 已连接" || echo "❌ 未检测到 ST-Link"
```

### 确认硬件连接
- ST-Link V2 SWD: SWDIO, SWCLK, GND
- CUAV V5 USB 连接电脑（用于 CDC 验证）
- 禁止物理断电（只能用 OpenOCD reset init）

## 编译
```bash
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
```
禁止 waf。输出在 `build/rtt_cuav_v5/rtthread.bin`（~1.3MB）。
ROM 目标 ≈84%，RAM ≈62%（512KB 总 RAM）。

### ⚠️ 构建缓存污染陷阱（2026-05-09）

修改源代码后重新编译，但二进制可能没有变化。原因是构建缓存污染：

```bash
# 诊断：检查修改是否生效
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "disassemble rt_hw_board_init" 2>&1 | grep "rt_components"
# 如果有输出 → 你的修改未生效，构建缓存未刷新

# 根治：删除整个构建缓存，重新编译
rm -rf build/rtt_deploy/ build/rtt_cuav_v5/
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
```

**常见场景**：`git checkout -- .` 清理工作区后，`build/rtt_deploy/` 中仍有旧版副本，SCons 认为时间戳未变 → 跳过重编译。**每次大规模改动后都建议 clean 重新编译**。

## 烧录

### ⚡ Flash 写入速度关键（2026-05-16 新增）

`adapter speed 200` 下 `flash write_image` 会报 `timeout waiting for algorithm`。原因：STM32F7 flash 编程算法在低速 SWD 下运行时耗时长到超时。

**正确做法：halt/reset 用低速，flash 写入用高速：**

```bash
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "transport select hla_swd" \
  -c "reset_config srst_only srst_nogate connect_assert_srst" \
  -c "adapter srst pulse_width 100" \
  -c "adapter speed 200" \        # 低速连接/halt
  -c "init" \
  -c "halt" \
  -c "adapter speed 1800" \       # 升速（自动协商到1800kHz）
  -c "flash write_image rtthread.bin 0x08008000" \
  -c "verify_image rtthread.bin 0x08008000" \
  -c "adapter speed 200" \        # 恢复低速再reset
  -c "reset run" \
  -c "shutdown"
```

### 🔴 重复 app_descriptor 陷阱（2026-05-16 发现）

- `references/chibios-bootloader-Firmware-validation-reference.md` | (🆕) ChibiOS bootloader 固件验证逻辑参考 (get_app_descriptor, check_good_firmware_unsigned, 常量汇总)
- `references/duplicate-app-descriptor-debug.md` | (更新) 重复 app_descriptor 分析与 bootloader 扇区擦除陷阱
### 💥 OpenOCD `program` 命令（推荐 — 2026-05-12 新增）\n\n对于 STM32F765 2MB flash，`flash write_bank` 带 offset 会失败（\"out of range\"），\n但 `program` 命令自动处理扇区擦除+写入+验证：\n\n```bash\nopenocd -f Tools/debug/openocd-f7.cfg \\\n  -c \"init; reset halt; program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify; reset run; exit\"\n```\n\n**与 `flash write_bank` 对比**：\n| 命令 | STM32F765 2MB | 扇区处理 |\n|------|---------------|---------|\n| `program bin 0x08008000 verify` | ✅ 工作 | 自动擦除所需扇区 |\n| `flash write_bank 0 bin 0x08008000` | ❌ `out of range` | 需手动 `erase_sector` |\n\n**扇区布局验证**：STM32F765 2MB 单 bank：sector 0-3 (32KB) + sector 4-11 (128KB) = 共 12 sectors。\n`flash erase_sector 0 1 11` 是正确擦除范围。sector index max = 11（不是 18，之前踩过坑）。\n\n### 🔥 pyOCD 烧录（推荐 — 2026-05-11 新增）

pyOCD 0.44.1 已安装，比 OpenOCD 更可靠（不受僵死 OpenOCD 进程影响）：

```bash
# 列出可用探针
pyocd list

# 烧录二进制固件
pyocd load -t STM32F767ZI --format bin -a 0x08008000 \
  /data/firmare/pogo-apm/build/rtt_cuav_v5/rtthread.bin

# 烧录后验证
pyocd commander -t STM32F767ZI -c "read32 0x08008000 4; reset; exit"
# 期望: 0x2000xxxx（有效栈指针）
```

**优势对比**：

| 方面 | OpenOCD | pyOCD |
|------|---------|-------|
| 受僵死进程影响 | ❌ 被锁住后静默失败 | ✅ 独立连接，不受影响 |
| 烧录速度 | 快（~15s） | 中等（~30s，逐扇区擦写） |
| 调试功能 | ✅ 完整GDB server | ✅ 完整commander/GDB |
| 自动速度协商 | ⚠️ 有时需手动降频 | ✅ 自动100kHz低速适配 |
| 安装状态 | 系统级（不需要pip） | pip3已安装，随时可用 |

### ⚠️ 前置检查：检测 stale OpenOCD 进程（新增：SWD失败的TOP1根因）

flash write 失败（输出"wrote 0 bytes"或 flash 仍是 `0xFFFFFFFF`）的第一检查项。

```bash
# 检测所有 openocd 进程
ps aux | grep -v grep | grep openocd
```

**关键区分**：stale OpenOCD 可能使用**完全不同的配置**运行，不仅限于同一 config 的重复实例：

| 场景 | 进程 | 影响 |
|------|------|------|
| `Tools/debug/openocd-f7.cfg`（正确配置） | `openocd -f Tools/debug/openocd-f7.cfg` | 正常 |
| `interface/stlink.cfg`（不同配置） | `openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c adapter speed 1000` | 占用 ST-Link，新 openocd 报"couldn't bind tcl" |
| shell 残留进程 | `/usr/bin/bash -lic openocd ...` | 同上 |

**稳定流程**：
```bash
# 1. 暴力清理所有 OpenOCD 进程
pkill -9 openocd 2>/dev/null; sleep 3

# 2. 确认端口释放
ss -tlnp | grep -E "4444|6666" || echo "ports free"

# 3. 启动后台 OpenOCD
openocd -f /data/firmare/pogo-apm/Tools/debug/openocd-f7.cfg &
sleep 5

# 4. 确认可连接（必须看到 ST-Link 信息）
ss -tlnp | grep 4444 && echo "✅ OpenOCD ready"
```

**注意**：`pkill openocd` (SIGTERM) 可能不会终止所有实例（尤其是不同 config 的）。用 `pkill -9 openocd` (SIGKILL) 强制清理。

### 烧录通过 telnet 的超时控制

通过 `nc` 发送 flash 命令时，各操作的时间预算：

| 操作 | 耗时 | nc -q 要求 | sleep 要求 |
|------|------|-----------|-----------|
| `reset halt` | ~2s | - | sleep 2 |
| `flash erase_sector 0 1 11` | ~18s | - | sleep 20 |
| `flash write_image` | ~14s | - | sleep 20 |
| 总计 | ~36s | `-q 40` | - |

**不稳定因素**：
- ST-Link V2 在 2000kHz 可能速度不匹配，降频到 1800kHz（自动协商）
- 长时间 telnet 连接可能被中间件断开（用 `timeout N` 外层包裹）
- `write_image` 有时不输出"wrote"行但实际已写入 → 用 `mdw 0x08008000 4` 验证向量表

**验证 flash 写入是否成功**（烧录后必须做）：
```bash
echo "mdw 0x08008000 4" | nc -q 3 localhost 4444
# 期望: 0x2000xxxx 0x080eexxx ... （非 0xFFFFFFFF）
```

**如果写入失败（0xFFFFFFFF）** → 检查 stale OpenOCD 进程，清理后重试。

## 🚨 闪存布局陷阱 — 2026-05-08 重大修正

**`rtthread.bin` 的 VMA 基址是 `0x08008000`（`.text` 段），不是 `0x08000000`。**  
BIN 文件内容从 VMA 0x08008000 开始。BOOTLOADER 在 `0x08000000-0x08007FFF`（32KB），是**单独烧录的**。

⚠️ **2026-05-08 故障复盘**: 之前错误地认为 `rtthread.bin` 从 `0x08000000` 开始（"完整镜像"），使用 `flash write_image ... 0x08000000` 烧录后，所有代码在 flash 中偏移了 32KB。结果是：
- 向量表在 flash[0x08000000] 有效（Reset=0x080EE841）
- 但 Reset_Handler 代码在 flash[0x080E6841]，而 MCU 从 flash[0x080EE841] 取指
- 读取错误指令 → 表现为各种随机 HardFault（AP_GPS_Blended::calc_state(this=0x33) 等）
- 耗费数小时在代码 bug 分析上，实际是烧录地址错误

**永远用这两步：**

```bash
# Step 1: 烧 bootloader 到 0x08000000（如果已有可跳过）
### 🚨 关键：CUAV V5 Bootloader 正确路径

**仓库路径**: `Tools/bootloaders/CUAVv5_bl.bin`（注意大写！）  
**烧录地址**: `0x08000000`（占用 16KB，即扇区 0）  
**文件大小**: ~16KB（16440 bytes）  
**格式**: 原始二进制，非 HEX

**常见错误**（如下，已踩过坑）：
- ❌ 用小写 `cuav-v5-bl.bin` → 文件不存在
- ❌ 用 `CUAVv5_bl.hex` → HEX 格式需要不同烧录方式
- ❌ 自行编写 boot stub（手工汇编）→ 栈在 FLASH 中→ 立即 HardFault
- ❌ 在不擦除 bootloader 的情况下写 app 到 0x08000000 → 覆盖 bootloader

### 🔴 致命陷阱：Runtime flash 边界检查误用 SRAM 地址（2026-05-17 发现）

**症状**：固件烧录后 USB CDC 不枚举。GDB halt 显示 PC 在 `rt_hw_board_init` 的 `while(1){wfi}` 死循环中。
`setup_stage=0`, `hal_run_called=0xdeadbeef`（已进入 `HAL_RTT::run()`），但系统在 board init 后无后续动作。

**根因**：`hwdef/common/board/flash_check.c` 中的 `rtt_flash_boundary_check()` 计算 `flash_end` 时使用了 `_edata` 和 `_ebss`：

```c
// ❌ 错误：
flash_end = (uint32_t)_etext;
if ((uint32_t)_edata > flash_end) flash_end = (uint32_t)_edata;  // _edata=0x2002156c (SRAM!)
if ((uint32_t)_ebss  > flash_end) flash_end = (uint32_t)_ebss;   // _ebss=0x20069ed4 (SRAM!)
```

`_edata` 和 `_ebss` 是 **VMA (SRAM) 地址**（0x200xxxxx），不是 flash 地址！
SRAM 地址远大于 `APP_MAX_END`（0x09FF8000）→ 检查 2 失败 → `return 1` → 系统 halt。

**修复**：使用 `_sidata`（flash 中 .data 初始化数据的 LMA）计算真正的 flash 图像终点：

```c
// ✅ 正确：
flash_end = (uint32_t)_sidata + ((uint32_t)_edata - (uint32_t)_sdata);
```

- `_sidata` = .ARM.exidx 结束处，即 .data 初始化数据在 flash 中的起始地址
- `_edata - _sdata` = .data 段大小（VMA，与 LMA 相同）
- 所以 `_sidata + (_edata - _sdata)` = flash 中图像的真实结束地址

**验证**：重新编译后 `flash_end` 应在 flash 范围内（如 `0x08152790` 远小于 `0x09FF8000`）。

**调试定位**：GDB halt 后 PC 在 `rt_hw_board_init` 的 `while(1){__asm volatile("wfi");}` 中，检查 `rtt_dbg_setup_stage=0` 且 `hal_run_called=0xdeadbeef` → flash 边界检查失败。

---

### 🔴 致命陷阱：Bootloader 扇区未擦除就写入（2026-05-16 发现 — 导致系统完全不启动的 TOP1 根因）

**症状**：Bootloader + app 均烧录验证通过（`verify_image` 返回 OK），但 `reset run` 后 USB CDC 完全不重新枚举。OpenOCD `halt` 显示 **HardFault**（CFSR=0x00028200: PRECISERR+INVSTATE, BFAR=0x00004000），PC 在 0x2000002e（RAM 中无效地址）。

**根因**：`flash write_image <bootloader.bin> 0x08000000`（**不带** `erase` 标志）不自动擦除目标扇区。如果该扇区已有旧数据，**仅有写入的 16440 字节被覆盖**，扇区剩余部分仍保留旧数据，导致 bootloader 的芯片初始化代码在执行时读取到残留的错误数据，立即 HardFault。

验证方法：
```bash
# 擦除后先写 bootloader，再写 app，两个都要写
openocd -c "flash erase_address 0x08000000 0x10000"  # 擦除 64KB
```

**关键行为**：
- `flash erase_address 0x08000000 0x10000` 擦除 64KB（0x08000000-0x0800FFFF）
- 这覆盖了 bootloader 区域（0x08000000-0x08003FFF，16KB）**和** app 的前 32KB（0x08008000-0x0800FFFF）
- 因此**擦除后必须同时重新写入 bootloader + app**，缺一不可
- 🔴 `flash write_image erase` 也会擦除（mass erase），但会擦除包括 app 描述符在内的所有扇区，且速度更慢

**正确烧录流程（2026-05-16 已验证）**：
```bash
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "transport select hla_swd" \
  -c "reset_config srst_only srst_nogate connect_assert_srst" \
  -c "adapter srst pulse_width 100" \
  -c "adapter speed 200" \
  -c "init" \
  -c "halt" \
  -c "adapter speed 1800" \
  -c "flash erase_address 0x08000000 0x10000" \
  -c "flash write_image /data/firmare/pogo-apm/Tools/bootloaders/CUAVv5_bl.bin 0x08000000" \
  -c "flash write_image /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rtthread.bin 0x08008000" \
  -c "verify_image /data/firmare/pogo-apm/Tools/bootloaders/CUAVv5_bl.bin 0x08000000" \
  -c "verify_image /data/firmare/pogo-apm/build/rtt_deploy/cuav_v5/rtthread.bin 0x08008000" \
  -c "reset run" \
  -c "shutdown"
```

### ✅ 正确烧录流程

```bash
# 使用 OpenOCD 单次命令烧录
openocd -f /data/firmare/pogo-apm/Tools/debug/openocd-f7.cfg \
  -c "init" \
  -c "flash write_image erase /data/firmare/pogo-apm/Tools/bootloaders/CUAVv5_bl.bin 0x08000000" \
  -c "flash verify_image /data/firmare/pogo-apm/Tools/bootloaders/CUAVv5_bl.bin 0x08000000" \
  -c "flash write_image /data/firmare/pogo-apm/build/rtt_cuav_v5/rtthread.bin 0x08008000" \
  -c "flash verify_image /data/firmare/pogo-apm/build/rtt_cuav_v5/rtthread.bin 0x08008000" \
  -c "reset run"
```

**验证 bootloader 写入正确**：
```bash
echo "mdw 0x08000000 2" | nc -q 1 localhost 4444
# 期望: 0x20020400 0x08000201 (bootloader 向量表)
echo "mdw 0x08008000 2" | nc -q 1 localhost 4444
# 期望: 0x20005424 0x080ee121 (固件向量表)
```
```

**验证 flash 布局正确性（烧录后立刻做）：**
```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "x/4xw 0x08000000" \
  -ex "x/4xw 0x08008000" \
  -ex "quit"
```
- `0x08000000`: SP≈0x2002xxxx, Reset≈0x08000201（bootloader 向量表）
- `0x08008000`: SP≈0x2000xxxx, Reset≈0x080EExxx（固件向量表）

**如果你之前用了 `flash erase_sector 0 0 last` 擦掉了 bootloader，** 必须先恢复 bootloader 再烧固件。bootloader 文件: `Tools/bootloaders/cuav-v5-bl.bin`。

**绝对不要：**
- ❌ `flash erase_sector 0 0 last` → 会擦掉 bootloader
- ❌ `flash write_image ... 0x08000000`（不配合 bootloader）→ 代码偏移 32KB
- ❌ 擦完 sectors 0-3 后只写固件到 0x08000000 → 向量表在但代码错位

**PC=0xFFFFFFFE 是flash空白的典型症状**（SP从0x00000000加载=0xFFFFFFFF → MSP=0xFFFFFFFC → PC跳到0xFFFFFFFE）。

### 🔄 Bootloader 上传法（当 OpenOCD SWD 连接失败时的备用方案，2026-05-09 新增）

**适用场景**: OpenOCD 报 `Error: init mode failed (unable to connect to the target)`，
ST-Link 能检测电压但无法通过 SWD 通信。此时 CUAV V5 bootloader 提供了干净的替代烧录路径。

**操作流程**:

1. **用户物理操作**: 按 CUAV V5 板的 BOOT 按钮（或拉高 BOOT0），然后复位或上电
2. **USB 枚举**: 板子进入 boot 模式后，USB 枚举为 `1209:5741 Generic CUAVv5-BL`
3. **转换 .bin → .apj**: uploader.py 需要 .apj（JSON格式）而非原始 .bin
   ```bash
   cd /data/firmare/pogo-apm
   python3 Tools/scripts/rtt_bin_to_apj.py build/rtt_cuav_v5/rtthread.bin
   # 输出: build/rtt_cuav_v5/rtthread.apj
   ```
4. **烧录**:
   ```bash
   cd /data/firmare/pogo-apm
   python3 Tools/scripts/uploader.py --port /dev/ttyACM1 build/rtt_cuav_v5/rtthread.apj
   ```
   等待 Erase → Program → Verify → Reboot 完成（约 60s）。

5. **验证**: 重启后 USB 枚举为 `1209:5741 Generic CUAVv5 RTT`，MAVLink 可用

**优缺点对比**:

| 方法 | 优点 | 缺点 |
|------|------|------|
| OpenOCD SWD (telnet/GDB) | 可 halt MCU、读内存、断点调试 | SWD可能被固件状态锁定 |
| Bootloader 上传 (uploader.py) | 不受 MCU 状态影响，干净烧录 | 需要用户物理操作 boot 按钮，无调试能力 |

**关键判断**: 当 OpenOCD 连接失败时，**不要浪费时间诊断 SWD 问题**。直接请求用户按 BOOT 按钮 + 复位，走 bootloader 上传路径。烧录完成后 SWD 通常恢复正常（clean flash 解除 debug 锁定）。

**常见问题**:
- ACM1 端口被占用（如 QGroundControl）→ `fuser -k /dev/ttyACM1` 或 `kill` 占用进程
- **ACM 端口号可能变化**：bootloader 模式下端口为 `/dev/ttyACM1`，但应用固件启动后可能枚举为 `/dev/ttyACM2`（取决于 CH552 调试口和 USB CDC 的枚举顺序）。**始终用 `ls -la /dev/ttyACM*` 检查时间戳确定最新端口**。
- bootloader 不跳转到应用 → 烧录后自动 reboot，用户也可手动复位

### GDB load 法（推荐，自动处理地址）
```bash
arm-none-eabi-gdb -batch -nx \
  -ex "target extended-remote :3333" \
  -ex "monitor reset halt" \
  -ex "load build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "monitor reset init"
```

### OpenOCD telnet 法（bin全量写入） ⚠️ 必须先确定扇区范围

**CUAV V5 (STM32F767, 2MB Flash) 扇区布局：**
| 扇区 | 地址范围 | 大小 | 用途 |
|------|---------|------|------|
| 0 | `0x08000000-0x08007FFF` | 32KB | Bootloader（不擦除） |
| 1-3 | `0x08008000-0x0801FFFF` | 32KB×3 | **固件起始区域** |
| 4 | `0x08020000-0x0803FFFF` | 128KB | 固件区域 |
| 5-7 | `0x08040000-0x080BFFFF` | 256KB×3 | 固件区域 |
| 8-11 | `0x080C0000-0x081FFFFF` | 256KB×4 | 固件区域（2MB尾部） |

**固件自 `0x08008000` 开始**，1.3MB 固件跨越扇区 1-8。擦除时**必须覆盖所有固件占用扇区**。

⚠️ **第一次烧录容易犯的错误**：只擦除 sectors 4-11 而漏掉 sectors 1-3。结果旧固件的向量表还在 0x08008000，新写的固件数据与旧数据重叠，写完成后 `verify_image` 发现大量 0xe8 vs 0xe9 类单比特差异（flash 只能 1→0，未擦除的比特无法从 0→1）。

```bash
# ✅ 正确：擦除 sectors 1-11（跳过 bootloader 的 sector 0）
echo "reset halt" | nc -q2 localhost 4444
echo "flash erase_sector 0 1 11" | nc -q20 localhost 4444
echo "flash write_image /path/to/rtthread.bin 0x08008000" | nc -q30 localhost 4444
echo "reset run" | nc -q1 localhost 4444

# ❌ 错误：只擦除 sectors 4-11 → 漏掉 sectors 1-3，固件写入后向量表不更新
```

**烧录后验证（通过 telnet 读向量表）：**
```bash
echo "mdw 0x08008000 4" | nc -q 2 localhost 4444
# 期望: 0x2000xxxx 0x080eexxx 0x080eexxx 0x08008xxx
# 与 bin 文件对比:
xxd -l 16 build/rtt_cuav_v5/rtthread.bin
# bin 的字节序是小端，对照验证：
# bin[0-3] = 4c34 0020 → 0x2000344c (SP)
# bin[4-7] = 21e9 0e08 → 0x080ee921 (Reset handler)
```

**⚠️ `flash write_bank` 偏移量陷阱（2026-05-12 发现）**：
`flash write_bank <bank_id> <file> <offset>` 的 offset 是 **bank-相对**偏移，不是绝对地址：
```bash
# ❌ 错误（绝对地址）
flash write_bank 0 rtthread.bin 0x08008000
# → "Offset 0x08008000 is out of range of the flash bank"

# ✅ 正确（bank-相对偏移）
flash write_bank 0 rtthread.bin 0x8000
```
`program` 命令和 `flash write_image` 使用绝对地址，不受此限制。仅 `write_bank` 使用 bank-相对偏移。

## 🚨 2026-05-09 踩坑记录：Boot Stub 灾难

**不要自己写 bootloader / boot stub！永远从 `Tools/bootloaders/` 目录使用现成的二进制！**

### 事故经过
烧录过程中 MCU 显示 PC=0xFFFFFFFE（flash 空白）。错误地以为 bootloader 被永久擦除且仓库中没有 bootloader 二进制文件。

### 实际发生了
1. bootloader 确实被擦除了（前一次 `flash write_image erase ... 0x08000000` 的副作用）
2. **bootloader 二进制文件确实在仓库中**：`Tools/bootloaders/CUAVv5_bl.bin`（注意大写！）
3. 手工写了一个 boot stub 汇编来跳转到固件，但：
   - **栈指针设置错误** — BSS 段在 FLASH 中（不可写），栈在 FLASH 中直接 HardFault
   - **向量表地址计算错误** — 导致 PC=0x6840d000（指令编码的垃圾值）
   - 浪费了 3 轮烧录时间在调试自己的 boot stub 上

### 教训
1. **先找仓库中现有文件** — `find Tools/bootloaders -name "*CUAV*bl*"` 
2. **文件名大小写敏感** — `cuav-v5-bl.bin` ≠ `CUAVv5_bl.bin`
3. **从不在嵌入式项目中手写启动代码** — 除非有硬件原理图+数据手册+汇编参考书在手
4. **问题：找不到文件怎么办？** → 去上游仓库下载，或使用 build system 生成，**绝不自己写汇编**

### ⚠️ MAVLink 2 帧对齐陷阱（2026-05-16 发现）

**症状**：用 MAVLink 1（0xFE）帧头解析 `/dev/ttyACM1` 时，只收到 `len=1 payload=57` 的"假心跳" —— 实际是 MAVLink 2 流中碰巧出现的 0xFE 字节被误判为帧头。

**根因**：RTT ArduPilot 输出的是 **MAVLink 2 协议**（帧头字节 `0xFD`），而非 MAVLink 1（`0xFE`）。CUAV V5 的 `SERIAL_ORDER OTG1` 配置编译进 MAVLink 2 输出。

**诊断方法**：
```python
# 扫原始流判断协议版本
import serial, struct
ser = serial.Serial('/dev/ttyACM1', 921600, timeout=0.5)
data = ser.read(200)
fd_count = data.count(b'\xfd')
fe_count = data.count(b'\xfe')
print(f"0xFD(MAV2): {fd_count}, 0xFE(MAV1): {fe_count}")
# 0xFD 应远多于 0xFE
```

**正确 MAVLink 2 解析**：
```python
import serial, struct
ser = serial.Serial('/dev/ttyACM1', 921600, timeout=1)
data = ser.read(2000)
i = 0
while i < len(data) - 12:
    if data[i] != 0xFD:    # ← MAVLink 2 magic, NOT 0xFE!
        i += 1
        continue
    plen = data[i+1]
    total = 12 + plen + 2   # header(10) + payload + CRC(2)
    if i + total > len(data): break
    msgid = struct.unpack('<I', data[i+7:i+10] + b'\x00')[0]
    p = data[i+12:i+12+plen]
    if msgid == 0 and plen >= 11:   # HEARTBEAT
        mav_type, autopilot, base_mode = struct.unpack('<BBB', p[:3])
        print(f'HB: type={mav_type} autopilot={autopilot} base_mode=0x{base_mode:02x}')
    i += total
```

**MAVLink 2 HEARTBEAT 字段布局**（len=11）：
| 偏移 | 字段 | 大小 | 说明 |
|------|------|------|------|
| 0 | type | 1 | MAV_TYPE (0=GENERIC, 2=QUADROTOR) |
| 1 | autopilot | 1 | MAV_AUTOPILOT (0=GENERIC, 3=ARDUPILOT) |
| 2 | base_mode | 1 | 0x00=STANDBY, 0x81=ACTIVE |
| 3 | custom_mode | 4 | 特定板模式 |
| 7 | system_status | 1 | MAV_STATE |
| 8 | mavlink_version | 1 | 协议版本 (2) |

**关键区别**：MAVLink 2 帧头比 MAVLink 1 多 2 字节（`incompat_flags` + `compat_flags`），msgid 从 1→3 字节。使用 `pymavlink` 自动处理两者差异。手动解析必须根据 `magic==0xFD` 走 MAVLink 2 路径。

> 🔴 **陷阱**：`rtt_dbg_*` 变量地址随编译变化。每次重建后必须用 `nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep rtt_dbg` 确认地址。BSS 段偏移导致 `setup_stage`(0x2001bc84→0x2001bd0c) 和 `loop_iter`(0x20019980→0x20019a24) 的地址——用错误地址读取会得到无关 RAM 数据。

### ⚠️ MAVLink 读取最佳实践（2026-05-09 新增）

**推荐使用 `mavutil.mavlink_connection()` 而非 raw `parse_char()`**：

```python
import pymavlink.mavutil as mavutil

c = mavutil.mavlink_connection('/dev/ttyACM1', baud=921600)
h = c.wait_heartbeat(timeout=8)        # 可靠阻塞等待
m = c.recv_match(blocking=True, timeout=0.5)  # 逐条读取
```

**避免原始 `parse_char()` 逐字节解析**，原因：
- CRC 错误会抛出异常 `MAVError("invalid MAVLink CRC")` 终止解析
- 逐字节循环在高波特率下极慢（921600 baud = 92000 bytes/sec）
- 每次 reset MAVLink 解析器都会丢失状态
- 用 `try/except` 包裹只能缓解不能根治

**连接被占用时的处理**：
- `Device or resource busy` → `fuser -k /dev/ttyACM1` 或杀 QGroundControl
- `device reports readiness to read but returned no data` → 端口被占用后断开，需重启或重连

### 第一步：MCU 运行 + CDC
```bash
# 完整复位
echo "reset" | nc -q1 localhost 4444
# 等待 USB 枚举 (10-15s)
# CDC 可能枚举到 ttyACM1 而不是 ttyACM0！
ls -la /dev/ttyACM*  # 看时间戳确认最新的
```

### 第二步：MAVLink 心跳
```bash
timeout 10 python3 -c "
import pymavlink.mavutil as m
c = m.mavlink_connection('/dev/ttyACM1', baud=921600)
h = c.wait_heartbeat(timeout=8)
print(f'HB: status={h.system_status} type={h.type}' if h else 'NO_HB')
"
```
期望: `system_status=3 (STANDBY)` — 系统活着且就绪。

### 第三步：传感器数据
```bash
timeout 15 python3 -c "
import pymavlink.mavutil as m
c = m.mavlink_connection('/dev/ttyACM1', baud=921600)
c.wait_heartbeat(timeout=5)
for i in range(10):
    r = c.recv_match(blocking=True, timeout=1)
    if r:
        t = r.get_type()
        if t in ('RAW_IMU','SYS_STATUS','EKF_STATUS_REPORT','STATUSTEXT'):
            print(f'{t}: {r}')
"
```
检查关键指标:
- **RAW_IMU**: zacc ≈ -1000 (1G 重力)
- **EKF_STATUS**: flags=167 (包含 attitude + velocity + pos_horiz)
- **SYS_STATUS**: load < 2000, unhealthy 只应有 MAG/GPS(没接)
- **STATUSTEXT**: "Loop: XXX/s timeavail=X"

### 第四步：传感器健康检查（取代 pymavlink 的原始帧法）

当 `pymavlink` 因 `_instances=None` bug 或其他问题无法工作时，用直接二进制解析：

```python
import serial, struct, time
ser = serial.Serial('/dev/ttyACM1', 921600, timeout=1)
ser.reset_input_buffer()
time.sleep(0.3)
data = ser.read(4000)
ser.close()

i = 0
health_data = {}
while i < len(data) - 12:
    if data[i] != 0xFD: i += 1; continue
    plen = data[i+1]
    total = 12 + plen + 2
    if i + total > len(data): break
    msgid = struct.unpack('<I', data[i+7:i+10] + b'\x00')[0]
    p = data[i+12:i+12+plen]
    
    if msgid == 1 and len(p) >= 12:  # SYS_STATUS
        pres = struct.unpack('<I', p[0:4])[0]
        hlth = struct.unpack('<I', p[8:12])[0]
        bits = [('gyro',1),('accel',2),('mag',4),('pressure',8),('gyro2',16)]
        unhealthy = [n for n,b in bits if (pres&b) and not (hlth&b)]
        print(f"SYS_STATUS: unhealthy={unhealthy}")
    elif msgid == 29 and len(p) >= 14:  # SCALED_PRESSURE
        pabs = struct.unpack('<f', p[4:8])[0]
        print(f"BARO: {pabs:.1f}hPa")
    elif msgid == 27 and len(p) >= 20:  # RAW_IMU
        xa, ya, za = struct.unpack('<hhh', p[8:14])
        xg, yg, zg = struct.unpack('<hhh', p[14:20])
        print(f"IMU1: acc=({xa},{ya},{za}) gyro=({xg},{yg},{zg})")
    elif msgid == 116 and len(p) >= 22:  # SCALED_IMU2
        xa, ya, za = struct.unpack('<hhh', p[4:10])
        print(f"IMU2: acc_mag={(xa*xa+ya*ya+za*za)**0.5:.0f}")
    i += total
```

**传感器健康判定标准**：
- RAW_IMU zacc ≈ -1000（1G），各轴不出现 `-999`（-999 = SPI 读失败）
- IMU zacc == 0 且 x/y/z 均 0 = **传感器未初始化或主循环未运行**（比出现 -999 更严重，说明 init 阻塞）
- SCALED_PRESSURE abs > 900hPa（正常气压），temp > -50°C
- SYS_STATUS health 位：GYRO/ACCEL/PRESSURE 应为 HEALTHY
- SCALED_IMU2 acc_mag ≈ 1000（BMI055 正常）
- **没有 SYS_STATUS 消息** = 主循环根本没运行，可能 init 阶段卡住了

### 第五步：长时间稳定性（可选）
```bash
timeout 60 python3 /tmp/mavlink_check.py  # 跑一分钟
```
稳定标准: 无 panic, 无重启, EKF hpos_var 持续收敛

详细参考 `references/mavlink-sensor-diagnostics.md`（含完整 SYS_STATUS 字段布局和位掩码表）。

## 自动健康监控（cron 自愈）

cron job `bdd86609b340` 每5分钟运行一次自动检测+修复流程。

### 检测链

```
MCU 存活 (OpenOCD) → CDC端口 (ACM0+ACM1) → MAVLink心跳 → 传感器数据 → 健康掩码
```

### 自动修复策略

| 检测失败 | 修复动作 |
|---------|---------|
| MCU 失联 | 重启 OpenOCD (`pkill openocd; openocd -f ...`) |
| CDC 端口丢失 | 复位 MCU (`echo "reset" | nc localhost 4444`) |
| 无心跳 | 复位 MCU + 等待20秒重试 |
| 气压计连续6次(30分钟)失败 | 自动重新编译+烧录 |

### 自愈脚本

`~/.hermes/scripts/rtt_health_monitor.py`

原理: 无外部依赖，通过 OpenOCD telnet + MAVLink 协议检测，逐级诊断并自动恢复。

### 状态文件

`/tmp/rtt_health_state.json` 跟踪连续失败计数，避免过度修复。

### MCU 完全锁死恢复（SWD 连接失败）

**症状**: OpenOCD 报 `Error: init mode failed (unable to connect to the target)`，

#### 🚨 铁律：先软件恢复，绝不建议物理插拔
调试只能用 OpenOCD reset init 或软件 NVIC reset。如果复位后 USB CDC 无法连接，必须是代码 bug 修复而非物理插拔。详情见 `rtt-stabilization-driver` 技能中的 `references/usb-recovery-xhci.md`。

#### 0. xhci_hcd PCI unbind/rebind（首试方案 — 6 秒完成）
当 ST-Link (0483:3748) 从 USB 总线上完全消失（`lsusb` 无输出）时，这是**唯一纯软件恢复方法**：

```bash
sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/unbind'
sleep 2
sudo sh -c 'echo "0000:00:14.0" > /sys/bus/pci/drivers/xhci_hcd/bind'
sleep 5
lsusb | grep "0483:3748"  # 应恢复
```

xhci_hcd 复位会杀掉所有 USB 进程（含 OpenOCD），恢复后需：
```bash
pkill -9 openocd 2>/dev/null; sleep 3
openocd -f /data/firmare/pogo-apm/Tools/debug/openocd-f7.cfg &
sleep 5
ss -tlnp | grep 4444  # 确认 OpenOCD 就绪
```
ST-Link 能检测到电压（~3.25V）但无法通过 SWD 连接。MAVLink 也无心跳。

**根因**: 在 flash 写入过程中 `kill -9` OpenOCD，损坏 flash 内容。
MCU 从损坏的 flash 启动，执行了锁定 SWD 接口的错误代码。

**恢复流程（按顺序尝试，每种都确认失败再试下一个）**:

#### 1. ioctl USBDEVFS_RESET（不需要 sudo，最推荐）
```python
import fcntl, os
USBDEVFS_RESET = 0x5514
for root, dirs, files in os.walk('/dev/bus/usb'):
    for f in files:
        path = os.path.join(root, f)
        try:
            fd = os.open(path, os.O_RDWR)
            buf = os.read(fd, 18)
            idVendor = buf[8] | (buf[9] << 8)
            idProduct = buf[10] | (buf[11] << 8)
            if idVendor == 0x0483 and idProduct == 0x3748:
                fcntl.ioctl(fd, USBDEVFS_RESET)
                print(f'Reset ST-Link at {path}')
            os.close(fd)
        except: pass
```
然后立即 `openocd -f Tools/debug/openocd-f7.cfg`

#### 2. USB authorized toggle（不需要 sudo，但需要写入权限）
```bash
echo 0 > /sys/bus/usb/devices/1-2.1/authorized
sleep 8
echo 1 > /sys/bus/usb/devices/1-2.1/authorized
sleep 5
openocd -f Tools/debug/openocd-f7.cfg
```

#### 3. SRST 硬件复位（如果 NRST 线已连接）
见 `references/openocd-srst-config.md`

#### 4. st-flash / stlink-tools
安装 stlink-tools 后用 `st-flash --reset` 尝试连接（不同的 ST-Link 协议实现）。

#### 5. DFU 模式（需要 BOOT0 按键）
按 BOOT0 + 复位进入系统内存 bootloader，`dfu-util -a 0 -s 0x08000000:leave -D firmware.bin`

#### 6. 识别死局
如果以上全部失败（特别是所有 USB 操作都返回 `Permission denied`），**及时止损**。
此时已无纯软件方案可用——USB 控制需要 root，SWD 连不上，MCU 在不可恢复状态。

**向用户诚实汇报**:
- 说明导致问题的具体原因（kill -9 中断了 flash 写入）
- 列出已尝试的所有恢复方法（证明已尽力）  
- 请求sudo权限或受控物理复位的批准

**不要**:
- 在相同工具链上再循环尝试（OpenOCD→pyOCD→stlink 再循环）
- 尝试不掌握协议的原始 USB 命令（可能损坏 ST-Link 固件）
- 自己打哑谜不汇报（浪费用户时间）

### 快速诊断: flash空白 vs 代码崩溃 vs bootloader正常

`reset halt` 后检查 PC 值即可准确判断故障范围:

| PC值 | 诊断 | 解决方法 |
|------|------|----------|
| `0xFFFFFFFE` / `SP=0xFFFFFFFC` | **flash空白** — bootloader区被擦除但未写入 | 写完整BIN到 `0x08000000`，无需复杂恢复 |
| `0x08000200` ~ `0x08003xxx` | **bootloader正常但未跳转到应用** — 向量表正确 (0x08008000) 但 app_descriptor 缺失导致 bootloader 不通过校验 | 1. 在 hwdef.dat 添加 `define AP_CHECK_FIRMWARE_ENABLED 1` + `APJ_BOARD_ID TARGET_HW_PX4_FMU_V5`（重建后签名出现）； 2. 或用 `reset_config srst_only` + `reset` 做硬件复位清除手动跳转污染； 3. 或 OpenOCD 手动设 PC/SP 跳转（临时验证，之后必须做硬件复位） |
| `0x0800xxxx` + HardFault | **固件代码崩溃** — 启动后立即异常 | GDB `bt` 查崩溃位置，分析代码 |
| `0x1fffxxxx` | **在系统bootloader** — BOOT0拉高，MCU进入DFU | 可 `dfu-util` 烧录或切换BOOT0 |
| OpenOCD报 `unable to connect` | SWD被锁定（flash损坏/低功耗模式/option byte） | 按 MCU完全锁死恢复 流程 |

### ⚠️ 手动跳转调试后的复位污染陷阱（2026-05-15 发现）

**核心问题**：手动设置 VTOR + PC + SP 直接跳转到应用（绕过 bootloader）之后，芯片处于**污染状态**。后续用 `program ... reset run` 无法干净复位——芯片仍然跳转到之前手动设置的地址，不经过 bootloader 的完整启动流程。表现为 PC 卡在 0x08003628（bootloader 内部）或 0x08070130 等应用区但 `iterations=0`。

**根因**：`reset run`（SYSRESETREQ 软件复位）不产生电源复位。手动设置的 VTOR/PC/SP 残留状态使芯片在软件复位后行为异常。需要真正的硬件复位才能清除。

**正确恢复流程（必做！）：**

```bash
# 方法一（推荐）：使用 srst_only 硬件复位
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "adapter speed 2000" \
  -c "reset_config srst_only" \
  -c "init" \
  -c "reset" \
  -c "shutdown"

# 方法二：如果方法一不行，先 program 再单独 reset
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "adapter speed 2000" \
  -c "init" \
  -c "halt" \
  -c "program /path/to/rtthread.bin 0x08008000 verify" \
  -c "reset_config srst_only" \
  -c "reset" \
  -c "shutdown"
```

**诊断方法**：怀疑复位污染时，用 `init` + `halt` 后检查：
- PC=0x08003628（bootloader 中）→ 芯片在 bootloader 但未跳转，可能是手动跳转残留
- `hal_run_called=0xBBBBBBBB` 但 `iterations=0` → 上次运行残留值，不是本次运行的证据

**验证恢复成功**：等待 8 秒后检查 CDC：
```bash
ls -la /dev/ttyACM*  # 时间戳应为最新
python3 -c "from pymavlink import mavutil; m=mavutil.mavlink_connection('/dev/ttyACM1',115200); print(m.wait_heartbeat(timeout=8))"
```

### 手动跳转到固件（bootloader不自动跳转时的应急方法）

> ⚠️ **使用前必读**：手动跳转会使芯片进入**污染状态**。之后的复位必须用 `reset_config srst_only` + `reset`（见上方陷阱说明）。**
> 建议当作**一次性诊断手段**，不要作为长期运行方法。

当bootloader判断固件无效（或bootloader区被意外擦除但固件已正确写入0x08000000）时，可直接通过OpenOCD跳转：

```bash
# 1. 读取BIN文件获取向量表
python3 -c "
import struct
with open('build/rtt_cuav_v5/rtthread.bin', 'rb') as f:
    data = f.read()
msp = struct.unpack('<I', data[0:4])[0]
reset = struct.unpack('<I', data[4:8])[0]
print(f'MSP=0x{msp:08X} Reset=0x{reset:08X}')
"

# 2. 通过OpenOCD telnet手动设置
echo "reset halt" | nc -q2 localhost 4444
echo "reg pc 0x<ResetHandler>" | nc -q1 localhost 4444  # 替换为上面的Reset地址
echo "reg sp 0x<InitialMSP>" | nc -q1 localhost 4444   # 替换为上面的MSP地址
echo "resume" | nc -q1 localhost 4444

# 3. 验证固件启动
lsusb | grep "1209:5741"  # 应显示 "Generic CUAVv5 RTT"
```

恢复后MCU直接运行固件，USB应该枚举。如果重启后问题重现（bootloader仍不跳转），则需要检查bootloader的跳转逻辑。

**典型场景**: 完整BIN已写入0x08000000（OpenOCD确认写入成功），但bootloader检查固件某校验失败而不跳转。此时手动PC跳转可验证固件本身是否可用。

### IMU 全零诊断（含 I2C 软驱动阻塞，2026-05-11 更新）

**症状**: RAW_IMU x=y=z=0, SYS_STATUS 消息完全不出现（HLTH=0），但心跳存在(status=1/3)。

**新症状模式 — 软I2C挂死（2026-05-11 发现）**:
- `lsusb` 显示 "Generic CUAVv5 RTT" 枚举成功
- `/dev/ttyACM0/1` 存在但 `cat /dev/ttyACM1` 无任何输出
- OpenOCD `monitor halt` 无 HardFault（CFSR=0, HFSR=0）
- **PC 停在 `stm32_set_sda()` @ `drv_soft_i2c.c:80`**（`GPIO_ResetBits` 或 `GPIO_SetBits` 操作）

**诊断流程**:

```bash
# 1. OpenOCD halt 并检查 CFSR+HFSR
echo "halt" | nc -q2 localhost 4444
echo "reg pc" | nc -q2 localhost 4444
echo "mdw 0xE000ED28 2" | nc -q2 localhost 4444  # CFSR+HFSR
# CFSR=0x00000000 + HFSR=0x00000000 = 无 HardFault，正常挂死

# 2. addr2line 定位源码
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf <PC值>

# 3. 确认是软I2C后，分析 GPIO 配置
# soft I2C 使用 GPIO bit-bang，检查对应的 GPIO MODER/PUPDR/OSPEEDR
# 常见根因: GPIO 未初始化为开漏输出，或 SDA/SCL 线被拉死
```

**根因分析方向**:
1. **GPIO 初始化缺失** — 软 I2C 驱动未在 init 时配置对应 GPIO 引脚模式（需设为开漏输出）
2. **I2C 总线被外部设备拉死** — 传感器上电后拉低 SDA 不放，导致 SDA_HIGH() 等待超时
3. **rtt_board_init 顺序问题** — GPIO 时钟使能在 I2C 初始化之后
4. **CUAV V5 上挂载的 I2C 外设**（如保险丝状态监控、安全开关）在启动初期异常

**修复验证**:
- 修复后 PC 不应再出现在 `drv_soft_i2c.c` 中
- OpenOCD halt 后 PC 应在主循环或线程调度中
- CDC 应输出 MAVLink 心跳 (system_status=3 STANDBY)

**症状**: RAW_IMU x=y=z=0, SYS_STATUS 消息完全不出现（HLTH=0），但心跳存在(status=1/3)。

**严重程度**: **比出现 -999 更严重**。x/y/z=0 说明主循环未读取传感器，大概率是 init 阶段阻塞。

**诊断路线**:

1. **MCU halt 查当前执行的线程**:
   ```bash
   arm-none-eabi-gdb -batch \
     -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
     -ex "target extended-remote :3333" \
     -ex "monitor halt" \
     -ex "thread apply all bt 3"
   ```
   - 如果所有线程都在 AP_Logger::io_thread 的 DWT delay 中 → 主循环未创建/未运行
   - 如果某线程在 spi1_poll_transfer 的 RXNE 等待循环中 → SPI 传输挂起

2. **检查 IMU backend 状态**:
   ```bash
   arm-none-eabi-gdb -batch \
     -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
     -ex "target extended-remote :3333" \
     -ex "monitor halt" \
     -ex "p AP::ins()->_backend_count" \
     -ex "p AP::ins()->_backends[0]" \
     -ex "p AP::ins()->_backends[1]" \
     -ex "p AP::ins()->_backends[2]"
   ```
   - `_backend_count == 0` → 传感器探针全部失败（更严重的问题）
   - `_backend_count == 3` 但传感器不工作 → IMU 探针成功但持续读取失败

3. **检查健康掩码**:
   SYS_STATUS health bits:
   ```
   0x01 = GYRO, 0x02 = ACCEL, 0x08 = BARO
   ```
   - GYRO+ACCEL 同时 unhealthy → SPI1 总线问题
   - 只有 GYRO 或只有 ACCEL unhealthy → 单个芯片故障
   - 三个传感器全部 unhealthy + 无 SYS_STATUS → init 阻塞（见下）

4. **IMU=0 + 无 SYS_STATUS = init 阻塞**:
   最常见原因：`AP_Baro::init()` 或 `AP_InertialSensor::init()` 中某一步挂起。
   - MS5611 (SPI4) probe 阻塞 → 影响了整个初始化序列
   - 即使 IMU 在 SPI1（不同总线），如果 probe 在主线程中串行执行，
     BARO probe 阻塞 = 后续 IMU probe 无法运行 → 所有传感器挂起
   - 诊断方法：检查 PC 位置，看是否在某 probe 函数的 SPI 传输中

**修理方向**:
- 如果 SPI1 传输正常(寄存器读回来正确)但 init 不继续 → SPI4 probe 阻塞
- 如果 SPI1 传输也挂起 → 检查 SPI1 时钟/引脚/CS 配置

**关键经验**: 不要假设 IMU 引脚配置正确。**`drv_spi_ll.c` 的 SPI1 pinmux 和 `_spi1_gpio_init()` 的 pinmux 可能不同！** 例如：
- `drv_spi_ll.c`: MISO=PG9, MOSI=PB5 (LLD 驱动路径)
- `_spi1_gpio_init()`: MISO=PA6, MOSI=PD7 (register-level polling 路径)

L0 验证时用的是 polling 路径（PA6/PD7 正确）。如果改用 RTT 框架路径（使用 drv_spi_ll.c 的 PG9/PB5），IMU 会失败。**始终验证用的是哪个路径**。

验证方法：
```bash
# 1. 对照 ChibiOS hwdef 验证 SPI 引脚
cat libraries/AP_HAL_ChibiOS/hwdef/fmuv5/hwdef.dat | grep "SPI1\|spi1"
# → PG11 SCK, PA6 MISO, PD7 MOSI

# 2. 检查 HAL_MspInit 确认
grep -A20 "Instance == SPI1" board/CubeMX_Config/Src/stm32f7xx_hal_msp.c

# 3. 检查 RTT _spi1_gpio_init()
grep -A15 "static void _spi1_gpio_init" libraries/AP_HAL_RTT/SPIDevice.cpp
```

**诊断路线**:
1. CDC 输出 "Config Error: INS: unable to initialise driver" → `_backend_count == 0`
2. 检查 hwdef.h 确认 IMU 探测列表完整: `cat build/rtt_cuav_v5/hwdef.h | grep INS_PROBE`
3. 追踪 SPI1 寄存器级轮询路径 (bus==1, _dev==nullptr)
4. 对比 ChibiOS hwdef 验证 GPIO 引脚表
5. 验证 IMU 电源 (VDD_3V3_SENSORS_EN PE3)
6. 用 OpenOCD 读 IMU WHO_AM_I 寄存器

### SPI1 GPIO 引脚一致性（2026-05-08 重大修复）

**致命陷阱**: SPI1 的 GPIO 引脚配置写在**两个文件中**，**必须保持一致**。

| 文件 | 路径 | 用途 |
|------|------|------|
| `_spi1_gpio_init()` | `SPIDevice.cpp` | register-level polling 路径 (`bus==1`, `_dev==nullptr`) |
| `spi1_ll_cfg` | `drv_spi_ll.c` | RTT 框架路径 (`bus==1`, `_dev!=nullptr`) |

**CUAV V5 正确的 SPI1 引脚:**

| 信号 | 引脚 | 验证来源 |
|------|------|---------|
| SCK | PG11 AF5 | hwdef.dat, ChibiOS |
| MISO | **PA6** AF5 | hwdef.dat, ChibiOS |
| MOSI | **PD7** AF5 | hwdef.dat, ChibiOS |

⚠️ **2026-05-08 踩坑**: 两个文件的引脚都被错误地改成了 PG9(PB5)。修复时两个文件都要改。

⚠️ **`_spi1_gpio_init_done` 保护绝不能删除**:
```c
static bool _spi1_gpio_init_done = false;
static void _spi1_gpio_init(void)
{
    if (_spi1_gpio_init_done) return;
    _spi1_gpio_init_done = true;
    ...
}
```
被删除的理由"GPIO可能被其他外设篡改"是错误的——重配产生的毛刺远比概率性的篡改更致命。

⚠️ **CS 引脚初始必须驱动 HIGH**:
```c
GPIOF->BSRR = (1U << 2) | (1U << 3) | (1U << 4);
```
删除这行会导致 CS 浮空，IMU 可能误检测片选。

详细修复记录: `references/spi1-gpio-pin-correction.md`

**2026-05-08 修复记录**: 系统达到 L0 的关键实际上是正确的复位策略和 CDC 处理，而非 SPI 引脚——原始代码的引脚配置一直正确(PA6/PD7)。

### CDC 热复位后丢失

**症状**: `reset init` 后 `cat /dev/ttyACM*` 返回空或二进制垃圾。

**原因**: STM32F7 DWC2 OTG 在 hot reset 后端点不重建。

**解决方案**:
- **用 `echo "reset" | nc -q1 localhost 4444`** 代替 `reset init`（让 bootloader 完整复位 USB）
- CDC 可能换端口: 检查 `/dev/ttyACM1`（最新时间戳的那个）
- CherryUSB BSP 已有 AHB 总线复位修复代码在 `cherryusb.c`

### CDC 枚举到 ttyACM1 而非 ttyACM0

复位后 CDC 可能以新的端点配置枚举，出现在 `/dev/ttyACM1`。
```bash
ls -la /dev/ttyACM*  # 看时间戳确定哪个是新鲜的
timeout 8 cat /dev/ttyACM1 2>/dev/null | strings | head
```

### OpenOCD 后台进程生命周期管理

```bash
# 启动（必须用 background=true）
openocd -f Tools/debug/openocd-f7.cfg  # 建议用 terminal(background=true)

# 等待就绪（5-8秒）
sleep 5 && cat /tmp/ocd.log  # 确认看到 "Listening on port 3333"

# 使用完后清理（正确方法: 先杀进程, 再等待端口释放）
kill -9 $(pgrep -f openocd) 2>/dev/null
sleep 2

# ❌ 错误: fuser -k + kill -9 同时用
fuser -k 6666/tcp 3333/tcp 4444/tcp 2>/dev/null  # ❌ 会杀死当前shell！
# 因为OpenOCD以subshell方式启动，fuser的SIGKILL传播到父进程

# ✅ 正确: 只用pgrep查杀
kill -9 $(pgrep -f openocd) 2>/dev/null
sleep 2
ss -tlnp | grep -E "3333|4444|6666" || echo "ports free"
```

### STM32F7 Flash Algorithm Timeout 恢复

**症状**: OpenOCD 报 `Error: timeout waiting for algorithm, a target reset is recommended`，
`flash write` 虽然返回"wrote N bytes"但实际部分扇区损坏（校验失败）。

**根因**: STM32F7 flash 编程算法在执行过程中超时，常见于 flash 未完全擦除或 option bytes 锁定。

**恢复步骤**:
```bash
# 1. 正确halt后再擦除
echo "reset halt" | nc -q2 localhost 4444
echo "flash erase_sector 0 0 last" | nc -q15 localhost 4444

# 2. 解锁flash option bytes再写入（解决算法超时）
# GDB法
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor reset halt" \
  -ex "set {int}0x40023C00 = 0x01" \   # 解锁option byte
  -ex "monitor flash write_image /path/to/rtthread.bin 0x08000000" \
  -ex "monitor reset run" \
  -ex "quit"

# 或telnet法
echo "reset halt" | nc -q2 localhost 4444
echo "flash write_image /data/firmare/pogo-apm/build/rtt_cuav_v5/rtthread.bin 0x08000000" | nc -q30 localhost 4444
echo "reset run" | nc -q1 localhost 4444
```

**验证写入完整性**:
`reset halt` 后检查 PC。PC=0x08000200 (bootloader) = 写入正确。
PC=0xFFFFFFFE = flash 仍空白或校验失败。

**关键区分**:
- `reset run` = bootloader 启动，检查固件有效性后跳转
- `resume` = 从halt位置继续执行（在halt状态执行这条可能无效果）
- **烧录验证后用 `reset run`，不要用 `resume`**

### ACM1端口存在但read()阻塞的诊断意义

**症状**: `lsusb` 显示 "Generic CUAVv5 RTT" 枚举正常，`/dev/ttyACM1` 存在且时间戳新鲜，
但 python serial 的 `ser.read()` 无限阻塞不返回数据。

**诊断**: 这 ≠ 固件完全崩溃。固件成功启动到了 USB 初始化之后（所以设备枚举了），
但后续在某处挂死（通常是 SPI1 ICM20689 传输），导致 MAVLink 发送线程停止。

### CDC 枚举 + 无 MAVLink 心跳 — 脏活模式（2026-05-11 发现，2026-05-12 更新诊断优先级）

**症状**: I2C 或类似阻塞问题修复后，系统顺利启动、调度器正常、idle 线程活跃，但 MAVLink 仍然无声。

| 检查项 | 典型值 |
|--------|--------|
| USB CDC | ✅ ttyACM0/1 存在（最新时间戳） |
| HardFault | ✅ 无（CFSR=0, HFSR=0） |
| OpenOCD halt (3次 × 100ms) | PC 在 `idle_thread_entry` / `rt_spin_lock_irqsave` |
| MAVLink read() | ❌ 超时无数据 |

**此模式根因不在 I2C/SPI 阻塞，而在 CDC 设备名映射或主循环线程缺失。**

**诊断三步走（按概率排序）**:

#### 1.（最可能）CDC 设备名不匹配
生成的 `hwdef.h` 中 `HAL_RTT_UART_DEVICE_LIST` 定义 Serial 端口到 RT-Thread 设备名的映射。CUAV V5 的 Serial0 = `"usb-acm0"`。但 CherryUSB 驱动注册的 CDC 设备可能名不同：

```bash
# 检查生成头文件中的设备名
grep "HAL_RTT_UART_DEVICE_LIST" build/rtt_cuav_v5/hwdef.h

# 对比 CherryUSB 注册名
grep 'DEV_FORMAT_CDC_ACM\|rt_device_create\|rt_device_register' \
  modules/rt-thread/components/drivers/usb/cherryusb/platform/rtthread/usbd_serial.c | head -5
```

**可能的不匹配模式**：

| HAL_RTT_UART_DEVICE_LIST | CherryUSB 实际注册名 | 匹配 |
|--------------------------|---------------------|------|
| `"usb-acm0"` | `"usb-acm0"` | ✅ |
| `"usb-acm0"` | `"usbd0"` | ❌ 最常见不匹配！ |
| `"usb-acm0"` | `"usb-cdc-0"` | ❌ |

**修复方法**：对齐两边的设备名（改 hwdef.dat 或 usbd_serial.c 的 DEV_FORMAT_CDC_ACM）。

#### 2. AP_HAL 主循环线程是否存在
对比 ChibiOS（`AP_HAL_ChibiOS/Scheduler.cpp` 创建 `APM_SCHED_THREAD`），RTT 的等价逻辑在 `HAL_RTT_Class.cpp` 和 `Scheduler.cpp` 中。如果线程不存在，`run_ap()` 从未被调用。

#### 3. USB CDC TX 路径是否正确
`UARTDriver.cpp` 的 `uart_poll_tx()`（寄存器直写）仅适用于硬件 UART，不适用于 USB CDC。USB CDC 必须通过 `rt_device_write()` 经 RT-Thread device 框架。

**参考**: `references/cdc-mavlink-silent-diagnosis.md`

**验证方法**:

```bash
# 验证方法:
```bash
# 1. 确认USB设备在
lsusb | grep "1209:5741"  # 应显示 "Generic CUAVv5 RTT"

# 2. 起OpenOCD查MCU当前位置
openocd -f Tools/debug/openocd-f7.cfg &
sleep 5
echo "reset halt" | nc -q2 localhost 4444  # PC应停在flash有效地址
```

#### 4. IOMCU UART 超时阻塞（2026-05-15 新增诊断路径）

**症状**: CDC 枚举到 ACM 端口（最新时间戳），`ser.read()` 返回 0 字节，`rtt_dbg_main_loop_entry_called=0x12345678` 但 `loop_iterations=0`，`setup_stage` 固定在 662。

**根因**: IOMCU 线程（`AP_IOMCU::thread_main`）通过 UART8 与物理 IOMCU 协处理器通信失败，反复在 `read_registers` → `wait_timeout` 中超时（`rt_sem_take` timeout=2 ticks = 20ms）。大量 IOMCU 超时消耗调度器 tick，延迟主线程的 `ins.init()` 执行。

**诊断（多线程采样法）**:
```bash
for i in 1 2 3; do
  arm-none-eabi-gdb -batch \
    -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
    -ex "target extended-remote :3333" \
    -ex "set remotetimeout 2" \
    -ex "monitor halt" \
    -ex "bt 5" \
    -ex "monitor resume" \
    -ex "quit" 2>&1 | grep "^#"
done
```

如果出现 `AP_IOMCU::read_registers` → `UARTDriver::wait_timeout` → `rt_sem_take` → IOMCU UART 超时已确认。

**临时绕过**: 注释 `hwdef.dat` 中 `IOMCU_UART UART8` 行 → 重建 → `HAL_WITH_IO_MCU` 不编译。
**根治**: 排查 UART8 GPIO（PE0/PE1 AF8）、IOMCU 上电（PE3）、IOMCU 固件上传。

详细诊断参考：`rtt-stabilization-driver`→`references/setup-hang-diagnosis.md` §IOMCU。

**与完全崩溃的区别**:
| 现象 | 诊断 |
|------|------|
| lsusb不显示CUAV + ACM端口消失 | 固件在USB初始化前崩溃 |
| lsusb显示CUAV + ACM端口可打开但read()阻塞 | 固件枚举后传感器init挂死 |
| 曾读到MAVLink数据但之后不再更新 | SPI传输挂死，任务不再发送心跳 |
**症状**: `Failed to start Logger IO thread` 或 `Unable to allocate message writer`  
**修复**:
1. 主线程栈 49152→8192 (`.config: CONFIG_RT_MAIN_THREAD_STACK_SIZE`)  
2. DeviceBus 用 `rt_thread_init` + 静态栈替代 `rt_thread_create` (6KB×8)  
3. Logger IO栈 8192→2048  

### 🔴 致命陷阱：RTT 线程栈全部从堆分配导致堆耗尽（2026-05-17 发现，2026-05-17 扩展）

#### 症状

固件烧录后 USB CDC 枚举成功（`lsusb` 显示 `Generic CUAVv5 RTT`），但无 MAVLink 数据。  
具体表现取决于耗尽时机：

| 耗尽时机 | 断点位置 | 症状 |
|---------|---------|------|
| serial 打开时 | `dev_serial.c:676` | `RT_ASSERT(rx_fifo != RT_NULL)` → while(1) |
| Logger 线程时 | `AP_Logger.cpp:1487` | `AP_HAL::panic("Failed to start Logger IO thread")` |
| 更早 | —— | USB 不枚举（耗在 init 前） |

#### 根因

**RTT 所有线程通过 `rt_thread_create()` 从动态堆分配栈空间**（`RT_KERNEL_MALLOC(stack_size)`）。  
而 ChibiOS 移植中，线程栈是 **BSS 静态分配**（linker script 中 `.pstack`/`.mstack` 段），不进堆。

```
SRAM1/2 堆总容量:    ~84KB  (0x2006aee0 → 0x20080000)

RTT 线程栈全部从堆取走（fix 前）：
  ap_timer    16384     ← 最大消费者
  ap_uart     8192
  ap_io       8192
  storage     8192
  main        4096
  tshell      4096
  ap_mon      2048
  ap_rcout    2048
  ap_rcin     2048
  timer       2048
  idle         256
  sdmnt       2048
  cpumon      1024
  + thread object + memberproc + thread_t
  总计 ≈ 65KB（堆仅剩 ~19KB）

→ serial rx_fifo × 8 ≈ 736B 放不下 → rt_malloc 返回 NULL
→ 或 Logger IO thread ≈ 2400B 放不下 → panic
```

#### 诊断命令

```bash
# 1. 检查 rtt_dbg_* 变量定位阻塞阶段
pyocd commander -t STM32F767ZI -c \
  "read32 0x2004089c 4; read32 0x200201c8 4; read32 0x2003e10c 4; exit" 2>&1 | tail -4
# hal_run_called=0xBBBBBBBB → run() 已进入
# setup_stage=502 → 卡在 Storage::_flash_load
# iterations=0 → 主循环未开始

# 2. 检查堆使用情况
# lfree 位置靠近 heap_end → 堆已几乎耗尽
pyocd commander -t STM32F767ZI -c "read32 0x2006af14 4; exit" 2>&1
# 0x2007ff38 → lfree 非常靠近 heap_end(0x2007fff0) → 只剩~184B

# 3. 反编译确认实际编译的栈大小
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf | \
  sed -n '/<ZN3RTT9Scheduler4initEv>:/,/^08072[0-9a-f]\{4\} </p' | grep -E "mov.*r3.*#"
# 检查每个 rt_thread_create 的第三个参数（栈大小）
```

#### 精确定位工具：GDB 跟踪堆使用

```bash
# 方法A：watchpoint 监视 parent.used 每次被写
cat > /tmp/heap_watch.gdb << 'GDBEOF'
set pagination off
file build/rtt_deploy/cuav_v5/rt-thread.elf
target remote localhost:3333
set $sm = 0x2006aee0
# used 在 smem 结构中的偏移（取决于 RT_NAME_MAX=12 时的对齐）
# 直接用 p &((struct rt_small_mem*)system_heap)->parent.used 得到精确地址
watch *(rt_size_t*)((char*)system_heap + 36)
commands
  p/x $pc
  bt 3
  continue
end
continue
quit
GDBEOF

# 方法B：连续检查每个 serial open 前后 used 值
# 从GDB跟踪看出 used 从 0 → 86072/86208 = 99.8% 仅需数秒
```

#### 修复方案

**方案 A（当前已实施）**：减少 Scheduler 线程栈大小（在 `Scheduler.cpp` 的 `init()` 中）

| 线程 | 原大小 | 合理大小 | 节省 |
|------|--------|---------|------|
| ap_timer | **16384** | **4096** | **12KB** |
| ap_uart | 8192 | 4096 | 4KB |
| ap_io | 8192 | 4096 | 4KB |
| storage | 8192 | 4096 | 4KB |
| **总计** | | | **24KB** |

```cpp
// Scheduler.cpp → init()
_timer_thread_ctx = rt_thread_create("ap_timer", ..., this, 4096, ...);     // was 16384
_uart_thread_ctx = rt_thread_create("ap_uart",  ..., this, 4096, ...);     // was 8192
_io_thread_ctx = rt_thread_create("ap_io",     ..., this, 4096, ...);     // was 8192
_storage_thread_ctx = rt_thread_create("storage", ..., this, 4096, ...);  // was 8192
```

**方案 B（长期架构修复）**：对照 ChibiOS，将关键线程栈改为**静态 BSS 分配**：
- linker script 增加 `.pstack`/`.mstack` 段
- 用 `rt_thread_init()` + 静态 buf 代替 `rt_thread_create()` + `RT_KERNEL_MALLOC`
- 参考 ChibiOS linker scripts 和 `chThdCreateStatic()` 模式

**方案 C（辅助）**：将 DTCM（128KB @ 0x20000000，当前未用）纳入可用 RAM 池。
当前 `.data` 从 `0x20020000`（SRAM1）开始，DTCM 区域完全闲置。

#### 验证

```bash
# 烧录后检查堆使用率
pyocd commander -t STM32F767ZI -c "read32 0x2006af00 3; read32 0x2006af14 1; exit" 2>&1
# total @ 0x2006af00
# used  @ 0x2006af04
# max   @ 0x2006af08
# lfree @ 0x2006af14
# 期望：used < total，lfree 离 heap_end 有足够余量
```

#### 参考

- `references/rtt-porting-pitfalls.md` — RTT 移植常见陷阱（含堆耗尽章节）
- ChibiOS 参考：`modules/CMSIS/hwdef/fmuv5/link.ld` 的 `.pstack` 静态栈分配

---
**症状**: `DeviceBus: FAILED to create thread!`  
**修复**: 静态栈+`rt_thread_init`。栈≥6KB (2KB溢出, 4KB IMU初始化失败)

### QGC 连接断连 — USB CDC TX 缓冲区溢出

**症状**: QGC 连接后参数获取卡顿、频繁断连重连。用 `scripts/param_fetch_bench.py` 可看到"突发→停顿3-4秒→再突发"模式。\n\n**定量诊断模式**（2026-05-09）：\n- 参数以 **突发→停顿→突发** 模式到达 → CherryUSB TX 缓冲满 + DWC2 FIFO 128 字节瓶颈\n- 532 参数在 20 秒仅完成 71% = 33 params/s = ~1000 B/s（USB FS 理论 12 Mbps 的 0.07%）\n- 停顿 3-4 秒说明宿主不主动轮询 USB IN 端点（常见于虚拟机/远程桌面环境）

**根因**: `CONFIG_USBDEV_SERIAL_TX_BUFSIZE=4096` 的 CherryUSB 环形缓冲在参数列表突发（~10KB）时填满，
`rt_device_write()` 返回 0 → 连续 500 次失败后 `_writebuf.clear()` 丢弃待发数据 → QGC 超时断连。

**修复**:
1. `usb_config.h`: `4096 → 32768`
2. `UARTDriver.cpp`: 写失败清除阈值 `500 → 5000` ticks

详见 `references/usb-cdc-tx-buffer-qgc-fix.md`。\n定量基准测试：`scripts/param_fetch_bench.py`（自动检测"突发-停顿"模式）

**验证**: QGC 连接后参数列表完整获取（≥530 参数），无卡顿断连。

---

### CDC文本污染
**症状**: MAVLink帧与文本交错  
**修复**: 
1. UARTDriver `_begin()` 移除 `!is_usb` 条件  
2. AnalogIn.cpp 删除 ADC STATUS/FIRST 的 `rt_kprintf`

### 调度器 tick 抖动（待修复）
**症状**: `timeavail` 在 0-143ms 间波动, GYRO/ACCEL 报 unhealthy  
**理论根因**: RT-Thread 默认 tick=100Hz (10ms), ArduPilot 需要 1ms 精度。  
**方案**: 在 `rtconfig.h` 改 `RT_TICK_PER_SECOND` 为 1000

## C 代码修改铁律（2026-05-08 用户明确纠正）

修改 C 代码前必须执行以下检查步骤，避免 MCU 不可用：

1. **大脑模拟**: 修改前完整执行一遍代码路径推演，确认不会产生 crash/HardFault
2. **最小侵入**: 优先改源头（CubeMX 配置文件、BSP 初始化）而非 HAL 层核心代码
3. **不跨组调用**: RTT build system 的 `DefineGroup` 隔离意味着跨组函数引用可能被 `--gc-sections` 丢弃；内联寄存器操作更可靠
4. **MCU 不可用 = 最严重的代价**: 一次 C 代码错误导致 CDC 无输出时，整个调试周期归零，必须复位重来
5. **DMA 不可用**: SPI4 DMA completion IRQ 不触发，会使 SPI 永久挂死。所有 SPI4 传输走 RTT 框架 HAL 轮询路径

## SPI4 (MS5611 气压计) 修复全记录

### 分层 Bug 修复进度（2026-05-08）

**症状**: SCALED_PRESSURE `abs=0.00hPa` — BARO 不在 SYS_STATUS present 掩码中。

**已修复的分层 bug**:

| 层 | 问题 | 修复状态 |
|---|---|---|
| 1 | SPI4_SCK PE2→PE12 (错误！PE12 不是有效 SPI4 引脚) | ❌ **待修正：应改回 PE2** |
| 2 | hwdef.dat 将 PE2 改为 PE12 | ❌ **待修正：应改回 PE2** |
| 3 | SPI4_MOSI PE14 与 TIM1_CH4 PWM(1) 冲突 | ❌ **待修正：应改用 PE6** |
| 4 | SPIDevice.cpp 硬编码 PE12/PE14 | ❌ **待修正：改为 PE2/PE6** |
| 5 | drv_spi_ll.c 硬编码 PE12/PE14 | ❌ **待修正：改为 PE2/PE6** |
| 5 | **SPI4 传输路径未定** | ⏳ 见下方 |

### SPI4 传输路径争议（第5层）

**路径 A — register-level polling**（之前方案，已撤回）:
在 SPIDevice.cpp 中 `bus==1 || bus==4` 让 SPI4 走 register-level polling 路径。
- ✅ 优点：不依赖 RTT 框架，代码路径成熟
- ❌ 疑点：GDB 确认 MS56XX::_init() 从未到达(断点不命中)，怀疑 `_sem.take()` 在 DeviceBus 信号量已被持有时死锁

**路径 B — RTT 框架路径**（当前方案）:
让 SPI4 通过 `rt_device_find("spi41")` + `rt_spi_transfer_message()` 走 RTT 框架。
- ✅ 优点：遵循标准 RTT 架构，GPIO 已由安全网正确配置
- ✅ hwdef.h 生成验证正确: `{"spi4", "spi41", GET_PIN(F, 10)}` 在 `HAL_RTT_SPI_ATTACH_LIST` 中
- ✅ `_spi_cs_table` 已含 `spi41` 项 
- ❌ **IMU 全零回归** — 改为 RTT 框架后 IMU (SPI1) 也停止工作，ACM0 输出二进制垃圾
- ⚠️ **只有 SPI4 相关代码变更**，却影响了 SPI1 的行为。可能根因: SPI4 probe 在 `AP_Baro::init()` 中阻塞整个初始化序列

### SPIDevice 构造器代码流 (SPIDevice.cpp:225-246)

```cpp
SPIDevice::SPIDevice(const RTT_SPIDesc &desc)
    : AP_HAL::SPIDevice()
    , _desc(desc)
    , _dev(nullptr)
    , _bus(DeviceBus::get_bus(desc.bus, 0))
    , _cs_pin(0)
{
    set_device_bus(desc.bus);
    _cs_pin = _lookup_cs_pin(desc.rtt_devname);
#ifndef FORCE_RTT_SPI_FRAMEWORK
    if (_desc.bus == 1) {       // ← SPI1 走 register-level polling
        _dev = nullptr;
        return;
    }
#endif
    // 总线 2(FRAM), 4(MS5611) 走这里
    _dev = (struct rt_spi_device *)rt_device_find(desc.rtt_devname);
    if (_dev != nullptr) {
        set_speed(AP_HAL::Device::SPEED_LOW);
    }
}
```

对于 bus==4 (MS5611, rtt_devname="spi41"): `_dev = rt_device_find("spi41")`

### MS5611 probe 流程追踪

```cpp
// AP_Baro_MS5611.cpp:68 (通过宏 _probe<AP_Baro_MS5611>)
AP_Baro_Backend *AP_Baro_MS56XX::_probe(AP_Baro &baro, AP_Baro_MS56XX *sensor) {
    if (sensor == nullptr || !sensor->_init()) {  // ← 断点设在这里但不命中
        delete sensor;
        return nullptr;
    }
    return sensor;
}
```

**`_init()` 断点不命中**可能的原因（按概率排序）:
1. **`sensor` 为 nullptr** — `NEW_NOTHROW AP_Baro_MS5611(...)` 分配失败（OOM? 堆不足?）
2. **`_init()` 地址错误** — GDB 符号表将 MS56XX::_init 解析到 `OwnPtr.h:95`（可能内联了）
3. **`_init()` 内最前面** `if(!_dev) return false` — 若 `rt_device_find("spi41")` 返回 NULL 则立即返回

### SPI4 设备注册检查（对照实验方法）

在 SPIDevice.cpp 构造器中加临时 `rt_kprintf` 或在 GDB 中检查:
```gdb
# 方法 1: 断点在构造器
break RTT::SPIDevice::SPIDevice if _desc.bus == 4
commands
  print (void*)rt_device_find("spi41")
  continue
end
# 方法 2: 检查 RTT 设备树
# 查 rt_device_find("spi41") 返回值。null = 设备未注册，非 null = 设备存在但 probe 在其他地方失败
```

### 对照实验法（2026-05-08 新引入）

当怀疑代码变更引起回归时，不要直接推断。执行对照实验:

```bash
# 步骤 1: 回到旧代码
git stash  # 保存当前改动
git log --oneline -5  # 确认 HEAD 在旧版本
# 步骤 2: 重建+烧录+验证
scons --v=ArduCopter --target=cuav_v5 -j$(nproc)
# 烧录并验证 IMU/BARO 数据
# 步骤 3: 如果旧版也失效 → 不是该改动引起；如果旧版恢复 → 确认是新改动引入回归
# 步骤 4: git stash pop 恢复当前改动继续修
```

## SPI4 引脚总表（CUAV V5 硬件 — 基于 ChibiOS 参考）

**⚠️ 重要警告（2026-05-09 发现）**：RTT hwdef.dat 此前将 SPI4_SCK 配置为 PE12，但 **PE12 不是 STM32F765 上有效的 SPI4_SCK 引脚**。根据 STM32F765 数据手册，SPI4_SCK 的有效选项仅为 **PE2(AF5)** 或 **PF6(AF5)**。ChibiOS fmuv5（在 CUAV V5 硬件上运行正常）使用以下引脚：

| 信号 | STM32F7 引脚 | AF | 用途 |
|------|-------------|----|------|
| **SPI4_SCK** | **PE2** (AF5) | AF5 | 时钟（✅ 唯一有效选项） |
| **SPI4_MISO** | **PE13** (AF5) | AF5 | 数据入 |
| **SPI4_MOSI** | **PE6** (AF5) | AF5 | 数据出（避免与 PWM(1) 冲突） |
| MS5611_CS# | PF10 | GPIO OUT | 片选 (active low) |

❌ **PE12 不是有效 SPI4_SCK 引脚** — STM32F765 的 PE12 无 SPI4 复用功能。
❌ **PE14 不可用于 SPI4_MOSI** — PE14 可做 SPI4_MOSI(AF5)，但同时被 `TIM1_CH4 PWM(1)` 使用，硬件冲突。
✅ **正确配置 = ChibiOS 配置** — PE2(SCK)/PE13(MISO)/PE6(MOSI)。

**验证方法**：查 STM32F765 数据手册（DS11532 Rev 6）Alternate Function 表。

**正确 GPIO 寄存器值验证**:
```bash
(echo "halt"; sleep 0.3; echo "mdw 0x40021000 8"; sleep 0.3; echo "mdw 0x40021020 4"; sleep 0.3; echo "resume") | nc -q3 localhost 4444 2>&1 | grep -E "0x[0-9a-f]{8}"
```
期望: MODER=`0x2A020040`, AFRH=`0x05550008`

### MS5611 probe 调试流程

```bash
# GDB 三连: 检查当前执行线程
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "bt 3" && echo "resume" | nc -q1 localhost 4444

# 检查 MS56XX::_init 符号位置
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "info func MS56XX::_init" 2>&1 | head -3

# 检查 probe 调用是否被编译
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "disassemble AP_Baro::init" | grep -c "bl.*probe"
```

### GDB 硬件断点陷阱

不要在 `commands` 块中使用 `shell sleep` —— `shell sleep` 期间 MCU 继续运行，可能触发断点但 commands 无法正确响应，导致断点被视为"missed"。
**正确做法**: 在 `.gdb` 脚本中用 `continue` 阻塞，然后 `timeout N gdb -batch -x script.gdb` 包裹。

### SPI 传输路径架构 (SPIDevice.cpp)

```
SPIDevice::transfer()
├─ bus == 1 (_dev == nullptr) → register-level polling (spi1_poll_transfer)
│  └── 适用于 IMU (ICM20689, ICM20602, BMI055)
└─ bus != 1 (_dev != nullptr) → RTT SPI 框架
   ├── bus == 2: rt_device_find("spi21") → rt_spi_transfer_message (FRAM)
   └── bus == 4: rt_device_find("spi41") → rt_spi_transfer_message (MS5611)
```

RTT 框架路径代码 (transfer 后半段):
```cpp
// full-duplex 路径 (send_len>0 && recv_len>0)
// 使用 bounce buffer 或 rt_malloc_align, 构造 rt_spi_message
// 调用 rt_spi_transfer_message(_dev, &msg)
// 如果返回 NULL → 成功, 拷贝 recv 数据
```

## SPI4 RTT 框架路径关键检查点

当 MS5611 通过 RTT 框架不工作时，以下检查点逐级排查:

1. **hwdef.h 生成** — 确认 SPI4 在 attach 列表中
   ```bash
   grep -A8 "HAL_RTT_SPI_ATTACH_LIST" build/rtt_cuav_v5/hwdef.h
   ```
   期望: `{"spi4", "spi41", GET_PIN(F, 10)}`

2. **SPIDevice.cpp CS 表** — 确认 spi41 已列
   ```bash
   grep "spi41" libraries/AP_HAL_RTT/SPIDevice.cpp
   ```
   期望: `{"spi41", 90},`

3. **`rt_device_find("spi41")` 返回值** — 在 SPIDevice 构造器加断点检查

4. **`_lock_bus()` 返回值** — 检查 `_dev->bus` 和 `_dev->bus->ops` 是否非 null
   ```cpp
   // SPIDevice.cpp:254
   if (_dev == nullptr || _dev->bus == nullptr || _dev->bus->ops == nullptr) {
       return false;  // 跳过传输
   }
   ```

5. **`rt_spi_transfer_message()` 返回值** — 断点检查是否返回 RT_NULL (成功)

## 调试技巧
- GDB三连: `monitor halt → bt 3 → monitor resume`
- 符号表: deploy 目录的 ~37MB ELF (`build/rtt_deploy/cuav_v5/rt-thread.elf`)
- 汇编检查 probe 是否编译: `disassemble FunctionName | grep "bl.*probe"`
- 线程栈溢出标志: 后栈帧 `0x23232323` = RT-Thread栈哨兵
- MCU halt恢复: `echo "resume" | nc -q1 localhost 4444`
- 烧录验证: `monitor mdw 0x08008000 4` 返回正确向量表
- 对照实验: 怀疑回归时 `git stash; build; verify` vs `git stash pop; build; verify`

## 完整参考
- `scripts/mavlink_diag.py` — MAVLink 二进制流诊断脚本
- `scripts/check_spi4_gpio.sh` — SPI4 GPIO 寄存器验证脚本
- `scripts/rtt_health_monitor.py` — cron 自愈监控脚本
- `references/spi4-ms5611-debug.md` — SPI4 MS5611 调试全记录
- `references/rtt-porting-pitfalls.md` — RTT 移植常见陷阱
- `references/cuav-v5-spi-pinout.md` — CUAV V5 SPI 引脚总表
- `references/usb-cdc-tx-buffer-qgc-fix.md` — USB CDC TX 缓冲导致 QGC 断连的根因与修复
- `references/mavlink-sensor-diagnostics.md` — 传感器诊断与 SYS_STATUS 位掩码
- `references/swd-hardware-vs-software-diagnosis.md` — SWD 连接失败诊断：硬件排线脱落 vs 软件可恢复锁死的决策流程
- `references/gdb-probe-tracing.md` — GDB probe 追踪模板脚本
- `references/spi-register-debugging.md` — SPI 寄存器级 OpenOCD telnet 诊断
| `references/flash-blank-recovery.md` | Flash空白/PC=0xFFFFFFFE恢复（2026-05-08） |
| `references/flash-write-timeout-recovery.md` | STM32F7 flash写入算法超时恢复（2026-05-08） |
| `references/stm32f7-dma-dtcm-trap.md` | DMA缓冲区必须在SRAM1，DTCM不可访问（2026-05-14） |
- `references/bootloader-hardfault-watchdog-recovery.md` | (🆕) 看门狗复位→bootloader HardFault 恢复（VTOR跳转方法）
- `references/pogo-can-hardware-family.md` | (🆕) Pogo CAN 硬件家族参考（PWMxServo/PWMx8/ESC/GPS等模块参数与拓扑）
