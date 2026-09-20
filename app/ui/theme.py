"""Colours, fonts and the generated icons, in a light and a dark palette.

Every colour is a **token** rather than a literal, and the two palettes
define the same tokens. Switching theme rebinds the tokens and rebuilds the
stylesheet, so the whole window changes at once and nothing is left behind
in the old colours.

Contrast is a stated contract, not a matter of taste. :data:`CONTRAST_PAIRS`
lists every text-on-background combination the interface actually produces,
and ``tests/test_theme.py`` measures each one against WCAG AA (4.5:1 for
normal text). The first version of this file failed that badly: disabled
buttons were made to *look* disabled by fading their text towards their own
background, which measured 1.35:1 and was simply unreadable.

The spreadsheet's colours live in :mod:`app.export.styles` and are
deliberately separate - a workbook is printed and shared, so it stays light
whatever the application is set to.
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

LIGHT = "light"
DARK = "dark"
SYSTEM = "system"

#: Both palettes define exactly these tokens.
PALETTES: dict[str, dict[str, str]] = {
    LIGHT: {
        "window": "#F4F6F8",
        "panel": "#FFFFFF",
        "border": "#D4DAE0",
        "grid": "#E3E8ED",
        "text": "#1C2430",
        "muted": "#5A6673",
        "idle": "#5A6673",
        "work": "#1B7A42",
        "software": "#1C5A87",
        "paused": "#8A5E10",
        "danger": "#A8281B",
        "on_accent": "#FFFFFF",
        "band": "#F1F5F9",
        "billed": "#DCEFE1",
        "totals": "#E7EDF3",
        "header": "#2E3B4E",
        "header_text": "#FFFFFF",
        "banner": "#FFF4DE",
        "next_row": "#FCECC0",
        "banner_border": "#B98B1E",
        "overdue": "#FBE4E1",
        "selection": "#CFE2F3",
        "selection_text": "#12181F",
        "button": "#FFFFFF",
        "button_hover": "#E9EFF5",
        "disabled": "#E4E8EC",
        "disabled_text": "#5A636D",
        "tab": "#E1E7ED",
        "input": "#FFFFFF",
    },
    DARK: {
        "window": "#191D22",
        "panel": "#232830",
        "border": "#3A424C",
        "grid": "#333B44",
        "text": "#E7ECF2",
        "muted": "#A7B1BC",
        "idle": "#8B95A1",
        "work": "#4FC77E",
        "software": "#68B2EA",
        "paused": "#E2AC4F",
        "danger": "#F2796F",
        "on_accent": "#10151A",
        "band": "#272D35",
        "billed": "#1F3A2A",
        "totals": "#2A313A",
        "header": "#323C48",
        "header_text": "#EEF2F6",
        "banner": "#38301B",
        "next_row": "#4A3C18",
        "banner_border": "#A98634",
        "overdue": "#3C2523",
        "selection": "#2E4A66",
        "selection_text": "#FFFFFF",
        "button": "#2C323A",
        "button_hover": "#363E48",
        "disabled": "#262B32",
        "disabled_text": "#949EA9",
        "tab": "#1F242A",
        "input": "#1E232A",
    },
}

#: Every text-on-background pair the interface produces, as
#: ``(description, foreground token, background token)``. The test suite
#: measures each one; adding a new coloured element means adding it here.
CONTRAST_PAIRS: list[tuple[str, str, str]] = [
    ("body text on a panel", "text", "panel"),
    ("body text on the window", "text", "window"),
    ("muted note on a panel", "muted", "panel"),
    ("muted note on the window", "muted", "window"),
    ("muted cell on a banded row", "muted", "band"),
    ("table header", "header_text", "header"),
    ("selected row", "selection_text", "selection"),
    ("ordinary button", "text", "button"),
    ("hovered button", "text", "button_hover"),
    ("disabled button", "disabled_text", "disabled"),
    ("start button", "on_accent", "work"),
    ("stop button", "on_accent", "danger"),
    ("running work readout", "work", "panel"),
    ("running software readout", "software", "panel"),
    ("paused readout", "paused", "panel"),
    ("idle readout", "idle", "panel"),
    ("warning inside a banner", "text", "banner"),
    ("the next date to enter", "text", "next_row"),
    ("overdue row", "text", "overdue"),
    ("billed hours cell", "text", "billed"),
    ("totals row", "text", "totals"),
    ("text on a tab", "text", "tab"),
    ("text in an input", "text", "input"),
    ("danger text on a panel", "danger", "panel"),
]

_current_theme = LIGHT


# -- theme selection -------------------------------------------------------


def resolve(name: str | None) -> str:
    """Turn a stored setting into an actual palette name.

    ``system`` asks Qt what the desktop is set to; if it cannot say, light
    is the safer answer - a light interface on a dark desktop is merely
    bright, whereas the reverse can be unreadable in daylight.
    """
    if name == DARK:
        return DARK
    if name == LIGHT:
        return LIGHT
    try:
        from PySide6.QtGui import QGuiApplication

        application = QGuiApplication.instance()
        if application is not None:
            scheme = application.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return DARK
    except (ImportError, AttributeError):
        pass
    return LIGHT


def set_theme(name: str | None) -> str:
    """Switch palette. Returns the palette actually in use."""
    global _current_theme
    _current_theme = resolve(name)
    _rebind_tokens()
    return _current_theme


def current_theme() -> str:
    return _current_theme


def palette() -> dict[str, str]:
    return PALETTES[_current_theme]


def colour(token: str) -> QColor:
    return QColor(palette()[token])


def hex_of(token: str) -> str:
    return palette()[token]


# -- named colours ---------------------------------------------------------
# Rebound whenever the theme changes. Code reads them as theme.WORK rather
# than importing them by name, so the lookup happens after the switch.

IDLE = WORK = SOFTWARE = PAUSED = DANGER = INK = MUTED = FACE = QColor("#000000")
BILLED_BACKGROUND = BANNER_BACKGROUND = BANNER_BORDER = OVERDUE_BACKGROUND = "#000000"


def _rebind_tokens() -> None:
    global IDLE, WORK, SOFTWARE, PAUSED, DANGER, INK, MUTED, FACE
    global BILLED_BACKGROUND, BANNER_BACKGROUND, BANNER_BORDER, OVERDUE_BACKGROUND
    values = palette()
    IDLE = QColor(values["idle"])
    WORK = QColor(values["work"])
    SOFTWARE = QColor(values["software"])
    PAUSED = QColor(values["paused"])
    DANGER = QColor(values["danger"])
    INK = QColor(values["text"])
    MUTED = QColor(values["muted"])
    FACE = QColor(values["on_accent"])
    BILLED_BACKGROUND = values["billed"]
    BANNER_BACKGROUND = values["banner"]
    BANNER_BORDER = values["banner_border"]
    OVERDUE_BACKGROUND = values["overdue"]


_rebind_tokens()


# -- fonts -----------------------------------------------------------------


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


def _draw_clock_hands(painter: QPainter, box: QRectF, colour_: QColor) -> None:
    pen = QPen(colour_)
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
    """Draw the status disc for the given combination of running timers.

    Always drawn against white hands on a saturated disc, whatever the
    application theme: this icon sits in the Windows tray and taskbar, not
    inside the window, so it has to read on either.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    inset = size * 0.06
    box = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)

    tray = PALETTES[LIGHT]
    face = QColor("#FFFFFF")
    work_colour = QColor(tray["paused"] if (work and paused) else tray["work"])

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
        painter.fillPath(right, QBrush(QColor(tray["software"])))
    else:
        if work:
            fill = work_colour
        elif software:
            fill = QColor(tray["software"])
        else:
            fill = QColor(tray["idle"])
        painter.setBrush(QBrush(fill))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(box)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(face, max(1.0, size * 0.035)))
    painter.drawEllipse(box)
    _draw_clock_hands(painter, box, face)

    if paused and (work or software):
        # Two white bars in the corner: the universal "paused" mark.
        bar_width = size * 0.08
        bar_height = size * 0.26
        top = size * 0.60
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(face))
        painter.drawRect(QRectF(size * 0.60, top, bar_width, bar_height))
        painter.drawRect(
            QRectF(size * 0.60 + bar_width * 1.8, top, bar_width, bar_height)
        )

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


# -- stylesheet ------------------------------------------------------------


def stylesheet() -> str:
    """Build the whole application stylesheet from the current palette.

    Labels are styled through the ``role`` property rather than inline, so
    one stylesheet swap re-themes every note and hint in the window. An
    inline style would survive the swap and leave grey-on-grey text behind.
    """
    c = palette()
    return f"""
QMainWindow, QDialog {{ background: {c['window']}; color: {c['text']}; }}
QWidget {{ color: {c['text']}; }}

QTabWidget::pane {{ border: 1px solid {c['border']}; background: {c['panel']}; }}
QTabBar::tab {{
    padding: 7px 16px; margin-right: 2px; color: {c['text']};
    background: {c['tab']}; border: 1px solid {c['border']}; border-bottom: none;
}}
QTabBar::tab:selected {{ background: {c['panel']}; font-weight: bold; }}

QGroupBox {{
    border: 1px solid {c['border']}; border-radius: 4px;
    margin-top: 10px; padding-top: 10px; background: {c['panel']};
    color: {c['text']};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}

QFrame {{ background: {c['panel']}; }}
QScrollArea {{ background: {c['window']}; }}

QPushButton {{
    padding: 6px 14px; border: 1px solid {c['border']}; border-radius: 4px;
    background: {c['button']}; color: {c['text']};
}}
QPushButton:hover {{ background: {c['button_hover']}; }}
QPushButton:disabled {{ color: {c['disabled_text']}; background: {c['disabled']};
                        border-color: {c['border']}; }}
QPushButton#primary {{
    background: {c['work']}; color: {c['on_accent']};
    border: 1px solid {c['work']}; font-weight: bold;
}}
QPushButton#primary:hover {{ background: {c['work']}; }}
QPushButton#primary:disabled {{ background: {c['disabled']};
                                color: {c['disabled_text']};
                                border-color: {c['border']}; }}
QPushButton#danger {{ background: {c['danger']}; color: {c['on_accent']};
                      border: 1px solid {c['danger']}; }}
QPushButton#danger:hover {{ background: {c['danger']}; }}
QPushButton#danger:disabled {{ background: {c['disabled']};
                               color: {c['disabled_text']};
                               border-color: {c['border']}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDateEdit, QTimeEdit {{
    background: {c['input']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 3px; padding: 3px 5px;
}}
QComboBox QAbstractItemView {{
    background: {c['panel']}; color: {c['text']};
    selection-background-color: {c['selection']};
    selection-color: {c['selection_text']};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {c['disabled']}; color: {c['disabled_text']};
}}

QHeaderView::section {{
    background: {c['header']}; color: {c['header_text']}; padding: 5px;
    border: none; border-right: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
}}
QTableWidget, QTableView, QListWidget {{
    background: {c['panel']}; color: {c['text']};
    gridline-color: {c['grid']};
    selection-background-color: {c['selection']};
    selection-color: {c['selection_text']};
    alternate-background-color: {c['band']};
}}
QTableCornerButton::section {{ background: {c['header']}; border: none; }}

QStatusBar {{ background: {c['tab']}; color: {c['text']};
              border-top: 1px solid {c['border']}; }}
QStatusBar QLabel {{ color: {c['text']}; }}
QMenu {{ background: {c['panel']}; color: {c['text']};
         border: 1px solid {c['border']}; }}
QMenu::item:selected {{ background: {c['selection']}; color: {c['selection_text']}; }}
QToolTip {{ background: {c['panel']}; color: {c['text']};
            border: 1px solid {c['border']}; }}
QCheckBox, QRadioButton {{ color: {c['text']}; }}
QSplitter::handle {{ background: {c['border']}; }}

/* Roles, so a theme switch repaints every hint and note in the window. */
*[role="muted"] {{ color: {c['muted']}; }}
*[role="danger"] {{ color: {c['danger']}; font-weight: bold; }}
*[role="ok"] {{ color: {c['work']}; }}
*[role="warning"] {{ color: {c['paused']}; font-weight: bold; }}

QWidget#TimeTrackBanner {{
    background: {c['banner']}; border: 1px solid {c['banner_border']};
    border-radius: 4px;
}}
QWidget#TimeTrackBanner[tone="danger"] {{
    background: {c['overdue']}; border: 1px solid {c['danger']};
}}
QWidget#TimeTrackBanner QLabel {{ background: transparent; border: none;
                                  color: {c['text']}; }}
"""


#: Kept for callers that referenced the old constant.
STYLE_SHEET = stylesheet()
