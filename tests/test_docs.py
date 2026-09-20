"""Keeps the written guide honest.

HOW-TO-USE.md is for somebody who does not read code, so it quotes what is
on screen word for word. If a button is renamed and the guide is not, the
guide becomes actively misleading - worse than having none. These tests fail
when that happens.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

GUIDE = Path("HOW-TO-USE.md")
README = Path("README.md")
APP = Path("app")

#: Text the guide quotes as appearing in the application. Each must exist
#: somewhere in the interface code.
QUOTED_LABELS = [
    "Start work",
    "Start software",
    "Start last task again",
    "Add entry by hand",
    "Log a site trip",
    "Edit selected",
    "Delete selected",
    "Restore selected",
    "Add project",
    "Add several (paste a list)",
    "Add task",
    "Mark done",
    "Bring back",
    "Mark period as submitted",
    "Copy description",
    "Save spreadsheet now",
    "Minimise to tray",
    "Exit TimeTrack",
    "Show finished projects",
    "Show deleted",
    "Choose folder",
    "Start TimeTrack when I log in",
    "Ask me about time when I have been away",
    "Ask what to do when I click the X",
    "Keep the spreadsheet up to date by itself",
    "Also make one tab per project",
    "Ends after midnight",
    "Save workbook now",
    "Stop all timers",
]


def app_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(APP.rglob("*.py"))
    )


def flattened(path: Path) -> str:
    """File text with runs of whitespace collapsed.

    The guide is wrapped prose, so a button name can land across a line
    break. Comparing raw text would fail on formatting rather than on
    substance.
    """
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


class TestTheGuideMatchesTheApplication:
    @pytest.mark.parametrize("label", QUOTED_LABELS)
    def test_a_quoted_label_still_exists_in_the_app(self, label):
        assert label in app_source(), (
            f"HOW-TO-USE.md tells the user to look for {label!r}, "
            "but no such text appears in the interface any more."
        )

    @pytest.mark.parametrize("label", QUOTED_LABELS)
    def test_the_guide_actually_mentions_each_label_it_claims(self, label):
        """Guards the guard: a stale entry in the list above proves nothing."""
        assert label in flattened(GUIDE)

    def test_the_tab_names_are_right(self):
        guide = flattened(GUIDE)
        for tab in ("Today", "Projects", "Log", "Review & Submit", "Settings"):
            assert tab in guide


class TestTheGuideIsWellFormed:
    def test_every_screenshot_it_links_to_exists(self):
        guide = GUIDE.read_text(encoding="utf-8")
        missing = [
            target
            for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", guide)
            if not Path(target).exists()
        ]
        assert not missing, f"Missing images: {missing}"

    def test_every_contents_link_points_at_a_real_heading(self):
        guide = GUIDE.read_text(encoding="utf-8")
        headings = {
            re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
            for heading in re.findall(r"^#{2,3} (.+)$", guide, re.M)
        }
        broken = [
            anchor
            for anchor in re.findall(r"\]\(#([^)]+)\)", guide)
            if anchor not in headings
        ]
        assert not broken, f"Broken contents links: {broken}"

    def test_it_covers_everything_the_brief_asked_for(self):
        """Daily use, a forgotten entry, travel, the intranet list, where the
        data lives, and restoring from a backup."""
        guide = GUIDE.read_text(encoding="utf-8").lower()
        for topic in (
            "a normal day",
            "when you forget to start a timer",
            "logging a site trip",
            "filling in the intranet timesheet",
            "where your information lives",
            "backups\\db",
        ):
            assert topic.replace("\\\\", "\\") in guide, topic

    def test_it_does_not_lapse_into_jargon(self):
        """The reader has said plainly that he does not read code."""
        guide = GUIDE.read_text(encoding="utf-8")
        banned = [
            "SQLite",
            "stack trace",
            "exception",
            "repository layer",
            "instantiate",
            "boolean",
            "serialise",
            "refactor",
        ]
        found = [word for word in banned if word.lower() in guide.lower()]
        assert not found, f"Jargon in the plain-English guide: {found}"


class TestTheReadmeStaysAccurate:
    def test_it_points_at_the_build_wrapper_not_the_script(self):
        """build.ps1 alone fails on a clean Windows machine."""
        readme = README.read_text(encoding="utf-8")
        assert "build.cmd" in readme

    def test_it_records_that_the_app_makes_no_network_calls(self):
        readme = README.read_text(encoding="utf-8")
        assert "no network calls" in readme.lower()

    def test_both_documents_exist_and_are_not_stubs(self):
        assert len(GUIDE.read_text(encoding="utf-8").split()) > 1500
        assert len(README.read_text(encoding="utf-8").split()) > 1000
