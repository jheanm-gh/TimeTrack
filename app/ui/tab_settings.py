"""The Settings tab.

A fifth tab rather than a separate dialog: the brief lists four tabs but then
refers to Settings repeatedly - the workbook folder picker, the backups
panel, the run-on-startup toggle, and the switch that reverses a
"don't ask again". Those need somewhere permanent to live, and a tab is
easier to find again than a menu item.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app import paths
from app.core.models import RoundingDirection
from app.db.repository import Repository
from app.services import idle as idle_module
from app.services import startup as startup_module
from app.services.backup import SETTING_WORKBOOK_LAST, describe_backups
from app.ui import theme
from app.ui.widgets import set_role
from app.ui.dialogs import warn


class SettingsTab(QWidget):
    """Everything configurable, grouped the way it is thought about."""

    settings_changed = Signal()

    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self._loading = False

        # Inside a scroll area: this page has more on it than fits a 680-pixel
        # window, and without one the bottom group gets squeezed until its
        # fields overlap.
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.addWidget(self._appearance_group())
        inner_layout.addWidget(self._timezone_group())
        inner_layout.addWidget(self._rounding_group())
        inner_layout.addWidget(self._idle_group())
        inner_layout.addWidget(self._windows_group())
        inner_layout.addWidget(self._workbook_group())
        inner_layout.addWidget(self._files_group())
        inner_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        self.reload()

    # -- groups -----------------------------------------------------------

    def _appearance_group(self) -> QGroupBox:
        """Light or dark, or follow the desktop."""
        group = QGroupBox("Appearance")
        self.theme = QComboBox()
        self.theme.addItem("Match this computer", theme.SYSTEM)
        self.theme.addItem("Light", theme.LIGHT)
        self.theme.addItem("Dark", theme.DARK)

        note = QLabel(
            "Changes straight away - there is no need to restart. The "
            "spreadsheet is always produced in light colours, because it is "
            "printed and shared."
        )
        note.setWordWrap(True)
        set_role(note, "muted")

        form = QFormLayout(group)
        form.addRow("Theme", self.theme)
        form.addRow("", note)

        self.theme.currentIndexChanged.connect(self._save_theme)
        return group

    def _timezone_group(self) -> QGroupBox:
        """Which clock the dates and times are shown against.

        The setting has always existed; this is the control for it. It
        matters because the default - "use this computer's setting" - gives
        Windows' current offset, which is correct all year in South Africa
        but would drift in a country that changes its clocks. Naming a zone
        explicitly uses the real time zone database instead.
        """
        group = QGroupBox("Dates and times")
        self.timezone = QComboBox()
        self.timezone.addItem("Use this computer's setting", "system")

        import zoneinfo

        for name in sorted(zoneinfo.available_timezones()):
            self.timezone.addItem(name, name)

        note = QLabel(
            "Days start and end according to this clock, which decides which "
            "date a late-night session belongs to."
        )
        note.setWordWrap(True)
        set_role(note, "muted")

        self.timezone_status = QLabel()
        self.timezone_status.setWordWrap(True)
        set_role(self.timezone_status, "muted")

        form = QFormLayout(group)
        form.addRow("Time zone", self.timezone)
        form.addRow("", note)
        form.addRow("", self.timezone_status)

        self.timezone.currentIndexChanged.connect(self._save_timezone)
        return group

    def _rounding_group(self) -> QGroupBox:
        group = QGroupBox("How hours are rounded")
        self.increment = QComboBox()
        for label, value in (
            ("Quarter hour (0.25)", "0.25"),
            ("Tenth of an hour (0.1)", "0.1"),
            ("Half hour (0.5)", "0.5"),
            ("No rounding", "0"),
        ):
            self.increment.addItem(label, value)

        self.direction = QComboBox()
        for label, value in (
            ("Always up", str(RoundingDirection.UP)),
            ("To the nearest", str(RoundingDirection.NEAREST)),
            ("Always down", str(RoundingDirection.DOWN)),
        ):
            self.direction.addItem(label, value)

        self.round_software = QCheckBox("Round software hours the same way")

        explanation = QLabel(
            "Hours are added up for one project on one day, and that daily "
            "total is rounded once. Individual entries are never rounded, so "
            "six ten-minute calls bill one hour, not one and a half."
        )
        explanation.setWordWrap(True)
        set_role(explanation, "muted")

        form = QFormLayout(group)
        form.addRow("Round to", self.increment)
        form.addRow("Direction", self.direction)
        form.addRow("", self.round_software)
        form.addRow("", explanation)

        self.increment.currentIndexChanged.connect(self._save_rounding)
        self.direction.currentIndexChanged.connect(self._save_rounding)
        self.round_software.toggled.connect(self._save_rounding)
        return group

    def _idle_group(self) -> QGroupBox:
        group = QGroupBox("When you step away from the desk")
        self.idle_enabled = QCheckBox("Ask me about time when I have been away")
        self.idle_threshold = QSpinBox()
        self.idle_threshold.setRange(1, 240)
        self.idle_threshold.setSuffix(" minutes")

        note = QLabel(
            "Only the work timer is watched. A software timer is never "
            "questioned and never stopped, because an analysis can legitimately "
            "run all night unattended.\n" + idle_module.describe()
        )
        note.setWordWrap(True)
        set_role(note, "muted")

        form = QFormLayout(group)
        form.addRow("", self.idle_enabled)
        form.addRow("Ask after", self.idle_threshold)
        form.addRow("", note)

        self.idle_enabled.toggled.connect(self._save_idle)
        self.idle_threshold.valueChanged.connect(self._save_idle)
        return group

    def _windows_group(self) -> QGroupBox:
        group = QGroupBox("Starting and closing")
        self.run_on_startup = QCheckBox("Start TimeTrack when I log in")
        self.open_minimised = QCheckBox("Start hidden in the tray")
        self.confirm_close = QCheckBox("Ask what to do when I click the X")

        self.startup_status = QLabel()
        self.startup_status.setWordWrap(True)
        set_role(self.startup_status, "muted")

        form = QFormLayout(group)
        form.addRow("", self.run_on_startup)
        form.addRow("", self.open_minimised)
        form.addRow("", self.confirm_close)
        form.addRow("", self.startup_status)

        self.run_on_startup.toggled.connect(self._save_startup)
        self.open_minimised.toggled.connect(self._save_startup_options)
        self.confirm_close.toggled.connect(self._save_startup_options)
        return group

    def _workbook_group(self) -> QGroupBox:
        group = QGroupBox("The spreadsheet")
        self.autosave_enabled = QCheckBox("Keep the spreadsheet up to date by itself")
        self.autosave_minutes = QSpinBox()
        self.autosave_minutes.setRange(1, 720)
        self.autosave_minutes.setSuffix(" minutes")
        self.per_project_sheets = QCheckBox("Also make one tab per project")

        note = QLabel(
            "The spreadsheet is also rewritten a couple of minutes after you "
            "change anything, and again when you close TimeTrack. If you have "
            "it open in Excel, TimeTrack waits and tries again rather than "
            "leaving copies lying about."
        )
        note.setWordWrap(True)
        set_role(note, "muted")

        form = QFormLayout(group)
        form.addRow("", self.autosave_enabled)
        form.addRow("Save every", self.autosave_minutes)
        form.addRow("", self.per_project_sheets)
        form.addRow("", note)

        self.autosave_enabled.toggled.connect(self._save_workbook_settings)
        self.autosave_minutes.valueChanged.connect(self._save_workbook_settings)
        self.per_project_sheets.toggled.connect(self._save_workbook_settings)
        return group

    def _files_group(self) -> QGroupBox:
        group = QGroupBox("Where your files are")
        self.workbook_path = QLineEdit()
        self.workbook_path.setReadOnly(True)
        browse = QPushButton("Choose folder...")
        browse.clicked.connect(self._choose_workbook_folder)

        row = QHBoxLayout()
        row.addWidget(self.workbook_path, 1)
        row.addWidget(browse)
        row_widget = QWidget()
        row_widget.setLayout(row)

        self.locations = QLabel()
        self.locations.setWordWrap(True)
        self.locations.setTextInteractionFlags(
            self.locations.textInteractionFlags()
            | self.locations.textInteractionFlags().TextSelectableByMouse
        )

        self.backups_label = QLabel()
        self.backups_label.setWordWrap(True)
        set_role(self.backups_label, "muted")

        open_backups = QPushButton("Open the backups folder")
        open_backups.clicked.connect(self._open_backups)

        form = QFormLayout(group)
        form.addRow("Spreadsheet folder", row_widget)
        form.addRow("Locations", self.locations)
        form.addRow("Backups", self.backups_label)
        form.addRow("", open_backups)
        return group

    # -- loading and saving ----------------------------------------------

    def reload(self) -> None:
        self._loading = True
        try:
            increment = self.repo.get_setting("rounding.increment", "0.25")
            index = self.increment.findData(str(Decimal(increment).normalize()))
            if index < 0:
                index = self.increment.findData(increment)
            self.increment.setCurrentIndex(max(0, index))

            index = self.direction.findData(self.repo.get_setting("rounding.direction"))
            self.direction.setCurrentIndex(max(0, index))
            self.round_software.setChecked(
                self.repo.get_bool("rounding.apply_to_software", True)
            )

            index = self.theme.findData(
                self.repo.get_setting("display.theme") or theme.SYSTEM
            )
            self.theme.setCurrentIndex(max(0, index))

            configured_zone = self.repo.get_setting("display.timezone") or "system"
            index = self.timezone.findData(configured_zone)
            self.timezone.setCurrentIndex(max(0, index))
            self._describe_timezone()

            self.idle_enabled.setChecked(self.repo.get_bool("idle.enabled", True))
            self.idle_threshold.setValue(
                self.repo.get_int("idle.threshold_minutes", 10)
            )

            self.run_on_startup.blockSignals(True)
            self.run_on_startup.setChecked(startup_module.is_enabled())
            self.run_on_startup.blockSignals(False)
            self.open_minimised.setChecked(
                self.repo.get_bool("startup.open_minimised", False)
            )
            self.confirm_close.setChecked(self.repo.get_bool("close.confirm", True))
            self.run_on_startup.setEnabled(startup_module.is_supported())
            if not startup_module.is_supported():
                self.startup_status.setText(
                    "Starting with Windows is only available on Windows."
                )

            self.autosave_enabled.setChecked(
                self.repo.get_bool("workbook.autosave_enabled", True)
            )
            self.autosave_minutes.setValue(
                self.repo.get_int("workbook.autosave_minutes", 30)
            )
            self.per_project_sheets.setChecked(
                self.repo.get_bool("workbook.per_project_sheets", True)
            )

            configured = self.repo.get_setting("workbook.path") or ""
            self.workbook_path.setText(configured or str(paths.default_workbook_dir()))
            self._refresh_locations()
        finally:
            self._loading = False

    def _refresh_locations(self) -> None:
        lines = [f"{name}: {value}" for name, value in paths.describe_locations().items()]
        self.locations.setText("\n".join(lines))

        counts = describe_backups(paths.backup_dir(), paths.db_backup_dir())

        def describe(count: int, noun: str, newest: str | None) -> str:
            plural = "" if count == 1 else "s"
            tail = f", newest {newest}" if newest else ""
            return f"{count} {noun} backup{plural}{tail}"

        lines = [
            describe(
                counts["workbook_count"], "weekly spreadsheet", counts["workbook_latest"]
            ),
            describe(
                counts["database_count"], "daily database", counts["database_latest"]
            ),
        ]
        last_workbook = self.repo.get_setting(SETTING_WORKBOOK_LAST)
        if last_workbook:
            lines.append(f"Last weekly backup taken on {last_workbook}.")
        self.backups_label.setText("\n".join(lines))

    def _save_theme(self) -> None:
        if self._loading:
            return
        self.repo.set_setting("display.theme", self.theme.currentData())
        self.settings_changed.emit()

    def _describe_timezone(self) -> None:
        """Show what the chosen setting actually resolves to right now."""
        import datetime as _dt

        zone = self.repo.timezone()
        now = _dt.datetime.now(tz=zone)
        offset = now.utcoffset() or _dt.timedelta()
        hours, remainder = divmod(int(offset.total_seconds()), 3600)
        sign = "+" if hours >= 0 else "-"
        self.timezone_status.setText(
            f"Right now that is {now:%H:%M} "
            f"(UTC{sign}{abs(hours):02d}:{abs(remainder) // 60:02d})."
        )

    def _save_timezone(self) -> None:
        if self._loading:
            return
        self.repo.set_setting("display.timezone", self.timezone.currentData())
        self._describe_timezone()
        self.settings_changed.emit()

    def _save_rounding(self) -> None:
        if self._loading:
            return
        self.repo.set_setting("rounding.increment", self.increment.currentData())
        self.repo.set_setting("rounding.direction", self.direction.currentData())
        self.repo.set_setting(
            "rounding.apply_to_software", "1" if self.round_software.isChecked() else "0"
        )
        self.settings_changed.emit()

    def _save_idle(self) -> None:
        if self._loading:
            return
        self.repo.set_setting("idle.enabled", "1" if self.idle_enabled.isChecked() else "0")
        self.repo.set_setting("idle.threshold_minutes", str(self.idle_threshold.value()))
        self.settings_changed.emit()

    def _save_workbook_settings(self) -> None:
        if self._loading:
            return
        self.repo.set_setting(
            "workbook.autosave_enabled", "1" if self.autosave_enabled.isChecked() else "0"
        )
        self.repo.set_setting(
            "workbook.autosave_minutes", str(self.autosave_minutes.value())
        )
        self.repo.set_setting(
            "workbook.per_project_sheets",
            "1" if self.per_project_sheets.isChecked() else "0",
        )
        self.settings_changed.emit()

    def _save_startup_options(self) -> None:
        if self._loading:
            return
        self.repo.set_setting(
            "startup.open_minimised", "1" if self.open_minimised.isChecked() else "0"
        )
        self.repo.set_setting(
            "close.confirm", "1" if self.confirm_close.isChecked() else "0"
        )
        self.settings_changed.emit()

    def _save_startup(self) -> None:
        if self._loading:
            return
        wanted = self.run_on_startup.isChecked()
        worked, message = startup_module.set_enabled(
            wanted, open_minimised=self.open_minimised.isChecked()
        )
        self.startup_status.setText(message)
        if not worked:
            self.run_on_startup.blockSignals(True)
            self.run_on_startup.setChecked(startup_module.is_enabled())
            self.run_on_startup.blockSignals(False)
        self.repo.set_setting("startup.run_on_login", "1" if worked and wanted else "0")

    def _choose_workbook_folder(self) -> None:
        current = self.workbook_path.text() or str(paths.default_workbook_dir())
        chosen = QFileDialog.getExistingDirectory(
            self, "Where should the spreadsheet be saved?", current
        )
        if not chosen:
            return
        self.repo.set_setting("workbook.path", chosen)
        self.workbook_path.setText(chosen)
        self._refresh_locations()
        self.settings_changed.emit()

    def _open_backups(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        target = paths.backup_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warn(self, f"Could not open the backups folder: {exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
