# Remaining tasks — Claude Sessions Widget

Updated 2026-07-08. The 2026-07-07 review fixes are in (`cfe6590`), and the
deferred items were worked through: **autostart is now implemented**
(`install.py --autostart` → `HKCU\...\Run` entry, registered on this
machine), the **tray icon reflects state** (red while any session needs
permission), and **UI render tests** exist (`tests/test_ui_render.py`,
offscreen; 38 tests passing total).

## 1. Verify width edge-drag visually — 5 min, human-only

The left/right edge-drag width resize (`ui/main_window.py`,
`mousePressEvent` → `startSystemResize`, 8px grab margin
`RESIZE_EDGE_MARGIN`) still hasn't been *dragged by a human*. Text
re-elision on resize is now covered by an automated test
(`TestTaskElision`), so only the drag feel is left to check: grab both
edges of the running widget and resize.

- If the edge is hard to grab → widen `RESIZE_EDGE_MARGIN` (8 → 10-12).
- Note: only the ~14px gutter at the window edges reaches the main
  window's mouse handler; clicks on a session row won't trigger resize.

## 2. Click-to-focus: verify it visually now that FOCUS_DENIED is fixed

Discovered 2026-07-08: `focus_session.ps1` was finding the right window
(`MATCHED_PID`) but never actually verifying `SetForegroundWindow`
succeeded — Windows silently denies that call from a process with no
"recent input" of its own, which is exactly what a helper process spawned
via `QProcess.startDetached` looks like. Fixed by attaching the calling
thread's input queue to the current foreground thread (`AttachThreadInput`)
before calling `SetForegroundWindow`, then verifying the switch actually
happened afterward. Every attempt now logs to
`~/.claude/widget-status/_focus.log` (`FOCUSED` / `FOCUS_DENIED` /
`NOT_FOUND`) since a denied call has no other visible symptom.

Tested by scripting the exact scenario (unrelated foreground app + calling
from an unrelated process) and confirming the terminal actually became
foreground afterward, not just that the script printed success. **Still
needs one real click from the widget UI itself** to close the loop — if a
row-click ever does nothing, check `_focus.log` first. A `FOCUS_DENIED`
line most likely means the terminal is elevated (Run as Administrator)
while the widget isn't — UIPI blocks focus-stealing across privilege
levels and no user-mode trick can bypass that; run both at the same
elevation level to fix it.

## 3. Exact-tab focus with multiple Windows Terminal windows — known limitation

Click-to-focus walks the process tree from the stored `shellPid` (the
`claude.exe` pid) up to the ancestor owning a top-level window
(`focus_session.ps1`). Confirmed limitation: all WT windows share one
process, and WT exposes no per-tab child-process mapping via UI
Automation — so with 2+ WT windows open it foregrounds whichever of that
process's windows was most recently focused (z-order), not necessarily
the right one. It never picks a wrong *project* anymore (the old
title-matching bug), just possibly the wrong *window* of the right app.

Ideas if ever revisiting (all unvalidated):
- `WT_SESSION` env var — hooks could capture it, but there's no public
  API to map `WT_SESSION` → window/tab either.
- Drive WT via `wt.exe -w <id> focus-tab` — needs a window id we can't
  currently obtain for an existing session.
- Accept as-is (current state; documented in README).

## 4. PostToolUse hook overhead — accepted, could revisit

Every tool call in every session spawns `python.exe` (~50-100ms, async)
just to maybe clear a permission state (`handle_tool_complete` early-exits
unless status == "permission"). Kept because it's what clears the red
blink instantly on approval. If overhead ever matters: drop the
`PostToolUse` entry from `~/.claude/settings.json` and let the next
`Stop`/`prompt-submit` clear the permission state instead (worse UX:
blinking persists until turn end).

## 5. Smaller / optional

- **No git remote** — repo is local-only. `gh repo create` + push if
  wanted.
- **Cumulative session token total** — per-turn was chosen deliberately;
  a running total could go in the tooltip if ever wanted.

## Key facts for a cold start

- Hooks: 6 entries in `~/.claude/settings.json` → `hooks/widget_status.py`
  (stdlib-only, always exits 0). Re-wire after moving the project:
  `C:\Python313\python.exe install.py` (idempotent, backs up first; also
  refreshes the autostart Run entry's paths if one exists).
- Autostart: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` →
  `ClaudeSessionsWidget`. Manage via `install.py --autostart` /
  `--remove-autostart`.
- Status files: `~/.claude/widget-status/<session_id>.json`;
  `_window.json` = geometry, `_widget.lock` = single-instance lock.
- Run: `.venv\Scripts\pythonw.exe app.py` (detached, no console). Note:
  the venv `pythonw.exe` is a shim that spawns the real interpreter as a
  child, so one instance = two `pythonw.exe` processes in the tree.
- Tests: `.venv\Scripts\python.exe -m pytest tests/` (38 passing; UI
  tests run on the offscreen Qt platform).
- The running widget never needs a restart for hook-script changes
  (hooks are re-invoked fresh each event); it DOES need a restart for
  changes to `app.py`, `status_store.py`, or `ui/*`.
