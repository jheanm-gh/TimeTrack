"""The autosave schedule: debouncing, coalescing, and the status line."""

from __future__ import annotations

import datetime as _dt

import pytest

from app.export.saver import SaveOutcome
from app.services.autosave import AutosaveService
from tests.conftest import sast


@pytest.fixture
def service(qapp, repo, project, tmp_path):
    repo.set_setting("workbook.path", str(tmp_path / "out"))
    repo.add_manual_entry(
        project, started_at=sast(2026, 9, 14, 9), ended_at=sast(2026, 9, 14, 11)
    )
    # In-memory databases cannot be reopened on a worker thread, so the
    # service detects that and saves inline - which is what the tests want.
    created = AutosaveService(repo, db_path=":memory:")
    yield created
    created.shutdown()


class TestScheduling:
    def test_an_in_memory_database_forces_synchronous_saves(self, service):
        assert service.synchronous

    def test_a_change_starts_the_debounce_rather_than_saving_at_once(self, service):
        service.schedule()
        assert service._debounce.isActive()  # noqa: SLF001
        assert service.last_outcome is None

    def test_a_burst_of_changes_still_only_arms_one_save(self, service):
        for _ in range(20):
            service.schedule()
        assert service._debounce.isActive()  # noqa: SLF001
        assert service.last_outcome is None

    def test_the_debounce_interval_comes_from_settings(self, repo, service):
        repo.set_setting("workbook.debounce_seconds", "45")
        service.reload_settings()
        assert service._debounce.interval() == 45_000  # noqa: SLF001

    def test_autosave_can_be_switched_off(self, repo, service):
        repo.set_setting("workbook.autosave_enabled", "0")
        service.reload_settings()
        assert not service._periodic.isActive()  # noqa: SLF001
        service.schedule()
        assert not service._debounce.isActive()  # noqa: SLF001

    def test_the_periodic_interval_comes_from_settings(self, repo, service):
        repo.set_setting("workbook.autosave_minutes", "15")
        service.reload_settings()
        assert service._periodic.interval() == 15 * 60 * 1000  # noqa: SLF001

    def test_requesting_a_save_cancels_the_pending_debounce(self, service):
        service.schedule()
        service.request_save(manual=True)
        assert not service._debounce.isActive()  # noqa: SLF001


class TestSaving:
    def test_a_save_writes_the_workbook(self, service):
        service.request_save(manual=True)
        assert service.last_outcome.saved
        assert service.last_outcome.path.exists()

    def test_save_on_exit_writes_synchronously(self, service):
        outcome = service.save_on_exit()
        assert outcome.saved
        assert outcome.path.exists()

    def test_save_on_exit_stops_the_timers(self, service):
        service.schedule()
        service.save_on_exit()
        assert not service._debounce.isActive()  # noqa: SLF001
        assert not service._periodic.isActive()  # noqa: SLF001

    def test_overlapping_requests_collapse_into_one_follow_up(self, service, monkeypatch):
        """A save arriving while one is running must not be lost."""
        calls: list[bool] = []
        real = service._on_finished  # noqa: SLF001

        def pretend_busy(manual=False):
            calls.append(manual)
            if len(calls) == 1:
                # Simulate a slow save that is still running when the next
                # request arrives.
                service._busy = True  # noqa: SLF001
                service.request_save(manual=False)
                service.request_save(manual=True)
                service._busy = False  # noqa: SLF001
                real(SaveOutcome(saved=True, at=_dt.datetime.now()))

        monkeypatch.setattr(service, "request_save", pretend_busy)
        service.request_save(manual=False)
        # The two requests made while busy became a single follow-up, and it
        # kept the stronger "manual" intent.
        assert calls == [False, False, True]


class TestStatusLine:
    def test_before_any_save(self, service):
        assert service.status_text() == "Not saved yet"

    def test_after_a_save_it_shows_the_time(self, service):
        service.request_save(manual=True)
        assert service.status_text().startswith("Last saved ")

    def test_when_excel_has_the_file_open(self, service):
        service.last_outcome = SaveOutcome(
            saved=False, locked=True, at=_dt.datetime(2026, 9, 14, 14, 32)
        )
        assert service.status_text() == (
            "Not saved 14:32 - workbook open in Excel, will retry"
        )

    def test_when_it_fell_back_to_another_name(self, service, tmp_path):
        service.last_outcome = SaveOutcome(
            saved=True,
            fell_back=True,
            path=tmp_path / "TimeLog_2026-09-14_1432.xlsx",
            at=_dt.datetime(2026, 9, 14, 14, 32),
        )
        assert "TimeLog_2026-09-14_1432.xlsx" in service.status_text()

    def test_when_autosave_is_off(self, repo, service):
        repo.set_setting("workbook.autosave_enabled", "0")
        assert service.status_text() == "Automatic saving is off"

    def test_a_failure_is_reported_in_the_status_line(self, service):
        service.last_outcome = SaveOutcome(saved=False, message="disk full")
        assert "disk full" in service.status_text()
