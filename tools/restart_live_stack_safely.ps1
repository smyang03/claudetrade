[CmdletBinding()]
param(
    [string]$Label = "operator_restart",
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

function Invoke-PythonChecked {
    param([string[]]$Arguments)
    & $PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

function Get-BrokerInventory {
    param([string]$SnapshotPath)
    if (-not (Test-Path -LiteralPath $SnapshotPath)) {
        return @{ trusted = $false; markets = @{} }
    }
    $payload = Get-Content -LiteralPath $SnapshotPath -Raw | ConvertFrom-Json
    $generatedAt = [string]$payload.generated_at
    $ageSec = [double]::PositiveInfinity
    if ($generatedAt) {
        try {
            $generated = [DateTimeOffset]::Parse($generatedAt)
            $ageSec = [Math]::Max(0.0, ([DateTimeOffset]::UtcNow - $generated.ToUniversalTime()).TotalSeconds)
        } catch {
            $ageSec = [double]::PositiveInfinity
        }
    }
    $markets = @{}
    foreach ($market in @("KR", "US")) {
        $row = $payload.markets.$market
        $positions = @($row.positions | ForEach-Object { "$($_.ticker):$($_.qty)" } | Sort-Object)
        $orders = @($row.open_orders | ForEach-Object { "$($_.ticker):$($_.remaining_qty):$($_.order_no)" } | Sort-Object)
        $markets[$market] = @{
            fresh = -not [bool]$row.stale -and -not [bool]$row.missing
            positions = $positions
            open_orders = $orders
            last_success_at = [string]$row.last_success_at
        }
    }
    return @{ trusted = $true; generated_at = $generatedAt; age_sec = $ageSec; markets = $markets }
}

function Get-RunningBrokerTruthScheduler {
    $lockPath = Join-Path $Root "state\broker_truth_scheduler.lock.json"
    if (-not (Test-Path -LiteralPath $lockPath)) {
        return @{ running = $false; pid = 0 }
    }
    try {
        $lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
        $pidValue = [int]$lock.pid
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        return @{ running = [bool]$process; pid = $pidValue }
    } catch {
        return @{ running = $false; pid = 0 }
    }
}

function Invoke-BrokerTruthChecked {
    # Windows PowerShell 5 can promote native stderr to a terminating
    # NativeCommandError when the script-wide preference is Stop.  Capture the
    # native exit code first so the healthy-lock fallback below can run.
    $previousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $refreshOutput = & $PythonExe @(
            "tools\broker_truth_scheduler.py", "--mode", "live", "--markets", "KR,US",
            "--once", "--force", "--json"
        ) 2>&1
        $refreshExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($refreshExitCode -eq 0) {
        return
    }

    # A healthy live stack already owns the scheduler lock.  In that case the
    # periodic scheduler is the writer authority, so accept only a very recent,
    # fully fresh snapshot instead of racing it with a second writer.
    $scheduler = Get-RunningBrokerTruthScheduler
    $snapshotPath = Join-Path $Root "state\live_broker_truth_snapshot.json"
    $inventory = Get-BrokerInventory $snapshotPath
    $bothFresh = [bool]$inventory.trusted -and
        [bool]$inventory.markets.KR.fresh -and [bool]$inventory.markets.US.fresh
    if ($scheduler.running -and $bothFresh -and [double]$inventory.age_sec -le 90.0) {
        Write-Output "[BROKER TRUTH] reused fresh scheduler snapshot age=$([Math]::Round([double]$inventory.age_sec, 1))s pid=$($scheduler.pid)"
        return
    }

    $detail = ($refreshOutput | ForEach-Object { [string]$_ }) -join "`n"
    throw "Broker truth refresh failed and no fresh scheduler snapshot was available: $detail"
}

Push-Location $Root
try {
    if ($DryRun) {
        Write-Output "[DRY-RUN] force fresh KR/US broker truth"
        Write-Output "[DRY-RUN] create SQLite-consistent event-store + critical runtime checkpoint"
        Invoke-PythonChecked -Arguments @("tools\stop_live_stack.py", "--dry-run", "--json")
        & .\tools\start_live_stack_headless.ps1 -DryRun
        exit 0
    }

    # Broker truth is the recovery authority. Capture it immediately before
    # the checkpoint so fills during a restart can be reconciled safely.
    Invoke-BrokerTruthChecked

    $safeLabel = ($Label -replace '[^A-Za-z0-9_.-]+', '_').Trim('._')
    if (-not $safeLabel) { $safeLabel = "operator_restart" }
    $backupJson = & $PythonExe "tools\live_maintenance.py" "backup" "--mode" "live" "--label" "before_restart_$safeLabel" "--json"
    if ($LASTEXITCODE -ne 0) {
        throw "Pre-restart checkpoint failed; live stack was not stopped."
    }
    $backup = ($backupJson -join "`n") | ConvertFrom-Json
    if (-not $backup.ok -or -not (Test-Path -LiteralPath $backup.backup_dir)) {
        throw "Pre-restart checkpoint was not verified; live stack was not stopped."
    }
    $before = Get-BrokerInventory (Join-Path $backup.backup_dir "live_broker_truth_snapshot.json")
    Write-Output "[CHECKPOINT] $($backup.backup_dir)"

    Invoke-PythonChecked -Arguments @("tools\stop_live_stack.py", "--timeout", "15", "--json")
    & .\tools\start_live_stack_headless.ps1
    if ($LASTEXITCODE -ne 0) {
        throw "Live stack startup failed after checkpoint $($backup.backup_dir)"
    }

    Invoke-BrokerTruthChecked
    $after = Get-BrokerInventory (Join-Path $Root "state\live_broker_truth_snapshot.json")
    $manifestPath = Join-Path $Root "state\headless_live_stack_pids.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "Restart completed without PID manifest. Checkpoint: $($backup.backup_dir)"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $dead = @()
    foreach ($property in $manifest.PSObject.Properties) {
        if (-not (Get-Process -Id ([int]$property.Value) -ErrorAction SilentlyContinue)) {
            $dead += "$($property.Name):$($property.Value)"
        }
    }
    if ($dead.Count -gt 0) {
        throw "Restart left dead roles: $($dead -join ', '). Checkpoint: $($backup.backup_dir)"
    }

    $continuity = [ordered]@{
        schema_version = "live_restart_continuity_v1"
        restarted_at = (Get-Date).ToString("o")
        label = $safeLabel
        checkpoint_dir = [string]$backup.backup_dir
        pids = $manifest
        before_broker = $before
        after_broker = $after
        all_roles_alive = $true
        broker_truth_fresh = [bool]$after.markets.KR.fresh -and [bool]$after.markets.US.fresh
    }
    $continuity | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "state\live_restart_last.json" -Encoding UTF8
    if (-not $continuity.broker_truth_fresh) {
        throw "Restarted, but broker truth is not fresh. Check state/live_restart_last.json"
    }
    Write-Output "[OK] safe restart complete; checkpoint=$($backup.backup_dir)"
} finally {
    Pop-Location
}
