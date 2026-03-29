"""BLE protocol implementations for Tuya and Aroma-Link diffusers."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from dataclasses import dataclass, field

from .const import (
    DeviceType,
    TUYA_HEADER, TUYA_VERSION,
    TUYA_CMD_DP_WRITE, TUYA_CMD_DP_REPORT, TUYA_CMD_QUERY, TUYA_CMD_TIME_SYNC,
    TUYA_DP_TYPE_BOOL, TUYA_DP_TYPE_RAW,
    TUYA_DP_POWER, TUYA_DP_SCHEDULE,
    AL_HEADER, AL_TRAILER,
    AL_CMD_QUERY, AL_CMD_STATUS, AL_CMD_WRITE,
    AL_SUB_POWER, AL_SUB_FAN, AL_SUB_SCHEDULE, AL_SUB_TIME_SYNC,
    AL_SUB_QUERY_SCHEDULES,
    AL_FAN_ON_VALUE, AL_FAN_OFF_VALUE,
    AL_SLOT_ENABLED, AL_SLOT_DISABLED,
    AL_PHASE_IDLE, AL_PHASE_SPRAYING, AL_PHASE_PAUSED,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared data structures
# ---------------------------------------------------------------------------

@dataclass
class DiffuserState:
    """Represents the current state of a diffuser."""

    power: bool | None = None
    fan: bool | None = None            # Aroma-Link only
    phase: str = "unknown"             # "off", "idle", "spraying", "paused"
    work_seconds: int = 0
    pause_seconds: int = 0
    start_hour: int = 0
    start_minute: int = 0
    end_hour: int = 23
    end_minute: int = 59


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

    @abstractmethod
    def build_power(self, on: bool) -> bytes:
        """Build power on/off command."""

    @abstractmethod
    def build_time_sync(self, now: datetime | None = None) -> bytes:
        """Build time synchronisation command."""

    @abstractmethod
    def build_query(self) -> bytes:
        """Build status query command."""

    @abstractmethod
    def parse_notification(self, data: bytes) -> dict:
        """Parse a notification from the device. Returns dict of state updates."""

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

    def build_power(self, on: bool) -> bytes:
        return self._build_packet(bytes([AL_CMD_WRITE, AL_SUB_POWER, 0x01 if on else 0x00]))

    def build_fan(self, on: bool) -> bytes:
        return self._build_packet(bytes([
            AL_CMD_WRITE, AL_SUB_FAN,
            AL_FAN_ON_VALUE if on else AL_FAN_OFF_VALUE,
        ]))

    def build_query(self) -> bytes:
        return self._build_packet(bytes([AL_CMD_QUERY, AL_SUB_QUERY_SCHEDULES]))

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

        for i in range(5):
            if i < len(slots):
                s = slots[i]
                data.extend([
                    s.start_hour, s.start_minute,
                    s.end_hour, s.end_minute,
                    AL_SLOT_ENABLED if s.enabled else AL_SLOT_DISABLED,
                    (s.work_seconds >> 8) & 0xFF, s.work_seconds & 0xFF,
                    (s.pause_seconds >> 8) & 0xFF, s.pause_seconds & 0xFF,
                ])
            else:
                data.extend([
                    0, 0, 0, 0,          # 00:00 - 00:00
                    AL_SLOT_DISABLED,
                    0x00, 0x0A,          # work = 10
                    0x00, 0x78,          # pause = 120
                ])

        return self._build_packet(bytes([AL_CMD_WRITE, AL_SUB_SCHEDULE]) + bytes(data))

    def supports_fan(self) -> bool:
        return True

    def parse_notification(self, data: bytes) -> dict:
        """Parse Aroma-Link notification packets."""
        result: dict = {}

        # Strip header/trailer if present
        if data[:3] == AL_HEADER and data[-3:] == AL_TRAILER:
            payload = data[4:-3]  # skip header(3) + xor(1), trim trailer(3)
        else:
            payload = data

        if len(payload) < 2:
            return result

        cmd = payload[0]
        sub = payload[1]

        if cmd == AL_CMD_STATUS:
            if sub == AL_SUB_POWER and len(payload) >= 3:
                result["power"] = payload[2] == 0x01
                if not result["power"]:
                    result["phase"] = "off"

            elif sub == AL_SUB_FAN and len(payload) >= 3:
                result["fan"] = payload[2] == AL_FAN_ON_VALUE

            elif sub == 0x09 and len(payload) >= 10:
                # Spray cycle status: phase, work, pause, start, end, enabled
                phase_byte = payload[2]
                if phase_byte == AL_PHASE_IDLE:
                    result["phase"] = "idle"
                elif phase_byte == AL_PHASE_SPRAYING:
                    result["phase"] = "spraying"
                elif phase_byte == AL_PHASE_PAUSED:
                    result["phase"] = "paused"

                result["work_seconds"] = (payload[3] << 8) | payload[4]
                result["pause_seconds"] = (payload[5] << 8) | payload[6]
                result["start_hour"] = payload[7]
                result["start_minute"] = payload[8]
                result["end_hour"] = payload[9]
                result["end_minute"] = payload[10]

        elif cmd == AL_CMD_WRITE and len(payload) >= 3:
            # ACK responses (57 XX "ACK")
            if payload[2:5] == b"ACK":
                result["ack"] = sub

        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_protocol(device_type: DeviceType) -> BleProtocol:
    """Get the appropriate protocol handler for a device type."""
    if device_type == DeviceType.TUYA_BLE:
        return TuyaBleProtocol()
    elif device_type == DeviceType.AROMA_LINK:
        return AromaLinkBleProtocol()
    raise ValueError(f"Unknown device type: {device_type}")


def detect_device_type(ble_name: str) -> DeviceType | None:
    """Detect device type from BLE advertisement name."""
    if not ble_name:
        return None
    for dtype, patterns in {
        DeviceType.AROMA_LINK: ["Scent "],
        DeviceType.TUYA_BLE: ["BT-ivy"],
    }.items():
        for pattern in patterns:
            if ble_name.startswith(pattern):
                return dtype
    return None
