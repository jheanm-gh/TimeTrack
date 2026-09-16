"""The "Now" strip across the top of the window.

Two independent channels side by side, because they are genuinely
independent: work can run without software, software without work, or both
at once. Each has its own elapsed readout and its own Pause/Stop.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.models import EntryKind
from app.core.timeutil import format_hms
from app.db.repository import Repository
from app.ui import theme


class ChannelPanel(QFrame):
    """One timer channel: what is running, for how long, and the controls."""

    pause_clicked = Signal()
    stop_clicked = Signal()

    def __init__(
        self, title: str, accent, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.accent = accent

        self.title = QLabel(title)
        self.title.setFont(theme.small_font())
        self.title.setStyleSheet(f"color: {theme.MUTED.name()};")

        self.what = QLabel("Not running")
        self.what.setFont(theme.heading_font(10))
        self.what.setWordWrap(False)

        self.elapsed = QLabel("0:00:00")
        self.elapsed.setFont(theme.elapsed_font(24))

        self.pause_button = QPushButton("Pause")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("danger")
        self.pause_button.clicked.connect(self.pause_clicked.emit)
        self.stop_button.clicked.connect(self.stop_clicked.emit)

        buttons = QHBoxLayout()
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self.title)
        layout.addWidget(self.elapsed)
        layout.addWidget(self.what)
        layout.addLayout(buttons)

        self.set_idle()

    def set_idle(self) -> None:
        self.what.setText("Not running")
        self.what.setStyleSheet(f"color: {theme.MUTED.name()};")
        self.elapsed.setText("0:00:00")
        self.elapsed.setStyleSheet(f"color: {theme.MUTED.name()};")
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.pause_button.setText("Pause")

    def set_running(self, what: str, seconds: int, paused: bool) -> None:
        colour = theme.PAUSED if paused else self.accent
        self.what.setText(what + ("   (paused)" if paused else ""))
        self.what.setStyleSheet(f"color: {theme.INK.name()};")
        self.elapsed.setText(format_hms(seconds))
        self.elapsed.setStyleSheet(f"color: {colour.name()};")
        self.pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.pause_button.setText("Resume" if paused else "Pause")


class NowStrip(QWidget):
    """The starter controls plus both channel panels."""

    start_work_requested = Signal(int, object)      # project_id, task_id
    start_software_requested = Signal(int, str)     # project_id, package
    start_last_requested = Signal()
    pause_requested = Signal(object)                # EntryKind
    stop_requested = Signal(object)                 # EntryKind

    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo

        self.project = QComboBox()
        self.task = QComboBox()
        self.software = QComboBox()
        self.software.setEditable(True)
        self.software.lineEdit().setPlaceholderText("Package, e.g. PLAXIS 2D")

        self.start_button = QPushButton("Start work")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumHeight(38)
        self.start_software_button = QPushButton("Start software")
        self.start_last_button = QPushButton("Start last task again")

        self.start_button.clicked.connect(self._on_start_work)
        self.start_software_button.clicked.connect(self._on_start_software)
        self.start_last_button.clicked.connect(self.start_last_requested.emit)
        self.project.currentIndexChanged.connect(self._reload_tasks)

        starter = QGridLayout()
        starter.addWidget(QLabel("Project"), 0, 0)
        starter.addWidget(self.project, 0, 1)
        starter.addWidget(QLabel("Task"), 1, 0)
        starter.addWidget(self.task, 1, 1)
        starter.addWidget(self.start_button, 0, 2, 2, 1)
        starter.addWidget(QLabel("Software"), 2, 0)
        starter.addWidget(self.software, 2, 1)
        starter.addWidget(self.start_software_button, 2, 2)
        starter.addWidget(self.start_last_button, 3, 1, 1, 2)
        starter.setColumnStretch(1, 1)

        starter_panel = QFrame()
        starter_panel.setFrameShape(QFrame.Shape.StyledPanel)
        starter_panel.setLayout(starter)

        self.work_panel = ChannelPanel("WORK", theme.WORK)
        self.software_panel = ChannelPanel("SOFTWARE", theme.SOFTWARE)
        self.work_panel.pause_clicked.connect(
            lambda: self.pause_requested.emit(EntryKind.WORK)
        )
        self.work_panel.stop_clicked.connect(
            lambda: self.stop_requested.emit(EntryKind.WORK)
        )
        self.software_panel.pause_clicked.connect(
            lambda: self.pause_requested.emit(EntryKind.SOFTWARE)
        )
        self.software_panel.stop_clicked.connect(
            lambda: self.stop_requested.emit(EntryKind.SOFTWARE)
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(starter_panel, 3)
        layout.addWidget(self.work_panel, 2)
        layout.addWidget(self.software_panel, 2)

    # -- pickers ----------------------------------------------------------

    def reload_pickers(self) -> None:
        current = self.project.currentData()
        self.project.blockSignals(True)
        self.project.clear()
        for row in self.repo.list_projects():
            self.project.addItem(row["name"], row["id"])
        index = self.project.findData(current)
        if index >= 0:
            self.project.setCurrentIndex(index)
        self.project.blockSignals(False)
        self._reload_tasks()

        text = self.software.currentText()
        self.software.blockSignals(True)
        self.software.clear()
        self.software.addItems(self.repo.software_names())
        self.software.setCurrentText(text)
        self.software.blockSignals(False)

        has_projects = self.project.count() > 0
        self.start_button.setEnabled(has_projects)
        self.start_software_button.setEnabled(has_projects)
        self.start_last_button.setEnabled(
            has_projects and self.repo.last_work_entry() is not None
        )

    def _reload_tasks(self) -> None:
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

    def _on_start_work(self) -> None:
        project_id = self.project.currentData()
        if project_id is None:
            return
        self.start_work_requested.emit(project_id, self.task.currentData())

    def _on_start_software(self) -> None:
        project_id = self.project.currentData()
        if project_id is None:
            return
        self.start_software_requested.emit(
            project_id, self.software.currentText().strip()
        )

    # -- display ----------------------------------------------------------

    def update_display(self, timers) -> None:
        """Refresh both panels from the timer service."""
        names = self.repo.project_names()

        work = timers.running(EntryKind.WORK)
        if work is None:
            self.work_panel.set_idle()
            self.start_button.setText("Start work")
        else:
            task_name = ""
            if work.task_id:
                task_row = self.repo.get_task(work.task_id)
                task_name = f" · {task_row['name']}" if task_row else ""
            self.work_panel.set_running(
                names.get(work.project_id, "?") + task_name,
                timers.elapsed_seconds(EntryKind.WORK),
                timers.is_paused(EntryKind.WORK),
            )
            self.start_button.setText("Switch to this")

        software = timers.running(EntryKind.SOFTWARE)
        if software is None:
            self.software_panel.set_idle()
        else:
            self.software_panel.set_running(
                f"{software.software_name} · {names.get(software.project_id, '?')}",
                timers.elapsed_seconds(EntryKind.SOFTWARE),
                timers.is_paused(EntryKind.SOFTWARE),
            )
