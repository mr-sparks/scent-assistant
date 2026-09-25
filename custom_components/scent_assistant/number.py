"""Number entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MOMENTARY_SECONDS, DOMAIN, DeviceType
from .device import ScentDiffuserDevice
from .timer_entity import TIMER_SLOTS, ScentTechTimerEntity

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
    # Scent Tech has per-slot durations instead of one work/pause pair.
    if device.device_type == DeviceType.SCENT_TECH:
        async_add_entities(
            ScentTechTimerDuration(device, slot, field)
            for slot in TIMER_SLOTS
            for field in ("run_seconds", "pause_seconds")
        )
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
    def native_value(self) -> float | None:
        if not self._device.schedule_durations_read:
            return None
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
    def native_value(self) -> float | None:
        if not self._device.schedule_durations_read:
            return None
        return self._device.state.pause_seconds or 120

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_pause_duration(int(value))


class MomentaryDurationNumber(NumberEntity):
    """Run time for the Diffuse Now button (Aroma-Link).

    Set to 0 to disable the automatic power-off after Diffuse Now, leaving
    the diffuser powered on. Saved locally in the config entry options
    and restored after an HA restart. Configuration entity, so it lands in
    the device's "Configuration" section rather than next to the live
    controls.
    """

    _attr_has_entity_name = True
    _attr_name = "Momentary Duration"
    _attr_icon = "mdi:timer-cog"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 0
    _attr_native_max_value = 600
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_momentary_duration"
        self._entry = entry
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
        if self.hass is not None:
            self.hass.config_entries.async_update_entry(
                self._entry,
                options={
                    **self._entry.options,
                    CONF_MOMENTARY_SECONDS: int(value),
                },
            )
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


class ScentTechTimerDuration(ScentTechTimerEntity, NumberEntity):
    """Spray or pause duration of one Scent Tech timer slot.

    The apps express intensity purely as this duty cycle; there is no
    separate level control.
    """

    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 0
    _attr_native_max_value = 65535
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, slot: int, field: str) -> None:
        spray = field == "run_seconds"
        super().__init__(device, slot, "spray" if spray else "pause", "Spray" if spray else "Pause")
        self._field = field
        self._attr_icon = "mdi:spray" if spray else "mdi:timer-pause"

    @property
    def native_value(self) -> float | None:
        return float(getattr(self.timer, self._field)) if self.timer else None

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_timer_slot(self._slot, **{self._field: int(value)})
