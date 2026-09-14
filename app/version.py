"""Single place where the application's identity is defined.

The packaging step (phase 4) reads these to build the Windows version
resource, so an antivirus scanner sees a binary with real metadata.
"""

from __future__ import annotations

APP_NAME = "TimeTrack"
APP_DESCRIPTION = "Timesheet companion"
COMPANY_NAME = "TimeTrack"
COPYRIGHT = "Internal use only."

VERSION_TUPLE = (1, 0, 0, 0)
VERSION = ".".join(str(part) for part in VERSION_TUPLE[:3])
