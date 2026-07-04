# execution 레버 3축 탐색 — 분봉 replay 검증 (2026-07-04)

운영자 요청: "기존 DB 결과는 −인데 데이터로 저런 축(capture)을 더 찾자, 3명 토론+툴+검증, 이번 발견 포함 리포트." 3에이전트가 execution/capture 축을 각자 분봉 양방향 replay로 파고들고, **OOS(5월 vs 6월)+outlier(top3 제거) stress-test 필수** 규율로 거짓양성을 걸렀다. read-only.

## 배경 — 왜 execution 축인가
수익=fat-tail 러너(+4%↑ 23건=양수 P&L 57%)가 전부. selection 죽음(AUC0.49). 유일 후보 축=출구/capture. 세션 중 "승자 peak-trail(trail3)"이 book을 −86→+13.6으로 뒤집는 듯 보여 3축으로 확장 검증.

## ★핵심 판정: 새 robust 수익 레버 없음 (discipline이 3개 거짓양성 적발)

| 축 | 후보 | stress-test | 판정 |
|---|---|---|---|
| **peak-trail (세션 발견)** | trail3가 book +13.6 | **top3 제거 → −54.7, 양월 음전.** 양수 전체가 3건(TENB/MRVL/CRDO) | **outlier 착시.** 흑자엔진 아님 (단 cap보다는 나음=손실축소) |
| **손실측 타이밍 (축1)** | −3% 넓은 손절 +0.25 | **5월 부호역전**+오버나잇 갭 realism 결함 | **−2% 최적 재확인.** 타이트/시간/무손절 양월 열위 |
| **carry/홀드 (축2)** | 오버나잇 carry | blanket=전형승자 중앙 −1.14/거래(66→47%승), **top3 제거 음전** | **blanket 무효.** proven-runner pocket n=5=표본부족 |
| **peak-trail 파라미터+조건부 (축3)** | act5/give2.5 최적셀, tape/zone 조건부 | **모든 셀·조건 top3 제거 시 음전.** 조건부=러너 정렬 착시 | **현행 유지.** 파라미터 변경 근거 없음 |

## 정직한 자기정정 (세션 발견)
"peak-trail이 book을 흑자로"는 **틀렸다.** trail3의 양수(+13.6)는 3건 러너가 전부(top3 제거 −54.7, 6월 −63.1). hold_close에 적용한 outlier 검증을 trail3엔 빠뜨린 결과. **단 trail3는 cap(+4 고정목표)보다는 outlier 빼도 나음**(6월 −63.1 vs cap −102.2) = "승자 안 자르기"는 진짜 손실축소지만 흑자엔진 아님, 그리고 **이미 배포됨**(US_LADDER_AB_MODE=enforce act4/give2).

## 구조적 진실 (모든 축이 수렴)
1. **수익 = 소수 예측불가 fat-tail 러너**(TENB/MRVL/CRDO +17~28%). 못 고르고(AUC0.49), 어떤 출구정책도 이들을 *체계적으로* 더 잡지 못함(모든 "개선"이 이 3건을 다르게 정렬한 착시).
2. **현 시스템 출구는 이미 near-optimal**: ladder trail(러너 태움, 배포됨)·−2% 손절(최적)·red-tape(진입 방어). execution에 남은 싼 레버 없음.
3. **outlier 빼고 살아남는 유일 신호 = red-tape(진입 방어)** — 손실축소지 흑자엔진 아님. 이미 구현.

## 유일하게 살아있는 얇은 실 = would_carry proven-runner carry (n=5)
강도게이트 carry(mfe≥4% AND D1마감 gross≥3%)만 익일 상승 방향 일치(중앙 forward +4.23, 3/5 상승). **but n=5 ≪ 킬기준 25 = 검증불가·폐기불가.** ★오늘 복구한 would_carry shadow(A1)가 정확히 이 pocket을 세션마다 누적 → n≥25 도달 후 재판정. 이게 execution에서 유일하게 forward를 기다리는 실.

## 도구 (read-only, 커밋/스크래치)
- `tools/capture_target_replay.py`(da14728) — 목표/trail 분봉 replay.
- `tools/loss_exit_replay.py`(축1) — 손실측 정책 replay.
- `tools/peak_trail_sweep.py`(축3) — act×give 스윕+조건부.
- carry_replay(축2, 스크래치).

## 결론
**3축 exhaustive 탐색 결과 새 robust 수익 레버 없음.** 운영자 규율(outlier·OOS stress-test)이 **3개 거짓양성을 적발**(넓은손절·blanket carry·peak-trail-as-profit) = discipline이 실자금을 지킴. 배포된 출구(ladder trail)는 이미 옳고, −2%도 최적. 흑자 천장은 구조적: fat-tail(예측불가)+비용(FX)+국면. execution에서 남은 건 would_carry pocket 누적(얇음)뿐. **수익 레버는 execution이 아니라 비용(운영자 FX)·국면 분산에 있다.**
