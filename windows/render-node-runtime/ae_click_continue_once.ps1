param(
  [int]$TimeoutSeconds = 90,
  [string]$LogPath = 'C:\ae_dev\logs\ae_modal_click_once.log'
)
$ErrorActionPreference = 'SilentlyContinue'
function Write-Log([string]$m) {
  $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  "$ts $m" | Out-File -FilePath $LogPath -Append -Encoding utf8
}
$wshell = New-Object -ComObject WScript.Shell
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
Write-Log 'clicker_start'
while ((Get-Date) -lt $deadline) {
  $p = Get-Process | Where-Object { $_.ProcessName -in @('AfterFX.com','AfterFX','AfterFX.exe','aerender','aerender.exe') } | Select-Object -First 1
  if ($p) {
    if ($wshell.AppActivate($p.Id)) {
      Start-Sleep -Milliseconds 300
      $wshell.SendKeys('{ENTER}')
      Write-Log ("enter_by_pid pid={0}" -f $p.Id)
      Start-Sleep -Milliseconds 700
    } else {
      Write-Log ("activate_failed pid={0}" -f $p.Id)
    }
  }
  if ($wshell.AppActivate('Crash Repair Options')) {
    Start-Sleep -Milliseconds 300
    $wshell.SendKeys('{ENTER}')
    Write-Log 'enter_by_title Crash Repair Options'
    Start-Sleep -Milliseconds 700
  }
  Start-Sleep -Seconds 1
}
Write-Log 'clicker_done'
