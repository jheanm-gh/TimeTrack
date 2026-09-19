"""Entry point for the packaged application.

PyInstaller is pointed at this rather than at ``app/__main__.py`` so that the
``app`` package is imported normally instead of being run as ``__main__``,
which avoids the module being initialised twice under its two names.

From source, ``python -m app`` remains the way to run it.
"""

from __future__ import annotations

import multiprocessing
import sys

from app.__main__ import main

if __name__ == "__main__":
    # Harmless here (nothing spawns a process), but it is the documented
    # guard against a frozen build re-launching itself if that ever changes.
    multiprocessing.freeze_support()
    sys.exit(main())
