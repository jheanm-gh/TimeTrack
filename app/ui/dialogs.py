"""Dialogs: adding and editing the things the main window lists.

Two conventions run through all of them.

Every message is written for somebody who does not read code. A validation
failure says what is wrong and what to do about it, never what the exception
was.

Nothing destructive happens without an explicit answer. The close dialog and
the crash-recovery dialog both refuse to pick for the user.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.calc import ValidationError
from app.core.models import (
    LAST_WORKING_DAY,
    EntryKind,
    IdleDecision,
    PeriodType,
    RecoveryDecision,
    TravelDetail,
)
from app.core.timeutil import format_hm, to_local
from app.db.repository import Repository, SavedEntry
from app.ui import theme
from app.ui.widgets import set_role


def warn(parent: QWidget | None, message: str, title: str = "TimeTrack") -> None:
    """Show a problem in plain English."""
    QMessageBox.warning(parent, title, message)


def _decimal_or_none(text: str) -> Decimal | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValidationError(f"'{text}' is not a number.") from exc


class TravelFields(QGroupBox):
    """The kilometre side of an entry, shared by every dialog that needs it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Travel (optional)", parent)
        self.setCheckable(True)
        self.setChecked(False)

        self.trip_from = QLineEdit()
        self.trip_to = QLineEdit()
        self.trip_purpose = QLineEdit()
        self.odo_start = QLineEdit()
        self.odo_end = QLineEdit()
        self.km = QLineEdit()

        self.odo_start.setPlaceholderText("e.g. 104200")
        self.odo_end.setPlaceholderText("e.g. 104320")
        self.km.setPlaceholderText("only if you have no odometer readings")
        self.trip_purpose.setPlaceholderText("e.g. Site inspection")

        self.hint = QLabel(
            "Enter both odometer readings and the kilometres work themselves out."
        )
        set_role(self.hint, "muted")
        self.hint.setWordWrap(True)

        form = QFormLayout(self)
        form.addRow("From", self.trip_from)
        form.addRow("To", self.trip_to)
        form.addRow("Purpose", self.trip_purpose)
        form.addRow("Odometer start", self.odo_start)
        form.addRow("Odometer end", self.odo_end)
        form.addRow("Kilometres", self.km)
        form.addRow("", self.hint)

        self.odo_start.textChanged.connect(self._recalculate)
        self.odo_end.textChanged.connect(self._recalculate)

    def _recalculate(self) -> None:
        """Live feedback as the readings are typed - including the error."""
        try:
            start = _decimal_or_none(self.odo_start.text())
            end = _decimal_or_none(self.odo_end.text())
        except ValidationError:
            return
        if start is None or end is None:
            return
        if end < start:
            self.hint.setText(
                "The closing reading is lower than the opening one - please check."
            )
            set_role(self.hint, "danger")
            return
        self.hint.setText(f"That is {end - start} km.")
        set_role(self.hint, "ok")

    def load(self, travel: TravelDetail) -> None:
        if not travel.has_any:
            return
        self.setChecked(True)
        self.trip_from.setText(travel.trip_from or "")
        self.trip_to.setText(travel.trip_to or "")
        self.trip_purpose.setText(travel.trip_purpose or "")
        self.odo_start.setText(str(travel.odo_start) if travel.odo_start else "")
        self.odo_end.setText(str(travel.odo_end) if travel.odo_end else "")
        if travel.odo_start is None or travel.odo_end is None:
            self.km.setText(str(travel.km_travelled) if travel.km_travelled else "")

    def prefill(self, site: str | None, default_km: Decimal | None) -> None:
        """Most trips to a given project are the same round trip."""
        if site and not self.trip_to.text():
            self.trip_to.setText(site)
        if default_km and not self.km.text() and not self.odo_start.text():
            self.km.setText(str(default_km))

    def value(self) -> TravelDetail:
        if not self.isChecked():
            return TravelDetail()
        return TravelDetail(
            km_travelled=_decimal_or_none(self.km.text()),
            odo_start=_decimal_or_none(self.odo_start.text()),
            odo_end=_decimal_or_none(self.odo_end.text()),
            trip_from=self.trip_from.text().strip() or None,
            trip_to=self.trip_to.text().strip() or None,
            trip_purpose=self.trip_purpose.text().strip() or None,
        )


class EntryDialog(QDialog):
    """Add or edit one time entry, with optional travel attached.

    Manual entry is first-class: reconstructing a forgotten week happens
    through this dialog, so it opens with sensible defaults and never
    demands more than it needs.
    """

    def __init__(
        self,
        repo: Repository,
        parent: QWidget | None = None,
        entry: SavedEntry | None = None,
        default_project_id: int | None = None,
        default_date: _dt.date | None = None,
        travel_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.entry = entry
        self.travel_only = travel_only
        self.tz = repo.timezone()
        self.entry_id: int | None = entry.id if entry else None

        if travel_only:
            title = "Log a site trip"
        else:
            title = "Edit entry" if entry else "Add an entry by hand"
        self.setWindowTitle(title)
        self.setMinimumWidth(460)

        self.project = QComboBox()
        self.task = QComboBox()
        self.kind_work = QRadioButton("Work")
        self.kind_software = QRadioButton("Software")
        self.software_name = QComboBox()
        self.software_name.setEditable(True)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.start_time = QTimeEdit()
        self.end_time = QTimeEdit()
        self.ends_next_day = QCheckBox("Ends after midnight (next day)")
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(70)
        self.duration_label = QLabel("-")
        self.duration_label.setFont(theme.heading_font())
        self.travel = TravelFields()

        self.start_time.setDisplayFormat("HH:mm")
        self.end_time.setDisplayFormat("HH:mm")
        self.date.setDisplayFormat("ddd d MMM yyyy")

        self._build_layout()
        self._populate_projects(default_project_id)
        self._populate_software_names()
        self._load(entry, default_date)
        self._wire()
        self._update_kind_visibility()
        self._update_duration()

    # -- construction -----------------------------------------------------

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Project", self.project)
        form.addRow("Task", self.task)

        kind_row = QHBoxLayout()
        kind_row.addWidget(self.kind_work)
        kind_row.addWidget(self.kind_software)
        kind_row.addStretch(1)
        self.kind_row_widget = QWidget()
        self.kind_row_widget.setLayout(kind_row)
        form.addRow("Type", self.kind_row_widget)
        self.software_row_label = QLabel("Package")
        form.addRow(self.software_row_label, self.software_name)

        form.addRow("Date", self.date)

        times = QHBoxLayout()
        times.addWidget(self.start_time)
        times.addWidget(QLabel("to"))
        times.addWidget(self.end_time)
        times.addWidget(self.duration_label)
        times.addStretch(1)
        self.times_widget = QWidget()
        self.times_widget.setLayout(times)
        form.addRow("Time", self.times_widget)
        form.addRow("", self.ends_next_day)
        form.addRow("Description", self.description)

        layout.addLayout(form)
        layout.addWidget(self.travel)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._on_save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _populate_projects(self, default_project_id: int | None) -> None:
        # Archived projects stay selectable when editing an old entry that
        # belongs to one, but are otherwise out of the way.
        include_archived = bool(
            self.entry
            and self.repo.get_project(self.entry.project_id)
            and self.repo.get_project(self.entry.project_id)["status"] == "archived"
        )
        for row in self.repo.list_projects(include_archived=include_archived):
            label = row["name"] + (" (done)" if row["status"] == "archived" else "")
            self.project.addItem(label, row["id"])
        if default_project_id is not None:
            index = self.project.findData(default_project_id)
            if index >= 0:
                self.project.setCurrentIndex(index)

    def _populate_software_names(self) -> None:
        self.software_name.addItems(self.repo.software_names())
        self.software_name.setCurrentText("")

    def _populate_tasks(self) -> None:
        current = self.task.currentData()
        self.task.clear()
        self.task.addItem("(no task)", None)
        project_id = self.project.currentData()
        if project_id is None:
            return
        for row in self.repo.list_tasks(project_id):
            prefix = "    " if row["parent_task_id"] else ""
            self.task.addItem(prefix + row["name"], row["id"])
        index = self.task.findData(current)
        if index >= 0:
            self.task.setCurrentIndex(index)

    def _load(self, entry: SavedEntry | None, default_date: _dt.date | None) -> None:
        self._populate_tasks()
        if entry is not None:
            local_start = to_local(entry.started_at, self.tz)
            self.date.setDate(QDate(local_start.year, local_start.month, local_start.day))
            self.start_time.setTime(QTime(local_start.hour, local_start.minute))
            if entry.ended_at:
                local_end = to_local(entry.ended_at, self.tz)
                self.end_time.setTime(QTime(local_end.hour, local_end.minute))
                self.ends_next_day.setChecked(local_end.date() > local_start.date())
            self.description.setPlainText(entry.description)
            self.kind_software.setChecked(entry.kind is EntryKind.SOFTWARE)
            self.kind_work.setChecked(entry.kind is EntryKind.WORK)
            if entry.software_name:
                self.software_name.setCurrentText(entry.software_name)
            index = self.task.findData(entry.task_id)
            if index >= 0:
                self.task.setCurrentIndex(index)
            self.travel.load(entry.travel)
            return

        today = default_date or _dt.date.today()
        self.date.setDate(QDate(today.year, today.month, today.day))
        now = _dt.datetime.now()
        self.start_time.setTime(QTime(max(0, now.hour - 1), 0))
        self.end_time.setTime(QTime(now.hour, 0))
        self.kind_work.setChecked(True)
        if self.travel_only:
            self.travel.setChecked(True)
            self.travel.setCheckable(False)
            self.times_widget.setEnabled(False)
            self.ends_next_day.setEnabled(False)
            self.kind_row_widget.setEnabled(False)
            self.description.setPlainText("Site trip")
            self._prefill_travel_defaults()

    def _prefill_travel_defaults(self) -> None:
        project_id = self.project.currentData()
        if project_id is None:
            return
        row = self.repo.get_project(project_id)
        if row is None:
            return
        default_km = row["default_km"]
        self.travel.prefill(
            row["default_site"], Decimal(default_km) if default_km else None
        )

    def _wire(self) -> None:
        self.project.currentIndexChanged.connect(self._populate_tasks)
        if self.travel_only:
            self.project.currentIndexChanged.connect(self._prefill_travel_defaults)
        self.kind_work.toggled.connect(self._update_kind_visibility)
        self.start_time.timeChanged.connect(self._update_duration)
        self.end_time.timeChanged.connect(self._update_duration)
        self.ends_next_day.toggled.connect(self._update_duration)

    # -- behaviour --------------------------------------------------------

    def _update_kind_visibility(self) -> None:
        is_software = self.kind_software.isChecked()
        self.software_name.setVisible(is_software)
        self.software_row_label.setVisible(is_software)

    def _times(self) -> tuple[_dt.datetime, _dt.datetime]:
        day = self.date.date().toPython()
        start_time = self.start_time.time().toPython()
        end_time = self.end_time.time().toPython()
        start = _dt.datetime.combine(day, start_time, tzinfo=self.tz)
        end_day = day + _dt.timedelta(days=1) if self.ends_next_day.isChecked() else day
        end = _dt.datetime.combine(end_day, end_time, tzinfo=self.tz)
        return start, end

    def _update_duration(self) -> None:
        start, end = self._times()
        if end < start and not self.ends_next_day.isChecked():
            # Almost always means the session ran past midnight; offer it
            # rather than making the user work out why Save is refusing.
            self.duration_label.setText("ends before it starts")
            set_role(self.duration_label, "danger")
            return
        seconds = int((end - start).total_seconds())
        self.duration_label.setText(f"= {format_hm(seconds)}")
        set_role(self.duration_label, None)

    def _on_save(self) -> None:
        project_id = self.project.currentData()
        if project_id is None:
            warn(self, "Choose a project for this entry.")
            return

        kind = EntryKind.SOFTWARE if self.kind_software.isChecked() else EntryKind.WORK
        software_name = (
            self.software_name.currentText().strip()
            if kind is EntryKind.SOFTWARE
            else None
        )
        start, end = self._times()
        description = self.description.toPlainText().strip()

        try:
            travel = self.travel.value()
            if self.travel_only:
                self.entry_id = self.repo.log_travel(
                    project_id,
                    on_date=self.date.date().toPython(),
                    travel=travel,
                    description=description,
                    tz=self.tz,
                )
            elif self.entry is None:
                self.entry_id = self.repo.add_manual_entry(
                    project_id,
                    kind,
                    started_at=start,
                    ended_at=end,
                    task_id=self.task.currentData(),
                    description=description,
                    software_name=software_name,
                    travel=travel,
                )
            else:
                updates = {
                    "project_id": project_id,
                    "task_id": self.task.currentData(),
                    "started_at": start,
                    "ended_at": end,
                    "description": description,
                    "km_travelled": travel.km_travelled,
                    "odo_start": travel.odo_start,
                    "odo_end": travel.odo_end,
                    "trip_from": travel.trip_from,
                    "trip_to": travel.trip_to,
                    "trip_purpose": travel.trip_purpose,
                }
                if self.entry.kind is EntryKind.SOFTWARE:
                    updates["software_name"] = software_name
                self.repo.update_entry(self.entry.id, **updates)
        except ValidationError as exc:
            warn(self, str(exc))
            return
        self.accept()


class ProjectDialog(QDialog):
    """Add or edit a project."""

    def __init__(
        self,
        repo: Repository,
        parent: QWidget | None = None,
        project_id: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.project_id = project_id
        self.setWindowTitle("Edit project" if project_id else "Add a project")
        self.setMinimumWidth(460)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Exactly as it appears in the intranet dropdown")
        self.client = QLineEdit()
        self.code = QLineEdit()
        self.submission = QComboBox()
        self.period_type = QComboBox()
        self.period_start_day = QComboBox()
        self.default_site = QLineEdit()
        self.default_km = QLineEdit()
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(60)

        self.submission.addItem("(no deadline)", None)
        self.submission.addItem("Last working day of the month", LAST_WORKING_DAY)
        for day in range(1, 32):
            self.submission.addItem(f"Day {day} of the month", str(day))

        self.period_type.addItem("Calendar month", str(PeriodType.CALENDAR_MONTH))
        self.period_type.addItem("Cutoff cycle (e.g. 26th to 25th)", str(PeriodType.CUSTOM_CUTOFF))
        for day in range(1, 32):
            self.period_start_day.addItem(f"Starts on day {day}", day)
        self.period_start_day.setCurrentIndex(25)

        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Client", self.client)
        form.addRow("Project code", self.code)
        form.addRow("Timesheet deadline", self.submission)
        form.addRow("Period", self.period_type)
        form.addRow("", self.period_start_day)
        form.addRow("Usual site", self.default_site)
        form.addRow("Usual round trip (km)", self.default_km)
        form.addRow("Notes", self.notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.period_type.currentIndexChanged.connect(self._update_period_visibility)
        self._load()
        self._update_period_visibility()

    def _update_period_visibility(self) -> None:
        is_cutoff = self.period_type.currentData() == str(PeriodType.CUSTOM_CUTOFF)
        self.period_start_day.setVisible(is_cutoff)

    def _load(self) -> None:
        if self.project_id is None:
            return
        row = self.repo.get_project(self.project_id)
        if row is None:
            return
        self.name.setText(row["name"])
        self.client.setText(row["client"] or "")
        self.code.setText(row["project_code"] or "")
        self.default_site.setText(row["default_site"] or "")
        self.default_km.setText(str(row["default_km"]) if row["default_km"] else "")
        self.notes.setPlainText(row["notes"] or "")
        index = self.submission.findData(row["submission_day"])
        self.submission.setCurrentIndex(max(0, index))
        index = self.period_type.findData(row["period_type"])
        self.period_type.setCurrentIndex(max(0, index))
        if row["period_start_day"]:
            index = self.period_start_day.findData(row["period_start_day"])
            if index >= 0:
                self.period_start_day.setCurrentIndex(index)

    def _on_save(self) -> None:
        period_type = self.period_type.currentData()
        is_cutoff = period_type == str(PeriodType.CUSTOM_CUTOFF)
        fields = {
            "name": self.name.text(),
            "client": self.client.text(),
            "project_code": self.code.text(),
            "submission_day": self.submission.currentData(),
            "period_type": period_type,
            "period_start_day": self.period_start_day.currentData() if is_cutoff else None,
            "default_site": self.default_site.text(),
            "notes": self.notes.toPlainText(),
        }
        try:
            fields["default_km"] = _decimal_or_none(self.default_km.text())
            if self.project_id is None:
                self.project_id = self.repo.add_project(**fields)
            else:
                self.repo.update_project(self.project_id, **fields)
        except ValidationError as exc:
            warn(self, str(exc))
            return
        self.accept()


class BulkAddProjectsDialog(QDialog):
    """Paste the intranet dropdown, one project name per line."""

    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.created: list[int] = []
        self.setWindowTitle("Add several projects")
        self.setMinimumSize(440, 340)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "Paste project names here, one per line.\n\n"
            "Kloof Tailings Dam - TSF Raise\n"
            "Rustenburg Slimes Dam - Stability Review"
        )

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Copy the project list from the intranet dropdown and paste it below. "
            "One name per line. Names you already have are skipped."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(self.text)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_ok(self) -> None:
        created, skipped = self.repo.bulk_add_projects(self.text.toPlainText())
        self.created = created
        message = f"Added {len(created)} project{'s' if len(created) != 1 else ''}."
        if skipped:
            message += f"\n\nAlready on the list, so skipped:\n" + "\n".join(
                f"  {name}" for name in skipped[:12]
            )
            if len(skipped) > 12:
                message += f"\n  ... and {len(skipped) - 12} more"
        QMessageBox.information(self, "Projects added", message)
        self.accept()


class CloseDialog(QDialog):
    """What clicking the X should do. Never decides on the user's behalf."""

    MINIMISE = "tray"
    EXIT = "exit"
    CANCEL = "cancel"

    def __init__(
        self, parent: QWidget | None = None, running_summary: list[str] | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Close TimeTrack")
        self.choice = self.CANCEL
        self.dont_ask_again = QCheckBox("Don't ask again - always minimise to the tray")

        layout = QVBoxLayout(self)
        running_summary = running_summary or []

        if running_summary:
            warning = QLabel(
                "<b>A timer is still running:</b><br>"
                + "<br>".join(running_summary)
                + "<br><br>If you exit, it will be stopped and the time saved."
            )
            warning.setStyleSheet(
                f"background: {theme.BANNER_BACKGROUND}; "
                f"border: 1px solid {theme.BANNER_BORDER}; padding: 8px;"
            )
            warning.setWordWrap(True)
            layout.addWidget(warning)
        else:
            layout.addWidget(QLabel("What would you like to do?"))

        minimise = QPushButton("Minimise to tray")
        minimise.setObjectName("primary")
        minimise.setDefault(True)
        exit_button = QPushButton("Exit TimeTrack")
        cancel = QPushButton("Cancel")

        minimise.clicked.connect(lambda: self._choose(self.MINIMISE))
        exit_button.clicked.connect(lambda: self._choose(self.EXIT))
        cancel.clicked.connect(self.reject)

        layout.addWidget(minimise)
        layout.addWidget(exit_button)
        layout.addWidget(cancel)

        # Off by default and reversible in Settings, as the brief requires.
        self.dont_ask_again.setChecked(False)
        layout.addWidget(self.dont_ask_again)

        self._running = bool(running_summary)

    def _choose(self, choice: str) -> None:
        if choice == self.EXIT and self._running:
            confirmed = QMessageBox.question(
                self,
                "Stop the running timer?",
                "Exiting will stop the running timer and save the time recorded "
                "so far.\n\nIs that what you want?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmed != QMessageBox.StandardButton.Yes:
                return
        self.choice = choice
        self.accept()


class RecoveryDialog(QDialog):
    """Offered at startup when a timer was running the last time we saw it."""

    def __init__(
        self,
        parent: QWidget | None,
        entry: SavedEntry,
        project_name: str,
        task_name: str | None,
        tz,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("A timer was still running")
        self.decision: RecoveryDecision | None = None
        self.edited_end: _dt.datetime | None = None
        self.entry = entry
        self.tz = tz

        last_seen = entry.heartbeat_at or entry.started_at
        local_seen = to_local(last_seen, tz)
        what = project_name + (f" · {task_name}" if task_name else "")

        layout = QVBoxLayout(self)
        message = QLabel(
            f"A <b>{entry.kind.value}</b> timer for <b>{what}</b> was running when "
            f"TimeTrack last closed.<br><br>"
            f"It was last seen at <b>{local_seen:%H:%M}</b> on "
            f"{local_seen:%a %d %b} "
            f"(<b>{format_hm(entry.duration_seconds)}</b> recorded)."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        keep = QPushButton(f"Keep that time ({format_hm(entry.duration_seconds)})")
        keep.setObjectName("primary")
        keep.setDefault(True)
        edit = QPushButton("Let me set the end time")
        discard = QPushButton("Discard it")

        keep.clicked.connect(lambda: self._choose(RecoveryDecision.KEEP))
        edit.clicked.connect(self._choose_edit)
        discard.clicked.connect(self._choose_discard)

        layout.addWidget(keep)
        layout.addWidget(edit)

        self.end_row = QWidget()
        end_layout = QHBoxLayout(self.end_row)
        end_layout.setContentsMargins(0, 0, 0, 0)
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("ddd d MMM yyyy")
        self.end_date.setDate(QDate(local_seen.year, local_seen.month, local_seen.day))
        self.end_time = QTimeEdit()
        self.end_time.setDisplayFormat("HH:mm")
        self.end_time.setTime(QTime(local_seen.hour, local_seen.minute))
        confirm = QPushButton("Use this time")
        confirm.clicked.connect(self._confirm_edit)
        end_layout.addWidget(QLabel("Finished at"))
        end_layout.addWidget(self.end_date)
        end_layout.addWidget(self.end_time)
        end_layout.addWidget(confirm)
        self.end_row.setVisible(False)
        layout.addWidget(self.end_row)

        layout.addWidget(discard)
        note = QLabel(
            "Nothing is thrown away unless you choose to. Discarded time stays "
            "in the audit trail and can be brought back."
        )
        note.setWordWrap(True)
        set_role(note, "muted")
        layout.addWidget(note)

    def _choose(self, decision: RecoveryDecision) -> None:
        self.decision = decision
        self.accept()

    def _choose_edit(self) -> None:
        self.end_row.setVisible(True)
        self.adjustSize()

    def _confirm_edit(self) -> None:
        day = self.end_date.date().toPython()
        time_of_day = self.end_time.time().toPython()
        self.edited_end = _dt.datetime.combine(day, time_of_day, tzinfo=self.tz)
        if self.edited_end < self.entry.started_at:
            warn(self, "That is before the timer started. Please pick a later time.")
            return
        self.decision = RecoveryDecision.EDIT
        self.accept()

    def _choose_discard(self) -> None:
        confirmed = QMessageBox.question(
            self,
            "Discard this time?",
            "The entry will be marked as deleted. You can still find it in the "
            "audit trail if you change your mind.\n\nDiscard it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed == QMessageBox.StandardButton.Yes:
            self._choose(RecoveryDecision.DISCARD)
