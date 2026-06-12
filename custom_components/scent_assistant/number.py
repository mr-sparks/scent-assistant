"""Number entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up number entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]

    if device.device_type == DeviceType.SCENTIMENT:
        async_add_entities([ScentimentLevelNumber(device, entry)])
        return

    entities: list[NumberEntity] = [
        WorkDurationNumber(device, entry),
        PauseDurationNumber(device, entry),
    ]
    if device.device_type == DeviceType.SCENT_MARKETING_AK:
        entities.append(ScentMarketingIntensityNumber(device, entry))
    if device.device_type == DeviceType.AROMA_LINK:
        entities.append(MomentaryDurationNumber(device, entry))
    async_add_entities(entities)


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
        if self.hass is None:
            return
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
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return self._device.state.pause_seconds or 120

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_pause_duration(int(value))


class MomentaryDurationNumber(NumberEntity):
    """Run time for the Diffuse Now button (Aroma-Link).

    Held on the device manager only — resets to the default after an HA
    restart. Configuration entity, so it lands in the device's
    "Configuration" section rather than next to the live controls.
    """

    _attr_has_entity_name = True
    _attr_name = "Momentary Duration"
    _attr_icon = "mdi:timer-cog"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 5
    _attr_native_max_value = 600
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_momentary_duration"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }

    @property
    def native_value(self) -> float:
        return self._device.momentary_seconds

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        self._device.momentary_seconds = int(value)
        if self.hass is not None:
            self.async_write_ha_state()


class ScentMarketingIntensityNumber(NumberEntity):
    """Spray intensity for Scent Marketing AK devices.

    Range goes up to 20 (V3 firmware ceiling); V2 devices accept 0-10 and
    the device manager clamps on send. Intensity isn't a standalone BLE
    write — it's the LL field in the AK schedule frame — so changing this
    re-applies the current schedule with the new level.
    """

    _attr_has_entity_name = True
    _attr_name = "Intensity"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 0
    _attr_native_max_value = 20
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_intensity"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._device.state.intensity

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_intensity(int(value))


class ScentimentLevelNumber(NumberEntity):
    """Spray intensity level (Scentiment, 1-3)."""

    _attr_has_entity_name = True
    _attr_name = "Level"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 1
    _attr_native_max_value = 3
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_level"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._device.state.level

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_level(int(value))
