# DMA & D-Cache 规则 — STM32F767

> 适用：RT-Thread / Arduino 适配，Cortex-M7 (STM32F767ZI)

---

## 1. 核心原则

| 原则 | 说明 |
|------|------|
| **DMA 只能访问 AHB 总线上的内存** | DTCM (D-Bus only) 和 ITCM (I-Bus only) 不可达 |
| **D-Cache 对 DMA 不透明** | Cache 内容与物理内存可能不一致，需软件维护一致性 |
| **Cache Line = 32 bytes** | 所有 Cache 维护操作必须 32 字节对齐 |

---

## 规则 1：DMA 缓冲区位置

**DMA 缓冲区必须位于 SRAM1 或 SRAM2，绝不能位于 DTCM。**

```
✅ SRAM1  (0x2002_0000 – 0x2007_BFFF)  — DMA可达
✅ SRAM2  (0x2007_C000 – 0x2007_FFFF)  — DMA可达
❌ DTCM   (0x2000_0000 – 0x2001_FFFF)  — D-Bus only, DMA不可达
❌ ITCM   (0x0000_0000 – 0x0000_3FFF)  — I-Bus only, DMA不可达
```

### 实现方式

```c
// 方式 1：专用段 (推荐)
__attribute__((section(".sram1_bss"), aligned(32)))
static uint8_t dma_rx_buf[256];

// 方式 2：堆分配 (rt_malloc → HEAP_BEGIN≥0x20020000)
uint8_t *buf = (uint8_t *)rt_malloc_align(256, 32);

// 方式 3：rt_dma_buffer 宏 (如果存在)
```

---

## 规则 2：DMA 缓冲区对齐

**所有 DMA 缓冲区必须 32 字节对齐**，以匹配 D-Cache Line 大小。

```c
// ✅ 正确：32 字节对齐
uint8_t buf[256] __attribute__((aligned(32)));

// ✅ 正确：动态分配 + 对齐
uint8_t *buf = (uint8_t *)rt_malloc_align(size, 32);

// ❌ 错误：未对齐 → Cache 操作可能影响相邻数据
uint8_t buf[256];  // 默认对齐可能为 4 或 8
```

**缓冲区大小建议**：应为 32 的整数倍，避免部分 Cache Line 失效问题。

---

## 规则 3：DMA RX — 接收完成后使 D-Cache 失效

```c
/* DMA 接收完成回调中 */
void dma_rx_complete(DMA_HandleTypeDef *hdma) {
    // 步骤 1：在访问 DMA 写入的数据前，使对应的 Cache Line 失效
    SCB_InvalidateDCache_by_Addr((uint32_t *)rx_buf, rx_len);

    // 步骤 2：现在可以安全地从 rx_buf 读取数据
    process_data(rx_buf, rx_len);
}
```

**为什么必须 Invalidate**：DMA 直接将数据写入物理内存，但 D-Cache 中可能包含
该地址范围的陈旧数据。Invalidate 会丢弃 Cache 中的陈旧副本，下次读取时从物理
内存重新加载。

---

## 规则 4：DMA TX — 发送前 Clean D-Cache

```c
/* 发起 DMA TX 前 */
void prepare_dma_tx(uint8_t *buf, uint32_t len) {
    // 步骤 1：将 CPU 可能写入 Cache 但尚未写回物理内存的数据 Clean
    SCB_CleanDCache_by_Addr((uint32_t *)buf, len);

    // 步骤 2：现在可以启动 DMA 传输 (DMA 从物理内存读取)
    HAL_DMA_Start(&hdma, (uint32_t)buf, ...);
}
```

**为什么必须 Clean**：CPU 写入数据时可能只更新了 Cache，物理内存中的数据可能
是旧版本。Clean 将 Cache 内容写回物理内存，确保 DMA 读取到最新数据。

---

## 规则 5：DMA TX+RX (双缓冲 / 乒乓) — Clean+Invalidate

```c
void dma_pingpong_switch(uint8_t *active_buf, uint32_t len) {
    // 步骤 1：Clean 当前 CPU 写入的缓冲区 (为下一次 DMA RX 准备)
    SCB_CleanDCache_by_Addr((uint32_t *)active_buf, len);

    // 步骤 2：Invalidate 下一个要读取的缓冲区
    uint8_t *next_buf = (active_buf == buf_a) ? buf_b : buf_a;
    SCB_InvalidateDCache_by_Addr((uint32_t *)next_buf, len);
}
```

---

## 规则 6：整个 DMA 生命周期中的 Cache 操作

```
┌──────────────┐
│  分配缓冲区  │ → 确保 aligned(32) + 在 SRAM1/SRAM2
└──────┬───────┘
       ▼
┌──────────────┐
│  CPU 写入数据│
└──────┬───────┘
       ▼
┌──────────────┐     DMA TX: CPU 写入完成 → Clean Cache → 启动 DMA
│ Clean Cache  │
└──────┬───────┘
       ▼
┌──────────────┐
│  启动 DMA    │
└──────┬───────┘
       ▼
┌──────────────┐
│ DMA 完成回调 │
└──────┬───────┘
       ▼
┌──────────────┐     DMA RX: DMA 完成 → Invalidate Cache → CPU 读取
│ Invalidate   │
└──────┬───────┘
       ▼
┌──────────────┐
│  CPU 读取数据│
└──────────────┘
```

---

## 规则 7：避免 Cache 伪共享 (False Sharing)

**问题**：两个不同的 DMA 缓冲区位于同一 Cache Line (32 bytes) 内，
Invalidate/Clean 一个会影响另一个。

```c
// ❌ 错误：同一 Cache Line 内含两个独立缓冲区
uint8_t buf_a[16] __attribute__((aligned(32)));  // 0x20020000-0x2002000F
uint8_t buf_b[16] __attribute__((aligned(32)));  // 0x20020010-0x2002001F
// → buf_a 和 buf_b 在同一个 32 字节 Cache Line 内 ❌

// ✅ 正确：每个缓冲区独占 Cache Line
uint8_t buf_a[32] __attribute__((aligned(32)));  // 0x20020000-0x2002001F
uint8_t buf_b[32] __attribute__((aligned(32)));  // 0x20020020-0x2002003F
// → 各自独占一个 Cache Line ✅
```

---

## 规则 8：MPU 配置 (可选但推荐)

为 SRAM1/SRAM2 配置 MPU 区域，设置 **non-cacheable** 或 **write-through** 属性
可以简化 DMA 缓冲区的 Cache 管理。

```c
// 为 DMA 专用区域配置 non-cacheable
MPU_Region_InitTypeDef MPU_InitStruct = {0};
MPU_InitStruct.Enable           = MPU_REGION_ENABLE;
MPU_InitStruct.BaseAddress      = 0x20022000;  // DMA 专用区域
MPU_InitStruct.Size             = MPU_REGION_SIZE_16KB;
MPU_InitStruct.TypeExtField     = MPU_TEX_LEVEL0;
MPU_InitStruct.Cacheable        = MPU_ACCESS_NOT_CACHEABLE;
MPU_InitStruct.Bufferable       = MPU_ACCESS_BUFFERABLE;
HAL_MPU_ConfigRegion(&MPU_InitStruct);
```

> ⚠️ MPU 配置是全局性的——确保不影响 RT-Thread 内核的正常运行。

---

## 规则 9：rt_malloc 分配的默认行为

由于 `HEAP_BEGIN = max(_end, 0x20020000)`：

```
rt_malloc(n) → 地址 ≥ 0x20020000 → SRAM1 ✅ → DMA 安全
```

**例外**：如果 `_end < 0x20020000`，堆仍会起始于 `0x20020000`，所以
`rt_malloc` 分配的内存**始终可靠地落在 SRAM1 中**。

---

## 规则 10：调试与验证

```c
/* 运行时断言：检查地址是否 DMA 安全 */
#define ASSERT_DMA_SAFE(addr)                                    \
    do {                                                         \
        uint32_t _a = (uint32_t)(addr);                          \
        if (_a >= 0x20000000 && _a < 0x20020000) {               \
            rt_kprintf("FATAL: %s:%d: addr 0x%08X is in DTCM!"   \
                       " DMA unsafe!\n",                         \
                       __FILE__, __LINE__, _a);                  \
            while(1);                                            \
        }                                                        \
    } while(0)

/* 使用示例 */
void uart_dma_send(uint8_t *data, uint32_t len) {
    ASSERT_DMA_SAFE(data);
    SCB_CleanDCache_by_Addr((uint32_t *)data, len);
    HAL_UART_Transmit_DMA(&huart, data, len);
}
```

---

## 快速参考卡

| 场景 | 必须的操作 | API |
|------|-----------|-----|
| DMA TX (CPU→Peripheral) | 发送前 Clean Cache | `SCB_CleanDCache_by_Addr` |
| DMA RX (Peripheral→CPU) | 接收后 Invalidate Cache | `SCB_InvalidateDCache_by_Addr` |
| DMA TX+RX (同时) | Clean + Invalidate | 两个 API 都需要调用 |
| 缓冲区分配 | SRAM1/SRAM2 + 32B 对齐 | `aligned(32)` + `.sram1_bss` |
| 运行时检查 | 地址 < 0x20020000 则报错 | `ASSERT_DMA_SAFE` 宏 |
| 性能优化 | 避免同一 Cache Line 中的伪共享 | 使用 ≥32B 对齐的分区 |

---

## 参考文档

- ARM Cortex-M7 Generic User Guide, §4.3: Level 1 Data Cache
- RM0410, §2.3: Memory Map
- AN4667: Managing DMA and Cache Coherency in STM32F7 Series
- STM32F767ZI Datasheet, §2.3.2: Embedded SRAM
