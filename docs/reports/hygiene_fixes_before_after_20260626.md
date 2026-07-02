# 코드 위생·오염 개선 — 전후 리포트 (2026-06-26)

> 감사: `code_hygiene_contamination_audit_20260626.md`. 운영자 지시: 모든 개선 항목 루프(검토→개선방향→구현)→전후 보고.
> 검증: py_compile PASS / **pytest 2722 passed** / mojibake PASS / live preflight ok=True·FAIL 0. 커밋 안 함.

---

## 0. 한눈에 — 완료 / 보류

| # | 항목 | 상태 | 형태 |
|---|---|---|---|
| 1 | 라벨 진실복원 (USER_MANUAL=Claude청산) | ✅ **완료**(forward+백필) | 라벨/sync |
| 2 | improvement_net_monitor US FX 차감 | ✅ 완료 | 내 도구 |
| 3 | lesson_forward_validation PoC deprecate | ✅ 완료 | 위생 |
| 4 | historical_sim `--train` brain 오염 가드 | ✅ 완료 | 안전 |
| 6a | 액션 만료 TZ 정합 (self-contained) | ✅ 완료 | 라이브(운영테스트 대기) |
| 5 | net 도구 모집단/basis 통일 | ⏸ **보류**(가이드만) | 다도구 |
| 6b | grace-calc TZ (6134) | ⏸ 보류(콜러 감사 필요) | 라이브 |
| 7 | dead STALE_MARKET_DATA 게이트 | ⏸ 보류(가격타이밍 소스 조사) | 라이브 |

손대지 않음(가드): INVALID_PRICE 4280, usd_krw 일괄.

---

## 1. 항목별 전후

### #1 라벨 진실복원 — Claude 청산이 "수동매도"로 위장하던 것
- **검토:** `CLOSED_USER_MANUAL −46.7%p`의 raw_reason 복원 → 100% Claude 청산(intraday_review_sell 33·pre_session_sell 1), 진짜 수동매도 0. 근본: `v2_lifecycle_runtime.py:58` default=USER_MANUAL + sync가 truth(raw_reason) 무시.
- **개선방향:** intraday_review_sell을 **별도 라벨**(`CLOSED_CLAUDE_INTRADAY_SELL`)로 — profit_guard 익절(양수)과 장중 손실컷(음수) 분리 분석. default→`CLOSED_UNKNOWN`. import-fallback·on_external_close default도 UNKNOWN.
- **구현:** `runtime/v2_lifecycle_runtime.py`(매핑+default), `trading_bot.py:247`(import fallback), `pathb_runtime.py:3760`(external default). 과거 34건 백필 `tools/backfill_user_manual_relabel.py --apply`.
- **전→후 (close_reason 재분포):**

| close_reason | 전 | 후 |
|---|---|---|
| CLOSED_USER_MANUAL | N=34, −46.7%p (가짜 "수동") | **N=0** |
| CLOSED_CLAUDE_INTRADAY_SELL | (없음) | **N=33, −44.6%p** (Claude 손실컷, 분리 측정 가능) |
| CLOSED_CLAUDE_SELL | N=13, +25.5%p | **N=14, +27.2%p** (익절 경로, pre_session 1 흡수) |

→ **함의:** 토론 #3 "매도 개입 net 기여"는 profit_guard 익절(양수)만 봤음. 이제 intraday_review_sell(−44.6%p)이 분리돼 **Claude 매도경로 재평가 가능**(손실컷이 정당한지 counterfactual은 별도).

### #2 improvement_net_monitor US FX 누락 (내 도구 버그)
- **검토:** `_net_of`가 `backfilled_fee_only`(US, FX 미반영 근사)를 그대로 net으로 반환 → US net 체계적 과대.
- **구현:** `FX_SPREAD_PCT={US:0.2}` 추가, backfilled_fee_only·gross fallback 경로에 FX 차감.
- **전→후:** US backfilled net이 trade당 −0.2%p 교정(과대 제거). capture_net_review와 정합 방향.

### #3 lesson_forward_validation PoC deprecate
- **검토:** 정식 `lesson_scoring.rescore_lessons`로 대체됐는데 별도 stale DB에 불일치 verdict 생성=혼선원.
- **구현:** main()에 deprecation 가드 — 기본 차단(`LESSON_FWD_POC_ALLOW=1` opt-in), 정식 경로 안내.
- **전→후:** 무심코 실행 시 stale verdict 안 만들고 정식 경로 안내.

### #4 historical_sim `--train` brain 오염 가드
- **검토:** 과거날짜 LLM 재생 postmortem을 brain.json(라이브 정책메모리)에 직접 씀=암기누수 오염. 유일한 라이브 오염면.
- **구현:** brain 변이 블록을 `_brain_write_allowed()` 가드로 — 기본 차단(`HISTORICAL_SIM_ALLOW_BRAIN_WRITE=1` opt-in), 분석 출력은 항상 생성.
- **전→후:** 과거날짜 replay가 더는 brain.json을 무단 오염 못 함.

### #6a 액션 만료 TZ 정합 (self-contained)
- **검토:** `_candidate_action_expired`가 expiry를 `.replace(tzinfo=None)`로 strip → UTC("Z") expiry가 KST 벽시계와 비교돼 **±9h 왜곡**(액션이 9h 일찍/늦게 만료).
- **개선방향:** aware-aware 비교 — "Z"=UTC, naive=KST 가정. self-contained(datetime을 콜러에 안 넘김)라 안전.
- **구현:** `trading_bot.py:5722` tz-aware 비교로 교체.
- **전→후:** 액션 만료가 정확한 시각에. **라이브 진입 타이밍 변화 → 운영테스트 대기**(A1처럼 결과 관찰).

---

## 2. 보류 항목 (라이브 위험 — 신중 처리)

| # | 항목 | 보류 이유 | 필요 작업 |
|---|---|---|---|
| 5 | net 도구 모집단/basis 통일 | 다도구(capture_net_review·full_profitability_review·monitor) quality_grade/learning_allowed 필터 불일치 — 정본 필터 결정 필요 | 정본 필터 합의 후 일괄(분석 위생, 라이브 무관) |
| 6b | grace-calc TZ (`trading_bot.py:6134`) + `_parse_candidate_route_time`(5735) | 헬퍼가 datetime을 **여러 콜러에 반환** → aware 전환 시 콜러 전수 감사 안 하면 naive/aware 혼합 크래시 | 콜러 전수 감사 후 일괄 tz-aware |
| 7 | dead STALE_MARKET_DATA 게이트 (`pathb_runtime.py:4311`) | `last_market_data_at=now()` 항상 → 신선도 no-op. 고치려면 bot.risk가 **실제 가격갱신 시각**을 추적·전파해야 하고, 소스 잘못 잡으면 전 진입 차단 | 가격갱신 타임스탬프 소스 식별 후 신중 수정 + 운영테스트 |

**왜 보류:** 6b·7은 라이브 진입을 깨뜨릴 수 있는 다지점/타이밍 의존 변경이라, 마라톤 세션 끝에 나이브 수정하면 "조용히 깨짐"(CLAUDE.md 금지). 전용 작업 + 운영테스트 필요. 5는 라이브 무관하나 정본 필터 결정이 운영자 몫.

---

## 3. 검증
| 단계 | 결과 |
|---|---|
| py_compile (변경 7파일) | ✅ PASS |
| pytest `tests/` | ✅ **2722 passed** |
| mojibake | ✅ PASS |
| live preflight | ✅ ok=True, FAIL 0 |

---

## 4. 한 줄 결론
코드 위생·오염 개선 — **5개 완료(#1·2·3·4·6a), 3개 보류(#5·6b·7, 라이브 위험).** 최대 수확은 **#1 라벨 진실복원**: "USER_MANUAL −46.7%p 적자"가 실은 Claude 자신의 장중 손실컷(intraday_review_sell)이었음을 분리·복원해, 토론 #3 매도 판정을 제대로 재평가할 토대를 만들었다(진실은 raw_reason에 이미 있었고 sync가 버리던 것). brain 오염면(#4)·내 도구 FX버그(#2)도 막았다. 라이브 게이트 결함(STALE·grace TZ)은 깨뜨릴 위험이 커 전용 작업+운영테스트로 분리.
