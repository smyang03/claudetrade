# Hold Advisor → lesson_validation 정식 편입 (다음 세션 핸드오프)

> 2026-06-23 준비. 출처: `docs/reports/hold_advisor_review_20260623.md`(2자토론) + 이 세션 코드 탐색.
> **목표**: hold advisor `profit_guard`(익절 우선) 교훈을 forward-validation에 편입 → "익절 SELL이 HOLD보다 net+였나"를 **자동 셀 축적·verdict**로 검증. 현재 임시 도구(`tools/hold_advisor_outcome_review.py`)는 selection bias(다른 포지션 SELL vs HOLD)가 있어 부정확 → 정식 편입은 **같은 포지션 counterfactual**(SELL actual vs HOLD would_be)이라 정확.
> **이게 #2의 본래 의도**(hold advisor 자기 교훈 자동화). #4 국면 조건부화의 선행 검증 인프라.

---

## 0. 이 작업이 "왜 큰가" — 핵심 한 줄

`score_cell`(채점 엔진)은 **범용이라 재사용**한다. 그런데 그 엔진이 먹는 **forward 라벨**이 selection엔 `ticker_selection_log.forward_3d`로 이미 있지만 **hold advisor 청산엔 아무 데도 없다.** 그래서 forward 라벨 인프라(yfinance 백필)를 새로 깔아야 한다. = 신규 3컴포넌트.

---

## 1. 재사용 (변경 0 — 그대로 호출)

| 자산 | 위치 | 역할 |
|---|---|---|
| `score_cell(lesson_key, market, regime, would_be_fwd, actual_fwd, *, sessions_confirmed, cost_floor=...)` | `minority_report/lesson_validation.py:190` | 범용 반사실 채점. verdict: insufficient/pending/invalid_block/valid_apply/marginal/neutral |
| `counterfactual_gain(would_be, actual)` = median(wb)−median(ac) | `lesson_validation.py:170` | gain |
| `sign_consistency_sessions(sub_gains, overall)` | `lesson_validation.py:178` | 월별 부호일관 |
| `upsert_cells(cells, db_path)` | `lesson_validation.py:324` | validated_lesson store upsert |
| `regime_from_consensus_mode(cmode)` → risk_on/off/mixed | `lesson_validation.py:126` | regime 라벨러 |
| **패턴 템플릿** `rescore_lessons()` | `lesson_scoring.py:25-66` | selection_log → (market×regime×month) 셀 → upsert. **이 함수를 그대로 본떠 hold advisor 버전 작성** |
| 세션마감 hook | `trading_bot.py:38314` `rescore_safe()` 호출 지점 | 여기에 hold advisor rescore 추가 |
| yfinance 백필 패턴 | `tools/backfill_mfe_yfinance.py`, 테이블 `mfe_backfill_yf`(decisions.db) | forward 가격 백필 코드 재사용 |

---

## 2. 신규 3컴포넌트 (이게 할 일)

### ① forward 라벨 테이블 + yfinance 백필
신규 테이블 `hold_advisor_exit_outcome` (decisions.db 권장):
```
sell_decision_id TEXT PK, path_run_id, market, ticker, sell_date, sell_ts,
sell_price, entry_price, realized_net,        -- SELL 실현(진입~매도) = v2.pnl_pct
hold_fwd_net,                                  -- HOLD 지속(진입~+3거래일) = (fwd_price/entry_price-1)*100, yfinance
regime, profit_guard_active(bool), hold_mode, synced_at
```
- **수집 소스**: `logs/hold_advisor/decisions_*.jsonl` 중 `decision=SELL` + `ts[:10] >= "2026-06-16"`(profit_guard ON) + **이익 중 SELL만**(`hold_mode in {profit_pullback, target_extension}` 또는 `pnl_pct>0`). 손절성 SELL(stop_recovery/loss_deferral)은 profit_guard 무관이므로 **제외**.
- `path_run_id`로 `v2_learning_performance` 조인 → `realized_net`, `entry_price`, `sell`(exit) 시점.
- **yfinance 백필**: `hold_fwd_net` = (sell_date+3거래일 종가 / entry_price − 1)×100. forward 미성숙(3거래일 안 지남)이면 NULL → score에서 제외(→ pending).

### ② regime 소스
- 1순위: `v2_learning_performance.market_regime`(6/21 배선 + 6/23 sync fallback 수정으로 forward 채워지는 중) → `regime_from_consensus_mode` 통과.
- fallback: hold advisor decisions의 `advisor_context_v2`/consensus 캐시.
- 비면 None → 그 건 제외(안전, 위조 금지).

### ③ cost_floor 프레임 — **방향 주의**
프레임: would_be = "익절(SELL) 교훈 적용시" / actual = "미적용(HOLD)".
- `would_be_fwd` = `realized_net` (SELL 실현, 진입~매도)
- `actual_fwd` = `hold_fwd_net` (HOLD 지속, 진입~+3일)
- `gain = median(realized_net) − median(hold_fwd_net)`
  - **gain > 0** = 익절(SELL)이 HOLD보다 나음 → profit_guard **valid_apply** 방향
  - **gain < −threshold** = HOLD가 나음 → profit_guard가 **조기절단**(invalid_block) ← 토론 핵심 가설 검증
- `cost_floor` 게이트: would_be(=realized_net) median ≥ cost_floor → 익절이 실제 수익. 손절성 SELL을 이미 제외했으므로 익절 건만 남아 cost_floor 의미 유지. **selection의 cost_floor(0.5) 그대로 쓰되, 첫 실행 결과 보고 조정.**

---

## 3. producer 함수 (lesson_scoring.py에 추가)
```python
def rescore_hold_advisor_exit_lessons(store_db=None) -> list[dict]:
    # 1. hold_advisor_exit_outcome 읽기 (hold_fwd_net IS NOT NULL = forward 성숙분만)
    # 2. (market, regime) → month → {sell:[realized_net], hold:[hold_fwd_net]}
    # 3. all_sell, all_hold + 월별 sub_gains → sign_consistency
    # 4. score_cell("hold_profit_guard_exit", market, regime,
    #               would_be_fwd=all_sell, actual_fwd=all_hold, sessions_confirmed=...)
    # 5. upsert_cells
def rescore_hold_advisor_exit_safe(store_db=None) -> int:  # 예외 삼킴, hook용
```
- `lesson_key = "hold_profit_guard_exit"`
- **`LESSON_CONTROL_MAP`에 추가하지 말 것** — control 자동토글(profit_guard on/off) 금지. **verdict 축적만**, 토글은 운영자 수동 판정(`HOLD_ADVISOR_PROFIT_GUARD_ENABLED`). (profit_guard는 binary라 bounded control과 다름.)

## 4. hook 등록
`trading_bot.py:38314` 인근:
```python
from minority_report.lesson_scoring import rescore_safe, rescore_hold_advisor_exit_safe
_lv_cells = rescore_safe()
_ha_cells = rescore_hold_advisor_exit_safe()   # 추가
```
yfinance 백필은 무거우니 **세션마감 hook이 아니라 별도 도구/주기**로 분리 권장 (`tools/backfill_hold_advisor_exit_outcome.py`). rescore는 라벨 읽기만(가벼움).

## 5. 검증 (다음 세션 작업 끝에)
- producer 단위테스트: mock 테이블 → score_cell verdict 방향 확인(HOLD 우위 → invalid_block, SELL 우위 → valid_apply).
- yfinance 백필 mock.
- 라이브 무영향 확인: 격리 store(validated_lesson) + read-only(decisions.db). 봇 루프 무관.
- `python -m pytest` 회귀 + py_compile + mojibake.

---

## 6. 데이터 가용성 (오늘 측정)
- profit_guard ON SELL: `post(ON) n=33` (`tools/hold_advisor_outcome_review.py` 실행값). **이익 중 SELL만 필터하면 더 작아짐** → 초기엔 `insufficient`/`pending` 다수 예상(정직).
- forward 3거래일 성숙: 6/20 이전 SELL만 현재 측정 가능. 시간 지나며 valid로 승격.
- regime: 6/21 배선 + 6/23 sync fallback으로 forward 채워지는 중(과거분 일부만).

## 7. 함정 (2자토론 결론 — 반드시 유지)
- **표본 작음** → min_wo/min_tr 게이트로 `insufficient` 정직 처리. 억지 valid 금지.
- **5월 강세장 confound**(prior 표본) → regime 분할로 일부만 완화. 표본 충분 전 판정 보류.
- **이게 임시 A/B 도구보다 정확한 이유** = 같은 포지션 counterfactual(SELL vs HOLD), selection bias 없음. 단 forward 재구성 가정(3거래일 표준화)은 multi-day hold 실제 청산시점과 다를 수 있음 → 한계 명시.
- control 자동적용 금지(verdict만). profit_ladder 롤백 전례 = 청산 자동변경은 데이터 충분+승인 후.

## 8. 관련 커밋/문서
- prior 정직화 + A/B 도구: `ae142b0`
- market_regime sync fallback(②regime 의존): CHANGELOG 2026-06-23 "market_regime 0/757 sync-layer fallback"
- 검토 리포트: `docs/reports/hold_advisor_review_20260623.md`
- lesson_validation 설계 원본: `docs/important/LESSON_QUALITY_CONFIG_PIPELINE_DESIGN_20260617.md`
