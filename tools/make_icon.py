"""Generate ``TimeTrack.ico`` from the same drawing code as the tray icon.

The application's icons are drawn in code rather than shipped as image
files, so the Windows icon is built at packaging time from the same
:mod:`app.ui.theme` routine. That keeps the executable, the taskbar and the
tray showing one consistent mark.

Qt can write a ``.ico``, but only a single image into it. Windows picks a
different size for each context - 16px in the taskbar, 32px on the desktop,
256px in the large-icon view - so the container is assembled here by hand
with every size inside it. An icon that has to be scaled up looks blurry in
exactly the places a user judges whether software is trustworthy.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

# A display is neither available nor needed to draw into a pixmap.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: The sizes Windows actually asks for.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _png_bytes(size: int) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtWidgets import QApplication

    from app.ui import theme

    if QApplication.instance() is None:
        QApplication([])

    pixmap = theme.state_pixmap(size)
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(store)


def build_ico(destination: Path) -> Path:
    """Write a multi-resolution .ico with PNG-compressed entries."""
    images = [(size, _png_bytes(size)) for size in SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))  # reserved, type=icon, count
    entry_size = 16
    offset = len(header) + entry_size * len(images)

    entries = bytearray()
    for size, payload in images:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,  # 0 means 256 in the ICO format
            0 if size >= 256 else size,
            0,  # colours in palette: 0 for true colour
            0,  # reserved
            1,  # colour planes
            32,  # bits per pixel
            len(payload),
            offset,
        )
        offset += len(payload)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        handle.write(header)
        handle.write(entries)
        for _size, payload in images:
            handle.write(payload)
    return destination


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the TimeTrack icon.")
    parser.add_argument("--out", default="build/TimeTrack.ico")
    args = parser.parse_args(argv)

    path = build_ico(Path(args.out))
    print(f"Wrote {path} ({path.stat().st_size:,} bytes, sizes: "
          f"{', '.join(str(size) for size in SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
