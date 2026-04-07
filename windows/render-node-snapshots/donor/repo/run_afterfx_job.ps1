param(
  [string]$JobDir = "C:\ae_jobs\cc45d71343084a96a4376a6f8605cc6e\app",
  [string]$AfterFXCom = "C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\AfterFX.com",
  [int]$TimeoutSec = 300,
  [string]$CompName = "Main Render",
  [string]$OutputRel = "work\output.mp4"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-DirIfMissing([string]$p) {
  if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

function Write-Info($msg) { Write-Host ("[INFO] " + $msg) }
function Write-Warn($msg) { Write-Host ("[WARN] " + $msg) -ForegroundColor Yellow }
function Write-Err ($msg) { Write-Host ("[ERR ] " + $msg) -ForegroundColor Red }

# --- prechecks
if (-not (Test-Path $JobDir)) { throw "JobDir not found: $JobDir" }
$jsxPath = Join-Path $JobDir "render.jsx"
if (-not (Test-Path $jsxPath)) { throw "render.jsx not found: $jsxPath" }

if (-not (Test-Path $AfterFXCom)) {
  throw "AfterFX.com not found at: $AfterFXCom"
}

# --- render-only flag (reduces licensing/UI noise; not a 'no window' switch)
$flagDir  = "C:\Users\Public\Documents\Adobe"
$flagPath = Join-Path $flagDir "ae_render_only_node.txt"
New-DirIfMissing $flagDir
if (-not (Test-Path $flagPath)) {
  New-Item -ItemType File -Path $flagPath | Out-Null
  Write-Info "Created render-only flag: $flagPath"
} else {
  Write-Info "Render-only flag exists: $flagPath"
}

# --- logs
$logDir = Join-Path $JobDir "logs"
New-DirIfMissing $logDir
$stdoutLog = Join-Path $logDir "afterfx_stdout.log"
$stderrLog = Join-Path $logDir "afterfx_stderr.log"

# --- env like your ae_sdk.py expects
$jobId = (Split-Path (Split-Path $JobDir -Parent) -Leaf)
$env:APP_DIR    = $JobDir
$env:JOB_ID     = $jobId
$env:COMP_NAME  = $CompName
$env:OUTPUT_REL = $OutputRel

# wipe old status
$statusPath = Join-Path $JobDir "ae_status.txt"
if (Test-Path $statusPath) { Remove-Item $statusPath -Force }

Write-Info "Using AfterFX.com: $AfterFXCom"
Write-Info "Running: `"$AfterFXCom`" -r `"$jsxPath`""
Write-Info "JOB_DIR=$JobDir"
Write-Info "JOB_ID=$jobId COMP_NAME=$CompName OUTPUT_REL=$OutputRel"
Write-Info "Logs: $stdoutLog / $stderrLog"

# --- start process (no shell, no window)
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $AfterFXCom
$psi.Arguments = "-r `"$jsxPath`""
$psi.WorkingDirectory = $JobDir
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true
$psi.CreateNoWindow = $true

$p = New-Object System.Diagnostics.Process
$p.StartInfo = $psi
[void]$p.Start()

$stdoutTask = $p.StandardOutput.ReadToEndAsync()
$stderrTask = $p.StandardError.ReadToEndAsync()

$sw = [System.Diagnostics.Stopwatch]::StartNew()

while ($true) {
  if ($p.HasExited) { break }

  # If status appears, give it a moment to flush
  if (Test-Path $statusPath) {
    Start-Sleep -Milliseconds 500
    if ($p.HasExited) { break }
  }

  if ($sw.Elapsed.TotalSeconds -ge $TimeoutSec) {
    Write-Warn "Timeout ${TimeoutSec}s reached. Killing AfterFX..."
    try { $p.Kill($true) } catch {}
    break
  }

  Start-Sleep -Milliseconds 250
}

# write logs
$stdout = $stdoutTask.Result
$stderr = $stderrTask.Result
[System.IO.File]::WriteAllText($stdoutLog, $stdout, [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText($stderrLog, $stderr, [System.Text.Encoding]::UTF8)

$exitCode = if ($p.HasExited) { $p.ExitCode } else { 999 }
Write-Info "AfterFX exit code: $exitCode"

if (-not (Test-Path $statusPath)) {
  Write-Err "ae_status.txt not found: $statusPath"
  Write-Info "stderr tail:"
  Get-Content $stderrLog -Tail 80 -ErrorAction SilentlyContinue
  exit 10
}

$statusText = Get-Content $statusPath -Raw
Write-Info "ae_status.txt:"
Write-Host $statusText

$firstLine = ($statusText -split "`r?`n")[0].Trim().ToUpperInvariant()
if ($firstLine -ne "OK") {
  Write-Err "AE script reported non-OK status: $firstLine"
  exit 20
}

Write-Info "SUCCESS: JSX produced OK status."
exit 0
