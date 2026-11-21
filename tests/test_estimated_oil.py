"""Unit tests for estimated-oil schedule and duty-cycle calculations."""
from datetime import datetime

from custom_components.scent_assistant.estimated_oil import (
    estimate_consumption_ml,
    scheduled_seconds_between,
)


def test_schedule_overlap_with_midnight_window() -> None:
    start = datetime(2026, 1, 1, 23, 30)
    end = datetime(2026, 1, 2, 0, 30)
    assert scheduled_seconds_between(start, end, 22 * 60, 2 * 60) == 3600


def test_schedule_overlap_outside_window() -> None:
    assert scheduled_seconds_between(
        datetime(2026, 1, 1, 8), datetime(2026, 1, 1, 9), 10 * 60, 11 * 60
    ) == 0


def test_duty_cycle_consumption_and_clamping() -> None:
    kwargs = dict(
        start=datetime(2026, 1, 1, 10), end=datetime(2026, 1, 1, 11),
        is_on=True, schedule_enabled=True, start_minute=0, end_minute=23 * 60 + 59,
        work_seconds=10, pause_seconds=10, full_duty_consumption_mlh=2.25,
    )
    assert estimate_consumption_ml(remaining_ml=10, **kwargs) == 8.875
    assert estimate_consumption_ml(remaining_ml=0.1, **kwargs) == 0


def test_no_consumption_when_off_or_outside_schedule() -> None:
    common = dict(
        remaining_ml=50,
        start=datetime(2026, 1, 1, 8), end=datetime(2026, 1, 1, 9),
        schedule_enabled=True, start_minute=9 * 60, end_minute=17 * 60,
        work_seconds=10, pause_seconds=20, full_duty_consumption_mlh=2.25,
    )
    assert estimate_consumption_ml(is_on=False, **common) == 50
    assert estimate_consumption_ml(is_on=True, **common) == 50


def test_missing_schedule_or_duty_data_leaves_estimate_unchanged() -> None:
    common = dict(
        remaining_ml=50, start=datetime(2026, 1, 1, 8), end=datetime(2026, 1, 1, 9),
        is_on=True, schedule_enabled=True, start_minute=0, end_minute=1439,
        work_seconds=None, pause_seconds=20, full_duty_consumption_mlh=2.25,
    )
    assert estimate_consumption_ml(**common) == 50
    assert scheduled_seconds_between(
        datetime(2026, 1, 1, 8), datetime(2026, 1, 2, 8), 0, 0
    ) == 24 * 3600
