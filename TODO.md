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

## 3. Exact focus with multiple Windows Terminal windows — confirmed unfixable with public APIs

Click-to-focus walks the process tree from the stored `shellPid` (the
`claude.exe` pid) up to the ancestor owning a top-level window
(`focus_session.ps1`). Confirmed on 2026-07-08 (this isn't a "might be a
tabs thing" anymore — actually inspected live):

- This machine has 2+ real, separate Windows Terminal **windows** open
  (not just tabs of one), confirmed via UI Automation
  (`ProcessIdProperty` query returned 2 distinct `NativeWindowHandle`
  values, titled e.g. `✳ market-insight` and `⠂ <task summary>`) — both
  owned by the **same OS process**. UI Automation can only match by
  `ProcessId`, so with 2+ windows sharing one pid there is no property
  that tells you which one hosts which shell; the script just takes
  whichever window enumerates first (usually whatever's already
  frontmost — not necessarily right).
- Confirmed the window title cannot be used as a tiebreaker either: this
  session's raw hook-recorded task was `"continue"` (the literal last
  prompt) while the live window title was `⠂ Implement TODO fixes and
  autostart setup` (Claude Code's own AI-summarized title) — the two
  don't correlate, which is exactly why the original design rejected
  title-matching. There's no salvageable fuzzy-match here.
- Checked upstream: microsoft/terminal issue
  [#18692](https://github.com/microsoft/terminal/issues/18692) asks for
  exactly this capability (query which tab/window hosts a given
  process), is still **open**, parked in their "Icebox," and the only
  known workaround anyone's found is `ReadProcessMemory` into WT's own
  memory to read internal tab-index state — not something to build a
  feature on (breaks on every WT update, no supported API).
- `wt.exe focus-tab -t <index>` exists but needs a tab index we have no
  way to derive from a bare child-process pid.

**Conclusion: not solvable from our side today.** It's a confirmed,
still-unresolved gap in Windows Terminal itself, not a bug in this
widget. Click-to-focus still correctly avoids the old failure mode
(bringing forward a totally unrelated app) but can't guarantee the exact
right WT window when 2+ are open. The only real lever: run sessions you
want guaranteed-precise focus on in a genuinely separate-process
terminal host (plain `conhost`-based `cmd.exe`/`powershell.exe`, not
Windows Terminal) — those really are one process per window, so the
existing pid-walk logic already handles them exactly. Revisit only if
Microsoft ships a fix for #18692.

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
