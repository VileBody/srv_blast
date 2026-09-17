$ErrorActionPreference = "Continue"

$LogFile = "C:\ae_dev\logs\render_server_watchdog.log"
$HealthUrl = "http://127.0.0.1:8000/health"
$TaskName = "BlastRenderNodeServer"
$ListenPort = 8000
$CheckSec = 20
$CooldownSec = 60
$ListenerGraceSec = 900

function Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8 -ErrorAction SilentlyContinue
}

function Get-RenderServerListener {
    return Get-NetTCPConnection `
        -LocalPort $ListenPort `
        -State Listen `
        -ErrorAction SilentlyContinue `
        | Select-Object -First 1
}

function Stop-RenderServerTree($listener) {
    if (-not $listener) {
        return
    }

    $pidToStop = [int]$listener.OwningProcess
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidToStop"
        if ($process -and [int]$process.ParentProcessId -gt 0) {
            $pidToStop = [int]$process.ParentProcessId
        }
    } catch {
        Log "failed to resolve render server parent process: $($_.Exception.Message)"
    }

    Log "stopping unresponsive render server process tree pid=$pidToStop"
    & taskkill.exe /PID $pidToStop /T /F | Out-Null
}

$duplicate = Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and ($_.CommandLine -match "render_server_watchdog\.ps1")
}
if ($duplicate) {
    Log "Duplicate render server watchdog detected (PIDs: $($duplicate.ProcessId -join ',')), exiting."
    exit 0
}

Log "=== RENDER SERVER WATCHDOG START ==="
$lastStart = Get-Date "2000-01-01"
$firstHealthFailure = $null
$failCount = 0

while ($true) {
    $ok = $false
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 5
        if ([int]$response.StatusCode -eq 200 -and [string]$response.Content -match '"status"\s*:\s*"ok"') {
            $ok = $true
        }
    } catch {
        $ok = $false
    }

    if ($ok) {
        if ($failCount -gt 0) {
            Log "health restored"
        }
        $failCount = 0
        $firstHealthFailure = $null
        Start-Sleep -Seconds $CheckSec
        continue
    }

    $failCount += 1
    if (-not $firstHealthFailure) {
        $firstHealthFailure = Get-Date
    }

    $listener = Get-RenderServerListener
    $failureAgeSec = ((Get-Date) - $firstHealthFailure).TotalSeconds
    if ($listener -and $failureAgeSec -lt $ListenerGraceSec) {
        Log "health failed count=$failCount but listener pid=$($listener.OwningProcess) is alive; restart suppressed age_s=$([int]$failureAgeSec)"
        Start-Sleep -Seconds $CheckSec
        continue
    }

    Log "health failed count=$failCount url=$HealthUrl listener=$([bool]$listener) age_s=$([int]$failureAgeSec)"
    $startAgeSec = ((Get-Date) - $lastStart).TotalSeconds
    if ($failCount -ge 2 -and $startAgeSec -ge $CooldownSec) {
        if ($listener) {
            Stop-RenderServerTree $listener
            Start-Sleep -Seconds 3
        }

        Log "starting $TaskName"
        try {
            Start-ScheduledTask -TaskName $TaskName
            $lastStart = Get-Date
            $firstHealthFailure = Get-Date
            $failCount = 0
            Start-Sleep -Seconds 10
        } catch {
            Log "start failed: $($_.Exception.Message)"
        }
    }

    Start-Sleep -Seconds $CheckSec
}
