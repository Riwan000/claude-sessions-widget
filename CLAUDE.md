# CLAUDE.md

Always-on-top Windows desktop widget that shows every live Claude Code CLI
session. README.md documents *what* it does and *why* each design decision was
made, in detail — read the relevant section before changing behavior it
describes. This file covers what you need to work on the code.

## Commands

```powershell
.venv\Scripts\python.exe -m pytest tests/     # tests (185, ~2s, no window appears)
.venv\Scripts\python.exe app.py               # run the widget (console)
.venv\Scripts\pythonw.exe app.py              # run with no console window
C:\Python313\python.exe install.py            # wire hooks into Antigravity (~/.gemini) & Claude (~/.claude)
C:\Python313\python.exe install.py --antigravity  # wire Antigravity hooks only
C:\Python313\python.exe install.py --claude       # wire Claude Code hooks only
```

`install.py` bakes `sys.executable` into the hook commands, so **run it with
the Python you want the hooks to use** — not the venv's. Re-run it after
moving the project folder or switching Python installs; it's idempotent and
backs up config files first.

## Architecture

Processes talking through the filesystem (`~/.claude/widget-status/`):

1. `hooks/widget_status.py` — invoked by Claude Code on 7 hook events.
2. `hooks/antigravity_status.py` — invoked by Google Antigravity on PreInvocation, PreToolUse, PostToolUse, and Stop events.
3. `app.py` + `ui/` — polls the status directory every 2s via `status_store.py` and renders a row per session with tool source badges (🟣 Claude, 🔷 Antigravity).

`status_store.py` is imported only by the UI side. `focus_session.ps1` is
spawned on row click to foreground a session's terminal or window.

## Hard constraints

**`hooks/widget_status.py` must stay stdlib-only.** It runs under whatever
Python `install.py` was invoked with — usually a bare system install with no
venv and no PySide6. Importing `status_store` or any third-party package
breaks every hook on the user's machine. Duplicating a small helper is the
correct trade here.

**Hooks must never raise or block.** Failures have to be invisible to Claude
Code, so `main()` runs inside a top-level `try/except` and the process always
exits 0. Keep new work inside that guarantee, and keep it fast — hooks run on
every tool call.

**`Stop` and `PostToolUse` are both async hooks and can land out of order.**
This is why `handle_tool_complete()` only touches records already in the
`permission` state: an unconditional write there can arrive after `Stop` and
flip a finished session back to running. Any new handler that writes state
needs the same reasoning.

**Windows-only, deliberately.** `winreg`, the Toolhelp32 process walk, and the
`AttachThreadInput` focus dance have no cross-platform equivalent here. Don't
add portability shims without asking.

## The status-file contract

The JSON files are the interface between the two processes. Keys are camelCase
on disk (`contextTokens`, `shellPid`, `currentToolDetail`) and snake_case on
the `Session` dataclass — `status_store._load_one()` is the only translation
point, so a new field means editing both sides plus the writer in
`hooks/widget_status.py`.

Files starting with `_` in the status directory are infrastructure, not
sessions: `_widget.lock`, `_window.json`, `_focus.log`. `get_sessions()` and
`clear_finished()` skip them by prefix — anything new you drop in that
directory must follow the same convention or it'll be parsed as a session.

## Token accounting

Easy to get subtly wrong; all three rules exist because the naive version was
wrong:

- Usage is deduplicated **by message id** — one API message spans several
  transcript lines that each repeat the same usage object, so summing per line
  roughly doubles the count.
- The per-turn total **excludes** `cache_read_input_tokens` /
  `cache_creation_input_tokens` (often 100x the real cost). `contextTokens`
  deliberately **includes** them — it's the size of what gets resent each turn.
- No recorded offset (a resumed session where `UserPromptSubmit` never fired)
  means **skip the token field**, never sum from 0 — that would count the whole
  transcript as one turn.

## Conventions

- **No type annotations** anywhere in this codebase; match that.
- Docstrings and comments explain **why**, not what — usually the wrong
  approach that was tried first and the reason it failed. Preserve that when
  editing; it's the point of them.
- Keep README.md in sync when you change described behavior. It's unusually
  narrative and users rely on it.
- Commits: `type: description` (feat/fix/docs/refactor/test/chore), no
  attribution footer.
- CI runs on windows-latest across Python 3.10–3.13, so hook code must stay
  compatible with 3.10.
