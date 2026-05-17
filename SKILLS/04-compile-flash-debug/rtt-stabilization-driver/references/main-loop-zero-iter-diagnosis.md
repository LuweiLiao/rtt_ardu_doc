# main_loop_iterations=0 诊断方法论

## 发现时间
2026-05-14 会话

## 场景
setup 已完成（hal_run_called=0xBBBBBBBB），但 main_loop_iterations 始终为 0。

## 三步排查

### Step 1: 读取两种状态变量

```bash
# hal_run_called + main_loop_iterations
echo -e "halt\nmdw 0x200001c0\nmdw 0x20019a24\nmdw 0x200001c8\nmdw 0x2001bd0c\nreset run\nexit" | nc -q 2 localhost 4444
```

### Step 2: 解读组合

| hal_run_called | main_loop_iterations | 含义 |
|----------------|---------------------|------|
| 0xDEADBEEF | 0 | 系统在 bootloader 或启动早期（未进入 run()） |
| 0xAAAAAAAA | 0 | 正在 run() 中初始化（hal.gpio->init / serial->begin / analogin->init） |
| 0xBBBBBBBB | 0 | ⚠️ **setup 完成但 loop 没跑！** DMA ISR 自锁或优先级饿死 |
| 0x11111111 | 0 | main_loop_entry 已进入但 setup 尚未完成 |
| 0xBBBBBBBB | N (N>0) | 系统正常运行，只需等待 |

### Step 3: 根因定位

**如果 L1 基线运行正常**（48,763+ iterations in 60s）而 P0 版本 main_loop_iterations=0：

→ **100% DMA ISR 自锁**（DMA2_Stream0 TCIF 遗留标志位）

验证：检查 DMA LISR 寄存器：
```bash
echo -e "halt\nmdw 0x40026400\nreset run\nexit" | nc localhost 4444
# 0x40026400 = DMA2 LISR
# 如果 bit 0 (TCIF0) = 1 → ISR 已触发
# 如果 bit 20 (EN0) 为 0 但 TCIF0=1 → ISR 自锁
```

修复参考：`ardupilot-rtt-architecture` 技能的 `references/p0-adc-dma-ispitfall.md`

**非 DMA 场景**：
- 检查 uart/SD 卡驱动是否在 loop() 初始化中阻塞
- 检查 timer 线程是否在运行（`_adc_timer_tick`）
- 检查是否有另一个高优先级线程占用 CPU

## 对比基线法

最有效的诊断方法：**对比 L1 基线（无 P0 改动）**：

1. `git stash` 保存当前改动
2. `git checkout faee486d1c -- .` 切到 L1 基线
3. 编译 + 烧录
4. 60s 后检查 main_loop_iterations
5. 基线跑 → P0 不跑 → P0 改动的 bug，非系统性问题
