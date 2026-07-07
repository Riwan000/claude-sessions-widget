"""Wires (or re-wires) the widget's 6 hooks into ~/.claude/settings.json.

Idempotent: safe to re-run after moving this project folder or switching
Python installs - existing widget entries are updated in place, missing
ones are appended, and nothing else in settings.json is touched. A
timestamped backup is written next to settings.json before any change.

Run it with the Python you want the hooks to use, e.g.:
    C:\\Python313\\python.exe install.py
(The hook script is stdlib-only, so any Python 3.9+ works. Avoid the
project venv interpreter unless you're sure the venv will never move.)
"""

import json
import shutil
import sys
import time
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
HOOK_SCRIPT = Path(__file__).resolve().parent / "hooks" / "widget_status.py"

HOOK_EVENTS = {
    "SessionStart": "session-start",
    "UserPromptSubmit": "prompt-submit",
    "Notification": "notification",
    "PostToolUse": "tool-complete",
    "Stop": "stop",
    "SessionEnd": "session-end",
}


def build_command(event_arg):
    return f'"{sys.executable}" "{HOOK_SCRIPT}" {event_arg}'


def ensure_hook(hooks_config, event, event_arg):
    """Updates the existing widget entry for `event`, or appends one.
    Returns 'updated', 'unchanged', or 'added'."""
    blocks = hooks_config.setdefault(event, [])
    command = build_command(event_arg)

    for block in blocks:
        for hook in block.get("hooks", []):
            if "widget_status.py" in hook.get("command", ""):
                if hook["command"] == command:
                    return "unchanged"
                hook["command"] = command
                return "updated"

    blocks.append(
        {
            "matcher": "*",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 5,
                    "async": True,
                }
            ],
        }
    )
    return "added"


def main():
    if not HOOK_SCRIPT.exists():
        sys.exit(f"Hook script not found: {HOOK_SCRIPT}")
    if not SETTINGS_PATH.exists():
        sys.exit(f"Claude Code settings not found: {SETTINGS_PATH}")

    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    hooks_config = settings.setdefault("hooks", {})

    results = {
        event: ensure_hook(hooks_config, event, arg)
        for event, arg in HOOK_EVENTS.items()
    }

    if set(results.values()) == {"unchanged"}:
        print("All 6 widget hooks already up to date - nothing written.")
        return

    backup = SETTINGS_PATH.with_name(
        f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(SETTINGS_PATH, backup)

    serialized = json.dumps(settings, indent=2, ensure_ascii=False)
    json.loads(serialized)  # refuse to write anything unparsable
    SETTINGS_PATH.write_text(serialized + "\n", encoding="utf-8")

    print(f"Backup: {backup}")
    for event, result in results.items():
        print(f"  {event}: {result}")


if __name__ == "__main__":
    main()
