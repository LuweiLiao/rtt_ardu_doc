# 重复 app_descriptor 与 Bootloader 扇区擦除陷阱 — 调试全记录

> 发现日期：2026-05-16
> 场景：Phase 1.1 SPI 优先级调整后重新烧录，板子完全无法启动
> 最终确认（17:39）：**重复 descriptor 是红鲱鱼，真正根因是 bootloader 扇区未擦除**

## 现象

- Bootloader(0x08000000) + app(0x08008000) 均烧录 + verify OK
- 但 CPU 启动后 halt 显示 HardFault
- USB CDC 完全不重新枚举（ACM1 时间戳不变）
- 直接 OpenOCD 设置 SP/PC 跳转到 app 同样 HardFault

## 故障寄存器

```
HFSR (0xE000ED2C) = 0x40000000 → FORCED (escalated)
CFSR (0xE000ED28) = 0x00028200
  BFSR: 0x02 → PRECISERR (精确数据总线错误)
  UFSR: 0x82 → INVSTATE = 1 (非法指令状态)
BFAR (0xE000ED38) = 0x00004000 → 错误地址 (aliased to 0x08004000, bootloader 区域内)
```

PC=0x2000002e (RAM)，MSP=0x200055c8。BFAR=0x4000 表明 bootloader 在访问自身代码区时读到损坏的数据。

## 真正根因  ✅（2026-05-16 17:39 最终确认）

### Bootloader 扇区未擦除导致写入失真

**问题**：`flash write_image CUAVv5_bl.bin 0x08000000`（**不带** `erase` 标志）**不自动擦除扇区**。bootloader 占用 16KB，但 flash 扇区为 32KB。未擦除的扇区尾部残留旧数据（前次 mass erase 或不同固件的数据），导致 bootloader 在初始化时读到损坏的配置，立即 HardFault。

**验证**：单独擦除 bootloader 扇区（`flash erase_address 0x08000000 0x10000`）后再写，系统正常启动。

### 为什么 verify OK 但运行失败？

OpenOCD 的 `verify_image` 只比较**已写入区域**（16440 字节），不检查扇区**未写入部分的残留数据**。所以 verify 通过，但运行失败。

## ❌ 红鲱鱼：重复 app_descriptor

### 最初发现的异常

二进制中确实有两份 app_descriptor 签名：

```
offset 0x1f8 (flash 0x080081f8): ✅ 已补丁 (crc1=0x83ba1117)
offset 0x21c (flash 0x0800821c): ❌ 全零 (version=3.1, board=50 → ChibiOS 遗留)
```

### 为什么这是红鲱鱼

CUAV V5 bootloader（基于 `AP_Bootloader`/`AP_CheckFirmware.cpp:138`）使用 **`memmem()`** 扫描 app_descriptor：

```c
const app_descriptor_unsigned *ad = (const app_descriptor_unsigned *)
    memmem(flash1, flash_size - sizeof(app_descriptor_unsigned), sig, sizeof(sig));
```

`memmem()` 从 app 基址（0x08008000）**向后扫描，返回第一个匹配**。第一份签名在 0x080081f8（CRC 正确），第二份在 0x0800821c（全零）。bootloader 永远找到第一份，不会受到第二份影响。

### 验证方法

```bash
# 读取 flash 中两个位置的 app_descriptor
openocd -c "mdw 0x080081f8 12"    # 第一个（已补丁）
openocd -c "mdw 0x0800821c 12"    # 第二个（全零但无关紧要）

# 分析二进制中的重复签名
python3 -c "
import struct
with open('build/rtt_deploy/cuav_v5/rtthread.bin', 'rb') as f:
    data = f.read()
target = b'\\x40\\xa2\\xe4\\xf1\\x64\\x68\\x91\\x06'
pos = 0
while True:
    pos = data.find(target, pos)
    if pos < 0: break
    vals = struct.unpack('<QIIII', data[pos:pos+24])
    print(f'offset 0x{pos:x}: crc1=0x{vals[1]:08x} crc2=0x{vals[2]:08x} size={vals[3]}')
    pos += 1
"
```

### 为什么有两个签名？

第二次签名在 offset 0x21c 处，距离第一个签名刚好 36 字节。两个可能：

1. **链接器重复** — 源文件中有两个 `__attribute__((used, section(".app_descriptor")))` 定义
2. **数据区巧合** — 某些 ROMFS 或数据恰好包含这 8 字节魔数

不重要——**bootloader 不受影响**，无需修复。

## 正确烧录流程（避免重蹈覆辙）

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

**注意**：`flash erase_address 0x08000000 0x10000` 擦除 64KB（0x08000000-0x0800FFFF），app 的前 32KB（含向量表起始部分）也被擦除。所以**必须同时重写 bootloader + app**。

## 诊断流程图

```
系统不启动 + USB CDC 不枚举
│
├─ halt 显示 HardFault?
│  ├─ PC=0xFFFFFFFE / SP=0xFFFFFFFC → flash 空白
│  └─ PC=0x2000002e, BFAR≈0x00004000 → bootloader 扇区未擦除
│     └─ 修复：flash erase_address 0x08000000 0x10000 → 重写 bootloader + app
│
└─ halt 正常 (no HardFault)？
   └─ 检查 app_descriptor → 可能是重复 descriptor 影响（概率极低）
```

## 经验教训

1. **`verify_image` 不能替代运行测试** — verify 只比较写入部分，扇区尾部残留不会被检测
2. **`flash write_image` 不带 `erase` 不擦扇区** — 必须先 `flash erase_address`
3. **`flash write_image erase` 做 mass erase** — 太快但会擦掉 bootloader，需重写
4. **遇到 HardFault 先查 BFAR** — BFAR=0x4000 明确指向 bootloader 扇区问题
5. **单靠 halt 查 VTOR 不能区分"bootloader 跳转到 app 后 crash"和"bootloader 自身 crash"** — 因为 app 的 Reset_Handler 第一件事就是设置 VTOR
