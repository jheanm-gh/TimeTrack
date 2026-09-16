"""The repository layer: everything that reads or writes the database.

Two rules run through the whole module.

**Nothing is ever hard-deleted.** Entries carry a ``deleted_at`` flag and
stay in the table. This data feeds billing, so a delete has to be a
recoverable act, not a destructive one.

**Every change to a saved entry is audited.** Each update writes the row as
it was and as it became into ``entry_audit`` with a reason, so "what did
that entry say before I changed it?" always has an answer.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.core.calc import (
    ValidationError,
    active_intervals,
    entry_duration_seconds,
    resolve_travel_km,
)
from app.core.models import (
    EntryCalc,
    EntryKind,
    EntrySource,
    Interval,
    PeriodType,
    ProjectStatus,
    RoundingDirection,
    RoundingRule,
    TaskStatus,
    TravelDetail,
)
from app.core.timeutil import (
    date_from_iso,
    date_to_iso,
    from_iso,
    local_date,
    resolve_timezone,
    to_iso,
    utc_now,
)
from app.db.connection import transaction

#: Columns copied into the audit snapshot. Deliberately explicit: adding a
#: column to the table should be a conscious decision about whether a change
#: to it is worth recording.
_AUDITED_COLUMNS = (
    "project_id",
    "task_id",
    "kind",
    "software_name",
    "started_at",
    "ended_at",
    "duration_seconds",
    "description",
    "km_travelled",
    "odo_start",
    "odo_end",
    "trip_from",
    "trip_to",
    "trip_purpose",
    "source",
    "is_running",
    "deleted_at",
)


def _is_unique_violation(exc: sqlite3.IntegrityError, *columns: str) -> bool:
    """Did this IntegrityError come from the unique index on ``columns``?

    SQLite names the offending *columns* in the message
    ("UNIQUE constraint failed: projects.name"), not the index, so matching
    on an index name silently never fires.
    """
    message = str(exc)
    if "UNIQUE constraint failed" not in message:
        return False
    return all(column in message for column in columns)


def _dec(value) -> Decimal | None:
    """Read a Decimal back out of a TEXT column."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"'{value}' is not a valid number.") from exc


def _dec_text(value) -> str | None:
    """Write a Decimal into a TEXT column without going via float."""
    if value is None or value == "":
        return None
    return str(Decimal(str(value)))


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class SavedEntry:
    """A time entry as it exists in the database, plus its pauses."""

    id: int
    project_id: int
    task_id: int | None
    kind: EntryKind
    software_name: str | None
    started_at: _dt.datetime
    ended_at: _dt.datetime | None
    duration_seconds: int
    description: str
    travel: TravelDetail
    source: EntrySource
    is_running: bool
    heartbeat_at: _dt.datetime | None
    edited: bool
    deleted_at: _dt.datetime | None
    created_at: _dt.datetime
    updated_at: _dt.datetime
    pauses: tuple[Interval, ...] = ()

    def to_calc(self) -> EntryCalc:
        return EntryCalc(
            project_id=self.project_id,
            kind=self.kind,
            start=self.started_at,
            end=self.ended_at,
            pauses=self.pauses,
            entry_id=self.id,
            task_id=self.task_id,
            description=self.description,
            software_name=self.software_name,
            km_travelled=self.travel.km_travelled,
        )


class Repository:
    """All database access lives behind this object."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- settings ---------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value) -> None:
        stamp = to_iso(utc_now())
        with transaction(self.conn):
            self.conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value,
                                                updated_at = excluded.updated_at
                """,
                (key, str(value), stamp),
            )

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get_setting(key)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def get_int(self, key: str, default: int = 0) -> int:
        raw = self.get_setting(key)
        try:
            return int(raw) if raw not in (None, "") else default
        except ValueError:
            return default

    def get_decimal(self, key: str, default: Decimal) -> Decimal:
        raw = self.get_setting(key)
        try:
            return Decimal(raw) if raw not in (None, "") else default
        except (InvalidOperation, TypeError):
            return default

    def all_settings(self) -> dict[str, str]:
        return {
            row["key"]: row["value"]
            for row in self.conn.execute("SELECT key, value FROM settings")
        }

    def rounding_rule(self) -> RoundingRule:
        """Build the rounding rule from settings. Used by every export."""
        direction_raw = (self.get_setting("rounding.direction") or "up").strip().lower()
        try:
            direction = RoundingDirection(direction_raw)
        except ValueError:
            direction = RoundingDirection.UP
        return RoundingRule(
            increment=self.get_decimal("rounding.increment", Decimal("0.25")),
            direction=direction,
            apply_to_software=self.get_bool("rounding.apply_to_software", True),
        )

    def timezone(self) -> _dt.tzinfo:
        return resolve_timezone(self.get_setting("display.timezone"))

    # -- projects ---------------------------------------------------------

    def add_project(
        self,
        name: str,
        *,
        client: str | None = None,
        project_code: str | None = None,
        colour: str | None = None,
        submission_day: str | None = None,
        period_type: PeriodType = PeriodType.CALENDAR_MONTH,
        period_start_day: int | None = None,
        default_site: str | None = None,
        default_km: Decimal | None = None,
        notes: str = "",
        now: _dt.datetime | None = None,
    ) -> int:
        name = (name or "").strip()
        if not name:
            raise ValidationError("A project needs a name.")
        if PeriodType(period_type) is PeriodType.CUSTOM_CUTOFF and not period_start_day:
            raise ValidationError(
                "A cutoff cycle needs the day of the month the period starts on."
            )
        stamp = to_iso(now or utc_now())
        try:
            with transaction(self.conn):
                cursor = self.conn.execute(
                    """
                    INSERT INTO projects (name, client, project_code, colour,
                                          submission_day, period_type,
                                          period_start_day, status, default_site,
                                          default_km, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        name,
                        _clean(client),
                        _clean(project_code),
                        _clean(colour),
                        _clean(submission_day),
                        str(PeriodType(period_type)),
                        int(period_start_day) if period_start_day else None,
                        _clean(default_site),
                        _dec_text(default_km),
                        notes or "",
                        stamp,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if _is_unique_violation(exc, "projects.name"):
                raise ValidationError(
                    f"There is already a project called '{name}'."
                ) from exc
            raise
        return int(cursor.lastrowid)

    def bulk_add_projects(
        self, text: str, now: _dt.datetime | None = None
    ) -> tuple[list[int], list[str]]:
        """Create several projects from a pasted list, one name per line.

        Returns the ids created and the names skipped because they already
        exist, so the GUI can report both without failing the whole paste.
        """
        created: list[int] = []
        skipped: list[str] = []
        seen: set[str] = set()
        for raw_line in (text or "").splitlines():
            name = raw_line.strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            try:
                created.append(self.add_project(name, now=now))
            except ValidationError:
                skipped.append(name)
        return created, skipped

    def update_project(self, project_id: int, **fields) -> None:
        allowed = {
            "name",
            "client",
            "project_code",
            "colour",
            "submission_day",
            "period_type",
            "period_start_day",
            "default_site",
            "default_km",
            "notes",
            "sort_order",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        if "name" in updates:
            updates["name"] = (updates["name"] or "").strip()
            if not updates["name"]:
                raise ValidationError("A project needs a name.")
        if "default_km" in updates:
            updates["default_km"] = _dec_text(updates["default_km"])
        if "period_type" in updates:
            updates["period_type"] = str(PeriodType(updates["period_type"]))
        assignments = ", ".join(f"{key} = ?" for key in updates)
        try:
            with transaction(self.conn):
                self.conn.execute(
                    f"UPDATE projects SET {assignments} WHERE id = ?",
                    (*updates.values(), project_id),
                )
        except sqlite3.IntegrityError as exc:
            if _is_unique_violation(exc, "projects.name"):
                raise ValidationError(
                    f"There is already a project called '{updates.get('name')}'."
                ) from exc
            raise

    def set_project_status(
        self, project_id: int, status: ProjectStatus, now: _dt.datetime | None = None
    ) -> None:
        """Archive or reactivate. History is untouched either way."""
        status = ProjectStatus(status)
        stamp = to_iso(now or utc_now())
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE projects SET status = ?, archived_at = ? WHERE id = ?",
                (
                    str(status),
                    stamp if status is ProjectStatus.ARCHIVED else None,
                    project_id,
                ),
            )

    def archive_project(self, project_id: int, now: _dt.datetime | None = None) -> None:
        self.set_project_status(project_id, ProjectStatus.ARCHIVED, now=now)

    def unarchive_project(self, project_id: int) -> None:
        self.set_project_status(project_id, ProjectStatus.ACTIVE)

    def get_project(self, project_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()

    def find_project_by_name(self, name: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (name.strip(),)
        ).fetchone()

    def list_projects(self, include_archived: bool = False) -> list[sqlite3.Row]:
        """Projects for the pickers and lists.

        Archived projects always sort *after* active ones. Sorting purely by
        name would put a closed-out job at the top of every dropdown, and
        anything that defaults to the first entry - the Review tab, for
        instance - would open on a project with no current time in it.
        """
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE status = 'active'"
        sql += (
            " ORDER BY (status = 'archived'), sort_order, name COLLATE NOCASE"
        )
        return list(self.conn.execute(sql))

    def project_names(self) -> dict[int, str]:
        return {
            row["id"]: row["name"] for row in self.conn.execute("SELECT id, name FROM projects")
        }

    # -- tasks ------------------------------------------------------------

    def add_task(
        self,
        project_id: int,
        name: str,
        *,
        parent_task_id: int | None = None,
        now: _dt.datetime | None = None,
    ) -> int:
        name = (name or "").strip()
        if not name:
            raise ValidationError("A task needs a name.")
        if parent_task_id is not None:
            parent = self.conn.execute(
                "SELECT parent_task_id, project_id FROM tasks WHERE id = ?",
                (parent_task_id,),
            ).fetchone()
            if parent is None:
                raise ValidationError("That parent task no longer exists.")
            if parent["parent_task_id"] is not None:
                # One level of nesting only, by design.
                raise ValidationError(
                    "Tasks can only be nested one level deep. Attach this task "
                    "to the top-level task instead."
                )
            if parent["project_id"] != project_id:
                raise ValidationError(
                    "A sub-task must belong to the same project as its parent."
                )
        stamp = to_iso(now or utc_now())
        try:
            with transaction(self.conn):
                cursor = self.conn.execute(
                    """
                    INSERT INTO tasks (project_id, parent_task_id, name, status,
                                       created_at)
                    VALUES (?, ?, ?, 'active', ?)
                    """,
                    (project_id, parent_task_id, name, stamp),
                )
        except sqlite3.IntegrityError as exc:
            if _is_unique_violation(exc, "tasks.project_id", "tasks.name"):
                raise ValidationError(
                    f"This project already has a task called '{name}'."
                ) from exc
            raise
        return int(cursor.lastrowid)

    def set_task_status(
        self, task_id: int, status: TaskStatus, now: _dt.datetime | None = None
    ) -> None:
        status = TaskStatus(status)
        stamp = to_iso(now or utc_now())
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
                (str(status), stamp if status is TaskStatus.DONE else None, task_id),
            )

    def list_tasks(
        self, project_id: int | None = None, include_done: bool = False
    ) -> list[sqlite3.Row]:
        clauses, params = [], []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if not include_done:
            clauses.append("status = 'active'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return list(
            self.conn.execute(
                f"SELECT * FROM tasks{where} ORDER BY parent_task_id IS NOT NULL, "
                "name COLLATE NOCASE",
                params,
            )
        )

    def get_task(self, task_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()

    # -- entries: reading -------------------------------------------------

    def _row_to_entry(self, row: sqlite3.Row, pauses: tuple[Interval, ...]) -> SavedEntry:
        return SavedEntry(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            task_id=row["task_id"],
            kind=EntryKind(row["kind"]),
            software_name=row["software_name"],
            started_at=from_iso(row["started_at"]),
            ended_at=from_iso(row["ended_at"]) if row["ended_at"] else None,
            duration_seconds=int(row["duration_seconds"]),
            description=row["description"] or "",
            travel=TravelDetail(
                km_travelled=_dec(row["km_travelled"]),
                odo_start=_dec(row["odo_start"]),
                odo_end=_dec(row["odo_end"]),
                trip_from=row["trip_from"],
                trip_to=row["trip_to"],
                trip_purpose=row["trip_purpose"],
            ),
            source=EntrySource(row["source"]),
            is_running=bool(row["is_running"]),
            heartbeat_at=from_iso(row["heartbeat_at"]) if row["heartbeat_at"] else None,
            edited=bool(row["edited"]),
            deleted_at=from_iso(row["deleted_at"]) if row["deleted_at"] else None,
            created_at=from_iso(row["created_at"]),
            updated_at=from_iso(row["updated_at"]),
            pauses=pauses,
        )

    def _pauses_for(self, entry_ids: list[int]) -> dict[int, tuple[Interval, ...]]:
        if not entry_ids:
            return {}
        placeholders = ",".join("?" for _ in entry_ids)
        found: dict[int, list[Interval]] = {}
        for row in self.conn.execute(
            f"SELECT * FROM entry_pauses WHERE entry_id IN ({placeholders}) "
            "ORDER BY paused_at",
            entry_ids,
        ):
            found.setdefault(int(row["entry_id"]), []).append(
                Interval(
                    from_iso(row["paused_at"]),
                    from_iso(row["resumed_at"]) if row["resumed_at"] else None,
                )
            )
        return {key: tuple(value) for key, value in found.items()}

    def get_entry(self, entry_id: int) -> SavedEntry | None:
        row = self.conn.execute(
            "SELECT * FROM time_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row, self._pauses_for([entry_id]).get(entry_id, ()))

    def list_entries(
        self,
        *,
        start: _dt.date | None = None,
        end: _dt.date | None = None,
        project_id: int | None = None,
        kind: EntryKind | None = None,
        include_deleted: bool = False,
        include_running: bool = True,
        tz: _dt.tzinfo | None = None,
    ) -> list[SavedEntry]:
        """Entries overlapping a local-date window.

        The window is widened by a day on each side before the SQL filter,
        then narrowed precisely in Python, because ``started_at`` is stored
        in UTC and the boundary the user means is local midnight.
        """
        tz = tz or self.timezone()
        clauses: list[str] = []
        params: list = []
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if not include_running:
            clauses.append("is_running = 0")
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(str(EntryKind(kind)))
        if start is not None:
            clauses.append("ended_at IS NULL OR ended_at >= ?")
            params.append(to_iso(_dt.datetime.combine(
                start - _dt.timedelta(days=2), _dt.time.min, tzinfo=tz
            )))
        if end is not None:
            clauses.append("started_at <= ?")
            params.append(to_iso(_dt.datetime.combine(
                end + _dt.timedelta(days=2), _dt.time.max, tzinfo=tz
            )))
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(f"({clause})" for clause in clauses)
        rows = list(
            self.conn.execute(
                f"SELECT * FROM time_entries{where} ORDER BY started_at, id", params
            )
        )
        pauses = self._pauses_for([int(row["id"]) for row in rows])
        entries = [
            self._row_to_entry(row, pauses.get(int(row["id"]), ())) for row in rows
        ]
        if start is None and end is None:
            return entries

        # Narrow to entries that actually touch the requested local dates.
        kept: list[SavedEntry] = []
        now = utc_now()
        for entry in entries:
            first = local_date(entry.started_at, tz)
            last = local_date(entry.ended_at or now, tz)
            if start is not None and last < start:
                continue
            if end is not None and first > end:
                continue
            kept.append(entry)
        return kept

    def entries_for_calc(self, **kwargs) -> list[EntryCalc]:
        """The calculation layer's view of a set of entries."""
        return [entry.to_calc() for entry in self.list_entries(**kwargs)]

    def running_entries(self) -> list[SavedEntry]:
        rows = list(
            self.conn.execute(
                "SELECT * FROM time_entries WHERE is_running = 1 AND deleted_at IS NULL"
            )
        )
        pauses = self._pauses_for([int(row["id"]) for row in rows])
        return [self._row_to_entry(row, pauses.get(int(row["id"]), ())) for row in rows]

    def running_entry(self, kind: EntryKind) -> SavedEntry | None:
        for entry in self.running_entries():
            if entry.kind is EntryKind(kind):
                return entry
        return None

    def recorded_dates(
        self,
        start: _dt.date,
        end: _dt.date,
        tz: _dt.tzinfo | None = None,
        kind: EntryKind | None = EntryKind.WORK,
    ) -> set[_dt.date]:
        """Local dates in the window that have any recorded time.

        Used by the catch-up nudge and the Gaps sheet. Defaults to the work
        channel: a night of unattended software time is not a worked day.
        """
        tz = tz or self.timezone()
        dates: set[_dt.date] = set()
        for entry in self.list_entries(start=start, end=end, kind=kind, tz=tz):
            if entry.duration_seconds <= 0 and not entry.is_running:
                continue
            for span in active_intervals(entry.to_calc(), now=utc_now()):
                cursor = span.start
                while cursor < (span.end or span.start):
                    day = local_date(cursor, tz)
                    dates.add(day)
                    cursor = _dt.datetime.combine(
                        day + _dt.timedelta(days=1), _dt.time.min, tzinfo=tz
                    )
        return {day for day in dates if start <= day <= end}

    def software_names(self, limit: int = 30) -> list[str]:
        """Previously used package names, most recent first, for the dropdown."""
        rows = self.conn.execute(
            """
            SELECT software_name, MAX(started_at) AS last_used
            FROM time_entries
            WHERE software_name IS NOT NULL AND software_name <> ''
              AND deleted_at IS NULL
            GROUP BY software_name COLLATE NOCASE
            ORDER BY last_used DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [row["software_name"] for row in rows]

    def last_work_entry(self) -> SavedEntry | None:
        """Most recent work entry - powers "Start last task again"."""
        row = self.conn.execute(
            """
            SELECT * FROM time_entries
            WHERE kind = 'work' AND deleted_at IS NULL
            ORDER BY started_at DESC, id DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        entry_id = int(row["id"])
        return self._row_to_entry(row, self._pauses_for([entry_id]).get(entry_id, ()))

    # -- entries: writing -------------------------------------------------

    def _snapshot(self, entry_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM time_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return {column: row[column] for column in _AUDITED_COLUMNS}

    def _write_audit(
        self,
        entry_id: int,
        before: dict | None,
        after: dict | None,
        reason: str,
        now: _dt.datetime | None = None,
    ) -> None:
        """Record a before/after pair. Called inside the caller's transaction."""
        self.conn.execute(
            """
            INSERT INTO entry_audit (entry_id, changed_at, before_json,
                                     after_json, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                to_iso(now or utc_now()),
                json.dumps(before, sort_keys=True) if before is not None else None,
                json.dumps(after, sort_keys=True) if after is not None else None,
                reason,
            ),
        )

    def audit_trail(self, entry_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM entry_audit WHERE entry_id = ? ORDER BY changed_at, id",
                (entry_id,),
            )
        )

    def _recompute_duration(self, entry_id: int, now: _dt.datetime | None = None) -> int:
        """Recalculate and store ``duration_seconds`` from start/end/pauses.

        The column is stored rather than derived on read (it is what the Raw
        Log shows and what crash recovery reports), so it has to be refreshed
        whenever any of its inputs change.
        """
        entry = self.get_entry(entry_id)
        if entry is None:
            return 0
        seconds = entry_duration_seconds(entry.to_calc(), now=now or utc_now())
        self.conn.execute(
            "UPDATE time_entries SET duration_seconds = ? WHERE id = ?",
            (int(seconds), entry_id),
        )
        return int(seconds)

    def _validate_travel(self, travel: TravelDetail) -> TravelDetail:
        """Normalise travel input, deriving km from odometer readings."""
        km = resolve_travel_km(travel)
        from dataclasses import replace

        return replace(travel, km_travelled=km)

    def start_timer(
        self,
        project_id: int,
        kind: EntryKind = EntryKind.WORK,
        *,
        task_id: int | None = None,
        description: str = "",
        software_name: str | None = None,
        travel: TravelDetail | None = None,
        now: _dt.datetime | None = None,
        source: EntrySource = EntrySource.TIMER,
    ) -> int:
        """Begin a timer on one of the two channels.

        Starting a second timer on the same channel closes the first, either
        by stopping it or by pausing it, according to the
        ``timer.on_start_new_work`` setting.
        """
        kind = EntryKind(kind)
        now = now or utc_now()
        if self.get_project(project_id) is None:
            raise ValidationError("Choose a project before starting a timer.")
        if kind is EntryKind.SOFTWARE and not (software_name or "").strip():
            raise ValidationError(
                "A software timer needs the name of the package being used."
            )
        if kind is EntryKind.WORK and software_name:
            raise ValidationError(
                "A package name belongs on a software timer, not a work timer."
            )

        existing = self.running_entry(kind)
        if existing is not None:
            if self.get_setting("timer.on_start_new_work", "stop") == "pause":
                self.pause_timer(kind, now=now)
                self.conn.execute(
                    "UPDATE time_entries SET is_running = 0, ended_at = ?, "
                    "updated_at = ? WHERE id = ?",
                    (to_iso(now), to_iso(now), existing.id),
                )
                self._recompute_duration(existing.id, now=now)
            else:
                self.stop_timer(kind, now=now)

        travel = self._validate_travel(travel or TravelDetail())
        stamp = to_iso(now)
        with transaction(self.conn):
            cursor = self.conn.execute(
                """
                INSERT INTO time_entries (project_id, task_id, kind, software_name,
                                          started_at, ended_at, duration_seconds,
                                          description, km_travelled, odo_start,
                                          odo_end, trip_from, trip_to, trip_purpose,
                                          source, is_running, heartbeat_at,
                                          created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, NULL, 0, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    project_id,
                    task_id,
                    str(kind),
                    _clean(software_name) if kind is EntryKind.SOFTWARE else None,
                    stamp,
                    description or "",
                    _dec_text(travel.km_travelled),
                    _dec_text(travel.odo_start),
                    _dec_text(travel.odo_end),
                    _clean(travel.trip_from),
                    _clean(travel.trip_to),
                    _clean(travel.trip_purpose),
                    str(EntrySource(source)),
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            entry_id = int(cursor.lastrowid)
            self._write_audit(
                entry_id, None, self._snapshot(entry_id), "timer started", now
            )
        return entry_id

    def pause_timer(
        self, kind: EntryKind, now: _dt.datetime | None = None, reason: str = "pause"
    ) -> bool:
        """Open a paused span on the running entry of this channel."""
        now = now or utc_now()
        entry = self.running_entry(kind)
        if entry is None:
            return False
        if any(pause.is_open for pause in entry.pauses):
            return False  # already paused
        with transaction(self.conn):
            self.conn.execute(
                """
                INSERT INTO entry_pauses (entry_id, paused_at, resumed_at, reason,
                                          created_at)
                VALUES (?, ?, NULL, ?, ?)
                """,
                (entry.id, to_iso(now), reason, to_iso(now)),
            )
            self.conn.execute(
                "UPDATE time_entries SET updated_at = ? WHERE id = ?",
                (to_iso(now), entry.id),
            )
        return True

    def resume_timer(self, kind: EntryKind, now: _dt.datetime | None = None) -> bool:
        """Close the open paused span, so the timer counts again."""
        now = now or utc_now()
        entry = self.running_entry(kind)
        if entry is None:
            return False
        row = self.conn.execute(
            """
            SELECT id FROM entry_pauses
            WHERE entry_id = ? AND resumed_at IS NULL
            ORDER BY paused_at DESC LIMIT 1
            """,
            (entry.id,),
        ).fetchone()
        if row is None:
            return False
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE entry_pauses SET resumed_at = ? WHERE id = ?",
                (to_iso(now), int(row["id"])),
            )
            self.conn.execute(
                "UPDATE time_entries SET updated_at = ? WHERE id = ?",
                (to_iso(now), entry.id),
            )
            self._recompute_duration(entry.id, now=now)
        return True

    def is_paused(self, kind: EntryKind) -> bool:
        entry = self.running_entry(kind)
        return bool(entry and any(pause.is_open for pause in entry.pauses))

    def stop_timer(
        self,
        kind: EntryKind,
        now: _dt.datetime | None = None,
        ended_at: _dt.datetime | None = None,
    ) -> int | None:
        """Close the running entry on a channel and write its duration.

        The entry is written immediately - nothing about a stopped timer ever
        exists only in memory.
        """
        now = now or utc_now()
        end = ended_at or now
        entry = self.running_entry(kind)
        if entry is None:
            return None
        if end < entry.started_at:
            raise ValidationError(
                "The end time cannot be before the time the timer started."
            )
        with transaction(self.conn):
            before = self._snapshot(entry.id)
            # A timer stopped while paused: close the open pause at the same
            # instant so the paused stretch is not counted.
            self.conn.execute(
                "UPDATE entry_pauses SET resumed_at = ? "
                "WHERE entry_id = ? AND resumed_at IS NULL",
                (to_iso(end), entry.id),
            )
            self.conn.execute(
                """
                UPDATE time_entries
                SET is_running = 0, ended_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (to_iso(end), to_iso(now), to_iso(now), entry.id),
            )
            self._recompute_duration(entry.id, now=now)
            self._write_audit(
                entry.id, before, self._snapshot(entry.id), "timer stopped", now
            )
        return entry.id

    def stop_all(self, now: _dt.datetime | None = None) -> list[int]:
        stopped = []
        for entry in self.running_entries():
            result = self.stop_timer(entry.kind, now=now)
            if result is not None:
                stopped.append(result)
        return stopped

    def heartbeat(self, now: _dt.datetime | None = None) -> None:
        """Mark running entries as alive, and keep their duration current.

        If the app dies, the last heartbeat is how much time we can honestly
        offer to recover.
        """
        now = now or utc_now()
        with transaction(self.conn):
            for entry in self.running_entries():
                self.conn.execute(
                    "UPDATE time_entries SET heartbeat_at = ? WHERE id = ?",
                    (to_iso(now), entry.id),
                )
                self._recompute_duration(entry.id, now=now)

    def add_manual_entry(
        self,
        project_id: int,
        kind: EntryKind = EntryKind.WORK,
        *,
        started_at: _dt.datetime,
        ended_at: _dt.datetime | None = None,
        task_id: int | None = None,
        description: str = "",
        software_name: str | None = None,
        travel: TravelDetail | None = None,
        source: EntrySource = EntrySource.MANUAL,
        now: _dt.datetime | None = None,
        reason: str = "manual entry added",
    ) -> int:
        """Create a completed entry by hand.

        First-class, not an afterthought: most reconstruction of a forgotten
        week happens through this path. An entry with no duration at all is
        allowed, because a standalone site trip is kilometres with no hours.
        """
        kind = EntryKind(kind)
        now = now or utc_now()
        if self.get_project(project_id) is None:
            raise ValidationError("Choose a project for this entry.")
        if ended_at is not None and ended_at < started_at:
            raise ValidationError(
                "The end time is before the start time. Please check them."
            )
        if kind is EntryKind.SOFTWARE and not (software_name or "").strip():
            raise ValidationError(
                "A software entry needs the name of the package used."
            )
        travel = self._validate_travel(travel or TravelDetail())
        if ended_at is None and not travel.has_any:
            raise ValidationError(
                "This entry has neither an end time nor any travel recorded, "
                "so there is nothing to save."
            )
        stamp = to_iso(now)
        with transaction(self.conn):
            cursor = self.conn.execute(
                """
                INSERT INTO time_entries (project_id, task_id, kind, software_name,
                                          started_at, ended_at, duration_seconds,
                                          description, km_travelled, odo_start,
                                          odo_end, trip_from, trip_to, trip_purpose,
                                          source, is_running, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    project_id,
                    task_id,
                    str(kind),
                    _clean(software_name) if kind is EntryKind.SOFTWARE else None,
                    to_iso(started_at),
                    to_iso(ended_at) if ended_at else None,
                    description or "",
                    _dec_text(travel.km_travelled),
                    _dec_text(travel.odo_start),
                    _dec_text(travel.odo_end),
                    _clean(travel.trip_from),
                    _clean(travel.trip_to),
                    _clean(travel.trip_purpose),
                    str(EntrySource(source)),
                    stamp,
                    stamp,
                ),
            )
            entry_id = int(cursor.lastrowid)
            self._recompute_duration(entry_id, now=now)
            self._write_audit(entry_id, None, self._snapshot(entry_id), reason, now)
        return entry_id

    def log_travel(
        self,
        project_id: int,
        *,
        on_date: _dt.date,
        travel: TravelDetail,
        description: str = "",
        tz: _dt.tzinfo | None = None,
        now: _dt.datetime | None = None,
    ) -> int:
        """Record a trip with kilometres and no hours.

        Anchored at midday local time so the trip cannot drift onto the day
        before or after when it is converted to UTC for storage.
        """
        tz = tz or self.timezone()
        anchor = _dt.datetime.combine(on_date, _dt.time(12, 0), tzinfo=tz)
        return self.add_manual_entry(
            project_id,
            EntryKind.WORK,
            started_at=anchor,
            ended_at=anchor,
            description=description,
            travel=travel,
            now=now,
            reason="travel logged",
        )

    #: Fields ``update_entry`` will change. Everything else is structural.
    _EDITABLE = {
        "project_id",
        "task_id",
        "description",
        "software_name",
        "started_at",
        "ended_at",
        "km_travelled",
        "odo_start",
        "odo_end",
        "trip_from",
        "trip_to",
        "trip_purpose",
    }

    def update_entry(
        self,
        entry_id: int,
        *,
        reason: str = "edited",
        now: _dt.datetime | None = None,
        **fields,
    ) -> None:
        """Change a saved entry, recording what it looked like beforehand."""
        now = now or utc_now()
        current = self.get_entry(entry_id)
        if current is None:
            raise ValidationError("That entry no longer exists.")

        updates = {key: value for key, value in fields.items() if key in self._EDITABLE}
        if not updates:
            return

        start = updates.get("started_at", current.started_at)
        end = updates.get("ended_at", current.ended_at)
        if isinstance(start, _dt.datetime) and isinstance(end, _dt.datetime):
            if end < start:
                raise ValidationError(
                    "The end time is before the start time. Please check them."
                )

        if "odo_start" in updates or "odo_end" in updates or "km_travelled" in updates:
            merged = TravelDetail(
                km_travelled=updates.get("km_travelled", current.travel.km_travelled),
                odo_start=updates.get("odo_start", current.travel.odo_start),
                odo_end=updates.get("odo_end", current.travel.odo_end),
            )
            updates["km_travelled"] = resolve_travel_km(merged)

        kind = current.kind
        if "software_name" in updates and kind is EntryKind.WORK:
            raise ValidationError(
                "A package name belongs on a software entry, not a work entry."
            )

        columns: dict[str, object] = {}
        for key, value in updates.items():
            if key in {"started_at", "ended_at"} and isinstance(value, _dt.datetime):
                columns[key] = to_iso(value)
            elif key in {"km_travelled", "odo_start", "odo_end"}:
                columns[key] = _dec_text(value)
            elif key in {"trip_from", "trip_to", "trip_purpose", "software_name"}:
                columns[key] = _clean(value)
            else:
                columns[key] = value

        assignments = ", ".join(f"{key} = ?" for key in columns)
        with transaction(self.conn):
            before = self._snapshot(entry_id)
            self.conn.execute(
                f"UPDATE time_entries SET {assignments}, edited = 1, updated_at = ? "
                "WHERE id = ?",
                (*columns.values(), to_iso(now), entry_id),
            )
            self._recompute_duration(entry_id, now=now)
            self._write_audit(entry_id, before, self._snapshot(entry_id), reason, now)

    def soft_delete_entry(
        self, entry_id: int, reason: str = "deleted", now: _dt.datetime | None = None
    ) -> None:
        """Flag an entry as deleted. The row itself is never removed."""
        now = now or utc_now()
        with transaction(self.conn):
            before = self._snapshot(entry_id)
            if before is None:
                raise ValidationError("That entry no longer exists.")
            self.conn.execute(
                "UPDATE time_entries SET deleted_at = ?, is_running = 0, "
                "updated_at = ? WHERE id = ?",
                (to_iso(now), to_iso(now), entry_id),
            )
            self._write_audit(entry_id, before, self._snapshot(entry_id), reason, now)

    def restore_entry(self, entry_id: int, now: _dt.datetime | None = None) -> None:
        now = now or utc_now()
        with transaction(self.conn):
            before = self._snapshot(entry_id)
            if before is None:
                raise ValidationError("That entry no longer exists.")
            self.conn.execute(
                "UPDATE time_entries SET deleted_at = NULL, updated_at = ? WHERE id = ?",
                (to_iso(now), entry_id),
            )
            self._write_audit(
                entry_id, before, self._snapshot(entry_id), "restored", now
            )

    # -- idle and crash recovery -----------------------------------------

    def record_idle_decision(
        self,
        entry_id: int,
        window: Interval,
        adjustment,
        now: _dt.datetime | None = None,
    ) -> int | None:
        """Apply the user's answer to an idle prompt and log the decision.

        Returns the id of the spin-off entry when the user chose to keep the
        idle stretch as a separate entry, otherwise ``None``. Nothing is ever
        discarded without an explicit choice, and every choice lands in the
        audit table.
        """
        now = now or utc_now()
        entry = self.get_entry(entry_id)
        if entry is None:
            raise ValidationError("That entry no longer exists.")

        spin_off_id: int | None = None
        with transaction(self.conn):
            before = self._snapshot(entry_id)
            for pause in adjustment.pauses:
                self.conn.execute(
                    """
                    INSERT INTO entry_pauses (entry_id, paused_at, resumed_at,
                                              reason, created_at)
                    VALUES (?, ?, ?, 'idle', ?)
                    """,
                    (entry_id, to_iso(pause.start), to_iso(pause.end), to_iso(now)),
                )
            if adjustment.pauses:
                self.conn.execute(
                    "UPDATE time_entries SET updated_at = ? WHERE id = ?",
                    (to_iso(now), entry_id),
                )
                self._recompute_duration(entry_id, now=now)
            self._write_audit(
                entry_id, before, self._snapshot(entry_id), adjustment.note, now
            )

        if adjustment.spin_off is not None:
            spin_off_id = self.add_manual_entry(
                entry.project_id,
                entry.kind,
                started_at=adjustment.spin_off.start,
                ended_at=adjustment.spin_off.end,
                task_id=entry.task_id,
                description="Away from the desk",
                source=EntrySource.RECOVERED,
                now=now,
                reason=f"idle time split off from entry {entry_id}",
            )
        return spin_off_id

    def recover_entry(
        self,
        entry_id: int,
        decision,
        *,
        ended_at: _dt.datetime | None = None,
        now: _dt.datetime | None = None,
    ) -> None:
        """Resolve a timer that was left running when the app died.

        ``keep``    - close it at its last heartbeat, which is the last moment
                      we can honestly say the work was happening.
        ``discard`` - soft-delete it; the row survives for the audit trail.
        ``edit``    - close it at a time the user supplies.
        """
        from app.core.models import RecoveryDecision

        now = now or utc_now()
        entry = self.get_entry(entry_id)
        if entry is None:
            raise ValidationError("That entry no longer exists.")
        decision = RecoveryDecision(decision)

        if decision is RecoveryDecision.DISCARD:
            self.soft_delete_entry(
                entry_id, reason="recovery: discarded by user", now=now
            )
            return

        if decision is RecoveryDecision.EDIT:
            if ended_at is None:
                raise ValidationError("Choose the time the work actually finished.")
            end = ended_at
            reason = "recovery: end time set by user"
        else:
            end = entry.heartbeat_at or entry.started_at
            reason = "recovery: closed at last heartbeat"

        if end < entry.started_at:
            raise ValidationError(
                "The end time cannot be before the time the timer started."
            )

        with transaction(self.conn):
            before = self._snapshot(entry_id)
            self.conn.execute(
                "UPDATE entry_pauses SET resumed_at = ? "
                "WHERE entry_id = ? AND resumed_at IS NULL",
                (to_iso(end), entry_id),
            )
            self.conn.execute(
                """
                UPDATE time_entries
                SET is_running = 0, ended_at = ?, source = 'recovered', updated_at = ?
                WHERE id = ?
                """,
                (to_iso(end), to_iso(now), entry_id),
            )
            self._recompute_duration(entry_id, now=now)
            self._write_audit(entry_id, before, self._snapshot(entry_id), reason, now)

    # -- daily notes, ticks and submissions -------------------------------

    def get_daily_note(self, project_id: int, day: _dt.date) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM daily_notes WHERE project_id = ? AND date = ?",
            (project_id, date_to_iso(day)),
        ).fetchone()

    def _upsert_daily_note(self, project_id: int, day: _dt.date, **columns) -> None:
        stamp = to_iso(utc_now())
        assignments = ", ".join(f"{key} = excluded.{key}" for key in columns)
        keys = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        with transaction(self.conn):
            self.conn.execute(
                f"""
                INSERT INTO daily_notes (project_id, date, updated_at, {keys})
                VALUES (?, ?, ?, {placeholders})
                ON CONFLICT (project_id, date) DO UPDATE
                SET updated_at = excluded.updated_at, {assignments}
                """,
                (project_id, date_to_iso(day), stamp, *columns.values()),
            )

    def set_description_override(
        self, project_id: int, day: _dt.date, text: str | None
    ) -> None:
        """The narrative the user writes for the intranet, replacing the
        auto-generated one for that project and date."""
        self._upsert_daily_note(
            project_id, day, description_override=(text or "").strip() or None
        )

    def set_ticked(
        self, project_id: int, day: _dt.date, ticked: bool, now: _dt.datetime | None = None
    ) -> None:
        """Mark a date as entered on the intranet. This is the tick-off column."""
        self._upsert_daily_note(
            project_id,
            day,
            ticked_at=to_iso(now or utc_now()) if ticked else None,
        )

    def daily_notes_for(
        self, project_id: int | None, start: _dt.date, end: _dt.date
    ) -> dict[tuple[int, _dt.date], sqlite3.Row]:
        clauses = ["date BETWEEN ? AND ?"]
        params: list = [date_to_iso(start), date_to_iso(end)]
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        rows = self.conn.execute(
            f"SELECT * FROM daily_notes WHERE {' AND '.join(clauses)}", params
        )
        return {(int(row["project_id"]), date_from_iso(row["date"])): row for row in rows}

    def mark_submitted(
        self,
        project_id: int,
        period_start: _dt.date,
        period_end: _dt.date,
        note: str = "",
        now: _dt.datetime | None = None,
    ) -> None:
        stamp = to_iso(now or utc_now())
        with transaction(self.conn):
            self.conn.execute(
                """
                INSERT INTO submissions (project_id, period_start, period_end,
                                         submitted_at, note)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (project_id, period_start, period_end) DO UPDATE
                SET submitted_at = excluded.submitted_at, note = excluded.note
                """,
                (
                    project_id,
                    date_to_iso(period_start),
                    date_to_iso(period_end),
                    stamp,
                    note,
                ),
            )

    def unmark_submitted(
        self, project_id: int, period_start: _dt.date, period_end: _dt.date
    ) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "DELETE FROM submissions WHERE project_id = ? AND period_start = ? "
                "AND period_end = ?",
                (project_id, date_to_iso(period_start), date_to_iso(period_end)),
            )

    def is_submitted(
        self, project_id: int, period_start: _dt.date, period_end: _dt.date
    ) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM submissions WHERE project_id = ? AND period_start = ? "
            "AND period_end = ?",
            (project_id, date_to_iso(period_start), date_to_iso(period_end)),
        ).fetchone()
        return row is not None

    def last_submission(self, project_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM submissions WHERE project_id = ?
            ORDER BY period_end DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()

    def list_submissions(self, project_id: int | None = None) -> list[sqlite3.Row]:
        if project_id is None:
            return list(
                self.conn.execute("SELECT * FROM submissions ORDER BY period_end DESC")
            )
        return list(
            self.conn.execute(
                "SELECT * FROM submissions WHERE project_id = ? ORDER BY period_end DESC",
                (project_id,),
            )
        )
