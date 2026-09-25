"""Diagnostics support for the Scent Diffuser integration.

A reporter can click "Download Diagnostics" on the device page in HA and
attach the resulting JSON to a bug report; everything we need to validate
or debug detection + protocol behaviour is contained here.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    CONF_BLE_ADDRESS,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_USERNAME,
    CONF_GW_PASSWORD,
)
from .device import ScentDiffuserDevice

TO_REDACT = {
    CONF_BLE_ADDRESS, CONF_CLOUD_USERNAME, CONF_CLOUD_PASSWORD,
    CONF_GW_PASSWORD, "mac_from_adv",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device: ScentDiffuserDevice | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    state_snapshot: dict[str, Any] = {}
    if device is not None:
        s = device.state
        state_snapshot = {
            "power": s.power,
            "phase": s.phase,
            "fan": s.fan,
            "has_fan": s.has_fan,
            "level": s.level,
            "battery": s.battery,
            "oil_remaining": s.oil_remaining,
            "oil_current_ml": s.oil_current_ml,
            "oil_max_ml": s.oil_max_ml,
            "oil_consumption_mlh": s.oil_consumption_mlh,
            "oil_days_remaining": s.oil_days_remaining,
            "schedule_custom_mode": s.schedule_custom_mode,
            "grade_table": s.grade_table,
            "work_remaining": s.work_remaining,
            "pause_remaining": s.pause_remaining,
            "device_clock": (
                s.device_clock.isoformat() if s.device_clock else None
            ),
            "lock": s.lock,
            "light_on": s.light_on,
            "device_name": s.device_name,
            "password_required": s.password_required,
            "firmware_version": s.firmware_version,
            "work_seconds": s.work_seconds,
            "pause_seconds": s.pause_seconds,
            # Scent Marketing AK read-back fields (populated by the
            # 8301-8305 / C5 / CA01XX response parsers). Without these
            # in the diagnostic output a reporter can't tell whether
            # the device's stored schedule was actually read.
            "intensity": s.intensity,
            "weekday_mask": s.weekday_mask,
            "schedule_slot": s.schedule_slot,
            "start_hour": s.start_hour,
            "start_minute": s.start_minute,
            "end_hour": s.end_hour,
            "end_minute": s.end_minute,
            "device_label": s.device_label,
            "model_code": s.model_code,
            "schedule_enabled": s.schedule_enabled,
            "timer_slots": (
                {slot: asdict(timer) for slot, timer in s.timer_slots.items()}
                if s.timer_slots else None
            ),
            "week_enabled_slots": (
                sum(sl["enabled"] for day in s.week_slots for sl in day)
                if s.week_slots is not None else None
            ),
            "pump": s.pump,
            "device_code": s.device_code,
        }

    payload: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "device": {
            "device_type": device.device_type.value if device else None,
            "model": device.model_name if device else None,
            "available": device.available if device else None,
            "connection_mode": device.connection_mode if device else None,
            "ble_write_response": device.ble_write_response if device else None,
            "ble_last_update": (
                device.ble_last_update.isoformat()
                if device and device.ble_last_update else None
            ),
            "periodic_refresh": device.supports_periodic_refresh if device else None,
            "supports_fan": device.supports_fan if device else None,
            "supports_cloud": device.supports_cloud if device else None,
            "schedule_window_read": device.schedule_window_read if device else None,
            "schedule_durations_read": device.schedule_durations_read if device else None,
        },
        "sm_metadata": async_redact_data(
            dict(device.sm_metadata) if device and device.sm_metadata else {},
            TO_REDACT,
        ),
        "state": state_snapshot,
        "recent_notifications_hex": device.recent_notifications if device else [],
        "recent_commands_hex": device.recent_commands if device else [],
    }
    return payload
