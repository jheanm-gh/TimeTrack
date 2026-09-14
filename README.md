# TimeTrack

A small, offline, single-user timesheet companion for Windows. It makes it
trivial to start a timer when work begins, and produces a spreadsheet to read
off while filling in a timesheet on a company intranet.

> **Build status: Phase 1 of 5 complete.** The database, the billing
> calculations and the test suite are finished and passing. The window, the
> workbook and the packaged application come next. See
> [Build phases](#build-phases).

## The one rule that matters

Hours are summed **per project, per day**, and that day's total is then
rounded **up** to the next quarter hour. Entries are never rounded
individually, and a total landing exactly on a quarter stays put.

| A day of...                    | Bills |
| ------------------------------ | ----- |
| Six 10-minute calls (60 min)   | 1.00  |
| Five 10-minute + one 11 (61 m) | 1.25  |
| Exactly 3h 00m                 | 3.00  |
| 3h 01m                         | 3.25  |

Two projects on the same day each round on their own total, never on the
combined day.

To see this worked through with real numbers, run `python -m app.explain`.

## No network access

The application makes **zero** outbound network calls. There is no telemetry,
no update check, no analytics and no cloud component. Nothing in
`requirements.txt` is a networking library. This is a deliberate, testable
property: it can be stated truthfully to an IT department.

## Requirements

* Windows 10 or 11 (the calculation and database layers are cross-platform;
  idle detection and the startup shortcut are Windows-specific)
* Python 3.12 — pinned deliberately, because every dependency ships a
  prebuilt wheel for it and no compiler is needed

## Getting started

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

On Linux or macOS, substitute `python3.12 -m venv .venv` and
`source .venv/bin/activate`.

### Run the tests

```powershell
python -m pytest
```

254 tests, under a second. They cover the rounding rule exhaustively,
including exact boundaries, single-second crossings and the floating-point
traps described below.

### Build a demo database

```powershell
python -m app.seed
python -m app.explain
```

`app.seed` writes six weeks of realistic fabricated history to
`demo/timetrack-demo.db` — several projects, tasks, travel with and without
odometer readings, software runs that cross midnight, and a handful of
weekdays deliberately left blank. It is reproducible: the same command always
produces the same weeks.

`app.explain` prints the billing arithmetic in plain English and then works
through one real day from that demo database.

## How the code is arranged

| Module         | Responsibility                                               |
| -------------- | ------------------------------------------------------------ |
| `app/core/`    | Pure calculation. No database, no files, no clock, no GUI.    |
| `app/db/`      | SQLite schema, forward-only migrations, repository layer.     |
| `app/paths.py` | Where files live on disk.                                     |
| `app/seed.py`  | Demo data generator (development only).                       |
| `app/explain.py` | Plain-English walkthrough of the maths.                     |
| `app/ui/`      | PySide6 window — *phase 2*.                                   |
| `app/export/`  | Workbook generation — *phase 3*.                              |

`app/core` is pure by design: every billing figure can be unit tested without
starting a GUI, which is what makes the test suite a meaningful safety net.

## Design decisions worth knowing about

**SQLite is the only source of truth.** The spreadsheet is an output. State is
never read back out of it.

**Nothing is ever hard-deleted.** Entries carry a `deleted_at` flag and stay in
the table; every edit to a saved entry writes a before/after pair to
`entry_audit` with a reason. A wrong edit is always recoverable.

**`Decimal`, never `float`, for hours and kilometres.** This is not fussiness.
Searching for whole-second durations that total an exact quarter hour turns up
real cases where float arithmetic overshoots the boundary and the ceiling bills
an extra quarter hour — for example five entries of 3794, 5046, 1319, 4482 and
3359 seconds total exactly 5.00 hours, but sum to 5.25 in float. Those cases are
locked into `tests/test_rounding.py`. Decimal values are stored in SQLite as
**TEXT**, not REAL, so the floats cannot creep back in through the database.

**Instants are stored as UTC ISO-8601 with an explicit offset, displayed local.**
The display timezone comes from the operating system and is overridable in
settings; it is never hardcoded.

**Durations are measured in UTC.** Python performs *naive* subtraction when two
aware datetimes share a `tzinfo` object, so in a zone that observes daylight
saving, a session across the spring-forward would report three hours where two
were worked. `Interval` normalises both ends to UTC on construction so every
duration in the application is real elapsed time.

**Pause is stored as intervals, not by splitting entries.** A paused stretch
becomes a row in `entry_pauses` that is subtracted from the entry's span. One
logical session therefore stays one row, with one description and one trip
attached to it, and the Sessions column still shows the true shape of the day.
The alternative — closing and reopening entries on each pause — would scatter a
single piece of work across several rows.

**One work timer and one software timer at a time, enforced by the database.**
A partial unique index makes two running work timers impossible, so no GUI bug
can produce them. The two channels are independent and additive: software hours
may exceed work hours on a day, or occur with no work hours at all.

**Tick-off state lives on `daily_notes`.** That table is already keyed by
project and date, which is exactly the grain the tick needs, so it carries a
`ticked_at` column rather than justifying a table of its own.

**Submission deadlines** are interpreted as: a day number falls in the period's
own month if it is on or after the period end, otherwise in the month after (so
a calendar month with a 5th deadline is due on the 5th of the following month);
`last working day` means the last Monday–Friday of the month the period ends in.
Public holidays are not modelled. *This is an assumption — see below.*

## Assumptions to confirm

These are built to sensible defaults and are all configurable, but they are
guesses and should be checked against the real intranet form:

1. **Period length.** Both a calendar month and a cutoff cycle (e.g. 26th to
   25th) are supported; calendar month is the default.
2. **Description length.** Assumed to be limited to 500 characters.
3. **Rounding.** Built exactly as specified — up, to 0.25, per project per day.
   Whether that is the firm's rule or a personal habit is worth confirming.
4. **Software hours exceeding work hours.** Assumed to be legitimate. If the
   intranet rejects it, a validation warning will be added rather than changing
   the model.
5. **Kilometres.** Assumed to be for personal records only, not reported on the
   timesheet itself. The Travel sheet is laid out for a SARS travel logbook.

## Build phases

| Phase | Scope                                                       | State |
| ----- | ----------------------------------------------------------- | ----- |
| 1     | Schema, migrations, calculations, repository, tests, demo   | Done  |
| 2     | Window, tabs, timers, tray, idle detection, crash recovery  | Next  |
| 3     | Workbook: six sheets, autosave, file locking, backups       | —     |
| 4     | Packaging, shortcuts, startup integration                   | —     |
| 5     | Documentation, including a plain-English `HOW-TO-USE.md`    | —     |

## Licensing note

PySide6 is licensed under the LGPLv3. That is appropriate for internal,
non-distributed use of this application. Redistributing it outside the
organisation would need that licence reviewed first.
