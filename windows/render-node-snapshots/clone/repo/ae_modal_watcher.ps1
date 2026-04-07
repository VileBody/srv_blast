param(
  [string[]]$TargetProcesses = @(
    "AfterFX",
    "aerender"
  ),
  [string[]]$AutoDismissTitles = @(
    "Crash Repair Options"
  ),
  [int]$PollSeconds = 2,
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
  $list = New-Object System.Collections.Generic.List[object]
  $cb = [WinApi+EnumWindowsProc]{
    param([IntPtr]$hWnd, [IntPtr]$lParam)

    if (-not [WinApi]::IsWindowVisible($hWnd)) {
      return $true
    }

    $len = [WinApi]::GetWindowTextLength($hWnd)
    if ($len -le 0) {
      return $true
    }

    $sb = New-Object System.Text.StringBuilder ($len + 1)
    [void][WinApi]::GetWindowText($hWnd, $sb, $sb.Capacity)
    $title = $sb.ToString().Trim()
    if ([string]::IsNullOrWhiteSpace($title)) {
      return $true
    }

    [uint32]$pid = 0
    [void][WinApi]::GetWindowThreadProcessId($hWnd, [ref]$pid)
    if ($pid -eq 0) {
      return $true
    }

    $procName = ""
    try {
      $procName = (Get-Process -Id $pid -ErrorAction Stop).Name
    } catch {
      return $true
    }

    $list.Add([PSCustomObject]@{
      Handle = $hWnd
      Pid = [int]$pid
      ProcessName = $procName
      Title = $title
    }) | Out-Null

    return $true
  }

  [void][WinApi]::EnumWindows($cb, [IntPtr]::Zero)
  return $list
}

function Get-UiSnapshot([IntPtr]$hWnd) {
  try {
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($hWnd)
    if (-not $root) {
      return ""
    }

    $all = $root.FindAll(
      [System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Condition]::TrueCondition
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

# Normalize target process names so "AfterFX" also matches "AfterFX.com"/"AfterFX.exe".
$targetRegexes = $TargetProcesses | ForEach-Object {
  $base = $_.ToLowerInvariant()
  "^$([regex]::Escape($base))(\.exe|\.com)?$"
}
$wshell = New-Object -ComObject WScript.Shell
$seen = @{}
Write-Log "watcher_start poll=$PollSeconds target_processes=$($TargetProcesses -join '|') dismiss_titles=$($AutoDismissTitles -join '|')"

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

    $key = "$($w.Pid)|$($w.Title)"
    $present[$key] = 1

    try {
      if (-not $seen.ContainsKey($key)) {
        $seen[$key] = 1
        $ui = Get-UiSnapshot -hWnd $w.Handle
        Write-Log "window_detected pid=$($w.Pid) proc=$($w.ProcessName) title=[$($w.Title)] $ui"
      }

      foreach ($pattern in $AutoDismissTitles) {
        if ($w.Title -like "*$pattern*") {
          $activated = $false
          if ($wshell.AppActivate($w.Pid)) {
            $activated = $true
          } elseif ($wshell.AppActivate($w.Title)) {
            $activated = $true
          }

          if ($activated) {
            Start-Sleep -Milliseconds 300
            $wshell.SendKeys("{ENTER}")
            Write-Log "window_action pid=$($w.Pid) title=[$($w.Title)] action=ENTER pattern=[$pattern]"
            Start-Sleep -Milliseconds 700
          } else {
            Write-Log "window_action_failed pid=$($w.Pid) title=[$($w.Title)] action=ENTER pattern=[$pattern] reason=AppActivateFailed"
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
