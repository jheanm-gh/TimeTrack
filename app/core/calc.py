"""Pure billing and rollup arithmetic.

Nothing here touches the database, the filesystem, the clock or the GUI.
Every value that reaches the spreadsheet is a :class:`~decimal.Decimal`,
never a float, because the rounding rule in this module is a *ceiling* and
binary floating point overshoots boundaries in exactly the way a ceiling
amplifies: ``0.7499999999`` would silently bill as a full extra quarter
hour and nobody would ever spot it in a month of rows.

The single most important rule in the application:

    Hours are summed **per project, per day**, and the *day total* is then
    rounded up to the next increment. Entries are never rounded
    individually - that inflates a day of short calls badly - and a total
    that lands exactly on a boundary stays where it is: 60 minutes bills as
    1.00 hours, not 1.25.
"""

from __future__ import annotations

import calendar
import datetime as _dt
from collections import OrderedDict
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

from app.core.models import (
    DEFAULT_DESCRIPTION_LIMIT,
    LAST_WORKING_DAY,
    DayRow,
    EntryCalc,
    EntryKind,
    IdleAdjustment,
    IdleDecision,
    Interval,
    Period,
    PeriodType,
    RoundingDirection,
    RoundingRule,
    TravelDetail,
)
from app.core.timeutil import is_weekday, local_date, local_midnight, to_local

#: Two decimal places is what the spreadsheet shows and what the intranet
#: form accepts. Aggregation never goes through these rounded values - it
#: goes through integer seconds - so this is display precision only.
CENTS = Decimal("0.01")

#: Kilometres are shown to one decimal place; odometers are whole numbers
#: in practice but are stored as Decimal so a half-kilometre survives.
KM_PLACES = Decimal("0.1")

SECONDS_PER_HOUR = 3600

#: Wide enough that ``seconds / 3600`` keeps every digit that matters when
#: it is subsequently divided by the rounding increment.
_PRECISION = 50


class ValidationError(ValueError):
    """Raised for input the user can and should fix, with a readable message.

    The GUI shows ``str(exc)`` straight to the user, so the message must be
    written in plain English, not developer shorthand.
    """


# ---------------------------------------------------------------------------
# Durations and rounding
# ---------------------------------------------------------------------------


def seconds_to_hours(seconds: int) -> Decimal:
    """Convert whole seconds to an exact Decimal number of hours.

    Kept exact (not rounded for display) because the result feeds the
    ceiling in :func:`round_hours`, where one part in a million decides
    whether a day bills 3.00 or 3.25.
    """
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return Decimal(int(seconds)) / Decimal(SECONDS_PER_HOUR)


def hours_to_seconds(hours: Decimal) -> int:
    """Convert hours back to whole seconds, rounding to the nearest second."""
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        exact = Decimal(hours) * Decimal(SECONDS_PER_HOUR)
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def quantize_hours(hours: Decimal) -> Decimal:
    """Round a figure to the two decimal places the spreadsheet displays."""
    return Decimal(hours).quantize(CENTS, rounding=ROUND_HALF_UP)


def quantize_km(km: Decimal) -> Decimal:
    return Decimal(km).quantize(KM_PLACES, rounding=ROUND_HALF_UP)


_ROUNDING_MODES = {
    RoundingDirection.UP: ROUND_CEILING,
    RoundingDirection.DOWN: ROUND_FLOOR,
    RoundingDirection.NEAREST: ROUND_HALF_UP,
}


def round_hours(raw_hours: Decimal, rule: RoundingRule) -> Decimal:
    """Apply the billing rounding rule to an **already-summed** figure.

    ``raw_hours`` must be the exact total for one project on one day. Passing
    a per-entry figure here is the bug the whole design is arranged to avoid.

    An increment of zero (or less) means "do not round", which is how the
    software channel behaves when its rounding toggle is switched off.
    """
    raw = Decimal(raw_hours)
    increment = Decimal(rule.increment)
    if increment <= 0:
        return quantize_hours(raw)

    mode = _ROUNDING_MODES[RoundingDirection(rule.direction)]
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        units = raw / increment
        # to_integral_value, not quantize: 'units' can be large and we want
        # the boundary test done on the exact quotient.
        whole_units = units.to_integral_value(rounding=mode)
        billed = whole_units * increment
    return quantize_hours(billed)


def round_seconds_to_hours(total_seconds: int, rule: RoundingRule) -> Decimal:
    """Convenience: exact seconds in, billed hours out, no float in between."""
    return round_hours(seconds_to_hours(total_seconds), rule)


# ---------------------------------------------------------------------------
# Intervals: pauses, overlap, midnight
# ---------------------------------------------------------------------------


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping / touching spans into a minimal ordered list."""
    closed = [iv for iv in intervals if iv.end is not None and iv.end > iv.start]
    if not closed:
        return []
    ordered = sorted(closed, key=lambda iv: (iv.start, iv.end))
    merged = [ordered[0]]
    for current in ordered[1:]:
        last = merged[-1]
        assert last.end is not None and current.end is not None
        if current.start <= last.end:
            if current.end > last.end:
                merged[-1] = Interval(last.start, current.end)
        else:
            merged.append(current)
    return merged


def subtract_intervals(
    span: Interval, holes: list[Interval], now: _dt.datetime | None = None
) -> list[Interval]:
    """Remove ``holes`` from ``span``, returning the remaining active pieces.

    Used to take paused time out of a timer entry. Holes outside the span are
    ignored; holes that are still open (a timer paused right now) are closed
    off at ``now``.
    """
    closed_span = span.closed_at(now) if span.is_open else span
    if closed_span.end is None:
        raise ValueError("an open span needs a 'now' to be measured")
    start, end = closed_span.start, closed_span.end
    if end <= start:
        return []

    clamped: list[Interval] = []
    for hole in holes:
        hole_end = hole.end if hole.end is not None else now
        if hole_end is None:
            raise ValueError("an open pause needs a 'now' to be measured")
        lo = max(hole.start, start)
        hi = min(hole_end, end)
        if hi > lo:
            clamped.append(Interval(lo, hi))

    remaining: list[Interval] = []
    cursor = start
    for hole in merge_intervals(clamped):
        assert hole.end is not None
        if hole.start > cursor:
            remaining.append(Interval(cursor, hole.start))
        cursor = max(cursor, hole.end)
    if cursor < end:
        remaining.append(Interval(cursor, end))
    return remaining


def active_intervals(
    entry: EntryCalc, now: _dt.datetime | None = None
) -> list[Interval]:
    """The spans of an entry during which the timer was actually counting."""
    span = Interval(entry.start, entry.end)
    return subtract_intervals(span, list(entry.pauses), now=now)


def entry_duration_seconds(entry: EntryCalc, now: _dt.datetime | None = None) -> int:
    """Total counted seconds for an entry, with paused time removed."""
    return sum(iv.seconds() for iv in active_intervals(entry, now=now))


def split_interval_across_days(
    interval: Interval, tz: _dt.tzinfo, now: _dt.datetime | None = None
) -> list[tuple[_dt.date, Interval]]:
    """Cut a span at local midnight so it lands on the right calendar dates.

    A session from 22:30 to 01:15 contributes 90 minutes to one date and 75
    to the next; the total is unchanged. Splitting happens in the *display*
    timezone, because that is the day the timesheet is about.
    """
    closed = interval.closed_at(now) if interval.is_open else interval
    if closed.end is None:
        raise ValueError("an open interval needs a 'now' to be split")
    start, end = closed.start, closed.end
    if end <= start:
        return []

    pieces: list[tuple[_dt.date, Interval]] = []
    cursor = start
    while cursor < end:
        day = local_date(cursor, tz)
        next_midnight = local_midnight(day + _dt.timedelta(days=1), tz)
        piece_end = min(end, next_midnight)
        if piece_end <= cursor:
            # Defensive: a zone with an unusual transition could otherwise
            # fail to advance. Step a whole day and re-evaluate.
            next_midnight = local_midnight(day + _dt.timedelta(days=2), tz)
            piece_end = min(end, next_midnight)
            if piece_end <= cursor:
                break
        pieces.append((day, Interval(cursor, piece_end)))
        cursor = piece_end
    return pieces


def find_overlaps(entries: list[EntryCalc], now: _dt.datetime | None = None) -> list[tuple[EntryCalc, EntryCalc]]:
    """Pairs of same-kind, same-project entries whose counted time overlaps.

    Overlapping work entries double-count time. The app does not silently
    repair them - the user may have meant it - but the GUI can warn.
    """
    pairs: list[tuple[EntryCalc, EntryCalc]] = []
    by_bucket: dict[tuple[int, EntryKind], list[tuple[EntryCalc, list[Interval]]]] = {}
    for entry in entries:
        bucket = (entry.project_id, EntryKind(entry.kind))
        by_bucket.setdefault(bucket, []).append(
            (entry, active_intervals(entry, now=now))
        )
    for members in by_bucket.values():
        for index, (entry_a, spans_a) in enumerate(members):
            for entry_b, spans_b in members[index + 1 :]:
                if _spans_overlap(spans_a, spans_b):
                    pairs.append((entry_a, entry_b))
    return pairs


def _spans_overlap(left: list[Interval], right: list[Interval]) -> bool:
    for a in left:
        for b in right:
            assert a.end is not None and b.end is not None
            if a.start < b.end and b.start < a.end:
                return True
    return False


def format_sessions(
    intervals: list[Interval], tz: _dt.tzinfo, separator: str = "; "
) -> str:
    """Render a day's shape as ``08:15-10:30; 13:00-16:45``.

    Uses an en dash between the two times so it reads well in the sheet.
    """
    parts = []
    for iv in merge_intervals(list(intervals)):
        assert iv.end is not None
        parts.append(
            f"{to_local(iv.start, tz):%H:%M}–{to_local(iv.end, tz):%H:%M}"
        )
    return separator.join(parts)


# ---------------------------------------------------------------------------
# Travel
# ---------------------------------------------------------------------------


def km_from_odometer(odo_start: Decimal | None, odo_end: Decimal | None) -> Decimal:
    """Derive kilometres from a pair of odometer readings.

    Raises :class:`ValidationError` with a plain-English message if the
    closing reading is below the opening one, which is the error the user
    will actually make when typing them in.
    """
    if odo_start is None or odo_end is None:
        raise ValidationError(
            "Both the opening and closing odometer readings are needed to "
            "work out the kilometres."
        )
    start = Decimal(odo_start)
    end = Decimal(odo_end)
    if end < start:
        raise ValidationError(
            f"The closing odometer reading ({end}) is lower than the opening "
            f"reading ({start}). Please check the numbers."
        )
    return quantize_km(end - start)


def resolve_travel_km(travel: TravelDetail) -> Decimal | None:
    """The kilometres for an entry: from the odometer pair when both are
    present, otherwise exactly what the user typed in the km box.
    """
    if travel.odo_start is not None and travel.odo_end is not None:
        return km_from_odometer(travel.odo_start, travel.odo_end)
    if travel.km_travelled is None:
        return None
    km = Decimal(travel.km_travelled)
    if km < 0:
        raise ValidationError("Kilometres cannot be negative.")
    return quantize_km(km)


def sum_km(values: list[Decimal | None]) -> Decimal:
    total = Decimal("0")
    for value in values:
        if value is not None:
            total += Decimal(value)
    return quantize_km(total)


# ---------------------------------------------------------------------------
# Daily rollup - the Timesheet sheet
# ---------------------------------------------------------------------------


def build_description(
    descriptions: list[str], limit: int = DEFAULT_DESCRIPTION_LIMIT
) -> str:
    """Join the day's entry descriptions into one narrative, de-duplicated.

    Order is preserved (the order the work happened), blanks are dropped and
    repeats collapsed, because "Site visit" typed four times should read
    once. Truncation is marked with an ellipsis so it is never mistaken for
    the whole story.
    """
    seen: "OrderedDict[str, None]" = OrderedDict()
    for text in descriptions:
        cleaned = (text or "").strip()
        if cleaned:
            seen.setdefault(cleaned, None)
    joined = "; ".join(seen)
    if limit and len(joined) > limit:
        return joined[: max(0, limit - 1)].rstrip() + "…"
    return joined


def daily_rollup(
    entries: list[EntryCalc],
    tz: _dt.tzinfo,
    rule: RoundingRule | None = None,
    now: _dt.datetime | None = None,
    description_limit: int = DEFAULT_DESCRIPTION_LIMIT,
) -> list[DayRow]:
    """Collapse individual entries into one row per project per date.

    This is the function the whole spreadsheet is built on. The order of
    operations matters and is the rule the user stated in his own words:
    split across midnight, **sum the day**, then round the sum once.

    Kilometres are attributed to the date the trip *started* - a drive that
    ends after midnight is still that day's trip in a travel logbook.
    """
    rule = rule or RoundingRule()
    work_rule = rule.for_kind(EntryKind.WORK)
    software_rule = rule.for_kind(EntryKind.SOFTWARE)

    buckets: dict[tuple[int, _dt.date], dict] = {}

    def bucket_for(project_id: int, day: _dt.date) -> dict:
        key = (project_id, day)
        if key not in buckets:
            buckets[key] = {
                "work_seconds": 0,
                "software_seconds": 0,
                "work_spans": [],
                "software_spans": [],
                "software_names": [],
                "km": Decimal("0"),
                "descriptions": [],
                "entry_ids": [],
                "entry_count": 0,
            }
        return buckets[key]

    for entry in entries:
        kind = EntryKind(entry.kind)
        spans = active_intervals(entry, now=now)
        touched_days: set[_dt.date] = set()

        for span in spans:
            for day, piece in split_interval_across_days(span, tz, now=now):
                bucket = bucket_for(entry.project_id, day)
                seconds = piece.seconds()
                if kind is EntryKind.WORK:
                    bucket["work_seconds"] += seconds
                    bucket["work_spans"].append(piece)
                else:
                    bucket["software_seconds"] += seconds
                    bucket["software_spans"].append(piece)
                touched_days.add(day)

        # An entry with kilometres but no measurable time (a standalone trip)
        # still has to create a row, so fall back to the start date.
        start_day = local_date(entry.start, tz)
        if not touched_days:
            touched_days.add(start_day)
            bucket_for(entry.project_id, start_day)

        km = entry.km_travelled
        if km is not None:
            bucket_for(entry.project_id, start_day)["km"] += Decimal(km)

        if kind is EntryKind.SOFTWARE and entry.software_name:
            for day in touched_days:
                bucket_for(entry.project_id, day)["software_names"].append(
                    entry.software_name
                )

        for day in sorted(touched_days):
            bucket = bucket_for(entry.project_id, day)
            bucket["entry_count"] += 1
            if entry.description:
                bucket["descriptions"].append(entry.description)
            if entry.entry_id is not None:
                bucket["entry_ids"].append(entry.entry_id)

    rows: list[DayRow] = []
    for (project_id, day), bucket in sorted(buckets.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        work_seconds = bucket["work_seconds"]
        software_seconds = bucket["software_seconds"]

        # The Sessions / First Start / Last End columns describe the shape of
        # the working day. On a day with no work at all - an unattended
        # overnight analysis - they describe the software run instead, so the
        # row is never mysteriously blank.
        if bucket["work_spans"]:
            shape_spans = merge_intervals(bucket["work_spans"])
            shape_kind = EntryKind.WORK
        else:
            shape_spans = merge_intervals(bucket["software_spans"])
            shape_kind = EntryKind.SOFTWARE

        first_start = shape_spans[0].start if shape_spans else None
        last_end = shape_spans[-1].end if shape_spans else None

        rows.append(
            DayRow(
                project_id=project_id,
                date=day,
                work_seconds=work_seconds,
                software_seconds=software_seconds,
                work_hours_raw=quantize_hours(seconds_to_hours(work_seconds)),
                work_hours_billed=round_seconds_to_hours(work_seconds, work_rule),
                software_hours_raw=quantize_hours(seconds_to_hours(software_seconds)),
                software_hours_billed=round_seconds_to_hours(
                    software_seconds, software_rule
                ),
                first_start=first_start,
                last_end=last_end,
                sessions=tuple(shape_spans),
                sessions_kind=shape_kind,
                software_names=tuple(sorted(set(bucket["software_names"]))),
                km=quantize_km(bucket["km"]),
                entry_count=bucket["entry_count"],
                descriptions=tuple(bucket["descriptions"]),
                entry_ids=tuple(bucket["entry_ids"]),
            )
        )
    return rows


def rollup_totals(rows: list[DayRow]) -> dict[str, Decimal | int]:
    """Aggregate a set of day rows.

    Aggregation runs over integer seconds, never over the already-rounded
    per-row figures, so a month total is not a sum of display roundings.
    Billed totals *are* summed from the rows, because each day genuinely
    bills its own rounded figure - that difference is the whole point of the
    raw-vs-billed column on the Summary sheet.
    """
    work_seconds = sum(row.work_seconds for row in rows)
    software_seconds = sum(row.software_seconds for row in rows)
    work_billed = sum((row.work_hours_billed for row in rows), Decimal("0"))
    software_billed = sum((row.software_hours_billed for row in rows), Decimal("0"))
    work_raw = seconds_to_hours(work_seconds)
    software_raw = seconds_to_hours(software_seconds)
    return {
        "work_seconds": work_seconds,
        "software_seconds": software_seconds,
        "work_hours_raw": quantize_hours(work_raw),
        "work_hours_billed": quantize_hours(work_billed),
        "software_hours_raw": quantize_hours(software_raw),
        "software_hours_billed": quantize_hours(software_billed),
        "work_rounding_gap": quantize_hours(Decimal(work_billed) - work_raw),
        "software_rounding_gap": quantize_hours(
            Decimal(software_billed) - software_raw
        ),
        "km": sum_km([row.km for row in rows]),
        "days": len(rows),
        "entries": sum(row.entry_count for row in rows),
    }


# ---------------------------------------------------------------------------
# Periods and deadlines
# ---------------------------------------------------------------------------


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def clamp_day(year: int, month: int, day: int) -> _dt.date:
    """``day`` in that month, pulled back to the last day if it overshoots.

    A 31st cutoff in February means the 28th (or 29th), not an error.
    """
    return _dt.date(year, month, min(int(day), days_in_month(year, month)))


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def last_working_day(year: int, month: int) -> _dt.date:
    """Last Monday-Friday of the month. Public holidays are not modelled."""
    day = _dt.date(year, month, days_in_month(year, month))
    while not is_weekday(day):
        day -= _dt.timedelta(days=1)
    return day


def month_period(day: _dt.date) -> Period:
    start = _dt.date(day.year, day.month, 1)
    end = _dt.date(day.year, day.month, days_in_month(day.year, day.month))
    return Period(start, end, label=f"{start:%B %Y}")


def cutoff_period(day: _dt.date, period_start_day: int) -> Period:
    """The period containing ``day`` for a cutoff cycle such as 26th-25th."""
    this_month_start = clamp_day(day.year, day.month, period_start_day)
    if day >= this_month_start:
        start = this_month_start
        nyear, nmonth = shift_month(day.year, day.month, 1)
        end = clamp_day(nyear, nmonth, period_start_day) - _dt.timedelta(days=1)
    else:
        pyear, pmonth = shift_month(day.year, day.month, -1)
        start = clamp_day(pyear, pmonth, period_start_day)
        end = this_month_start - _dt.timedelta(days=1)
    return Period(start, end, label=_cutoff_label(start, end))


def _cutoff_label(start: _dt.date, end: _dt.date) -> str:
    if start.year == end.year:
        return f"{start:%-d %b} – {end:%-d %b %Y}"
    return f"{start:%-d %b %Y} – {end:%-d %b %Y}"


def period_for_date(
    day: _dt.date,
    period_type: PeriodType = PeriodType.CALENDAR_MONTH,
    period_start_day: int | None = None,
) -> Period:
    """The submission period a given date falls into, for one project."""
    if PeriodType(period_type) is PeriodType.CUSTOM_CUTOFF:
        if not period_start_day:
            raise ValidationError(
                "This project uses a cutoff cycle but no start day is set. "
                "Set the day the period starts on (for example 26) in the "
                "project's settings."
            )
        return cutoff_period(day, int(period_start_day))
    return month_period(day)


def next_period(period: Period, period_type: PeriodType, period_start_day: int | None = None) -> Period:
    return period_for_date(
        period.end + _dt.timedelta(days=1), period_type, period_start_day
    )


def previous_period(period: Period, period_type: PeriodType, period_start_day: int | None = None) -> Period:
    return period_for_date(
        period.start - _dt.timedelta(days=1), period_type, period_start_day
    )


def submission_deadline(period_end: _dt.date, submission_day: str | int | None) -> _dt.date | None:
    """When the timesheet covering a period is due.

    * ``"last_working_day"`` - the last Monday-Friday of the month the period
      ends in, i.e. file it before the month is out.
    * a day number - that day of the month, in the period's own month if it
      still falls on or after the period ends, otherwise in the month after.
      So a calendar month with a 5th deadline is due on the 5th of the
      following month, which is how a monthly timesheet normally works.

    Returns ``None`` when the project has no deadline set.
    """
    if submission_day is None or submission_day == "":
        return None
    if str(submission_day) == LAST_WORKING_DAY:
        return last_working_day(period_end.year, period_end.month)

    try:
        day_number = int(submission_day)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"'{submission_day}' is not a valid submission day. Use a day "
            "number from 1 to 31, or the last working day of the month."
        ) from exc
    if not 1 <= day_number <= 31:
        raise ValidationError(
            "The submission day must be between 1 and 31, or the last "
            "working day of the month."
        )

    candidate = clamp_day(period_end.year, period_end.month, day_number)
    if candidate >= period_end:
        return candidate
    year, month = shift_month(period_end.year, period_end.month, 1)
    return clamp_day(year, month, day_number)


def days_until(deadline: _dt.date | None, today: _dt.date) -> int | None:
    if deadline is None:
        return None
    return (deadline - today).days


# ---------------------------------------------------------------------------
# Gaps - the catch-up nudge
# ---------------------------------------------------------------------------


def weekday_gaps(
    recorded_dates: set[_dt.date],
    start: _dt.date,
    end: _dt.date,
    include_today: bool = False,
    today: _dt.date | None = None,
) -> list[_dt.date]:
    """Weekdays in a window with no recorded time at all.

    Today is excluded by default - the day is not over yet, and nagging
    about it every morning would train the user to dismiss the banner.
    """
    if end < start:
        return []
    gaps: list[_dt.date] = []
    day = start
    while day <= end:
        if is_weekday(day) and day not in recorded_dates:
            if include_today or today is None or day != today:
                gaps.append(day)
        day += _dt.timedelta(days=1)
    return gaps


def recent_window(today: _dt.date, days: int) -> tuple[_dt.date, _dt.date]:
    """The ``days``-long window ending yesterday, used by the nudge and Gaps."""
    end = today - _dt.timedelta(days=1)
    start = end - _dt.timedelta(days=days - 1)
    return start, end


# ---------------------------------------------------------------------------
# Idle handling
# ---------------------------------------------------------------------------


def idle_window(
    now: _dt.datetime, idle_seconds: int, entry_start: _dt.datetime
) -> Interval | None:
    """The span the user was away for, clipped to the running entry.

    Returns ``None`` if the idle period does not overlap the entry at all,
    which happens when a timer is started after the user walked away.
    """
    if idle_seconds <= 0:
        return None
    started = now - _dt.timedelta(seconds=int(idle_seconds))
    start = max(started, entry_start)
    if now <= start:
        return None
    return Interval(start, now)


def build_idle_adjustment(
    decision: IdleDecision, window: Interval, tz: _dt.tzinfo
) -> IdleAdjustment:
    """Translate the user's answer into concrete changes to the entry.

    There is deliberately no "auto" option: nothing is ever discarded on the
    user's behalf, and every decision is recorded so a later question about
    a short day has an answer.

    ``tz`` is required rather than optional because the note it produces is
    read by a human ("No activity from 14:12 to 14:59"). An Interval stores
    UTC internally, so formatting one without converting would quietly show
    the user a clock time two hours off their own.
    """
    decision = IdleDecision(decision)
    minutes = max(0, window.seconds()) // 60
    stamp = (
        f"{to_local(window.start, tz):%H:%M}–{to_local(window.end, tz):%H:%M}"
        if window.end
        else ""
    )

    if decision is IdleDecision.KEEP:
        return IdleAdjustment(note=f"Idle {stamp} ({minutes} min) kept as worked time.")
    if decision is IdleDecision.DISCARD:
        return IdleAdjustment(
            pauses=(window,),
            note=f"Idle {stamp} ({minutes} min) removed from the entry.",
        )
    return IdleAdjustment(
        pauses=(window,),
        spin_off=window,
        note=f"Idle {stamp} ({minutes} min) moved to a separate entry.",
    )


def apply_idle_adjustment(entry: EntryCalc, adjustment: IdleAdjustment) -> EntryCalc:
    """Return a copy of ``entry`` with the idle decision applied.

    Pure: the caller persists the result, this does not.
    """
    from dataclasses import replace

    if not adjustment.pauses:
        return entry
    return replace(entry, pauses=tuple(entry.pauses) + tuple(adjustment.pauses))
