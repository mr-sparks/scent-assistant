"""The Scent Diffuser integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.entity_registry as er

from .const import (
    DOMAIN,
    CONF_BLE_ADDRESS,
    CONF_BLE_NAME,
    CONF_DEVICE_TYPE,
    CONF_CLOUD_USERNAME,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_DEVICE_ID,
    CONF_CONNECTION_MODE,
    CONF_MOMENTARY_SECONDS,
    BLE_REFRESH_INTERVAL_SECONDS,
    CLOUD_POLL_INTERVAL_SECONDS,
    WEEKDAY_MON, WEEKDAY_TUE, WEEKDAY_WED, WEEKDAY_THU,
    WEEKDAY_FRI, WEEKDAY_SAT, WEEKDAY_SUN,
    DeviceType,
)
from .device import ScentDiffuserDevice
from .protocol_ble import ScheduleSlot, ScheduleSetup
from .protocol_cloud import AromaLinkCloudClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "sensor", "number", "time", "button", "light", "select"]

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
    vol.Optional("start_time"): cv.time,
    vol.Optional("end_time"): cv.time,
    vol.Optional("work_seconds"): vol.All(
        vol.Coerce(int), vol.Range(min=5, max=600),
    ),
    vol.Optional("pause_seconds"): vol.All(
        vol.Coerce(int), vol.Range(min=5, max=3600),
    ),
    vol.Optional("enabled"): cv.boolean,
    vol.Optional("entity_id"): cv.string,
})

# Omitted set_schedule fields on Tuya, Scent Marketing and Aromely.
SET_SCHEDULE_DEFAULTS = {
    "start_time": (0, 0),
    "end_time": (23, 59),
    "work_seconds": 10,
    "pause_seconds": 120,
    "enabled": True,
}

SET_SCHEDULE_UNSUPPORTED = {DeviceType.SCENT_TECH, DeviceType.SCENTIMENT}


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
        hass=hass,
        ble_address=ble_address if connection_mode == "ble" else None,
        ble_name=ble_name,
        device_type=device_type,
        cloud_client=cloud_client,
        cloud_device_id=cloud_device_id,
        sm_metadata=entry.data.get("sm_metadata"),
        gw_password=entry.data.get("gw_password"),
    )

    # Restore local configuration before setting up the device and platforms.
    device.momentary_seconds = entry.options.get(
        CONF_MOMENTARY_SECONDS, device.momentary_seconds
    )

    # Initial state query (BLE: connects briefly then disconnects; Cloud: polls API)
    try:
        await device.async_setup()
    except Exception as err:
        _LOGGER.warning("Initial state query failed, will retry on first command: %s", err)

    hass.data[DOMAIN][entry.entry_id] = device

    # Cloud-mode devices have no push channel for autonomous state changes
    # (BLE devices push notifications when connected). Poll the cloud
    # periodically so HA reflects the device's real state, not just the
    # last command we sent. See CLOUD_POLL_INTERVAL_SECONDS in const.py.
    if connection_mode == "cloud" and cloud_client is not None:
        async def _periodic_cloud_poll(now=None) -> None:
            try:
                await device.refresh_state()
            except Exception as err:
                _LOGGER.debug("Cloud state poll failed (will retry): %s", err)

        device._unsub_cloud_poll = async_track_time_interval(
            hass,
            _periodic_cloud_poll,
            timedelta(seconds=CLOUD_POLL_INTERVAL_SECONDS),
        )

    # BLE devices are disconnected almost all the time (connect-on-demand),
    # so their query-only registers only ever got read at setup. Protocols
    # that carry such telemetry opt in via `periodic_refresh`; the device
    # manager decides whether a given tick is safe to act on.
    if device.supports_periodic_refresh:
        async def _periodic_ble_refresh(now=None) -> None:
            await device.async_periodic_refresh()

        device._unsub_ble_refresh = async_track_time_interval(
            hass,
            _periodic_ble_refresh,
            timedelta(seconds=BLE_REFRESH_INTERVAL_SECONDS),
        )

    # Register services (once for all entries)
    if not hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE):
        async def handle_set_schedule(call: ServiceCall) -> None:
            """Handle the set_schedule service call."""
            days_list = call.data["days"]
            entity_id = call.data.get("entity_id")
            given = dict(call.data)
            for key in ("start_time", "end_time"):
                if key in given:
                    given[key] = (given[key].hour, given[key].minute)

            # Build weekday mask
            weekday_mask = 0
            for day in days_list:
                if day == "all":
                    weekday_mask = 0x7F
                    break
                weekday_mask |= DAY_NAME_TO_BIT.get(day, 0)

            # Find target device(s)
            entry_id = entity_id
            entity = er.async_get(hass).async_get(entity_id) if entity_id else None
            if entity is not None:
                entry_id = entity.config_entry_id
            targets = []
            for eid, dev in hass.data[DOMAIN].items():
                if isinstance(dev, ScentDiffuserDevice):
                    if entity_id is None or eid == entry_id:
                        targets.append(dev)

            if not targets:
                _LOGGER.error("No devices found for set_schedule service")
                return
            writable = [
                dev for dev in targets
                if dev.device_type not in SET_SCHEDULE_UNSUPPORTED
            ]
            if entity_id is None and len(writable) > 1:
                _LOGGER.warning(
                    "No entity_id in set_schedule call, writing %d diffusers",
                    len(writable),
                )

            for dev in targets:
                if dev.device_type in SET_SCHEDULE_UNSUPPORTED:
                    _LOGGER.warning(
                        "Schedule write skipped on %s: set_schedule not supported on this device",
                        dev.name,
                    )
                    continue
                if dev.device_type != DeviceType.AROMA_LINK:
                    data = {**SET_SCHEDULE_DEFAULTS, **given}
                elif (
                    not dev.schedule_window_read
                    and not {"start_time", "end_time"} <= given.keys()
                ) or (
                    not dev.schedule_durations_read
                    and not {"work_seconds", "pause_seconds", "enabled"} <= given.keys()
                ):
                    _LOGGER.warning(
                        "Schedule write skipped on %s: schedule not read from device yet",
                        dev.name,
                    )
                    continue
                else:
                    s = dev.state
                    data = {
                        "start_time": (s.start_hour, s.start_minute),
                        "end_time": (s.end_hour, s.end_minute),
                        "work_seconds": s.work_seconds,
                        "pause_seconds": s.pause_seconds,
                        # None keeps the unit's own enabled bit.
                        "enabled": None,
                        **given,
                    }
                    if data["start_time"] > data["end_time"]:
                        _LOGGER.warning(
                            "Schedule write skipped on %s: start time is after end time",
                            dev.name,
                        )
                        continue
                await dev.set_schedule(
                    weekday_mask=weekday_mask,
                    start_hour=data["start_time"][0],
                    start_minute=data["start_time"][1],
                    end_hour=data["end_time"][0],
                    end_minute=data["end_time"][1],
                    work_seconds=data["work_seconds"],
                    pause_seconds=data["pause_seconds"],
                    enabled=data["enabled"],
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
        for attr in ("_unsub_cloud_poll", "_unsub_ble_refresh"):
            unsub = getattr(device, attr, None)
            if unsub is not None:
                unsub()
        await device.async_shutdown()

    return unload_ok
