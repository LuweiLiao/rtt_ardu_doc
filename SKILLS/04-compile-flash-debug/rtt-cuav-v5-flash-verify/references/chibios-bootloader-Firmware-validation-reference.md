# ChibiOS Bootloader 固件验证逻辑参考

> 来源：`libraries/AP_CheckFirmware/AP_CheckFirmware.cpp`
> 用途：理解 CUAV V5 bootloader 如何验证 app_descriptor，辅助 RTT 移植调试

## 关键函数

### `get_app_descriptor()` (line 204)

```c
const app_descriptor_t *get_app_descriptor(void)
{
    const uint8_t sig[8] = AP_APP_DESCRIPTOR_SIGNATURE_UNSIGNED;
    const uint8_t *flash1 = (const uint8_t *)(
        FLASH_LOAD_ADDRESS + (FLASH_BOOTLOADER_LOAD_KB + APP_START_OFFSET_KB)*1024);
    const uint32_t flash_size = (BOARD_FLASH_SIZE - (FLASH_BOOTLOADER_LOAD_KB + APP_START_OFFSET_KB))*1024;
    const app_descriptor_t *ad = (const app_descriptor_t *)
        memmem(flash1, flash_size-sizeof(app_descriptor_t), sig, sizeof(sig));
    return ad;
}
```

- **扫描范围**：从 `FLASH_LOAD_ADDRESS + APP_START_OFFSET_KB` 开始
  - CUAV V5 (fmuv5): `FLASH_LOAD_ADDRESS=0x08000000`, `APP_START_OFFSET_KB=32` → 0x08008000
  - `flash_size` = 2016KB (2MB - 32KB bootloader)
- **扫描算法**：`memmem()` — 标准 C 字符串搜索，**返回第一个匹配**
- **签名**：`0x40a2e4f164689106`（UNSIGNED）

### `check_good_firmware_unsigned()` (line 133)

```c
static check_fw_result_t check_good_firmware_unsigned(void)
{
    // 1. memmem 找签名
    ad = memmem(flash1, flash_size-sizeof(app_descriptor_unsigned), sig, sizeof(sig));
    if (ad == nullptr) return NO_APP_SIG;

    // 2. 检查 image_size
    if (ad->image_size > flash_size) return BAD_LENGTH_APP;

    // 3. 检查 board_id
    if (ad->board_id != APJ_BOARD_ID) return BAD_BOARD_ID;
    // CUAV V5: APJ_BOARD_ID = TARGET_HW_PX4_FMU_V5 = 50

    // 4. 检查描述符长度
    desc_len = offsetof(app_descriptor_unsigned, version_major) - offsetof(app_descriptor_unsigned, image_crc1);
    // desc_len = 24 - 8 = 16
    len1 = ((const uint8_t *)&ad->image_crc1) - flash1;
    // len1 = 距离 flash 起始到 image_crc1 字段的字节数
    if ((len1 + desc_len) > ad->image_size) return BAD_LENGTH_DESCRIPTOR;

    // 5. CRC 校验
    len2 = ad->image_size - (len1 + desc_len);
    crc1 = crc32_small(0, flash1, len1);      // flash 开始 → image_crc1 字段
    crc2 = crc32_small(0, flash2, len2);      // version_major → 文末
    if (crc1 != ad->image_crc1 || crc2 != ad->image_crc2) return BAD_CRC;

    return CHECK_FW_OK;
}
```

### `check_good_firmware()` (line 175)

```c
check_fw_result_t check_good_firmware(void)
{
#if !AP_SIGNED_FIRMWARE
    const auto ret = check_good_firmware_unsigned();
    if (ret != CHECK_FW_OK) {
        // 允许回退到 signed 格式（支持无签名 bootloader 启动有签名固件）
        const auto ret2 = check_good_firmware_signed();
        if (ret2 == CHECK_FW_OK) return CHECK_FW_OK;
    }
    return ret;
#else
    // 类似，signed 优先，回退 unsigned
#endif
}
```

## CUAV V5 关键常量

| 常量 | 值 | 来源 |
|------|-----|------|
| `FLASH_LOAD_ADDRESS` | 0x08000000 | hwdef/dat |
| `FLASH_BOOTLOADER_LOAD_KB` | 32 | hwdef-bl.dat: `FLASH_BOOTLOADER_LOAD_KB 32` |
| `APJ_BOARD_ID` | 50 = `TARGET_HW_PX4_FMU_V5` | hwdef-bl.dat |
| `BOARD_FLASH_SIZE` | 2048 (2MB) | hwdef-bl.dat |

## RTT 的 app_descriptor 打补丁脚本

- `Tools/scripts/rtt_app_descriptor.py` — 补丁 .bin + .elf
- `Tools/scripts/rtt_set_app_descriptor.py` — 备用版本

补丁逻辑（`rtt_app_descriptor.py`）：
```python
offset = img.find(UNSIGNED_SIG)  # 只补第一个匹配！
# CRC1 = CRC(data[:offset+8])   从 flash 起始到 sig 结束
# CRC2 = CRC(data[offset+24:])  从 version_major 到文件末尾
# img_size = len(img)
```

**重要**：脚本只修补**第一个**签名匹配。如果二进制中有多个签名（RTT 链接器产生），第二个不会被修补。但这不影响 bootloader 验证（详见 `duplicate-app-descriptor-debug.md`）。

## 调试检查点

```bash
# 检查 bootloader 验证是否通过（复位后立即 halt，看 PC 是否在 0x08008000+）
openocd -c "halt" -c "reg pc" -c "reg sp"
# 如果在 0x0800xxxx+app 区域 = bootloader 已跳转
# 如果在 0x0800xxxx+bootloader 区域 = bootloader 未跳转

# 检查 app_descriptor 完整性
openocd -c "mdw 0x080081f8 12"
# 期望: sig + crc1 + crc2 + size + hash + version + board_id
# crc1 必须非零

# 检查 VTOR
openocd -c "mdw 0xE000ED08 1"
# 期望: 0x08008000 (app 向量表)
```
