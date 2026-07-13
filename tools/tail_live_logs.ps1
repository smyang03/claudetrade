[CmdletBinding()]
param(
    [ValidateSet("live_trading", "error", "trading_bot", "live_guardian", "broker_truth_scheduler", "preopen_scheduler")]
    [string]$Role = "live_trading"
)

# 라이브 스택은 headless(창 없음)로 돌기 때문에 실행 화면이 없다.
# 이 스크립트는 프로세스를 띄우지 않고 로그만 따라 읽는다(read-only) — 중복 기동 위험 없음.

$Root = Split-Path -Parent $PSScriptRoot
$today = Get-Date -Format 'yyyyMMdd'

switch ($Role) {
    "live_trading" { $path = Join-Path $Root "logs\system\live_trading_$today.log" }
    "error"        { $path = Join-Path $Root "logs\system\live_error_$today.log" }
    default        { $path = Join-Path $Root "logs\runtime\$Role.err.log" }
}

Write-Host "[TAIL] $Role" -ForegroundColor Cyan
Write-Host "       $path" -ForegroundColor DarkGray
Write-Host "       (읽기 전용 — 이 창을 닫아도 봇은 계속 돈다)" -ForegroundColor DarkGray
Write-Host ""

# 날짜가 바뀌면 새 로그 파일이 생기므로, 없으면 생길 때까지 기다린다.
while (-not (Test-Path -LiteralPath $path)) {
    Write-Host "[WAIT] 로그 파일 생성 대기: $path" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}

Get-Content -LiteralPath $path -Wait -Tail 40 -Encoding UTF8
