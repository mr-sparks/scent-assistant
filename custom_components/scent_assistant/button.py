"""Button entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DeviceType
from .device import ScentDiffuserDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]
    if device.device_type == DeviceType.SCENTIMENT:
        return

    entities: list[ButtonEntity] = [TimeSyncButton(device, entry)]
    # Momentary diffusion is power-on + delayed power-off, which only
    # makes sense on families where power is a plain on/off (Aroma-Link).
    if device.device_type == DeviceType.AROMA_LINK:
        entities.append(MomentaryDiffuseButton(device, entry))
    async_add_entities(entities)


class MomentaryDiffuseButton(ButtonEntity):
    """One-shot diffusion: power on, auto-off after a set duration.

    The Aroma-Link protocol has no native momentary command (checked
    against the decompiled official app), so the device manager emulates
    it. The run time comes from the Momentary Duration number entity.
    """

    _attr_has_entity_name = True
    _attr_name = "Diffuse Now"
    _attr_icon = "mdi:spray"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_momentary"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_press(self) -> None:
        """Start a momentary diffusion run."""
        if not await self._device.momentary_diffuse():
            _LOGGER.warning(
                "Momentary diffusion failed to start on %s", self._device.name
            )


class TimeSyncButton(ButtonEntity):
    """Button to manually sync the device clock."""

    _attr_has_entity_name = True
    _attr_name = "Sync Time"
    _attr_icon = "mdi:clock-check"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_time_sync"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }

    @property
    def available(self) -> bool:
        return self._device.connection_mode == "ble"

    async def async_press(self) -> None:
        """Sync the device clock to current local time."""
        success = await self._device.sync_time()
        if success:
            _LOGGER.info("Time synced to %s", self._device.name)
        else:
            _LOGGER.warning("Time sync failed for %s", self._device.name)
