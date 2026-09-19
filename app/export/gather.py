"""Turning the database into the rows each sheet needs.

Separated from the openpyxl code on purpose: these functions return plain
dataclasses, so the contents of every sheet can be checked in a test without
opening a spreadsheet. :mod:`app.export.workbook` then only has to worry
about formatting.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.calc import (
    build_description,
    daily_rollup,
    format_sessions,
    period_for_date,
    quantize_hours,
    quantize_km,
    recent_window,
    rollup_totals,
    seconds_to_hours,
    submission_deadline,
    sum_km,
    weekday_gaps,
)
from app.core.models import LAST_WORKING_DAY, DayRow, EntryKind
from app.core.timeutil import day_name, format_hm, to_local, utc_now
from app.db.repository import Repository, SavedEntry


@dataclass(frozen=True, slots=True)
class TimesheetRow:
    """One row of the sheet the user reads while filling in the intranet."""

    project_id: int
    project: str
    date: _dt.date
    day: str
    first_start: _dt.time | None
    last_end: _dt.time | None
    sessions: str
    hours_raw: Decimal
    hours_billed: Decimal
    software_raw: Decimal
    software_billed: Decimal
    software_used: str
    km: Decimal | None
    description: str
    submitted: bool


@dataclass(frozen=True, slots=True)
class RawLogRow:
    """One recorded entry, exactly as captured. The audit trail."""

    entry_id: int
    project: str
    task: str
    kind: str
    date: _dt.date
    start: _dt.time
    end: _dt.time | None
    duration_hm: str
    duration_hours: Decimal
    software_name: str
    km: Decimal | None
    odo_start: Decimal | None
    odo_end: Decimal | None
    trip_from: str
    trip_to: str
    trip_purpose: str
    description: str
    source: str
    edited: str


@dataclass(frozen=True, slots=True)
class TravelRow:
    """One trip, laid out in the order a SARS travel logbook wants it."""

    date: _dt.date
    odo_start: Decimal | None
    odo_end: Decimal | None
    km: Decimal
    destination: str
    reason: str
    trip_from: str
    project: str
    entry_id: int


@dataclass(frozen=True, slots=True)
class ProjectRow:
    name: str
    client: str
    code: str
    status: str
    submission_day: str
    period_type: str
    default_site: str
    default_km: Decimal | None
    hours_to_date: Decimal
    billed_to_date: Decimal
    km_to_date: Decimal
    last_submitted: str
    next_deadline: _dt.date | None


@dataclass(frozen=True, slots=True)
class SummaryRow:
    project: str
    month: _dt.date
    month_label: str
    hours_raw: Decimal
    hours_billed: Decimal
    rounding_difference: Decimal
    software_raw: Decimal
    software_billed: Decimal
    km: Decimal
    submitted: str


@dataclass(frozen=True, slots=True)
class GapRow:
    date: _dt.date
    day: str


@dataclass(frozen=True, slots=True)
class TravelTotals:
    by_month: list[tuple[str, Decimal]] = field(default_factory=list)
    by_project: list[tuple[str, Decimal]] = field(default_factory=list)
    year_to_date: Decimal = Decimal("0.0")
    all_time: Decimal = Decimal("0.0")


@dataclass(frozen=True, slots=True)
class WorkbookData:
    """Everything the workbook needs, already computed."""

    generated_at: _dt.datetime
    today: _dt.date
    timesheet: list[TimesheetRow]
    raw_log: list[RawLogRow]
    travel: list[TravelRow]
    travel_totals: TravelTotals
    projects: list[ProjectRow]
    summary: list[SummaryRow]
    gaps: list[GapRow]
    grand_total: SummaryRow | None
    project_names: dict[int, str]


def _time_of(value: _dt.datetime | None, tz) -> _dt.time | None:
    return to_local(value, tz).time().replace(microsecond=0) if value else None


def gather(repo: Repository, today: _dt.date | None = None) -> WorkbookData:
    """Read the whole database and compute every sheet's contents."""
    tz = repo.timezone()
    rule = repo.rounding_rule()
    now = utc_now()
    today = today or to_local(now, tz).date()

    entries = repo.list_entries(tz=tz)
    names = repo.project_names()
    day_rows = daily_rollup([entry.to_calc() for entry in entries], tz, rule, now=now)

    timesheet = _timesheet_rows(repo, day_rows, names, tz)
    raw_log = _raw_log_rows(repo, entries, names, tz)
    travel, travel_totals = _travel_rows(repo, entries, names, tz, today)
    projects = _project_rows(repo, day_rows, today)
    summary, grand_total = _summary_rows(repo, day_rows, names, rule)
    gaps = _gap_rows(repo, today)

    return WorkbookData(
        generated_at=to_local(now, tz),
        today=today,
        timesheet=timesheet,
        raw_log=raw_log,
        travel=travel,
        travel_totals=travel_totals,
        projects=projects,
        summary=summary,
        gaps=gaps,
        grand_total=grand_total,
        project_names=names,
    )


def _timesheet_rows(
    repo: Repository, day_rows: list[DayRow], names: dict[int, str], tz
) -> list[TimesheetRow]:
    if not day_rows:
        return []
    first = min(row.date for row in day_rows)
    last = max(row.date for row in day_rows)
    notes = repo.daily_notes_for(None, first, last)
    limit = repo.get_int("workbook.description_limit", 500)

    rows: list[TimesheetRow] = []
    for day_row in day_rows:
        note = notes.get((day_row.project_id, day_row.date))
        override = note["description_override"] if note else None
        rows.append(
            TimesheetRow(
                project_id=day_row.project_id,
                project=names.get(day_row.project_id, "(unknown project)"),
                date=day_row.date,
                day=day_name(day_row.date),
                first_start=_time_of(day_row.first_start, tz),
                last_end=_time_of(day_row.last_end, tz),
                sessions=format_sessions(list(day_row.sessions), tz),
                hours_raw=day_row.work_hours_raw,
                hours_billed=day_row.work_hours_billed,
                software_raw=day_row.software_hours_raw,
                software_billed=day_row.software_hours_billed,
                software_used=", ".join(day_row.software_names),
                km=day_row.km if day_row.km else None,
                description=override
                or build_description(list(day_row.descriptions), limit=limit),
                submitted=bool(note and note["ticked_at"]),
            )
        )
    # Project first, then date: the order the user works down the form.
    rows.sort(key=lambda row: (row.project.lower(), row.date))
    return rows


def _raw_log_rows(
    repo: Repository, entries: list[SavedEntry], names: dict[int, str], tz
) -> list[RawLogRow]:
    task_names: dict[int, str] = {}
    rows: list[RawLogRow] = []
    for entry in entries:
        task_name = ""
        if entry.task_id is not None:
            if entry.task_id not in task_names:
                task_row = repo.get_task(entry.task_id)
                task_names[entry.task_id] = task_row["name"] if task_row else ""
            task_name = task_names[entry.task_id]

        start_local = to_local(entry.started_at, tz)
        rows.append(
            RawLogRow(
                entry_id=entry.id,
                project=names.get(entry.project_id, "(unknown project)"),
                task=task_name,
                kind="Software" if entry.kind is EntryKind.SOFTWARE else "Work",
                date=start_local.date(),
                start=start_local.time().replace(microsecond=0),
                end=_time_of(entry.ended_at, tz),
                duration_hm=format_hm(entry.duration_seconds),
                duration_hours=quantize_hours(seconds_to_hours(entry.duration_seconds)),
                software_name=entry.software_name or "",
                km=entry.travel.km_travelled,
                odo_start=entry.travel.odo_start,
                odo_end=entry.travel.odo_end,
                trip_from=entry.travel.trip_from or "",
                trip_to=entry.travel.trip_to or "",
                trip_purpose=entry.travel.trip_purpose or "",
                description=entry.description,
                source=entry.source.value,
                edited="yes" if entry.edited else "",
            )
        )
    rows.sort(key=lambda row: (row.date, row.start, row.entry_id))
    return rows


def _travel_rows(
    repo: Repository,
    entries: list[SavedEntry],
    names: dict[int, str],
    tz,
    today: _dt.date,
) -> tuple[list[TravelRow], TravelTotals]:
    rows: list[TravelRow] = []
    for entry in entries:
        km = entry.travel.km_travelled
        if km is None or km <= 0:
            continue
        start_local = to_local(entry.started_at, tz)
        rows.append(
            TravelRow(
                date=start_local.date(),
                # Left blank rather than invented when the readings were not
                # taken: a travel logbook with made-up odometer values is
                # worse than one with gaps.
                odo_start=entry.travel.odo_start,
                odo_end=entry.travel.odo_end,
                km=quantize_km(km),
                destination=entry.travel.trip_to or "",
                reason=entry.travel.trip_purpose or entry.description or "",
                trip_from=entry.travel.trip_from or "",
                project=names.get(entry.project_id, "(unknown project)"),
                entry_id=entry.id,
            )
        )
    rows.sort(key=lambda row: (row.date, row.entry_id))

    by_month: dict[str, list[Decimal]] = {}
    by_project: dict[str, list[Decimal]] = {}
    year_to_date: list[Decimal] = []
    for row in rows:
        by_month.setdefault(f"{row.date:%B %Y}", []).append(row.km)
        by_project.setdefault(row.project, []).append(row.km)
        if row.date.year == today.year:
            year_to_date.append(row.km)

    totals = TravelTotals(
        by_month=[(label, sum_km(values)) for label, values in by_month.items()],
        by_project=sorted(
            ((label, sum_km(values)) for label, values in by_project.items()),
            key=lambda pair: pair[0].lower(),
        ),
        year_to_date=sum_km(year_to_date),
        all_time=sum_km([row.km for row in rows]),
    )
    return rows, totals


def _project_rows(
    repo: Repository, day_rows: list[DayRow], today: _dt.date
) -> list[ProjectRow]:
    rows: list[ProjectRow] = []
    for project in repo.list_projects(include_archived=True):
        mine = [row for row in day_rows if row.project_id == project["id"]]
        totals = rollup_totals(mine)
        last = repo.last_submission(project["id"])
        period = period_for_date(
            today, project["period_type"], project["period_start_day"]
        )
        submission_day = project["submission_day"]
        label = ""
        if submission_day == LAST_WORKING_DAY:
            label = "Last working day"
        elif submission_day:
            label = f"Day {submission_day}"

        rows.append(
            ProjectRow(
                name=project["name"],
                client=project["client"] or "",
                code=project["project_code"] or "",
                status="Done" if project["status"] == "archived" else "Active",
                submission_day=label,
                period_type=(
                    "Calendar month"
                    if project["period_type"] == "calendar_month"
                    else f"Cutoff from day {project['period_start_day']}"
                ),
                default_site=project["default_site"] or "",
                default_km=(
                    Decimal(project["default_km"]) if project["default_km"] else None
                ),
                hours_to_date=totals["work_hours_raw"],
                billed_to_date=totals["work_hours_billed"],
                km_to_date=totals["km"],
                last_submitted=(
                    f"{last['period_start']} to {last['period_end']}" if last else ""
                ),
                next_deadline=submission_deadline(period.end, submission_day),
            )
        )
    return rows


def _summary_rows(
    repo: Repository, day_rows: list[DayRow], names: dict[int, str], rule
) -> tuple[list[SummaryRow], SummaryRow | None]:
    """Per project per month, plus the grand total row."""
    buckets: dict[tuple[int, _dt.date], list[DayRow]] = {}
    for row in day_rows:
        month = row.date.replace(day=1)
        buckets.setdefault((row.project_id, month), []).append(row)

    submissions_by_project: dict[int, list] = {}
    for row in repo.list_submissions():
        submissions_by_project.setdefault(int(row["project_id"]), []).append(row)

    rows: list[SummaryRow] = []
    for (project_id, month), members in sorted(
        buckets.items(), key=lambda kv: (names.get(kv[0][0], "").lower(), kv[0][1])
    ):
        totals = rollup_totals(members)
        rows.append(
            SummaryRow(
                project=names.get(project_id, "(unknown project)"),
                month=month,
                month_label=f"{month:%B %Y}",
                hours_raw=totals["work_hours_raw"],
                hours_billed=totals["work_hours_billed"],
                rounding_difference=totals["work_rounding_gap"],
                software_raw=totals["software_hours_raw"],
                software_billed=totals["software_hours_billed"],
                km=totals["km"],
                submitted=_submission_state(
                    submissions_by_project.get(project_id, []), month
                ),
            )
        )

    if not rows:
        return [], None

    overall = rollup_totals(day_rows)
    grand = SummaryRow(
        project="ALL PROJECTS",
        month=_dt.date.min,
        month_label="Grand total",
        hours_raw=overall["work_hours_raw"],
        hours_billed=overall["work_hours_billed"],
        rounding_difference=overall["work_rounding_gap"],
        software_raw=overall["software_hours_raw"],
        software_billed=overall["software_hours_billed"],
        km=overall["km"],
        submitted="",
    )
    return rows, grand


def _submission_state(submissions: list, month: _dt.date) -> str:
    """Has the period covering this month been marked as filed?

    Tested against the middle of the month so a cutoff cycle - which
    straddles two calendar months - still lands in exactly one period.
    """
    if not submissions:
        return ""
    midpoint = month.replace(day=15)
    for row in submissions:
        start = _dt.date.fromisoformat(row["period_start"])
        end = _dt.date.fromisoformat(row["period_end"])
        if start <= midpoint <= end:
            return "Yes"
    return ""


def _gap_rows(repo: Repository, today: _dt.date) -> list[GapRow]:
    window = gap_window(repo, today, repo.get_int("gaps.window_days", 60))
    if window is None:
        return []
    start, end = window
    recorded = repo.recorded_dates(start, end)
    return [
        GapRow(date=day, day=day_name(day))
        for day in weekday_gaps(recorded, start, end, today=today)
    ]


def gap_window(
    repo: Repository, today: _dt.date, window_days: int
) -> tuple[_dt.date, _dt.date] | None:
    """The window the gap hunt should cover, or ``None`` if there is none.

    Never reaches back before the first entry ever recorded: days that passed
    before the application was installed are not missing time. With no
    history at all there is nothing to catch up on, so the answer is
    ``None`` rather than "every weekday for the last two months".
    """
    first = repo.first_entry_date()
    if first is None:
        return None
    start, end = recent_window(today, window_days)
    start = max(start, first)
    return (start, end) if start <= end else None
