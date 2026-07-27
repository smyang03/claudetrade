' shadow_observers_hidden.vbs — 관측기들을 창 없이(hidden) 순차 실행한다.
'
' 대상 (둘 다 라이브 미개입, read-only + shadow 원장 append)
'   ① shadow_fade_entry_observer  fade 진입 신호를 대조군과 쌍으로 누적
'   ② judge_budget_observer       judge 예산 사용을 공급/호출/플랜 3숫자로 기록
'
' ②가 왜 필요한가: 지금은 "매수 0건"이라는 결과만 보이고 그 원인이
'   ㉠데이터 파이프라인 장애 ㉡큐/게이트 문제 ㉢후보 질 중 무엇인지 구분되지 않는다.
'   세션마다 공급·호출·플랜 세 숫자가 남아야 게이트를 넣은 뒤 효과를 잴 수 있다.
'   기준선 없이 게이트부터 넣으면 무엇이 효과를 냈는지 영원히 모른다.
'
' watchdog_hidden.vbs와 같은 규약 — wscript로 부르면 콘솔이 뜨지 않고,
' Run의 window-style 0이 python을 숨김으로 기동한다.
'   Run(command, 0, True): 0 = 숨김 창, True = 완료까지 대기(순차 실행 보장).
'
' 두 스크립트 모두 키 기준 멱등이라 창이 겹치거나 재실행돼도 중복되지 않는다.
Dim sh, py, base
Set sh = CreateObject("WScript.Shell")
py = "C:\Users\Unknown\anaconda3\envs\upbit\python.exe"
base = "E:\code\claudetrade\tools\"

sh.Run py & " """ & base & "shadow_fade_entry_observer.py"" --since-days 10", 0, True
sh.Run py & " """ & base & "judge_budget_observer.py"" --since-days 10", 0, True
