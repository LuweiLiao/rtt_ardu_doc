# STM32F767 Memory Map — RT-Thread / Arduino 适配

> 基于 RM0410 (STM32F76xxx Reference Manual) 和 AN4667 (DMA safety guidelines)

---

## 1. 物理内存布局

| 区域        | 起始地址      | 大小      | 总线路径                   | DMA 可达 |
|-------------|--------------|-----------|----------------------------|----------|
| ITCM        | `0x0000_0000` | 16 KB     | CPU I-Bus (专用)           | ❌ 否    |
| DTCM        | `0x2000_0000` | 128 KB    | CPU D-Bus (专用)           | ❌ 否    |
| SRAM1       | `0x2002_0000` | 368 KB    | AHB 总线矩阵 (AHB/APB)     | ✅ 是    |
| SRAM2       | `0x2007_C000` | 16 KB     | AHB 总线矩阵 (AHB/APB)     | ✅ 是    |
| Flash       | `0x0800_0000` | 2 MB      | I/D-Bus + AHB              | ✅ (读)  |

### 关键物理地址重叠

```
0x2000_0000 ─── DTCM (128 KB, D-Bus only, no DMA)
                SRAM1 物理起点相同但访问路径不同
0x2002_0000 ─── SRAM1 (368 KB, AHB Bus, DMA ✅)
0x2007_C000 ─── SRAM2 (16 KB, AHB Bus, DMA ✅)
0x2008_0000 ─── 外设 / 保留
```

> ⚠️ DTCM 与 SRAM1 的 **物理地址不同**：DTCM 占据 `0x2000_0000–0x2001_FFFF`，
> SRAM1 从 `0x2002_0000` 开始。两者地址连续但总线路径完全不同。

---

## 2. Linker Script 当前配置

| 区域   | 起始地址       | 大小      | 说明                      |
|--------|---------------|-----------|---------------------------|
| ROM    | `0x0800_8000` | 2016 KB   | 0x0–0x7FFF 保留给 Bootloader |
| RAM    | `0x2000_0000` | 512 KB    | DTCM + SRAM1 + SRAM2 连续映射 |

### 栈和段布局

```ld
_system_stack_size = 0x4000;   /* 16 KB 系统栈 */

SECTIONS {
    .text   : { ... } > ROM        /* 代码 + 只读数据 */
    .data   : { ... } > RAM AT>ROM /* 已初始化数据，运行时在 RAM */
    .stack  : {                     /* 系统栈：放置在 .data 和 .bss 之间 */
        . = ALIGN(8);
        _sstack = .;
        . = . + _system_stack_size;
        _estack = .;
    } > RAM
    .bss    : { ... } > RAM        /* 未初始化数据 */
    .sram1_bss : {                  /* DMA 安全缓冲区段 */
        . = ALIGN(32);
        *(.sram1_bss)
        *(.sram1_bss.*)
        . = ALIGN(32);
    } > RAM
}
```

---

## 3. board.h 内存分配策略

```c
#define STM32_SRAM_SIZE   512
#define STM32_SRAM_END    0x20080000

#define STM32F7_SRAM1_START  0x20020000

/* 堆起始：取 _end 和 SRAM1 起始两者中的较大值 */
#define HEAP_BEGIN  MAX(_end, STM32F7_SRAM1_START)

/* 堆结束：SRAM2 末尾 */
#define HEAP_END    STM32_SRAM_END
```

### 关键约束

| 参数         | 值             | 说明                               |
|--------------|----------------|------------------------------------|
| `HEAP_BEGIN` | `max(_end, 0x20020000)` | 强制堆起始 ≥ SRAM1 起始地址           |
| `HEAP_END`   | `0x20080000`   | 堆结束于 SRAM2 末尾                  |

---

## 4. 内存分区示意图

```
0x2000_0000  ┌──────────────────────┐
             │  DTCM (128 KB)       │  ← .data, .stack, .bss (部分)
             │  CPU D-Bus only      │  ← ❌ DMA 不可达
0x2002_0000  ├──────────────────────┤
             │  SRAM1 (368 KB)      │  ← HEAP 起始 (rt_malloc 从此分配)
             │  AHB Bus, DMA ✅     │  ← ✅ 线程栈、DMA 缓冲区
             │  .sram1_bss 段       │  ← ✅ DMA-safe 缓冲区
0x2007_C000  ├──────────────────────┤
             │  SRAM2 (16 KB)       │  ← HEAP 末尾部分
             │  AHB Bus, DMA ✅     │  ← ✅ 也可用于 DMA
0x2008_0000  └──────────────────────┘
```

---

## 5. 关键规则 (Critical Rules)

### 规则 1：线程栈必须在 SRAM1/SRAM2

```c
// ✅ 正确：线程栈通过 rt_malloc 从堆分配
// HEAP_BEGIN >= 0x20020000 确保堆在 SRAM1 中
void *stack = rt_malloc(stack_size);  // → SRAM1 ✅

// ❌ 错误：静态分配可能落入 DTCM
static uint8_t stack[1024];  // → 可能在 DTCM ❌
```

### 规则 2：DMA 缓冲区必须在 SRAM1/SRAM2

```c
// ✅ 正确：显式放入 .sram1_bss 段
__attribute__((section(".sram1_bss"), aligned(32)))
static uint8_t dma_buffer[256];

// ✅ 正确：从堆分配（堆在 SRAM1）
uint8_t *buf = (uint8_t *)rt_malloc_align(256, 32);

// ❌ 错误：落入 DTCM (0x20000000-0x2001FFFF)
static uint8_t bad_buffer[256];  // 默认 BSS 可能落在 DTCM
```

### 规则 3：DMA 缓冲区必须 32 字节对齐

```c
// ✅ 正确：32 字节对齐
uint8_t buf[256] __attribute__((aligned(32)));

// ✅ 正确：对齐分配
uint8_t *buf = (uint8_t *)rt_malloc_align(256, 32);
```

### 规则 4：DMA RX 后必须使能 D-Cache 失效

```c
/* DMA 接收完成后 */
SCB_InvalidateDCache_by_Addr((uint32_t *)dma_buf, len);

/* DMA 发送前必须 Clean */
SCB_CleanDCache_by_Addr((uint32_t *)dma_buf, len);
```

### 规则 5：BSS 可能部分落在 DTCM

由于 RAM 段被定义为 `0x20000000` 起始的 512 KB 连续区域，BSS 的前 128 KB
(`0x20000000–0x2001FFFF`) 属于 DTCM。**静态分配的全局变量默认 BSS 段可能位于
DTCM，不可用于 DMA。**

---

## 6. 调试辅助：检查变量所在的存储区域

```c
// 运行时检查地址归属
static inline int memory_region(void *addr) {
    uint32_t a = (uint32_t)addr;
    if (a >= 0x20000000 && a < 0x20020000) return 1; // DTCM ❌
    if (a >= 0x20020000 && a < 0x2007C000) return 2; // SRAM1 ✅
    if (a >= 0x2007C000 && a < 0x20080000) return 3; // SRAM2 ✅
    return 0; // 其他区域
}
```

---

## 参考

- RM0410: STM32F76xxx Reference Manual, §2.3 Memory Map
- AN4667: Managing DMA and Cache Coherency in STM32F7 MCUs
- STM32F767ZI Datasheet, §2.3 Memory Map
