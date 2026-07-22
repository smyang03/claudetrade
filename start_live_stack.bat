@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

:: 라이브 스택 통제 재시작 — 소유자는 headless watchdog 하나뿐이다.
::
:: 과거 이 파일은 wt 탭으로 자체 스택을 띄웠다. 그런데 watchdog(schtasks, 5분 주기)이
:: 이미 스택을 띄워둔 상태에서 실행하면, CommandLine에 프로젝트 경로가 없는 watchdog
:: 프로세스를 "남의 프로세스"로 오판해 죽이지 못한 채 두 번째 스택을 띄웠다.
:: → 라이브 봇 2개 동시 실행(2026-07-13 텔레그램 getUpdates 409 충돌로 실측).
:: 이제는 정지(cwd 기준 tools\stop_live_stack.py)만 하고, 기동은 watchdog에 위임한다.

set "PROJECT_DIR=E:\code\claudetrade"
set "CONDA_ENV=upbit"
set "WATCHDOG_TASK=claudetrade_live_stack_watchdog"
set "DRY_RUN=0"
set "NO_PAUSE=0"
:: 인자는 순서 무관하게 훑는다(--dry-run --no-pause 조합 허용).
:: --no-pause: 끝에서 멈추지 않고 창을 닫는다. 자동/반복 재시작용 —
:: pause로 대기하던 cmd 창이 재시작마다 쌓였다(2026-07-22 실측 3개 잔존).
for %%A in (%*) do (
  if /I "%%~A"=="--dry-run" set "DRY_RUN=1"
  if /I "%%~A"=="--no-pause" set "NO_PAUSE=1"
)

echo [INFO] project=%PROJECT_DIR%
if "%DRY_RUN%"=="1" echo [INFO] dry-run: 프로세스를 죽이거나 기동하지 않는다.

if not exist "%PROJECT_DIR%\trading_bot.py" (
  echo [ERROR] PROJECT_DIR is invalid: %PROJECT_DIR%
  exit /b 1
)

cd /d "%PROJECT_DIR%"
call conda activate %CONDA_ENV%
if errorlevel 1 (
  echo [ERROR] failed to activate conda env: %CONDA_ENV%
  exit /b 1
)

:: 1) watchdog 잠금 — 정지~기동 사이에 되살리지 못하게 한다.
echo [LOCK] watchdog 일시 중지...
if "%DRY_RUN%"=="1" (
  echo [DRY-RUN] schtasks /change /tn "%WATCHDOG_TASK%" /disable
) else (
  schtasks /change /tn "%WATCHDOG_TASK%" /disable >nul 2>nul
  if errorlevel 1 echo [WARN] watchdog 태스크 비활성화 실패(미등록? register_tasks.bat 확인^)
)

:: 2) 전체 정지 — headless/wt 어느 쪽이 띄웠든 cwd 기준으로 잡는다.
echo [STOP] 라이브 스택 정지...
if "%DRY_RUN%"=="1" (
  python tools\stop_live_stack.py --dry-run
) else (
  python tools\stop_live_stack.py
  if errorlevel 1 (
    echo [ERROR] 스택 정지 실패 — 살아있는 프로세스가 있어 중단한다(중복 기동 방지^)
    schtasks /change /tn "%WATCHDOG_TASK%" /enable >nul 2>nul
    exit /b 1
  )
)

:: 3) 브로커 truth 선갱신 — 봇이 뜨기 전에 스냅샷을 데워둔다.
::    (watchdog은 broker_truth_scheduler를 --no-refresh-on-start로 띄운다)
if "%DRY_RUN%"=="1" (
  echo [DRY-RUN] broker truth 선갱신 생략
) else (
  echo [REFRESH] broker truth 스냅샷 갱신...
  python tools\broker_truth_scheduler.py --mode live --markets KR,US --once --force --ttl-sec 180 --json
  if errorlevel 1 echo [WARN] broker truth 선갱신 실패(KIS 불가 또는 휴장^) — 스택 기동 후 자동 재시도한다.
)

:: 4) watchdog 해제 + 즉시 1회 실행 → 단일 스택 기동
echo [START] watchdog 재활성화 + 즉시 기동...
if "%DRY_RUN%"=="1" (
  echo [DRY-RUN] schtasks /change /tn "%WATCHDOG_TASK%" /enable
  echo [DRY-RUN] powershell -File tools\start_live_stack_headless.ps1
  exit /b 0
)
schtasks /change /tn "%WATCHDOG_TASK%" /enable >nul 2>nul
if errorlevel 1 echo [WARN] watchdog 재활성화 실패 — 자동복구가 꺼진 상태다. register_tasks.bat 실행 필요.
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\tools\start_live_stack_headless.ps1"
if errorlevel 1 (
  echo [ERROR] 스택 기동 실패.
  exit /b 1
)

:: 5) 결과 확인 — 역할별 1개여야 한다(DUP 경고가 뜨면 즉시 조치^)
echo.
echo [VERIFY] 역할별 프로세스 확인...
timeout /t 20 /nobreak >nul
python tools\stop_live_stack.py --dry-run

echo.
echo [OK] 단일 스택 기동 완료. 로그: logs\runtime\*.log / logs\system\live_trading_*.log
echo      자동복구: watchdog(%WATCHDOG_TASK%)이 5분마다 죽은 역할을 되살린다.

:: 6) 로그 tail 탭 — 프로세스를 띄우는 게 아니라 읽기만 한다(주문에는 무관^).
::    스택은 headless라 실행 창이 없으므로, 돌아가는 걸 눈으로 보려면 이 탭들을 쓴다.
::
::    ★재시작마다 3개씩 쌓인다. stop_live_stack은 cwd 기준으로 스택 역할만 정리하므로
::    이 tail 창들은 대상이 아니었고, 2026-07-22 재시작 5회에 15개가 남아 있었다.
::    새로 열기 전에 기존 tail을 먼저 닫는다(읽기 전용이라 언제 끊어도 안전^).
echo [CLEANUP] 이전 로그 tail 창 정리...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\tools\stop_log_tails.ps1"

where wt >nul 2>nul
if errorlevel 1 (
  echo [INFO] Windows Terminal(wt^)이 없어 로그 탭을 열지 않는다. 대시보드: http://localhost:5000
) else (
  echo [VIEW] 통합 로그 탭 여는 중(창 1개^)...
  :: 2026-07-22: 탭 3개 -> 1개. 재시작할 때마다 tail 창이 3개씩 쌓였다
  :: (재시작 5회에 15개 잔존 실측). -Role all 이 같은 세 로그를 한 창에 합쳐 보여준다.
  wt new-tab --title "claudetrade" powershell -NoProfile -NoExit -ExecutionPolicy Bypass -File "%PROJECT_DIR%\tools\tail_live_logs.ps1" -Role all
)

echo.
echo      대시보드: http://localhost:5000
if "%NO_PAUSE%"=="1" (
  echo [INFO] --no-pause: 창을 닫는다.
) else (
  pause
)
exit /b 0
