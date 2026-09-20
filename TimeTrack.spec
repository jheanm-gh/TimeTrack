# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for TimeTrack.

Built in **one-folder** mode, deliberately. A one-file executable unpacks
itself into a temporary directory every time it starts, which is the same
behaviour self-extracting malware packers use and a well-known trigger for
corporate antivirus heuristics. One-folder skips the extraction entirely: it
starts faster and is far less likely to be quarantined. The user still has a
single thing to double-click, because the build creates shortcuts.

Run it through ``build.ps1`` rather than directly - the icon and the Windows
version resource are generated first, and this file expects them to exist.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
BUILD_DIR = PROJECT_ROOT / "build"
IS_WINDOWS = sys.platform.startswith("win")

APP_NAME = "TimeTrack"

#: Qt ships a great deal that a small desktop form never touches. Excluding
#: it is what keeps the folder well under 100 MB; WebEngine alone is over
#: 150 MB. QtNetwork is *not* here: the single-instance guard uses a local
#: socket from it. Nothing in this list reaches the network.
EXCLUDED_QT = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebView",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickTest",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtHelp",
    "PySide6.QtSql",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtHttpServer",
    "PySide6.QtOpcUa",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtTextToSpeech",
    "PySide6.QtDBus",
]

#: Nothing here is used either, and tkinter in particular drags in a whole
#: second GUI toolkit.
EXCLUDED_OTHER = [
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "pytest",
    "_pytest",
    "numpy",
    "pandas",
    "matplotlib",
    "PIL",
    "setuptools",
    "pip",
]

icon_file = BUILD_DIR / f"{APP_NAME}.ico"
version_file = BUILD_DIR / "version_info.txt"

analysis = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "run_timetrack.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        # openpyxl reaches for this lazily; naming it keeps the analyser from
        # missing it and producing a build that fails on the first save.
        "openpyxl.cell._writer",
        # The IANA time zone database. Windows has none of its own, so
        # without this the packaged application cannot resolve a named zone
        # and the display-timezone setting silently does nothing.
        #
        # PyInstaller's own zoneinfo hook adds this, but only when the build
        # runs on Windows. Naming it here makes the package identical
        # whatever machine builds it, and means the result can be checked
        # from a Linux build too.
        "tzdata",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_QT + EXCLUDED_OTHER,
    noarchive=False,
    optimize=0,
)

# The pruning rules live in tools/prune_rules.py so they can be unit tested;
# a mistake in them either breaks the packaged application on a machine that
# cannot be debugged from here, or silently doubles the folder size.
sys.path.insert(0, str(PROJECT_ROOT))
from tools.prune_rules import prune  # noqa: E402

_before = len(analysis.binaries) + len(analysis.datas)
analysis.binaries = prune(analysis.binaries)
analysis.datas = prune(analysis.datas)
print(
    f"[TimeTrack] pruned "
    f"{_before - len(analysis.binaries) - len(analysis.datas)} unused Qt files"
)

pyz = PYZ(analysis.pure)  # noqa: F821

executable = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression is itself an antivirus trigger.
    console=False,  # A desktop application, not a console tool.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if (IS_WINDOWS and icon_file.exists()) else None,
    version=str(version_file) if (IS_WINDOWS and version_file.exists()) else None,
)

collected = COLLECT(  # noqa: F821
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
