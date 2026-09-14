"""Shared fixtures.

Every test builds its own in-memory database, so tests never see each
other's data and the suite leaves nothing behind on disk.
"""

from __future__ import annotations

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
