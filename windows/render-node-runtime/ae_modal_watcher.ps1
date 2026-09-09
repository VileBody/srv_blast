param(
  [string[]]$TargetProcesses = @(
    "AfterFX",
    "aerender"
  ),
  [string[]]$AutoDismissTitles = @(
    "Crash Repair Options"
  ),
  [int]$PollSeconds = 2,
  [string[]]$MainWindowTitlePatterns = @(
    "^Adobe After Effects \d"
  ),
  [string]$LogPath = "C:\ae_dev\logs\ae_modal_watcher.log"
)

$ErrorActionPreference = "SilentlyContinue"

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$native = @"
using System;
using System.Text;
using System.Runtime.InteropServices;

public static class WinApi {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

  [DllImport("user32.dll")]
  public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

  [DllImport("user32.dll")]
  public static extern bool IsWindowVisible(IntPtr hWnd);

  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

  [DllImport("user32.dll")]
  public static extern int GetWindowTextLength(IntPtr hWnd);

  [DllImport("user32.dll")]
  public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
"@
Add-Type -TypeDefinition $native -Language CSharp

$logDir = Split-Path -Parent $LogPath
if ($logDir) {
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

function Write-Log([string]$msg) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  "$ts $msg" | Out-File -FilePath $LogPath -Append -Encoding utf8
}

function Get-TopWindows {
  # IMPORTANT: the EnumWindows callback runs on an unmanaged call-in thread.
  # Invoking PowerShell cmdlets (e.g. Get-Process) from inside it is a known
  # source of pipeline reentrancy hangs — this collects only raw
  # handle/pid/title via native calls here, and resolves process names in a
  # single batched Get-Process call afterwards, outside the callback.
  $raw = New-Object System.Collections.Generic.List[object]
  $cb = [WinApi+EnumWindowsProc]{
    param([IntPtr]$hWnd, [IntPtr]$lParam)

    if (-not [WinApi]::IsWindowVisible($hWnd)) {
      return $true
    }

    $len = [WinApi]::GetWindowTextLength($hWnd)
    $title = ""
    if ($len -gt 0) {
      $sb = New-Object System.Text.StringBuilder ($len + 1)
      [void][WinApi]::GetWindowText($hWnd, $sb, $sb.Capacity)
      $title = $sb.ToString().Trim()
    }
    # Title-less windows are kept on purpose: AE's blocking dialogs (Crash
    # Repair Options, "one chance to save your project") have no title, which
    # is exactly why this watcher never saw them before.

    [uint32]$procId = 0
    [void][WinApi]::GetWindowThreadProcessId($hWnd, [ref]$procId)
    if ($procId -eq 0) {
      return $true
    }

    $raw.Add([PSCustomObject]@{
      Handle = $hWnd
      Pid = [int]$procId
      Title = $title
    }) | Out-Null

    return $true
  }

  [void][WinApi]::EnumWindows($cb, [IntPtr]::Zero)

  if ($raw.Count -eq 0) {
    return (New-Object System.Collections.Generic.List[object])
  }

  $pidNames = @{}
  $uniquePids = $raw | Select-Object -ExpandProperty Pid -Unique
  foreach ($proc in (Get-Process -Id $uniquePids -ErrorAction SilentlyContinue)) {
    $pidNames[$proc.Id] = $proc.Name
  }

  $list = New-Object System.Collections.Generic.List[object]
  foreach ($r in $raw) {
    if (-not $pidNames.ContainsKey($r.Pid)) {
      continue
    }
    $list.Add([PSCustomObject]@{
      Handle = $r.Handle
      Pid = $r.Pid
      ProcessName = $pidNames[$r.Pid]
      Title = $r.Title
    }) | Out-Null
  }
  return $list
}

function Get-UiSnapshot([IntPtr]$hWnd) {
  try {
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($hWnd)
    if (-not $root) {
      return ""
    }

    # ControlViewCondition (vs. raw TrueCondition) skips non-interactive
    # elements, which matters a lot on a window this deep.
    $all = $root.FindAll(
      [System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Automation]::ControlViewCondition
    )

    $texts = New-Object System.Collections.Generic.List[string]
    $buttons = New-Object System.Collections.Generic.List[string]

    foreach ($el in $all) {
      $name = [string]$el.Current.Name
      if ([string]::IsNullOrWhiteSpace($name)) {
        continue
      }
      $name = $name.Trim()
      $ctype = [string]$el.Current.ControlType.ProgrammaticName

      if ($ctype -eq "ControlType.Text") {
        if (-not $texts.Contains($name)) { $texts.Add($name) | Out-Null }
      } elseif ($ctype -eq "ControlType.Button") {
        if (-not $buttons.Contains($name)) { $buttons.Add($name) | Out-Null }
      }
    }

    $txt = ($texts | Select-Object -First 12) -join " || "
    $btn = ($buttons | Select-Object -First 8) -join " | "
    return "ui_text=[$txt] ui_buttons=[$btn]"
  } catch {
    return "ui_read_err=$($_.Exception.Message)"
  }
}

# Real UI Automation click: find a Button control by name (first match wins,
# in the given priority order) scoped to $root, and invoke it directly.
# This does NOT depend on window focus/foreground, unlike AppActivate+SendKeys,
# which is what made the old dismiss logic unreliable (focus-steal prevention,
# or ENTER landing on a button that wasn't actually the default one).
function Invoke-ButtonByName([System.Windows.Automation.AutomationElement]$root, [string[]]$names) {
  foreach ($n in $names) {
    $condName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $n)
    $condBtn = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
    $cond = New-Object System.Windows.Automation.AndCondition($condName, $condBtn)
    $btn = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
    if (-not $btn) {
      continue
    }

    $invokePattern = $null
    if ($btn.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$invokePattern)) {
      try {
        $invokePattern.Invoke()
        return $n
      } catch {
        Write-Log "invoke_error name=[$n] err=$($_.Exception.Message)"
      }
    }

    # Some custom-drawn Adobe dialog controls only expose LegacyIAccessible.
    $legacyPattern = $null
    if ($btn.TryGetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern, [ref]$legacyPattern)) {
      try {
        $legacyPattern.DoDefaultAction()
        return $n
      } catch {
        Write-Log "legacy_invoke_error name=[$n] err=$($_.Exception.Message)"
      }
    }
  }
  return $null
}

# Per-dialog-title button candidates, tried in priority order. Add more
# titles/buttons here as new blocking dialogs get observed in the log.
$ButtonCandidatesByTitle = @{
  "Crash Repair Options" = @("Continue", "OK", "Repair", "Close")
}

# Normalize target process names so "AfterFX" also matches "AfterFX.com"/"AfterFX.exe".
$targetRegexes = $TargetProcesses | ForEach-Object {
  $base = $_.ToLowerInvariant()
  "^$([regex]::Escape($base))(\.exe|\.com)?$"
}
$seen = @{}
Write-Log "watcher_start poll=$PollSeconds target_processes=$($TargetProcesses -join '|') dismiss_titles=$($AutoDismissTitles -join '|') mode=uia_invoke"

while ($true) {
  $windows = Get-TopWindows
  $present = @{}

  foreach ($w in $windows) {
    $procLower = $w.ProcessName.ToLowerInvariant()
    $isTarget = $false
    foreach ($rx in $targetRegexes) {
      if ($procLower -match $rx) {
        $isTarget = $true
        break
      }
    }
    if (-not $isTarget) {
      continue
    }
    $key = "$($w.Pid)|$($w.Handle)|$($w.Title)"
    $present[$key] = 1

    if ([string]::IsNullOrWhiteSpace($w.Title)) {
      if (-not $seen.ContainsKey($key)) {
        $seen[$key] = 1
        Write-Log "untitled_dialog_detected pid=$($w.Pid) proc=$($w.ProcessName) handle=$($w.Handle)"
      }
      try {
        $shell = New-Object -ComObject WScript.Shell
        if ($shell.AppActivate([int]$w.Pid)) {
          Start-Sleep -Milliseconds 200
          $shell.SendKeys("{ENTER}")
          Write-Log "untitled_dialog_action pid=$($w.Pid) action=sendkeys_enter"
          Start-Sleep -Milliseconds 500
        } else {
          Write-Log "untitled_dialog_action_failed pid=$($w.Pid) reason=AppActivateFailed"
        }
      } catch {
        Write-Log "untitled_dialog_error pid=$($w.Pid) err=$($_.Exception.Message)"
      }
      continue
    }

    $isMainWindow = $false
    foreach ($mp in $MainWindowTitlePatterns) {
      if ($w.Title -match $mp) {
        $isMainWindow = $true
        break
      }
    }

    try {
      $ui = ""
      if (-not $isMainWindow) {
        $ui = Get-UiSnapshot -hWnd $w.Handle
      }

      if (-not $seen.ContainsKey($key)) {
        $seen[$key] = 1
        if ($isMainWindow) {
          # The main app window's control tree is huge (docked panels,
          # timeline, viewer, etc.) — a full descendants walk on it can
          # take minutes. It's never the crash/repair dialog anyway, so
          # skip the snapshot and just log that we saw it.
          Write-Log "window_detected pid=$($w.Pid) proc=$($w.ProcessName) title=[$($w.Title)] main_window_snapshot_skipped=1"
        } else {
          Write-Log "window_detected pid=$($w.Pid) proc=$($w.ProcessName) title=[$($w.Title)] $ui"
        }
      }

      if ($ui -match "(?i)internal structure inconsistency|before quitting you have one chance to save your project") {
        $root = $null
        try { $root = [System.Windows.Automation.AutomationElement]::FromHandle($w.Handle) } catch {}
        if ($root) {
          $clicked = Invoke-ButtonByName -root $root -names @("OK")
          if ($clicked) {
            Write-Log "window_action pid=$($w.Pid) title=[$($w.Title)] action=uia_invoke button=[$clicked] pattern=[fatal_afterfx_dialog]"
            Start-Sleep -Milliseconds 500
          }
        }
      }

      foreach ($pattern in $AutoDismissTitles) {
        if ($w.Title -like "*$pattern*") {
          $candidates = $ButtonCandidatesByTitle[$pattern]
          if (-not $candidates) { $candidates = @("Continue", "OK") }

          $root = $null
          try { $root = [System.Windows.Automation.AutomationElement]::FromHandle($w.Handle) } catch {}

          if ($root) {
            $clicked = Invoke-ButtonByName -root $root -names $candidates
            if ($clicked) {
              Write-Log "window_action pid=$($w.Pid) title=[$($w.Title)] action=uia_invoke button=[$clicked] pattern=[$pattern]"
              Start-Sleep -Milliseconds 500
            } else {
              Write-Log "window_action_failed pid=$($w.Pid) title=[$($w.Title)] action=uia_invoke pattern=[$pattern] reason=no_matching_button candidates=[$($candidates -join ',')]"
            }
          } else {
            Write-Log "window_action_failed pid=$($w.Pid) title=[$($w.Title)] action=uia_invoke pattern=[$pattern] reason=FromHandleFailed"
          }
        }
      }
    } catch {
      Write-Log "window_watch_error pid=$($w.Pid) proc=$($w.ProcessName) title=[$($w.Title)] err=$($_.Exception.Message)"
    }
  }

  foreach ($k in @($seen.Keys)) {
    if (-not $present.ContainsKey($k)) {
      $null = $seen.Remove($k)
    }
  }

  Start-Sleep -Seconds $PollSeconds
}
