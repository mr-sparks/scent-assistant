"""Persisted, schedule-aware estimated oil tracking.

This module keeps estimated volume separate from device-reported oil telemetry.
The integration's platform setup can use ``OilEstimator.entities()`` when the
option is enabled and call ``async_advance`` on state changes and a timer.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

DEFAULT_OIL_ESTIMATION_ENABLED = False
DEFAULT_OIL_BOTTLE_CAPACITY_ML = 1000.0
DEFAULT_OIL_FULL_DUTY_CONSUMPTION_MLH = 2.25
DEFAULT_OIL_LOW_THRESHOLD_PERCENT = 10.0
OIL_ESTIMATION_INTERVAL = timedelta(minutes=1)
OIL_PERSIST_INTERVAL = timedelta(minutes=15)


def scheduled_seconds_between(
    start: datetime,
    end: datetime,
    start_minute: int,
    end_minute: int,
    weekday_mask: int | None = None,
) -> float:
    """Return the seconds in [start, end) that fall within a daily schedule.

    The window is local wall time and may cross midnight. Equal start/end is
    treated as a full-day window, consistent with a 24-hour schedule.
    """
    if start.tzinfo is not None and end.tzinfo is not None:
        end = end.astimezone(start.tzinfo)
    elif start.tzinfo is not None and end.tzinfo is None:
        end = end.replace(tzinfo=start.tzinfo)
    elif start.tzinfo is None and end.tzinfo is not None:
        start = start.replace(tzinfo=end.tzinfo)
    if end <= start:
        return 0.0
    start_minute = max(0, min(1439, int(start_minute)))
    end_minute = max(0, min(1439, int(end_minute)))
    tz = start.tzinfo
    start_t = time(start_minute // 60, start_minute % 60)
    end_t = time(end_minute // 60, end_minute % 60)
    crosses_midnight = end_minute <= start_minute
    total = 0.0
    day = start.date() - timedelta(days=1)
    last_day = end.date()
    while day <= last_day:
        if weekday_mask is None or (weekday_mask & (1 << day.weekday())):
            window_start = datetime.combine(day, start_t, tzinfo=tz)
            end_day = day + timedelta(days=1) if crosses_midnight else day
            window_end = datetime.combine(end_day, end_t, tzinfo=tz)
            overlap_start = max(start, window_start)
            overlap_end = min(end, window_end)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds()
        day += timedelta(days=1)
    return total


def _week_slots_consumption_ml(
    *,
    start: datetime,
    end: datetime,
    week_slots: list[list[dict[str, Any]]],
    full_duty_consumption_mlh: float,
) -> float:
    """Integrate consumption across Aroma-Link per-weekday schedule slots."""
    if start.tzinfo is not None and end.tzinfo is not None:
        end = end.astimezone(start.tzinfo)
    elif start.tzinfo is not None and end.tzinfo is None:
        end = end.replace(tzinfo=start.tzinfo)
    elif start.tzinfo is None and end.tzinfo is not None:
        start = start.replace(tzinfo=end.tzinfo)
    if end <= start or full_duty_consumption_mlh <= 0:
        return 0.0
    tz = start.tzinfo
    used_ml = 0.0
    day = start.date() - timedelta(days=1)
    last_day = end.date()
    while day <= last_day:
        wd = day.weekday()
        if 0 <= wd < len(week_slots):
            for slot in week_slots[wd]:
                if not slot.get("enabled"):
                    continue
                work = slot.get("work_seconds") or 0
                pause = slot.get("pause_seconds")
                if work <= 0 or pause is None or pause < 0:
                    continue
                sh = slot.get("start_hour")
                sm = slot.get("start_minute")
                eh = slot.get("end_hour")
                em = slot.get("end_minute")
                if None in (sh, sm, eh, em):
                    continue
                start_min = max(0, min(1439, int(sh) * 60 + int(sm)))
                end_min = max(0, min(1439, int(eh) * 60 + int(em)))
                start_t = time(start_min // 60, start_min % 60)
                end_t = time(end_min // 60, end_min % 60)
                window_start = datetime.combine(day, start_t, tzinfo=tz)
                end_day = day + timedelta(days=1) if end_min <= start_min else day
                window_end = datetime.combine(end_day, end_t, tzinfo=tz)
                overlap_start = max(start, window_start)
                overlap_end = min(end, window_end)
                if overlap_end > overlap_start:
                    seconds = (overlap_end - overlap_start).total_seconds()
                    duty = work / (work + pause)
                    used_ml += seconds / 3600 * full_duty_consumption_mlh * duty
        day += timedelta(days=1)
    return used_ml


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
    weekday_mask: int | None = None,
    week_slots: list[list[dict[str, Any]]] | None = None,
) -> float:
    """Calculate an estimated remaining volume for one time interval.

    ``None`` means schedule/device data is incomplete, so the estimate is
    deliberately left unchanged. With a known valid schedule, off/disabled
    periods consume nothing. Work/pause values model the average duty cycle,
    including while the device reports its paused phase.
    """
    if full_duty_consumption_mlh <= 0 or end <= start:
        return remaining_ml
    if is_on is None or not is_on:
        return remaining_ml
    if schedule_enabled is None or not schedule_enabled:
        return remaining_ml

    if week_slots:
        used = _week_slots_consumption_ml(
            start=start,
            end=end,
            week_slots=week_slots,
            full_duty_consumption_mlh=full_duty_consumption_mlh,
        )
        return max(0.0, remaining_ml - used)

    if (
        start_minute is None
        or end_minute is None
        or work_seconds is None
        or pause_seconds is None
        or work_seconds <= 0
        or pause_seconds < 0
    ):
        return remaining_ml
    seconds = scheduled_seconds_between(
        start, end, start_minute, end_minute, weekday_mask=weekday_mask
    )
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
        self._last_saved: datetime | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        """Restore volume and timestamp from Home Assistant's storage."""
        async with self._lock:
            data = await self._store.async_load()
            if data:
                try:
                    self.remaining_ml = max(0.0, min(self.capacity_ml, float(data["remaining_ml"])))
                    stamp = data.get("last_update")
                    self.last_update = datetime.fromisoformat(stamp) if stamp else None
                    if self.last_update is not None and self.last_update.tzinfo is None:
                        self.last_update = self.last_update.astimezone()
                except (KeyError, TypeError, ValueError):
                    self.remaining_ml = self.capacity_ml
                    self.last_update = None
            if self.last_update is None:
                self.last_update = self._now()
                await self._save(force=True)
            else:
                self._last_saved = self.last_update

    @staticmethod
    def _now() -> datetime:
        return dt_util.now()

    async def _save(self, *, force: bool = False) -> None:
        now = self.last_update or self._now()
        if (
            not force
            and self._last_saved is not None
            and (now - self._last_saved) < OIL_PERSIST_INTERVAL
        ):
            return
        await self._store.async_save({
            "remaining_ml": round(self.remaining_ml, 3),
            "last_update": now.isoformat(),
        })
        self._last_saved = now

    def _compute_for_current_state(self, start: datetime, end: datetime) -> float:
        """Evaluate consumption for [start, end) against the current device state."""
        state = self.device.state
        # Radar mode does not follow the time-of-day schedule.
        if getattr(state, "radar_mode", None) == 1:
            return self.remaining_ml

        window_known = getattr(self.device, "schedule_window_read", False)
        durations_known = getattr(self.device, "schedule_durations_read", False)

        start_minute = None
        end_minute = None
        if (
            window_known
            and None not in (state.start_hour, state.start_minute, state.end_hour, state.end_minute)
        ):
            start_minute = state.start_hour * 60 + state.start_minute
            end_minute = state.end_hour * 60 + state.end_minute

        work_seconds = state.work_seconds if durations_known else None
        pause_seconds = state.pause_seconds if durations_known else None

        return estimate_consumption_ml(
            remaining_ml=self.remaining_ml,
            start=start,
            end=end,
            is_on=state.power,
            schedule_enabled=state.schedule_enabled,
            start_minute=start_minute,
            end_minute=end_minute,
            work_seconds=work_seconds,
            pause_seconds=pause_seconds,
            full_duty_consumption_mlh=self.full_duty_mlh,
            weekday_mask=state.weekday_mask,
            week_slots=state.week_slots if window_known else None,
        )

    async def async_advance(self, now: datetime | None = None, *, force_save: bool = False) -> None:
        """Account once for time since the prior update and persist it."""
        if not self.enabled:
            return
        async with self._lock:
            now = now or self._now()
            previous = self.last_update
            if previous is not None and now > previous:
                self.remaining_ml = self._compute_for_current_state(previous, now)
                self.last_update = now
            elif previous is None:
                self.last_update = now
            await self._save(force=force_save)
        self._notify()

    async def async_advance_snapshot(
        self,
        *,
        is_on: bool | None,
        schedule_enabled: bool | None,
        start_minute: int | None,
        end_minute: int | None,
        work_seconds: int | None,
        pause_seconds: int | None,
        weekday_mask: int | None = None,
        week_slots: list[list[dict[str, Any]]] | None = None,
        radar_mode: int | None = None,
        now: datetime | None = None,
        force_save: bool = False,
    ) -> None:
        """Advance using a pre-mutation state snapshot (used by BLE notifications)."""
        if not self.enabled:
            return
        async with self._lock:
            now = now or self._now()
            previous = self.last_update
            if previous is not None and now > previous and radar_mode != 1:
                self.remaining_ml = estimate_consumption_ml(
                    remaining_ml=self.remaining_ml,
                    start=previous,
                    end=now,
                    is_on=is_on,
                    schedule_enabled=schedule_enabled,
                    start_minute=start_minute,
                    end_minute=end_minute,
                    work_seconds=work_seconds,
                    pause_seconds=pause_seconds,
                    full_duty_consumption_mlh=self.full_duty_mlh,
                    weekday_mask=weekday_mask,
                    week_slots=week_slots,
                )
                self.last_update = now
            elif previous is None:
                self.last_update = now
            await self._save(force=force_save)
        self._notify()

    async def async_set_remaining(self, volume_ml: float) -> None:
        """Apply a manual volume entry immediately and save it."""
        async with self._lock:
            self.remaining_ml = max(0.0, min(self.capacity_ml, float(volume_ml)))
            self.last_update = self._now()
            await self._save(force=True)
        self._notify()

    async def async_refill(self) -> None:
        """Set the estimate to the configured bottle capacity."""
        await self.async_set_remaining(self.capacity_ml)

    async def async_update_options(self, options: dict[str, Any]) -> None:
        """Apply options, clamp to capacity and reset the interval baseline."""
        async with self._lock:
            now = self._now()
            if self.enabled and self.last_update is not None and now > self.last_update:
                self.remaining_ml = self._compute_for_current_state(self.last_update, now)
            self.enabled = bool(options.get("oil_estimation_enabled", DEFAULT_OIL_ESTIMATION_ENABLED))
            self.capacity_ml = max(1.0, float(options.get("oil_bottle_capacity_ml", DEFAULT_OIL_BOTTLE_CAPACITY_ML)))
            self.full_duty_mlh = max(0.0, float(options.get("oil_full_duty_consumption_mlh", DEFAULT_OIL_FULL_DUTY_CONSUMPTION_MLH)))
            self.low_threshold_percent = max(0.0, min(100.0, float(options.get("oil_low_threshold_percent", DEFAULT_OIL_LOW_THRESHOLD_PERCENT))))
            self.remaining_ml = min(self.remaining_ml, self.capacity_ml)
            self.last_update = now
            await self._save(force=True)
        self._notify()

    def async_add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity state listener and return an unsubscribe callback."""
        self._listeners.append(callback)

        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _remove

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    def entities(self) -> list:
        """Return estimated-oil entities (caller adds them to its platform)."""
        if not self.enabled:
            return []
        return [
            EstimatedOilSensor(self),
            EstimatedOilNumber(self),
            OilRefillButton(self),
            EstimatedLowOilBinarySensor(self),
        ]


class _EstimatedOilEntity:
    _attr_has_entity_name = True

    def __init__(self, estimator: OilEstimator, suffix: str) -> None:
        self.estimator = estimator
        device = estimator.device
        self._attr_unique_id = f"{device.unique_id}_estimated_oil_{suffix}"
        self._attr_device_info = device.device_info
        self._unsub_listener: Callable[[], None] | None = None
        self._unsub_device: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        return self.estimator.enabled and self.estimator.device.available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_listener = self.estimator.async_add_listener(
            self.async_write_ha_state
        )
        self._unsub_device = self.estimator.device.register_state_callback(
            self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        if self._unsub_device is not None:
            self._unsub_device()
            self._unsub_device = None
        await super().async_will_remove_from_hass()


class EstimatedOilSensor(_EstimatedOilEntity, SensorEntity):
    """Estimated oil volume in millilitres (never a physical sensor reading)."""

    _attr_name = "Estimated oil remaining"
    _attr_icon = "mdi:cup-water"
    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
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
    _attr_device_class = NumberDeviceClass.VOLUME
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
    _attr_native_min_value = 0.0
    _attr_native_step = 0.1
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

    _attr_name = "Reset estimated oil"
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
        return (
            self.estimator.remaining_ml
            < self.estimator.capacity_ml * self.estimator.low_threshold_percent / 100
        )
