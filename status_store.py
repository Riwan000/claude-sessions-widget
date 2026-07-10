"""Reads and interprets the status files written by hooks/widget_status.py."""

import json
import time
from dataclasses import dataclass
from pathlib import Path

STATUS_DIR = Path.home() / ".claude" / "widget-status"
STALE_AFTER_SECONDS = 10 * 60
PRUNE_AFTER_SECONDS = 24 * 60 * 60

ACTIVE_STATUSES = {"idle", "running", "permission"}
STATUS_RANK = {"permission": 0, "running": 1, "idle": 1}


@dataclass
class Session:
    session_id: str
    project: str
    cwd: str
    task: str
    status: str
    display_status: str
    started_at: float
    updated_at: float
    alert: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    shell_pid: int = 0
    current_tool: str = ""
    current_tool_detail: str = ""
    language_icon: str = ""

    @property
    def has_tokens(self):
        return self.tokens_in > 0 or self.tokens_out > 0

    @property
    def age_seconds(self):
        return max(0.0, time.time() - self.updated_at)


def _load_one(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    session_id = data.get("sessionId")
    updated_at = data.get("updatedAt")
    if not session_id or not isinstance(updated_at, (int, float)):
        return None

    age = time.time() - updated_at
    if age > PRUNE_AFTER_SECONDS:
        path.unlink(missing_ok=True)
        return None

    status = data.get("status", "idle")
    display_status = status
    if status == "running" and age > STALE_AFTER_SECONDS:
        display_status = "stale"

    tokens = data.get("tokens") or {}

    return Session(
        session_id=session_id,
        project=data.get("project") or "unknown",
        cwd=data.get("cwd", ""),
        task=data.get("task", ""),
        status=status,
        display_status=display_status,
        started_at=data.get("startedAt", updated_at),
        updated_at=updated_at,
        alert=data.get("alert", ""),
        tokens_in=int(tokens.get("input") or 0),
        tokens_out=int(tokens.get("output") or 0),
        shell_pid=int(data.get("shellPid") or 0),
        current_tool=data.get("currentTool", ""),
        current_tool_detail=data.get("currentToolDetail", ""),
        language_icon=data.get("languageIcon", ""),
    )


def get_sessions():
    """Returns sessions sorted: active (idle/running/stale) first, then
    finished, each group most-recently-updated first."""
    if not STATUS_DIR.exists():
        return []

    sessions = []
    for path in STATUS_DIR.glob("*.json"):
        if path.name.startswith("_"):
            continue
        session = _load_one(path)
        if session is not None:
            sessions.append(session)

    def sort_key(session):
        group = 0 if session.status in ACTIVE_STATUSES else 1
        rank = STATUS_RANK.get(session.status, 1)
        return (group, rank, -session.updated_at)

    sessions.sort(key=sort_key)
    return sessions


def clear_finished():
    """Deletes status files for sessions currently marked finished."""
    if not STATUS_DIR.exists():
        return
    for path in STATUS_DIR.glob("*.json"):
        if path.name.startswith("_"):
            continue
        session = _load_one(path)
        if session is not None and session.status == "finished":
            path.unlink(missing_ok=True)


def format_tokens(count):
    if count < 1000:
        return str(count)
    if count < 10_000:
        return f"{count / 1000:.1f}k"
    if count < 1_000_000:
        return f"{round(count / 1000)}k"
    return f"{count / 1_000_000:.1f}M"


def format_relative(seconds):
    seconds = int(seconds)
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"
