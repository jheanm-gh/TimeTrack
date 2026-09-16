"""The Review & Submit tab: the rows that get typed into the intranet.

This is the tab the application exists for. The user works down it with the
form open on the other screen, so three things matter more than anything
else:

* the rows appear in the order he will type them, one per date;
* each date can be **ticked off** as it is entered, and the tick persists;
* the next un-ticked row is obvious at a glance.

Because the intranet accepts no block paste, every field has to be copied
individually - so Ctrl+C copies whichever single cell is under the cursor,
rather than the whole row.
"""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.calc import (
    build_description,
    daily_rollup,
    format_sessions,
    period_for_date,
    rollup_totals,
    submission_deadline,
)
from app.core.timeutil import day_name, format_hm, utc_now
from app.db.repository import Repository
from app.ui import theme
from app.ui.widgets import ID_ROLE, SortableItem, configure_table, emphasise, muted

COLUMNS = ["Done", "Date", "Day", "Hours", "Software", "Description", "Sessions", "Km"]
COL_TICK, COL_DATE, COL_DAY, COL_HOURS, COL_SOFTWARE, COL_DESC, COL_SESSIONS, COL_KM = range(8)

#: Highlight for the row the user should be typing in next.
NEXT_ROW_BACKGROUND = "#FFF3CD"


class ReviewTable(QTableWidget):
    """A table whose Ctrl+C copies one cell, because the form takes one field."""

    def keyPressEvent(self, event):  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Copy):
            item = self.currentItem()
            if item is not None:
                QApplication.clipboard().setText(item.text())
                return
        super().keyPressEvent(event)


class ReviewTab(QWidget):
    """Pick a project and a period; work down the list ticking dates off."""

    data_changed = Signal()

    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self._loading = False
        self._period_start: _dt.date | None = None
        self._period_end: _dt.date | None = None

        self.project = QComboBox()
        self.preset = QComboBox()
        self.preset.addItem("This project's current period", "current")
        self.preset.addItem("This month", "this_month")
        self.preset.addItem("Last month", "last_month")
        self.preset.addItem("Custom dates", "custom")

        self.start_date = QDateEdit()
        self.end_date = QDateEdit()
        for widget in (self.start_date, self.end_date):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("d MMM yyyy")
            widget.dateChanged.connect(self._on_custom_dates)

        self.project.currentIndexChanged.connect(self.refresh)
        self.preset.currentIndexChanged.connect(self.refresh)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Project"))
        controls.addWidget(self.project, 2)
        controls.addWidget(self.preset)
        controls.addWidget(self.start_date)
        self.to_label = QLabel("to")
        controls.addWidget(self.to_label)
        controls.addWidget(self.end_date)
        controls.addStretch(1)

        self.summary = QLabel()
        self.summary.setFont(theme.heading_font(11))
        self.deadline_label = QLabel()

        self.table = ReviewTable(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        configure_table(self.table)
        self.table.itemChanged.connect(self._on_item_changed)

        self.progress = QLabel()
        copy_description = QPushButton("Copy description")
        copy_description.setToolTip("Copy this row's description to the clipboard")
        copy_description.clicked.connect(self._copy_description)
        tick_button = QPushButton("Tick / untick selected")
        tick_button.clicked.connect(self._toggle_selected_tick)
        self.submit_button = QPushButton("Mark period as submitted")
        self.submit_button.setObjectName("primary")
        self.submit_button.clicked.connect(self._toggle_submitted)

        actions = QHBoxLayout()
        actions.addWidget(self.progress)
        actions.addStretch(1)
        actions.addWidget(copy_description)
        actions.addWidget(tick_button)
        actions.addWidget(self.submit_button)

        hint = QLabel(
            "Tick each date as you enter it on the intranet. The next date to "
            "do is highlighted. Ctrl+C copies just the cell you are on."
        )
        hint.setStyleSheet(f"color: {theme.MUTED.name()};")

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.deadline_label)
        layout.addWidget(self.table, 1)
        layout.addWidget(hint)
        layout.addWidget(self.summary)
        layout.addLayout(actions)

    # -- period selection -------------------------------------------------

    def _reload_projects(self) -> None:
        current = self.project.currentData()
        self.project.blockSignals(True)
        self.project.clear()
        for row in self.repo.list_projects(include_archived=True):
            label = row["name"] + (" (done)" if row["status"] == "archived" else "")
            self.project.addItem(label, row["id"])
        index = self.project.findData(current)
        if index >= 0:
            self.project.setCurrentIndex(index)
        self.project.blockSignals(False)

    def _on_custom_dates(self) -> None:
        if self.preset.currentData() == "custom":
            self.refresh()

    def _resolve_period(self, project_row) -> tuple[_dt.date, _dt.date, str]:
        today = _dt.datetime.now(tz=self.repo.timezone()).date()
        choice = self.preset.currentData()

        if choice == "custom":
            return (
                self.start_date.date().toPython(),
                self.end_date.date().toPython(),
                "Custom dates",
            )
        if choice == "this_month":
            period = period_for_date(today, "calendar_month")
        elif choice == "last_month":
            first = today.replace(day=1)
            period = period_for_date(first - _dt.timedelta(days=1), "calendar_month")
        else:
            period = period_for_date(
                today, project_row["period_type"], project_row["period_start_day"]
            )
        return period.start, period.end, period.label

    # -- loading ----------------------------------------------------------

    def refresh(self) -> None:
        self._loading = True
        try:
            self._populate()
        finally:
            self._loading = False

    def _populate(self) -> None:
        self._reload_projects()
        project_id = self.project.currentData()
        custom = self.preset.currentData() == "custom"
        self.start_date.setVisible(custom)
        self.to_label.setVisible(custom)
        self.end_date.setVisible(custom)

        self.table.setRowCount(0)
        if project_id is None:
            self.summary.setText("Add a project to get started.")
            self.deadline_label.setText("")
            self.progress.setText("")
            return

        project_row = self.repo.get_project(project_id)
        start, end, label = self._resolve_period(project_row)
        self._period_start, self._period_end = start, end

        if not custom:
            self.start_date.blockSignals(True)
            self.end_date.blockSignals(True)
            self.start_date.setDate(QDate(start.year, start.month, start.day))
            self.end_date.setDate(QDate(end.year, end.month, end.day))
            self.start_date.blockSignals(False)
            self.end_date.blockSignals(False)

        tz = self.repo.timezone()
        entries = self.repo.entries_for_calc(
            start=start, end=end, project_id=project_id, tz=tz
        )
        rows = daily_rollup(entries, tz, self.repo.rounding_rule(), now=utc_now())
        rows = [row for row in rows if start <= row.date <= end]
        notes = self.repo.daily_notes_for(project_id, start, end)
        limit = self.repo.get_int("workbook.description_limit", 500)

        self.table.setRowCount(len(rows))
        next_row_index: int | None = None
        ticked_count = 0

        for row_index, day_row in enumerate(rows):
            note = notes.get((project_id, day_row.date))
            is_ticked = bool(note and note["ticked_at"])
            ticked_count += int(is_ticked)
            override = note["description_override"] if note else None
            description = override or build_description(
                list(day_row.descriptions), limit=limit
            )

            tick_item = QTableWidgetItem()
            tick_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            tick_item.setCheckState(
                Qt.CheckState.Checked if is_ticked else Qt.CheckState.Unchecked
            )

            description_item = SortableItem(description, editable=True)
            if override:
                description_item.setToolTip("You wrote this description yourself.")
                font = description_item.font()
                font.setItalic(True)
                description_item.setFont(font)

            cells = {
                COL_TICK: tick_item,
                COL_DATE: SortableItem(f"{day_row.date:%d %b %Y}", day_row.date),
                COL_DAY: SortableItem(day_name(day_row.date)),
                COL_HOURS: emphasise(
                    SortableItem(
                        str(day_row.work_hours_billed), day_row.work_hours_billed
                    )
                ),
                COL_SOFTWARE: SortableItem(
                    str(day_row.software_hours_billed)
                    if day_row.software_seconds
                    else "",
                    day_row.software_hours_billed,
                ),
                COL_DESC: description_item,
                COL_SESSIONS: muted(
                    SortableItem(format_sessions(list(day_row.sessions), tz))
                ),
                COL_KM: SortableItem(str(day_row.km) if day_row.km else ""),
            }

            for column, item in cells.items():
                item.setData(ID_ROLE, day_row.date.toordinal())
                if is_ticked:
                    muted(item)
                self.table.setItem(row_index, column, item)

            if not is_ticked and next_row_index is None:
                next_row_index = row_index

        # Make the next date to enter impossible to miss.
        if next_row_index is not None:
            for column in range(len(COLUMNS)):
                item = self.table.item(next_row_index, column)
                if item is not None and column != COL_HOURS:
                    item.setBackground(QColor(NEXT_ROW_BACKGROUND))
            self.table.selectRow(next_row_index)
            self.table.scrollToItem(self.table.item(next_row_index, COL_DATE))

        header = self.table.horizontalHeader()
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DESC, QHeaderView.ResizeMode.Stretch)

        self._update_summary(rows, label, start, end, project_row, ticked_count)

    def _update_summary(
        self, rows, label, start, end, project_row, ticked_count
    ) -> None:
        totals = rollup_totals(rows)
        submitted = self.repo.is_submitted(project_row["id"], start, end)

        self.summary.setText(
            f"{label}   "
            f"Hours worked {totals['work_hours_raw']} → "
            f"bill {totals['work_hours_billed']}   "
            f"Software {totals['software_hours_billed']}   "
            f"Travel {totals['km']} km   "
            f"(rounding adds {totals['work_rounding_gap']})"
        )
        self.progress.setText(
            f"{ticked_count} of {len(rows)} dates entered on the intranet"
            + ("   ✓ period marked submitted" if submitted else "")
        )
        self.submit_button.setText(
            "Undo 'submitted'" if submitted else "Mark period as submitted"
        )

        deadline = submission_deadline(end, project_row["submission_day"])
        if deadline is None:
            self.deadline_label.setText("No submission deadline set for this project.")
            self.deadline_label.setStyleSheet(f"color: {theme.MUTED.name()};")
            return
        today = _dt.datetime.now(tz=self.repo.timezone()).date()
        remaining = (deadline - today).days
        if submitted:
            text = f"Submitted. Deadline was {deadline:%a %d %B}."
            colour = theme.MUTED.name()
        elif remaining < 0:
            text = f"Overdue - the deadline was {deadline:%a %d %B}, {abs(remaining)} days ago."
            colour = theme.DANGER.name()
        else:
            text = f"Due {deadline:%a %d %B} - {remaining} days from now."
            colour = theme.PAUSED.name() if remaining <= 5 else theme.MUTED.name()
        self.deadline_label.setText(text)
        self.deadline_label.setStyleSheet(f"color: {colour}; font-weight: bold;")

    # -- editing ----------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        project_id = self.project.currentData()
        ordinal = item.data(ID_ROLE)
        if project_id is None or ordinal is None:
            return
        day = _dt.date.fromordinal(int(ordinal))

        if item.column() == COL_TICK:
            self.repo.set_ticked(
                project_id, day, item.checkState() == Qt.CheckState.Checked
            )
        elif item.column() == COL_DESC:
            self.repo.set_description_override(project_id, day, item.text())
        else:
            return
        self.refresh()
        self.data_changed.emit()

    def _toggle_selected_tick(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), COL_TICK)
        if item is None:
            return
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def _copy_description(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), COL_DESC)
        if item is not None:
            QApplication.clipboard().setText(item.text())

    def _toggle_submitted(self) -> None:
        project_id = self.project.currentData()
        if project_id is None or self._period_start is None:
            return
        start, end = self._period_start, self._period_end
        if self.repo.is_submitted(project_id, start, end):
            self.repo.unmark_submitted(project_id, start, end)
        else:
            confirmed = QMessageBox.question(
                self,
                "Mark this period as submitted?",
                f"This records that you have filed the timesheet for "
                f"{start:%d %b} to {end:%d %b %Y}.\n\n"
                "The deadline reminder for this period will stop.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if confirmed != QMessageBox.StandardButton.Yes:
                return
            self.repo.mark_submitted(project_id, start, end)
        self.refresh()
        self.data_changed.emit()
