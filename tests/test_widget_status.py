"""Tests for the Claude Code hook script (hooks/widget_status.py)."""

import json

import pytest

import widget_status as ws


@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    directory = tmp_path / "widget-status"
    monkeypatch.setattr(ws, "STATUS_DIR", directory)
    return directory


def write_transcript(path, entries):
    lines = [json.dumps(entry) for entry in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def assistant_entry(message_id, input_tokens, output_tokens, uuid=None):
    return {
        "type": "assistant",
        "uuid": uuid or f"uuid-{message_id}-{input_tokens}-{output_tokens}",
        "message": {
            "id": message_id,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    }


class TestSumTurnTokens:
    def test_dedupes_lines_sharing_one_message_id(self, tmp_path):
        # One API message split across two JSONL lines (thinking + tool_use)
        # repeats the same usage object - it must be counted once.
        transcript = write_transcript(
            tmp_path / "t.jsonl",
            [
                assistant_entry("msg_1", 5, 400, uuid="a"),
                assistant_entry("msg_1", 5, 400, uuid="b"),
            ],
        )
        assert ws.sum_turn_tokens(transcript, 0) == {"input": 5, "output": 400}

    def test_sums_distinct_messages(self, tmp_path):
        transcript = write_transcript(
            tmp_path / "t.jsonl",
            [
                assistant_entry("msg_1", 5, 100),
                assistant_entry("msg_2", 3, 50),
            ],
        )
        assert ws.sum_turn_tokens(transcript, 0) == {"input": 8, "output": 150}

    def test_entries_without_message_id_count_individually(self, tmp_path):
        entries = [
            {
                "uuid": "u1",
                "message": {"usage": {"input_tokens": 1, "output_tokens": 10}},
            },
            {
                "uuid": "u2",
                "message": {"usage": {"input_tokens": 2, "output_tokens": 20}},
            },
        ]
        transcript = write_transcript(tmp_path / "t.jsonl", entries)
        assert ws.sum_turn_tokens(transcript, 0) == {"input": 3, "output": 30}

    def test_offset_skips_earlier_turns(self, tmp_path):
        old_line = json.dumps(assistant_entry("msg_old", 999, 999))
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(old_line + "\n", encoding="utf-8")
        offset = transcript.stat().st_size
        with open(transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(assistant_entry("msg_new", 4, 40)) + "\n")

        assert ws.sum_turn_tokens(transcript, offset) == {"input": 4, "output": 40}

    def test_none_offset_returns_none(self, tmp_path):
        # A resumed session has no recorded offset; summing from 0 would
        # report the entire transcript as one turn.
        transcript = write_transcript(
            tmp_path / "t.jsonl", [assistant_entry("msg_1", 5, 100)]
        )
        assert ws.sum_turn_tokens(transcript, None) is None

    def test_missing_transcript_returns_none(self, tmp_path):
        assert ws.sum_turn_tokens(tmp_path / "missing.jsonl", 0) is None
        assert ws.sum_turn_tokens(None, 0) is None

    def test_lines_without_usage_are_ignored(self, tmp_path):
        entries = [
            {"type": "user", "uuid": "u1", "message": {"role": "user"}},
            {"type": "permission-mode", "permissionMode": "auto"},
        ]
        transcript = write_transcript(tmp_path / "t.jsonl", entries)
        assert ws.sum_turn_tokens(transcript, 0) is None


class TestFindStableAncestorPid:
    def test_walks_up_to_claude_exe(self, monkeypatch):
        processes = {
            100: (50, "cmd.exe"),      # ephemeral hook wrapper
            50: (10, "claude.exe"),    # the CLI itself
            10: (1, "powershell.exe"),
        }
        monkeypatch.setattr(ws, "_snapshot_processes", lambda: processes)
        assert ws.find_stable_ancestor_pid(100) == 50

    def test_start_pid_already_claude(self, monkeypatch):
        processes = {50: (10, "claude.exe"), 10: (1, "powershell.exe")}
        monkeypatch.setattr(ws, "_snapshot_processes", lambda: processes)
        assert ws.find_stable_ancestor_pid(50) == 50

    def test_no_claude_in_chain_falls_back_to_start(self, monkeypatch):
        processes = {100: (10, "cmd.exe"), 10: (1, "powershell.exe")}
        monkeypatch.setattr(ws, "_snapshot_processes", lambda: processes)
        assert ws.find_stable_ancestor_pid(100) == 100

    def test_unknown_pid_falls_back_to_start(self, monkeypatch):
        monkeypatch.setattr(ws, "_snapshot_processes", lambda: {})
        assert ws.find_stable_ancestor_pid(123) == 123

    def test_parent_cycle_terminates(self, monkeypatch):
        processes = {100: (200, "a.exe"), 200: (100, "b.exe")}
        monkeypatch.setattr(ws, "_snapshot_processes", lambda: processes)
        assert ws.find_stable_ancestor_pid(100) == 100


class TestHandlers:
    def read_record(self, status_dir, session_id):
        return json.loads((status_dir / f"{session_id}.json").read_text(encoding="utf-8"))

    def test_lifecycle_idle_running_finished_removed(self, status_dir, tmp_path):
        transcript = write_transcript(
            tmp_path / "t.jsonl", [assistant_entry("msg_0", 1, 1)]
        )
        ws.handle_session_start("s1", "C:/x/proj", {}, 42)
        record = self.read_record(status_dir, "s1")
        assert record["status"] == "idle"
        assert record["project"] == "proj"
        assert record["shellPid"] == 42

        ws.handle_prompt_submit(
            "s1", "C:/x/proj", {"prompt": "do it", "transcript_path": str(transcript)}, 42
        )
        record = self.read_record(status_dir, "s1")
        assert record["status"] == "running"
        assert record["task"] == "do it"
        assert record["transcriptOffset"] == transcript.stat().st_size

        with open(transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(assistant_entry("msg_1", 7, 70)) + "\n")

        ws.handle_stop("s1", "C:/x/proj", {"transcript_path": str(transcript)}, 42)
        record = self.read_record(status_dir, "s1")
        assert record["status"] == "finished"
        assert record["tokens"] == {"input": 7, "output": 70}

        ws.handle_session_end("s1", "C:/x/proj", {}, 42)
        assert not (status_dir / "s1.json").exists()

    def test_stop_without_offset_skips_tokens(self, status_dir, tmp_path):
        # Resumed session: Stop fires but prompt-submit never recorded an
        # offset - the whole transcript must NOT be summed as one turn.
        transcript = write_transcript(
            tmp_path / "t.jsonl", [assistant_entry("msg_1", 999, 999)]
        )
        ws.handle_session_start("s2", "C:/x/proj", {}, 42)
        ws.handle_stop("s2", "C:/x/proj", {"transcript_path": str(transcript)}, 42)
        record = self.read_record(status_dir, "s2")
        assert record["status"] == "finished"
        assert "tokens" not in record

    def test_tool_complete_only_clears_permission(self, status_dir):
        ws.handle_session_start("s3", "C:/x/proj", {}, 42)
        ws.handle_stop("s3", "C:/x/proj", {}, 42)

        # Async race: a late PostToolUse landing after Stop must not flip
        # a finished session back to running.
        ws.handle_tool_complete("s3", "C:/x/proj", {}, 42)
        assert self.read_record(status_dir, "s3")["status"] == "finished"

        ws.handle_notification(
            "s3", "C:/x/proj", {"message": "Claude needs your permission to use Bash"}, 42
        )
        assert self.read_record(status_dir, "s3")["status"] == "permission"

        ws.handle_tool_complete("s3", "C:/x/proj", {}, 42)
        record = self.read_record(status_dir, "s3")
        assert record["status"] == "running"
        assert record["alert"] == ""

    def test_tool_complete_without_record_is_noop(self, status_dir):
        ws.handle_tool_complete("ghost", "C:/x/proj", {}, 42)
        assert not (status_dir / "ghost.json").exists()

    def test_notification_ignores_non_permission_messages(self, status_dir):
        ws.handle_session_start("s4", "C:/x/proj", {}, 42)
        ws.handle_notification("s4", "C:/x/proj", {"message": "Claude is waiting for input"}, 42)
        assert self.read_record(status_dir, "s4")["status"] == "idle"


class TestHelpers:
    def test_sanitize_session_id(self):
        assert ws.sanitize_session_id("abc-123") == "abc-123"
        assert ws.sanitize_session_id("a/b\\c:d") == "a-b-c-d"
        assert ws.sanitize_session_id("") is None
        assert ws.sanitize_session_id("///") is None

    def test_truncate(self):
        assert ws.truncate("short") == "short"
        long_text = "word " * 100
        result = ws.truncate(long_text)
        assert len(result) <= ws.MAX_TASK_CHARS
        assert result.endswith("…")
        assert ws.truncate("  spaced\n\nout  ") == "spaced out"
