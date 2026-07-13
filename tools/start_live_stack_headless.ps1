[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PythonExe = if ($env:CLAUDETRADE_PYTHON) {
    $env:CLAUDETRADE_PYTHON
} else {
    "C:\Users\Unknown\anaconda3\envs\upbit\python.exe"
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "ClaudeTrade Python executable not found: $PythonExe"
}

# Concurrent NumPy imports have caused intermittent access violations on this host.
# Keep each long-running worker single-threaded at the native math layer.
$env:OPENBLAS_NUM_THREADS = "1"
$env:OMP_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"

$logDir = Join-Path $Root "logs\runtime"
if (-not $DryRun) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$roles = @(
    @{ Name = "trading_bot"; PidFile = "state\live_trading_bot.pid"; Args = @("trading_bot.py", "--live") },
    @{ Name = "dashboard"; PidFile = "state\dashboard_server.pid"; Args = @("dashboard\dashboard_server.py") },
    @{ Name = "live_guardian"; PidFile = "state\live_guardian_heartbeat.json"; Args = @("tools\live_guardian.py", "--mode", "live", "--watch", "--ensure-bot", "--interval-sec", "300", "--telegram-alert") },
    @{ Name = "broker_truth_scheduler"; PidFile = "state\broker_truth_scheduler.lock.json"; Args = @("tools\broker_truth_scheduler.py", "--mode", "live", "--markets", "KR,US", "--loop", "--interval-sec", "30", "--refresh-interval-min", "2", "--failure-retry-min", "2", "--preopen-min", "20", "--postclose-min", "15", "--ttl-sec", "180", "--no-refresh-on-start") },
    @{ Name = "preopen_scheduler"; PidFile = "state\preopen_scheduler.lock.json"; Args = @("tools\preopen_scheduler.py", "--mode", "live", "--markets", "KR,US", "--loop", "--interval-sec", "60") },
    @{ Name = "counterfactual_pipeline"; PidFile = ""; Args = @("tools\run_counterfactual_pipeline.py", "--phase", "due", "--market", "KR,US", "--loop", "--interval-sec", "300", "--json") },
    @{ Name = "integrity_check"; PidFile = ""; Args = @("tools\integrity_check.py", "--watch", "--interval-sec", "600", "--telegram-alert") }
)

$manifestPath = Join-Path $Root "state\headless_live_stack_pids.json"
$manifest = @{}
if (Test-Path -LiteralPath $manifestPath) {
    try {
        $saved = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        foreach ($property in $saved.PSObject.Properties) {
            $manifest[$property.Name] = [int]$property.Value
        }
    } catch {
        Write-Warning "Ignoring invalid PID manifest: $($_.Exception.Message)"
    }
}

function Read-PidFromJson([string]$RelativePath) {
    if (-not $RelativePath) { return 0 }
    $path = Join-Path $Root $RelativePath
    if (-not (Test-Path -LiteralPath $path)) { return 0 }
    try {
        $payload = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        return [int]$payload.pid
    } catch {
        return 0
    }
}

function Test-ClaudeTradePythonPid([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        return $process.Path -and ((Resolve-Path -LiteralPath $process.Path).Path -ieq (Resolve-Path -LiteralPath $PythonExe).Path)
    } catch {
        return $false
    }
}

foreach ($role in $roles) {
    $candidatePids = @()
    if ($manifest.ContainsKey($role.Name)) {
        $candidatePids += [int]$manifest[$role.Name]
    }
    $statePid = Read-PidFromJson $role.PidFile
    if ($statePid -gt 0) {
        $candidatePids += $statePid
    }
    $runningPid = @($candidatePids | Select-Object -Unique | Where-Object { Test-ClaudeTradePythonPid $_ } | Select-Object -First 1)
    if ($runningPid.Count -gt 0) {
        $manifest[$role.Name] = [int]$runningPid[0]
        Write-Output "[OK] $($role.Name) already running pid=$($runningPid[0])"
        continue
    }

    if ($DryRun) {
        if ($role.Name -eq "trading_bot") {
            Write-Output "[DRY-RUN] guardian would preflight and ensure trading_bot"
        } else {
            Write-Output "[DRY-RUN] would start $($role.Name): $PythonExe $($role.Args -join ' ')"
        }
        continue
    }

    if ($role.Name -eq "trading_bot") {
        # The guardian owns bot creation. It writes market-scoped gates before
        # launching, so a failed KR preflight cannot silently start KR entries
        # and cannot unnecessarily suppress a healthy US market.
        & $PythonExe "tools\live_guardian.py" "--mode" "live" "--ensure-bot" "--skip-dashboard"
        $statePid = 0
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            $statePid = Read-PidFromJson $role.PidFile
            if ($statePid -gt 0 -and (Test-ClaudeTradePythonPid $statePid)) { break }
            Start-Sleep -Milliseconds 500
        }
        if ($statePid -gt 0 -and (Test-ClaudeTradePythonPid $statePid)) {
            $manifest[$role.Name] = [int]$statePid
            Write-Output "[START] trading_bot started by guardian pid=$statePid"
            continue
        }
        throw "Guardian did not start trading_bot; inspect the latest live_guardian report."
    }

    $process = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList $role.Args `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "$($role.Name).out.log") `
        -RedirectStandardError (Join-Path $logDir "$($role.Name).err.log") `
        -PassThru
    $manifest[$role.Name] = [int]$process.Id
    Write-Output "[START] $($role.Name) pid=$($process.Id)"
}

if (-not $DryRun) {
    $manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8
}
