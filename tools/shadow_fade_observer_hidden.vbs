' shadow_fade_observer_hidden.vbs — fade 진입 shadow 관측기를 창 없이(hidden) 실행한다.
'
' 배경: fade 신호는 오프라인 재현(2026-07-13~24, 10세션)에서 유일하게 양수였고
' 플라시보 대조까지 통과했지만(KR +0.687%p p=0.0146 / US +1.374%p p=0.0004),
' 현재 라이브 정체성(즉시매수·눌림 폐기)과 충돌해 enforce하지 않는다.
' enforce 판단의 전제가 "forward 표본 누적"이므로 매 세션 관측이 쌓여야 한다.
'
' 왜 라이브 코드가 아니라 스케줄 작업인가:
'   shadow 관측은 주문 경로에 있을 이유가 없고, 실패해도 봇에 영향이 없어야 한다.
'   봇의 session_close에 붙이면 관측 실패가 마감 경로의 위험이 된다.
'
' watchdog_hidden.vbs와 같은 규약이다 — wscript.exe로 호출하면 콘솔이 뜨지 않고
' Run의 window-style 0이 python을 숨김으로 기동한다.
'   Run(command, 0, True): 0 = 숨김 창, True = 완료까지 대기(중복 실행 방지).
'
' 기록은 (session_date|market|ticker) 키 기준 멱등이라 창이 겹쳐도 중복되지 않는다.
' --since-days 10: 최근 10일만 재스캔한다(고정 날짜면 매 실행이 전체 로그를 훑는다).
Set sh = CreateObject("WScript.Shell")
sh.Run "C:\Users\Unknown\anaconda3\envs\upbit\python.exe ""E:\code\claudetrade\tools\shadow_fade_entry_observer.py"" --since-days 10", 0, True
