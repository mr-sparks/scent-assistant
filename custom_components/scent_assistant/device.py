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
    CHAR_WRITE_UUID,
    CHAR_NOTIFY_UUID,
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
    ) -> None:
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
            self._device_type = detect_device_type(ble_name) or DeviceType.AROMA_LINK
        else:
            self._device_type = DeviceType.AROMA_LINK

        # Protocol handler
        self._protocol: BleProtocol = get_protocol(self._device_type)

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
                    await self._ble_client.start_notify(CHAR_NOTIFY_UUID, self._on_ble_notification)
                except Exception:
                    _LOGGER.debug("Could not subscribe to notifications")

                # Time sync on first connection of this session
                if not self._ble_has_synced_time:
                    await self._ble_send(self._protocol.build_time_sync())
                    self._ble_has_synced_time = True
                    _LOGGER.info("BLE connected + time synced: %s", self._ble_name)

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
        """Send bytes via BLE (assumes already connected)."""
        if not self._ble_client or not self._ble_client.is_connected:
            return False
        try:
            await self._ble_client.write_gatt_char(CHAR_WRITE_UUID, data, response=True)
            return True
        except (BleakError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.warning("BLE write failed: %s", err)
            return False

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
        updates = self._protocol.parse_notification(bytes(data))
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
        """Turn fan on or off (Aroma-Link only, BLE only)."""
        if not self.supports_fan or not self._ble_address:
            return False

        if isinstance(self._protocol, AromaLinkBleProtocol):
            cmd = self._protocol.build_fan(on)
            if await self._ble_execute(cmd):
                self._state.fan = on
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
