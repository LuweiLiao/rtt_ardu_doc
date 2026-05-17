# App Descriptor 根因：scons 缺少 set_app_descriptor() 后处理

> 2026-05-16 A1-Research 重要更新：此前本文件描述的三层修复（ifdef + macro + link.lds）是不完整的。
> 实际根因是 scons 缺少 ChibiOS waf 的 set_app_descriptor() 后处理脚本。

## 实际症状

搜索 app_descriptor 签名发现**签名存在但字段全零**：

```python
with open('build/rtt_cuav_v5/rtthread.bin', 'rb') as f: d = f.read()
sig = bytes([0x40, 0xa2, 0xe4, 0xf1, 0x64, 0x68, 0x91, 0x06])
pos = d.find(sig)  # -> 0x107b00 签名存在
```

实际值：
- board_id=50 正确
- image_size=0 错误
- image_crc1=0 错误
- image_crc2=0 错误
- git_hash=0 错误

## Bootloader 拒绝原因

在 Tools/AP_Bootloader/AP_Bootloader.cpp 中 check_good_firmware_unsigned()：
1. memmem() 找到签名 -> 通过
2. 读 board_id=50 -> 通过
3. 读 image_size=0 -> len1+desc_len(1080088) > image_size(0) -> FAIL_REASON_BAD_LENGTH_DESCRIPTOR
4. Bootloader 保持 firmware-upload 模式，不调用 jump_to_app()

## 完整 4 层修复（缺一不可）

| 层级 | 文件 | 修改 | ChibiOS 参考 |
|------|------|------|-------------|
| 1. 编译 | hwdef/cuav_v5/hwdef.dat | define AP_CHECK_FIRMWARE_ENABLED 1 + APJ_BOARD_ID TARGET_HW_PX4_FMU_V5 | chibios_hwdef.py 强制写入 hwdef.h |
| 2. Section | AP_CheckFirmwareDefine.h | #if 条件加 HAL_BOARD_RTT | 使 .app_descriptor 属性生效 |
| 3. 链接 | link.lds | KEEP(.apsec_data); KEEP(.app_descriptor); | common.ld:76 |
| 4. 后处理 | 新建脚本 + scons 集成 | 复制 chibios.py:266-328 的 set_app_descriptor() | Tools/ardupilotwaf/chibios.py:266-328 |

## 后处理脚本原理

ChibiOS waf 的 set_app_descriptor()（chibios.py:266-328）：
1. objcopy 生成 .bin
2. 搜索 8 字节签名
3. 计算 CRC/size/git_hash
4. 直接写入 bin 文件对应偏移

RTT scons 需要在 SConscript 中增加类似的后处理步骤。

## 验证

```bash
# 检查字段非零
python3 -c "
import struct, zlib
with open('build/rtt_cuav_v5/rtthread.bin', 'rb') as f: d = f.read()
sig = bytes([0x40, 0xa2, 0xe4, 0xf1, 0x64, 0x68, 0x91, 0x06])
pos = d.find(sig)
off = pos + 8
bid, _, crc1, crc2 = struct.unpack_from('<IIII', d, off)
sz, gh_lo, gh_hi = struct.unpack_from('<III', d, off + 16)
print(f'sig at 0x{pos:x}: bid={bid} sz={sz}/{len(d)} crc1=0x{crc1:08x} crc2=0x{crc2:08x}')
"

# 烧录验证
openocd -f Tools/debug/openocd-f7.cfg -c "program build/rtt_cuav_v5/rtthread.bin 0x08008000 verify reset exit"
sleep 15
ls /dev/ttyACM*
python3 -c "from pymavlink import mavutil; m=mavutil.mavlink_connection('/dev/ttyACM1'); m.wait_heartbeat(timeout=10); print(f'HEARTBEAT: status={m.status}')"
```
