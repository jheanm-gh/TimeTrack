"""Writing the workbook to disk safely.

Two hazards, both of which will happen regularly near month end.

**A crash part-way through a write** would leave a truncated, unopenable
workbook where the good one used to be. Every save is therefore written to a
temporary file in the same folder and then *atomically renamed* over the
target, so the file on disk is always either the old version or the new one.

**Excel holding the file open** is the normal state of affairs when the user
is working from it. Windows refuses to replace a file Excel has open, so:

* an *autosave* gives up quietly and says so in the status bar. It does not
  scatter timestamped copies through the folder - that is how an export
  folder becomes unusable;
* a *manual* Save Now writes a timestamped file instead and says plainly
  what it did. It never fails silently.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook

from app import paths
from app.core.timeutil import utc_now
from app.db.repository import Repository
from app.export.csv_export import write_timesheet_csv
from app.export.gather import WorkbookData
from app.export.workbook import build_from_repository


@dataclass(frozen=True, slots=True)
class SaveOutcome:
    """What happened, in terms the status bar and a dialog can both use."""

    saved: bool
    path: Path | None = None
    locked: bool = False
    fell_back: bool = False
    message: str = ""
    csv_path: Path | None = None
    at: _dt.datetime | None = None
    manual: bool = False

    @property
    def needs_telling(self) -> bool:
        """Should this interrupt the user?

        A background save that skipped because Excel has the file open is
        exactly what the brief asks to be quiet about - it belongs in the
        status bar and nowhere else. A save the user actually asked for, or
        any genuine failure, must be said out loud.
        """
        if self.fell_back:
            return True
        if self.saved:
            return False
        return self.manual or not self.locked


def workbook_dir(repo: Repository) -> Path:
    configured = (repo.get_setting("workbook.path") or "").strip()
    return Path(configured) if configured else paths.default_workbook_dir()


def workbook_path(repo: Repository) -> Path:
    return workbook_dir(repo) / paths.WORKBOOK_FILENAME


def csv_path(repo: Repository) -> Path:
    return workbook_dir(repo) / paths.CSV_FILENAME


def timestamped_name(target: Path, when: _dt.datetime) -> Path:
    """``TimeLog.xlsx`` -> ``TimeLog_2026-09-14_1432.xlsx``."""
    return target.with_name(f"{target.stem}_{when:%Y-%m-%d_%H%M}{target.suffix}")


def _is_locked_error(exc: OSError) -> bool:
    """Is this "the file is open in another program" rather than a real fault?

    Windows reports a sharing violation as errno EACCES (13) or as winerror
    5 / 32. Anything else - a missing folder, a full disk - is a genuine
    problem and must not be mistaken for Excel being open.
    """
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) in (5, 32)


def atomic_save(workbook: Workbook, target: Path) -> tuple[bool, bool, str]:
    """Write ``workbook`` over ``target``. Returns ``(saved, locked, message)``.

    The temporary file is created in the destination folder, because an
    atomic rename cannot cross a filesystem boundary.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, False, f"Could not create the folder {target.parent}: {exc}"

    temporary = target.with_name(f".{target.stem}.{os.getpid()}.tmp{target.suffix}")
    try:
        workbook.save(temporary)
    except OSError as exc:
        _discard(temporary)
        return False, False, f"Could not write the spreadsheet: {exc}"

    try:
        os.replace(temporary, target)
    except OSError as exc:
        _discard(temporary)
        if _is_locked_error(exc):
            return False, True, f"{target.name} is open in Excel."
        return False, False, f"Could not replace {target.name}: {exc}"
    return True, False, ""


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # A leftover temporary file is untidy but harmless; never let
        # cleanup failure mask the error we are already reporting.
        pass


def save_workbook(
    repo: Repository,
    manual: bool = False,
    today: _dt.date | None = None,
) -> SaveOutcome:
    """Rebuild the workbook from the database and write it out.

    ``manual`` is the Save Now path: it falls back to a timestamped file when
    the target is locked, and always has something to report.
    """
    now = utc_now().astimezone(repo.timezone())
    target = workbook_path(repo)

    try:
        workbook, data = build_from_repository(repo, today=today)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return SaveOutcome(
            saved=False,
            message=f"The spreadsheet could not be built: {exc}",
            at=now,
            manual=manual,
        )

    saved, locked, message = atomic_save(workbook, target)

    if saved:
        written_csv = _write_csv(repo, data)
        return SaveOutcome(
            saved=True,
            path=target,
            message=f"Last saved {now:%H:%M}",
            csv_path=written_csv,
            at=now,
            manual=manual,
        )

    if locked and manual:
        fallback = timestamped_name(target, now)
        saved_fallback, _locked, fallback_message = atomic_save(workbook, fallback)
        if saved_fallback:
            return SaveOutcome(
                saved=True,
                path=fallback,
                locked=True,
                fell_back=True,
                message=(
                    f"{target.name} is open in Excel - saved as "
                    f"{fallback.name} instead."
                ),
                csv_path=_write_csv(repo, data),
                at=now,
                manual=manual,
            )
        return SaveOutcome(
            saved=False, locked=True, message=fallback_message, at=now, manual=manual
        )

    if locked:
        # Autosave: say so and try again on the next cycle. No stray copies.
        return SaveOutcome(
            saved=False,
            locked=True,
            message="workbook open in Excel, will retry",
            at=now,
            manual=manual,
        )

    return SaveOutcome(saved=False, message=message, at=now, manual=manual)


def _write_csv(repo: Repository, data: WorkbookData) -> Path | None:
    """The CSV is a convenience; failing to write it must not fail the save."""
    try:
        return write_timesheet_csv(csv_path(repo), data.timesheet)
    except OSError:
        return None
