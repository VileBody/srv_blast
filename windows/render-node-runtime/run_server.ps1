param(
    [string]$DevRoot = "C:\ae_dev"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DevRoot  = [System.IO.Path]::GetFullPath($DevRoot)
$RepoDir  = Join-Path $DevRoot "repo"
$VenvDir  = Join-Path $DevRoot "venv"
$PyVenv   = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "=== RUN AE JSX NODE ==="
Write-Host "DevRoot : $DevRoot"
Write-Host "RepoDir : $RepoDir"
Write-Host "Venv    : $PyVenv"
Write-Host ""

if (-not (Test-Path $PyVenv)) {
    throw "Не найден venv python: $PyVenv"
}

if (-not (Test-Path (Join-Path $RepoDir "main.py"))) {
    throw "Не найден main.py в $RepoDir"
}

Set-Location $RepoDir

Write-Host "[*] Запускаю FastAPI-сервер..."
& $PyVenv -m uvicorn main:app --host 0.0.0.0 --port 8000
