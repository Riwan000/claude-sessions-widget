# AI Sessions Widget & Companion

**Windows only** — the click-to-focus and autostart features are built on
Win32 APIs (`winreg`, `AttachThreadInput`/`SetForegroundWindow` via
`focus_session.ps1`) with no cross-platform equivalent implemented.

> Unofficial, community-built tool. Compatible with Anthropic's **Claude Code**
> and Google's **Antigravity**. Not affiliated with, endorsed by, or supported by
> Anthropic or Google. Trademarks belong to their respective owners.

An always-on-top desktop widget and roaming desktop companion (Baymax) that
tracks, live, every Claude Code CLI and Google Antigravity session running on
your machine: project, current tool, prompt, model, per-turn tokens, full context
size, and whether it's running, idle, finished, or waiting on your permission.

![AI Sessions Widget in Emerald theme showing permission alert, Google Antigravity and Claude Code sessions, model and context pills, and Baymax companion roaming the taskbar](docs/screenshot.png)

*(Rendered from synthetic demo data for illustration — not a real session.)*

## Key Highlights

- **Multi-Tool Live Tracking**: Seamlessly monitors active sessions from **Claude Code** and **Google Antigravity** (with official tool badges for Claude, Antigravity, and Cursor).
- **Desktop Avatar Companion (Baymax)**: An animated procedural companion roaming along your taskbar with Big Hero 6 mechanics. Waddles with real-time speech bubbles, sweeps vertical scan beams while tools run, suits up in flying superhero mech armor when permissions are needed, and deflates into his recharge station box when idle.
- **Click-to-Anchor Pop-up**: Clicking Baymax opens the full sessions window anchored directly above his head; the window automatically expands and contracts upward so it stays perfectly aligned. Toggle between Avatar Mode and Standalone Widget Mode anytime via the system tray.
- **Cyberpunk / Emerald Theme**: Modern glassmorphic styling with glowing status bars, live blinking permission alerts, and clear tier-colored token and context badges.
- **Deep Metrics**: Real-time per-turn token usage, cumulative context window size tracking, model pills (e.g. `Sonnet 3.7`, `Gemini 2.5 Pro`, `Opus 3.5`), and task duration.
- **Task Completion Notifications & Audio Chime**: Tray alert with audio chime on long-running task completions (>5s); click to instantly foreground the corresponding terminal or IDE window.
- **CSV History Log**: Append-only logging of completed tasks across all sessions into `history.csv`.

## How it works

1. **Dual Hook Architecture**:
   - **Claude Code**: 7 hooks are wired into the global `~/.claude/settings.json` (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Notification`, `PostToolUse`, `Stop`, `SessionEnd`), calling `hooks/widget_status.py <event>` with the hook payload on stdin.
   - **Google Antigravity**: 4 lifecycle hooks are wired into `~/.gemini/config/hooks.json` (`PreInvocation`, `PreToolUse`, `PostToolUse`, `Stop`), calling `hooks/antigravity_status.py <event>` with event JSON on stdin.
2. Both hook scripts write standardized session JSON records to `~/.claude/widget-status/`:
   - `SessionStart` → creates the file, `status: "idle"`, and sniffs
     `languageIcon` once from top-level marker files in the project
     directory (`pyproject.toml`/`requirements.txt`/`setup.py`/any `*.py`
     → 🐍, `package.json` → 📦, `Cargo.toml` → 🦀, `go.mod` → 🐹). Cached on
     first write and never recomputed, so it can't flicker mid-session —
     see `detect_language_icon()`. Recorded in the status file but not
     currently rendered in the UI (the project name is shown plain); kept
     around for tooling/future use.
   - `UserPromptSubmit` → `status: "running"`, `task` = the prompt text, and
     clears any leftover tool line from the previous turn.
   - `PreToolUse` (a tool is about to run) → `currentTool` = the tool name,
     `currentToolDetail` = a one-line human description derived from
     `tool_input` (e.g. `Read` on `README.md` → "Reading README.md", `Bash`
     with `pytest tests/` → "Running pytest tests/"). Unrecognized tools
     (custom/MCP tools) fall back to "Using \<ToolName\>" rather than showing
     nothing. This line takes priority over the plain task text in the row
     while it's set, so you can see what Claude is actually doing turn by
     turn — see `describe_tool_use()`. The same `currentTool` value also
     drives a short "personality" tag next to the project name (e.g. "✏️
     Editing", "🧠 Thinking" while no tool is active yet this turn, "✅
     Done" once finished, "💤 Idle" before the first prompt) — computed
     purely in the UI from data already in the status file, see
     `_personality_text()` in `ui/session_row.py`. It's suppressed for the
     `permission` and `stale` states, which already have their own
     dedicated visual treatment.
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
     `tokens: {input, output}` for that turn (see below), records
     `turnDuration`, clears the tool line, and appends the completed task to
     `history.csv` in the widget project folder.
   - `SessionEnd` (the CLI actually exits) → deletes the file.
3. `app.py` polls that directory every 2 seconds and renders one row per file.
   - A `running` session whose file hasn't been touched in 10+ minutes is
     shown as **possibly closed** (amber) — this covers a terminal window
     being force closed without Claude Code getting a chance to run
     `SessionEnd`.
   - A session waiting on a permission prompt is sorted to the very top and
     its accent bar/status dot blink red every 500ms until it clears.
4. **Task completion notifications**: when a session finishes a turn that took
   at least 5.0 seconds (customizable via `WIDGET_NOTIFY_THRESHOLD_SECONDS`),
   a system tray notification and gentle audio chime trigger. Clicking the
   notification focuses the terminal or IDE window that completed the task.
5. **CSV History Logging**: every completed task across Claude Code and
   Antigravity is appended to `history.csv` (located directly in the widget
   folder) with: `timestamp`, `project`, `cli_or_ide`, `model`,
   `duration_seconds`, `prompt`, `tokens_in`, `tokens_out`, and
   `context_tokens`. Entries are append-only and never overwritten.
6. Sessions inactive for more than 15 minutes are removed from the list
   and pruned automatically, and a system tray notification is displayed.
7. **Token count per turn**: `UserPromptSubmit` records the byte offset of the
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
8. **Token pill colors**: both token pills (per-turn and context, see below)
   tint using the same tiers — grey by default, yellow past 100k, orange past
   150k, red past 200k (see `token_tier()` in `status_store.py`).
9. **Context size**: the per-turn token pill deliberately excludes
   `cache_read_input_tokens`/`cache_creation_input_tokens` (see above), but
   that means it can't show how large the conversation itself has gotten —
   which is what actually gets resent (and billed) on every future turn, and
   what eventually triggers auto-compaction. `Stop` also calls
   `latest_context_size()`, which reads just the tail of the transcript
   (bounded to `CONTEXT_TAIL_BYTES`, so the cost doesn't grow with session
   length) and takes `input_tokens + cache_creation_input_tokens +
   cache_read_input_tokens` from the *last* usage entry seen — i.e. the full
   size of what was actually sent to the model on the most recent API call.
   That's stored as `contextTokens` and rendered as a second pill (e.g. "82k
   ctx") next to the per-turn one, using the same `token_tier()` color
   thresholds — which is the number those thresholds actually describe well,
   since 100k–200k of *cumulative* context is a realistic and meaningful
   range, unlike 100k–200k in a single turn. The first time a session's
   `contextTokens` crosses 150k, a one-time tray notification fires
   suggesting you start a fresh session (large contexts get slower and more
   expensive); it won't repeat for that session even if the total keeps
   climbing, and it resets if the session disappears and a new one reuses
   the slot. It's deliberately keyed off context size rather than the
   per-turn total — a single huge turn (e.g. reading one large file) isn't
   the same signal as a conversation that's actually grown large — see
   `_check_token_warning()` in `ui/main_window.py`.
10. **`cd` commands are shortened** in the task line to just the target
    folder (e.g. `cd "C:/long/path/to/widget" && pytest` → `cd widget/ &&
    pytest`) — see `_shorten_cd_command()` in `hooks/widget_status.py`. Long
    absolute paths otherwise dominate the line with no useful signal.
11. **Model pill**: `Stop` also calls `latest_model()`, which reads the same
   transcript tail as `latest_context_size()` and pulls the `model` field off
   the most recent assistant message — the model actually used for the last
   API call, so a mid-session `/model` switch (or an automatic fallback)
   shows up rather than whatever the session started with. The raw id (e.g.
   `claude-sonnet-5-20250929`) is stored as-is, but `short_model_label()` in
   `status_store.py` turns it into a short label (`Sonnet 5`) for the pill —
   raw ids are too long for the row, and the version-before/after-family
   ordering has changed across model generations, so both are parsed. The
   pill sits directly left of the context pill on the task line, forming one
   right-aligned cluster under the token pill rather than spreading across
   the row; the raw id is still available in the row's tooltip. Like the
   token/context pills, it's only known once a turn completes, so a
   freshly-started session shows no model pill until its first response
   finishes.
12. **Official tool icons**: Session rows dynamically show crisp official
    source icons (`ui/assets/claude.png`, `ui/assets/antigravity.png`,
    `ui/assets/cursor.png`) with clean fallback badges (`🤖`, `🌊`, etc.) for
    custom or third-party tools.
13. **Antigravity token & context telemetry**: `hooks/antigravity_status.py`
    inspects session JSONL transcripts at turn boundaries, estimating per-turn
    tokens (via `tiktoken` with heuristic fallback) and computing full cumulative
    conversation context size across turns. Completed tasks are appended to
    `history.csv` with complete token metrics.
14. **Baymax Desktop Companion Mechanics**:
    - **60 FPS procedural rendering**: Rendered entirely through vector and pixel
      math with zero external sprite dependencies.
    - **Live status bubble**: Clean matte speech bubble hovering above Baymax,
      showing the current prompt and project name. If multiple sessions run
      concurrently, the bubble cycles through active tasks every 3 seconds.
    - **Reactive State Machine**:
      - `in_box_sleeping`: Resting in his Malachite & Spruce recharge station box
        when idle.
      - `inflating_out`: Automatically inflates up when a prompt is submitted.
      - `waddling`: Signature slow waddle along the taskbar during work.
      - `working`: Sweeping vertical scan beam across his chassis while tools run.
      - `permission`: Suits up in superhero mech armor with jet thrusters and hovers
        in flight along the taskbar when a session needs approval!
      - `deflating_in`: Safely packs away into his recharge box when sessions finish.
    - **Smart Anchor Pop-up**: Left-clicking Baymax opens the sessions list
      directly above his head. If sessions are added or finished, the window
      smoothly expands and contracts upward so it stays anchored to Baymax.

## Setup

```powershell
cd widget
C:\Python313\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Wire hooks for both Claude Code and Google Antigravity:
C:\Python313\python.exe install.py --all

# Or wire hooks individually:
C:\Python313\python.exe install.py --claude
C:\Python313\python.exe install.py --antigravity
```

`install.py` wires the hooks into `~/.claude/settings.json` and
`~/.gemini/config/hooks.json` (backing up each config file first) and is
idempotent — re-run it any time you move this project folder or switch Python
installs, and it updates the existing entries in place. Run it with the Python
you want the hooks to use (stdlib-only hook scripts avoid breakages if the venv
is rebuilt).

## Tests

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
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

### Desktop Companion (Baymax) & Controls

- **Click Baymax**: Left-clicking the Baymax avatar on your taskbar summons or hides
  the full sessions window directly above his head. The window smartly expands
  upward so the anchor stays aligned.
- **Drag & Reposition**: Click and drag Baymax to reposition him along your
  taskbar or across multi-monitor setups.
- **Speech Bubble Ticker**: Baymax's matte speech bubble automatically displays
  the latest active prompt and tool. When running concurrent sessions, it rotates
  every 3 seconds through all active tasks.

The **▾ button** in the header collapses the window down to just the header
bar, hiding the session list — useful when you want the widget parked on
screen without it taking up room. Click **▸** to expand it again. The
collapsed state is saved to `_window.json` alongside the position and width,
so it survives a restart.

### System Tray

The widget adds a system tray icon with a right-click menu:
- **Show / Hide**: toggle window visibility.
- **Avatar Mode**: toggle checkbox to switch between roaming Baymax desktop
  companion mode and classic standalone window mode.
- **Clear finished**: dismiss finished sessions from view.
- **Quit**: exit the application.

The tray icon doubles as a status light: green normally, red while any
session is waiting on a permission prompt — so you still get the signal even
when the window is hidden.

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
best-effort but isn't guaranteed to be the exact right one. This is a
confirmed gap in Windows Terminal itself, not something fixable from
outside it — see [microsoft/terminal#18692](https://github.com/microsoft/terminal/issues/18692)
(open, unresolved as of this writing; the only known workaround is
`ReadProcessMemory` into WT's own memory, not something to depend on).
If nothing can be resolved at all (e.g. an old status file from before
this feature existed, with no `shellPid` recorded), clicking is just a
no-op.

Finding the right window isn't enough on its own — Windows silently denies
`SetForegroundWindow` calls from a process with no "recent input" of its
own, which is exactly what a helper process spawned just to do the
focusing (`QProcess.startDetached`) looks like from the OS's perspective.
Nothing throws in that case; the call just does nothing. `focus_session.ps1`
works around this with `AttachThreadInput` (borrowing foreground permission
from whichever thread currently owns the real foreground window for the
duration of the call), then verifies the switch actually happened
afterward instead of assuming success. Every attempt — matched, denied, or
not-found — is appended to `~/.claude/widget-status/_focus.log`, since a
denied focus call has no other visible symptom. If clicking a row ever
stops working, that log says why (a `FOCUS_DENIED` line most likely means
the terminal is running elevated/as-Administrator while the widget isn't,
since UIPI blocks focus-stealing across privilege levels no matter what;
matching them fixes it).

## Lifecycle

The widget starts and stops itself around your sessions — no login autostart needed:

- **Start**: The `SessionStart` hook (`hooks/widget_status.py`) or `PreInvocation`
  hook (`hooks/antigravity_status.py`) launches the widget with the venv's
  `pythonw.exe` (no console window) the first time any session begins. The
  single-instance lock makes every subsequent launch attempt a harmless no-op.
- **Stop**: In standalone mode, the widget polls its status directory every 2s
  (`app.py:quit_if_idle`) and quits once no session status files remain. In Avatar
  Mode, Baymax deflates back into his recharge station box and sleeps until the
  next session arrives.

Optional: `install.py --autostart` / `--remove-autostart` registers an
`AISessionsWidget` value under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` if you'd rather have
the widget up before your first session starts. A plain `install.py` run
refreshes the entry's paths if it already exists but never creates one.

## Files

```
widget/
  app.py                 # entry point: window, tray icon, poll timer, single-instance lock
  status_store.py        # reads widget-status/*.json, sorts, flags stale/prunes old
  focus_session.ps1      # click-to-focus: walks the process tree, no title matching
  install.py             # wires hooks into Antigravity (~/.gemini) and Claude (~/.claude)
  history.csv            # append-only log of all completed tasks and token metrics
  ui/
    avatar_window.py     # Baymax procedural desktop companion roaming the taskbar
    main_window.py       # frameless/translucent/always-on-top sessions window
    session_row.py       # session row widgets, status indicators, and pill badges
    style.qss            # modern Emerald glassmorphism stylesheet
    assets/              # official tool badges (claude.png, antigravity.png, cursor.png)
  hooks/
    antigravity_status.py# Google Antigravity hook handler (tokens, tools, context)
    widget_status.py     # Claude Code hook handler (tokens, tools, context)
  tests/                 # pytest suite for hooks, status store, avatar, and Qt layer
    conftest.py          # puts project root and hooks/ on sys.path
    test_avatar_window.py# avatar state machine, physics, and interaction tests
    test_antigravity_status.py
    test_history_csv.py
    test_status_store.py
    test_ui_render.py
    test_widget_status.py
  docs/
    screenshot.png       # visual showcase embedded at the top of this README
  requirements.txt       # runtime dep (PySide6)
  requirements-dev.txt   # runtime + test deps (pytest)
```
