@echo off
setlocal

rem === Change these two values ===
set "PROCESS_NAME=YourProgram.exe"
set "PROGRAM_PATH=C:\Path\To\YourProgram.exe"

rem 30 minutes = 1800 seconds
set "CHECK_INTERVAL_SECONDS=1800"

:check_loop
tasklist /FI "IMAGENAME eq %PROCESS_NAME%" 2>NUL | find /I "%PROCESS_NAME%" >NUL

if errorlevel 1 (
    echo [%date% %time%] %PROCESS_NAME% is not running. Starting program...
    start "" "%PROGRAM_PATH%"
) else (
    echo [%date% %time%] %PROCESS_NAME% is already running.
)

timeout /t %CHECK_INTERVAL_SECONDS% /nobreak >NUL
goto check_loop
