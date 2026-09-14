"""TimeTrack - a local, offline timesheet companion.

The package is deliberately layered:

* ``app.core``  - pure, side-effect-free calculation and time helpers.
                  Everything billable is computed here and unit tested.
* ``app.db``    - SQLite schema, migrations and the repository layer.
                  The database is the single source of truth.
* ``app.ui``    - PySide6 widgets (added in phase 2).
* ``app.export``- workbook generation (added in phase 3).

Nothing in this package makes a network call.
"""

from app.version import (
    APP_NAME,
    COMPANY_NAME,
    VERSION,
    VERSION_TUPLE,
)

__all__ = ["APP_NAME", "COMPANY_NAME", "VERSION", "VERSION_TUPLE"]
