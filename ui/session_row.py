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

TASK_ELIDE_WIDTH = 240


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

        self.project_label.setText(session.project)

        if session.display_status == "permission" and session.alert:
            task_text = session.alert
        else:
            task_text = session.task or STATUS_LABELS.get(session.display_status, "")
        fm = QFontMetrics(self.task_label.font())
        elided = fm.elidedText(task_text, Qt.ElideRight, TASK_ELIDE_WIDTH)
        self.task_label.setText(elided)

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

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.session)
        super().mousePressEvent(event)
