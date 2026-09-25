"""Constants for the Scent Diffuser integration."""
from enum import StrEnum

DOMAIN = "scent_assistant"

# ---------------------------------------------------------------------------
# Device types
# ---------------------------------------------------------------------------

class DeviceType(StrEnum):
    TUYA_BLE = "tuya_ble"          # ShinePick QT-I300 and similar
    AROMA_LINK = "aroma_link"      # Aroma-Link WiFi+BLE diffusers
    SCENTIMENT = "scentiment"      # Scentiment Diffuser Air 2 (BLE, JSON protocol)
    SCENT_MARKETING_AK = "scent_marketing_ak"          # Scent Marketing app, AK family (FFF0 service, simple byte commands)
    SCENT_MARKETING_GW = "scent_marketing_gw"          # Scent Marketing app, GW family (EE01 service, framed DP protocol)
    SCENT_MARKETING_GW_XOR = "scent_marketing_gw_xor"  # Scent Marketing app, GW family with XOR-encrypted JSON payload
    AROMELY_ARO_MAX = "aromely_aro_max"                # Aromely Aro Max (FFE0 service, 55-framed register protocol)
    SCENT_TECH = "scent_tech"                          # YooAI OEM, ScentLab / Scent Tech apps (FFE1, 55 AA … 5A frames)


# ---------------------------------------------------------------------------
# BLE UUIDs
# ---------------------------------------------------------------------------

SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
CHAR_WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
CHAR_INDICATE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"  # Aroma-Link only

# Scentiment Diffuser Air 2 — custom 16-bit UUIDs in the SIG base
SCENTIMENT_SERVICE_UUID = "00000180-0000-1000-8000-00805f9b34fb"
SCENTIMENT_CHAR_WRITE_UUID = "0000dead-0000-1000-8000-00805f9b34fb"  # Commands (JSON)
SCENTIMENT_CHAR_NOTIFY_UUID = "0000fef3-0000-1000-8000-00805f9b34fb"  # State notifications
SCENTIMENT_CHAR_INFO_UUID = "0000fef4-0000-1000-8000-00805f9b34fb"   # Device metadata (read)

# Scent Marketing app — AK family (FFF0 service, simple byte commands + heartbeat)
SM_AK_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
SM_AK_CHAR_UUID = "0000fff6-0000-1000-8000-00805f9b34fb"

# ---------------------------------------------------------------------------
# Aromely Aro Max — 55-framed register protocol
# ---------------------------------------------------------------------------
# Data flows on the FFE0 service (write FFE2 / notify FFE1). The device
# *advertises* the AF30 service UUID and carries "DiffuserAroMax" in its
# manufacturer data; the BLE local name is a per-unit serial, so detection
# keys on the advertised service / manufacturer data, not the name.
# Decoded from @ahbhimani1's two nRF sniffer captures (#4), cross-checked
# against the official Aromely app screenshots.
AROMELY_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
AROMELY_CHAR_WRITE_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"
AROMELY_CHAR_NOTIFY_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
AROMELY_ADV_SERVICE_UUID = "0000af30-0000-1000-8000-00805f9b34fb"

# Frame: 55 <dir> <reg> <type> [<len> <payload...>] <checksum>
# checksum = sum of every byte after the 0x55 header, mod 256.
AROMELY_FRAME_HEADER = 0x55
AROMELY_DIR_WRITE = 0x10    # app -> device
AROMELY_DIR_NOTIFY = 0x11   # device -> app
AROMELY_TYPE_READ = 0x00    # read request (no length/payload follows)
AROMELY_TYPE_DATA = 0x05    # data write / response (length + payload follow)
# Registers
AROMELY_REG_TIME = 0x00         # time sync: 20 HH MM SS (BCD)
AROMELY_REG_SCHED_WRITE = 0x01  # write schedule slot 1
AROMELY_REG_FAN = 0x06          # fan mode (0=off, 1=low, 2=full)
AROMELY_REG_SESSION = 0x07      # session start (07 05 01 01)
AROMELY_REG_POWER = 0x11        # power / schedule-enable (1=on, 0=off)
AROMELY_REG_ID = 0x20           # device id push (read-only)
AROMELY_REG_NAME = 0xDA         # device name (read)
AROMELY_REG_INFO = 0xDB         # device flags (read)
AROMELY_REG_LABEL = 0xDC        # note/label (read)
AROMELY_REG_SCHED1 = 0xF1       # schedule slot 1 incl. enabled flag (read)
AROMELY_REG_SCHED_ALL = 0xF2    # all four schedule slots (read)

# ---------------------------------------------------------------------------
# Scent Tech / ScentLab (YooAI OEM) — `55 AA LEN CMD DATA… CHK 5A`
# ---------------------------------------------------------------------------
# Units advertise as "Scent-<serial>" (Magnifiscent ZenPlug / Grasse Aroma
# GAH-04P "Scent-B04P…", "Scent-B501F…", unbranded "Scent-BG101W…") and are
# driven by YooAI's ScentLab (com.yooai.scent.lab) or Scent Tech
# (com.yooai.scentlife) apps. Protocol from @alexlewer's APK analysis and
# ScentLab BLE integration (#30, MIT) and @marzliak's live Scent Tech log
# (#36, 681 frames).
#
# One characteristic, FFE1, carries both writes and notifications.
# LEN counts CMD + DATA; CHK makes the sum of every byte from the first
# 0x55 through CHK zero mod 256. Integers are little-endian. A reply's
# command is usually the request's command | 0x80.
SCENT_TECH_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
SCENT_TECH_CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

SCENT_TECH_HEADER = b"\x55\xAA"
SCENT_TECH_TRAILER = 0x5A

SCENT_TECH_CMD_TIME_SYNC = 0x06       # u32 LE local wall clock read as UTC
SCENT_TECH_CMD_CONTROL = 0x07         # [action, value, 00]
SCENT_TECH_CMD_READ_TIMERS = 0x08     # -> 0x88
SCENT_TECH_CMD_ENQUIRY = 0x09         # 01 00, sent after authentication
SCENT_TECH_CMD_WRITE_TIMER = 0x14     # one 16-byte record -> 0x94
SCENT_TECH_CMD_PW_QUERY = 0x47        # -> 0xC7, data[0] != 0: password set
SCENT_TECH_CMD_PW_SUBMIT = 0x48       # 04 + 4 ASCII -> 0xC8, data[0] != 0: ok
SCENT_TECH_CMD_CAPABILITY = 0x51      # -> 0xD1 identity block

SCENT_TECH_RESP_STATE = 0x21          # pushed state; data[4] bit0 = power
SCENT_TECH_RESP_TIMERS = 0x88
SCENT_TECH_RESP_WRITE_TIMER = 0x94
SCENT_TECH_RESP_PW_QUERY = 0xC7
SCENT_TECH_RESP_PW_SUBMIT = 0xC8

# Control actions (cmd 0x07). Only power is used: fan and lock are
# swapped between the two sources (ScentLab APK: fan 0x10 / lock 0x11;
# Scent Tech live log: fan 0x11 / lock 0x10), so they stay out until a
# unit confirms which applies to it.
SCENT_TECH_ACTION_POWER = 0x12

SCENT_TECH_TIMER_RECORD_SIZE = 16
SCENT_TECH_TIMER_SLOTS = 5
# Weekday field: bit0 Mon … bit6 Sun, bit7 set whenever any day is.
SCENT_TECH_WEEKDAY_ANY = 0x80

# AK command opcodes (mirrors com.IAA360.ChengHao.Device.Data.BtDataModel).
# Negative bytes in the Java source are decoded to their 0..255 equivalents.
SM_AK_CMD_QUERY_INFO = 0x81           # request device name
SM_AK_CMD_PROBE = 0x82                # request firmware/PCB info
SM_AK_CMD_QUERY_DEVICE_TYPE = 0x89    # ask the device for its model string
SM_AK_CMD_TIME_SYNC = 0x80            # writeTime() opcode
SM_AK_CMD_DEVICE_NAME = 0x82          # writeDeviceName() opcode
SM_AK_CMD_DEVICE_LABEL = 0x85         # writeDeviceLabel() opcode prefix
SM_AK_CMD_OIL_NAME = 0x86             # writeOilName()
SM_AK_CMD_OIL_AMOUNT = 0x8D           # writeOilAmount() opcode (0x8D = -115)
SM_AK_CMD_CONTROL_STATE = 0x2D        # writeTotalControl(): 0x2D + bitmask
SM_AK_CMD_SCHEDULE_V2 = 0x03          # DeviceTimeModel v2.0 schedule write
SM_AK_CMD_SCHEDULE_V3 = 0x2A          # DeviceTimeModel v3.0 schedule write (CMD_GET_FIRMWARE_VERSION_RESEX)

# AK read opcodes — observed in @Mins95's V2/V3 captures of the official
# Scent Marketing app. The app issues these on every connect to rebuild
# its UI state from the device. Used here for state read-back on HA
# restart so entities reflect what the diffuser actually has stored.
#
# V2 read commands (single-byte opcode, slot in low nibble for schedule):
SM_AK_CMD_READ_SCHEDULE_V2 = 0x83     # 8301..8305 → 83 SS HH MM HH MM DD LL ...
SM_AK_CMD_READ_DEVICE_TYPE = 0x89     # 89 → 89 + type byte
SM_AK_CMD_READ_DEVICE_NAME = 0x81     # 81 → 81 + len + utf8 name
SM_AK_CMD_READ_DEVICE_LABEL = 0x85    # 85 → 85 + label byte
SM_AK_CMD_READ_FIRMWARE = 0x86        # 86 → 86 + "Vx.xx" PCB firmware
SM_AK_CMD_READ_EQUIPMENT = 0x88       # 88 → 88 + "Vx.x" equipment version

# V3 read commands (introduced after the V3 secondary login). The device
# pushes responses async, sometimes multiple per query:
SM_AK_CMD_V3_READ_NAME = 0xC6         # C6 → 42 + utf8 name (multi-push variant)
SM_AK_CMD_V3_READ_SCHEDULES = 0xC5    # C5 → multiple 4A 01 02 03 04 SS ... pushes
SM_AK_CMD_V3_READ_SLOT = 0xCA         # CA 01 SS → individual slot 4A response
SM_AK_CMD_V3_READ_LABEL = 0xC7        # C7 → 48 + 16-byte label (e.g. "Evasion" padded)
SM_AK_CMD_V3_READ_OIL = 0xC8          # C8 → 4B 00 <max_ml u16> <current_ml u16>
SM_AK_CMD_V3_READ_OIL_INFO = 0xCE     # CE → 50 <enabled> <consumption×100 u16> <…> ; only consumption trusted (4B owns current/max/%, days computed — @Mins95 #8)
SM_AK_CMD_V3_READ_FIRMWARE = 0xCB     # CB → 44 + PCB version + Equipment version (32 bytes)
SM_AK_CMD_V3_READ_CONTROL = 0xC4      # C4 → 4D 01 <mask>  (power/fan/lamp/lock bitmask)
SM_AK_CMD_V3_READ_MODEL = 0xD0        # D0 → 45 + utf8 model code (e.g. "A305M")
SM_AK_CMD_V3_READ_GRADE_TABLE = 0xC3  # C3 → 47 + N×(work_u16 pause_u16) Level grade table

# Response opcodes (parsed by `parse_notification`):
SM_AK_RESP_SCHEDULE_V2 = 0x83         # mirrors the V2 read opcode
SM_AK_RESP_SCHEDULE_V3 = 0x4A         # mirrors the V3 schedule write opcode (0x2A) flipped
SM_AK_RESP_DEVICE_NAME_V3 = 0x42      # response to C6
SM_AK_RESP_LABEL_V3 = 0x48            # response to C7
SM_AK_RESP_MODEL_V3 = 0x45            # response to D0
SM_AK_RESP_FIRMWARE_V3 = 0x44         # response to CB (same opcode as our existing V2 parser)
SM_AK_RESP_CONTROL = 0x4D             # control bitmask push/read (V2: 4D mask, V3: 4D 01 mask)
SM_AK_RESP_GRADE_TABLE = 0x47         # response to C3: 47 + N×(work_u16 pause_u16), @Mins95 #8

# Offset 1 of the V3 schedule frame (2A write / 4A read) is the diffuser
# endpoint. Single-pump units always report 01; the A309 has three
# independently programmable pumps and pushes a full slot set per
# endpoint (@danieledwardgeorgehitchcock, #22). We write to the first one
# only, so we read that one only — see the 4A branch of
# `parse_notification`.
SM_AK_V3_PRIMARY_ENDPOINT = 0x01

# AK control-state bitmask layout (LSB = onOff). Mirrors writeTotalControl()
# which builds a binary string "lock|lamp|1|demo|fan|onOff" → int(s, 2).
SM_AK_CTRL_BIT_ONOFF = 0
SM_AK_CTRL_BIT_FAN = 1
SM_AK_CTRL_BIT_DEMO = 2
SM_AK_CTRL_BIT_RESERVED = 3   # always 1 in the Android source
SM_AK_CTRL_BIT_LAMP = 4
SM_AK_CTRL_BIT_LOCK = 5

# AK login & V3-mode handshake (reverse-engineered by @Mins95 from real BLE
# captures of two SA_-named devices). The device ignores every other write
# until it has seen the primary login frame; without it the integration
# appears to work (writes succeed at the GATT level) but the device drops
# the payload silently.
#
# Primary login = opcode 0x8F + ASCII "8888" (the default app PIN). The
# device replies with 0x8F + "OK_V2.0" or 0x8F + "OK_V3.0", which selects
# the command set.
SM_AK_LOGIN_PRIMARY = bytes.fromhex("8F38383838")
# V3 devices accept a secondary login frame after the primary; only after
# that do they respond to the V3 power/fan/schedule opcodes. V2 devices
# ignore this frame.
SM_AK_LOGIN_SECONDARY_V3 = bytes.fromhex("8F383838384F4B3031")
SM_AK_OPCODE_LOGIN_RESPONSE = 0x8F

# V3 devices require this 3-byte "commit" suffix as a *separate* frame after
# most state-changing writes (power-on, fan toggle, schedule write). Same
# bytes as the legacy AK heartbeat — different role.
SM_AK_V3_COMMIT = bytes([0xE0, 0xAA, 0x55])

# V3 fan-control frames (note: 0x2A prefix, not the 0x2D control bitmask).
SM_AK_V3_FAN_ON = bytes.fromhex("2A01020300")
SM_AK_V3_FAN_OFF = bytes.fromhex("2A01020100")

# V3 schedule layout has only two slots, fixed by purpose in the official
# app's UI (Weekend / Weekday). The slot index is captured verbatim — we
# don't know if the device's firmware accepts other indices.
SM_AK_V3_SLOT_WEEKEND = 0x04
SM_AK_V3_SLOT_WEEKDAY = 0x05
# Trailer bytes appended to every V3 schedule write. Purpose unknown
# (possibly fade-in / fade-out durations); captured verbatim from the app.
SM_AK_V3_SCHEDULE_TRAILER = bytes.fromhex("000F012C")

# Day-mask bit layout for AK schedule writes, derived from @Mins95's
# observations: 0x7F = every day, 0x3E = Mon-Fri, 0x41 = Sat+Sun. That
# gives bit 0 = Sun, bit 1 = Mon, ..., bit 6 = Sat.
SM_AK_DAY_MASK_SUN = 0x01
SM_AK_DAY_MASK_MON = 0x02
SM_AK_DAY_MASK_SAT = 0x40
SM_AK_DAY_MASK_WEEKDAYS = 0x3E  # Mon-Fri
SM_AK_DAY_MASK_WEEKEND = 0x41   # Sat+Sun
SM_AK_DAY_MASK_DAILY = 0x7F

# Scent Marketing app — GW family (EE01 service, framed DP protocol)
SM_GW_SERVICE_UUID = "0000ee01-0000-1000-8000-00805f9b34fb"
SM_GW_NOTIFY_UUID = "0000ee02-0000-1000-8000-00805f9b34fb"
SM_GW_WRITE_UUID = "0000ee03-0000-1000-8000-00805f9b34fb"

# Optional alternate GW service used by WiFi-enabled GW devices. The notification
# pipeline treats both UUIDs identically; only the encryption layer differs.
SM_GW_ALT_SERVICE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
SM_GW_ALT_NOTIFY_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
SM_GW_ALT_WRITE_UUID = "0000ff03-0000-1000-8000-00805f9b34fb"

# ---------------------------------------------------------------------------
# BLE name patterns for device detection
# ---------------------------------------------------------------------------

BLE_NAME_PATTERNS = {
    # "Scent " — Aroma-Link / JCloud / Cavir / Crearoma (Dewoo OEM, Aroma-Link app)
    # "DAP.A5" — DAP Smart Scent Air Machine (Dewoo OEM, AromaPlan app)
    DeviceType.AROMA_LINK: ["Scent ", "DAP.A5"],
    DeviceType.TUYA_BLE: ["BT-ivy"],
    DeviceType.SCENTIMENT: ["Scentiment"],
    # Most Aro Max units advertise a per-unit serial as the local name and
    # are detected via the AF30 service / manufacturer data instead, but
    # some expose "DiffuserAroMax" directly — match that as a fallback.
    DeviceType.AROMELY_ARO_MAX: ["DiffuserAroMax", "DiffuserAro"],
    # "SA_" — Scent Marketing AK sold under other brands (AromaTech
    # Ambience SA_AT600, #29). Normally these are identified by their
    # manufacturer data, but some units advertise none at all: just the
    # FFF0 service UUID and the name. @kartikkp's GATT dump shows the
    # AK shape unambiguously — FFF6 carrying read + write-without-
    # response + notify as one command/response channel — so route the
    # name prefix to the AK family rather than letting it fall through
    # to the Aroma-Link default, whose FFF1-notify / FFF2-write layout
    # these devices do not have.
    DeviceType.SCENT_MARKETING_AK: ["SA_"],
    # "Scent-" (hyphen) — Scent Tech / ScentLab units (YooAI OEM). Distinct
    # from Aroma-Link's "Scent " (space); before this entry they fell
    # through to the Aroma-Link default and never answered (#30).
    DeviceType.SCENT_TECH: ["Scent-"],
}

# Aroma-Link name code: the second dot part of "<name>.<code>.<suffix>".
AL_NAME_CODES = {"A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"}

# Scent Marketing devices are identified primarily by manufacturer-specific
# data in their advertisement, not by name. These constants are used by the
# detection logic when an AdvertisementData object is available.
SM_MFR_ID_AK = 22851       # 0x5943 — AK family
SM_MFR_ID_GW = 17932       # 0x460C — GW family (BLE/WiFi/Cellular)
SM_MFR_ID_GW_ALT = 61441   # 0xF001 — GW family alternate ID (WiFi-routed devices)

# GW manufacturer-data leading byte determines the encoding sub-variant.
# 00 / unknown        → plain binary DP protocol
# 01 / 02 / 03        → WiFi-enabled, XOR-encrypted JSON payload
# B1 / B2             → Cellular, XOR-encrypted JSON payload (treated as XOR)
SM_GW_FLAG_WIFI = {"01", "02", "03"}
SM_GW_FLAG_CELLULAR = {"B1", "B2"}

# AK manufacturer-data leading byte 02 → device requires periodic heartbeat
# `E0AA55` to keep BLE notifications flowing.
SM_AK_FLAG_HEARTBEAT = "02"
SM_AK_HEARTBEAT_BYTES = bytes([0xE0, 0xAA, 0x55])
SM_AK_HEARTBEAT_INTERVAL_S = 5.0

# ---------------------------------------------------------------------------
# Tuya BLE protocol constants
# ---------------------------------------------------------------------------

TUYA_HEADER = bytes([0x55, 0xAA])
TUYA_VERSION = 0x00

TUYA_CMD_DP_WRITE = 0x06
TUYA_CMD_DP_REPORT = 0x07
TUYA_CMD_QUERY = 0x08
TUYA_CMD_TIME_SYNC = 0x1C

TUYA_DP_TYPE_RAW = 0x00
TUYA_DP_TYPE_BOOL = 0x01
TUYA_DP_TYPE_VALUE = 0x02
TUYA_DP_TYPE_STRING = 0x03
TUYA_DP_TYPE_ENUM = 0x04

TUYA_DP_POWER = 1
TUYA_DP_SCHEDULE = 18

# ---------------------------------------------------------------------------
# Aroma-Link BLE protocol constants
# ---------------------------------------------------------------------------

AL_HEADER = bytes([0xA5, 0xAA, 0xAC])
AL_TRAILER = bytes([0xC5, 0xCC, 0xCA])

AL_CMD_QUERY = 0x52
AL_CMD_STATUS = 0x53
AL_CMD_WRITE = 0x57

AL_SUB_POWER = 0x08
AL_SUB_FAN = 0x03
AL_SUB_SCHEDULE = 0x16
AL_SUB_TIME_SYNC = 0x17
AL_SUB_DEVICE_NAME = 0x01
AL_SUB_DEVICE_INFO = 0x0D
AL_SUB_QUERY_SCHEDULES = 0x15   # READ_WEEK_WORK_TIME — ~320-byte reply, unused (see build_query)
AL_SUB_WORK_INFO = 0x09         # READ_WORK_INFO / LISTEN_WORKING_MSG: phase + *remaining* times
AL_SUB_WORK_FREQUENCY = 0x06    # READ_WORK_FREQUENCY <weekday>: 5 slots × (work u16, pause u16, flags)
# Reassembly cap for multi-notification frames. The longest reply the app
# reads is READ_WEEK_WORK_TIME at ~320 bytes; anything past this is junk.
AL_RX_BUFFER_MAX = 512
# Read-register sub-command for the liquid/oil level. The device answers a
# `52 1E` query with `52 1E <percent>` (e.g. 0x50 = 80%). Decoded from
# @ndoty's Aromadd U5 Pro HCI snoop (#18), where the app read 0x1E and got
# 0x50 while its UI showed 80%.
AL_SUB_OIL_LEVEL = 0x1E
# "All work info" register (READ_ALL_WORK_INFO in the decompiled app).
# The response carries device time, fan/lamp/power, work status, the
# remaining seconds of the current work and pause phases, the schedule
# window, MAC, raw oil weight, battery, and capability flags — parsed by
# the app's handlerAllWorkStatus(). The device may also push it
# unsolicited as `53 0A` (seen in @ndoty's capture after time sync).
AL_SUB_ALL_WORK_INFO = 0x0A

AL_FAN_ON_VALUE = 0x10
AL_FAN_OFF_VALUE = 0x00

AL_SLOT_ENABLED = 0x11
AL_SLOT_DISABLED = 0x10

# Status report phases
AL_PHASE_IDLE = 0x00
AL_PHASE_SPRAYING = 0x01
AL_PHASE_PAUSED = 0x02

# ---------------------------------------------------------------------------
# Scent Marketing — GW family DP-frame protocol constants
# ---------------------------------------------------------------------------

# Frame header byte (precedes the DP-count byte).
SM_GW_FRAME_HEADER = 0xFF

# Per-DP type tags. Composite (length-prefixed) tags are 0xAF (binary) and
# 0xBF (UTF-8). Inline boolean tags are 0x01 (generic bool), 0x11 (lock,
# = aisbase.Constants.CMD_TYPE.CMD_GEN_CIPHER). The Tuya-style hex parser
# also reads narrow integers as type 0x0X where X is the byte count.
SM_GW_TYPE_BINARY = 0xAF
SM_GW_TYPE_TEXT = 0xBF
SM_GW_TYPE_BOOL = 0x01
SM_GW_TYPE_LOCK = 0x11

# Fixed payload lengths the firmware demands for some text DPs.
SM_GW_LEN_NAME = 19      # DP 6 — 19-byte zero-padded device name
SM_GW_LEN_REMARK = 16    # DP 20 — 16-byte zero-padded remark

# Schedule frame (DP 4) constants. The mode byte encodes:
#   0 = INTERVAL (timed tasks)
#   1 = NONE / COUNT_DOWN
#   2 = QUICK_FRAGRANCE (one-shot spray)
SM_GW_MODE_INTERVAL = 0
SM_GW_MODE_NONE = 1
SM_GW_MODE_QUICK = 2

# Chunk size for multi-packet writes. The Scent Marketing app uses 18 bytes
# of payload per chunk, prefixed by a 2-byte (nonce, sequence) header — fitting
# the 20-byte default BLE write MTU.
SM_GW_CHUNK_SIZE = 18

# Data Point IDs (subset of the app's full DP map — only the ones we read or
# write are listed here).
SM_GW_DP_POWER = 1
SM_GW_DP_FAN = 2
SM_GW_DP_LOCK = 3
SM_GW_DP_MODE_TASKS = 4
SM_GW_DP_VERSION = 5         # PCB + MCU version, text
SM_GW_DP_NAME = 6            # Device name, text
SM_GW_DP_OIL = 8
SM_GW_DP_LIGHT = 11
SM_GW_DP_BATTERY = 12
SM_GW_DP_PASSWORD = 13
SM_GW_DP_FIXED_GEAR = 14
SM_GW_DP_CUSTOMIZE_GEAR = 15
SM_GW_DP_REMARK = 20
SM_GW_DP_MULTI_NOZZLE = 23
SM_GW_DP_NOZZLE_CUSTOM = 24

# Marker byte that prefixes a 5-byte ASCII password inside DP 13.
SM_GW_PASSWORD_MARKER = 0xC0
SM_GW_PASSWORD_OK_BYTE = 0xA1

# Init/keep-alive packet sent after successful password verification.
SM_GW_INIT_PACKET = bytes([0x01, 0x01, 0x00])

# A few notification payloads the firmware sends as a heartbeat-style pulse
# rather than a data frame. The app discards them. We do too.
SM_GW_HEARTBEAT_HEX = "02030405060708090a0b0c0d0e"

# ---------------------------------------------------------------------------
# Scent Marketing — GW XOR-encryption lookup table
# ---------------------------------------------------------------------------
# 256-byte lookup table used by `HexConver.dataEncrypt`/`dataDecrypt` in the
# Scent Marketing Android app. The encryption is a stream cipher: index into
# this table starting at `(int(mac[8:10], 16) XOR nonce) & 0xFF`, then XOR each
# subsequent payload byte with `SM_GW_XOR_DICT[index++]`. The first byte of
# the on-wire packet is the random nonce that selects the starting offset.

SM_GW_XOR_DICT = bytes([
    226, 103,  87, 132,  63,  66,  59,  88, 176, 241, 188, 194, 123, 228, 209,  42,
     19, 100, 195, 219, 189, 176, 198,  24, 138, 237, 115, 187,  61, 152,  67, 146,
    176, 179, 140,  48, 182, 156,  17, 161, 183,  69, 137, 207,  17,  23,  47, 211,
     70, 177, 182, 141, 226,   4,  93, 106, 105,  24, 226,   2,  50,  89, 176, 161,
     51, 178, 182, 145, 201, 170, 180, 158, 158, 113, 175,  58,  94, 208, 239, 254,
     88, 147,  56,  27, 161, 254,  17,  48, 108, 109, 230,   7, 134, 147, 109, 130,
     12,  54,  36,   0,  61,   0,  41, 219, 129, 210, 119, 239,  42, 201,  35, 244,
     80, 133,  85,   7, 146,  55,  24, 124, 199, 165,  95,  11, 231, 161,  95, 149,
    192, 141,  35,   3, 129, 126,  45,  82,  50, 254, 114, 183, 222,   1, 163,  73,
    121,  75,   4, 181, 179, 196, 195, 200, 176, 113, 144,  44, 110, 181,  15,  76,
     19,  24, 231, 190, 104, 161, 131, 175,  47, 194, 186,  64, 156,  88,  37,  26,
     80,  53,  90, 165,  78, 228, 119, 240, 253, 144, 192,  67, 109,  14,  38, 145,
    139, 187, 101, 250, 179, 191,  68, 217,  46, 165, 120, 198,  52, 175, 106,  95,
      3,  99,  78,  16, 226, 248, 217, 149, 230, 131,   1, 203,  57,  11,  49, 216,
     92, 242, 131, 189,  53,  76,  93, 152,  33,  18, 138, 156, 246,   1, 227,  81,
    167,  20,  19, 209, 253, 243,  65, 104,  80,   2,   3, 148, 129, 167, 114, 187,
])

# ---------------------------------------------------------------------------
# Aroma-Link Cloud API constants
# ---------------------------------------------------------------------------

CLOUD_BASE_URL = "https://www.aroma-link.com"
CLOUD_WEB_URL = "https://www.aroma-link.com"

# TLS certificate verification for the cloud API is deliberately off.
# The vendor's own app (Aroma-Link 2.8.8) talks to this API over plain
# http:// and never uses the certificate, while the certificate itself
# is a 90-day one the vendor has no reason to keep renewing. Verifying
# it would buy little (the app already sends the same credentials in
# cleartext) at the cost of cloud mode breaking for every user whenever
# the certificate lapses. Traffic stays encrypted either way.
CLOUD_VERIFY_SSL = False

CLOUD_ENDPOINT_TOKEN = "/v2/app/token"
CLOUD_ENDPOINT_DEVICES = "/v1/app/device/listAll/{user_id}"
CLOUD_ENDPOINT_SWITCH = "/v1/app/data/newSwitch"
CLOUD_ENDPOINT_STATUS = "/v1/app/device/work/{device_id}"
CLOUD_ENDPOINT_SCHEDULE = "/v1/app/data/workSetApp"
# Schedule read-back. The app's GET_WEEK_WORK_TIME_URL — one call per
# weekday, returning that day's work-time slots.
CLOUD_ENDPOINT_WORK_TIME = "/v1/app/device/newWorkTime/{device_id}"

# How many cloud polls to skip between schedule read-backs. The schedule
# only changes when someone edits it, so re-reading it every poll would
# double our request rate against the vendor's API for nothing; once
# every 10 polls still picks up an app-side edit within minutes.
CLOUD_SCHEDULE_REFRESH_EVERY = 10

# Polling interval for cloud-mode devices. The integration previously had no
# periodic refresh, so HA never observed autonomous spray cycles between
# user-initiated commands. The Aroma-Link cloud exposes near-real-time state
# (onOff, workStatus, work/pauseRemainTime, pumpCount) via /v1/app/device/work/{id}.
CLOUD_POLL_INTERVAL_SECONDS = 60

# BLE devices push state while connected, but connect-on-demand means
# they're disconnected almost all the time — so refresh_state() ran
# exactly once, at setup, and query-only telemetry (Aroma-Link oil and
# remaining-time registers) went stale forever after (@gitorcus, #32).
# Protocols opt in via `BleProtocol.periodic_refresh`; this is the
# interval. Five minutes keeps the device's single BLE slot free for the
# official app ~97% of the time while still tracking oil consumption.
BLE_REFRESH_INTERVAL_SECONDS = 300

# ---------------------------------------------------------------------------
# Weekday bitmask (shared by both protocols)
# ---------------------------------------------------------------------------

WEEKDAY_MON = 0x01
WEEKDAY_TUE = 0x02
WEEKDAY_WED = 0x04
WEEKDAY_THU = 0x08
WEEKDAY_FRI = 0x10
WEEKDAY_SAT = 0x20
WEEKDAY_SUN = 0x40
WEEKDAY_ALL = 0x7F

# ---------------------------------------------------------------------------
# Config keys
# ---------------------------------------------------------------------------

CONF_DEVICE_TYPE = "device_type"
CONF_BLE_ADDRESS = "ble_address"
CONF_BLE_NAME = "ble_name"
CONF_CLOUD_USERNAME = "cloud_username"
CONF_CLOUD_PASSWORD = "cloud_password"
CONF_CLOUD_DEVICE_ID = "cloud_device_id"
CONF_CLOUD_USER_ID = "cloud_user_id"
CONF_CONNECTION_MODE = "connection_mode"
CONF_MOMENTARY_SECONDS = "momentary_seconds"
# Optional 4-char ASCII password for Scent Marketing GW devices (the
# firmware sometimes ships locked; setting this lets the device accept
# our control commands).
CONF_GW_PASSWORD = "gw_password"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_WORK_DURATION = 10    # seconds
DEFAULT_PAUSE_DURATION = 120  # seconds
DEFAULT_SCAN_TIMEOUT = 10.0   # BLE scan seconds
DEFAULT_CONNECT_TIMEOUT = 15  # BLE connect seconds
DEFAULT_RECONNECT_DELAY = 30  # seconds between reconnect attempts
