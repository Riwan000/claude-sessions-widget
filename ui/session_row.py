"""A single session row: status dot + accent bar, project name, task, time."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

import status_store

STATUS_LABELS = {
    "idle": "waiting",
    "running": "running",
    "finished": "finished",
    "stale": "possibly closed",
    "permission": "needs permission",
}

# Icon + verb shown next to the project name while a tool is active, inferred
# from the last PreToolUse call. Unrecognized tools (custom/MCP) fall back to
# a generic "Working" rather than showing nothing.
TOOL_PERSONALITY = {
    "Read": ("📖", "Reading"),
    "NotebookEdit": ("📖", "Reading"),
    "Edit": ("✏️", "Editing"),
    "Write": ("✏️", "Editing"),
    "Bash": ("⚡", "Running"),
    "Grep": ("🔍", "Searching"),
    "Glob": ("🔍", "Searching"),
    "WebFetch": ("🔍", "Searching"),
    "WebSearch": ("🔍", "Searching"),
    "TodoWrite": ("📝", "Planning"),
    "Task": ("🤖", "Delegating"),
}
TOOL_PERSONALITY_FALLBACK = ("🔧", "Working")

# Shown when a session has no active tool call. permission/stale are
# deliberately absent here - they already have their own dedicated visual
# treatment (blinking alert / "possibly closed" status word) and a
# personality tag would just be redundant noise on top of that.
STATUS_PERSONALITY = {
    "idle": ("💤", "Idle"),
    "running": ("🧠", "Thinking"),
    "finished": ("✅", "Done"),
}

TASK_ELIDE_MIN_WIDTH = 80
TASK_ELIDE_PADDING = 4


def _personality_text(session):
    if session.display_status == "running" and session.current_tool:
        icon, verb = TOOL_PERSONALITY.get(session.current_tool, TOOL_PERSONALITY_FALLBACK)
        return f"{icon} {verb}"
    entry = STATUS_PERSONALITY.get(session.display_status)
    return f"{entry[0]} {entry[1]}" if entry else ""


def _apply_property(widget, name, value):
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class SessionRow(QFrame):
    clicked = Signal(object)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.setObjectName("sessionRow")
        self.session_id = session.session_id
        self._task_text = ""
        self.setCursor(Qt.PointingHandCursor)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 10, 0)
        outer.setSpacing(10)

        self.accent_bar = QFrame()
        self.accent_bar.setObjectName("accentBar")
        self.accent_bar.setFixedWidth(4)
        outer.addWidget(self.accent_bar)

        body = QVBoxLayout()
        body.setContentsMargins(0, 8, 0, 8)
        body.setSpacing(3)

        header = QHBoxLayout()
        header.setSpacing(6)

        self.status_dot = QLabel()
        self.status_dot.setObjectName("statusDot")
        self.status_dot.setFixedSize(8, 8)
        header.addWidget(self.status_dot, alignment=Qt.AlignVCenter)

        self.project_label = QLabel()
        self.project_label.setObjectName("projectLabel")
        header.addWidget(self.project_label)

        self.personality_label = QLabel()
        self.personality_label.setObjectName("personalityLabel")
        header.addWidget(self.personality_label)

        header.addStretch(1)

        self.token_label = QLabel()
        self.token_label.setObjectName("tokenLabel")
        header.addWidget(self.token_label, alignment=Qt.AlignVCenter)

        self.time_label = QLabel()
        self.time_label.setObjectName("timeLabel")
        header.addWidget(self.time_label, alignment=Qt.AlignVCenter)

        body.addLayout(header)

        self.task_label = QLabel()
        self.task_label.setObjectName("taskLabel")
        body.addWidget(self.task_label)

        outer.addLayout(body)

        self.update_session(session)

    def update_session(self, session):
        self.session = session
        _apply_property(self, "status", session.display_status)
        _apply_property(self.accent_bar, "status", session.display_status)
        _apply_property(self.status_dot, "status", session.display_status)
        for widget in (self.accent_bar, self.status_dot):
            _apply_property(widget, "blink", "off")

        project_text = f"{session.language_icon} {session.project}" if session.language_icon else session.project
        self.project_label.setText(project_text)
        self.personality_label.setText(_personality_text(session))

        if session.display_status == "permission" and session.alert:
            self._task_text = session.alert
        elif session.current_tool_detail:
            self._task_text = session.current_tool_detail
        else:
            self._task_text = session.task or STATUS_LABELS.get(session.display_status, "")
        self._apply_task_elide()

        status_word = STATUS_LABELS.get(session.display_status, session.display_status)
        tooltip_lines = [f"{session.project} — {status_word}", session.task or "(no task yet)"]
        if session.has_tokens:
            total = session.tokens_in + session.tokens_out
            tooltip_lines.append(
                f"Last turn: {total:,} tokens (in: {session.tokens_in:,}, out: {session.tokens_out:,})"
            )
        tooltip_lines.append(session.cwd)
        self.setToolTip("\n".join(tooltip_lines))

        if session.has_tokens:
            total = session.tokens_in + session.tokens_out
            self.token_label.setText(f"{status_store.format_tokens(total)} tok")
        else:
            self.token_label.setText("")

        self.time_label.setText(status_store.format_relative(session.age_seconds))

    def set_blink(self, on):
        if self.session.display_status != "permission":
            return
        state = "on" if on else "off"
        for widget in (self.accent_bar, self.status_dot):
            _apply_property(widget, "blink", state)

    def _apply_task_elide(self):
        available = max(TASK_ELIDE_MIN_WIDTH, self.task_label.width() - TASK_ELIDE_PADDING)
        fm = QFontMetrics(self.task_label.font())
        self.task_label.setText(fm.elidedText(self._task_text, Qt.ElideRight, available))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_task_elide()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.session)
        super().mousePressEvent(event)
