# 전략·청산·shadow 종합 분석 (2026-06-20)

> 세션 범위: Codex shadow trader 측정(US/KR) → 1주 매수상한 진단 → 청산 capture 실측(Phase 0) → 전략별 성과 분해 → 유지/개선/버림 판정 → 신규 전략 분석.
> 모든 수치 gross 또는 명시된 net. 소표본·yfinance 의존 구간은 방향성 판단용. 라이브 매수/매도/원장 무수정(read-only 분석).

---

## 0. 한 줄 결론

- **selection(어떤 종목 살까)은 레버가 아니다 — 5번 측정 + Codex shadow로 6번째 확정.**
- **청산(capture)도 쉬운 답이 없다** — 봇 청산 판단은 베타 차감 후 무차익, 진짜 누수는 `LOSS_CAP`(진입이 틀린 종목), 수익 경로(claude_price)는 이미 잘 작동.
- **버릴 것은 명확하다(gap_pullback 등). 신규 전략 추가는 권장하지 않는다(욕심·무엣지 재베팅).**
- 가장 큰 단일 레버는 코드 밖(거래비용 우대)과 **KR claude_price 집중**이다.

---

## 1. Codex Shadow Trader 측정 결과

운영자 의도: "봇 시스템이 아니라 AI(Claude/Codex)를 트레이더로 직접 붙이면 봇보다 나은가"를 shadow(주문/config/broker truth 무영향)로 실측. QQQ/지수 대비 초과수익(excess)으로 채점.

### 1-1. US (2026-06-18 세션, 마감 결산)

| 구분 | avg excess vs QQQ | 개별 (excess / MFE→마감) |
|---|---|---|
| BUY (5) | **+0.54pp** (분산 극심, 무의미) | BE +4.76 / SMCI +1.38 / AMZN +0.36 / KLAC 0.00 / **MRVL -3.80** |
| 독립 AVOID (2) | -1.02pp | IREN -2.35(적중) / ACN +0.31(실패) |
| 봇 실주문(관찰) | — | **DIOD MFE+1.68→-1.47**(capture 반납), AAL -0.54 |

**해석**: ① BUE/MRVL이 평균을 다 만든 노이즈. ② **MRVL = 봇 PULLBACK_WAIT를 "사자"로 override → -3.80 대패**(내 진입 직관이 봇 신중함보다 못함, 6/17 시뮬과 동일). ③ **DIOD = capture 누수 생중계**(+1.68 찍고 손실 마감).

### 1-2. KR (2026-06-19 세션, 마감 결산)

폭락장 — KOSPI -2.84% / KOSDAQ -2.73% (장중 -4.5%에서 종가 회복).

| 구분 | avg excess vs KOSPI | 비고 |
|---|---|---|
| BUY (5) | **-1.85pp** | 067290 +6.1%(MFE+16→반납) 혼자 떠받침, 나머지 -6~-9%대 |
| AVOID (5) | **+1.78pp** (회피 실패) | 052710 +6.9% 막판 급반등 |

**해석**: ① codex(AI) 변별 **역방향**(AVOID > BUY). ② **봇 0매수 = 폭락 손실 0 = 오늘 최선** — 봇 보수 게이트가 명백히 승. ③ 067290도 MFE 반납 = capture 누수 KR 동일.

### 1-3. 종합

- "AI가 봇보다 낫나" → **US 아침 약우위(BE), KR 폭락장 무너짐. 일관된 우위 없음.** selection 레버 없음 재확인.
- 1일·종목 비겹침·분산 극심 → 표본 누적(1~2주 30픽) 전 결론 불가.
- 인프라: codex 전용 러너(US/KR) 5분 가격추적 + 내가 추가한 excess 백필 도구(`codex_shadow_excess_report.py`, `codex_shadow_kr_excess_report.py`).

---

## 2. 1주 매수 상한 진단 (버그 없음 — 오진 정정)

**공식**: `max_entry = max(고정주문 50만, min(절대 100만, equity×30%))` (동적, 잔고 반영, config 3파라미터)

| 시장 | 총자산 | ×30% | **1주 상한** |
|---|---|---|---|
| US | ~526만 | 158만 | **100만원** (절대상한) |
| KR | ~102만 | 31만 | **50만원** (잔고 작아 floor) |

- **`max_entry_krw=10000/1000`은 버그 아님** — `cut -c1-150` 로그 잘림으로 `=1000000`이 `=10000`으로 보인 내 오독. 실제 grep: US 100만 / KR 50만 정상.
- 동적 계산·잔고 반영·config·safety_gate 연결 **모두 정상 동작**. 손댈 것 없음.
- KR 50만 제약은 정상 동작(잔고 102만). 늘리려면 잔고↑ 또는 `MAX_ACCOUNT_PCT` 조정(운영자 결정).
- selection 단계엔 affordability 필터 부재 → 고가주가 후보 슬롯 점유 가능(실재하나 빈도 미확인, 오늘 KR 0매수의 직접 원인은 아님 = trade_ready 승격 0이 원인).

---

## 3. 청산 Capture 실측 (Phase 0, read-only)

### 3-1. capture_net_review (수수료 왕복 0.5% 반영, closed 268건)

| 시장 | n | gross/건 | **net/건** | net capture (실제 MFE 기준) |
|---|---|---|---|---|
| US | 210 | +0.33% | **-0.09%** (-19%p) | **0.113** (MFE +2.97%의 11%만 실현 = 89% 반납) |
| KR | 58 | -0.47% | **-0.86%** (-50%p) | **-0.175** |

### 3-2. US 청산경로별 net

| 경로 | n | net합 | capture | 판정 |
|---|---|---|---|---|
| CLAUDE_PRICE_TARGET | 19 | +93.7%p | 51% | ✅ 수익핵심 |
| CLAUDE_SELL | 10 | +23.7%p | 74% | ✅ |
| PRE_CLOSE | 35 | +30.7%p | 23% | ✅ |
| **LOSS_CAP** | **50** | **-131.2%p** | -80% | **최대 누수** |
| USER_MANUAL/HARD_STOP/WEAK_MFE | — | 각 -9~12%p | 음수 | 손실경로 |

### 3-3. exit_decision_scorer (봇 청산 판단 forward 정오)

- HOLD n=1340 gain60 -4.29%, SELL n=269 +5.47% — **단 UNKNOWN stage 82건이 ±100%p 오염값**(delisting/split).
- **오염 제외 시 봇 청산 판단은 베타 차감 후 ±2%p 무차익** (HOLD/INTRADAY -1.96%p 약한 반납).

### 3-4. 결론

1. **봇 청산 판단(=Claude hold advisor)은 무차익** → AI overlay(또 다른 Claude)로 이길 여지 작음 = "Claude vs Claude" 함정.
2. **진짜 누수 = LOSS_CAP**(진입이 틀린 종목). 줄이는 길 = selection(무엣지) 또는 조기절단(weak_mfe 6/16 실패)으로 회귀 = 막힌 길.
3. **수익 경로(TARGET/SELL/PRE_CLOSE)는 이미 잘 작동** — 보존 대상.
4. **청산 overlay 신규 빌드는 EV 낮음** → 빌드 보류 결정.

---

## 4. 전략별 성과 (v2_learning_performance, gross·소표본)

| 전략 | US (n / sum) | KR (n / sum) |
|---|---|---|
| **claude_price (PathB)** | 164 / **+61.6** | 19 / **+19.2** |
| momentum | 13 / +9.4 | 11 / **-21.5** |
| opening_range_pullback | 7 / +11.8 | 6 / -7.9 |
| gap_pullback | 24 / **-9.7** | 20 / **-13.9** |
| mean_reversion / RECOVERY_MICRO | 각 1 / 음수 | 1 / -0.7 |

**핵심 분해**:
- **claude_price(PathB)는 KR·US 둘 다 gross+** — KR에서 유일하게 작동.
- **KR 손실의 정체 = Path A 전략신호**(momentum -21.5 + gap_pullback -13.9 + orp -7.9 = -43.3). KR PathB가 아님.
- **gap_pullback은 US·KR 둘 다 손실** — 표본 큰데 양쪽 - = 전략 설계 자체 무효(국면·KR 탓 아님).

---

## 5. 유지 / 개선 / 버림 판정

| 전략 | US | KR | 버림 우선순위 |
|---|---|---|---|
| claude_price | **유지·강화** | **유지·강화** | — |
| momentum | 유지(보호) | 게이트 개선 또는 버림 | 2 (KR) |
| opening_range_pullback | 유지 | 축소·관찰 | 3 (KR) |
| **gap_pullback** | **버림·축소** | **버림·축소** | **1 (양쪽)** |
| mean_reversion | 관찰 | 관찰 | — (Risk-Off 예외) |

- **가장 깨끗한 단일 결정 = gap_pullback 정리** (US·KR, 표본 충분·양쪽 손실 = 변명 불가 무효).
- KR momentum: 데이터상 버림이나 운영자 "KR 개선" 의지 존중 → **정량 게이트**("향후 15건/N거래일 net 손익분기 미달 시 중단").
- **KR 개선의 정확한 경로 = 신호 튜닝이 아니라 비중 재배치**: KR claude_price(되는 것) 집중 + Path A 손실신호 축소.

---

## 6. 신규 전략 분석 (요청)

| 후보 | 메커니즘 | 엣지 정합 | 빌드 부담 | 판정 |
|---|---|---|---|---|
| **volatility_breakout** | 변동성 돌파 | US 과열추격과 정합 | **코드 있음(live OFF)** | **shadow 검증 (유일 권장)** |
| **tail_capture** | 러너 capture | capture 레버 정합 | **이미 shadow 빌드됨** | **검증 우선(신규 아님)** |
| gap-and-go | 갭 상방 추격 | 모멘텀 정합 | 신규 빌드 | 낮음(gap_pullback 버리며 사촌 추가 모순) |
| PEAD 전략화 | 어닝 서프라이즈 | — | 정책 위반 | **금지(input-only)** |
| pairs/통계차익 | 페어 회귀 | 무관 | 큼 | X |

**판단**: 신규 전략 빌드는 **권장하지 않음** — selection 무엣지 5번 + "전략추가보다 입력/실행 품질" 원칙 + 욕심 경계와 충돌. **합리적 "추가"는 새로 짓는 게 아니라 이미 빌드된 미검증 자산(volatility_breakout 코드 / tail_capture shadow)을 shadow 검증하는 것.** 버린 자리는 비워둬도 무방(claude_price가 커버).

---

## 7. 데이터 품질 / 침묵 배선 발견 (별도 정합성 대상)

- `decisions.db.strategy_used` 대부분 **NULL** (전략 attribution 공백, v2_learning_performance로 커버됨)
- `exit_decision_scorer` **UNKNOWN stage 82건 ±100%p 오염** (delisting/split)
- `state/live_status_US.json`·`live_status_KR.json` **4월 17~18일자 stale** (봇은 정상 가동, 갱신 배선 끊김)
- **KR yfinance split 오염** (삼성 362,500 / KOSPI 8,864 등 — 절대값 불신, 등락률만 robust)

---

## 8. 종합 권고 (실행 순서)

1. **gap_pullback 축소/중단** (US+KR, 가장 확실) — enable 토글 위치·US 영향 검증 후, 오염 검토 동반
2. **KR momentum 정량 게이트** (개선 의지 존중 + 중단 조건 박기)
3. **KR claude_price 집중** (되는 것 키우기 = KR 개선의 본질)
4. **claude_price·US 수익경로 보존** (건드리면 손해, 보호영역)
5. **신규 빌드 0** — volatility_breakout·tail_capture만 shadow 검증 옵션
6. 코드 밖 레버: **거래비용 우대 협상** (US gross+를 net+로 전환, 운영자 영역)

### 오염/분리 원칙 (모든 실행 시)
- KR/US 전략 **분리** — KR 손볼 때 US 수익경로(momentum/orp/claude_price) **코드 비접촉**
- claude_price = **보호영역**, sizing/청산경로 변경 시 MD 위반 기록
- 전략 토글 = 운영자 확인 필수 파라미터, 변경 후 preflight + 회귀 + 매수/매도/broker truth 오염 전수 검토
