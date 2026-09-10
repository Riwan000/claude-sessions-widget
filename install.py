"""Wires (or re-wires) the widget's hooks for Antigravity and Claude Code.

Idempotent: safe to re-run after moving this project folder or switching
Python installs - existing widget entries are updated in place, missing
ones are appended, and nothing else in config files is touched. Timestamped
backups are written before any changes.

Run it with the Python you want the hooks to use, e.g.:
    C:\\Python313\\python.exe install.py
    C:\\Python313\\python.exe install.py --all
    C:\\Python313\\python.exe install.py --antigravity
    C:\\Python313\\python.exe install.py --claude

The widget launches itself when a session begins and shuts down when all
sessions end. No login autostart is needed, though --autostart is available.
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

WIDGET_DIR = Path(__file__).resolve().parent

# Claude Code configuration
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
CLAUDE_HOOK_SCRIPT = WIDGET_DIR / "hooks" / "widget_status.py"

CLAUDE_HOOK_EVENTS = {
    "SessionStart": "session-start",
    "UserPromptSubmit": "prompt-submit",
    "PreToolUse": "tool-start",
    "Notification": "notification",
    "PostToolUse": "tool-complete",
    "Stop": "stop",
    "SessionEnd": "session-end",
}

# Google Antigravity configuration
ANTIGRAVITY_CONFIG_PATH = Path.home() / ".gemini" / "config" / "hooks.json"
ANTIGRAVITY_HOOK_SCRIPT = WIDGET_DIR / "hooks" / "antigravity_status.py"

# Autostart configuration
AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "AISessionsWidget"
AUTOSTART_LEGACY_NAME = "ClaudeSessionsWidget"
AUTOSTART_PYTHONW = WIDGET_DIR / ".venv" / "Scripts" / "pythonw.exe"
AUTOSTART_APP = WIDGET_DIR / "app.py"


def build_claude_command(event_arg, python_exe=sys.executable):
    return f'"{python_exe}" "{CLAUDE_HOOK_SCRIPT}" {event_arg}'


def ensure_claude_hook(hooks_config, event, event_arg, python_exe=sys.executable):
    blocks = hooks_config.setdefault(event, [])
    command = build_claude_command(event_arg, python_exe)

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


def install_claude(python_exe=sys.executable):
    if not CLAUDE_HOOK_SCRIPT.exists():
        print(f"Claude hook script not found: {CLAUDE_HOOK_SCRIPT}")
        return False
    if not CLAUDE_SETTINGS_PATH.exists():
        print(f"Claude Code settings not found: {CLAUDE_SETTINGS_PATH} (skipping)")
        return False

    try:
        settings = json.loads(CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"Failed to read Claude settings: {err}")
        return False

    hooks_config = settings.setdefault("hooks", {})
    results = {
        event: ensure_claude_hook(hooks_config, event, arg, python_exe)
        for event, arg in CLAUDE_HOOK_EVENTS.items()
    }

    if set(results.values()) == {"unchanged"}:
        print("All Claude Code hooks already up to date.")
    else:
        backup = CLAUDE_SETTINGS_PATH.with_name(
            f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(CLAUDE_SETTINGS_PATH, backup)
        serialized = json.dumps(settings, indent=2, ensure_ascii=False)
        json.loads(serialized)
        CLAUDE_SETTINGS_PATH.write_text(serialized + "\n", encoding="utf-8")
        print(f"Claude Backup: {backup}")
        for event, result in results.items():
            print(f"  Claude {event}: {result}")

    return True


def remove_claude():
    if not CLAUDE_SETTINGS_PATH.exists():
        return "absent"
    try:
        settings = json.loads(CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "error"

    hooks_config = settings.get("hooks", {})
    changed = False
    for event, blocks in list(hooks_config.items()):
        new_blocks = []
        for block in blocks:
            filtered_hooks = [
                h for h in block.get("hooks", [])
                if "widget_status.py" not in h.get("command", "")
            ]
            if filtered_hooks:
                block["hooks"] = filtered_hooks
                new_blocks.append(block)
            else:
                changed = True
        if new_blocks:
            hooks_config[event] = new_blocks
        elif event in hooks_config:
            del hooks_config[event]
            changed = True

    if changed:
        serialized = json.dumps(settings, indent=2, ensure_ascii=False)
        CLAUDE_SETTINGS_PATH.write_text(serialized + "\n", encoding="utf-8")
        return "removed"
    return "unchanged"


def build_antigravity_command(event_arg, python_exe=sys.executable):
    py = str(python_exe)
    script = str(ANTIGRAVITY_HOOK_SCRIPT)
    if " " in py or " " in script:
        return f'""{py}" "{script}" {event_arg}""'
    return f"{py} {script} {event_arg}"


def install_antigravity(python_exe=sys.executable):
    if not ANTIGRAVITY_HOOK_SCRIPT.exists():
        print(f"Antigravity hook script not found: {ANTIGRAVITY_HOOK_SCRIPT}")
        return False

    ANTIGRAVITY_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    hooks_data = {}
    if ANTIGRAVITY_CONFIG_PATH.exists():
        try:
            hooks_data = json.loads(ANTIGRAVITY_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hooks_data = {}

    pre_invocation_cmd = build_antigravity_command("pre-invocation", python_exe)
    pre_tool_cmd = build_antigravity_command("pre-tool-use", python_exe)
    post_tool_cmd = build_antigravity_command("post-tool-use", python_exe)
    stop_cmd = build_antigravity_command("stop", python_exe)

    desired_config = {
        "PreInvocation": [
            {
                "type": "command",
                "command": pre_invocation_cmd,
                "timeout": 5,
            }
        ],
        "PreToolUse": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": pre_tool_cmd,
                        "timeout": 5,
                    }
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": post_tool_cmd,
                        "timeout": 5,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": stop_cmd,
                "timeout": 5,
            }
        ],
    }

    if hooks_data.get("widget-status") == desired_config:
        print("All Antigravity hooks already up to date.")
        return True

    if ANTIGRAVITY_CONFIG_PATH.exists():
        backup = ANTIGRAVITY_CONFIG_PATH.with_name(
            f"hooks.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        try:
            shutil.copy2(ANTIGRAVITY_CONFIG_PATH, backup)
            print(f"Antigravity Backup: {backup}")
        except OSError:
            pass

    hooks_data["widget-status"] = desired_config
    serialized = json.dumps(hooks_data, indent=2, ensure_ascii=False)
    json.loads(serialized)
    ANTIGRAVITY_CONFIG_PATH.write_text(serialized + "\n", encoding="utf-8")
    print(f"Antigravity hooks installed successfully into {ANTIGRAVITY_CONFIG_PATH}")
    return True


def remove_antigravity():
    if not ANTIGRAVITY_CONFIG_PATH.exists():
        return "absent"
    try:
        hooks_data = json.loads(ANTIGRAVITY_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "error"

    if "widget-status" in hooks_data:
        del hooks_data["widget-status"]
        serialized = json.dumps(hooks_data, indent=2, ensure_ascii=False)
        ANTIGRAVITY_CONFIG_PATH.write_text(serialized + "\n", encoding="utf-8")
        return "removed"
    return "unchanged"


def autostart_command():
    return f'"{AUTOSTART_PYTHONW}" "{AUTOSTART_APP}"'


def read_autostart():
    import winreg

    for key_name in (AUTOSTART_VALUE_NAME, AUTOSTART_LEGACY_NAME):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, key_name)
                return value
        except OSError:
            pass
    return None


def set_autostart():
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
        try:
            winreg.DeleteValue(key, AUTOSTART_LEGACY_NAME)
        except OSError:
            pass
        winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, command)
    return "updated" if current is not None else "added"


def remove_autostart():
    import winreg

    removed = False
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        for name in (AUTOSTART_VALUE_NAME, AUTOSTART_LEGACY_NAME):
            try:
                winreg.DeleteValue(key, name)
                removed = True
            except OSError:
                pass
    return "removed" if removed else "absent"


def sync_autostart(requested):
    if requested or read_autostart() is not None:
        result = set_autostart()
        print(f"  autostart: {result} ({autostart_command()})")
    else:
        print("  autostart: not set (opt in with: install.py --autostart)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all", action="store_true", help="install hooks for both Claude Code and Antigravity"
    )
    parser.add_argument(
        "--antigravity", action="store_true", help="install hooks for Google Antigravity only"
    )
    parser.add_argument(
        "--claude", action="store_true", help="install hooks for Claude Code only"
    )
    parser.add_argument(
        "--remove-antigravity", action="store_true", help="remove Antigravity hooks"
    )
    parser.add_argument(
        "--remove-claude", action="store_true", help="remove Claude Code hooks"
    )
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

    if args.remove_antigravity:
        print(f"  Antigravity hooks: {remove_antigravity()}")
    if args.remove_claude:
        print(f"  Claude Code hooks: {remove_claude()}")
    if args.remove_antigravity or args.remove_claude:
        return

    # Default to installing all if no specific target is given
    target_all = args.all or (not args.antigravity and not args.claude)

    if target_all or args.antigravity:
        install_antigravity()

    if target_all or args.claude:
        install_claude()

    sync_autostart(args.autostart)


if __name__ == "__main__":
    main()
