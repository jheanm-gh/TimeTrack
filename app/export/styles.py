"""Shared spreadsheet formatting.

Two rules from the brief shape everything here.

**Real values, never text.** Dates are Excel dates, hours are numbers with a
display format. A column of numbers stored as text cannot be summed, sorted
or filtered, and looks fine until the moment it matters.

**No merged cells in a data region.** Merged cells break filtering, sorting
and copying, and this workbook exists to be filtered and copied from.
"""

from __future__ import annotations

from dataclasses import dataclass

from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import (
    Alignment,
    Border,
    Font,
    NamedStyle,
    PatternFill,
    Side,
)
from openpyxl.utils import get_column_letter

# -- number formats --------------------------------------------------------

FMT_HOURS = "0.00"
FMT_KM = "0.0"
FMT_DATE = "dd mmm yyyy"
FMT_TIME = "hh:mm"
FMT_ODOMETER = "#,##0"

# -- colours ---------------------------------------------------------------

HEADER_BACKGROUND = "2E3B4E"
HEADER_TEXT = "FFFFFF"
BAND_BACKGROUND = "F4F7FA"
BILLED_BACKGROUND = "E3F2E6"
TOTALS_BACKGROUND = "EDF1F5"
SUBMITTED_TEXT = "9AA4B0"
SUBMITTED_BACKGROUND = "EFEFEF"
GRID_LINE = "D4DAE0"

HEADER_FONT = Font(bold=True, color=HEADER_TEXT, size=11)
HEADER_FILL = PatternFill("solid", fgColor=HEADER_BACKGROUND)
BAND_FILL = PatternFill("solid", fgColor=BAND_BACKGROUND)
BILLED_FILL = PatternFill("solid", fgColor=BILLED_BACKGROUND)
TOTALS_FILL = PatternFill("solid", fgColor=TOTALS_BACKGROUND)

BODY_FONT = Font(size=11)
BILLED_FONT = Font(size=11, bold=True)
TOTALS_FONT = Font(size=11, bold=True)
TITLE_FONT = Font(size=12, bold=True)
NOTE_FONT = Font(size=10, italic=True, color="6B7785")

THIN = Side(style="thin", color=GRID_LINE)
CELL_BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

LEFT = Alignment(horizontal="left", vertical="center")
RIGHT = Alignment(horizontal="right", vertical="center")
CENTRE = Alignment(horizontal="center", vertical="center")
WRAP_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=False)


@dataclass(frozen=True, slots=True)
class Column:
    """One column of a tabular sheet."""

    header: str
    attr: str
    width: float
    number_format: str | None = None
    align: str = "left"
    #: The "this is the number you type into the intranet" treatment:
    #: bold, shaded and wider, so the eye lands on it.
    emphasis: bool = False

    @property
    def alignment(self) -> Alignment:
        return {"left": LEFT, "right": RIGHT, "centre": CENTRE}[self.align]


def write_header(ws, columns: list[Column], row: int = 1) -> None:
    for index, column in enumerate(columns, start=1):
        cell = ws.cell(row=row, column=index, value=column.header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTRE
        cell.border = CELL_BORDER
        ws.column_dimensions[get_column_letter(index)].width = column.width
    ws.row_dimensions[row].height = 26


class StyleRegistry:
    """Named styles, created once and reused for every cell that needs them.

    This is a performance fix with a real user consequence. Assigning font,
    alignment, border and fill separately makes openpyxl hash and de-duplicate
    a style object per attribute per cell; across a few years of entries that
    is close to a million hashes, and the save took nine seconds - long enough
    to freeze the window on every autosave. One named style per distinct
    combination turns that into a single lookup per cell.

    Data cells carry no border: the worksheet's own gridlines already
    separate them, and a border on every cell is both slower and busier.
    """

    def __init__(self, workbook) -> None:
        self._workbook = workbook
        self._names: dict[tuple, str] = {}

    def name_for(self, column: "Column", banded: bool) -> str:
        key = (column.number_format or "General", column.align, column.emphasis, banded)
        existing = self._names.get(key)
        if existing is not None:
            return existing

        name = f"tt{len(self._names)}"
        style = NamedStyle(name=name)
        style.font = BILLED_FONT if column.emphasis else BODY_FONT
        style.alignment = column.alignment
        style.number_format = column.number_format or "General"
        if column.emphasis:
            style.fill = BILLED_FILL
        elif banded:
            style.fill = BAND_FILL
        self._workbook.add_named_style(style)
        self._names[key] = name
        return name


def write_rows(
    ws,
    columns: list[Column],
    rows: list,
    first_row: int = 2,
    registry: "StyleRegistry | None" = None,
) -> int:
    """Write the body of a table. Returns the last row written.

    Rows are banded as they are written rather than through conditional
    formatting, so the shading survives being copied elsewhere.
    """
    registry = registry or StyleRegistry(ws.parent)
    # Resolve each column's two possible styles once, not once per cell.
    style_names = [
        (registry.name_for(column, False), registry.name_for(column, True))
        for column in columns
    ]

    for offset, record in enumerate(rows):
        excel_row = first_row + offset
        banded = offset % 2 == 1
        for index, column in enumerate(columns, start=1):
            value = getattr(record, column.attr, None)
            if isinstance(value, bool):
                value = "Yes" if value else ""
            cell = ws.cell(row=excel_row, column=index, value=value)
            cell.style = style_names[index - 1][1 if banded else 0]
    return first_row + len(rows) - 1 if rows else first_row - 1


def finish_table(
    ws, columns: list[Column], header_row: int, last_row: int, freeze: bool = True
) -> None:
    """Autofilter and freeze the header of a finished table."""
    last_letter = get_column_letter(len(columns))
    # The filter covers the header even when there are no data rows, so the
    # dropdowns are there the moment the first entry appears.
    ws.auto_filter.ref = f"A{header_row}:{last_letter}{max(last_row, header_row)}"
    if freeze:
        ws.freeze_panes = f"A{header_row + 1}"


def grey_out_when(
    ws, columns: list[Column], flag_attr: str, header_row: int, last_row: int
) -> None:
    """Grey a whole row when its flag column says "Yes".

    Used for dates already ticked off as entered on the intranet: they stay
    visible and filterable, but the eye skips straight past them to the work
    still to do.
    """
    if last_row < header_row + 1:
        return
    flag_index = next(
        (index for index, column in enumerate(columns, start=1) if column.attr == flag_attr),
        None,
    )
    if flag_index is None:
        return
    flag_letter = get_column_letter(flag_index)
    last_letter = get_column_letter(len(columns))
    first_data_row = header_row + 1
    ws.conditional_formatting.add(
        f"A{first_data_row}:{last_letter}{last_row}",
        FormulaRule(
            formula=[f'${flag_letter}{first_data_row}="Yes"'],
            font=Font(color=SUBMITTED_TEXT, italic=True, size=11),
            fill=PatternFill("solid", bgColor=SUBMITTED_BACKGROUND),
            stopIfTrue=False,
        ),
    )


def write_title(ws, text: str, row: int = 1, note: str | None = None) -> int:
    """A heading above a block. Never merged - merging breaks copying."""
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = TITLE_FONT
    if note:
        note_cell = ws.cell(row=row + 1, column=1, value=note)
        note_cell.font = NOTE_FONT
        return row + 2
    return row + 1


def write_totals_block(
    ws, first_row: int, title: str, pairs: list[tuple[str, object]], number_format: str
) -> int:
    """A small two-column labelled block, e.g. kilometres per month.

    Placed below the main table with a gap, so the autofilter above cannot
    hide it.
    """
    cell = ws.cell(row=first_row, column=1, value=title)
    cell.font = TITLE_FONT
    row = first_row + 1
    for label, value in pairs:
        label_cell = ws.cell(row=row, column=1, value=label)
        label_cell.font = TOTALS_FONT
        label_cell.fill = TOTALS_FILL
        label_cell.border = CELL_BORDER
        value_cell = ws.cell(row=row, column=2, value=value)
        value_cell.font = TOTALS_FONT
        value_cell.fill = TOTALS_FILL
        value_cell.number_format = number_format
        value_cell.alignment = RIGHT
        value_cell.border = CELL_BORDER
        row += 1
    return row


#: Characters Excel refuses in a sheet name.
_FORBIDDEN = set(r"[]:*?/\\")


def sanitise_sheet_name(name: str, prefix: str = "", used: set[str] | None = None) -> str:
    """Make a safe, unique sheet name (Excel allows 31 characters).

    Project names are long and contain punctuation Excel rejects, so they are
    cleaned and truncated; a numeric suffix breaks any tie left over.
    """
    cleaned = "".join(" " if character in _FORBIDDEN else character for character in name)
    cleaned = " ".join(cleaned.split()).strip("'")
    candidate = f"{prefix}{cleaned}"[:31].strip()
    if not candidate:
        candidate = "Sheet"

    if used is None:
        return candidate
    if candidate not in used:
        used.add(candidate)
        return candidate
    for suffix in range(2, 100):
        tail = f" ({suffix})"
        trimmed = f"{candidate[: 31 - len(tail)]}{tail}"
        if trimmed not in used:
            used.add(trimmed)
            return trimmed
    used.add(candidate)
    return candidate
