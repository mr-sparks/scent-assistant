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


class ScentDiffuserDevice:
    """Manages a single scent diffuser via BLE and/or cloud."""

    def __init__(
        self,
        ble_address: str | None = None,
        ble_name: str | None = None,
        device_type: DeviceType | None = None,
        cloud_client: AromaLinkCloudClient | None = None,
        cloud_device_id: str | None = None,
        sm_metadata: dict | None = None,
        gw_password: str | None = None,
    ) -> None:
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
        self._ble_lock = asyncio.Lock()
        self._ble_disconnect_task: asyncio.Task | None = None
        self._ble_has_synced_time = False

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

        async with self._ble_lock:
            # Double-check after acquiring lock
            if self._ble_connected and self._ble_client and self._ble_client.is_connected:
                self._schedule_disconnect()
                return True

            try:
                _LOGGER.debug("BLE connecting to %s", self._ble_name)
                self._ble_client = BleakClient(
                    self._ble_address,
                    timeout=DEFAULT_CONNECT_TIMEOUT,
                )
                await self._ble_client.connect()
                self._ble_connected = True

                # Subscribe to notifications for responses
                try:
                    await self._ble_client.start_notify(
                        self._protocol.notify_char_uuid, self._on_ble_notification
                    )
                except Exception:
                    _LOGGER.debug("Could not subscribe to notifications")

                # Scent Marketing AK family — PIN 8888 login must precede
                # every other write, otherwise the device drops them
                # silently. The response also tells us whether to use the
                # V2 or V3 command set; we wait briefly for it before
                # sending follow-ups so `_v3_mode` is set in time.
                if isinstance(self._protocol, ScentMarketingAkProtocol):
                    self._protocol.reset_login_state()
                    try:
                        await self._ble_send(self._protocol.build_login_primary())
                        # Give the device time to respond — the
                        # notification handler will set _v3_mode.
                        await asyncio.sleep(0.5)
                        if self._protocol.is_v3:
                            await self._ble_send(self._protocol.build_login_secondary_v3())
                            await asyncio.sleep(0.3)
                    except Exception as err:
                        _LOGGER.debug("Scent Marketing AK: login failed: %s", err)

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
                self._ble_connected = False
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
            if self._ble_client and self._ble_client.is_connected:
                try:
                    await self._ble_client.disconnect()
                    _LOGGER.debug("BLE disconnected (idle): %s", self._ble_name)
                except Exception:
                    pass
            self._ble_client = None
            self._ble_connected = False

    async def _ble_send(self, data: bytes) -> bool:
        """Send a command via BLE.

        Protocols may return more than one on-wire chunk per command — the
        Scent Marketing GW family for instance needs (nonce, seq)-prefixed
        18-byte chunks. We delegate the split decision to the protocol and
        write each chunk sequentially.
        """
        if not self._ble_client or not self._ble_client.is_connected:
            return False
        chunks = self._protocol.wire_chunks(data) if data else []
        if chunks:
            self._recent_commands.append(data.hex())
            if len(self._recent_commands) > 10:
                del self._recent_commands[0]
        for chunk in chunks:
            try:
                await self._ble_client.write_gatt_char(
                    self._protocol.write_char_uuid, chunk, response=True
                )
            except (BleakError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.warning("BLE write failed: %s", err)
                return False
        return True

    async def _ble_execute(self, data: bytes) -> bool:
        """Connect, send command, schedule disconnect."""
        if await self._ble_connect():
            success = await self._ble_send(data)
            # Wait briefly for notification response
            await asyncio.sleep(1.0)
            return success
        return False

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

    async def _write_schedule_to_device(self, weekday_mask: int = 0x7F, enabled: bool = True) -> bool:
        """Write the current schedule state to the device."""
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

    async def refresh_state(self) -> None:
        """Refresh device state."""
        if self._ble_address:
            if await self._ble_connect():
                await self._ble_send(self._protocol.build_query())
                await asyncio.sleep(1.0)
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
