"""Entities created only once the unit reports the hardware they need."""
from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.helpers.entity_registry as er

from .device import ScentDiffuserDevice

_LOGGER = logging.getLogger(__name__)


def add_when_present(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: ScentDiffuserDevice,
    async_add_entities: AddEntitiesCallback,
    entities: list[Entity],
    present: Callable[[], bool | None],
) -> None:
    """Add entities once present() is True; unregister them once False."""
    def resolve() -> bool:
        found = present()
        if found is None:
            return False
        if found:
            async_add_entities(entities)
            return True
        registry = er.async_get(hass)
        unique_ids = {entity.unique_id for entity in entities}
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg_entry.unique_id in unique_ids:
                _LOGGER.info("Removing %s: the unit reports no such hardware", reg_entry.entity_id)
                registry.async_remove(reg_entry.entity_id)
        return True

    if resolve():
        return

    def on_state_update() -> None:
        if resolve():
            remove()

    remove = device.register_state_callback(on_state_update)
    device._capability_unsubs.append(remove)
