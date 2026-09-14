"""Submission periods and deadlines."""

from __future__ import annotations

import datetime as _dt

import pytest

from app.core.calc import (
    ValidationError,
    clamp_day,
    cutoff_period,
    days_until,
    last_working_day,
    month_period,
    next_period,
    period_for_date,
    previous_period,
    submission_deadline,
)
from app.core.models import LAST_WORKING_DAY, PeriodType

D = _dt.date


class TestCalendarMonth:
    def test_a_month_runs_first_to_last(self):
        period = month_period(D(2026, 9, 14))
        assert period.start == D(2026, 9, 1)
        assert period.end == D(2026, 9, 30)
        assert period.label == "September 2026"

    def test_february_in_a_leap_year(self):
        assert month_period(D(2024, 2, 10)).end == D(2024, 2, 29)

    def test_february_in_a_normal_year(self):
        assert month_period(D(2026, 2, 10)).end == D(2026, 2, 28)

    def test_a_month_has_the_right_number_of_dates(self):
        assert len(month_period(D(2026, 9, 14)).dates()) == 30


class TestCutoffCycle:
    """A 26th-to-25th cycle, which is the example the user gave."""

    def test_a_date_after_the_cutoff_is_in_the_period_starting_this_month(self):
        period = cutoff_period(D(2026, 9, 28), 26)
        assert period.start == D(2026, 9, 26)
        assert period.end == D(2026, 10, 25)

    def test_a_date_before_the_cutoff_is_in_the_period_from_last_month(self):
        period = cutoff_period(D(2026, 9, 14), 26)
        assert period.start == D(2026, 8, 26)
        assert period.end == D(2026, 9, 25)

    def test_the_cutoff_day_itself_starts_a_new_period(self):
        assert cutoff_period(D(2026, 9, 26), 26).start == D(2026, 9, 26)

    def test_the_day_before_the_cutoff_ends_the_old_one(self):
        assert cutoff_period(D(2026, 9, 25), 26).end == D(2026, 9, 25)

    def test_periods_are_contiguous_and_never_overlap(self):
        """Walk a year and check every date lands in exactly one period."""
        day = D(2026, 1, 1)
        period = cutoff_period(day, 26)
        for _ in range(14):
            following = next_period(period, PeriodType.CUSTOM_CUTOFF, 26)
            assert following.start == period.end + _dt.timedelta(days=1)
            period = following

    def test_a_31st_cutoff_is_pulled_back_in_short_months(self):
        period = cutoff_period(D(2026, 2, 15), 31)
        assert period.start == D(2026, 1, 31)
        assert period.end == D(2026, 2, 27)  # day before the clamped 28th

    def test_clamp_day_never_overshoots_the_month(self):
        assert clamp_day(2026, 2, 31) == D(2026, 2, 28)
        assert clamp_day(2024, 2, 31) == D(2024, 2, 29)
        assert clamp_day(2026, 9, 15) == D(2026, 9, 15)


class TestPeriodNavigation:
    def test_next_and_previous_are_inverses_for_months(self):
        period = month_period(D(2026, 9, 14))
        forward = next_period(period, PeriodType.CALENDAR_MONTH)
        assert forward.start == D(2026, 10, 1)
        assert previous_period(forward, PeriodType.CALENDAR_MONTH).start == period.start

    def test_crossing_a_year_boundary(self):
        period = month_period(D(2026, 12, 5))
        assert next_period(period, PeriodType.CALENDAR_MONTH).start == D(2027, 1, 1)

    def test_a_cutoff_project_needs_a_start_day(self):
        with pytest.raises(ValidationError) as caught:
            period_for_date(D(2026, 9, 14), PeriodType.CUSTOM_CUTOFF, None)
        assert "cutoff cycle" in str(caught.value)


class TestDeadlines:
    def test_a_day_number_after_the_period_ends_falls_in_the_next_month(self):
        """A calendar month with a 5th deadline is due on the 5th of the
        month after - which is how a monthly timesheet normally works."""
        assert submission_deadline(D(2026, 9, 30), "5") == D(2026, 10, 5)

    def test_a_day_number_on_or_after_the_period_end_stays_in_that_month(self):
        assert submission_deadline(D(2026, 9, 25), "26") == D(2026, 9, 26)

    def test_last_working_day_skips_a_weekend(self):
        # 31 May 2026 is a Sunday, so the last working day is Friday the 29th.
        assert last_working_day(2026, 5) == D(2026, 5, 29)
        assert submission_deadline(D(2026, 5, 31), LAST_WORKING_DAY) == D(2026, 5, 29)

    def test_last_working_day_when_the_month_ends_on_a_weekday(self):
        # 30 September 2026 is a Wednesday.
        assert last_working_day(2026, 9) == D(2026, 9, 30)

    def test_no_deadline_set_is_none(self):
        assert submission_deadline(D(2026, 9, 30), None) is None
        assert submission_deadline(D(2026, 9, 30), "") is None

    def test_a_deadline_day_beyond_the_month_is_clamped(self):
        assert submission_deadline(D(2026, 1, 31), "31") == D(2026, 1, 31)
        assert submission_deadline(D(2026, 2, 28), "31") == D(2026, 2, 28)

    def test_an_impossible_day_number_is_rejected(self):
        with pytest.raises(ValidationError):
            submission_deadline(D(2026, 9, 30), "45")
        with pytest.raises(ValidationError):
            submission_deadline(D(2026, 9, 30), "nonsense")

    def test_days_until_counts_forwards_and_backwards(self):
        assert days_until(D(2026, 9, 19), D(2026, 9, 14)) == 5
        assert days_until(D(2026, 9, 10), D(2026, 9, 14)) == -4
        assert days_until(None, D(2026, 9, 14)) is None
