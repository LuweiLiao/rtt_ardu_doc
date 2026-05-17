# 子Agent代码常见编译错误模式（2026-05-16 会话 + 更新）

> 本文件记录 RTT ArduPilot 移植中，子Agent（delegate_task）遗留的编译错误模式。
> 作为 `rtt-multi-agent-closed-loop` 技能中"子Agent代码质量验证"节的补充参考。

## 模式1：头文件声明与实现文件命名不一致

**场景**：子Agent修改了 `.h` 和 `.cpp` 但两边的变量名/方法名不匹配。

**示例**（2026-05-16 UARTDriver）：
```
UARTDriver.h:   uint8_t *_tx_bounce_buf{nullptr};
UARTDriver.cpp: uint32_t n = _writebuf.peekbytes(_tx_bounce, sizeof(_tx_bounce));
                //                     ^^^^^^^^^^
                // error: '_tx_bounce' was not declared in this scope; did you mean '_rx_bounce'?
```

**根因**：子Agent在 `.h` 中用了 `_tx_bounce_buf` 却忘了在 `.cpp` 中同步。子Agent的写入过程在 `_begin()` 中分配了 `_tx_bounce_buf`，但TX线程入口函数用了不同的名字。

**修复**：统一为头文件声明的名称。注意同时修改数组长度（用 `BOUNCE_CHUNK = 256` 代替 `sizeof(_tx_bounce)`）。

**预防**：子Agent返回后，Orchestrator 检查 `.h` 和 `.cpp` 中同一逻辑实体的命名完全一致。重点关注：成员变量声明 vs 使用处。

---

## 模式2：缺失常量定义

**场景**：子Agent在实现中引用了一个常量，但在任何头文件中都未定义。

**示例**（2026-05-16 UARTDriver.cpp:177）：
```cpp
rt_uint8_t prio = _unbuffered_writes ?
    (APM_RTT_UART_PRIORITY - 1) : APM_RTT_UART_PRIORITY;
//  ^^^^^^^^^^^^^^^^^^^^ error: not declared
```

**根因**：子Agent假设 `APM_RTT_UART_PRIORITY` 已存在于某个 RTT 头文件中（就像 `APM_RTT_MAIN_PRIORITY` 存在于 `Scheduler.h` 一样），但实际上该常量从未定义。子Agent推理出"应该存在"但不验证。

**修复**：
```c
// 在 UARTDriver.cpp 顶部添加（或 Scheduler.h 的优先级定义区域）：
#ifndef APM_RTT_UART_PRIORITY
#define APM_RTT_UART_PRIORITY 22
#endif
```

**预防**：子Agent返回后，检查代码中所有大写常量名是否在某个头文件中被 `#define`。使用 `grep -rn "常量名" AP_HAL_RTT/` 验证。

---

## 模式3：宏自带花括号导致双重初始化嵌套

**场景**：`#define MACRO { ... }` 已有外层花括号，但在数组初始化时又包了一层 `{ MACRO }`。

**示例**（2026-05-16 RCOutput.cpp:56）：
```cpp
// 生成器输出：
#define HAL_RTT_PWM_MAP { \
    {TIM1_BASE, 4, 216000000UL}, \
    {TIM4_BASE, 2, 108000000UL}, \
    ...  \
    {TIM12_BASE, 2, 108000000UL} }
//                          ^^ 注意宏自带花括号！

// 错误的用法：
static const struct pwm_channel_map _chan_map[] = {
    HAL_RTT_PWM_MAP    // ← 展开为 { { {TIM1...}, {TIM4...}, ... } }
};                     //    外层{}+宏{}→双重嵌套，编译器试图用8个struct
                       //   init初始化一个struct→"too many initializers"

// 正确的用法：
static const struct pwm_channel_map _chan_map[] = HAL_RTT_PWM_MAP;
// 展开为: { {TIM1...}, {TIM4...}, ... }  — 正确！
```

**诊断**：看宏定义是否有最外层 `{ }`。如果是 `#define MACRO { ... }`，在C中的正确用法是 `Type var = MACRO;`（无外层花括号）。

**修复**：
```cpp
// 错误：
Type arr[] = { MACRO };
// 正确：
Type arr[] = MACRO;
```

**同类陷阱**：同样问题也适用于 `HAL_PIN_ALT_CONFIG` 和任何生成器输出的完整数组宏。检查所有 `#define HAL_xxx { \` 格式的宏，它们都是直接赋值用的，不是放在花括号里的。

---

## 模式4：ChibiOS 特有宏在 RTT 中未定义

**场景**：hwdef.h 生成器输出引用 ChibiOS 宏（如 `STM32_DMA_STREAM_ID`），但 RTT 无对应定义。

**示例**（2026-05-16 shared_dma.cpp）：
```c
#define SHARED_DMA_MASK ((1U<<STM32_DMA_STREAM_ID(1,1))|...)
//                ^^^^^^^^^^^^^^^^^^^^^^ error: not declared
```

**根因**：`dma_resolver.py` 直接输出 `STM32_DMA_STREAM_ID(x,y)` 宏调用。该宏在 ChibiOS 中定义于 `modules/ChibiOS/os/hal/ports/STM32/LLD/DMAv2/stm32_dma.h`。RTT 不包含这个头文件，需要显式定义。

**修复**：在 `rtt_hwdef.py` 的 `write_dma_header()` 函数开头添加：
```python
f.write('/* STM32 DMA stream ID helper macros */\\n')
f.write('#define STM32_DMA_STREAM_ID(dma, stream) ((((dma) - 1U) * 8U) + (stream))\\n')
f.write('#define STM32_DMA_STREAM_ID_ANY 255\\n\\n')
```

**检查工具**：
```bash
# 找出 hwdef.h 中所有未被 define 的标识符
grep -oP '[A-Z_]+(?=\\()' build/rtt_cuav_v5/hwdef.h | sort -u | \
  while read macro; do grep -q "#define $macro" build/rtt_cuav_v5/hwdef.h || echo "MISSING: $macro"; done
```

---

## 模式5：生成器反斜杠转义错误

**场景**：子Agent修改 `rtt_hwdef.py` 时，Python 字符串中的反斜杠层数错误，导致生成的 C 文件格式异常。

**示例**（2026-05-16 rtt_hwdef.py `_write_pwm_map` / `write_alt_config`）：
```python
# ❌ 错误：sep = ' \\\\\\\\'  → Python 运行时得到 4 个反斜杠→hwdef.h中"\\\\"
# ✅ 正确：sep = ' \\\\'     → Python 运行时得到 2 个反斜杠→hwdef.h中"\"（行连续符）
```

**根本原因**：Python 字符串中 `\\` 是一个转义序列（一个字符）。需要通过嵌套层级来推导：
- Python源码 `' \\\\'` → Python字符串值: ` \` (space + backslash) → `f.write('...%s...', sep)` 输出到文件: 空格+`\`
- Python源码 `' \\\\\\\\'` → Python字符串值: ` \\` (space + 2 backslashes) → 输出到文件: 空格+`\\` (两个斜杠→C预处理器不认)

**修复快速参考**：
| 想要 C 输出 | Python 源码要写 |
|------------|---------------|
| 行末 `\` （C行连续） | `' \\\\'` |
| 行末 `\\\\` （C字面） | `' \\\\\\\\'` |
| 普通空格 | `' '` |

**预防**：修改生成器后，手动运行一次，检查输出的最后几行：
```bash
python3 scripts/rtt_hwdef.py -D /tmp/verify cuav_v5/hwdef.dat
tail -10 /tmp/verify/hwdef.h   # 检查关键区域的 \\ 是否出现在正确位置
```

---

## 模式6：TIM_TypeDef 指针 vs uint32_t 基址

**场景**：PWM 通道映射结构体使用 `TIM_TypeDef *` 指针类型，但宏初始化中 `TIM1` 展开为 `((TIM_TypeDef *)TIM1_BASE)`，在 aggregate initialization 上下文中可能引起类型不兼容。

**修复方案**：将结构体成员改为 `uint32_t tim_base`（直接存基址），使用时再强制转换：
```cpp
struct pwm_channel_map {
    uint32_t tim_base;     /* TIM基址如 TIM1_BASE */
    uint8_t  ch;           /* 通道号 1..4 */
    uint32_t clock;        /* 定时器时钟频率 Hz */
};

// 初始化用 TIM1_BASE（uint32_t 宏）而不是 TIM1（指针宏）：
#define HAL_RTT_PWM_MAP { \\
    {TIM1_BASE, 4, 216000000UL}, \\
    ...
}

// 使用时转换回指针：
TIM_TypeDef *tim = (TIM_TypeDef *)_chan_map[chan].tim_base;
```

**生成器改动**：`rtt_hwdef.py` 的 `_write_pwm_map()` 中：
```python
# 改为输出 TIM%d_BASE 而不是 TIM%d
tim_base = 'TIM%d_BASE' % timer_num
```

---

## 模式7：新 .c 文件未加入 SConscript 构建

**场景**：子Agent创建了新的 `.c` 源文件（如 `flash_check.c`），但 SConscript 没有添加对应的 `Glob()`，导致链接时 undefined reference。

**示例**（2026-05-16 flash_check.c）：
```
undefined reference to `rtt_flash_boundary_check'
```

**修复**：在 `hwdef/common/board/SConscript` 中添加：
```python
src += Glob('flash_check.c')
```

**所有需要 SConscript 注册的目录**：
| 目录 | SConscript 路径 |
|------|---------------|
| board 通用代码 | `hwdef/common/board/SConscript` |
| board 驱动 LL | `hwdef/common/board/drivers_ll/SConscript` |
| board 端口 | `hwdef/common/board/ports/SConscript` |
| rtt_bsp_cuav_v5 | `rtt_bsp_cuav_v5/board/SConscript` |
| rtt_bsp_pixhawk6c_mini | `rtt_bsp_pixhawk6c_mini/board/SConscript` |

**预防**：子Agent返回后，检查 git 中是否有新的 `.c` 文件：
```bash
git diff HEAD --name-only -- '*.c' | grep -v 'build/'
# 对每个新 .c 文件，检查其目录的 SConscript 是否包含它
for f in $(git diff HEAD --name-only -- '*.c'); do
    dir=$(dirname $f)
    if [ -f "$dir/SConscript" ]; then
        grep -q "$(basename $f)" "$dir/SConscript" || echo "MISSING in SConscript: $f"
    fi
done
```

---

## 诊断优先顺序

当子Agent返回后编译失败时，按此顺序诊断：

```
1. 检查是编译错误还是链接错误
   ├── 编译错误（gcc/clang 报错）
   │  → 看是哪个文件报错，按模式1-6定位
   └── 链接错误（ld 报 undefined reference）
      → 模式7：新 .c 文件未在 SConscript 中注册
      → 或：函数声明了但未实现
      
2. 该文件是子Agent修改的吗？（git diff 检查）
   ├── 否 → 预存 bug，不在子Agent的范围内
   │      → Orchestrator 自行修复或创建新的子任务
   └── 是 → 子Agent引入的 bug
          → 属于哪类模式？（模式1-7）
          → 按对应模式修复

3. 编译通过后，还有其它错误吗？
   └── 有 → 回到第1步循环
   └── 无 → 记录为"编译通过"，继续验证流程
```

**2026-05-16 关键教训**：先判断"编译错误是预存的还是子Agent引入的"再分配修复责任。本轮有 2/5 个错误是预存bug（hwdef.h 语法 + STM32_DMA_STREAM_ID 宏），不是子Agent的错。

## 统计：2026-05-16 会话

| 编译错误 | 模式 | 是否为子Agent引入 | 修复耗时 |
|---------|------|-----------------|---------|
| `hwdef.h:1367` 裸 `}` | 模式5 | ❌ 预存bug（P0） | 1min |
| `shared_dma.cpp` STM32_DMA_STREAM_ID | 模式4 | ❌ 预存bug（hwdef生成器） | 3min |
| `UARTDriver.cpp:177` APM_RTT_UART_PRIORITY | 模式2 | ✅ 子Agent引入 | 1min |
| `UARTDriver.cpp:476` _tx_bounce | 模式1 | ✅ 子Agent引入 | 1min |
| `RCOutput.cpp:56` too many initializers | 模式3（双重花括号） | ✅ 子Agent引入 | 15min（debug最长） |
| `rtt_flash_boundary_check` undefined | 模式7（SConscript遗漏） | ✅ 子Agent引入 | 2min |
