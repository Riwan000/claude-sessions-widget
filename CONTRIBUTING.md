# Contributing

Thanks for considering a contribution to Claude Sessions Widget.

## Scope

This is a small, focused, **Windows-only** desktop utility. Known gaps and
deliberately-accepted tradeoffs (e.g. the Windows Terminal multi-window
focus limitation — see the README's "Click any row" section) are documented
inline next to the code they explain, rather than tracked separately.

## Setup

```powershell
cd widget
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

See the README's "Setup" section for wiring the Claude Code hooks locally.

## Before opening a PR

```powershell
.venv\Scripts\python.exe -m pytest tests/
```

- All tests must pass, including the offscreen Qt tests in
  `tests/test_ui_render.py` (`QT_QPA_PLATFORM=offscreen`, no window appears).
- Add or update tests for any behavior change — see the existing test files
  for the patterns used (fixtures in `conftest.py`, `write_session()` helpers
  for status-file based tests).
- Match the existing docstring style: comments explain *why* a decision was
  made (especially around Windows quirks — focus-stealing, process-tree
  walking, pid reuse), not what the code obviously does.

## Reporting bugs

Open an issue with:
- What you expected vs. what happened
- Whether it reproduces with a single Claude Code session or only with
  multiple concurrent sessions
- If it's a click-to-focus issue, the contents of
  `~/.claude/widget-status/_focus.log` after the failed attempt

## Pull requests

Keep PRs narrowly scoped — one behavior change per PR.
