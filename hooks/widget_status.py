#!/usr/bin/env python
"""Claude Code hook: reports session status to the widget.

Invoked by Claude Code hooks (SessionStart, UserPromptSubmit, Stop,
SessionEnd, Notification, PostToolUse) with the event name as argv[1] and
the hook JSON payload on stdin. Writes one status file per session to
STATUS_DIR so the desktop widget can render live project/task/status
(including a blinking "needs permission" state) across every open Claude
Code CLI.

Must never raise or block: any failure here should be invisible to
Claude Code, so everything runs inside a top-level try/except and the
process always exits 0.
"""

import ctypes
import json
import os
import re
import sys
import time
from ctypes import wintypes
from pathlib import Path

STATUS_DIR = Path.home() / ".claude" / "widget-status"
MAX_STDIN_BYTES = 2_000_000
MAX_TASK_CHARS = 200
MAX_ANCESTOR_DEPTH = 12

TH32CS_SNAPPROCESS = 0x00000002


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
    since `offset` (the byte offset recorded at UserPromptSubmit time)."""
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8") as handle:
            handle.seek(offset)
            new_content = handle.read()
    except OSError:
        return None

    input_tokens = 0
    output_tokens = 0
    found = False
    for line in new_content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        usage = entry.get("message", {}).get("usage")
        if not isinstance(usage, dict):
            continue
        found = True
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)

    if not found:
        return None
    return {"input": input_tokens, "output": output_tokens}


def handle_session_start(session_id, cwd, payload, shell_pid):
    path = status_path(session_id)
    now = time.time()
    existing = load_existing(path)
    record = existing or {
        "sessionId": session_id,
        "project": Path(cwd).name or cwd,
        "cwd": cwd,
        "task": "",
        "status": "idle",
        "startedAt": now,
    }
    record["cwd"] = cwd
    record["project"] = Path(cwd).name or cwd
    record["pid"] = os.getpid()
    record["shellPid"] = shell_pid
    record["updatedAt"] = now
    atomic_write(path, record)


def handle_prompt_submit(session_id, cwd, payload, shell_pid):
    path = status_path(session_id)
    now = time.time()
    record = load_existing(path) or {
        "sessionId": session_id,
        "project": Path(cwd).name or cwd,
        "cwd": cwd,
        "startedAt": now,
    }
    prompt = payload.get("prompt") or payload.get("message") or ""
    record["cwd"] = cwd
    record["project"] = Path(cwd).name or cwd
    record["task"] = truncate(prompt)
    record["status"] = "running"
    record["updatedAt"] = now
    record["shellPid"] = shell_pid
    record["transcriptPath"] = payload.get("transcript_path") or record.get("transcriptPath")
    record["transcriptOffset"] = transcript_size(record["transcriptPath"])
    atomic_write(path, record)


def handle_stop(session_id, cwd, payload, shell_pid):
    path = status_path(session_id)
    now = time.time()
    record = load_existing(path) or {
        "sessionId": session_id,
        "project": Path(cwd).name or cwd,
        "cwd": cwd,
        "task": "",
        "startedAt": now,
    }
    transcript_path = payload.get("transcript_path") or record.get("transcriptPath")
    tokens = sum_turn_tokens(transcript_path, record.get("transcriptOffset", 0))
    if tokens is not None:
        record["tokens"] = tokens

    record["status"] = "finished"
    record["updatedAt"] = now
    record["shellPid"] = shell_pid
    atomic_write(path, record)


def handle_session_end(session_id, cwd, payload, shell_pid):
    status_path(session_id).unlink(missing_ok=True)


def handle_notification(session_id, cwd, payload, shell_pid):
    message = payload.get("message") or ""
    if "permission" not in message.lower():
        return  # not a permission prompt (e.g. idle-input nudge) - ignore

    path = status_path(session_id)
    now = time.time()
    record = load_existing(path) or {
        "sessionId": session_id,
        "project": Path(cwd).name or cwd,
        "cwd": cwd,
        "task": "",
        "startedAt": now,
    }
    record["status"] = "permission"
    record["alert"] = truncate(message)
    record["updatedAt"] = now
    record["shellPid"] = shell_pid
    atomic_write(path, record)


def handle_tool_complete(session_id, cwd, payload, shell_pid):
    path = status_path(session_id)
    record = load_existing(path)
    if record is None:
        return  # no prior SessionStart record for this session - nothing to clear
    record["status"] = "running"
    record["alert"] = ""
    record["updatedAt"] = time.time()
    record["shellPid"] = shell_pid
    atomic_write(path, record)


HANDLERS = {
    "session-start": handle_session_start,
    "prompt-submit": handle_prompt_submit,
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
