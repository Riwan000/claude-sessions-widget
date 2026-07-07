<#
.SYNOPSIS
  Brings the terminal window hosting a specific Claude Code session to the
  foreground, identified by walking the real OS process tree - not by
  matching window/tab titles (Claude Code overwrites tab titles with its own
  dynamic AI-summarized text, which does not reliably contain the project
  name or the raw task text, so title matching was unreliable and could even
  match a completely unrelated session by coincidence).

  Starting from the hook's recorded shell PID, walks up the parent-process
  chain until it finds an ancestor that owns a top-level window, then
  foregrounds that window. This is exact (no guessing) as long as that
  ancestor owns only one top-level window. If the ancestor is a terminal
  app that hosts multiple top-level windows under one process (e.g. Windows
  Terminal with more than one window open), there is no OS-level way to tell
  which of those windows contains this specific session's tab - Windows
  Terminal does not expose a per-tab process id via UI Automation - so this
  falls back to whichever of that process's windows is found first.

  Windows also silently denies SetForegroundWindow calls from a process
  that doesn't hold "recent input" permission - which a freshly spawned
  helper process (this script, launched via QProcess.startDetached) never
  does on its own. AttachThreadInput borrows that permission from whichever
  thread currently owns the real foreground window for the duration of the
  call. The result is verified afterward (not just assumed) and logged to
  widget-status\_focus.log, since a denied call fails silently otherwise -
  nothing throws, the window just never comes forward.

  Prints one of: MATCHED_PID:<pid> / MATCHED_PID:<pid>:FOCUS_DENIED / NOT_FOUND
#>
param(
    [Parameter(Mandatory = $true)]
    [int]$ShellPid
)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WidgetFocusWin32 {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
}
"@

$LogPath = Join-Path $env:USERPROFILE ".claude\widget-status\_focus.log"

function Write-FocusLog([string]$line) {
    try {
        $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Add-Content -Path $LogPath -Value "$timestamp  $line" -ErrorAction SilentlyContinue
    } catch {}
}

function Get-WindowProcessId([IntPtr]$hwnd) {
    [uint32]$processId = 0
    [WidgetFocusWin32]::GetWindowThreadProcessId($hwnd, [ref]$processId) | Out-Null
    return $processId
}

function Get-WindowThreadId([IntPtr]$hwnd) {
    # The out-parameter of GetWindowThreadProcessId is the process id; the
    # function's own return value is the thread id, which is what
    # AttachThreadInput actually needs.
    [uint32]$unusedProcessId = 0
    return [WidgetFocusWin32]::GetWindowThreadProcessId($hwnd, [ref]$unusedProcessId)
}

function Focus-Hwnd([IntPtr]$hwnd) {
    if ($hwnd -eq [IntPtr]::Zero) { return $false }
    if ([WidgetFocusWin32]::IsIconic($hwnd)) {
        [WidgetFocusWin32]::ShowWindow($hwnd, 9) | Out-Null  # SW_RESTORE
    }

    $foregroundThreadId = Get-WindowThreadId ([WidgetFocusWin32]::GetForegroundWindow())
    $currentThreadId = [WidgetFocusWin32]::GetCurrentThreadId()

    $attached = $false
    if ($foregroundThreadId -ne 0 -and $foregroundThreadId -ne $currentThreadId) {
        $attached = [WidgetFocusWin32]::AttachThreadInput($currentThreadId, $foregroundThreadId, $true)
    }

    [WidgetFocusWin32]::SetForegroundWindow($hwnd) | Out-Null
    [WidgetFocusWin32]::BringWindowToTop($hwnd) | Out-Null

    if ($attached) {
        [WidgetFocusWin32]::AttachThreadInput($currentThreadId, $foregroundThreadId, $false) | Out-Null
    }

    Start-Sleep -Milliseconds 50
    $targetProcessId = Get-WindowProcessId $hwnd
    $resultProcessId = Get-WindowProcessId ([WidgetFocusWin32]::GetForegroundWindow())
    return $resultProcessId -eq $targetProcessId
}

# pid -> parent-pid and pid -> exe-name maps, built once
# (cheaper than one WMI query per ancestor)
$parentOf = @{}
$nameOf = @{}
Get-CimInstance Win32_Process | ForEach-Object {
    $parentOf[[int]$_.ProcessId] = [int]$_.ParentProcessId
    $nameOf[[int]$_.ProcessId] = [string]$_.Name
}

# Windows recycles pids: if the recorded claude.exe died without a
# SessionEnd hook (crash, force-closed terminal), some unrelated process may
# now own this pid - walking up from it would focus a random window.
if ($nameOf[$ShellPid] -ne 'claude.exe') {
    Write-FocusLog "ShellPid=$ShellPid : NOT_FOUND (pid not claude.exe - stale record?)"
    Write-Output "NOT_FOUND"
    exit 0
}

$root = [System.Windows.Automation.AutomationElement]::RootElement
$windowCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Window
)
$allWindows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $windowCondition)

$visited = New-Object 'System.Collections.Generic.HashSet[int]'
$current = $ShellPid

while ($current -and $current -ne 0 -and $visited.Add($current)) {
    foreach ($win in $allWindows) {
        if ($win.Current.ProcessId -eq $current) {
            $hwnd = [IntPtr]$win.Current.NativeWindowHandle
            $focused = Focus-Hwnd $hwnd
            if ($focused) {
                Write-FocusLog "ShellPid=$ShellPid -> hwnd pid=$current : FOCUSED"
                Write-Output "MATCHED_PID:$current"
            } else {
                Write-FocusLog "ShellPid=$ShellPid -> hwnd pid=$current : FOCUS_DENIED (window found but did not become foreground - possible causes: elevated/admin terminal vs non-elevated widget, different virtual desktop, or OS focus-steal lock)"
                Write-Output "MATCHED_PID:$current`:FOCUS_DENIED"
            }
            exit 0
        }
    }
    if (-not $parentOf.ContainsKey($current)) { break }
    $current = $parentOf[$current]
}

Write-FocusLog "ShellPid=$ShellPid : NOT_FOUND (no ancestor owns a top-level window)"
Write-Output "NOT_FOUND"
