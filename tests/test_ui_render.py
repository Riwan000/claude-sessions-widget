"""Offscreen render tests for the Qt layer (MainWindow, SessionRow, tray icon).

These run against the "offscreen" Qt platform plugin, so no window ever
appears; they cover widget construction, row lifecycle, task elision, the
permission blink/signal plumbing, and tray icon colors.
"""

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

import status_store


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    directory = tmp_path / "widget-status"
    directory.mkdir()
    monkeypatch.setattr(status_store, "STATUS_DIR", directory)
    import ui.main_window as main_window_module

    monkeypatch.setattr(
        main_window_module, "WINDOW_STATE_PATH", directory / "_window.json"
    )
    return directory


@pytest.fixture
def make_window(qapp, status_dir):
    """Builds MainWindows and guarantees they're torn down between tests."""
    from ui.main_window import MainWindow

    windows = []

    def build():
        window = MainWindow()
        windows.append(window)
        return window

    yield build

    for window in windows:
        window.close()
        window.deleteLater()
    QApplication.processEvents()


def write_session(status_dir, session_id, status, age_seconds=0, **extra):
    now = time.time()
    payload = {
        "sessionId": session_id,
        "project": extra.pop("project", session_id),
        "cwd": f"C:/x/{session_id}",
        "task": extra.pop("task", "some task"),
        "status": status,
        "startedAt": now - age_seconds - 60,
        "updatedAt": now - age_seconds,
        **extra,
    }
    (status_dir / f"{session_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def make_session(**overrides):
    now = time.time()
    fields = dict(
        session_id="s1",
        project="proj",
        cwd="C:/x/proj",
        task="short task",
        status="running",
        display_status="running",
        started_at=now - 60,
        updated_at=now,
    )
    fields.update(overrides)
    return status_store.Session(**fields)


class TestMainWindowRendering:
    def test_one_row_per_session_and_empty_label_hidden(self, status_dir, make_window):
        write_session(status_dir, "alpha", "running")
        write_session(status_dir, "beta", "idle")

        window = make_window()
        QApplication.processEvents()

        assert len(window._rows) == 2
        assert set(window._rows) == {"alpha", "beta"}
        assert not window.empty_label.isVisibleTo(window)
        assert "1 running" in window.count_label.text()

    def test_empty_state_shows_placeholder(self, status_dir, make_window):
        window = make_window()
        QApplication.processEvents()

        assert window._rows == {}
        assert window.empty_label.isVisibleTo(window)
        assert window.count_label.text() == ""

    def test_row_removed_when_session_file_disappears(self, status_dir, make_window):
        write_session(status_dir, "alpha", "running")
        window = make_window()
        assert "alpha" in window._rows

        (status_dir / "alpha.json").unlink()
        window.refresh()
        QApplication.processEvents()

        assert window._rows == {}
        assert window.empty_label.isVisibleTo(window)

    def test_height_grows_with_more_rows(self, status_dir, make_window):
        write_session(status_dir, "one", "running")
        window = make_window()
        window.show()
        QApplication.processEvents()
        window._fit_height_to_content()
        height_one = window.height()

        for name in ("two", "three", "four"):
            write_session(status_dir, name, "running")
        window.refresh()
        QApplication.processEvents()
        window._fit_height_to_content()

        assert window.height() > height_one


class TestPermissionState:
    def test_signal_tracks_permission_sessions(self, status_dir, make_window):
        write_session(status_dir, "alpha", "running")
        window = make_window()

        received = []
        window.permission_state_changed.connect(received.append)

        window.refresh()
        assert received == [False]

        write_session(status_dir, "beta", "permission", alert="Bash needs approval")
        window.refresh()
        assert received == [False, True]

        (status_dir / "beta.json").unlink()
        window.refresh()
        assert received == [False, True, False]

    def test_permission_row_shows_alert_and_blinks(self, qapp):
        from ui.session_row import SessionRow

        session = make_session(
            status="permission",
            display_status="permission",
            alert="Claude needs your permission to run Bash",
        )
        row = SessionRow(session)
        row.resize(400, 48)
        row.show()
        QApplication.processEvents()

        assert row.task_label.text().startswith("Claude needs your permission")

        row.set_blink(True)
        assert row.status_dot.property("blink") == "on"
        row.set_blink(False)
        assert row.status_dot.property("blink") == "off"
        row.deleteLater()

    def test_non_permission_row_never_blinks(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session())
        row.set_blink(True)
        assert row.status_dot.property("blink") == "off"
        row.deleteLater()


class TestTaskElision:
    LONG_TASK = "implement the extremely long task description that cannot possibly fit " * 3

    def test_narrow_row_elides_and_wide_row_recovers(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(task=self.LONG_TASK))
        row.show()

        row.resize(160, 48)
        QApplication.processEvents()
        narrow_text = row.task_label.text()
        assert narrow_text.endswith("…")
        assert len(narrow_text) < len(self.LONG_TASK)

        row.resize(900, 48)
        QApplication.processEvents()
        wide_text = row.task_label.text()
        assert len(wide_text) > len(narrow_text)
        row.deleteLater()


class TestTrayIcon:
    def test_colors_reflect_state(self, qapp):
        import app as app_module

        for color in (app_module.TRAY_COLOR_OK, app_module.TRAY_COLOR_PERMISSION):
            icon = app_module.make_tray_icon(color)
            image = icon.pixmap(64, 64).toImage()
            assert image.pixelColor(32, 32) == QColor(color)

    def test_default_is_ok_color(self, qapp):
        import app as app_module

        image = app_module.make_tray_icon().pixmap(64, 64).toImage()
        assert image.pixelColor(32, 32) == QColor(app_module.TRAY_COLOR_OK)
