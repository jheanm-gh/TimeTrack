"""The main window: one compact window that remembers where it was.

Holds the Now strip, the tabs, the banners and the status bar, and owns the
policy decisions that span them - what happens when the X is clicked, what
happens when a timer was left running by a crash, and what happens when the
user comes back to the keyboard after a break.
"""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import paths
from app.core.calc import (
    ValidationError,
    build_idle_adjustment,
    recent_window,
    weekday_gaps,
)
from app.core.models import EntryKind, IdleDecision, RecoveryDecision
from app.core.timeutil import format_hm, to_local
from app.db.repository import Repository
from app.services.timers import TimerService
from app.ui import theme
from app.ui.dialogs import CloseDialog, RecoveryDialog, warn
from app.ui.now_strip import NowStrip
from app.ui.tab_log import LogTab
from app.ui.tab_projects import ProjectsTab
from app.ui.tab_review import ReviewTab
from app.ui.tab_settings import SettingsTab
from app.ui.tab_today import TodayTab
from app.ui.tray import Tray
from app.version import APP_NAME

ORGANISATION = "TimeTrack"
DEFAULT_SIZE = (960, 680)


class MainWindow(QMainWindow):
    """The single window the application lives in."""

    def __init__(self, repo: Repository, start_in_tray: bool = False) -> None:
        super().__init__()
        self.repo = repo
        self.timers = TimerService(repo, self)
        self.settings = QSettings(ORGANISATION, APP_NAME)
        self._really_quitting = False
        self._idle_dialog_open = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.setStyleSheet(theme.STYLE_SHEET)

        self._build()
        self._wire()
        self._restore_geometry()

        self.timers.start()
        self.refresh_all()
        self._offer_recovery()
        self._show_startup_banners()

        if start_in_tray:
            self.hide()
        else:
            self.show()

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 6)

        self.banner_area = QVBoxLayout()
        self.banner_area.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.banner_area)

        self.now_strip = NowStrip(self.repo)
        layout.addWidget(self.now_strip)

        self.tabs = QTabWidget()
        self.today_tab = TodayTab(self.repo)
        self.projects_tab = ProjectsTab(self.repo)
        self.log_tab = LogTab(self.repo)
        self.review_tab = ReviewTab(self.repo)
        self.settings_tab = SettingsTab(self.repo)

        self.tabs.addTab(self.today_tab, "Today")
        self.tabs.addTab(self.projects_tab, "Projects")
        self.tabs.addTab(self.log_tab, "Log")
        self.tabs.addTab(self.review_tab, "Review && Submit")
        self.tabs.addTab(self.settings_tab, "Settings")
        layout.addWidget(self.tabs, 1)

        self.setCentralWidget(central)

        self.status_left = QLabel()
        self.status_right = QLabel()
        self.statusBar().addWidget(self.status_left, 1)
        self.statusBar().addPermanentWidget(self.status_right)
        self._update_status_bar()

        self.tray = Tray(self)
        self.tray.show()

    def _wire(self) -> None:
        self.now_strip.start_work_requested.connect(self._start_work)
        self.now_strip.start_software_requested.connect(self._start_software)
        self.now_strip.start_last_requested.connect(self._start_last)
        self.now_strip.pause_requested.connect(self._toggle_pause)
        self.now_strip.stop_requested.connect(self._stop_channel)

        self.timers.ticked.connect(self._on_tick)
        self.timers.state_changed.connect(self.refresh_all)
        self.timers.idle_detected.connect(self._on_idle_detected)
        self.timers.clock_jumped.connect(self._on_clock_jump)

        for tab in (self.today_tab, self.projects_tab, self.log_tab, self.review_tab):
            tab.data_changed.connect(self.refresh_all)
        self.settings_tab.settings_changed.connect(self._on_settings_changed)

        self.tray.open_requested.connect(self.show_and_raise)
        self.tray.start_last_requested.connect(self._start_last)
        self.tray.stop_all_requested.connect(self._stop_all)
        self.tray.quit_requested.connect(self.quit_application)
        self.tray.save_workbook_requested.connect(self._save_workbook_placeholder)

        self.tabs.currentChanged.connect(self._on_tab_changed)

    # -- geometry ---------------------------------------------------------

    def _restore_geometry(self) -> None:
        stored = self.settings.value("window/geometry")
        if isinstance(stored, QByteArray) and not stored.isEmpty():
            self.restoreGeometry(stored)
        else:
            self.resize(*DEFAULT_SIZE)

    def _save_geometry(self) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())

    # -- refreshing -------------------------------------------------------

    def refresh_all(self) -> None:
        self.timers.refresh()
        self.now_strip.reload_pickers()
        self.now_strip.update_display(self.timers)
        self._refresh_current_tab()
        self._update_tray()
        self._update_status_bar()

    def _refresh_current_tab(self) -> None:
        widget = self.tabs.currentWidget()
        if hasattr(widget, "refresh"):
            widget.refresh()
        elif hasattr(widget, "reload"):
            widget.reload()

    def _on_tab_changed(self, _index: int) -> None:
        self._refresh_current_tab()

    def _on_tick(self) -> None:
        # Only the elapsed readouts move every second; rebuilding a table at
        # 1 Hz would fight the user every time they clicked a cell.
        self.now_strip.update_display(self.timers)
        self._update_tray()

    def _on_settings_changed(self) -> None:
        self.timers.reload_settings()
        self.refresh_all()

    def _update_tray(self) -> None:
        work = self.timers.running(EntryKind.WORK)
        software = self.timers.running(EntryKind.SOFTWARE)
        names = self.repo.project_names()

        work_text = None
        if work is not None:
            work_text = (
                f"{names.get(work.project_id, '?')} "
                f"({format_hm(self.timers.elapsed_seconds(EntryKind.WORK))})"
            )
        software_text = None
        if software is not None:
            software_text = (
                f"{software.software_name} "
                f"({format_hm(self.timers.elapsed_seconds(EntryKind.SOFTWARE))})"
            )

        paused = self.timers.is_paused(EntryKind.WORK) or self.timers.is_paused(
            EntryKind.SOFTWARE
        )
        self.tray.update_state(
            work is not None, software is not None, paused, work_text, software_text
        )
        self.setWindowIcon(
            theme.state_icon(
                work=work is not None, software=software is not None, paused=paused
            )
        )

    def _update_status_bar(self) -> None:
        self.status_left.setText(f"Database: {paths.db_path()}")
        workbook = self.repo.get_setting("workbook.path") or str(
            paths.default_workbook_dir()
        )
        self.status_right.setText(
            f"Spreadsheet: {workbook}   |   Not yet saved (coming in the next step)"
        )

    # -- banners ----------------------------------------------------------

    def _clear_banners(self) -> None:
        while self.banner_area.count():
            item = self.banner_area.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _show_startup_banners(self) -> None:
        """The catch-up nudge and the overdue-timesheet flag.

        Prominent but never modal - a dialog on every launch would be
        dismissed on reflex within a week.
        """
        from app.ui.widgets import Banner

        self._clear_banners()
        today = _dt.datetime.now(tz=self.repo.timezone()).date()

        overdue = self.projects_tab.overdue_projects()
        if overdue:
            names = ", ".join(
                f"{name} ({days} days)" for name, days in overdue[:3]
            )
            banner = Banner(
                f"<b>Timesheet overdue:</b> {names}. Time is recorded but the "
                "period has not been marked as submitted.",
                "Go to Review & Submit",
                tone="danger",
            )
            banner.action_clicked.connect(
                lambda: self.tabs.setCurrentWidget(self.review_tab)
            )
            self.banner_area.addWidget(banner)

        window_days = self.repo.get_int("ui.catchup_days", 30)
        start, end = recent_window(today, window_days)
        recorded = self.repo.recorded_dates(start, end)
        gaps = weekday_gaps(recorded, start, end, today=today)
        if gaps:
            banner = Banner(
                f"<b>{len(gaps)} weekdays have no time logged</b> in the last "
                f"{window_days} days — worth a review before month end.",
                "Show me",
            )
            banner.action_clicked.connect(lambda: self._show_gaps(gaps))
            self.banner_area.addWidget(banner)

    def _show_gaps(self, gaps: list[_dt.date]) -> None:
        self.tabs.setCurrentWidget(self.log_tab)
        self.log_tab.show_dates(gaps)

    # -- timer commands ---------------------------------------------------

    def _start_work(self, project_id: int, task_id) -> None:
        try:
            self.timers.start_work(project_id, task_id=task_id)
        except ValidationError as exc:
            warn(self, str(exc))

    def _start_software(self, project_id: int, package: str) -> None:
        if not package:
            warn(self, "Type or choose the software package first.")
            return
        try:
            self.timers.start_software(project_id, package)
        except ValidationError as exc:
            warn(self, str(exc))

    def _start_last(self) -> None:
        if self.timers.start_last_task() is None:
            warn(self, "There is no previous task to start yet.")
            return
        self.show_and_raise()

    def _toggle_pause(self, kind: EntryKind) -> None:
        self.timers.toggle_pause(kind)

    def _stop_channel(self, kind: EntryKind) -> None:
        self.timers.stop(kind)

    def _stop_all(self) -> None:
        self.timers.stop_all()

    def _save_workbook_placeholder(self) -> None:
        QMessageBox.information(
            self,
            "Not built yet",
            "Saving the spreadsheet arrives in the next step of the build.\n\n"
            "Everything you record now is already stored safely in the "
            "database, so nothing will be lost.",
        )

    # -- idle and recovery ------------------------------------------------

    def _on_idle_detected(self, entry_id: int, window) -> None:
        """Ask what to do with a stretch of inactivity. Never decide alone."""
        if self._idle_dialog_open:
            return
        self._idle_dialog_open = True
        try:
            tz = self.repo.timezone()
            minutes = max(0, window.seconds()) // 60
            start_text = f"{to_local(window.start, tz):%H:%M}"
            end_text = f"{to_local(window.end, tz):%H:%M}"

            box = QMessageBox(self)
            box.setWindowTitle("You were away")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(
                f"No activity from {start_text} to {end_text} ({minutes} min)."
            )
            box.setInformativeText("What should happen to that time?")
            keep = box.addButton("Keep it", QMessageBox.ButtonRole.AcceptRole)
            discard = box.addButton("Discard it", QMessageBox.ButtonRole.DestructiveRole)
            separate = box.addButton(
                "Keep as a separate entry", QMessageBox.ButtonRole.ActionRole
            )
            box.setDefaultButton(keep)
            # Non-modal to the application: the user can carry on working and
            # answer when they are ready.
            box.setWindowModality(Qt.WindowModality.WindowModal)
            box.exec()

            clicked = box.clickedButton()
            if clicked is discard:
                decision = IdleDecision.DISCARD
            elif clicked is separate:
                decision = IdleDecision.SEPARATE
            else:
                decision = IdleDecision.KEEP

            adjustment = build_idle_adjustment(decision, window, tz)
            self.repo.record_idle_decision(entry_id, window, adjustment)
            self.refresh_all()
        finally:
            self._idle_dialog_open = False

    def _offer_recovery(self) -> None:
        """On startup, deal with any timer left running by a crash."""
        names = self.repo.project_names()
        for entry in self.repo.running_entries():
            # A timer that is genuinely still running from this session is
            # not a crash; only entries that predate this launch are offered.
            task_name = None
            if entry.task_id:
                task_row = self.repo.get_task(entry.task_id)
                task_name = task_row["name"] if task_row else None

            dialog = RecoveryDialog(
                self,
                entry,
                names.get(entry.project_id, "an unknown project"),
                task_name,
                self.repo.timezone(),
            )
            if not dialog.exec() or dialog.decision is None:
                # Closing the dialog without choosing leaves the timer running
                # and the entry untouched - nothing is lost either way.
                continue
            try:
                self.repo.recover_entry(
                    entry.id, dialog.decision, ended_at=dialog.edited_end
                )
            except ValidationError as exc:
                warn(self, str(exc))
        self.timers.refresh()

    def _on_clock_jump(self, seconds: float) -> None:
        """The machine slept, or the clock was corrected."""
        if abs(seconds) < 60:
            return
        minutes = int(abs(seconds) // 60)
        self.statusBar().showMessage(
            f"The computer's clock moved by about {minutes} minutes "
            "(sleep or a clock correction). Running timers have been "
            "re-checked against it.",
            10000,
        )
        self.refresh_all()

    # -- window lifecycle -------------------------------------------------

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.refresh_all()

    def _running_summary(self) -> list[str]:
        names = self.repo.project_names()
        summary = []
        for kind, label in ((EntryKind.WORK, "Work"), (EntryKind.SOFTWARE, "Software")):
            entry = self.timers.running(kind)
            if entry is None:
                continue
            elapsed = format_hm(self.timers.elapsed_seconds(kind))
            what = names.get(entry.project_id, "?")
            if entry.software_name:
                what = f"{entry.software_name} · {what}"
            summary.append(f"&nbsp;&nbsp;{label}: {what} — running for {elapsed}")
        return summary

    def closeEvent(self, event) -> None:  # noqa: N802
        """Clicking the X asks; it never exits silently with time running."""
        if self._really_quitting:
            self._shutdown()
            event.accept()
            return

        if not self.repo.get_bool("close.confirm", True):
            event.ignore()
            self.hide()
            return

        dialog = CloseDialog(self, self._running_summary())
        if not dialog.exec():
            event.ignore()
            return

        if dialog.dont_ask_again.isChecked():
            self.repo.set_setting("close.confirm", "0")

        if dialog.choice == CloseDialog.EXIT:
            self._really_quitting = True
            self._shutdown()
            event.accept()
            from PySide6.QtWidgets import QApplication

            QApplication.quit()
            return

        event.ignore()
        self.hide()
        self.tray.notify(
            APP_NAME, "Still running in the tray. Your timers keep going."
        )

    def quit_application(self) -> None:
        from PySide6.QtWidgets import QApplication

        if self.timers.any_running():
            confirmed = QMessageBox.question(
                self,
                "Stop the running timer?",
                "Quitting will stop the running timer and save the time "
                "recorded so far.\n\nQuit TimeTrack?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmed != QMessageBox.StandardButton.Yes:
                return
        self._really_quitting = True
        self._shutdown()
        QApplication.quit()

    def _shutdown(self) -> None:
        """Stop cleanly: no timer is ever left running into a closed app."""
        self._save_geometry()
        if self.timers.any_running():
            self.timers.stop_all()
        self.timers.shutdown()
        self.tray.hide()
