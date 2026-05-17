# MAVLink 原始帧传感器健康诊断

## 适用场景

当 pymavlink 库有 bug（如 `_instances=None`）、解析失败、或需要精确检查传感器位掩码时，直接解析 MAVLink v2 二进制帧。

## 关键技术参数

### MAVLink v2 帧结构

```
[0xFD] [payload_len] [incompat] [compat] [seq] [sysid] [compid] [msgid(3)] [payload] [checksum(2)]
  ^magic  ^1 byte      ^1         ^1      ^1    ^1      ^1       ^LE uint24
```

- 帧头: 12 字节
- 总帧长: 12 + payload_len + 2
- Magic: `0xFD`

### 关键消息 ID

| ID | 名称 | 用途 |
|----|------|------|
| 0 | HEARTBEAT | system_status、base_mode |
| 1 | SYS_STATUS | 传感器 present/enabled/health 位掩码 |
| 27 | RAW_IMU | IMU1 (ICM20602) 原始值 |
| 29 | SCALED_PRESSURE | 气压计数据 |
| 74 | VFR_HUD | 空速、地速 |
| 116 | SCALED_IMU2 | IMU2 (BMI055) 缩放值 + 磁力计 |
| 152 | MEMINFO | 剩余内存 |
| 253 | STATUSTEXT | 文本输出 |

### SYS_STATUS 消息布局 (31 bytes)

```
偏移  类型      字段
0-3   uint32_t  onboard_control_sensors_present
4-7   uint32_t  onboard_control_sensors_enabled
8-11  uint32_t  onboard_control_sensors_health
12-13 uint16_t  load (d%)
14-15 uint16_t  voltage_battery (mV)
16-17 int16_t   current_battery (cA)
18    int8_t    battery_remaining (%)
19-20 uint16_t  drop_rate_comm
21-22 uint16_t  errors_comm
23-30 uint16_t  errors_count1-4
```

### SYS_STATUS 传感器位掩码

```
bit 0  (0x01) = MAV_SYS_STATUS_SENSOR_3D_GYRO
bit 1  (0x02) = MAV_SYS_STATUS_SENSOR_3D_ACCEL
bit 2  (0x04) = MAV_SYS_STATUS_SENSOR_3D_MAG
bit 3  (0x08) = MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE
bit 4  (0x10) = MAV_SYS_STATUS_SENSOR_3D_GYRO2
bit 5  (0x20) = MAV_SYS_STATUS_SENSOR_3D_ACCEL2
bit 6  (0x40) = MAV_SYS_STATUS_SENSOR_3D_MAG2
bit 7  (0x80) = MAV_SYS_STATUS_SENSOR_GPS
```

### RAW_IMU 消息布局 (20 bytes)

```
偏移  类型      字段
0-7   uint64_t  time_usec
8-9   int16_t   xacc
10-11 int16_t   yacc
12-13 int16_t   zacc
14-15 int16_t   xgyro
16-17 int16_t   ygyro
18-19 int16_t   zgyro
```

### SCALED_PRESSURE 消息 (14 bytes)

```
0-3   uint32_t  time_boot_ms
4-7   float     press_abs (hPa)
8-11  float     press_diff (hPa)
12-13 int16_t   temperature (cdegC/100)
```

## 诊断解析模板

```python
def decode_sys_status(payload):
    """Decode SYS_STATUS from raw 31-byte payload"""
    if len(payload) < 17:
        return None
    pres = struct.unpack('<I', payload[0:4])[0]
    enab = struct.unpack('<I', payload[4:8])[0]
    hlth = struct.unpack('<I', payload[8:12])[0]
    
    bits = [('gyro',1),('accel',2),('mag',4),('pressure',8),
            ('gyro2',16),('accel2',32),('mag2',64)]
    
    unhealthy = []
    for name, bit in bits:
        if (pres & bit) and not (hlth & bit):
            unhealthy.append(name)
    return {
        'present': pres,
        'healthy': hlth,
        'unhealthy': unhealthy,
        'volt_mv': struct.unpack('<H', payload[14:16])[0],
        'load': struct.unpack('<H', payload[12:14])[0],
    }
```

## 诊断模式 — 传感器全零判定法

### 场景：气压计全零 (SCALED_PRESSURE abs=0.0, temp≈-142°C)

**根因追踪**：
1. SCALED_PRESSURE 出现 → 传感器被成功 probe（backend 已注册）
2. abs=0.0 → ADC 返回全零 → SPI 读失败
3. SYS_STATUS: present 含 pressure bit，health 不含 → 已 probe 但无数据
4. 追溯原因：SPI 总线未初始化 / CS 引脚未配置 / 时钟未使能

### 场景：IMU 间歇 -999 (RAW_IMU 某轴偶发 -999)

- `-999` 在 ArduPilot 中是 SPI 读失败的典型返回值（零校验后回退值）
- 对比 SCALED_IMU2 确认哪颗 IMU 受影响
- 可能原因：CS 时序 / 中断抢占 / SPI DMA 竞态

### 场景：SYS_STATUS health 与系统实际表现不符

- SYS_STATUS 传感器健康≠系统功能健康
- EKF 可能已收敛 (flags=167) 但 SYS_STATUS 仍报 unhealthy
- 优先看 EKF_STATUS_REPORT 的 flags 和 variance
