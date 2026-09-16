"""Colours, fonts and the generated icons.

The icons are drawn with QPainter rather than shipped as image files. That
keeps the repository free of binary assets, renders crisply at any DPI, and
means the tray icon can be regenerated whenever the timer state changes.

The tray icon is the main status display when the window is hidden, so the
four states are distinguished by colour *and* by shape - a colour-blind user
still sees a different picture, not just a different hue.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

# -- palette ---------------------------------------------------------------

IDLE = QColor("#6B7785")      # slate: nothing running
WORK = QColor("#2E9E5B")      # green: work timer running
SOFTWARE = QColor("#2E6DA4")  # blue: software timer running
PAUSED = QColor("#D99A2B")    # amber: running but paused
DANGER = QColor("#C0392B")    # red: overdue
INK = QColor("#1C2430")
MUTED = QColor("#6B7785")
FACE = QColor("#FFFFFF")

#: Row shading for the "this is the number you type in" column.
BILLED_BACKGROUND = "#EAF3EA"
BANNER_BACKGROUND = "#FFF6E5"
BANNER_BORDER = "#E6C67A"
OVERDUE_BACKGROUND = "#FBEAE8"


def elapsed_font(point_size: int = 26) -> QFont:
    """The big running-timer readout.

    Digits are fixed-width so the display does not shuffle sideways every
    time a 1 becomes a 7.
    """
    font = QFont()
    font.setPointSize(point_size)
    font.setBold(True)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    return font


def heading_font(point_size: int = 11) -> QFont:
    font = QFont()
    font.setPointSize(point_size)
    font.setBold(True)
    return font


def small_font(point_size: int = 9) -> QFont:
    font = QFont()
    font.setPointSize(point_size)
    return font


def mono_font() -> QFont:
    font = QFont()
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    return font


# -- icons -----------------------------------------------------------------


def _draw_clock_hands(painter: QPainter, box: QRectF, colour: QColor) -> None:
    pen = QPen(colour)
    pen.setWidthF(max(1.2, box.width() * 0.07))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    centre = box.center()
    # Hour hand pointing up, minute hand pointing right: reads as "a clock"
    # even at 16 pixels.
    painter.drawLine(
        centre.x(), centre.y(), centre.x(), centre.y() - box.height() * 0.26
    )
    painter.drawLine(
        centre.x(), centre.y(), centre.x() + box.width() * 0.20, centre.y()
    )


def state_pixmap(
    size: int = 64, work: bool = False, software: bool = False, paused: bool = False
) -> QPixmap:
    """Draw the status disc for the given combination of running timers."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    inset = size * 0.06
    box = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)

    work_colour = PAUSED if (work and paused) else WORK

    if work and software:
        # Split disc: green for work on the left, blue for software on the
        # right, so "both running" is its own distinct picture.
        left = QPainterPath()
        left.moveTo(box.center())
        left.arcTo(box, 90, 180)
        left.closeSubpath()
        right = QPainterPath()
        right.moveTo(box.center())
        right.arcTo(box, 270, 180)
        right.closeSubpath()
        painter.fillPath(left, QBrush(work_colour))
        painter.fillPath(right, QBrush(SOFTWARE))
    else:
        if work:
            fill = work_colour
        elif software:
            fill = SOFTWARE
        else:
            fill = IDLE
        painter.setBrush(QBrush(fill))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(box)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(FACE, max(1.0, size * 0.035)))
    painter.drawEllipse(box)
    _draw_clock_hands(painter, box, FACE)

    if paused and (work or software):
        # Two white bars in the corner: the universal "paused" mark.
        bar_width = size * 0.08
        bar_height = size * 0.26
        top = size * 0.60
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(FACE))
        painter.drawRect(QRectF(size * 0.60, top, bar_width, bar_height))
        painter.drawRect(QRectF(size * 0.60 + bar_width * 1.8, top, bar_width, bar_height))

    painter.end()
    return pixmap


def state_icon(work: bool = False, software: bool = False, paused: bool = False) -> QIcon:
    """A multi-resolution icon for the tray and the window."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(state_pixmap(size, work=work, software=software, paused=paused))
    return icon


def app_icon() -> QIcon:
    return state_icon()


def state_tooltip(work_text: str | None, software_text: str | None) -> str:
    """Tray tooltip: what is running, and for how long."""
    lines = ["TimeTrack"]
    if work_text:
        lines.append(f"Work: {work_text}")
    if software_text:
        lines.append(f"Software: {software_text}")
    if not work_text and not software_text:
        lines.append("No timer running")
    return "\n".join(lines)


STYLE_SHEET = """
QMainWindow, QDialog { background: #F4F6F8; }
QTabWidget::pane { border: 1px solid #D4DAE0; background: #FFFFFF; }
QTabBar::tab {
    padding: 7px 16px; margin-right: 2px;
    background: #E4E9EE; border: 1px solid #D4DAE0; border-bottom: none;
}
QTabBar::tab:selected { background: #FFFFFF; font-weight: bold; }
QGroupBox {
    border: 1px solid #D4DAE0; border-radius: 4px;
    margin-top: 10px; padding-top: 10px; background: #FFFFFF;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton {
    padding: 6px 14px; border: 1px solid #C2CAD2; border-radius: 4px;
    background: #FFFFFF;
}
QPushButton:hover { background: #EEF3F8; }
QPushButton:disabled { color: #A6AEB7; background: #F0F2F4; }
QPushButton#primary {
    background: #2E9E5B; color: white; border: 1px solid #27834B; font-weight: bold;
}
QPushButton#primary:hover { background: #34B267; }
QPushButton#primary:disabled { background: #B8CFC0; border-color: #A8BFB0; color: #F0F4F0; }
QPushButton#danger { background: #C0392B; color: white; border: 1px solid #A32E22; }
QPushButton#danger:hover { background: #D14336; }
QPushButton#danger:disabled {
    background: #E7D2CF; border-color: #D8C2BF; color: #FBF6F5;
}
QHeaderView::section {
    background: #EDF1F5; padding: 5px; border: none;
    border-right: 1px solid #D4DAE0; border-bottom: 1px solid #D4DAE0;
}
QTableView { gridline-color: #E3E8ED; selection-background-color: #D6E7F5;
             selection-color: #1C2430; }
QStatusBar { background: #E9EDF1; border-top: 1px solid #D4DAE0; }
"""
