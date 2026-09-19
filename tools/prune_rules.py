"""Which files PyInstaller collects but the application never uses.

These rules live in their own module, rather than inside ``TimeTrack.spec``,
so they can be unit tested. A mistake here is expensive in both directions:
too aggressive and the packaged application fails to start on a machine
nobody can debug from here; too timid and the folder doubles in size and
starts attracting antivirus attention.

Excluding a Python module in the spec is not enough on its own. PyInstaller's
Qt hook also collects Qt's *plugins*, and a plugin pulls its own dependencies
in behind it - the virtual-keyboard input plugin drags in the entire QML
runtime, and the PDF image-format plugin drags in Qt PDF.
"""

from __future__ import annotations

import os

#: Matched anywhere in the destination path.
DROP_SUBSTRINGS = (
    "qt6qml",
    "qt6quick",
    "qt6virtualkeyboard",
    "qt6pdf",
    "virtualkeyboardplugin",
    "/qmltooling/",
)

#: Image formats Qt can decode but this application never opens. Its icons
#: are drawn in code, not loaded from files.
DROP_IMAGE_FORMATS = frozenset(
    {"qicns", "qtga", "qwbmp", "qwebp", "qtiff", "qpdf"}
)

#: Qt ships its own developer tools inside the wheel. They are of no use in a
#: packaged application and add roughly 10 MB.
DROP_TOOLS = frozenset(
    {
        "assistant", "designer", "linguist", "lupdate", "lrelease", "lconvert",
        "qmllint", "qmlls", "qmlformat", "qmlprofiler", "qmlscene",
        "qmltestrunner", "qmlimportscanner", "qmlcachegen", "qmltyperegistrar",
        "qdbusviewer", "qdbuscpp2xml", "qdbusxml2cpp", "qtpaths", "qtdiag",
        "uic", "rcc", "svgtoqml", "balsam", "balsamui",
    }
)

#: Mesa's software OpenGL fallback, about 20 MB. Qt reaches for it only when
#: a hardware OpenGL context is needed and unavailable, which for a Widgets
#: application never happens - Widgets paint through the raster engine.
SOFTWARE_OPENGL = "opengl32sw"

#: Set to "1" to keep it anyway, if a machine ever fails to show the window.
KEEP_OPENGL_ENV = "TIMETRACK_KEEP_SOFTWARE_OPENGL"


def keep_software_opengl() -> bool:
    return os.environ.get(KEEP_OPENGL_ENV) == "1"


def should_drop(destination: str) -> bool:
    """Should this collected file be left out of the packaged folder?"""
    normalised = str(destination).replace("\\", "/").lower()

    if any(fragment in normalised for fragment in DROP_SUBSTRINGS):
        return True
    if SOFTWARE_OPENGL in normalised and not keep_software_opengl():
        return True

    name = normalised.rsplit("/", 1)[-1]
    stem = name[3:] if name.startswith("lib") else name
    stem = stem.split(".", 1)[0]

    if "imageformats/" in normalised:
        return stem in DROP_IMAGE_FORMATS
    # Only at the top of the PySide6 folder, so a Qt library that happens to
    # share a name with a tool is never caught by accident.
    if stem in DROP_TOOLS and normalised.count("/") <= 1:
        return True
    return False


def prune(entries):
    """Filter a PyInstaller TOC, keeping everything the application needs."""
    return [entry for entry in entries if not should_drop(entry[0])]
