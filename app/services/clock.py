"""Reconciling the monotonic clock with the wall clock.

The brief asks for a monotonic clock to drive the elapsed-time display and
the wall clock for the timestamps that get stored. The reason is that they
fail differently:

* the **wall clock** can jump - NTP corrects it, the user changes the
  timezone, the laptop sleeps and wakes. A running timer driven by the wall
  clock would visibly leap or run backwards.
* the **monotonic clock** never jumps, but it has no idea what time it is,
  so it cannot produce a timestamp worth storing.

So: anchor the two together, drive the display from the monotonic delta, and
watch for the pair drifting apart. Drift means the wall clock moved
underneath us, and the anchor has to be reset - that is the reconciliation.
"""

from __future__ import annotations

import datetime as _dt
import time
from collections.abc import Callable

from app.core.timeutil import utc_now

#: How far the two clocks may drift before it counts as a jump rather than
#: ordinary scheduling slop. A sleeping laptop produces minutes of drift; a
#: busy event loop produces milliseconds.
DEFAULT_JUMP_THRESHOLD_SECONDS = 5.0


class ElapsedClock:
    """Tracks 'now' for the running timers, immune to wall-clock jumps.

    Both clock sources are injectable so the behaviour can be tested without
    waiting for real time to pass or changing the system clock.
    """

    def __init__(
        self,
        wall: Callable[[], _dt.datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        jump_threshold: float = DEFAULT_JUMP_THRESHOLD_SECONDS,
    ) -> None:
        self._wall = wall
        self._monotonic = monotonic
        self.jump_threshold = jump_threshold
        self._wall_anchor: _dt.datetime = wall()
        self._mono_anchor: float = monotonic()
        self.last_jump_seconds: float = 0.0

    def anchor(self) -> None:
        """Re-tie the monotonic clock to the current wall-clock reading."""
        self._wall_anchor = self._wall()
        self._mono_anchor = self._monotonic()

    def now(self) -> _dt.datetime:
        """The current instant, advanced monotonically since the last anchor.

        This is what the ticking display uses, so a clock correction cannot
        make a running timer jump forwards or count backwards.
        """
        elapsed = self._monotonic() - self._mono_anchor
        return self._wall_anchor + _dt.timedelta(seconds=elapsed)

    def drift(self) -> float:
        """Seconds by which the wall clock has moved relative to monotonic.

        Positive means the wall clock ran ahead (a forward jump, or the
        machine slept); negative means it was set backwards.
        """
        wall_delta = (self._wall() - self._wall_anchor).total_seconds()
        mono_delta = self._monotonic() - self._mono_anchor
        return wall_delta - mono_delta

    def poll(self) -> float | None:
        """Check for a jump, reconciling if one happened.

        Returns the size of the jump in seconds when one is detected (and
        re-anchors so the display picks up from the corrected time), or
        ``None`` when the two clocks still agree.
        """
        observed = self.drift()
        if abs(observed) < self.jump_threshold:
            return None
        self.last_jump_seconds = observed
        self.anchor()
        return observed

    def wall_now(self) -> _dt.datetime:
        """The raw wall clock, for timestamps that get written to the database."""
        return self._wall()
