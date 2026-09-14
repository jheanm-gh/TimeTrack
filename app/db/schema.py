"""Schema definition and forward-only migrations.

Every schema change is a new numbered step appended to :data:`MIGRATIONS`.
Steps are never edited once released and never run backwards, so upgrading
an existing database can add to history but can never destroy it.

Decimal-valued columns (hours, kilometres, odometer readings) are stored as
**TEXT**, not REAL. Storing them as REAL would put the very floating-point
values back into the system that :mod:`app.core.calc` exists to keep out.
"""

from __future__ import annotations

import sqlite3

from app.core.timeutil import utc_now, to_iso
from app.db.connection import transaction

SCHEMA_VERSION = 1


_MIGRATION_1 = """
CREATE TABLE projects (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    client           TEXT,
    project_code     TEXT,
    colour           TEXT,
    submission_day   TEXT,
    period_type      TEXT    NOT NULL DEFAULT 'calendar_month'
                             CHECK (period_type IN ('calendar_month', 'custom_cutoff')),
    period_start_day INTEGER CHECK (period_start_day IS NULL
                                    OR (period_start_day BETWEEN 1 AND 31)),
    status           TEXT    NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active', 'archived')),
    default_site     TEXT,
    default_km       TEXT,
    notes            TEXT    NOT NULL DEFAULT '',
    sort_order       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    archived_at      TEXT
);

-- The name is the exact string copied from the intranet dropdown, so it must
-- be unique regardless of capitalisation.
CREATE UNIQUE INDEX ux_projects_name ON projects (name COLLATE NOCASE);
CREATE INDEX ix_projects_status ON projects (status);

CREATE TABLE tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    parent_task_id INTEGER REFERENCES tasks (id) ON DELETE RESTRICT,
    name           TEXT    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'done')),
    created_at     TEXT    NOT NULL,
    completed_at   TEXT
);

CREATE INDEX ix_tasks_project ON tasks (project_id, status);
CREATE UNIQUE INDEX ux_tasks_project_name
    ON tasks (project_id, name COLLATE NOCASE);

CREATE TABLE time_entries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    task_id          INTEGER REFERENCES tasks (id) ON DELETE SET NULL,
    kind             TEXT    NOT NULL CHECK (kind IN ('work', 'software')),
    software_name    TEXT,
    started_at       TEXT    NOT NULL,
    ended_at         TEXT,
    duration_seconds INTEGER NOT NULL DEFAULT 0
                             CHECK (duration_seconds >= 0),
    description      TEXT    NOT NULL DEFAULT '',

    km_travelled     TEXT,
    odo_start        TEXT,
    odo_end          TEXT,
    trip_from        TEXT,
    trip_to          TEXT,
    trip_purpose     TEXT,

    source           TEXT    NOT NULL DEFAULT 'timer'
                             CHECK (source IN ('timer', 'manual', 'recovered')),
    is_running       INTEGER NOT NULL DEFAULT 0 CHECK (is_running IN (0, 1)),
    heartbeat_at     TEXT,
    edited           INTEGER NOT NULL DEFAULT 0 CHECK (edited IN (0, 1)),

    deleted_at       TEXT,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,

    -- A package name only makes sense on the software channel.
    CHECK (kind = 'software' OR software_name IS NULL)
);

CREATE INDEX ix_entries_project_start ON time_entries (project_id, started_at);
CREATE INDEX ix_entries_start ON time_entries (started_at);
CREATE INDEX ix_entries_live ON time_entries (deleted_at, kind);

-- Enforces "one work timer and one software timer at a time" in the
-- database itself, so no GUI bug can produce two running work timers.
CREATE UNIQUE INDEX ux_entries_one_running_per_kind
    ON time_entries (kind) WHERE is_running = 1 AND deleted_at IS NULL;

-- Pause is modelled as spans subtracted from the entry, rather than by
-- splitting the entry, so one logical session stays one row with one
-- description and one trip attached to it.
CREATE TABLE entry_pauses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id   INTEGER NOT NULL REFERENCES time_entries (id) ON DELETE CASCADE,
    paused_at  TEXT    NOT NULL,
    resumed_at TEXT,
    reason     TEXT    NOT NULL DEFAULT 'pause'
                       CHECK (reason IN ('pause', 'idle')),
    created_at TEXT    NOT NULL
);

CREATE INDEX ix_pauses_entry ON entry_pauses (entry_id);

-- One row per project per day: the description the user writes for the
-- intranet, and whether that date has been ticked off as entered.
CREATE TABLE daily_notes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id           INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    date                 TEXT    NOT NULL,
    description_override TEXT,
    ticked_at            TEXT,
    updated_at           TEXT    NOT NULL,
    UNIQUE (project_id, date)
);

CREATE INDEX ix_daily_notes_date ON daily_notes (date);

-- Every edit to a saved entry writes a before/after pair here. Nothing is
-- ever hard-deleted, so this plus the soft-delete flag means a wrong edit
-- is always recoverable.
CREATE TABLE entry_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    INTEGER NOT NULL,
    changed_at  TEXT    NOT NULL,
    before_json TEXT,
    after_json  TEXT,
    reason      TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX ix_audit_entry ON entry_audit (entry_id, changed_at);

CREATE TABLE submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    period_start TEXT    NOT NULL,
    period_end   TEXT    NOT NULL,
    submitted_at TEXT    NOT NULL,
    note         TEXT    NOT NULL DEFAULT '',
    UNIQUE (project_id, period_start, period_end)
);

CREATE INDEX ix_submissions_project ON submissions (project_id, period_end);

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


#: ``(version, sql)`` pairs applied in order. Append only - never edit a
#: released step, and never write one that drops a column or a table.
MIGRATIONS: list[tuple[int, str]] = [
    (1, _MIGRATION_1),
]


def split_statements(sql: str) -> list[str]:
    """Split a migration script into individual SQL statements.

    Why not :meth:`sqlite3.Connection.executescript`? Because it issues an
    implicit ``COMMIT`` before it runs, which would silently end the
    transaction this module opens around each migration step and leave a
    half-applied schema behind on failure. Executing statement by statement
    keeps each step atomic.

    Single-quoted literals are respected so a ``;`` inside a default value
    or a CHECK constraint does not split a statement in half.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_string = False
    in_comment = False
    index = 0
    while index < len(sql):
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < len(sql) else ""
        if in_comment:
            if char == "\n":
                in_comment = False
                buffer.append(char)
            index += 1
            continue
        if in_string:
            buffer.append(char)
            if char == "'":
                if nxt == "'":  # escaped quote inside a literal
                    buffer.append(nxt)
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if char == "-" and nxt == "-":
            in_comment = True
            index += 2
            continue
        if char == "'":
            in_string = True
            buffer.append(char)
            index += 1
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def current_version(conn: sqlite3.Connection) -> int:
    _ensure_version_table(conn)
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the database up to :data:`SCHEMA_VERSION`. Returns the new version.

    Each step runs in its own transaction: a failure part-way through a
    multi-step upgrade leaves the database at the last step that fully
    succeeded, rather than in a half-applied state.
    """
    _ensure_version_table(conn)
    version = current_version(conn)
    for step_version, sql in MIGRATIONS:
        if step_version <= version:
            continue
        with transaction(conn):
            for statement in split_statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (step_version, to_iso(utc_now())),
            )
        version = step_version
    return version


def initialise(conn: sqlite3.Connection) -> int:
    """Create or upgrade the schema and seed the default settings."""
    version = migrate(conn)
    _seed_default_settings(conn)
    return version


#: Defaults for every configurable value. Anything the GUI offers in
#: Settings must have an entry here so a fresh install behaves sensibly.
DEFAULT_SETTINGS: dict[str, str] = {
    # Rounding (section 7 of the brief)
    "rounding.increment": "0.25",
    "rounding.direction": "up",
    "rounding.apply_to_software": "1",
    # Idle detection (work channel only)
    "idle.enabled": "1",
    "idle.threshold_minutes": "10",
    "idle.poll_seconds": "30",
    # Timers
    "timer.heartbeat_seconds": "15",
    "timer.on_start_new_work": "stop",  # 'stop' or 'pause' the previous one
    # Workbook
    "workbook.path": "",  # empty means "use the default location"
    "workbook.autosave_enabled": "1",
    "workbook.autosave_minutes": "30",
    "workbook.debounce_seconds": "120",
    "workbook.per_project_sheets": "1",
    "workbook.max_project_sheets": "40",
    "workbook.description_limit": "500",
    # Backups
    "backup.workbook_interval_days": "7",
    "backup.workbook_keep": "52",
    "backup.db_keep": "30",
    # Windows integration
    "startup.run_on_login": "0",
    "startup.open_minimised": "0",
    "close.confirm": "1",
    "close.default_action": "tray",
    # Display
    "display.timezone": "system",
    "ui.catchup_days": "30",
    "ui.deadline_warning_days": "5",
    "gaps.window_days": "60",
}


def _seed_default_settings(conn: sqlite3.Connection) -> None:
    stamp = to_iso(utc_now())
    with transaction(conn):
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT (key) DO NOTHING
                """,
                (key, value, stamp),
            )
