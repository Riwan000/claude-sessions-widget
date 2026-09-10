"""Entry point: always-on-top widget showing live Claude Code CLI sessions."""

import sys
import winsound
from pathlib import Path

from PySide6.QtCore import QLockFile, QProcess, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import status_store
from ui.main_window import MainWindow

POLL_INTERVAL_MS = 2000
LOCK_PATH = status_store.STATUS_DIR / "_widget.lock"
FOCUS_SCRIPT_PATH = Path(__file__).resolve().parent / "focus_session.ps1"


TRAY_COLOR_OK = "#22c55e"
TRAY_COLOR_PERMISSION = "#ef4444"


def make_tray_icon(color=TRAY_COLOR_OK):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(8, 8, 48, 48)
    painter.end()
    return QIcon(pixmap)


def quit_if_idle(app):
    """Closes the widget once no session status file remains - it exists
    only to show live Claude Code sessions, so once the last one's
    SessionEnd hook removes its file there's nothing left to display.
    Runs off the same poll timer as window.refresh(); QApplication.quit()
    is a no-op if the app is already quitting."""
    if not status_store.get_sessions():
        app.quit()


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

    icon_ok = make_tray_icon(TRAY_COLOR_OK)
    icon_permission = make_tray_icon(TRAY_COLOR_PERMISSION)

    tray = QSystemTrayIcon(icon_ok, app)
    tray.setToolTip("Claude Sessions")

    # Mirror the permission state in the tray so it's visible even while
    # the window itself is hidden. Only touch the icon on actual changes -
    # this fires on every 2s poll.
    tray_state = {"permission": False}

    def sync_tray_icon(needs_permission):
        if needs_permission == tray_state["permission"]:
            return
        tray_state["permission"] = needs_permission
        tray.setIcon(icon_permission if needs_permission else icon_ok)
        tray.setToolTip(
            "Claude Sessions — needs permission" if needs_permission else "Claude Sessions"
        )

    window.permission_state_changed.connect(sync_tray_icon)

    def show_task_completed(session):
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

        duration_text = f"{session.turn_duration:.1f}s" if session.turn_duration > 0 else "done"
        task_preview = session.task or session.prompt or "Task finished"
        if len(task_preview) > 120:
            task_preview = task_preview[:117] + "…"

        tray_state["last_completed_pid"] = session.shell_pid

        tool_label = session.tool.capitalize() if session.tool else "AI"
        tray.showMessage(
            f"Task Finished — {session.project}",
            f"Completed in {duration_text} ({tool_label})\n{task_preview}",
            QSystemTrayIcon.Information,
            7000,
        )

    window.task_completed.connect(show_task_completed)

    def show_token_warning(project, total):
        tray_state["last_completed_pid"] = None
        formatted = status_store.format_tokens_precise(total)
        tray.showMessage(
            "Claude Sessions",
            f"{project} has exceeded 150k tokens ({formatted}).\n\n"
            "Large contexts can become slower and more expensive. "
            "Consider starting a new Claude session.",
            QSystemTrayIcon.Warning,
            10000,
        )

    window.token_warning.connect(show_token_warning)

    def show_inactive_notification(project):
        tray_state["last_completed_pid"] = None
        tray.showMessage(
            "Claude Sessions",
            f"Session '{project}' was removed due to 15 minutes of inactivity.",
            QSystemTrayIcon.Information,
            5000,
        )

    window.session_inactive.connect(show_inactive_notification)

    def on_tray_message_clicked():
        pid = tray_state.pop("last_completed_pid", None)
        if pid and FOCUS_SCRIPT_PATH.exists():
            QProcess.startDetached(
                "powershell.exe",
                [
                    "-NoProfile",
                    "-ExecutionPolicy", "Bypass",
                    "-File", str(FOCUS_SCRIPT_PATH),
                    "-ShellPid", str(pid),
                ],
            )

    tray.messageClicked.connect(on_tray_message_clicked)

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
    timer.timeout.connect(lambda: quit_if_idle(app))
    timer.start(POLL_INTERVAL_MS)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
