[CmdletBinding()]
param(
    [switch]$DryRun,
    [int]$KeepLatest = 0
)

# 로그 tail 창 정리.
#
# start_live_stack.bat은 마지막에 wt 탭 3개(live_trading/bot_stderr/live_error)를 띄우는데,
# stop_live_stack.py는 cwd 기준으로 '스택 역할'만 정리하므로 이 tail 창들은 대상이 아니었다.
# 그래서 재시작할 때마다 3개씩 쌓인다(2026-07-22 재시작 5회에 15개 잔존 실측).
# tail은 로그를 읽기만 하므로 언제 끊어도 주문·상태에 영향이 없다.

$ErrorActionPreference = "Stop"

$tails = @(
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'tail_live_logs\.ps1' } |
        Sort-Object CreationDate
)

if ($tails.Count -eq 0) {
    Write-Output "[tail cleanup] 정리할 tail 창 없음"
    exit 0
}

$keep = @()
if ($KeepLatest -gt 0) {
    $keep = @($tails | Select-Object -Last $KeepLatest)
}
$kill = @($tails | Where-Object { $keep.ProcessId -notcontains $_.ProcessId })

Write-Output "[tail cleanup] 발견 $($tails.Count)개 / 유지 $($keep.Count)개 / 정리 $($kill.Count)개"

foreach ($p in $kill) {
    $role = if ($p.CommandLine -match '-Role\s+(\w+)') { $Matches[1] } else { '?' }
    if ($DryRun) {
        Write-Output "  [DRY-RUN] would stop pid=$($p.ProcessId) role=$role"
        continue
    }
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Output "  stopped pid=$($p.ProcessId) role=$role"
    } catch {
        Write-Output "  skip pid=$($p.ProcessId): $($_.Exception.Message)"
    }
}
exit 0
