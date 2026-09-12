# AI Sessions Widget & Companion

A lightweight, always-on-top desktop widget and roaming taskbar companion (**Baymax**) for Windows that tracks your **Claude Code** and **Google Antigravity** sessions in real time.

![AI Sessions Widget in Emerald theme showing permission alert, Google Antigravity and Claude Code sessions, model and context pills, and Baymax companion roaming the taskbar](docs/screenshot.png)

> **Note:** Windows only. Built for Claude Code and Google Antigravity. Unofficial community tool, not affiliated with Anthropic or Google.

---

## Features

- **Live Session Tracking**: See project names, current tasks, active tools, and status (*running*, *thinking*, *editing*, *idle*, *done*, or *waiting for permission*).
- **Baymax Desktop Companion**: An animated companion that roams your taskbar. Waddles with task speech bubbles, sweeps a scan beam while tools run, suits up in flying superhero mech armor on permission alerts, and sleeps in his recharge box when idle.
- **Click to Focus**: Click any session row to instantly bring its terminal or IDE window to the front. Click Baymax to toggle the widget directly above his head!
- **Token & Model Badges**: Clear pills show the active model (e.g. `Sonnet 3.7`, `Gemini 2.5 Pro`), per-turn tokens, and total context size with color-coded alerts.
- **Sound & Tray Alerts**: Gentle chime and tray notification when a long task (>5s) finishes, or when a session needs your approval.
- **Task History**: Automatically logs completed tasks, duration, model, and token usage to `history.csv`.
- **Emerald Theme**: Clean, modern dark-green glassmorphism interface with official tool logos.

---

## Quick Start

### 1. Install

Open PowerShell in the `widget` folder:

```powershell
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Wire hooks for both Claude Code and Google Antigravity:
python install.py --all
```

*(You can also use `python install.py --claude` or `python install.py --antigravity` to set up hooks individually).*

### 2. Run

```powershell
# Run with console
.venv\Scripts\python.exe app.py

# Or run silently in the background (no console window)
.venv\Scripts\pythonw.exe app.py
```

> **Auto-start with sessions**: Once hooks are installed, the widget automatically launches when you start a session in Claude Code or Antigravity, and closes when all sessions end.

---

## Usage & Controls

- **Click Baymax**: Shows or hides the sessions list right above him. The window smoothly resizes upward to stay anchored to him.
- **Drag Baymax**: Move him anywhere along your taskbar or across monitors.
- **Click a Session**: Brings that terminal or IDE window to the foreground.
- **Header Buttons**:
  - `▾` / `▸`: Collapse the window to just the header bar, or expand it back.
  - `—`: Hide the window to the system tray.
- **System Tray Icon**:
  - Right-click the tray icon to **Show/Hide**, toggle **Avatar Mode** on/off, **Clear finished** sessions, or **Quit**.
  - The icon glows green normally and turns red if any session needs your permission.
- **Start on Windows Login (Optional)**: Run `python install.py --autostart` to start the widget when you log into Windows (remove anytime with `--remove-autostart`).

---

## Running Tests

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\pytest tests/
```

---

## Project Structure

```
widget/
  app.py                  # Main entry point, tray icon, and polling loop
  status_store.py         # Reads and manages session data from disk
  focus_session.ps1       # Script to focus terminal/IDE window on click
  install.py              # Installs hooks into Claude Code and Antigravity
  history.csv             # Completed tasks log (model, duration, tokens)
  ui/
    avatar_window.py      # Baymax desktop companion roaming the taskbar
    main_window.py        # Frameless sessions list window
    session_row.py        # Individual session row and badge rendering
    style.qss             # Emerald glassmorphic stylesheet
    assets/               # Tool logos (Claude, Antigravity, Cursor)
  hooks/
    antigravity_status.py # Google Antigravity lifecycle hook
    widget_status.py      # Claude Code lifecycle hook
  tests/                  # Full pytest test suite
  docs/
    screenshot.png        # UI screenshot
```
