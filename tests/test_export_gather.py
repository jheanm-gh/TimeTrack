"""What ends up on each sheet, checked without opening a spreadsheet."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from app.core.models import EntryKind, TravelDetail
from app.export.gather import gather
from tests.conftest import sast

TODAY = _dt.date(2026, 9, 14)


class TestTimesheetSheet:
    def test_one_row_per_project_per_date(self, repo, project):
        other = repo.add_project("Second Job")
        for project_id in (project, other):
            repo.add_manual_entry(
                project_id, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
            )
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 15, 9), ended_at=sast(2026, 9, 15, 10)
        )
        rows = gather(repo, today=TODAY).timesheet
        assert len(rows) == 3
        assert {(row.project, row.date) for row in rows} == {
            ("Kloof Tailings Dam", _dt.date(2026, 9, 14)),
            ("Kloof Tailings Dam", _dt.date(2026, 9, 15)),
            ("Second Job", _dt.date(2026, 9, 14)),
        }

    def test_only_dates_with_recorded_time_appear(self, repo, project):
        """No padding rows: there is nothing to line the sheet up against."""
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
        )
        rows = gather(repo, today=_dt.date(2026, 9, 30)).timesheet
        assert [row.date for row in rows] == [_dt.date(2026, 9, 14)]

    def test_the_billed_figure_is_the_rounded_day_total(self, repo, project):
        for hour in (9, 11, 14):
            repo.add_manual_entry(
                project,
                started_at=sast(2026, 9, 14, hour),
                ended_at=sast(2026, 9, 14, hour, 25),
            )
        row = gather(repo, today=TODAY).timesheet[0]
        assert row.hours_raw == Decimal("1.25")  # 75 minutes
        assert row.hours_billed == Decimal("1.25")

    def test_first_start_last_end_and_sessions_describe_the_day(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 8, 15), ended_at=sast(2026, 9, 14, 10, 30)
        )
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 13), ended_at=sast(2026, 9, 14, 16, 45)
        )
        row = gather(repo, today=TODAY).timesheet[0]
        assert row.first_start == _dt.time(8, 15)
        assert row.last_end == _dt.time(16, 45)
        assert row.sessions == "08:15–10:30; 13:00–16:45"

    def test_a_written_description_replaces_the_generated_one(self, repo, project):
        repo.add_manual_entry(
            project,
            started_at=sast(2026, 9, 14, 9),
            ended_at=sast(2026, 9, 14, 11),
            description="Auto text",
        )
        repo.set_description_override(project, _dt.date(2026, 9, 14), "My narrative")
        assert gather(repo, today=TODAY).timesheet[0].description == "My narrative"

    def test_the_submitted_flag_follows_the_tick(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
        )
        assert gather(repo, today=TODAY).timesheet[0].submitted is False
        repo.set_ticked(project, _dt.date(2026, 9, 14), True)
        assert gather(repo, today=TODAY).timesheet[0].submitted is True

    def test_software_columns_are_separate_from_work(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 10)
        )
        repo.add_manual_entry(
            project,
            EntryKind.SOFTWARE,
            started_at=sast(2026, 9, 14, 20),
            ended_at=sast(2026, 9, 14, 23, 30),
            software_name="PLAXIS 3D",
        )
        row = gather(repo, today=TODAY).timesheet[0]
        assert row.hours_billed == Decimal("1.00")
        assert row.software_billed == Decimal("3.50")
        assert row.software_used == "PLAXIS 3D"


class TestTravelSheet:
    def test_odometer_cells_are_left_blank_when_not_recorded(self, repo, project):
        """A travel logbook with invented readings is worse than one with gaps."""
        repo.log_travel(
            project,
            on_date=_dt.date(2026, 9, 14),
            travel=TravelDetail(km_travelled=Decimal("240"), trip_to="Site"),
        )
        row = gather(repo, today=TODAY).travel[0]
        assert row.km == Decimal("240.0")
        assert row.odo_start is None
        assert row.odo_end is None

    def test_odometer_readings_are_carried_through(self, repo, project):
        repo.log_travel(
            project,
            on_date=_dt.date(2026, 9, 14),
            travel=TravelDetail(
                odo_start=Decimal("104200"),
                odo_end=Decimal("104320"),
                trip_to="Kloof TSF",
                trip_purpose="Site inspection",
            ),
        )
        row = gather(repo, today=TODAY).travel[0]
        assert (row.odo_start, row.odo_end, row.km) == (
            Decimal("104200"),
            Decimal("104320"),
            Decimal("120.0"),
        )
        assert row.destination == "Kloof TSF"
        assert row.reason == "Site inspection"

    def test_entries_without_travel_are_not_on_the_travel_sheet(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
        )
        assert gather(repo, today=TODAY).travel == []

    def test_totals_by_month_project_and_year(self, repo, project):
        other = repo.add_project("Second Job")
        repo.log_travel(
            project, on_date=_dt.date(2026, 9, 14), travel=TravelDetail(km_travelled=Decimal("120"))
        )
        repo.log_travel(
            project, on_date=_dt.date(2026, 8, 14), travel=TravelDetail(km_travelled=Decimal("80"))
        )
        repo.log_travel(
            other, on_date=_dt.date(2026, 9, 20), travel=TravelDetail(km_travelled=Decimal("50"))
        )
        totals = gather(repo, today=TODAY).travel_totals
        assert totals.all_time == Decimal("250.0")
        assert totals.year_to_date == Decimal("250.0")
        assert dict(totals.by_project)["Kloof Tailings Dam"] == Decimal("200.0")
        assert dict(totals.by_month)["September 2026"] == Decimal("170.0")


class TestSummarySheet:
    def test_rows_are_per_project_per_month_with_the_rounding_difference(
        self, repo, project
    ):
        for day in (1, 2):
            repo.add_manual_entry(
                project,
                started_at=sast(2026, 9, day, 9),
                ended_at=sast(2026, 9, day, 9, 40),
            )
        rows = gather(repo, today=TODAY).summary
        assert len(rows) == 1
        row = rows[0]
        assert row.month_label == "September 2026"
        assert row.hours_raw == Decimal("1.33")
        assert row.hours_billed == Decimal("1.50")  # 0.75 twice
        assert row.rounding_difference == Decimal("0.17")

    def test_months_are_kept_apart(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 8, 31, 9), ended_at=sast(2026, 8, 31, 11)
        )
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 1, 9), ended_at=sast(2026, 9, 1, 11)
        )
        assert [row.month_label for row in gather(repo, today=TODAY).summary] == [
            "August 2026",
            "September 2026",
        ]

    def test_the_grand_total_covers_everything(self, repo, project):
        other = repo.add_project("Second Job")
        for project_id in (project, other):
            repo.add_manual_entry(
                project_id, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 10, 40)
            )
        grand = gather(repo, today=TODAY).grand_total
        assert grand.project == "ALL PROJECTS"
        assert grand.hours_raw == Decimal("3.33")
        assert grand.hours_billed == Decimal("3.50")  # 1.75 each

    def test_a_submitted_period_is_marked(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
        )
        assert gather(repo, today=TODAY).summary[0].submitted == ""
        repo.mark_submitted(project, _dt.date(2026, 9, 1), _dt.date(2026, 9, 30))
        assert gather(repo, today=TODAY).summary[0].submitted == "Yes"


class TestOtherSheets:
    def test_the_raw_log_carries_every_entry_and_its_id(self, repo, project):
        first = repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
        )
        second = repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 13), ended_at=sast(2026, 9, 14, 14)
        )
        rows = gather(repo, today=TODAY).raw_log
        assert [row.entry_id for row in rows] == [first, second]
        assert rows[0].duration_hm == "2:00"
        assert rows[0].duration_hours == Decimal("2.00")

    def test_deleted_entries_stay_off_every_sheet(self, repo, project):
        entry_id = repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
        )
        repo.soft_delete_entry(entry_id)
        data = gather(repo, today=TODAY)
        assert data.raw_log == []
        assert data.timesheet == []

    def test_an_edited_entry_is_flagged(self, repo, project):
        entry_id = repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
        )
        repo.update_entry(entry_id, description="corrected")
        assert gather(repo, today=TODAY).raw_log[0].edited == "yes"

    def test_the_project_register_includes_archived_projects(self, repo, project):
        """Archiving hides a project from the pickers, not from the workbook."""
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
        )
        repo.archive_project(project)
        rows = gather(repo, today=TODAY).projects
        assert len(rows) == 1
        assert rows[0].status == "Done"
        assert rows[0].hours_to_date == Decimal("2.00")

    def test_gaps_lists_weekdays_with_nothing_recorded(self, repo, project):
        # 14 September 2026 is a Monday; record only that day.
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 17)
        )
        gaps = gather(repo, today=_dt.date(2026, 9, 18)).gaps
        assert _dt.date(2026, 9, 15) in [row.date for row in gaps]
        assert _dt.date(2026, 9, 14) not in [row.date for row in gaps]
        # Weekends are never gaps.
        assert all(row.date.weekday() < 5 for row in gaps)


class TestEmptyDatabase:
    def test_everything_is_empty_but_nothing_explodes(self, repo):
        data = gather(repo, today=TODAY)
        assert data.timesheet == []
        assert data.raw_log == []
        assert data.travel == []
        assert data.summary == []
        assert data.grand_total is None


class TestGapWindow:
    """A brand-new install must not report the weeks before it existed.

    The catch-up nudge is, in the user's words, the feature that solves his
    real problem. A banner announcing forty missing days on the first launch
    would be wrong, and a banner that is wrong the first time never gets read
    again.
    """

    def test_no_gaps_are_reported_before_the_first_entry(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 17)
        )
        gaps = gather(repo, today=_dt.date(2026, 9, 18)).gaps
        assert all(row.date >= _dt.date(2026, 9, 14) for row in gaps)
        assert gaps  # the days after it are still reported

    def test_an_empty_database_reports_no_gaps_at_all(self, repo):
        assert gather(repo, today=_dt.date(2026, 9, 18)).gaps == []

    def test_a_long_history_still_uses_the_full_window(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 1, 5, 9), ended_at=sast(2026, 1, 5, 17)
        )
        gaps = gather(repo, today=_dt.date(2026, 9, 18)).gaps
        # The sixty-day window applies, not the whole year since January.
        assert min(row.date for row in gaps) >= _dt.date(2026, 7, 1)

    def test_the_first_entry_date_is_read_in_local_time(self, repo, project):
        """22:30 local is the previous day in UTC; the local date is what counts."""
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 0, 30), ended_at=sast(2026, 9, 14, 1, 30)
        )
        assert repo.first_entry_date() == _dt.date(2026, 9, 14)

    def test_deleted_entries_do_not_count_as_history(self, repo, project):
        old = repo.add_manual_entry(
            project, started_at=sast(2026, 1, 5, 9), ended_at=sast(2026, 1, 5, 17)
        )
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 17)
        )
        repo.soft_delete_entry(old)
        assert repo.first_entry_date() == _dt.date(2026, 9, 14)
