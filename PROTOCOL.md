# BLE Protocol Documentation - Scent Diffusers

## Devices

### 1. ShinePick QT-I300 (BT-ivy034A31)
- **Amazon ASIN**: B0DR8V4T97
- **Capacity**: 240ml
- **Connectivity**: BLE only
- **App**: Aroma Buddy
- **BLE Name**: `BT-ivy034A31`
- **BLE Address (macOS)**: `119028BD-BDDD-67CA-E4C7-B97174D095E3`
- **Serial**: YKF342-202B
- **Firmware**: WB33-1.1.2.2
- **Protocol**: Tuya BLE

### 2. Aroma-Link Diffuser (Scent K.A5.WIFI)
- **Amazon ASIN**: B0CCVG2BLY
- **Capacity**: 300ml
- **Connectivity**: WiFi + BLE (we use BLE only)
- **App**: Aroma-Link
- **BLE Name**: `Scent K.A5.WIFI`
- **BLE Address (macOS)**: `A1E65CAE-4DD7-BD60-3599-6093ACFA7F24`
- **Model**: A6/Pro300_2
- **Firmware**: V1.0.20250114
- **Serial**: 20412519605109099
- **Protocol**: Custom (Aroma-Link proprietary)

---

## GATT Structure (both devices)

Both devices use the same GATT service:

| Service | UUID |
|---------|------|
| Vendor Specific | `0000FFF0-0000-1000-8000-00805f9b34fb` |

### ShinePick Characteristics
| Char | UUID | Handle | Properties | Purpose |
|------|------|--------|------------|---------|
| FFF2 | `0000FFF2-...` | 0x0012 | read, write, write-no-resp | **Send commands** |
| FFF1 | `0000FFF1-...` | 0x0014 | notify | **Receive responses** |

Additional services: Battery (0x180F), Device Information (0x180A)

### Aroma-Link Characteristics
| Char | UUID | Handle | Properties | Purpose |
|------|------|--------|------------|---------|
| FFF2 | `0000FFF2-...` | 0x0012 | write | **Send commands** |
| FFF1 | `0000FFF1-...` | 0x0017 | read, notify | **Receive responses** |
| FFF3 | `0000FFF3-...` | 0x0013 | indicate | Indications |
| FFF4 | `0000FFF4-...` | 0x0019 | write-no-resp | Alt write channel |

---

## ShinePick Protocol (Tuya BLE)

### Packet Format
```
55 AA [version] [command] [length_hi length_lo] [data...] [checksum]
```
- **Header**: `55 AA` (fixed)
- **Version**: `0x00`
- **Command types**:
  - `0x06` = DP Write (send command to device)
  - `0x07` = DP Report (device status notification)
  - `0x08` = Query all DPs
  - `0x1C` = Time Sync
- **Length**: 2 bytes big-endian, data length only
- **Checksum**: sum of ALL bytes (incl header) mod 256

### DP (Data Point) Format
```
[dp_id] [dp_type] [value_len_hi value_len_lo] [value...]
```
DP types: `0x00`=raw, `0x01`=bool, `0x02`=value, `0x03`=string, `0x04`=enum

### Known DPs

| DP ID | Type | Description | Values |
|-------|------|-------------|--------|
| 1 | bool | Power | 0=off, 1=on |
| 3 | bool | Unknown | |
| 4 | bool | Unknown (always 1) | |
| 5 | bool | Unknown (always 1) | |
| 9 | bool | Unknown | |
| 15 | bool | Unknown | |
| 17 | bool | Unknown | |
| 18 | raw | Schedule config (55 bytes) | See below |
| 20 | raw | Status/counters | |
| 22 | raw | Unknown | |
| 23 | raw | Unknown (limits?) | |

### Commands

**Power ON:**
```
55 AA 00 06 00 05 01 01 00 01 01 0E
```

**Power OFF:**
```
55 AA 00 06 00 05 01 01 00 01 00 0D
```

**Query all DPs:**
```
55 AA 00 08 00 00 07
```

**Time Sync:**
```
55 AA 00 1C 00 08 01 [year%100] [month] [day] [hour] [min] [sec] [weekday] [checksum]
```
Weekday: 1=Monday ... 7=Sunday

### Schedule Configuration (DP18)

DP18 contains ALL 5 setups in one write (55 bytes raw value).

Each setup is 11 bytes:
```
[index]       - Setup number 0-4
[weekday]     - Bitmask: bit0=Mon, bit1=Tue, bit2=Wed, bit3=Thu, bit4=Fri, bit5=Sat, bit6=Sun
[start_h]     - Start hour (0-23)
[start_m]     - Start minute (0-59)
[end_h]       - End hour (0-23)
[end_m]       - End minute (0-59)
[enabled]     - 0=disabled, 1=enabled
[work_hi]     - Work duration seconds (big-endian 16-bit)
[work_lo]
[pause_hi]    - Pause duration seconds (big-endian 16-bit)
[pause_lo]
```

Full DP18 write:
```
55 AA 00 06 00 3B 12 00 00 37 [setup0: 11 bytes] [setup1: 11 bytes] ... [setup4: 11 bytes] [checksum]
```

Example - Setup 0: Monday+Tuesday, 08:00-18:00, work=30s, pause=60s:
```
Setup 0: 00 03 08 00 12 00 01 00 1E 00 3C
         idx days strt  end   en work  pause
```

---

## Aroma-Link Protocol (Custom)

### Packet Format
```
A5 AA AC [xor_checksum] [payload...] C5 CC CA
```
- **Header**: `A5 AA AC` (fixed)
- **XOR checksum**: XOR of all payload bytes
- **Trailer**: `C5 CC CA` (fixed)

### Command Types (first byte of payload)
- `0x52` = Read/Query (app -> device)
- `0x53` = Status Report (device -> app)
- `0x57` = Write/Command (app -> device)

### Sub-Commands

| Cmd | SubCmd | Direction | Description |
|-----|--------|-----------|-------------|
| 0x57 | 0x08 | Write | Power control |
| 0x57 | 0x03 | Write | Fan control |
| 0x57 | 0x16 | Write | Schedule config |
| 0x57 | 0x17 | Write | Time sync |
| 0x52 | 0x01 | Query | Device name |
| 0x52 | 0x03 | Query | Fan status |
| 0x52 | 0x0D | Query | Device info |
| 0x52 | 0x15 | Query | All schedules |
| 0x53 | 0x08 | Report | Power state |
| 0x53 | 0x03 | Report | Fan state |
| 0x53 | 0x09 | Report | Spray cycle status |
| 0x53 | 0x0A | Report | Full status (after time sync) |

### Commands

**Power ON:**
```
A5 AA AC 5E 57 08 01 C5 CC CA
```

**Power OFF:**
```
A5 AA AC 5F 57 08 00 C5 CC CA
```

**Fan ON:**
```
A5 AA AC 44 57 03 10 C5 CC CA
```

**Fan OFF:**
```
A5 AA AC 54 57 03 00 C5 CC CA
```

**Time Sync:**
```
A5 AA AC [xor] 57 17 [year_hi year_lo] [month] [day] [hour] [min] [sec] [weekday] C5 CC CA
```
Year: full year as 16-bit big-endian (e.g., 0x07EA = 2026)
Weekday: 1=Monday ... 7=Sunday (ISO weekday)

### Schedule Configuration (57 16)

The Aroma-Link stores schedules **per day of the week**. Each day has 5 independent time slots.

Write command data (46 bytes after 57 16):
```
[weekday_mask]  (1 byte - which day(s) to write to)

5x Slot, each 9 bytes:
  [start_h]     - Start hour (0-23)
  [start_m]     - Start minute (0-59)
  [end_h]       - End hour (0-23)
  [end_m]       - End minute (0-59)
  [flags]       - 0x11 = slot enabled, 0x10 = slot disabled
  [work_hi]     - Work duration seconds (big-endian 16-bit)
  [work_lo]
  [pause_hi]    - Pause duration seconds (big-endian 16-bit)
  [pause_lo]
```

Weekday mask: bit0=Mon, bit1=Tue, bit2=Wed, bit3=Thu, bit4=Fri, bit5=Sat, bit6=Sun

Example - Thursday, Slot 1: 13:15-21:30, work=35s, pause=55s:
```
A5 AA AC [xor] 57 16
  08                          <- Thursday (bit 3)
  0D 0F 15 1E 11 00 23 00 37 <- Slot 1: 13:15-21:30, ON, work=35, pause=55
  00 00 00 00 10 00 0A 00 78  <- Slot 2: disabled, defaults
  00 00 00 00 10 00 0A 00 78  <- Slot 3: disabled, defaults
  00 00 00 00 10 00 0A 00 78  <- Slot 4: disabled, defaults
  00 00 00 00 10 00 0A 00 78  <- Slot 5: disabled, defaults
C5 CC CA
```

### Status Reports

**Spray cycle status (53 09):**
```
53 09 [phase] [work_hi work_lo] [pause_hi pause_lo] [start_h start_m] [end_h end_m] [enabled]
```
Phase: 0x00=idle, 0x01=spraying, 0x02=paused

**Power state (53 08):**
```
53 08 [state]   <- 0x01=on, 0x00=off
```

**Fan state (53 03):**
```
53 03 [state]   <- 0x10=on, 0x00=off
```

---

## Building Packets (Python)

```python
# Tuya (ShinePick)
def tuya_packet(cmd: int, data: bytes) -> bytes:
    pkt = bytes([0x55, 0xAA, 0x00, cmd, (len(data) >> 8) & 0xFF, len(data) & 0xFF]) + data
    return pkt + bytes([sum(pkt) & 0xFF])

def tuya_dp_bool(dp_id: int, value: bool) -> bytes:
    data = bytes([dp_id, 0x01, 0x00, 0x01, 0x01 if value else 0x00])
    return tuya_packet(0x06, data)

# Aroma-Link
def aroma_link_packet(payload: bytes) -> bytes:
    xor = 0
    for b in payload:
        xor ^= b
    return bytes([0xA5, 0xAA, 0xAC, xor]) + payload + bytes([0xC5, 0xCC, 0xCA])
```

---

## Notes
- BLE allows only ONE connection at a time - disconnect apps before connecting from HA/Mac
- Both devices use service UUID 0000FFF0 and write to characteristic FFF2
- Aroma-Link has additional WiFi capability (cloud API documented elsewhere) but BLE is preferred for local control
- Time sync should be sent on every connection to keep device clock accurate
- ShinePick minimum pause time is 15 seconds
- Aroma-Link default values: work=10s, pause=120s
