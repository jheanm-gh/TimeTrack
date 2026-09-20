"""The Today tab: everything recorded today, editable in place.

Manual entry is first-class here, not tucked away. The user has said plainly
that he will forget to start timers, so "Add entry" and "Log a site trip" sit
at the top of the tab rather than behind a menu.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.calc import (
    ValidationError,
    daily_rollup,
    entry_duration_seconds,
    rollup_totals,
)
from app.core.models import EntryKind
from app.core.timeutil import format_hm, to_local, utc_now
from app.db.repository import Repository
from app.ui import theme
from app.ui.dialogs import EntryDialog, warn
from app.ui.widgets import (
    ID_ROLE,
    ComboDelegate,
    DecimalDelegate,
    SortableItem,
    TimeDelegate,
    configure_table,
    muted,
    set_role,
)

COLUMNS = [
    "Project",
    "Task",
    "Type",
    "Start",
    "End",
    "Duration",
    "Description",
    "Software",
    "Km",
]
COL_PROJECT, COL_TASK, COL_TYPE, COL_START, COL_END, COL_DURATION, COL_DESC, COL_SOFTWARE, COL_KM = range(9)

#: Columns the user may type into directly.
EDITABLE = {COL_PROJECT, COL_TASK, COL_START, COL_END, COL_DESC, COL_SOFTWARE, COL_KM}


class TodayTab(QWidget):
    """Today's entries with running totals."""

    data_changed = Signal()

    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self._loading = False

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        configure_table(self.table)
        self._install_delegates()

        self.totals = QLabel()
        self.totals.setFont(theme.heading_font(11))

        add_entry = QPushButton("Add entry by hand")
        add_entry.setToolTip("For work you did without starting a timer")
        log_travel = QPushButton("Log a site trip")
        edit_button = QPushButton("Edit selected")
        delete_button = QPushButton("Delete selected")

        add_entry.clicked.connect(self.add_entry)
        log_travel.clicked.connect(self.log_travel)
        edit_button.clicked.connect(self.edit_selected)
        delete_button.clicked.connect(self.delete_selected)

        buttons = QHBoxLayout()
        buttons.addWidget(add_entry)
        buttons.addWidget(log_travel)
        buttons.addStretch(1)
        buttons.addWidget(edit_button)
        buttons.addWidget(delete_button)

        hint = QLabel(
            "Double-click a cell to change it. Every edit is recorded, and the "
            "previous value can always be recovered."
        )
        set_role(hint, "muted")

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.table, 1)
        layout.addWidget(hint)
        layout.addWidget(self.totals)

        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemDoubleClicked.connect(self._on_double_click)

    # -- setup ------------------------------------------------------------

    def _install_delegates(self) -> None:
        self.table.setItemDelegateForColumn(
            COL_PROJECT,
            ComboDelegate(lambda _index: self._project_options(), self.table),
        )
        self.table.setItemDelegateForColumn(
            COL_TASK, ComboDelegate(self._task_options, self.table)
        )
        self.table.setItemDelegateForColumn(COL_START, TimeDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_END, TimeDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_KM, DecimalDelegate(self.table))
        self.table.setItemDelegateForColumn(
            COL_SOFTWARE,
            ComboDelegate(
                lambda _index: [(name, name) for name in self.repo.software_names()],
                self.table,
                editable=True,
            ),
        )

    def _project_options(self) -> list[tuple[str, object]]:
        return [(row["name"], row["id"]) for row in self.repo.list_projects()]

    def _task_options(self, index) -> list[tuple[str, object]]:
        entry_id = self._entry_id_for_row(index.row())
        entry = self.repo.get_entry(entry_id) if entry_id else None
        options: list[tuple[str, object]] = [("(no task)", None)]
        if entry is not None:
            options += [
                (row["name"], row["id"]) for row in self.repo.list_tasks(entry.project_id)
            ]
        return options

    # -- loading ----------------------------------------------------------

    def today(self) -> _dt.date:
        return _dt.datetime.now(tz=self.repo.timezone()).date()

    def refresh(self) -> None:
        self._loading = True
        try:
            self._populate()
        finally:
            self._loading = False

    def _populate(self) -> None:
        tz = self.repo.timezone()
        day = self.today()
        entries = self.repo.list_entries(start=day, end=day, tz=tz)

        self.table.setRowCount(0)
        self.table.setRowCount(len(entries))
        names = self.repo.project_names()

        for row_index, entry in enumerate(entries):
            task_name = ""
            if entry.task_id:
                task_row = self.repo.get_task(entry.task_id)
                task_name = task_row["name"] if task_row else ""

            start_local = to_local(entry.started_at, tz)
            # The stored duration is only refreshed by the heartbeat, so a
            # timer started moments ago still reads zero. Compute the live
            # figure for anything still running.
            counted = (
                entry_duration_seconds(entry.to_calc(), now=utc_now())
                if entry.is_running
                else entry.duration_seconds
            )
            end_local = to_local(entry.ended_at, tz) if entry.ended_at else None
            running = entry.is_running

            cells = {
                COL_PROJECT: SortableItem(names.get(entry.project_id, "?")),
                COL_TASK: SortableItem(task_name),
                COL_TYPE: SortableItem(
                    "Software" if entry.kind is EntryKind.SOFTWARE else "Work"
                ),
                COL_START: SortableItem(f"{start_local:%H:%M}", start_local),
                COL_END: SortableItem(
                    f"{end_local:%H:%M}" if end_local else "running", end_local
                ),
                COL_DURATION: SortableItem(
                    format_hm(counted), counted
                ),
                COL_DESC: SortableItem(entry.description),
                COL_SOFTWARE: SortableItem(entry.software_name or ""),
                COL_KM: SortableItem(
                    str(entry.travel.km_travelled) if entry.travel.km_travelled else ""
                ),
            }

            for column, item in cells.items():
                editable = column in EDITABLE and not running
                if column is COL_SOFTWARE and entry.kind is not EntryKind.SOFTWARE:
                    editable = False
                flags = item.flags()
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEditable
                else:
                    flags &= ~Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                item.setData(ID_ROLE, entry.id)
                if running:
                    muted(item)
                self.table.setItem(row_index, column, item)

        header = self.table.horizontalHeader()
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DESC, QHeaderView.ResizeMode.Stretch)

        self._update_totals(entries, day, tz)

    def _update_totals(self, entries, day, tz) -> None:
        rows = daily_rollup(
            [entry.to_calc() for entry in entries],
            tz,
            self.repo.rounding_rule(),
            now=utc_now(),
        )
        totals = rollup_totals(rows)
        self.totals.setText(
            f"Today   "
            f"Work {format_hm(totals['work_seconds'])} "
            f"(bills {totals['work_hours_billed']})    "
            f"Software {format_hm(totals['software_seconds'])} "
            f"(bills {totals['software_hours_billed']})    "
            f"Travel {totals['km']} km"
        )

    # -- editing ----------------------------------------------------------

    def _entry_id_for_row(self, row: int) -> int | None:
        item = self.table.item(row, COL_PROJECT)
        return item.data(ID_ROLE) if item else None

    def _on_item_changed(self, item) -> None:
        if self._loading:
            return
        entry_id = item.data(ID_ROLE)
        if entry_id is None:
            return
        column = item.column()
        text = item.text().strip()

        try:
            updates = self._updates_for(entry_id, column, text, item)
            if updates:
                self.repo.update_entry(entry_id, **updates)
        except ValidationError as exc:
            warn(self, str(exc))
        self.refresh()
        self.data_changed.emit()

    def _updates_for(self, entry_id: int, column: int, text: str, item) -> dict:
        entry = self.repo.get_entry(entry_id)
        if entry is None:
            return {}
        tz = self.repo.timezone()

        if column == COL_DESC:
            return {"description": text}
        if column == COL_KM:
            if not text:
                return {"km_travelled": None, "odo_start": None, "odo_end": None}
            try:
                value = Decimal(text)
            except InvalidOperation as exc:
                raise ValidationError(f"'{text}' is not a number.") from exc
            # A typed distance replaces the odometer pair; keeping both would
            # leave the derived value fighting the entered one.
            return {"km_travelled": value, "odo_start": None, "odo_end": None}
        if column == COL_SOFTWARE:
            return {"software_name": text or None}
        if column == COL_PROJECT:
            project_id = item.data(ID_ROLE + 1) or self._lookup_project(text)
            if project_id is None:
                raise ValidationError(f"There is no project called '{text}'.")
            return {"project_id": project_id, "task_id": None}
        if column == COL_TASK:
            if not text or text == "(no task)":
                return {"task_id": None}
            for row in self.repo.list_tasks(entry.project_id):
                if row["name"] == text:
                    return {"task_id": row["id"]}
            raise ValidationError(
                f"'{text}' is not a task on this project. Add it on the "
                "Projects tab first."
            )
        if column in (COL_START, COL_END):
            return self._time_update(entry, column, text, tz)
        return {}

    def _time_update(self, entry, column: int, text: str, tz) -> dict:
        parts = text.split(":")
        try:
            hour, minute = int(parts[0]), int(parts[1])
            new_time = _dt.time(hour, minute)
        except (ValueError, IndexError) as exc:
            raise ValidationError(
                f"'{text}' is not a time. Use 24-hour format, for example 14:30."
            ) from exc

        start_local = to_local(entry.started_at, tz)
        if column == COL_START:
            new_start = _dt.datetime.combine(start_local.date(), new_time, tzinfo=tz)
            return {"started_at": new_start}

        if entry.ended_at is None:
            raise ValidationError("This timer is still running - stop it first.")
        end_local = to_local(entry.ended_at, tz)
        new_end = _dt.datetime.combine(end_local.date(), new_time, tzinfo=tz)
        if new_end < entry.started_at:
            # Sessions that run past midnight are normal; assume that rather
            # than rejecting a perfectly sensible edit.
            new_end += _dt.timedelta(days=1)
        return {"ended_at": new_end}

    def _lookup_project(self, name: str) -> int | None:
        row = self.repo.find_project_by_name(name)
        return row["id"] if row else None

    def _on_double_click(self, item) -> None:
        if item.column() in EDITABLE:
            return  # inline editing handles it
        self.edit_selected()

    # -- actions ----------------------------------------------------------

    def _selected_entry_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._entry_id_for_row(rows[0].row())

    def add_entry(self) -> None:
        if not self.repo.list_projects():
            warn(self, "Add a project first, on the Projects tab.")
            return
        dialog = EntryDialog(self.repo, self, default_date=self.today())
        if dialog.exec():
            self.refresh()
            self.data_changed.emit()

    def log_travel(self) -> None:
        if not self.repo.list_projects():
            warn(self, "Add a project first, on the Projects tab.")
            return
        dialog = EntryDialog(
            self.repo, self, default_date=self.today(), travel_only=True
        )
        if dialog.exec():
            self.refresh()
            self.data_changed.emit()

    def edit_selected(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is None:
            warn(self, "Select a row first.")
            return
        entry = self.repo.get_entry(entry_id)
        if entry is None:
            return
        if entry.is_running:
            warn(self, "That timer is still running. Stop it before editing it.")
            return
        dialog = EntryDialog(self.repo, self, entry=entry)
        if dialog.exec():
            self.refresh()
            self.data_changed.emit()

    def delete_selected(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is None:
            warn(self, "Select a row first.")
            return
        confirmed = QMessageBox.question(
            self,
            "Delete this entry?",
            "The entry will be hidden from your timesheet, but it is not "
            "destroyed - it stays in the audit trail and can be brought back.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self.repo.soft_delete_entry(entry_id)
        self.refresh()
        self.data_changed.emit()
