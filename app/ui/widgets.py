"""Small shared widgets: sortable table cells, editing delegates, banners."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QModelIndex, QTime, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QWidget,
)

from app.ui import theme

#: Where a row stashes the database id of the thing it represents.
ID_ROLE = Qt.ItemDataRole.UserRole + 1
#: Where a cell stashes the value to sort by, when that differs from the text.
SORT_ROLE = Qt.ItemDataRole.UserRole + 2


class SortableItem(QTableWidgetItem):
    """A cell that sorts by a stored key rather than by its displayed text.

    Without this, a Date column reading "Mon 14 Sep" sorts alphabetically -
    April before January - and an Hours column sorts "10.00" before "9.00".
    """

    def __init__(self, text: str, sort_key=None, editable: bool = False) -> None:
        super().__init__(text)
        self.setData(SORT_ROLE, sort_key if sort_key is not None else text)
        flags = self.flags()
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        else:
            flags &= ~Qt.ItemFlag.ItemIsEditable
        self.setFlags(flags)

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D105
        mine = self.data(SORT_ROLE)
        theirs = other.data(SORT_ROLE)
        if mine is None or theirs is None:
            return super().__lt__(other)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


def muted(item: QTableWidgetItem) -> QTableWidgetItem:
    item.setForeground(QColor(theme.MUTED))
    return item


def emphasise(item: QTableWidgetItem) -> QTableWidgetItem:
    """Used for the billed-hours column: the number actually typed in."""
    font = item.font()
    font.setBold(True)
    item.setFont(font)
    item.setBackground(QColor(theme.BILLED_BACKGROUND))
    return item


def configure_table(table: QTableWidget, *, sortable: bool = False) -> None:
    """House style for every table in the application."""
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.setSortingEnabled(sortable)
    # Deliberately NOT stretchLastSection: every table nominates its own
    # column to absorb slack (usually Description). Turning both on makes the
    # nominated column collapse to an ellipsis instead.
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setMinimumSectionSize(48)
    table.setWordWrap(False)


class TimeDelegate(QStyledItemDelegate):
    """Edit a cell as a 24-hour clock time."""

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = QTimeEdit(parent)
        editor.setDisplayFormat("HH:mm")
        return editor

    def setEditorData(self, editor, index):  # noqa: N802
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        parsed = QTime.fromString(str(text), "HH:mm")
        editor.setTime(parsed if parsed.isValid() else QTime(9, 0))

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.time().toString("HH:mm"), Qt.ItemDataRole.EditRole)


class ComboDelegate(QStyledItemDelegate):
    """Edit a cell by picking from a list supplied at the moment of editing.

    The options are fetched lazily so a project added five minutes ago shows
    up without the table being rebuilt.
    """

    def __init__(
        self,
        options: Callable[[QModelIndex], list[tuple[str, object]]],
        parent: QWidget | None = None,
        editable: bool = False,
    ) -> None:
        super().__init__(parent)
        self._options = options
        self._editable = editable

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = QComboBox(parent)
        editor.setEditable(self._editable)
        for label, value in self._options(index):
            editor.addItem(label, value)
        return editor

    def setEditorData(self, editor, index):  # noqa: N802
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        position = editor.findText(text)
        if position >= 0:
            editor.setCurrentIndex(position)
        elif editor.isEditable():
            editor.setCurrentText(text)

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)
        model.setData(index, editor.currentData(), ID_ROLE)


class DecimalDelegate(QStyledItemDelegate):
    """Edit a numeric cell without ever letting a float near the value."""

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = QLineEdit(parent)
        editor.setPlaceholderText("0")
        return editor

    def setModelData(self, editor, model, index):  # noqa: N802
        text = editor.text().strip()
        if not text:
            model.setData(index, "", Qt.ItemDataRole.EditRole)
            return
        try:
            value = Decimal(text)
        except InvalidOperation:
            return  # leave the old value alone rather than storing nonsense
        model.setData(index, str(value), Qt.ItemDataRole.EditRole)


class Banner(QWidget):
    """A dismissible strip across the top of the window.

    Used for the catch-up nudge and for overdue timesheets. Deliberately not
    modal: it has to be noticeable without being in the way.
    """

    dismissed = Signal()
    action_clicked = Signal()

    def __init__(
        self,
        text: str,
        action_text: str | None = None,
        parent: QWidget | None = None,
        tone: str = "warning",
    ) -> None:
        super().__init__(parent)
        background = (
            theme.OVERDUE_BACKGROUND if tone == "danger" else theme.BANNER_BACKGROUND
        )
        border = theme.DANGER.name() if tone == "danger" else theme.BANNER_BORDER
        # Scoped by object name: an unscoped "QWidget { ... }" rule would
        # repaint the banner's own buttons with the banner's background and
        # border, which looks like a rendering fault.
        self.setObjectName("TimeTrackBanner")
        self.setStyleSheet(
            f"QWidget#TimeTrackBanner {{ background: {background};"
            f" border: 1px solid {border}; border-radius: 4px; }}"
        )

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("border: none;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.addWidget(self.label, 1)

        if action_text:
            action = QPushButton(action_text)
            action.setCursor(Qt.CursorShape.PointingHandCursor)
            action.clicked.connect(self.action_clicked.emit)
            layout.addWidget(action)

        # U+00D7 rather than a heavier multiplication glyph: it is present in
        # every font the application is likely to meet.
        close = QPushButton("X")
        close.setFixedWidth(30)
        close.setToolTip("Dismiss")
        close.clicked.connect(self._dismiss)
        layout.addWidget(close)

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()

    def set_text(self, text: str) -> None:
        self.label.setText(text)


def day_bounds(day: _dt.date, tz) -> tuple[_dt.datetime, _dt.datetime]:
    """Local midnight to local midnight, as aware datetimes."""
    start = _dt.datetime.combine(day, _dt.time.min, tzinfo=tz)
    return start, start + _dt.timedelta(days=1)
