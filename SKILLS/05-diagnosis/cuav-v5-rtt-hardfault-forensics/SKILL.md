---
name: cuav-v5-rtt-hardfault-forensics
description: AP_HAL_RTT on CUAV v5 (STM32F767) 启动早期 HardFault 与启动失败的定位流程（SCons + OpenOCD + GDB），含 Flash 布局检查、ArduPilot bootloader 跳转验证、VTOR 时序、看门狗复位诊断、SPI4 DMA IRQ 问题判定。
---

# 适用场景

当 AP_HAL_RTT / RT-Thread 在 CUAV v5 上出现以下症状时使用：
- 上电后 App 似乎没有启动（PC 在 bootloader 空间）
- OpenOCD/GDB halt 后 PC=0x08003628（bootloader idle_thread）
- 看似"回到 bootloader"或"app 崩溃后重启"

## 已验证状态（2026-04-12）

**App 完全正常运行。** 之前所有"回到 bootloader"诊断是误判，根因是 bootloader 5 秒等待期。

当前验证通过的功能：
- 主循环：~300Hz（rtt_dbg_main_loop_iterations=11279/60s）
- USB CDC MAVLink：30msg/s, 30种消息类型（HEARTBEAT+ATTITUDE+RAW_IMU+STATUSTEXT等）
- EKF："EKF3 IMU1 tilt alignment complete"
- STATUSTEXT: "PreArm: Motors: Check frame class and type"（预期，IOMCU 未实现）

**进行中：IOMCU 支持**（RC/PWM 通过 STM32F100 协处理器 over UART8 PE0/PE1）
- hwdef 已添加 UART8/IOMCU 配置 + HAL_WITH_IO_MCU 1
- AP_IOMCU.cpp 需适配 6 处 ChibiOS API（chEvtSignal→rt_event_send 等）
- 需在 RTT hwdef 生成脚本中生成 HAL_UART_IOMCU_IDX
- RTT BSP 已有 UART8 驱动（BSP_USING_UART8, PE0=RX PE1=TX AF8）

## ⚠️ 最大陷阱：Bootloader 5 秒等待期

**ArduPilot bootloader 正常上电时先等 5 秒接受固件上传，然后才 jump_to_app！**
- `main()` 流程：`check_fast_reboot()` → `stm32_was_watchdog_reset()` → 若都不是 → `bootloader(5000)` 等 5 秒 → `jump_to_app()`
- **所有"自由运行后 PC 在 bootloader"的测试只等了 1~3 秒，bootloader 还没跳转**
- 等 60 秒后确认：rtt_dbg_main_loop_iterations=11279，app 完全正常

### DTCM 标记法（判断 app 是否运行的最可靠方法）
- **Bootloader 只用 SRAM1**（0x20020000+），不用 DTCM（0x20000000~0x2001FFFF）
- 用 DTCM 地址 0x200000F0 写 magic 值：app 的 .data init 会覆盖它 → 可靠的"app 是否运行"标记
- **不要用 0x20020000+ 的地址**做标记——bootloader 的 BSS/栈/堆会覆盖它们
- 实验方法：`reset halt → mww 0x200000F0 0x12345678 → resume → sleep N → halt → mdw 0x200000F0`
  - 等 3 秒 → 仍为 0x12345678（app 没跑）
  - 等 12 秒 → 变为 app .data 值（app 已运行）

### 测试等待时间规则
- `reset halt → resume → halt` 至少等 **8 秒**（5 秒 bootloader + 3 秒 app init）
- 断点模式下 bootloader 被停在断点处不会等 5 秒，容易误判

### RCC_CSR 复位标志（STM32F7）
- 写 `mww 0x40023874 0x01000000`（RMVF 位）清除累积标志
- 然后自由运行后 halt 检查哪些新置位
- **CSR 低 16 位不是复位标志**：bit0=LSION, bit1=LSIRDY
- 高位复位标志：bit24=PINRSTF, bit25=BORRSTF, bit26=SFTRSTF, bit27=PORRSTF, bit28=WWDGRSTF, bit29=IWDGRSTF

# 核心原则

1. 先做"冻结现场"再分析
- HardFault handler 只采样寄存器后死循环，避免二次压栈破坏现场。

2. 以"绝对地址读内存"为准，不信坏现场下的符号求值
- 坏现场下 `gdb p/x symbol` 可能错误重定位。
- 用 `arm-none-eabi-nm` 得到符号绝对地址，再 `x/wx <addr>` 读取。

3. 优先最小 A/B 回退，不做大改
- 用单点改动证伪/证实假设（如回退 FPCA 同步、延后 NVIC 使能）。

4. **Flash 布局一致性是第零优先检查项（先于 VTOR）**
- app 链接在 ROM ORIGIN（由 link.lds 决定），向量表（g_pfnVectors）必须在 Flash 起始处。
- CUAV v5 使用 32KB bootloader reserve：app 在 0x08008000，boot_stub 在 0x08000000。
- **已发过的严重 bug**：scons 部署脚本把 link.lds 的 ROM ORIGIN 从 0x08008000 覆盖为 0x08000000。导致：
  - ELF 中 g_pfnVectors=0x08008000 但 LOAD segment 从 0x08000000 开始
  - bin 文件内容正确（向量表在 bin 偏移 0），但烧录到 0x08008000 后 0x08000000 处残留旧向量表
  - 板子复位从 0x08000000 读到旧向量 → 跳到随机地址 → HardFault
- **验证命令**：
  ```bash
  arm-none-eabi-objdump -d rt-thread.elf | grep "<g_pfnVectors>:"
  # 应为 0x08008000
  arm-none-eabi-readelf -l rt-thread.elf | head -10
  # LOAD segment VirtAddr 应包含 0x08008000
  ```
- **三个文件必须一致为 0x08008000**：
  - `modules/.../link.lds`: `ROM (rx) : ORIGIN = 0x08008000`
  - `modules/.../rtconfig.h`: `#define FLASH_ORIGIN 0x08008000`
  - `modules/.../hwdef.h`: `#define FLASH_ORIGIN 0x08008000`
- **注意**：scons 部署可能在下次全量构建时重新覆盖这些值！检查 `rtt_bsp_deploy.py` 或 `rtt_hwdef.py` 的覆盖逻辑。

5. **VTOR 时序是第一优先检查项**
- boot_stub 跳转到 app 后 VTOR 仍指向 0x200000（boot_stub SRAM 向量表副本）或 0x08000000。
- 如果 VTOR 不正确，任何 fault 会走到错误的 handler，产生二级 fault（CFSR PRECISERR+INVSTATE, HFSR FORCED）。
- **修复已在 startup_rtt_override.S 中**：Reset_Handler 在加载 SP 后、调用 SystemInit 前立即设置 `SCB->VTOR = 0x08008000`。
- GDB 检查：`x/wx 0xE000ED08` 应返回 `0x08008000`。若返回 `0x00200000` 则 VTOR 未修复。
- **但 VTOR 修复可能被 rt_hw_board_init() 覆盖**：如果 rtconfig.h 中 FLASH_ORIGIN=0x08000000，则 `SCB->VTOR = FLASH_ORIGIN` 会把 VTOR 设回错误值。

6. **使用 ArduPilot 官方 bootloader（禁止自造 boot_stub！）**
- 官方 bootloader：`Tools/bootloaders/CUAVv5_bl.bin`（16KB，带 UART/USB 升级协议）
- 对应 ELF（反汇编用）：`Tools/bootloaders/CUAVv5_bl.elf`
- **绝对不要自己造 boot_stub**！之前自造 boot_stub2 导致严重问题。
- 烧录方式：
  ```bash
  # 烧 bootloader 到 0x08000000（16KB，占用 0x08000000~0x08003FFF）
  openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
    -c "program Tools/bootloaders/CUAVv5_bl.bin 0x08000000 verify reset exit"
  # 烧 app 到 0x08008000（32KB 偏移）
  openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
    -c "program build/rtt_deploy/cuav_v5/rtthread.bin 0x08008000 verify reset exit"
  ```
- **验证 0x08000000 内容**：`x/4xw 0x08000000` — 应看到 SP=0x20020400, Reset≈0x08000201（bootloader 入口）。
- **验证 0x08008000 内容**：`x/4xw 0x08008000` — 应看到 SP≈0x2000d498, Reset≈0x0810cfb1（app Reset_Handler）。
- Bootloader main() 流程：`check_limit_flash_1M()` → `check_fast_reboot()` → 首次启动直接 `jump_to_app()` → 如果返回则进入 UART 升级模式。

# 标准流程

## 0) 第零优先：验证 Flash 布局（先于任何寄存器分析！）

**2026-05-08 致命教训**：固件被烧录到错误地址（0x08000000 而非 0x08008000）时，所有代码在 flash 中位移 32KB。症状表现为**看似合理的特定函数 HardFault**（如 `AP_GPS_Blended::calc_state(this=0x33)`），但实际不是代码 bug——MCU 在错误地址执行随机数据。

```bash
# 第一步：halt 后检查 0x08000000 和 0x08008000 的向量表
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "x/4xw 0x08000000" \
  -ex "x/4xw 0x08008000" \
  -ex "quit"
```

**判据**：
- `0x08000000`: SP≈0x2002xxxx, Reset≈0x08000201（bootloader）— ✅ 正确
- `0x08008000`: SP≈0x2000xxxx, Reset≈0x080EExxx（固件）— ✅ 正确
- 两者**相同**（都是 0x0800xxxx/0x080EExxx）→ ❌ bootloader 被覆盖，固件位移！
- 0x08000000 为 0xFFFFFFFF → ❌ flash 空白（erase_sector 过但没写）

**如果 flash 布局错误，不要分析 HardFault！** 先修复烧录方式。

完全修复参考：`rtt-cuav-v5-flash-verify` 技能的 `references/firmware-displacement-mimics-hardfault.md`

## 0) 构建与产物确认

**【强制】使用 scons 编译，禁止 waf。**

```bash
cd /home/llw/firmare/pogo-apm
python3 -m SCons --target=cuav-v5 -j16
```

产物：`build/rtt_deploy/cuav_v5/rt-thread.elf` 和 `rtthread.bin`。
成功判据：ROM < 2016KB，无链接错误。

**同步启动文件**：修改 `libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S` 后，需同步到 modules：
```bash
cp libraries/AP_HAL_RTT/hwdef/common/board/startup_rtt_override.S \
   modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/startup_rtt_override.S
```

验证 VTOR 设置是否编译进二进制：
```bash
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf | grep -A 30 '<Reset_Handler>:'
# 应在 SystemInit 调用前看到 ldr r0,=0x08008000; ldr r1,=0xE000ED08; str r0,[r1]
```

验证向量表位置：
```bash
arm-none-eabi-objdump -d build/rtt_deploy/cuav_v5/rt-thread.elf | grep "<g_pfnVectors>:"
# 必须是 0x08008000
```

## 1) 烧录

**⚠️ 必须用 `program` 命令，不要用 `flash write_image erase`！**
`flash write_image erase` 会静默失败（无输出无报错），固件不会被写入。

**两步烧录**（ArduPilot bootloader + app）：

```bash
# Step 1: 烧官方 bootloader 到 0x08000000
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "program Tools/bootloaders/CUAVv5_bl.bin 0x08000000 verify reset exit"

# Step 2: 烧 app 到 0x08008000
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "program build/rtt_deploy/cuav_v5/rtthread.bin 0x08008000 verify reset exit"
```

**一步烧录**（bootloader + app 一起）：

```bash
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "init; reset halt; \
      program Tools/bootloaders/CUAVv5_bl.bin 0x08000000 verify; \
      program build/rtt_deploy/cuav_v5/rtthread.bin 0x08008000 verify; \
      reset run; shutdown"
```

如果 bootloader 已烧过且未改动，Step 1 可跳过。

如果失败，先查：
- `lsusb | grep -i "0483:3748"` — ST-LINK V2 应在线
- 是否有残留 OpenOCD 占用（`pkill -9 -f openocd`）
- 设备权限（必要时 udev/组权限）
- 确认输出中有 `** Verified OK **` — 如果没有则烧录未成功

## 1.5) Flash 布局快速验证（GDB attach 后首先执行）

```bash
arm-none-eabi-gdb -batch \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "target remote | openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c 'gdb_port pipe; log_output /dev/null'" \
  -ex "monitor halt" \
  -ex "x/4xw 0x08000000" \
  -ex "x/4xw 0x08008000" \
  -ex "p/x *(volatile uint32_t*)0xE000ED08" \
  -ex "quit"
```

**判据**：
- `0x08000000` 处：SP=0x20020400, Reset≈0x08000201（ArduPilot bootloader）
- `0x08008000` 处：SP≈0x2000d498, Reset≈0x0810cfb1（app Reset_Handler）
- VTOR=0x08008000（运行中）或 0x08000000（刚复位在 bootloader 中）

## 1.6) Bootloader jump_to_app() 验证（当 app 不启动时）

当 app 在断点模式下能命中 Reset_Handler 但自由运行后回到 bootloader 时，需验证 bootloader 的跳转逻辑：

```bash
# 在 jump_to_app 入口设断点，单步到地址检查
arm-none-eabi-gdb -batch \
  -ex "file Tools/bootloaders/CUAVv5_bl.elf" \
  -ex "target remote | openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c 'gdb_port pipe; log_output /dev/null'" \
  -ex "monitor reset halt" \
  -ex "break *0x08000552" \
  -ex "continue" \
  -ex "info reg r2 r3 r4 r5" \
  -ex "stepi 4" \
  -ex "info reg r3 pc" \
  -ex "quit"
```

**jump_to_app 反汇编关键点**（CUAVv5_bl.elf）：
- `0x08000548`: `ldr r2, [r4=0x08008004]` → r2 = app Reset_Handler
- `0x0800054a`: `cmp r2, 0x08007fff` → 检查 Reset_Handler > bootloader 区域
- `0x08000552-0x0800055c`: `r3 = board_info.flash_size + 0x8000000 + 0x8000` → 计算上限地址
  - 实测：0x1f8000 + 0x8000000 + 0x8000 = 0x8200000（即 0x08000000 + 2MB）
- `0x080005ac-0x080005b0`: 设 VTOR = 0x08008000
- `0x080005b2`: 读 app SP = [0x08008000]
- `0x080005b4`: 读 app Reset = [0x08008004]
- `0x08000638-0x0800063e`: `mov sp, r5; msr MSP, r5; bx r4` → 跳转

**验证判据**：
- PC 走到 0x0800055e（通过地址检查）→ 跳转逻辑正确
- PC 走到 0x08000640（返回）→ 地址检查失败，需检查 app 链接地址

## 1.7) 自由运行 vs 断点模式差异诊断

**症状**：断点模式下 Reset_Handler 被命中且 app 运行，但自由运行后 CPU 回到 bootloader idle_thread。

**诊断步骤（按优先级排列）**：

### A) RAM magic 值验证主循环是否真正执行
```bash
# 用 OpenOCD telnet 做精确时序控制
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c "gdb_port disabled; telnet_port 4444" >/dev/null 2>&1 &
sleep 2

{ echo "reset halt"; sleep 1
  echo "mww 0x2001c3c4 0xDEADBEEF"  # 写 magic 到 rtt_dbg_main_loop_iterations
  echo "resume"
  sleep 3
  echo "halt"; sleep 1
  echo "mdw 0x2001c3c4"              # 读回看是否变了
  echo "mdw 0xe000ed08"              # VTOR
  echo "shutdown"; } | nc -q2 localhost 4444 2>/dev/null
```
- **若仍为 0xDEADBEEF** → 主循环从未执行（app 在 init 阶段就崩了）
- **若变为非零递增值** → 主循环在跑，问题在后续阶段
- **⚠️ 切勿信任 RAM 变量的初始值！** 之前 47154 是上电残值，不是真正计数

### B) RCC_CSR 复位标志清除实验（区分真复位 vs 软跳转）
```bash
# 关键：先清标志再运行，只看新增的标志
openocd ... -c "
init; reset halt
mww 0x40023874 0x01000000   # 写 RMVF 位清除所有复位标志
mdw 0x40023874              # 确认清零
resume
sleep 3
halt
mdw 0x40023874              # 看哪些新标志被设置
shutdown"
```
**STM32F7 CSR 位定义**：
- bit 29 = IWDGRSTF, bit 28 = WWDGRSTF, bit 27 = PORRSTF
- bit 26 = SFTRSTF, bit 25 = BORRSTF, bit 24 = PINRSTF
- bit 0 = LSION, bit 1 = LSIRDY（低两位是 LSI 状态，不是复位标志！）
- **实测案例**：清除后 CSR=0x00000003（bit0+bit1=LSI），无任何复位标志 → 证明不是芯片复位

### C) 判断回到 bootloader 的机制
- **有新复位标志** → 芯片确实发生了复位（查 IWDG/WWDG/SFT/POR）
- **无新复位标志但 PC 在 bootloader** → app 中某路径跳回了 bootloader（不是复位）
- **Reset_Handler 尾部 `bx lr` 陷阱**：如果 `entry()` 提前返回，`bx lr` 跳到 bootloader 设置的 LR=0x3FE0 → 0x08003FE0（bootloader 区域）
- **异常 handler 跳转**：HardFault_Handler 可能执行了某种跳转回到 bootloader 的向量表

### D) Reset_Handler 返回路径保护
在 `startup_rtt_override.S` 的 Reset_Handler 中，`bl entry` 后的 `bx lr` 应改为死循环：
```asm
bl entry
dead: b dead    # 而非 bx lr — 防止 entry() 意外返回时跳到 bootloader
```

### E) WWDG/IWDG 排除检查
- 读 WWDG_CR（0x40002C00）bit7 WDGA → 0 表示未激活
- 读 IWDG_KR（0x40003000）→ 0x0000 表示未启动
- 检查 FLASH_OPTCR（0x40023C14）bit12 IWDG_SW → 1 = 软件模式（不自动启动）
- **app 的 `ap_rtt_iwdg_init()` 被 `#if 0` 禁用** — app 不会启动 IWDG

## 2) 读取异常帧（MSP/PSP + CFSR/HFSR）

GDB 连接后优先抓：
- `info reg`
- `x/8wx $msp` 与/或 `x/8wx $psp`
- `x/wx 0xE000ED28` (CFSR)
- `x/wx 0xE000ED2C` (HFSR)
- `x/wx 0xE000ED38` (BFAR)

关键判据：
- `stacked PC == 0x00000000` 通常是空函数指针/非法跳转，不是当前 `pc` 附近指令本体 fault。
- **basic frame 解码顺序固定**：`[sp+0]=r0, +4=r1, +8=r2, +12=r3, +16=r12, +20=LR, +24=PC, +28=xPSR`。
- 若 `xPSR` 也异常（如 `0x0`），优先按“调用目标指针被破坏/为空”处理，不要仅凭 backtrace 下结论。

## 3) 启动阶段定位（排除“运行后才fault”误判）

- 不使用 `rtt_dbg_main_loop_iterations` 作为时序依据（可为残值）。
- 用断点/反汇编确认调用链：
  `Reset_Handler -> entry -> rtthread_startup -> rt_hw_board_init -> rt_components_board_init -> rt_components_init`
- C++ ctor 在该项目里通过 `INIT_COMPONENT_EXPORT(rtt_run_cpp_ctors)` 触发，而非 `rt_hw_board_init` 直接调用。
- **若你在 startup 中“跳过了 __libc_init_array”**，仍要检查是否在 RT-Thread component init 阶段执行了 ctor（避免误以为 ctor 已完全禁用）。

## 4) DMA 调用链收敛

若停在 `HAL_DMA_Start_IT+4` 且参数全零：
- 枚举 `HAL_DMA_Start_IT` 调用者（SPI/UART/SDIO路径）
- 检查 IRQ 使能时序是否早于 handle/context 赋值

## 5) 已验证高概率根因模板：SPI4 DMA IRQ 时序违规

现象模板：
- `_spi_lld_board_init()` 中提前使能 SPI4 DMA NVIC
- `spi_bus_obj[x].lld` 要到 `rt_components_board_init()->rt_hw_spi_init()` 才赋值
- 期间 stale DMA pending IRQ 触发，走到未初始化句柄路径，最终 HardFault

A/B修复：
- 在早期 board init 只做 `spi_lld_register + spi_lld_bus_init`
- 不在该阶段启用 SPI4 DMA NVIC
- 让 `stm32_spi_init()`（对象/handle已就绪后）统一启用 NVIC

# 常见坑

1. `openocd reset halt` 看到旧 PC（如 DMA_SetConfig）
- 这可能是 reset 前残留现场，不代表新镜像已运行到该处。

2. 把 `_GLOBAL__sub_I...` 误判为根因
- 先看其反汇编是否只是简单静态写操作；很多时候它只是“受害者栈帧”。
- 若 stacked PC=0 且 stacked xPSR=0，优先追“空指针跳转”，不要因为 backtrace 里出现全局构造函数名就立刻假设是 C++ 静态初始化顺序问题。

3. 现场污染时继续相信高层回溯
- 优先信硬件 fault 寄存器 + 异常帧原始内存。
- 坏现场下 GDB 符号求值不可靠，用 `nm` 提取绝对地址后 `x/wx` 直接读。

4. **GDB 符号与实际代码不匹配**
- `reset halt` 后看到 PC=0x0810533c，GDB 可能显示为 `HAL_DMA_Start_IT+4`，但实际反汇编发现是 `SRV_Channels::set_aux_channel_default+72`。
- 这发生在 ELF 与实际烧录地址不一致时（如 ELF 链接在 0x08000000 但烧到 0x08008000）。
- **验证方法**：`arm-none-eabi-nm rt-thread.elf | grep 0810533c` 看符号归属，再 `x/4i 0x0810533c` 看实际指令。

5. **objcopy bin 的地址陷阱**
- `objcopy -O binary` 生成的 bin 从最低地址 section 开始。
- 如果 link.lds ROM=0x08008000 但 readelf 显示 LOAD VirtAddr=0x08000000（因为 section file offset 与 VirtAddr 对齐差），这是 ELF 元数据问题，bin 内容是正确的。
- **关键**：bin 偏移 0 = g_pfnVectors = 向量表。烧到 0x08008000 后 bin 偏移0 在 flash 0x08008000。
- **但 0x08000000 处仍是旧数据**！必须烧 ArduPilot bootloader 到 0x08000000。

5. **断点模式通过但自由运行失败 = 时序/中断竞争问题**
- 这是最难诊断的一类：GDB 断点降低了执行速度，改变了中断触发时序
- 典型根因：某个中断在初始化期间触发，handler 访问了未完成初始化的数据结构
- **历史案例**：SPI DMA 中断在 handle 赋值前触发 → 空指针 → HardFault
- **诊断策略**：
  1. 逐步缩小断点范围（从 entry → main → loop），找到最后一个命中的断点
  2. 在该断点之后的关键初始化路径上设硬件断点
  3. 如果在 HardFault_Handler 设断点也不命中但自由运行仍回 bootloader → 可能是 fault handler 本身有问题（跳回 bootloader 而非死循环）
  4. 最终手段：在 HardFault_Handler 中写调试变量（保存 PC/LR/MSP/PSP 到已知 RAM 地址）+ 死循环

6. GDB 批处理脚本容易"跑飞"导致超时
- 典型症状：`Cannot execute this command while the target is running`、`target not halted`、`continue` 后 180s 超时。
- 触发原因：在 `monitor reset run` 后直接 `continue`，又在未 `interrupt/halt` 的情况下执行 `info reg/x/ bt`。
- 建议做法：
  - 若要抓首次命中，先 `hbreak HAL_DMA_Start_IT`，再 `monitor reset run`，随后只 `continue` 一次；
  - 读取寄存器前先 `interrupt`（或 `monitor halt`）确认停机；
  - 更稳妥是分两段批处理：第一段“运行到断点/故障并停住”，第二段“只做寄存器与内存采样”。

## GDB 运行时变量检查（无调试串口时）

当没有调试UART（USB CDC全是MAVLink二进制数据）时，用 ST-Link + GDB 直接读取 volatile 全局变量：

**⚠️ ELF 文件路径**：`build/rtt_deploy/cuav_v5/rt-thread.elf`（不是 `build/rtt_cuav_v5/`！）

```bash
# 1. 启动 OpenOCD GDB server（禁用 telnet/tcl 减少干扰）
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "gdb_port 3333; telnet_port disabled; tcl_port disabled" &
OCPID=$!
sleep 2

# 2. 等板子运行一段时间积累数据
sleep 5

# 3. GDB attach 读变量（使用 target extended-remote 避免 warning）
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "print/x rtt_dbg_main_loop_iterations" \
  -ex "print/x rtt_dbg_hal_run_called" \       # 0x11111111=setup完成
  -ex "print/x rtt_dbg_main_loop_entry_called" \ # 0x12345678=main_loop已进入
  -ex "print/x rtt_dbg_loop_time_us" \
  -ex "print rtt_dbg_overrun_count" \
  -ex "print rtt_sd_mount_result" \            # -1=失败 0=未尝试 1=成功
  -ex "print/x rtt_dbg_loop_time_max_us" \
  -ex "print/x rtt_dbg_loop_time_min_us" \
  -ex "monitor reset run" \
  -ex "quit"

kill $OCPID 2>/dev/null; wait $OCPID 2>/dev/null
```

**注意**：GDB attach 会短暂 halt CPU（~1秒），不影响正常运行。变量必须是 `volatile` 的才能读到真实值。

**已知可检查的全局变量**：
- 定义在 `rt_board_init.c`：`rtt_sd_mount_stage`（1=init,10=dirs_created,<0=error）、`rtt_sd_mount_result`（-1=失败,0=未尝试,1=成功）、`rtt_cpu_idle_pct`（0~100空闲率）
- 定义在 `HAL_RTT_Class.cpp`：`rtt_dbg_main_loop_iterations`、`rtt_dbg_hal_run_called`（0x11111111=正常）、`rtt_dbg_main_loop_entry_called`（0x12345678=正常）、`rtt_dbg_loop_time_us`、`rtt_dbg_overrun_count`、`rtt_dbg_loop_time_max_us`、`rtt_dbg_loop_time_min_us`

## AP_Logger IO Thread 卡死诊断（反复重启）

### 症状
- STATUSTEXT: `AP_Logger: stuck thread ()` — IO 线程卡死
- `Loop: N/s timeavail=0` — 看门狗即将超时
- 反复出现 "Initialising ArduPilot"（50秒内重启6次）

### 根因分析
AP_Logger 的 IO 线程在 `_io_thread()` 中执行文件操作（open/write/fsync），
`last_io_operation` 变量标记当前操作。若 `io_thread_alive()` 检测到超时，
发送 CRITICAL STATUSTEXT "AP_Logger: stuck thread (%s)"。

**`last_io_operation` 为空字符串 "()" 说明卡在未标记的路径**——最可能是
`start_new_log()` 中的 `AP::FS().open()` 调用 RTT DFS elm fatfs 的 POSIX
`open()` 阻塞（FAT 表损坏/SD卡超时/DFS互斥锁死锁）。

### AP_Logger 结构体关键偏移（GDB 检查用）
```
AP_Logger (singleton at 0x2000ec90):
  backends@68(8B), _log_bitmask@76(4B), _armed@88(1B),
  _writes_enabled@90:0(bit), _force_log_disarmed@90:1(bit),
  _io_thread_started@91(1B), _next_backend@66

AP_Logger_File (backends[0]=0x20051a98):
  _write_fd@92(4B), _write_filename@96(4B ptr),
  _open_error_ms@124(4B), _log_directory@128(4B ptr),
  _have_ever_opened@133(1B), _last_write_failed@132(1B)
```

### RTT POSIX 文件系统限制
- `AP_Filesystem_Posix::disk_free()` / `disk_space()`: RTT 路径直接返回 -1（不阻塞）
- `::open()`, `::write()`, `::close()`, `::fsync()`: 通过 RTT DFS elm fatfs 实现
- **这些 POSIX 调用可能无限阻塞**（无超时机制），导致 IO 线程卡死
- `_log_directory="/APM/LOGS"` — 由 `rt_board_init.c` 在 SD 挂载后创建

### 调试方法
1. 在 `AP_Logger_File.cpp` 的 `start_new_log()` 的 open 失败路径加 STATUSTEXT:
   ```cpp
   char _dbgmsg[64];
   snprintf(_dbgmsg, sizeof(_dbgmsg), "LOG open fail errno=%d %s", saved_errno, strerror(saved_errno));
   GCS_SEND_TEXT(MAV_SEVERITY_INFO, "%s", _dbgmsg);
   ```
2. 用 pymavlink 捕获 STATUSTEXT（见下方 MAVLink 验证章节）
3. GDB 读 `_write_fd` 确认是否 -1（打开失败）
4. GDB 读 `_open_error_ms` 确认失败时间

### 临时解决方案（禁用日志以恢复稳定）
如果 logger 卡死导致系统不稳定，可临时在 hwdef.dat 添加：
```
define HAL_LOGGING_FILESYSTEM_ENABLED 0
```
或设置 MAVLink 参数 `LOG_DISARMED=0` 避免 disarm 状态下尝试写日志。

---

## MAVLink USB CDC 快速验证

烧录后等 12 秒，验证 app 通信：

```bash
# 1. 检查 USB 枚举
ls -la /dev/ttyACM*   # 应看到 ArduPilot_CUAVv5_RTT_RTTUSB0001

# 2. OpenOCD 干净复位 + 等 15 秒
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c 'init; reset run; shutdown'
sleep 15

# 3. 读原始 MAVLink 数据
timeout 3 cat /dev/ttyACM1 | xxd | head -15
# 应看到 0xFD (v2 sync) 和 0xFE (v1 sync)

# 4. 提取 STATUSTEXT 消息
timeout 10 cat /dev/ttyACM1 | strings -n 8 | sort -u
# 应看到 "Initialising ArduPilot", "Loop: N/s timeavail=..."
```

### pymavlink 快速验证（烧录后标准检查）

```python
from pymavlink import mavutil
import time

SERIAL = "/dev/serial/by-id/usb-ArduPilot_CUAVv5_RTT_RTTUSB0001-if00"
mav = mavutil.mavlink_connection(SERIAL, baud=57600)

# 1. 等心跳
hb = mav.recv_match(type='HEARTBEAT', blocking=True, timeout=10)
if not hb:
    print("NO HEARTBEAT"); exit(1)
print(f"HEARTBEAT: type={hb.type} autopilot={hb.autopilot} base_mode={hb.base_mode} status={hb.system_status}")

# 2. 收集消息统计（10秒）
start = time.time()
msgs = {}
while time.time() - start < 10:
    m = mav.recv_msg()
    if m is None: continue
    t = m.get_type()
    msgs[t] = msgs.get(t, 0) + 1
for t, c in sorted(msgs.items(), key=lambda x: -x[1]):
    print(f"  {t}: {c}")

# 3. 捕获 STATUSTEXT（检查错误）
mav2 = mavutil.mavlink_connection(SERIAL, baud=57600)
for i in range(100):
    m = mav2.recv_match(type='STATUSTEXT', blocking=True, timeout=2)
    if m:
        print(f"  [{m.severity}] {m.text}")
    else:
        break
mav.close(); mav2.close()
```

**正常输出应包含**：Loop: 275~315/s timeavail>0, PreArm: Motors: Check frame class and type
**异常输出**：AP_Logger: stuck thread, 反复 "Initialising ArduPilot"（重启循环）

**注意**：
- 复位后至少等 20~25 秒让板子启动完成再连接
- pymavlink 连接可能挂起，用 `pkill -9 -f pymavlink` 清理
- 不要用 `param_request_list` 配短超时——943 个参数会 BLOCK

## IOMCU 实现指南（下一阶段）

CUAV V5 的 RC/PWM 由 STM32F100 (IOMCU) 处理，主 MCU 通过 UART8 通信。

### 已完成的 hwdef 配置
```
PE0  UART8_RX  UART8 AF8
PE1  UART8_TX  UART8 AF8
IOMCU_UART UART8
define HAL_WITH_IO_MCU 1
```

### 待完成的代码适配
1. **rtt_hwdef.py 生成脚本**：处理 `IOMCU_UART` 行，生成 `HAL_UART_IOMCU_IDX`（参考 chibios_hwdef.py:1736）
2. **AP_IOMCU.cpp ChibiOS→RTT 适配**（6 处）：
   - `chEvtSignal(thread_ctx, mask)` → `rt_event_send(thread_ctx, mask)`
   - `chEvtWaitAnyTimeout(~0, chTimeMS2I(10))` → `rt_event_recv(thread_ctx, 0xFFFF, RT_EVENT_FLAG_OR|RT_EVENT_FLAG_CLEAR, rt_tick_from_millisecond(10), &recv)`
   - `chThdGetSelfX()` → `rt_thread_self()`
   - `eventmask_t` → `uint32_t`, `thread_t` → `rt_thread_t`
   - `ch.h` → RTT 头文件
3. **HAL_RTT_Class.cpp**：实例化 AP_IOMCU，将 RTT UART8 UARTDriver 传给它
4. **编译验证**：`python3 -m SCons --target=cuav-v5 -j16`

## FPU Lazy Stacking + Flash Write 冲突（2026-04-14 发现）

### 症状
- 固件 reset 后立即 HardFault，USB CDC 不枚举
- `halt` 后 PC 在 `hardfault_hang()`，mode = Handler HardFault

### 故障寄存器
- **HFSR** = `0x40000000` → FORCED (bit 30) — BusFault 升级为 HardFault
- **CFSR/BFSR** = `0x000A0000` → BFSR = `0x0A`
  - **Bit 3 LSPERR** = Lazy State Preservation Error (FPU 相关)
  - **Bit 1 PRECISERR** = 精确总线错误

### 崩溃调用链
```
AP_Param::save(bool) → AP_Param.cpp:1288 (delay_microseconds during flash wait)
  → RTT::Scheduler::_delay_microseconds_dwt()
    → PendSV thread switch 期间 FPU Lazy Preservation Error
```

### 涉及线程
- **from_thread** = SPI1 DeviceBus (`RTT::DeviceBus::_bus_thread_entry`)，栈含 `SPI1` magic
- **to_thread** = `_thread_sleep` (RTT idle)

### 根因分析
STM32F7 Cortex-M7 的 FPU Lazy Context 保存与 Flash 编程冲突：
1. `AP_Param::save()` 写 Flash 时 CPU 总线等待
2. 此时 PendSV 触发线程切换，尝试保存 FPU 上下文
3. LSPEN=1（Lazy Stacking 启用）时，FPU 上下文保存需要访问被 Flash 操作占用的总线
4. 触发 LSPERR + PRECISERR → FORCED HardFault

### 诊断方法（RTT 调试变量）
RTT 的 HardFault handler 将上下文保存到固定 RAM 地址：
```
0x20029a00  rt_interrupt_from_thread (源线程 SP)
0x20029a04  rt_interrupt_to_thread   (目标线程 SP)
0x20029a10  rtt_dbg_hardfault_frame_sp
0x20029a20  rtt_dbg_hardfault_stack_lr
0x20029a30  rtt_dbg_pendsv_enter_psp
0x20029a38  rtt_dbg_pendsv_exit_stack_pc
```

用 OpenOCD halt 后 GDB 读取：
```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "monitor halt" \
  -ex "x/32xw 0x20029a00" \
  -ex "x/4xw 0xE000ED28" \
  build/rtt_deploy/cuav_v5/rt-thread.elf
```

然后解码关键 PC：
```bash
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf -f -C <ADDR>
```

### 可能修复方向
1. **检查 RTT PendSV 汇编**：确认 FPU 上下文保存/恢复正确实现（context_gcc.S）
2. **Flash 操作期间禁用 LSPEN**：在 `AP_HAL_RTT/Flash.cpp` 的 erase/write 前设置 `FPU->FPCCR.LSPEN = 0`
3. **确认 RTT 配置**：`RT_USING_FPU` 已启用，PendSV handler 包含 FPU 分支
4. **临时绕过**：禁用 `AP_Param::save()` Flash 写入测试，先验证其他基础功能

### ⚠️ 注意：烧录后设备消失
固件 HardFault 在 USB CDC 初始化前/期间崩溃时，USB 设备不会枚举。
这是正常的——固件在 USB 初始化之前就崩了。不要误判为"烧录失败"。

### ⚠️ 注意：HardFault 后 OpenOCD 可能挂死

MCU 进入 HardFault 死循环后，OpenOCD 可能无法重新连接——卡在 `clock speed` 行后无响应。
**恢复方法**：USB reset ST-LINK 设备：
```bash
usbreset "STM32 STLink"   # 或 usbreset 0483:3748
sleep 2
# 然后重试 OpenOCD
```

### dump_image 替代 GDB 读取故障上下文

当 GDB 无法连接（OpenOCD 挂死时），可用 OpenOCD 的 `dump_image` 直接读取 RAM：

```bash
# 先 halt MCU（不用 reset，否则故障状态丢失！）
timeout 15 openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c '
init; halt;
dump_image /tmp/hf.bin 0x20029a00 80;
shutdown
'
# 解析二进制数据
xxd /tmp/hf.bin
# 然后用 addr2line 解码关键地址
arm-none-eabi-addr2line -f -e build/rtt_deploy/cuav_v5/rt-thread.elf <PC_ADDR>
```

**关键**：不要在 dump 前执行 `reset halt`——这会清除所有故障寄存器和调试变量！
只使用 `halt`（不复位）来保留现场。

### 查找 RTT 调试变量地址

RTT HardFault handler 保存上下文到 BSS 变量，地址可通过 ELF 符号表获取：

```bash
arm-none-eabi-objdump -t build/rtt_deploy/cuav_v5/rt-thread.elf | grep rtt_dbg_hardfault
```

输出示例：
```
20029a1c g  O .bss  00000004 rtt_dbg_hardfault_psp
20029a20 g  O .bss  00000004 rtt_dbg_hardfault_stack_lr
20029a24 g  O .bss  00000004 rtt_dbg_hardfault_stack_pc
20029a28 g  O .bss  00000004 rtt_dbg_hardfault_stack_xpsr
```

# 最小验收标准

- **构建烧录**：`python3 -m SCons --target=cuav-v5 -j16` 成功，ROM < 2016KB
- **App 启动**：等 12 秒后 `mdw 0xe000ed08` = 0x08008000（VTOR 正确）
- **主循环**：`mdw 0x2001c3c4` 为非零递增值
- **MAVLink**：`cat /dev/ttyACM1 | strings` 能看到 ArduPilot STATUSTEXT
- **IOMCU（目标）**：RC 通道值能通过 GCS 读取，RCOutput PWM 能驱动电调

## 回归 #1：FPU Lazy Stacking + Flash Write（2026-04-14 早期）

见上方"FPU Lazy Stacking + Flash Write 冲突"章节。HFSR=0x40000000, CFSR BFSR=LSPERR+PRECISERR。

## 回归 #2：ADC CMSIS GPIO MODER 滥用（2026-04-14 晚期）— 已解决

**⚠️ 初步诊断为 GPIO MODER 导致的 HardFault，实为 flash 地址偏移错误。**

### 实际根因：flash 写入地址错误

首次 OpenOCD halt 显示 `Handler HardFault, PC=0x080083da (hardfault_hang)`。
**根因是使用了 `flash write_image erase ... 0x08000000`**，把 app bin 写到了 bootloader 区域。
正确地址是 `0x08008000`（FLASH_ORIGIN）。

使用正确的 `program ... 0x08008000` 烧录后，**板子正常启动**：
- Thread 模式运行，PC 在 `DeviceBus.cpp:52`（主循环等待回调）
- USB CDC 枚举为 `/dev/ttyACM1`（VID:PID `1209:5741`）
- MAVLink 数据流通：Heartbeat type=2 (QUADROTOR), autopilot=3 (ArduPilot), status=1 (STANDBY)

### 烧录地址速查

| 命令 | 地址 | 说明 |
|------|------|------|
| `flash write_image erase rtthread.bin 0x08000000` | ❌ 错误 | 覆盖 bootloader → HardFault |
| `flash write_image erase rtthread.bin 0x08008000` | ❌ 危险 | 可能静默部分失败（见下方） |
| `program rtthread.bin 0x08008000 verify` | ✅ 推荐 | 快速且验证 |

### ⚠️ `flash write_image erase` 静默部分失败（2026-04-14 确认）

**症状**：OpenOCD 报告烧录成功，但实际上只写了第一个 32KB sector（0x08000000~0x08007FFF），其余 1.25MB 全为 0xFF（擦除状态）。

**诊断方法**：
```bash
# 烧录后立即验证：对比 flash 内容与 binary 文件
arm-none-eabi-gdb -batch \
  -ex "target remote :3333" \
  -ex "monitor halt" \
  -ex "x/4xw 0x08008000" \
  -ex "x/4xw 0x0810E000"
# 如果为 0xFFFFFFFF 则烧录不完整
```

**HardFault 特征**（flash 不完整时）：
- PC = `0xFFFFFFFE`（无效地址，不是正常的 HardFault PC）
- CFSR = `0x000A0001`：IACCVIOL + INVSTATE + NOCP
- HFSR = `0x40000000`：FORCED
- 异常帧中 PC=0xFFFFFFFE, LR=0xFFFFFFED（均为 EXC_RETURN 魔数值）
- **原因**：向量表中 Reset Handler 指向 0x0810E465 等高位地址，但该地址 flash 为 0xFF → 执行 0xFFFFFFFF → 非法指令 → HardFault

**根因**：`flash write_image erase` 对大容量固件（>1MB, ROM>93%）可能静默失败。可能是 erase 超时或 sector 写入超时。

**正确做法**：
```bash
# 始终使用 program + verify
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c "program build/rtt_deploy/cuav_v5/rtthread.bin 0x08008000 verify reset exit"
# 确认输出中有 ** Verified OK ** — 如果没有则烧录失败
```

**快速验证烧录完整性**：烧录后 halt 检查 app 向量表和 Reset Handler 地址处是否有有效指令（非 0xFFFFFFFF）：
```bash
arm-none-eabi-gdb -batch \
  -ex "target remote :3333" \
  -ex "monitor halt" \
  -ex "x/2xw 0x08008000" \
  -ex "x/2xw 0x08008364" \
  -ex "quit"
# 0x08008000 应为 SP + Reset Handler（非 0xFFFFFFFF）
# 0x08008364 应为 HardFault_Handler 指令（非 0xFFFFFFFF）
```

### GPIO MODER 注意事项（潜在风险，当前未触发）

`AnalogIn.cpp` 的 ADC 引脚初始化使用 `|=` 操作：

```c
GPIOA->MODER |= 0xFF;     // PA0-3 analog — 注意 PA4=SPI1_NSS，MODER bit8-9 被改为 11
GPIOB->MODER |= 0x3;      // PB0 analog
GPIOC->MODER |= 0x30F;    // PC0,PC1,PC4 analog
```

**当前未触发故障**，因为 `|=` 只能把位设为 1（analog=0b11），SPI NSS 等 push-pull 输出的 MODER=0b00 不会被 `|=` 破坏。但**如果同一端口有 MODER=0b10（AF）或 0b01（output）的引脚被意外覆盖，可能出问题**。建议后续改为精确位操作。

### ⚠️ GPIO 寄存器地址陷阱（STM32F7 vs STM32F4）

**STM32F7 的 GPIO 基址是 AHB1（0x4002_xxxx），不是 APB1（0x4000_xxxx）！**

| 外设 | STM32F4 | STM32F7 | 正确 F7 地址 |
|------|---------|---------|-------------|
| GPIOA | 0x40020000 | 0x40020000 | ✅ 相同 |
| GPIOB | 0x40020400 | 0x40020400 | ✅ 相同 |
| GPIOC | 0x40020800 | 0x40020800 | ✅ 相同 |
| GPIOD | 0x40020C00 | 0x40020C00 | ✅ 相同 |
| **GPIOE** | **0x40021000** | **0x40021000** | ✅ **非 0x40001800** |
| GPIOF | 0x40021400 | 0x40021400 | ✅ 相同 |
| GPIOG | 0x40021800 | 0x40021800 | ✅ 相同 |

**2026-05-12 教训**：使用 `0x40001800` 作为 GPIOE 基址（这实际上是 STM32F4 APB1 上的 TIM2 地址）。正确的 F7 GPIOE 地址是 `0x40021000`。用错误地址读了几个小时的 0x00000000，实际 PE3 一直是 OUTPUT HIGH，传感器电源正常工作。

**验证方法**：
```bash
# 用 nm 查外设基址
grep "GPIOE_BASE\|#define GPIOE" modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/packages/stm32f7_cmsis_driver-latest/Include/stm32f769xx.h | head -3
# 应为 #define GPIOE_BASE           (AHB1PERIPH_BASE + 0x1000)
# AHB1PERIPH_BASE = 0x40020000
```

### PE3 (VDD_3V3_SENSORS_EN) 断言策略

PE3 控制传感器 3.3V 电源轨，必须在所有 GPIOE 配置完成后 OUTPUT HIGH。

**2026-05-12 确认有效的修复**：在 `rt_board_init.c` 的 `rt_hw_board_init()` 末尾添加直接寄存器写：

```c
/* 必须在所有 INIT_BOARD/PREV_EXPORT 函数执行后执行，
 * 因为 SPI4 HAL_GPIO_Init 对 GPIOE 的 read-modify-write
 * 可能把 _sensor_power_init 设置的 PE3 OUTPUT 覆盖掉。 */
GPIOE->MODER = (GPIOE->MODER & ~GPIO_MODER_MODER3) | GPIO_MODER_MODER3_0;  // PE3=OUTPUT
GPIOE->BSRR = GPIO_BSRR_BS3;  // PE3=HIGH
```

注意 `_sensor_power_init()` 函数用 `rt_pin_mode/rt_pin_write`（RTT pin 驱动层）设置 PE3，但 `INIT_DEVICE_EXPORT` 级的 `_spi_device_board_init()` 会再次配置 GPIOE → 覆盖。**三保险**：
1. `_sensor_power_init`（INIT_PREV_EXPORT, level 2）
2. `_spi_device_board_init` 末尾追加 PE3 断言（INIT_DEVICE_EXPORT, level 3）
3. `rt_hw_board_init` 末尾直接寄存器写（最可靠）

### BSP_USING_SPI 编译条件陷阱

**问题**：`_spi_device_board_init()` 被 `#if defined(BSP_USING_SPI) && defined(HAL_RTT_SPI_ATTACH_LIST)` 保护。
但 `BSP_USING_SPI` 仅定义在 Kconfig 中（menuconfig 配置用），**不是 hwdef.py 生成的编译宏**。

**修复**：改为 `#if defined(HAL_RTT_SPI_ATTACH_LIST)`，因为 hwdef.h 由 `rtt_hwdef.py` 自动生成，`HAL_RTT_SPI_ATTACH_LIST` 在 hwdef.dat 有 SPI 设备时必定生成。

**验证**：
```bash
grep "BSP_USING_SPI\|HAL_RTT_SPI_ATTACH_LIST" build/rtt_deploy/cuav_v5/hwdef.h
# 应只看到 HAL_RTT_SPI_ATTACH_LIST

## 新问题：USB CDC 吞吐量极低（2026-04-14 晚期确认）

板子正常启动后，USB CDC 数据速率极低：

| 指标 | 实际值 | 预期值 |
|------|--------|--------|
| 数据速率 | 38 bytes/s | ~4400 bytes/s |
| 消息速率 | ~3.8 msg/s | ~44 msg/s |
| 10秒内消息类型 | 13种（每种仅1条） | 30种（每种多条） |
| Heartbeat | 收到1条 | 每1秒1条 |

### 诊断方法

```bash
# 重启板子后检查
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c 'init; reset run; shutdown'
sleep 12  # 等 bootloader 5s + app init 7s

# 原始 MAVLink 数据量检测（10秒）
python3 -c "
import serial, time, struct
s = serial.Serial('/dev/serial/by-id/usb-ArduPilot_CUAVv5_RTT_RTTUSB0001-if00', 57600, timeout=1)
start = time.time()
total = 0
while time.time() - start < 10:
    total += len(s.read(4096))
print(f'{total} bytes in 10s = {total/10:.0f} B/s')
s.close()
"
```

### UART7 调试串口同样无输出

RTT console 配置为 UART7，但 CH340 串口适配器（ttyACM0）在所有波特率下均读不到数据。
可能原因：UART7 引脚配置错误、CH340 未连接到正确引脚、或 RTT console 未初始化。

### pymavlink TypeError 绕过

pymavlink 2.4.48 的 `recv_match()` 调用 `post_message()` 时会触发 TypeError：
```
TypeError: 'NoneType' object does not support item assignment (in add_message)
```
**绕过方法**：用 `serial.Serial` 直接读原始字节，手动解析 MAVLink 头（0xFD/0xFE），或用 `m.recv_msg()` 代替 `m.recv_match()`。

### CFSR 快速判读表

| CFSR 值 | 含义 | 常见原因 |
|---------|------|----------|
| `0x00020000` | INVSTATE/UNDEFINSTR (UFSR bit1) | 执行非法指令或跳转到数据区（含 C++ vtable/查找表） |
| `0x00010000` | UNDEFINSTR (UFSR bit0) | 执行未定义指令（coprocessor 指令等） |
| `0x00040000` | **INVPC** (UFSR bit2) | **异常返回 PC 非法** — LR 或栈帧被破坏。常见于 MSP 栈溢出、嵌套中断破坏栈帧、中断 handler 返回时 bx lr 跳到无效地址 |
| `0x00080000` | INVPC (bit21) | 异常返回 PC 非法（旧位，UFSR 高位） |
| `0x000A0000` | INVSTATE+INVPC | 栈破坏/跳转到无效地址 |
| `0x00008200` | **PRECISERR + BFARVALID** (BFSR) | **精确数据访问总线错误** — 访问了无效内存地址，BFAR 含目标地址。常见于 C++ 虚函数调用时的野指针 vtable |
| `0x00000200` | PRECISERR (BFSR) | 精确总线错误（无 BFAR） |
| `0x00000400` | LSPERR (BFSR) | FPU Lazy Stacking 错误 |
| `0x000A0400` | INVSTATE+INVPC+LSPERR+PRECISERR | FPU+Flash 冲突 |
| `0x000A0001` | IACCVIOL+INVSTATE+NOCP | 栈帧破坏（PC=0xFFFFFFxx） |

### CFSR=0x00040000 — MSP 栈溢出导致 SysTick_Handler INVPC（2026-05-12 发现）

**特征**：
- CFSR = `0x00040000` (UFSR INVPC)
- HFSR = `0x40000000` (FORCED)
- PC 在 `hardfault_hang()` (0x080083ca)
- LR = `0xFFFFFFFD` (Exception return to Thread/PSP)
- **异常帧 PC = SysTick_Handler 中有效指令地址**（如 `0x080ea254` = `ldmia.w sp!, {r3, lr}`）
- 异常帧寄存器值异常：R0/ R3 含无效地址（如 `0x04e1834c`, `0x04e03ef5`）

**根因分析**：
虽然指令编码正确（`e8bd 4008` = `ldmia.w sp!, {r3, lr}` 完全有效），但执行时从 MSP 加载了错误的数据，导致 LR 被错误值覆盖。之后 `bx lr` 跳转到非法地址 → INVPC。

**关键发现：RT-Thread 重新配置了中断栈**
```
_estack (链接脚本定义) = 0x200054bc
MSP (运行时)         = 0x20071b0c  ← 远高于 _estack！
```

RT-Thread 在初始化阶段通过 `rt_hw_stack_init()` 重新配置 MSP 指向 SRAM1（非 DTCM），`_estack` 在 DTCM 中不再代表实际 MSP 顶部。因此**不能通过 `_estack` 判断栈溢出**，需直接检查 MSP 相对于 RT-Thread 分配的栈空间边界。

**诊断方法**：

```bash
# 1. 读取 MSP（中断 handler 栈指针）
echo "reg msp" | nc -q 2 localhost 4444

# 2. 找到 RT-Thread 中断栈分配地址
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep "stack\|_sp\|_estack\|hardware_stack\|_sstack\|int_stack\|isr_stack" | head -10

# 3. 如果 MSP 接近分配边界 → 栈溢出
# 常见 MSP 值：0x20071b0c（正常工作状态）
# 若 MSP < 0x20020000 → 进入 DTCM 区（可能意味着 MSP 已超出 SRAM1 范围）
```

**可能的根因**：
1. **嵌套中断深度过大**：SysTick 中断中发生高优先级中断（如 PendSV），两次入栈消耗过多 MSP
2. **中断 handler 栈分配不足**：RT-Thread 配置的 ISR 栈大小不够
3. **主线程延迟 + 嵌套中断**：在 `_delay_microseconds_dwt` 的 `dsb+循环` 中不断被中断打断，ISR 栈累积

**注意事项**：
- 此故障模式与 I-Cache 污染的症状不同（I-Cache 故障表现为 UNDEFINSTR 在非代码区域）
- 与 `0x00020000` (NULL 函数指针) 不同：INVPC 是指令本身不在代码区域，而非调用 NULL
- **多次 halt 检查**：如果每次 halt 的 PC 和异常帧内容不同，更指向栈溢出而非固定 bug

### CFSR=0x00008200 — GCS_MAVLINK UARTDriver vtable 破坏（2026-05-12 发现 + 2026-05-12 更新 + 2026-05-12 修复）

> 详细 session 记录见 `references/dtcm-bootloader-stale-data.md`

**特征**：
- CFSR = `0x00008200` (PRECISERR + BFARVALID)
- BFAR = `0x7936XXXX` 或类似 0x79 段地址（堆/栈垃圾值）
- PC 在 `GCS_MAVLINK::txspace()` 或 `check_payload_size()`
- LR 指向 `GCS_MAVLINK::try_send_message()`
- 反汇编链：`ldr r0, [r0, #0x1e4] → ldr r3, [r0] → ldr r3, [r3, #40] → blx r3`

**解码**：
```bash
addr2line -e rt-thread.elf -f -a <PC>          # 确认 txspace
arm-none-eabi-objdump -d rt-thread.elf --start-address=<PC-8> --stop-address=<PC+16>  # 看 ldr.in 链
grep "_port;" libraries/GCS_MAVLink/GCS.h        # offset 0x1e4
```

**根因定位方法**（优先级由高到低）：

#### 方法 1：复位后立即读 DTCM 检查 bootloader 残留
```bash
# 复位后 halt（MCU 还在 bootloader 中），读 UARTDriver 实例的 vtable
echo "reset halt" | nc -q 2 localhost 4444
echo "mdw 0x2000e510 4"      # serial1Driver vtable
```
- **判据**：若值 = 0x08122xxx（正确 vtable 范围）或 = 0x00000000（BSS 已清）→ 正常
- **若值 != 0 且 != 正确 vtable 地址** → **bootloader 在 DTCM 中遗留了脏数据！**
  - 常见错误值：0x08122960（"CUAVv5-RTT..." 板名字符串地址）
  - 根因：bootloader 使用 DTCM（0x20000000-0x2001FFFF）作为临时存储，跳转到固件后在 DTCM 中残留数据

#### 方法 2：Write Watchpoint 追踪 vtable 覆盖指令
```bash
# 1. 手动清零（模拟固件 BSS 清零后的状态）
echo "mww 0x2000e510 0x00000000" | nc -q 2 localhost 4444
# 2. 设置写入监视点
echo "wp 0x2000e510 4 w" | nc -q 2 localhost 4444
# 3. 恢复执行，等待触发
echo "resume" | nc -q 1 localhost 4444
# 4. 等待后 halt，检查 wp 是否触发
echo "halt" | nc -q 2 localhost 4444
```
- **注意**：DTCM 无 D-Cache，所有读写直接到真实内存（SRAM1 则有 D-Cache，OpenOCD/JTAG 读的是真实内存而非缓存行，可能导致读取值滞后）

#### 方法 3：检查相邻对象的缓冲区溢出
```bash
# UARTDriver 实例在 DTCM 的布局（RTT 实现）：
# 0x2000e004: ioUartDriver (1124B)
# 0x2000e468: utilInstance (168B) ← 紧邻 serial1Driver！
# 0x2000e510: serial1Driver ← vtable 很可能被 utilInstance 的写入溢出覆盖
```
常见 vtable 被覆盖的值模式：
- **0x08122960** → "CUAVv5-RTT..." 板名字符串 → `Util::board_name()` 的 snprintf 写入 utilInstance 溢出到 serial1Driver
- **0x7936XXXX** → 堆垃圾值 → UARTDriver 构造函数未运行或对象被回收

#### 方法 4：检查 `_drivers[]` 数组验证 UARTDriver 注册状态
```bash
arm-none-eabi-nm rt-thread.elf | grep "_ZN3RTT10UARTDriver8_driversE"
# 读取该地址看 _drivers[0] 和 _drivers[1] 是否指向有效的 serialDriver 实例
```

**可能根因**：
1. **Bootloader 在 DTCM 遗留脏数据，BSS 清零后残值仍在**
   - 测试：手动 BSS 清零 + 运行 → 若 crash 模式改变（stacked PC 从 txspace 变到 heap 区域），说明 bootloader 残留影响了初始化路径
2. **utilInstance（RTT::Util）缓冲区溢出覆盖 serial1Driver vtable**
3. **C++ 构造器在 BSS 清零前运行** → 构造器设置 vtable → BSS 清零清除 vtable → 虚函数调用时 vtable=0 → crash
   - 验证方法：检查 `startup_rtt_override.S` 中 BSS 清零与 `__libc_init_array` 的执行顺序
4. **检查 `.config` vs `rtconfig.h` 的 `RT_MAIN_THREAD_STACK_SIZE` 一致性**

#### 实际修复（2026-05-12 确认有效）

在 `startup_rtt_override.S` 的 Reset_Handler 中，BSS 清零完成后添加 **DSB + ISB + ICIALLU（全 I-Cache 无效化）** 屏障。虽然 D-Cache 在此平台已禁用（USB DWC2 DMA coherency 原因），但 I-Cache 可能保留了 bootloader 的残留数据，导致 BSS 清零和后续内存操作出现不确定行为。

```asm
    /* Zero-fill .bss — existing code */
    ldr   r2, =_sbss
    ldr   r4, =_ebss
    movs  r3, #0
    b     .L_LoopFillBss
.L_FillBss:
    str   r3, [r2]
    adds  r2, r2, #4
.L_LoopFillBss:
    cmp   r2, r4
    bcc   .L_FillBss

    /* === 添加的修复：内存屏障 + I-Cache 无效化 === */
    dsb                          /* 确保所有 STR 完成 */
    isb                          /* 刷新流水线 */
    movs  r0, #0
    mcr   p15, 0, r0, c7, c5, #0  /* ICIALLU — invalidate entire I-Cache */
    dsb
    isb

    bl    entry                 /* 跳转到 RT-Thread 启动 */
```

**验证结果**：修复后 serial1Driver vtable 正确为 `0x081228b8`（RTT::UARTDriver 虚函数表基地址），CFSR=0（无故障），USB CDC 成功枚举（`/dev/ttyACM1`）。

**为什么有效**：Bootloader（ArduPilot CUAVv5_bl.bin）在执行过程中可能污染 DTCM 和 I-Cache。固件 Reset_Handler 的 BSS 清零（STR 到 DTCM 地址）需要 DSB 确保完成，且 I-Cache 中缓存的 bootloader 指令可能与固件代码在虚拟地址空间上冲突。ICIALLU 确保固件启动时 I-Cache 中无残留。

#### vtable 验证（rtt_run_cpp_ctors 中）

在 `rt_board_init.c` 的 `rtt_run_cpp_ctors()` 中，`__libc_init_array()` 返回后立即验证所有 UARTDriver 实例的 vtable：

```c
extern uint32_t _ZL13serial1Driver[];
extern uint32_t _ZL13serial2Driver[];
uint32_t expected_vtable;
__asm__ volatile("ldr %0, =_ZTVN3RTT10UARTDriverE + 8" : "=r"(expected_vtable));
// serial1Driver[0] should == expected_vtable
if (_ZL13serial1Driver[0] != expected_vtable) {
    rt_kprintf("[CTOR] serial1 vtable CORRUPT (0x%08x != 0x%08x)\n",
        _ZL13serial1Driver[0], expected_vtable);
    _ZL13serial1Driver[0] = expected_vtable;  // 紧急修复
}
```

**注意**：vtable 破坏发生在 C++ 构造器运行之前或期间，该验证可以检测并在运行时修复。但根因修复仍需 I-Cache 屏障。
```asm
; Reset_Handler in startup_rtt_override.S
1. bl SystemInit
2. 复制 .data（flash → SRAM）
3. 清零 BSS（_sbss → _ebss）—— [serial1Driver] = 0
4. bl entry → rtthread_startup → rt_hw_board_init
5. INIT_COMPONENT_EXPORT(rtt_run_cpp_ctors) → __libc_init_array()
6. C++ 构造器执行 —— [serial1Driver] = 0x081228b8（正确 vtable）
7. 后续初始化中 vtable 被覆盖
```

### CFSR=0x00020000 — NULL 函数指针 → 数据表跳转模式（UFSR INVSTATE/UNDEFINSTR）

**特征**：
- CFSR = `0x00020000`（INVSTATE 或 UNDEFINSTR）
- 异常帧 PC 在 `.text` 范围内但 `objdump -d` 显示原始数据（不是有效 Thumb 指令）
- **LR = 0x00000000**（NULL 返回地址 → NULL 函数指针调用）
- `addr2line` 可能返回不相关的函数名（如 `scalbnf`），因为数据表紧跟在函数 literal pool 后面

### CFSR=0x00008200 — NOCP + UNDEFINSTR（FPU 未使能，常见于 bootloader 开了 D-Cache）

**特征**：
- CFSR = `0x00008200`（UFSR: NOCP bit7 + UNDEFINSTR bit1）
- HFSR = `0x40000000`（FORCED）
- PC 在 `hardfault_hang()` 死循环
- CPACR 物理读 `stm32f7x.cpu mdw phys 0xE000ED88 1` = `0x00000000`（FPU 未使能！）
- 但 CCR 中 DC bit = 0（D-Cache 看似关闭）

**根因**：Bootloader 在跳转到固件前使能了 D-Cache 和 FPU。固件 Reset_Handler 的 `SystemInit()` 用 C 代码 `SCB->CPACR |= ...`（read-modify-write）写 CPACR。由于 D-Cache 开启且 SCB 在 0xE000_xxxx 区域，写操作进入 D-Cache 而非物理寄存器 → FPU 实际上未使能 → 第一条 FPU 指令触发 NOCP fault。

**确认方法**：
```bash
# 1. 刚复位后读 CCP（D-Cache 状态）
echo "reset halt" | nc -q 2 localhost 4444
echo "stm32f7x.cpu mdw phys 0xE000ED14 1"  # CCR
# 复位时：0x00040200（DC=0, IC=0）

# 2. 让 bootloader 运行几秒后再次检查
echo "resume" | nc -q 1 localhost 4444; sleep 2
echo "halt
stm32f7x.cpu mdw phys 0xE000ED14 1" | nc -q 2 localhost 4444
# bootloader 运行后：0x00070200（DC=1, IC=1）→ D-Cache 已被 bootloader 开启！

# 3. 检查 CPACR 物理值
echo "stm32f7x.cpu mdw phys 0xE000ED88 1"
# 如果固件已经 HardFault 但 CPACR 物理仍 = 0x00000000 → 确认为此故障
```

> **注意**：普通 `mdw`（不带 `phys`）读取 SCB 寄存器可能返回 D-Cache 中的值（如 `0x00F00000`），只有 `stm32f7x.cpu mdw phys` 才能读到真实的物理寄存器值。这是导致最初误判 CPACR 已设置的原因。

**修复方法**（参考 ChibiOS `crt0_v7m.S` 模式）：

在 `startup_rtt_override.S` 的 Reset_Handler 中，在 `bl SystemInit` 之前：

```asm
    /* 1. 先关 D-Cache（bootloader 可能开了）*/
    mrc   p15, 0, r0, c1, c0, 0   /* 读 SCTLR */
    bic   r1, r0, #0x0004          /* 清 bit2(C)=关 D-Cache */
    mcr   p15, 0, r1, c1, c0, 0
    dsb; isb
    movs  r0, #0
    mcr   p15, 0, r0, c7, c14, #0 /* DCCSW: clean+invalidate 全 D-Cache */
    dsb; isb

    /* 2. 直接 STR 写 CPACR (0x00F00000) + DSB/ISB */
    ldr   r0, =0x00F00000
    ldr   r1, =0xE000ED88          /* SCB_CPACR */
    str   r0, [r1]
    dsb; isb

    /* 3. 清 FPSCR（放 canary：如果 CPACR 没生效，这条 vmsr 就是 NOCP fault）*/
    mov   r0, #0
    vmsr  FPSCR, r0

    /* 4. 清 FPDSCR */
    ldr   r1, =0xE000EF3C          /* SCB_FPDSCR */
    str   r0, [r1]

    /* 5. 设置 CONTROL.FPCA，让上下文切换保存 FPU 状态 */
    mrs   r0, CONTROL
    orr   r0, r0, #0x04            /* 设 FPCA 位 */
    msr   CONTROL, r0
    isb
```

**为什么 ChibiOS 的方法有效**：
- 用 `str r0, [r1]` **直接写入**（不是 read-modify-write `|=`），避免 C 编译器的读-改-写时序不确定性
- 每条写入后紧跟 `dsb; isb`，确保值到达物理寄存器
- `vmsr FPSCR` 是 FPU 指令——如果 CPACR 未生效，此处立刻 NOCP fault，定位精确
- CONTROL.FPCA 确保 PendSV 切换线程时会保存/恢复 FPU 上下文

**验证修复有效**：烧录后检查
```bash
echo "reset run" | nc -q 2 localhost 4444; sleep 20
echo "halt
stm32f7x.cpu mdw phys 0xE000ED88 1" | nc -q 2 localhost 4444
# 应为 0x00F00000 ✅ — FPU 物理寄存器已使能
# 系统不再 HardFault，setup_stage 能推进到 662+
```

**诊断流程**：

```bash
# 1. 确认 PC 处是数据而非代码
arm-none-eabi-objdump -d --start-address=<PC> --stop-address=<PC+16> <ELF>
# 如果输出是 .word 数据而非指令 → PC 在数据表中

# 2. 用 nm 找附近符号确认数据表归属
arm-none-eabi-nm -n <ELF> | awk '{ addr=strtonum("0x"$1); if (addr >= PC-0x100 && addr <= PC+0x100) print }'
# 常见数据表：cplus_demangle_operators, cplus_demangle_builtin_types, standard_subs

# 3. 用 addr2line 获取符号名（可能不准确但给出线索）
arm-none-eabi-addr2line -e <ELF> -f -C -a <PC>

# 4. 检查 LR 是否为 0（NULL 函数指针调用）
# 如果 LR=0 → 某处通过空 vtable 指针或空函数指针调用了代码
# 根因通常是：C++ 对象在构造函数完成前被使用，或 vtable 指针未初始化
```

**根因方向**：
1. C++ 全局构造函数执行顺序改变（新增代码引入新的 `INIT_COMPONENT_EXPORT` 依赖）
2. 虚函数通过未初始化的 vtable 调用（对象已分配但构造函数未运行）
3. 某个初始化函数被意外跳过或提前返回

**修复策略**：bisect 或 revert 最近 commits 到最后一个已知正常状态，逐个 re-apply 定位具体引入者。

### Flash 完整性验证方法（排除 flash 损坏）

当故障 PC 在 `.text` 范围内但内容可疑时，验证 flash 是否与 binary 一致：

```bash
# 1. 读取 binary 文件在故障地址的内容
# binary 偏移 = 故障地址 - FLASH_ORIGIN (0x08008000)
python3 -c "
import struct
with open('build/rtt_deploy/cuav_v5/rtthread.bin', 'rb') as f:
    f.seek(0x00107b00)  # 0x0810fb00 - 0x08008000
    data = f.read(16)
    words = struct.unpack('<4I', data)
    print(f'Binary: {[hex(w) for w in words]}')
"

# 2. 读取 flash 中同一地址的内容
arm-none-eabi-gdb -batch \
  -ex "target remote :3333" \
  -ex "mon halt" \
  -ex "x/4xw 0x0810fb00" \
  -ex "mon resume" \
  2>&1 | grep "0x0810fb"

# 3. 比较向量表头部确认整个 flash 匹配
# Flash: x/4xw 0x08008000 vs Binary: xxd -l 16 rtthread.bin
```

**关键注意**：
- `objdump -d` 对数据表区域会显示 `.word` 值（正确），但如果 objdump 使用了错误的重定位信息，可能显示完全不同的数据
- **始终以 `xxd` 读取 binary 文件 + GDB `x/Nxw` 读取 flash 的方式对比**，不要只依赖 objdump
- 向量表（0x08008000）是快速验证标志：如果向量表的 SP 和 Reset_Handler 匹配，说明至少 flash 的前 32 字节正确

### OpenOCD 直接读寄存器模式（无 GDB）

GDB 无法连接或不需要完整调试时，用 OpenOCD `-c` 链式命令直接读取：

```bash
# 注意：必须用多个 -c 参数，脚本文件(-f)对 mdw/reg 不产生输出
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c 'init' \
  -c 'halt' \
  -c 'reg' \
  -c 'mdw 0xE000ED24 1' \    # SHCSR
  -c 'mdw 0xE000ED28 1' \    # CFSR
  -c 'mdw 0xE000ED2C 1' \    # HFSR
  -c 'exit'
```

**⚠️ 不要用 `-f script.ocd` 方式**：OpenOCD halt 后不执行脚本文件中的后续 mdw/reg 命令（无输出）。
只有 `-c` 链式参数能正确产生输出。

### 异常帧从 MSP 恢复（Handler HardFault）

当 MCU 处于 Handler HardFault 状态时，异常帧压在 MSP 上：
- MSP 显示的是压栈后的值
- 异常帧在 MSP - 32 处（8 个 32-bit 字）
- 格式：`r0, r1, r2, r3, r12, LR, PC, xPSR`

```bash
# 假设 halt 后 MSP=0x2000D478
openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c 'init' -c 'halt' \
  -c 'mdw 0x2000D458 8' \    # MSP-32 = 异常帧起始
  -c 'exit'
```

**判读**：PC=0xFFFFFFC3 或 PC=0xFFFFFFFE → 栈帧被破坏，真实故障地址已丢失。
需检查 r9=0xDEADBEEF 等未初始化填充模式来确认栈破坏。

### CFSR=0x000A0001 的解码方法

```
CFSR = 0x000A0001
低字节 (MemManage) = 0x01 → IACCVIOL（尝试从无效地址取指）
高字节 (BusFault)  = 0x00 → 无总线错误
次高字节 (UsageFault) = 0x0A:
  bit 1 INVSTATE = EPSR.T 位无效（非 Thumb 指令）
  bit 3 NOCP = 协处理器不可用（FPU 未启用时使用 FPU 指令）
```

此模式通常表示：栈帧被破坏导致异常返回时加载了非法 PC 值（如 0xFFFFFFC3）。
**根因不是当前指令本身，而是更早发生的栈破坏**。需要追踪栈溢出或内存损坏的源头。

### 当前阻塞（2026-04-18 10:00 更新）

- **HardFault CFSR=0x00020000 NULL 函数指针**：MCU 每次启动都 crash（见回归 #4）
  - PC=0x0810fb00（cplus_demangle_operators 数据表），LR=0x00000000
  - 4 个 ADC commit 引入的回归，需 revert 到 7be92381ad 恢复
- USB CDC 热复位死锁（P0）：GRSTCTL CSRST+AHBIDL 死锁，详见 nightly-plan.md
- **未提交修改**：RCOutput.cpp（IOMCU io_enabled 检查）

### 任务进度跟踪（2026-04-18 10:00）

| # | 任务 | 状态 |
|---|------|------|
| 1 | 提交 AnalogIn CMSIS 直写 + hwdef ADC 定义 | ⚠️ done but 回归（引入 HardFault） |
| 2 | 烧录最新固件并验证主循环 | ❌ 阻塞（HardFault） |
| 3 | ADC/电池电压读数 | ❌ 阻塞 |
| 4 | RC Input SBUS 验证 | ❌ 待做 |
| 5 | Servo PWM Output 验证 | ⚠️ 部分完成（TIM 运行，未实际驱动） |
| 6 | AnalogIn ADC 与 SPI 冲突最终验证 | ❌ 阻塞 |
| 7 | SD卡/Logger 替代方案评估 | ✅ done |
| 8 | IWDG 看门狗启用验证 | ⏸️ 暂停 |
| 9 | GPS 验证 | ❌ 待做 |
| 10 | Frame 配置 + Motor Test | ❌ 待做 |
| 11 | 整体稳定性长时运行测试 | ❌ 待做 |

**最后已知正常状态**（commit 7be92381ad, 2026-04-15 16:09）：
- Flash: 1,283,820 / 2,064,384 = **62.2%**
- RAM: ~178KB / 512KB = **34%**
- 主循环: ~400Hz, CPU 0.9%, 531.8 msg/s, 19 种 MAVLink 消息类型

### 区分堆损坏 vs 堆耗尽

相同的 `rt_assert_handler` 循环可能是两种不同的根因：

| 特征 | 堆损坏 (heap corruption) | 堆耗尽 (heap exhaustion) |
|------|-------------------------|--------------------------|
| PC 位置 | 每次 halt 不同（`mem.c:299`、`ipc.c:1604` 等随机） | 固定在 `dev_serial.c:676` 或其他 `rt_malloc` 调用站 |
| 触发场景 | 启动后运行一段时间才 crash | 启动早期（serial open、thread create 等）立即触发 |
| assert 函数 | `rt_smem_alloc` / `rt_mutex_release` / 内存管理 | `rt_serial_open` → `RT_ASSERT(rx_fifo != RT_NULL)` |
| 根因 | 内存越界写导致 RT-Thread 内存管理元数据损坏 | `RT_MAIN_THREAD_STACK_SIZE` 过大吃光堆空间 |
| 修复 | 排查谁越界写了堆数据（DMA 边界、栈溢出） | 减小 `RT_MAIN_THREAD_STACK_SIZE`（64KB→8KB） |

**诊断命令**：
```bash
# 查看剩余堆空间
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep _end
# _end = HEAP_BEGIN, 检查 SRAM_END（rtconfig.h 或 board.h 中）

# 查看 RT_MAIN_THREAD_STACK_SIZE
grep RT_MAIN_THREAD_STACK_SIZE build/rtt_deploy/cuav_v5/rtconfig.h
```

**快速判断**：PC 卡在 `dev_serial.c:676` → 肯定是堆耗尽，不是损坏。

### 症状
- MAVLink 所有波特率无心跳（57600/115200/460800/921600）
- USB CDC 设备存在（ttyACM1），但串口无任何数据输出
- OpenOCD halt 后 MCU 在 Thread 模式（非 Handler），PC 在 Flash 范围

### 诊断流程

**Step 1：MAVLink 不通时立即用 OpenOCD 探测**

```bash
# 快速检查 MCU 状态
timeout 5 openocd -f interface/stlink.cfg -f target/stm32f7x.cfg \
  -c 'init; halt; exit' 2>&1
```

关键输出解读：
- `current mode: Thread` + PC 在 Flash = 不是 HardFault，是 RT-Thread assert 或死循环
- `current mode: Handler` + PC 在 Flash = HardFault（见上方流程）
- PC=0x08003xxx = 在 bootloader 中（等5秒跳转）

**Step 2：用 addr2line 定位崩溃函数**

```bash
arm-none-eabi-addr2line -e build/rtt_deploy/cuav_v5/rt-thread.elf <PC_ADDR>
```

本案例 PC=0x080ffade → `ipc.c:1604` = `rt_mutex_release` 中的断言

**Step 3：反汇编确认断言调用**

```bash
arm-none-eabi-objdump -d --start-address=<PC-8> --stop-address=<PC+32> \
  build/rtt_deploy/cuav_v5/rt-thread.elf
```

关键模式识别：
- `movw r2, #1604` → 行号压入 r2（这是 assert 的 ex_string/line 参数）
- `bl rt_assert_handler` → 跳转到 assert handler
- assert handler 实现为 `while(dummy==0)` 死循环 → MCU 永远卡在这里

**Step 4：区分 HardFault vs Assert 死循环**

| 特征 | HardFault | RT-Thread Assert |
|------|-----------|------------------|
| OpenOCD halt 显示 | `Handler HardFault` | `Thread` |
| PC 位置 | `hardfault_hang` 或随机 | `rt_assert_handler` |
| 可恢复性 | 需 reset | 需 reset |
| 故障寄存器 | CFSR/HFSR 有意义 | 通常正常 |
| 串口输出 | 无（可能在 USB init 前崩） | 无（assert handler 死循环） |

**Step 5：多次 halt 确认是固定位置还是随机**

```bash
# halt → resume → halt → 记录 PC
timeout 5 openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c 'init; halt; exit'
# 如果每次 halt PC 不同 → 可能是随机内存损坏
# 如果每次 halt PC 相同 → 固定 assert 条件触发
```

本案例两次 halt 分别在：
1. PC=0x080ffade → `ipc.c:1604` = `rt_mutex_release` 类型检查失败
2. PC=0x08100480 → `mem.c:299` = `rt_smem_alloc` 分配器断言

**两次不同位置 = 系统性堆内存损坏（heap corruption）**

### 堆损坏根因排查方向

1. **USB CDC TX 竞态修复不够完整** — PRIMASK 保护了 `usbd_serial_write` 路径，但 ISR 中的其他路径可能未保护
2. **线程栈溢出** — 虽然 Logger IO 线程已从 4KB→8KB，其他线程可能仍有溢出
3. **DMA 缓冲区 aliasing** — USB CDC DMA 与内存分配器区域重叠
4. **SPI/USB DMA 写越界** — 损坏相邻堆元数据

### 诊断工具

**检查各线程栈使用量**（GDB attach 后）：
```bash
arm-none-eabi-gdb -batch \
  -ex "target extended-remote :3333" \
  -ex "file build/rtt_deploy/cuav_v5/rt-thread.elf" \
  -ex "monitor halt" \
  -ex "call rt_thread_print_all()" \
  -ex "quit"
```

**启用 RTT 内存调试**（rtconfig.h）：
```c
#define RT_DEBUGING_MEMORY 1
#define RT_USING_MEMTRACE 1
```

### OpenOCD `reset run` 注意事项

`openocd -c 'init; resume; exit'` 不会真正恢复执行（因为 OpenOCD halt 后的 resume 需要先 `reset`）。
使用 `reset run` 命令复位并自由运行：
```bash
timeout 8 openocd -f interface/stlink.cfg -f target/stm32f7x.cfg -c 'init; reset run; exit'
```

### USB CDC TX Endpoint 竞态修复详情（commit 9e6edb35dc）

CherryUSB CDC 的 `usbd_serial_write` 和 XFRC 中断处理（`usbd_cdc_acm_bulk_in`）可能同时调用 `kick_tx`，导致两个 `usbd_ep_start_write` 同时操作同一个 endpoint 的 EPENA，造成端点永久卡住。

修复：PRIMASK 保存/恢复保护 `tx_active` 检查和 `kick_tx` 调用：
```c
{
    volatile uint32_t primask;
    __asm volatile("mrs %0, primask" : "=r"(primask));
    __asm volatile("cpsid i" ::: "memory");
    if (!serial->tx_active) {
        usbd_serial_kick_tx(serial);
    }
    __asm volatile("msr primask, %0" :: "r"(primask) : "memory");
}
```

### Logger IO 线程栈溢出修复（commit 86e3c2cc6a）

AP_Logger IO 线程在 RTT 上 4KB 栈不足，EKF 初始化期间递归调用耗尽。
增大到 8KB（`libraries/AP_Logger/AP_Logger.cpp` HAL_LOGGING_STACK_SIZE）。

## 回归 #4：ADC Commits 引入 NULL 函数指针 → 数据表 UNDEFINSTR（2026-04-18）

### 症状
- 固件烧录后每次启动都 HardFault，MAVLink/USB CDC 不可用
- MCU 卡在 `hardfault_hang()` (PC=0x080083ca)

### 故障寄存器
- **CFSR** = `0x00020000` → UFSR INVSTATE/UNDEFINSTR
- **HFSR** = `0x40000000` → FORCED
- **PRIMASK** = 1（中断已禁用）

### 异常帧
- **PC** = `0x0810fb00` — `cplus_demangle_operators+0x1e4`（C++ name demangling 数据表）
- **LR** = `0x00000000` — **NULL 返回地址**
- **R0** = `0x40020c00` (GPIOI 基址)
- **SP** = `0x2000d4a0` (DTCM)
- **xPSR** = `0x80000200` (Thumb bit set)

### 根因分析
4 个 ADC 相关 commit（8708fdb636, 4c8cd5824e, 2625f93590, e4ed44ef75）在已知正常状态（7be92381ad）之后引入。
代码通过 NULL 函数指针跳转到了 C++ 数据表区域，触发非法指令 fault。

### 验证过程
1. `addr2line` 显示 `scalbnf` — 误导（该函数的 literal pool 紧邻数据表）
2. `nm -n` 确认 0x0810fb00 在 `cplus_demangle_operators` 表中
3. `objdump -d` 显示 `.word` 数据而非指令
4. Flash 与 binary 对比确认内容一致（排除 flash 损坏）
5. 向量表对比确认烧录正确

### 修复方向
1. `git revert` 4 个 ADC commit 或 `git bisect` 定位具体引入者
2. 逐个 re-apply ADC 改动，确保每个都能正常启动
3. 检查 AnalogIn.cpp 新增的 `#include <rtthread.h>` 是否引入了新的初始化依赖
4. 检查未提交的 RCOutput.cpp 改动（`AP_BoardConfig::io_enabled()` 检查）

### 经验教训
- **CFSR=0x00020000 + LR=0** 的组合高度提示 NULL 函数指针调用
- `addr2line` 在数据表区域可能返回前一个函数名（如 `scalbnf`），需用 `nm -n` 精确定位
- **先验证 flash 完整性再分析代码逻辑**：对比 binary 文件与 flash 实际内容

### 快速固件占用计算

```bash
arm-none-eabi-size build/rtt_deploy/cuav_v5/rt-thread.elf
# text       data     bss     dec
# 1280724    5252     173012  1458988

# STM32F767: 2MB Flash, 512KB RAM
# Flash = text + data = 1,285,976 / 2,097,152 = 61.32%
# Static RAM = data + bss = 178,264 / 524,288 = 34.00%
```
