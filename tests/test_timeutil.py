"""Storage and display of instants."""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import pytest

from app.core.timeutil import (
    ensure_aware,
    format_clock,
    format_hm,
    format_hms,
    from_iso,
    is_weekday,
    local_date,
    resolve_timezone,
    to_iso,
    to_local,
    utc_now,
)
from tests.conftest import SAST, sast, utc


class TestStorage:
    def test_instants_are_stored_as_utc_with_an_explicit_offset(self):
        stored = to_iso(sast(2026, 9, 14, 14, 30))
        assert stored == "2026-09-14T12:30:00+00:00"
        assert stored.endswith("+00:00")

    def test_a_stored_instant_round_trips_exactly(self):
        moment = sast(2026, 9, 14, 14, 30, 45)
        assert from_iso(to_iso(moment)) == moment

    def test_a_naive_datetime_is_refused(self):
        with pytest.raises(ValueError):
            ensure_aware(_dt.datetime(2026, 9, 14, 14, 30))
        with pytest.raises(ValueError):
            to_iso(_dt.datetime(2026, 9, 14, 14, 30))

    def test_an_offsetless_legacy_value_is_read_as_utc(self):
        """Tolerated on read so an older row never becomes unreadable."""
        assert from_iso("2026-09-14T12:30:00") == utc(2026, 9, 14, 12, 30)

    def test_utc_now_is_aware(self):
        assert utc_now().tzinfo is not None


class TestDisplay:
    def test_an_instant_displays_in_local_time(self):
        assert to_local(utc(2026, 9, 14, 12, 30), SAST).hour == 14

    def test_the_local_date_can_differ_from_the_utc_date(self):
        """22:30 UTC on the 14th is 00:30 on the 15th in Johannesburg."""
        assert local_date(utc(2026, 9, 14, 22, 30), SAST) == _dt.date(2026, 9, 15)

    def test_the_clock_format_is_the_one_used_in_the_sheet(self):
        assert format_clock(utc(2026, 9, 14, 6, 15), SAST) == "08:15"


class TestTimezoneResolution:
    def test_a_named_zone_is_honoured(self):
        assert resolve_timezone("Africa/Johannesburg") == ZoneInfo("Africa/Johannesburg")

    def test_the_zone_is_not_hardcoded(self):
        assert resolve_timezone("Europe/London") == ZoneInfo("Europe/London")

    def test_an_unknown_zone_falls_back_rather_than_crashing(self):
        """A bad setting must never stop the app from opening."""
        assert resolve_timezone("Mars/Olympus_Mons") is not None

    @pytest.mark.parametrize("value", [None, "", "system", "local", "auto"])
    def test_the_system_zone_is_the_default(self, value):
        assert resolve_timezone(value) is not None

    def test_johannesburg_has_no_daylight_saving(self):
        """UTC+2 all year - so a July and a January entry behave the same."""
        january = sast(2026, 1, 15, 12, 0)
        july = sast(2026, 7, 15, 12, 0)
        assert january.utcoffset() == july.utcoffset() == _dt.timedelta(hours=2)


class TestFormatting:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(0, "0:00"), (60, "0:01"), (3600, "1:00"), (3660, "1:01"), (37800, "10:30")],
    )
    def test_durations_read_as_hours_and_minutes(self, seconds, expected):
        assert format_hm(seconds) == expected

    def test_seconds_are_shown_on_the_live_timer(self):
        assert format_hms(3661) == "1:01:01"

    def test_a_negative_duration_is_visible_rather_than_hidden(self):
        assert format_hm(-3600) == "-1:00"

    def test_weekdays_and_weekends(self):
        assert is_weekday(_dt.date(2026, 9, 14))  # Monday
        assert is_weekday(_dt.date(2026, 9, 18))  # Friday
        assert not is_weekday(_dt.date(2026, 9, 19))  # Saturday
        assert not is_weekday(_dt.date(2026, 9, 20))  # Sunday


class TestDaylightSavingSafety:
    """The zone is configurable, so the maths must survive a DST jump."""

    LONDON = ZoneInfo("Europe/London")

    def test_a_day_losing_an_hour_still_measures_real_elapsed_time(self):
        from app.core.calc import split_interval_across_days
        from app.core.models import Interval

        # 29 March 2026: London clocks go forward at 01:00.
        start = _dt.datetime(2026, 3, 29, 0, 30, tzinfo=self.LONDON)
        end = _dt.datetime(2026, 3, 29, 3, 30, tzinfo=self.LONDON)
        span = Interval(start, end)
        pieces = split_interval_across_days(span, self.LONDON)
        # Two real hours pass, even though the clock shows three.
        assert sum(piece.seconds() for _, piece in pieces) == 2 * 3600
