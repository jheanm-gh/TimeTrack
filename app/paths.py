"""Where TimeTrack keeps its files.

The rules from the brief, encoded once:

* the database lives in **LOCALAPPDATA**, never in a roaming profile - a
  sync engine touching a live SQLite file is a reliable way to corrupt it;
* backups sit beside it;
* the workbook defaults to ``Documents\\TimeTrack`` but is user-configurable;
* nothing is *ever* written next to the executable.

On Linux/macOS (used for development and the test suite) the equivalent
XDG-style locations are used instead, so the app and its tests run anywhere.
``TIMETRACK_DATA_DIR`` overrides everything, which is how the tests get a
throwaway directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.version import APP_NAME

ENV_DATA_DIR = "TIMETRACK_DATA_DIR"
ENV_WORKBOOK_DIR = "TIMETRACK_WORKBOOK_DIR"

DB_FILENAME = "timetrack.db"
WORKBOOK_FILENAME = "TimeLog.xlsx"
CSV_FILENAME = "TimeLog.csv"
LOCK_FILENAME = "timetrack.lock"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def data_dir() -> Path:
    """The application's private folder: database, backups, logs, lock file."""
    override = _env_path(ENV_DATA_DIR)
    if override is not None:
        return override
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def backup_dir() -> Path:
    return data_dir() / "backups"


def db_backup_dir() -> Path:
    return backup_dir() / "db"


def log_dir() -> Path:
    return data_dir() / "logs"


def lock_path() -> Path:
    """Single-instance marker; a second launch focuses the first window."""
    return data_dir() / LOCK_FILENAME


def documents_dir() -> Path:
    if is_windows():
        profile = os.environ.get("USERPROFILE")
        if profile:
            return Path(profile) / "Documents"
    return Path.home() / "Documents"


def default_workbook_dir() -> Path:
    """Default export folder. Overridable in Settings via a folder picker."""
    override = _env_path(ENV_WORKBOOK_DIR)
    if override is not None:
        return override
    return documents_dir() / APP_NAME


def default_workbook_path() -> Path:
    return default_workbook_dir() / WORKBOOK_FILENAME


def ensure_dirs() -> None:
    """Create every folder the app writes to. Safe to call repeatedly."""
    for path in (data_dir(), backup_dir(), db_backup_dir(), log_dir()):
        path.mkdir(parents=True, exist_ok=True)


def describe_locations() -> dict[str, str]:
    """Human-readable summary for the status bar and the Settings panel."""
    return {
        "Database": str(db_path()),
        "Backups": str(backup_dir()),
        "Database backups": str(db_backup_dir()),
        "Default workbook": str(default_workbook_path()),
    }
