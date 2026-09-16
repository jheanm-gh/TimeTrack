"""The system tray icon.

When the window is hidden this is the entire user interface, so the icon
carries the state (idle / work / software / both / paused) and the tooltip
carries the detail.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from app.ui import theme


class Tray(QObject):
    """Wraps QSystemTrayIcon with the menu the brief asks for."""

    open_requested = Signal()
    start_last_requested = Signal()
    stop_all_requested = Signal()
    save_workbook_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon = QSystemTrayIcon(theme.state_icon(), parent)

        menu = QMenu(parent)
        self.start_last_action = QAction("Start last task", menu)
        self.stop_all_action = QAction("Stop all timers", menu)
        open_action = QAction("Open TimeTrack", menu)
        save_action = QAction("Save workbook now", menu)
        quit_action = QAction("Quit", menu)

        self.start_last_action.triggered.connect(self.start_last_requested.emit)
        self.stop_all_action.triggered.connect(self.stop_all_requested.emit)
        open_action.triggered.connect(self.open_requested.emit)
        save_action.triggered.connect(self.save_workbook_requested.emit)
        quit_action.triggered.connect(self.quit_requested.emit)

        menu.addAction(self.start_last_action)
        menu.addAction(self.stop_all_action)
        menu.addSeparator()
        menu.addAction(open_action)
        menu.addAction(save_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_activated)
        self.menu = menu

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_requested.emit()

    def show(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def update_state(
        self,
        work: bool,
        software: bool,
        paused: bool,
        work_text: str | None,
        software_text: str | None,
    ) -> None:
        self.icon.setIcon(theme.state_icon(work=work, software=software, paused=paused))
        self.icon.setToolTip(theme.state_tooltip(work_text, software_text))
        self.stop_all_action.setEnabled(work or software)

    def notify(self, title: str, message: str) -> None:
        if QSystemTrayIcon.supportsMessages():
            self.icon.showMessage(title, message, theme.app_icon(), 5000)
