"""Unified device manager for scent diffusers.

Uses connect-on-demand for BLE: connects only when sending a command,
then disconnects after a short idle period. This frees the BLE adapter
for other devices.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from bleak import BleakClient, BleakScanner, BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.device_registry as dr

from .const import (
    DeviceType,
    AL_DURATION_LIMITS,
    AL_DURATION_LIMITS_DEFAULT,
    BLE_CONNECT_MAX_ATTEMPTS,
    BLE_PASSIVE_ONLY_RECENT_SECONDS,
    DIAG_FRAME_LOG_MAX,
    BLE_INITIAL_REFRESH_RETRY_SECONDS,
    BLE_INITIAL_SILENT_READS_MAX,
    SM_AK_CY_PUSH_SETTLE_SECONDS,
    CLOUD_SCHEDULE_REFRESH_EVERY,
    BLE_REFRESH_INTERVAL_SECONDS,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_WORK_DURATION,
    DEFAULT_PAUSE_DURATION,
    AL_MANY_PUMP_DEVICE_CODES,
    AL_SUB_POWER,
    AL_SUB_TIME_SYNC,
)
from .protocol_ble import (
    BleProtocol,
    DiffuserState,
    ScheduleSlot,
    ScheduleSetup,
    AromaLinkBleProtocol,
    ScentimentProtocol,
    ScentMarketingAkProtocol,
    ScentMarketingGwProtocol,
    ScentMarketingGwXorProtocol,
    AromelyAroMaxProtocol,
    ScentTechProtocol,
    AiDiffuserProtocol,
    get_protocol,
    detect_device_type,
)
from .protocol_cloud import AromaLinkCloudClient

if TYPE_CHECKING:
    from .estimated_oil import OilEstimator

_LOGGER = logging.getLogger(__name__)

# How long to wait for a Scent Tech timer table / write acknowledgement.
SCENT_TECH_REPLY_TIMEOUT = 4.0

# Disconnect BLE after this many seconds of inactivity
BLE_IDLE_DISCONNECT_SECONDS = 10
# Cooldown after a failed connect / write before we try again, so a
# stuck device gets a chance to recover instead of being hammered.
BLE_FAILURE_COOLDOWN_SECONDS = 3.0
# A timed refresh running longer than this stops holding off the next tick.
BLE_REFRESH_BUSY_MAX_SECONDS = 120
# Below the default interval, timed refreshes wait this long after a failure.
BLE_REFRESH_FAILURE_BACKOFF_SECONDS = 60
# Aroma-Link 0A clock drift from local time that triggers a 57 17 on refresh.
AL_CLOCK_DRIFT_SECONDS = 120
# Refreshes that send the 52 15 week read before a unit that never
# reports its durations is taken as not reporting them. Its schedule
# edits then use the values held in HA, as before the read gate.
AL_WEEK_QUERY_MAX_ATTEMPTS = 3
# Default run time for the momentary "Diffuse Now" button. Adjustable
# per device via the Momentary Duration number entity and persisted
# locally in the config entry options.
DEFAULT_MOMENTARY_SECONDS = 30


class _NoConnectableScanner(BleakError):
    """No connectable adapter or proxy has registered yet (HA startup)."""


class ScentDiffuserDevice:
    """Manages a single scent diffuser via BLE and/or cloud."""

    def __init__(
        self,
        hass: HomeAssistant | None = None,
        ble_address: str | None = None,
        ble_name: str | None = None,
        device_type: DeviceType | None = None,
        cloud_client: AromaLinkCloudClient | None = None,
        cloud_device_id: str | None = None,
        sm_metadata: dict | None = None,
        gw_password: str | None = None,
        entry_id: str | None = None,
    ) -> None:
        # HomeAssistant reference, used to fetch a cached BLEDevice via
        # the core bluetooth integration before opening a connection.
        # Optional so the manager can still be unit-tested without HA.
        self._hass = hass
        self._entry_id = entry_id
        # Detection metadata from the config flow — populated only for
        # Scent Marketing family devices.
        self._sm_metadata = sm_metadata or {}
        # Optional 4-char ASCII password for Scent Marketing GW devices.
        # Sent proactively after every BLE connect.
        self._gw_password = gw_password or None
        # Trace ring-buffer for the diagnostics download.
        self._recent_notifications: list[str] = []
        self._recent_commands: list[str] = []
        # Both directions in arrival order with wall-clock time, so a
        # diagnostics download shows what the device answered to which
        # write (#22's fan flicker couldn't be ordered without it).
        self._frame_log: list[tuple[str, str, str]] = []
        # BLE
        self._ble_address = ble_address
        self._ble_name = ble_name or ""
        self._ble_client: BleakClient | None = None
        self._ble_connected = False
        self._ble_notify_subscribed = False
        # Whether the write characteristic wants a GATT response —
        # resolved from the device's own GATT table on first write and
        # cached for the lifetime of the connection.
        self._ble_write_response: bool | None = None
        self._ble_lock = asyncio.Lock()
        self._ble_disconnect_task: asyncio.Task | None = None
        self._ble_has_synced_time = False
        # NACK replies by sub-command, stamped with a running count so
        # each write only sees the NACKs that arrived after it started —
        # a shared "last NACK" let concurrent writes clear each other's.
        self._ble_nack_seq = 0
        self._ble_nacks: dict[int, int] = {}
        self._ble_time_acked = False
        # Monotonic timestamp of the last failed BLE connect/write —
        # used to back off after errors instead of hammering a stuck
        # device (which can wedge a V3 diffuser's GATT stack badly
        # enough that even the official app can't reconnect until a
        # power cycle, per @Mins95's 2026-06-01 report).
        self._ble_last_failure_ts: float = 0.0
        self._device_info_query_sent = False
        self._radar_query_sent = False
        # 57 21 rewrites all five levels; interleaved writes lose a change.
        self._radar_write_lock = asyncio.Lock()

        # Device type
        if device_type:
            self._device_type = device_type
        elif ble_name:
            # No advertisement object available at this point — fall back to
            # name-only detection (config flow performs the richer
            # advertisement-aware detection up front and persists the result).
            self._device_type = detect_device_type(ble_name) or DeviceType.AROMA_LINK
        else:
            self._device_type = DeviceType.AROMA_LINK

        # Protocol handler. GW-XOR needs the MAC for its keystream; GW
        # devices with PID 98 use the Tuya-DP hex parser.
        mac = (ble_address or "").replace(":", "")
        pid = self._sm_metadata.get("pid") if self._sm_metadata else None
        self._protocol: BleProtocol = get_protocol(self._device_type, mac=mac, pid=pid)

        # Cloud
        self._cloud: AromaLinkCloudClient | None = cloud_client
        self._cloud_device_id = cloud_device_id
        # Counts cloud refreshes so the schedule read-back can run on the
        # first one and then only every CLOUD_SCHEDULE_REFRESH_EVERY.
        self._cloud_schedule_poll_count = 0

        # State
        self._state = DiffuserState()
        self._state_callbacks: list[callable] = []
        self._capability_unsubs: list[Callable[[], None]] = []
        # Wall-clock time of the last BLE notification that changed
        # state. Lets a user tell a fresh reading from a stale one
        # without the entity flapping to unavailable (#32).
        self._ble_last_update: datetime | None = None
        # Whether a BLE state read has completed since setup, and when
        # the last advertisement-triggered retry of it was started.
        # Set by the first notification that carries state, not by sending
        # the query: a unit that stays silent must not count as read (#42).
        self._ble_state_read = False
        self._silent_state_reads = 0
        self._initial_retry_ts: float | None = None
        self._week_query_attempts = 0
        # The unit answered neither 52 06 nor 52 15 (see AL_WEEK_QUERY_MAX_ATTEMPTS).
        self._durations_unreported = False
        # Configured durations read from the device since the entry loaded.
        self._schedule_durations_read = False
        # Schedule window read from the device since the entry loaded.
        self._schedule_window_read = False

        # Momentary diffusion ("Diffuse Now" button): power on, then
        # auto-off after this many seconds via a background task.
        self.momentary_seconds: int = DEFAULT_MOMENTARY_SECONDS
        self._momentary_task: asyncio.Task | None = None

        # Loop time the timed refresh in flight started, or None.
        self._periodic_refresh_ts: float | None = None
        self.refresh_interval: int = BLE_REFRESH_INTERVAL_SECONDS
        # Loop time the last full read started, or None.
        self._ble_full_refresh_ts: float | None = None
        # The last full read ended with no state read since setup.
        self._ble_full_refresh_unanswered = False

        # Scent Tech timer writes are read-modify-write on one record, so
        # they must not interleave; the events let a write wait for the
        # device's 0x88 table and 0x94 acknowledgement.
        self._timer_write_lock = asyncio.Lock()
        # AI-Diffuser: whether the last connect sent the full read-back.
        self._ai_read_sent = False
        self._timers_received = asyncio.Event()
        self._timer_write_acked = asyncio.Event()
        self.oil_estimator: OilEstimator | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._ble_name or f"Diffuser {self._ble_address or self._cloud_device_id}"

    @property
    def unique_id(self) -> str:
        return self._ble_address or self._cloud_device_id or "unknown"

    @property
    def device_type(self) -> DeviceType:
        return self._device_type

    @property
    def sm_metadata(self) -> dict:
        """Scent Marketing detection metadata (empty for other families)."""
        return self._sm_metadata

    @property
    def recent_notifications(self) -> list[str]:
        return list(self._recent_notifications)

    @property
    def recent_commands(self) -> list[str]:
        return list(self._recent_commands)

    @property
    def frame_log(self) -> list[str]:
        return [f"{stamp} {arrow} {hex_}" for stamp, arrow, hex_ in self._frame_log]

    def _log_frame(self, arrow: str, hex_: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._frame_log.append((stamp, arrow, hex_))
        if len(self._frame_log) > DIAG_FRAME_LOG_MAX:
            del self._frame_log[0]

    @property
    def ble_write_response(self) -> bool | None:
        """Write mode resolved from the device's GATT table.

        `None` until the first BLE write of a connection. Surfaced in
        diagnostics because a unit that only accepts
        write-without-response looks identical to a dead one otherwise.
        """
        return self._ble_write_response

    @property
    def ble_last_update(self) -> datetime | None:
        """When the device last pushed a state-changing notification."""
        return self._ble_last_update

    @property
    def supports_periodic_refresh(self) -> bool:
        """BLE device whose protocol asked for a timed refresh."""
        return bool(self._ble_address) and self._protocol.periodic_refresh

    @property
    def model_name(self) -> str:
        """Human-readable model name for HA's device-info "model" field.

        For Scent Marketing devices this surfaces the detected family
        directly on the device page, so a reporter can verify our
        detection at a glance without digging through logs.
        """
        mapping = {
            DeviceType.TUYA_BLE: "ShinePick / Tuya BLE",
            DeviceType.AROMA_LINK: "Aroma-Link",
            DeviceType.SCENTIMENT: "Scentiment Air 2",
            DeviceType.SCENT_MARKETING_AK: "Scent Marketing (AK)",
            DeviceType.SCENT_MARKETING_GW: "Scent Marketing (GW)",
            DeviceType.SCENT_MARKETING_GW_XOR: "Scent Marketing (GW, encrypted)",
            DeviceType.AROMELY_ARO_MAX: "Aromely Aro Max",
            DeviceType.SCENT_TECH: "Scent Tech / ScentLab",
            DeviceType.AI_DIFFUSER: "AI-Diffuser",
        }
        base = mapping.get(self._device_type, self._device_type.value)
        if self._device_type == DeviceType.AROMA_LINK and self._state.model_code:
            base = f"{base} {self._state.model_code}"
        # Append the PID when known — different OEMs share the same family
        # but have distinct PIDs, useful for triage.
        pid = self._sm_metadata.get("pid")
        if pid is not None:
            return f"{base} — PID {pid}"
        return base

    @property
    def device_info(self) -> dict:
        """Shared HA device_info block, consumed by every entity."""
        return {
            "identifiers": {("scent_assistant", self.unique_id)},
            "name": self.name,
            "manufacturer": "Scent Diffuser",
            "model": self.model_name,
            "sw_version": self._state.firmware_version,
        }

    @property
    def state(self) -> DiffuserState:
        return self._state

    @property
    def supports_fan(self) -> bool:
        # A device that explicitly reports "no fan" wins over the family
        # default; an unknown flag keeps the default so a failed first
        # connect never drops the switch from a device that has one.
        if self._state.has_fan is False:
            return False
        return self._protocol.supports_fan()

    @property
    def supports_oil_percent(self) -> bool:
        # App oil read order (dynaGetRemainOli): low-oil 52 1D if the unit
        # has oil detection, else 52 1E if oijShowType, and only then the
        # scale. A scale doesn't stop the percentage read.
        s = self._state
        if s.has_oil_percent is False or s.has_oil_detect:
            return False
        return True

    @property
    def supports_oil_detect(self) -> bool:
        return self._state.has_oil_detect is True

    @property
    def fan_present(self) -> bool | None:
        return self._once_reported(self.supports_fan)

    @property
    def oil_percent_present(self) -> bool | None:
        return self._once_reported(self.supports_oil_percent)

    @property
    def oil_detect_present(self) -> bool | None:
        return self._once_reported(self.supports_oil_detect)

    @property
    def battery_present(self) -> bool | None:
        return self._once_reported(self._state.has_battery is not False)

    def _once_reported(self, value: bool) -> bool | None:
        """Return value, or None before an Aroma-Link BLE unit's first 0A.

        A unit that stays silent through the initial reads keeps the
        entities it had before capability flags existed.
        """
        proto = self._protocol
        if (
            self._ble_address
            and isinstance(proto, AromaLinkBleProtocol)
            and not proto.status_seen
            and self._silent_state_reads < BLE_INITIAL_SILENT_READS_MAX
        ):
            return None
        return value

    @property
    def supports_radar(self) -> bool:
        return self._state.has_radar is True

    @property
    def radar_present(self) -> bool | None:
        # None until a 0A carries the radar byte, so a short 0A never removes the radar entities.
        if self._ble_address and self._state.has_radar is None:
            return None
        return self.supports_radar

    @property
    def radar_mode_active(self) -> bool:
        return self.supports_radar and self._state.radar_mode == 1

    @property
    def duration_limits(self) -> dict:
        return AL_DURATION_LIMITS.get(self._state.model_code, AL_DURATION_LIMITS_DEFAULT)

    @property
    def protocol_is_v3(self) -> bool:
        """True when the AK protocol has identified the device as V3.

        Stays False both for non-AK protocols and for AK devices whose
        login response hasn't been parsed yet (i.e. before the first
        successful BLE connect).
        """
        proto = self._protocol
        if isinstance(proto, ScentMarketingAkProtocol):
            return proto.is_v3
        return False

    @property
    def ak_cy_variant(self) -> bool | None:
        """True for an AK device that logged in as "CY_V3.0" (diagnostics)."""
        proto = self._protocol
        if isinstance(proto, ScentMarketingAkProtocol):
            return proto.is_cy_variant
        return None

    @property
    def ai_diffuser_schedule(self) -> dict | None:
        """Parsed AI-Diffuser tables, day masks and mode (diagnostics)."""
        proto = self._protocol
        if isinstance(proto, AiDiffuserProtocol):
            return proto.diagnostics()
        return None

    @property
    def gw_gear_limits(self) -> tuple[int, int, int, int] | None:
        """GW work/pause limits from the last DP 15 report (diagnostics)."""
        proto = self._protocol
        if isinstance(proto, ScentMarketingGwProtocol):
            return proto.gear_limits
        return None

    @property
    def schedule_window_read(self) -> bool:
        """False while a BLE Aroma-Link unit has not reported its window."""
        if self._ble_address and isinstance(self._protocol, AromaLinkBleProtocol):
            return self._schedule_window_read
        return True

    @property
    def schedule_durations_read(self) -> bool:
        """False while a BLE Aroma-Link unit has not reported its durations."""
        if self._ble_address and isinstance(self._protocol, AromaLinkBleProtocol):
            return self._schedule_durations_read or self._durations_unreported
        return True

    @property
    def durations_unreported(self) -> bool:
        """True once an Aroma-Link unit was taken as not reporting durations."""
        return self._durations_unreported

    def require_schedule_read(self) -> None:
        """Refuse a schedule edit that would overwrite values not yet read."""
        if isinstance(self._protocol, AiDiffuserProtocol) and not self._protocol.schedule_known:
            # A slot write carries the whole record (mode, window, gear,
            # pump, days): built from defaults it would wipe the slot.
            raise HomeAssistantError(
                f"{self._ble_name}: the schedule hasn't been read from the "
                "device yet, so this edit would overwrite it with defaults. "
                "Press Refresh, or try again once the device is reachable."
            )
        if not (self.schedule_window_read and self.schedule_durations_read):
            raise HomeAssistantError(
                f"{self._ble_name}: the schedule hasn't been read from the "
                "device yet, so this edit would overwrite it with guesses. "
                "Try again once the device has been reachable for a moment."
            )

    def require_app_mode(self) -> None:
        """Refuse a schedule edit while an Aroma-Link unit is in radar mode."""
        if self.radar_mode_active:
            raise HomeAssistantError(
                f"{self._ble_name}: the unit is in radar mode, which doesn't "
                "use the schedule. Switch Mode to App first."
            )

    @property
    def supports_cloud(self) -> bool:
        return self._cloud is not None and self._cloud_device_id is not None

    @property
    def connection_mode(self) -> str:
        if self._ble_address:
            return "ble"
        if self.supports_cloud and self._cloud and self._cloud.authenticated:
            return "cloud"
        return "offline"

    @property
    def available(self) -> bool:
        return self.connection_mode != "offline"

    def register_state_callback(self, callback: callable) -> Callable[[], None]:
        """Add a state listener; return a function that removes it."""
        self._state_callbacks.append(callback)

        def _remove() -> None:
            if callback in self._state_callbacks:
                self._state_callbacks.remove(callback)

        return _remove

    def _notify_state_changed(self) -> None:
        for cb in list(self._state_callbacks):
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in state callback")

    async def _advance_oil_estimate(self) -> None:
        """Account for oil consumed up to now before mutating state."""
        if self.oil_estimator is not None:
            await self.oil_estimator.async_advance()

    def _schedule_oil_advance(self) -> None:
        """Schedule an oil estimate update from a synchronous BLE callback."""
        if self.oil_estimator is None or not self.oil_estimator.enabled:
            return
        if self._hass is not None:
            self._hass.async_create_task(self.oil_estimator.async_advance())
        else:
            try:
                asyncio.get_running_loop().create_task(
                    self.oil_estimator.async_advance()
                )
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # BLE connect-on-demand
    # ------------------------------------------------------------------
