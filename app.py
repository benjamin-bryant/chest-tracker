"""Chest Tracker: starts the app."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from settings import data_dir, import_old_setup
from ui import MainWindow

INSTANCE_NAME = "ChestTrackerSingleInstance"


def resource_folder() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def app_folder() -> Path:
    """The folder holding the .exe, or this script when run from source."""
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def set_up_logging(folder: Path) -> None:
    handler = RotatingFileHandler(
        folder / "chest-tracker.log", maxBytes=500_000, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("chest-tracker")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    discord_logger = logging.getLogger("discord")
    discord_logger.setLevel(logging.WARNING)
    discord_logger.addHandler(handler)
    discord_logger.propagate = False
    sys.excepthook = lambda kind, value, trace: logger.error(
        "Unhandled error", exc_info=(kind, value, trace)
    )


def already_running() -> bool:
    """Ask a running copy to show itself. Two copies would count every chest twice."""
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_NAME)
    if socket.waitForConnected(300):
        socket.disconnectFromServer()
        return True
    return False


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Chest Tracker")
    app.setQuitOnLastWindowClosed(False)

    if already_running():
        return 0

    folder = data_dir()
    set_up_logging(folder)
    log = logging.getLogger("chest-tracker")
    if import_old_setup(app_folder(), folder):
        log.info("Imported the old setup from %s", app_folder())

    icon = QIcon(str(resource_folder() / "assets" / "icon.png"))
    app.setWindowIcon(icon)
    window = MainWindow(folder, icon)
    window.show()

    QLocalServer.removeServer(INSTANCE_NAME)
    server = QLocalServer()
    server.newConnection.connect(window.bring_to_front)
    server.listen(INSTANCE_NAME)

    log.info("App started")
    code = app.exec()
    log.info("App closed")
    return code


if __name__ == "__main__":
    sys.exit(main())
