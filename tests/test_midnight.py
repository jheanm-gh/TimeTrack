"""Midnight rollover: a session spanning midnight splits across both dates
without changing the total. Acceptance checklist 20.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from app.core.calc import daily_rollup, split_interval_across_days
from app.core.models import EntryCalc, EntryKind, Interval, RoundingRule
from tests.conftest import SAST, sast

RULE = RoundingRule()


class TestSplitting:
    def test_a_session_across_midnight_lands_on_both_dates(self):
        span = Interval(sast(2026, 9, 14, 22, 30), sast(2026, 9, 15, 1, 15))
        pieces = split_interval_across_days(span, SAST)
        assert [day for day, _ in pieces] == [_dt.date(2026, 9, 14), _dt.date(2026, 9, 15)]
        assert [piece.seconds() for _, piece in pieces] == [90 * 60, 75 * 60]

    def test_the_total_is_unchanged_by_splitting(self):
        span = Interval(sast(2026, 9, 14, 22, 30), sast(2026, 9, 15, 1, 15))
        pieces = split_interval_across_days(span, SAST)
        assert sum(piece.seconds() for _, piece in pieces) == span.seconds()

    def test_a_session_inside_one_day_is_not_split(self):
        span = Interval(sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 17, 0))
        pieces = split_interval_across_days(span, SAST)
        assert len(pieces) == 1
        assert pieces[0][0] == _dt.date(2026, 9, 14)

    def test_a_session_ending_exactly_at_midnight_stays_on_one_day(self):
        """The boundary is half-open: midnight belongs to the next day."""
        span = Interval(sast(2026, 9, 14, 20, 0), sast(2026, 9, 15, 0, 0))
        pieces = split_interval_across_days(span, SAST)
        assert len(pieces) == 1
        assert pieces[0][0] == _dt.date(2026, 9, 14)
        assert pieces[0][1].seconds() == 4 * 3600

    def test_a_session_starting_exactly_at_midnight_is_the_new_day(self):
        span = Interval(sast(2026, 9, 15, 0, 0), sast(2026, 9, 15, 2, 0))
        pieces = split_interval_across_days(span, SAST)
        assert [day for day, _ in pieces] == [_dt.date(2026, 9, 15)]

    def test_a_multi_day_run_covers_every_day_in_between(self):
        """An unattended analysis left running over a long weekend."""
        span = Interval(sast(2026, 9, 11, 18, 0), sast(2026, 9, 14, 6, 0))
        pieces = split_interval_across_days(span, SAST)
        assert [day for day, _ in pieces] == [
            _dt.date(2026, 9, 11),
            _dt.date(2026, 9, 12),
            _dt.date(2026, 9, 13),
            _dt.date(2026, 9, 14),
        ]
        assert [piece.seconds() for _, piece in pieces] == [
            6 * 3600,
            24 * 3600,
            24 * 3600,
            6 * 3600,
        ]
        assert sum(piece.seconds() for _, piece in pieces) == span.seconds()

    def test_splitting_uses_local_midnight_not_utc_midnight(self):
        """The user is at UTC+2, so local midnight is 22:00 UTC.

        Work from 23:00 to 01:00 local is one hour on each date - which a
        naive UTC split would get wrong by two hours.
        """
        span = Interval(sast(2026, 9, 14, 23, 0), sast(2026, 9, 15, 1, 0))
        pieces = split_interval_across_days(span, SAST)
        assert [(day, piece.seconds()) for day, piece in pieces] == [
            (_dt.date(2026, 9, 14), 3600),
            (_dt.date(2026, 9, 15), 3600),
        ]


class TestRollupAcrossMidnight:
    def test_one_entry_becomes_two_rows_with_the_total_preserved(self):
        entry = EntryCalc(
            project_id=1,
            kind=EntryKind.WORK,
            start=sast(2026, 9, 14, 22, 30),
            end=sast(2026, 9, 15, 1, 15),
        )
        rows = daily_rollup([entry], SAST, RULE)
        assert len(rows) == 2
        assert [row.date for row in rows] == [_dt.date(2026, 9, 14), _dt.date(2026, 9, 15)]
        assert [row.work_seconds for row in rows] == [5400, 4500]
        assert sum(row.work_seconds for row in rows) == 9900

    def test_each_date_rounds_its_own_share(self):
        """90 minutes -> 1.50, 75 minutes -> 1.25. Total billed 2.75 for a
        2h45m session, because each calendar day rounds separately."""
        entry = EntryCalc(
            project_id=1,
            kind=EntryKind.WORK,
            start=sast(2026, 9, 14, 22, 30),
            end=sast(2026, 9, 15, 1, 15),
        )
        rows = daily_rollup([entry], SAST, RULE)
        assert [row.work_hours_billed for row in rows] == [
            Decimal("1.50"),
            Decimal("1.25"),
        ]

    def test_kilometres_stay_on_the_day_the_trip_started(self):
        """A drive home after midnight is still that day's trip."""
        entry = EntryCalc(
            project_id=1,
            kind=EntryKind.WORK,
            start=sast(2026, 9, 14, 22, 30),
            end=sast(2026, 9, 15, 1, 15),
            km_travelled=Decimal("180"),
        )
        rows = daily_rollup([entry], SAST, RULE)
        assert rows[0].km == Decimal("180.0")
        assert rows[1].km == Decimal("0.0")

    def test_a_pause_spanning_midnight_is_removed_from_the_right_day(self):
        entry = EntryCalc(
            project_id=1,
            kind=EntryKind.WORK,
            start=sast(2026, 9, 14, 22, 0),
            end=sast(2026, 9, 15, 2, 0),
            pauses=(Interval(sast(2026, 9, 14, 23, 30), sast(2026, 9, 15, 0, 30)),),
        )
        rows = daily_rollup([entry], SAST, RULE)
        # 22:00-23:30 = 90 min on the 14th; 00:30-02:00 = 90 min on the 15th.
        assert [row.work_seconds for row in rows] == [5400, 5400]
