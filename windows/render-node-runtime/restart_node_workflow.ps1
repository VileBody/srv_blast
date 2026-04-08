param(
    [string]$DevRoot = "C:\ae_dev",
    [string]$NodeUrl = "http://127.0.0.1:8000",
    [string]$StartAfterFX = "true",
    [string]$KillAfterFXFirst = "true",
    [int]$HealthTimeoutSec = 180,
    [int]$HealthPollSec = 2,
    [string]$LogPath = "C:\ae_dev\logs\node_restart_workflow.log"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Log([string]$Message) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $Message" | Out-File -FilePath $LogPath -Append -Encoding utf8
}

function Emit-Step(
    [string]$Step,
    [string]$Status,
    [string]$Message = ""
) {
    $line = "WF_STEP=$Step STATUS=$Status"
    if ($Message) {
        $line = "$line MSG=$Message"
    }
    Write-Output $line
    Write-Log $line
}

function Resolve-AfterFXExe {
    $candidates = @(
        "C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\AfterFX.exe",
        "C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.exe",
        "C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe",
        "C:\Program Files\Adobe\Adobe After Effects 2023\Support Files\AfterFX.exe",
        "C:\Program Files\Adobe\Adobe After Effects 2022\Support Files\AfterFX.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) {
            return $path
        }
    }
    return ""
}

function Parse-Bool(
    [string]$Raw,
    [bool]$DefaultValue
) {
    $s = ""
    if ($null -ne $Raw) {
        $s = [string]$Raw
    }
    $s = $s.Trim().ToLowerInvariant()
    $s = $s.TrimStart('$')
    if (-not $s) {
        return $DefaultValue
    }
    if ($s -in @("1", "true", "yes", "on")) {
        return $true
    }
    if ($s -in @("0", "false", "no", "off")) {
        return $false
    }
    return $DefaultValue
}

function Register-InteractiveTask(
    [string]$TaskName,
    [string]$Execute,
    [string]$Arguments
) {
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
    } catch {
    }
    $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2))
    $principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType Interactive -RunLevel Highest
    $argsTrimmed = ""
    if ($null -ne $Arguments) {
        $argsTrimmed = $Arguments.Trim()
    }
    if ($argsTrimmed) {
        $action = New-ScheduledTaskAction -Execute $Execute -Argument $argsTrimmed
    } else {
        $action = New-ScheduledTaskAction -Execute $Execute
    }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
}

$DevRoot = [System.IO.Path]::GetFullPath($DevRoot)
$RepoDir = Join-Path $DevRoot "repo"
$LogsDir = Join-Path $DevRoot "logs"
$RunServerPath = Join-Path $RepoDir "run_server.ps1"
$ModalWatcherPath = Join-Path $RepoDir "ae_modal_watcher.ps1"
$ClickOncePath = Join-Path $RepoDir "ae_click_continue_once.ps1"
$DontSendPath = Join-Path $RepoDir "ae_dont_send_once.ps1"
$RunServerConsoleLog = Join-Path $LogsDir "run_server.console.log"

New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
New-Item -ItemType File -Path $LogPath -Force | Out-Null

$startAfterFxFlag = Parse-Bool -Raw $StartAfterFX -DefaultValue $true
$killAfterFxFirstFlag = Parse-Bool -Raw $KillAfterFXFirst -DefaultValue $true

Emit-Step -Step "bootstrap" -Status "started" -Message "dev_root=$DevRoot node_url=$NodeUrl"

if (-not (Test-Path $RunServerPath)) {
    Emit-Step -Step "bootstrap" -Status "failed" -Message "missing_file=$RunServerPath"
    throw "run_server.ps1 not found: $RunServerPath"
}
if (-not (Test-Path $ModalWatcherPath)) {
    Emit-Step -Step "bootstrap" -Status "failed" -Message "missing_file=$ModalWatcherPath"
    throw "ae_modal_watcher.ps1 not found: $ModalWatcherPath"
}
if (-not (Test-Path $ClickOncePath)) {
    Emit-Step -Step "bootstrap" -Status "failed" -Message "missing_file=$ClickOncePath"
    throw "ae_click_continue_once.ps1 not found: $ClickOncePath"
}
if (-not (Test-Path $DontSendPath)) {
    Emit-Step -Step "bootstrap" -Status "failed" -Message "missing_file=$DontSendPath"
    throw "ae_dont_send_once.ps1 not found: $DontSendPath"
}

$nodeUri = [System.Uri]::new($NodeUrl)
$port = if ($nodeUri.Port -gt 0) { [int]$nodeUri.Port } else { 8000 }
$healthUrl = "$($nodeUri.Scheme)://$($nodeUri.Host):$port/health"

$listenerPids = @()
try {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $connections) {
        $pid = [int]$conn.OwningProcess
        if ($pid -le 0) {
            continue
        }
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        $listenerPids += $pid
    }
} catch {
}
Emit-Step -Step "port_cleanup" -Status "ok" -Message "port=$port killed_pids=$($listenerPids -join ',')"

if ($killAfterFxFirstFlag) {
    foreach ($name in @("AfterFX.exe", "AfterFX.com", "aerender.exe", "aerender", "CEPHtmlEngine.exe", "dynamiclinkmanager.exe")) {
        cmd /c "taskkill /F /IM $name" | Out-Null
    }
    Emit-Step -Step "afterfx_cleanup" -Status "ok" -Message "kill_afterfx_first=true"
} else {
    Emit-Step -Step "afterfx_cleanup" -Status "skipped" -Message "kill_afterfx_first=false"
}

$watcherArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$ModalWatcherPath`" -PollSeconds 2 -LogPath `"$LogsDir\ae_modal_watcher.log`""
$clickArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$ClickOncePath`" -TimeoutSeconds 240 -LogPath `"$LogsDir\ae_modal_click_once.log`""
$dontSendArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$DontSendPath`" -TimeoutSeconds 240 -LogPath `"$LogsDir\ae_dont_send_once.log`""
$runServerArgs = "-NoProfile -ExecutionPolicy Bypass -Command `"& '$RunServerPath' *>> '$RunServerConsoleLog'`""

Register-InteractiveTask -TaskName "BlastModalWatcher" -Execute "powershell.exe" -Arguments $watcherArgs
Register-InteractiveTask -TaskName "BlastClickContinueOnce" -Execute "powershell.exe" -Arguments $clickArgs
Register-InteractiveTask -TaskName "BlastDontSendOnce" -Execute "powershell.exe" -Arguments $dontSendArgs
Register-InteractiveTask -TaskName "BlastRunServer" -Execute "powershell.exe" -Arguments $runServerArgs
Emit-Step -Step "tasks_start" -Status "ok" -Message "started=BlastModalWatcher,BlastClickContinueOnce,BlastDontSendOnce,BlastRunServer"

if ($startAfterFxFlag) {
    $afterfxExe = Resolve-AfterFXExe
    if (-not $afterfxExe) {
        Emit-Step -Step "afterfx_start" -Status "failed" -Message "afterfx_exe_not_found"
        throw "AfterFX.exe not found in known install paths"
    }
    Register-InteractiveTask -TaskName "BlastStartAfterFX" -Execute $afterfxExe -Arguments ""
    Emit-Step -Step "afterfx_start" -Status "ok" -Message "exe=$afterfxExe"
} else {
    Emit-Step -Step "afterfx_start" -Status "skipped" -Message "start_afterfx=false"
}

$deadline = (Get-Date).AddSeconds([Math]::Max(5, $HealthTimeoutSec))
$healthOk = $false
$lastError = ""
while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 5 -UseBasicParsing
        if ([int]$resp.StatusCode -eq 200) {
            $healthOk = $true
            break
        }
        $lastError = "unexpected_status=$($resp.StatusCode)"
    } catch {
        $lastError = $_.Exception.Message
    }
    Start-Sleep -Seconds ([Math]::Max(1, $HealthPollSec))
}

if (-not $healthOk) {
    Emit-Step -Step "health_check" -Status "failed" -Message "health_url=$healthUrl last_error=$lastError"
    throw "health_check_failed url=$healthUrl err=$lastError"
}
Emit-Step -Step "health_check" -Status "ok" -Message "health_url=$healthUrl"

$activeListeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
if ($activeListeners.Count -ne 1) {
    Emit-Step -Step "port_verify" -Status "failed" -Message "expected=1 actual=$($activeListeners.Count) pids=$($activeListeners -join ',')"
    throw "unexpected_listener_count port=$port count=$($activeListeners.Count)"
}
Emit-Step -Step "port_verify" -Status "ok" -Message "port=$port pid=$($activeListeners[0])"

$procSnapshot = Get-Process | Where-Object { $_.ProcessName -match "AfterFX|aerender|python|powershell" } |
    Select-Object Id, ProcessName, SessionId |
    Sort-Object ProcessName, Id
$procLine = ($procSnapshot | ForEach-Object { "$($_.ProcessName):$($_.Id):s$($_.SessionId)" }) -join ";"
Emit-Step -Step "process_snapshot" -Status "ok" -Message $procLine

Emit-Step -Step "workflow" -Status "completed" -Message "node_url=$NodeUrl"
