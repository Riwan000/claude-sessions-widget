# Claude Sessions Widget

An always-on-top desktop widget that shows, live, every Claude Code CLI session
running on this machine: project (folder name), current task, and whether it's
running, idle, finished, possibly closed, or waiting on your permission. The
window height grows and shrinks automatically with the number of sessions.

## How it works

1. Six hooks are wired into the **global** `~/.claude/settings.json`
   (`SessionStart`, `UserPromptSubmit`, `Notification`, `PostToolUse`, `Stop`,
   `SessionEnd`). Each one calls `hooks/widget_status.py <event>` with the
   Claude Code hook JSON on stdin.
2. That script writes one JSON file per session to `~/.claude/widget-status/`:
   - `SessionStart` → creates the file, `status: "idle"`.
   - `UserPromptSubmit` → `status: "running"`, `task` = the prompt text.
   - `Notification` → if the message mentions "permission" (Claude Code sends
     this whenever it needs you to approve a tool call), `status: "permission"`
     and `alert` = the notification text. Idle-input nudges (the other kind of
     Notification event) are ignored on purpose — this is only for permission
     prompts.
   - `PostToolUse` (a tool actually ran) → clears a `permission` state back
     to `running` (proof the prompt was approved). It only touches records
     currently in the permission state: Stop and PostToolUse are both async
     hooks, so an unconditional write could land after Stop and flip a
     finished session back to running.
   - `Stop` (Claude finishes responding) → `status: "finished"`, plus
     `tokens: {input, output}` for that turn (see below).
   - `SessionEnd` (the CLI actually exits) → deletes the file.
3. `app.py` polls that directory every 2 seconds and renders one row per file.
   - A `running` session whose file hasn't been touched in 10+ minutes is
     shown as **possibly closed** (amber) — this covers a terminal window
     being force closed without Claude Code getting a chance to run
     `SessionEnd`.
   - A session waiting on a permission prompt is sorted to the very top and
     its accent bar/status dot blink red every 500ms until it clears.
4. Files untouched for 24+ hours are pruned automatically as basic garbage
   collection for crashed/never-closed sessions.
5. **Token count per turn**: `UserPromptSubmit` records the byte offset of the
   session's transcript file (`transcript_path` from the hook payload) at the
   moment the prompt is submitted. `Stop` reads everything appended to that
   transcript since that offset and sums the `usage.input_tokens` /
   `usage.output_tokens` fields across every assistant message in that turn.
   Usage is deduplicated by message id, because one API message can span
   several transcript lines (e.g. separate entries for its thinking block
   and its tool_use block) that each repeat the same usage object — summing
   per line would roughly double the count. If no offset was ever recorded
   (a resumed session where `UserPromptSubmit` never fired), the token field
   is skipped rather than summing the entire transcript as one turn.
   This is the fresh input+output count — it deliberately excludes
   `cache_read_input_tokens`/`cache_creation_input_tokens`, which are often
   100x larger than the real per-turn cost due to prompt caching and would
   dominate the display. The full breakdown is available in the row's tooltip
   if you hover it; only the input+output total is shown inline (e.g. "30k
   tok"). If no transcript path is available, the token badge is just omitted
   for that row — this never blocks or errors.

## Setup

```powershell
cd widget
C:\Python313\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
C:\Python313\python.exe install.py
```

`install.py` wires the 6 hooks into `~/.claude/settings.json` (backing it up
first) and is idempotent — re-run it any time you move this project folder
or switch Python installs, and it updates the existing entries in place.
Run it with the Python you want the hooks to use (the hook script is
stdlib-only; avoid the venv interpreter so the hooks don't break if the
venv is rebuilt).

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/
```

Covers the hook script's token accounting (including the message-id
dedupe and resumed-session cases), process-tree walking, status
transitions, and the status store's sorting/staleness/pruning logic.
`tests/test_ui_render.py` additionally exercises the Qt layer offscreen
(no window appears): row lifecycle, task elision at different widths,
the permission blink/signal plumbing, and tray icon colors.

## Running

```powershell
.venv\Scripts\python.exe app.py
```

To launch without a console window flashing, use `pythonw.exe` instead:

```powershell
.venv\Scripts\pythonw.exe app.py
```

Only one instance runs at a time — a second launch (e.g. from a startup
shortcut while one is already running) exits immediately instead of showing
a duplicate window (`~/.claude/widget-status/_widget.lock`).

The widget starts in the top-left-ish area by default and remembers wherever
you drag it (position saved to `~/.claude/widget-status/_window.json`,
debounced). Drag anywhere on the header to move it; drag the left or right
window edge to change the width. Height is automatic — it grows and shrinks
with the number of sessions shown, capped at 80% of your screen height (it
scrolls internally beyond that).

It also adds a system tray icon with a right-click menu: **Show/Hide**,
**Clear finished**, **Quit**. The icon doubles as a status light: green
normally, red while any session is waiting on a permission prompt — so
you still get the signal when the window itself is hidden to the tray.

**Click any row** to jump to that session's terminal. This is *not* done by
matching window/tab titles — an earlier version tried that and it was
unreliable, because Claude Code overwrites the tab title with its own
AI-summarized description of the current task, which doesn't reliably
contain the project name or the literal task text (and a short project name
could coincidentally substring-match a completely unrelated tab).

Instead, every hook call records `shellPid` = the pid of the actual `claude.exe`
process for that session. Naively, this could just be `os.getppid()` (the
hook's immediate parent) — but that turned out to be wrong: Claude Code
spawns hook commands through a short-lived shell wrapper that exits the
instant the hook finishes, so by the time you click a row later, that pid is
already dead. `find_stable_ancestor_pid()` walks up the real process tree
(via a `CreateToolhelp32Snapshot`, no subprocess spawn) from that ephemeral
pid to find the actual `claude.exe` ancestor, which stays alive for the
whole CLI session — that's what gets stored.

On click, `focus_session.ps1` walks up from that stable pid until it finds
an ancestor that owns a top-level window, then foregrounds it — exact, no
title-guessing. This is 100% precise when that ancestor owns exactly one
top-level window. The one remaining limitation, confirmed by testing with 2
Windows Terminal windows open side by side: if you have *multiple* Windows
Terminal windows, they're all one process, and Windows Terminal doesn't
expose which of its windows/tabs hosts which child process via UI
Automation — so in that case it foregrounds whichever of that process's
windows was **most recently in the foreground** (confirmed empirically: it's
z-order-based, not an arbitrary/fixed pick), which is a reasonable
best-effort but isn't guaranteed to be the exact right one. If nothing
can be resolved at all (e.g. an old status file from before this feature
existed, with no `shellPid` recorded), clicking is just a no-op.

## Autostart on login

```powershell
C:\Python313\python.exe install.py --autostart      # register
C:\Python313\python.exe install.py --remove-autostart  # unregister
```

This writes a `ClaudeSessionsWidget` value under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` pointing at the venv's
`pythonw.exe` (no console window). A plain `install.py` run refreshes the
entry's paths if it already exists (e.g. after moving the project) but never
creates one — enabling login autostart stays an explicit opt-in. The
single-instance lock makes a login launch while the widget is already
running a harmless no-op.

## Files

```
widget/
  app.py                 # entry point: window, tray icon, poll timer, single-instance lock
  status_store.py        # reads widget-status/*.json, sorts, flags stale/prunes old
  focus_session.ps1      # click-to-focus: walks the process tree, no title matching
  install.py             # wires/re-wires the 6 hooks; --autostart manages the login Run entry
  ui/
    main_window.py         # frameless/translucent/always-on-top window
    session_row.py          # one row's widgets + rendering + click handling
    style.qss                # stylesheet
  hooks/
    widget_status.py        # the Claude Code hook script (see "How it works")
  tests/                 # pytest suite for the hook script and status store
  requirements.txt
```
