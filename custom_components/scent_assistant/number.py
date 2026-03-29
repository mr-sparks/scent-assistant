"""Number entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
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
    """Set up number entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        WorkDurationNumber(device, entry),
        PauseDurationNumber(device, entry),
    ])


class WorkDurationNumber(NumberEntity):
    """Spray work duration in seconds."""

    _attr_has_entity_name = True
    _attr_name = "Work Duration"
    _attr_icon = "mdi:timer"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 5
    _attr_native_max_value = 600
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_work_duration"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return self._device.state.work_seconds or 10

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_work_duration(int(value))


class PauseDurationNumber(NumberEntity):
    """Pause duration between sprays in seconds."""

    _attr_has_entity_name = True
    _attr_name = "Pause Duration"
    _attr_icon = "mdi:timer-pause"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 15
    _attr_native_max_value = 3600
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_pause_duration"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return self._device.state.pause_seconds or 120

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_pause_duration(int(value))
