# Remaining tasks — Claude Sessions Widget

Everything from the 2026-07-07 code review is implemented and committed
(`b5eddb2` baseline → `cfe6590` fixes; 28 tests passing). What's below is
the leftover / deferred work, with enough context to pick up cold.

## 1. Verify width edge-drag and text elision visually — 5 min, do first

The left/right edge-drag width resize (`ui/main_window.py`,
`mousePressEvent` → `startSystemResize`, 8px grab margin
`RESIZE_EDGE_MARGIN`) and the width-aware task elision
(`ui/session_row.py`, `_apply_task_elide`) were verified by tests and
imports only, **never visually**. Launch the widget, drag both edges,
confirm text re-elides while resizing.

- If the edge is hard to grab → widen `RESIZE_EDGE_MARGIN` (8 → 10-12).
- Note: only the ~14px gutter at the window edges reaches the main
  window's mouse handler; clicks on a session row won't trigger resize.

## 2. Autostart on login — deferred by explicit decision

Never implemented (changing login behavior was deemed worth its own
ask). Manual instructions are in README "Not included (by design)":
a `shell:startup` shortcut to `.venv\Scripts\pythonw.exe app.py`.
If automating instead: a small `install.py` extension could create the
shortcut (or a `HKCU\...\Run` registry entry). The single-instance lock
(`app.py`, `QLockFile`) already makes double-launch at login safe.

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
- `WindowsTerminal` session env var `WT_SESSION` — hooks could capture it,
  but there's no public API to map `WT_SESSION` → window/tab either.
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
- **UI test coverage** — pytest covers hook script + status store (pure
  logic). `ui/` has no automated tests; the offscreen-render scripts used
  during development lived in the session scratchpad and are gone.
  Could add a `tests/test_ui_render.py` using `QApplication` + `grab()`.
- **Tray icon** is a plain green dot; could reflect state (red when any
  session needs permission).
- **Cumulative session token total** — per-turn was chosen deliberately;
  a running total could go in the tooltip if ever wanted.

## Key facts for a cold start

- Hooks: 6 entries in `~/.claude/settings.json` → `hooks/widget_status.py`
  (stdlib-only, always exits 0). Re-wire after moving the project:
  `C:\Python313\python.exe install.py` (idempotent, backs up first).
- Status files: `~/.claude/widget-status/<session_id>.json`;
  `_window.json` = geometry, `_widget.lock` = single-instance lock.
- Run: `.venv\Scripts\pythonw.exe app.py` (detached, no console).
- Tests: `.venv\Scripts\python.exe -m pytest tests/` (28 passing).
- The running widget never needs a restart for hook-script changes
  (hooks are re-invoked fresh each event); it DOES need a restart for
  changes to `app.py`, `status_store.py`, or `ui/*`.
