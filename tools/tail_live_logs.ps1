[CmdletBinding()]
param(
    [ValidateSet("all", "live_trading", "error", "trading_bot", "live_guardian", "broker_truth_scheduler", "preopen_scheduler")]
    [string]$Role = "all",
    [int]$Tail = 30
)

# 라이브 스택은 headless(창 없음)로 돌기 때문에 실행 화면이 없다.
# 이 스크립트는 프로세스를 띄우지 않고 로그만 따라 읽는다(read-only) — 중복 기동 위험 없음.
#
# 2026-07-22: 기본을 -Role all 로 바꿨다. 이전에는 bat이 wt 탭 3개(live_trading /
# bot_stderr / live_error)를 띄워서 재시작할 때마다 창이 3개씩 쌓였다(재시작 5회에
# 15개 잔존 실측). all 모드는 같은 세 로그를 한 창에서 역할 태그와 색으로 구분해
# 보여주므로 창이 하나면 된다.

$Root = Split-Path -Parent $PSScriptRoot
$today = Get-Date -Format 'yyyyMMdd'

function Resolve-LogPath([string]$r) {
    switch ($r) {
        "live_trading" { return (Join-Path $Root "logs\system\live_trading_$today.log") }
        "error"        { return (Join-Path $Root "logs\system\live_error_$today.log") }
        default        { return (Join-Path $Root "logs\runtime\$r.err.log") }
    }
}

if ($Role -ne "all") {
    $path = Resolve-LogPath $Role
    Write-Host "[TAIL] $Role" -ForegroundColor Cyan
    Write-Host "       $path" -ForegroundColor DarkGray
    Write-Host "       (읽기 전용 - 이 창을 닫아도 봇은 계속 돈다)" -ForegroundColor DarkGray
    Write-Host ""
    while (-not (Test-Path -LiteralPath $path)) {
        Write-Host "[WAIT] 로그 파일 생성 대기: $path" -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
    Get-Content -LiteralPath $path -Wait -Tail $Tail -Encoding UTF8
    return
}

# ── all 모드: 세 로그를 한 창에 합쳐 따라간다 ──────────────────────────────
# Get-Content -Wait은 한 파일만 붙잡으므로, 파일별 읽은 줄 수를 기억하며 폴링한다.
# 폴링이라 CPU가 거의 들지 않고 파일 로테이션(날짜 변경)에도 자연스럽게 대응한다.
$targets = @(
    @{ Role = "live_trading"; Tag = "BOT "; Color = "Gray" },
    @{ Role = "trading_bot";  Tag = "STDERR"; Color = "Yellow" },
    @{ Role = "error";        Tag = "ERROR"; Color = "Red" }
)

Write-Host "[TAIL] 통합 (live_trading + stderr + error)" -ForegroundColor Cyan
foreach ($t in $targets) {
    Write-Host ("       {0,-7} {1}" -f $t.Tag.Trim(), (Resolve-LogPath $t.Role)) -ForegroundColor DarkGray
}
Write-Host "       (읽기 전용 - 이 창을 닫아도 봇은 계속 돈다. Ctrl+C로 종료)" -ForegroundColor DarkGray
Write-Host ""

$state = @{}
foreach ($t in $targets) {
    $p = Resolve-LogPath $t.Role
    $n = 0
    if (Test-Path -LiteralPath $p) {
        try { $n = @(Get-Content -LiteralPath $p -Encoding UTF8 -ErrorAction Stop).Count } catch { $n = 0 }
        # 시작 시 각 로그의 마지막 $Tail 줄만 보여준다.
        $start = [Math]::Max(0, $n - $Tail)
        if ($n -gt 0) {
            try {
                $lines = Get-Content -LiteralPath $p -Encoding UTF8 -ErrorAction Stop
                foreach ($line in $lines[$start..($n-1)]) {
                    if ($null -ne $line -and $line -ne "") {
                        Write-Host ("[{0}] {1}" -f $t.Tag.Trim(), $line) -ForegroundColor $t.Color
                    }
                }
            } catch { }
        }
    }
    $state[$t.Role] = @{ Path = $p; Count = $n; Date = $today }
}

while ($true) {
    Start-Sleep -Milliseconds 800
    $now = Get-Date -Format 'yyyyMMdd'
    foreach ($t in $targets) {
        $s = $state[$t.Role]
        # 날짜가 바뀌면 새 로그 파일로 갈아탄다(처음부터 읽는다).
        if ($s.Date -ne $now) {
            $script:today = $now
            $s.Path = Resolve-LogPath $t.Role
            $s.Count = 0
            $s.Date = $now
        }
        if (-not (Test-Path -LiteralPath $s.Path)) { continue }
        try { $lines = Get-Content -LiteralPath $s.Path -Encoding UTF8 -ErrorAction Stop } catch { continue }
        $total = @($lines).Count
        if ($total -lt $s.Count) { $s.Count = 0 }   # 파일이 잘렸다면 되감는다
        if ($total -gt $s.Count) {
            foreach ($line in $lines[$s.Count..($total-1)]) {
                if ($null -ne $line -and $line -ne "") {
                    Write-Host ("[{0}] {1}" -f $t.Tag.Trim(), $line) -ForegroundColor $t.Color
                }
            }
            $s.Count = $total
        }
    }
}
