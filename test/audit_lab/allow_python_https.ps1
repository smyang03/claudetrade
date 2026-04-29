param(
    [string]$PythonPath = "C:\Users\Unknown\anaconda3\python.exe",
    [string]$RuleName = "Allow Python HTTPS Outbound"
)

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "관리자 권한 PowerShell에서 실행해야 합니다."
    Write-Host "실행 예:"
    Write-Host "powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 1
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "python.exe 경로를 찾을 수 없습니다: $PythonPath"
    exit 2
}

$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "기존 규칙이 있어 제거 후 재생성합니다: $RuleName"
    $existing | Remove-NetFirewallRule
}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Outbound `
    -Program $PythonPath `
    -Action Allow `
    -Protocol TCP `
    -RemotePort 443 `
    -Profile Any | Out-Null

Write-Host "방화벽 허용 규칙 추가 완료: $RuleName"
Write-Host "확인 명령:"
Write-Host "python -B -m test.audit_lab.cli diagnose-network"
