"""Domain types shared by the calculation layer, the database and the GUI.

These are plain dataclasses and string enums with no behaviour beyond
validation, so they can be constructed in a test without a database.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class EntryKind(StrEnum):
    """The two independent, additive channels the brief describes.

    ``WORK`` is labour. ``SOFTWARE`` is licensed-software usage billed
    separately. Software time may overlap work time, or occur with no work
    time at all (an unattended overnight analysis), so the two are never
    treated as a subset of one another.
    """

    WORK = "work"
    SOFTWARE = "software"


class EntrySource(StrEnum):
    TIMER = "timer"
    MANUAL = "manual"
    RECOVERED = "recovered"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    ACTIVE = "active"
    DONE = "done"


class PeriodType(StrEnum):
    CALENDAR_MONTH = "calendar_month"
    CUSTOM_CUTOFF = "custom_cutoff"


class RoundingDirection(StrEnum):
    UP = "up"
    NEAREST = "nearest"
    DOWN = "down"


class IdleDecision(StrEnum):
    """What to do with a stretch of detected inactivity. Never auto-applied."""

    KEEP = "keep"
    DISCARD = "discard"
    SEPARATE = "separate"


class RecoveryDecision(StrEnum):
    """What to do with a timer that was running when the app died."""

    KEEP = "keep"
    DISCARD = "discard"
    EDIT = "edit"


#: Sentinel stored in ``projects.submission_day`` meaning "the last working
#: day of the month" rather than a fixed day number.
LAST_WORKING_DAY = "last_working_day"

#: Default character limit for a description typed into the intranet form.
#: Assumption flagged in the README - confirm against the real form.
DEFAULT_DESCRIPTION_LIMIT = 500


@dataclass(frozen=True, slots=True)
class Interval:
    """A half-open span of real time, ``[start, end)``.

    Both ends are timezone-aware. ``end`` may be ``None`` for a span that is
    still open (a running timer, or a pause that has not been resumed).

    **Both ends are normalised to UTC on construction.** This is not tidiness,
    it is correctness: Python performs *naive* subtraction and comparison when
    two aware datetimes share the same ``tzinfo`` object, so in a zone that
    observes daylight saving, ``23:30 - 00:30`` across the spring-forward
    would report three hours of work where only two were done. Converting to
    UTC here means every duration in the application is real elapsed time,
    measured once, in one place. Display converts back with
    :func:`app.core.timeutil.to_local`.
    """

    start: _dt.datetime
    end: _dt.datetime | None = None

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("Interval.start must be timezone-aware")
        object.__setattr__(self, "start", self.start.astimezone(_dt.timezone.utc))
        if self.end is not None:
            if self.end.tzinfo is None:
                raise ValueError("Interval.end must be timezone-aware")
            object.__setattr__(self, "end", self.end.astimezone(_dt.timezone.utc))
            if self.end < self.start:
                raise ValueError("Interval.end must not precede Interval.start")

    @property
    def is_open(self) -> bool:
        return self.end is None

    def closed_at(self, now: _dt.datetime) -> "Interval":
        """Return this interval with an open end closed off at ``now``."""
        if self.end is not None:
            return self
        if now is None:
            raise ValueError(
                "this interval is still open (a running timer), so measuring "
                "it needs a 'now' - pass now=... to the calculation"
            )
        return Interval(self.start, max(self.start, now))

    def seconds(self, now: _dt.datetime | None = None) -> int:
        end = self.end
        if end is None:
            if now is None:
                raise ValueError("an open interval needs a 'now' to be measured")
            end = max(self.start, now)
        return int((end - self.start).total_seconds())


@dataclass(frozen=True, slots=True)
class TravelDetail:
    """The kilometre side of an entry. Every field is optional."""

    km_travelled: Decimal | None = None
    odo_start: Decimal | None = None
    odo_end: Decimal | None = None
    trip_from: str | None = None
    trip_to: str | None = None
    trip_purpose: str | None = None

    @property
    def has_any(self) -> bool:
        return any(
            value is not None and value != ""
            for value in (
                self.km_travelled,
                self.odo_start,
                self.odo_end,
                self.trip_from,
                self.trip_to,
                self.trip_purpose,
            )
        )


@dataclass(frozen=True, slots=True)
class EntryCalc:
    """The minimum an entry needs to expose for the calculation layer.

    Deliberately not the database row: the pure functions must be callable
    from a test that never opens SQLite. ``pauses`` holds the paused spans
    that are subtracted from the wall-clock span.
    """

    project_id: int
    kind: EntryKind
    start: _dt.datetime
    end: _dt.datetime | None = None
    pauses: tuple[Interval, ...] = ()
    entry_id: int | None = None
    task_id: int | None = None
    description: str = ""
    software_name: str | None = None
    km_travelled: Decimal | None = None

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("EntryCalc.start must be timezone-aware")
        if self.end is not None and self.end.tzinfo is None:
            raise ValueError("EntryCalc.end must be timezone-aware")
        if self.kind is EntryKind.WORK and self.software_name:
            raise ValueError("software_name is only valid on a software entry")


@dataclass(frozen=True, slots=True)
class RoundingRule:
    """How raw hours become billed hours.

    Defaults match the brief: round **up** to the next quarter hour, applied
    once per project per day to that day's summed total.
    """

    increment: Decimal = Decimal("0.25")
    direction: RoundingDirection = RoundingDirection.UP
    apply_to_software: bool = True

    def for_kind(self, kind: EntryKind) -> "RoundingRule":
        """Software rounding is independently toggleable."""
        if kind is EntryKind.SOFTWARE and not self.apply_to_software:
            return RoundingRule(
                increment=Decimal("0"),
                direction=self.direction,
                apply_to_software=False,
            )
        return self


@dataclass(frozen=True, slots=True)
class DayRow:
    """One row of the Timesheet sheet: one project, one date.

    This is the shape the user reads off while typing into the intranet, so
    it carries both the raw and the billed figure for every number.
    """

    project_id: int
    date: _dt.date

    work_seconds: int = 0
    software_seconds: int = 0
    work_hours_raw: Decimal = Decimal("0.00")
    work_hours_billed: Decimal = Decimal("0.00")
    software_hours_raw: Decimal = Decimal("0.00")
    software_hours_billed: Decimal = Decimal("0.00")

    first_start: _dt.datetime | None = None
    last_end: _dt.datetime | None = None
    sessions: tuple[Interval, ...] = ()
    #: Whether ``sessions``/``first_start``/``last_end`` describe work or,
    #: on a day with software time only, software.
    sessions_kind: EntryKind = EntryKind.WORK

    software_names: tuple[str, ...] = ()
    km: Decimal = Decimal("0")
    entry_count: int = 0
    descriptions: tuple[str, ...] = ()
    entry_ids: tuple[int, ...] = ()

    @property
    def rounding_gap_hours(self) -> Decimal:
        """How much this day gained from rounding up. Shown on the Summary."""
        return (self.work_hours_billed - self.work_hours_raw) + (
            self.software_hours_billed - self.software_hours_raw
        )


@dataclass(frozen=True, slots=True)
class Period:
    """A submission period: an inclusive range of calendar dates."""

    start: _dt.date
    end: _dt.date
    label: str = ""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("period end must not precede period start")

    def contains(self, day: _dt.date) -> bool:
        return self.start <= day <= self.end

    def dates(self) -> list[_dt.date]:
        span = (self.end - self.start).days
        return [self.start + _dt.timedelta(days=offset) for offset in range(span + 1)]


@dataclass(frozen=True, slots=True)
class IdleAdjustment:
    """The outcome of answering an idle prompt.

    ``pauses`` are spans to subtract from the original entry;
    ``spin_off`` is a new entry to create when the user chose "separate".
    """

    pauses: tuple[Interval, ...] = ()
    spin_off: Interval | None = None
    note: str = ""


@dataclass(slots=True)
class ProjectCalc:
    """The project fields the calculation layer needs."""

    project_id: int
    name: str
    period_type: PeriodType = PeriodType.CALENDAR_MONTH
    period_start_day: int | None = None
    submission_day: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    default_site: str | None = None
    default_km: Decimal | None = None
    colour: str | None = None
    client: str | None = None
    project_code: str | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)
