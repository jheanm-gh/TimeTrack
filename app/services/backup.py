"""Backups: a weekly copy of the workbook, a daily copy of the database.

The database copy uses SQLite's own ``VACUUM INTO`` rather than a file copy.
A live WAL database is three files, and the most recent writes may live only
in the ``-wal`` sidecar - copying ``timetrack.db`` on its own can therefore
produce a backup that is missing the last hour of work, or is simply
corrupt. ``VACUUM INTO`` asks SQLite to write a complete, consistent
database, which is the only kind worth keeping.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.core.timeutil import date_from_iso, date_to_iso

DB_BACKUP_PREFIX = "timetrack_"
WORKBOOK_BACKUP_PREFIX = "TimeLog_"

SETTING_WORKBOOK_LAST = "backup.workbook_last_date"
SETTING_DB_LAST = "backup.db_last_date"


@dataclass(frozen=True, slots=True)
class BackupOutcome:
    made: bool
    path: Path | None = None
    message: str = ""
    pruned: int = 0


def iso_week_tag(day: _dt.date) -> str:
    """``2026-W38`` - the ISO week, so a year of snapshots sorts correctly."""
    year, week, _weekday = day.isocalendar()
    return f"{year}-W{week:02d}"


# -- database --------------------------------------------------------------


def backup_database(
    conn: sqlite3.Connection,
    destination_dir: Path,
    today: _dt.date,
    keep: int = 30,
) -> BackupOutcome:
    """Take today's database snapshot, unless one already exists."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"{DB_BACKUP_PREFIX}{date_to_iso(today)}.db"
    if target.exists():
        return BackupOutcome(made=False, path=target, message="Already backed up today.")

    staging = target.with_suffix(".db.part")
    try:
        # VACUUM INTO refuses to write over an existing file, so a stale
        # temporary from an interrupted run has to go first. Inside the try:
        # if removing it fails, that is a backup failure like any other.
        staging.unlink(missing_ok=True)
        conn.execute("VACUUM INTO ?", (str(staging),))
        staging.replace(target)
    except (sqlite3.Error, OSError) as exc:
        staging.unlink(missing_ok=True)
        return BackupOutcome(made=False, message=f"Database backup failed: {exc}")

    pruned = prune(destination_dir, f"{DB_BACKUP_PREFIX}*.db", keep)
    return BackupOutcome(
        made=True, path=target, message=f"Database backed up to {target.name}.", pruned=pruned
    )


# -- workbook --------------------------------------------------------------


def workbook_backup_due(
    last_backup: _dt.date | None, today: _dt.date, interval_days: int = 7
) -> bool:
    """Is a weekly snapshot due?

    Compared against the *date of the last backup* rather than against a
    fixed day of the week, so a fortnight away from the laptop produces a
    backup on the first day back instead of skipping one silently.
    """
    if last_backup is None:
        return True
    return (today - last_backup).days >= interval_days


def backup_workbook(
    workbook: Path,
    destination_dir: Path,
    today: _dt.date,
    keep: int = 52,
) -> BackupOutcome:
    """Copy the workbook into the backups folder, tagged with the ISO week."""
    if not workbook.exists():
        return BackupOutcome(made=False, message="There is no workbook to back up yet.")

    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"{WORKBOOK_BACKUP_PREFIX}{iso_week_tag(today)}.xlsx"
    try:
        # copy2 keeps the modification time, so the backups folder shows when
        # the work was done rather than when it was copied.
        shutil.copy2(workbook, target)
    except OSError as exc:
        return BackupOutcome(made=False, message=f"Workbook backup failed: {exc}")

    pruned = prune(destination_dir, f"{WORKBOOK_BACKUP_PREFIX}*.xlsx", keep)
    return BackupOutcome(
        made=True, path=target, message=f"Workbook backed up as {target.name}.", pruned=pruned
    )


# -- retention -------------------------------------------------------------


def prune(directory: Path, pattern: str, keep: int) -> int:
    """Keep the newest ``keep`` files matching ``pattern``; delete the rest.

    Sorted by name, which for both naming schemes here sorts chronologically.
    """
    if keep <= 0:
        return 0
    existing = sorted(directory.glob(pattern))
    surplus = existing[:-keep] if len(existing) > keep else []
    removed = 0
    for path in surplus:
        try:
            path.unlink()
            removed += 1
        except OSError:
            # A file held open by a backup tool is not worth failing over.
            continue
    return removed


# -- orchestration ---------------------------------------------------------


def run_startup_backups(repo, conn, workbook: Path, db_backup_dir: Path, backup_dir: Path,
                        today: _dt.date) -> list[BackupOutcome]:
    """Everything that should happen once, when the application opens."""
    outcomes: list[BackupOutcome] = []

    database = backup_database(
        conn, db_backup_dir, today, keep=repo.get_int("backup.db_keep", 30)
    )
    outcomes.append(database)
    if database.made:
        repo.set_setting(SETTING_DB_LAST, date_to_iso(today))

    last_raw = repo.get_setting(SETTING_WORKBOOK_LAST)
    last = date_from_iso(last_raw) if last_raw else None
    interval = repo.get_int("backup.workbook_interval_days", 7)
    if workbook_backup_due(last, today, interval):
        result = backup_workbook(
            workbook, backup_dir, today, keep=repo.get_int("backup.workbook_keep", 52)
        )
        outcomes.append(result)
        if result.made:
            repo.set_setting(SETTING_WORKBOOK_LAST, date_to_iso(today))
    return outcomes


def describe_backups(backup_dir: Path, db_backup_dir: Path) -> dict[str, object]:
    """Counts and latest dates, for the Backups panel in Settings."""
    workbooks = sorted(backup_dir.glob(f"{WORKBOOK_BACKUP_PREFIX}*.xlsx"))
    databases = sorted(db_backup_dir.glob(f"{DB_BACKUP_PREFIX}*.db"))
    return {
        "workbook_count": len(workbooks),
        "workbook_latest": workbooks[-1].name if workbooks else None,
        "database_count": len(databases),
        "database_latest": databases[-1].name if databases else None,
    }
