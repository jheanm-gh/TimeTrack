"""Build a demo database full of realistic, fabricated history.

Run it with ``python -m app.seed`` to get a database you can click around in
without touching your real one. The data is generated from a fixed random
seed, so the same command always produces the same weeks - which makes it
useful for checking the spreadsheet as well as the window.

Nothing in here runs in the real application; it is a development and
demonstration tool only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import random
from decimal import Decimal
from pathlib import Path

from app.core.calc import daily_rollup, rollup_totals
from app.core.models import EntryKind, PeriodType, TravelDetail
from app.core.timeutil import is_weekday, resolve_timezone
from app.db.connection import connect
from app.db.repository import Repository
from app.db.schema import initialise

#: Fixed so the demo is reproducible.
SEED = 20260914

PROJECTS = [
    {
        "name": "Kloof Tailings Dam - TSF Raise",
        "client": "Sibanye Gold",
        "project_code": "GT-2451",
        "colour": "#2E6DA4",
        "submission_day": "5",
        "default_site": "Kloof TSF, Westonaria",
        "default_km": Decimal("118"),
    },
    {
        "name": "Rustenburg Slimes Dam - Stability Review",
        "client": "Impala Platinum",
        "project_code": "GT-2478",
        "colour": "#5CB85C",
        "submission_day": "5",
        "default_site": "Rustenburg No. 4 Slimes",
        "default_km": Decimal("242"),
    },
    {
        "name": "Mogalakwena Pit Slope - Phase 2",
        "client": "Anglo American",
        "project_code": "GT-2502",
        "colour": "#D9534F",
        "submission_day": "last_working_day",
        "period_type": PeriodType.CUSTOM_CUTOFF,
        "period_start_day": 26,
        "default_site": "Mogalakwena North Pit",
        "default_km": Decimal("612"),
    },
    {
        "name": "N3 Van Reenen Cut Slope - Design Check",
        "client": "SANRAL",
        "project_code": "GT-2390",
        "colour": "#F0AD4E",
        "submission_day": "10",
    },
]

ARCHIVED_PROJECT = {
    "name": "Bloemfontein Landfill Liner (closed out)",
    "client": "Mangaung Metro",
    "project_code": "GT-2201",
}

TASKS = {
    0: ["Slope stability analysis", "Piezometer data review", "Design report"],
    1: ["Site inspection", "Laboratory schedule", "Stability modelling"],
    2: ["Kinematic analysis", "Bench design", "Client workshop"],
    3: ["Cut slope sections", "Drainage assessment"],
}

SOFTWARE = ["RS2", "Slide2", "GeoStudio SLOPE/W", "Leapfrog Geo", "RS3"]

WORK_NOTES = [
    "Slope stability analysis and sensitivity runs",
    "Reviewed piezometer readings and updated phreatic surface",
    "Drafted sections for the design report",
    "Client call on the staged raise programme",
    "Checked laboratory triaxial results against design parameters",
    "Updated stability model with revised material properties",
    "Site inspection and photographic record",
    "Reviewed drilling logs and produced a geological section",
    "Internal design review with the team",
    "Prepared figures for the interim report",
]

TRIP_PURPOSES = [
    "Site inspection",
    "Piezometer readings",
    "Client meeting on site",
    "Drilling supervision",
    "Sampling and site walkover",
]


def _at(tz, day: _dt.date, hour: int, minute: int = 0) -> _dt.datetime:
    return _dt.datetime.combine(day, _dt.time(hour, minute), tzinfo=tz)


def build_demo(
    path: str | Path,
    today: _dt.date | None = None,
    timezone_name: str = "Africa/Johannesburg",
    weeks: int = 6,
) -> tuple[Repository, dict]:
    """Create a fresh demo database at ``path`` and fill it with history."""
    target = Path(path)
    if target.exists():
        target.unlink()
    for suffix in ("-wal", "-shm"):
        sibling = target.with_name(target.name + suffix)
        if sibling.exists():
            sibling.unlink()

    conn = connect(target)
    initialise(conn)
    repo = Repository(conn)
    repo.set_setting("display.timezone", timezone_name)
    tz = resolve_timezone(timezone_name)
    today = today or _dt.date.today()
    rng = random.Random(SEED)

    project_ids = [repo.add_project(**spec) for spec in PROJECTS]
    archived_id = repo.add_project(**ARCHIVED_PROJECT)

    task_ids: dict[int, list[int]] = {}
    for index, project_id in enumerate(project_ids):
        task_ids[project_id] = [
            repo.add_task(project_id, name) for name in TASKS.get(index, [])
        ]

    # A little history on the archived project, to prove archiving keeps it.
    repo.add_manual_entry(
        archived_id,
        started_at=_at(tz, today - _dt.timedelta(days=120), 9),
        ended_at=_at(tz, today - _dt.timedelta(days=120), 16),
        description="Final close-out report issued",
    )
    repo.archive_project(archived_id)

    start = today - _dt.timedelta(days=weeks * 7)
    #: Counts site trips so the demo reliably contains both styles of
    #: travel record: with odometer readings, and with a typed distance.
    #: The Travel sheet has to show blank odometer cells for the second
    #: kind rather than inventing values, so the demo must exercise it.
    trip_count = 0
    #: Weekdays deliberately left empty, so the catch-up nudge has something
    #: to find and the Gaps sheet is not blank.
    forgotten: list[_dt.date] = []

    day = start
    while day <= today:
        if not is_weekday(day):
            day += _dt.timedelta(days=1)
            continue

        # Roughly one weekday in seven is never logged - the exact problem
        # the application exists to solve.
        if rng.random() < 0.14 and day != today:
            forgotten.append(day)
            day += _dt.timedelta(days=1)
            continue

        active = rng.sample(project_ids, k=rng.choice([1, 1, 2, 2, 3]))

        # Lay the day out on a single moving cursor. Only one work timer can
        # run at a time, so the generated sessions must never overlap either -
        # otherwise the demo would show a day whose sessions and total do not
        # reconcile, which is exactly the kind of thing that destroys trust in
        # the numbers.
        cursor = _at(tz, day, rng.choice([7, 8, 8, 9]), rng.choice([0, 15, 30]))

        # A site trip happens first thing, and pushes the desk work later.
        trip_project = None
        if rng.random() < 0.18:
            trip_project = rng.choice(active)
            project = repo.get_project(trip_project)
            default_km = project["default_km"]
            km = Decimal(default_km) if default_km else Decimal(rng.randrange(40, 300))
            odo_start = Decimal(104000 + (day - start).days * 95 + rng.randrange(0, 40))
            trip_count += 1
            use_odometer = trip_count % 3 != 0
            trip_start = _at(tz, day, 6, 30)
            trip_end = trip_start + _dt.timedelta(minutes=rng.choice([45, 60, 90]))
            repo.add_manual_entry(
                trip_project,
                started_at=trip_start,
                ended_at=trip_end,
                description="Travel to site",
                travel=TravelDetail(
                    km_travelled=None if use_odometer else km,
                    odo_start=odo_start if use_odometer else None,
                    odo_end=odo_start + km if use_odometer else None,
                    trip_from="Office - Centurion",
                    trip_to=project["default_site"] or "Site",
                    trip_purpose=rng.choice(TRIP_PURPOSES),
                ),
            )
            cursor = max(cursor, trip_end + _dt.timedelta(minutes=15))

        # Build the day's sessions, then shuffle so projects interleave the
        # way a real day does rather than running in neat blocks.
        planned: list[tuple[int, int]] = []
        for project_id in active:
            for _ in range(rng.choice([1, 2, 2, 3])):
                planned.append((project_id, rng.choice([25, 40, 55, 70, 95, 130])))
        rng.shuffle(planned)

        end_of_day = _at(tz, day, 18, 30)
        for project_id, length in planned:
            session_end = cursor + _dt.timedelta(minutes=length)
            if session_end > end_of_day:
                break
            repo.add_manual_entry(
                project_id,
                started_at=cursor,
                ended_at=session_end,
                task_id=(
                    rng.choice(task_ids[project_id]) if task_ids[project_id] else None
                ),
                description=rng.choice(WORK_NOTES),
            )
            # A short break between sessions, and a longer one over lunch.
            gap = rng.choice([5, 10, 15, 20])
            if session_end.hour <= 13 <= (session_end + _dt.timedelta(minutes=gap)).hour:
                gap += rng.choice([30, 45])
            cursor = session_end + _dt.timedelta(minutes=gap)

        # Licensed software, which may overlap work time quite legitimately.
        for project_id in active:
            if rng.random() < 0.45:
                package = rng.choice(SOFTWARE)
                soft_start = _at(tz, day, rng.choice([10, 13, 15, 16]))
                soft_end = soft_start + _dt.timedelta(
                    minutes=rng.choice([45, 90, 150, 240])
                )
                repo.add_manual_entry(
                    project_id,
                    EntryKind.SOFTWARE,
                    started_at=soft_start,
                    ended_at=soft_end,
                    software_name=package,
                    description=f"{package} analysis run",
                )

        day += _dt.timedelta(days=1)

    # An unattended overnight analysis that crosses midnight: lots of
    # software hours, no work hours, split across two dates.
    overnight_day = today - _dt.timedelta(days=9)
    while not is_weekday(overnight_day):
        overnight_day -= _dt.timedelta(days=1)
    repo.add_manual_entry(
        project_ids[0],
        EntryKind.SOFTWARE,
        started_at=_at(tz, overnight_day, 18, 40),
        ended_at=_at(tz, overnight_day + _dt.timedelta(days=1), 6, 15),
        software_name="Leapfrog Geo",
        description="Consolidation analysis left running overnight",
    )

    # A late working session that also crosses midnight.
    late_day = today - _dt.timedelta(days=16)
    while not is_weekday(late_day):
        late_day -= _dt.timedelta(days=1)
    repo.add_manual_entry(
        project_ids[1],
        started_at=_at(tz, late_day, 22, 30),
        ended_at=_at(tz, late_day + _dt.timedelta(days=1), 1, 15),
        description="Report deadline - final checks and issue",
    )

    # A standalone trip: kilometres, no hours at all.
    trip_day = today - _dt.timedelta(days=4)
    while not is_weekday(trip_day):
        trip_day -= _dt.timedelta(days=1)
    repo.log_travel(
        project_ids[2],
        on_date=trip_day,
        travel=TravelDetail(
            odo_start=Decimal("106420"),
            odo_end=Decimal("107032"),
            trip_from="Office - Centurion",
            trip_to="Mogalakwena North Pit",
            trip_purpose="Client workshop on bench design",
        ),
        description="Return trip to Mogalakwena, no billable time",
    )

    # Last month, marked as submitted, with a couple of dates ticked off and
    # a hand-written narrative - so the workbook shows all three states.
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - _dt.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    repo.mark_submitted(project_ids[0], last_month_start, last_month_end)

    ticked = 0
    cursor = last_month_start
    while cursor <= last_month_end and ticked < 6:
        if is_weekday(cursor):
            repo.set_ticked(project_ids[0], cursor, True)
            ticked += 1
        cursor += _dt.timedelta(days=1)

    repo.set_description_override(
        project_ids[0],
        today - _dt.timedelta(days=2),
        "Stability analysis for the stage 4 raise; updated phreatic surface "
        "from the latest piezometer set and reissued figures 6 to 9.",
    )

    summary = _summarise(repo, forgotten)
    return repo, summary


def _summarise(repo: Repository, forgotten: list[_dt.date]) -> dict:
    tz = repo.timezone()
    entries = repo.entries_for_calc(include_deleted=False)
    rows = daily_rollup(entries, tz, repo.rounding_rule())
    totals = rollup_totals(rows)
    return {
        "projects": len(repo.list_projects(include_archived=True)),
        "entries": len(entries),
        "day_rows": len(rows),
        "forgotten_weekdays": forgotten,
        "totals": totals,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a TimeTrack demo database.")
    parser.add_argument(
        "--path",
        default="demo/timetrack-demo.db",
        help="where to write the demo database (default: demo/timetrack-demo.db)",
    )
    parser.add_argument("--weeks", type=int, default=6)
    args = parser.parse_args(argv)

    target = Path(args.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    repo, summary = build_demo(target, weeks=args.weeks)
    totals = summary["totals"]

    print(f"Demo database written to {target.resolve()}")
    print(f"  Projects            {summary['projects']} (one archived)")
    print(f"  Time entries        {summary['entries']}")
    print(f"  Timesheet rows      {summary['day_rows']} (one per project per date)")
    print(f"  Work hours   raw    {totals['work_hours_raw']}")
    print(f"  Work hours   billed {totals['work_hours_billed']}")
    print(f"  Rounding gained     {totals['work_rounding_gap']} hours")
    print(f"  Software hrs billed {totals['software_hours_billed']}")
    print(f"  Kilometres          {totals['km']}")
    print(f"  Weekdays not logged {len(summary['forgotten_weekdays'])}")
    repo.conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
