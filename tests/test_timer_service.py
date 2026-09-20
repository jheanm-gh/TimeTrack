"""The timer service: channels, pausing, and the idle prompt.

Both the clock and the input-idle counter are faked, so a laptop left alone
for an hour can be tested in a millisecond without anyone leaving the desk.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app.core.models import EntryKind
from app.core.timeutil import UTC
from app.services.timers import TimerService

AT_NINE = _dt.datetime(2026, 9, 14, 9, 0, tzinfo=UTC)


class FakeClock:
    """A wall clock and a monotonic clock that advance together on demand."""

    def __init__(self, start: _dt.datetime) -> None:
        self.wall = start
        self.mono = 0.0

    def advance(self, seconds: float) -> None:
        self.wall += _dt.timedelta(seconds=seconds)
        self.mono += seconds


class FakeIdle:
    """Stands in for the Windows GetLastInputInfo counter."""

    def __init__(self) -> None:
        self.seconds = 0
        self.supported = True


@pytest.fixture
def service(qapp, repo, monkeypatch):
    fake_idle = FakeIdle()
    import app.services.timers as timers_module

    monkeypatch.setattr(
        timers_module.idle_module, "idle_seconds", lambda: fake_idle.seconds
    )
    monkeypatch.setattr(
        timers_module.idle_module, "is_supported", lambda: fake_idle.supported
    )

    created = TimerService(repo)
    clock = FakeClock(AT_NINE)
    created.clock._wall = lambda: clock.wall  # noqa: SLF001
    created.clock._monotonic = lambda: clock.mono  # noqa: SLF001
    created.clock.anchor()

    created.fake_idle = fake_idle  # type: ignore[attr-defined]
    created.fake_clock = clock  # type: ignore[attr-defined]
    yield created
    created.shutdown()


def away_and_back(service, away_seconds: int, worked_first: int = 1800) -> list:
    """Work for a while, walk away, then come back and type something."""
    service.fake_clock.advance(worked_first)

    detected: list = []
    service.idle_detected.connect(lambda entry_id, window: detected.append(window))

    service.fake_clock.advance(away_seconds)
    service.fake_idle.seconds = away_seconds
    service._on_idle_poll()  # noqa: SLF001

    service.fake_idle.seconds = 0  # the first keystroke on returning
    service._on_idle_poll()  # noqa: SLF001
    return detected


class TestChannels:
    def test_work_and_software_run_independently(self, service, project):
        service.start_work(project)
        service.start_software(project, "RS2")
        assert service.is_running(EntryKind.WORK)
        assert service.is_running(EntryKind.SOFTWARE)

        service.stop(EntryKind.WORK)
        assert not service.is_running(EntryKind.WORK)
        assert service.is_running(EntryKind.SOFTWARE)

    def test_elapsed_follows_the_monotonic_clock(self, service, project):
        service.start_work(project)
        service.fake_clock.advance(3600)
        assert service.elapsed_seconds(EntryKind.WORK) == 3600

    def test_a_paused_timer_stops_counting(self, service, project):
        service.start_work(project)
        service.fake_clock.advance(1800)
        service.pause(EntryKind.WORK)
        assert service.is_paused(EntryKind.WORK)

        service.fake_clock.advance(3600)
        # An hour passed while paused; only the first half hour counts.
        assert service.elapsed_seconds(EntryKind.WORK) == 1800

        service.resume(EntryKind.WORK)
        service.fake_clock.advance(900)
        assert service.elapsed_seconds(EntryKind.WORK) == 2700

    def test_start_last_task_repeats_the_previous_work(self, service, project):
        service.start_work(project, description="Slope review")
        service.fake_clock.advance(600)
        service.stop(EntryKind.WORK)
        assert service.start_last_task() is not None
        assert service.running(EntryKind.WORK).description == "Slope review"

    def test_start_last_task_is_none_when_there_is_no_history(self, service):
        assert service.start_last_task() is None

    def test_stop_all_clears_both_channels(self, service, project):
        service.start_work(project)
        service.start_software(project, "RS2")
        service.fake_clock.advance(600)
        service.stop_all()
        assert not service.any_running()

    def test_stopping_writes_the_elapsed_time_to_the_database(self, service, project, repo):
        entry_id = service.start_work(project)
        service.fake_clock.advance(5400)
        service.stop(EntryKind.WORK)
        assert repo.get_entry(entry_id).duration_seconds == 5400


class TestIdleDetection:
    """Acceptance checklist 7 and 8."""

    def test_returning_after_a_long_break_raises_a_prompt(self, service, project):
        service.start_work(project)
        detected = away_and_back(service, 47 * 60)
        assert len(detected) == 1
        assert detected[0].seconds() == 47 * 60

    def test_nothing_is_discarded_by_the_prompt_itself(self, service, project, repo):
        """The service only reports; the user decides what happens."""
        entry_id = service.start_work(project)
        away_and_back(service, 47 * 60)
        entry = repo.get_entry(entry_id)
        assert entry.is_running
        assert entry.pauses == ()

    def test_a_short_break_is_ignored(self, service, project):
        service.start_work(project)
        assert away_and_back(service, 120) == []

    def test_the_prompt_never_fires_for_the_software_channel(self, service, project):
        """Acceptance checklist 7: an unattended analysis is left alone."""
        service.start_software(project, "Leapfrog Geo")
        assert away_and_back(service, 3600) == []
        assert service.is_running(EntryKind.SOFTWARE)  # and never stopped

    def test_a_software_timer_survives_an_hour_of_no_keyboard_activity(
        self, service, project
    ):
        service.start_software(project, "Leapfrog Geo")
        for _ in range(120):  # an hour of thirty-second polls
            service.fake_clock.advance(30)
            service.fake_idle.seconds += 30
            service._on_idle_poll()  # noqa: SLF001
        assert service.is_running(EntryKind.SOFTWARE)

    def test_idle_detection_can_be_switched_off(self, service, project, repo):
        repo.set_setting("idle.enabled", "0")
        service.start_work(project)
        assert away_and_back(service, 3600) == []

    def test_a_paused_timer_raises_no_idle_prompt(self, service, project):
        """Being away from a paused timer is exactly what pause is for."""
        service.start_work(project)
        service.pause(EntryKind.WORK)
        assert away_and_back(service, 3600) == []

    def test_nothing_is_reported_when_no_timer_is_running(self, service):
        assert away_and_back(service, 3600) == []

    def test_the_window_never_starts_before_the_timer_did(self, service, project):
        """Walked away, came back, and only then started the timer.

        The idle stretch reaches back further than the entry does, so the
        window has to be clipped to the entry - otherwise the prompt would
        offer to remove time the entry never contained.
        """
        service.start_work(project)
        service.fake_clock.advance(600)  # timer has been running ten minutes

        detected: list = []
        service.idle_detected.connect(lambda entry_id, window: detected.append(window))

        service.fake_idle.seconds = 3600  # but no input for a full hour
        service._on_idle_poll()  # noqa: SLF001
        service.fake_idle.seconds = 0
        service._on_idle_poll()  # noqa: SLF001

        assert len(detected) == 1
        assert detected[0].seconds() == 600

    def test_the_threshold_comes_from_settings(self, service, project, repo):
        repo.set_setting("idle.threshold_minutes", "45")
        service.start_work(project)
        assert away_and_back(service, 30 * 60) == []
        assert len(away_and_back(service, 50 * 60)) == 1

    def test_idle_is_skipped_where_the_platform_cannot_report_it(self, service, project):
        service.fake_idle.supported = False
        service.start_work(project)
        assert away_and_back(service, 3600) == []

    def test_stopping_the_timer_clears_a_pending_prompt(self, service, project):
        """A break that ends with Stop rather than a keystroke asks nothing."""
        service.start_work(project)
        service.fake_clock.advance(1800)
        service.fake_idle.seconds = 3600
        service._on_idle_poll()  # noqa: SLF001

        detected: list = []
        service.idle_detected.connect(lambda entry_id, window: detected.append(window))
        service.stop(EntryKind.WORK)
        service.fake_idle.seconds = 0
        service._on_idle_poll()  # noqa: SLF001
        assert detected == []
