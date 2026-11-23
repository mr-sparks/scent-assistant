"""Freshness of phase and countdown reports already received from Aroma-Link."""
from dataclasses import dataclass

PHASE_MIN_MAX_AGE = 660
COUNTDOWN_MAX_AGE = 10
_COUNTER = {"spraying": "work_remaining", "paused": "pause_remaining"}


@dataclass
class AromaLinkReport:
    """Store single-frame evidence independently from cached optimistic state."""

    phase_max_age: int = PHASE_MIN_MAX_AGE
    power: bool | None = None
    power_at: float | None = None
    phase: str | None = None
    phase_at: float | None = None
    countdown: int | None = None

    def ingest(self, updates: dict, received_at: float) -> bool:
        """Consume relevant fields only; an unrelated report changes no receipt."""
        reported_power = updates.get("power")
        reported_phase = updates.get("phase")
        if isinstance(reported_power, bool):
            if reported_power is not self.power or (
                self.power_at is None
                or not 0 <= received_at - self.power_at < self.phase_max_age
            ):
                self.phase = None
                self.phase_at = None
                self.countdown = None
            self.power = reported_power
            self.power_at = received_at
        if reported_phase == "off" and reported_power is False:
            self.phase, self.phase_at, self.countdown = "off", received_at, None
            return True
        if reported_phase not in ("idle", "spraying", "paused") or not (
            self.power is True and self.power_at is not None
            and 0 <= received_at - self.power_at < self.phase_max_age
        ):
            return isinstance(reported_power, bool)
        self.phase = reported_phase
        self.phase_at = received_at
        key = _COUNTER.get(reported_phase)
        # A short phase-only report never extends a cached countdown. Zero
        # from the current phase is distinct from absence.
        self.countdown = updates.get(key) if key else None
        return True

    def current_phase(self, now: float) -> str | None:
        if self.phase_at is None or not 0 <= now - self.phase_at < self.phase_max_age:
            return None
        if self.phase != "off" and (
            self.power_at is None or not 0 <= now - self.power_at < self.phase_max_age
        ):
            return None
        return self.phase

    def current_countdown(self, phase: str, now: float) -> int | None:
        if self.current_phase(now) != phase or self.phase_at is None:
            return None
        if 0 <= now - self.phase_at < COUNTDOWN_MAX_AGE:
            return self.countdown
        return None

    def next_expiry(self, now: float) -> float | None:
        """Return the next local publication deadline, never a query deadline."""
        deadlines = []
        if self.current_phase(now) is not None:
            deadlines.append(self.phase_at + self.phase_max_age)
            if self.phase != "off" and self.power_at is not None:
                deadlines.append(self.power_at + self.phase_max_age)
            if self.countdown is not None:
                deadline = self.phase_at + COUNTDOWN_MAX_AGE
                if deadline > now:
                    deadlines.append(deadline)
        return min(deadlines, default=None)
