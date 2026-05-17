# SPI1 轮询传输超时修复（2026-05-09）

## 问题
ICM20689 的 70 字节 FIFO 读传输在 spi1_poll_transfer() 的 RXNE 等待循环中永久挂死。
GDB halt 时 PC 在 SPIDevice.cpp:232 `while (!(spi->SR & SPI_SR_RXNE) && --timeout)`。

## 根因
- timeout=10000（~46μs @ 216MHz）太小
- **超时后没有中止逻辑**——循环继续执行第 i+1 字节，但 RXNE 仍不置位，CPU 永远卡在循环中
- 当前线程（设备总线/IMU 读取线程）永久挂死 = MAVLink 更新停止 = 系统看起来"死机"
- SPI 从机在某个字节后停止响应（原因可能是 ICM 状态机紊乱、CS 毛刺等）

## 修复
- timeout 从 10000 增加到 100000（~460μs）
- TXE 超时后：释放 CS → 禁用 SPE → 排空 RX FIFO → 释放堆内存 → 返回 false
- RXNE 超时后：排空 DR → 释放 CS → 禁用 SPE → 排空 RX FIFO → 释放堆内存 → 返回 false
- 超时函数会干净退出，下次调用时 SPI 重新初始化，恢复正常通信

## 修改的文件
`libraries/AP_HAL_RTT/SPIDevice.cpp` — `spi1_poll_transfer()` 函数

## 验证
烧录后运行 30 秒：MCU 无 HardFault，MAVLink 心跳稳定（state=3 STANDBY），
BMI055 和 MS5611 数据正常。

## 关键代码段
```c
for (uint32_t i = 0; i < total_len; i++) {
    uint32_t timeout = 100000;
    while (!(spi->SR & SPI_SR_TXE) && --timeout) { __NOP(); }
    if (timeout == 0) {
        // TXE timeout — release CS, disable SPI, drain FIFO, return false
        ...
        CLEAR_BIT(spi->CR1, SPI_CR1_SPE);
        while (spi->SR & SPI_SR_RXNE) { (void)(*(__IO uint8_t *)&spi->DR); }
        if (heap) { rt_free_align(buf); }
        return false;
    }
    *((__IO uint8_t *)&spi->DR) = buf[i];
    timeout = 100000;
    while (!(spi->SR & SPI_SR_RXNE) && --timeout) { __NOP(); }
    if (timeout == 0) {
        // RXNE timeout — drain DR, release CS, disable SPI, drain FIFO, return false
        ...
        CLEAR_BIT(spi->CR1, SPI_CR1_SPE);
        while (spi->SR & SPI_SR_RXNE) { (void)(*(__IO uint8_t *)&spi->DR); }
        if (heap) { rt_free_align(buf); }
        return false;
    }
    buf[i] = *((__IO uint8_t *)&spi->DR);
}
```

## 教训
嵌入式 SPI 轮询传输必须有健壮的超时和中止逻辑。永远不要假设从机会响应。
"从机不响应"是 SPI 调试中常见的问题——CS 毛刺、时钟速率不匹配、从机内部状态机问题都可能触发。
