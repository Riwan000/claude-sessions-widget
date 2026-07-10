#!/usr/bin/env python
"""Claude Code hook: reports session status to the widget.

Invoked by Claude Code hooks (SessionStart, UserPromptSubmit, PreToolUse,
Stop, SessionEnd, Notification, PostToolUse) with the event name as argv[1]
and the hook JSON payload on stdin. Writes one status file per session to
STATUS_DIR so the desktop widget can render live project/task/status
(including a blinking "needs permission" state, the current tool call,
e.g. "Editing app.py", and a one-time-per-session language icon guess,
e.g. 🐍) across every open Claude Code CLI.

Must never raise or block: any failure here should be invisible to
Claude Code, so everything runs inside a top-level try/except and the
process always exits 0.
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

STATUS_DIR = Path.home() / ".claude" / "widget-status"
MAX_STDIN_BYTES = 2_000_000
MAX_TASK_CHARS = 200
MAX_ANCESTOR_DEPTH = 12

TH32CS_SNAPPROCESS = 0x00000002

WIDGET_DIR = Path(__file__).resolve().parent.parent
WIDGET_PYTHONW = WIDGET_DIR / ".venv" / "Scripts" / "pythonw.exe"
WIDGET_APP = WIDGET_DIR / "app.py"


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def _snapshot_processes():
    """Returns {pid: (parent_pid, exe_name_lower)} for every running process,
    via a single Toolhelp32 snapshot (fast, no subprocess spawn)."""
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (0, -1):
        return {}

    processes = {}
    entry = _PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
    try:
        has_more = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while has_more:
            name = entry.szExeFile.decode("mbcs", errors="ignore").lower()
            processes[entry.th32ProcessID] = (entry.th32ParentProcessID, name)
            has_more = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return processes


def find_stable_ancestor_pid(start_pid):
    """The hook's immediate parent is often a short-lived shell wrapper that
    Claude Code spawns just to run the hook command, which exits the moment
    the hook finishes - useless to record for a later click-to-focus action.
    Walks up the real process tree to find the actual claude.exe process,
    which stays alive for the life of the CLI session. Falls back to
    start_pid if claude.exe can't be found in the chain."""
    processes = _snapshot_processes()
    current = start_pid
    for _ in range(MAX_ANCESTOR_DEPTH):
        info = processes.get(current)
        if info is None:
            break
        parent_pid, name = info
        if name == "claude.exe":
            return current
        if not parent_pid or parent_pid == current:
            break
        current = parent_pid
    return start_pid


def sanitize_session_id(raw):
    value = re.sub(r"[^a-zA-Z0-9_-]", "-", raw or "")
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or None


def read_payload():
    data = sys.stdin.read(MAX_STDIN_BYTES) if not sys.stdin.isatty() else ""
    if not data.strip():
        return {}
    try:
        return json.loads(data)
    except ValueError:
        return {}


def status_path(session_id):
    return STATUS_DIR / f"{session_id}.json"


def load_existing(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def atomic_write(path, record):
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def truncate(text, limit=MAX_TASK_CHARS):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_tool_use(tool_name, tool_input):
    """One human-readable line for what a tool call is about to do, e.g.
    'Reading README.md' or 'Running pytest tests/'. Falls back to a generic
    'Using <ToolName>' for tools not called out below (custom/MCP tools),
    so the widget never shows a blank line for a running tool."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}

    def file_name(key="file_path"):
        path = tool_input.get(key)
        return Path(str(path)).name if path else ""

    if tool_name in ("Read", "NotebookEdit"):
        name = file_name()
        return f"Reading {name}" if name else "Reading a file"
    if tool_name == "Edit":
        name = file_name()
        return f"Editing {name}" if name else "Editing a file"
    if tool_name == "Write":
        name = file_name()
        return f"Writing {name}" if name else "Writing a file"
    if tool_name == "Bash":
        command = str(tool_input.get("command") or "").strip()
        return f"Running {command}" if command else "Running a command"
    if tool_name == "Grep":
        pattern = tool_input.get("pattern")
        return f"Searching for {pattern}" if pattern else "Searching code"
    if tool_name == "Glob":
        pattern = tool_input.get("pattern")
        return f"Finding {pattern}" if pattern else "Finding files"
    if tool_name == "WebFetch":
        url = tool_input.get("url")
        return f"Fetching {url}" if url else "Fetching a page"
    if tool_name == "WebSearch":
        query = tool_input.get("query")
        return f"Searching {query}" if query else "Searching the web"
    if tool_name == "TodoWrite":
        return "Updating task list"
    if tool_name == "Task":
        description = tool_input.get("description")
        return f"Delegating: {description}" if description else "Delegating to a subagent"
    return f"Using {tool_name}" if tool_name else "Working"


LANGUAGE_MARKERS = (
    ("go.mod", "🐹"),
    ("Cargo.toml", "🦀"),
    ("package.json", "📦"),
    ("pyproject.toml", "🐍"),
    ("requirements.txt", "🐍"),
    ("setup.py", "🐍"),
)


def detect_language_icon(cwd):
    """Best-effort, one-time-per-session language guess from top-level marker
    files in the project directory, so the widget can show e.g. '🐍 widget'
    instead of a plain project name. Never raises; unknown/unreadable
    directories just get no icon."""
    try:
        names = {entry.name for entry in Path(cwd).iterdir()}
    except OSError:
        return ""
    for marker, icon in LANGUAGE_MARKERS:
        if marker in names:
            return icon
    if any(name.endswith(".py") for name in names):
        return "🐍"
    return ""


def transcript_size(transcript_path):
    """Byte offset marking 'end of transcript so far' - used as a checkpoint
    so a later Stop event can sum only the lines written during this turn."""
    if not transcript_path:
        return 0
    try:
        return Path(transcript_path).stat().st_size
    except OSError:
        return 0


def sum_turn_tokens(transcript_path, offset):
    """Sums usage across every assistant message appended to the transcript
    since `offset` (the byte offset recorded at UserPromptSubmit time).

    One API message can span several JSONL lines (e.g. separate entries for
    its thinking block and its tool_use block), each repeating the same
    usage object - so usage is deduplicated by message id, not per line."""
    if not transcript_path or offset is None:
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8") as handle:
            handle.seek(offset)
            new_content = handle.read()
    except (OSError, ValueError):
        return None

    usage_by_message = {}
    for line in new_content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        # Entries without a message id can't be correlated, so each one
        # counts once (keyed by its own entry uuid).
        key = message.get("id") or entry.get("uuid") or id(entry)
        usage_by_message[key] = usage

    if not usage_by_message:
        return None

    input_tokens = 0
    output_tokens = 0
    for usage in usage_by_message.values():
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return {"input": input_tokens, "output": output_tokens}


def load_or_create(session_id, cwd, now, shell_pid):
    """Loads the session's record (or scaffolds a fresh one) and refreshes
    the fields every event keeps current. Returns (path, record)."""
    path = status_path(session_id)
    record = load_existing(path) or {
        "sessionId": session_id,
        "task": "",
        "status": "idle",
        "startedAt": now,
    }
    record["cwd"] = cwd
    record["project"] = Path(cwd).name or cwd
    record["updatedAt"] = now
    record["shellPid"] = shell_pid
    return path, record


def spawn_widget():
    """Launches the widget UI, detached from this hook's process tree so it
    outlives the hook (which Claude Code kills shortly after it returns).
    Safe to call on every SessionStart even if the widget is already
    running: its own single-instance lock (app.py) makes a redundant launch
    a harmless no-op."""
    if not WIDGET_PYTHONW.exists() or not WIDGET_APP.exists():
        return
    subprocess.Popen(
        [str(WIDGET_PYTHONW), str(WIDGET_APP)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def handle_session_start(session_id, cwd, payload, shell_pid):
    path, record = load_or_create(session_id, cwd, time.time(), shell_pid)
    record["pid"] = os.getpid()
    # Sniffed once and cached rather than every poll cycle, since it's a
    # filesystem listdir and the answer can't change during a session.
    if "languageIcon" not in record:
        record["languageIcon"] = detect_language_icon(cwd)
    atomic_write(path, record)
    spawn_widget()


def handle_prompt_submit(session_id, cwd, payload, shell_pid):
    path, record = load_or_create(session_id, cwd, time.time(), shell_pid)
    prompt = payload.get("prompt") or payload.get("message") or ""
    record["task"] = truncate(prompt)
    record["status"] = "running"
    # A new turn is starting - drop the previous turn's tool line so it
    # doesn't linger over the new task text until the first tool call.
    record["currentTool"] = ""
    record["currentToolDetail"] = ""
    record["transcriptPath"] = payload.get("transcript_path") or record.get("transcriptPath")
    record["transcriptOffset"] = transcript_size(record["transcriptPath"])
    atomic_write(path, record)


def handle_tool_start(session_id, cwd, payload, shell_pid):
    path, record = load_or_create(session_id, cwd, time.time(), shell_pid)
    tool_name = payload.get("tool_name") or ""
    record["status"] = "running"
    record["currentTool"] = tool_name
    record["currentToolDetail"] = truncate(describe_tool_use(tool_name, payload.get("tool_input")))
    atomic_write(path, record)


def handle_stop(session_id, cwd, payload, shell_pid):
    path, record = load_or_create(session_id, cwd, time.time(), shell_pid)
    transcript_path = payload.get("transcript_path") or record.get("transcriptPath")
    # No recorded offset means prompt-submit never ran for this turn (e.g. a
    # resumed session) - summing from 0 would count the entire transcript as
    # one turn, so skip the token field instead.
    tokens = sum_turn_tokens(transcript_path, record.get("transcriptOffset"))
    if tokens is not None:
        record["tokens"] = tokens

    record["status"] = "finished"
    record["currentTool"] = ""
    record["currentToolDetail"] = ""
    atomic_write(path, record)


def handle_session_end(session_id, cwd, payload, shell_pid):
    status_path(session_id).unlink(missing_ok=True)


def handle_notification(session_id, cwd, payload, shell_pid):
    message = payload.get("message") or ""
    if "permission" not in message.lower():
        return  # not a permission prompt (e.g. idle-input nudge) - ignore

    path, record = load_or_create(session_id, cwd, time.time(), shell_pid)
    record["status"] = "permission"
    record["alert"] = truncate(message)
    atomic_write(path, record)


def handle_tool_complete(session_id, cwd, payload, shell_pid):
    """Clears a permission alert once a tool actually runs (proof it was
    approved). Only touches records currently in the permission state:
    Stop and PostToolUse hooks are both async, so an unconditional write
    here could land after Stop and flip a finished session back to running."""
    path = status_path(session_id)
    record = load_existing(path)
    if record is None or record.get("status") != "permission":
        return
    record["status"] = "running"
    record["alert"] = ""
    record["updatedAt"] = time.time()
    record["shellPid"] = shell_pid
    atomic_write(path, record)


HANDLERS = {
    "session-start": handle_session_start,
    "prompt-submit": handle_prompt_submit,
    "tool-start": handle_tool_start,
    "stop": handle_stop,
    "session-end": handle_session_end,
    "notification": handle_notification,
    "tool-complete": handle_tool_complete,
}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = HANDLERS.get(event)
    if handler is None:
        return

    payload = read_payload()
    session_id = sanitize_session_id(
        payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
    )
    if not session_id:
        return
    cwd = payload.get("cwd") or os.getcwd()

    # The stable claude.exe ancestor - walking up from here (in focus_session.ps1)
    # finds the terminal window actually hosting this Claude Code session.
    shell_pid = find_stable_ancestor_pid(os.getppid())

    handler(session_id, cwd, payload, shell_pid)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
