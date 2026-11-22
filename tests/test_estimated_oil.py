"""Comprehensive unit tests for estimated-oil schedule, duty-cycle, and entity tracking."""
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from tests.offline import MODULES

estimated_oil = MODULES.estimated_oil
device_module = MODULES.device

scheduled_seconds_between = estimated_oil.scheduled_seconds_between
estimate_consumption_ml = estimated_oil.estimate_consumption_ml
_week_slots_consumption_ml = estimated_oil._week_slots_consumption_ml
OilEstimator = estimated_oil.OilEstimator
EstimatedOilSensor = estimated_oil.EstimatedOilSensor
EstimatedOilNumber = estimated_oil.EstimatedOilNumber
OilRefillButton = estimated_oil.OilRefillButton
EstimatedLowOilBinarySensor = estimated_oil.EstimatedLowOilBinarySensor


class ScheduleSecondsTests(unittest.TestCase):
    """Tests for scheduled_seconds_between calculation."""

    def test_schedule_overlap_standard_window(self) -> None:
        start = datetime(2026, 1, 1, 8, 30)
        end = datetime(2026, 1, 1, 12, 30)
        # Window: 09:00 (540m) to 17:00 (1020m) -> overlap is 09:00 to 12:30 (3.5 hours = 12600s)
        self.assertEqual(scheduled_seconds_between(start, end, 9 * 60, 17 * 60), 3.5 * 3600)

    def test_schedule_overlap_with_midnight_window(self) -> None:
        start = datetime(2026, 1, 1, 23, 30)
        end = datetime(2026, 1, 2, 0, 30)
        # Window: 22:00 to 02:00 -> overlap is 23:30 to 00:30 (1 hour = 3600s)
        self.assertEqual(scheduled_seconds_between(start, end, 22 * 60, 2 * 60), 3600)

    def test_timezone_aware_schedule_overlap(self) -> None:
        tz = timezone(timedelta(hours=-6))
        start = datetime(2026, 1, 1, 6, 0, tzinfo=tz)
        end = datetime(2026, 1, 1, 23, 0, tzinfo=tz)
        self.assertEqual(
            scheduled_seconds_between(start, end, 6 * 60 + 30, 22 * 60),
            15.5 * 3600,
        )

    def test_different_timezones_converted_correctly(self) -> None:
        tz_chicago = timezone(timedelta(hours=-6))
        tz_utc = timezone.utc
        # 06:00 CST is 12:00 UTC
        start = datetime(2026, 1, 1, 6, 0, tzinfo=tz_chicago)
        # 16:00 UTC is 10:00 CST -> 4 hours elapsed (06:00 to 10:00 CST)
        end = datetime(2026, 1, 1, 16, 0, tzinfo=tz_utc)
        # Full day window 00:00 to 00:00
        self.assertEqual(scheduled_seconds_between(start, end, 0, 0), 4 * 3600)

    def test_mixed_naive_and_aware_datetimes_do_not_crash(self) -> None:
        tz = timezone.utc
        start_aware = datetime(2026, 1, 1, 10, 0, tzinfo=tz)
        end_naive = datetime(2026, 1, 1, 12, 0)
        self.assertEqual(scheduled_seconds_between(start_aware, end_naive, 0, 0), 2 * 3600)

        start_naive = datetime(2026, 1, 1, 10, 0)
        end_aware = datetime(2026, 1, 1, 12, 0, tzinfo=tz)
        self.assertEqual(scheduled_seconds_between(start_naive, end_aware, 0, 0), 2 * 3600)

    def test_schedule_overlap_outside_window(self) -> None:
        self.assertEqual(
            scheduled_seconds_between(
                datetime(2026, 1, 1, 8), datetime(2026, 1, 1, 9), 10 * 60, 11 * 60
            ),
            0.0,
        )

    def test_equal_start_end_is_24_hour_schedule(self) -> None:
        # Full day at 00:00 to 00:00
        self.assertEqual(
            scheduled_seconds_between(
                datetime(2026, 1, 1, 8), datetime(2026, 1, 2, 8), 0, 0
            ),
            24 * 3600,
        )
        # Full day at 08:00 to 08:00
        self.assertEqual(
            scheduled_seconds_between(
                datetime(2026, 1, 1, 8), datetime(2026, 1, 2, 8), 480, 480
            ),
            24 * 3600,
        )

    def test_end_before_start_returns_zero(self) -> None:
        self.assertEqual(
            scheduled_seconds_between(
                datetime(2026, 1, 2, 10), datetime(2026, 1, 1, 10), 0, 0
            ),
            0.0,
        )
        self.assertEqual(
            scheduled_seconds_between(
                datetime(2026, 1, 1, 10), datetime(2026, 1, 1, 10), 0, 0
            ),
            0.0,
        )

    def test_minute_bounds_clamped(self) -> None:
        # start_minute < 0 clamped to 0; end_minute > 1439 clamped to 1439
        self.assertEqual(
            scheduled_seconds_between(
                datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 12, 0), -10, 2000
            ),
            12 * 3600,
        )

    def test_weekday_mask_filters_days(self) -> None:
        # 2026-01-01 was a Thursday (weekday() == 3, mask bit: 1 << 3 = 0x08)
        thursday_start = datetime(2026, 1, 1, 10, 0)
        thursday_end = datetime(2026, 1, 1, 12, 0)

        # Matching Thursday (bit 3)
        self.assertEqual(
            scheduled_seconds_between(thursday_start, thursday_end, 0, 0, weekday_mask=0x08),
            2 * 3600,
        )
        # Non-matching Wednesday (bit 2)
        self.assertEqual(
            scheduled_seconds_between(thursday_start, thursday_end, 0, 0, weekday_mask=0x04),
            0.0,
        )
        # Empty mask (0)
        self.assertEqual(
            scheduled_seconds_between(thursday_start, thursday_end, 0, 0, weekday_mask=0),
            0.0,
        )

    def test_weekday_mask_with_bit7_set(self) -> None:
        # Scent Tech sets bit 7 (0x80) on weekdays (e.g. 0x9F = 0x80 | 0x1F for Mon-Fri)
        thursday_start = datetime(2026, 1, 1, 10, 0)
        thursday_end = datetime(2026, 1, 1, 12, 0)
        self.assertEqual(
            scheduled_seconds_between(thursday_start, thursday_end, 0, 0, weekday_mask=0x9F),
            2 * 3600,
        )

    def test_weekday_mask_crossing_midnight(self) -> None:
        # Friday 2026-01-02 (weekday 4, 1<<4 = 0x10). Window: 22:00 Fri to 02:00 Sat.
        # Friday is enabled, Saturday (weekday 5, 0x20) is disabled.
        start = datetime(2026, 1, 2, 22, 0)
        end = datetime(2026, 1, 3, 2, 0)
        self.assertEqual(
            scheduled_seconds_between(start, end, 22 * 60, 2 * 60, weekday_mask=0x10),
            4 * 3600,
        )

    def test_multi_day_span(self) -> None:
        # 3 full days: 2026-01-01 00:00 to 2026-01-04 00:00. Daily 10:00 to 14:00 (4h/day).
        start = datetime(2026, 1, 1, 0, 0)
        end = datetime(2026, 1, 4, 0, 0)
        self.assertEqual(
            scheduled_seconds_between(start, end, 10 * 60, 14 * 60),
            3 * 4 * 3600,
        )


class WeekSlotsConsumptionTests(unittest.TestCase):
    """Tests for _week_slots_consumption_ml integration across weekday slots."""

    def setUp(self) -> None:
        # 7 days (Mon=0 .. Sun=6), each with 5 slots
        self.empty_week = [[{"enabled": False} for _ in range(5)] for _ in range(7)]

    def test_single_active_slot_consumption(self) -> None:
        # Thursday (weekday 3): slot 1 active 10:00 to 12:00, 10s work, 10s pause (50% duty)
        self.empty_week[3][0] = {
            "enabled": True,
            "start_hour": 10, "start_minute": 0,
            "end_hour": 12, "end_minute": 0,
            "work_seconds": 10, "pause_seconds": 10,
        }
        start = datetime(2026, 1, 1, 10, 0)  # Thursday
        end = datetime(2026, 1, 1, 12, 0)
        # 2 hours * 2.0 mL/h * 50% duty = 2.0 mL
        used = _week_slots_consumption_ml(
            start=start, end=end,
            week_slots=self.empty_week,
            full_duty_consumption_mlh=2.0,
        )
        self.assertAlmostEqual(used, 2.0)

    def test_continuous_spray_zero_pause(self) -> None:
        self.empty_week[3][0] = {
            "enabled": True,
            "start_hour": 10, "start_minute": 0,
            "end_hour": 11, "end_minute": 0,
            "work_seconds": 20, "pause_seconds": 0,
        }
        start = datetime(2026, 1, 1, 10, 0)
        end = datetime(2026, 1, 1, 11, 0)
        used = _week_slots_consumption_ml(
            start=start, end=end,
            week_slots=self.empty_week,
            full_duty_consumption_mlh=3.0,
        )
        self.assertAlmostEqual(used, 3.0)

    def test_disabled_or_invalid_slots_skipped(self) -> None:
        self.empty_week[3][0] = {
            "enabled": False,
            "start_hour": 10, "start_minute": 0,
            "end_hour": 12, "end_minute": 0,
            "work_seconds": 10, "pause_seconds": 10,
        }
        self.empty_week[3][1] = {
            "enabled": True,
            "start_hour": 10, "start_minute": 0,
            "end_hour": 12, "end_minute": 0,
            "work_seconds": 0, "pause_seconds": 10,  # invalid work
        }
        self.empty_week[3][2] = {
            "enabled": True,
            "start_hour": None, "start_minute": 0,
            "end_hour": 12, "end_minute": 0,
            "work_seconds": 10, "pause_seconds": 10,
        }
        start = datetime(2026, 1, 1, 10, 0)
        end = datetime(2026, 1, 1, 12, 0)
        used = _week_slots_consumption_ml(
            start=start, end=end,
            week_slots=self.empty_week,
            full_duty_consumption_mlh=2.0,
        )
        self.assertEqual(used, 0.0)

    def test_end_before_start_or_zero_consumption_rate(self) -> None:
        self.assertEqual(
            _week_slots_consumption_ml(
                start=datetime(2026, 1, 2), end=datetime(2026, 1, 1),
                week_slots=self.empty_week, full_duty_consumption_mlh=2.0,
            ),
            0.0,
        )
        self.assertEqual(
            _week_slots_consumption_ml(
                start=datetime(2026, 1, 1), end=datetime(2026, 1, 2),
                week_slots=self.empty_week, full_duty_consumption_mlh=0.0,
            ),
            0.0,
        )

    def test_week_slots_timezone_handling(self) -> None:
        self.empty_week[3][0] = {
            "enabled": True, "start_hour": 10, "start_minute": 0,
            "end_hour": 12, "end_minute": 0, "work_seconds": 10, "pause_seconds": 10,
        }
        tz = timezone.utc
        start_aware = datetime(2026, 1, 1, 10, 0, tzinfo=tz)
        end_aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))
        self.assertAlmostEqual(
            _week_slots_consumption_ml(
                start=start_aware, end=end_aware,
                week_slots=self.empty_week, full_duty_consumption_mlh=2.0,
            ),
            0.0,
        )
        # Mixed aware/naive
        self.assertAlmostEqual(
            _week_slots_consumption_ml(
                start=start_aware, end=datetime(2026, 1, 1, 12, 0),
                week_slots=self.empty_week, full_duty_consumption_mlh=2.0,
            ),
            1.0,
        )
        self.assertAlmostEqual(
            _week_slots_consumption_ml(
                start=datetime(2026, 1, 1, 10, 0), end=datetime(2026, 1, 1, 12, 0, tzinfo=tz),
                week_slots=self.empty_week, full_duty_consumption_mlh=2.0,
            ),
            1.0,
        )


class EstimateConsumptionTests(unittest.TestCase):
    """Tests for estimate_consumption_ml."""

    def test_duty_cycle_consumption_and_clamping(self) -> None:
        kwargs = dict(
            start=datetime(2026, 1, 1, 10), end=datetime(2026, 1, 1, 11),
            is_on=True, schedule_enabled=True, start_minute=0, end_minute=0,
            work_seconds=10, pause_seconds=10, full_duty_consumption_mlh=2.25,
        )
        # 1h * 2.25 * 0.5 = 1.125 consumed -> 10 - 1.125 = 8.875
        self.assertAlmostEqual(estimate_consumption_ml(remaining_ml=10, **kwargs), 8.875)
        # Clamps to 0 when remaining < consumed
        self.assertEqual(estimate_consumption_ml(remaining_ml=0.1, **kwargs), 0.0)

    def test_continuous_spray_zero_pause(self) -> None:
        self.assertAlmostEqual(
            estimate_consumption_ml(
                remaining_ml=10,
                start=datetime(2026, 1, 1, 10),
                end=datetime(2026, 1, 1, 11),
                is_on=True,
                schedule_enabled=True,
                start_minute=0,
                end_minute=0,
                work_seconds=10,
                pause_seconds=0,
                full_duty_consumption_mlh=2.25,
            ),
            7.75,
        )

    def test_no_consumption_when_off_or_outside_schedule(self) -> None:
        common = dict(
            remaining_ml=50,
            start=datetime(2026, 1, 1, 8), end=datetime(2026, 1, 1, 9),
            schedule_enabled=True, start_minute=9 * 60, end_minute=17 * 60,
            work_seconds=10, pause_seconds=20, full_duty_consumption_mlh=2.25,
        )
        self.assertEqual(estimate_consumption_ml(is_on=False, **common), 50)
        self.assertEqual(estimate_consumption_ml(is_on=True, **common), 50)
        self.assertEqual(estimate_consumption_ml(is_on=None, **common), 50)

    def test_missing_schedule_or_duty_data_leaves_estimate_unchanged(self) -> None:
        common = dict(
            remaining_ml=50, start=datetime(2026, 1, 1, 8), end=datetime(2026, 1, 1, 9),
            is_on=True, schedule_enabled=True, start_minute=0, end_minute=1439,
            work_seconds=None, pause_seconds=20, full_duty_consumption_mlh=2.25,
        )
        self.assertEqual(estimate_consumption_ml(**common), 50)

        # Invalid work/pause seconds
        self.assertEqual(estimate_consumption_ml(**{**common, "work_seconds": -5}), 50)
        self.assertEqual(estimate_consumption_ml(**{**common, "work_seconds": 10, "pause_seconds": -1}), 50)

        # Missing start/end minute
        self.assertEqual(estimate_consumption_ml(**{**common, "work_seconds": 10, "start_minute": None}), 50)
        self.assertEqual(estimate_consumption_ml(**{**common, "work_seconds": 10, "end_minute": None}), 50)

        # Schedule disabled or None
        self.assertEqual(
            estimate_consumption_ml(**{**common, "work_seconds": 10, "schedule_enabled": False}), 50
        )
        self.assertEqual(
            estimate_consumption_ml(**{**common, "work_seconds": 10, "schedule_enabled": None}), 50
        )

    def test_rate_zero_or_negative_leaves_unchanged(self) -> None:
        common = dict(
            remaining_ml=50, start=datetime(2026, 1, 1, 8), end=datetime(2026, 1, 1, 9),
            is_on=True, schedule_enabled=True, start_minute=0, end_minute=0,
            work_seconds=10, pause_seconds=10,
        )
        self.assertEqual(estimate_consumption_ml(full_duty_consumption_mlh=0.0, **common), 50)
        self.assertEqual(estimate_consumption_ml(full_duty_consumption_mlh=-1.0, **common), 50)

    def test_week_slots_branch(self) -> None:
        week = [[{"enabled": False} for _ in range(5)] for _ in range(7)]
        week[3][0] = {
            "enabled": True, "start_hour": 10, "start_minute": 0,
            "end_hour": 11, "end_minute": 0, "work_seconds": 10, "pause_seconds": 10,
        }
        res = estimate_consumption_ml(
            remaining_ml=100.0,
            start=datetime(2026, 1, 1, 10), end=datetime(2026, 1, 1, 11),
            is_on=True, schedule_enabled=True,
            start_minute=None, end_minute=None, work_seconds=None, pause_seconds=None,
            full_duty_consumption_mlh=2.0, week_slots=week,
        )
        # 1h * 2.0 * 50% = 1.0 consumed -> 99.0
        self.assertAlmostEqual(res, 99.0)


class MockDiffuserDevice:
    """Mock for ScentDiffuserDevice used in estimator tests."""

    def __init__(self, unique_id: str = "test_diffuser_123") -> None:
        self.unique_id = unique_id
        self.available = True
        self.schedule_window_read = True
        self.schedule_durations_read = True
        self.state = SimpleNamespace(
            power=True,
            schedule_enabled=True,
            start_hour=0,
            start_minute=0,
            end_hour=0,
            end_minute=0,
            work_seconds=10,
            pause_seconds=10,
            weekday_mask=0x7F,
            week_slots=None,
            radar_mode=0,
        )
        self._callbacks = []

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {("scent_assistant", self.unique_id)},
            "name": "Mock Diffuser",
        }

    def register_state_callback(self, cb):
        self._callbacks.append(cb)
        return lambda: self._callbacks.remove(cb) if cb in self._callbacks else None

    def trigger_state_callbacks(self):
        for cb in list(self._callbacks):
            cb()


class OilEstimatorTests(unittest.IsolatedAsyncioTestCase):
    """Lifecycle and calculation tests for OilEstimator."""

    def setUp(self) -> None:
        self.hass = MagicMock()
        self.device = MockDiffuserDevice()
        self.entry = SimpleNamespace(
            entry_id="entry_abc",
            options={
                "oil_estimation_enabled": True,
                "oil_bottle_capacity_ml": 500.0,
                "oil_full_duty_consumption_mlh": 2.0,
                "oil_low_threshold_percent": 15.0,
            },
        )
        self.estimator = OilEstimator(self.hass, self.device, self.entry)

    async def test_async_load_empty_store_initializes_defaults(self) -> None:
        await self.estimator.async_load()
        self.assertEqual(self.estimator.remaining_ml, 500.0)
        self.assertIsNotNone(self.estimator.last_update)
        # Saved data should now exist in store
        saved = await self.estimator._store.async_load()
        self.assertEqual(saved["remaining_ml"], 500.0)

    async def test_async_load_restores_existing_valid_data(self) -> None:
        ts = "2026-01-01T10:00:00+00:00"
        await self.estimator._store.async_save({"remaining_ml": 345.5, "last_update": ts})
        await self.estimator.async_load()
        self.assertEqual(self.estimator.remaining_ml, 345.5)
        self.assertEqual(self.estimator.last_update, datetime.fromisoformat(ts))

    async def test_async_load_handles_corrupt_data(self) -> None:
        await self.estimator._store.async_save({"remaining_ml": "invalid", "last_update": "corrupt"})
        await self.estimator.async_load()
        self.assertEqual(self.estimator.remaining_ml, 500.0)
        self.assertIsNotNone(self.estimator.last_update)

    async def test_async_load_handles_naive_timestamp(self) -> None:
        ts_naive = "2026-01-01T10:00:00"
        await self.estimator._store.async_save({"remaining_ml": 250.0, "last_update": ts_naive})
        await self.estimator.async_load()
        self.assertEqual(self.estimator.remaining_ml, 250.0)
        self.assertIsNotNone(self.estimator.last_update.tzinfo)

    async def test_save_debouncing_and_force(self) -> None:
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        self.estimator.last_update = t0
        await self.estimator._save(force=True)
        self.assertEqual(self.estimator._last_saved, t0)

        # 5 minutes later without force -> not saved
        t1 = t0 + timedelta(minutes=5)
        self.estimator.last_update = t1
        self.estimator.remaining_ml = 400.0
        await self.estimator._save(force=False)
        saved = await self.estimator._store.async_load()
        self.assertNotEqual(saved["remaining_ml"], 400.0)

        # 16 minutes later without force -> saved
        t2 = t0 + timedelta(minutes=16)
        self.estimator.last_update = t2
        self.estimator.remaining_ml = 400.0
        await self.estimator._save(force=False)
        saved = await self.estimator._store.async_load()
        self.assertEqual(saved["remaining_ml"], 400.0)

    async def test_async_advance_computes_consumption(self) -> None:
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.estimator.last_update = t0
        self.estimator.remaining_ml = 100.0

        notified = []
        self.estimator.async_add_listener(lambda: notified.append(True))

        # 2 hours elapsed at 50% duty * 2.0 mL/h = 2.0 mL consumed
        await self.estimator.async_advance(now=t1)
        self.assertAlmostEqual(self.estimator.remaining_ml, 98.0)
        self.assertEqual(self.estimator.last_update, t1)
        self.assertEqual(len(notified), 1)

    async def test_async_advance_disabled_does_nothing(self) -> None:
        self.estimator.enabled = False
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.estimator.last_update = t0
        self.estimator.remaining_ml = 100.0

        await self.estimator.async_advance(now=t1)
        self.assertEqual(self.estimator.remaining_ml, 100.0)
        self.assertEqual(self.estimator.last_update, t0)

    async def test_async_advance_snapshot_uses_pre_mutation_state(self) -> None:
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
        self.estimator.last_update = t0
        self.estimator.remaining_ml = 100.0

        # Snapshot shows power=True even if current device state mutated to power=False
        self.device.state.power = False
        await self.estimator.async_advance_snapshot(
            is_on=True,
            schedule_enabled=True,
            start_minute=0,
            end_minute=0,
            work_seconds=10,
            pause_seconds=10,
            now=t1,
        )
        # 1 hour * 2.0 mL/h * 50% = 1.0 mL consumed
        self.assertAlmostEqual(self.estimator.remaining_ml, 99.0)

    async def test_async_advance_snapshot_skips_in_radar_mode(self) -> None:
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
        self.estimator.last_update = t0
        self.estimator.remaining_ml = 100.0

        await self.estimator.async_advance_snapshot(
            is_on=True,
            schedule_enabled=True,
            start_minute=0,
            end_minute=0,
            work_seconds=10,
            pause_seconds=10,
            radar_mode=1,
            now=t1,
        )
        self.assertEqual(self.estimator.remaining_ml, 100.0)

    async def test_async_advance_snapshot_disabled_does_nothing(self) -> None:
        self.estimator.enabled = False
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
        self.estimator.last_update = t0
        self.estimator.remaining_ml = 100.0

        await self.estimator.async_advance_snapshot(
            is_on=True,
            schedule_enabled=True,
            start_minute=0,
            end_minute=0,
            work_seconds=10,
            pause_seconds=10,
            now=t1,
        )
        self.assertEqual(self.estimator.remaining_ml, 100.0)

    def test_compute_for_current_state_radar_mode(self) -> None:
        self.device.state.radar_mode = 1
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
        self.estimator.remaining_ml = 100.0
        res = self.estimator._compute_for_current_state(t0, t1)
        self.assertEqual(res, 100.0)

    async def test_async_set_remaining_and_refill(self) -> None:
        notified = []
        self.estimator.async_add_listener(lambda: notified.append(True))

        await self.estimator.async_set_remaining(250.0)
        self.assertEqual(self.estimator.remaining_ml, 250.0)
        saved = await self.estimator._store.async_load()
        self.assertEqual(saved["remaining_ml"], 250.0)

        # Clamping to max capacity (500) and min 0
        await self.estimator.async_set_remaining(800.0)
        self.assertEqual(self.estimator.remaining_ml, 500.0)
        await self.estimator.async_set_remaining(-50.0)
        self.assertEqual(self.estimator.remaining_ml, 0.0)

        # Refill resets to capacity
        await self.estimator.async_refill()
        self.assertEqual(self.estimator.remaining_ml, 500.0)
        self.assertEqual(len(notified), 4)

    async def test_async_update_options(self) -> None:
        t0 = self.estimator._now()
        self.estimator.last_update = t0
        self.estimator.remaining_ml = 450.0

        new_options = {
            "oil_estimation_enabled": True,
            "oil_bottle_capacity_ml": 300.0,  # lower than current 450
            "oil_full_duty_consumption_mlh": 3.5,
            "oil_low_threshold_percent": 20.0,
        }
        await self.estimator.async_update_options(new_options)

        self.assertEqual(self.estimator.capacity_ml, 300.0)
        self.assertEqual(self.estimator.full_duty_mlh, 3.5)
        self.assertEqual(self.estimator.low_threshold_percent, 20.0)
        # Clamped to new capacity
        self.assertEqual(self.estimator.remaining_ml, 300.0)

    def test_listener_unsubscribe(self) -> None:
        notified = []
        unsub = self.estimator.async_add_listener(lambda: notified.append(True))
        self.estimator._notify()
        self.assertEqual(len(notified), 1)

        unsub()
        self.estimator._notify()
        self.assertEqual(len(notified), 1)


class EstimatedOilEntitiesTests(unittest.IsolatedAsyncioTestCase):
    """Tests for the 4 estimated oil entities."""

    def setUp(self) -> None:
        self.hass = MagicMock()
        self.device = MockDiffuserDevice()
        self.entry = SimpleNamespace(
            entry_id="entry_abc",
            options={
                "oil_estimation_enabled": True,
                "oil_bottle_capacity_ml": 500.0,
                "oil_full_duty_consumption_mlh": 2.0,
                "oil_low_threshold_percent": 10.0,
            },
        )
        self.estimator = OilEstimator(self.hass, self.device, self.entry)
        self.estimator.remaining_ml = 350.5

    def test_entities_empty_when_disabled(self) -> None:
        self.estimator.enabled = False
        self.assertEqual(self.estimator.entities(), [])

    def test_entities_created_when_enabled(self) -> None:
        entities = self.estimator.entities()
        self.assertEqual(len(entities), 4)
        types = {type(e) for e in entities}
        self.assertEqual(
            types,
            {
                EstimatedOilSensor,
                EstimatedOilNumber,
                OilRefillButton,
                EstimatedLowOilBinarySensor,
            },
        )

    async def test_estimated_oil_sensor(self) -> None:
        sensor = EstimatedOilSensor(self.estimator)
        self.assertEqual(sensor.native_value, 350.5)
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.unique_id, "test_diffuser_123_estimated_oil_remaining")

        # Updates on entity write state
        writes = []
        sensor.async_write_ha_state = lambda: writes.append(True)
        await sensor.async_added_to_hass()

        self.estimator._notify()
        self.assertEqual(len(writes), 1)

        # Device availability change notifies entity
        self.device.trigger_state_callbacks()
        self.assertEqual(len(writes), 2)

        # When device unavailable or estimator disabled
        self.device.available = False
        self.assertFalse(sensor.available)
        self.device.available = True
        self.estimator.enabled = False
        self.assertFalse(sensor.available)

        await sensor.async_will_remove_from_hass()

    async def test_estimated_oil_number(self) -> None:
        number = EstimatedOilNumber(self.estimator)
        self.assertEqual(number.native_value, 350.5)
        self.assertEqual(number.native_max_value, 500.0)
        self.assertEqual(number.unique_id, "test_diffuser_123_estimated_oil_volume")

        await number.async_set_native_value(200.0)
        self.assertEqual(self.estimator.remaining_ml, 200.0)

    async def test_oil_refill_button(self) -> None:
        button = OilRefillButton(self.estimator)
        self.assertEqual(button.unique_id, "test_diffuser_123_estimated_oil_refill")

        self.estimator.remaining_ml = 50.0
        await button.async_press()
        self.assertEqual(self.estimator.remaining_ml, 500.0)

    def test_estimated_low_oil_binary_sensor(self) -> None:
        sensor = EstimatedLowOilBinarySensor(self.estimator)
        self.assertEqual(sensor.unique_id, "test_diffuser_123_estimated_oil_low")

        # Threshold is 10% of 500 = 50 mL
        self.estimator.remaining_ml = 51.0
        self.assertFalse(sensor.is_on)

        self.estimator.remaining_ml = 50.0
        self.assertFalse(sensor.is_on)

        self.estimator.remaining_ml = 49.9
        self.assertTrue(sensor.is_on)

        self.estimator.remaining_ml = 0.0
        self.assertTrue(sensor.is_on)


class DeviceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Integration tests for ScentDiffuserDevice oil estimator handling."""

    async def test_device_oil_advance_and_pre_mutation_snapshot(self) -> None:
        DeviceClass = device_module.ScentDiffuserDevice
        device = DeviceClass(
            ble_address="02:00:00:00:00:01",
            ble_name="Test Diffuser",
            device_type=MODULES.const.DeviceType.AROMA_LINK,
        )
        hass = MagicMock()
        entry = SimpleNamespace(
            entry_id="entry_int_test",
            options={
                "oil_estimation_enabled": True,
                "oil_bottle_capacity_ml": 1000.0,
                "oil_full_duty_consumption_mlh": 2.0,
                "oil_low_threshold_percent": 10.0,
            },
        )
        estimator = OilEstimator(hass, device, entry)
        await estimator.async_load()
        device.oil_estimator = estimator

        # Advance method delegates to estimator
        t0 = datetime.now(timezone.utc) - timedelta(hours=1)
        estimator.last_update = t0
        estimator.remaining_ml = 1000.0
        await device._advance_oil_estimate(force_save=True)
        # Advance ran and updated last_update
        self.assertGreater(estimator.last_update, t0)

        # Verify async_shutdown force-saves
        with unittest.mock.patch.object(device, "_advance_oil_estimate", AsyncMock()) as mock_adv:
            await device.async_shutdown()
            mock_adv.assert_awaited_once_with(force_save=True)


if __name__ == "__main__":
    unittest.main()
