"""Repository behaviour: timers, edits, the audit trail and soft deletes."""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal

import pytest

from app.core.calc import ValidationError
from app.core.models import (
    EntryKind,
    IdleDecision,
    Interval,
    ProjectStatus,
    RecoveryDecision,
    TaskStatus,
    TravelDetail,
)
from app.core.calc import build_idle_adjustment
from tests.conftest import SAST, sast


class TestProjects:
    def test_a_project_can_be_added_and_found(self, repo):
        project_id = repo.add_project(
            "Kloof Tailings Dam",
            submission_day="5",
            default_site="Kloof TSF",
            default_km=Decimal("120"),
        )
        row = repo.get_project(project_id)
        assert row["name"] == "Kloof Tailings Dam"
        assert row["submission_day"] == "5"
        assert Decimal(row["default_km"]) == Decimal("120")

    def test_duplicate_names_are_refused_in_plain_english(self, repo):
        repo.add_project("Kloof Tailings Dam")
        with pytest.raises(ValidationError) as caught:
            repo.add_project("kloof tailings dam")
        assert "already a project" in str(caught.value)

    def test_a_blank_name_is_refused(self, repo):
        with pytest.raises(ValidationError):
            repo.add_project("   ")

    def test_archiving_hides_the_project_but_keeps_its_history(self, repo):
        """Acceptance checklist 3."""
        project_id = repo.add_project("Old Dam Study")
        repo.add_manual_entry(
            project_id,
            started_at=sast(2026, 9, 1, 9, 0),
            ended_at=sast(2026, 9, 1, 12, 0),
        )
        repo.archive_project(project_id)

        assert [row["id"] for row in repo.list_projects()] == []
        assert project_id in [row["id"] for row in repo.list_projects(include_archived=True)]
        # The history survives.
        assert len(repo.list_entries(project_id=project_id)) == 1

    def test_an_archived_project_can_be_brought_back(self, repo):
        project_id = repo.add_project("Old Dam Study")
        repo.archive_project(project_id)
        repo.unarchive_project(project_id)
        assert project_id in [row["id"] for row in repo.list_projects()]
        assert repo.get_project(project_id)["archived_at"] is None

    def test_bulk_add_creates_several_and_reports_duplicates(self, repo):
        """Pasting the intranet dropdown, one name per line."""
        repo.add_project("Kloof Tailings Dam")
        created, skipped = repo.bulk_add_projects(
            "Kloof Tailings Dam\n"
            "Rustenburg Slimes Dam\n"
            "\n"
            "  Mogalakwena Pit Slope  \n"
            "Rustenburg Slimes Dam\n"
        )
        assert len(created) == 2
        assert skipped == ["Kloof Tailings Dam"]
        names = {row["name"] for row in repo.list_projects()}
        assert "Mogalakwena Pit Slope" in names  # whitespace trimmed

    def test_a_cutoff_project_must_say_when_the_period_starts(self, repo):
        from app.core.models import PeriodType

        with pytest.raises(ValidationError):
            repo.add_project("Cutoff Job", period_type=PeriodType.CUSTOM_CUTOFF)


class TestTasks:
    def test_tasks_belong_to_a_project_and_can_be_completed(self, repo, project):
        task_id = repo.add_task(project, "Slope stability analysis")
        assert [row["id"] for row in repo.list_tasks(project)] == [task_id]
        repo.set_task_status(task_id, TaskStatus.DONE)
        assert repo.list_tasks(project) == []
        assert len(repo.list_tasks(project, include_done=True)) == 1

    def test_nesting_is_limited_to_one_level(self, repo, project):
        parent = repo.add_task(project, "Reporting")
        child = repo.add_task(project, "Draft chapter 3", parent_task_id=parent)
        with pytest.raises(ValidationError) as caught:
            repo.add_task(project, "Figure 3.1", parent_task_id=child)
        assert "one level" in str(caught.value)

    def test_duplicate_task_names_within_a_project_are_refused(self, repo, project):
        repo.add_task(project, "Reporting")
        with pytest.raises(ValidationError):
            repo.add_task(project, "reporting")


class TestTimers:
    def test_start_pause_resume_stop_records_the_right_duration(self, repo, project):
        """Acceptance checklist 5."""
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 9, 0))
        repo.pause_timer(EntryKind.WORK, now=sast(2026, 9, 14, 10, 0))
        repo.resume_timer(EntryKind.WORK, now=sast(2026, 9, 14, 10, 30))
        repo.stop_timer(EntryKind.WORK, now=sast(2026, 9, 14, 12, 0))

        entry = repo.get_entry(entry_id)
        assert entry.duration_seconds == int(2.5 * 3600)
        assert entry.is_running is False
        assert entry.ended_at == sast(2026, 9, 14, 12, 0)

    def test_both_channels_run_at_once_and_record_independently(self, repo, project):
        """Acceptance checklist 6."""
        work_id = repo.start_timer(project, EntryKind.WORK, now=sast(2026, 9, 14, 9, 0))
        soft_id = repo.start_timer(
            project,
            EntryKind.SOFTWARE,
            software_name="PLAXIS 2D",
            now=sast(2026, 9, 14, 9, 30),
        )
        assert len(repo.running_entries()) == 2

        repo.stop_timer(EntryKind.WORK, now=sast(2026, 9, 14, 12, 0))
        assert len(repo.running_entries()) == 1  # software keeps going

        repo.stop_timer(EntryKind.SOFTWARE, now=sast(2026, 9, 14, 18, 30))
        assert repo.get_entry(work_id).duration_seconds == 3 * 3600
        assert repo.get_entry(soft_id).duration_seconds == 9 * 3600
        assert repo.get_entry(soft_id).software_name == "PLAXIS 2D"

    def test_starting_a_second_work_timer_stops_the_first(self, repo, project):
        first = repo.start_timer(project, now=sast(2026, 9, 14, 9, 0))
        second = repo.start_timer(project, now=sast(2026, 9, 14, 10, 0))
        assert repo.get_entry(first).is_running is False
        assert repo.get_entry(first).duration_seconds == 3600
        assert repo.get_entry(second).is_running is True
        assert len([e for e in repo.running_entries() if e.kind is EntryKind.WORK]) == 1

    def test_a_software_timer_needs_a_package_name(self, repo, project):
        with pytest.raises(ValidationError) as caught:
            repo.start_timer(project, EntryKind.SOFTWARE)
        assert "package" in str(caught.value)

    def test_a_work_timer_must_not_carry_a_package_name(self, repo, project):
        with pytest.raises(ValidationError):
            repo.start_timer(project, EntryKind.WORK, software_name="PLAXIS")

    def test_starting_a_timer_requires_a_project(self, repo):
        with pytest.raises(ValidationError):
            repo.start_timer(9999)

    def test_stopping_writes_the_entry_immediately(self, repo, project):
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 9, 0))
        repo.stop_timer(EntryKind.WORK, now=sast(2026, 9, 14, 10, 0))
        # Read through a completely fresh repository object on the same
        # connection: nothing about the entry lived only in memory.
        from app.db.repository import Repository

        assert Repository(repo.conn).get_entry(entry_id).duration_seconds == 3600

    def test_stopping_while_paused_does_not_count_the_paused_tail(self, repo, project):
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 9, 0))
        repo.pause_timer(EntryKind.WORK, now=sast(2026, 9, 14, 10, 0))
        repo.stop_timer(EntryKind.WORK, now=sast(2026, 9, 14, 12, 0))
        assert repo.get_entry(entry_id).duration_seconds == 3600

    def test_the_heartbeat_keeps_a_running_duration_current(self, repo, project):
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 9, 0))
        repo.heartbeat(now=sast(2026, 9, 14, 11, 15))
        entry = repo.get_entry(entry_id)
        assert entry.duration_seconds == int(2.25 * 3600)
        assert entry.heartbeat_at == sast(2026, 9, 14, 11, 15)

    def test_last_work_entry_powers_start_last_task_again(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 13, 9, 0), ended_at=sast(2026, 9, 13, 10, 0)
        )
        latest = repo.add_manual_entry(
            project,
            started_at=sast(2026, 9, 14, 9, 0),
            ended_at=sast(2026, 9, 14, 10, 0),
            description="Slope review",
        )
        assert repo.last_work_entry().id == latest

    def test_software_names_are_remembered_for_the_dropdown(self, repo, project):
        for name, hour in (("PLAXIS 2D", 9), ("RS2", 11), ("Leapfrog", 13)):
            repo.start_timer(
                project, EntryKind.SOFTWARE, software_name=name, now=sast(2026, 9, 14, hour)
            )
            repo.stop_timer(EntryKind.SOFTWARE, now=sast(2026, 9, 14, hour, 30))
        assert repo.software_names()[0] == "Leapfrog"  # most recent first
        assert set(repo.software_names()) == {"PLAXIS 2D", "RS2", "Leapfrog"}


class TestManualEntriesAndTravel:
    def test_a_manual_entry_is_first_class(self, repo, project):
        entry_id = repo.add_manual_entry(
            project,
            started_at=sast(2026, 9, 10, 8, 0),
            ended_at=sast(2026, 9, 10, 11, 30),
            description="Reconstructed from diary",
        )
        entry = repo.get_entry(entry_id)
        assert entry.duration_seconds == int(3.5 * 3600)
        assert entry.source.value == "manual"

    def test_an_end_before_the_start_is_refused(self, repo, project):
        with pytest.raises(ValidationError) as caught:
            repo.add_manual_entry(
                project,
                started_at=sast(2026, 9, 10, 11, 0),
                ended_at=sast(2026, 9, 10, 8, 0),
            )
        assert "before the start" in str(caught.value)

    def test_a_standalone_trip_needs_no_hours(self, repo, project):
        """Acceptance checklist 11."""
        entry_id = repo.log_travel(
            project,
            on_date=_dt.date(2026, 9, 14),
            travel=TravelDetail(
                km_travelled=Decimal("240"),
                trip_from="Office",
                trip_to="Kloof TSF",
                trip_purpose="Site inspection",
            ),
        )
        entry = repo.get_entry(entry_id)
        assert entry.duration_seconds == 0
        assert entry.travel.km_travelled == Decimal("240.0")
        assert entry.travel.trip_to == "Kloof TSF"

    def test_travel_attaches_to_a_work_entry_too(self, repo, project):
        entry_id = repo.add_manual_entry(
            project,
            started_at=sast(2026, 9, 14, 8, 0),
            ended_at=sast(2026, 9, 14, 16, 0),
            travel=TravelDetail(odo_start=Decimal("104200"), odo_end=Decimal("104440")),
        )
        entry = repo.get_entry(entry_id)
        assert entry.duration_seconds == 8 * 3600
        assert entry.travel.km_travelled == Decimal("240.0")

    def test_odometer_readings_derive_the_kilometres(self, repo, project):
        """Acceptance checklist 12."""
        entry_id = repo.add_manual_entry(
            project,
            started_at=sast(2026, 9, 14, 8, 0),
            ended_at=sast(2026, 9, 14, 9, 0),
            travel=TravelDetail(odo_start=Decimal("104200"), odo_end=Decimal("104320")),
        )
        assert repo.get_entry(entry_id).travel.km_travelled == Decimal("120.0")

    def test_a_backwards_odometer_is_refused(self, repo, project):
        with pytest.raises(ValidationError) as caught:
            repo.add_manual_entry(
                project,
                started_at=sast(2026, 9, 14, 8, 0),
                ended_at=sast(2026, 9, 14, 9, 0),
                travel=TravelDetail(odo_start=Decimal("104400"), odo_end=Decimal("104300")),
            )
        assert "lower than the opening" in str(caught.value)

    def test_an_entry_with_neither_time_nor_travel_is_refused(self, repo, project):
        with pytest.raises(ValidationError):
            repo.add_manual_entry(project, started_at=sast(2026, 9, 14, 8, 0))

    def test_decimals_survive_the_round_trip_through_sqlite(self, repo, project):
        """Stored as TEXT, never REAL - so 0.1 is still exactly 0.1."""
        entry_id = repo.log_travel(
            project, on_date=_dt.date(2026, 9, 14), travel=TravelDetail(km_travelled=Decimal("0.1"))
        )
        assert repo.get_entry(entry_id).travel.km_travelled == Decimal("0.1")


class TestEditingAndAudit:
    def test_an_edit_keeps_the_old_value_recoverable(self, repo, project):
        """Acceptance checklist 19."""
        entry_id = repo.add_manual_entry(
            project,
            started_at=sast(2026, 9, 14, 9, 0),
            ended_at=sast(2026, 9, 14, 10, 0),
            description="Rough note",
        )
        repo.update_entry(
            entry_id,
            description="Slope stability review for TSF raise",
            ended_at=sast(2026, 9, 14, 11, 30),
            reason="corrected from diary",
        )

        entry = repo.get_entry(entry_id)
        assert entry.description == "Slope stability review for TSF raise"
        assert entry.duration_seconds == int(2.5 * 3600)
        assert entry.edited is True

        trail = repo.audit_trail(entry_id)
        assert [row["reason"] for row in trail] == [
            "manual entry added",
            "corrected from diary",
        ]
        before = json.loads(trail[-1]["before_json"])
        assert before["description"] == "Rough note"
        assert before["duration_seconds"] == 3600

    def test_editing_the_times_recomputes_the_duration(self, repo, project):
        entry_id = repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9, 0), ended_at=sast(2026, 9, 14, 10, 0)
        )
        repo.update_entry(entry_id, started_at=sast(2026, 9, 14, 8, 0))
        assert repo.get_entry(entry_id).duration_seconds == 2 * 3600

    def test_an_edit_that_inverts_the_times_is_refused(self, repo, project):
        entry_id = repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9, 0), ended_at=sast(2026, 9, 14, 10, 0)
        )
        with pytest.raises(ValidationError):
            repo.update_entry(entry_id, ended_at=sast(2026, 9, 14, 8, 0))

    def test_editing_the_odometer_recomputes_the_kilometres(self, repo, project):
        entry_id = repo.add_manual_entry(
            project,
            started_at=sast(2026, 9, 14, 8, 0),
            ended_at=sast(2026, 9, 14, 9, 0),
            travel=TravelDetail(odo_start=Decimal("104200"), odo_end=Decimal("104320")),
        )
        repo.update_entry(entry_id, odo_end=Decimal("104400"))
        assert repo.get_entry(entry_id).travel.km_travelled == Decimal("200.0")


class TestSoftDelete:
    def test_a_deleted_entry_is_hidden_but_never_destroyed(self, repo, project):
        entry_id = repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9, 0), ended_at=sast(2026, 9, 14, 10, 0)
        )
        repo.soft_delete_entry(entry_id)

        assert repo.list_entries() == []
        assert len(repo.list_entries(include_deleted=True)) == 1
        assert repo.get_entry(entry_id) is not None
        row = repo.conn.execute(
            "SELECT COUNT(*) AS n FROM time_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        assert row["n"] == 1  # the row is still physically there

    def test_a_deleted_entry_can_be_restored(self, repo, project):
        entry_id = repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9, 0), ended_at=sast(2026, 9, 14, 10, 0)
        )
        repo.soft_delete_entry(entry_id)
        repo.restore_entry(entry_id)
        assert len(repo.list_entries()) == 1
        assert [row["reason"] for row in repo.audit_trail(entry_id)][-1] == "restored"


class TestIdleAndRecovery:
    def test_discarding_idle_time_shortens_the_entry_and_is_logged(self, repo, project):
        """Acceptance checklist 8: nothing is discarded without being asked."""
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 13, 0))
        repo.stop_timer(EntryKind.WORK, now=sast(2026, 9, 14, 16, 0))
        window = Interval(sast(2026, 9, 14, 14, 12), sast(2026, 9, 14, 14, 59))

        repo.record_idle_decision(
            entry_id, window, build_idle_adjustment(IdleDecision.DISCARD, window, SAST)
        )

        assert repo.get_entry(entry_id).duration_seconds == 3 * 3600 - 47 * 60
        assert "removed from the entry" in repo.audit_trail(entry_id)[-1]["reason"]

    def test_keeping_idle_time_changes_nothing_but_is_still_recorded(self, repo, project):
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 13, 0))
        repo.stop_timer(EntryKind.WORK, now=sast(2026, 9, 14, 16, 0))
        window = Interval(sast(2026, 9, 14, 14, 12), sast(2026, 9, 14, 14, 59))

        repo.record_idle_decision(
            entry_id, window, build_idle_adjustment(IdleDecision.KEEP, window, SAST)
        )
        assert repo.get_entry(entry_id).duration_seconds == 3 * 3600
        assert "kept" in repo.audit_trail(entry_id)[-1]["reason"]

    def test_splitting_idle_time_off_creates_a_second_entry(self, repo, project):
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 13, 0))
        repo.stop_timer(EntryKind.WORK, now=sast(2026, 9, 14, 16, 0))
        window = Interval(sast(2026, 9, 14, 14, 12), sast(2026, 9, 14, 14, 59))

        spin_off = repo.record_idle_decision(
            entry_id, window, build_idle_adjustment(IdleDecision.SEPARATE, window, SAST)
        )
        assert spin_off is not None
        assert repo.get_entry(entry_id).duration_seconds == 3 * 3600 - 47 * 60
        assert repo.get_entry(spin_off).duration_seconds == 47 * 60

    def test_recovery_keeps_the_time_up_to_the_last_heartbeat(self, repo, project):
        """Acceptance checklist 9."""
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 12, 18))
        repo.heartbeat(now=sast(2026, 9, 14, 14, 32))
        # ... the application dies here; the entry is still marked running.
        assert repo.running_entry(EntryKind.WORK).id == entry_id

        repo.recover_entry(entry_id, RecoveryDecision.KEEP)
        entry = repo.get_entry(entry_id)
        assert entry.is_running is False
        assert entry.ended_at == sast(2026, 9, 14, 14, 32)
        assert entry.duration_seconds == 2 * 3600 + 14 * 60
        assert entry.source.value == "recovered"

    def test_recovery_can_discard_without_destroying_the_row(self, repo, project):
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 12, 18))
        repo.heartbeat(now=sast(2026, 9, 14, 14, 32))
        repo.recover_entry(entry_id, RecoveryDecision.DISCARD)
        assert repo.list_entries() == []
        assert repo.get_entry(entry_id) is not None

    def test_recovery_can_take_a_user_supplied_end_time(self, repo, project):
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 12, 18))
        repo.heartbeat(now=sast(2026, 9, 14, 14, 32))
        repo.recover_entry(
            entry_id, RecoveryDecision.EDIT, ended_at=sast(2026, 9, 14, 15, 0)
        )
        assert repo.get_entry(entry_id).ended_at == sast(2026, 9, 14, 15, 0)

    def test_recovery_refuses_an_end_before_the_start(self, repo, project):
        entry_id = repo.start_timer(project, now=sast(2026, 9, 14, 12, 18))
        with pytest.raises(ValidationError):
            repo.recover_entry(
                entry_id, RecoveryDecision.EDIT, ended_at=sast(2026, 9, 14, 11, 0)
            )


class TestTicksAndSubmissions:
    def test_a_tick_persists(self, repo, project):
        """Acceptance checklist 18."""
        day = _dt.date(2026, 9, 14)
        repo.set_ticked(project, day, True)
        assert repo.get_daily_note(project, day)["ticked_at"] is not None
        repo.set_ticked(project, day, False)
        assert repo.get_daily_note(project, day)["ticked_at"] is None

    def test_a_description_override_replaces_the_generated_one(self, repo, project):
        day = _dt.date(2026, 9, 14)
        repo.set_description_override(project, day, "  Site inspection and report  ")
        assert (
            repo.get_daily_note(project, day)["description_override"]
            == "Site inspection and report"
        )

    def test_a_tick_and_an_override_share_one_row(self, repo, project):
        day = _dt.date(2026, 9, 14)
        repo.set_description_override(project, day, "Narrative")
        repo.set_ticked(project, day, True)
        note = repo.get_daily_note(project, day)
        assert note["description_override"] == "Narrative"
        assert note["ticked_at"] is not None

    def test_marking_a_period_submitted_clears_the_deadline_flag(self, repo, project):
        start, end = _dt.date(2026, 9, 1), _dt.date(2026, 9, 30)
        assert repo.is_submitted(project, start, end) is False
        repo.mark_submitted(project, start, end)
        assert repo.is_submitted(project, start, end) is True
        assert repo.last_submission(project)["period_end"] == "2026-09-30"

    def test_marking_twice_is_not_an_error(self, repo, project):
        start, end = _dt.date(2026, 9, 1), _dt.date(2026, 9, 30)
        repo.mark_submitted(project, start, end)
        repo.mark_submitted(project, start, end, note="refiled")
        assert len(repo.list_submissions(project)) == 1

    def test_a_submission_can_be_undone(self, repo, project):
        start, end = _dt.date(2026, 9, 1), _dt.date(2026, 9, 30)
        repo.mark_submitted(project, start, end)
        repo.unmark_submitted(project, start, end)
        assert repo.is_submitted(project, start, end) is False


class TestSettings:
    def test_settings_have_sensible_defaults(self, repo):
        assert repo.get_decimal("rounding.increment", Decimal("0")) == Decimal("0.25")
        assert repo.get_setting("rounding.direction") == "up"
        assert repo.get_bool("rounding.apply_to_software") is True
        assert repo.get_int("idle.threshold_minutes") == 10

    def test_a_setting_can_be_changed_and_read_back(self, repo):
        repo.set_setting("rounding.increment", "0.1")
        assert repo.rounding_rule().increment == Decimal("0.1")

    def test_a_corrupt_setting_falls_back_instead_of_crashing(self, repo):
        repo.set_setting("rounding.increment", "not a number")
        assert repo.rounding_rule().increment == Decimal("0.25")
        repo.set_setting("rounding.direction", "sideways")
        assert repo.rounding_rule().direction.value == "up"

    def test_the_rounding_rule_is_built_from_settings(self, repo):
        repo.set_setting("rounding.apply_to_software", "0")
        rule = repo.rounding_rule()
        assert rule.apply_to_software is False
        assert rule.for_kind(EntryKind.SOFTWARE).increment == Decimal("0")


class TestQueries:
    def test_entries_are_filtered_by_local_date_not_utc_date(self, repo, project):
        """22:30 local on the 14th is 20:30 UTC - still the 14th locally."""
        repo.add_manual_entry(
            project,
            started_at=sast(2026, 9, 14, 22, 30),
            ended_at=sast(2026, 9, 14, 23, 30),
        )
        found = repo.list_entries(start=_dt.date(2026, 9, 14), end=_dt.date(2026, 9, 14))
        assert len(found) == 1

    def test_an_entry_spanning_midnight_is_found_from_either_date(self, repo, project):
        repo.add_manual_entry(
            project,
            started_at=sast(2026, 9, 14, 22, 30),
            ended_at=sast(2026, 9, 15, 1, 15),
        )
        for day in (_dt.date(2026, 9, 14), _dt.date(2026, 9, 15)):
            assert len(repo.list_entries(start=day, end=day)) == 1

    def test_recorded_dates_reports_days_with_work(self, repo, project):
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9, 0), ended_at=sast(2026, 9, 14, 10, 0)
        )
        dates = repo.recorded_dates(_dt.date(2026, 9, 1), _dt.date(2026, 9, 30))
        assert dates == {_dt.date(2026, 9, 14)}

    def test_recorded_dates_ignores_software_only_days(self, repo, project):
        """An overnight analysis is not a worked day."""
        repo.add_manual_entry(
            project,
            EntryKind.SOFTWARE,
            started_at=sast(2026, 9, 15, 20, 0),
            ended_at=sast(2026, 9, 15, 23, 0),
            software_name="PLAXIS 2D",
        )
        assert repo.recorded_dates(_dt.date(2026, 9, 1), _dt.date(2026, 9, 30)) == set()

    def test_filtering_by_kind_and_project(self, repo, project):
        other = repo.add_project("Other Job")
        repo.add_manual_entry(
            project, started_at=sast(2026, 9, 14, 9, 0), ended_at=sast(2026, 9, 14, 10, 0)
        )
        repo.add_manual_entry(
            other, started_at=sast(2026, 9, 14, 9, 0), ended_at=sast(2026, 9, 14, 10, 0)
        )
        assert len(repo.list_entries(project_id=project)) == 1
        assert len(repo.list_entries(kind=EntryKind.WORK)) == 2
        assert len(repo.list_entries(kind=EntryKind.SOFTWARE)) == 0


class TestProjectOrdering:
    """Archived projects must never lead a picker.

    Anything that defaults to the first project in the list - the Review &
    Submit tab does - would otherwise open on a closed-out job with no
    current time in it, and look empty and broken.
    """

    def test_archived_projects_sort_after_active_ones(self, repo):
        # 'Archived' sorts before 'Kloof' alphabetically, so name order alone
        # would put it first.
        archived = repo.add_project("Archived Old Job")
        active = repo.add_project("Kloof Tailings Dam")
        repo.archive_project(archived)

        listed = repo.list_projects(include_archived=True)
        assert [row["id"] for row in listed] == [active, archived]

    def test_active_projects_still_sort_by_name(self, repo):
        second = repo.add_project("Zebra Project")
        first = repo.add_project("Alpha Project")
        assert [row["id"] for row in repo.list_projects()] == [first, second]
