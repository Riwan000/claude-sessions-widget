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


def assistant_entry(message_id, input_tokens, output_tokens, uuid=None, model=None):
    message = {
        "id": message_id,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    if model is not None:
        message["model"] = model
    return {
        "type": "assistant",
        "uuid": uuid or f"uuid-{message_id}-{input_tokens}-{output_tokens}",
        "message": message,
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


class TestLatestContextSize:
    def test_returns_latest_usage_total(self, tmp_path):
        transcript = write_transcript(
            tmp_path / "t.jsonl",
            [
                {
                    "message": {
                        "usage": {
                            "input_tokens": 10,
                            "cache_creation_input_tokens": 20,
                            "cache_read_input_tokens": 30,
                        }
                    }
                },
                {
                    "message": {
                        "usage": {
                            "input_tokens": 1,
                            "cache_creation_input_tokens": 2,
                            "cache_read_input_tokens": 97,
                        }
                    }
                },
            ],
        )
        assert ws.latest_context_size(transcript) == 100

    def test_missing_cache_fields_default_to_zero(self, tmp_path):
        transcript = write_transcript(
            tmp_path / "t.jsonl", [{"message": {"usage": {"input_tokens": 5}}}]
        )
        assert ws.latest_context_size(transcript) == 5

    def test_lines_without_usage_are_skipped(self, tmp_path):
        transcript = write_transcript(
            tmp_path / "t.jsonl",
            [
                {"message": {"usage": {"input_tokens": 42}}},
                {"type": "user", "message": {"role": "user"}},
            ],
        )
        assert ws.latest_context_size(transcript) == 42

    def test_missing_transcript_returns_none(self, tmp_path):
        assert ws.latest_context_size(tmp_path / "missing.jsonl") is None
        assert ws.latest_context_size(None) is None

    def test_no_usage_entries_returns_none(self, tmp_path):
        transcript = write_transcript(
            tmp_path / "t.jsonl", [{"type": "permission-mode", "permissionMode": "auto"}]
        )
        assert ws.latest_context_size(transcript) is None

    def test_only_reads_tail_of_large_transcript(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ws, "CONTEXT_TAIL_BYTES", 200)
        old_line = json.dumps({"message": {"usage": {"input_tokens": 999_999}}})
        padding = " " * 500  # forces the real usage entry past the tail window
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(f"{old_line}\n// {padding}\n", encoding="utf-8")
        with open(transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"message": {"usage": {"input_tokens": 7}}}) + "\n")

        assert ws.latest_context_size(transcript) == 7


class TestLatestModel:
    def test_returns_model_from_latest_entry(self, tmp_path):
        transcript = write_transcript(
            tmp_path / "t.jsonl",
            [
                assistant_entry("msg_1", 5, 100, model="claude-haiku-4-5-20251001"),
                assistant_entry("msg_2", 3, 50, model="claude-sonnet-5-20250929"),
            ],
        )
        assert ws.latest_model(transcript) == "claude-sonnet-5-20250929"

    def test_skips_entries_without_model(self, tmp_path):
        transcript = write_transcript(
            tmp_path / "t.jsonl",
            [
                assistant_entry("msg_1", 5, 100, model="claude-sonnet-5-20250929"),
                assistant_entry("msg_2", 3, 50),
            ],
        )
        assert ws.latest_model(transcript) == "claude-sonnet-5-20250929"

    def test_no_model_anywhere_returns_none(self, tmp_path):
        transcript = write_transcript(tmp_path / "t.jsonl", [assistant_entry("msg_1", 5, 100)])
        assert ws.latest_model(transcript) is None

    def test_missing_transcript_returns_none(self, tmp_path):
        assert ws.latest_model(tmp_path / "missing.jsonl") is None
        assert ws.latest_model(None) is None


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
    @pytest.fixture(autouse=True)
    def no_real_widget_spawn(self, monkeypatch):
        """handle_session_start now launches the widget process - these
        tests must not actually spawn it. test_session_start_spawns_widget
        overrides this patch to assert on calls instead."""
        monkeypatch.setattr(ws, "spawn_widget", lambda: None)

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
            handle.write(
                json.dumps(assistant_entry("msg_1", 7, 70, model="claude-sonnet-5-20250929"))
                + "\n"
            )

        ws.handle_stop("s1", "C:/x/proj", {"transcript_path": str(transcript)}, 42)
        record = self.read_record(status_dir, "s1")
        assert record["status"] == "finished"
        assert record["tokens"] == {"input": 7, "output": 70}
        assert record["contextTokens"] == 7
        assert record["model"] == "claude-sonnet-5-20250929"

        ws.handle_session_end("s1", "C:/x/proj", {}, 42)
        assert not (status_dir / "s1.json").exists()

    def test_session_start_spawns_widget(self, status_dir, monkeypatch):
        calls = []
        monkeypatch.setattr(ws, "spawn_widget", lambda: calls.append(True))

        ws.handle_session_start("s1", "C:/x/proj", {}, 42)

        assert calls == [True]

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

    def test_stop_without_transcript_skips_context_tokens(self, status_dir):
        ws.handle_session_start("s2b", "C:/x/proj", {}, 42)
        ws.handle_stop("s2b", "C:/x/proj", {}, 42)
        record = self.read_record(status_dir, "s2b")
        assert record["status"] == "finished"
        assert "contextTokens" not in record
        assert "model" not in record

    def test_stop_records_model_from_transcript(self, status_dir, tmp_path):
        transcript = write_transcript(
            tmp_path / "t.jsonl",
            [assistant_entry("msg_1", 5, 40, model="claude-opus-4-8")],
        )
        ws.handle_session_start("s2c", "C:/x/proj", {}, 42)
        ws.handle_stop("s2c", "C:/x/proj", {"transcript_path": str(transcript)}, 42)
        assert self.read_record(status_dir, "s2c")["model"] == "claude-opus-4-8"

    def test_stop_keeps_previous_model_when_turn_has_none(self, status_dir, tmp_path):
        # e.g. a turn with no assistant usage entry yet - shouldn't blank out
        # a model recorded on an earlier turn.
        transcript = write_transcript(tmp_path / "t.jsonl", [])
        ws.handle_session_start("s2d", "C:/x/proj", {}, 42)
        path = ws.status_path("s2d")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["model"] = "claude-sonnet-5-20250929"
        ws.atomic_write(path, record)

        ws.handle_stop("s2d", "C:/x/proj", {"transcript_path": str(transcript)}, 42)
        assert self.read_record(status_dir, "s2d")["model"] == "claude-sonnet-5-20250929"

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

    def test_tool_start_records_read_of_named_file(self, status_dir):
        ws.handle_session_start("s5", "C:/x/proj", {}, 42)
        ws.handle_tool_start(
            "s5", "C:/x/proj",
            {"tool_name": "Read", "tool_input": {"file_path": "C:/x/proj/README.md"}},
            42,
        )
        record = self.read_record(status_dir, "s5")
        assert record["currentTool"] == "Read"
        assert record["currentToolDetail"] == "Reading README.md"
        assert record["status"] == "running"

    def test_tool_start_records_edit_of_named_file(self, status_dir):
        ws.handle_session_start("s6", "C:/x/proj", {}, 42)
        ws.handle_tool_start(
            "s6", "C:/x/proj",
            {"tool_name": "Edit", "tool_input": {"file_path": "C:/x/proj/app.py"}},
            42,
        )
        assert self.read_record(status_dir, "s6")["currentToolDetail"] == "Editing app.py"

    def test_tool_start_records_bash_command(self, status_dir):
        ws.handle_session_start("s7", "C:/x/proj", {}, 42)
        ws.handle_tool_start(
            "s7", "C:/x/proj",
            {"tool_name": "Bash", "tool_input": {"command": "pytest tests/"}},
            42,
        )
        assert self.read_record(status_dir, "s7")["currentToolDetail"] == "Running pytest tests/"

    def test_tool_start_unknown_tool_uses_generic_fallback(self, status_dir):
        ws.handle_session_start("s8", "C:/x/proj", {}, 42)
        ws.handle_tool_start(
            "s8", "C:/x/proj", {"tool_name": "SomeFutureTool", "tool_input": {}}, 42
        )
        assert self.read_record(status_dir, "s8")["currentToolDetail"] == "Using SomeFutureTool"

    def test_stop_clears_current_tool(self, status_dir):
        ws.handle_session_start("s9", "C:/x/proj", {}, 42)
        ws.handle_tool_start(
            "s9", "C:/x/proj", {"tool_name": "Read", "tool_input": {"file_path": "a.py"}}, 42
        )
        ws.handle_stop("s9", "C:/x/proj", {}, 42)
        record = self.read_record(status_dir, "s9")
        assert record["currentTool"] == ""
        assert record["currentToolDetail"] == ""

    def test_prompt_submit_clears_stale_tool_from_previous_turn(self, status_dir):
        ws.handle_session_start("s10", "C:/x/proj", {}, 42)
        ws.handle_tool_start(
            "s10", "C:/x/proj", {"tool_name": "Bash", "tool_input": {"command": "pytest"}}, 42
        )
        ws.handle_prompt_submit("s10", "C:/x/proj", {"prompt": "next task"}, 42)
        assert self.read_record(status_dir, "s10")["currentToolDetail"] == ""

    def test_session_start_records_language_icon(self, status_dir, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

        ws.handle_session_start("s11", str(project), {}, 42)

        assert self.read_record(status_dir, "s11")["languageIcon"] == "🐍"

    def test_session_start_keeps_cached_language_icon_on_resume(self, status_dir, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

        ws.handle_session_start("s12", str(project), {}, 42)
        (project / "package.json").write_text("", encoding="utf-8")
        ws.handle_session_start("s12", str(project), {}, 42)

        # Cached at first SessionStart - a later marker file appearing (or a
        # second SessionStart on a resumed session) must not flip it.
        assert self.read_record(status_dir, "s12")["languageIcon"] == "🐍"

    def test_session_start_unknown_project_has_no_language_icon(self, status_dir, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()

        ws.handle_session_start("s13", str(project), {}, 42)

        assert self.read_record(status_dir, "s13")["languageIcon"] == ""


class TestDescribeToolUse:
    def test_read_without_file_path_uses_generic_phrase(self):
        assert ws.describe_tool_use("Read", {}) == "Reading a file"

    def test_bash_without_command_uses_generic_phrase(self):
        assert ws.describe_tool_use("Bash", {}) == "Running a command"

    def test_bash_cd_shortens_to_folder_name(self):
        command = 'cd "C:/Users/dev/Desktop/Claude_space/widget"'
        assert ws.describe_tool_use("Bash", {"command": command}) == "cd widget/"

    def test_bash_cd_unquoted_shortens_to_folder_name(self):
        assert ws.describe_tool_use("Bash", {"command": "cd widget"}) == "cd widget/"

    def test_bash_cd_chained_keeps_remaining_command(self):
        command = 'cd "C:/Users/dev/Desktop/widget" && pytest tests/'
        assert ws.describe_tool_use("Bash", {"command": command}) == "cd widget/ && pytest tests/"

    def test_grep_includes_pattern(self):
        assert ws.describe_tool_use("Grep", {"pattern": "TODO"}) == "Searching for TODO"

    def test_non_dict_tool_input_does_not_raise(self):
        assert ws.describe_tool_use("Read", None) == "Reading a file"

    def test_empty_tool_name_uses_generic_working_label(self):
        assert ws.describe_tool_use("", {}) == "Working"


class TestDetectLanguageIcon:
    def test_pyproject_toml_is_python(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        assert ws.detect_language_icon(tmp_path) == "🐍"

    def test_requirements_txt_is_python(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
        assert ws.detect_language_icon(tmp_path) == "🐍"

    def test_loose_py_file_is_python(self, tmp_path):
        (tmp_path / "app.py").write_text("", encoding="utf-8")
        assert ws.detect_language_icon(tmp_path) == "🐍"

    def test_package_json_is_node(self, tmp_path):
        (tmp_path / "package.json").write_text("", encoding="utf-8")
        assert ws.detect_language_icon(tmp_path) == "📦"

    def test_cargo_toml_is_rust(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
        assert ws.detect_language_icon(tmp_path) == "🦀"

    def test_go_mod_is_go(self, tmp_path):
        (tmp_path / "go.mod").write_text("", encoding="utf-8")
        assert ws.detect_language_icon(tmp_path) == "🐹"

    def test_no_markers_returns_empty(self, tmp_path):
        (tmp_path / "notes.txt").write_text("", encoding="utf-8")
        assert ws.detect_language_icon(tmp_path) == ""

    def test_missing_directory_returns_empty(self, tmp_path):
        assert ws.detect_language_icon(tmp_path / "does-not-exist") == ""


class TestSpawnWidget:
    def test_launches_pythonw_with_app_path(self, tmp_path, monkeypatch):
        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("", encoding="utf-8")
        app_script = tmp_path / "app.py"
        app_script.write_text("", encoding="utf-8")
        monkeypatch.setattr(ws, "WIDGET_PYTHONW", pythonw)
        monkeypatch.setattr(ws, "WIDGET_APP", app_script)

        calls = []
        monkeypatch.setattr(
            ws.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs))
        )

        ws.spawn_widget()

        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args == [str(pythonw), str(app_script)]
        assert kwargs["creationflags"] & ws.subprocess.DETACHED_PROCESS

    def test_missing_pythonw_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ws, "WIDGET_PYTHONW", tmp_path / "missing-pythonw.exe")
        monkeypatch.setattr(ws, "WIDGET_APP", tmp_path / "app.py")

        calls = []
        monkeypatch.setattr(
            ws.subprocess, "Popen", lambda *a, **k: calls.append((a, k))
        )

        ws.spawn_widget()

        assert calls == []

    def test_missing_app_script_is_noop(self, tmp_path, monkeypatch):
        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("", encoding="utf-8")
        monkeypatch.setattr(ws, "WIDGET_PYTHONW", pythonw)
        monkeypatch.setattr(ws, "WIDGET_APP", tmp_path / "missing-app.py")

        calls = []
        monkeypatch.setattr(
            ws.subprocess, "Popen", lambda *a, **k: calls.append((a, k))
        )

        ws.spawn_widget()

        assert calls == []


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
