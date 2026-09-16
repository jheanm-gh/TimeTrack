"""Run-on-login, via the Startup folder.

Deliberately *not* the ``Run`` registry key. A shortcut in the Startup folder
is visible in Explorer, removable by the user without regedit, and obvious to
an IT department auditing what launches at login - all of which matter on a
managed corporate laptop. It also needs no administrator rights.

The shortcut is created by asking Windows Script Host to do it through
PowerShell, so the application needs no COM dependency (pywin32) at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.version import APP_NAME

SHORTCUT_NAME = f"{APP_NAME}.lnk"

#: Passed to the executable when the setting says "start hidden in the tray".
TRAY_ARGUMENT = "--tray"


def is_supported() -> bool:
    return sys.platform.startswith("win")


def startup_dir() -> Path:
    """``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup``."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def launch_target() -> tuple[str, str]:
    """What the shortcut should point at: ``(target, arguments)``.

    A packaged build points at the executable directly. Running from source
    points at the interpreter with ``-m app``, so the toggle is still usable
    during development.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    return sys.executable, "-m app"


def is_enabled() -> bool:
    return shortcut_path().exists()


def enable(open_minimised: bool = False) -> tuple[bool, str]:
    """Create the Startup shortcut. Returns ``(worked, message)``."""
    if not is_supported():
        return False, "Starting with Windows is only available on Windows."

    target, arguments = launch_target()
    if open_minimised:
        arguments = f"{arguments} {TRAY_ARGUMENT}".strip()

    destination = shortcut_path()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not open the Startup folder: {exc}"

    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut('{destination}'); "
        f"$link.TargetPath = '{target}'; "
        f"$link.Arguments = '{arguments}'; "
        f"$link.WorkingDirectory = '{Path(target).parent}'; "
        f"$link.Description = '{APP_NAME} - timesheet companion'; "
        "$link.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"Windows would not create the shortcut: {exc}"

    if not destination.exists():
        return False, "The shortcut was not created."
    return True, f"{APP_NAME} will start when you log in."


def disable() -> tuple[bool, str]:
    """Remove the Startup shortcut."""
    destination = shortcut_path()
    if not destination.exists():
        return True, f"{APP_NAME} does not start automatically."
    try:
        destination.unlink()
    except OSError as exc:
        return False, f"Could not remove the shortcut: {exc}"
    return True, f"{APP_NAME} will no longer start when you log in."


def set_enabled(enabled: bool, open_minimised: bool = False) -> tuple[bool, str]:
    return enable(open_minimised) if enabled else disable()
