# IOMCU RTT API 恢复记录（2026-05-10）

## 背景

Phase 0 架构清理（commit `062a16fb8d`）将 AP_IOMCU.cpp 和 AP_IOMCU.h 中的 RTT 适配补丁回退到 ChibiOS API。
恢复这些补丁后 IOMCU 正常工作。

## 改动涉及的三个原始 commit

| Commit | 说明 |
|--------|------|
| `7552b904f0` | IOMCU适配：ChibiOS API→RTT API（rt_event/rt_thread） |
| `488aa1490e` | fix(iomcu): don't hard-fault on firmware upload failure |
| `e6f4f2561c` | fix(iomcu): add timeout to event_failed to prevent infinite hang |

## 恢复后的改动清单

### AP_IOMCU.h 添加成员

```diff
+struct rt_event iomcu_event;
+uint8_t init_fail_count;
```

### AP_IOMCU.cpp 七处修改

**1. Include 替换**（文件顶部）：
```diff
-#include <ch.h>
+#include <rtthread.h>
+#define EVENT_MASK(n) (1U << (n))
 #include <AP_SerialManager/AP_SerialManager.h>
+#include <cstdio>
```

**2. event_failed()**（约 L110-115）：
```diff
 void AP_IOMCU::event_failed(uint32_t event_mask)
 {
-    // wait 0.5ms then retry
-    hal.scheduler->delay_microseconds(500);
-    chEvtSignal(thread_ctx, event_mask);
+    init_fail_count++;
+    if (init_fail_count > 50) {
+        DEV_PRINTF("IOMCU: not responding after %u attempts, giving up\n", init_fail_count);
+        return;
+    }
+    hal.scheduler->delay(1);
+    rt_event_send(&iomcu_event, event_mask);
 }
```

**3. thread_main() 入口**（约 L120-125）：
```diff
 void AP_IOMCU::thread_main(void)
 {
-    thread_ctx = chThdGetSelfX();
-    chEvtSignal(thread_ctx, initial_event_mask);
+    thread_ctx = (thread_t *)rt_thread_self();
+    rt_event_init(&iomcu_event, "iomcu", RT_IPC_FLAG_PRIO);
+    rt_event_send(&iomcu_event, initial_event_mask);
```

**4. 事件等待循环**（约 L147-148）：
```diff
-        eventmask_t mask = chEvtWaitAnyTimeout(~0, chTimeMS2I(10));
+        rt_uint32_t recved = 0;
+        rt_event_recv(&iomcu_event, (rt_uint32_t)~0,
+                      RT_EVENT_FLAG_OR | RT_EVENT_FLAG_CLEAR,
+                      rt_tick_from_millisecond(10), &recved);
+        eventmask_t mask = recved;
```

**5. trigger_event()**（约 L831）：
```diff
-        chEvtSignal(thread_ctx, EVENT_MASK(event));
+        rt_event_send(&iomcu_event, EVENT_MASK(event));
```

**6. check_crc() 固件上传失败处理**（约 L1143-1146）：
```diff
-        AP_BoardConfig::config_error("Failed to update IO firmware");
+        ::printf("IOMCU fw upload failed, using existing fw\n");
```

## 验证标准

| 指标 | 检查方法 | 期望结果 |
|------|---------|---------|
| 编译通过 | `scons --v=ArduCopter --target=cuav_v5 -j$(nproc)` | ROM ≤ 86%, 零 error |
| CDC 枚举 | `ls /dev/ttyACM*` 15s 后 | ttyACM1 出现 |
| MAVLink 心跳 | pymavlink serial read | msg_id=0(HEARTBEAT) |
| RAW_IMU | pymavlink serial read | msg_id=27(RAW_IMU) 数据流 |
| ATTITUDE | pymavlink serial read | msg_id=30(ATTITUDE) |
| CFSR/HFSR | OpenOCD halt + mdw | 全部 = 0 |
| IOMCU 无 Internal Error | MAVLink STATUS_TEXT | 无 "Internal Errors 0x1000" |
