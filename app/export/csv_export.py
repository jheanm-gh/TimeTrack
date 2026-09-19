"""The Timesheet sheet, also written as CSV.

A stable column order and plain ISO values, so a future bulk import has
something predictable to read. Deliberately not the workbook's formatting:
CSV carries data, not presentation.
"""

from __future__ import annotations

import csv
import datetime as _dt
from decimal import Decimal
from pathlib import Path

from app.export.gather import TimesheetRow

#: Fixed order. Append new columns at the end; never reorder or remove, or a
#: consumer written against last month's file silently reads the wrong column.
CSV_COLUMNS: list[tuple[str, str]] = [
    ("project", "Project"),
    ("date", "Date"),
    ("day", "Day"),
    ("first_start", "First Start"),
    ("last_end", "Last End"),
    ("sessions", "Sessions"),
    ("hours_raw", "Hours (raw)"),
    ("hours_billed", "Hours (billed)"),
    ("software_raw", "Software Hours (raw)"),
    ("software_billed", "Software Hours (billed)"),
    ("software_used", "Software Used"),
    ("km", "Km"),
    ("description", "Description"),
    ("submitted", "Submitted"),
]


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else ""
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, _dt.time):
        return value.strftime("%H:%M")
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def timesheet_csv_text(rows: list[TimesheetRow]) -> str:
    """Render the rows as CSV text (used by the tests and by the writer)."""
    import io

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([header for _attr, header in CSV_COLUMNS])
    for row in rows:
        writer.writerow([_cell(getattr(row, attr)) for attr, _header in CSV_COLUMNS])
    return buffer.getvalue()


def write_timesheet_csv(path: Path, rows: list[TimesheetRow]) -> Path:
    """Write the CSV beside the workbook. UTF-8 with a BOM so Excel opens it
    with the right encoding when double-clicked."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(timesheet_csv_text(rows), encoding="utf-8-sig")
    return path
