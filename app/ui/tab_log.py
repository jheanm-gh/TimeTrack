"""The Log tab: every entry, filtered and sortable.

This is where a forgotten week gets reconstructed, so the filters are the
important part: narrow to a date range and a project, see what is there, and
fill in what is missing.
"""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.calc import daily_rollup, rollup_totals
from app.core.models import EntryKind
from app.core.timeutil import day_name, format_hm, to_local, utc_now
from app.db.repository import Repository
from app.ui import theme
from app.ui.dialogs import EntryDialog, warn
from app.ui.widgets import ID_ROLE, SortableItem, configure_table, muted

COLUMNS = [
    "Date", "Day", "Project", "Task", "Type", "Start", "End",
    "Duration", "Description", "Software", "Km", "Source", "Edited",
]
(
    COL_DATE, COL_DAY, COL_PROJECT, COL_TASK, COL_TYPE, COL_START, COL_END,
    COL_DURATION, COL_DESC, COL_SOFTWARE, COL_KM, COL_SOURCE, COL_EDITED,
) = range(13)


class LogTab(QWidget):
    """All entries, with date-range, project and kind filters."""

    data_changed = Signal()

    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo

        self.start_date = QDateEdit()
        self.end_date = QDateEdit()
        for widget in (self.start_date, self.end_date):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("d MMM yyyy")
            widget.dateChanged.connect(self.refresh)

        self.project_filter = QComboBox()
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("Work and software", None)
        self.kind_filter.addItem("Work only", str(EntryKind.WORK))
        self.kind_filter.addItem("Software only", str(EntryKind.SOFTWARE))
        self.project_filter.currentIndexChanged.connect(self.refresh)
        self.kind_filter.currentIndexChanged.connect(self.refresh)

        self.show_deleted = QCheckBox("Show deleted")
        self.show_deleted.toggled.connect(self.refresh)

        presets = QComboBox()
        presets.addItem("Last 30 days", 30)
        presets.addItem("Last 7 days", 7)
        presets.addItem("Last 60 days", 60)
        presets.addItem("Last 90 days", 90)
        presets.currentIndexChanged.connect(
            lambda: self.set_recent_days(presets.currentData())
        )
        self.presets = presets

        filters = QHBoxLayout()
        filters.addWidget(QLabel("From"))
        filters.addWidget(self.start_date)
        filters.addWidget(QLabel("to"))
        filters.addWidget(self.end_date)
        filters.addWidget(presets)
        filters.addWidget(QLabel("Project"))
        filters.addWidget(self.project_filter)
        filters.addWidget(self.kind_filter)
        filters.addWidget(self.show_deleted)
        filters.addStretch(1)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        configure_table(self.table, sortable=True)
        self.table.itemDoubleClicked.connect(lambda _item: self.edit_selected())

        add_button = QPushButton("Add entry by hand")
        edit_button = QPushButton("Edit selected")
        delete_button = QPushButton("Delete selected")
        self.restore_button = QPushButton("Restore selected")
        add_button.clicked.connect(self.add_entry)
        edit_button.clicked.connect(self.edit_selected)
        delete_button.clicked.connect(self.delete_selected)
        self.restore_button.clicked.connect(self.restore_selected)
        self.restore_button.setVisible(False)
        self.show_deleted.toggled.connect(self.restore_button.setVisible)

        actions = QHBoxLayout()
        actions.addWidget(add_button)
        actions.addStretch(1)
        actions.addWidget(edit_button)
        actions.addWidget(delete_button)
        actions.addWidget(self.restore_button)

        self.totals = QLabel()
        self.totals.setFont(theme.heading_font(10))

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.totals)
        layout.addLayout(actions)

        self.set_recent_days(30)

    # -- filters ----------------------------------------------------------

    def set_recent_days(self, days: int) -> None:
        today = _dt.datetime.now(tz=self.repo.timezone()).date()
        start = today - _dt.timedelta(days=days - 1)
        self.start_date.blockSignals(True)
        self.end_date.blockSignals(True)
        self.start_date.setDate(QDate(start.year, start.month, start.day))
        self.end_date.setDate(QDate(today.year, today.month, today.day))
        self.start_date.blockSignals(False)
        self.end_date.blockSignals(False)
        self.refresh()

    def show_dates(self, days: list[_dt.date]) -> None:
        """Jump to a specific set of dates - used by the catch-up nudge."""
        if not days:
            return
        first, last = min(days), max(days)
        self.start_date.blockSignals(True)
        self.end_date.blockSignals(True)
        self.start_date.setDate(QDate(first.year, first.month, first.day))
        self.end_date.setDate(QDate(last.year, last.month, last.day))
        self.start_date.blockSignals(False)
        self.end_date.blockSignals(False)
        self.project_filter.setCurrentIndex(0)
        self.refresh()

    def _reload_projects(self) -> None:
        current = self.project_filter.currentData()
        self.project_filter.blockSignals(True)
        self.project_filter.clear()
        self.project_filter.addItem("All projects", None)
        for row in self.repo.list_projects(include_archived=True):
            label = row["name"] + (" (done)" if row["status"] == "archived" else "")
            self.project_filter.addItem(label, row["id"])
        index = self.project_filter.findData(current)
        if index >= 0:
            self.project_filter.setCurrentIndex(index)
        self.project_filter.blockSignals(False)

    # -- loading ----------------------------------------------------------

    def refresh(self) -> None:
        self._reload_projects()
        tz = self.repo.timezone()
        start = self.start_date.date().toPython()
        end = self.end_date.date().toPython()
        kind_value = self.kind_filter.currentData()

        entries = self.repo.list_entries(
            start=start,
            end=end,
            project_id=self.project_filter.currentData(),
            kind=EntryKind(kind_value) if kind_value else None,
            include_deleted=self.show_deleted.isChecked(),
            tz=tz,
        )

        names = self.repo.project_names()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(entries))

        for row_index, entry in enumerate(entries):
            start_local = to_local(entry.started_at, tz)
            end_local = to_local(entry.ended_at, tz) if entry.ended_at else None
            task_name = ""
            if entry.task_id:
                task_row = self.repo.get_task(entry.task_id)
                task_name = task_row["name"] if task_row else ""

            cells = [
                SortableItem(f"{start_local:%d %b %Y}", start_local.date()),
                SortableItem(day_name(start_local.date())),
                SortableItem(names.get(entry.project_id, "?")),
                SortableItem(task_name),
                SortableItem("Software" if entry.kind is EntryKind.SOFTWARE else "Work"),
                SortableItem(f"{start_local:%H:%M}", start_local),
                SortableItem(
                    f"{end_local:%H:%M}" if end_local else "running",
                    end_local or _dt.datetime.max.replace(tzinfo=tz),
                ),
                SortableItem(format_hm(entry.duration_seconds), entry.duration_seconds),
                SortableItem(entry.description),
                SortableItem(entry.software_name or ""),
                SortableItem(
                    str(entry.travel.km_travelled) if entry.travel.km_travelled else "",
                    entry.travel.km_travelled or 0,
                ),
                SortableItem(entry.source.value),
                SortableItem("yes" if entry.edited else ""),
            ]

            for column, item in enumerate(cells):
                item.setData(ID_ROLE, entry.id)
                if entry.deleted_at is not None:
                    muted(item)
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                self.table.setItem(row_index, column, item)

        header = self.table.horizontalHeader()
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DESC, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)

        live = [entry for entry in entries if entry.deleted_at is None]
        rows = daily_rollup(
            [entry.to_calc() for entry in live],
            tz,
            self.repo.rounding_rule(),
            now=utc_now(),
        )
        totals = rollup_totals(rows)
        self.totals.setText(
            f"{len(live)} entries over {totals['days']} days   "
            f"Work {format_hm(totals['work_seconds'])} (bills {totals['work_hours_billed']})   "
            f"Software {format_hm(totals['software_seconds'])} "
            f"(bills {totals['software_hours_billed']})   "
            f"Travel {totals['km']} km"
        )

    # -- actions ----------------------------------------------------------

    def _selected_entry_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), COL_DATE)
        return item.data(ID_ROLE) if item else None

    def add_entry(self) -> None:
        if not self.repo.list_projects():
            warn(self, "Add a project first, on the Projects tab.")
            return
        dialog = EntryDialog(
            self.repo,
            self,
            default_project_id=self.project_filter.currentData(),
            default_date=self.end_date.date().toPython(),
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
            "It will be hidden from your timesheet but not destroyed - tick "
            "'Show deleted' to find it again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self.repo.soft_delete_entry(entry_id)
        self.refresh()
        self.data_changed.emit()

    def restore_selected(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is None:
            warn(self, "Select a row first.")
            return
        self.repo.restore_entry(entry_id)
        self.refresh()
        self.data_changed.emit()
