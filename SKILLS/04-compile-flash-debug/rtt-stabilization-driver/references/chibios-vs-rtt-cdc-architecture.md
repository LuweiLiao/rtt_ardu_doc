# ChibiOS vs RTT CDC TX Architecture Comparison

## Executive Summary
RTT ArduPilot CDC TX throughput is bottlenecked at the **MAVLink scheduling layer** (`GCS_MAVLINK::update_send()`), not the USB data path. Even with a direct USB endpoint write bypassing all ringbuffers, message flow is capped at 2.6 Hz (RAW_IMU) / 6.5 Hz (GPS) vs ChibiOS's 18.7 / 72.1 Hz.

## Architecture Difference

### ChibiOS (CUAV V5 baseline)
```
Thread: uavcan_send @ 1000 Hz
  → GCS_MAVLINK::send()  ← INDEPENDENT of main loop
  → port layer writes to USB FIFO
  → 18.7 Hz RAW_IMU, 72.1 Hz GPS, 50+ Hz SYSTEM_TIME

Thread: main @ 340 Hz
  → Scheduler::run()
  → AP_InertialSensor::update() etc.
```

**Key**: Two independent threads. MAVLink send runs at its own pace regardless of main loop load.

### RTT (current)
```
Thread: Main Thread
  → Scheduler::run() @ 340 Hz (calls loop())
    → AP_InertialSensor::update() (RAW_IMU every iteration)
    → GCS_MAVLINK::update_send()  ← OPPORTUNISTIC, same thread
      → send() only when idle ticks available
      → With RAW_IMU at 1000 Hz, almost no idle time
  → ~~No dedicated MAVLink send thread~~
```

**Key**: Single-thread. `GCS_MAVLINK::update_send()` is conjoined with main loop. With RAW_IMU consuming most of the 4000 µs slot, there's no time for MAVLink TX.

## Quantitative Evidence

### RTT direct-write benchmark (2026-05-11)
```
HEARTBEAT:   114 in 120s = 0.95 Hz    (ChibiOS: 1.0 Hz)
SYS_STATUS:  114 in 120s = 0.95 Hz    (ChibiOS: 1.0 Hz)
POWER_STATUS: 114 in 120s = 0.95 Hz   (ChibiOS: 1.0 Hz)
MEMINFO:     114 in 120s = 0.95 Hz    (ChibiOS: 1.0 Hz)
RAW_IMU:     324 in 120s = 2.70 Hz    (ChibiOS: 18.7 Hz)
SCALED_IMU2: 324 in 120s = 2.70 Hz    (ChibiOS: 18.7 Hz)
SCALED_IMU3: 324 in 120s = 2.70 Hz    (ChibiOS: 18.7 Hz)
ATTITUDE:    126 in 120s = 1.05 Hz    (ChibiOS: 6.8 Hz)
GPS_RAW:     791 in 120s = 6.59 Hz    (ChibiOS: 72.1 Hz)
SYSTEM_TIME: 68 in 120s = 0.57 Hz     (ChibiOS: 50+ Hz)
```

### ChibiOS baseline (for comparison)
```
RAW_IMU:      ~2,244 in 120s = 18.7 Hz  (6.9x faster)
GPS_RAW:      ~8,652 in 120s = 72.1 Hz  (10.9x faster)
ATTITUDE:     ~816 in 120s = 6.8 Hz  (6.5x faster)
SYSTEM_TIME:  ~6,000+ in 120s = 50+ Hz  (88x+ faster)
```

## Root Cause Trace

```
Scheduler::run() @ 340 Hz (each ~4000 µs max)
  → wait loop until next main loop tick (350µs resolution)
  → the main loop calls loop()
    → AP_InertialSensor::update() — reads IMU registers, takes ~500µs
    → ... other tasks ...
    → GCS_MAVLINK::update_send()
      → for each MAVLink channel:
        → GCS_MAVLINK::send()
          → check if this stream has a slot available
          → if yes: mavlink_msg_xxx_encode → serial_write
          → serial_write → TX buffer → USB DMA
  → end of loop()

RAW_IMU stream rate: defined by AP_Scheduler as "every loop" (loop_rate=340 Hz internally → 1000 Hz with loop_output)
GCS stream rate: streams are throttled by AP_GCS internal scheduling (timer-based slot assignment)

PCR = Per Channel Rate: Each MAVLink channel gets a ~1/stream_rate slot.
With RAW_IMU at 1000 Hz and GPS at 400 Hz, but the send thread only runs
opportunistically during idle time of the main loop...

In ChibiOS: uavcan_send thread runs at 1000 Hz regardless → GPS_RAW gets 
72.1 Hz (400 Hz * efficient scheduling)
In RTT: GPS_RAW gets 6.59 Hz = same scheduling but 10x fewer send opportunities
```

## Path to ChibiOS Parity

The fundamental fix requires decoupling MAVLink send from the main loop:

```c
/* Option A: Create a dedicated MAVLink send thread */
static void mavlink_send_thread_entry(void *parameter)
{
    while (1) {
        for (uint8_t i=0; i<MAVLINK_COMM_NUM_BUFFERS; i++) {
            GCS_MAVLINK::update_send();  // Or just send()
        }
        rt_thread_mdelay(1);  // ~1000 Hz
    }
}
/* Registered in HAL_RTT_Class.cpp init */
```

This requires careful locking for shared serial port data and is **not a trivial change** — the `GCS_MAVLINK::send()` method accesses shared state that the main loop also touches (mavlink status, stream rate counters). A mutex or thread-safe queue would be needed.

### Trade-offs
| Approach | Complexity | Throughput | Risk |
|----------|-----------|-----------|------|
| Dedicated send thread (1000 Hz) | High | ~ChibiOS parity | Shared state races, mutex overhead |
| Increase main loop rate | Low | Moderate | CPU saturation, lower sensor rate |
| Batch send in idle callback | Medium | Partial improvement | Depends on idle time, unpredictable |

## What NOT to Chase
- **CherryUSB ringbuffer optimization**: drain_zero=0% already achieved with direct write, no throughput improvement
- **DMA tuning**: DWC2 FIFO config is fine, ChibiOS uses identical DMA settings
- **Baud rate / USB descriptors**: USB HS (480 Mbps) is not the limit
