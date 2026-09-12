"""Tests for AvatarWindow (Baymax companion avatar roaming along taskbar)."""

import os
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from ui.avatar_window import (
    AvatarWindow,
    AVATAR_WINDOW_WIDTH,
    AVATAR_WINDOW_HEIGHT,
)
from ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def avatar(qapp):
    win = AvatarWindow()
    yield win
    win.close()
    win.deleteLater()
    QApplication.processEvents()


def _make_fake_session(session_id="s1", project="my-project", status="running", tool="edit"):
    return SimpleNamespace(
        id=session_id,
        project=project,
        display_status=status,
        tool=tool,
        current_tool_detail="editing file.py",
        task="refactoring models",
        prompt="fix bug",
        turn_duration=3.2,
        shell_pid=1234,
    )


class TestAvatarWindowInit:
    def test_dimensions(self, avatar):
        assert avatar.width() == AVATAR_WINDOW_WIDTH
        assert avatar.height() == AVATAR_WINDOW_HEIGHT

    def test_initial_state(self, avatar):
        assert avatar._state == "in_box_sleeping"
        assert avatar.is_paused() is False
        assert avatar._bubble_visible is True


class TestAvatarStateTransitions:
    def test_transitions_from_sleeping_to_inflating_when_session_arrives(self, avatar):
        session = _make_fake_session(status="running")
        avatar.set_sessions([session])
        assert avatar._state == "inflating_out"

    def test_transitions_to_permission_mech_suit(self, avatar):
        avatar._state = "waddling"
        session = _make_fake_session(status="permission")
        avatar.set_sessions([session])
        assert avatar._state == "permission"

    def test_transitions_to_working_scan_beam(self, avatar):
        avatar._state = "waddling"
        session = _make_fake_session(status="running")
        avatar.set_sessions([session])
        assert avatar._state == "working"

    def test_transitions_to_box_when_no_sessions_left(self, avatar):
        avatar._state = "waddling"
        avatar.set_sessions([])
        assert avatar._state == "walking_to_box"


class TestAvatarControlsAndSignals:
    def test_pause_and_bubble_toggles(self, avatar):
        avatar.set_paused(True)
        assert avatar.is_paused() is True
        avatar.set_paused(False)
        assert avatar.is_paused() is False

        avatar.set_bubble_visible(False)
        assert avatar._bubble_visible is False
        avatar.set_bubble_visible(True)
        assert avatar._bubble_visible is True

    def test_clicked_signal_emitted_on_mouse_click(self, avatar):
        received = []
        avatar.clicked.connect(lambda: received.append(True))

        from PySide6.QtCore import QEvent, QPointF
        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(50.0, 50.0),
            QPointF(50.0, 50.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        avatar.mousePressEvent(press_event)

        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(50.0, 50.0),
            QPointF(50.0, 50.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        avatar.mouseReleaseEvent(release_event)
        assert len(received) == 1

    def test_drag_and_drop_across_screen_does_not_emit_click(self, avatar):
        received = []
        avatar.clicked.connect(lambda: received.append(True))

        from PySide6.QtCore import QEvent, QPointF
        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(50.0, 50.0),
            QPointF(50.0, 50.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        avatar.mousePressEvent(press_event)

        # Move far enough to exceed drag threshold (6px)
        move_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(120.0, 120.0),
            QPointF(120.0, 120.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        avatar.mouseMoveEvent(move_event)
        assert avatar._is_dragging is True

        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(120.0, 120.0),
            QPointF(120.0, 120.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        avatar.mouseReleaseEvent(release_event)

        assert avatar._is_dragging is False
        assert len(received) == 0  # No click emitted on drag drop


class TestAvatarRendering:
    @pytest.mark.parametrize(
        "state",
        [
            "in_box_sleeping",
            "inflating_out",
            "waddling",
            "working",
            "permission",
            "walking_to_box",
            "deflating_in",
        ],
    )
    def test_renders_each_state_without_exception(self, avatar, state):
        avatar._state = state
        session = _make_fake_session(status="running" if state != "permission" else "permission")
        avatar._sessions = [session]
        avatar.repaint()


class TestMainWindowAvatarAnchoring:
    def test_position_above_aligns_above_avatar(self, qapp, avatar):
        window = MainWindow()
        window.set_anchor_avatar(avatar)

        avatar.move(300, 700)
        window.position_above(avatar.x(), avatar.y(), avatar.width())

        expected_y = avatar.y() - window.height() - 8
        assert window.y() == expected_y

        avatar_center_x = avatar.x() + (avatar.width() // 2)
        expected_x = avatar_center_x - (window.width() // 2)
        assert window.x() == expected_x

        window.close()
        window.deleteLater()


class TestMultiScreenSupport:
    def test_recalculate_screen_bounds(self, avatar):
        avatar._recalculate_screen_bounds()
        assert avatar._max_x > avatar._min_x

    def test_dynamic_ground_adjustment_on_tick(self, avatar):
        avatar._state = "waddling"
        avatar._paused = False
        avatar._is_dragging = False

        # Set an arbitrary ground Y
        avatar._ground_y = 100
        # Run tick to let it dynamically seek current screen's taskbar ground
        avatar._tick()
        # ground_y should have moved towards the screen's taskbar ground
        assert avatar._ground_y != 100

