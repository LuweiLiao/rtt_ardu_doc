# Bootloader Jump_to_App 反汇编验证（2026-05-14 会话分析）

## 跳转前检查逻辑

`CUAVv5_bl.elf` 的 `_Z11jump_to_appv` 函数（0x0800052c）执行以下验证：

```asm
0800052c <_Z11jump_to_appv>:
  ; r3 = app_base_ptr (from flash_init, e.g. app_base in flash)
  ; r1 = flash_end (scan limit)

loop_start (0x08000534):
  r3 += 4              ; advance to next word
  r2 = *r3             ; read word from flash
  r2 += 1              ; check if == 0xFFFFFFFF (erased)
  if r2 == 0 → return  ; flash erased, don't jump

  if r3 != r1 → loop   ; continue scanning until flash end

; All words from app_base to flash_end are non-erased
; → perform Reset_Handler range check

0x08000544-0x0800055c:
  r4 = *(literal)      ; address of Reset_Handler value in RAM
  r3 = *(literal)      ; limit1 (bootloader end = 0x08007FFF)
  r2 = *r4             ; Reset_Handler = *(app_base + 4)
  if r2 <= 0x08007FFF → FAIL (Reset_Handler in bootloader area)

  r3 = *(flash_geometry + 8)  ; flash total size or image size limit
  r3 += FLASH_BASE (0x08000000)
  r3 += 0x8000 (32KB bootloader reserve)
  if r2 >= r3 → FAIL (Reset_Handler beyond flash end)

; All checks passed → prepare jump
0x0800055e-0x080005ea:
  flash_set_keep_unlocked(false)
  
  ; Clear NVIC (ICER/ICPR)
  *0xE000E180 = 0; *0xE000E184 = 0;  ; ICER[0..1]
  *0xE000E280 = 0; *0xE000E284 = 0;  ; ICPR[0..1]
  
  ; Write VTOR
  *0xE000ED08 = app_base (0x08008000)
  
  ; Read SP and Reset from vector table
  SP = *(app_base)     ; 0x200054bc
  PC = *(app_base + 4) ; 0x080f0071 (with Thumb bit)
  
  ; Disable MPU
  *0xE000ED94 = 0  ; MPU_CTRL = 0
  
  ; Set CONTROL = 0 (privileged, MSP)
  msr CONTROL, r0=0
  
  ; Jump
  bx PC  ; branch to Reset_Handler
```

## bootloader 不启用 D-Cache

反汇编 `__early_init` (0x0800376c) 和 `__core_init` (0x080028a4) 确认：

- `__core_init` 写 `0xE000EF50 (SCB_CCR) = 0` → **禁用 I-Cache、D-Cache、所有预取特性**
- `__early_init` 只做 GPIO 时钟 + 引脚初始化 + `stm32_clock_init()`
- 全程无 `0xE000ED30 (SCTLR)` 访问 → 不启用 D-Cache
- **bootloader 跳转前 D-Cache 是关闭的** → 不需要在应用启动代码中添加 D-Cache 禁用

## app_descriptor 对跳转的影响

**bootloader 的 `jump_to_app()` 不验证 app_descriptor。** 只检查：
1. Flash（从向量表地址开始）非全擦除（存在有效数据）
2. Reset_Handler 地址在有效范围内（> bootloader 区且 < flash 末尾）

**app_descriptor 的作用范围（仅在 bootloader 中）**：
- `bootloader()` 的 `check_good_firmware()` 函数使用 app_descriptor 做 CRC 校验
- serial upload 协议使用 app_descriptor 验证上传的固件完整性
- `jump_to_app()` 本身不读取 app_descriptor

**结论**：缺少 app_descriptor 时，bootloader 的 `jump_to_app()` 仍会跳转（只要向量表有效）。
