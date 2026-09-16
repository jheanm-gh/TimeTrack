"""Orchestrates the two timer channels while the application is running.

Owns the three periodic jobs the brief asks for:

* a **one-second tick** that refreshes the elapsed-time display, driven by
  the monotonic clock so a wall-clock correction cannot make it jump;
* a **fifteen-second heartbeat** written to any running entry, so a crash
  leaves behind an honest record of how far the work had got;
* a **thirty-second idle poll** on the work channel only.

The software channel is deliberately exempt from idle handling: an analysis
can legitimately run unattended all night, and stopping it or questioning it
would be wrong.
"""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.models import EntryKind, Interval, TravelDetail
from app.core.calc import entry_duration_seconds
from app.db.repository import Repository, SavedEntry
from app.services import idle as idle_module
from app.services.clock import ElapsedClock


class TimerService(QObject):
    """A thin, signal-emitting layer over the repository's timer methods."""

    #: A timer was started, paused, resumed or stopped.
    state_changed = Signal()
    #: One-second display refresh.
    ticked = Signal()
    #: The user came back after being away. Carries ``(entry_id, Interval)``.
    idle_detected = Signal(int, object)
    #: The wall clock moved relative to the monotonic clock, by N seconds.
    clock_jumped = Signal(float)

    def __init__(self, repo: Repository, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.clock = ElapsedClock()

        self._running: dict[EntryKind, SavedEntry] = {}
        #: When the user stopped touching the keyboard during the current
        #: away-stretch. ``None`` means they are (or were last seen) present.
        self._last_input_at: _dt.datetime | None = None
        self._idle_prompt_pending = False

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_tick)

        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._on_heartbeat)

        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._on_idle_poll)

        self.reload_settings()
        self.refresh()

    # -- configuration ----------------------------------------------------

    def reload_settings(self) -> None:
        """Pick up changed intervals without restarting the application."""
        heartbeat = max(5, self.repo.get_int("timer.heartbeat_seconds", 15))
        poll = max(5, self.repo.get_int("idle.poll_seconds", 30))
        self._heartbeat_timer.setInterval(heartbeat * 1000)
        self._idle_timer.setInterval(poll * 1000)

    @property
    def idle_enabled(self) -> bool:
        return self.repo.get_bool("idle.enabled", True) and idle_module.is_supported()

    @property
    def idle_threshold_seconds(self) -> int:
        return max(60, self.repo.get_int("idle.threshold_minutes", 10) * 60)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self.clock.anchor()
        self._tick_timer.start()
        self._heartbeat_timer.start()
        self._idle_timer.start()

    def shutdown(self) -> None:
        self._tick_timer.stop()
        self._heartbeat_timer.stop()
        self._idle_timer.stop()

    # -- state ------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read which timers are running straight from the database."""
        self._running = {entry.kind: entry for entry in self.repo.running_entries()}

    def running(self, kind: EntryKind) -> SavedEntry | None:
        return self._running.get(EntryKind(kind))

    def is_running(self, kind: EntryKind) -> bool:
        return EntryKind(kind) in self._running

    def is_paused(self, kind: EntryKind) -> bool:
        entry = self.running(kind)
        return bool(entry and any(pause.is_open for pause in entry.pauses))

    def any_running(self) -> bool:
        return bool(self._running)

    def elapsed_seconds(self, kind: EntryKind) -> int:
        """Counted seconds so far, excluding any paused stretch.

        Measured against the monotonic clock, so the number on screen only
        ever goes forwards at one second per second.
        """
        entry = self.running(kind)
        if entry is None:
            return 0
        return entry_duration_seconds(entry.to_calc(), now=self.clock.now())

    # -- commands ---------------------------------------------------------
    # Timestamps written to the database come from the wall clock; only the
    # display uses the monotonic one.

    def start_work(
        self,
        project_id: int,
        task_id: int | None = None,
        description: str = "",
        travel: TravelDetail | None = None,
    ) -> int:
        entry_id = self.repo.start_timer(
            project_id,
            EntryKind.WORK,
            task_id=task_id,
            description=description,
            travel=travel,
            now=self.clock.wall_now(),
        )
        self._reset_idle_tracking()
        self._changed()
        return entry_id

    def start_software(self, project_id: int, software_name: str) -> int:
        entry_id = self.repo.start_timer(
            project_id,
            EntryKind.SOFTWARE,
            software_name=software_name,
            now=self.clock.wall_now(),
        )
        self._changed()
        return entry_id

    def start_last_task(self) -> int | None:
        """Restart the most recent work entry - most days repeat."""
        previous = self.repo.last_work_entry()
        if previous is None:
            return None
        return self.start_work(
            previous.project_id,
            task_id=previous.task_id,
            description=previous.description,
        )

    def pause(self, kind: EntryKind) -> bool:
        result = self.repo.pause_timer(kind, now=self.clock.wall_now())
        self._changed()
        return result

    def resume(self, kind: EntryKind) -> bool:
        result = self.repo.resume_timer(kind, now=self.clock.wall_now())
        if EntryKind(kind) is EntryKind.WORK:
            self._reset_idle_tracking()
        self._changed()
        return result

    def toggle_pause(self, kind: EntryKind) -> bool:
        return self.resume(kind) if self.is_paused(kind) else self.pause(kind)

    def stop(self, kind: EntryKind) -> int | None:
        entry_id = self.repo.stop_timer(kind, now=self.clock.wall_now())
        if EntryKind(kind) is EntryKind.WORK:
            self._reset_idle_tracking()
        self._changed()
        return entry_id

    def stop_all(self) -> list[int]:
        stopped = self.repo.stop_all(now=self.clock.wall_now())
        self._reset_idle_tracking()
        self._changed()
        return stopped

    def _changed(self) -> None:
        self.refresh()
        self.state_changed.emit()

    # -- periodic jobs ----------------------------------------------------

    def _on_tick(self) -> None:
        jump = self.clock.poll()
        if jump is not None:
            # The wall clock moved under us - a sleep/wake, or a correction.
            # Re-read state so the display reflects reality rather than a
            # stale in-memory copy.
            self.refresh()
            self.clock_jumped.emit(jump)
        self.ticked.emit()

    def _on_heartbeat(self) -> None:
        if not self._running:
            return
        self.repo.heartbeat(now=self.clock.wall_now())
        self.refresh()

    def _reset_idle_tracking(self) -> None:
        self._last_input_at = None
        self._idle_prompt_pending = False

    def _on_idle_poll(self) -> None:
        """Watch for the user leaving, and ask about it when they come back.

        The prompt fires on *return*, not on departure - asking while nobody
        is at the desk would only queue a dialog they come back to anyway,
        and the length of the absence is not known until it ends.
        """
        entry = self.running(EntryKind.WORK)
        if entry is None or not self.idle_enabled or self.is_paused(EntryKind.WORK):
            self._reset_idle_tracking()
            return

        away_seconds = idle_module.idle_seconds()
        if away_seconds is None:
            return

        now = self.clock.wall_now()
        # While there is no input, this instant stays fixed at the moment of
        # the user's last keystroke, which is the true start of the absence.
        last_input_at = now - _dt.timedelta(seconds=away_seconds)

        if away_seconds >= self.idle_threshold_seconds:
            if self._last_input_at is None:
                self._last_input_at = max(last_input_at, entry.started_at)
            self._idle_prompt_pending = True
            return

        # Input again: the user is back. Report the stretch that just ended.
        if self._idle_prompt_pending and self._last_input_at is not None:
            window_start = self._last_input_at
            window_end = last_input_at
            self._reset_idle_tracking()
            if window_end > window_start:
                self.idle_detected.emit(entry.id, Interval(window_start, window_end))
