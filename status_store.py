"""Reads and interprets the status files written by hooks/widget_status.py."""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

STATUS_DIR = Path.home() / ".claude" / "widget-status"
STALE_AFTER_SECONDS = 10 * 60
PRUNE_AFTER_SECONDS = 24 * 60 * 60

ACTIVE_STATUSES = {"idle", "running", "permission"}
STATUS_RANK = {"permission": 0, "running": 1, "idle": 1}

# Token-pill color tiers. TOKEN_TIER_HIGH doubles as the one-time "large
# context" notification threshold in ui/main_window.py, since that's the
# point a session becomes worth flagging outside the pill color alone.
TOKEN_TIER_WARN = 100_000
TOKEN_TIER_HIGH = 150_000
TOKEN_TIER_CRITICAL = 200_000


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
    context_tokens: int = 0
    shell_pid: int = 0
    current_tool: str = ""
    current_tool_detail: str = ""
    language_icon: str = ""
    model: str = ""

    @property
    def has_tokens(self):
        return self.tokens_in > 0 or self.tokens_out > 0

    @property
    def has_context(self):
        return self.context_tokens > 0

    @property
    def has_model(self):
        return bool(self.model)

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
        context_tokens=int(data.get("contextTokens") or 0),
        shell_pid=int(data.get("shellPid") or 0),
        current_tool=data.get("currentTool", ""),
        current_tool_detail=data.get("currentToolDetail", ""),
        language_icon=data.get("languageIcon", ""),
        model=data.get("model") or "",
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


def format_tokens_precise(count):
    """One-decimal 'Nk' formatting (e.g. 151300 -> '151.3k'), used for the
    large-context warning notification where format_tokens()'s whole-k
    rounding at this scale would lose the precision the message implies."""
    return f"{count / 1000:.1f}k"


def token_tier(total):
    """Pill color tier for a session's token total: '' (grey, default),
    'warn' (yellow), 'high' (orange), or 'critical' (red)."""
    if total >= TOKEN_TIER_CRITICAL:
        return "critical"
    if total >= TOKEN_TIER_HIGH:
        return "high"
    if total >= TOKEN_TIER_WARN:
        return "warn"
    return ""


MODEL_FAMILIES = ("opus", "sonnet", "haiku", "fable")


def short_model_label(model_id):
    """Turns a raw model id into a short display label for the row pill,
    e.g. 'claude-sonnet-5-20250929' -> 'Sonnet 5', or the older
    'claude-3-5-sonnet-20241022' -> 'Sonnet 3.5'. Version numbers have
    appeared both before and after the family name across model
    generations, so both orderings are handled. Falls back to the raw id
    for anything unrecognized rather than showing nothing."""
    if not model_id:
        return ""
    tokens = [t for t in re.split(r"[-_]", model_id.lower()) if t != "claude"]
    # A trailing 8-digit token is a release date, not a version number.
    if tokens and re.fullmatch(r"\d{8}", tokens[-1]):
        tokens = tokens[:-1]

    family = next((t for t in tokens if t in MODEL_FAMILIES), None)
    if family is None:
        return model_id

    version = ".".join(t for t in tokens if t != family and t.isdigit())
    return f"{family.capitalize()} {version}" if version else family.capitalize()


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
