"""Tests for history CSV logging (hooks/widget_status.py and hooks/antigravity_status.py)."""

import csv
import json
import time
from pathlib import Path

import pytest

import antigravity_status as ag
import widget_status as ws


@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    directory = tmp_path / "widget-status"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ws, "STATUS_DIR", directory)
    monkeypatch.setattr(ag, "STATUS_DIR", directory)
    csv_file = directory / "history.csv"
    monkeypatch.setattr(ws, "HISTORY_CSV_PATH", csv_file)
    monkeypatch.setattr(ag, "HISTORY_CSV_PATH", csv_file)
    return directory


class TestHistoryCSV:
    def test_creates_file_with_headers_on_first_turn(self, status_dir):
        record = {
            "project": "my-project",
            "model": "claude-sonnet-5-20250929",
            "turnDuration": 8.5,
            "turnStartedAt": 1000.0,
            "prompt": "Fix database connection",
            "tokens": {"input": 1200, "output": 450},
            "contextTokens": 45000,
        }
        ws.append_to_history(record, tool="claude")

        csv_path = status_dir / "history.csv"
        assert csv_path.exists()

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))

        assert len(reader) == 2  # header + 1 row
        assert reader[0] == ws.HISTORY_CSV_HEADERS
        row = reader[1]
        assert row[1] == "my-project"
        assert row[2] == "claude"
        assert row[3] == "claude-sonnet-5-20250929"
        assert row[4] == "8.5"
        assert row[5] == "Fix database connection"
        assert row[6] == "1200"
        assert row[7] == "450"
        assert row[8] == "45000"

    def test_appends_and_does_not_replace(self, status_dir):
        record1 = {
            "project": "proj-1",
            "model": "claude-sonnet-5-20250929",
            "turnDuration": 4.2,
            "turnStartedAt": 100.0,
            "prompt": "first task",
        }
        record2 = {
            "project": "proj-2",
            "model": "gemini-2.5-pro",
            "turnDuration": 12.0,
            "turnStartedAt": 200.0,
            "prompt": "second task, with commas and \"quotes\"",
        }

        ws.append_to_history(record1, tool="claude")
        ag.append_to_history(record2, tool="antigravity")

        csv_path = status_dir / "history.csv"
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))

        assert len(reader) == 3  # header + 2 data rows
        assert reader[1][1] == "proj-1"
        assert reader[1][2] == "claude"
        assert reader[1][4] == "4.2"
        assert reader[1][5] == "first task"

        assert reader[2][1] == "proj-2"
        assert reader[2][2] == "antigravity"
        assert reader[2][4] == "12.0"
        assert reader[2][5] == "second task, with commas and \"quotes\""

    def test_turn_deduplication_prevents_duplicate_csv_rows(self, status_dir):
        record = {
            "project": "proj",
            "turnDuration": 5.0,
            "turnStartedAt": 500.0,
            "prompt": "single task",
        }
        ws.append_to_history(record, tool="claude")
        ws.append_to_history(record, tool="claude")  # duplicate call with same record

        csv_path = status_dir / "history.csv"
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))

        assert len(reader) == 2  # header + only 1 row

    def test_handles_multiline_prompt_properly(self, status_dir):
        record = {
            "project": "proj",
            "turnDuration": 2.0,
            "turnStartedAt": 600.0,
            "prompt": "Line 1\nLine 2\nLine 3",
        }
        ws.append_to_history(record, tool="claude")

        csv_path = status_dir / "history.csv"
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))

        assert len(reader) == 2
        assert reader[1][5] == "Line 1\nLine 2\nLine 3"

    def test_widget_status_handle_stop_writes_history(self, status_dir, monkeypatch):
        t0 = 1000.0
        monkeypatch.setattr(time, "time", lambda: t0)
        ws.handle_prompt_submit("s1", "C:/x/proj", {"prompt": "Implement auth"}, 123)

        monkeypatch.setattr(time, "time", lambda: t0 + 15.3)
        ws.handle_stop("s1", "C:/x/proj", {}, 123)

        session_record = ws.load_existing(status_dir / "s1.json")
        assert session_record["status"] == "finished"
        assert session_record["turnDuration"] == 15.3

        csv_path = status_dir / "history.csv"
        assert csv_path.exists()
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        assert len(reader) == 2
        assert reader[1][1] == "proj"
        assert reader[1][2] == "claude"
        assert reader[1][4] == "15.3"
        assert reader[1][5] == "Implement auth"

    def test_antigravity_status_handle_stop_writes_history(self, status_dir, monkeypatch):
        t0 = 2000.0
        monkeypatch.setattr(time, "time", lambda: t0)
        ag.handle_pre_invocation(
            "ag1",
            "C:/x/agproj",
            {"modelName": "gemini-2.5-pro", "workspacePaths": ["C:/x/agproj"]},
            456,
        )

        record = ag.load_existing(status_dir / "ag1.json")
        record["prompt"] = "Run pytest"
        ag.atomic_write(status_dir / "ag1.json", record)

        monkeypatch.setattr(time, "time", lambda: t0 + 7.8)
        ag.handle_stop("ag1", "C:/x/agproj", {"modelName": "gemini-2.5-pro"}, 456)

        session_record = ag.load_existing(status_dir / "ag1.json")
        assert session_record["status"] == "finished"
        assert session_record["turnDuration"] == 7.8

        csv_path = status_dir / "history.csv"
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        assert len(reader) == 2
        assert reader[1][1] == "agproj"
        assert reader[1][2] == "antigravity"
        assert reader[1][3] == "gemini-2.5-pro"
        assert reader[1][4] == "7.8"
        assert reader[1][5] == "Run pytest"

    def test_antigravity_status_handle_stop_writes_history_with_tokens(
        self, status_dir, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(ag, "spawn_widget", lambda: None)
        transcript = tmp_path / "transcript_full.jsonl"
        steps = [
            json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "Run tests and summarize"}),
            json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "thinking": "Let me run tests", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest"}}]}),
            json.dumps({"step_index": 2, "type": "GENERIC", "content": "10 passed in 0.5s"}),
            json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "content": "All tests passed successfully!"}),
        ]
        transcript.write_text("\n".join(steps) + "\n", encoding="utf-8")

        t0 = 3000.0
        monkeypatch.setattr(time, "time", lambda: t0)
        ag.handle_pre_invocation(
            "ag2",
            "C:/x/myproject",
            {
                "conversationId": "ag2",
                "modelName": "gemini-3.7-flash-medium",
                "transcriptPath": str(transcript),
            },
            789,
        )

        monkeypatch.setattr(time, "time", lambda: t0 + 4.5)
        ag.handle_stop(
            "ag2",
            "C:/x/myproject",
            {
                "conversationId": "ag2",
                "modelName": "gemini-3.7-flash-medium",
                "transcriptPath": str(transcript),
            },
            789,
        )

        csv_path = status_dir / "history.csv"
        assert csv_path.exists()
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))

        assert len(reader) == 2
        row = reader[1]
        assert row[1] == "myproject"
        assert row[2] == "antigravity"
        assert row[3] == "gemini-3.7-flash-medium"
        assert row[4] == "4.5"
        assert int(row[6]) > 0  # tokens_in
        assert int(row[7]) > 0  # tokens_out
        assert int(row[8]) > 0  # context_tokens

