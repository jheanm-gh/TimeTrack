"""Backups: the daily database copy and the weekly workbook snapshot."""

from __future__ import annotations

import datetime as _dt

import pytest

from app.db.connection import connect
from app.db.schema import initialise
from app.services.backup import (
    backup_database,
    backup_workbook,
    describe_backups,
    iso_week_tag,
    prune,
    workbook_backup_due,
)


class TestDatabaseBackup:
    def test_it_writes_a_complete_usable_copy(self, tmp_path, repo, project):
        """VACUUM INTO, not a file copy: a live WAL database is three files."""
        live = tmp_path / "live.db"
        conn = connect(live)
        initialise(conn)
        conn.execute(
            "INSERT INTO projects (name, period_type, status, notes, created_at) "
            "VALUES ('Kloof', 'calendar_month', 'active', '', '2026-01-01T00:00:00+00:00')"
        )

        outcome = backup_database(conn, tmp_path / "bk", _dt.date(2026, 9, 14))
        assert outcome.made
        assert outcome.path.name == "timetrack_2026-09-14.db"

        # The copy must contain the row that was only in the WAL.
        restored = connect(outcome.path)
        assert restored.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        conn.close()
        restored.close()

    def test_it_only_backs_up_once_a_day(self, tmp_path):
        conn = connect(tmp_path / "live.db")
        initialise(conn)
        first = backup_database(conn, tmp_path / "bk", _dt.date(2026, 9, 14))
        second = backup_database(conn, tmp_path / "bk", _dt.date(2026, 9, 14))
        assert first.made
        assert not second.made
        assert "Already backed up" in second.message
        conn.close()

    def test_old_copies_are_pruned(self, tmp_path):
        conn = connect(tmp_path / "live.db")
        initialise(conn)
        destination = tmp_path / "bk"
        for day in range(1, 8):
            backup_database(conn, destination, _dt.date(2026, 9, day), keep=3)
        remaining = sorted(path.name for path in destination.glob("timetrack_*.db"))
        assert len(remaining) == 3
        assert remaining[-1] == "timetrack_2026-09-07.db"  # newest kept
        conn.close()

    def test_no_partial_file_is_left_after_a_failure(self, tmp_path):
        conn = connect(tmp_path / "live.db")
        initialise(conn)
        destination = tmp_path / "bk"
        destination.mkdir()

        # A closed connection stands in for any database-level failure.
        conn.close()
        outcome = backup_database(conn, destination, _dt.date(2026, 9, 14))

        assert not outcome.made
        assert "backup failed" in outcome.message
        assert list(destination.glob("*.part")) == []
        assert list(destination.glob("*.db")) == []


class TestWeeklyWorkbookBackup:
    def test_the_week_tag_sorts_chronologically(self):
        assert iso_week_tag(_dt.date(2026, 9, 14)) == "2026-W38"
        assert iso_week_tag(_dt.date(2026, 1, 5)) == "2026-W02"
        tags = [iso_week_tag(_dt.date(2026, 1, 5) + _dt.timedelta(weeks=n)) for n in range(6)]
        assert tags == sorted(tags)

    @pytest.mark.parametrize(
        ("last", "expected"),
        [
            (None, True),
            (_dt.date(2026, 9, 13), False),
            (_dt.date(2026, 9, 8), False),
            (_dt.date(2026, 9, 7), True),
            (_dt.date(2026, 8, 20), True),
        ],
    )
    def test_when_a_backup_is_due(self, last, expected):
        assert workbook_backup_due(last, _dt.date(2026, 9, 14)) is expected

    def test_a_fortnight_away_still_produces_a_backup_on_return(self):
        """The brief is explicit: a week off must not skip the snapshot."""
        assert workbook_backup_due(_dt.date(2026, 8, 25), _dt.date(2026, 9, 14))

    def test_it_copies_the_workbook_under_the_week_tag(self, tmp_path):
        workbook = tmp_path / "TimeLog.xlsx"
        workbook.write_bytes(b"pretend spreadsheet")
        outcome = backup_workbook(workbook, tmp_path / "bk", _dt.date(2026, 9, 14))
        assert outcome.made
        assert outcome.path.name == "TimeLog_2026-W38.xlsx"
        assert outcome.path.read_bytes() == b"pretend spreadsheet"

    def test_a_missing_workbook_is_not_an_error(self, tmp_path):
        outcome = backup_workbook(tmp_path / "nothing.xlsx", tmp_path / "bk", _dt.date(2026, 9, 14))
        assert not outcome.made
        assert "no workbook" in outcome.message

    def test_a_year_of_snapshots_is_kept(self, tmp_path):
        destination = tmp_path / "bk"
        workbook = tmp_path / "TimeLog.xlsx"
        workbook.write_bytes(b"x")
        for week in range(60):
            backup_workbook(
                workbook,
                destination,
                _dt.date(2026, 1, 5) + _dt.timedelta(weeks=week),
                keep=52,
            )
        assert len(list(destination.glob("TimeLog_*.xlsx"))) == 52


class TestPruning:
    def test_it_keeps_the_newest(self, tmp_path):
        for day in range(1, 11):
            (tmp_path / f"file_{day:02d}.txt").write_text("x")
        removed = prune(tmp_path, "file_*.txt", keep=4)
        assert removed == 6
        assert sorted(p.name for p in tmp_path.glob("file_*.txt")) == [
            "file_07.txt",
            "file_08.txt",
            "file_09.txt",
            "file_10.txt",
        ]

    def test_keeping_zero_is_treated_as_keep_everything(self, tmp_path):
        (tmp_path / "file_1.txt").write_text("x")
        assert prune(tmp_path, "file_*.txt", keep=0) == 0
        assert len(list(tmp_path.glob("file_*.txt"))) == 1


class TestDescribe:
    def test_it_counts_what_is_there(self, tmp_path):
        backups = tmp_path / "bk"
        databases = backups / "db"
        databases.mkdir(parents=True)
        (backups / "TimeLog_2026-W37.xlsx").write_text("x")
        (backups / "TimeLog_2026-W38.xlsx").write_text("x")
        (databases / "timetrack_2026-09-14.db").write_text("x")

        described = describe_backups(backups, databases)
        assert described["workbook_count"] == 2
        assert described["workbook_latest"] == "TimeLog_2026-W38.xlsx"
        assert described["database_count"] == 1
