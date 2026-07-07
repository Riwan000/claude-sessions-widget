"""Wires (or re-wires) the widget's 6 hooks into ~/.claude/settings.json.

Idempotent: safe to re-run after moving this project folder or switching
Python installs - existing widget entries are updated in place, missing
ones are appended, and nothing else in settings.json is touched. A
timestamped backup is written next to settings.json before any change.

Run it with the Python you want the hooks to use, e.g.:
    C:\\Python313\\python.exe install.py
(The hook script is stdlib-only, so any Python 3.9+ works. Avoid the
project venv interpreter unless you're sure the venv will never move.)

Autostart on login (HKCU Run registry entry, launches the widget with the
venv's pythonw.exe so no console window appears):
    install.py --autostart           # add / refresh the entry
    install.py --remove-autostart    # delete the entry
A plain run refreshes an existing autostart entry's paths but never
creates one - opting into login behavior stays explicit.
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
WIDGET_DIR = Path(__file__).resolve().parent
HOOK_SCRIPT = WIDGET_DIR / "hooks" / "widget_status.py"

AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "ClaudeSessionsWidget"
AUTOSTART_PYTHONW = WIDGET_DIR / ".venv" / "Scripts" / "pythonw.exe"
AUTOSTART_APP = WIDGET_DIR / "app.py"

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


def autostart_command():
    return f'"{AUTOSTART_PYTHONW}" "{AUTOSTART_APP}"'


def read_autostart():
    """Returns the current Run-entry command, or None if not registered."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
            return value
    except OSError:
        return None


def set_autostart():
    """Creates or refreshes the Run entry. Returns 'added'/'updated'/'unchanged'."""
    import winreg

    if not AUTOSTART_PYTHONW.exists():
        sys.exit(
            f"venv interpreter not found: {AUTOSTART_PYTHONW}\n"
            "Create the venv first (see README Setup), then re-run --autostart."
        )

    current = read_autostart()
    command = autostart_command()
    if current == command:
        return "unchanged"

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, command)
    return "updated" if current is not None else "added"


def remove_autostart():
    """Deletes the Run entry. Returns 'removed' or 'absent'."""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
            return "removed"
    except OSError:
        return "absent"


def sync_autostart(requested):
    """--autostart creates/refreshes; a plain run only refreshes an existing
    entry (so moving the project + re-running install.py fixes its paths
    without silently opting anyone into login autostart)."""
    if requested or read_autostart() is not None:
        result = set_autostart()
        print(f"  autostart: {result} ({autostart_command()})")
    else:
        print("  autostart: not set (opt in with: install.py --autostart)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--autostart", action="store_true", help="register the widget to start on login"
    )
    group.add_argument(
        "--remove-autostart", action="store_true", help="unregister autostart on login"
    )
    args = parser.parse_args()

    if args.remove_autostart:
        print(f"  autostart: {remove_autostart()}")
        return

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
    else:
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

    sync_autostart(args.autostart)


if __name__ == "__main__":
    main()
