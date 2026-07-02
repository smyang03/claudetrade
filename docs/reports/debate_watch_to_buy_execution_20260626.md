# 토론 리포트 — 관찰→매수 전환 마지막 실행 단계 (signal_fired·미매수) 2026-06-26

> 명제: "관찰→매수 전환의 마지막 실행 단계(signal_fired=1인데 미매수, US 18·KR 8)는 net에 기여하는 *고칠 수 있는* 누수다 — 특히 INVALID_PRICE 코드 비대칭·ready=1 고품질 건의 실행 마찰."
> 방식: PRO(고칠 누수)/CON(정당 게이트) + 사회자 코드 검증. READ-ONLY.

---

## 1. signal_fired=1·미매수 분해 (양측 일치)

| reason | uniq | forward_3d | 정당성 |
|---|---:|---:|---|
| qty_zero | US5·KR2 | +2.9 | **정당**(현금/1주가>예산) |
| order_size_too_small | KR3 | +15.9 | **정당**(min_order) |
| insufficient_cash | US1 | +14.8 | **정당**(자본) |
| DAILY_LOSS_LIMIT | US1·KR1 | +0.5/−7.9 | **정당**(손실한도) |
| MAX_DAILY_ENTRIES·GROSS_EXPOSURE | US3 | ~0 | **정당**(캡) |
| **permanent_order_reject·ANALYST_NEW_BUY_BLOCK** | US2 | **−9.95·−15.5** | **정당(loser 정확 차단)** |
| order_rejected | US3·KR2 | +3.3/−7.4 | 정당(브로커 거부) |
| **INVALID_PRICE** | **US3**(INTC·MRVL·APP) | +7.8 | **코드 비대칭(유일 결함)** |

→ **US 미매수 25행 중 22행, KR 11행 전부가 정당 게이트.** 일부(permanent_reject·ANALYST_NEW_BUY −9~−15%)는 loser를 정확히 차단(게이트가 제값).

## 2. 사회자 코드 검증 — INVALID_PRICE 비대칭 확정
`runtime/pathb_runtime.py` 진입 게이트만 fallback 결손:
```
1235:  _price_to_krw(float(signal.limit_price or signal.price or 0.0), ...)   ← fallback O
1592:  _price_to_krw(float(signal.price or 0.0), ...)                          ← fallback O
1734:  _price_to_krw(float(order_price or signal.price or 0.0), ...)           ← fallback O
4280:  _price_to_krw(signal.limit_price, market)                              ← 단독, fallback X
```
`safety_gate.py:122` `price_krw<=0 → INVALID_PRICE`. limit_price=0이고 signal.price 유효하면 **4280만 자기차단.** 코드 일관성 결함 **확정**.

## 3. 판정 — 명제 대체로 반대, 단 1개 코드 위생 인정

**판정: signal_fired·미매수는 "고칠 net 누수"가 아니다.**
- 압도적 다수(US 88%·KR 100%)가 정당 실행/리스크 게이트. 차단 해제 = 자본·캡 완화 = **직전 "net 음수에 스케일업=손실확대" 결론과 모순.**
- loser 게이트(permanent_reject·ANALYST_NEW_BUY)는 forward 음수 차단 = 제값.
- 유일한 비-lookahead 실자금 증거(실체결 74건)는 **net 음수**(KR −2.52%·US −0.98%) → 진입을 더 먹이는 건 −EV 증량.

**단 인정(net ROI 아님, 코드 위생):** `pathb_runtime.py:4280` `signal.limit_price` → `float(signal.limit_price or signal.price or 0.0)` 정합화. 다른 3개 호출부와 일관성 회복.

**왜 "net 누수"가 아니라 "위생"인가 (4중 약점):**
1. forward(+7.8%, MRVL +39%)는 **lookahead** — 차단 후 runup이지 진입가 보장 아님.
2. **N=3 uniq** (MRVL·APP 같은날 중복).
3. **근본원인 limit_price=0** — 진입가 자체가 부재. fallback이 사이징은 통과시켜도 주문가(`place_order` ~4407) 0이면 **체결가 불확실.**
4. 실체결 net 음수.

## 4. 권고 (코드는 운영자 승인 후)
1. **INVALID_PRICE: 4280 fallback 정합화** — **코드 일관성 근거로만** 진행, "net 누수 복구"로 포장 금지. 단 limit_price=0 **근본원인**(시그널/플랜 가격 부재)을 함께 점검하고, fallback 시 주문가도 동일 소스로 통일(사이징만 통과·체결가 0 방지). 적용 후 INVALID_PRICE reason 재발 0 확인.
2. **order_rejected 동일종목-일 dedup/backoff** — ORCL 2026-05-29 3연타(20분 간격). 재시도 위생(자원), net 무관.
3. **그 외 미매수: 건드리지 말 것** — 현금·min_order·캡·손실한도·loser·재진입 전부 정당.

## 5. 한 줄 결론
관찰→매수 마지막 실행 단계는 **net 레버가 아니다** — 막힌 건의 88%+는 정당 게이트(일부는 loser를 정확히 차단)이고, 실체결 net이 음수라 차단 해제는 −EV 증량이다. 유일한 결함은 `pathb:4280` fallback 비대칭 1개인데, 그조차 lookahead·N=3·limit_price=0이라 **net 누수가 아닌 코드 위생 수정**이며, 고치더라도 근본원인(가격 부재) 점검이 선행돼야 한다.
