# RTT hwdef Infrastructure: VAL_GPIO + dma_resolver + ldscript

> Created: 2026-05-16 (Session: P0 hwdef补齐)
> Reference: ChibiOS `chibios_hwdef.py` (3159行) + `dma_resolver.py` (605行)

## Overview

This document documents the complete hwdef infrastructure created for AP_HAL_RTT,
transforming it from a minimal `rtt_hwdef.py` (1103行, 仅生成 hwdef.h+link.lds+rtconfig.h)
to a full-featured system matching ChibiOS capabilities: VAL_GPIO register macros,
dma_resolver constraint solver, enriched linker scripts, env.py, and common.ld.

## File Structure

```
libraries/AP_HAL_RTT/hwdef/scripts/
├── rtt_hwdef.py              # ~1650 行 — 主脚本（含 VAL_GPIO + dma_resolver + ldscript + env）
├── STM32F767xx.py            # 1178 行 — MCU 定义（pincount/AltFunction_map/DMA_Map/ADC_map/RAM_MAP）
├── dma_resolver.py           # 683 行 — DMA Stream/Channel 回溯约束求解器
├── dma_parse.py              # 91 行 — DMA 映射表解析器（从 datasheet CSV 生成）
├── __pycache__/
```

## 1. MCU Definition File (STM32F767xx.py)

Must contain:

| Data | Entries | Description |
|------|---------|-------------|
| `pincount` | 11 dict | `A:16, B:16, ..., I:10, J:0, K:0` (VI 100-pin: I=10) |
| `AltFunction_map` | ~994 | `"PA0:USART2_CTS": 7` format — pin:function → AF number |
| `DMA_Map` | ~97 | `"SPI1_RX": [(2,0,3), (2,2,3)]` — peripheral → [(DMA#, Stream#, Chan#)] |
| `ADC1_map` / `ADC2_map` / `ADC3_map` | 16 each | `"PA0": 0` — pin name → ADC channel number |
| `RAM_MAP` | ~3 tuples | `(0x20020000, 384, 0)` — (addr, sizeKB, priority) for SRAM/DTCM |
| `mcu` | str | e.g. `"CORTEX-M7"` |
| `mcu_series` | str | e.g. `"STM32F7"` (used for vtypes selection) |

**Key**: module must be importable as `import STM32F767xx`.

## 2. VAL_GPIO Register Macro Generation

### Architecture (portmap-based)

```python
# In RTTHWDef.__init__():
self.vtypes = []          # Set by setup_mcu_type_defaults()
self.ports = []           # Sorted port names
self.pincount = {}        # From MCU module
self.portmap = {}         # port → [generic_pin, ...] per pin number
```

**Initialization** (`setup_mcu_type_defaults()`):
```python
lib = get_mcu_lib(self.mcu_type)
self.pincount = lib.pincount
self.vtypes = f4f7_vtypes  # ['MODER','OTYPER','OSPEEDR','PUPDR','ODR','AFRL','AFRH']
self.ports = sorted(self.pincount.keys())
for port in self.ports:
    for pin in range(self.pincount[port]):
        default = self.generic_pin(port, pin, None, 'INPUT', ['FLOATING'], ...)
        self.portmap[port].append(default)
```

**Pin update** (`process_line()`):
Every pin definition in hwdef.dat updates the corresponding portmap entry:
- CS pin → type=devname, extra=['CS'], sig_dir='OUTPUT'
- GPIO OUTPUT → type='OUTPUT', label=name, extra=[init_state]
- ADC pin → type='ADCn', label=name
- UART/SPI/I2C/TIM → type=periph, label=signal, af=resolved

**Output** (`_write_gpio_register_defines(f)`):
1. Writes PIN_* macros (MODE_INPUT/OUTPUT/ALTERNATE/ANALOG, etc.)
2. For each port with pincount>0, generates:
   ```c
   #define VAL_GPIOA_MODER (PIN_MODE_ALTERNATE(0U) | PIN_MODE_INPUT(1U) | ...)
   #define VAL_GPIOA_OTYPER (PIN_OTYPE_PUSHPULL(0U) | ...)
   #define VAL_GPIOA_OSPEEDR (PIN_OSPEED_MEDIUM(0U) | ...)
   #define VAL_GPIOA_PUPDR (PIN_PUPDR_PULLUP(0U) | ...)
   #define VAL_GPIOA_ODR (PIN_ODR_HIGH(0U) | ...)
   #define VAL_GPIOA_AFRL (PIN_AFIO_AF(0U, 7U) | ...)
   #define VAL_GPIOA_AFRH (PIN_AFIO_AF(8U, 0U) | ...)
   ```
3. For empty ports: `#define VAL_GPIOA_MODER 0x0`

### generic_pin class (inner class of RTTHWDef)

Key methods:
- `get_MODER_value()`: ALTERNATE|OUTPUT|ANALOG|INPUT based on type/af/extra
- `get_OTYPER_value()`: PUSHPULL|OPENDRAIN (I2C → OPENDRAIN)
- `get_PUPDR_value()`: FLOATING|PULLUP|PULLDOWN (CS→PULLUP, UART→PULLUP, RTS→PULLDOWN, SWCLK→PULLDOWN)
- `get_OSPEEDR_value()`: VERFLOW|LOW|MEDIUM|HIGH (from extra or default MEDIUM)
- `get_ODR_value()`: LOW|HIGH
- `get_AFRL()`: None if pin>=8 else get_AFIO()
- `get_AFRH()`: None if pin<8 else get_AFIO()
- `pal_modeline()`: PAL_STM32_MODE_/OTYPE_/SPEED/PUPDR/ALTERNATE string

### Pin Macro Definitions for F4/F7

```c
#define PIN_MODE_INPUT(n)           (0U << ((n) * 2U))
#define PIN_MODE_OUTPUT(n)          (1U << ((n) * 2U))
#define PIN_MODE_ALTERNATE(n)       (2U << ((n) * 2U))
#define PIN_MODE_ANALOG(n)          (3U << ((n) * 2U))
#define PIN_OTYPE_PUSHPULL(n)       (0U << (n))
#define PIN_OTYPE_OPENDRAIN(n)      (1U << (n))
#define PIN_PUPDR_FLOATING(n)       (0U << ((n) * 2U))
#define PIN_PUPDR_PULLUP(n)         (1U << ((n) * 2U))
#define PIN_PUPDR_PULLDOWN(n)       (2U << ((n) * 2U))
#define PIN_OSPEED_HIGH(n)          (3U << ((n) * 2U))
#define PIN_AFIO_AF(n, v)           ((v) << (((n) % 8U) * 4U))
```

## 3. dma_resolver (RTT port)

Port of ChibiOS `dma_resolver.py` (605行 → RTT 683行).

### Core Algorithm (unchanged from ChibiOS)

1. Load DMA_Map from MCU module (e.g., STM32F767xx.DMA_Map)
2. For non-DMAMUX (F4/F7): backtracking constraint solver (`check_possibility`)
3. For DMAMUX (H7/G4): direct assignment (`generate_DMAMUX_map`)
4. Output: `#define HAL_DMA_*` macros

### RTT-specific Output Format Changes

| ChibiOS Output | RTT Output |
|---------------|------------|
| `#define STM32_SPI_1_TX_DMA_STREAM` | `#define HAL_DMA_STREAM_SPI1_TX` |
| `#define STM32_UART_USART1_RX_DMA_STREAM` | `#define HAL_DMA_USART1_RX` |
| `#define STM32_UART_USART1_RX_DMA_CONFIG` | `#define HAL_USART1_RX_DMA_CONFIG` |
| `#define STM32_SPI_1_DMA_STREAMS` | `#define HAL_SPI1_DMA_STREAMS` |

### Integration with rtt_hwdef.py

- `periph_list()`: builds deduplicated peripheral list from hwdef pin definitions
- `write_dma_header(f)`: calls `dma_resolver.write_dma_header()` with RTT-compatible output
- Called from `write_hwdef_header_content()` after VAL_GPIO generation
- DMA peripheral filtering: peripherals not in DMA_Map (e.g., TIM12) are skipped

### Typical Output for CUAV V5 (46 DMA macros)

```c
#define HAL_DMA_STREAM_SPI1_RX   STM32_DMA_STREAM_ID(2, 2)
#define HAL_DMA_STREAM_SPI1_TX   STM32_DMA_STREAM_ID(2, 5)
#define HAL_DMA_STREAM_SPI4_RX   STM32_DMA_STREAM_ID(2, 0)
#define HAL_DMA_USART2_RX        STM32_DMA_STREAM_ID(1, 5)
#define HAL_DMA_USART2_TX        STM32_DMA_STREAM_ID(1, 6)
// ... etc
```

## 4. Enhanced Linker Script Generation

### write_linker_script(outdir)
Generates `link.lds` (RT-Thread linker script):
- Uses `RAM_MAP[0]` from MCU module for SRAM base/size
- Default: STM32F767 → SRAM1/2 @0x20020000, 384KB
- FLASH_ORIGIN = 0x08000000 + FLASH_RESERVE_START_KB * 1024 (default 16KB)
- Sections layout copied from existing template

### write_ldscript(outdir)
Generates `ldscript.ld` (ChibiOS/waf-compatible format):
```ld
/* generated ldscript.ld */
MEMORY {
    flash : org = 0x08008000, len = 1504K
    ram0  : org = 0x20020000, len = 393216
}
INCLUDE common.ld
```

### copy_common_linkerscript(outdir)
Copies section layout from `common/board/linker_scripts/link.lds` to `common.ld`.

## 5. env.py Generation

```python
def write_env_py(self, outdir):
    self.env_vars['FLASH_RESERVE_START_KB'] = str(...)
    self.env_vars['FLASH_RESERVE_END_KB'] = str(...)
    self.env_vars['FLASH_TOTAL'] = ...
    self.env_vars['HAS_EXTERNAL_FLASH_SECTIONS'] = 0
    pickle.dump(self.env_vars, open(path, 'wb'))
```

## 6. Alt Config Table

Generated by `write_alt_config(f)`:
```c
#define HAL_PIN_ALT_CONFIG_COUNT 46
#define HAL_PIN_ALT_CONFIG { \
    {PE5, PAL_STM32_MODE_ALTERNATE|PAL_STM32_OTYPE_PUSHPULL|PAL_STM32_SPEED(1)|PAL_STM32_PUPDR_FLOATING|PAL_STM32_ALTERNATE(3)}, \
    ...
}
```

## 7. main() Output — Full Generation Flow

```python
h = RTTHWDef(outdir=outdir, hwdef=[hwdef_path])
h.run()                    # Parent class: writes hwdef.h + probes + defaults
h.write_pin_config_c()    # HAL_MspInit functions (UART/SPI/PWM/SD/USB)
h.write_linker_script()   # link.lds
h.write_rtconfig_h()      # Peripheral enable macros
h.write_ROMFS()           # ROMFS embedded files
h.write_ldscript()        # ldscript.ld (INCLUDE common.ld)
h.copy_common_linkerscript() # common.ld
h.write_env_py()          # Build environment variables
```

## 8. Common Pitfalls

1. **MCU module case sensitivity**: `hwdef.dat` has `MCU STM32F7xx STM32F767xx`. The second token must preserve original case (don't `.upper()`) for `importlib.import_module('STM32F767xx')` to find `STM32F767xx.py`.

2. **AltFunction_map lookup**: Use signal name (e.g. `TIM9_CH1`) not peripheral name (e.g. `TIM9`). `PE5:TIM9` is NOT in the map; `PE5:TIM9_CH1` is.

3. **DMA deduplication**: `periph_list()` must use a `set()` to avoid duplicate ADC peripherals (CUAV V5 has 9 ADC pins → would add ADC1 9× without dedup).

4. **Non-DMA peripherals**: Some TIM peripherals (TIM6, TIM7, TIM12, TIM13, TIM14) have no DMA entries. Filter via `dma_keys` set.

5. **VAL_GPIO vs HAL_MspInit duality**: VAL_GPIO macros provide compile-time register tables; rt_pin_config.c provides runtime HAL_MspInit for each peripheral. Both exist because different parts of the codebase use different init styles.

6. **AFRL vs AFRH**: AFRL handles pins 0-7, AFRH handles pins 8-15. `get_AFRL()` returns None for pin≥8, `get_AFRH()` returns None for pin<8. The VAL_GPIO generator skips None returns.

7. **⚠️ `#define` 宏闭合陷阱**（2026-05-16 发现 — 实际导致编译失败）：
   - `write_alt_config()` 用 `f.write()` 生成多行 `#define` 宏，每行末尾的 `\`（行连续符）**必须**每行都有，包括最后一项。
   - **错误代码**（原实现）：
     ```python
     sep = ' \\\\' if i < len(alt_pins) - 1 else ''
     ```
   - **效果**：最后一项无 `\` → `#define` 在此行结束 → 下一行的 `}` 成为裸 C 代码 → 编译错误 `error: expected identifier or '(' before '}' token`
   - **修复**：
     ```python
     sep = ' \\\\'  # 永远加反斜杠，让 '}' 行成为 define 的一部分
     ```
   - **验证方法**：检查生成的 hwdef.h 末尾段，确认 `}` 之前有 `\\`：
     ```bash
     tail -10 /tmp/test/hwdef.h | grep -E "ALTERNATE|^}"
     # 正确输出：每项相同格式，最后一行以 } 结尾
     {PI3, ...ALTERNATE(5)} \    ← 有 \
     }                           ← 属于 define
     ```

8. **⚠️ ChibiOS 特有宏缺失**（2026-05-16 发现）：
   - `dma_resolver.write_dma_header()` 输出的 `SHARED_DMA_MASK` 使用 `STM32_DMA_STREAM_ID(dma, stream)` 宏，该宏在 ChibiOS `stm32_dma.h` 中定义但 RTT 不包含该头文件。
   - **症状**：编译错误 `'STM32_DMA_STREAM_ID' was not declared in this scope`
   - **修复**：在 `rtt_hwdef.py` 的 `write_dma_header()` 函数中、调用 `dma_resolver.write_dma_header()` 之前添加：
     ```python
     f.write('/* STM32 DMA stream ID helper macros */\\n')
     f.write('#define STM32_DMA_STREAM_ID(dma, stream) ((((dma) - 1U) * 8U) + (stream))\\n')
     f.write('#define STM32_DMA_STREAM_ID_ANY 255\\n\\n')
     ```
   - **泛化**：任何从 ChibiOS 移植的生成脚本，输出的宏可能依赖 ChibiOS 特有的辅助宏。必须检查所有输出宏中被调用的标识符是否在 RTT 上下文中有定义。

9. **⚠️ RAM_MAP 访问路径**（2026-05-16 reviewer 发现）：
   - `write_linker_script()` 和 `write_ldscript()` 中，`RAM_MAP` 存在于 MCU 模块的 `mcu` 字典内（`lib.mcu['RAM_MAP'][0]`）而非模块级属性。
   - **错误代码**：`hasattr(lib, 'RAM_MAP') and lib.RAM_MAP` → 永远为 False
   - **修复**：`hasattr(lib, 'mcu') and 'RAM_MAP' in lib.mcu` → `ram0 = lib.mcu['RAM_MAP'][0]`
   - **影响**：不改的话 CUAV V5 的 RAM base 用 `0x20000000`（DTCM）而非正确的 `0x20020000`（SRAM1），多板型时问题更严重

## 9. Test Command

```bash
cd libraries/AP_HAL_RTT/hwdef
rm -rf /tmp/rtt_hwdef_test
mkdir -p /tmp/rtt_hwdef_test
python3 scripts/rtt_hwdef.py -D /tmp/rtt_hwdef_test cuav_v5/hwdef.dat
ls /tmp/rtt_hwdef_test/
# Expected: hwdef.h, rt_pin_config.c, link.lds, ldscript.ld, common.ld, 
#           env.py, rtconfig.h, romfs.pickle, ap_romfs_embedded.h
```
