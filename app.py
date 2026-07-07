"""Entry point: always-on-top widget showing live Claude Code CLI sessions."""

import sys

from PySide6.QtCore import QLockFile, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import status_store
from ui.main_window import MainWindow

POLL_INTERVAL_MS = 2000
LOCK_PATH = status_store.STATUS_DIR / "_widget.lock"


def make_tray_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#22c55e"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(8, 8, 48, 48)
    painter.end()
    return QIcon(pixmap)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # One widget at a time: a second launch (e.g. from a startup shortcut
    # while one is already running) would silently show two overlapping
    # copies, one of which may be running stale code. QLockFile self-heals
    # if the previous owner crashed without releasing the lock.
    status_store.STATUS_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(LOCK_PATH))
    lock.setStaleLockTime(0)  # 0 = a lock from a dead process is always reclaimable
    if not lock.tryLock(100):
        print("Claude Sessions widget is already running.", file=sys.stderr)
        sys.exit(0)

    window = MainWindow()
    window.show()

    tray = QSystemTrayIcon(make_tray_icon(), app)
    tray.setToolTip("Claude Sessions")

    menu = QMenu()

    toggle_action = QAction("Hide")

    def toggle_visibility():
        if window.isVisible():
            window.hide()
            toggle_action.setText("Show")
        else:
            window.show()
            window.raise_()
            toggle_action.setText("Hide")

    toggle_action.triggered.connect(toggle_visibility)
    menu.addAction(toggle_action)

    clear_action = QAction("Clear finished")
    clear_action.triggered.connect(lambda: (status_store.clear_finished(), window.refresh()))
    menu.addAction(clear_action)

    menu.addSeparator()

    quit_action = QAction("Quit")
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: toggle_visibility() if reason == QSystemTrayIcon.Trigger else None
    )
    tray.show()

    def sync_hide_state():
        toggle_action.setText("Show" if not window.isVisible() else "Hide")

    window.hidden_to_tray.connect(sync_hide_state)

    timer = QTimer()
    timer.timeout.connect(window.refresh)
    timer.start(POLL_INTERVAL_MS)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
