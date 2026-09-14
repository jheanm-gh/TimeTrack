"""Explain the billing maths in plain English.

Run with ``python -m app.explain``. It works through the rounding rule using
the exact examples from the brief, then pulls a real day out of the demo
database and shows how its number was arrived at.

This exists so the arithmetic can be checked by reading the output rather
than by reading the code.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from pathlib import Path

from app.core.calc import (
    daily_rollup,
    format_sessions,
    round_seconds_to_hours,
    rollup_totals,
    seconds_to_hours,
)
from app.core.models import EntryCalc, EntryKind, RoundingRule
from app.core.timeutil import format_hm, resolve_timezone, to_local
from app.db.connection import connect
from app.db.repository import Repository

RULE = RoundingRule()
LINE = "-" * 72


def _title(text: str) -> None:
    print()
    print(text)
    print(LINE)


def _mins(count: int) -> int:
    return count * 60


def explain_the_rule() -> None:
    _title("THE RULE")
    print(
        "Hours are added up for one project on one day, and only then rounded\n"
        "UP to the next quarter hour. Individual entries are never rounded."
    )

    _title("YOUR WORKED EXAMPLE: six 10-minute calls")
    total = _mins(60)
    wrong = sum((round_seconds_to_hours(_mins(10), RULE) for _ in range(6)), Decimal("0"))
    print(f"  Six calls of 10 minutes      = {format_hm(total)}  ({total} seconds)")
    print(f"  Rounding EACH call first     = {wrong}  <- wrong, inflates the day")
    print(f"  Rounding the DAY TOTAL       = {round_seconds_to_hours(total, RULE)}  <- what TimeTrack bills")

    _title("NOW MAKE ONE CALL 11 MINUTES INSTEAD OF 10")
    total = _mins(61)
    print(f"  Five at 10 min + one at 11   = {format_hm(total)}  ({total} seconds)")
    print(f"  Billed                       = {round_seconds_to_hours(total, RULE)}")
    print("  One extra minute costs a whole quarter hour. That is the rule working.")

    _title("EXACT BOUNDARIES DO NOT MOVE")
    for minutes, note in ((180, "a day of exactly 3 hours"), (181, "3 hours and 1 minute")):
        billed = round_seconds_to_hours(_mins(minutes), RULE)
        print(f"  {note:<28} = {format_hm(_mins(minutes)):>6}  ->  billed {billed}")
    print("  3h 00m bills 3.00, NOT 3.25. Only going over a quarter moves it up.")

    _title("TWO PROJECTS ON THE SAME DAY ROUND SEPARATELY")
    tz = resolve_timezone("Africa/Johannesburg")
    day = _dt.date(2026, 9, 14)
    entries = [
        EntryCalc(
            project_id=1,
            kind=EntryKind.WORK,
            start=_dt.datetime.combine(day, _dt.time(8, 0), tzinfo=tz),
            end=_dt.datetime.combine(day, _dt.time(8, 40), tzinfo=tz),
        ),
        EntryCalc(
            project_id=2,
            kind=EntryKind.WORK,
            start=_dt.datetime.combine(day, _dt.time(9, 0), tzinfo=tz),
            end=_dt.datetime.combine(day, _dt.time(9, 50), tzinfo=tz),
        ),
    ]
    rows = daily_rollup(entries, tz, RULE)
    for row in rows:
        print(
            f"  Project {row.project_id}: {format_hm(row.work_seconds):>6} worked"
            f"  ->  billed {row.work_hours_billed}"
        )
    pooled = round_seconds_to_hours(sum(r.work_seconds for r in rows), RULE)
    print(f"  Total billed                 = {sum(r.work_hours_billed for r in rows)}")
    print(f"  If the day had been pooled   = {pooled}  <- not what happens")

    _title("A SESSION THAT CROSSES MIDNIGHT")
    entry = EntryCalc(
        project_id=1,
        kind=EntryKind.WORK,
        start=_dt.datetime.combine(day, _dt.time(22, 30), tzinfo=tz),
        end=_dt.datetime.combine(day + _dt.timedelta(days=1), _dt.time(1, 15), tzinfo=tz),
    )
    rows = daily_rollup([entry], tz, RULE)
    print(f"  One session 22:30 -> 01:15   = {format_hm(entry.end and 9900 or 0)} in total")
    for row in rows:
        print(
            f"    {row.date:%a %d %b}  {format_hm(row.work_seconds):>6} worked"
            f"  ->  billed {row.work_hours_billed}"
        )
    print(f"  The two parts add back to    = {format_hm(sum(r.work_seconds for r in rows))}")
    print("  Nothing is lost; each date simply bills its own share.")


def explain_a_real_day(db_path: Path) -> None:
    if not db_path.exists():
        print()
        print(f"(No demo database at {db_path} - run 'python -m app.seed' first.)")
        return

    conn = connect(db_path)
    repo = Repository(conn)
    tz = repo.timezone()
    rule = repo.rounding_rule()
    entries = repo.entries_for_calc()
    rows = daily_rollup(entries, tz, rule)
    names = repo.project_names()

    # Pick the busiest day in the demo: the most entries makes the clearest
    # illustration of "sum first, then round once".
    busiest = max(rows, key=lambda row: row.entry_count)

    _title("A REAL DAY FROM THE DEMO DATA")
    print(f"  Project : {names[busiest.project_id]}")
    print(f"  Date    : {busiest.date:%A %d %B %Y}")
    print()
    print("  The individual entries recorded that day:")

    same_day = [
        entry
        for entry in entries
        if entry.project_id == busiest.project_id
        and to_local(entry.start, tz).date() == busiest.date
        and entry.kind is EntryKind.WORK
    ]
    running = 0
    for entry in same_day:
        seconds = int((entry.end - entry.start).total_seconds()) if entry.end else 0
        running += seconds
        start_text = f"{to_local(entry.start, tz):%H:%M}"
        end_text = f"{to_local(entry.end, tz):%H:%M}" if entry.end else "  -  "
        print(
            f"    {start_text}-{end_text}  {format_hm(seconds):>6}"
            f"   running total {format_hm(running)}"
        )

    print()
    print(f"  Sessions        : {format_sessions(list(busiest.sessions), tz)}")
    print(f"  Day total (raw) : {format_hm(busiest.work_seconds)}"
          f"  = {seconds_to_hours(busiest.work_seconds):.4f} hours")
    print(f"  Shown as        : {busiest.work_hours_raw}   (Hours raw)")
    print(f"  BILLED          : {busiest.work_hours_billed}   <- the number you type in")
    if busiest.software_seconds:
        print(f"  Software billed : {busiest.software_hours_billed}"
              f"   ({', '.join(busiest.software_names)})")
    if busiest.km:
        print(f"  Kilometres      : {busiest.km}")

    totals = rollup_totals(rows)
    _title("THE WHOLE DEMO PERIOD")
    print(f"  Days with time recorded  : {totals['days']}")
    print(f"  Work hours actually done : {totals['work_hours_raw']}")
    print(f"  Work hours billed        : {totals['work_hours_billed']}")
    print(f"  Gained by rounding up    : {totals['work_rounding_gap']} hours")
    print(f"  Software hours billed    : {totals['software_hours_billed']}")
    print(f"  Kilometres travelled     : {totals['km']}")
    print()
    print("  That rounding figure is the one to keep an eye on - it is the")
    print("  difference between the hours worked and the hours billed, and it")
    print("  appears on the Summary sheet of the workbook.")
    conn.close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Explain the TimeTrack maths.")
    parser.add_argument("--db", default="demo/timetrack-demo.db")
    args = parser.parse_args(argv)

    print("=" * 72)
    print("TimeTrack - how the hours are worked out".center(72))
    print("=" * 72)
    explain_the_rule()
    explain_a_real_day(Path(args.db))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
