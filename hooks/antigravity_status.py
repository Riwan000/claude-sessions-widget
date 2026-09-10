#!/usr/bin/env python
"""Google Antigravity hook: reports session status to the widget.

Invoked by Antigravity lifecycle hooks (PreInvocation, PreToolUse,
PostToolUse, Stop) with the event name as argv[1] and the hook JSON payload
on stdin. Writes one status file per session to STATUS_DIR so the desktop
widget can render live project/task/status across every open Antigravity
agent session.

Must never raise or block: any failure here should be invisible to
Antigravity, so everything runs inside a top-level try/except, always outputs
valid JSON response to stdout as required by the hook contract, and exits 0.
"""

import csv
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

STATUS_DIR = Path.home() / ".claude" / "widget-status"
MAX_STDIN_BYTES = 2_000_000
MAX_TASK_CHARS = 200
MAX_ANCESTOR_DEPTH = 12
CONTEXT_TAIL_BYTES = 200_000

TH32CS_SNAPPROCESS = 0x00000002

WIDGET_DIR = Path(__file__).resolve().parent.parent
WIDGET_PYTHONW = WIDGET_DIR / ".venv" / "Scripts" / "pythonw.exe"
WIDGET_APP = WIDGET_DIR / "app.py"
HISTORY_CSV_PATH = WIDGET_DIR / "history.csv"


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
    """Returns {pid: (parent_pid, exe_name_lower)} for every running process."""
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


KNOWN_AGENT_EXES = {
    "agy.exe",
    "antigravity.exe",
    "claude.exe",
    "code.exe",
    "cursor.exe",
    "electron.exe",
    "node.exe",
}


def find_stable_ancestor_pid(start_pid):
    """Walks up the process tree to find the stable CLI or IDE process."""
    processes = _snapshot_processes()
    current = start_pid
    for _ in range(MAX_ANCESTOR_DEPTH):
        info = processes.get(current)
        if info is None:
            break
        parent_pid, name = info
        if name in KNOWN_AGENT_EXES:
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


HISTORY_CSV_HEADERS = [
    "timestamp",
    "project",
    "cli_or_ide",
    "model",
    "duration_seconds",
    "prompt",
    "tokens_in",
    "tokens_out",
    "context_tokens",
]


def append_to_history(record, tool="antigravity"):
    """Appends a completed turn to HISTORY_CSV_PATH (widget folder / history.csv).
    Safely creates the file and header if not present, and appends
    fields: timestamp, project, cli_or_ide, model, duration_seconds,
    prompt, tokens_in, tokens_out, context_tokens.
    Deduplicated per turn using turnStartedAt so multiple stop events
    don't create duplicate entries."""
    try:
        turn_started = record.get("turnStartedAt")
        if turn_started and record.get("lastLoggedTurn") == turn_started:
            return

        csv_path = getattr(sys.modules.get(__name__), "HISTORY_CSV_PATH", None) or (
            WIDGET_DIR / "history.csv"
        )
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = csv_path.exists() and csv_path.stat().st_size > 0
        with open(csv_path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if not file_exists:
                writer.writerow(HISTORY_CSV_HEADERS)

            tokens = record.get("tokens") or {}
            now_iso = datetime.now(timezone.utc).isoformat()
            duration = record.get("turnDuration")
            row = [
                now_iso,
                record.get("project", ""),
                tool,
                record.get("model", ""),
                duration if duration is not None else "",
                record.get("prompt") or record.get("task", ""),
                tokens.get("input", 0),
                tokens.get("output", 0),
                record.get("contextTokens", 0),
            ]
            writer.writerow(row)

        if turn_started:
            record["lastLoggedTurn"] = turn_started
    except Exception:
        pass


def truncate(text, limit=MAX_TASK_CHARS):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


CD_COMMAND_RE = re.compile(r'^cd\s+("[^"]*"|\'[^\']*\'|\S+)(?:\s*&&\s*(.*))?$', re.IGNORECASE)


def _shorten_cd_command(command):
    match = CD_COMMAND_RE.match(command)
    if not match:
        return None
    raw_path, rest = match.group(1), match.group(2)
    path = raw_path.strip("\"'")
    folder = Path(path.rstrip("/\\")).name or path
    short = f"cd {folder}/"
    return f"{short} && {rest}" if rest else short


def describe_tool_use(tool_name, tool_input):
    """Human-readable string for what an Antigravity tool is doing."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}

    def file_name(key):
        path = tool_input.get(key)
        return Path(str(path)).name if path else ""

    if tool_name in ("view_file", "Read", "NotebookEdit"):
        name = file_name("AbsolutePath") or file_name("file_path")
        return f"Reading {name}" if name else "Reading a file"
    if tool_name in ("replace_file_content", "Edit"):
        name = file_name("TargetFile") or file_name("file_path")
        return f"Editing {name}" if name else "Editing a file"
    if tool_name in ("write_to_file", "Write"):
        name = file_name("TargetFile") or file_name("file_path")
        return f"Writing {name}" if name else "Writing a file"
    if tool_name in ("run_command", "Bash"):
        command = str(tool_input.get("CommandLine") or tool_input.get("command") or "").strip()
        if not command:
            return "Running a command"
        return _shorten_cd_command(command) or f"Running {command}"
    if tool_name in ("grep_search", "Grep"):
        pattern = tool_input.get("Query") or tool_input.get("pattern")
        return f"Searching for {pattern}" if pattern else "Searching code"
    if tool_name in ("find_by_name", "Glob"):
        pattern = tool_input.get("Pattern") or tool_input.get("pattern")
        return f"Finding {pattern}" if pattern else "Finding files"
    if tool_name in ("read_url_content", "WebFetch"):
        url = tool_input.get("Url") or tool_input.get("url")
        return f"Fetching {url}" if url else "Fetching a page"
    if tool_name in ("search_web", "WebSearch"):
        query = tool_input.get("query")
        return f"Searching {query}" if query else "Searching the web"
    if tool_name in ("invoke_subagent", "Task"):
        role = ""
        subagents = tool_input.get("Subagents")
        if isinstance(subagents, list) and subagents and isinstance(subagents[0], dict):
            role = subagents[0].get("Role") or subagents[0].get("TypeName") or ""
        desc = role or tool_input.get("description")
        return f"Delegating: {desc}" if desc else "Delegating to a subagent"
    if tool_name == "define_subagent":
        name = tool_input.get("name")
        return f"Defining subagent {name}" if name else "Defining subagent"
    if tool_name == "manage_subagents":
        action = tool_input.get("Action")
        return f"Managing subagents ({action})" if action else "Managing subagents"
    if tool_name == "manage_task":
        action = tool_input.get("Action")
        return f"Managing task ({action})" if action else "Managing task"
    if tool_name == "schedule":
        return "Scheduling task"
    if tool_name == "ask_question":
        return "Asking a question"
    if tool_name == "generate_image":
        name = tool_input.get("ImageName")
        return f"Generating image {name}" if name else "Generating image"
    if tool_name == "TodoWrite":
        return "Updating task list"
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


def extract_latest_prompt(transcript_path):
    """Extracts the latest user request text from the Antigravity transcript."""
    if not transcript_path:
        return ""
    try:
        path = Path(transcript_path)
        if not path.exists():
            return ""
        size = path.stat().st_size
        with open(path, "rb") as handle:
            if size > 100_000:
                handle.seek(size - 100_000)
            content = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return ""

    for line in reversed(content.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") == "USER_INPUT":
            text = entry.get("content") or ""
            match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", text, re.DOTALL)
            if match:
                text = match.group(1).strip()
            # Remove any surrounding tag structures
            text = re.sub(r"<[A-Z_]+>.*?</[A-Z_]+>", "", text, flags=re.DOTALL).strip()
            return truncate(text)
    return ""


def load_or_create(session_id, cwd, now, shell_pid, tool="antigravity"):
    path = status_path(session_id)
    record = load_existing(path) or {
        "sessionId": session_id,
        "task": "",
        "status": "idle",
        "startedAt": now,
        "tool": tool,
    }
    record["tool"] = tool
    record["cwd"] = cwd
    record["project"] = Path(cwd).name or cwd
    record["updatedAt"] = now
    record["shellPid"] = shell_pid
    return path, record


def spawn_widget():
    if not WIDGET_PYTHONW.exists() or not WIDGET_APP.exists():
        return
    subprocess.Popen(
        [str(WIDGET_PYTHONW), str(WIDGET_APP)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def handle_pre_invocation(session_id, cwd, payload, shell_pid):
    now = time.time()
    path, record = load_or_create(session_id, cwd, now, shell_pid, tool="antigravity")
    record["status"] = "running"
    record["currentTool"] = ""
    record["currentToolDetail"] = ""
    record["turnStartedAt"] = now

    transcript_path = payload.get("transcriptPath")
    if transcript_path:
        record["transcriptPath"] = transcript_path
        prompt = extract_latest_prompt(transcript_path)
        if prompt:
            record["prompt"] = prompt
            record["task"] = prompt

    model = payload.get("modelName")
    if model:
        record["model"] = model

    if "languageIcon" not in record:
        record["languageIcon"] = detect_language_icon(cwd)

    atomic_write(path, record)
    spawn_widget()
    # Contract: Return JSON response
    print(json.dumps({"injectSteps": []}))


def handle_pre_tool_use(session_id, cwd, payload, shell_pid):
    path, record = load_or_create(session_id, cwd, time.time(), shell_pid, tool="antigravity")
    tool_call = payload.get("toolCall") or {}
    tool_name = tool_call.get("name") or ""
    tool_args = tool_call.get("args") or {}

    record["status"] = "running"
    record["currentTool"] = tool_name
    record["currentToolDetail"] = truncate(describe_tool_use(tool_name, tool_args))
    atomic_write(path, record)
    # Contract: Return JSON decision
    print(json.dumps({"decision": "allow"}))


def handle_post_tool_use(session_id, cwd, payload, shell_pid):
    path = status_path(session_id)
    record = load_existing(path)
    if record is not None:
        record["updatedAt"] = time.time()
        record["shellPid"] = shell_pid
        atomic_write(path, record)
    # Contract: Return empty JSON object
    print(json.dumps({}))


def handle_stop(session_id, cwd, payload, shell_pid):
    now = time.time()
    path, record = load_or_create(session_id, cwd, now, shell_pid, tool="antigravity")
    record["status"] = "finished"
    record["currentTool"] = ""
    record["currentToolDetail"] = ""

    model = payload.get("modelName")
    if model:
        record["model"] = model

    turn_started = record.get("turnStartedAt")
    if turn_started:
        record["turnDuration"] = round(max(0.0, now - turn_started), 2)

    append_to_history(record, tool="antigravity")
    atomic_write(path, record)
    # Contract: Return JSON decision
    print(json.dumps({"decision": "allow"}))


HANDLERS = {
    "pre-invocation": handle_pre_invocation,
    "PreInvocation": handle_pre_invocation,
    "pre-tool-use": handle_pre_tool_use,
    "PreToolUse": handle_pre_tool_use,
    "post-tool-use": handle_post_tool_use,
    "PostToolUse": handle_post_tool_use,
    "stop": handle_stop,
    "Stop": handle_stop,
}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = HANDLERS.get(event)
    if handler is None:
        # Always output valid JSON so Antigravity hook runner never crashes
        print(json.dumps({}))
        return

    payload = read_payload()
    session_id = sanitize_session_id(
        payload.get("conversationId") or payload.get("sessionId")
    )
    if not session_id:
        print(json.dumps({}))
        return

    workspace_paths = payload.get("workspacePaths") or []
    cwd = workspace_paths[0] if workspace_paths else (payload.get("cwd") or os.getcwd())
    shell_pid = find_stable_ancestor_pid(os.getppid())

    handler(session_id, cwd, payload, shell_pid)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fallback to outputting valid JSON
        try:
            print(json.dumps({}))
        except Exception:
            pass
    sys.exit(0)
