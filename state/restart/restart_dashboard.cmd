@echo off
setlocal
timeout /t 2 /nobreak >nul
cd /d "E:\code\claudetrade\dashboard"
start "" "C:\Users\Unknown\anaconda3\python.exe" "E:\code\claudetrade\dashboard\dashboard_server.py"
endlocal
