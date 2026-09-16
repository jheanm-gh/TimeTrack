"""The monotonic/wall-clock reconciliation.

Both clocks are injected, so a laptop sleeping for two hours can be tested
in a millisecond and without touching the system clock.
"""

from __future__ import annotations

import datetime as _dt

from app.core.timeutil import UTC
from app.services.clock import ElapsedClock


class FakeClocks:
    """A controllable pair of clocks that start out in agreement."""

    def __init__(self) -> None:
        self.wall = _dt.datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
        self.mono = 1000.0

    def advance(self, seconds: float) -> None:
        """Time passes normally: both clocks move together."""
        self.wall += _dt.timedelta(seconds=seconds)
        self.mono += seconds

    def jump_wall(self, seconds: float) -> None:
        """Only the wall clock moves: a correction, or a sleeping laptop."""
        self.wall += _dt.timedelta(seconds=seconds)

    def clock(self) -> ElapsedClock:
        return ElapsedClock(
            wall=lambda: self.wall, monotonic=lambda: self.mono, jump_threshold=5.0
        )


class TestNormalRunning:
    def test_now_advances_with_the_monotonic_clock(self):
        clocks = FakeClocks()
        clock = clocks.clock()
        clocks.advance(90)
        assert clock.now() == _dt.datetime(2026, 9, 14, 9, 1, 30, tzinfo=UTC)

    def test_no_jump_is_reported_while_the_clocks_agree(self):
        clocks = FakeClocks()
        clock = clocks.clock()
        for _ in range(10):
            clocks.advance(60)
            assert clock.poll() is None

    def test_tiny_drift_is_not_treated_as_a_jump(self):
        """Event-loop slop must not trigger a reconciliation every tick."""
        clocks = FakeClocks()
        clock = clocks.clock()
        clocks.advance(30)
        clocks.jump_wall(0.4)
        assert clock.poll() is None


class TestWallClockJumps:
    def test_a_forward_jump_is_detected(self):
        """The laptop slept for two hours."""
        clocks = FakeClocks()
        clock = clocks.clock()
        clocks.advance(60)
        clocks.jump_wall(7200)
        jump = clock.poll()
        assert jump is not None
        assert round(jump) == 7200

    def test_a_backward_jump_is_detected(self):
        """NTP pulled the clock back."""
        clocks = FakeClocks()
        clock = clocks.clock()
        clocks.advance(60)
        clocks.jump_wall(-120)
        jump = clock.poll()
        assert jump is not None
        assert round(jump) == -120

    def test_the_clock_reconciles_after_a_jump(self):
        clocks = FakeClocks()
        clock = clocks.clock()
        clocks.advance(60)
        clocks.jump_wall(7200)
        clock.poll()
        # Having reconciled, the next reading follows the corrected wall time
        # and the jump is not reported twice.
        assert clock.poll() is None
        assert clock.now() == clocks.wall

    def test_the_display_does_not_leap_before_reconciliation(self):
        """A correction must not make a running timer jump on screen.

        Until poll() reconciles, 'now' follows the monotonic clock only, so
        the elapsed readout keeps moving at one second per second.
        """
        clocks = FakeClocks()
        clock = clocks.clock()
        clocks.advance(60)
        before = clock.now()
        clocks.jump_wall(7200)
        assert clock.now() == before

    def test_the_size_of_the_last_jump_is_remembered(self):
        clocks = FakeClocks()
        clock = clocks.clock()
        clocks.advance(10)
        clocks.jump_wall(3600)
        clock.poll()
        assert round(clock.last_jump_seconds) == 3600


class TestWallNow:
    def test_wall_now_is_the_raw_clock_for_stored_timestamps(self):
        clocks = FakeClocks()
        clock = clocks.clock()
        clocks.jump_wall(500)
        assert clock.wall_now() == clocks.wall
