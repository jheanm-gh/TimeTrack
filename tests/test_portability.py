"""Guards against code that works on the development machine but not the
target one.

This application is written on Linux and runs on Windows, and the two differ
in ways that are invisible until the code reaches the target. Each check
here exists because the difference it covers actually broke something.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import pytest

#: Date formatting is handed to the platform's C library, so the available
#: directives differ. These are glibc and BSD extensions: they strip padding
#: nicely on Linux and macOS, and raise ValueError on Windows.
UNIX_ONLY_DIRECTIVES = {
    "%-d": "use the day number directly, e.g. f'{value.day} {value:%b}'",
    "%-m": "use value.month",
    "%-H": "use value.hour",
    "%-I": "compute the 12-hour value yourself",
    "%-j": "use value.timetuple().tm_yday",
    "%-M": "use value.minute",
    "%-S": "use value.second",
    "%-y": "use value.year % 100",
    "%-Y": "use value.year",
    "%e": "space-padded day; format the number yourself",
    "%k": "space-padded hour; format the number yourself",
    "%l": "space-padded 12-hour; format the number yourself",
    "%P": "lowercase am/pm is not available on Windows",
    "%T": "spell out %H:%M:%S",
    "%D": "spell out %m/%d/%y",
    "%F": "spell out %Y-%m-%d",
    "%R": "spell out %H:%M",
    "%s": "epoch seconds; use value.timestamp()",
}

#: The mirror image: these work only on Windows and break on Linux, so they
#: would fail in this very test run rather than in the field.
WINDOWS_ONLY_DIRECTIVES = {"%#d", "%#m", "%#H", "%#M", "%#S", "%#j", "%#y", "%#Y"}

#: f-string conversions like ``{value:%d %b}``.
FSTRING_FORMAT_SPEC = re.compile(r"\{[^{}]*:(%[^{}'\"]*)\}")
#: Explicit ``value.strftime("...")`` calls.
STRFTIME_CALL = re.compile(r"strftime\(\s*[\"']([^\"']*)[\"']")

SOURCE_DIRS = ("app", "tools")


def python_sources() -> list[Path]:
    files: list[Path] = []
    for directory in SOURCE_DIRS:
        files.extend(sorted(Path(directory).rglob("*.py")))
    return files


def date_formats_in(path: Path) -> list[tuple[int, str]]:
    """Every date format spec in a file, with its line number."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for pattern in (FSTRING_FORMAT_SPEC, STRFTIME_CALL):
            for match in pattern.finditer(line):
                found.append((number, match.group(1)))
    return found


class TestDateFormatsArePortable:
    """`%-d` on Windows raises ValueError: Invalid format string.

    It reached the Projects tab, Review & Submit and every workbook save
    before it was caught, because a Linux machine formats it happily.
    """

    def test_no_unix_only_directives_in_the_application(self):
        problems: list[str] = []
        for path in python_sources():
            for number, spec in date_formats_in(path):
                for directive, remedy in UNIX_ONLY_DIRECTIVES.items():
                    if directive in spec:
                        problems.append(
                            f"{path}:{number} uses {directive} in {spec!r} "
                            f"- {remedy}"
                        )
        assert not problems, "Date formats that fail on Windows:\n" + "\n".join(problems)

    def test_no_windows_only_directives_either(self):
        problems: list[str] = []
        for path in python_sources():
            for number, spec in date_formats_in(path):
                for directive in WINDOWS_ONLY_DIRECTIVES:
                    if directive in spec:
                        problems.append(f"{path}:{number} uses {directive} in {spec!r}")
        assert not problems, "Date formats that fail on Linux:\n" + "\n".join(problems)

    def test_every_format_the_application_uses_actually_works(self):
        """Render each spec found in the source against this platform."""
        moment = _dt.datetime(2026, 9, 14, 8, 5, 3, tzinfo=_dt.timezone.utc)
        for path in python_sources():
            for number, spec in date_formats_in(path):
                try:
                    format(moment, spec)
                except ValueError as exc:  # pragma: no cover - the failure path
                    pytest.fail(f"{path}:{number} cannot format {spec!r}: {exc}")

    def test_the_scanner_would_catch_a_regression(self):
        """Prove the guard works, so a passing run means something."""
        source = "label = f\"{start:%-d %b}\"\n"
        specs = FSTRING_FORMAT_SPEC.findall(source)
        assert specs == ["%-d %b"]
        assert any(directive in specs[0] for directive in UNIX_ONLY_DIRECTIVES)


class TestCutoffLabelsRenderEverywhere:
    """The label that actually broke, checked on whatever platform runs this."""

    @pytest.mark.parametrize(
        ("day", "cutoff", "expected"),
        [
            (_dt.date(2026, 9, 28), 26, "26 Sep – 25 Oct 2026"),
            (_dt.date(2026, 9, 14), 26, "26 Aug – 25 Sep 2026"),
            (_dt.date(2026, 1, 1), 26, "26 Dec 2025 – 25 Jan 2026"),
            (_dt.date(2026, 2, 15), 31, "31 Jan – 27 Feb 2026"),
        ],
    )
    def test_labels_read_correctly(self, day, cutoff, expected):
        from app.core.calc import cutoff_period

        assert cutoff_period(day, cutoff).label == expected

    def test_the_day_number_has_no_leading_zero(self):
        """What %-d was there for in the first place."""
        from app.core.calc import cutoff_period

        label = cutoff_period(_dt.date(2026, 3, 10), 5).label
        assert label.startswith("5 Mar")
        assert not label.startswith("05")

    def test_a_whole_year_of_labels_renders(self):
        """Every month, both branches of the label, no exceptions."""
        from app.core.calc import cutoff_period

        for month in range(1, 13):
            for cutoff in (1, 15, 26, 31):
                label = cutoff_period(_dt.date(2026, month, 15), cutoff).label
                assert "–" in label
                assert label.strip()


class TestFilePathsArePortable:
    """Paths are built with pathlib, never by joining strings with "/"."""

    def test_no_hardcoded_unix_path_separators_in_paths(self):
        suspicious = re.compile(r'Path\([^)]*"/(?:home|usr|tmp|var)/')
        problems = [
            f"{path}" for path in python_sources()
            if suspicious.search(path.read_text(encoding="utf-8"))
        ]
        assert not problems, f"Hardcoded Unix paths in: {problems}"
