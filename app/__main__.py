"""Entry point: ``python -m app``.

Opens the database (creating and migrating it on first run), enforces a
single instance, and shows the window.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from app import paths
from app.db.connection import connect
from app.db.repository import Repository
from app.db.schema import initialise
from app.services.instance import SingleInstance, signal_existing_instance
from app.services.startup import TRAY_ARGUMENT
from app.ui import theme
from app.version import APP_NAME, VERSION


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    start_in_tray = TRAY_ARGUMENT in argv

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName(APP_NAME)
    app.setWindowIcon(theme.app_icon())
    # Closing the last window must not end the process: the tray icon is
    # still there and timers may still be running.
    app.setQuitOnLastWindowClosed(False)

    guard = SingleInstance()
    if not guard.try_acquire():
        signal_existing_instance()
        return 0

    paths.ensure_dirs()
    try:
        conn = connect(paths.db_path())
        initialise(conn)
    except Exception as exc:  # noqa: BLE001 - last line of defence
        QMessageBox.critical(
            None,
            APP_NAME,
            "TimeTrack could not open its database.\n\n"
            f"{paths.db_path()}\n\n{exc}\n\n"
            "Your data is still there. If this keeps happening, restore the "
            "most recent copy from the backups folder.",
        )
        return 1

    repo = Repository(conn)
    if repo.get_bool("startup.open_minimised", False):
        start_in_tray = True

    from app.ui.main_window import MainWindow

    window = MainWindow(repo, start_in_tray=start_in_tray)
    guard.activation_requested.connect(window.show_and_raise)

    try:
        return app.exec()
    finally:
        guard.release()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
