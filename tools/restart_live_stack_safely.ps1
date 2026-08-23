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
    # Windows PowerShell 5.1의 Get-Content는 BOM 없는 UTF-8을 시스템 ANSI(CP949)로 읽는다.
    # 브로커 스냅샷에는 한글 종목명이 들어있어(예: "KODEX 모멘텀주") 인코딩을 생략하면
    # mojibake로 깨지면서 문자열 종료 따옴표까지 소실돼 ConvertFrom-Json이 문법 오류로 죽는다.
    # 2026-07-28 실측: KR 코어 275280/275300 보유 상태에서 재시작이 봇 정지 전 단계에서 중단됐다.
    $payload = Get-Content -LiteralPath $SnapshotPath -Raw -Encoding UTF8 | ConvertFrom-Json
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

    # 2026-08-03: 장외(주말·야간)에는 스케줄러가 락은 쥔 채 갱신을 안 해서 스냅샷이
    # 90초 조건을 절대 못 넘긴다 — 08-02에 두 번 수동 개입(스케줄러 정지→강제갱신)으로
    # 우회한 것을 자동화한다. 어차피 전체 재시작이 스케줄러를 곧 정지·재기동하므로,
    # 루프를 먼저 내리고 강제 갱신을 1회 재시도한다.
    if ($scheduler.running) {
        Write-Output "[BROKER TRUTH] snapshot stale under running scheduler pid=$($scheduler.pid); stopping loop for a forced refresh (restart will relaunch it)"
        try {
            Stop-Process -Id ([int]$scheduler.pid) -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 800
        } catch {
            Write-Output "[BROKER TRUTH] scheduler stop failed: $($_.Exception.Message)"
        }
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
            Write-Output "[BROKER TRUTH] forced refresh succeeded after stopping the scheduler loop"
            return
        }
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

    # 2026-08-24 수리: 기동을 **실제로 떴는지 확인하며 재시도**한다.
    #
    # start_live_stack_headless.ps1은 전역 뮤텍스로 재진입을 막는다(07-21 중복 세트 사고
    # 수리). 다른 인스턴스가 뮤텍스를 쥐고 있으면 "[SKIP] 다른 기동 인스턴스가 진행 중"을
    # 찍고 **아무것도 안 띄운 채 exit 0**으로 끝난다. 그러면 아래 $LASTEXITCODE 검사를
    # 통과해버리고, 스택은 내려간 상태로 남는다.
    #
    # 08-24 00:36 실측: 정지 창에서 `claudetrade_live_stack_watchdog` 스케줄 태스크가
    # 발동해 뮤텍스를 선점 → 재시작의 기동이 SKIP → PID 원장 없음 → throw.
    # **스택이 완전히 내려간 채로 남았다**(수동 기동으로 복구). 장 시작 전이었다면 사고다.
    #
    # 원장 존재를 성공 기준으로 삼고, 뮤텍스 창(짧다)이 닫힐 때까지 재시도한다.
    $manifestPath = Join-Path $Root "state\headless_live_stack_pids.json"
    $startAttempts = 3
    for ($attempt = 1; $attempt -le $startAttempts; $attempt++) {
        & .\tools\start_live_stack_headless.ps1
        if ($LASTEXITCODE -ne 0) {
            throw "Live stack startup failed after checkpoint $($backup.backup_dir)"
        }
        if (Test-Path -LiteralPath $manifestPath) { break }
        if ($attempt -lt $startAttempts) {
            # 문자열 리터럴은 ASCII만 쓴다. 이 파일은 BOM이 없어서 Windows PowerShell 5.1이
            # CP949로 읽는데, 문자열 안의 비ASCII가 깨지면 닫는 따옴표까지 소실돼 파서가
            # 블록 전체를 못 닫는다(2026-08-24 실측: 한글 문자열 추가 후 파스 에러).
            # 한국어 설명은 주석에만 둔다 — 이 파일의 기존 규약이 그렇다(문자열 내 비ASCII 0건).
            Write-Output "[RETRY] startup was skipped (another instance likely holds the mutex) - attempt $attempt/$startAttempts, retrying in 20s"
            Start-Sleep -Seconds 20
        }
    }

    Invoke-BrokerTruthChecked
    $after = Get-BrokerInventory (Join-Path $Root "state\live_broker_truth_snapshot.json")
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        # 여기 문자열도 ASCII만 (위 주석 참고).
        throw ("Restart completed without PID manifest after $startAttempts attempts " +
               "(startup kept getting skipped - check claudetrade_live_stack_watchdog task " +
               "or another running start instance). Checkpoint: $($backup.backup_dir)")
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
    # 2026-08-23: 성공 경로에서 **명시적으로 0을 돌려준다.**
    # 이 스크립트는 exit를 안 불렀기 때문에 PowerShell이 마지막 네이티브 명령의
    # $LASTEXITCODE를 그대로 프로세스 종료코드로 내보냈다. 정상 재시작에서도
    # Invoke-BrokerTruthChecked가 "락은 새로 뜬 스케줄러가 쥐었으니 그 스냅샷을
    # 재사용" 경로로 빠지면 broker_truth_scheduler --once가 비0으로 끝나고,
    # 함수는 성공 반환하지만 $LASTEXITCODE는 그 값이 남는다.
    # 실측(08-23): "[OK] safe restart complete"를 찍고도 종료코드 2.
    # 종료코드로 성공을 판단하는 호출자·자동화가 정상 재시작을 실패로 읽는다.
    # 실패는 전부 throw로 나가므로 여기 도달했다는 것 자체가 성공이다.
    exit 0
} finally {
    Pop-Location
}
