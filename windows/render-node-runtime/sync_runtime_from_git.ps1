param(
    [string]$RepoUrl = "https://github.com/VileBody/srv_blast.git",
    [string]$Branch = "main",
    [string]$CheckoutDir = "C:\ae_dev\srv_blast",
    [string]$RuntimeSubdir = "windows/render-node-runtime",
    [string]$RuntimeLinkDir = "C:\ae_dev\repo",
    [string]$GitAuthToken = "",
    [switch]$ReplaceRuntimeLinkDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Git {
    param(
        [string]$Cwd,
        [string[]]$GitArgs
    )
    & git -C $Cwd @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git failed in '$Cwd': git $($GitArgs -join ' ')"
    }
}

function New-Junction {
    param(
        [string]$LinkPath,
        [string]$TargetPath
    )
    $cmd = "mklink /J `"$LinkPath`" `"$TargetPath`""
    cmd /c $cmd | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "failed to create junction: $cmd"
    }
}

$CheckoutDir = [System.IO.Path]::GetFullPath($CheckoutDir)
$RuntimeLinkDir = [System.IO.Path]::GetFullPath($RuntimeLinkDir)
$checkoutParent = Split-Path $CheckoutDir -Parent
if (-not (Test-Path $checkoutParent)) {
    New-Item -ItemType Directory -Path $checkoutParent -Force | Out-Null
}

Write-Host "=== Sync Windows Render Runtime From Git ==="
Write-Host "RepoUrl        : $RepoUrl"
Write-Host "Branch         : $Branch"
Write-Host "CheckoutDir    : $CheckoutDir"
Write-Host "RuntimeSubdir  : $RuntimeSubdir"
Write-Host "RuntimeLinkDir : $RuntimeLinkDir"
Write-Host ""

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is not installed or not available in PATH"
}

if (-not (Test-Path (Join-Path $CheckoutDir ".git"))) {
    New-Item -ItemType Directory -Path $CheckoutDir -Force | Out-Null
    Invoke-Git -Cwd $CheckoutDir -GitArgs @("init")
    Invoke-Git -Cwd $CheckoutDir -GitArgs @("remote", "add", "origin", $RepoUrl)
}

Invoke-Git -Cwd $CheckoutDir -GitArgs @("remote", "set-url", "origin", $RepoUrl)

$authRepoUrl = $RepoUrl
$tokenProvided = -not [string]::IsNullOrWhiteSpace($GitAuthToken)
if ($tokenProvided -and $RepoUrl -match "^https://github\.com/") {
    $authRepoUrl = $RepoUrl -replace "^https://github\.com/", ("https://x-access-token:{0}@github.com/" -f $GitAuthToken)
}

if ($tokenProvided) {
    Invoke-Git -Cwd $CheckoutDir -GitArgs @("remote", "set-url", "origin", $authRepoUrl)
}

try {
    Invoke-Git -Cwd $CheckoutDir -GitArgs @("fetch", "--prune", "origin", $Branch)
    $hasLocalBranch = $false
    try {
        Invoke-Git -Cwd $CheckoutDir -GitArgs @("rev-parse", "--verify", "refs/heads/$Branch")
        $hasLocalBranch = $true
    }
    catch {
        $hasLocalBranch = $false
    }

    if ($hasLocalBranch) {
        Invoke-Git -Cwd $CheckoutDir -GitArgs @("checkout", "-f", $Branch)
    }
    else {
        Invoke-Git -Cwd $CheckoutDir -GitArgs @("checkout", "-B", $Branch, "origin/$Branch")
    }

    Invoke-Git -Cwd $CheckoutDir -GitArgs @("sparse-checkout", "init", "--cone")
    Invoke-Git -Cwd $CheckoutDir -GitArgs @("sparse-checkout", "set", $RuntimeSubdir)
    Invoke-Git -Cwd $CheckoutDir -GitArgs @("reset", "--hard", "origin/$Branch")
}
finally {
    if ($tokenProvided) {
        Invoke-Git -Cwd $CheckoutDir -GitArgs @("remote", "set-url", "origin", $RepoUrl)
    }
}

$runtimeSourceDir = Join-Path $CheckoutDir ($RuntimeSubdir -replace "/", "\")
if (-not (Test-Path (Join-Path $runtimeSourceDir "main.py"))) {
    throw "runtime source directory does not contain main.py: $runtimeSourceDir"
}

if (Test-Path $RuntimeLinkDir) {
    if (-not $ReplaceRuntimeLinkDir) {
        throw "RuntimeLinkDir already exists: $RuntimeLinkDir. Re-run with -ReplaceRuntimeLinkDir."
    }
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backup = "${RuntimeLinkDir}.backup_${stamp}"
    Move-Item -LiteralPath $RuntimeLinkDir -Destination $backup -Force
    Write-Host "[ok] moved old RuntimeLinkDir to $backup"
}

$linkParent = Split-Path $RuntimeLinkDir -Parent
if (-not (Test-Path $linkParent)) {
    New-Item -ItemType Directory -Path $linkParent -Force | Out-Null
}

New-Junction -LinkPath $RuntimeLinkDir -TargetPath $runtimeSourceDir

$sha = (& git -C $CheckoutDir rev-parse --short HEAD).Trim()
Write-Host "[ok] runtime synced and linked"
Write-Host "[ok] commit=$sha branch=$Branch"
Write-Host "[ok] RuntimeLinkDir -> $runtimeSourceDir"
