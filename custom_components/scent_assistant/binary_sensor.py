"""Binary sensor entities for Scent Diffuser."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import ScentDiffuserDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]
    estimator = getattr(device, "oil_estimator", None)
    if estimator is None:
        return

    entities = [
        entity
        for entity in estimator.entities()
        if isinstance(entity, BinarySensorEntity)
    ]
    if entities:
        async_add_entities(entities)
