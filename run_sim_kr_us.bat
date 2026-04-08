@echo off
setlocal

set "ROOT=E:\code\claudetrade"
set "START_DATE=2022-01-01"
set "TOP_N=50"
set "ENGINE=both"

echo ============================================================
echo ClaudeTrade KR/US simulation runner
echo ROOT       : %ROOT%
echo START_DATE : %START_DATE%
echo ENGINE     : %ENGINE%
echo TOP_N      : %TOP_N%
echo RESULT DIR : %ROOT%\data\backtest
echo ============================================================
echo.

start "KR Simulation" powershell -NoExit -Command "cd /d '%ROOT%'; python -m phase1_trainer.sim_runner --market KR --engine %ENGINE% --start %START_DATE% --top %TOP_N%"
start "US Simulation" powershell -NoExit -Command "cd /d '%ROOT%'; python -m phase1_trainer.sim_runner --market US --engine %ENGINE% --start %START_DATE% --top %TOP_N%"

echo KR/US simulation windows started.
echo Check result files under:
echo %ROOT%\data\backtest
echo.
pause

