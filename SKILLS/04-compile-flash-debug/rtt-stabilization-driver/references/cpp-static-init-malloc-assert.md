# C++ Static Init — Early malloc Assertion Failure

## Symptom

System boots (Thread mode, CFSR=0) but halts in `rt_assert_handler`. CDC console produces no output. Backtrace:

```
#0  rt_assert_handler("0", "_rt_mutex_take", line=1334)  at kservice.c
#1  _rt_mutex_take(mutex=&_lock, timeout=-1, suspend_flag=2)  at ipc.c
#2  rt_malloc(size=124)  at kservice.c
#3  rt_calloc(count=X, size=Y)
#4  _calloc_r(ptr=impure_data, ...)  at newlib/syscalls.c
```

## Root Cause

C++ static constructors (via `__libc_init_array` during `INIT_COMPONENT_EXPORT(rtt_run_cpp_ctors)`) call `malloc` internally. The call chain is:

```
AP_InertialSensor constructor
  → detect_backends()
  → HAL_INS_PROBE_LIST
  → AP_InertialSensor_Invensense::probe(*this, hal.spi->get_device("icm20689"), ...)
  → SPIDeviceManager::get_device_ptr("icm20689")
  → NEW_NOTHROW SPIDevice(...)
  → operator new(nothrow) → _calloc_r → rt_calloc → rt_malloc
  → _heap_lock() → rt_mutex_take(&_lock, RT_WAITING_FOREVER)
  → RT_DEBUG_SCHEDULER_AVAILABLE(RT_TRUE)
  → RT_ASSERT(0)  ← FAILS HERE
```

The assertion at `_rt_mutex_take` line 1334 is `RT_DEBUG_SCHEDULER_AVAILABLE(RT_TRUE)` which checks `rt_critical_level() == 0` and `rt_in_thread_context()`. During C++ static init (before `rt_system_scheduler_start()`), `rt_in_thread_context()` returns false, triggering the assertion.

## Fix: SPI Device Static Pool (Preferred)

Replace `NEW_NOTHROW SPIDevice(...)` with placement-new on a static byte buffer:

```cpp
AP_HAL::SPIDevice *SPIDeviceManager::get_device_ptr(const char *name)
{
    for (uint8_t i = 0; i < _DEVICE_TABLE_COUNT; i++) {
        if (strcmp(name, _device_table[i].name) == 0) {
            /* Static pool avoids rt_malloc during C++ static init
             * (scheduler not yet running, mutex-based heap lock asserts). */
            static SPIDevice *s_devices[_DEVICE_TABLE_COUNT] = {};
            static uint8_t s_mem[sizeof(SPIDevice) * _DEVICE_TABLE_COUNT];
            if (!s_devices[i]) {
                s_devices[i] = new (&s_mem[i * sizeof(SPIDevice)]) SPIDevice(_device_table[i]);
            }
            return s_devices[i];
        }
    }
    return nullptr;
}
```

File: `libraries/AP_HAL_RTT/SPIDeviceManager.cpp`

### Why This Works

- `static` local variables are allocated in BSS (zero-initialized by startup code before any C++ constructors run)
- Placement-new on a `uint8_t` buffer doesn't require a default constructor for `SPIDevice`
- No heap allocation = no `_heap_lock()` = no mutex assertion

## Avoided: Kernel-Level Fix

A kernel-level fix (modifying `_heap_lock()` in `kservice.c` to skip mutex when scheduler isn't available) was attempted but caused a secondary IBUSERR HardFault. The kernel fix changes fundamental RT-Thread behavior and can have unintended side effects. **Always prefer the application-layer fix** (static pool in `SPIDeviceManager.cpp`) over kernel modification.

## Diagnostic

```bash
# 1. Confirm assertion failure location
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "bt 5" \
  /path/to/rt-thread.elf

# 2. Check if _lock mutex is initialized
echo "mdw 0x20045048 4" | nc -q 2 localhost 4444
# _lock.type should be RT_Object_Class_Mutex (0x04)

# 3. Verify scheduler not running
gdb-multiarch -batch \
  -ex "target extended-remote localhost:3333" \
  -ex "monitor halt" \
  -ex "p/x rtt_dbg_setup_stage" \
  -ex "p/x copter.ap.initialised" \
  /path/to/rt-thread.elf
# Both should be 0 — system is still in C++ static init
```

## Verification

After applying the SPIDevice static pool fix:
- Build succeeds (BSS increases by ~N*sizeof(SPIDevice) where N = device count)
- System boots past C++ static init without assertion
- Setup stage progresses beyond 662 into main loop

## Discovery Context

2026-05-12 session: RTT ArduPilot CUAV V5, linker KEEP fix already applied. After clean rebuild, system hit the malloc assertion during C++ static init. The kernel fix (kservice.c `_heap_lock()` + `rt_scheduler_is_available()`) was rejected in favor of the application-layer SPI device pool approach.
