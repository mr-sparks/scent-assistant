"""Select entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .capability_entity import add_when_present
from .const import DOMAIN, SCENT_TECH_WEEKDAY_ANY, DeviceType
from .device import ScentDiffuserDevice
from .timer_entity import TIMER_SLOTS, ScentTechTimerEntity, timer_slots_for

_LOGGER = logging.getLogger(__name__)

MODE_CUSTOM = "Custom"
MODE_LEVEL = "Level"
RADAR_MODES = {0: "App", 1: "Radar"}

# GW family: plain BLE and the XOR-encrypted WiFi/cellular variants.
GW_TYPES = {DeviceType.SCENT_MARKETING_GW, DeviceType.SCENT_MARKETING_GW_XOR}

# The Scent Marketing app's five fixed-gear levels (Low..Max), plus Custom
# (grade 0 — the Work/Pause Duration timing). Level N maps to grade N in the
# DP 4 task byte, i.e. row N-1 of the device's DP 14 gear table.
GW_LEVEL_NAMES = ["Low", "Mild", "Medium", "High", "Max"]
GW_LEVEL_OPTIONS = [MODE_CUSTOM] + GW_LEVEL_NAMES

# Scent Tech weekday options: the four common patterns first, then every
# other combination ordered by number of days (after @alexlewer's
# ScentLab BLE, MIT). Masks are bit0 Mon … bit6 Sun.
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_COMMON_DAYS = {
    0x7F: "Every day",
    0x1F: "Weekdays (Mon–Fri)",
    0x60: "Weekends (Sat, Sun)",
    0x00: "No days",
}


def _day_label(mask: int) -> str:
    if mask in _COMMON_DAYS:
        return _COMMON_DAYS[mask]
    return ", ".join(name for bit, name in enumerate(_DAY_NAMES) if mask & (1 << bit))


_DAY_MASKS = tuple(_COMMON_DAYS) + tuple(sorted(
    (mask for mask in range(0x80) if mask not in _COMMON_DAYS),
    key=lambda mask: (bin(mask).count("1"), mask),
))
DAY_OPTIONS = [_day_label(mask) for mask in _DAY_MASKS]
_OPTION_TO_MASK = dict(zip(DAY_OPTIONS, _DAY_MASKS))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = []
    # The Custom/Level schedule mode is an AK V3 concept. The entity stays
    # unavailable until the device identifies as V3 on first connect, so it's
    # safe to register for the whole AK family (V2 simply never exposes it).
    if device.device_type == DeviceType.SCENT_MARKETING_AK:
        entities.append(ScheduleModeSelect(device, entry))
    if device.device_type in GW_TYPES:
        entities.append(GwLevelSelect(device, entry))
    if device.device_type == DeviceType.SCENT_TECH:
        entities.extend(ScentTechTimerDays(device, slot) for slot in TIMER_SLOTS)
    # AI-Diffuser: the app's six "Modes".
    if device.device_type == DeviceType.AI_DIFFUSER:
        entities.extend(
            ScentTechTimerDays(device, slot) for slot in timer_slots_for(device)
        )
    if device.device_type == DeviceType.AROMA_LINK:
        add_when_present(
            hass, entry, device, async_add_entities,
            [DiffuserRadarModeSelect(device, entry)],
            lambda: device.radar_present,
        )

    async_add_entities(entities)


class ScheduleModeSelect(SelectEntity):
    """Custom vs Level schedule-mode selector for AK V3 (@Mins95, #8).

    The integration already switches mode implicitly (setting a Work/Pause
    Duration selects Custom, setting Intensity selects Level). This makes the
    mode an explicit control so the user can pin it without accidentally
    flipping it via a side-effect of another change.
    """

    _attr_has_entity_name = True
    _attr_name = "Schedule mode"
    _attr_icon = "mdi:tune-variant"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [MODE_LEVEL, MODE_CUSTOM]

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_schedule_mode"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:
        mode = self._device.state.schedule_custom_mode
        if mode is None:
            return None
        return MODE_CUSTOM if mode else MODE_LEVEL

    @property
    def available(self) -> bool:
        return (
            self._device.available
            and self._device.protocol_is_v3
            and self._device.state.schedule_custom_mode is not None
        )

    async def async_select_option(self, option: str) -> None:
        await self._device.set_schedule_mode(option == MODE_CUSTOM)


class GwLevelSelect(SelectEntity):
    """Spray level for Scent Marketing GW devices.

    Mirrors the app's five fixed-gear levels (Low..Max) plus Custom. A
    level writes its grade (1-5) into the DP 4 task byte — row grade-1 of
    the DP 14 gear table; Custom writes grade 0 and the Work/Pause
    Duration timing applies. Read-back comes from DP 4 (grade byte) and
    the DP 14 table caps the selectable range on the device manager.
    """

    _attr_has_entity_name = True
    _attr_name = "Level"
    _attr_icon = "mdi:gauge"
    _attr_options = GW_LEVEL_OPTIONS

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_level"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:
        state = self._device.state
        if state.schedule_custom_mode:
            return MODE_CUSTOM
        intensity = state.intensity
        if intensity is not None and 1 <= intensity <= len(GW_LEVEL_NAMES):
            return GW_LEVEL_NAMES[intensity - 1]
        return None

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_select_option(self, option: str) -> None:
        if option == MODE_CUSTOM:
            await self._device.set_schedule_mode(True)
        else:
            await self._device.set_intensity(GW_LEVEL_NAMES.index(option) + 1)


class DiffuserRadarModeSelect(SelectEntity):
    """App or radar work mode (Aroma-Link 52 0A, 57 20)."""

    _attr_has_entity_name = True
    _attr_name = "Mode"
    _attr_icon = "mdi:radar"
    _attr_options = list(RADAR_MODES.values())

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_radar_mode"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:
        return RADAR_MODES.get(self._device.state.radar_mode)

    @property
    def available(self) -> bool:
        return (
            self._device.available
            and self._device.supports_radar
            and self._device.state.radar_mode is not None
        )

    async def async_select_option(self, option: str) -> None:
        await self._device.set_radar_mode(option == RADAR_MODES[1])


class ScentTechTimerDays(ScentTechTimerEntity, SelectEntity):
    """Weekdays one Scent Tech timer slot runs on."""

    _attr_icon = "mdi:calendar-week"
    _attr_options = DAY_OPTIONS

    def __init__(self, device: ScentDiffuserDevice, slot: int) -> None:
        super().__init__(device, slot, "days", "Days")

    @property
    def current_option(self) -> str | None:
        return _day_label(self.timer.weekdays & 0x7F) if self.timer else None

    async def async_select_option(self, option: str) -> None:
        mask = _OPTION_TO_MASK[option]
        # Bit 7 is the apps' "at least one day selected" marker.
        weekdays = mask | (SCENT_TECH_WEEKDAY_ANY if mask else 0)
        await self._async_set_timer_fields(weekdays=weekdays)
