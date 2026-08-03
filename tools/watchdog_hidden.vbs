' watchdog_hidden.vbs — claudetrade live-stack watchdog을 창 없이(hidden) 실행한다.
'
' 배경: 스케줄 태스크 claudetrade_live_stack_watchdog은 5분 주기로 powershell.exe를
' InteractiveToken으로 직접 띄웠다. 그 결과 5분마다 powershell 콘솔 창이 화면에 깜빡였다.
' watchdog은 죽은 역할만 되살리는 점검이라 화면 표시가 전혀 필요 없다.
'
' 이 래퍼를 wscript.exe로 호출하면 콘솔이 뜨지 않고, 아래 Run이 powershell을
' window-style 0(숨김)으로 기동한다. 실행 컨텍스트(현재 대화형 세션)는 그대로라
' 스택이 도는 방식은 기존과 동일하고 깜빡임만 사라진다.
'   Run(command, 0, False): 0 = 숨김 창, False = 완료를 기다리지 않고 즉시 반환.
'
' 2026-08-03 관측성 수정: 기존에는 출력을 전부 버려서, 기동이 반복 실패해도(예:
' 08-01 월경계·08-02 BLOCK_START) 아무 흔적이 없었다. 이제 매 틱을 타임스탬프와
' 함께 logs\runtime\watchdog.log에 append한다. 창 없는 동작은 그대로다.
Set sh = CreateObject("WScript.Shell")
logCmd = "cmd.exe /c ""(echo [watchdog %date% %time%] tick & powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""E:\code\claudetrade\tools\start_live_stack_headless.ps1"") >> ""E:\code\claudetrade\logs\runtime\watchdog.log"" 2>&1"""
sh.Run logCmd, 0, False
