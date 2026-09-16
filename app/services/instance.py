"""Single-instance guard.

A second copy of the application fighting over the same SQLite file is a good
way to end up with two running timers and a confusing audit trail. Instead, a
second launch hands its request to the first and exits, and the first brings
its window to the front.

Implemented with a local socket (a named pipe on Windows) rather than a lock
file, because a lock file left behind by a crash would block every later
launch until somebody deleted it by hand.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

#: Per-user pipe name. Two different Windows accounts get their own.
SERVER_NAME = "TimeTrack.SingleInstance"

CONNECT_TIMEOUT_MS = 500


class SingleInstance(QObject):
    """Owns the server end when this process is the first instance."""

    #: Emitted when another launch asked this instance to show itself.
    activation_requested = Signal()

    def __init__(self, name: str = SERVER_NAME, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self._server: QLocalServer | None = None

    def try_acquire(self) -> bool:
        """Become the primary instance, or report that one already exists."""
        if self._already_running():
            return False

        # A crash can leave the pipe behind; clearing it is safe now that we
        # know nothing is actually listening.
        QLocalServer.removeServer(self.name)
        server = QLocalServer(self)
        if not server.listen(self.name):
            # Without a guard it is better to run than to refuse to start.
            return True
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def _already_running(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        connected = probe.waitForConnected(CONNECT_TIMEOUT_MS)
        if connected:
            probe.disconnectFromServer()
        return connected

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        connection = self._server.nextPendingConnection()
        if connection is not None:
            connection.disconnectFromServer()
        self.activation_requested.emit()

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        QLocalServer.removeServer(self.name)


def signal_existing_instance(name: str = SERVER_NAME) -> bool:
    """Ask a running instance to show its window. True if one answered."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True
