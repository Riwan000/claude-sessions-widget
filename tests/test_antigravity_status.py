"""Tests for the Antigravity hook script (hooks/antigravity_status.py)."""

import json
import pytest

import antigravity_status as ags


@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    directory = tmp_path / "widget-status"
    monkeypatch.setattr(ags, "STATUS_DIR", directory)
    return directory


class TestDescribeToolUse:
    def test_view_file(self):
        assert (
            ags.describe_tool_use("view_file", {"AbsolutePath": "C:/proj/README.md"})
            == "Reading README.md"
        )

    def test_replace_file_content(self):
        assert (
            ags.describe_tool_use("replace_file_content", {"TargetFile": "C:/proj/app.py"})
            == "Editing app.py"
        )

    def test_write_to_file(self):
        assert (
            ags.describe_tool_use("write_to_file", {"TargetFile": "C:/proj/new.py"})
            == "Writing new.py"
        )

    def test_run_command_shortens_cd(self):
        cmd = 'cd "C:/Users/dev/Desktop/Claude_space/widget" && pytest tests/'
        assert (
            ags.describe_tool_use("run_command", {"CommandLine": cmd})
            == "cd widget/ && pytest tests/"
        )

    def test_run_command_plain(self):
        assert (
            ags.describe_tool_use("run_command", {"CommandLine": "npm test"})
            == "Running npm test"
        )

    def test_grep_search(self):
        assert (
            ags.describe_tool_use("grep_search", {"Query": "def handle_stop"})
            == "Searching for def handle_stop"
        )

    def test_find_by_name(self):
        assert (
            ags.describe_tool_use("find_by_name", {"Pattern": "*.py"})
            == "Finding *.py"
        )

    def test_read_url_content(self):
        assert (
            ags.describe_tool_use("read_url_content", {"Url": "https://example.com/api"})
            == "Fetching https://example.com/api"
        )

    def test_search_web(self):
        assert (
            ags.describe_tool_use("search_web", {"query": "python PySide6 docs"})
            == "Searching python PySide6 docs"
        )

    def test_invoke_subagent(self):
        subagents = [{"Role": "Codebase Researcher", "TypeName": "research"}]
        assert (
            ags.describe_tool_use("invoke_subagent", {"Subagents": subagents})
            == "Delegating: Codebase Researcher"
        )

    def test_define_subagent(self):
        assert (
            ags.describe_tool_use("define_subagent", {"name": "debugger"})
            == "Defining subagent debugger"
        )

    def test_manage_subagents(self):
        assert (
            ags.describe_tool_use("manage_subagents", {"Action": "list"})
            == "Managing subagents (list)"
        )

    def test_manage_task(self):
        assert (
            ags.describe_tool_use("manage_task", {"Action": "kill"})
            == "Managing task (kill)"
        )

    def test_schedule(self):
        assert ags.describe_tool_use("schedule", {}) == "Scheduling task"

    def test_ask_question(self):
        assert ags.describe_tool_use("ask_question", {}) == "Asking a question"

    def test_generate_image(self):
        assert (
            ags.describe_tool_use("generate_image", {"ImageName": "app_mockup"})
            == "Generating image app_mockup"
        )

    def test_unknown_tool_fallback(self):
        assert ags.describe_tool_use("custom_mcp_tool", {}) == "Using custom_mcp_tool"


class TestExtractLatestPrompt:
    def test_extracts_user_request_tag(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>\nfix the tests\n</USER_REQUEST>\n<META>data</META>"}),
            json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "tool_calls": []}),
        ]
        transcript.write_text("\n".join(lines), encoding="utf-8")
        assert ags.extract_latest_prompt(transcript) == "fix the tests"

    def test_extracts_plain_user_input(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "hello world"}),
        ]
        transcript.write_text("\n".join(lines), encoding="utf-8")
        assert ags.extract_latest_prompt(transcript) == "hello world"

    def test_missing_transcript_returns_empty(self, tmp_path):
        assert ags.extract_latest_prompt(tmp_path / "missing.jsonl") == ""
        assert ags.extract_latest_prompt(None) == ""


class TestFindStableAncestorPid:
    def test_walks_up_to_antigravity_exe(self, monkeypatch):
        processes = {
            100: (50, "cmd.exe"),
            50: (10, "antigravity.exe"),
            10: (1, "powershell.exe"),
        }
        monkeypatch.setattr(ags, "_snapshot_processes", lambda: processes)
        assert ags.find_stable_ancestor_pid(100) == 50

    def test_walks_up_to_agy_exe(self, monkeypatch):
        processes = {
            100: (50, "cmd.exe"),
            50: (10, "agy.exe"),
            10: (1, "powershell.exe"),
        }
        monkeypatch.setattr(ags, "_snapshot_processes", lambda: processes)
        assert ags.find_stable_ancestor_pid(100) == 50


class TestHandlers:
    @pytest.fixture(autouse=True)
    def no_real_widget_spawn(self, monkeypatch):
        monkeypatch.setattr(ags, "spawn_widget", lambda: None)

    def read_record(self, status_dir, session_id):
        return json.loads((status_dir / f"{session_id}.json").read_text(encoding="utf-8"))

    def test_lifecycle_pre_invocation_tool_use_stop(self, status_dir, tmp_path, capsys):
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>build the app</USER_REQUEST>"}),
        ]
        transcript.write_text("\n".join(lines), encoding="utf-8")

        # 1. PreInvocation
        payload_inv = {
            "conversationId": "conv-1",
            "workspacePaths": ["C:/projects/myapp"],
            "transcriptPath": str(transcript),
            "modelName": "Gemini 3.7 Flash",
        }
        ags.handle_pre_invocation("conv-1", "C:/projects/myapp", payload_inv, 123)
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"injectSteps": []}

        record = self.read_record(status_dir, "conv-1")
        assert record["status"] == "running"
        assert record["tool"] == "antigravity"
        assert record["project"] == "myapp"
        assert record["task"] == "build the app"
        assert record["model"] == "Gemini 3.7 Flash"
        assert record["shellPid"] == 123

        # 2. PreToolUse
        payload_tool = {
            "conversationId": "conv-1",
            "toolCall": {
                "name": "replace_file_content",
                "args": {"TargetFile": "C:/projects/myapp/main.py"},
            },
        }
        ags.handle_pre_tool_use("conv-1", "C:/projects/myapp", payload_tool, 123)
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"decision": "allow"}

        record = self.read_record(status_dir, "conv-1")
        assert record["currentTool"] == "replace_file_content"
        assert record["currentToolDetail"] == "Editing main.py"

        # 3. PostToolUse
        payload_post = {"conversationId": "conv-1"}
        ags.handle_post_tool_use("conv-1", "C:/projects/myapp", payload_post, 123)
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {}

        # 4. Stop
        payload_stop = {
            "conversationId": "conv-1",
            "modelName": "Gemini 3.7 Flash",
        }
        ags.handle_stop("conv-1", "C:/projects/myapp", payload_stop, 123)
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"decision": "allow"}

        record = self.read_record(status_dir, "conv-1")
        assert record["status"] == "finished"
        assert record["currentTool"] == ""
        assert record["currentToolDetail"] == ""
