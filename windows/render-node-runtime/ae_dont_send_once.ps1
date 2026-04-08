param(
  [int]$TimeoutSeconds = 90,
  [string]$LogPath = 'C:\ae_dev\logs\ae_dont_send_once.log'
)
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
function Write-Log([string]$m) {
  $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  "$ts $m" | Out-File -FilePath $LogPath -Append -Encoding utf8
}
function Try-InvokeButton([System.Windows.Automation.AutomationElement]$root, [string[]]$names) {
  foreach ($n in $names) {
    $condName = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $n)
    $condBtn = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
    $cond = New-Object System.Windows.Automation.AndCondition($condName, $condBtn)
    $btn = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
    if ($btn) {
      $p = $null
      if ($btn.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) {
        $p.Invoke()
        Write-Log ("clicked_button name=[{0}]" -f $n)
        return $true
      }
    }
  }
  return $false
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
Write-Log 'dont_send_start'
while ((Get-Date) -lt $deadline) {
  $desktop = [System.Windows.Automation.AutomationElement]::RootElement
  if ($null -eq $desktop) { Start-Sleep -Seconds 1; continue }

  $clicked = $false
  # crash report dialog button names can vary by apostrophe glyph and casing
  $clicked = Try-InvokeButton -root $desktop -names @("Don't send", "Don’t send", "Dont send")
  if ($clicked) { break }

  Start-Sleep -Seconds 1
}
Write-Log 'dont_send_done'
