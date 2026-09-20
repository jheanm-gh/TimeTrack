"""Shared fixtures.

Every test builds its own in-memory database, so tests never see each
other's data and the suite leaves nothing behind on disk.
"""

from __future__ import annotations

import os

# Must happen before anything imports PySide6: it tells Qt to render into
# memory instead of looking for a display, so `pytest` works unchanged on a
# build server or over SSH.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import datetime as _dt
from zoneinfo import ZoneInfo

import pytest

from app.db.connection import connect
from app.db.repository import Repository
from app.db.schema import initialise

#: The user's zone. Tests pin it explicitly rather than using the machine's,
#: so the suite gives the same answers on any developer's laptop.
SAST = ZoneInfo("Africa/Johannesburg")
UTC = _dt.timezone.utc


@pytest.fixture
def conn():
    connection = connect(":memory:")
    initialise(connection)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn) -> Repository:
    repository = Repository(conn)
    repository.set_setting("display.timezone", "Africa/Johannesburg")
    return repository


@pytest.fixture
def tz():
    return SAST


@pytest.fixture
def project(repo: Repository) -> int:
    return repo.add_project("Kloof Tailings Dam")


def sast(year, month, day, hour=0, minute=0, second=0) -> _dt.datetime:
    """Build an aware datetime in the user's local zone."""
    return _dt.datetime(year, month, day, hour, minute, second, tzinfo=SAST)


def utc(year, month, day, hour=0, minute=0, second=0) -> _dt.datetime:
    return _dt.datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# --------------------------------------------------------------------------
# GUI fixtures
#
# The widget tests run against Qt's "offscreen" platform, so they need no
# display and can run on a build server. One QApplication is shared by the
# whole session because Qt refuses to create a second one.
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6", reason="PySide6 is not installed")
    from PySide6.QtWidgets import QApplication

    existing = QApplication.instance()
    if existing is not None:
        yield existing
        return
    application = QApplication([])
    yield application
    application.quit()


@pytest.fixture
def window(qapp, repo, tmp_path, monkeypatch):
    """A real MainWindow wired to a throwaway database."""
    from PySide6.QtCore import QSettings

    from app.ui.main_window import MainWindow

    # Keep window geometry out of the developer's real settings store.
    monkeypatch.setattr(
        QSettings, "value", lambda self, *args, **kwargs: None, raising=False
    )
    monkeypatch.setattr(
        QSettings, "setValue", lambda self, *args, **kwargs: None, raising=False
    )

    # Point the workbook at a throwaway folder and keep saves inline. Without
    # this the tests would write a real spreadsheet into the developer's
    # Documents folder and spawn worker threads against the live database.
    repo.set_setting("workbook.path", str(tmp_path / "workbook"))

    main = MainWindow(repo)
    main.autosave.synchronous = True
    main.hide()
    yield main
    main.timers.shutdown()
    main.autosave.shutdown()
    main.tray.hide()
    # Deliberately not close(): that runs closeEvent, which opens the modal
    # "what should happen to your running timers" dialog and waits forever
    # for an answer nobody is there to give.
    main.hide()
    main.deleteLater()
    # deleteLater() only queues the deletion, and processEvents() does NOT
    # deliver DeferredDelete outside a running event loop - those are held
    # until the loop that posted them returns, and there is no loop here.
    # Without this every window ever created stays alive, and since applying
    # a stylesheet re-polishes all of them, a theme switch late in the suite
    # took nineteen seconds.
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
