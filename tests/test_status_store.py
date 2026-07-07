"""Tests for the widget's status-file reader (status_store.py)."""

import json
import time

import pytest

import status_store


@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    directory = tmp_path / "widget-status"
    directory.mkdir()
    monkeypatch.setattr(status_store, "STATUS_DIR", directory)
    return directory


def write_session(status_dir, session_id, status, age_seconds, **extra):
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


class TestGetSessions:
    def test_permission_sorts_first_then_active_then_finished(self, status_dir):
        write_session(status_dir, "fin", "finished", 5)
        write_session(status_dir, "run", "running", 10)
        write_session(status_dir, "perm", "permission", 300)
        write_session(status_dir, "idle", "idle", 20)

        order = [s.session_id for s in status_store.get_sessions()]
        assert order[0] == "perm"  # permission wins despite being oldest
        assert order[1:3] == ["run", "idle"]  # active, most recent first
        assert order[3] == "fin"

    def test_running_goes_stale_after_threshold(self, status_dir):
        write_session(status_dir, "old", "running", status_store.STALE_AFTER_SECONDS + 60)
        write_session(status_dir, "fresh", "running", 5)

        by_id = {s.session_id: s for s in status_store.get_sessions()}
        assert by_id["old"].display_status == "stale"
        assert by_id["fresh"].display_status == "running"

    def test_ancient_files_are_pruned(self, status_dir):
        write_session(status_dir, "ancient", "finished", status_store.PRUNE_AFTER_SECONDS + 60)
        assert status_store.get_sessions() == []
        assert not (status_dir / "ancient.json").exists()

    def test_underscore_and_corrupt_files_are_skipped(self, status_dir):
        (status_dir / "_window.json").write_text('{"x": 1}', encoding="utf-8")
        (status_dir / "corrupt.json").write_text("{not json", encoding="utf-8")
        write_session(status_dir, "ok", "running", 5)

        sessions = status_store.get_sessions()
        assert [s.session_id for s in sessions] == ["ok"]

    def test_tokens_and_shell_pid_are_loaded(self, status_dir):
        write_session(
            status_dir, "t", "finished", 5,
            tokens={"input": 10, "output": 200}, shellPid=1234,
        )
        session = status_store.get_sessions()[0]
        assert session.tokens_in == 10
        assert session.tokens_out == 200
        assert session.shell_pid == 1234
        assert session.has_tokens

    def test_missing_tokens_default_to_zero(self, status_dir):
        write_session(status_dir, "t", "running", 5)
        session = status_store.get_sessions()[0]
        assert not session.has_tokens
        assert session.shell_pid == 0


class TestClearFinished:
    def test_removes_only_finished(self, status_dir):
        write_session(status_dir, "fin", "finished", 5)
        write_session(status_dir, "run", "running", 5)

        status_store.clear_finished()
        remaining = [s.session_id for s in status_store.get_sessions()]
        assert remaining == ["run"]


class TestFormatting:
    def test_format_tokens(self):
        assert status_store.format_tokens(0) == "0"
        assert status_store.format_tokens(999) == "999"
        assert status_store.format_tokens(1500) == "1.5k"
        assert status_store.format_tokens(30250) == "30k"
        assert status_store.format_tokens(2_400_000) == "2.4M"

    def test_format_relative(self):
        assert status_store.format_relative(2) == "just now"
        assert status_store.format_relative(45) == "45s ago"
        assert status_store.format_relative(120) == "2m ago"
        assert status_store.format_relative(7200) == "2h ago"
        assert status_store.format_relative(180000) == "2d ago"
