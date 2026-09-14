"""Schema, migrations and the database-level guarantees."""

from __future__ import annotations

import sqlite3

import pytest

from app.db.connection import connect, transaction
from app.db.schema import (
    DEFAULT_SETTINGS,
    MIGRATIONS,
    SCHEMA_VERSION,
    current_version,
    initialise,
    migrate,
    split_statements,
)
from app.core.timeutil import to_iso, utc_now


class TestMigrations:
    def test_a_fresh_database_lands_on_the_current_version(self, conn):
        assert current_version(conn) == SCHEMA_VERSION

    def test_migrating_twice_changes_nothing(self, conn):
        before = current_version(conn)
        assert migrate(conn) == before
        rows = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
        assert rows["n"] == len(MIGRATIONS)

    def test_migrations_are_numbered_forward_only(self):
        versions = [version for version, _ in MIGRATIONS]
        assert versions == sorted(versions)
        assert len(set(versions)) == len(versions)
        assert versions[0] == 1
        assert versions[-1] == SCHEMA_VERSION

    def test_no_migration_destroys_data(self):
        """A released migration may add, but never drop, a table or column."""
        for _, sql in MIGRATIONS:
            upper = sql.upper()
            assert "DROP TABLE" not in upper
            assert "DROP COLUMN" not in upper

    def test_a_failing_step_leaves_the_version_behind(self, tmp_path):
        """Each step is atomic: a broken step must not half-apply."""
        path = tmp_path / "broken.db"
        conn = connect(path)
        initialise(conn)
        broken = [(SCHEMA_VERSION + 1, "CREATE TABLE ok_one (id INTEGER); "
                                       "CREATE TABLE bad ( THIS IS NOT SQL );")]
        import app.db.schema as schema

        original = schema.MIGRATIONS
        schema.MIGRATIONS = original + broken
        try:
            with pytest.raises(sqlite3.Error):
                migrate(conn)
        finally:
            schema.MIGRATIONS = original

        assert current_version(conn) == SCHEMA_VERSION
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "ok_one" not in tables  # the good half was rolled back too
        conn.close()

    def test_the_statement_splitter_respects_quoted_semicolons(self):
        statements = split_statements(
            "CREATE TABLE a (x TEXT DEFAULT 'has ; inside'); CREATE TABLE b (y TEXT);"
        )
        assert len(statements) == 2
        assert "has ; inside" in statements[0]

    def test_the_statement_splitter_drops_comments(self):
        statements = split_statements("-- a comment\nCREATE TABLE a (x TEXT);")
        assert len(statements) == 1
        assert statements[0].startswith("CREATE TABLE")


class TestPragmas:
    def test_write_ahead_logging_is_on(self, tmp_path):
        conn = connect(tmp_path / "wal.db")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        conn.close()

    def test_foreign_keys_are_enforced(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks (project_id, name, status, created_at) "
                "VALUES (999, 'orphan', 'active', ?)",
                (to_iso(utc_now()),),
            )

    def test_a_busy_timeout_is_set(self, conn):
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0


class TestConstraints:
    def test_only_one_work_timer_can_run_at_a_time(self, repo, project):
        """Enforced by the database, not just by the GUI."""
        stamp = to_iso(utc_now())
        repo.start_timer(project)
        with pytest.raises(sqlite3.IntegrityError):
            repo.conn.execute(
                """
                INSERT INTO time_entries (project_id, kind, started_at, is_running,
                                          source, created_at, updated_at)
                VALUES (?, 'work', ?, 1, 'timer', ?, ?)
                """,
                (project, stamp, stamp, stamp),
            )

    def test_one_work_and_one_software_timer_may_run_together(self, repo, project):
        repo.start_timer(project)
        repo.start_timer(project, "software", software_name="PLAXIS 2D")
        assert len(repo.running_entries()) == 2

    def test_a_work_entry_cannot_carry_a_package_name(self, conn):
        stamp = to_iso(utc_now())
        conn.execute(
            "INSERT INTO projects (name, period_type, status, notes, created_at) "
            "VALUES ('P', 'calendar_month', 'active', '', ?)",
            (stamp,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO time_entries (project_id, kind, software_name, started_at,
                                          source, created_at, updated_at)
                VALUES (1, 'work', 'PLAXIS', ?, 'timer', ?, ?)
                """,
                (stamp, stamp, stamp),
            )

    def test_an_unknown_kind_is_rejected(self, conn):
        stamp = to_iso(utc_now())
        conn.execute(
            "INSERT INTO projects (name, period_type, status, notes, created_at) "
            "VALUES ('P', 'calendar_month', 'active', '', ?)",
            (stamp,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO time_entries (project_id, kind, started_at, source, "
                "created_at, updated_at) VALUES (1, 'travel', ?, 'timer', ?, ?)",
                (stamp, stamp, stamp),
            )

    def test_a_negative_duration_is_rejected(self, conn):
        stamp = to_iso(utc_now())
        conn.execute(
            "INSERT INTO projects (name, period_type, status, notes, created_at) "
            "VALUES ('P', 'calendar_month', 'active', '', ?)",
            (stamp,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO time_entries (project_id, kind, started_at, "
                "duration_seconds, source, created_at, updated_at) "
                "VALUES (1, 'work', ?, -60, 'timer', ?, ?)",
                (stamp, stamp, stamp),
            )


class TestSettingsSeed:
    def test_every_default_is_present(self, conn):
        """Uses a bare connection: the ``repo`` fixture pins the timezone."""
        from app.db.repository import Repository

        bare = Repository(conn)
        for key, value in DEFAULT_SETTINGS.items():
            assert bare.get_setting(key) == value

    def test_reseeding_does_not_overwrite_a_users_choice(self, conn):
        conn.execute(
            "UPDATE settings SET value = '0.1' WHERE key = 'rounding.increment'"
        )
        initialise(conn)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'rounding.increment'"
        ).fetchone()
        assert row["value"] == "0.1"


class TestTransactions:
    def test_a_failing_block_rolls_everything_back(self, conn):
        stamp = to_iso(utc_now())
        with pytest.raises(RuntimeError):
            with transaction(conn):
                conn.execute(
                    "INSERT INTO projects (name, period_type, status, notes, created_at)"
                    " VALUES ('Half written', 'calendar_month', 'active', '', ?)",
                    (stamp,),
                )
                raise RuntimeError("something went wrong half way")
        assert conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"] == 0
