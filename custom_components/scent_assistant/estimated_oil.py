"""Persisted, schedule-aware estimated oil tracking.

This module keeps estimated volume separate from device-reported oil telemetry.
The integration's platform setup can use ``OilEstimator.entities()`` when the
option is enabled and call ``async_advance`` on state changes and a timer.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.storage import Store

DEFAULT_OIL_ESTIMATION_ENABLED = False
DEFAULT_OIL_BOTTLE_CAPACITY_ML = 1000.0
DEFAULT_OIL_FULL_DUTY_CONSUMPTION_MLH = 2.25
DEFAULT_OIL_LOW_THRESHOLD_PERCENT = 10.0
OIL_ESTIMATION_INTERVAL = timedelta(minutes=1)


def scheduled_seconds_between(
    start: datetime,
    end: datetime,
    start_minute: int,
    end_minute: int,
) -> float:
    """Return the seconds in [start, end) that fall within a daily schedule.

    The window is local wall time and may cross midnight. Equal start/end is
    treated as a full-day window, consistent with a 24-hour schedule.
    """
    if end <= start:
        return 0.0
    start_minute = max(0, min(1439, int(start_minute)))
    end_minute = max(0, min(1439, int(end_minute)))
    total = 0.0
    day = start.date() - timedelta(days=1)
    last_day = end.date()
    while day <= last_day:
        window_start = datetime.combine(day, time()) + timedelta(minutes=start_minute)
        duration_minutes = (end_minute - start_minute) % 1440
        if duration_minutes == 0:
            duration_minutes = 1440
        window_end = window_start + timedelta(minutes=duration_minutes)
        total += max(0.0, (min(end, window_end) - max(start, window_start)).total_seconds())
        day += timedelta(days=1)
    return total


def estimate_consumption_ml(
    *,
    remaining_ml: float,
    start: datetime,
    end: datetime,
    is_on: bool | None,
    schedule_enabled: bool | None,
    start_minute: int | None,
    end_minute: int | None,
    work_seconds: int | None,
    pause_seconds: int | None,
    full_duty_consumption_mlh: float,
) -> float:
    """Calculate an estimated remaining volume for one time interval.

    ``None`` means schedule/device data is incomplete, so the estimate is
    deliberately left unchanged. With a known valid schedule, off/disabled
    periods consume nothing. Work/pause values model the average duty cycle,
    including while the device reports its paused phase.
    """
    if (
        start_minute is None
        or end_minute is None
        or work_seconds is None
        or pause_seconds is None
        or work_seconds <= 0
        or pause_seconds < 0
        or full_duty_consumption_mlh < 0
    ):
        return remaining_ml
    if is_on is None or schedule_enabled is None:
        return remaining_ml
    if not is_on or not schedule_enabled or end <= start:
        return remaining_ml
    seconds = scheduled_seconds_between(start, end, start_minute, end_minute)
    duty = work_seconds / (work_seconds + pause_seconds)
    used = seconds / 3600 * full_duty_consumption_mlh * duty
    return max(0.0, remaining_ml - used)


class OilEstimator:
    """Persisted volume estimate and entities for one diffuser config entry."""

    def __init__(self, hass, device, entry) -> None:
        self.hass = hass
        self.device = device
        self.entry = entry
        options = entry.options
        self.enabled = bool(options.get("oil_estimation_enabled", DEFAULT_OIL_ESTIMATION_ENABLED))
        self.capacity_ml = max(1.0, float(options.get("oil_bottle_capacity_ml", DEFAULT_OIL_BOTTLE_CAPACITY_ML)))
        self.full_duty_mlh = max(0.0, float(options.get("oil_full_duty_consumption_mlh", DEFAULT_OIL_FULL_DUTY_CONSUMPTION_MLH)))
        self.low_threshold_percent = max(0.0, min(100.0, float(options.get("oil_low_threshold_percent", DEFAULT_OIL_LOW_THRESHOLD_PERCENT))))
        self._store = Store(hass, 1, f"scent_assistant_oil_estimate_{entry.entry_id}")
        self.remaining_ml = self.capacity_ml
        self.last_update: datetime | None = None
        self._lock = None

    async def async_load(self) -> None:
        """Restore volume and timestamp from Home Assistant's storage."""
        data = await self._store.async_load()
        if data:
            try:
                self.remaining_ml = max(0.0, min(self.capacity_ml, float(data["remaining_ml"])))
                stamp = data.get("last_update")
                self.last_update = datetime.fromisoformat(stamp) if stamp else None
            except (KeyError, TypeError, ValueError):
                self.remaining_ml = self.capacity_ml
                self.last_update = None
        if self.last_update is None:
            self.last_update = self._now()
            await self._save()

    @staticmethod
    def _now() -> datetime:
        from homeassistant.util import dt as dt_util
        return dt_util.now()

    async def _save(self) -> None:
        await self._store.async_save({
            "remaining_ml": round(self.remaining_ml, 3),
            "last_update": self.last_update.isoformat() if self.last_update else None,
        })

    async def async_advance(self, now: datetime | None = None) -> None:
        """Account once for time since the prior update and persist it."""
        now = now or self._now()
        previous = self.last_update
        if previous is not None and now > previous and self.enabled:
            state = self.device.state
            start_minute = None
            end_minute = None
            if None not in (state.start_hour, state.start_minute, state.end_hour, state.end_minute):
                start_minute = state.start_hour * 60 + state.start_minute
                end_minute = state.end_hour * 60 + state.end_minute
            self.remaining_ml = estimate_consumption_ml(
                remaining_ml=self.remaining_ml,
                start=previous,
                end=now,
                is_on=state.power,
                schedule_enabled=state.schedule_enabled,
                start_minute=start_minute,
                end_minute=end_minute,
                work_seconds=state.work_seconds or None,
                pause_seconds=state.pause_seconds if state.pause_seconds else None,
                full_duty_consumption_mlh=self.full_duty_mlh,
            )
        self.last_update = now
        await self._save()
        self._notify()

    async def async_set_remaining(self, volume_ml: float) -> None:
        """Apply a manual volume entry immediately and save it."""
        self.remaining_ml = max(0.0, min(self.capacity_ml, float(volume_ml)))
        self.last_update = self._now()
        await self._save()
        self._notify()

    async def async_refill(self) -> None:
        """Set the estimate to the configured bottle capacity."""
        await self.async_set_remaining(self.capacity_ml)

    async def async_update_options(self, options: dict[str, Any]) -> None:
        """Apply options, clamp to capacity and reset the interval baseline."""
        self.enabled = bool(options.get("oil_estimation_enabled", DEFAULT_OIL_ESTIMATION_ENABLED))
        self.capacity_ml = max(1.0, float(options.get("oil_bottle_capacity_ml", DEFAULT_OIL_BOTTLE_CAPACITY_ML)))
        self.full_duty_mlh = max(0.0, float(options.get("oil_full_duty_consumption_mlh", DEFAULT_OIL_FULL_DUTY_CONSUMPTION_MLH)))
        self.low_threshold_percent = max(0.0, min(100.0, float(options.get("oil_low_threshold_percent", DEFAULT_OIL_LOW_THRESHOLD_PERCENT))))
        self.remaining_ml = min(self.remaining_ml, self.capacity_ml)
        self.last_update = self._now()
        await self._save()
        self._notify()

    def _notify(self) -> None:
        for callback in list(getattr(self.device, "_state_callbacks", ())):
            try:
                callback()
            except Exception:
                pass

    def entities(self) -> list:
        """Return estimated-oil entities (caller adds them to its platform)."""
        if not self.enabled:
            return []
        return [EstimatedOilSensor(self), EstimatedOilNumber(self), OilRefillButton(self), EstimatedLowOilBinarySensor(self)]


class _EstimatedOilEntity:
    _attr_has_entity_name = True

    def __init__(self, estimator: OilEstimator, suffix: str) -> None:
        self.estimator = estimator
        device = estimator.device
        self._attr_unique_id = f"{device.unique_id}_estimated_oil_{suffix}"
        self._attr_device_info = device.device_info

    @property
    def available(self) -> bool:
        return self.estimator.enabled and self.estimator.device.available

    async def _write(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class EstimatedOilSensor(_EstimatedOilEntity, SensorEntity):
    """Estimated oil volume in millilitres (never a physical sensor reading)."""
    _attr_name = "Estimated oil remaining"
    _attr_icon = "mdi:cup-water"
    _attr_native_unit_of_measurement = "mL"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, estimator: OilEstimator) -> None:
        super().__init__(estimator, "remaining")

    @property
    def native_value(self) -> float:
        return round(self.estimator.remaining_ml, 1)


class EstimatedOilNumber(_EstimatedOilEntity, NumberEntity):
    """Manual adjustment control for estimated oil remaining."""
    _attr_name = "Estimated oil volume"
    _attr_icon = "mdi:cup-water"
    _attr_native_unit_of_measurement = "mL"
    _attr_native_min_value = 0
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, estimator: OilEstimator) -> None:
        super().__init__(estimator, "volume")

    @property
    def native_value(self) -> float:
        return round(self.estimator.remaining_ml, 1)

    @property
    def native_max_value(self) -> float:
        return self.estimator.capacity_ml

    async def async_set_native_value(self, value: float) -> None:
        await self.estimator.async_set_remaining(value)


class OilRefillButton(_EstimatedOilEntity, ButtonEntity):
    """Reset estimated volume to the configured bottle capacity."""
    _attr_name = "Refill oil"
    _attr_icon = "mdi:water-plus"

    def __init__(self, estimator: OilEstimator) -> None:
        super().__init__(estimator, "refill")

    async def async_press(self) -> None:
        await self.estimator.async_refill()


class EstimatedLowOilBinarySensor(_EstimatedOilEntity, BinarySensorEntity):
    """Low-oil alert derived from estimated volume and configured threshold."""
    _attr_name = "Low oil"
    _attr_icon = "mdi:water-alert"

    def __init__(self, estimator: OilEstimator) -> None:
        super().__init__(estimator, "low")

    @property
    def is_on(self) -> bool:
        return self.estimator.remaining_ml < self.estimator.capacity_ml * self.estimator.low_threshold_percent / 100
