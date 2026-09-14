"""Pause and resume arithmetic. Acceptance checklist 5.

Pause is implemented as spans subtracted from the entry, rather than by
splitting the entry into several rows, so one logical session stays one row
with one description and one trip attached to it.
"""

from __future__ import annotations

from app.core.calc import active_intervals, entry_duration_seconds, subtract_intervals
from app.core.models import EntryCalc, EntryKind, Interval
from tests.conftest import sast


def entry(start, end, *pauses) -> EntryCalc:
    return EntryCalc(
        project_id=1,
        kind=EntryKind.WORK,
        start=start,
        end=end,
        pauses=tuple(pauses),
    )


class TestDurationExcludesPausedTime:
    def test_a_single_pause_is_subtracted(self):
        result = entry(
            sast(2026, 9, 14, 9, 0),
            sast(2026, 9, 14, 12, 0),
            Interval(sast(2026, 9, 14, 10, 0), sast(2026, 9, 14, 10, 30)),
        )
        assert entry_duration_seconds(result) == int(2.5 * 3600)

    def test_several_pauses_are_all_subtracted(self):
        result = entry(
            sast(2026, 9, 14, 9, 0),
            sast(2026, 9, 14, 17, 0),
            Interval(sast(2026, 9, 14, 10, 0), sast(2026, 9, 14, 10, 15)),
            Interval(sast(2026, 9, 14, 13, 0), sast(2026, 9, 14, 14, 0)),
        )
        assert entry_duration_seconds(result) == 8 * 3600 - 15 * 60 - 60 * 60

    def test_accuracy_is_to_the_second(self):
        result = entry(
            sast(2026, 9, 14, 9, 0, 0),
            sast(2026, 9, 14, 9, 0, 59),
            Interval(sast(2026, 9, 14, 9, 0, 10), sast(2026, 9, 14, 9, 0, 17)),
        )
        assert entry_duration_seconds(result) == 59 - 7

    def test_no_pauses_means_the_whole_span(self):
        result = entry(sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 12, 0))
        assert entry_duration_seconds(result) == 3 * 3600

    def test_a_pause_that_covers_the_whole_entry_leaves_nothing(self):
        result = entry(
            sast(2026, 9, 14, 9, 0),
            sast(2026, 9, 14, 10, 0),
            Interval(sast(2026, 9, 14, 8, 0), sast(2026, 9, 14, 11, 0)),
        )
        assert entry_duration_seconds(result) == 0

    def test_overlapping_pauses_are_not_double_subtracted(self):
        result = entry(
            sast(2026, 9, 14, 9, 0),
            sast(2026, 9, 14, 12, 0),
            Interval(sast(2026, 9, 14, 10, 0), sast(2026, 9, 14, 11, 0)),
            Interval(sast(2026, 9, 14, 10, 30), sast(2026, 9, 14, 11, 30)),
        )
        # The paused stretch is 10:00-11:30, i.e. 90 minutes, not 120.
        assert entry_duration_seconds(result) == 3 * 3600 - 90 * 60

    def test_a_pause_outside_the_entry_is_ignored(self):
        result = entry(
            sast(2026, 9, 14, 9, 0),
            sast(2026, 9, 14, 12, 0),
            Interval(sast(2026, 9, 14, 14, 0), sast(2026, 9, 14, 15, 0)),
        )
        assert entry_duration_seconds(result) == 3 * 3600

    def test_a_pause_is_clipped_to_the_entry(self):
        result = entry(
            sast(2026, 9, 14, 9, 0),
            sast(2026, 9, 14, 12, 0),
            Interval(sast(2026, 9, 14, 11, 0), sast(2026, 9, 14, 14, 0)),
        )
        assert entry_duration_seconds(result) == 2 * 3600


class TestActiveIntervals:
    def test_a_pause_splits_the_entry_into_two_active_spans(self):
        result = entry(
            sast(2026, 9, 14, 9, 0),
            sast(2026, 9, 14, 12, 0),
            Interval(sast(2026, 9, 14, 10, 0), sast(2026, 9, 14, 10, 30)),
        )
        spans = active_intervals(result)
        assert len(spans) == 2
        assert spans[0].seconds() == 3600
        assert spans[1].seconds() == int(1.5 * 3600)

    def test_a_running_entry_is_measured_up_to_now(self):
        result = entry(sast(2026, 9, 14, 9, 0), None)
        assert entry_duration_seconds(result, now=sast(2026, 9, 14, 11, 0)) == 2 * 3600

    def test_a_timer_paused_right_now_stops_counting(self):
        result = entry(
            sast(2026, 9, 14, 9, 0),
            None,
            Interval(sast(2026, 9, 14, 10, 0), None),
        )
        # Paused at 10:00 and still paused at 11:00: only one hour counts.
        assert entry_duration_seconds(result, now=sast(2026, 9, 14, 11, 0)) == 3600


class TestSubtractIntervals:
    def test_removing_nothing_returns_the_whole_span(self):
        span = Interval(sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 10, 0))
        assert subtract_intervals(span, []) == [span]

    def test_a_zero_length_hole_changes_nothing(self):
        span = Interval(sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 10, 0))
        hole = Interval(sast(2026, 9, 14, 9, 30), sast(2026, 9, 14, 9, 30))
        assert subtract_intervals(span, [hole]) == [span]
