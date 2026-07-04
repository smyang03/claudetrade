# 토론 판정 — 매수/진입 살리기: 게이트 완화가 답인가

날짜: 2026-06-30 · 사회자 판정 · read-only(주문/코드/config 무변경)
명제: "매수 미체결의 주된 통제가능 원인은 게이트/제약이며, 완화·수정하면 매수가 살아나 net이 개선된다(게이트 과보수)."
계기: 운영자 — "강세장인데 매수 0, 매수가 있어야 출구가 먹힌다."

## 계측(확정)
- 클로드 buy_zone은 reference 대비 US+0.23%/KR+0.00% — **깊은 눌림 요구 아님**(내 초기 "5~7% 아래" 진단 오류, cancel_if_open_above 오독).
- 미체결 190건 cancel사유: REQUIRE_TRADE_READY_BLOCK 41·EXPIRED 37·INVALID_PRICE 29·SAME_DAY_REENTRY 28·FAST_FILL 18·ALREADY_HOLDING 10.

## 사회자 직접검증 (판정 가른 3건)
1. **"filled 99%"는 체결 아닌 측정완료** — followup 경과 중앙 32분이나 23% 3h초과(최대 89h). followup_status는 시세 채움(커버리지)이지 브로커 체결 아님. P1·P5 "99% 결국 체결" 오독. 재진입 근사는 zone_reentered 89%.
2. **churn은 2일 버스트** — REQUIRE_TRADE_READY 41건 전량 6/26(32)+6/29(9), 6/26 단일 78%. 상시 봉쇄 아님.
3. **신뢰행(US 137, post_open_history) 미체결 후 균형** — mfe중앙+0.48%/mae중앙-0.36%, mfe≥1% 38 vs mae≤-1% 27. 상방 약간 우세하나 대칭. mfe는 실현 아닌 잠재고점(마찰+leak 빼면 cap-widen -1.4%).

## 6패널 요지
- P1 완화: 게이트 57% 차단, 신뢰행 mfe+0.48%. SLS 6/26 하루 10번 차단. 단 churn은 intraday·filled는 shadow추정 자인.
- P2 옹호: 실체결 74건 net음수(KR-2.52/US-0.98), cap-widen-1.4%. "완화 켜되 KR/US분리+log-only shadow 선행".
- P3 INVALID_PRICE: validate아닌 시세공백(current NULL 29/29), 6월 0건 자가소멸. **레버 아님**(지연일 뿐).
- P4 SAME_DAY: 손절사유 부호일률처리(노이즈0.3%도 -5%역행과 동일 전면차단, 쿨다운 없음). 막힌28건 mfe+0.46박빙. "사유분류 shadow"만, 당일재매수 손실편향 인정.
- P5 착시: 진입 개장후1~3h집중→세션초 positions:0 정상. 6월US 18거래일 모두 진입. "입구 아닌 출구가 레버". up131>down59는 약해서 안 기댐 자인.
- P6 측정회의: filled=커버리지(체결아님), 단일샘플46행=중앙17h오염, REQUIRE_TRADE_READY 2일집중. 신뢰행 142만 써라.

## 판정: 명제 **기각** (완화는 매수 살리지만 net 개선 아님)
- 매수 미체결의 주범이 게이트인 건 사실. 그러나 **"완화하면 net 개선"은 데이터가 반대**: cap-widen -1.4%, 실체결 net음수, 미체결 후 추이 균형(~0, 마찰빼면 음). 완화로 늘리는 진입은 손실편향.
- **"매수 0/가뭄"은 상당부분 착시**: churn 2일 버스트, filled 99%는 측정오독, 진입 월100건+, positions:0은 세션초 정상. 영구봉쇄 없음(SAME_DAY 당일한정·INVALID 자가소멸·REQUIRE_TRADE_READY intraday).
- **입구(진입량)는 레버가 아니다 — 출구가 레버. 재확인.**

## 통제가능 레버 (알파 아니라 효율/위생, shadow)
| 항목 | 성격 | 행동 |
|---|---|---|
| REQUIRE_TRADE_READY churn | 같은종목 intraday 재등록→재차단 반복(SLS 6/26 10번)=계산낭비·로그오염 | **log-only shadow 전환** → churn 제거 + 차단건 모수복구(부호 재검증). net 알파 아님 |
| SAME_DAY 부호일률처리 | 노이즈 0.3% 손절도 -5% 역행과 동일 전면차단(쿨다운 없음) | **역행/노이즈 사유분류 shadow** → 노이즈손절만 쿨다운. enforce 아닌 측정 |

## 기각/주의
- "게이트 완화로 매수 늘려 수익" ❌ (cap-widen -1.4%·실체결 net음수).
- "매수가 막혀 0" ❌ (착시 — 월100건+·churn 2일·세션초).
- KR 신뢰행 5건뿐 → KR 결론 불가. 전부 6월말 집중(6/26 39건) 단일국면 → shadow 누적 후 재측정.
- 운영자 파라미터(REQUIRE_TRADE_READY·SAME_DAY) 무단변경 금지.
