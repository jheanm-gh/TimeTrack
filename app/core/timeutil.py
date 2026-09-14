"""Timezone and formatting helpers.

Storage rule for the whole application: every instant is stored as an
ISO-8601 string in UTC with an explicit ``+00:00`` offset, and is displayed
converted to the user's local timezone. The local zone is discovered from
the operating system - it is never hardcoded - but can be overridden by a
setting for people who travel or whose laptop clock is set oddly.
"""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = _dt.timezone.utc

#: Offsets are written with this many characters, e.g. ``+00:00``.
ISO_FORMAT_NOTE = "UTC ISO-8601 with explicit offset"


def utc_now() -> _dt.datetime:
    """The current instant, as a timezone-aware UTC datetime."""
    return _dt.datetime.now(tz=UTC)


def system_timezone() -> _dt.tzinfo:
    """The operating system's local timezone, as a tzinfo.

    Falls back to a fixed-offset zone derived from the OS if no IANA name
    is available (which is the normal situation on Windows).
    """
    local = _dt.datetime.now().astimezone().tzinfo
    return local if local is not None else UTC


def resolve_timezone(name: str | None = None) -> _dt.tzinfo:
    """Return a tzinfo for ``name``, or the system zone when not given.

    An unknown or unusable name falls back to the system zone rather than
    raising: a bad setting must never stop the app from opening.
    """
    if not name or name.strip().lower() in {"", "system", "local", "auto"}:
        return system_timezone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return system_timezone()


def ensure_aware(value: _dt.datetime) -> _dt.datetime:
    """Reject naive datetimes loudly.

    A naive datetime is always a bug in this codebase - it means an instant
    was created without saying which zone it belongs to, and that is exactly
    how hours go missing across a DST boundary or a timezone change.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "naive datetime is not allowed; attach a timezone before storing"
        )
    return value


def to_utc(value: _dt.datetime) -> _dt.datetime:
    """Convert any aware datetime to UTC."""
    return ensure_aware(value).astimezone(UTC)


def to_iso(value: _dt.datetime) -> str:
    """Serialise an instant for storage: UTC, explicit offset, seconds kept."""
    return to_utc(value).isoformat(timespec="seconds")


def from_iso(value: str) -> _dt.datetime:
    """Parse a stored instant back into an aware UTC datetime."""
    parsed = _dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        # Tolerate rows written by an older build that omitted the offset.
        # They were always UTC, so say so rather than guessing local.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_local(value: _dt.datetime, tz: _dt.tzinfo) -> _dt.datetime:
    """Convert a stored instant into the display timezone."""
    return ensure_aware(value).astimezone(tz)


def local_date(value: _dt.datetime, tz: _dt.tzinfo) -> _dt.date:
    """Which calendar date an instant falls on, in the display timezone."""
    return to_local(value, tz).date()


def local_midnight(day: _dt.date, tz: _dt.tzinfo) -> _dt.datetime:
    """The first instant of ``day`` in the display timezone."""
    return _dt.datetime.combine(day, _dt.time(0, 0), tzinfo=tz)


def date_to_iso(day: _dt.date) -> str:
    """Dates are stored as plain ``YYYY-MM-DD`` - no zone, no time."""
    return day.isoformat()


def date_from_iso(value: str) -> _dt.date:
    return _dt.date.fromisoformat(value)


def format_hm(seconds: int) -> str:
    """Render a duration as ``h:mm`` (e.g. ``2:05``), rounding down to the minute.

    Negative durations should not occur, but are rendered with a leading
    minus rather than silently swallowed, so a bug is visible.
    """
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{sign}{hours}:{minutes:02d}"


def format_hms(seconds: int) -> str:
    """Render a duration as ``h:mm:ss`` - used by the live timer display."""
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{sign}{hours}:{minutes:02d}:{secs:02d}"


def format_clock(value: _dt.datetime, tz: _dt.tzinfo) -> str:
    """Render an instant as a local ``HH:MM`` wall-clock time."""
    return to_local(value, tz).strftime("%H:%M")


def day_name(day: _dt.date) -> str:
    """Short weekday name, e.g. ``Mon`` - used as the Day column."""
    return day.strftime("%a")


def is_weekday(day: _dt.date) -> bool:
    """Monday-Friday. Public holidays are not modelled; see README."""
    return day.weekday() < 5
