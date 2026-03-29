"""Sensor entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import ScentDiffuserDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        DiffuserStatusSensor(device, entry),
        DiffuserConnectionSensor(device, entry),
    ])


class DiffuserStatusSensor(SensorEntity):
    """Shows the current spray cycle phase."""

    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_icon = "mdi:spray"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self._device.state.phase

    @property
    def extra_state_attributes(self) -> dict:
        state = self._device.state
        return {
            "work_seconds": state.work_seconds,
            "pause_seconds": state.pause_seconds,
            "start_time": f"{state.start_hour:02d}:{state.start_minute:02d}",
            "end_time": f"{state.end_hour:02d}:{state.end_minute:02d}",
        }

    @property
    def available(self) -> bool:
        return self._device.available


class DiffuserConnectionSensor(SensorEntity):
    """Shows the current connection mode (BLE/Cloud/Offline)."""

    _attr_has_entity_name = True
    _attr_name = "Connection"
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_registry_enabled_default = False  # hidden by default

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_connection"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self._device.connection_mode
