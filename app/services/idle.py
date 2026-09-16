"""How long the user has been away from the keyboard.

Windows keeps a single system-wide "last input" tick that covers keyboard and
mouse across every application, which is exactly what is wanted here: the
question is whether the person is at the machine at all, not whether they are
typing into this particular window.

On any other platform this reports "unknown" rather than guessing. Idle
detection then simply switches itself off, which is the safe direction: the
application never discards time it is not sure about.
"""

from __future__ import annotations

import ctypes
import sys

#: Windows tick counts are 32-bit and wrap around roughly every 49.7 days.
_TICK_MASK = 0xFFFFFFFF


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def is_supported() -> bool:
    return sys.platform.startswith("win")


def idle_seconds() -> int | None:
    """Seconds since the last keyboard or mouse input system-wide.

    Returns ``None`` when the platform cannot answer, so callers can tell
    "the user has been idle for zero seconds" apart from "we do not know".
    """
    if not is_supported():
        return None
    try:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now_ticks = ctypes.windll.kernel32.GetTickCount()
        # Masked subtraction so a wrap-around reads as a small positive
        # number rather than roughly fifty days of idleness.
        elapsed_ms = (now_ticks - info.dwTime) & _TICK_MASK
        return int(elapsed_ms // 1000)
    except (AttributeError, OSError):
        # No user32/kernel32, or the call was refused. Treat as unsupported
        # rather than letting an OS quirk take the application down.
        return None


def describe() -> str:
    """One line for the Settings panel explaining the current capability."""
    if not is_supported():
        return (
            "Idle detection is only available on Windows. On this system a "
            "timer left running will never be flagged as idle."
        )
    if idle_seconds() is None:
        return "Idle detection is unavailable - Windows did not answer."
    return "Idle detection is active."
