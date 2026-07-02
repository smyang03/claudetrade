# 코드 위생·오염 전수 감사 + 토론 보고 (2026-06-26)

> 운영자 지시: 사용 중인 모든 기능의 코드 위생·오염 재점검 + 분석에 써야 하는데 안 쓰던 도구 포함 리스트업 → 토론 → 보고.
> 방식: 4개 병렬 감사(측정/라벨/라이브코드/도구) → 사회자 직접 검증(피벗) → 실재 vs 코스메틱 vs 가드 판정. READ-ONLY.

---

## 0. 사회자 검증 — 가장 큰 오염 (이전 판정 흔듦)

**CLOSED_USER_MANUAL 34건의 진짜 사유(event store `raw_reason` 복원):**
| raw_reason | N | net합 |
|---|---:|---:|
| **intraday_review_sell** (Claude 장중 매도 결정) | **33** | **−44.6%p** |
| pre_session_sell | 1 | −2.1%p |
| (진짜 운영자 수동매도) | **0** | — |

→ "USER_MANUAL −46.7%p"는 **100% Claude 자신의 청산이 오라벨된 것.** 근본: `v2_lifecycle_runtime.py:58` default=USER_MANUAL + `sync_v2_learning_performance.py:947`이 truth(`raw_reason`, event store에 보존됨)를 **안 읽음**.

**함의 (토론 #3 정정):** hold advisor 토론(#3)은 "매도 개입 net 기여"를 profit_guard 익절(`hold_advisor_exit_outcome` N=8 양수)로만 판정했다. 그런데 **더 큰 Claude 매도 경로 intraday_review_sell(N=33, net −44.6%p, avg −1.35%)가 USER_MANUAL에 숨어 안 보였다.** → "Claude 매도 개입 net 기여"는 **재평가 필요**(profit_guard 익절은 양수지만, 장중리뷰 매도는 음수 — 단 후자가 손실 컷이면 정당일 수 있어 counterfactual 필요).

---

## 1. 오염 체크리스트 (4감사 통합, 우선순위순)

### P0 — 결론/진실을 실제 왜곡, 수정 가치 큼
| # | 항목 | 위치 | 오염 | 판정 |
|---|---|---|---|---|
| 1 | **USER_MANUAL = Claude 청산 오라벨** | `v2_lifecycle_runtime.py:58` default + `sync:947` raw_reason 무시 | Claude 장중매도 33건(−44.6%p)이 "수동"으로 위장 → 매도경로 성과 측정불가, #3 판정 불완전 | **실재 ★최우선** |
| 2 | **net 헤드라인 ≠ 학습 모집단** | `capture_net_review`(311행 전체) vs `full_profitability_review`(learning_allowed=1, 4행) | 두 리뷰가 다른 모집단 → net 결론 불일치. 헤드라인이 SUSPECT 129+LEGACY 100 풀에 좌우 | **실재** |

### P1 — 라이브/측정 오염, 신중 수정
| # | 항목 | 위치 | 오염 | 판정 |
|---|---|---|---|---|
| 3 | **STALE_MARKET_DATA 게이트 데드** | `pathb_runtime.py:4311` `last_market_data_at=now()` 항상 | 시세 신선도 차단이 영구 no-op → PathB 진입에 신선도 검증 없음 | **실재(나이브수정 위험)** |
| 4 | **타임존 UTC/KST naive 혼합** | `trading_bot.py:5725·6134`, `candidate_actions.py:158` | Claude "Z"(UTC) expiry를 KST와 비교 → 만료/grace ±9h 왜곡 | **실재** |
| 5 | **US FX 누락 (내가 만든 도구)** | `tools/improvement_net_monitor.py:37` `_net_of` | backfilled_fee_only(FX 미반영)를 그대로 net → US net 체계적 과대. capture_net_review와 다른 숫자 | **실재(이번세션 산물)** |
| 6 | **usd_krw 기본값 3종 혼재** | `_price_to_krw`(raw, `trading_bot.py:24011`) vs `_usd_krw`(1350, `pathb:12718`) vs `or 0` | 같은 KRW 변환에 1350/0/raw 섞임 → US sizing/net 오염 | **실재(일부 가드)** |
| 7 | **selection_log 4.3x 중복 → lesson_scoring 무가중** | `lesson_scoring.py:35` dedup 없이 append, 194키 trade_ready flip | forward 풀 4.1배 부풀림, tr/wo 양쪽 오염. lesson_validation enforce-on이라 위험 | **실재(enforce-on)** |
| 8 | **lesson_validation enforce 배선 vs docstring OFF** | `.env.live:543` ENABLED=true·APPLY_MODE=enforce / docstring "기본 OFF" | 무장 상태. 현재 valid_apply=0(무해)지만 1셀 valid되면 자동 cutoff 조정 | **실재(잠복)** |

### P2 — 분석 오염 (검증으로 쓰면 안 됨)
| # | 항목 | 위치 | 오염 | 판정 |
|---|---|---|---|---|
| 9 | **pre-cutoff LLM replay 5종** | historical_sim·preopen_replay·simulate_hold·tier1/fasttrack | 과거날짜 messages.create 재생=암기누수. **historical_sim `--train`은 brain.json 변이**(유일 라이브 오염면) | **실재(검증 무효)** |
| 10 | **tail_capture_forward_review 스텁** | net Δ 판정 미구현 | shadow 5일치 쌓이는데 판정기 비어 방치 | **실재** |
| 11 | **lesson_forward_validation.py 중복 PoC** | 별도 stale DB, 정식엔진과 불일치 verdict | 혼선원 | **닫기** |
| 12 | import 실패 fallback=USER_MANUAL | `trading_bot.py:246` | 모듈 깨지면 전 청산 USER_MANUAL | **잠복** |

### 손대면 안 됨 (가드, 버그 아님)
- **INVALID_PRICE 4280** — limit_price=0 차단 가드(나이브 fallback시 4458 tp/sl 0나누기·주문가 0).
- `_price_to_krw` 무가드(rate=0→INVALID_PRICE 가드 겸함), `or 0` 기본(if rate>0 가드 안).

### 코스메틱/누수 없음 (확인 완료, 안심)
- forward_* 라벨: 라이브 스코어에서 차단(`future_fields_ignored`) → **라이브 누수 없음**.
- market_regime: shadow_only, 라이브 게이트 아님, 신규 충진 정상(37/37).
- MFE/MAE: 실시간 관측만(누수 없음). PEAD surprise gate 계약 준수.
- portfolio_realized 310/311 신뢰, broker_sell 이중계상 없음.
- candidate_audit lifecycle_state: 전량 NULL(미사용 컬럼, 전이 오염 논외).

---

## 2. 토론 판정 — 실재 vs 코스메틱

**합의(실재 오염, 결론/진실 왜곡):** #1(USER_MANUAL=Claude청산) #2(헤드라인≠학습) #5(US FX 누락) #9(pre-cutoff replay). 이들은 **우리가 본 net 숫자·매도 판정을 실제로 오염**시켰다.

**합의(라이브 오염, 신중):** #3(dead STALE gate) #4(TZ 혼합) — 실제 매매 결정에 영향. 단 나이브 수정 위험.

**불일치/주의:** #6(usd_krw 3종)은 일부 가드라 일괄 수정 위험. #7·#8(lesson)은 현재 valid_apply=0이라 무해하나 enforce-on이라 잠복.

**코스메틱·가드(손대지 마라):** INVALID_PRICE 4280, forward 차단(누수없음), market_regime shadow, candidate lifecycle_state NULL. — 오염 *단서*는 있었으나 실제 영향 없거나 의도된 가드.

---

## 3. 권고 (우선순위 — 코드는 운영자 승인 후)

| 순위 | 조치 | 형태 | 비고 |
|---|---|---|---|
| 1 | **#1 라벨 진실복원** — sync가 `raw_reason` fallback 읽기 + intraday_review_sell→`CLOSED_CLAUDE_INTRADAY_SELL` 매핑 + default USER_MANUAL→UNKNOWN | 수정(저비용·진실 이미 보존됨) | **Claude 매도경로 성과 측정 가능해짐 → #3 재평가 가능** |
| 2 | **#5 improvement_net_monitor US FX 차감** | 수정(내 도구) | US net 과대 교정 |
| 3 | **#2 net 도구 모집단 통일** — quality_grade/learning_allowed 필터 명시·일관 | 위생 | 두 리뷰 정합 |
| 4 | **#9 pre-cutoff replay 5종 검증 무효 + historical_sim `--train` cutoff 가드** | 봉인/가드 | brain.json 오염면 차단 |
| 5 | **#3 dead STALE gate** — bot.risk가 실제 스냅샷 시각 추적 | 신중 수정 | 나이브 수정시 전진입 차단 |
| 6 | **#4 TZ 정합** — UTC/KST tz-aware 통일(정석 `trading_bot.py:26479`) | 수정 | 만료/grace ±9h |
| 7 | #7·#8 lesson dedup + enforce/docstring 정합, #10 stub 완성, #11 닫기 | 위생/관측 | enforce 전 P0 승격 |

**손대지 마라:** INVALID_PRICE 4280, forward 차단, market_regime, lifecycle_state.

---

## 4. 한 줄 결론
코드 위생·오염 전수 감사 — **가장 큰 오염은 라이브 버그가 아니라 라벨/측정 오염**이다. ①USER_MANUAL −46.7%p는 100% Claude 장중매도(intraday_review_sell)가 오라벨된 것(진실은 raw_reason에 이미 보존, sync가 버림) → **#3 매도 판정 불완전**. ②net 헤드라인 도구가 모집단·FX(내 도구 포함)에서 불일치. ③pre-cutoff replay 5종은 검증 무효(historical_sim은 brain 변이까지). 라이브 코드 결함은 dead STALE gate·TZ 혼합 2개가 실재하나 나이브 수정 위험. **수정 1순위는 #1 라벨 진실복원**(저비용·진실 이미 존재·매도경로 측정 잠금해제). INVALID_PRICE류 가드는 손대지 마라.
