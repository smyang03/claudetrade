' shadow_observers_hidden.vbs — 관측기들을 창 없이(hidden) 순차 실행한다.
'
' 대상 (전부 라이브 미개입, read-only + shadow 원장 append)
'   ① shadow_fade_entry_observer  fade 진입 신호를 대조군과 쌍으로 누적
'   ② judge_budget_observer       judge 예산 사용을 공급/호출/플랜 3숫자로 기록
'   ③ observe_tail_risk_axes      꼬리 위험 축 10종을 신호일 값으로 박제(08-30)
'   ④ observe_pick_rules          픽 규칙 5종의 세션당 픽을 결정 시점 값으로 박제(09-01)
'
' ④가 왜 필요한가: 모델 제거(09-01) 후 라이브 픽 순서는 dvol_desc(잠정)인데,
'   픽 시뮬에서 5규칙 전부 통계적으로 구별 불가였다. 어느 순서가 라이브든
'   나머지 규칙들의 픽을 병행 박제해야 forward 30건 시점에 "매수 코호트끼리"
'   재판정할 수 있다. candidate_pool_all은 덮어쓰기라 결정 시점 값 보존이 필요.
'
' ③이 왜 필요한가: 08-30 검정에서 꼬리 기준 6축이 3기준을 통과했으나 클러스터
'   t가 검정력 미달이라 판정을 못 냈다. 판정에 필요한 것은 표본인데 현행 계약
'   실거래 정산은 0건이다. 30건을 그냥 기다리면 그 시점에도 재료가 없다.
'   축 값은 사후 계산도 가능하지만 가격 CSV가 소급 조정되면 과거 값이 바뀌므로
'   진입 시점 값을 박제해야 no-lookahead를 사후에 증명할 수 있다.
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
sh.Run py & " """ & base & "observe_tail_risk_axes.py""", 0, True
sh.Run py & " """ & base & "observe_pick_rules.py""", 0, True
' ⑤ virtual_books — 가상 운용 전환(09-01 운영자 결정)의 본체. 다전략 가상 북을
'   매일 진입·정산·MTM한다. 전부 [VIRTUAL]이며 실주문 무접촉.
sh.Run py & " """ & base & "virtual_books.py"" run", 0, True
