"""Frameless, translucent, always-on-top window listing Claude Code sessions."""

import json
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import status_store
from ui.session_row import SessionRow

WINDOW_STATE_PATH = status_store.STATUS_DIR / "_window.json"
DEFAULT_GEOMETRY = {"x": 100, "y": 100, "width": 340, "height": 110}
STYLE_PATH = Path(__file__).with_name("style.qss")
FOCUS_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "focus_session.ps1"

MIN_WINDOW_HEIGHT = 110
MAX_WINDOW_HEIGHT_RATIO = 0.8
MAX_WINDOW_HEIGHT_FALLBACK = 640
BLINK_INTERVAL_MS = 500
GEOMETRY_SAVE_DEBOUNCE_MS = 500
RESIZE_EDGE_MARGIN = 8


def load_window_geometry():
    try:
        data = json.loads(WINDOW_STATE_PATH.read_text(encoding="utf-8"))
        geometry = {**DEFAULT_GEOMETRY, **data}
    except (OSError, ValueError):
        geometry = dict(DEFAULT_GEOMETRY)
    return _clamp_to_screen(geometry)


def _clamp_to_screen(geometry):
    # A position saved while a second monitor was attached can land fully
    # off-screen once that monitor is gone - the window is then still
    # running, just invisible, with no on-screen affordance to drag it back.
    screen = QApplication.primaryScreen()
    if screen is None:
        return geometry
    avail = screen.availableGeometry()
    x = max(avail.x(), min(geometry["x"], avail.x() + avail.width() - geometry["width"]))
    y = max(avail.y(), min(geometry["y"], avail.y() + avail.height() - geometry["height"]))
    return {**geometry, "x": x, "y": y}


def save_window_geometry(geometry):
    try:
        status_store.STATUS_DIR.mkdir(parents=True, exist_ok=True)
        WINDOW_STATE_PATH.write_text(json.dumps(geometry), encoding="utf-8")
    except OSError:
        pass


class DragHeader(QFrame):
    """Header bar that lets the user drag the frameless window by clicking it."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._window.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class MainWindow(QWidget):
    hidden_to_tray = Signal()
    permission_state_changed = Signal(bool)  # True while any session needs permission
    token_warning = Signal(str, int)  # project, total tokens - fires once per session
    session_inactive = Signal(str)  # project - fires when a session is removed due to inactivity
    task_completed = Signal(object)  # session - fires when a session completes a task

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        geometry = load_window_geometry()
        self.setGeometry(
            geometry["x"], geometry["y"], geometry["width"], geometry["height"]
        )

        self._rows = {}
        self._token_warned = set()  # session_ids already notified past TOKEN_TIER_HIGH
        self._prev_statuses = {}  # session_id -> status for transition detection
        self._blink_on = False
        self._collapsed = bool(geometry.get("collapsed", False))
        self._build_ui()
        self.setStyleSheet(STYLE_PATH.read_text(encoding="utf-8"))
        self._apply_collapsed_state()

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)

        # Debounce geometry persistence: a window drag fires moveEvent for
        # every pixel, which would mean dozens of file writes per second.
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.timeout.connect(self._persist_geometry)
        QApplication.instance().aboutToQuit.connect(self._persist_geometry)

        self.refresh()

    def _build_ui(self):
        root = QFrame(self)
        root.setObjectName("root")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(8)
        self._root_layout = layout

        header = DragHeader(self)
        header.setObjectName("header")
        self._header = header
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("AI Sessions")
        title.setObjectName("titleLabel")
        header_layout.addWidget(title)

        self.count_label = QLabel("")
        self.count_label.setObjectName("countLabel")
        header_layout.addWidget(self.count_label)

        header_layout.addStretch(1)

        collapse_button = QPushButton("▾")
        collapse_button.setObjectName("collapseButton")
        collapse_button.setFixedSize(22, 22)
        collapse_button.setCursor(Qt.PointingHandCursor)
        collapse_button.clicked.connect(self._on_collapse_clicked)
        self.collapse_button = collapse_button
        header_layout.addWidget(collapse_button)

        hide_button = QPushButton("–")
        hide_button.setObjectName("hideButton")
        hide_button.setFixedSize(22, 22)
        hide_button.setCursor(Qt.PointingHandCursor)
        hide_button.clicked.connect(self._on_hide_clicked)
        header_layout.addWidget(hide_button)

        layout.addWidget(header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)

        self.empty_label = QLabel("No active AI sessions")
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.list_layout.insertWidget(0, self.empty_label)

        self.scroll_area.setWidget(self.list_container)
        layout.addWidget(self.scroll_area, 1)

    def _on_hide_clicked(self):
        self.hide()
        self.hidden_to_tray.emit()

    def _on_collapse_clicked(self):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed):
        self._collapsed = collapsed
        self._apply_collapsed_state()
        self._fit_height_to_content()
        self._geometry_save_timer.start(GEOMETRY_SAVE_DEBOUNCE_MS)

    def _apply_collapsed_state(self):
        self.scroll_area.setVisible(not self._collapsed)
        self.collapse_button.setText("▸" if self._collapsed else "▾")
        self.collapse_button.setToolTip("Expand" if self._collapsed else "Collapse")

    def _on_row_clicked(self, session):
        if not FOCUS_SCRIPT_PATH.exists() or not session.shell_pid:
            return  # no recorded process to focus (e.g. a record from before this feature existed)

        QProcess.startDetached(
            "powershell.exe",
            [
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", str(FOCUS_SCRIPT_PATH),
                "-ShellPid", str(session.shell_pid),
            ],
        )

    def refresh(self):
        sessions = status_store.get_sessions()
        seen_ids = set()

        for index, session in enumerate(sessions):
            seen_ids.add(session.session_id)
            row = self._rows.get(session.session_id)
            if row is None:
                row = SessionRow(session)
                row.clicked.connect(self._on_row_clicked)
                self._rows[session.session_id] = row
                self.list_layout.insertWidget(index, row)
            else:
                row.update_session(session)
                self.list_layout.removeWidget(row)
                self.list_layout.insertWidget(index, row)
            self._check_token_warning(session)
            self._check_task_completed(session)

        for session_id in list(self._rows):
            if session_id not in seen_ids:
                row = self._rows.pop(session_id)
                self.list_layout.removeWidget(row)
                row.deleteLater()
                self._token_warned.discard(session_id)
                self._prev_statuses.pop(session_id, None)
                if row.session.age_seconds >= status_store.INACTIVE_TIMEOUT_SECONDS:
                    self.session_inactive.emit(row.session.project)

        self.count_label.setText(self._count_text(sessions))
        self.empty_label.setVisible(not sessions)

        needs_permission = any(s.display_status == "permission" for s in sessions)
        self._update_blink_timer(needs_permission)
        self.permission_state_changed.emit(needs_permission)
        QTimer.singleShot(0, self._fit_height_to_content)

    def _check_task_completed(self, session):
        """Emits task_completed when a session transitions from running to finished,
        provided turn_duration meets or exceeds DEFAULT_NOTIFICATION_THRESHOLD_SECONDS."""
        prev = self._prev_statuses.get(session.session_id)
        if prev == "running" and session.status == "finished":
            threshold = getattr(
                status_store, "DEFAULT_NOTIFICATION_THRESHOLD_SECONDS", 5.0
            )
            if session.turn_duration >= threshold:
                self.task_completed.emit(session)
        self._prev_statuses[session.session_id] = session.status

    def _check_token_warning(self, session):
        """Emits token_warning once per session the first time its cumulative
        context size crosses TOKEN_TIER_HIGH, never again for that session
        (even if the total keeps climbing) - the pill color already tracks
        further growth, so re-notifying on every poll would just be noise.
        Keyed off context_tokens rather than the per-turn total, since it's
        the size of the whole conversation - what actually gets resent (and
        billed) on every future turn - that makes a fresh session worth
        suggesting."""
        if session.session_id in self._token_warned:
            return
        if session.context_tokens >= status_store.TOKEN_TIER_HIGH:
            self._token_warned.add(session.session_id)
            self.token_warning.emit(session.project, session.context_tokens)

    def _update_blink_timer(self, needs_blink):
        if needs_blink and not self._blink_timer.isActive():
            self._blink_timer.start(BLINK_INTERVAL_MS)
        elif not needs_blink and self._blink_timer.isActive():
            self._blink_timer.stop()
            self._blink_on = False

    def _count_text(self, sessions):
        if not sessions:
            return ""
        running = sum(1 for s in sessions if s.display_status == "running")
        needs_permission = sum(1 for s in sessions if s.display_status == "permission")
        parts = []
        if needs_permission:
            parts.append(f"{needs_permission} needs permission")
        if running:
            parts.append(f"{running} running")
        return f"({', '.join(parts)})" if parts else ""

    def _toggle_blink(self):
        self._blink_on = not self._blink_on
        for row in self._rows.values():
            row.set_blink(self._blink_on)

    def _max_window_height(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            return int(screen.availableGeometry().height() * MAX_WINDOW_HEIGHT_RATIO)
        return MAX_WINDOW_HEIGHT_FALLBACK

    def _fit_height_to_content(self):
        self.list_layout.invalidate()
        self.list_layout.activate()
        margins = self._root_layout.contentsMargins()
        header_height = self._header.sizeHint().height() + margins.top() + margins.bottom()

        if self._collapsed:
            target_height = header_height
        else:
            content_height = self.list_layout.sizeHint().height()
            target_height = header_height + self._root_layout.spacing() + content_height
            target_height = max(MIN_WINDOW_HEIGHT, min(target_height, self._max_window_height()))

        current = self.geometry()
        if target_height != current.height():
            self.resize(current.width(), target_height)

    def mousePressEvent(self, event):
        # The auto-height logic owns the vertical dimension, but width is
        # still the user's: dragging the left/right window edge starts a
        # native horizontal resize (this replaced the QSizeGrip, which
        # fought the auto-height by resizing both dimensions).
        if event.button() == Qt.LeftButton and self.windowHandle() is not None:
            x = event.position().x()
            if x <= RESIZE_EDGE_MARGIN:
                self.windowHandle().startSystemResize(Qt.LeftEdge)
                return
            if x >= self.width() - RESIZE_EDGE_MARGIN:
                self.windowHandle().startSystemResize(Qt.RightEdge)
                return
        super().mousePressEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._geometry_save_timer.start(GEOMETRY_SAVE_DEBOUNCE_MS)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._geometry_save_timer.start(GEOMETRY_SAVE_DEBOUNCE_MS)

    def _persist_geometry(self):
        geo = self.geometry()
        save_window_geometry(
            {
                "x": geo.x(),
                "y": geo.y(),
                "width": geo.width(),
                "height": geo.height(),
                "collapsed": self._collapsed,
            }
        )
