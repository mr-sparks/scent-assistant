"""Sensor entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DeviceType
from .device import ScentDiffuserDevice

_LOGGER = logging.getLogger(__name__)

SCENT_MARKETING_TYPES = {
    DeviceType.SCENT_MARKETING_AK,
    DeviceType.SCENT_MARKETING_GW,
    DeviceType.SCENT_MARKETING_GW_XOR,
}
GW_TYPES = {DeviceType.SCENT_MARKETING_GW, DeviceType.SCENT_MARKETING_GW_XOR}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        DiffuserStatusSensor(device, entry),
        DiffuserConnectionSensor(device, entry),
    ]
    if device.device_type == DeviceType.SCENTIMENT:
        entities.append(DiffuserBatterySensor(device, entry))

    if device.device_type in GW_TYPES:
        entities.append(DiffuserBatterySensor(device, entry))
        entities.append(DiffuserOilSensor(device, entry))

    # Aroma-Link reports a liquid level via read-register 0x1E and live
    # work/pause countdowns + battery via 0x0A. All of these sensors stay
    # unavailable until a value arrives, so it's safe to register them for
    # the whole family even though only some models answer the queries.
    if device.device_type == DeviceType.AROMA_LINK:
        entities.append(DiffuserOilSensor(device, entry))
        entities.append(DiffuserWorkRemainSensor(device, entry))
        entities.append(DiffuserPauseRemainSensor(device, entry))
        # Like the fan switch (#34): only an explicit "no battery" from
        # the setup read drops it; unknown keeps it (#18).
        if device.state.has_battery is not False:
            entities.append(DiffuserBatterySensor(device, entry))

    # Aroma IOT battery models push battery % (and oil % on models that
    # measure it) in their 0x22 state.
    if device.device_type == DeviceType.AROMA_IOT:
        entities.append(DiffuserBatterySensor(device, entry))
        entities.append(DiffuserOilSensor(device, entry))

    if device.device_type in SCENT_MARKETING_TYPES:
        entities.append(DiffuserDetectionDiagnostic(device, entry))

    # Scent Marketing AK V3 oil block (decoded by @Mins95, #18). Sensors
    # stay unavailable until the device answers C8/CE, so registering them
    # for the whole AK family is safe (V2 simply never populates them).
    if device.device_type == DeviceType.SCENT_MARKETING_AK:
        entities.append(DiffuserOilSensor(device, entry))
        entities.append(DiffuserOilCurrentSensor(device, entry))
        entities.append(DiffuserOilCapacitySensor(device, entry))
        entities.append(DiffuserOilConsumptionSensor(device, entry))
        entities.append(DiffuserOilDaysSensor(device, entry))

    async_add_entities(entities)


class DiffuserStatusSensor(SensorEntity):
    """Shows the current spray cycle phase."""

    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_icon = "mdi:spray"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_status"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self._device.state.phase

    @property
    def extra_state_attributes(self) -> dict:
        state = self._device.state
        if self._device.device_type == DeviceType.SCENTIMENT:
            return {"level": state.level}
        return {
            "work_seconds": state.work_seconds,
            "pause_seconds": state.pause_seconds,
            "start_time": f"{state.start_hour:02d}:{state.start_minute:02d}",
            "end_time": f"{state.end_hour:02d}:{state.end_minute:02d}",
        }

    @property
    def available(self) -> bool:
        return self._device.available


class DiffuserBatterySensor(SensorEntity):
    """Battery level (Scentiment only)."""

    _attr_has_entity_name = True
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_battery"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        return self._device.state.battery

    @property
    def available(self) -> bool:
        return self._device.available and self._device.state.battery is not None


class DiffuserOilSensor(SensorEntity):
    """Remaining fragrance oil percentage (Scent Marketing GW + Aroma-Link)."""

    _attr_has_entity_name = True
    _attr_name = "Oil remaining"
    _attr_icon = "mdi:water-percent"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_oil"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        return self._device.state.oil_remaining

    @property
    def extra_state_attributes(self) -> dict | None:
        # Oil is query-only on some families, so "how old is this
        # number" is a fair question. Answer it with a timestamp rather
        # than by flipping the entity unavailable — an oil level doesn't
        # move in minutes, and the last known value beats a gap.
        last = self._device.ble_last_update
        if last is None:
            return None
        return {"last_device_update": last.isoformat()}

    @property
    def available(self) -> bool:
        return self._device.available and self._device.state.oil_remaining is not None


class _OilFieldSensor(SensorEntity):
    """Base for the AK V3 oil-block detail sensors (#18).

    Subclasses set `_attr_name`, `_uid_suffix`, `_state_attr` and the
    unit/device-class attributes. Each stays unavailable until the device
    reports its value.
    """

    _attr_has_entity_name = True
    _state_attr: str = ""
    _uid_suffix: str = ""

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_{self._uid_suffix}"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self):
        return getattr(self._device.state, self._state_attr)

    @property
    def available(self) -> bool:
        return self._device.available and getattr(self._device.state, self._state_attr) is not None


class DiffuserOilCurrentSensor(_OilFieldSensor):
    """Current fragrance oil volume in millilitres (AK V3)."""

    _attr_name = "Oil remaining (ml)"
    _attr_icon = "mdi:cup-water"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "mL"
    _state_attr = "oil_current_ml"
    _uid_suffix = "oil_current_ml"


class DiffuserOilCapacitySensor(_OilFieldSensor):
    """Bottle capacity in millilitres (AK V3)."""

    _attr_name = "Oil capacity"
    _attr_icon = "mdi:cup"
    _attr_native_unit_of_measurement = "mL"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _state_attr = "oil_max_ml"
    _uid_suffix = "oil_max_ml"


class DiffuserOilConsumptionSensor(_OilFieldSensor):
    """Fragrance consumption rate in millilitres per hour (AK V3)."""

    _attr_name = "Oil consumption"
    _attr_icon = "mdi:speedometer"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "mL/h"
    _state_attr = "oil_consumption_mlh"
    _uid_suffix = "oil_consumption_mlh"


class DiffuserOilDaysSensor(_OilFieldSensor):
    """Estimated days of fragrance remaining (AK V3).

    Computed in the device layer from current oil, consumption rate, the
    active schedule window and the work/pause duty cycle (matching the
    official app). Custom mode uses the live work/pause; Level mode looks the
    duty up in the device grade table (0x47). Stays unavailable only until the
    inputs (oil, consumption, schedule, and the grade table in Level mode) are
    known, rather than showing the unreliable raw value (@Mins95 #8).
    """

    _attr_name = "Oil days remaining"
    _attr_icon = "mdi:calendar-clock"
    _attr_native_unit_of_measurement = "d"
    _state_attr = "oil_days_remaining"
    _uid_suffix = "oil_days_remaining"


class DiffuserWorkRemainSensor(SensorEntity):
    """Remaining seconds of the current spray phase (Aroma-Link 52 0A).

    While the device is idle the firmware reports the configured work
    duration instead of a live countdown. The value only refreshes when
    HA polls the device, so treat it as a snapshot, not a ticking timer.
    """

    _attr_has_entity_name = True
    _attr_name = "Diffusion time remaining"
    _attr_icon = "mdi:timer-sand"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_work_remaining"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        return self._device.state.work_remaining

    @property
    def available(self) -> bool:
        return self._device.available and self._device.state.work_remaining is not None


class DiffuserPauseRemainSensor(SensorEntity):
    """Remaining seconds of the current pause phase (Aroma-Link 52 0A)."""

    _attr_has_entity_name = True
    _attr_name = "Pause time remaining"
    _attr_icon = "mdi:timer-pause-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_pause_remaining"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        return self._device.state.pause_remaining

    @property
    def available(self) -> bool:
        return self._device.available and self._device.state.pause_remaining is not None


class DiffuserDetectionDiagnostic(SensorEntity):
    """Exposes Scent Marketing detection metadata as a diagnostic entity.

    The state is the detected protocol family; attributes carry the raw
    advertisement bytes plus the manufacturer ID, PID and WiFi flag. This
    is what a non-technical reporter screenshots when something doesn't
    work — no need to dig through HA's text logs.
    """

    _attr_has_entity_name = True
    _attr_name = "Detected family"
    _attr_icon = "mdi:bluetooth-settings"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_sm_detection"
        self._attr_device_info = device.device_info

    @property
    def native_value(self) -> str:
        return self._device.device_type.value

    @property
    def extra_state_attributes(self) -> dict:
        meta = self._device.sm_metadata or {}
        return {
            "manufacturer_id": meta.get("mfr_id"),
            "pid": meta.get("pid"),
            "wifi_flag": meta.get("wifi_flag"),
            "heartbeat": meta.get("heartbeat"),
            "mac_from_advertisement": meta.get("mac_from_adv"),
            "raw_advertisement_hex": meta.get("raw_hex"),
            "model": self._device.model_name,
            "firmware_version": self._device.state.firmware_version,
            "password_required": self._device.state.password_required,
        }


class DiffuserConnectionSensor(SensorEntity):
    """Shows the current connection mode (BLE/Cloud/Offline)."""

    _attr_has_entity_name = True
    _attr_name = "Connection"
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_registry_enabled_default = False

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_connection"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self._device.connection_mode
