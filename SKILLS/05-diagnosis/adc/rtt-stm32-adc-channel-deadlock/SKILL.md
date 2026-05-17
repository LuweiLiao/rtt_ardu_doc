---
name: rtt-stm32-adc-channel-deadlock
description: RT-Thread STM32 ADC channel deadlock — rt_adc_read() hangs because channel isn't configured before polling
tags: [rt-thread, stm32, adc, embedded, arudpilot]
---

# RTT STM32 ADC Channel Deadlock Fix

## Problem
On STM32F7 with RTT's `drv_adc.c`, calling `rt_adc_read(dev, channel)` directly causes `HAL_ADC_PollForConversion()` to deadlock — the call never returns.

## Root Cause
`rt_adc_read()` internally calls `HAL_ADC_Start()` + `HAL_ADC_PollForConversion()`. However, it does NOT re-configure the ADC channel register before starting conversion. The ADC remains configured for whatever channel was last set (typically channel 0). Since the wrong channel is selected, the EOC (End Of Conversion) flag is never set, and `PollForConversion()` waits forever.

## Solution
Before every `rt_adc_read(dev, channel)`, call `rt_adc_enable(dev, channel)` to configure the channel:

```cpp
rt_device_t dev = rt_device_find("adc1");
for (int16_t i = 0; i < RTT_ANALOG_MAX_CHANNELS; i++) {
    rt_adc_enable((struct rt_adc_device *)dev, i);   // configure channel FIRST
    rt_uint32_t val = rt_adc_read((struct rt_adc_device *)dev, i);
    _sources[i]._add_sample((float)(val & 0xFFF));
}
```

## Alternative: Direct HAL Bypass
Bypass RTT's ADC framework entirely and use STM32 HAL directly:

```cpp
static ADC_HandleTypeDef _hadc1;
static bool _adc_initialized = false;

static bool _adc_init_once() {
    if (_adc_initialized) return true;
    __HAL_RCC_ADC1_CLK_ENABLE();
    _hadc1.Instance = ADC1;
    _hadc1.Init.ClockPrescaler = ADC_CLOCK_SYNC_PCLK_DIV4;
    _hadc1.Init.Resolution = ADC_RESOLUTION_12B;
    _hadc1.Init.ScanConvMode = DISABLE;
    _hadc1.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
    _hadc1.Init.ContinuousConvMode = DISABLE;
    _hadc1.Init.NbrOfConversion = 1;
    _hadc1.Init.ExternalTrigConv = ADC_SOFTWARE_START;
    HAL_ADC_Init(&_hadc1);
    _adc_initialized = true;
    return true;
}

static uint32_t _adc_read_channel(uint32_t channel) {
    ADC_ChannelConfTypeDef sConfig = {};
    sConfig.Channel = channel;
    sConfig.Rank = 1;
    sConfig.SamplingTime = ADC_SAMPLETIME_112CYCLES;
    HAL_ADC_ConfigChannel(&_hadc1, &sConfig);
    __HAL_ADC_CLEAR_FLAG(&_hadc1, ADC_FLAG_EOC | ADC_FLAG_OVR);
    HAL_ADC_Start(&_hadc1);
    // Poll with timeout (HAL_GetTick returns tick count)
    uint32_t tick = HAL_GetTick();
    while (!(__HAL_ADC_GET_FLAG(&_hadc1, ADC_FLAG_EOC))) {
        if (HAL_GetTick() - tick > 10) return 0; // 10ms timeout
    }
    return HAL_ADC_GetValue(&_hadc1);
}
```

## Related: hal_conf.h Deploy Issue
The RTT deploy script (`rtt_bsp_deploy.py`) copies BSP template files to `build/rtt_deploy/` but does NOT overwrite `stm32f7xx_hal_conf.h`. Changes to `HAL_ADC_MODULE_ENABLED` in the BSP template have no effect unless you also add hal_conf copying to `_copy_hwdef_board_overrides`:

```python
# In rtt_bsp_deploy.py RTT_TARGETS['cuav_v5']:
hal_conf_rel': 'modules/rt-thread/bsp/stm32/stm32f765-cuav-v5/board/CubeMX_Config/Inc/stm32f7xx_hal_conf.h'

# In _copy_hwdef_board_overrides():
hal_conf_rel = tinfo.get('hal_conf_rel')
if hal_conf_rel:
    hal_conf_src = os.path.join(ap_root, _norm(hal_conf_rel))
    if os.path.isfile(hal_conf_src):
        hal_conf_dst = os.path.join(deploy_dir, 'board', 'CubeMX_Config', 'Inc', 'stm32f7xx_hal_conf.h')
        _safe_copy(hal_conf_src, hal_conf_dst)
```

## Files Involved
- `Tools/scripts/rtt_bsp_deploy.py` — deploy script
- `modules/rt-thread/bsp/stm32/libraries/HAL_Drivers/drivers/drv_adc.c` — RTT ADC driver (deadlock source)
- `modules/rt-thread/components/drivers/misc/adc.c` — RTT ADC device framework
- `libraries/AP_HAL_RTT/AnalogIn.cpp` — ArduPilot analog input implementation

## Verification
After fix, check with pymavlink:
```python
mavutil.mavlink_connection('/dev/ttyACM1', baud=57600)
# Request SYS_STATUS stream — voltage_battery should be non-zero
```
