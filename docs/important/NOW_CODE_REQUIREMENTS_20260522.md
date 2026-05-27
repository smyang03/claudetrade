# Immediate Code Requirements

Updated: 2026-05-27

Scope: profitability-first cleanup 이후 즉시 구현/검토해야 할 P0/P1 항목이다. 자세한 active ledger는 [ACTIVE_WORK.md](ACTIVE_WORK.md)를 따른다.

## Non-Goals

- PathB live gates, order sizing policy, max positions, daily entry limit, confidence, slippage caps, protective hold distance, hard stops, broker-truth priority를 변경하지 않는다.
- Prompt overlay, PLAN_A, raw-score shadow, US KIS ranking, KR first-entry, exit overlay를 live execution 영향으로 승격하지 않는다.
- `state/brain.json`을 runtime truth 또는 자동 학습 write target으로 사용하지 않는다.
- selection 품질 변경과 execution/risk 변경을 한 patch에서 섞지 않는다.

## NOW Matrix

| 순서 | 카테고리 | 우선순위 | 요구사항 | 개선 전 | 개선 후/완료 기준 |
| --- | --- | --- | --- | --- | --- |
| 0 | 운영 | P0 | Commit hygiene guardrail | 런타임 산출물, `state/brain.json`, DB sidecar가 섞여 stage될 수 있음. | explicit path로 stage하고 `git status --short`, `git diff --stat` 확인. |
| 1 | 수익성 | P0 | Latest-cycle profit visibility verification | legacy `input_to_claude`/timestamp join이 실제 Claude 입력 여부를 오판. | 최신 KR/US `actual_prompt_v1` rows에서 prompt included/missing, raw/trainer missing, 30/60m outcomes 분리. |
| 2 | 데이터베이스 | P0 | Candidate bucket/source/score data quality | blank bucket/source, broad `INVALID_PRICE`, score component 누락으로 원인 분리 불가. | bucket/source/data-quality/raw-score/trainer-score가 audit/outcome query 가능하고 broad reason이 세분화됨. |
| 3 | 수익성 | P0 | KR entry/exit profit shadow instrumentation | first-entry/exit-cap 판단이 sparse sample과 top-day concentration에 취약. | 30 fills 또는 4 weeks broker-fill-aware replay로 OR/VWAP/MFE/MAE/cap-hit 검토 가능. |
| 4 | 운영 | P0 | KIS token `EGW00133` classification/backoff | token issuance pressure가 credential failure나 refresh storm으로 처리될 수 있음. | rate-limit/backoff와 credential/config failure가 분리되고 shared credential 중복 refresh가 방지됨. |
| 5 | 버그 | P0 | Broker-truth zero-holding fixture tests | destructive cleanup이 실제 KIS row shape 가정에 의존. | KR/US fresh zero/no-open만 reconcile되고 stale/error/open remainder/fill-only는 fail closed. |
| 6 | 운영 | P0 | PathB entry broker-truth gate ops visibility | PathB entry block 원인을 log scraping 없이는 알기 어려움. | preflight/ops에서 TTL, attempt, success/failure, latency, error, block reason, paper skip 확인 가능. |
| 7 | 버그 | P0 | PathB pending-buy TTL/order matching review | plan-created TTL 또는 same-ticker fallback으로 잘못된 취소/복구 가능. | actual sent/ACK timestamp와 exact `order_no` 기준으로 TTL/fill/open/cancel 판단. |
| 8 | 버그 | P1 | US PathB sizing context/reason split | PathB `qty=0`이 broad `INVALID_QTY`로 뭉침. | qty policy는 유지하면서 early-gate shrink와 high-price budget block이 payload reason으로 분리됨. |
| 9 | 데이터베이스 | P1 | V2 canonical freshness and fallback exclusion | stale truth 또는 timeout fallback HOLD가 분석/학습에 섞일 수 있음. | freshness warning이 보이고 `advisor_unavailable`/`learning_excluded`는 aggregate에서 제외됨. |
| 10 | 운영 | P1 | Brain/sub-screener/operator-visible guard tests | hidden trigger, stale canonical, direct brain write가 운영 판단을 오염시킬 수 있음. | direct write 방지, effective trigger visibility, stale truth warning tests 존재. |
| 11 | 버그 | P1 | Runtime tuning override cleanup | non-tuning fields가 runtime override payload에 남을 수 있음. | `coerce_runtime_adjustments()`가 bounded numeric keys만 반환. |
| 12 | 운영 | P1 | PathB fill truth / sell pending / EXPIRED monitoring | ORDER_UNKNOWN, partial remainder, expired plan이 local inference에 기대는 위험. | real KIS payload, partial remainder, EXPIRED resampling, stale waiting-plan cleanup이 broker truth 기준으로 검증됨. |

## Focused Acceptance

- P0 수익성/DB 작업은 audit/report/observability만 바꾸며 live ranking, prompt cap, PathB gates, sizing, stop/exit rule은 변경하지 않는다.
- P0 운영/버그 작업은 broker/order fail-closed behavior를 약화하지 않는다.
- 작업트리 구현만 있는 항목은 관련 tests와 commit 전까지 완료로 문서화하지 않는다.

## Verification Commands

Touched modules에 맞춰 가까운 테스트부터 실행한다.

```powershell
python -m pytest tests/test_candidate_audit.py tests/test_screener_quality.py tests/test_candidate_action_live_mapping.py -q
python -m pytest tests/test_broker_truth_snapshot.py tests/test_kis_kr_order_safety.py tests/test_pathb_runtime.py -q
python -m pytest tests/test_live_order_safety.py tests/test_entry_risk_controls.py -q
python -m py_compile trading_bot.py kis_api.py runtime/pathb_runtime.py runtime/tuning_bounds.py dashboard/dashboard_server.py
```
