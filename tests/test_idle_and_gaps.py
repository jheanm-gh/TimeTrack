"""Idle handling and the catch-up nudge."""

from __future__ import annotations

import datetime as _dt

from app.core.calc import build_idle_adjustment, idle_window, recent_window, weekday_gaps
from app.core.models import EntryCalc, EntryKind, IdleDecision, Interval
from app.core.calc import apply_idle_adjustment, entry_duration_seconds
from tests.conftest import SAST, sast

D = _dt.date


class TestIdleWindow:
    def test_the_window_runs_from_last_input_to_now(self):
        window = idle_window(
            now=sast(2026, 9, 14, 14, 59),
            idle_seconds=47 * 60,
            entry_start=sast(2026, 9, 14, 9, 0),
        )
        assert window.start == sast(2026, 9, 14, 14, 12)
        assert window.end == sast(2026, 9, 14, 14, 59)
        assert window.seconds() == 47 * 60

    def test_the_window_never_starts_before_the_entry_did(self):
        """Walking away, then starting a timer, must not backdate the idle."""
        window = idle_window(
            now=sast(2026, 9, 14, 10, 0),
            idle_seconds=3600,
            entry_start=sast(2026, 9, 14, 9, 30),
        )
        assert window.start == sast(2026, 9, 14, 9, 30)
        assert window.seconds() == 30 * 60

    def test_no_idle_time_produces_no_window(self):
        assert idle_window(sast(2026, 9, 14, 10, 0), 0, sast(2026, 9, 14, 9, 0)) is None

    def test_idle_entirely_before_the_entry_produces_no_window(self):
        assert (
            idle_window(
                now=sast(2026, 9, 14, 9, 0),
                idle_seconds=600,
                entry_start=sast(2026, 9, 14, 9, 0),
            )
            is None
        )


class TestIdleDecisions:
    WINDOW = Interval(sast(2026, 9, 14, 14, 12), sast(2026, 9, 14, 14, 59))

    def test_keep_changes_nothing_but_is_still_logged(self):
        adjustment = build_idle_adjustment(IdleDecision.KEEP, self.WINDOW, SAST)
        assert adjustment.pauses == ()
        assert adjustment.spin_off is None
        assert "kept" in adjustment.note

    def test_discard_removes_exactly_the_idle_stretch(self):
        adjustment = build_idle_adjustment(IdleDecision.DISCARD, self.WINDOW, SAST)
        assert adjustment.pauses == (self.WINDOW,)
        assert adjustment.spin_off is None

    def test_separate_removes_it_and_offers_it_as_its_own_entry(self):
        adjustment = build_idle_adjustment(IdleDecision.SEPARATE, self.WINDOW, SAST)
        assert adjustment.pauses == (self.WINDOW,)
        assert adjustment.spin_off == self.WINDOW

    def test_the_note_records_the_times_and_the_length(self):
        note = build_idle_adjustment(IdleDecision.DISCARD, self.WINDOW, SAST).note
        assert "14:12" in note and "14:59" in note and "47 min" in note

    def test_applying_a_discard_shortens_the_entry_by_the_idle_time(self):
        entry = EntryCalc(
            project_id=1,
            kind=EntryKind.WORK,
            start=sast(2026, 9, 14, 13, 0),
            end=sast(2026, 9, 14, 16, 0),
        )
        assert entry_duration_seconds(entry) == 3 * 3600
        adjusted = apply_idle_adjustment(
            entry, build_idle_adjustment(IdleDecision.DISCARD, self.WINDOW, SAST)
        )
        assert entry_duration_seconds(adjusted) == 3 * 3600 - 47 * 60

    def test_applying_a_keep_leaves_the_entry_alone(self):
        entry = EntryCalc(
            project_id=1,
            kind=EntryKind.WORK,
            start=sast(2026, 9, 14, 13, 0),
            end=sast(2026, 9, 14, 16, 0),
        )
        adjusted = apply_idle_adjustment(
            entry, build_idle_adjustment(IdleDecision.KEEP, self.WINDOW, SAST)
        )
        assert entry_duration_seconds(adjusted) == 3 * 3600


class TestWeekdayGaps:
    def test_weekdays_with_no_time_are_reported(self):
        # 14 Sept 2026 is a Monday.
        recorded = {D(2026, 9, 14), D(2026, 9, 16)}
        gaps = weekday_gaps(recorded, D(2026, 9, 14), D(2026, 9, 18))
        assert gaps == [D(2026, 9, 15), D(2026, 9, 17), D(2026, 9, 18)]

    def test_weekends_are_never_reported_as_gaps(self):
        gaps = weekday_gaps(set(), D(2026, 9, 19), D(2026, 9, 20))  # Sat, Sun
        assert gaps == []

    def test_today_is_excluded_because_the_day_is_not_over(self):
        gaps = weekday_gaps(
            set(), D(2026, 9, 14), D(2026, 9, 16), today=D(2026, 9, 16)
        )
        assert D(2026, 9, 16) not in gaps

    def test_today_can_be_included_on_request(self):
        gaps = weekday_gaps(
            set(), D(2026, 9, 14), D(2026, 9, 16), include_today=True, today=D(2026, 9, 16)
        )
        assert D(2026, 9, 16) in gaps

    def test_a_backwards_window_is_empty_rather_than_an_error(self):
        assert weekday_gaps(set(), D(2026, 9, 18), D(2026, 9, 14)) == []

    def test_recent_window_ends_yesterday(self):
        start, end = recent_window(D(2026, 9, 14), 30)
        assert end == D(2026, 9, 13)
        assert start == D(2026, 8, 15)
        assert (end - start).days == 29
