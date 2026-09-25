"""BLE protocol implementations for Tuya and Aroma-Link diffusers."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from dataclasses import dataclass, field, replace

from .const import (
    DeviceType,
    SERVICE_UUID, CHAR_WRITE_UUID, CHAR_NOTIFY_UUID,
    SCENTIMENT_SERVICE_UUID, SCENTIMENT_CHAR_WRITE_UUID, SCENTIMENT_CHAR_NOTIFY_UUID,
    SM_AK_SERVICE_UUID, SM_AK_CHAR_UUID,
    SM_AK_CMD_QUERY_INFO, SM_AK_CMD_PROBE, SM_AK_CMD_QUERY_DEVICE_TYPE,
    SM_AK_CMD_TIME_SYNC, SM_AK_CMD_DEVICE_NAME, SM_AK_CMD_DEVICE_LABEL,
    SM_AK_CMD_OIL_NAME, SM_AK_CMD_OIL_AMOUNT, SM_AK_CMD_CONTROL_STATE,
    SM_AK_CMD_SCHEDULE_V2, SM_AK_CMD_SCHEDULE_V3,
    SM_AK_CMD_READ_SCHEDULE_V2, SM_AK_CMD_READ_DEVICE_TYPE,
    SM_AK_CMD_READ_DEVICE_NAME, SM_AK_CMD_READ_DEVICE_LABEL,
    SM_AK_CMD_READ_FIRMWARE, SM_AK_CMD_READ_EQUIPMENT,
    SM_AK_CMD_V3_READ_NAME, SM_AK_CMD_V3_READ_SCHEDULES,
    SM_AK_CMD_V3_READ_SLOT, SM_AK_CMD_V3_READ_LABEL,
    SM_AK_CMD_V3_READ_OIL, SM_AK_CMD_V3_READ_OIL_INFO, SM_AK_CMD_V3_READ_FIRMWARE,
    SM_AK_CMD_V3_READ_CONTROL, SM_AK_CMD_V3_READ_MODEL,
    SM_AK_CMD_V3_READ_GRADE_TABLE, SM_AK_RESP_GRADE_TABLE,
    SM_AK_V3_PRIMARY_ENDPOINT,
    SM_AK_RESP_SCHEDULE_V3, SM_AK_RESP_DEVICE_NAME_V3,
    SM_AK_RESP_LABEL_V3, SM_AK_RESP_MODEL_V3,
    SM_AK_CTRL_BIT_ONOFF, SM_AK_CTRL_BIT_FAN, SM_AK_CTRL_BIT_DEMO,
    SM_AK_CTRL_BIT_RESERVED, SM_AK_CTRL_BIT_LAMP, SM_AK_CTRL_BIT_LOCK,
    SM_AK_LOGIN_PRIMARY, SM_AK_LOGIN_SECONDARY_V3,
    SM_AK_OPCODE_LOGIN_RESPONSE, SM_AK_V3_COMMIT,
    SM_AK_V3_FAN_ON, SM_AK_V3_FAN_OFF,
    SM_AK_V3_SLOT_WEEKEND, SM_AK_V3_SLOT_WEEKDAY,
    SM_AK_DAY_MASK_WEEKDAYS, SM_AK_DAY_MASK_WEEKEND, SM_AK_DAY_MASK_DAILY,
    SM_GW_SERVICE_UUID, SM_GW_NOTIFY_UUID, SM_GW_WRITE_UUID,
    SM_MFR_ID_AK, SM_MFR_ID_GW, SM_MFR_ID_GW_ALT,
    SM_GW_FLAG_WIFI, SM_GW_FLAG_CELLULAR,
    SM_AK_FLAG_HEARTBEAT, SM_AK_HEARTBEAT_BYTES,
    SM_GW_FRAME_HEADER, SM_GW_TYPE_BINARY, SM_GW_TYPE_TEXT, SM_GW_CHUNK_SIZE,
    SM_GW_TYPE_BOOL, SM_GW_TYPE_LOCK,
    SM_GW_LEN_NAME, SM_GW_LEN_REMARK,
    SM_GW_MODE_INTERVAL, SM_GW_MODE_NONE, SM_GW_MODE_QUICK,
    SM_GW_DP_POWER, SM_GW_DP_FAN, SM_GW_DP_LOCK, SM_GW_DP_MODE_TASKS,
    SM_GW_DP_VERSION, SM_GW_DP_NAME, SM_GW_DP_OIL, SM_GW_DP_LIGHT,
    SM_GW_DP_BATTERY, SM_GW_DP_PASSWORD, SM_GW_DP_FIXED_GEAR,
    SM_GW_DP_CUSTOMIZE_GEAR, SM_GW_DP_REMARK,
    SM_GW_PASSWORD_MARKER, SM_GW_PASSWORD_OK_BYTE,
    SM_GW_INIT_PACKET, SM_GW_HEARTBEAT_HEX, SM_GW_XOR_DICT,
    AROMELY_SERVICE_UUID, AROMELY_CHAR_WRITE_UUID, AROMELY_CHAR_NOTIFY_UUID,
    AROMELY_ADV_SERVICE_UUID,
    SCENT_TECH_SERVICE_UUID, SCENT_TECH_CHAR_UUID,
    SCENT_TECH_HEADER, SCENT_TECH_TRAILER,
    SCENT_TECH_CMD_TIME_SYNC, SCENT_TECH_CMD_CONTROL,
    SCENT_TECH_CMD_READ_TIMERS, SCENT_TECH_CMD_ENQUIRY,
    SCENT_TECH_CMD_WRITE_TIMER, SCENT_TECH_CMD_PW_QUERY,
    SCENT_TECH_CMD_PW_SUBMIT, SCENT_TECH_CMD_CAPABILITY,
    SCENT_TECH_RESP_STATE, SCENT_TECH_RESP_TIMERS,
    SCENT_TECH_RESP_WRITE_TIMER, SCENT_TECH_RESP_PW_QUERY,
    SCENT_TECH_RESP_PW_SUBMIT, SCENT_TECH_ACTION_POWER,
    SCENT_TECH_TIMER_RECORD_SIZE,
    AROMELY_FRAME_HEADER, AROMELY_DIR_WRITE, AROMELY_DIR_NOTIFY,
    AROMELY_TYPE_READ, AROMELY_TYPE_DATA,
    AROMELY_REG_TIME, AROMELY_REG_SCHED_WRITE, AROMELY_REG_FAN,
    AROMELY_REG_SESSION, AROMELY_REG_POWER, AROMELY_REG_NAME,
    AROMELY_REG_LABEL, AROMELY_REG_SCHED1,
    BLE_NAME_PATTERNS,
    TUYA_HEADER, TUYA_VERSION,
    TUYA_CMD_DP_WRITE, TUYA_CMD_DP_REPORT, TUYA_CMD_QUERY, TUYA_CMD_TIME_SYNC,
    TUYA_DP_TYPE_BOOL, TUYA_DP_TYPE_RAW,
    TUYA_DP_POWER, TUYA_DP_SCHEDULE,
    AL_HEADER, AL_TRAILER,
    AL_CMD_QUERY, AL_CMD_STATUS, AL_CMD_WRITE,
    AL_SUB_POWER, AL_SUB_FAN, AL_SUB_SCHEDULE, AL_SUB_TIME_SYNC,
    AL_SUB_QUERY_SCHEDULES, AL_SUB_OIL_LEVEL, AL_SUB_ALL_WORK_INFO,
    AL_SUB_WORK_INFO, AL_SUB_WORK_FREQUENCY, AL_RX_BUFFER_MAX,
    AL_FAN_ON_VALUE, AL_FAN_OFF_VALUE,
    AL_SLOT_DISABLED,
    AL_PHASE_IDLE, AL_PHASE_SPRAYING, AL_PHASE_PAUSED,
)

import json
import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bleak.backends.scanner import AdvertisementData

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared data structures
# ---------------------------------------------------------------------------

@dataclass
class DiffuserState:
    """Represents the current state of a diffuser."""

    power: bool | None = None
    fan: bool | None = None            # Aroma-Link only
    # Aroma-Link capability flag from the 0A frame. None = not reported
    # (yet); only an explicit False hides the fan switch.
    has_fan: bool | None = None
    phase: str = "unknown"             # "off", "idle", "spraying", "paused"
    work_seconds: int = 0
    pause_seconds: int = 0
    # Scentiment-only
    level: int | None = None           # spray intensity 1-3
    battery: int | None = None         # battery percent
    rgb_on: bool | None = None         # RGB LED on/off
    rgb_color: tuple[int, int, int] | None = None  # (r, g, b) 0-255
    start_hour: int = 0
    start_minute: int = 0
    end_hour: int = 23
    end_minute: int = 59
    # Aroma-Link "all work info" (52 0A) — live countdown of the current
    # spray / pause phase in seconds. While idle the firmware reports the
    # configured durations instead of a countdown.
    work_remaining: int | None = None
    pause_remaining: int | None = None
    # Aroma-Link 0A bytes 6-12: device clock.
    device_clock: datetime | None = None
    # Scent Marketing GW-only
    lock: bool | None = None           # child-lock state
    oil_remaining: int | None = None   # percent 0-100
    # AK V3 oil block (@Mins95's #18 decode)
    oil_current_ml: int | None = None
    oil_max_ml: int | None = None
    oil_consumption_mlh: float | None = None
    oil_days_remaining: int | None = None
    # AK V3 schedule mode: True = Custom (work/pause honoured), False =
    # Level (device grade table). None until first read.
    schedule_custom_mode: bool | None = None
    # AK V3 Level grade table: per-level (work_s, pause_s) the device uses in
    # Level mode, pushed as a 0x47 frame in response to C3 (@Mins95's #8
    # decode). Index = intensity - 1. None until read. Used to compute oil
    # days-remaining in Level mode (Custom mode has the live work/pause).
    grade_table: list | None = None
    light_on: bool | None = None       # auxiliary LED state
    device_name: str | None = None     # user-set device name (DP 6)
    password_required: bool | None = None  # GW / Scent Tech device demands password auth
    # Scent Tech: the device's timer records keyed by 1-based slot, as
    # last read from a 0x88 frame. None until the first read.
    timer_slots: dict[int, "ScentTechTimer"] | None = None
    firmware_version: str | None = None    # PCB+MCU version string
    # Scent Marketing AK family — spray intensity bundled into schedule
    # writes. Per @Mins95's captures the V2 firmware accepts 0-10 and the
    # V3 firmware accepts 0-20; we clamp on send and let the AK protocol
    # use this value as the LL field when (re)building schedule frames.
    intensity: int | None = None
    # Day-of-week mask for the active schedule slot. Bit layout matches
    # the AK schedule frame: bit 0 = Sun, bit 1 = Mon, ..., bit 6 = Sat.
    # Read back from the device on connect (V2: 8301-8305, V3: C5/4A);
    # used as the DD field when rebuilding schedule frames so an
    # intensity-only tweak doesn't lose the user's day pattern.
    weekday_mask: int | None = None
    # Slot index of the currently active schedule on the device (only
    # meaningful for V2 where multiple slots may exist; V3 effectively
    # has slots 04/05 keyed to weekend/weekday).
    schedule_slot: int | None = None
    # V3-only "scene label" the user assigned via the official app
    # (e.g. "Evasion"). Surfaced in the device info on read-back.
    device_label: str | None = None
    # V3-only model code (e.g. "A305M"). Useful for triage when
    # different hardware variants reveal protocol quirks.
    model_code: str | None = None
    # Whether the device's active program/schedule is currently set to
    # run. Distinct from `power` on V3 — a V3 diffuser can be powered
    # on with the program disabled, in which case it won't spray even
    # though Power+Fan look active. On V2 this duplicates `power`
    # because V2 firmware only has the one toggle.
    schedule_enabled: bool | None = None
    # Aroma-Link 52 15: 7 days (Mon..Sun) x 5 slot dicts, raw flag byte kept.
    week_slots: list | None = None
    # Aroma-Link 52/53 0A [21], 53 09 [11]: pump nibble.
    pump: int | None = None
    # Aroma-Link 0A [47..48]: deviceCode.
    device_code: int | None = None


@dataclass
class ScheduleSlot:
    """A single schedule time slot."""

    start_hour: int = 0
    start_minute: int = 0
    end_hour: int = 0
    end_minute: int = 0
    enabled: bool = False
    work_seconds: int = 10
    pause_seconds: int = 120
    pump: int = 1


@dataclass
class ScheduleSetup:
    """A complete schedule setup (ShinePick: per-index, Aroma-Link: per-day)."""

    index: int = 0
    weekday_mask: int = 0x7F
    enabled: bool = False
    start_hour: int = 0
    start_minute: int = 0
    end_hour: int = 0
    end_minute: int = 0
    work_seconds: int = 10
    pause_seconds: int = 120
    slots: list[ScheduleSlot] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BleProtocol(ABC):
    """Base class for diffuser BLE protocols."""

    device_type: DeviceType

    # Per-protocol BLE characteristic UUIDs (defaults to FFF0 family used by
    # Tuya BLE and Aroma-Link). Override for devices with different GATT layout.
    service_uuid: str = SERVICE_UUID
    write_char_uuid: str = CHAR_WRITE_UUID
    notify_char_uuid: str = CHAR_NOTIFY_UUID

    # Whether HA should connect and re-run refresh_state() on a timer.
    # Opt-in per family rather than global: a periodic connect is only
    # worth it when the protocol has telemetry that is *query-only*, and
    # it's actively harmful on firmwares that beep on every read query
    # (one AK V3 variant does, #8).
    periodic_refresh: bool = False

    @abstractmethod
    def build_power(self, on: bool) -> bytes:
        """Build power on/off command."""

    def build_time_sync(self, now: datetime | None = None) -> bytes | None:
        """Build time synchronisation command. Return None if not supported."""
        return None

    @abstractmethod
    def build_query(self) -> bytes:
        """Build status query command."""

    @abstractmethod
    def parse_notification(self, data: bytes) -> dict:
        """Parse a notification from the device. Returns dict of state updates."""

    def wire_chunks(self, frame: bytes) -> list[bytes]:
        """Split a command into on-wire chunks.

        Most protocols send a command as a single GATT write. The Scent
        Marketing GW family overrides this to apply 18-byte chunking with
        a per-chunk (nonce, seq) header.
        """
        return [frame]

    def supports_fan(self) -> bool:
        """Whether this device has a fan control."""
        return False


# ---------------------------------------------------------------------------
# Tuya BLE (ShinePick QT-I300)
# ---------------------------------------------------------------------------

class TuyaBleProtocol(BleProtocol):
    """Tuya BLE protocol for ShinePick-style diffusers."""

    device_type = DeviceType.TUYA_BLE

    @staticmethod
    def _checksum(data: bytes) -> int:
        return sum(data) & 0xFF

    @staticmethod
    def _build_packet(cmd: int, payload: bytes) -> bytes:
        pkt = bytes([
            0x55, 0xAA, TUYA_VERSION, cmd,
            (len(payload) >> 8) & 0xFF,
            len(payload) & 0xFF,
        ]) + payload
        return pkt + bytes([TuyaBleProtocol._checksum(pkt)])

    @staticmethod
    def _build_dp_bool(dp_id: int, value: bool) -> bytes:
        payload = bytes([dp_id, TUYA_DP_TYPE_BOOL, 0x00, 0x01, 0x01 if value else 0x00])
        return TuyaBleProtocol._build_packet(TUYA_CMD_DP_WRITE, payload)

    def build_power(self, on: bool) -> bytes:
        return self._build_dp_bool(TUYA_DP_POWER, on)

    def build_query(self) -> bytes:
        return self._build_packet(TUYA_CMD_QUERY, b"")

    def build_time_sync(self, now: datetime | None = None) -> bytes:
        if now is None:
            now = datetime.now()
        payload = bytes([
            0x01,                   # sub-type
            now.year % 100,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
            now.isoweekday(),       # 1=Mon .. 7=Sun
        ])
        return self._build_packet(TUYA_CMD_TIME_SYNC, payload)

    def build_schedule(self, setups: list[ScheduleSetup]) -> bytes:
        """Build DP18 schedule write with up to 5 setups (55 bytes raw)."""
        raw = bytearray()
        for i in range(5):
            if i < len(setups):
                s = setups[i]
                raw.append(s.index)
                raw.append(s.weekday_mask)
                raw.append(s.start_hour)
                raw.append(s.start_minute)
                raw.append(s.end_hour)
                raw.append(s.end_minute)
                raw.append(1 if s.enabled else 0)
                raw.append((s.work_seconds >> 8) & 0xFF)
                raw.append(s.work_seconds & 0xFF)
                raw.append((s.pause_seconds >> 8) & 0xFF)
                raw.append(s.pause_seconds & 0xFF)
            else:
                raw.extend([i, 0x7F, 0, 0, 0, 0, 0, 0, 15, 0, 15])

        dp_payload = bytes([TUYA_DP_SCHEDULE, TUYA_DP_TYPE_RAW, 0x00, len(raw)]) + bytes(raw)
        return self._build_packet(TUYA_CMD_DP_WRITE, dp_payload)

    def parse_notification(self, data: bytes) -> dict:
        """Parse Tuya DP report notifications."""
        result: dict = {}

        if len(data) < 7 or data[0:2] != TUYA_HEADER:
            return result

        cmd = data[3]
        payload = data[6:-1]  # skip header(2) + ver(1) + cmd(1) + len(2), trim checksum

        if cmd != TUYA_CMD_DP_REPORT or len(payload) < 4:
            return result

        dp_id = payload[0]
        dp_type = payload[1]
        value_len = (payload[2] << 8) | payload[3]
        value_data = payload[4:4 + value_len]

        if dp_id == TUYA_DP_POWER and dp_type == TUYA_DP_TYPE_BOOL and value_len == 1:
            result["power"] = value_data[0] == 1
            result["phase"] = "idle" if result["power"] else "off"

        elif dp_id == TUYA_DP_SCHEDULE and dp_type == TUYA_DP_TYPE_RAW and value_len == 55:
            setups = []
            for i in range(5):
                off = i * 11
                setups.append(ScheduleSetup(
                    index=value_data[off],
                    weekday_mask=value_data[off + 1],
                    start_hour=value_data[off + 2],
                    start_minute=value_data[off + 3],
                    end_hour=value_data[off + 4],
                    end_minute=value_data[off + 5],
                    enabled=value_data[off + 6] == 1,
                    work_seconds=(value_data[off + 7] << 8) | value_data[off + 8],
                    pause_seconds=(value_data[off + 9] << 8) | value_data[off + 10],
                ))
            result["schedules"] = setups

            # Use first enabled setup for current work/pause
            for s in setups:
                if s.enabled:
                    result["work_seconds"] = s.work_seconds
                    result["pause_seconds"] = s.pause_seconds
                    break

        return result


# ---------------------------------------------------------------------------
# Aroma-Link custom protocol
# ---------------------------------------------------------------------------

class AromaLinkBleProtocol(BleProtocol):
    """Custom BLE protocol for Aroma-Link diffusers."""

    device_type = DeviceType.AROMA_LINK
    # Oil level (52 1E) and the work-info block (52 0A) are answered only
    # on request — the device never pushes them — so without a periodic
    # refresh those sensors freeze at whatever the setup query returned.
    periodic_refresh = True

    def __init__(self) -> None:
        # Multi-notification reassembly. Frames longer than the link MTU
        # (20 bytes by default) arrive split; the header `A5 AA AC` marks
        # a new frame and the trailer `C5 CC CA` a candidate end, and the
        # XOR byte decides whether that candidate is the real end or just
        # those three bytes occurring inside the payload. The frame has
        # no length field, so the checksum is the only arbiter.
        self._rx_buffer = bytearray()
        # Set on a header; then a bare chunk on an empty buffer is a lost tail.
        self._framed = False

    @staticmethod
    def _phase_from_status(status: int, power: bool) -> str:
        """Map the device's workStatus byte (0 off/idle, 1 spraying, 2 paused)."""
        if not power:
            return "off"
        if status == AL_PHASE_SPRAYING:
            return "spraying"
        if status == AL_PHASE_PAUSED:
            return "paused"
        return "idle"

    @staticmethod
    def _xor_checksum(payload: bytes) -> int:
        result = 0
        for b in payload:
            result ^= b
        return result

    @staticmethod
    def _build_packet(payload: bytes) -> bytes:
        xor = AromaLinkBleProtocol._xor_checksum(payload)
        return AL_HEADER + bytes([xor]) + payload + AL_TRAILER

    @staticmethod
    def write_sub(frame: bytes) -> int | None:
        """Return the sub-command of a 57 write frame, else None."""
        if len(frame) >= 6 and frame[:3] == AL_HEADER and frame[4] == AL_CMD_WRITE:
            return frame[5]
        return None

    def build_power(self, on: bool) -> bytes:
        return self._build_packet(bytes([AL_CMD_WRITE, AL_SUB_POWER, 0x01 if on else 0x00]))

    def build_fan(self, on: bool) -> bytes:
        return self._build_packet(bytes([
            AL_CMD_WRITE, AL_SUB_FAN,
            AL_FAN_ON_VALUE if on else AL_FAN_OFF_VALUE,
        ]))

    def build_query(self) -> bytes:
        """Status query: the "all work info" register (52 0A).

        Previously this sent READ_WEEK_WORK_TIME (52 15), whose ~320-byte
        reply arrives as 16+ notifications that were never reassembled —
        so every refresh made the device stream a response we could not
        read. 52 0A is what the official app polls every 20 s: on/off,
        phase, remaining times, window and battery in one frame.
        """
        return self._build_packet(bytes([AL_CMD_QUERY, AL_SUB_ALL_WORK_INFO]))

    def build_work_frequency_query(self, weekday: int) -> bytes:
        """READ_WORK_FREQUENCY for one weekday (0 = Sun … 6 = Sat).

        Reply: `52 06 <weekday>` then five slots of
        `<work u16> <pause u16> <pump<<4 | enabled>`: the configured
        durations, as opposed to the countdowns in 53 09 / 52 0A.
        Mirrors the app's getWorkFrePack().
        """
        return self._build_packet(bytes([AL_CMD_QUERY, AL_SUB_WORK_FREQUENCY, weekday & 0xFF]))

    def build_oil_query(self) -> bytes:
        """Read the liquid/oil level register (`52 1E`).

        The device only reports the level on demand, so this is sent
        alongside the schedule query on every refresh. The reply is
        parsed below into `oil_remaining`.
        """
        return self._build_packet(bytes([AL_CMD_QUERY, AL_SUB_OIL_LEVEL]))

    def build_week_schedule_query(self) -> bytes:
        """Build READ_WEEK_WORK_TIME (`52 15`, app: getAllWorkTimePack)."""
        return self._build_packet(bytes([AL_CMD_QUERY, AL_SUB_QUERY_SCHEDULES]))

    def build_all_work_query(self) -> bytes:
        """Read the "all work info" register (`52 0A`).

        Port of the app's READ_ALL_WORK_INFO. The response carries the
        remaining work / pause seconds plus battery and capability
        flags — see the parser branch below.
        """
        return self._build_packet(bytes([AL_CMD_QUERY, AL_SUB_ALL_WORK_INFO]))

    def build_time_sync(self, now: datetime | None = None) -> bytes:
        if now is None:
            now = datetime.now()
        payload = bytes([
            AL_CMD_WRITE, AL_SUB_TIME_SYNC,
            (now.year >> 8) & 0xFF, now.year & 0xFF,
            now.month, now.day,
            now.hour, now.minute, now.second,
            now.isoweekday(),
        ])
        return self._build_packet(payload)

    def build_schedule(self, weekday_mask: int, slots: list[ScheduleSlot]) -> bytes:
        """Build schedule write for specific day(s).

        Args:
            weekday_mask: Bitmask of days to apply (bit0=Mon .. bit6=Sun).
            slots: Up to 5 time slots. Missing slots filled with disabled defaults.
        """
        data = bytearray([weekday_mask])
        pad_flags = (slots[0].pump << 4) if slots else AL_SLOT_DISABLED

        for i in range(5):
            if i < len(slots):
                s = slots[i]
                data.extend([
                    s.start_hour, s.start_minute,
                    s.end_hour, s.end_minute,
                    (s.pump << 4) | (1 if s.enabled else 0),
                    (s.work_seconds >> 8) & 0xFF, s.work_seconds & 0xFF,
                    (s.pause_seconds >> 8) & 0xFF, s.pause_seconds & 0xFF,
                ])
            else:
                data.extend([
                    0, 0, 0, 0,          # 00:00 - 00:00
                    pad_flags,
                    0x00, 0x0A,          # work = 10
                    0x00, 0x78,          # pause = 120
                ])

        return self._build_packet(bytes([AL_CMD_WRITE, AL_SUB_SCHEDULE]) + bytes(data))

    def supports_fan(self) -> bool:
        return True

    def _feed(self, data: bytes) -> bytes | None:
        """Accumulate one notification; return a complete, checksum-valid frame or None."""
        buf = self._rx_buffer
        if data[:3] == AL_HEADER:
            # A header always starts a new frame; whatever was buffered
            # is a stale partial (device reset mid-frame, missed packet).
            buf.clear()
            self._framed = True
        elif not buf:
            if self._framed:
                return None
            # Unframed data with nothing pending — some firmwares emit
            # bare payloads; hand it through unchanged as before.
            return bytes(data)
        buf += data
        if len(buf) > AL_RX_BUFFER_MAX:
            buf.clear()
            return None
        if len(buf) >= 8 and buf[-3:] == AL_TRAILER:
            frame = bytes(buf)
            if self._xor_checksum(frame[4:-3]) == frame[3]:
                buf.clear()
                return frame
            # Trailer bytes inside the payload — not the end yet.
        return None

    def reset_rx(self) -> None:
        """Drop a partial frame left from the previous link."""
        self._rx_buffer.clear()

    def parse_notification(self, data: bytes) -> dict:
        """Parse Aroma-Link notification packets."""
        result: dict = {}

        frame = self._feed(bytes(data))
        if frame is None:
            return result

        # Strip header/trailer if present
        if frame[:3] == AL_HEADER and frame[-3:] == AL_TRAILER:
            payload = frame[4:-3]  # skip header(3) + xor(1), trim trailer(3)
        else:
            payload = frame

        if len(payload) < 2:
            return result

        cmd = payload[0]
        sub = payload[1]

        # Before dispatch, or a 52 1E NACK reads as 78 % oil.
        if cmd in (AL_CMD_QUERY, AL_CMD_WRITE) and payload[2:] == b"NACK":
            result["nack"] = sub
            return result

        # "All work info" (0x0A) — arrives both as a reply to our 52 0A
        # query and as an unsolicited 53 0A push. Layout per the app's
        # handlerAllWorkStatus(); payload[2] is the app's offset i+6:
        #   [2..3] year  [4] month [5] day [6] hh [7] mm [8] ss [9] weekday
        #   [10] fan/lamp nibbles  [11] on/off  [12] work status
        #   [13..14] work remaining (s, u16)  [15..16] pause remaining (s)
        #   [17..18] start HH MM  [19..20] end HH MM  [21] air pump
        #   [22..27] MAC  [28..29] raw oil weight  [30] battery
        #   [31] has-battery flag  [32] has-fan flag  [33..] more flags
        #   [47..48] deviceCode (u16)
        # [10]: low nibble fan, read only when the [32] has-fan flag is set.
        # The on/off byte and work status are plain bytes the app reads
        # directly (handlerAllWorkStatus: setOnOff(i+15), setWorkStatus
        # (i+16)), so those are safe and are what keeps the phase honest
        # between pushes — see the 53 09 branch for why that matters.
        if sub == AL_SUB_ALL_WORK_INFO and cmd in (AL_CMD_STATUS, AL_CMD_QUERY):
            if len(payload) >= 9:
                try:
                    result["device_clock"] = datetime(
                        (payload[2] << 8) | payload[3], payload[4], payload[5],
                        payload[6], payload[7], payload[8],
                    )
                except ValueError:
                    # An impossible date counts as drift; a new object each read.
                    result["device_clock"] = datetime(1, 1, 1)
            if len(payload) >= 13:
                result["power"] = payload[11] == 0x01
                result["phase"] = self._phase_from_status(payload[12], result["power"])
            if len(payload) >= 17:
                result["work_remaining"] = (payload[13] << 8) | payload[14]
                result["pause_remaining"] = (payload[15] << 8) | payload[16]
            if len(payload) >= 21:
                result["start_hour"] = payload[17]
                result["start_minute"] = payload[18]
                result["end_hour"] = payload[19]
                result["end_minute"] = payload[20]
            if len(payload) >= 22:
                result["pump"] = payload[21]
            # Battery is only meaningful when the has-battery capability
            # flag is set (mains-only devices report 0 there).
            if len(payload) >= 32 and payload[31] == 1:
                result["battery"] = max(0, min(100, payload[30]))
            # The app hides its fan controls when this flag is 0
            # (DeviceControlActivity: hintFan(getHasFan() == 0)).
            if len(payload) >= 33:
                result["has_fan"] = payload[32] != 0
            if result.get("has_fan"):
                result["fan"] = (payload[10] & 0x0F) != 0
            if len(payload) >= 49:
                result["device_code"] = (payload[47] << 8) | payload[48]
            return result

        if cmd == AL_CMD_STATUS:
            if sub == AL_SUB_POWER and len(payload) >= 3:
                result["power"] = payload[2] == 0x01
                if not result["power"]:
                    result["phase"] = "off"

            elif sub == AL_SUB_FAN and len(payload) >= 3:
                result["fan"] = (payload[2] >> 4) != 0

            elif sub == AL_SUB_WORK_INFO and len(payload) >= 11:
                # Work-info push. Per the app's parseWorkInfo():
                #   [2] workStatus  [3..4] workRemainTime  [5..6] pauseRemainTime
                #   [7..8] start HH MM  [9..10] end HH MM  [11] airPump
                # Bytes 3..6 are the live *countdowns*, not the configured
                # durations — v1.0.0 through v1.2.2 stored them in
                # work_seconds/pause_seconds, which happened to look right
                # because the device pushes this frame right after a
                # schedule write, when remaining == configured. The
                # configured values come from 52 06 instead.
                result["phase"] = self._phase_from_status(payload[2], True)
                result["work_remaining"] = (payload[3] << 8) | payload[4]
                result["pause_remaining"] = (payload[5] << 8) | payload[6]
                result["start_hour"] = payload[7]
                result["start_minute"] = payload[8]
                result["end_hour"] = payload[9]
                result["end_minute"] = payload[10]
                if len(payload) >= 12:
                    result["pump"] = payload[11]

        elif cmd == AL_CMD_QUERY and sub == AL_SUB_WORK_FREQUENCY and len(payload) >= 8:
            # `52 06 <weekday>` + 5 × `<work u16> <pause u16> <pump<<4|enabled>`
            # (app: handlerWorkFre). Prefer the first enabled slot; fall
            # back to the first slot so a disabled schedule still shows
            # the durations the user last set — same policy as the cloud
            # path.
            slots = []
            for k in range(5):
                base = 3 + 5 * k
                if len(payload) < base + 5:
                    break
                work = (payload[base] << 8) | payload[base + 1]
                pause = (payload[base + 2] << 8) | payload[base + 3]
                enabled = bool(payload[base + 4] & 0x0F)
                slots.append((enabled, work, pause))
            chosen = next((sl for sl in slots if sl[0]), slots[0] if slots else None)
            if chosen is not None:
                enabled, work, pause = chosen
                result["schedule_enabled"] = enabled
                if work > 0:
                    result["work_seconds"] = work
                if pause > 0:
                    result["pause_seconds"] = pause

        elif cmd == AL_CMD_QUERY and sub == AL_SUB_QUERY_SCHEDULES and len(payload) >= 317:
            # `52 15` + 7 days (Mon..Sun) x 5 slots of
            # `<sH sM eH eM> <pump<<4|en> <work u16> <pause u16>`
            week = []
            for d in range(7):
                day = []
                for k in range(5):
                    base = 2 + 45 * d + 9 * k
                    flags = payload[base + 4]
                    day.append({
                        "start_hour": payload[base],
                        "start_minute": payload[base + 1],
                        "end_hour": payload[base + 2],
                        "end_minute": payload[base + 3],
                        "flags": flags,
                        "pump": flags >> 4,
                        "enabled": bool(flags & 0x0F),
                        "work_seconds": (payload[base + 5] << 8) | payload[base + 6],
                        "pause_seconds": (payload[base + 7] << 8) | payload[base + 8],
                    })
                week.append(day)
            result["week_slots"] = week

        elif cmd == AL_CMD_QUERY and sub == AL_SUB_OIL_LEVEL and len(payload) >= 3:
            # Read-register reply for the liquid level: `52 1E <percent>`.
            # @ndoty's capture showed 0x50 (80) matching the app's 80%, so
            # the byte is a straight 0–100 percentage.
            result["oil_remaining"] = max(0, min(100, payload[2]))

        elif cmd == AL_CMD_WRITE:
            # Any 57 reply other than NACK is an ACK, including empty data.
            result["ack"] = sub

        return result


# ---------------------------------------------------------------------------
# Aromely Aro Max — 55-framed register protocol (FFE0 service)
# ---------------------------------------------------------------------------

class AromelyAroMaxProtocol(BleProtocol):
    """Aromely Aro Max diffuser.

    Frame layout (decoded from @ahbhimani1's captures, #4):

        55 <dir> <reg> <type> [<len> <payload...>] <checksum>

    `dir` is 0x10 (app->device) or 0x11 (device->app). `type` is 0x00 for
    a bare read request (no length/payload) or 0x05 for a data write /
    response. `checksum` is the sum of every byte after the 0x55 header,
    mod 256.

    The schedule (register 0x01 write / 0xF1 read) carries a day mask,
    start/end time and the work/pause durations as big-endian u16 seconds.
    Power is register 0x11, fan is register 0x06 (the device has three fan
    modes; we expose a plain on/off switch).
    """

    device_type = DeviceType.AROMELY_ARO_MAX
    service_uuid = AROMELY_SERVICE_UUID
    write_char_uuid = AROMELY_CHAR_WRITE_UUID
    notify_char_uuid = AROMELY_CHAR_NOTIFY_UUID

    @staticmethod
    def _frame(reg: int, payload: bytes | None) -> bytes:
        """Wrap a register write/read in the 55 envelope with checksum."""
        body = bytearray([AROMELY_DIR_WRITE, reg])
        if payload is None:
            body.append(AROMELY_TYPE_READ)
        else:
            body.append(AROMELY_TYPE_DATA)
            body.append(len(payload) & 0xFF)
            body.extend(payload)
        body.append(sum(body) & 0xFF)
        return bytes([AROMELY_FRAME_HEADER]) + bytes(body)

    # -- handshake / reads ------------------------------------------------

    def build_session_start(self) -> bytes:
        """First frame after connect — the app sends this before anything."""
        return self._frame(AROMELY_REG_SESSION, bytes([0x01]))

    def build_time_sync(self, now: datetime | None = None) -> bytes:
        """Time sync: `00 05 04 20 HH MM SS` with HH/MM/SS in BCD."""
        if now is None:
            now = datetime.now()
        bcd = lambda v: (((v // 10) << 4) | (v % 10)) & 0xFF
        return self._frame(
            AROMELY_REG_TIME,
            bytes([0x20, bcd(now.hour), bcd(now.minute), bcd(now.second)]),
        )

    def build_read_queries(self) -> list[bytes]:
        """Read name, label and the slot-1 schedule (incl. enabled flag)."""
        return [
            self._frame(AROMELY_REG_NAME, None),
            self._frame(AROMELY_REG_LABEL, None),
            self._frame(AROMELY_REG_SCHED1, None),
        ]

    def build_query(self) -> bytes:
        return self._frame(AROMELY_REG_SCHED1, None)

    # -- controls ---------------------------------------------------------

    def build_power(self, on: bool) -> bytes:
        return self._frame(AROMELY_REG_POWER, bytes([0x01 if on else 0x00]))

    def build_fan(self, on: bool) -> bytes:
        # Three device modes (0=off, 1=low, 2=full). The HA switch maps to
        # low/off; "full" is only reachable from the official app for now.
        return self._frame(AROMELY_REG_FAN, bytes([0x01 if on else 0x00]))

    def supports_fan(self) -> bool:
        return True

    def build_schedule(
        self,
        slot: ScheduleSlot,
        weekday_mask: int = 0x7F,
        intensity: int = 0,
    ) -> bytes:
        """Schedule slot-1 write: day mask, window, work/pause (u16 s)."""
        work = max(0, min(0xFFFF, int(slot.work_seconds)))
        pause = max(0, min(0xFFFF, int(slot.pause_seconds)))
        payload = bytes([
            weekday_mask & 0x7F,
            slot.start_hour & 0xFF, slot.start_minute & 0xFF,
            slot.end_hour & 0xFF, slot.end_minute & 0xFF,
            (work >> 8) & 0xFF, work & 0xFF,
            (pause >> 8) & 0xFF, pause & 0xFF,
        ])
        return self._frame(AROMELY_REG_SCHED_WRITE, payload)

    # -- parsing ----------------------------------------------------------

    def parse_notification(self, data: bytes) -> dict:
        result: dict = {}
        if (
            len(data) < 6
            or data[0] != AROMELY_FRAME_HEADER
            or data[1] != AROMELY_DIR_NOTIFY
            or data[3] != AROMELY_TYPE_DATA
        ):
            return result
        reg = data[2]
        length = data[4]
        payload = data[5:5 + length]

        if reg == AROMELY_REG_NAME:
            name = payload.lstrip(b"\x00").decode("utf-8", "replace").strip()
            if name:
                result["device_name"] = name
        elif reg == AROMELY_REG_LABEL:
            label = payload.lstrip(b"\x00").decode("utf-8", "replace").strip()
            if label:
                result["device_label"] = label
        elif reg == AROMELY_REG_POWER and payload:
            result["power"] = payload[0] == 0x01
            result["phase"] = "idle" if result["power"] else "off"
        elif reg == AROMELY_REG_FAN and payload:
            result["fan"] = payload[0] != 0x00
        elif reg in (AROMELY_REG_SCHED1, AROMELY_REG_SCHED_WRITE):
            self._parse_schedule(reg, payload, result)
        return result

    @staticmethod
    def _parse_schedule(reg: int, payload: bytes, result: dict) -> None:
        # F1 read:        <enabled> <mask> <sH sM eH eM> <work_u16> <pause_u16> ...
        # 01 write echo:           <mask> <sH sM eH eM> <work_u16> <pause_u16>
        if reg == AROMELY_REG_SCHED1 and len(payload) >= 10:
            result["schedule_enabled"] = payload[0] == 0x01
            base = 1
        elif reg == AROMELY_REG_SCHED_WRITE and len(payload) >= 9:
            base = 0
        else:
            return
        result["weekday_mask"] = payload[base]
        result["start_hour"] = payload[base + 1]
        result["start_minute"] = payload[base + 2]
        result["end_hour"] = payload[base + 3]
        result["end_minute"] = payload[base + 4]
        work = (payload[base + 5] << 8) | payload[base + 6]
        pause = (payload[base + 7] << 8) | payload[base + 8]
        if 0 < work <= 0xFFFF:
            result["work_seconds"] = work
        if 0 < pause <= 0xFFFF:
            result["pause_seconds"] = pause


# ---------------------------------------------------------------------------
# Scentiment Diffuser Air 2 — JSON-over-BLE
# ---------------------------------------------------------------------------

class ScentimentProtocol(BleProtocol):
    """Scentiment Diffuser Air 2 — JSON commands on 0xDEAD, text status on 0xFEF3."""

    device_type = DeviceType.SCENTIMENT
    service_uuid = SCENTIMENT_SERVICE_UUID
    write_char_uuid = SCENTIMENT_CHAR_WRITE_UUID
    notify_char_uuid = SCENTIMENT_CHAR_NOTIFY_UUID

    # The device requires every command to be terminated with these two bytes.
    _TERMINATOR = b"-|"

    @classmethod
    def _encode(cls, action: str, payload: dict | None = None) -> bytes:
        obj: dict = {"action": action}
        if payload is not None:
            obj["payload"] = payload
        return json.dumps(obj, separators=(",", ":")).encode("utf-8") + cls._TERMINATOR

    def build_power(self, on: bool) -> bytes:
        return self._encode("TURN_ON", {"on": 1 if on else 0})

    def build_set_level(self, level: int) -> bytes:
        return self._encode("SET_LEVEL", {"intensity": int(level)})

    def build_set_rgb_color(self, r: int, g: int, b: int) -> bytes:
        return self._encode("SET_RGB_LEVEL", {"red": int(r), "green": int(g), "blue": int(b)})

    def build_set_rgb_led(self, on: bool) -> bytes:
        return self._encode("SET_RGB_LED", {"on": 1 if on else 0})

    def build_ping(self) -> bytes:
        return self._encode("PING")

    def build_query(self) -> bytes:
        # The device pushes state via notifications; no explicit query needed.
        return b""

    def parse_notification(self, data: bytes) -> dict:
        try:
            text = data.decode("utf-8", errors="replace").strip()
        except Exception:
            return {}

        # Strip the trailing "-|" terminator if the device echoes it back.
        if text.endswith("-|"):
            text = text[:-2].strip()

        result: dict = {}

        if text.startswith("{"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    result.update(parsed)
            except Exception:
                _LOGGER.debug("Scentiment: unparseable JSON notification: %r", text)
            return result

        # Comma-separated status: "S:1,L:3,C:1,Bat:100"
        if "," in text and ":" in text:
            for part in text.split(","):
                key, _, value = part.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if not value:
                    continue
                try:
                    n = int(value)
                except ValueError:
                    continue
                if key == "s":
                    result["power"] = n > 0
                    result["phase"] = "spraying" if n > 0 else "off"
                elif key == "l":
                    result["level"] = n
                elif key == "bat":
                    result["battery"] = n
            return result

        # Single key:value notification
        if ":" in text:
            key, _, value = text.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "level":
                try:
                    lvl = int(value)
                    result["level"] = lvl
                    if lvl > 0:
                        result["phase"] = "spraying"
                except ValueError:
                    pass
            elif key == "enable":
                try:
                    en = int(value)
                    result["power"] = en > 0
                    result["phase"] = "spraying" if en > 0 else "off"
                except ValueError:
                    pass
            else:
                result[key] = value
        else:
            _LOGGER.debug("Scentiment: unrecognised notification: %r", text)

        return result


# ---------------------------------------------------------------------------
# Scent Marketing app — three protocol families
# ---------------------------------------------------------------------------
#
# The Scent Marketing Android app supports three protocol families. They are
# distinguished primarily by manufacturer-specific data in BLE advertisement
# packets, not by device name (the app routes purely on manufacturer IDs):
#
#   * AK family   — 0x5943 (22851). Service FFF0 / Char FFF6. Simple bytes,
#                   optionally with a periodic E0AA55 heartbeat.
#   * GW family   — 0x460C (17932) or 0xF001 (61441). Service EE01 / Notify
#                   EE02 / Write EE03. A length-prefixed data-point frame
#                   wrapped in 18-byte chunks with a (nonce, seq) header per
#                   chunk. The first byte of manufacturer data determines
#                   whether the inner payload is plain binary or wrapped in
#                   an additional XOR stream-cipher layer (01/02/03 = WiFi
#                   variant with XOR encryption; B1/B2 = cellular with XOR;
#                   anything else = plain binary).
#
# All three implementations live in this module. Until @angeldero or another
# reporter validates against real hardware, treat them as beta-quality.


class ScentMarketingAkProtocol(BleProtocol):
    """Scent Marketing — AK family (FFF0 service).

    Commands and responses share `FFF6`. A response's first byte is the
    opcode of the corresponding read/write. Many of the writes here are
    portable copies of `BtDataModel.writeXxx()` and `TotalControlModel`
    from the decompiled app.
    """

    device_type = DeviceType.SCENT_MARKETING_AK
    service_uuid = SM_AK_SERVICE_UUID
    write_char_uuid = SM_AK_CHAR_UUID
    notify_char_uuid = SM_AK_CHAR_UUID

    def __init__(self) -> None:
        # We track the on-device control bitmask locally so a "set only the
        # power bit" command doesn't clobber lamp/fan/lock state. Updated
        # whenever the device pushes a 0x4D control-state notification.
        self._ctrl_bits = {
            SM_AK_CTRL_BIT_ONOFF: False,
            SM_AK_CTRL_BIT_FAN: False,
            SM_AK_CTRL_BIT_DEMO: False,
            SM_AK_CTRL_BIT_LAMP: False,
            SM_AK_CTRL_BIT_LOCK: False,
        }
        # Set by `parse_notification` from the device's reply to the primary
        # login frame. None = login not yet completed; True/False selects the
        # V3 vs V2 command set (different fan & schedule encodings).
        self._v3_mode: bool | None = None

    # ------------------------------------------------------------------
    # Login handshake (must complete before any other write)
    # ------------------------------------------------------------------

    @property
    def is_v3(self) -> bool:
        """True once the device has identified itself as a V3 model."""
        return self._v3_mode is True

    @property
    def login_completed(self) -> bool:
        """True once we've parsed any 0x8F login response."""
        return self._v3_mode is not None

    def reset_login_state(self) -> None:
        """Called by the device manager before each fresh BLE connect."""
        self._v3_mode = None

    def build_login_primary(self) -> bytes:
        """0x8F + ASCII '8888' — the default app PIN, required before any
        other write."""
        return SM_AK_LOGIN_PRIMARY

    def build_login_secondary_v3(self) -> bytes:
        """0x8F + ASCII '8888OK01' — the V3-only follow-up login. Only send
        this after the primary response identified the device as V3."""
        return SM_AK_LOGIN_SECONDARY_V3

    # ------------------------------------------------------------------
    # Control-state helpers
    # ------------------------------------------------------------------

    def _build_control(self, override_bit: int | None = None,
                       override_value: bool = False) -> bytes:
        """Build a 0x2D control-write with at most one bit overridden.

        Mirrors `TotalControlModel.writeTotalControl()`: 6-bit mask where
        bit 3 is reserved-always-1 and bits 5..0 are
        lock|lamp|1|demo|fan|onOff. The override is persisted into
        `_ctrl_bits` so consecutive HA toggles accumulate correctly —
        without this, toggling lamp while power is on would emit a mask
        with the power bit cleared, turning the diffuser off entirely.
        Real device pushes (0x4D notifications) overwrite the cache when
        they arrive.
        """
        if override_bit is not None:
            self._ctrl_bits[override_bit] = override_value
        mask = (1 << SM_AK_CTRL_BIT_RESERVED)
        for bit in (SM_AK_CTRL_BIT_ONOFF, SM_AK_CTRL_BIT_FAN,
                    SM_AK_CTRL_BIT_DEMO, SM_AK_CTRL_BIT_LAMP,
                    SM_AK_CTRL_BIT_LOCK):
            if self._ctrl_bits.get(bit):
                mask |= 1 << bit
        return bytes([SM_AK_CMD_CONTROL_STATE, mask & 0xFF])

    def build_power(self, on: bool) -> bytes:
        return self._build_control(SM_AK_CTRL_BIT_ONOFF, on)

    def build_lock(self, on: bool) -> bytes:
        return self._build_control(SM_AK_CTRL_BIT_LOCK, on)

    def build_lamp(self, on: bool) -> bytes:
        return self._build_control(SM_AK_CTRL_BIT_LAMP, on)

    def build_fan(self, on: bool) -> bytes:
        # V3 devices don't use the 0x2D control bitmask for fan — they
        # have a dedicated 0x2A short frame. V2 devices keep using the
        # bitmask path.
        if self.is_v3:
            # Optimistically cache the fan state so the next V3
            # schedule write (Program toggle, intensity change, …)
            # carries the correct fan byte at offset 3 instead of
            # defaulting back to 0x01 and switching the fan off.
            self._ctrl_bits[SM_AK_CTRL_BIT_FAN] = on
            return SM_AK_V3_FAN_ON if on else SM_AK_V3_FAN_OFF
        return self._build_control(SM_AK_CTRL_BIT_FAN, on)

    def supports_fan(self) -> bool:
        # The AK control bitmask carries a fan bit. We don't yet know
        # which models actually wire it up, but exposing the switch lets
        # users discover that empirically.
        return True

    # ------------------------------------------------------------------
    # Misc writes
    # ------------------------------------------------------------------

    def build_query(self) -> bytes:
        return bytes([SM_AK_CMD_QUERY_INFO])

    def build_heartbeat(self) -> bytes:
        return SM_AK_HEARTBEAT_BYTES

    def build_time_sync(self, now: datetime | None = None) -> bytes | None:
        """Time sync command for the detected protocol version.

        V2: `0x80 + YY MM DD HH MM SS WD` — port of `writeTime()` from
        the decompiled Android app. @Mins95 confirmed this works on V2
        devices.

        V3: `0x21 0x03 + YY MM DD HH MM SS` — decoded from @Mins95's
        salon_v3 capture (`21031A05140F351C` → 2026-05-20 15:53:28).
        Empirically required *before* the V3 read opcodes (C5/C6/C7/...)
        produce responses, otherwise the device silently ignores them.
        """
        if now is None:
            now = datetime.now()
        if self.is_v3:
            return bytes([
                0x21, 0x03,
                now.year % 100, now.month, now.day,
                now.hour, now.minute, now.second,
            ])
        return bytes([
            SM_AK_CMD_TIME_SYNC,
            now.year % 100, now.month, now.day,
            now.hour, now.minute, now.second,
            now.isoweekday() % 7,  # 0=Sun .. 6=Sat per the AK firmware
        ])

    def build_device_name(self, name: str) -> bytes:
        """Port of `BtDataModel.writeDeviceName()` — UTF-8, max 16 bytes."""
        payload = name.encode("utf-8")[:16]
        return bytes([SM_AK_CMD_DEVICE_NAME, len(payload)]) + payload

    def build_oil_amount(self, remaining: int, battery_pct: int) -> bytes:
        """Port of `BtDataModel.writeOilAmount()`."""
        return bytes([
            SM_AK_CMD_OIL_AMOUNT,
            (remaining >> 8) & 0xFF, remaining & 0xFF,
            battery_pct & 0xFF,
        ])

    def build_schedule(
        self,
        slot: ScheduleSlot,
        weekday_mask: int = SM_AK_DAY_MASK_DAILY,
        index: int = 1,
        intensity: int = 6,
        custom_mode: bool = True,
    ) -> bytes:
        """Build a schedule write in the dialect this device speaks.

        V2 vs V3 selection follows the login-response. V2 is the simple
        16-byte 0x03 frame; V3 is the longer 18-byte 0x2A frame with a
        slot tied to weekday/weekend in the official app.

        `custom_mode` (V3 only) selects the schedule mode: True = Custom
        (the device honours our Work/Pause Duration), False = Level (the
        device uses its own grade→work/pause table and ignores ours).
        """
        if self.is_v3:
            return self._build_schedule_v3(
                slot, weekday_mask, intensity,
                fan=self._ctrl_bits.get(SM_AK_CTRL_BIT_FAN, False),
                custom_mode=custom_mode,
            )
        return self._build_schedule_v2(slot, weekday_mask, index, intensity)

    @staticmethod
    def _build_schedule_v2(
        slot: ScheduleSlot,
        weekday_mask: int,
        index: int,
        intensity: int,
    ) -> bytes:
        """V2 schedule frame (16 bytes), matching @Mins95's captures:

            03 SS HH MM HH MM DD LL 00 00 00 00 00 00 00 00

        where SS = index low-nibble + 0x10 if enabled, DD = day mask,
        LL = intensity level. Trailing bytes are zero-padded.
        """
        status = (index & 0x0F)
        if slot.enabled:
            status |= 0x10
        out = bytearray(16)
        out[0] = SM_AK_CMD_SCHEDULE_V2
        out[1] = status & 0xFF
        out[2] = slot.start_hour & 0xFF
        out[3] = slot.start_minute & 0xFF
        out[4] = slot.end_hour & 0xFF
        out[5] = slot.end_minute & 0xFF
        out[6] = weekday_mask & 0xFF
        out[7] = max(0, min(10, int(intensity))) & 0xFF
        return bytes(out)

    @staticmethod
    def _build_schedule_v3(
        slot: ScheduleSlot,
        weekday_mask: int,
        intensity: int,
        fan: bool = False,
        custom_mode: bool = True,
    ) -> bytes:
        """V3 schedule frame (18 bytes), matching @Mins95's captures:

            2A 01 02 FF 00 SS EE HH MM HH MM DD 00 LL 00 0F 01 2C

        Where FF (offset 3) carries the fan state — 0x03 if the fan is
        currently on, 0x01 otherwise. The schedule frame shares its
        prefix with the V3 fan-only commands (`2A 01 02 01/03 00`), so
        firmware reads offset 3 as the fan bit whether or not the rest
        of a schedule follows. Embedding the current fan state here
        stops `set_schedule_enabled` / `set_intensity` from accidentally
        toggling the Fan switch off as a side effect.

        SS = slot ID (04=weekend, 05=weekday in the official app).
        EE on write = 03 enable / 01 disable — confirmed by @Mins95's
        2026-05-23 fresh capture of the official app toggling Program
        ON from a clean disabled state. Earlier captures showing the
        opposite were of a different action ("set as weekend pattern"
        vs. "make this slot live"), not the Program switch toggle.

        The trailing four bytes are the work and pause durations, each a
        big-endian u16: `<work_hi work_lo pause_hi pause_lo>`.
        @christiandion's app capture showed `00 05 00 F0` = (5 s work,
        240 s pause), matching an entry in his device's intensity table.
        The old hardcoded `00 0F 01 2C` was simply one such pair (15 s /
        300 s) — writing it verbatim meant the user's Work / Pause
        Duration never actually reached the device (#20, A323).
        """
        # Pick the slot that matches the typical weekend/weekday day-mask
        # pattern; default to the weekend slot for custom masks, since
        # we've only seen DD=0x41 used with it.
        if (weekday_mask & 0x7F) == SM_AK_DAY_MASK_WEEKDAYS:
            slot_id = SM_AK_V3_SLOT_WEEKDAY
        else:
            slot_id = SM_AK_V3_SLOT_WEEKEND
        state = 0x03 if slot.enabled else 0x01
        fan_byte = 0x03 if fan else 0x01
        # Offset 12 is the Custom/Level mode selector: 0x01 = Custom
        # (device honours the work/pause trailer), 0x00 = Level (device
        # uses its grade→work/pause table and ignores the trailer).
        # @Mins95's #8 differential nailed this: his Custom frame is
        # `…7F 01 01 0018 0128`, his Level-3 frame `…7F 00 03 0018 0128`.
        # We previously hardcoded 0x00 here, so the device stayed in
        # Level mode and silently ignored every Work/Pause Duration the
        # user set (#20). Default to Custom so those values take effect.
        mode_byte = 0x01 if custom_mode else 0x00
        head = bytes([
            SM_AK_CMD_SCHEDULE_V3, 0x01, 0x02, fan_byte,
            0x00, slot_id,
            state,
            slot.start_hour & 0xFF,
            slot.start_minute & 0xFF,
            slot.end_hour & 0xFF,
            slot.end_minute & 0xFF,
            weekday_mask & 0xFF,
            mode_byte,
            max(0, min(20, int(intensity))) & 0xFF,
        ])
        work = max(0, min(0xFFFF, int(slot.work_seconds)))
        pause = max(0, min(0xFFFF, int(slot.pause_seconds)))
        trailer = bytes([
            (work >> 8) & 0xFF, work & 0xFF,
            (pause >> 8) & 0xFF, pause & 0xFF,
        ])
        return head + trailer

    # Backwards-compatible alias kept for external callers that still
    # invoke `build_schedule_v2` directly. New code should use
    # `build_schedule` which dispatches to the correct version.
    def build_schedule_v2(self, slot: ScheduleSlot, index: int = 1) -> bytes:
        return self.build_schedule(slot, index=index)

    # ------------------------------------------------------------------
    # State read-back queries
    #
    # Mirrors what the official Scent Marketing app does on every BLE
    # connect (per @Mins95's V2 + V3 captures), so the integration can
    # populate HA entities from the device's actual stored state after
    # an HA restart instead of starting from a blank optimistic guess.
    # ------------------------------------------------------------------

    def build_read_schedule_queries(self) -> list[bytes]:
        """Frames to send after login to read the device's schedule state.

        V2: poll slots 1..5 individually with `83 SS`. V3: a single `C5`
        triggers the device to push one `4A...` per slot asynchronously.
        """
        if self.is_v3:
            return [bytes([SM_AK_CMD_V3_READ_SCHEDULES])]
        return [bytes([SM_AK_CMD_READ_SCHEDULE_V2, i]) for i in range(1, 6)]

    def build_read_state_queries(self) -> list[bytes]:
        """Frames to read non-schedule device state (power/fan, label,
        firmware, model). Different opcodes per protocol version.

        These are useful but non-essential — schedule state is the
        priority for state restoration on restart. The returned frames
        can be fired without blocking; responses are parsed
        asynchronously by `parse_notification`.
        """
        if self.is_v3:
            return [
                bytes([SM_AK_CMD_V3_READ_NAME]),
                bytes([SM_AK_CMD_V3_READ_LABEL]),
                bytes([SM_AK_CMD_V3_READ_FIRMWARE]),
                bytes([SM_AK_CMD_V3_READ_CONTROL]),
                bytes([SM_AK_CMD_V3_READ_MODEL]),
                # Oil block (@Mins95's #18 decode): C8 → 4B max/current ml,
                # CE → 50 enabled/consumption/days/current. Without these
                # the oil sensors stay null.
                bytes([SM_AK_CMD_V3_READ_OIL]),
                bytes([SM_AK_CMD_V3_READ_OIL_INFO]),
            ]
        return [
            bytes([SM_AK_CMD_READ_DEVICE_NAME]),
            bytes([SM_AK_CMD_READ_DEVICE_LABEL]),
            bytes([SM_AK_CMD_READ_FIRMWARE]),
            bytes([SM_AK_CMD_READ_EQUIPMENT]),
        ]

    def build_grade_table_query(self) -> bytes:
        """C3 query for the V3 Level grade table (→ 0x47).

        Sent on its own, *immediately after* the schedule read-back and
        *before* the label / oil / firmware reads — this mirrors the official
        app's order. @Mins95's #8 test showed the ordering is load-bearing:
        with C3 at the tail of the state queries (after the oil reads) his
        A305M sent C3 but never returned the 47 table; sent right after the
        schedule read (once the 4A/43 push has drained) it comes back and
        parses. Empty for V2 / pre-login (no grade table there).
        """
        if self.is_v3:
            return bytes([SM_AK_CMD_V3_READ_GRADE_TABLE])
        return b""

    # ------------------------------------------------------------------
    # On-wire framing
    # ------------------------------------------------------------------

    def wire_chunks(self, frame: bytes) -> list[bytes]:
        """Append the V3 'commit' frame after state-changing writes.

        On V3 devices, the official app follows certain writes with a
        separate `E0AA55` frame; without it, the device acknowledges the
        command at the GATT layer but doesn't actually apply it. Per the
        captures from @Mins95 the commit is sent after power-on, fan, and
        schedule writes — but not after power-off.
        """
        if not frame or not self.is_v3:
            return [frame]

        op = frame[0] & 0xFF
        needs_commit = False
        if op == SM_AK_CMD_CONTROL_STATE and len(frame) >= 2:
            # Only commit on writes that turn ONOFF *on*. Mirrors the
            # captured pattern: 2D1B (power-on) → commit, 2D1A (off) → no
            # commit.
            needs_commit = bool(frame[1] & (1 << SM_AK_CTRL_BIT_ONOFF))
        elif op == SM_AK_CMD_SCHEDULE_V3:
            needs_commit = True

        if needs_commit:
            return [frame, SM_AK_V3_COMMIT]
        return [frame]

    # ------------------------------------------------------------------
    # Notification parsing
    # ------------------------------------------------------------------

    def parse_notification(self, data: bytes) -> dict:
        """Parse an AK status notification by opcode (first byte).

        Mirrors `BtDataModel.readBluetoothData(byte[])` — the response
        opcode picks the layout.
        """
        if not data:
            return {}
        result: dict = {}
        op = data[0] & 0xFF

        if op == SM_AK_OPCODE_LOGIN_RESPONSE and len(data) >= 7:
            # Reply format: 0x8F + "OK_VX.0" where X is 2 or 3. Pin the
            # device's command-set version for subsequent build_* calls.
            payload = bytes(data[1:])
            if b"V3" in payload:
                self._v3_mode = True
            elif b"V2" in payload:
                self._v3_mode = False
            return result

        if op == 0x4D and len(data) >= 2:  # control state
            # V2: `4D <mask>` (2 bytes). V3: `4D 01 <mask>` (3 bytes,
            # the 0x01 being a leading count/mode byte per @Mins95's V3
            # init capture where the app issues `C4` and receives
            # `4D 01 FF`). On V3 the mask is unreliable for fan/lamp —
            # @Mins95 confirmed via differential capture that V3
            # `4D 01 FF` reports all bits set regardless of actual
            # state. So on V3 we only trust the ONOFF + LOCK bits and
            # leave fan/lamp to the authoritative 4A schedule frame.
            is_v3_frame = len(data) >= 3
            mask = data[2] & 0xFF if is_v3_frame else data[1] & 0xFF
            self._ctrl_bits[SM_AK_CTRL_BIT_ONOFF] = bool(mask & (1 << SM_AK_CTRL_BIT_ONOFF))
            self._ctrl_bits[SM_AK_CTRL_BIT_LOCK] = bool(mask & (1 << SM_AK_CTRL_BIT_LOCK))
            result["power"] = self._ctrl_bits[SM_AK_CTRL_BIT_ONOFF]
            result["phase"] = "idle" if result["power"] else "off"
            result["lock"] = self._ctrl_bits[SM_AK_CTRL_BIT_LOCK]
            if not is_v3_frame:
                # Legacy V2 path — bitmask is the source of truth.
                self._ctrl_bits[SM_AK_CTRL_BIT_FAN] = bool(mask & (1 << SM_AK_CTRL_BIT_FAN))
                self._ctrl_bits[SM_AK_CTRL_BIT_DEMO] = bool(mask & (1 << SM_AK_CTRL_BIT_DEMO))
                self._ctrl_bits[SM_AK_CTRL_BIT_LAMP] = bool(mask & (1 << SM_AK_CTRL_BIT_LAMP))
                result["fan"] = self._ctrl_bits[SM_AK_CTRL_BIT_FAN]
                result["light_on"] = self._ctrl_bits[SM_AK_CTRL_BIT_LAMP]

        elif op == 0x4B and len(data) >= 6:  # oil block (C8 response)
            # @Mins95's #18 decode: `4B 00 <max_ml u16> <current_ml u16>`.
            # e.g. 4B0003520258 = 850 ml capacity, 600 ml current → 71%.
            max_ml = (data[2] << 8) | data[3]
            current_ml = (data[4] << 8) | data[5]
            if max_ml > 0:
                result["oil_max_ml"] = max_ml
                result["oil_current_ml"] = current_ml
                result["oil_remaining"] = max(0, min(100, round(100 * current_ml / max_ml)))

        elif op == 0x50 and len(data) >= 8:  # oil info (CE response)
            # @Mins95's #18 decode:
            #   `50 <enabled> <consumption×100 u16> <field u16> <field u16>`
            # Only the consumption rate is trustworthy here. The 4B frame is
            # the single source of truth for current_ml / max_ml / percentage
            # — @Mins95's beta.7 test (#8) showed 4B reporting 584 ml (and the
            # % matched it) while this 50 frame's last u16 read 595 ml, so we
            # no longer let it overwrite oil_current_ml. The other u16 ("days")
            # is also unreliable: the official app *computes* days-remaining
            # from oil / consumption / schedule / duty-cycle rather than
            # reading a raw field (his device reported 836 vs the app's 293),
            # so we derive it ourselves in device.py and ignore this value.
            consumption = (data[2] << 8) | data[3]
            if consumption > 0:
                result["oil_consumption_mlh"] = consumption / 100.0

        elif op == SM_AK_RESP_GRADE_TABLE and len(data) >= 5:
            # V3 Level grade table, response to C3 (@Mins95's #8 decode):
            #     47 + N×(work_u16-be pause_u16-be)
            # His SA_Salon (20 levels) returns 20 pairs:
            #     47 000F012C 000F0096 00140078 … = L1 15/300, L2 15/150,
            #     L3 20/120, … matching his app's Level 1-20.
            # N = (len - 1) / 4. The selected intensity indexes this table to
            # recover the real work/pause the device uses in Level mode, which
            # lets us compute oil days-remaining there (Custom mode has the
            # live durations directly).
            table = []
            n = (len(data) - 1) // 4
            for i in range(n):
                base = 1 + i * 4
                work = (data[base] << 8) | data[base + 1]
                pause = (data[base + 2] << 8) | data[base + 3]
                # Skip implausible entries (uninitialised / junk) but keep the
                # index aligned to the level by storing None placeholders.
                if 0 < work <= 0xFFFF and 0 < pause <= 0xFFFF:
                    table.append((work, pause))
                else:
                    table.append(None)
            if any(t is not None for t in table):
                result["grade_table"] = table

        elif op == 0x83 and len(data) >= 8:
            # V2 schedule slot read-back. Layout matches the write but
            # echoes whatever the device has stored:
            #     83 SS HH MM HH MM DD LL [extra...]
            # SS lower nibble = slot index, 0x10 bit = enabled. Trailing
            # bytes carry work/pause and other slot config that we
            # currently don't surface as separate state.
            if data[1] & 0x10:
                # Only absorb the slot the device says is enabled.
                self._absorb_schedule(
                    slot_index=data[1] & 0x0F,
                    start_hour=data[2], start_minute=data[3],
                    end_hour=data[4], end_minute=data[5],
                    weekday_mask=data[6],
                    intensity=data[7],
                    result=result,
                )
                # V2 firmware has no dedicated power-state read opcode —
                # the only readable signal that the device is "on" is
                # the enabled-slot bit. Mirror it into `state.power` so
                # the HA Power switch reflects reality after restart.
                # On V2 the schedule-enabled flag and power are the same
                # concept, so populate both.
                result["power"] = True
                result["phase"] = "idle"
                result["schedule_enabled"] = True

        elif op == SM_AK_RESP_SCHEDULE_V3 and len(data) >= 14:
            # Offset 1 is the diffuser endpoint. Single-pump units report
            # a constant 01 here, but multi-pump models like the A309
            # carry three independently programmable diffusers and push
            # one set of slots per endpoint — 15 frames in total
            # (@danieledwardgeorgehitchcock, #22). Absorbing all of them
            # into one state means endpoint 2 and 3 overwrite endpoint
            # 1's schedule, so HA displays the last endpoint that
            # happened to push while our writes (2A 01 …) still go to the
            # first one: the user edits something other than what they
            # see.
            #
            # Until each endpoint gets its own entities, keep to the one
            # we actually write to. Skip only endpoints above the first,
            # so a variant that numbers its sole pump 00 still works.
            if data[1] > SM_AK_V3_PRIMARY_ENDPOINT:
                return result

            # V3 schedule slot read-back, response to C5 / CA01XX:
            #     4A 01 02 FF FE SS EE HH MM HH MM DD 00 LL 00 0F 01 2C
            # where FF (offset 3) carries the fan state and FE (offset
            # 4) tracks whether the schedule is currently enabled.
            # @Mins95's enabled/disabled comparison captures pinned this
            # down: on-the-wire fan and program state are mirrored in
            # the read-back, with read/write semantics for EE inverted
            # (write: 01=enable / 03=disable; read: 03=enabled / 01=
            # disabled).
            # Trailer (offsets 14..17) = work / pause durations, each a
            # big-endian u16 — the same encoding we now write. Surface
            # them so the Work / Pause Duration entities reflect what the
            # device actually has stored instead of snapping back to a
            # stale optimistic value (#20).
            work_seconds = pause_seconds = None
            if len(data) >= 18:
                work_seconds = (data[14] << 8) | data[15]
                pause_seconds = (data[16] << 8) | data[17]
            self._absorb_schedule(
                slot_index=data[5],
                start_hour=data[7], start_minute=data[8],
                end_hour=data[9], end_minute=data[10],
                weekday_mask=data[11],
                intensity=data[13],
                result=result,
                work_seconds=work_seconds,
                pause_seconds=pause_seconds,
            )
            # The V3 device pushes one 4A per slot, and most are empty
            # placeholders (all-zero times + mask). Only derive live
            # device state (fan, program-enabled, mode) from a slot that
            # actually carries a schedule — otherwise a trailing empty
            # slot clobbers the real one's values back to off/false
            # (@Mins95 saw schedule_enabled flip to false this way, #8).
            slot_empty = (
                data[7] == 0 and data[8] == 0 and data[9] == 0
                and data[10] == 0 and data[11] == 0
            )
            if not slot_empty:
                # V3 fan state is authoritative on read-back; it overrides
                # the 4D bitmask (V3's 4D 01 FF reports all bits set).
                if data[3] in (0x01, 0x03):
                    fan_now = data[3] == 0x03
                    result["fan"] = fan_now
                    self._ctrl_bits[SM_AK_CTRL_BIT_FAN] = fan_now
                # Offset 12 = Custom/Level mode selector (01 = Custom,
                # 00 = Level), NOT the enabled flag — @Mins95's #8
                # Level↔Custom differential confirmed this. Surface it so
                # HA can keep writing in Custom mode (and so the schedule
                # switch stops mistaking Level mode for "disabled").
                result["schedule_custom_mode"] = data[12] == 0x01
                # Program-enabled: data[4] = "active slot" indicator (slot
                # ID when genuinely live, 0 otherwise); data[6] = EE, whose
                # bit1 (0x02) carries enabled. @Mins95's V3 uses 0x03/0x01,
                # christiandion's Flair 0x07/0x05 — same bit1 meaning.
                active_slot = data[4] != 0
                ee_enabled = bool(data[6] & 0x02)
                result["schedule_enabled"] = active_slot and ee_enabled

        elif op == SM_AK_RESP_DEVICE_NAME_V3 and len(data) >= 2:
            # V3 reply to C6: `42 <utf8 name bytes…>`. Name is variable
            # length, no explicit length prefix.
            try:
                name = bytes(data[1:]).decode("utf-8", errors="replace").rstrip("\x00").strip()
                if name:
                    result["device_name"] = name
            except Exception:
                pass

        elif op == 0x81 and len(data) >= 3:
            # V2 reply to 0x81: `81 <len> <utf8 name>`.
            length = data[1] & 0xFF
            name_bytes = bytes(data[2:2 + length])
            try:
                name = name_bytes.decode("utf-8", errors="replace").rstrip("\x00").strip()
                if name:
                    result["device_name"] = name
            except Exception:
                pass

        elif op == SM_AK_RESP_LABEL_V3 and len(data) >= 2:
            # V3 reply to C7: `48 <16 utf8 bytes, zero-padded>`. We
            # surface it as the device_name fallback when no name has
            # come through yet — it's the user-visible "scene name" in
            # the official app (e.g. "Evasion").
            label = bytes(data[1:]).rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            if label:
                result.setdefault("device_label", label)

        elif op == SM_AK_RESP_MODEL_V3 and len(data) >= 2:
            # V3 reply to D0: `45 <utf8 model code>` (e.g. "A305M").
            model = bytes(data[1:]).rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            if model:
                result["model_code"] = model

        elif op == 0x44 and len(data) >= 17:  # PCB + Equipment firmware version
            pcb = data[1:17].rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            eqv = data[17:].rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            result["firmware_version"] = f"{pcb} / {eqv}".strip(" /")

        elif op == 0x82 and len(data) >= 2:  # PCB version (BLE v2)
            result["firmware_version"] = data[1:].rstrip(b"\x00").decode("utf-8", errors="replace").strip()

        elif op == 0x86 and len(data) >= 2:
            # V2 reply to 0x86: `86 <utf8 firmware version>` (e.g. "V2.00").
            version = bytes(data[1:]).rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            if version:
                result["firmware_version"] = version

        elif op == 0x88 and len(data) >= 2:  # equipment version
            version = data[1:].rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            if version:
                # Don't clobber a richer V2/V3 firmware string if one was
                # already parsed from 0x44 / 0x86; only fill in if empty.
                result.setdefault("firmware_version", version)

        return result

    def _absorb_schedule(
        self,
        slot_index: int,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        weekday_mask: int,
        intensity: int,
        result: dict,
        work_seconds: int | None = None,
        pause_seconds: int | None = None,
    ) -> None:
        """Populate `result` with schedule fields from a parsed slot.

        Skip slots that are clearly empty (all-zero times AND zero day
        mask) — the V3 reads in particular return five slots per query
        and most will be uninitialised junk with a wide range of values
        in the intensity / trailer bytes. We only want to surface the
        first slot that actually carries a real schedule.
        """
        if start_hour == 0 and start_minute == 0 and end_hour == 0 and end_minute == 0 and weekday_mask == 0:
            return
        result["start_hour"] = start_hour & 0xFF
        result["start_minute"] = start_minute & 0xFF
        result["end_hour"] = end_hour & 0xFF
        result["end_minute"] = end_minute & 0xFF
        result["weekday_mask"] = weekday_mask & 0xFF
        result["schedule_slot"] = slot_index & 0xFF
        # Intensity clamp to firmware ceiling (defensive — should already
        # be in range from the device, but read-back bytes can have junk
        # values in uninitialised slots).
        ceiling = 20 if self.is_v3 else 10
        result["intensity"] = max(0, min(ceiling, intensity & 0xFF))
        # Work / pause durations (V3 trailer). Only surface plausible
        # values — uninitialised read-back slots can carry junk here.
        if work_seconds is not None and 0 < work_seconds <= 0xFFFF:
            result["work_seconds"] = work_seconds
        if pause_seconds is not None and 0 < pause_seconds <= 0xFFFF:
            result["pause_seconds"] = pause_seconds


class ScentMarketingGwProtocol(BleProtocol):
    """Scent Marketing — GW family (EE01 service), plain binary DP protocol.

    Frame layout (inside the multi-packet envelope):

        FF <dp_count>
            <dp_id:u16-be> <type=AF> <len:u16-be> <payload...>     # composite
            <dp_id:u16-be> <inline-value>                          # inline DPs
            ...

    Composite DPs use type tag 0xAF (binary) or 0xBF (UTF-8 text). Inline
    DPs (POWER, LOCK) have no length prefix; their payload size is implied
    by the DP-ID.

    On the wire every frame is chopped into 18-byte chunks, each prefixed
    with a (nonce_byte, sequence_byte) header. If the last chunk happens to
    be exactly 18 bytes, an extra terminator chunk of just (nonce, count+1)
    must be appended.
    """

    device_type = DeviceType.SCENT_MARKETING_GW
    service_uuid = SM_GW_SERVICE_UUID
    write_char_uuid = SM_GW_WRITE_UUID
    notify_char_uuid = SM_GW_NOTIFY_UUID

    # The chunk reassembly state lives on the protocol instance because
    # GW notifications arrive as a stream of small fragments — we cannot
    # parse a single chunk in isolation.
    def __init__(self, tuya_dp_mode: bool = False) -> None:
        self._notify_buffer: list[bytes] = []
        # Some GW firmwares (those advertising PID 98) push their state
        # in Tuya-DP hex-string format instead of the regular binary
        # frame. The wire layout is the same; only the type-tag-driven
        # length decoding differs.
        self._tuya_dp_mode = tuya_dp_mode

    # ------------------------------------------------------------------
    # Frame builders
    # ------------------------------------------------------------------

    @staticmethod
    def _u16_be(v: int) -> bytes:
        return bytes([(v >> 8) & 0xFF, v & 0xFF])

    @classmethod
    def _build_bool_dp(cls, dp_id: int, on: bool, type_tag: int = SM_GW_TYPE_BOOL) -> bytes:
        """Build an inline boolean DP `[DP-ID:2][type][value:1]`.

        `type_tag` defaults to 0x01 (generic bool) — pass 0x11 for the
        lock DP, since the firmware encodes it as `CMD_GEN_CIPHER`.
        """
        return cls._u16_be(dp_id) + bytes([type_tag, 0x01 if on else 0x00])

    @staticmethod
    def _padded_utf8(s: str, length: int) -> bytes:
        """Pad a string to exactly `length` bytes (zero-fill, truncate)."""
        data = s.encode("utf-8")[:length]
        return data + b"\x00" * (length - len(data))

    @classmethod
    def _build_composite_dp(
        cls, dp_id: int, payload: bytes, type_tag: int = SM_GW_TYPE_BINARY
    ) -> bytes:
        return cls._u16_be(dp_id) + bytes([type_tag]) + cls._u16_be(len(payload)) + payload

    @classmethod
    def _build_frame(cls, dp_packets: list[bytes]) -> bytes:
        """Wrap a list of encoded DPs into an FF-prefixed frame."""
        return bytes([SM_GW_FRAME_HEADER, len(dp_packets)]) + b"".join(dp_packets)

    @staticmethod
    def chunk_for_transport(frame: bytes, nonce: int | None = None) -> list[bytes]:
        """Split a frame into (nonce, seq)-prefixed 18-byte transport chunks."""
        if nonce is None:
            nonce = random.randint(0, 0xFF)
        chunks: list[bytes] = []
        for i in range(0, len(frame), SM_GW_CHUNK_SIZE):
            seq = (i // SM_GW_CHUNK_SIZE) + 1
            chunks.append(bytes([nonce, seq]) + frame[i:i + SM_GW_CHUNK_SIZE])
        # The app appends a terminator chunk if the last payload chunk is
        # exactly the full chunk size (the receiver uses sub-chunk-size
        # packets as an end-of-frame marker).
        if chunks and len(chunks[-1]) - 2 == SM_GW_CHUNK_SIZE:
            chunks.append(bytes([nonce, len(chunks) + 1]))
        return chunks

    def wire_chunks(self, frame: bytes) -> list[bytes]:
        return self.chunk_for_transport(frame)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def build_power(self, on: bool) -> bytes:
        return self._build_frame([self._build_bool_dp(SM_GW_DP_POWER, on)])

    def build_lock(self, on: bool) -> bytes:
        return self._build_frame([
            self._build_bool_dp(SM_GW_DP_LOCK, on, type_tag=SM_GW_TYPE_LOCK),
        ])

    def build_light(self, on: bool, light_type: int = 1) -> bytes:
        """Build a DP-11 light command. `light_type` mirrors the firmware
        Light.getType() field (1 = simple on/off, 3/7 = colour wheel)."""
        if light_type == 1:
            payload = bytes([1, 0x01 if on else 0x00])
        else:
            payload = bytes([light_type & 0xFF, 0x01 if on else 0x00])
        return self._build_frame([self._build_composite_dp(SM_GW_DP_LIGHT, payload)])

    def build_device_name(self, name: str) -> bytes:
        """Build a DP-6 device-name write. Always 19 bytes zero-padded."""
        return self._build_frame([
            self._build_composite_dp(
                SM_GW_DP_NAME, self._padded_utf8(name, SM_GW_LEN_NAME),
                type_tag=SM_GW_TYPE_TEXT,
            ),
        ])

    def build_remark(self, remark: str) -> bytes:
        """Build a DP-20 remark write. Always 16 bytes zero-padded."""
        return self._build_frame([
            self._build_composite_dp(
                SM_GW_DP_REMARK, self._padded_utf8(remark, SM_GW_LEN_REMARK),
            ),
        ])

    def build_wind_sensing(self, on: bool, pairing_status: int = 0,
                           wind_status: int = 0, battery_pct: int = 0) -> bytes:
        """Build a DP-28 wind-sensing command. Port of `writeWind()`."""
        payload = bytes([
            1,                              # presence flag
            0x01 if on else 0x00,
            pairing_status & 0xFF,
            wind_status & 0xFF,
            battery_pct & 0xFF,
        ])
        return self._build_frame([self._build_composite_dp(0x1C, payload)])

    def build_time_sync(self, now: datetime | None = None) -> bytes:
        """Build a DP-10 clock-sync command (7 bytes BCD-ish)."""
        if now is None:
            now = datetime.now()
        weekday = (now.isoweekday() % 7)  # 0=Sun .. 6=Sat
        payload = bytes([
            now.year % 100, now.month, now.day,
            weekday,
            now.hour, now.minute, now.second,
        ])
        return self._build_frame([self._build_composite_dp(0x0A, payload)])

    def build_query(self) -> bytes:
        # The GW firmware autonomously pushes its full DP state after the
        # init handshake, so an explicit query is rarely needed. We send
        # the init packet here, which causes the device to re-emit state.
        return SM_GW_INIT_PACKET

    def build_init_ack(self) -> bytes:
        """Build the init/ACK packet that follows successful password auth."""
        return SM_GW_INIT_PACKET

    def build_password(self, password: str) -> bytes:
        """Build a DP-13 password-check write.

        Mirrors `BluetoothDataParser.encodePassword()`: a composite DP-13
        with a fixed declared length of 5 bytes, leading with the
        `0xC0` marker byte followed by a 4-byte ASCII password.
        """
        pwd_bytes = password.encode("ascii")[:4].ljust(4, b"\x00")
        payload = bytes([SM_GW_PASSWORD_MARKER]) + pwd_bytes
        # We hand-build the DP because the declared length (5) is fixed
        # by the protocol, not derived from the actual payload length.
        dp = self._u16_be(SM_GW_DP_PASSWORD) + bytes([
            SM_GW_TYPE_BINARY, 0x00, 0x05,
        ]) + payload
        return self._build_frame([dp])

    # ------------------------------------------------------------------
    # Schedule (DP 4) — interval tasks + customize-gear
    # ------------------------------------------------------------------

    def build_schedule(self, slots: list["ScheduleSlot"],
                       weekday_mask: int = 0x7F) -> bytes:
        """Port of `GwBleCtrl.handleTasksAndPower()` for single-nozzle
        devices (modelNode != 23).

        Layout (everything in big-endian):

            FF NN                            frame header + DP count
            00 04 AF <len:u16>               DP 4 header
            01 00                            sub-version / reserved
            00 <task_count>                  task count u16
            for each task: 7 bytes
                [enabled<<7 | weekday_mask:7] [start_h] [start_m]
                [end_h] [end_m] [spray=1] [power=intensity]
            01 64 00 <countdown_power>       reserved + countdown sentinel
            02 01 2C <quick_power>           quick-fragrance sentinel
            (optionally) 00 0F AF 00 0C ...  customize-gear block

        We do not currently emit the customize-gear block (we leave it
        for users who explicitly opt-in to per-slot custom timings via
        the integration's existing schedule service).
        """
        task_count = len(slots)
        payload = bytearray()
        payload += bytes([0x01, 0x00])                # sub-version
        payload += bytes([0x00, task_count & 0xFF])   # task count u16
        for slot in slots:
            day_bits = weekday_mask & 0x7F
            flags = day_bits | (0x80 if slot.enabled else 0x00)
            power = slot.work_seconds if slot.work_seconds <= 0xFF else 0xFF
            payload += bytes([
                flags & 0xFF,
                slot.start_hour & 0xFF, slot.start_minute & 0xFF,
                slot.end_hour & 0xFF, slot.end_minute & 0xFF,
                0x01,                       # spray = 1
                power & 0xFF,
            ])
        payload += bytes([0x01, 0x64, 0x00, 0x00])    # countdown sentinel
        payload += bytes([0x02, 0x01, 0x2C, 0x00])    # quick-fragrance sentinel
        dp4 = self._build_composite_dp(SM_GW_DP_MODE_TASKS, bytes(payload))
        return self._build_frame([dp4])

    # ------------------------------------------------------------------
    # Notification parsing
    # ------------------------------------------------------------------

    def handle_transport_chunk(self, chunk: bytes) -> bytes | None:
        """Feed an on-wire 20-byte chunk into the reassembly buffer.

        Returns the reassembled inner frame once a short (< 20-byte) chunk
        is received signalling end-of-frame, otherwise None.
        """
        if len(chunk) < 2:
            return None
        # Strip the (nonce, seq) header.
        self._notify_buffer.append(chunk[2:])
        if len(chunk) < 20:
            frame = b"".join(self._notify_buffer)
            self._notify_buffer.clear()
            # Some firmwares periodically send a noise pulse rather than a
            # data frame; the app discards them.
            if frame.hex() == SM_GW_HEARTBEAT_HEX:
                return None
            return frame
        return None

    def parse_notification(self, data: bytes) -> dict:
        """Parse a single on-wire chunk. Returns updates only on full frames."""
        frame = self.handle_transport_chunk(data)
        if frame is None:
            return {}
        return self.parse_frame(frame)

    def parse_frame(self, frame: bytes) -> dict:
        """Parse a reassembled DP frame."""
        if self._tuya_dp_mode:
            return self._parse_frame_tuya(frame)
        result: dict = {}
        # The frame begins with two bytes that the app reads as a 16-bit
        # DP-ID, before any framing header. The original parser starts at
        # index 2 and treats those leading bytes as throw-away count info.
        idx = 2
        while idx < len(frame):
            if idx + 1 >= len(frame):
                break
            dp_id = (frame[idx] << 8) | frame[idx + 1]
            idx += 2
            try:
                idx = self._parse_dp(frame, idx, dp_id, result)
            except (IndexError, ValueError) as err:
                _LOGGER.debug("Scent Marketing GW: DP parse failed at idx=%d dp=%d: %s",
                              idx, dp_id, err)
                break
        return result

    def _parse_frame_tuya(self, frame: bytes) -> dict:
        """Parse a PID-98 frame using Tuya-style type-driven length decoding.

        Mirrors `HexConver.dpStringToJson`. The wire layout is identical to
        the regular frame but the parser interprets the type-tag byte as
        a hint for the value length (low-nibble = byte-count, 0xAF / 0xBF
        = length-prefixed).
        """
        result: dict = {}
        if len(frame) < 2:
            return result
        dp_count = frame[1] & 0xFF
        idx = 2
        for _ in range(dp_count):
            if idx + 3 > len(frame):
                break
            dp_id = (frame[idx] << 8) | frame[idx + 1]
            type_tag = frame[idx + 2]
            idx += 3
            if type_tag in (SM_GW_TYPE_BINARY, SM_GW_TYPE_TEXT):
                if idx + 2 > len(frame):
                    break
                length = (frame[idx] << 8) | frame[idx + 1]
                idx += 2
                payload = frame[idx:idx + length]
                idx += length
                # Reuse the regular DP handler by faking it received a
                # composite payload.
                self._dispatch_composite(dp_id, type_tag, payload, result)
            else:
                size = type_tag & 0x0F
                payload = frame[idx:idx + size]
                idx += size
                if dp_id == SM_GW_DP_POWER and len(payload) >= 1:
                    result["power"] = payload[0] == 1
                    result["phase"] = "idle" if result["power"] else "off"
                elif dp_id == SM_GW_DP_LOCK and len(payload) >= 1:
                    result["lock"] = payload[0] == 1
        return result

    def _dispatch_composite(self, dp_id: int, type_tag: int, payload: bytes, result: dict) -> None:
        """Composite-DP dispatcher shared by binary + Tuya parsers."""
        if dp_id == SM_GW_DP_VERSION:
            result["firmware_version"] = payload.split(b"\x00", 1)[0].decode("ascii", errors="replace")
        elif dp_id == SM_GW_DP_NAME:
            text = payload.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            if text:
                result["device_name"] = text
        elif dp_id == SM_GW_DP_REMARK:
            text = payload.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            if text:
                result["remark"] = text
        elif dp_id == SM_GW_DP_BATTERY and len(payload) >= 3:
            result["battery"] = payload[1] & 0xFF
        elif dp_id == SM_GW_DP_OIL and len(payload) >= 1:
            first = payload[0] & 0xFF
            if 113 <= first <= 127 and len(payload) >= 7:
                result["oil_remaining"] = (payload[5] << 8) | payload[6]
            elif len(payload) >= 2:
                pct_byte = payload[1] & 0xFF
                if pct_byte not in (0xE0,):
                    result["oil_remaining"] = int(pct_byte / 2.55)
        elif dp_id == SM_GW_DP_LIGHT and len(payload) >= 2:
            light_type = payload[0] & 0xFF
            light_status = payload[1] & 0xFF
            if light_type == 1:
                result["light_on"] = light_status == 1
            elif light_type in (3, 7):
                result["light_on"] = light_status in (1, 2)
            else:
                result["light_on"] = False
        elif dp_id == SM_GW_DP_PASSWORD and len(payload) >= 1:
            result["password_required"] = payload[0] != SM_GW_PASSWORD_OK_BYTE
        elif dp_id == SM_GW_DP_FAN and len(payload) >= 3:
            # [size, max_level, current_level] — surface current_level only.
            result["fan"] = (payload[2] & 0xFF) > 0

    def _parse_dp(self, frame: bytes, idx: int, dp_id: int, result: dict) -> int:
        """Parse a single DP starting at `idx`. Returns the new index."""
        if dp_id == SM_GW_DP_POWER:
            result["power"] = (frame[idx + 1] & 0xFF) == 1
            result["phase"] = "idle" if result["power"] else "off"
            return idx + 2

        if dp_id == SM_GW_DP_LOCK:
            result["lock"] = frame[idx + 1] == 1
            return idx + 2

        # All remaining DPs we care about are composite (AF/BF-tagged)
        # with a 2-byte big-endian length prefix.
        type_tag = frame[idx]
        if type_tag not in (SM_GW_TYPE_BINARY, SM_GW_TYPE_TEXT):
            # Unknown / inline DP — bail out, the rest of the frame is
            # likely misaligned anyway.
            return len(frame)

        length = (frame[idx + 1] << 8) | frame[idx + 2]
        payload_start = idx + 3
        payload = frame[payload_start:payload_start + length]
        self._dispatch_composite(dp_id, type_tag, payload, result)
        return payload_start + length


class ScentMarketingGwXorProtocol(ScentMarketingGwProtocol):
    """Scent Marketing — GW family with XOR-encrypted JSON payloads.

    Used by WiFi (mfr-data leading byte 01/02/03) and Cellular (B1/B2)
    variants. The transport layer is identical to plain GW (multi-packet
    chunking on EE01/EE02/EE03), but the inner payload is JSON encrypted
    with a 256-byte lookup-table stream cipher keyed by the MAC address.
    """

    device_type = DeviceType.SCENT_MARKETING_GW_XOR

    def __init__(self, mac: str = "", tuya_dp_mode: bool = False) -> None:
        super().__init__(tuya_dp_mode=tuya_dp_mode)
        # MAC is needed for the encryption key. Some flows learn it only
        # after the first advertisement is observed, so we allow updates.
        self._mac = mac.upper().replace(":", "")

    def set_mac(self, mac: str) -> None:
        self._mac = mac.upper().replace(":", "")

    # ------------------------------------------------------------------
    # XOR stream cipher (HexConver.dataEncrypt / dataDecrypt)
    # ------------------------------------------------------------------

    def _xor_keystream_start(self, nonce: int) -> int:
        if len(self._mac) < 10:
            raise ValueError("XOR protocol requires a 12-hex-char MAC")
        return int(self._mac[8:10], 16) ^ (nonce & 0xFF)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt a JSON payload. The first byte of the result is the nonce."""
        nonce = random.randint(0, len(SM_GW_XOR_DICT) - 1)
        out = bytearray(len(plaintext) + 1)
        out[0] = nonce
        idx = self._xor_keystream_start(nonce)
        for i, b in enumerate(plaintext):
            out[i + 1] = SM_GW_XOR_DICT[idx & 0xFF] ^ b
            idx += 1
        return bytes(out)

    def decrypt(self, ciphertext: bytes) -> bytes:
        if len(ciphertext) < 2:
            return b""
        nonce = ciphertext[0]
        idx = self._xor_keystream_start(nonce)
        out = bytearray(len(ciphertext) - 1)
        for i in range(1, len(ciphertext)):
            out[i - 1] = SM_GW_XOR_DICT[idx & 0xFF] ^ ciphertext[i]
            idx += 1
        return bytes(out)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    # WiFi-flagged GW devices receive the same DP frame as plain BLE
    # devices, just wrapped in an outer `{"time": <unix>, "data": {<dps>}}`
    # JSON and then run through the XOR stream cipher. We reuse the parent
    # class's binary frame builders and translate them into the JSON
    # representation Mirrors of `IOTDataParse.writeDeviceProperty()` use.

    @staticmethod
    def _wrap_for_wire(dp_json: dict) -> bytes:
        wrapped = {"time": int(time.time()), "data": dp_json}
        return json.dumps(wrapped, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _dp_to_json(dp_id: int, value: int | str, type_int: int) -> dict:
        return {str(dp_id): {"type": type_int, "value": value}}

    def build_power(self, on: bool) -> bytes:
        dp_json = self._dp_to_json(SM_GW_DP_POWER, 1 if on else 0, SM_GW_TYPE_BOOL)
        return self.encrypt(self._wrap_for_wire(dp_json))

    def build_lock(self, on: bool) -> bytes:
        dp_json = self._dp_to_json(SM_GW_DP_LOCK, 1 if on else 0, SM_GW_TYPE_LOCK)
        return self.encrypt(self._wrap_for_wire(dp_json))

    def build_query(self) -> bytes:
        # The device emits state automatically; an explicit query is not
        # part of the WiFi-XOR protocol. We still send something to nudge
        # the firmware (the plaintext init packet) — encrypted, so the
        # device's input pipeline accepts it.
        return self.encrypt(b'{"query":1}')

    # ------------------------------------------------------------------
    # Notification parsing
    # ------------------------------------------------------------------

    def parse_notification(self, data: bytes) -> dict:
        frame = self.handle_transport_chunk(data)
        if frame is None:
            return {}
        try:
            plaintext = self.decrypt(frame)
            obj = json.loads(plaintext.decode("utf-8", errors="replace"))
        except Exception as err:
            _LOGGER.debug("Scent Marketing GW-XOR: decrypt/parse failed: %s", err)
            return {}
        return self._map_json_state(obj)

    @staticmethod
    def _map_json_state(obj: dict) -> dict:
        """Translate the device's JSON state into our DiffuserState fields.

        Accepts both the `{<dp_id>: {"type": ..., "value": ...}}` flat form
        and the `{"time": ..., "data": {...}}` wrapped form the Android
        app uses on the write path.
        """
        if not isinstance(obj, dict):
            return {}
        if "data" in obj and isinstance(obj["data"], dict):
            obj = obj["data"]
        result: dict = {}
        for raw_key, raw_val in obj.items():
            try:
                dp_id = int(raw_key)
            except (TypeError, ValueError):
                continue
            value = raw_val
            if isinstance(raw_val, dict):
                value = raw_val.get("value", raw_val)
            if dp_id == SM_GW_DP_POWER:
                result["power"] = bool(int(value)) if value not in (None, "") else False
                result["phase"] = "idle" if result["power"] else "off"
            elif dp_id == SM_GW_DP_LOCK:
                result["lock"] = bool(int(value)) if value not in (None, "") else False
            elif dp_id == SM_GW_DP_BATTERY:
                try:
                    result["battery"] = int(value, 16) if isinstance(value, str) else int(value)
                except (TypeError, ValueError):
                    pass
            elif dp_id == SM_GW_DP_NAME and isinstance(value, str):
                # Tuya-style BF values arrive as hex strings; try to decode.
                try:
                    decoded = bytes.fromhex(value).rstrip(b"\x00").decode("utf-8", errors="replace")
                    if decoded:
                        result["device_name"] = decoded.strip()
                except ValueError:
                    result["device_name"] = value
        return result


# ---------------------------------------------------------------------------
# Scent Tech / ScentLab (YooAI OEM)
# ---------------------------------------------------------------------------
# The timer record, frame builder and frame assembler follow @alexlewer's
# ScentLab BLE integration (github.com/alexlewer/scentlab-ble):
#
#   Copyright (c) 2026 Alex Lewer. MIT License. Permission is hereby
#   granted, free of charge, to any person obtaining a copy of this
#   software and associated documentation files, to deal in the Software
#   without restriction, subject to including this notice. THE SOFTWARE
#   IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
#
# The 0x21 state push is from @marzliak's live Scent Tech capture (#36).

@dataclass(frozen=True)
class ScentTechTimer:
    """One 16-byte Scent Tech timer record.

    Writes send back the whole record with one field changed, so the
    serial and the server-assigned timer id survive untouched.
    """

    enabled: bool
    serial: int
    weekdays: int          # bit0 Mon … bit6 Sun, bit7 = any day set
    start_minute: int      # minute of day, 0..1439
    stop_minute: int
    run_seconds: int
    pause_seconds: int
    timer_id: int

    @classmethod
    def from_bytes(cls, data: bytes) -> "ScentTechTimer":
        if len(data) != SCENT_TECH_TIMER_RECORD_SIZE:
            raise ValueError(f"expected 16 timer bytes, got {len(data)}")
        return cls(
            enabled=data[0] != 0,
            serial=data[1],
            weekdays=int.from_bytes(data[2:4], "little"),
            start_minute=int.from_bytes(data[4:6], "little"),
            stop_minute=int.from_bytes(data[6:8], "little"),
            run_seconds=int.from_bytes(data[8:10], "little"),
            pause_seconds=int.from_bytes(data[10:12], "little"),
            timer_id=int.from_bytes(data[12:16], "little"),
        )

    def to_bytes(self) -> bytes:
        return b"".join((
            bytes((int(self.enabled), self.serial & 0xFF)),
            self.weekdays.to_bytes(2, "little"),
            self.start_minute.to_bytes(2, "little"),
            self.stop_minute.to_bytes(2, "little"),
            self.run_seconds.to_bytes(2, "little"),
            self.pause_seconds.to_bytes(2, "little"),
            self.timer_id.to_bytes(4, "little"),
        ))

    def with_changes(self, **changes) -> "ScentTechTimer":
        return replace(self, **changes)


class ScentTechProtocol(BleProtocol):
    """Scent Tech / ScentLab diffusers ("Scent-<serial>", YooAI OEM).

    Session (mirrors both apps): password-status query 0x47, the
    password 0x48 if one is configured, enquiry 0x09 01 00, capability
    query 0x51. Timers are read with 0x08 (-> 0x88, five 16-byte
    records) and written one record at a time with 0x14 (-> 0x94).
    """

    device_type = DeviceType.SCENT_TECH
    service_uuid = SCENT_TECH_SERVICE_UUID
    write_char_uuid = SCENT_TECH_CHAR_UUID
    notify_char_uuid = SCENT_TECH_CHAR_UUID

    def __init__(self) -> None:
        self._rx_buffer = bytearray()

    # -- framing -----------------------------------------------------------

    @staticmethod
    def _frame(command: int, data: bytes = b"") -> bytes:
        body = SCENT_TECH_HEADER + bytes((len(data) + 1, command)) + data
        return body + bytes(((-sum(body)) & 0xFF, SCENT_TECH_TRAILER))

    def _feed(self, data: bytes) -> list[bytes]:
        """Append a notification and return every complete, valid frame.

        LEN gives the frame size up front, so a reply split across
        notifications (the 0x88 timer table is 88 bytes) is held until
        the rest arrives.
        """
        self._rx_buffer.extend(data)
        frames: list[bytes] = []
        while True:
            start = self._rx_buffer.find(SCENT_TECH_HEADER)
            if start < 0:
                # Keep a lone 0x55 that may be the first header byte.
                if self._rx_buffer.endswith(SCENT_TECH_HEADER[:1]):
                    self._rx_buffer[:] = SCENT_TECH_HEADER[:1]
                else:
                    self._rx_buffer.clear()
                return frames
            del self._rx_buffer[:start]
            if len(self._rx_buffer) < 3:
                return frames
            size = self._rx_buffer[2] + 5
            if len(self._rx_buffer) < size:
                return frames
            frame = bytes(self._rx_buffer[:size])
            del self._rx_buffer[:size]
            if frame[-1] != SCENT_TECH_TRAILER or sum(frame[:-1]) & 0xFF:
                _LOGGER.debug("Scent Tech: dropping invalid frame %s", frame.hex())
                continue
            frames.append(frame)

    # -- TX ----------------------------------------------------------------

    def build_power(self, on: bool) -> bytes:
        return self._frame(
            SCENT_TECH_CMD_CONTROL,
            bytes((SCENT_TECH_ACTION_POWER, 0x01 if on else 0x00, 0x00)),
        )

    def build_query(self) -> bytes:
        return self._frame(SCENT_TECH_CMD_READ_TIMERS)

    def build_session_frames(self, password: str | None) -> list[bytes]:
        """Frames that open a session, in the apps' order."""
        frames = [self._frame(SCENT_TECH_CMD_PW_QUERY)]
        if password:
            encoded = password.encode("ascii", errors="replace")[:4]
            frames.append(self._frame(
                SCENT_TECH_CMD_PW_SUBMIT, bytes((len(encoded),)) + encoded,
            ))
        frames.append(self._frame(SCENT_TECH_CMD_ENQUIRY, b"\x01\x00"))
        frames.append(self._frame(SCENT_TECH_CMD_CAPABILITY))
        return frames

    def build_write_timer(self, timer: ScentTechTimer) -> bytes:
        return self._frame(SCENT_TECH_CMD_WRITE_TIMER, timer.to_bytes())

    def build_time_sync(self, now: datetime | None = None) -> bytes:
        """Local wall-clock time, encoded as if it were a UTC epoch.

        That is what both apps send, and the firmware compares its timer
        windows against it. Sending real UTC shifts every window by the
        UTC offset (@marzliak saw 3 h in São Paulo, #36).
        """
        if now is None:
            try:
                from homeassistant.util import dt as dt_util
                now = dt_util.now()
            except ImportError:
                now = datetime.now().astimezone()
        wall = now.replace(tzinfo=timezone.utc)
        ts = int(wall.timestamp()) & 0xFFFFFFFF
        return self._frame(SCENT_TECH_CMD_TIME_SYNC, ts.to_bytes(4, "little"))

    # -- RX ----------------------------------------------------------------

    def parse_notification(self, data: bytes) -> dict:
        result: dict = {}
        for frame in self._feed(bytes(data)):
            command = frame[3]
            payload = frame[4:-2]
            if command == SCENT_TECH_RESP_TIMERS:
                timers = self._parse_timers(payload)
                if timers is not None:
                    result["timer_slots"] = timers
            elif command == SCENT_TECH_RESP_STATE and len(payload) > 4:
                power = bool(payload[4] & 0x01)
                result["power"] = power
                result["phase"] = "idle" if power else "off"
            elif command == SCENT_TECH_RESP_PW_QUERY and payload:
                result["password_required"] = payload[0] != 0
            elif command == SCENT_TECH_RESP_PW_SUBMIT and payload:
                if payload[0] == 0:
                    _LOGGER.warning("Scent Tech: device rejected the password")
            elif command == SCENT_TECH_RESP_WRITE_TIMER:
                result["timer_write_ack"] = True
        return result

    @staticmethod
    def _parse_timers(payload: bytes) -> dict[int, ScentTechTimer] | None:
        """0x88 payload: u16 LE record count, then that many records."""
        if len(payload) < 2:
            return None
        count = int.from_bytes(payload[0:2], "little")
        size = SCENT_TECH_TIMER_RECORD_SIZE
        if len(payload) < 2 + count * size:
            _LOGGER.debug(
                "Scent Tech: timer frame declares %d records but has %d bytes",
                count, len(payload),
            )
            return None
        return {
            index + 1: ScentTechTimer.from_bytes(
                payload[2 + index * size:2 + (index + 1) * size]
            )
            for index in range(count)
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_protocol(
    device_type: DeviceType,
    mac: str = "",
    pid: int | None = None,
) -> BleProtocol:
    """Get the appropriate protocol handler for a device type.

    For Scent Marketing GW devices a PID of 98 selects the Tuya-DP hex
    parser instead of the regular binary one.
    """
    tuya = (pid == 98)
    if device_type == DeviceType.TUYA_BLE:
        return TuyaBleProtocol()
    elif device_type == DeviceType.AROMA_LINK:
        return AromaLinkBleProtocol()
    elif device_type == DeviceType.SCENTIMENT:
        return ScentimentProtocol()
    elif device_type == DeviceType.SCENT_MARKETING_AK:
        return ScentMarketingAkProtocol()
    elif device_type == DeviceType.SCENT_MARKETING_GW:
        return ScentMarketingGwProtocol(tuya_dp_mode=tuya)
    elif device_type == DeviceType.SCENT_MARKETING_GW_XOR:
        return ScentMarketingGwXorProtocol(mac=mac, tuya_dp_mode=tuya)
    elif device_type == DeviceType.AROMELY_ARO_MAX:
        return AromelyAroMaxProtocol()
    elif device_type == DeviceType.SCENT_TECH:
        return ScentTechProtocol()
    raise ValueError(f"Unknown device type: {device_type}")


def _detect_scent_marketing(advertisement_data) -> DeviceType | None:
    """Match Scent Marketing manufacturer-specific data.

    Mirrors `DeviceModel.deviceFiltration` in the decompiled app: the
    manufacturer ID picks the family, and (for GW devices) the first byte
    of the manufacturer payload distinguishes plain BLE from the WiFi /
    Cellular XOR-encrypted variants.
    """
    if advertisement_data is None:
        return None
    mfr_data = getattr(advertisement_data, "manufacturer_data", None)
    if not mfr_data:
        return None

    if SM_MFR_ID_AK in mfr_data:
        return DeviceType.SCENT_MARKETING_AK

    gw_payload = mfr_data.get(SM_MFR_ID_GW) or mfr_data.get(SM_MFR_ID_GW_ALT)
    if gw_payload is None:
        return None

    lead = gw_payload[:1].hex().upper() if gw_payload else ""
    if lead in SM_GW_FLAG_WIFI or lead in SM_GW_FLAG_CELLULAR:
        return DeviceType.SCENT_MARKETING_GW_XOR
    return DeviceType.SCENT_MARKETING_GW


def extract_scent_marketing_metadata(advertisement_data) -> dict:
    """Return diagnostic fields from a Scent Marketing advertisement.

    Used by the config flow + diagnostics exporter so the reporter's logs
    contain everything we need to validate/debug detection without having
    to ask for raw advertisement dumps separately.
    """
    out: dict = {"mfr_id": None, "raw_hex": "", "pid": None, "wifi_flag": None,
                 "heartbeat": False, "mac_from_adv": None}
    if advertisement_data is None:
        return out
    mfr_data = getattr(advertisement_data, "manufacturer_data", None) or {}
    for mfr_id in (SM_MFR_ID_AK, SM_MFR_ID_GW, SM_MFR_ID_GW_ALT):
        if mfr_id in mfr_data:
            payload = mfr_data[mfr_id]
            out["mfr_id"] = mfr_id
            out["raw_hex"] = payload.hex().upper()
            hex_str = out["raw_hex"]
            if mfr_id == SM_MFR_ID_AK:
                if hex_str[:2] == SM_AK_FLAG_HEARTBEAT:
                    out["heartbeat"] = True
                if len(hex_str) > 22:
                    out["mac_from_adv"] = hex_str[10:22]
            else:  # GW
                lead = hex_str[:2]
                if lead in SM_GW_FLAG_WIFI:
                    out["wifi_flag"] = "wifi"
                elif lead in SM_GW_FLAG_CELLULAR:
                    out["wifi_flag"] = "cellular"
                else:
                    out["wifi_flag"] = "ble"
                if len(hex_str) > 5:
                    try:
                        out["pid"] = int(hex_str[2:6], 16)
                    except ValueError:
                        pass
                if len(hex_str) > 33:
                    out["mac_from_adv"] = hex_str[22:34]
            break
    return out


def detect_device_type(
    ble_name: str,
    advertisement_data=None,
) -> DeviceType | None:
    """Detect device type from advertisement.

    Detection priority:
      1. Manufacturer-specific data for Scent Marketing families (most
         reliable — the Android app uses this exclusively).
      2. Advertised service / manufacturer data for Aromely Aro Max
         (its local name is a per-unit serial).
      3. BLE local-name prefix patterns for the other families.
    """
    sm_type = _detect_scent_marketing(advertisement_data)
    if sm_type is not None:
        return sm_type

    if _detect_aromely(advertisement_data):
        return DeviceType.AROMELY_ARO_MAX

    if not ble_name:
        return None
    lowered = ble_name.lower()
    for dtype, patterns in BLE_NAME_PATTERNS.items():
        for pattern in patterns:
            if lowered.startswith(pattern.lower()):
                return dtype
    return None


def _detect_aromely(advertisement_data) -> bool:
    """Match an Aromely Aro Max from its advertisement.

    The unit advertises the AF30 service UUID and carries the ASCII
    "AroMax" / "Diffuser" in its manufacturer data, while the local name
    is a per-unit serial — so neither name prefix nor a Scent-Marketing
    style manufacturer-ID match works here.
    """
    if advertisement_data is None:
        return False
    uuids = getattr(advertisement_data, "service_uuids", None) or []
    for u in uuids:
        if str(u).lower() == AROMELY_ADV_SERVICE_UUID or str(u).lower().startswith("0000af30"):
            return True
    mfr_data = getattr(advertisement_data, "manufacturer_data", None) or {}
    for payload in mfr_data.values():
        if b"AroMax" in payload or b"DiffuserAro" in payload:
            return True
    return False
