[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:CLAUDETRADE_RUNTIME_DIR = $Root
# 스케줄 태스크(claudetrade_live_stack_watchdog)는 CWD=system32로 실행된다. 이 스크립트의
# 인라인 python 호출(Sync-CoreLiveManifests·live_guardian --ensure-bot)이 상대경로 tools\..를
# 쓰므로, 호출자 CWD와 무관하게 항상 repo 루트에서 해석되도록 작업 디렉터리를 고정한다.
# (Start-Process role 기동은 -WorkingDirectory $Root로 이미 격리돼 영향 없음.)
Set-Location -LiteralPath $Root

# 재진입 잠금 — watchdog(5분 주기)이 이 스크립트의 기동 도중에 다시 실행되면, manifest
# (state\headless_live_stack_pids.json)가 아직 기록되기 전이라 PidFile이 없는 역할
# (counterfactual_pipeline·integrity_check)을 "부재"로 오판해 두 번째 세트를 띄운다.
# manifest는 마지막에 한 번만 쓰이고 그 앞에 Wait-BrokerTruthSchedulerReady(<=20초)와
# Sync-CoreLiveManifests(<=60초) 배리어가 있어 창이 1~2분 열린다.
# → 2026-07-21 23:38:41 / 23:39:35 두 세트로 보조 3종이 중복 실행된 것을 실측했다.
# 기동 구간 전체를 단일 인스턴스로 묶어 그 창을 닫는다.
$StackMutex = New-Object System.Threading.Mutex($false, "Global\claudetrade_live_stack_headless")
$MutexHeld = $false
try {
    $MutexHeld = $StackMutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    # 직전 인스턴스가 락을 쥔 채 죽은 경우다. 소유권은 이 프로세스로 넘어오므로 그대로 진행한다.
    $MutexHeld = $true
}
if (-not $MutexHeld) {
    # ⚠️ 이 경로는 **아무것도 안 띄우고 exit 0**이다. 호출자는 종료코드로 성공/실패를
    # 구분할 수 없으므로 반드시 manifest(state\headless_live_stack_pids.json) 존재로
    # 확인해야 한다. 2026-08-24 00:36 실측: 재시작의 정지 창에서
    # claudetrade_live_stack_watchdog 태스크가 뮤텍스를 선점 → 재시작의 기동이 이 경로로
    # 빠져 **스택이 내려간 채로 남았다.** restart_live_stack_safely.ps1이 재시도로 대응한다.
    #
    # 문자열은 ASCII만 쓴다 — 이 파일은 BOM이 없어 PowerShell 5.1이 CP949로 읽고,
    # 문자열 안의 비ASCII가 깨지면 닫는 따옴표까지 소실돼 파서가 죽는다.
    # 한국어 설명은 주석에 둔다(tests/test_powershell_scripts_parse.py가 고정).
    Write-Output "[SKIP] another startup instance is in progress - skipping duplicate start (nothing was started)"
    exit 0
}

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
    # The core source must be stable and promoted before the live bot can read it.
    @{ Name = "core_shadow_tracker"; PidFile = "state\core_shadow_tracker_heartbeat.json"; Args = @("tools\core_shadow_tracker.py", "--loop", "--interval-sec", "21600") },
    @{ Name = "trading_bot"; PidFile = "state\live_trading_bot.pid"; Args = @("trading_bot.py", "--live") },
    @{ Name = "dashboard"; PidFile = "state\dashboard_server.pid"; Args = @("dashboard\dashboard_server.py") },
    @{ Name = "live_guardian"; PidFile = "state\live_guardian_heartbeat.json"; Args = @("tools\live_guardian.py", "--mode", "live", "--watch", "--ensure-bot", "--interval-sec", "300", "--telegram-alert") },
    @{ Name = "broker_truth_scheduler"; PidFile = "state\broker_truth_scheduler.lock.json"; Args = @("tools\broker_truth_scheduler.py", "--mode", "live", "--markets", "KR,US", "--loop", "--interval-sec", "30", "--refresh-interval-min", "2", "--failure-retry-min", "2", "--preopen-min", "20", "--postclose-min", "15", "--ttl-sec", "180", "--no-refresh-on-start") },
    @{ Name = "preopen_scheduler"; PidFile = "state\preopen_scheduler.lock.json"; Args = @("tools\preopen_scheduler.py", "--mode", "live", "--markets", "KR,US", "--loop", "--interval-sec", "60") },
    @{ Name = "counterfactual_pipeline"; PidFile = ""; Args = @("tools\run_counterfactual_pipeline.py", "--phase", "due", "--market", "KR,US", "--loop", "--interval-sec", "300", "--json") },
    @{ Name = "integrity_check"; PidFile = ""; Args = @("tools\integrity_check.py", "--watch", "--interval-sec", "600", "--telegram-alert") },
    # 2026-08-05: breadth/VIX/등락비율 5개 파일의 상시 생산자. 이 파일들은 07-10 일회성
    # 스크립트로 만들어진 뒤 갱신 주체가 없어 25일 정체됐다(신선도 게이트가 탐지).
    # 6시간 주기·멱등(없는 날짜만 append)이라 중복 실행 위험이 없다.
    @{ Name = "market_context_refresher"; PidFile = ""; Args = @("tools\refresh_market_context_daily.py", "--loop", "--interval-sec", "21600") }
)

# Break the cold-start dependency cycle only when the bot is actually absent.
# When the bot is already alive, a missing dashboard/observer role must be
# repairable without racing the single-instance broker-truth scheduler.
$existingBotPid = 0
$existingBotRunning = $false
$existingBotPidPath = Join-Path $Root "state\live_trading_bot.pid"
if (Test-Path -LiteralPath $existingBotPidPath) {
    try {
        $existingBotPid = [int]((Get-Content -LiteralPath $existingBotPidPath -Raw | ConvertFrom-Json).pid)
        $existingBotRunning = [bool](Get-Process -Id $existingBotPid -ErrorAction SilentlyContinue)
    } catch {
        $existingBotPid = 0
        $existingBotRunning = $false
    }
}
$existingBrokerSchedulerPid = 0
$existingBrokerSchedulerRunning = $false
$existingBrokerSchedulerHealthy = $false
$brokerSchedulerLockPath = Join-Path $Root "state\broker_truth_scheduler.lock.json"
$brokerSchedulerHeartbeatPath = Join-Path $Root "state\broker_truth_scheduler_heartbeat.json"
if (Test-Path -LiteralPath $brokerSchedulerLockPath) {
    try {
        $brokerLock = Get-Content -LiteralPath $brokerSchedulerLockPath -Raw | ConvertFrom-Json
        $existingBrokerSchedulerPid = [int]$brokerLock.pid
        $brokerProcess = Get-Process -Id $existingBrokerSchedulerPid -ErrorAction SilentlyContinue
        if ($brokerProcess -and $brokerProcess.Path) {
            $existingBrokerSchedulerRunning = ((Resolve-Path -LiteralPath $brokerProcess.Path).Path -ieq (Resolve-Path -LiteralPath $PythonExe).Path)
        }
        if ($existingBrokerSchedulerRunning -and (Test-Path -LiteralPath $brokerSchedulerHeartbeatPath)) {
            $brokerHeartbeat = Get-Content -LiteralPath $brokerSchedulerHeartbeatPath -Raw | ConvertFrom-Json
            $existingBrokerSchedulerHealthy = (
                [int]$brokerHeartbeat.pid -eq $existingBrokerSchedulerPid -and
                [bool]$brokerHeartbeat.healthy
            )
        }
    } catch {
        $existingBrokerSchedulerPid = 0
        $existingBrokerSchedulerRunning = $false
        $existingBrokerSchedulerHealthy = $false
    }
}
if (-not $DryRun) {
    if ($existingBotRunning) {
        Write-Output "[BROKER TRUTH] bot already alive pid=$existingBotPid; reuse the running scheduler for role repair"
    } elseif ($existingBrokerSchedulerRunning -and $existingBrokerSchedulerHealthy) {
        Write-Output "[BROKER TRUTH] scheduler already alive and healthy pid=$existingBrokerSchedulerPid; reuse it before starting the bot"
    } elseif ($existingBrokerSchedulerRunning) {
        throw "Existing broker_truth_scheduler pid=$existingBrokerSchedulerPid owns the lock but is not healthy; refusing a competing force refresh."
    } else {
        & $PythonExe "tools\broker_truth_scheduler.py" "--mode" "live" "--markets" "KR,US" "--once" "--force" "--json"
        if ($LASTEXITCODE -ne 0) {
            throw "Initial broker-truth refresh failed; trading_bot was not started."
        }
    }
} else {
    Write-Output "[DRY-RUN] would force broker truth only if trading_bot is absent"
}

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

function Wait-BrokerTruthSchedulerReady([int]$ProcessId) {
    if ($DryRun) { return }
    $lockPath = Join-Path $Root "state\broker_truth_scheduler.lock.json"
    $heartbeatPath = Join-Path $Root "state\broker_truth_scheduler_heartbeat.json"
    # 2026-08-21: 여기서는 "프로세스가 사라졌는가"만 본다.
    #
    # 이전에는 Test-ClaudeTradePythonPid로 경로까지 대조하고 실패하면 첫 iteration에
    # 즉시 throw했다. 그런데 스폰 직후에는 Get-Process가 성공해도 $process.Path가
    # 아직 비어 있는 창이 있어 살아있는 프로세스를 "exited"로 오판한다 —
    # 08-20 재시작 5회 중 2회가 이 오탐으로 실패했다(매니페스트 미기록·부분 기동).
    #
    # 공유 헬퍼는 일부러 건드리지 않는다. 그쪽을 이름 기반으로 완화하면 stale pid와
    # PID 재사용이 겹칠 때 중복 탐지(아래 $runningPid)가 무관한 python을 우리 것으로
    # 오인해 기동 자체를 건너뛴다 — 08-13 중복 실행 사고의 거울상이 된다.
    # 진짜 권위 검사는 아래 lock.pid·heartbeat.pid 일치이고, 루프가 80x250ms=20초로
    # 유계라 죽은 프로세스여도 최악 20초 뒤 말미 throw로 끝난다.
    $deadStreak = 0
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        $alive = $true
        try {
            Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null
        } catch {
            $alive = $false
        }
        if ($alive) {
            $deadStreak = 0
        } else {
            $deadStreak++
            if ($deadStreak -ge 3) {
                throw "broker_truth_scheduler exited before acquiring writer authority pid=$ProcessId"
            }
        }
        try {
            $lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
            $heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw | ConvertFrom-Json
            if (
                [int]$lock.pid -eq $ProcessId -and
                [int]$heartbeat.pid -eq $ProcessId -and
                [bool]$heartbeat.healthy
            ) {
                Write-Output "[READY] broker_truth_scheduler owns lock and heartbeat pid=$ProcessId"
                return
            }
        } catch {
            # Lock and heartbeat are atomic files but may not exist during the
            # first scheduler tick. Keep the startup barrier bounded.
        }
        Start-Sleep -Milliseconds 250
    }
    throw "broker_truth_scheduler did not acquire lock and heartbeat authority pid=$ProcessId"
}

function Sync-CoreLiveManifests {
    if ($DryRun) {
        Write-Output "[DRY-RUN] would wait for core_shadow_tracker and refresh current KR/US core live manifests"
        return
    }
    $heartbeatPath = Join-Path $Root "state\core_shadow_tracker_heartbeat.json"
    $healthy = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw | ConvertFrom-Json
            $trackerPid = [int]$heartbeat.pid
            if ($heartbeat.status -eq "healthy" -and (Test-ClaudeTradePythonPid $trackerPid)) {
                $healthy = $true
                break
            }
        } catch {
            # The tracker writes its heartbeat atomically; retry while it starts.
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $healthy) {
        throw "core_shadow_tracker did not become healthy; live core manifests were not refreshed"
    }
    & $PythonExe "tools\profit_strategy_materializer.py" "--core-current-sessions"
    if ($LASTEXITCODE -ne 0) {
        # exit!=0은 실패가 아니라 "코어에 라이브 권한을 주지 않는다"는 정상 판정에서도 난다.
        # 예) 월 경계의 core_shadow_effective_month_mismatch, 계약 미허용 자산(core_asset_not_allowed).
        # 이 경우에도 매니페스트 파일 자체는 authority=NO_LIVE_AUTHORITY / status=blocked로 갱신되므로
        # 코어는 이미 안전하게 막혀 있고, 트레이딩 봇 기동까지 막을 이유가 없다.
        # 2026-08-01 01:37: 이 throw 때문에 8개 프로세스를 정지한 뒤 기동이 중단돼 봇이 죽은 채 남았다
        # (watchdog도 같은 지점에서 계속 실패 → 자동 복구 불가). 그래서 아래 조건에서만 계속한다.
        #   - KR/US 매니페스트가 모두 존재하고 15분 이내로 갱신됐고
        #   - 둘 다 authority=NO_LIVE_AUTHORITY (권한이 살아있는데 갱신만 실패한 경우는 여전히 throw)
        $manifestSafe = $true
        foreach ($mk in @("KR", "US")) {
            $manifestPath = Join-Path $Root "state\profit_strategy_core_live_manifest_$mk.json"
            if (-not (Test-Path -LiteralPath $manifestPath)) { $manifestSafe = $false; break }
            try {
                $coreManifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
                if ([string]$coreManifest.authority -ne "NO_LIVE_AUTHORITY") { $manifestSafe = $false; break }
                $ageMin = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse([string]$coreManifest.generated_at).ToUniversalTime()).TotalMinutes
                if ($ageMin -gt 15) { $manifestSafe = $false; break }
            } catch {
                $manifestSafe = $false
                break
            }
        }
        if (-not $manifestSafe) {
            throw "Current-session core live manifest refresh failed (manifests missing/stale/still-authorized)"
        }
        Write-Output "[CORE AUTHORITY] core blocked (NO_LIVE_AUTHORITY) - manifests fresh, continuing stack startup"
    } else {
        Write-Output "[CORE AUTHORITY] current KR/US manifests refreshed after tracker"
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
        if ($role.Name -eq "core_shadow_tracker") {
            Sync-CoreLiveManifests
        }
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
    if ($role.Name -eq "broker_truth_scheduler") {
        # A safe restart performs a forced broker refresh immediately after
        # this launcher returns. Do not let that one-shot process win the
        # single-writer lock and strand this loop as a false-alive PID.
        Wait-BrokerTruthSchedulerReady -ProcessId ([int]$process.Id)
    }
    if ($role.Name -eq "core_shadow_tracker") {
        Sync-CoreLiveManifests
    }
}

if (-not $DryRun) {
    $manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8
}

# 잠금 해제. 예외로 여기 도달하지 못하고 프로세스가 죽어도 OS가 abandoned 상태로 넘겨
# 다음 인스턴스가 위 catch 경로로 획득하므로, 락이 영구히 잠기지는 않는다.
if ($MutexHeld) {
    $StackMutex.ReleaseMutex()
    $StackMutex.Dispose()
}
