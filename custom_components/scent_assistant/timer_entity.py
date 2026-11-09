"""Shared base for the per-slot Scent Tech timer entities.

Scent Tech / ScentLab diffusers hold five independent timer records
(enabled, weekdays, start, end, spray, pause). Each platform exposes one
field per slot; this base wires an entity to its slot's cached record.
"""
from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError

from .const import AI_DIFFUSER_SLOTS, SCENT_TECH_TIMER_SLOTS, DeviceType
from .device import ScentDiffuserDevice
from .protocol_ble import ScentTechTimer

TIMER_SLOTS = range(1, SCENT_TECH_TIMER_SLOTS + 1)
# AI-Diffuser units hold six slots ("Mode 1-6" in the app) in the same
# record shape, so they reuse these entities.
AI_DIFFUSER_TIMER_SLOTS = range(1, AI_DIFFUSER_SLOTS + 1)


def timer_slots_for(device: ScentDiffuserDevice) -> range:
    """Per-slot schedule entities a device gets (empty when it has none)."""
    if device.device_type == DeviceType.SCENT_TECH:
        return TIMER_SLOTS
    if device.device_type == DeviceType.AI_DIFFUSER:
        return AI_DIFFUSER_TIMER_SLOTS
    return range(0)


class ScentTechTimerEntity:
    """Mixin for an entity bound to one field of one timer slot."""

    _attr_has_entity_name = True

    def __init__(self, device: ScentDiffuserDevice, slot: int, key: str, label: str) -> None:
        self._device = device
        self._slot = slot
        self._attr_name = f"Schedule {slot} {label}".rstrip()
        self._attr_unique_id = f"{device.unique_id}_timer_{slot}_{key}"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    async def _async_set_timer_fields(self, **changes) -> None:
        """Surface an unconfirmed write as a failed Home Assistant action."""
        if not await self._device.set_timer_slot(self._slot, **changes):
            raise HomeAssistantError(
                f"Schedule {self._slot} write was not confirmed by the device. "
                "Check the current schedule before making another change."
            )

    @property
    def timer(self) -> ScentTechTimer | None:
        slots = self._device.state.timer_slots
        return slots.get(self._slot) if slots else None

    @property
    def available(self) -> bool:
        return self._device.available and self.timer is not None
