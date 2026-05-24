@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "BOT_MODE=%CLAUDETRADE_BOT_MODE%"
if not defined BOT_MODE set "BOT_MODE=paper"
if /I "%~1"=="--help" (
    echo Usage: auto_start_if_missing.bat [paper^|live]
    echo.
    echo Environment overrides:
    echo   CLAUDETRADE_BOT_MODE=paper^|live
    echo   CLAUDETRADE_PYTHON=C:\Path\To\python.exe
    echo   CLAUDETRADE_WATCHDOG_INTERVAL_SEC=1800
    exit /b 0
)
if not "%~1"=="" (
    if /I "%~1"=="live" set "BOT_MODE=live"
    if /I "%~1"=="--live" set "BOT_MODE=live"
    if /I "%~1"=="paper" set "BOT_MODE=paper"
    if /I "%~1"=="--paper" set "BOT_MODE=paper"
    if /I not "%~1"=="live" if /I not "%~1"=="--live" if /I not "%~1"=="paper" if /I not "%~1"=="--paper" (
        echo Unsupported mode argument "%~1". Use "paper" or "live".
        exit /b 2
    )
)

if /I not "%BOT_MODE%"=="live" if /I not "%BOT_MODE%"=="paper" (
    echo Unsupported BOT_MODE "%BOT_MODE%". Use "paper" or "live".
    exit /b 2
)

set "PYTHON_EXE=%CLAUDETRADE_PYTHON%"
if not defined PYTHON_EXE set "PYTHON_EXE=C:\Users\Unknown\anaconda3\envs\upbit\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "CHECK_INTERVAL_SECONDS=%CLAUDETRADE_WATCHDOG_INTERVAL_SEC%"
if not defined CHECK_INTERVAL_SECONDS set "CHECK_INTERVAL_SECONDS=1800"

set "GUARDIAN_SCRIPT=%ROOT%\tools\live_guardian.py"
if not exist "%GUARDIAN_SCRIPT%" (
    echo Guardian script not found: "%GUARDIAN_SCRIPT%"
    exit /b 2
)

echo [%date% %time%] ClaudeTrade watchdog starting.
echo Root     : %ROOT%
echo Mode     : %BOT_MODE%
echo Python   : %PYTHON_EXE%
echo Interval : %CHECK_INTERVAL_SECONDS% seconds
echo.

cd /d "%ROOT%"
rem --auto-fix is intentional for watchdog recovery: live_guardian may remove stale PID locks
rem and refresh expired KIS tokens before deciding whether the bot can start. It does not
rem change trading config, order sizing, broker truth, or PathB live gates.
"%PYTHON_EXE%" "%GUARDIAN_SCRIPT%" --mode "%BOT_MODE%" --watch --interval-sec "%CHECK_INTERVAL_SECONDS%" --ensure-bot --auto-fix --skip-dashboard
exit /b %ERRORLEVEL%
