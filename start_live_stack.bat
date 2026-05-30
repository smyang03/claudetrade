@echo off
setlocal EnableExtensions EnableDelayedExpansion
goto :main

:kill_pid_file
set "PID_FILE=%~1"
set "LABEL=%~2"
if not exist "%PID_FILE%" (
  echo [SKIP] %LABEL% pid file not found: %PID_FILE%
  exit /b 0
)
set "PID="
for /f %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [int]((Get-Content -Raw -LiteralPath '%PID_FILE%' | ConvertFrom-Json).pid) } catch { '' }"') do (
  if not defined PID set "PID=%%P"
)
if not defined PID (
  echo [WARN] %LABEL% pid file has no pid: %PID_FILE%
  exit /b 0
)
call :kill_pid_tree "%PID%" "%LABEL%"
if "%DRY_RUN%"=="1" exit /b 0
tasklist /FI "PID eq %PID%" 2>nul | findstr /r /c:"[ ]%PID%[ ]" >nul
if errorlevel 1 (
  del /f /q "%PID_FILE%" >nul 2>nul
  echo [OK] removed pid file: %PID_FILE%
) else (
  echo [WARN] %LABEL% pid %PID% is still alive; pid file kept.
)
exit /b 0

:kill_matching
set "SCRIPT_NEEDLE=%~1"
set "LABEL=%~2"
set "FOUND_MATCH=0"
for /f %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = '%PROJECT_DIR%'; $needle = '%SCRIPT_NEEDLE%'; $me = $PID; Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -and $_.CommandLine -like ('*' + $root + '*') -and $_.CommandLine -like ('*' + $needle + '*') } | Select-Object -ExpandProperty ProcessId"') do (
  set "FOUND_MATCH=1"
  call :kill_pid_tree "%%P" "%LABEL%"
)
if "%FOUND_MATCH%"=="0" echo [SKIP] %LABEL% process not found.
exit /b 0

:kill_pid_tree
set "TARGET_PID=%~1"
set "TARGET_LABEL=%~2"
if "%TARGET_PID%"=="" exit /b 0
if "%DRY_RUN%"=="1" (
  echo [DRY-RUN] taskkill /PID %TARGET_PID% /T /F  [%TARGET_LABEL%]
  exit /b 0
)
echo [KILL] %TARGET_LABEL% pid=%TARGET_PID%
taskkill /PID %TARGET_PID% /T /F >nul 2>nul
if errorlevel 1 echo [WARN] taskkill failed or process already exited: pid=%TARGET_PID%
exit /b 0

:main
set "PROJECT_DIR=E:\code\claudetrade"
set "CONDA_ENV=upbit"
set "STATE_DIR=%PROJECT_DIR%\state"
set "DRY_RUN=0"
if /I "%~1"=="--dry-run" set "DRY_RUN=1"

echo [INFO] project=%PROJECT_DIR%
if "%DRY_RUN%"=="1" echo [INFO] dry-run mode: no process will be killed and wt will not be started.

if not exist "%PROJECT_DIR%\trading_bot.py" (
  echo [ERROR] PROJECT_DIR is invalid: %PROJECT_DIR%
  exit /b 1
)

echo [STOP] stopping existing live stack processes...
call :kill_pid_file "%STATE_DIR%\live_trading_bot.pid" "live trading_bot"
call :kill_pid_file "%STATE_DIR%\dashboard_server.pid" "dashboard"
call :kill_matching "trading_bot.py --live" "live trading_bot"
call :kill_matching "dashboard\dashboard_server.py" "dashboard"
call :kill_matching "tools\live_guardian.py" "live_guardian"
call :kill_matching "tools\preopen_scheduler.py" "preopen_scheduler"
call :kill_matching "tools\run_counterfactual_pipeline.py" "counterfactual_pipeline"

if "%DRY_RUN%"=="1" (
  echo [DRY-RUN] startup skipped.
  exit /b 0
)

timeout /t 3 /nobreak >nul

where wt >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows Terminal wt.exe was not found in PATH.
  exit /b 1
)

echo [START] opening live stack tabs...
wt ^
  new-tab --title "trading_bot" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] call conda activate %CONDA_ENV% ^&^& python trading_bot.py --live && call conda activate %CONDA_ENV% && python trading_bot.py --live" ^
  ; new-tab --title "dashboard" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] call conda activate %CONDA_ENV% ^&^& python dashboard\dashboard_server.py && call conda activate %CONDA_ENV% && python dashboard\dashboard_server.py" ^
  ; new-tab --title "live_guardian" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] call conda activate %CONDA_ENV% ^&^& python tools\live_guardian.py --mode live --watch --interval-sec 300 --telegram-alert && call conda activate %CONDA_ENV% && python tools\live_guardian.py --mode live --watch --interval-sec 300 --telegram-alert" ^
  ; new-tab --title "preopen_scheduler" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] call conda activate %CONDA_ENV% ^&^& python tools\preopen_scheduler.py --mode live --markets KR,US --loop --interval-sec 60 && call conda activate %CONDA_ENV% && python tools\preopen_scheduler.py --mode live --markets KR,US --loop --interval-sec 60" ^
  ; new-tab --title "counterfactual_pipeline" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] call conda activate %CONDA_ENV% ^&^& python tools\run_counterfactual_pipeline.py --phase due --market KR,US --loop --interval-sec 300 --json && call conda activate %CONDA_ENV% && python tools\run_counterfactual_pipeline.py --phase due --market KR,US --loop --interval-sec 300 --json"

exit /b %ERRORLEVEL%
