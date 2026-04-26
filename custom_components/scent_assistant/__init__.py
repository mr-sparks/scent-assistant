"""The Scent Diffuser integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_BLE_ADDRESS,
    CONF_BLE_NAME,
    CONF_DEVICE_TYPE,
    CONF_CLOUD_USERNAME,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_DEVICE_ID,
    CONF_CONNECTION_MODE,
    WEEKDAY_MON, WEEKDAY_TUE, WEEKDAY_WED, WEEKDAY_THU,
    WEEKDAY_FRI, WEEKDAY_SAT, WEEKDAY_SUN,
    DeviceType,
)
from .device import ScentDiffuserDevice
from .protocol_ble import ScheduleSlot, ScheduleSetup
from .protocol_cloud import AromaLinkCloudClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "sensor", "number", "time", "button"]

SERVICE_SET_SCHEDULE = "set_schedule"

DAY_NAME_TO_BIT = {
    "mon": WEEKDAY_MON,
    "tue": WEEKDAY_TUE,
    "wed": WEEKDAY_WED,
    "thu": WEEKDAY_THU,
    "fri": WEEKDAY_FRI,
    "sat": WEEKDAY_SAT,
    "sun": WEEKDAY_SUN,
}

SET_SCHEDULE_SCHEMA = vol.Schema({
    vol.Required("days"): vol.All(
        cv.ensure_list,
        [vol.In(["mon", "tue", "wed", "thu", "fri", "sat", "sun", "all"])],
    ),
    vol.Optional("start_time", default="00:00"): cv.string,
    vol.Optional("end_time", default="23:59"): cv.string,
    vol.Optional("work_seconds", default=10): vol.All(
        vol.Coerce(int), vol.Range(min=5, max=600),
    ),
    vol.Optional("pause_seconds", default=120): vol.All(
        vol.Coerce(int), vol.Range(min=5, max=3600),
    ),
    vol.Optional("enabled", default=True): cv.boolean,
    vol.Optional("entity_id"): cv.string,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Scent Diffuser from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    ble_address = entry.data.get(CONF_BLE_ADDRESS)
    ble_name = entry.data.get(CONF_BLE_NAME, "")
    device_type = DeviceType(entry.data.get(CONF_DEVICE_TYPE, "aroma_link"))

    connection_mode = entry.data.get(CONF_CONNECTION_MODE, "ble")

    # Set up cloud client if configured for cloud mode
    cloud_client = None
    cloud_device_id = entry.data.get(CONF_CLOUD_DEVICE_ID)
    username = entry.data.get(CONF_CLOUD_USERNAME)
    password = entry.data.get(CONF_CLOUD_PASSWORD)

    if connection_mode == "cloud" and username and password:
        session = async_get_clientsession(hass)
        cloud_client = AromaLinkCloudClient(session=session)
        if not await cloud_client.login(username, password):
            _LOGGER.error("Cloud login failed for %s", ble_name or cloud_device_id)
            return False

    # Create device manager
    device = ScentDiffuserDevice(
        ble_address=ble_address if connection_mode == "ble" else None,
        ble_name=ble_name,
        device_type=device_type,
        cloud_client=cloud_client,
        cloud_device_id=cloud_device_id,
    )

    # Initial state query (BLE: connects briefly then disconnects; Cloud: polls API)
    try:
        await device.async_setup()
    except Exception as err:
        _LOGGER.warning("Initial state query failed, will retry on first command: %s", err)

    hass.data[DOMAIN][entry.entry_id] = device

    # Register services (once for all entries)
    if not hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE):
        async def handle_set_schedule(call: ServiceCall) -> None:
            """Handle the set_schedule service call."""
            days_list = call.data["days"]
            start_time = call.data["start_time"]
            end_time = call.data["end_time"]
            work_seconds = call.data["work_seconds"]
            pause_seconds = call.data["pause_seconds"]
            enabled = call.data["enabled"]
            entity_id = call.data.get("entity_id")

            # Build weekday mask
            weekday_mask = 0
            for day in days_list:
                if day == "all":
                    weekday_mask = 0x7F
                    break
                weekday_mask |= DAY_NAME_TO_BIT.get(day, 0)

            # Parse times
            start_h, start_m = (int(x) for x in start_time.split(":"))
            end_h, end_m = (int(x) for x in end_time.split(":"))

            # Find target device(s)
            targets = []
            for eid, dev in hass.data[DOMAIN].items():
                if isinstance(dev, ScentDiffuserDevice):
                    if entity_id is None or eid == entity_id:
                        targets.append(dev)

            if not targets:
                _LOGGER.error("No devices found for set_schedule service")
                return

            for dev in targets:
                await dev.set_schedule(
                    weekday_mask=weekday_mask,
                    start_hour=start_h,
                    start_minute=start_m,
                    end_hour=end_h,
                    end_minute=end_m,
                    work_seconds=work_seconds,
                    pause_seconds=pause_seconds,
                    enabled=enabled,
                )

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            handle_set_schedule,
            schema=SET_SCHEDULE_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        device: ScentDiffuserDevice = hass.data[DOMAIN].pop(entry.entry_id)
        await device.async_shutdown()

    return unload_ok
