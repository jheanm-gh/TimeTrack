"""The demo database must stay buildable and internally consistent."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from app.core.calc import daily_rollup, rollup_totals, seconds_to_hours
from app.core.models import EntryKind
from app.seed import build_demo


class TestDemoDatabase:
    def test_it_builds_and_contains_a_few_weeks_of_work(self, tmp_path):
        repo, summary = build_demo(
            tmp_path / "demo.db", today=_dt.date(2026, 9, 14), weeks=6
        )
        assert summary["entries"] > 50
        assert summary["day_rows"] > 20
        assert summary["totals"]["work_seconds"] > 0
        repo.conn.close()

    def test_it_is_reproducible(self, tmp_path):
        """A fixed seed, so the same command gives the same weeks."""
        first, summary_a = build_demo(tmp_path / "a.db", today=_dt.date(2026, 9, 14))
        second, summary_b = build_demo(tmp_path / "b.db", today=_dt.date(2026, 9, 14))
        assert summary_a["entries"] == summary_b["entries"]
        assert summary_a["totals"] == summary_b["totals"]
        first.conn.close()
        second.conn.close()

    def test_it_includes_travel_with_and_without_odometer_readings(self, tmp_path):
        repo, _ = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        travel = [e for e in repo.list_entries() if e.travel.km_travelled is not None]
        assert len(travel) > 3
        assert any(e.travel.odo_start is not None for e in travel)
        assert any(e.travel.odo_start is None for e in travel)
        repo.conn.close()

    def test_it_includes_a_standalone_trip_with_no_hours(self, tmp_path):
        repo, _ = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        assert any(
            e.duration_seconds == 0 and e.travel.km_travelled
            for e in repo.list_entries()
        )
        repo.conn.close()

    def test_it_includes_software_time_that_crosses_midnight(self, tmp_path):
        repo, _ = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        tz = repo.timezone()
        crossing = [
            entry
            for entry in repo.list_entries()
            if entry.ended_at
            and entry.started_at.astimezone(tz).date()
            != entry.ended_at.astimezone(tz).date()
        ]
        assert len(crossing) >= 2
        assert any(e.kind is EntryKind.SOFTWARE for e in crossing)
        repo.conn.close()

    def test_it_leaves_some_weekdays_unlogged_for_the_nudge(self, tmp_path):
        _, summary = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        assert len(summary["forgotten_weekdays"]) >= 2
        repo = None
        del repo

    def test_it_has_an_archived_project_whose_history_survives(self, tmp_path):
        repo, _ = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        archived = [
            row
            for row in repo.list_projects(include_archived=True)
            if row["status"] == "archived"
        ]
        assert len(archived) == 1
        assert len(repo.list_entries(project_id=archived[0]["id"])) == 1
        repo.conn.close()

    def test_billed_is_never_less_than_raw_when_rounding_up(self, tmp_path):
        """A sanity check across the whole demo, not just one day."""
        repo, _ = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        rows = daily_rollup(
            repo.entries_for_calc(), repo.timezone(), repo.rounding_rule()
        )
        for row in rows:
            assert row.work_hours_billed >= row.work_hours_raw
            assert row.software_hours_billed >= row.software_hours_raw
            # Rounding up can never add a whole increment or more.
            assert row.work_hours_billed - row.work_hours_raw < Decimal("0.25")
        repo.conn.close()

    def test_every_row_bills_its_own_summed_day(self, tmp_path):
        """The core rule, re-checked against every row of the demo."""
        from app.core.calc import round_seconds_to_hours

        repo, _ = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        rule = repo.rounding_rule()
        rows = daily_rollup(repo.entries_for_calc(), repo.timezone(), rule)
        for row in rows:
            assert row.work_hours_billed == round_seconds_to_hours(
                row.work_seconds, rule
            )
            assert row.work_hours_raw == seconds_to_hours(row.work_seconds).quantize(
                Decimal("0.01")
            )
        repo.conn.close()

    def test_totals_reconcile_with_the_entries(self, tmp_path):
        repo, summary = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        entries = repo.list_entries()
        work_seconds = sum(
            e.duration_seconds for e in entries if e.kind is EntryKind.WORK
        )
        rows = daily_rollup(
            repo.entries_for_calc(), repo.timezone(), repo.rounding_rule()
        )
        # Splitting across midnight moves seconds between days but must never
        # create or destroy any.
        assert rollup_totals(rows)["work_seconds"] == work_seconds
        repo.conn.close()


class TestDemoRealism:
    def test_work_entries_never_overlap_each_other(self, tmp_path):
        """Only one work timer runs at a time, so the demo must reflect that.

        A day whose listed sessions do not reconcile with its total is the
        fastest way to make the numbers look untrustworthy.
        """
        from app.core.calc import find_overlaps

        repo, _ = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        work = [
            entry.to_calc()
            for entry in repo.list_entries()
            if entry.kind is EntryKind.WORK
        ]
        assert find_overlaps(work) == []
        repo.conn.close()

    def test_software_time_may_legitimately_overlap_work(self, tmp_path):
        """The two channels are additive, so this overlap is expected."""
        repo, _ = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        rows = daily_rollup(
            repo.entries_for_calc(), repo.timezone(), repo.rounding_rule()
        )
        assert any(row.work_seconds > 0 and row.software_seconds > 0 for row in rows)
        repo.conn.close()

    def test_sessions_reconcile_with_the_day_total(self, tmp_path):
        """What the Sessions column shows must add up to the hours billed."""
        from app.core.calc import merge_intervals

        repo, _ = build_demo(tmp_path / "demo.db", today=_dt.date(2026, 9, 14))
        rows = daily_rollup(
            repo.entries_for_calc(), repo.timezone(), repo.rounding_rule()
        )
        for row in rows:
            if row.sessions_kind is not EntryKind.WORK or not row.sessions:
                continue
            shown = sum(iv.seconds() for iv in merge_intervals(list(row.sessions)))
            assert shown == row.work_seconds, f"{row.date} does not reconcile"
        repo.conn.close()
