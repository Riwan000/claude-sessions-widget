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

  Prints one of: MATCHED_PID:<pid> / NOT_FOUND
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
}
"@

function Focus-Hwnd([IntPtr]$hwnd) {
    if ($hwnd -eq [IntPtr]::Zero) { return $false }
    if ([WidgetFocusWin32]::IsIconic($hwnd)) {
        [WidgetFocusWin32]::ShowWindow($hwnd, 9) | Out-Null  # SW_RESTORE
    }
    return [WidgetFocusWin32]::SetForegroundWindow($hwnd)
}

# pid -> parent-pid map, built once (cheaper than one WMI query per ancestor)
$parentOf = @{}
Get-CimInstance Win32_Process | ForEach-Object { $parentOf[[int]$_.ProcessId] = [int]$_.ParentProcessId }

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
            Focus-Hwnd $hwnd | Out-Null
            Write-Output "MATCHED_PID:$current"
            exit 0
        }
    }
    if (-not $parentOf.ContainsKey($current)) { break }
    $current = $parentOf[$current]
}

Write-Output "NOT_FOUND"
