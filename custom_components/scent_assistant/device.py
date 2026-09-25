"""Unified device manager for scent diffusers.

Uses connect-on-demand for BLE: connects only when sending a command,
then disconnects after a short idle period. This frees the BLE adapter
for other devices.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from bleak import BleakClient, BleakScanner, BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import (
    DeviceType,
    CLOUD_SCHEDULE_REFRESH_EVERY,
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
    get_protocol,
    detect_device_type,
)
from .protocol_cloud import AromaLinkCloudClient

_LOGGER = logging.getLogger(__name__)

# How long to wait for a Scent Tech timer table / write acknowledgement.
SCENT_TECH_REPLY_TIMEOUT = 4.0

# Disconnect BLE after this many seconds of inactivity
BLE_IDLE_DISCONNECT_SECONDS = 10
# Cooldown after a failed connect / write before we try again, so a
# stuck device gets a chance to recover instead of being hammered.
BLE_FAILURE_COOLDOWN_SECONDS = 3.0
# bleak_retry_connector max-attempts. HA's bluetooth stack already
# layers its own retries on top of ours, so keeping this low avoids
# 6-8 rapid connect attempts that can wedge some firmwares.
BLE_CONNECT_MAX_ATTEMPTS = 2
# Aroma-Link 0A clock drift from local time that triggers a 57 17 on refresh.
AL_CLOCK_DRIFT_SECONDS = 120
# Default run time for the momentary "Diffuse Now" button. Adjustable
# per device via the Momentary Duration number entity and persisted
# locally in the config entry options.
DEFAULT_MOMENTARY_SECONDS = 30


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
    ) -> None:
        # HomeAssistant reference, used to fetch a cached BLEDevice via
        # the core bluetooth integration before opening a connection.
        # Optional so the manager can still be unit-tested without HA.
        self._hass = hass
        # Detection metadata from the config flow — populated only for
        # Scent Marketing family devices.
        self._sm_metadata = sm_metadata or {}
        # Optional 4-char ASCII password for Scent Marketing GW devices.
        # Sent proactively after every BLE connect.
        self._gw_password = gw_password or None
        # Trace ring-buffer for the diagnostics download.
        self._recent_notifications: list[str] = []
        self._recent_commands: list[str] = []
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
        # Sub-command byte of the last NACK reply.
        self._ble_nack: int | None = None
        self._ble_time_acked = False
        # Monotonic timestamp of the last failed BLE connect/write —
        # used to back off after errors instead of hammering a stuck
        # device (which can wedge a V3 diffuser's GATT stack badly
        # enough that even the official app can't reconnect until a
        # power cycle, per @Mins95's 2026-06-01 report).
        self._ble_last_failure_ts: float = 0.0

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
        # Wall-clock time of the last BLE notification that changed
        # state. Lets a user tell a fresh reading from a stale one
        # without the entity flapping to unavailable (#32).
        self._ble_last_update: datetime | None = None
        self._week_query_sent = False
        # Configured durations read from the device since the entry loaded.
        self._schedule_durations_read = False
        # Schedule window read from the device since the entry loaded.
        self._schedule_window_read = False

        # Momentary diffusion ("Diffuse Now" button): power on, then
        # auto-off after this many seconds via a background task.
        self.momentary_seconds: int = DEFAULT_MOMENTARY_SECONDS
        self._momentary_task: asyncio.Task | None = None

        # Scent Tech timer writes are read-modify-write on one record, so
        # they must not interleave; the events let a write wait for the
        # device's 0x88 table and 0x94 acknowledgement.
        self._timer_write_lock = asyncio.Lock()
        self._timers_received = asyncio.Event()
        self._timer_write_acked = asyncio.Event()

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
        }
        base = mapping.get(self._device_type, self._device_type.value)
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
    def schedule_window_read(self) -> bool:
        """False while a BLE Aroma-Link unit has not reported its window."""
        if self._ble_address and isinstance(self._protocol, AromaLinkBleProtocol):
            return self._schedule_window_read
        return True

    @property
    def schedule_durations_read(self) -> bool:
        """False while a BLE Aroma-Link unit has not reported its durations."""
        if self._ble_address and isinstance(self._protocol, AromaLinkBleProtocol):
            return self._schedule_durations_read
        return True

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

    def register_state_callback(self, callback: callable) -> None:
        self._state_callbacks.append(callback)

    def _notify_state_changed(self) -> None:
        for cb in self._state_callbacks:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in state callback")

    # ------------------------------------------------------------------
    # BLE connect-on-demand
    # ------------------------------------------------------------------

    async def _ble_connect(self) -> bool:
        """Connect to BLE if not already connected. Auto-disconnects after idle."""
        if not self._ble_address:
            return False

        # Cancel pending disconnect
        if self._ble_disconnect_task and not self._ble_disconnect_task.done():
            self._ble_disconnect_task.cancel()

        if self._ble_connected and self._ble_client and self._ble_client.is_connected:
            self._schedule_disconnect()
            return True

        # Cooldown after a recent failure: skip the connect attempt
        # entirely and let the next user action retry. Prevents back-
        # to-back retries from wedging a V3 firmware whose GATT stack
        # is already in a bad state.
        loop = asyncio.get_event_loop()
        since_failure = loop.time() - self._ble_last_failure_ts
        if 0 < since_failure < BLE_FAILURE_COOLDOWN_SECONDS:
            _LOGGER.debug(
                "BLE connect to %s skipped — within failure cooldown (%.1fs left)",
                self._ble_name, BLE_FAILURE_COOLDOWN_SECONDS - since_failure,
            )
            return False

        async with self._ble_lock:
            # Double-check after acquiring lock
            if self._ble_connected and self._ble_client and self._ble_client.is_connected:
                self._schedule_disconnect()
                return True
            since_failure = loop.time() - self._ble_last_failure_ts
            if 0 < since_failure < BLE_FAILURE_COOLDOWN_SECONDS:
                _LOGGER.debug(
                    "BLE connect to %s skipped after waiting for the lock: within failure cooldown",
                    self._ble_name,
                )
                return False

            try:
                _LOGGER.debug("BLE connecting to %s", self._ble_name)
                # Under HA, a MAC string target raises AttributeError.
                target = self._ble_address
                if self._hass is not None:
                    cached = bluetooth.async_ble_device_from_address(
                        self._hass, self._ble_address, connectable=True,
                    )
                    if cached is not None:
                        target = cached
                    else:
                        _LOGGER.warning(
                            "BLE connect failed for %s: no connectable adapter or proxy sees it",
                            self._ble_name,
                        )
                        self._ble_last_failure_ts = loop.time()
                        return False
                # Use bleak_retry_connector for robust connection
                # establishment (handles transient failures with
                # exponential backoff and is required by HA's bluetooth
                # stack to interoperate with bluetooth-proxy / ESPHome).
                self._ble_client = await establish_connection(
                    BleakClient,
                    target,
                    self._ble_name or self._ble_address,
                    max_attempts=BLE_CONNECT_MAX_ATTEMPTS,
                )
                self._ble_connected = True
                if isinstance(self._protocol, AromaLinkBleProtocol):
                    self._protocol.reset_rx()

                # Subscribe to notifications for responses. Without these
                # the AK family can't sync state back to HA, so a silent
                # failure here is worth surfacing — bump it from debug to
                # warning so it shows up in logs and diagnostics. Track
                # whether the subscription took, so the disconnect path
                # can call stop_notify before tearing the link down.
                try:
                    await self._ble_client.start_notify(
                        self._protocol.notify_char_uuid, self._on_ble_notification
                    )
                    self._ble_notify_subscribed = True
                except Exception as err:
                    self._ble_notify_subscribed = False
                    _LOGGER.warning(
                        "BLE start_notify failed on %s (%s): %s",
                        self._ble_name, self._protocol.notify_char_uuid, err,
                    )

                # Scent Marketing AK family — PIN 8888 login must precede
                # every other write, otherwise the device drops them
                # silently. The response also tells us whether to use the
                # V2 or V3 command set; we wait briefly for it before
                # sending follow-ups so `_v3_mode` is set in time. Once
                # login completes, mirror the official app and read back
                # schedule / power / firmware state so HA entities
                # reflect what the device actually has stored rather
                # than starting from a blank optimistic guess.
                if isinstance(self._protocol, ScentMarketingAkProtocol):
                    self._protocol.reset_login_state()
                    try:
                        await self._ble_send(self._protocol.build_login_primary())
                        # Give the device time to respond — the
                        # notification handler will set _v3_mode.
                        await asyncio.sleep(0.5)
                        # V3 devices need a secondary login. Some answer the
                        # primary on its own (so is_v3 is already known
                        # here); others — e.g. christiandion's Flair Tower
                        # (#8) — stay silent until they receive the
                        # secondary. Gating the secondary on is_v3 then
                        # deadlocks those: no secondary → no login response
                        # → no state read-back → device looks uncontrollable.
                        # So send the secondary whenever the device is
                        # known-V3 OR hasn't answered at all yet; only skip
                        # it once a device has positively identified as V2.
                        if self._protocol.is_v3 or not self._protocol.login_completed:
                            await self._ble_send(self._protocol.build_login_secondary_v3())
                            await asyncio.sleep(0.3)
                        # Time sync must precede the read-back queries
                        # on V3 devices — without it the firmware ACKs
                        # the writes at the GATT layer but never pushes
                        # the corresponding 4A/42/48/... responses. We
                        # send on every connect (not gated by the
                        # _ble_has_synced_time flag below) since each
                        # AK session needs the device in a known time
                        # state for reads to work.
                        ak_time = self._protocol.build_time_sync()
                        if ak_time:
                            await self._ble_send(ak_time)
                            await asyncio.sleep(0.2)
                            self._ble_has_synced_time = True
                        # State read-back: fire queries; responses are
                        # parsed asynchronously by parse_notification.
                        for frame in self._protocol.build_read_schedule_queries():
                            await self._ble_send(frame)
                            await asyncio.sleep(0.15)
                        # Grade table (C3 → 47): the device only returns it
                        # when asked right after the schedule read-back has
                        # finished — mirroring the official app — and before
                        # the label/oil/firmware reads. Let the 4A/43 schedule
                        # push drain first, then ask, then let the 47 land.
                        # Ordering + settle time are load-bearing here
                        # (@Mins95's #8 test: C3 after the oil reads returned
                        # no table; C3 here does).
                        grade_query = self._protocol.build_grade_table_query()
                        if grade_query:
                            await asyncio.sleep(0.5)
                            await self._ble_send(grade_query)
                            await asyncio.sleep(0.4)
                        for frame in self._protocol.build_read_state_queries():
                            await self._ble_send(frame)
                            await asyncio.sleep(0.15)
                    except (BleakError, asyncio.TimeoutError, OSError) as err:
                        # Mid-handshake BLE failure leaves the link in
                        # an unknown state — tear down rather than
                        # keeping a half-broken connection scheduled
                        # for idle disconnect, since reusing it tends
                        # to make the V3 firmware's GATT stack wedge.
                        _LOGGER.warning(
                            "Scent Marketing AK handshake failed on %s: %s",
                            self._ble_name, err,
                        )
                        await self._teardown_ble_client()
                        self._ble_last_failure_ts = loop.time()
                        return False

                # Aromely Aro Max — the app opens every session with a
                # session-start frame, then a time sync, then reads back
                # the name / label / schedule. We mirror that so HA starts
                # from the device's real stored state.
                if isinstance(self._protocol, AromelyAroMaxProtocol):
                    try:
                        await self._ble_send(self._protocol.build_session_start())
                        await asyncio.sleep(0.2)
                        ar_time = self._protocol.build_time_sync()
                        if ar_time:
                            await self._ble_send(ar_time)
                            await asyncio.sleep(0.2)
                            self._ble_has_synced_time = True
                        for frame in self._protocol.build_read_queries():
                            await self._ble_send(frame)
                            await asyncio.sleep(0.15)
                    except (BleakError, asyncio.TimeoutError, OSError) as err:
                        _LOGGER.warning(
                            "Aromely Aro Max handshake failed on %s: %s",
                            self._ble_name, err,
                        )
                        await self._teardown_ble_client()
                        self._ble_last_failure_ts = loop.time()
                        return False

                # Scent Tech / ScentLab — both apps open every session with
                # a password-status query, the password when one is set,
                # then an enquiry and a capability query. The device
                # ignores commands that arrive before that sequence.
                if isinstance(self._protocol, ScentTechProtocol):
                    try:
                        for frame in self._protocol.build_session_frames(self._gw_password):
                            await self._ble_send(frame)
                            await asyncio.sleep(0.5 if frame[3] in (0x09, 0x51) else 0.25)
                    except (BleakError, asyncio.TimeoutError, OSError) as err:
                        _LOGGER.warning(
                            "Scent Tech handshake failed on %s: %s",
                            self._ble_name, err,
                        )
                        await self._teardown_ble_client()
                        self._ble_last_failure_ts = loop.time()
                        return False

                # Time sync on first connection of this session (skipped for
                # protocols that don't support it).
                if not self._ble_has_synced_time:
                    time_sync = self._protocol.build_time_sync()
                    if time_sync:
                        await self._ble_send(time_sync)
                        _LOGGER.info("BLE connected + time synced: %s", self._ble_name)
                    else:
                        _LOGGER.info("BLE connected: %s", self._ble_name)
                    self._ble_has_synced_time = True

                # GW family password handshake. We send unconditionally —
                # the firmware ignores a password write on unprotected
                # devices, and there's no reliable pre-connect way to tell
                # which mode the device is in.
                if self._gw_password and isinstance(
                    self._protocol, ScentMarketingGwProtocol
                ):
                    try:
                        await self._ble_send(self._protocol.build_password(self._gw_password))
                    except Exception as err:
                        _LOGGER.debug("Scent Marketing GW: password send failed: %s", err)

                self._schedule_disconnect()
                return True

            except (BleakError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.warning("BLE connect failed for %s: %s", self._ble_name, err)
                await self._teardown_ble_client()
                self._ble_last_failure_ts = loop.time()
                return False

    def _schedule_disconnect(self) -> None:
        """Schedule BLE disconnect after idle period."""
        if self._ble_disconnect_task and not self._ble_disconnect_task.done():
            self._ble_disconnect_task.cancel()
        self._ble_disconnect_task = asyncio.ensure_future(self._delayed_disconnect())

    async def _delayed_disconnect(self) -> None:
        """Disconnect BLE after idle timeout."""
        await asyncio.sleep(BLE_IDLE_DISCONNECT_SECONDS)
        async with self._ble_lock:
            await self._teardown_ble_client(reason="idle")

    async def _teardown_ble_client(self, *, reason: str = "error") -> None:
        """Cleanly release the BLE client.

        Always stop notifications before disconnecting (some firmwares —
        notably the Scent Marketing V3 ESP32 — get into a stuck GATT
        state if a client disconnects without unsubscribing first), then
        disconnect, then drop the client reference so the next connect
        attempt starts fresh. Safe to call when the client is already
        gone; logs at debug.

        Caller must hold `_ble_lock` if called from anywhere other than
        the connect / delayed-disconnect paths (which already do).
        """
        client = self._ble_client
        if client is not None and self._ble_notify_subscribed:
            try:
                await client.stop_notify(self._protocol.notify_char_uuid)
            except Exception as err:
                _LOGGER.debug(
                    "BLE stop_notify on %s failed during teardown (%s): %s",
                    self._ble_name, reason, err,
                )
        self._ble_notify_subscribed = False
        if client is not None:
            try:
                if client.is_connected:
                    await client.disconnect()
                    _LOGGER.debug("BLE disconnected (%s): %s", reason, self._ble_name)
            except Exception as err:
                _LOGGER.debug(
                    "BLE disconnect on %s failed during teardown (%s): %s",
                    self._ble_name, reason, err,
                )
        self._ble_client = None
        self._ble_connected = False
        self._ble_write_response = None

    def _write_needs_response(self) -> bool:
        """Decide whether writes to this device need a GATT response.

        Most supported units declare `WRITE` on their write
        characteristic and answer a with-response write happily. Some
        rebadged ones don't: the YOOAI-OEM Aromely variant sold as
        "SPACE-BLE" declares *only* `write-without-response` on FFE2, so
        every with-response write is rejected with GATT error 3 and the
        handshake never completes.

        So honour whatever the characteristic actually declares, and
        fall back to with-response whenever the GATT table can't be
        resolved — that is the behaviour every currently-supported
        device was verified against.
        """
        if self._ble_write_response is not None:
            return self._ble_write_response
        with_response = True
        try:
            char = self._ble_client.services.get_characteristic(
                self._protocol.write_char_uuid
            )
            if char is not None:
                props = [str(p).lower() for p in (char.properties or [])]
                if props and "write" not in props:
                    with_response = False
                    _LOGGER.debug(
                        "BLE write char %s on %s is write-without-response "
                        "only (props=%s) — writing without response",
                        self._protocol.write_char_uuid, self._ble_name, props,
                    )
        except Exception as err:
            _LOGGER.debug(
                "Could not resolve write properties for %s on %s: %s",
                self._protocol.write_char_uuid, self._ble_name, err,
            )
        self._ble_write_response = with_response
        return with_response

    async def _ble_send(self, data: bytes) -> bool:
        """Send a command via BLE.

        Protocols may return more than one on-wire chunk per command — the
        Scent Marketing GW family for instance needs (nonce, seq)-prefixed
        18-byte chunks. We delegate the split decision to the protocol and
        write each chunk sequentially.

        Raises `BleakError`/`asyncio.TimeoutError`/`OSError` on write
        failure (GATT-133 on Android surfaces here too). Callers wrap
        this so they can decide whether to tear the BLE client down —
        leaving a half-broken handle around made V3 firmwares unable
        to be reached by *any* client until a power cycle.
        """
        if not self._ble_client or not self._ble_client.is_connected:
            return False
        chunks = self._protocol.wire_chunks(data) if data else []
        if chunks:
            self._recent_commands.append(data.hex())
            if len(self._recent_commands) > 10:
                del self._recent_commands[0]
        with_response = self._write_needs_response()
        for chunk in chunks:
            await self._ble_client.write_gatt_char(
                self._protocol.write_char_uuid, chunk, response=with_response
            )
        return True

    async def _ble_execute(self, data: bytes) -> bool:
        """Connect, send command, schedule disconnect."""
        if not await self._ble_connect():
            return False
        self._ble_nack = None
        try:
            success = await self._ble_send(data)
        except (BleakError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.warning("BLE write failed on %s: %s", self._ble_name, err)
            self._ble_last_failure_ts = asyncio.get_event_loop().time()
            async with self._ble_lock:
                await self._teardown_ble_client(reason="write-failure")
            return False
        # Wait briefly for notification response
        await asyncio.sleep(1.0)
        write_sub = getattr(self._protocol, "write_sub", None)
        if (
            write_sub is not None
            and self._ble_nack is not None
            and write_sub(data) == self._ble_nack
        ):
            _LOGGER.warning(
                "BLE write failed on %s: NACK for 57 %02X",
                self._ble_name, self._ble_nack,
            )
            return False
        return success

    def _on_ble_notification(self, sender: int, data: bytearray) -> None:
        """Handle incoming BLE notification."""
        raw = bytes(data)
        # Keep a short ring-buffer of raw frames for the diagnostics export.
        self._recent_notifications.append(raw.hex())
        if len(self._recent_notifications) > 20:
            del self._recent_notifications[0]
        updates = self._protocol.parse_notification(raw)
        if not updates:
            return
        if "nack" in updates:
            self._ble_nack = updates["nack"]

        changed = False
        if "power" in updates:
            self._state.power = updates["power"]
            changed = True
        if "fan" in updates:
            self._state.fan = updates["fan"]
            changed = True
        # Aroma-Link 53 09 status 0 reads "idle" even while the unit is off.
        if "phase" in updates and not (updates["phase"] == "idle" and self._state.power is False):
            self._state.phase = updates["phase"]
            changed = True
        if "work_seconds" in updates:
            self._state.work_seconds = updates["work_seconds"]
            changed = True
        if "pause_seconds" in updates:
            self._state.pause_seconds = updates["pause_seconds"]
            changed = True
        if "work_seconds" in updates and "pause_seconds" in updates:
            self._schedule_durations_read = True
        if "start_hour" in updates:
            self._state.start_hour = updates["start_hour"]
            self._state.start_minute = updates.get("start_minute", 0)
            self._schedule_window_read = True
            changed = True
        if "end_hour" in updates:
            self._state.end_hour = updates["end_hour"]
            self._state.end_minute = updates.get("end_minute", 59)
            changed = True
        if "level" in updates:
            self._state.level = updates["level"]
            changed = True
        if "battery" in updates:
            self._state.battery = updates["battery"]
            changed = True
        if "has_fan" in updates:
            self._state.has_fan = updates["has_fan"]
            changed = True
        if "rgb_on" in updates:
            self._state.rgb_on = updates["rgb_on"]
            changed = True
        if "rgb_color" in updates:
            self._state.rgb_color = updates["rgb_color"]
            changed = True
        # Scent Marketing GW family — new state fields
        if "lock" in updates:
            self._state.lock = updates["lock"]
            changed = True
        if "oil_remaining" in updates:
            self._state.oil_remaining = updates["oil_remaining"]
            changed = True
        if "work_remaining" in updates:
            self._state.work_remaining = updates["work_remaining"]
            changed = True
        if "pause_remaining" in updates:
            self._state.pause_remaining = updates["pause_remaining"]
            changed = True
        if "device_clock" in updates:
            self._state.device_clock = updates["device_clock"]
            changed = True
        for _oil_field in (
            "oil_current_ml", "oil_max_ml",
            "oil_consumption_mlh",
            "schedule_custom_mode", "grade_table",
        ):
            if _oil_field in updates:
                setattr(self._state, _oil_field, updates[_oil_field])
                changed = True
        if "light_on" in updates:
            self._state.light_on = updates["light_on"]
            changed = True
        if "device_name" in updates:
            self._state.device_name = updates["device_name"]
            changed = True
        if "password_required" in updates:
            self._state.password_required = updates["password_required"]
            changed = True
        if "firmware_version" in updates:
            self._state.firmware_version = updates["firmware_version"]
            changed = True
        # Scent Marketing AK — read-back fields
        if "intensity" in updates:
            self._state.intensity = updates["intensity"]
            changed = True
        if "weekday_mask" in updates:
            self._state.weekday_mask = updates["weekday_mask"]
            changed = True
        if "schedule_slot" in updates:
            self._state.schedule_slot = updates["schedule_slot"]
            changed = True
        if "device_label" in updates:
            self._state.device_label = updates["device_label"]
            changed = True
        if "model_code" in updates:
            self._state.model_code = updates["model_code"]
            changed = True
        if "schedule_enabled" in updates:
            self._state.schedule_enabled = updates["schedule_enabled"]
            changed = True
        if "pump" in updates:
            self._state.pump = updates["pump"]
            changed = True
        if "device_code" in updates:
            self._state.device_code = updates["device_code"]
            changed = True
        if "week_slots" in updates:
            self._state.week_slots = updates["week_slots"]
            day = updates["week_slots"][datetime.now().weekday()]
            if self._state.device_code in AL_MANY_PUMP_DEVICE_CODES:
                slot = day[0]
            else:
                slot = next((s for s in day if s["enabled"]), day[0])
                self._state.schedule_enabled = slot["enabled"]
            if slot["work_seconds"] > 0:
                self._state.work_seconds = slot["work_seconds"]
            if slot["pause_seconds"] > 0:
                self._state.pause_seconds = slot["pause_seconds"]
            if slot["work_seconds"] > 0 and slot["pause_seconds"] > 0:
                self._schedule_durations_read = True
            changed = True
        # Scent Tech timer table / write acknowledgement
        if "timer_slots" in updates:
            self._state.timer_slots = updates["timer_slots"]
            self._timers_received.set()
            changed = True
        if updates.get("timer_write_ack"):
            self._timer_write_acked.set()
        if updates.get("ack") == AL_SUB_TIME_SYNC:
            self._ble_time_acked = True

        # Derive oil days-remaining from the latest oil + schedule state.
        # The 0x50 frame's raw value doesn't match the official app, which
        # computes it, so we mirror that math here (see _recompute_oil_days).
        if self._recompute_oil_days():
            changed = True

        if changed:

            self._ble_last_update = datetime.now().astimezone()

            self._notify_state_changed()

    def _recompute_oil_days(self) -> bool:
        """Derive estimated oil days-remaining the way the official app does.

            days = current_ml / (consumption_mlh × active_hours_per_day × duty)

        where ``duty = work / (work + pause)``. In Custom mode the work/pause
        durations are the live values, so this is exact. In Level mode the
        device runs an internal grade→work/pause table; we read it from the
        0x47 frame (C3 query) and index it by the selected intensity. If that
        table hasn't been read yet, Level-mode days stays unavailable rather
        than showing a wrong number (the raw value in the 0x50 frame disagreed
        with the app: 836 d vs 293 d, @Mins95 #8).

        Returns True when the stored value changed.
        """
        s = self._state
        prev = s.oil_days_remaining
        new: int | None = None
        cur = s.oil_current_ml
        cons = s.oil_consumption_mlh
        if cur and cur > 0 and cons and cons > 0:
            sh, sm, eh, em = (
                s.start_hour, s.start_minute, s.end_hour, s.end_minute,
            )
            if None not in (sh, sm, eh, em):
                minutes = (eh * 60 + em) - (sh * 60 + sm)
                if minutes <= 0:  # window wraps past midnight
                    minutes += 24 * 60
                hours = minutes / 60.0
                # Duty cycle: Custom mode carries the real work/pause; Level
                # mode uses the device grade table (0x47), indexed by the
                # selected intensity.
                work = pause = None
                if s.schedule_custom_mode and s.work_seconds and s.pause_seconds:
                    work, pause = s.work_seconds, s.pause_seconds
                elif (
                    s.schedule_custom_mode is False
                    and s.grade_table
                    and s.intensity
                ):
                    idx = s.intensity - 1
                    if 0 <= idx < len(s.grade_table) and s.grade_table[idx]:
                        work, pause = s.grade_table[idx]
                if work and pause and hours > 0:
                    duty = work / (work + pause)
                    daily = cons * hours * duty
                    if daily > 0:
                        new = round(cur / daily)
        if new != prev:
            s.oil_days_remaining = new
            return True
        return False

    # ------------------------------------------------------------------
    # Commands (BLE first, cloud fallback)
    # ------------------------------------------------------------------

    async def set_power(self, on: bool) -> bool:
        """Turn device on or off."""
        # Try BLE
        if self._ble_address:
            cmd = self._protocol.build_power(on)
            if await self._ble_execute(cmd):
                self._state.power = on
                # A phase the unit already reported wins over an assumed idle.
                if not on or self._state.phase in ("off", "unknown"):
                    self._state.phase = "idle" if on else "off"
                self._notify_state_changed()
                return True

        # Cloud fallback
        if self.supports_cloud and self._cloud:
            success = await self._cloud.set_power(self._cloud_device_id, on)
            if success:
                self._state.power = on
                if not on or self._state.phase in ("off", "unknown"):
                    self._state.phase = "idle" if on else "off"
                self._notify_state_changed()
            return success

        return False

    async def momentary_diffuse(self) -> bool:
        """Run the diffuser, optionally switching it off after a delay.

        There is no native one-shot command in the Aroma-Link protocol
        (verified against the decompiled official app), so this is
        power-on followed by an optional delayed power-off task. Pressing
        again while a run is active restarts the countdown.

        A Momentary Duration of 0 disables the delayed power-off, leaving
        the diffuser powered on so its onboard schedule can continue.
        """
        if self._momentary_task and not self._momentary_task.done():
            self._momentary_task.cancel()
        self._momentary_task = None

        self._ble_nack = None
        powered = await self.set_power(True)
        # A NACKed power-on still arms the power-off: a running unit can clog.
        if not powered and self._ble_nack != AL_SUB_POWER:
            return False

        if self.momentary_seconds > 0:
            self._momentary_task = asyncio.ensure_future(
                self._momentary_off_later(self.momentary_seconds)
            )

        return powered

    async def _momentary_off_later(self, delay: int) -> None:
        await asyncio.sleep(delay)
        if not await self.set_power(False):
            _LOGGER.warning(
                "Momentary diffusion on %s: auto power-off failed — "
                "the diffuser may still be running", self.name,
            )

    async def set_fan(self, on: bool) -> bool:
        """Turn fan on or off (Aroma-Link + Scent Marketing AK)."""
        if not self._ble_address:
            return False
        proto = self._protocol
        if isinstance(proto, (AromaLinkBleProtocol, ScentMarketingAkProtocol, AromelyAroMaxProtocol)):
            cmd = proto.build_fan(on)
            if await self._ble_execute(cmd):
                self._state.fan = on
                self._notify_state_changed()
                return True
        return False

    async def set_lock(self, on: bool) -> bool:
        """Toggle child-lock (Scent Marketing AK + GW + GW-XOR)."""
        if not self._ble_address:
            return False
        proto = self._protocol
        if isinstance(proto, (ScentMarketingAkProtocol, ScentMarketingGwProtocol)):
            cmd = proto.build_lock(on)
            if await self._ble_execute(cmd):
                self._state.lock = on
                self._notify_state_changed()
                return True
        return False

    async def set_lamp(self, on: bool) -> bool:
        """Toggle auxiliary lamp (Scent Marketing AK lamp-bit, GW DP-11 light)."""
        if not self._ble_address:
            return False
        proto = self._protocol
        if isinstance(proto, ScentMarketingAkProtocol):
            cmd = proto.build_lamp(on)
        elif isinstance(proto, ScentMarketingGwProtocol):
            cmd = proto.build_light(on)
        else:
            return False
        if await self._ble_execute(cmd):
            self._state.light_on = on
            self._notify_state_changed()
            return True
        return False

    async def set_level(self, level: int) -> bool:
        """Set Scentiment spray intensity (1-3)."""
        if not isinstance(self._protocol, ScentimentProtocol) or not self._ble_address:
            return False
        cmd = self._protocol.build_set_level(level)
        if await self._ble_execute(cmd):
            self._state.level = level
            self._notify_state_changed()
            return True
        return False

    async def set_schedule_enabled(self, enabled: bool) -> bool:
        """Enable or disable the active V3 schedule/program.

        On V3 devices the program-enabled flag is a separate control
        from Power: a V3 diffuser can be powered on with the program
        disabled, in which case it won't spray. We re-apply the
        currently cached schedule with the enabled bit flipped — same
        approach as `set_intensity`, since there's no standalone
        program-enable opcode in @Mins95's captures.

        On V2 this method delegates to `set_power`, because V2's
        firmware treats the schedule-enabled bit and the power
        concept as the same toggle.
        """
        if not isinstance(self._protocol, ScentMarketingAkProtocol):
            return False
        if not self._protocol.is_v3:
            return await self.set_power(enabled)
        self._state.schedule_enabled = enabled
        self._notify_state_changed()
        if self._ble_address:
            return await self._write_schedule_to_device(enabled=enabled)
        return True

    async def set_intensity(self, intensity: int) -> bool:
        """Set Scent Marketing AK spray intensity.

        The AK protocol bundles intensity into schedule frames (no
        dedicated opcode is known), so this stores the value locally and
        the next schedule write will pick it up. The Number entity also
        triggers a schedule re-write so the change takes effect
        immediately even when the user doesn't separately touch a Start
        Time / End Time entity.
        """
        if not isinstance(self._protocol, ScentMarketingAkProtocol):
            return False
        # Clamp to the firmware-accepted range. V2 caps at 10, V3 at 20.
        max_value = 20 if self._protocol.is_v3 else 10
        clamped = max(0, min(max_value, int(intensity)))
        self._state.intensity = clamped
        self._notify_state_changed()
        # Push the current schedule with the new intensity so the change
        # is observable on the device immediately. Intensity is the
        # grade in the device's *Level* mode, so a deliberate intensity
        # change selects Level mode (work/pause come from the device's
        # grade table). Adjusting Work/Pause Duration switches back to
        # Custom mode — see `_write_schedule_to_device`.
        if self._ble_address:
            return await self._write_schedule_to_device(custom_mode=False)
        return True

    async def set_rgb_color(self, r: int, g: int, b: int) -> bool:
        """Set Scentiment RGB LED color."""
        if not isinstance(self._protocol, ScentimentProtocol) or not self._ble_address:
            return False
        cmd = self._protocol.build_set_rgb_color(r, g, b)
        if await self._ble_execute(cmd):
            self._state.rgb_color = (r, g, b)
            self._notify_state_changed()
            return True
        return False

    async def set_rgb_led(self, on: bool) -> bool:
        """Turn Scentiment RGB LED on or off."""
        if not isinstance(self._protocol, ScentimentProtocol) or not self._ble_address:
            return False
        cmd = self._protocol.build_set_rgb_led(on)
        if await self._ble_execute(cmd):
            self._state.rgb_on = on
            self._notify_state_changed()
            return True
        return False

    async def set_schedule_mode(self, custom: bool) -> bool:
        """Explicitly select the AK V3 schedule mode (Custom vs Level).

        Mirrors the official app's mode toggle. Custom honours the Work/Pause
        Duration; Level uses the device's grade table (Intensity selects the
        grade). The implicit mode-follows-control behaviour stays — this just
        lets the user pin the mode directly without nudging a duration or the
        intensity (@Mins95's UX request, #8).
        """
        if not isinstance(self._protocol, ScentMarketingAkProtocol):
            return False
        if not self._protocol.is_v3:
            return False
        self._state.schedule_custom_mode = custom
        self._notify_state_changed()
        if self._ble_address:
            return await self._write_schedule_to_device(custom_mode=custom)
        return True

    async def set_work_duration(self, seconds: int) -> bool:
        """Set the spray work duration and write to device."""
        if not (self.schedule_window_read and self.schedule_durations_read):
            _LOGGER.warning(
                "Schedule write skipped on %s: schedule not read from device yet",
                self._ble_name,
            )
            return False
        previous = self._state.work_seconds
        self._state.work_seconds = seconds
        # Setting an explicit duration means the user wants Custom mode.
        if await self._write_schedule_to_device(custom_mode=True):
            return True
        self._state.work_seconds = previous
        self._notify_state_changed()
        return False

    async def set_pause_duration(self, seconds: int) -> bool:
        """Set the pause duration and write to device."""
        if not (self.schedule_window_read and self.schedule_durations_read):
            _LOGGER.warning(
                "Schedule write skipped on %s: schedule not read from device yet",
                self._ble_name,
            )
            return False
        previous = self._state.pause_seconds
        self._state.pause_seconds = seconds
        if await self._write_schedule_to_device(custom_mode=True):
            return True
        self._state.pause_seconds = previous
        self._notify_state_changed()
        return False

    async def set_schedule(
        self,
        weekday_mask: int,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        work_seconds: int,
        pause_seconds: int,
        enabled: bool | None = None,
    ) -> bool:
        """Set a full schedule on the device."""
        kept = {
            field: getattr(self._state, field) for field in (
                "start_hour", "start_minute", "end_hour", "end_minute",
                "work_seconds", "pause_seconds", "schedule_enabled",
            )
        }
        # Aroma-Link state is today's slot, unchanged by a mask without today.
        restore = (
            isinstance(self._protocol, AromaLinkBleProtocol)
            and not weekday_mask & (1 << datetime.now().weekday())
        )
        self._state.work_seconds = work_seconds
        self._state.pause_seconds = pause_seconds
        self._state.start_hour = start_hour
        self._state.start_minute = start_minute
        self._state.end_hour = end_hour
        self._state.end_minute = end_minute
        if (
            enabled is not None
            and self._ble_address
            and isinstance(self._protocol, AromaLinkBleProtocol)
        ):
            self._state.schedule_enabled = enabled

        # An explicit work/pause schedule means Custom mode.
        result = await self._write_schedule_to_device(
            weekday_mask=weekday_mask, enabled=enabled, custom_mode=True,
        )
        if restore or not result:
            for field, value in kept.items():
                setattr(self._state, field, value)
            self._notify_state_changed()
        return result

    async def _write_schedule_to_device(
        self,
        weekday_mask: int | None = None,
        enabled: bool | None = None,
        custom_mode: bool | None = None,
    ) -> bool:
        """Write the current schedule state to the device.

        `weekday_mask=None` (the default for callers like `set_intensity`
        or `set_schedule_enabled` that aren't changing the day pattern)
        preserves whatever mask was last read back from the device, so a
        one-axis tweak doesn't silently flatten an M-F schedule into
        every-day.

        `custom_mode=None` preserves the device's current V3 schedule mode
        (Custom vs Level); callers that change Work/Pause pass True, and
        `set_intensity` passes False. Ignored on non-AK / V2 devices.
        """
        if weekday_mask is None:
            weekday_mask = self._state.weekday_mask if self._state.weekday_mask is not None else 0x7F
        if enabled is None:
            enabled = self._state.schedule_enabled
            if (
                enabled is None
                or not isinstance(self._protocol, AromaLinkBleProtocol)
                or self._state.device_code in AL_MANY_PUMP_DEVICE_CODES
            ):
                enabled = True
        work = self._state.work_seconds or DEFAULT_WORK_DURATION
        pause = self._state.pause_seconds or DEFAULT_PAUSE_DURATION
        s_h = self._state.start_hour
        s_m = self._state.start_minute
        e_h = self._state.end_hour
        e_m = self._state.end_minute

        # Try BLE
        if self._ble_address:
            cmd = None
            if self._device_type == DeviceType.TUYA_BLE:
                setup = ScheduleSetup(
                    index=0, weekday_mask=weekday_mask, enabled=enabled,
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                    work_seconds=work, pause_seconds=pause,
                )
                cmd = self._protocol.build_schedule([setup])
            elif isinstance(self._protocol, AromaLinkBleProtocol):
                pump = self._state.pump
                if (
                    pump is None
                    or pump > 0x0F
                    or self._state.device_code in AL_MANY_PUMP_DEVICE_CODES
                ):
                    pump = 1
                slot = ScheduleSlot(
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                    enabled=enabled, work_seconds=work, pause_seconds=pause,
                    pump=pump,
                )
                cmd = self._protocol.build_schedule(weekday_mask, [slot])
            elif isinstance(self._protocol, ScentMarketingGwProtocol):
                slot = ScheduleSlot(
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                    enabled=enabled, work_seconds=work, pause_seconds=pause,
                )
                cmd = self._protocol.build_schedule([slot], weekday_mask=weekday_mask)
            elif isinstance(self._protocol, ScentMarketingAkProtocol):
                slot = ScheduleSlot(
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                    enabled=enabled, work_seconds=work, pause_seconds=pause,
                )
                # Pull intensity from state. Default to mid-range for the
                # detected protocol version (V2 caps at 10, V3 at 20).
                if self._state.intensity is not None:
                    level = self._state.intensity
                elif self._protocol.is_v3:
                    level = 10
                else:
                    level = 6
                # Resolve the V3 schedule mode: preserve the device's
                # current mode when the caller didn't specify one.
                if custom_mode is None:
                    resolved_mode = (
                        self._state.schedule_custom_mode
                        if self._state.schedule_custom_mode is not None
                        else True
                    )
                else:
                    resolved_mode = custom_mode
                cmd = self._protocol.build_schedule(
                    slot, weekday_mask=weekday_mask, intensity=level,
                    custom_mode=resolved_mode,
                )
                self._state.schedule_custom_mode = resolved_mode
            elif isinstance(self._protocol, AromelyAroMaxProtocol):
                slot = ScheduleSlot(
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                    enabled=enabled, work_seconds=work, pause_seconds=pause,
                )
                cmd = self._protocol.build_schedule(slot, weekday_mask=weekday_mask)

            if cmd and await self._ble_execute(cmd):
                self._notify_state_changed()
                return True

        # Cloud fallback
        if self.supports_cloud and self._cloud:
            day_indices = [i + 1 for i in range(7) if weekday_mask & (1 << i)]
            success = await self._cloud.set_schedule(
                self._cloud_device_id,
                work_seconds=work, pause_seconds=pause,
                weekdays=day_indices,
                start_time=f"{s_h:02d}:{s_m:02d}",
                end_time=f"{e_h:02d}:{e_m:02d}",
            )
            if success:
                self._notify_state_changed()
            return success

        return False

    async def set_timer_slot(self, slot: int, **changes) -> bool:
        """Change fields of one Scent Tech timer record on the device.

        Reads the table fresh, changes only the given fields of that one
        record and writes the whole record back, so the serial, timer id
        and anything the official app set stay intact. Reads the table
        again afterwards so HA shows what the device actually stored.
        """
        if not isinstance(self._protocol, ScentTechProtocol) or not self._ble_address:
            return False
        async with self._timer_write_lock:
            if not await self._ble_connect():
                return False
            try:
                timers = await self._read_scent_tech_timers()
                if timers is None or slot not in timers:
                    _LOGGER.warning(
                        "Scent Tech %s: timer %s not available for writing",
                        self._ble_name, slot,
                    )
                    return False
                updated = timers[slot].with_changes(**changes)
                if not (0 <= updated.start_minute <= 1439 and 0 <= updated.stop_minute <= 1439):
                    _LOGGER.warning("Scent Tech %s: times must be within one day", self._ble_name)
                    return False
                if not (0 <= updated.run_seconds <= 0xFFFF and 0 <= updated.pause_seconds <= 0xFFFF):
                    _LOGGER.warning("Scent Tech %s: duration out of range", self._ble_name)
                    return False
                self._timer_write_acked.clear()
                await self._ble_send(self._protocol.build_write_timer(updated))
                try:
                    await asyncio.wait_for(
                        self._timer_write_acked.wait(), SCENT_TECH_REPLY_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    _LOGGER.debug("Scent Tech %s: no 0x94 for timer %s", self._ble_name, slot)
                await asyncio.sleep(0.25)
                if await self._read_scent_tech_timers() is None:
                    # No read-back: show what we sent rather than the old value.
                    slots = dict(self._state.timer_slots or {})
                    slots[slot] = updated
                    self._state.timer_slots = slots
                    self._notify_state_changed()
                return True
            except (BleakError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.warning("Scent Tech timer write failed on %s: %s", self._ble_name, err)
                self._ble_last_failure_ts = asyncio.get_event_loop().time()
                async with self._ble_lock:
                    await self._teardown_ble_client(reason="write-failure")
                return False

    async def _read_scent_tech_timers(self) -> dict | None:
        """Ask for the timer table and wait for it. Connection must be open."""
        self._timers_received.clear()
        await self._ble_send(self._protocol.build_query())
        try:
            await asyncio.wait_for(self._timers_received.wait(), SCENT_TECH_REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.debug("Scent Tech %s: no timer table received", self._ble_name)
            return None
        return self._state.timer_slots

    async def async_periodic_refresh(self) -> None:
        """Timer-driven refresh for BLE devices (see BLE_REFRESH_INTERVAL_SECONDS).

        Skipped while a momentary run is active: the run ends with a
        power-off write, and a refresh landing in the middle of it would
        connect for nothing and could reorder the writes.
        """
        if not self.supports_periodic_refresh:
            return
        if self._momentary_task is not None and not self._momentary_task.done():
            return
        try:
            if (
                self._hass is not None
                and not (self._ble_connected and self._ble_client and self._ble_client.is_connected)
                and not bluetooth.async_scanner_devices_by_address(
                    self._hass, self._ble_address, connectable=True,
                )
            ):
                _LOGGER.debug(
                    "Periodic BLE refresh skipped on %s: no connectable adapter or proxy sees it",
                    self._ble_name,
                )
                return
            await self.refresh_state()
        except Exception as err:
            _LOGGER.debug("Periodic BLE refresh failed on %s: %s", self._ble_name, err)

    async def refresh_state(self) -> None:
        """Refresh device state."""
        if self._ble_address:
            if await self._ble_connect():
                try:
                    clock_before = self._state.device_clock
                    await self._ble_send(self._protocol.build_query())
                    await asyncio.sleep(1.0)
                    # Some protocols expose extra read-registers that the
                    # device only reports on demand (e.g. Aroma-Link's oil
                    # level). Query them too when the protocol offers one.
                    oil_query = getattr(self._protocol, "build_oil_query", None)
                    if oil_query is not None:
                        await self._ble_send(oil_query())
                        await asyncio.sleep(0.3)
                    # Configured work/pause durations live in a separate
                    # per-weekday register on Aroma-Link (the status frame
                    # only carries the *remaining* times). We write every
                    # weekday identically, so today's is representative.
                    # Device weekday numbering: 0 = Sun, 1 = Mon … 6 = Sat.
                    freq_query = getattr(self._protocol, "build_work_frequency_query", None)
                    if freq_query is not None:
                        weekday = (datetime.now().weekday() + 1) % 7
                        await self._ble_send(freq_query(weekday))
                        await asyncio.sleep(0.3)
                    # 52 15 fallback: its 324-byte reply floods notifications.
                    week_query = getattr(self._protocol, "build_week_schedule_query", None)
                    if (
                        week_query is not None
                        and not self._schedule_durations_read
                        and not self._week_query_sent
                    ):
                        if await self._ble_send(week_query()):
                            self._week_query_sent = True
                            await asyncio.sleep(0.3)
                    # Only a clock parsed in this refresh counts, compared by
                    # identity; an older one reads as drift.
                    clock = self._state.device_clock
                    if (
                        self._ble_time_acked
                        and clock is not None
                        and clock is not clock_before
                        and abs((datetime.now() - clock).total_seconds()) > AL_CLOCK_DRIFT_SECONDS
                    ):
                        _LOGGER.debug(
                            "Aroma-Link clock on %s reads %s, resyncing",
                            self._ble_name, clock,
                        )
                        await self._ble_send(self._protocol.build_time_sync())
                except (BleakError, asyncio.TimeoutError, OSError) as err:
                    _LOGGER.debug("BLE refresh query failed on %s: %s", self._ble_name, err)
                    self._ble_last_failure_ts = asyncio.get_event_loop().time()
                    async with self._ble_lock:
                        await self._teardown_ble_client(reason="refresh-failure")
            return

        if self.supports_cloud and self._cloud:
            got_schedule_from_status = False
            status = await self._cloud.get_status(self._cloud_device_id)
            if status:
                if "power" in status and status["power"] is not None:
                    self._state.power = status["power"]
                if "phase" in status:
                    self._state.phase = status["phase"]
                # The cloud work-status payload carries the same live
                # countdown the BLE 52 0A frame does (plus oil/battery
                # on devices that report them) — feed it into the same
                # state fields so the sensors work in cloud mode too.
                if status.get("work_remain") is not None:
                    self._state.work_remaining = int(status["work_remain"])
                if status.get("pause_remain") is not None:
                    self._state.pause_remaining = int(status["pause_remain"])
                if status.get("oil_remaining") is not None:
                    self._state.oil_remaining = status["oil_remaining"]
                if status.get("battery") is not None:
                    self._state.battery = status["battery"]
                # The status payload usually carries the configured
                # window and durations too — cheaper and more direct
                # than the separate schedule endpoint (#24).
                for field in (
                    "start_hour", "start_minute", "end_hour", "end_minute",
                    "work_seconds", "pause_seconds",
                ):
                    if field in status:
                        setattr(self._state, field, status[field])
                # The fallback exists to supply the window, so only skip
                # it once we actually have one — a payload carrying just
                # the durations shouldn't suppress it.
                got_schedule_from_status = (
                    "start_hour" in status and "end_hour" in status
                )
                self._notify_state_changed()

            # Only fall back to the dedicated schedule endpoint when the
            # status payload didn't carry the window itself.
            if not got_schedule_from_status:
                await self._refresh_cloud_schedule()

    async def _refresh_cloud_schedule(self) -> None:
        """Read the schedule the device holds back from the cloud.

        `get_status` reports what the device is doing right now but not
        what it is scheduled to do, so without this the Start/End Time
        and duration entities have nothing to restore from and show
        their defaults after a restart — claiming 00:00–23:59 while the
        device happily runs 08:00–21:30.

        The schedule only changes when someone edits it, so this runs on
        the first refresh and then every `CLOUD_SCHEDULE_REFRESH_EVERY`
        polls rather than on every one.
        """
        if not (self.supports_cloud and self._cloud):
            return
        due = (
            self._cloud_schedule_poll_count % CLOUD_SCHEDULE_REFRESH_EVERY == 0
        )
        self._cloud_schedule_poll_count += 1
        if not due:
            return

        # 1=Mon … 7=Sun, matching set_schedule's numbering. We write the
        # same schedule to every weekday, so reading today's is enough.
        weekday = datetime.now().weekday() + 1
        schedule = await self._cloud.get_schedule(self._cloud_device_id, weekday)
        if not schedule:
            return

        for field in (
            "start_hour",
            "start_minute",
            "end_hour",
            "end_minute",
            "work_seconds",
            "pause_seconds",
            "schedule_enabled",
        ):
            if field in schedule:
                setattr(self._state, field, schedule[field])
        self._notify_state_changed()

    async def sync_time(self) -> bool:
        """Sync device clock to current local time (BLE only)."""
        if not self._ble_address:
            return False
        self._ble_has_synced_time = False
        if not await self._ble_connect():
            return False
        # Wait for a handshake in another task; it sends the time frame.
        async with self._ble_lock:
            pass
        if self._ble_has_synced_time:
            return True
        # The link was already open, so the connect sent no time frame.
        frame = self._protocol.build_time_sync()
        self._ble_has_synced_time = await self._ble_execute(frame)
        return self._ble_has_synced_time

    # ------------------------------------------------------------------
    # Startup / Shutdown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Initial setup - query state once."""
        await self.refresh_state()

    async def async_shutdown(self) -> None:
        """Clean up resources."""
        if self._momentary_task and not self._momentary_task.done():
            self._momentary_task.cancel()
        if self._ble_disconnect_task and not self._ble_disconnect_task.done():
            self._ble_disconnect_task.cancel()
        if self._ble_client:
            try:
                if self._ble_client.is_connected:
                    await self._ble_client.disconnect()
            except Exception:
                pass
        if self._cloud and hasattr(self._cloud, "close"):
            await self._cloud.close()
