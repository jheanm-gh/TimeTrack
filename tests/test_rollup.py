"""Daily rollup: one row per project per date, rounded once on the day total."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest

from app.core.calc import build_description, daily_rollup, format_sessions, rollup_totals
from app.core.models import EntryCalc, EntryKind, Interval, RoundingRule
from tests.conftest import SAST, sast

RULE = RoundingRule()


def work(project_id, start, end, **kwargs) -> EntryCalc:
    return EntryCalc(project_id=project_id, kind=EntryKind.WORK, start=start, end=end, **kwargs)


def software(project_id, start, end, name="PLAXIS 2D", **kwargs) -> EntryCalc:
    return EntryCalc(
        project_id=project_id,
        kind=EntryKind.SOFTWARE,
        start=start,
        end=end,
        software_name=name,
        **kwargs,
    )


class TestPerProjectPerDayRounding:
    def test_six_ten_minute_calls_on_one_project_bill_one_hour(self):
        """Acceptance checklist 13, through the real rollup path."""
        entries = [
            work(1, sast(2026, 9, 14, 9 + n, 0), sast(2026, 9, 14, 9 + n, 10))
            for n in range(6)
        ]
        rows = daily_rollup(entries, SAST, RULE)
        assert len(rows) == 1
        assert rows[0].work_seconds == 3600
        assert rows[0].work_hours_raw == Decimal("1.00")
        assert rows[0].work_hours_billed == Decimal("1.00")

    def test_making_one_call_eleven_minutes_pushes_the_day_to_1_25(self):
        entries = [
            work(1, sast(2026, 9, 14, 9 + n, 0), sast(2026, 9, 14, 9 + n, 10))
            for n in range(5)
        ]
        entries.append(work(1, sast(2026, 9, 14, 15, 0), sast(2026, 9, 14, 15, 11)))
        rows = daily_rollup(entries, SAST, RULE)
        assert rows[0].work_seconds == 3660
        assert rows[0].work_hours_billed == Decimal("1.25")

    def test_two_projects_on_the_same_day_round_on_their_own_totals(self):
        """Acceptance checklist 13, final clause.

        Each project gets 40 minutes. Individually that is 0.75 each, so
        1.50 in total. Rounding the *combined* 80 minutes would give 1.50
        as well, so the test uses amounts where the two differ: 40 + 50
        minutes rounds to 0.75 + 1.00 = 1.75 per project, but 1.50 if the
        day were pooled across projects.
        """
        entries = [
            work(1, sast(2026, 9, 14, 8, 0), sast(2026, 9, 14, 8, 40)),
            work(2, sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 9, 50)),
        ]
        rows = daily_rollup(entries, SAST, RULE)
        assert len(rows) == 2
        by_project = {row.project_id: row for row in rows}
        assert by_project[1].work_hours_billed == Decimal("0.75")
        assert by_project[2].work_hours_billed == Decimal("1.00")
        combined = sum(row.work_hours_billed for row in rows)
        assert combined == Decimal("1.75")
        # Pooling the two projects would have billed less; prove they differ.
        pooled_seconds = sum(row.work_seconds for row in rows)
        from app.core.calc import round_seconds_to_hours

        assert round_seconds_to_hours(pooled_seconds, RULE) == Decimal("1.50")

    def test_same_project_on_two_days_rounds_each_day_separately(self):
        entries = [
            work(1, sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 9, 40)),
            work(1, sast(2026, 9, 15, 9, 0), sast(2026, 9, 15, 9, 40)),
        ]
        rows = daily_rollup(entries, SAST, RULE)
        assert [row.work_hours_billed for row in rows] == [
            Decimal("0.75"),
            Decimal("0.75"),
        ]


class TestWorkAndSoftwareAreIndependent:
    def test_software_hours_can_exceed_work_hours(self):
        """An unattended overnight analysis: lots of software, little work."""
        entries = [
            work(1, sast(2026, 9, 14, 16, 0), sast(2026, 9, 14, 16, 30)),
            software(1, sast(2026, 9, 14, 16, 0), sast(2026, 9, 14, 23, 59)),
        ]
        rows = daily_rollup(entries, SAST, RULE)
        row = rows[0]
        assert row.work_hours_billed == Decimal("0.50")
        assert row.software_hours_billed > row.work_hours_billed
        assert row.software_names == ("PLAXIS 2D",)

    def test_a_day_of_software_only_still_produces_a_row(self):
        entries = [software(1, sast(2026, 9, 14, 22, 0), sast(2026, 9, 14, 23, 0))]
        rows = daily_rollup(entries, SAST, RULE)
        assert len(rows) == 1
        assert rows[0].work_seconds == 0
        assert rows[0].work_hours_billed == Decimal("0.00")
        assert rows[0].software_hours_billed == Decimal("1.00")
        # With no work that day, the shape columns describe the software run
        # rather than being mysteriously blank.
        assert rows[0].sessions_kind is EntryKind.SOFTWARE
        assert rows[0].first_start is not None

    def test_overlapping_work_and_software_both_count_in_full(self):
        """They are additive, not a subset: sitting at the machine counts twice."""
        entries = [
            work(1, sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 12, 0)),
            software(1, sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 12, 0)),
        ]
        rows = daily_rollup(entries, SAST, RULE)
        assert rows[0].work_hours_billed == Decimal("3.00")
        assert rows[0].software_hours_billed == Decimal("3.00")

    def test_several_packages_are_listed_alphabetically(self):
        entries = [
            software(1, sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 10, 0), "RS2"),
            software(1, sast(2026, 9, 14, 11, 0), sast(2026, 9, 14, 12, 0), "GeoStudio"),
        ]
        rows = daily_rollup(entries, SAST, RULE)
        assert rows[0].software_names == ("GeoStudio", "RS2")


class TestSessionsAndShape:
    def test_sessions_show_the_shape_of_the_day(self):
        entries = [
            work(1, sast(2026, 9, 14, 8, 15), sast(2026, 9, 14, 10, 30)),
            work(1, sast(2026, 9, 14, 13, 0), sast(2026, 9, 14, 16, 45)),
        ]
        rows = daily_rollup(entries, SAST, RULE)
        assert format_sessions(list(rows[0].sessions), SAST) == "08:15–10:30; 13:00–16:45"

    def test_first_start_and_last_end_span_the_day(self):
        entries = [
            work(1, sast(2026, 9, 14, 13, 0), sast(2026, 9, 14, 16, 45)),
            work(1, sast(2026, 9, 14, 8, 15), sast(2026, 9, 14, 10, 30)),
        ]
        row = daily_rollup(entries, SAST, RULE)[0]
        assert row.first_start.astimezone(SAST).hour == 8
        assert row.last_end.astimezone(SAST).hour == 16

    def test_touching_sessions_are_merged_for_display(self):
        entries = [
            work(1, sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 10, 0)),
            work(1, sast(2026, 9, 14, 10, 0), sast(2026, 9, 14, 11, 0)),
        ]
        row = daily_rollup(entries, SAST, RULE)[0]
        assert format_sessions(list(row.sessions), SAST) == "09:00–11:00"
        assert row.work_seconds == 7200  # merging is display only


class TestDescriptions:
    def test_descriptions_are_joined_and_deduplicated(self):
        entries = [
            work(1, sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 10, 0), description="Site visit"),
            work(1, sast(2026, 9, 14, 11, 0), sast(2026, 9, 14, 12, 0), description="Site visit"),
            work(1, sast(2026, 9, 14, 13, 0), sast(2026, 9, 14, 14, 0), description="Slope analysis"),
        ]
        row = daily_rollup(entries, SAST, RULE)[0]
        assert build_description(list(row.descriptions)) == "Site visit; Slope analysis"

    def test_blank_descriptions_are_dropped(self):
        assert build_description(["", "  ", "Real text"]) == "Real text"

    def test_long_descriptions_are_truncated_visibly(self):
        result = build_description(["x" * 600], limit=500)
        assert len(result) == 500
        assert result.endswith("…")


class TestKilometres:
    def test_km_are_totalled_per_project_per_day(self):
        entries = [
            work(1, sast(2026, 9, 14, 7, 0), sast(2026, 9, 14, 8, 0), km_travelled=Decimal("120")),
            work(1, sast(2026, 9, 14, 16, 0), sast(2026, 9, 14, 17, 0), km_travelled=Decimal("118.5")),
        ]
        row = daily_rollup(entries, SAST, RULE)[0]
        assert row.km == Decimal("238.5")

    def test_a_trip_with_no_hours_still_creates_a_row(self):
        """A standalone site trip: kilometres, no time."""
        moment = sast(2026, 9, 14, 12, 0)
        entries = [work(1, moment, moment, km_travelled=Decimal("240"))]
        rows = daily_rollup(entries, SAST, RULE)
        assert len(rows) == 1
        assert rows[0].km == Decimal("240.0")
        assert rows[0].work_seconds == 0


class TestTotals:
    def test_totals_aggregate_seconds_not_rounded_values(self):
        entries = [
            work(1, sast(2026, 9, 14, 9, 0), sast(2026, 9, 14, 9, 40)),
            work(1, sast(2026, 9, 15, 9, 0), sast(2026, 9, 15, 9, 40)),
        ]
        rows = daily_rollup(entries, SAST, RULE)
        totals = rollup_totals(rows)
        assert totals["work_seconds"] == 4800
        assert totals["work_hours_raw"] == Decimal("1.33")
        # Each day bills 0.75, so the month bills 1.50 - and the gap between
        # raw and billed is what the Summary sheet reports.
        assert totals["work_hours_billed"] == Decimal("1.50")
        assert totals["work_rounding_gap"] == Decimal("0.17")

    def test_empty_input_is_safe(self):
        assert daily_rollup([], SAST, RULE) == []
        totals = rollup_totals([])
        assert totals["work_seconds"] == 0
        assert totals["km"] == Decimal("0.0")
