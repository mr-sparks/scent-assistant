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
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_WORK_DURATION,
    DEFAULT_PAUSE_DURATION,
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
    get_protocol,
    detect_device_type,
)
from .protocol_cloud import AromaLinkCloudClient

_LOGGER = logging.getLogger(__name__)

# Disconnect BLE after this many seconds of inactivity
BLE_IDLE_DISCONNECT_SECONDS = 10
# Cooldown after a failed connect / write before we try again, so a
# stuck device gets a chance to recover instead of being hammered.
BLE_FAILURE_COOLDOWN_SECONDS = 3.0
# bleak_retry_connector max-attempts. HA's bluetooth stack already
# layers its own retries on top of ours, so keeping this low avoids
# 6-8 rapid connect attempts that can wedge some firmwares.
BLE_CONNECT_MAX_ATTEMPTS = 2
# Default run time for the momentary "Diffuse Now" button. Adjustable
# per device via the Momentary Duration number entity (not persisted
# across HA restarts).
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
        self._ble_lock = asyncio.Lock()
        self._ble_disconnect_task: asyncio.Task | None = None
        self._ble_has_synced_time = False
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

        # State
        self._state = DiffuserState()
        self._state_callbacks: list[callable] = []

        # Momentary diffusion ("Diffuse Now" button): power on, then
        # auto-off after this many seconds via a background task.
        self.momentary_seconds: int = DEFAULT_MOMENTARY_SECONDS
        self._momentary_task: asyncio.Task | None = None

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

            try:
                _LOGGER.debug("BLE connecting to %s", self._ble_name)
                # Prefer the BLEDevice cached by HA's bluetooth
                # integration (it carries the right adapter / proxy
                # routing info); fall back to a plain MAC string if the
                # device hasn't been observed recently.
                target = self._ble_address
                if self._hass is not None:
                    cached = bluetooth.async_ble_device_from_address(
                        self._hass, self._ble_address, connectable=True,
                    )
                    if cached is not None:
                        target = cached
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
        for chunk in chunks:
            await self._ble_client.write_gatt_char(
                self._protocol.write_char_uuid, chunk, response=True
            )
        return True

    async def _ble_execute(self, data: bytes) -> bool:
        """Connect, send command, schedule disconnect."""
        if not await self._ble_connect():
            return False
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

        changed = False
        if "power" in updates:
            self._state.power = updates["power"]
            changed = True
        if "fan" in updates:
            self._state.fan = updates["fan"]
            changed = True
        if "phase" in updates:
            self._state.phase = updates["phase"]
            changed = True
        if "work_seconds" in updates:
            self._state.work_seconds = updates["work_seconds"]
            changed = True
        if "pause_seconds" in updates:
            self._state.pause_seconds = updates["pause_seconds"]
            changed = True
        if "start_hour" in updates:
            self._state.start_hour = updates["start_hour"]
            self._state.start_minute = updates.get("start_minute", 0)
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

        if changed:
            self._notify_state_changed()

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
                self._state.phase = "idle" if on else "off"
                self._notify_state_changed()
                return True

        # Cloud fallback
        if self.supports_cloud and self._cloud:
            success = await self._cloud.set_power(self._cloud_device_id, on)
            if success:
                self._state.power = on
                self._state.phase = "idle" if on else "off"
                self._notify_state_changed()
            return success

        return False

    async def momentary_diffuse(self) -> bool:
        """Run the diffuser for `momentary_seconds`, then switch it off.

        There is no native one-shot command in the Aroma-Link protocol
        (verified against the decompiled official app), so this is
        power-on followed by a delayed power-off task. Pressing again
        while a run is active restarts the countdown.
        """
        if self._momentary_task and not self._momentary_task.done():
            self._momentary_task.cancel()
        if not await self.set_power(True):
            return False
        self._momentary_task = asyncio.ensure_future(
            self._momentary_off_later(self.momentary_seconds)
        )
        return True

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
        if isinstance(proto, (AromaLinkBleProtocol, ScentMarketingAkProtocol)):
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
        # is observable on the device immediately.
        if self._ble_address:
            return await self._write_schedule_to_device()
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

    async def set_work_duration(self, seconds: int) -> bool:
        """Set the spray work duration and write to device."""
        self._state.work_seconds = seconds
        return await self._write_schedule_to_device()

    async def set_pause_duration(self, seconds: int) -> bool:
        """Set the pause duration and write to device."""
        self._state.pause_seconds = seconds
        return await self._write_schedule_to_device()

    async def set_schedule(
        self,
        weekday_mask: int,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        work_seconds: int,
        pause_seconds: int,
        enabled: bool = True,
    ) -> bool:
        """Set a full schedule on the device."""
        self._state.work_seconds = work_seconds
        self._state.pause_seconds = pause_seconds
        self._state.start_hour = start_hour
        self._state.start_minute = start_minute
        self._state.end_hour = end_hour
        self._state.end_minute = end_minute

        return await self._write_schedule_to_device(weekday_mask=weekday_mask, enabled=enabled)

    async def _write_schedule_to_device(self, weekday_mask: int | None = None, enabled: bool = True) -> bool:
        """Write the current schedule state to the device.

        `weekday_mask=None` (the default for callers like `set_intensity`
        or `set_schedule_enabled` that aren't changing the day pattern)
        preserves whatever mask was last read back from the device, so a
        one-axis tweak doesn't silently flatten an M-F schedule into
        every-day.
        """
        if weekday_mask is None:
            weekday_mask = self._state.weekday_mask if self._state.weekday_mask is not None else 0x7F
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
                slot = ScheduleSlot(
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                    enabled=enabled, work_seconds=work, pause_seconds=pause,
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
                cmd = self._protocol.build_schedule(
                    slot, weekday_mask=weekday_mask, intensity=level,
                )

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

    async def refresh_state(self) -> None:
        """Refresh device state."""
        if self._ble_address:
            if await self._ble_connect():
                try:
                    await self._ble_send(self._protocol.build_query())
                    await asyncio.sleep(1.0)
                    # Some protocols expose extra read-registers that the
                    # device only reports on demand (e.g. Aroma-Link's oil
                    # level). Query them too when the protocol offers one.
                    oil_query = getattr(self._protocol, "build_oil_query", None)
                    if oil_query is not None:
                        await self._ble_send(oil_query())
                        await asyncio.sleep(0.3)
                    work_query = getattr(self._protocol, "build_all_work_query", None)
                    if work_query is not None:
                        await self._ble_send(work_query())
                        await asyncio.sleep(0.3)
                except (BleakError, asyncio.TimeoutError, OSError) as err:
                    _LOGGER.debug("BLE refresh query failed on %s: %s", self._ble_name, err)
                    self._ble_last_failure_ts = asyncio.get_event_loop().time()
                    async with self._ble_lock:
                        await self._teardown_ble_client(reason="refresh-failure")
            return

        if self.supports_cloud and self._cloud:
            status = await self._cloud.get_status(self._cloud_device_id)
            if status:
                if "power" in status and status["power"] is not None:
                    self._state.power = status["power"]
                if "phase" in status:
                    self._state.phase = status["phase"]
                self._notify_state_changed()

    async def sync_time(self) -> bool:
        """Sync device clock to current local time (BLE only)."""
        if not self._ble_address:
            return False
        self._ble_has_synced_time = False
        return await self._ble_connect()

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
