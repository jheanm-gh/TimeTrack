"""The Projects tab: the register, its deadlines, and the tasks under each.

Archiving is presented as "mark done", because that is what it means to the
user: the project leaves the pickers but every hour ever booked to it stays
in the database and in the workbook.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.calc import (
    ValidationError,
    daily_rollup,
    days_until,
    period_for_date,
    rollup_totals,
    submission_deadline,
)
from app.core.models import ProjectStatus, TaskStatus
from app.core.timeutil import format_hm, utc_now
from app.db.repository import Repository
from app.ui import theme
from app.ui.dialogs import BulkAddProjectsDialog, ProjectDialog, warn
from app.ui.widgets import ID_ROLE, SortableItem, configure_table, muted

COLUMNS = ["Project", "Client", "Code", "Period", "Deadline", "Hours", "Billed", "Km", "Status"]
(
    COL_NAME,
    COL_CLIENT,
    COL_CODE,
    COL_PERIOD,
    COL_DEADLINE,
    COL_HOURS,
    COL_BILLED,
    COL_KM,
    COL_STATUS,
) = range(9)


class ProjectsTab(QWidget):
    """Projects, their current period totals, and their tasks."""

    data_changed = Signal()

    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        configure_table(self.table, sortable=True)

        self.show_archived = QCheckBox("Show finished projects")
        self.show_archived.toggled.connect(self.refresh)

        add = QPushButton("Add project")
        add.setObjectName("primary")
        bulk = QPushButton("Add several (paste a list)")
        edit = QPushButton("Edit")
        self.archive_button = QPushButton("Mark done")

        add.clicked.connect(self.add_project)
        bulk.clicked.connect(self.bulk_add)
        edit.clicked.connect(self.edit_project)
        self.archive_button.clicked.connect(self.toggle_archived)

        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(bulk)
        buttons.addWidget(edit)
        buttons.addWidget(self.archive_button)
        buttons.addStretch(1)
        buttons.addWidget(self.show_archived)

        # -- tasks panel --
        self.tasks = QListWidget()
        self.task_heading = QLabel("Tasks")
        self.task_heading.setFont(theme.heading_font())
        # Project names are long; let the heading wrap rather than clip.
        self.task_heading.setWordWrap(True)
        self.show_done_tasks = QCheckBox("Show finished tasks")
        self.show_done_tasks.toggled.connect(self.refresh_tasks)

        add_task = QPushButton("Add task")
        self.task_done_button = QPushButton("Mark task done")
        add_task.clicked.connect(self.add_task)
        self.task_done_button.clicked.connect(self.toggle_task_done)

        task_buttons = QHBoxLayout()
        task_buttons.addWidget(add_task)
        task_buttons.addWidget(self.task_done_button)
        task_buttons.addStretch(1)

        task_panel = QWidget()
        # Capped rather than merely weighted: a stretch factor is re-applied on
        # every resize and kept stealing width from the projects table.
        task_panel.setMaximumWidth(320)
        task_layout = QVBoxLayout(task_panel)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.addWidget(self.task_heading)
        task_layout.addWidget(self.tasks, 1)
        task_layout.addLayout(task_buttons)
        task_layout.addWidget(self.show_done_tasks)

        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addLayout(buttons)
        table_layout.addWidget(self.table, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(table_panel)
        splitter.addWidget(task_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([760, 280])

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self.edit_project())

    # -- loading ----------------------------------------------------------

    def refresh(self) -> None:
        include_archived = self.show_archived.isChecked()
        projects = self.repo.list_projects(include_archived=include_archived)
        today = _dt.datetime.now(tz=self.repo.timezone()).date()

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(projects))
        warning_days = self.repo.get_int("ui.deadline_warning_days", 5)

        for row_index, project in enumerate(projects):
            totals, period = self._period_totals(project, today)
            deadline = submission_deadline(period.end, project["submission_day"])
            submitted = self.repo.is_submitted(project["id"], period.start, period.end)
            remaining = days_until(deadline, today)

            deadline_text = "-"
            if deadline is not None:
                deadline_text = f"{deadline:%a %d %b}"
                if submitted:
                    deadline_text += "  (submitted)"
                elif remaining is not None and remaining < 0:
                    deadline_text += f"  ({abs(remaining)} days overdue)"
                elif remaining is not None and remaining <= warning_days:
                    deadline_text += f"  (in {remaining} days)"

            cells = [
                SortableItem(project["name"]),
                SortableItem(project["client"] or ""),
                SortableItem(project["project_code"] or ""),
                SortableItem(period.label, period.start),
                SortableItem(deadline_text, deadline or _dt.date.max),
                SortableItem(format_hm(totals["work_seconds"]), totals["work_seconds"]),
                SortableItem(
                    str(totals["work_hours_billed"]), totals["work_hours_billed"]
                ),
                SortableItem(str(totals["km"]), totals["km"]),
                SortableItem(
                    "Done" if project["status"] == "archived" else "Active"
                ),
            ]

            overdue = (
                deadline is not None
                and remaining is not None
                and remaining < 0
                and not submitted
                and totals["work_seconds"] > 0
            )
            due_soon = (
                deadline is not None
                and remaining is not None
                and 0 <= remaining <= warning_days
                and not submitted
            )

            for column, item in enumerate(cells):
                item.setData(ID_ROLE, project["id"])
                if project["status"] == "archived":
                    muted(item)
                elif overdue:
                    item.setBackground(QColor(theme.OVERDUE_BACKGROUND))
                elif due_soon:
                    item.setBackground(QColor(theme.BANNER_BACKGROUND))
                self.table.setItem(row_index, column, item)

        header = self.table.horizontalHeader()
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        # The project name is the column the user actually reads, and these
        # names are long. Stretch would let the other eight columns crowd it
        # down to "Kloof Tail...", so it gets a fixed, generous width that the
        # user can drag, and the table scrolls if the rest does not fit.
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(COL_NAME, 240)
        header.setStretchLastSection(True)
        self.table.setSortingEnabled(True)

        if self.table.rowCount() and not self.table.selectionModel().selectedRows():
            self.table.selectRow(0)
        self.refresh_tasks()

    def _period_totals(self, project, today: _dt.date):
        """This project's current submission period, and what is in it."""
        period = period_for_date(
            today, project["period_type"], project["period_start_day"]
        )
        entries = self.repo.entries_for_calc(
            start=period.start, end=period.end, project_id=project["id"]
        )
        rows = daily_rollup(
            entries, self.repo.timezone(), self.repo.rounding_rule(), now=utc_now()
        )
        return rollup_totals(rows), period

    # -- tasks ------------------------------------------------------------

    def selected_project_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), COL_NAME)
        return item.data(ID_ROLE) if item else None

    def _on_selection_changed(self) -> None:
        project_id = self.selected_project_id()
        if project_id is not None:
            project = self.repo.get_project(project_id)
            if project is not None:
                self.archive_button.setText(
                    "Bring back" if project["status"] == "archived" else "Mark done"
                )
        self.refresh_tasks()

    def refresh_tasks(self) -> None:
        self.tasks.clear()
        project_id = self.selected_project_id()
        if project_id is None:
            self.task_heading.setText("Tasks")
            return
        project = self.repo.get_project(project_id)
        self.task_heading.setText(f"Tasks - {project['name']}" if project else "Tasks")

        for row in self.repo.list_tasks(
            project_id, include_done=self.show_done_tasks.isChecked()
        ):
            label = row["name"]
            if row["parent_task_id"]:
                label = "    " + label
            if row["status"] == "done":
                label += "  (done)"
            item = QListWidgetItem(label)
            item.setData(ID_ROLE, row["id"])
            if row["status"] == "done":
                item.setForeground(QColor(theme.MUTED))
            self.tasks.addItem(item)

    def add_task(self) -> None:
        project_id = self.selected_project_id()
        if project_id is None:
            warn(self, "Select a project first.")
            return
        name, accepted = QInputDialog.getText(self, "Add a task", "Task name")
        if not accepted or not name.strip():
            return
        try:
            self.repo.add_task(project_id, name)
        except ValidationError as exc:
            warn(self, str(exc))
            return
        self.refresh_tasks()
        self.data_changed.emit()

    def toggle_task_done(self) -> None:
        item = self.tasks.currentItem()
        if item is None:
            warn(self, "Select a task first.")
            return
        task_id = item.data(ID_ROLE)
        task = self.repo.get_task(task_id)
        if task is None:
            return
        new_status = (
            TaskStatus.ACTIVE if task["status"] == "done" else TaskStatus.DONE
        )
        self.repo.set_task_status(task_id, new_status)
        self.refresh_tasks()
        self.data_changed.emit()

    # -- projects ---------------------------------------------------------

    def add_project(self) -> None:
        dialog = ProjectDialog(self.repo, self)
        if dialog.exec():
            self.refresh()
            self.data_changed.emit()

    def bulk_add(self) -> None:
        dialog = BulkAddProjectsDialog(self.repo, self)
        if dialog.exec():
            self.refresh()
            self.data_changed.emit()

    def edit_project(self) -> None:
        project_id = self.selected_project_id()
        if project_id is None:
            warn(self, "Select a project first.")
            return
        dialog = ProjectDialog(self.repo, self, project_id=project_id)
        if dialog.exec():
            self.refresh()
            self.data_changed.emit()

    def toggle_archived(self) -> None:
        project_id = self.selected_project_id()
        if project_id is None:
            warn(self, "Select a project first.")
            return
        project = self.repo.get_project(project_id)
        if project is None:
            return

        if project["status"] == "archived":
            self.repo.unarchive_project(project_id)
        else:
            confirmed = QMessageBox.question(
                self,
                "Mark this project done?",
                f"'{project['name']}' will disappear from the timer pickers.\n\n"
                "All of its history is kept, it still appears in the workbook, "
                "and you can bring it back at any time.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if confirmed != QMessageBox.StandardButton.Yes:
                return
            self.repo.archive_project(project_id)

        self.refresh()
        self.data_changed.emit()

    def overdue_projects(self) -> list[tuple[str, int]]:
        """Projects with time recorded, a passed deadline and no submission.

        Used by the main window to raise the loud banner.
        """
        today = _dt.datetime.now(tz=self.repo.timezone()).date()
        overdue: list[tuple[str, int]] = []
        for project in self.repo.list_projects():
            totals, period = self._period_totals(project, today)
            if totals["work_seconds"] <= 0:
                continue
            deadline = submission_deadline(period.end, project["submission_day"])
            remaining = days_until(deadline, today)
            if remaining is None or remaining >= 0:
                continue
            if self.repo.is_submitted(project["id"], period.start, period.end):
                continue
            overdue.append((project["name"], abs(remaining)))
        return overdue
