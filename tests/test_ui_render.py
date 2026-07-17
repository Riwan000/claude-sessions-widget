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


class TestCollapse:
    def test_starts_expanded_by_default(self, status_dir, make_window):
        window = make_window()
        assert window._collapsed is False
        assert window.scroll_area.isVisibleTo(window)
        assert window.collapse_button.text() == "▾"

    def test_collapse_hides_list_and_shrinks_height(self, status_dir, make_window):
        write_session(status_dir, "one", "running")
        write_session(status_dir, "two", "running")
        window = make_window()
        window.show()
        QApplication.processEvents()
        window._fit_height_to_content()
        expanded_height = window.height()

        window.set_collapsed(True)
        QApplication.processEvents()

        assert window._collapsed is True
        assert not window.scroll_area.isVisibleTo(window)
        assert window.collapse_button.text() == "▸"
        assert window.height() < expanded_height

    def test_expand_restores_list_and_height(self, status_dir, make_window):
        write_session(status_dir, "one", "running")
        window = make_window()
        window.show()
        QApplication.processEvents()
        window._fit_height_to_content()
        expanded_height = window.height()

        window.set_collapsed(True)
        QApplication.processEvents()
        window.set_collapsed(False)
        QApplication.processEvents()

        assert window._collapsed is False
        assert window.scroll_area.isVisibleTo(window)
        assert window.height() == expanded_height

    def test_collapse_button_click_toggles_state(self, status_dir, make_window):
        window = make_window()
        window.show()
        QApplication.processEvents()

        window.collapse_button.click()
        assert window._collapsed is True

        window.collapse_button.click()
        assert window._collapsed is False

    def test_collapsed_state_persists_across_restart(self, status_dir, make_window):
        window = make_window()
        window.show()
        QApplication.processEvents()
        window.set_collapsed(True)
        window._persist_geometry()

        window2 = make_window()
        assert window2._collapsed is True
        assert not window2.scroll_area.isVisibleTo(window2)

    def test_refresh_keeps_working_while_collapsed(self, status_dir, make_window):
        window = make_window()
        window.set_collapsed(True)

        write_session(status_dir, "alpha", "permission", alert="needs approval")
        window.refresh()
        QApplication.processEvents()

        assert "alpha" in window._rows
        assert "needs permission" in window.count_label.text()


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


class TestCurrentToolDisplay:
    def test_tool_detail_overrides_task_text(self, qapp):
        from ui.session_row import SessionRow

        session = make_session(task="do the thing", current_tool_detail="Reading README.md")
        row = SessionRow(session)
        row.resize(400, 48)
        row.show()
        QApplication.processEvents()

        assert row.task_label.text() == "Reading README.md"
        row.deleteLater()

    def test_permission_alert_still_wins_over_tool_detail(self, qapp):
        from ui.session_row import SessionRow

        session = make_session(
            status="permission",
            display_status="permission",
            alert="Claude needs your permission to run Bash",
            current_tool_detail="Running rm -rf /",
        )
        row = SessionRow(session)
        row.resize(400, 48)
        row.show()
        QApplication.processEvents()

        assert row.task_label.text().startswith("Claude needs your permission")
        row.deleteLater()

    def test_no_tool_detail_falls_back_to_task(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(task="plain task"))
        row.resize(400, 48)
        row.show()
        QApplication.processEvents()

        assert row.task_label.text() == "plain task"
        row.deleteLater()


class TestPersonalityDisplay:
    def test_running_with_no_tool_yet_shows_thinking(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(status="running", display_status="running"))
        assert row.personality_label.text() == "🧠 Thinking"
        row.deleteLater()

    def test_running_with_edit_tool_shows_editing(self, qapp):
        from ui.session_row import SessionRow

        session = make_session(
            status="running", display_status="running", current_tool="Edit"
        )
        row = SessionRow(session)
        assert row.personality_label.text() == "✏️ Editing"
        row.deleteLater()

    def test_unknown_tool_falls_back_to_working(self, qapp):
        from ui.session_row import SessionRow

        session = make_session(
            status="running", display_status="running", current_tool="SomeFutureTool"
        )
        row = SessionRow(session)
        assert row.personality_label.text() == "🔧 Working"
        row.deleteLater()

    def test_idle_shows_idle_tag(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(status="idle", display_status="idle"))
        assert row.personality_label.text() == "💤 Idle"
        row.deleteLater()

    def test_finished_shows_done_tag(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(status="finished", display_status="finished"))
        assert row.personality_label.text() == "✅ Done"
        row.deleteLater()

    def test_permission_shows_no_personality_tag(self, qapp):
        from ui.session_row import SessionRow

        session = make_session(
            status="permission",
            display_status="permission",
            alert="Claude needs your permission to run Bash",
            current_tool="Bash",
        )
        row = SessionRow(session)
        assert row.personality_label.text() == ""
        row.deleteLater()

    def test_stale_shows_no_personality_tag(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(status="running", display_status="stale"))
        assert row.personality_label.text() == ""
        row.deleteLater()

    def test_project_label_ignores_language_icon(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(project="widget", language_icon="🐍"))
        assert row.project_label.text() == "widget"
        row.deleteLater()

    def test_project_label_plain_when_no_language_icon(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(project="widget"))
        assert row.project_label.text() == "widget"
        row.deleteLater()


class TestTokenPill:
    def test_no_tokens_hides_pill(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session())
        assert row.token_label.isVisibleTo(row) is False
        row.deleteLater()

    def test_below_warn_threshold_has_default_tier(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(tokens_in=1000, tokens_out=500))
        assert row.token_label.property("tokenTier") == ""
        row.deleteLater()

    def test_warn_tier_at_100k(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(tokens_in=100_000, tokens_out=0))
        assert row.token_label.property("tokenTier") == "warn"
        row.deleteLater()

    def test_high_tier_at_150k(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(tokens_in=150_000, tokens_out=0))
        assert row.token_label.property("tokenTier") == "high"
        row.deleteLater()

    def test_critical_tier_at_200k(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(tokens_in=200_000, tokens_out=0))
        assert row.token_label.property("tokenTier") == "critical"
        row.deleteLater()


class TestTokenWarningNotification:
    def test_fires_once_when_session_crosses_threshold(self, status_dir, make_window):
        write_session(status_dir, "alpha", "running", project="example-project",
                      contextTokens=10_000)
        window = make_window()

        received = []
        window.token_warning.connect(lambda project, total: received.append((project, total)))

        write_session(status_dir, "alpha", "running", project="example-project",
                      contextTokens=151_300)
        window.refresh()

        assert received == [("example-project", 151_300)]

    def test_does_not_refire_on_subsequent_refreshes(self, status_dir, make_window):
        write_session(status_dir, "alpha", "running", contextTokens=10_000)
        window = make_window()

        received = []
        window.token_warning.connect(lambda project, total: received.append((project, total)))

        write_session(status_dir, "alpha", "running", contextTokens=151_300)
        window.refresh()
        window.refresh()
        window.refresh()

        assert len(received) == 1

    def test_does_not_fire_below_threshold(self, status_dir, make_window):
        write_session(status_dir, "alpha", "running", contextTokens=10_000)
        window = make_window()

        received = []
        window.token_warning.connect(lambda project, total: received.append((project, total)))
        window.refresh()

        assert received == []

    def test_does_not_fire_on_large_single_turn_alone(self, status_dir, make_window):
        # A single huge turn (e.g. reading one large file) shouldn't trip
        # this warning - only the cumulative context size should.
        write_session(status_dir, "alpha", "running",
                      tokens={"input": 200_000, "output": 0})
        window = make_window()

        received = []
        window.token_warning.connect(lambda project, total: received.append((project, total)))
        window.refresh()

        assert received == []

    def test_forgets_session_once_it_disappears(self, status_dir, make_window):
        write_session(status_dir, "alpha", "running", contextTokens=151_300)
        window = make_window()
        assert "alpha" in window._token_warned

        (status_dir / "alpha.json").unlink()
        window.refresh()

        assert "alpha" not in window._token_warned


class TestContextPill:
    def test_no_context_hides_pill(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session())
        assert row.context_label.isVisibleTo(row) is False
        row.deleteLater()

    def test_shows_formatted_context_total(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(context_tokens=82_000))
        assert row.context_label.text() == "82k ctx"
        assert row.context_label.isVisibleTo(row) is True
        row.deleteLater()

    def test_below_warn_threshold_has_default_tier(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(context_tokens=1_000))
        assert row.context_label.property("tokenTier") == ""
        row.deleteLater()

    def test_warn_tier_at_100k(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(context_tokens=100_000))
        assert row.context_label.property("tokenTier") == "warn"
        row.deleteLater()

    def test_critical_tier_at_200k(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(context_tokens=200_000))
        assert row.context_label.property("tokenTier") == "critical"
        row.deleteLater()

    def test_tooltip_includes_context_line(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(context_tokens=82_000))
        assert "Context so far: 82,000 tokens" in row.toolTip()
        row.deleteLater()

    def test_tooltip_omits_context_line_when_absent(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session())
        assert "Context so far" not in row.toolTip()
        row.deleteLater()


class TestModelPill:
    def test_no_model_hides_pill(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session())
        assert row.model_label.isVisibleTo(row) is False
        row.deleteLater()

    def test_shows_short_model_label(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(model="claude-sonnet-5-20250929"))
        assert row.model_label.text() == "Sonnet 5"
        assert row.model_label.isVisibleTo(row) is True
        row.deleteLater()

    def test_tooltip_includes_raw_model_id(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session(model="claude-opus-4-8"))
        assert "Model: claude-opus-4-8" in row.toolTip()
        row.deleteLater()

    def test_tooltip_omits_model_line_when_absent(self, qapp):
        from ui.session_row import SessionRow

        row = SessionRow(make_session())
        assert "Model:" not in row.toolTip()
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


class TestQuitIfIdle:
    def test_quits_when_no_sessions_remain(self, status_dir):
        import app as app_module

        class FakeApp:
            def __init__(self):
                self.quit_called = False

            def quit(self):
                self.quit_called = True

        fake_app = FakeApp()
        app_module.quit_if_idle(fake_app)

        assert fake_app.quit_called is True

    def test_does_not_quit_while_sessions_remain(self, status_dir):
        import app as app_module

        write_session(status_dir, "alpha", "running")

        class FakeApp:
            def __init__(self):
                self.quit_called = False

            def quit(self):
                self.quit_called = True

        fake_app = FakeApp()
        app_module.quit_if_idle(fake_app)

        assert fake_app.quit_called is False


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
