# TimeTrack

A small, offline, single-user timesheet companion for Windows. It makes it
trivial to start a timer when work begins, and produces a spreadsheet to read
off while filling in a timesheet on a company intranet.

> **Build status: Phase 4 of 5 complete.** The application is finished and
> packages into a standalone Windows folder. Run it from source with
> `python -m app`, or build it with `.\build.ps1`. The plain-English guide
> comes next. See [Build phases](#build-phases).

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

The work lives on the `claude/new-session-lq5yxu` branch, and the repository
has no `main` branch — so a plain `git pull` downloads it without switching to
it, and leaves you looking at an empty folder. Check the branch out once:

```powershell
git fetch origin
git checkout claude/new-session-lq5yxu
```

After that, `git pull` on its own keeps you up to date.

To run it from source:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

Or skip all of that and run `.\build.cmd`, which does it for you.

On Linux or macOS, substitute `python3.12 -m venv .venv` and
`source .venv/bin/activate`.

### Run the application

```powershell
python -m app
```

### Run the tests

```powershell
python -m pytest
```

624 tests, about fourteen seconds. They cover the rounding rule exhaustively -
exact boundaries, single-second crossings, the floating-point traps described
below - plus the timer channels, idle detection, clock jumps, the widgets, and
the spreadsheet itself (opened back off disk and checked cell by cell). The
widget tests render into memory rather than onto a screen, so they need no
display.

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
| `tools/`       | Packaging helpers: icon, version resource, prune rules.       |
| `app/explain.py` | Plain-English walkthrough of the maths.                     |
| `app/services/`| The operating system: idle counter, clocks, Startup folder.    |
| `app/ui/`      | PySide6 window, tabs, dialogs and tray icon.                   |
| `app/export/`  | Workbook and CSV generation, and writing them to disk.        |

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
Settings; it is never hardcoded.

**Colour is a palette, and contrast is a measured contract.** Both themes
define the same tokens, and `theme.CONTRAST_PAIRS` lists every
text-on-background combination the interface produces. `tests/test_theme.py`
measures each against WCAG AA and fails below 4.5:1. The first version put
near-white text on a light background for disabled buttons — 1.35:1, which is
unreadable — by implementing "looks disabled" as "fade the text towards its
own background". A second test forbids hardcoded colours anywhere in
`app/ui`, because a colour written into a widget cannot follow a theme
change.

**Date formatting avoids glibc-only directives.** Python hands format
strings to the platform's C library, so `%-d` (day without a leading zero)
works on Linux and macOS and raises `ValueError` on Windows. `tests/
test_portability.py` scans the source for those directives and renders every
format the application uses, because this class of bug is invisible on the
machine it is written on.

**The time zone database is a runtime dependency.** Windows ships no IANA
database — Linux and macOS have one in `/usr/share/zoneinfo`, so
`ZoneInfo("Africa/Johannesburg")` works there and raises
`ZoneInfoNotFoundError` on Windows. The `tzdata` package supplies it as pure
Python data. It is named explicitly in `TimeTrack.spec` rather than left to
PyInstaller's hook, because that hook only adds it when the *build* runs on
Windows — which would make the package differ depending on which machine
built it.

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

**The elapsed display is driven by the monotonic clock, the stored
timestamps by the wall clock.** They fail differently: the wall clock jumps
when NTP corrects it or the laptop wakes, and the monotonic clock has no idea
what time it is. The two are anchored together and watched for drift; drift
beyond five seconds means the wall clock moved, and the anchor is reset. A
running timer therefore never leaps or counts backwards on screen.

**Pausing and the idle prompt are separate ideas.** Pause is something the
user does. The idle prompt is something the application notices - and it only
ever *asks*. It fires when the user comes back, not when they leave, because
the length of an absence is not known until it ends, and a dialog raised at an
empty desk is just a dialog waiting to be dismissed on reflex.

**The software channel is never idle-checked and never auto-stopped.** An
unattended overnight analysis is real licensed-software usage.

**Settings is a fifth tab.** The brief lists four, then refers to Settings
repeatedly - the workbook folder picker, the backups panel, the startup
toggle, the switch that reverses a "don't ask again". A tab is easier to find
again than a menu item.

**The tray icon encodes state by shape as well as colour** - a split disc for
both timers running, pause bars when paused - so it is still readable without
colour vision.

**The workbook is written atomically, and never as a partial file.** Each
save goes to a temporary file in the destination folder and is then renamed
over the target, so what is on disk is always either the previous version or
the complete new one. A crash mid-write cannot produce an unopenable
spreadsheet.

**A locked file is handled differently depending on who asked.** Excel holding
the workbook open is normal near month end. An *autosave* gives up quietly,
says so in the status bar and retries on the next cycle — it deliberately does
not scatter timestamped copies, because that is how an export folder becomes
unusable. A *manual* Save Now writes a timestamped file instead and says
plainly what it did.

**Saves run on a worker thread.** Building the spreadsheet is proportional to
how much history exists. Measured on five years of entries (8,000 records,
4,000 timesheet rows) a save took 9.5 seconds — long enough to freeze the
window twice an hour. Switching the cell formatting to named styles cut that
to about 5 seconds, and moving the work onto a worker thread with its own
read-only connection removed the freeze entirely. WAL mode is what allows that
reader to run alongside the writes the user is still making.

**Gap detection never reaches back before the first recorded entry.** On a
fresh install a sixty-day window would otherwise announce forty missing
weekdays, and a nudge that is wrong the first time it appears never gets read
again.

**Tick-off state lives on `daily_notes`.** That table is already keyed by
project and date, which is exactly the grain the tick needs, so it carries a
`ticked_at` column rather than justifying a table of its own.

**Submission deadlines** are interpreted as: a day number falls in the period's
own month if it is on or after the period end, otherwise in the month after (so
a calendar month with a 5th deadline is due on the 5th of the following month);
`last working day` means the last Monday–Friday of the month the period ends in.
Public holidays are not modelled. *This is an assumption — see below.*

## Building the Windows application

```powershell
.\build.cmd
```

Use `build.cmd`, not `build.ps1` directly. Windows refuses to run unsigned
PowerShell scripts out of the box — the execution policy on Windows 10 and 11
client editions is `Restricted` — so `.\build.ps1` fails with a security error
on a clean machine. `build.cmd` bypasses that policy **for that one command
only**; it changes no system setting and leaves the machine as it was. You can
also just double-click it in Explorer.

Switches pass straight through, for example `.\build.cmd -SkipTests`.

That is the whole build: it creates the virtual environment, installs the
pinned dependencies, runs the tests, generates the icon and the Windows
version resource, packages everything, and creates a desktop shortcut and a
Start Menu entry. The result is `dist\TimeTrack\` containing
`TimeTrack.exe`.

| Switch | What it does |
| ------ | ------------ |
| `-SkipTests` | Skip the test suite (faster; you are then packaging something unchecked) |
| `-NoShortcuts` | Do not create the desktop and Start Menu shortcuts |
| `-KeepSoftwareOpenGL` | Include Mesa's software OpenGL fallback, ~20 MB. See below |
| `-Python <path>` | Build with a specific interpreter instead of auto-detecting 3.12 |

**One folder, not one file.** A one-file executable unpacks itself into a
temporary directory every time it starts — the same behaviour self-extracting
malware packers use, and a well-known trigger for corporate antivirus
heuristics. One-folder mode skips the extraction: it starts faster and is far
less likely to be quarantined. The shortcuts mean it is still one thing to
double-click. UPX compression is off for the same reason.

**Size.** **70.1 MB across 759 files**, measured on Windows. Well under
the 100 MB target. Qt ships a great
deal a small desktop form never touches, so `tools/prune_rules.py` drops it:
the QML runtime (pulled in behind the virtual-keyboard input plugin), Qt PDF
(pulled in behind the PDF image-format plugin), Qt's own developer tools, and
image formats the application never opens. `build.ps1` prints the real figure
and warns if it exceeds 100 MB.

**If the window ever fails to appear** on a particular machine, rebuild with
`.\build.ps1 -KeepSoftwareOpenGL`. Qt Widgets paint through the raster engine
and never need OpenGL, so the software fallback is left out by default, but
that is the one exclusion that cannot be verified from here.

## If antivirus quarantines it

The application makes **no network calls of any kind** — no telemetry, no
update check, no analytics, no cloud. Nothing in `requirements.txt` is a
networking library, and the one Qt networking component used is a *local*
socket that never leaves the machine (it stops a second copy of the app
opening and fighting over the database).

If your IT team needs to allow it, give them this folder:

```
C:\Users\<your username>\...\TimeTrack\dist\TimeTrack\
```

— that is, wherever you put the project, plus `dist\TimeTrack\`. The exact
path is printed at the end of every build. Allowing the folder is enough;
they do not need to allow anything else.

The executable carries proper metadata — company, product name, description,
version — and its own icon. An unsigned binary with none of that looks more
suspicious to endpoint protection than one that says plainly what it is. It
is unsigned because code-signing certificates are bought per-organisation; if
your firm already has one, signing `TimeTrack.exe` afterwards will remove most
remaining friction.

## The spreadsheet

`TimeLog.xlsx` is rewritten from the database - every thirty minutes, a couple
of minutes after any change, on close, and on demand. A `TimeLog.csv` of the
Timesheet sheet is written beside it with a fixed column order.

| Sheet | What it is for |
| ----- | -------------- |
| `Timesheet` | Every project, one row per project per date. The sheet to read while filling in the intranet. |
| `TS — <project>` | The same figures for one project, minus the Project column. On by default, capped at 40 tabs. |
| `Raw Log` | Every individual entry, with its id. The audit trail. |
| `Travel` | The kilometre log, in SARS travel-logbook column order, with monthly, year-to-date and per-project totals. |
| `Projects` | The register: deadlines, period type, hours and kilometres to date. |
| `Summary` | Per project per month, including the raw-versus-billed rounding difference, and a grand total. |
| `Gaps` | Weekdays in the recent past with no hours recorded. |

Every tabular sheet has a frozen header, an autofilter, real Excel dates and
real numbers, and no merged cells. Dates already ticked off in Review & Submit
are greyed out by conditional formatting.

## Backups

* **Weekly workbook snapshot** to `backups\TimeLog_2026-W38.xlsx`, 52 kept.
  Due seven days after the last one rather than on a fixed weekday, so a
  fortnight away from the laptop does not skip a snapshot.
* **Daily database copy** to `backups\db\timetrack_2026-09-19.db`, 30 kept.
  Taken with SQLite's `VACUUM INTO`, never a file copy: a live WAL database is
  three files, and copying the main one alone can miss the most recent writes
  or produce something that will not open.

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
| 2     | Window, tabs, timers, tray, idle detection, crash recovery  | Done  |
| 3     | Workbook: six sheets, autosave, file locking, backups       | Done  |
| 4     | Packaging, shortcuts, startup integration                   | Done  |
| 5     | Documentation, including a plain-English `HOW-TO-USE.md`    | Done  |

Since then: light and dark themes, a measured contrast contract, and the
ability to remove an unused project from the list.

## Three defects that only appeared on Windows

The whole application was written on Linux and runs on Windows. Everything
below passed every test on the development machine and failed immediately on
the target, which is worth recording because the pattern will repeat.

1. **PowerShell refuses to run unsigned scripts.** The execution policy on
   Windows client editions is `Restricted`, so `build.ps1` failed before it
   did anything. Fixed with the `build.cmd` wrapper.
2. **Windows has no time zone database.** `ZoneInfo("Africa/Johannesburg")`
   works on Linux and raises `ZoneInfoNotFoundError` on Windows. Fixed by
   making `tzdata` a runtime dependency and naming it in the spec.
3. **`%-d` is a glibc extension.** It strips a leading zero on Linux and
   raises `ValueError` on Windows. It was used to label cutoff periods, so
   every project on a cutoff cycle would have crashed the Projects tab,
   Review & Submit and every workbook save.

`tests/test_portability.py` now scans for the third class automatically, and
`tests/test_docs.py` keeps the written guide in step with the interface.

## Licensing note

PySide6 is licensed under the LGPLv3. That is appropriate for internal,
non-distributed use of this application. Redistributing it outside the
organisation would need that licence reviewed first.
