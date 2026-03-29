"""Switch entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
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
    """Set up switch entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = [DiffuserPowerSwitch(device, entry)]

    is_cloud = entry.data.get("connection_mode") == "cloud"
    if device.supports_fan and not is_cloud:
        entities.append(DiffuserFanSwitch(device, entry))

    async_add_entities(entities)


class DiffuserPowerSwitch(SwitchEntity):
    """Power on/off switch for the diffuser."""

    _attr_has_entity_name = True
    _attr_name = "Power"
    _attr_icon = "mdi:power"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
            "name": device.name,
            "manufacturer": "Scent Diffuser",
            "model": device.device_type.value,
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        return self._device.state.power

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.set_power(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.set_power(False)


class DiffuserFanSwitch(SwitchEntity):
    """Fan on/off switch (Aroma-Link only, requires Bluetooth)."""

    _attr_has_entity_name = True
    _attr_name = "Fan"
    _attr_icon = "mdi:fan"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._is_cloud_only = entry.data.get("connection_mode") == "cloud"
        self._attr_unique_id = f"{device.unique_id}_fan"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        return self._device.state.fan

    @property
    def available(self) -> bool:
        if self._is_cloud_only:
            return False
        return self._device.connection_mode == "ble"

    @property
    def extra_state_attributes(self) -> dict:
        if self._is_cloud_only:
            return {"note": "Fan control requires Bluetooth connection"}
        return {}

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.set_fan(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.set_fan(False)
