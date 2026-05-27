@echo off
set PROJECT_DIR=E:\code\claudetrade
set CONDA_ENV=upbit

wt ^
  new-tab --title "trading_bot" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] conda activate %CONDA_ENV% ^&^& python trading_bot.py --live && conda activate %CONDA_ENV% && python trading_bot.py --live" ^
  ; new-tab --title "dashboard" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] conda activate %CONDA_ENV% ^&^& python dashboard\dashboard_server.py && conda activate %CONDA_ENV% && python dashboard\dashboard_server.py" ^
  ; new-tab --title "live_guardian" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] conda activate %CONDA_ENV% ^&^& python tools\live_guardian.py --mode live --watch --interval-sec 300 --telegram-alert && conda activate %CONDA_ENV% && python tools\live_guardian.py --mode live --watch --interval-sec 300 --telegram-alert" ^
  ; new-tab --title "preopen_scheduler" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] conda activate %CONDA_ENV% ^&^& python tools\preopen_scheduler.py --mode live --markets KR,US --loop --interval-sec 60 && conda activate %CONDA_ENV% && python tools\preopen_scheduler.py --mode live --markets KR,US --loop" ^
  ; new-tab --title "counterfactual_pipeline" cmd /k "cd /d %PROJECT_DIR% && echo [RUN] conda activate %CONDA_ENV% ^&^& python tools\run_counterfactual_pipeline.py --phase due --market KR,US --loop --interval-sec 300 --json && conda activate %CONDA_ENV% && python tools\run_counterfactual_pipeline.py --phase due --market KR,US --loop --interval-sec 300 --json"
