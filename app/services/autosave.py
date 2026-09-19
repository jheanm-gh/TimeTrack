"""Keeping the workbook up to date without being asked.

The brief's requirement is that the user never has to remember to export.
So the workbook is rewritten:

* every thirty minutes while the application is running (configurable, and
  it can be switched off);
* about two minutes after a timer stops or an entry is edited, debounced so
  a burst of corrections produces one save rather than twenty;
* when the application closes;
* on demand, from the Save Now button or the tray menu.

**Saves run on a worker thread.** Building the spreadsheet is proportional to
the amount of history: a few years of entries takes several seconds, which
on the GUI thread would freeze the window every half hour and again on every
edit. The worker opens its own read-only connection, which WAL mode allows
to run alongside the writes the user is still making.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from app import paths
from app.db.connection import connect
from app.db.repository import Repository
from app.export.saver import SaveOutcome, save_workbook, workbook_path


class _SaveSignals(QObject):
    finished = Signal(object)


class _SaveJob(QRunnable):
    """One save, off the GUI thread, against its own database connection."""

    def __init__(self, db_path: Path, manual: bool) -> None:
        super().__init__()
        self.db_path = db_path
        self.manual = manual
        self.signals = _SaveSignals()
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        connection = None
        try:
            connection = connect(self.db_path)
            outcome = save_workbook(Repository(connection), manual=self.manual)
        except Exception as exc:  # noqa: BLE001 - must never kill the thread
            outcome = SaveOutcome(
                saved=False, message=f"The spreadsheet could not be saved: {exc}"
            )
        finally:
            if connection is not None:
                connection.close()
        self.signals.finished.emit(outcome)


class AutosaveService(QObject):
    """Owns the save schedule and the last-save status."""

    #: A save finished. Carries the SaveOutcome.
    saved = Signal(object)
    #: The status-bar text changed.
    status_changed = Signal(str)

    def __init__(
        self,
        repo: Repository,
        parent: QObject | None = None,
        db_path: Path | None = None,
        synchronous: bool = False,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.db_path = Path(db_path) if db_path else paths.db_path()
        # An in-memory database cannot be reopened by a worker thread, so the
        # tests (and only the tests) run saves inline.
        self.synchronous = synchronous or str(self.db_path) == ":memory:"

        self.last_outcome: SaveOutcome | None = None
        self._busy = False
        self._pending_manual = False
        self._pending = False

        self._periodic = QTimer(self)
        self._periodic.timeout.connect(lambda: self.request_save(manual=False))

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(lambda: self.request_save(manual=False))

        self.reload_settings()

    # -- configuration ----------------------------------------------------

    def reload_settings(self) -> None:
        enabled = self.repo.get_bool("workbook.autosave_enabled", True)
        minutes = max(1, self.repo.get_int("workbook.autosave_minutes", 30))
        seconds = max(5, self.repo.get_int("workbook.debounce_seconds", 120))
        self._debounce.setInterval(seconds * 1000)
        self._periodic.setInterval(minutes * 60 * 1000)
        if enabled:
            self._periodic.start()
        else:
            self._periodic.stop()

    @property
    def target(self) -> Path:
        return workbook_path(self.repo)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self.reload_settings()
        self.status_changed.emit(self.status_text())

    def shutdown(self) -> None:
        self._periodic.stop()
        self._debounce.stop()

    def schedule(self) -> None:
        """Something changed. Save shortly, unless more changes arrive first."""
        if self.repo.get_bool("workbook.autosave_enabled", True):
            self._debounce.start()

    # -- saving -----------------------------------------------------------

    def request_save(self, manual: bool = False) -> None:
        """Queue a save. Overlapping requests collapse into one."""
        self._debounce.stop()
        if self._busy:
            # Remember that something still needs writing, and keep the
            # stronger of the two intents: a manual request must not be
            # downgraded to a quiet autosave.
            self._pending = True
            self._pending_manual = self._pending_manual or manual
            return

        self._busy = True
        if manual:
            self.status_changed.emit("Saving the spreadsheet...")

        if self.synchronous:
            self._on_finished(save_workbook(self.repo, manual=manual))
            return

        job = _SaveJob(self.db_path, manual)
        job.signals.finished.connect(self._on_finished)
        QThreadPool.globalInstance().start(job)

    def save_now(self) -> None:
        """The Save Now button and the tray menu item."""
        self.request_save(manual=True)

    def save_on_exit(self) -> SaveOutcome:
        """Save synchronously while closing.

        Deliberately blocking: the application is going away, and a worker
        thread would be killed mid-write. The atomic rename means even that
        could not corrupt the workbook, but it would silently lose the save.
        """
        self._periodic.stop()
        self._debounce.stop()
        outcome = save_workbook(self.repo, manual=False)
        self.last_outcome = outcome
        return outcome

    def _on_finished(self, outcome: SaveOutcome) -> None:
        self._busy = False
        self.last_outcome = outcome
        self.saved.emit(outcome)
        self.status_changed.emit(self.status_text())

        if self._pending:
            manual = self._pending_manual
            self._pending = False
            self._pending_manual = False
            self.request_save(manual=manual)

    # -- status -----------------------------------------------------------

    def status_text(self) -> str:
        """What the status bar shows about the workbook."""
        outcome = self.last_outcome
        if outcome is None:
            if not self.repo.get_bool("workbook.autosave_enabled", True):
                return "Automatic saving is off"
            return "Not saved yet"
        if outcome.saved and outcome.fell_back:
            return f"Saved {outcome.at:%H:%M} as {outcome.path.name}"
        if outcome.saved:
            return f"Last saved {outcome.at:%H:%M}"
        if outcome.locked:
            stamp = f"{outcome.at:%H:%M}" if outcome.at else "-"
            return f"Not saved {stamp} - workbook open in Excel, will retry"
        return f"Save failed: {outcome.message}"
