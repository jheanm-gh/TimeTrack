"""Opening the database correctly.

Three pragmas matter and are set on every connection:

``journal_mode=WAL``
    lets the GUI read while a write is in flight, and survives a hard power
    loss far better than the rollback journal.
``foreign_keys=ON``
    SQLite has them off by default; without this the referential rules in
    the schema are decoration.
``busy_timeout``
    the autosave thread and the GUI thread will occasionally collide. Five
    seconds of patience turns a crash into a pause nobody notices.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

BUSY_TIMEOUT_MS = 5000


def connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open (and create, if needed) the database at ``path``."""
    target = Path(path)
    if not read_only:
        target.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(target),
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,  # explicit transactions; see transaction()
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block as one atomic unit.

    ``IMMEDIATE`` takes the write lock up front rather than part-way
    through, so two writers queue instead of one failing late with
    "database is locked".
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def checkpoint(conn: sqlite3.Connection) -> None:
    """Fold the write-ahead log back into the main file.

    Called before taking a backup so the copied file is complete.
    """
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
