---
name: rtt-chibios-api-adaptation
description: Adapt ChibiOS-dependent ArduPilot libraries (AP_IOMCU etc.) to work under AP_HAL_RTT. Covers sync primitives, UART mapping, hwdef generation, and type fixes.
tags: [rtt, chibios, porting, ap_iomcu, uart, hwdef]
---

# Adapting ChibiOS-dependent ArduPilot Libraries to RTT

## When to use
When porting an ArduPilot library (e.g., AP_IOMCU, AP_HAL_ChibiOS drivers) that uses ChibiOS APIs to work under AP_HAL_RTT. Look for `#include <ch.h>`, `chEvt*`, `chThd*`, `chSem*`, etc.

## Step 1: Identify all ChibiOS dependencies
```bash
grep -n "chEvt\|chThd\|chSem\|chBSem\|chCond\|chSysLock\|chSysUnlock\|chVT\|ch.h\|eventmask_t\|thread_t\|chTimeMS" <file>
```

## Step 2: Replace includes
- `#include <ch.h>` → `#include <rtthread.h>`

## Step 3: Map synchronization primitives

### Critical: ChibiOS events vs RTT events

ChibiOS events are **per-thread** (`chEvtSignal(thread, mask)` sends to a thread's embedded event mask). RTT events are **standalone IPC objects** (`struct rt_event` + `rt_event_init()` + `rt_event_send(&event, mask)`).

**You MUST add a `struct rt_event` member to the class** and initialize it in the thread entry function:
```cpp
// In header (class member):
struct rt_event my_event;

// In thread_main(), before first use:
rt_event_init(&my_event, "name", RT_IPC_FLAG_PRIO);
```

### API mapping table

| ChibiOS | RTT | Notes |
|---------|-----|-------|
| `chEvtSignal(thread, mask)` | `rt_event_send(&my_event, mask)` | **NOT rt_event_send(thread, ...)** — use event object! |
| `chEvtWaitAnyTimeout(mask, timeout_ticks)` | `rt_event_recv(&my_event, mask, RT_EVENT_FLAG_OR \| RT_EVENT_FLAG_CLEAR, timeout_ms, &recv_mask)` | RTT timeout is ms, returns rt_err_t, mask via pointer |
| `chThdGetSelfX()` | `rt_thread_self()` | Returns `rt_thread_t` |
| `chTimeMS2I(ms)` | `rt_tick_from_millisecond(ms)` | Convert ms to ticks; or pass ms directly to rt_event_recv |
| `chSemWait/Signal` | `rt_sem_take/release` | |
| `chBSemWait/Signal` | Use `rt_sem_t` with count=1 | |
| `chSysLock/Unlock` | `rt_enter_critical/exit_critical` | |

### Event adaptation pattern (concrete example from AP_IOMCU)

```cpp
// BEFORE (ChibiOS):
chEvtSignal(thread_ctx, EVENT_MASK(event));
eventmask_t mask = chEvtWaitAnyTimeout(~0, chTimeMS2I(10));

// AFTER (RTT):
rt_event_send(&iomcu_event, EVENT_MASK(event));
rt_uint32_t recved = 0;
rt_event_recv(&iomcu_event, (rt_uint32_t)~0,
              RT_EVENT_FLAG_OR | RT_EVENT_FLAG_CLEAR,
              rt_tick_from_millisecond(10), &recved);
eventmask_t mask = recved;
```

## Step 4: Fix type declarations in headers
```cpp
// BEFORE
typedef struct ch_thread thread_t;
// AFTER
typedef struct rt_thread thread_t;
```

## Step 5: Handle UART driver mapping for hwdef
1. Add pin config to hwdef.dat: `PE0 UART8_RX UART8 AF8`
2. Add `IOMCU_UART UART8` to hwdef.dat
3. Add key to `rtt_hwdef.py` `rtt_keys` set
4. Append UART to `device_names[]`, set `HAL_UART_IOMCU_IDX`
5. In `HAL_RTT_Class.cpp`, create `static UARTDriver(port_num)` for IOMCU

## Step 6: Handle conditional compilation
Extend guards: `#if CONFIG_HAL_BOARD == HAL_BOARD_CHIBIOS || CONFIG_HAL_BOARD == HAL_BOARD_RTT`
Or add separate RTT block.

## Pitfalls
- **RTT events are NOT per-thread**: This is the #1 mistake. `rt_event_send()` takes `rt_event_t` (pointer to `struct rt_event`), NOT a thread. You must create a standalone event object and `rt_event_init()` it. ChibiOS `chEvtSignal(thread, mask)` has no direct equivalent — the event object is the replacement.
- **rt_event_recv semantics**: Returns `rt_err_t` error code, NOT the event mask. Received mask is written to the pointer parameter. Restructure calling code accordingly.
- **thread_t typedef**: Use `#if CONFIG_HAL_BOARD` guards in headers — `struct ch_thread` for ChibiOS, `struct rt_thread` for RTT, `void` for others.
- **UART device_names ordering**: `HAL_UART_IOMCU_IDX` must match position in `_device_names[]` AND `AP_HAL::HAL` serial array. IOMCU UART should be appended AFTER serial_order ports.
- **hwdef key silently ignored**: Any new hwdef.dat key MUST be added to `rtt_keys` set in `rtt_hwdef.py` line ~263, or it will be silently dropped and never reach `self.config`.
- **Duplicate defines**: Let hwdef script be single source of truth for `HAL_WITH_IO_MCU` etc. Don't also put `define HAL_WITH_IO_MCU 1` in hwdef.dat or the script's else-branch will conflict.
- **AP_BoardConfig.h conditional guards**: Fields like `sbus_out_rate` are wrapped in `#if HAL_BOARD_CHIBIOS`. Add `#if HAL_BOARD_RTT` block alongside.
- **EVENT_MASK macro**: Not defined in RTT. Add `#define EVENT_MASK(n) (1U << (n))` after `#include <rtthread.h>`.

## Step 7: ROMFS pipeline (required for IOMCU firmware binary)

IOMCU needs `io_firmware.bin` loaded via ROMFS at runtime. The RTT build system must generate `ap_romfs_embedded.h` the same way ChibiOS does.

### 7a. Add ROMFS directive to hwdef.dat
```
ROMFS io_firmware.bin Tools/IO_Firmware/iofirmware_lowpolh.bin
```
The format is `ROMFS <embedded_name> <host_path>`. Multiple lines supported.

### 7b. Implement `write_ROMFS()` in `rtt_hwdef.py`
```python
def write_ROMFS(self):
    """Generate ap_romfs_embedded.h from ROMFS directives in hwdef."""
    romfs_list = self.config.get('ROMFS', [])
    if not romfs_list:
        return
    
    # Import embed.py from ArduPilot Tools
    embed_path = os.path.join(self.POGO_ROOT, 'Tools', 'ardupilotwaf', 'embed.py')
    spec = importlib.util.spec_from_file_location("embed", embed_path)
    embed_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(embed_mod)
    
    # files format: [(name, path), ...]
    header_path = os.path.join(self.gen_dir, 'ap_romfs_embedded.h')
    embed_mod.create_embedded_h(header_path, romfs_list)
    
    # Signal that ROMFS is available (AP_ROMFS checks this)
    self.write_define('HAL_HAVE_AP_ROMFS_EMBEDDED_H', 1)
```

### 7c. Propagate header through build pipeline
Three places must handle the generated header:
1. **rtt_hwdef.py**: `write_ROMFS()` generates to `build/rtt_deploy/<board>/ap_romfs_embedded.h`
2. **rtt_bsp_deploy.py**: `_copy_generated()` copies from `gen_dir` to `deploy_dir` (BSP source tree)
3. **BSP SConscript**: Copies from BSP cwd to `build/rtt_cuav_v5/` (CPPPATH already includes this dir)

### 7d. Pitfalls
- `ROMFS` must be in `rtt_keys` set in `rtt_hwdef.py` or the directive is silently dropped
- `romfs_list` format is `[(name, path), ...]` — matches `embed.py`'s `create_embedded_h(filename, files)` signature
- The header must exist before `AP_ROMFS` compiles; build order dependency
- ROM increase is significant (~70KB for IOMCU firmware binary)

## Step 8: RTT Subsystem Linkage — defeating --gc-sections

**Problem**: RTT kernel drivers (SDIO, DFS, block devices) compile to `.o` files but are **garbage-collected by `--gc-sections`** from the final ELF. `arm-none-eabi-nm rtthread.elf | grep -i sdio` returns nothing even though `drv_sdio.o` is 53KB on disk.

**Root cause**: ArduPilot's `ARDUPILOT_FULL=1` build mode uses custom linking via `SConscript_ardupilot`. The linker flag `-Wl,--gc-sections` removes any symbol not reachable from the entry point. RTT's `PrepareBuilding()` collects the `.o` files, but if nothing in the call graph references `rt_hw_sdio_init()`, `dfs_mount()`, etc., they're stripped.

**Diagnosis commands**:
```bash
# Check if driver .o exists (compiled but maybe not linked)
find modules/rt-thread -name 'drv_sdio.o' -exec ls -la {} \;

# Check ELF for symbols (empty = not linked)
arm-none-eabi-nm build/rtt_deploy/cuav_v5/rt-thread.elf | grep -i 'sdio\|dfs\|mount\|__rt_init'

# Dry-run to see what would compile
python3 -m SCons --target=cuav-v5 --dry-run 2>&1 | grep -i sdio
```

**Solution pattern — create a port file with `INIT_APP_EXPORT`**:

### 8a. Create `board/ports/<feature>_port.c`

```c
#include <rtthread.h>
#ifdef BSP_USING_SDIO   // Guard with rtconfig.h define

#include <dfs_fs.h>
#include <dfs_posix.h>

// This function pointer lands in .rti_fn.6 section via INIT_APP_EXPORT
// The linker MUST keep it and everything it calls
int sdcard_mount(void)
{
    // 1. Power on SD card (board-specific GPIO)
    rt_pin_mode(103, PIN_MODE_OUTPUT);  // PG7 = 6*16+7
    rt_pin_write(103, PIN_HIGH);
    rt_thread_mdelay(100);

    // 2. Explicitly call the driver init (forces linkage)
    extern int rt_hw_sdio_init(void);
    rt_hw_sdio_init();
    rt_thread_mdelay(500);

    // 3. Create mount point and mount
    mkdir("/sdcard", 0x777);
    if (dfs_mount("sd0", "/sdcard", "elm", 0, 0) == 0) {
        rt_kprintf("[SD] mounted /sdcard OK\n");
    }
    return 0;
}
INIT_APP_EXPORT(sdcard_mount);   // <-- This is the key: auto-init forces linkage

#endif
```

**Why this works**: `INIT_APP_EXPORT(fn)` places a function pointer in the `.rti_fn` section. RTT's auto-init (`rt_components_init()`) iterates `.rti_fn` sections and calls each function. The linker sees these `.rti_fn` references as roots, keeping `sdcard_mount()` and all its callees (`rt_hw_sdio_init`, `dfs_mount`, `drv_sdio.o` symbols) alive.

### 8b. Create/update `board/ports/SConscript`

```python
from building import *
cwd = GetCurrentDir()
src = []
if GetDepend(['BSP_USING_SDIO']):
    src += ['sdcard_port.c']
group = DefineGroup('Ports', src, depend=[''], CPPPATH=[cwd])
objs = group
Return('objs')
```

### 8c. Ensure deploy script copies `board/ports/`

The `rtt_bsp_deploy.py` must copy source BSP's `board/ports/` to the deploy directory. Add `ports_rel` to the target config and extend `_copy_hwdef_board_overrides()` to overlay ports files.

### 8d. RTT auto-init levels (for reference)

| Macro | Section | When called | Use for |
|-------|---------|-------------|---------|
| `INIT_BOARD_EXPORT(fn)` | `.rti_fn.1` | Before scheduler starts | HW init (clocks, pins) |
| `INIT_DEVICE_EXPORT(fn)` | `.rti_fn.3` | During `rt_components_board_init()` | Device drivers |
| `INIT_APP_EXPORT(fn)` | `.rti_fn.6` | During `rt_components_init()` | Application-level (mount FS) |

**Use `INIT_APP_EXPORT` for filesystem mounting** — it runs after device init but before ArduPilot's `main()`. Use `INIT_DEVICE_EXPORT` for low-level driver init.

### 8e. Pitfalls
- **Empty `SConscript` = silently skipped**: If `board/ports/SConscript` exists but is empty (returns no objs), the port file won't compile. Must have `DefineGroup()` + `Return('objs')`.
- **Deploy script must copy ports/**: The BSP deploy copies common template first, then overlays source BSP files. If `board/ports/` isn't in the overlay list, your new port file won't reach the build directory.
- **DFS V1 doesn't support symlink**: Don't try `symlink()` — just create directories directly (`/sdcard/APM/`, `/sdcard/APM/LOGS/`).
- **SD card power pin**: CUAV v5 uses PG7 (`VDD_3V3_SD_CARD_EN`). Must be driven HIGH before SDIO init. RTT pin number = port*16+pin = 6*16+7 = 103.
- **Non-fatal errors**: SD card might not be inserted. Wrap mount in try/catch equivalent, don't fail boot.

## Step 9: Enabling a New Peripheral via hwdef Pipeline (ADC, etc.)

When enabling a new hardware peripheral (ADC, DAC, etc.) that wasn't previously in the RTT build, three layers must be updated:

### 9a. Add pin definitions to hwdef.dat

```bash
# Format: PIN LABEL PERIPHERAL SCALE(n) — matches rtt_hwdef.py parser
PA0 BATT_VOLTAGE_SENS ADC1 SCALE(1)
PA1 BATT_CURRENT_SENS ADC1 SCALE(1)
PC0 VDD_5V_SENS ADC1 SCALE(2)
```

### 9b. Update rtt_hwdef.py — three places

1. **`write_rtconfig_h()`** — add peripheral enable macros to the `enables` list:
```python
if self.adc_pins:
    enables.append('#define RT_USING_ADC')
    adc_buses = set(a['adc'].upper() for a in self.adc_pins)
    for bus in adc_buses:
        enables.append('#define BSP_USING_%s' % bus)
```

2. **`write_pin_config_c()`** — add MSP generator call:
```python
# ADC MSP
if self.adc_pins:
    self._gen_adc_msp(f)
```

3. **Add MSP generator method** (e.g., `_gen_adc_msp`) — generates `HAL_xxx_MspInit()`:
```python
def _gen_adc_msp(self, f):
    """Generate HAL_ADC_MspInit from ADC pin definitions."""
    f.write('void HAL_ADC_MspInit(ADC_HandleTypeDef* hadc)\n{\n')
    f.write('    GPIO_InitTypeDef GPIO_InitStruct = {0};\n\n')
    adc_periphs = {}
    for a in self.adc_pins:
        periph = a['adc'].upper()
        if periph not in adc_periphs:
            adc_periphs[periph] = []
        adc_periphs[periph].append(a)
    for periph, pins in adc_periphs.items():
        f.write('    if (hadc->Instance == %s) {\n' % periph)
        f.write('        __HAL_RCC_%s_CLK_ENABLE();\n' % periph)
        ports_seen = set()
        for p in pins:
            if p['port'] not in ports_seen:
                f.write('        __HAL_RCC_GPIO%s_CLK_ENABLE();\n' % p['port'])
                ports_seen.add(p['port'])
        f.write('        GPIO_InitStruct.Mode = GPIO_MODE_ANALOG;\n')
        f.write('        GPIO_InitStruct.Pull = GPIO_NOPULL;\n')
        port_pins = {}
        for p in pins:
            if p['port'] not in port_pins:
                port_pins[p['port']] = []
            port_pins[p['port']].append(p)
        for port, pp in port_pins.items():
            pin_mask = ' | '.join(['GPIO_PIN_%d' % p['pin_num'] for p in pp])
            f.write('        GPIO_InitStruct.Pin = %s;\n' % pin_mask)
            f.write('        HAL_GPIO_Init(GPIO%s, &GPIO_InitStruct);\n' % port)
        f.write('    }\n')
    f.write('}\n\n')
```

### 9c. Enable HAL module in BSP rtconfig.h / hal_conf.h

RTT's peripheral drivers (e.g., `drv_adc.c`) need the HAL driver headers. Check:
- **BSP's `rtconfig.h`** must have `#define RT_USING_ADC` and `#define BSP_USING_ADC1`
- **BSP's `stm32f7xx_hal_conf.h`** (or equivalent) must have `#define HAL_ADC_MODULE_ENABLED`
- These are in `modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/` and/or `build/rtt_cuav_v5/`

**TWO rtconfig.h locations**: The hwdef script writes to `build/rtt_deploy/<board>/rtconfig.h`, but BSP compilation reads `build/rtt_cuav_v5/rtconfig.h`. Both must have the peripheral macros. The deploy/copy step must propagate the generated macros.

### 9d. Fix RTT driver compilation issues

RTT HAL drivers (`drv_adc.c`, etc.) may have bugs with certain STM32 families:
- `stm32_adc_get_resolution()` missing return statement on F7 (add `return 0;` after `#endif`)
- `ADC_HandleTypeDef` unknown → HAL module header not included → enable `HAL_ADC_MODULE_ENABLED`
- `ADC_CLOCK_SYNC_PCLK_DIV4` → STM32F7 HAL uses `ADC_CLOCKPRESCALER_PCLK_DIV4` (API version mismatch between RTT driver config and HAL version)

### 9e. Fix ArduPilot API mismatches

When adapting ChibiOS AnalogIn code, use AP_HAL's own enum types:
```cpp
// WRONG (MAVLink enum, not available in AP_HAL context):
flags |= MAV_POWER_STATUS_BRICK_VALID;

// CORRECT (AP_HAL::AnalogIn::PowerStatusFlag):
flags |= (uint16_t)PowerStatusFlag::BRICK_VALID;
```

### 9f. Pitfalls

- **Two rtconfig.h files**: `build/rtt_deploy/<board>/rtconfig.h` (generated by hwdef) vs `build/rtt_cuav_v5/rtconfig.h` (used by BSP build). Both need the macros. Check which one BSP compilation actually uses.
- **HAL module not enabled**: Even with `BSP_USING_ADC1` in rtconfig.h, the HAL driver won't compile without `HAL_ADC_MODULE_ENABLED` in `stm32f7xx_hal_conf.h`. Check the BSP's hal_conf for commented-out module enables.
- **RTT driver config version mismatch**: The RTT HAL driver config files (`drivers/config/f7/adc_config.h`) may reference older HAL API names. Compare with the actual HAL headers in `packages/stm32f7_hal_driver-latest/`.
- **Weak MspInit**: HAL provides `__weak void HAL_ADC_MspInit()` — you must provide a strong version via `rt_pin_config.c` to configure GPIO clocks and pins.

### references/iomcu-rtt-api-restoration.md

此参考文件记录了 2026-05-10 恢复 IOMCU RTT API 适配的完整过程，包括 Phase 0 清理回退的三个 commit、AP_IOMCU.cpp 和 AP_IOMCU.h 的七处具体修改、验证标准表。在需要排查 IOMCU 在 RTT 平台不工作（`_rt_mutex_take` 阻塞、无 MAVLink 输出）时优先查阅。

## Verification

### Build verification
1. `python3 -m SCons --target=cuav-v5 -j16` zero errors
2. Check `build/rtt_cuav_v5/hwdef.h` for `HAL_HAVE_AP_ROMFS_EMBEDDED_H 1` and `HAL_WITH_IO_MCU 1`
3. Check `build/rtt_deploy/cuav_v5/ap_romfs_embedded.h` exists and contains firmware data
4. ROM size increase should be expected (~70KB for IOMCU firmware)

### Hardware verification via pymavlink
After flashing, use pymavlink to verify IOMCU and subsystem health:

```python
from pymavlink import mavutil

mav = mavutil.mavlink_connection('/dev/serial/by-id/<device>', baud=57600)
mav.wait_heartbeat(timeout=5)

# Request all data streams
mav.mav.request_data_stream_send(mav.target_system, mav.target_component,
    mavutil.mavlink.MAV_DATA_STREAM_ALL, 2, 1)

# Collect SYS_STATUS messages
msg = mav.recv_match(type='SYS_STATUS', blocking=True, timeout=5)
present = msg.onboard_control_sensors_present
healthy = msg.onboard_control_sensors_health

# Key bits for IOMCU verification:
# bit 15 (MOTOR_OUTPUTS): present + healthy = IOMCU firmware loaded and responding
# bit 16 (RC_RECEIVER): present only if RC receiver physically connected
motor_ok = bool(present & (1 << 15)) and bool(healthy & (1 << 15))

# Check RC_CHANNELS
rc = mav.recv_match(type='RC_CHANNELS', blocking=True, timeout=5)
# chancount=0 is normal if no RC receiver connected
# Getting RC_CHANNELS messages at all = IOMCU UART communication working
```

**Key SYS_STATUS bits** (MAV_SYS_STATUS_SENSOR):
- bit 0: 3D_GYRO, bit 1: 3D_ACCEL, bit 2: 3D_MAG
- bit 3: ABS_PRESSURE, bit 5: GPS
- bit 15: MOTOR_OUTPUTS (= IOMCU servo output healthy)
- bit 16: RC_RECEIVER (= physical RC input detected)
- bit 21: AHRS

**Interpretation**: `MOTOR_OUTPUTS present + healthy` = IOMCU firmware loaded via ROMFS, UART communication established. `RC_RECEIVER not present` = no physical RC receiver (expected on bench).
