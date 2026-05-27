# Code-Level Requirements

Updated: 2026-05-27

Purpose: plan/report cleanup 이후 남은 code-level 요구사항과 acceptance gate를 정리한다. 완료 항목은 커밋/코드 근거가 있을 때만 제거하며, 작업트리 구현만 있는 항목은 active review로 남긴다.

Detailed P0/P1 implementation requirements are now tracked in [P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md](P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md). This file remains the compact judgment and acceptance matrix.

## Code-Level Judgment

| 카테고리 | Item | Current code status | Remaining judgment |
| --- | --- | --- | --- |
| 수익성 | Actual prompt visibility | prompt count/trace 기반은 커밋되어 있고, 현재 코드에는 `selection_trace_id`, `actual_prompt_*`, raw/trainer score component 계측이 더 보강되어 있다. | fresh KR/US cycle에서 실제 DB rows를 검증하고 report가 legacy `input_to_claude`보다 actual prompt fields를 우선하는지 확인. |
| 데이터베이스 | Candidate bucket/source quality | source/bucket 일부 필드는 있으나 broad reason과 blank bucket/source gap이 남아 있다. `INVALID_PRICE` reason split은 작업트리 구현으로 보이며 commit 완료로 보지 않는다. | bucket/source/data-quality/score components를 audit/outcome까지 전파하고 blank rate를 측정. |
| 수익성 | KR entry/exit profit shadow | counterfactual minute base는 있으나 first-entry/OR/VWAP/MFE-cap review가 promotion-ready가 아니다. | sample-size/concentration gate 충족 전 live first-entry/exit 변경 금지. |
| 운영 | KIS token rate-limit handling | refresh/retry는 있으나 `EGW00133` rate-limit/backoff 분류가 미완료. | duplicate force-refresh prevention과 focused tests 필요. |
| 버그 | Broker-truth zero-holding reconcile | runtime logic은 있으나 realistic KR/US fixture coverage가 부족. | parser 변경 전 fixture tests로 destructive reconcile fail-closed 보장. |
| 운영 | PathB entry broker-truth gate | runtime gate는 있다. | operator-visible TTL/failure/block reason이 필요. |
| 버그 | PathB pending-buy TTL/order matching | 작업트리에 sent/ACK timestamp, exact mismatch defer, cancel-confirm follow-up 구현 흔적이 있다. | commit/QA 전에는 완료로 보지 않고 existing order behavior 불변성 검증 필요. |
| 버그 | US PathB sizing context | 작업트리에 sizing context와 reason split 구현 흔적이 있다. | qty 결과와 order policy 불변 test가 필요. |
| 데이터베이스 | Canonical/fallback truth | V2 canonical truth preferred rule은 문서화되어 있으나 freshness warning과 fallback exclusion tests가 부족. | stale/missing truth와 `advisor_unavailable` aggregate exclusion을 visible/tested로 만든다. |
| 운영 | Brain/sub-screener guards | 일부 runtime behavior는 있으나 직접 brain write 방지와 effective trigger visibility가 부족. | direct-write guard, trigger precedence/counter visibility, stale truth warning tests. |
| 버그 | Runtime tuning bounds | 기존 bounds는 `minority_report/tuner.py`에 있었고, 현재 작업트리에 `runtime/tuning_bounds.py` migration이 있다. | tracked file/commit 상태 정리 후 non-override key drop assertion 필요. |
| 운영 | PathB fill/sell-pending/EXPIRED | lifecycle reconcile은 있다. | real KIS payload, partial remainder, EXPIRED resampling, stale waiting-plan cleanup 검증. |

## Before/After Acceptance Matrix

| Requirement | 개선 전 | 개선 후 |
| --- | --- | --- |
| R1 actual prompt profit visibility | prompt inclusion을 old field 또는 timestamp-nearest join으로 추정. | `selection_trace_id`, `actual_prompt_call_id`, `actual_prompt_included`, `actual_prompt_rank` 기준으로 measured/unmeasured, included/missing outcomes 분리. |
| R2 candidate data quality | blank bucket/source와 broad `INVALID_PRICE`가 ranker/source/price/execution 원인을 섞음. | bucket/source/data-quality/raw-score/trainer-score와 invalid-price concrete reason이 query 가능. |
| R3 KR entry/exit shadow | first-entry/exit overlay 판단이 소수 샘플에 과적합될 수 있음. | 30 fills 또는 4 weeks, top-day contribution < 40%, broker-fill-aware replay 후 review. |
| R4 KIS token backoff | `EGW00133`가 credential issue처럼 보이거나 force-refresh storm을 유발. | rate-limit/backoff로 throttling되고 credential/config failure는 별도 fail-closed. |
| R5 zero-holding fixtures | destructive reconcile이 local/cache assumptions에 기대는 위험. | KR/US fixtures가 fresh zero/no-open only cleanup과 unsafe fail-closed를 증명. |
| R6 PathB broker-truth visibility | entry block이 log-only 상태. | ops/preflight에서 TTL, attempts, latency, error, block reason 확인. |
| R7 PathB TTL/order matching | plan age와 same-ticker fallback이 wrong cancel/fill 판단을 만들 수 있음. | actual sent/ACK timestamp와 exact `order_no`가 있으면 exact-only matching. |
| R8 US PathB sizing context | `qty=0` 원인이 `INVALID_QTY`로 뭉침. | order quantity는 유지하고 MRVL형 `ORDER_SIZE_TOO_SMALL_GATE`, APP형 `HIGH_PRICE_BUDGET_BLOCK` 분리. |
| R9 canonical/fallback truth | stale canonical or fallback HOLD가 성과/학습에 섞일 수 있음. | freshness visible, fallback unavailable rows excluded from learning/canonical aggregate. |
| R10 Brain/sub-screener guards | hidden trigger/direct memory write가 운영 판단을 오염. | effective trigger visible, automatic brain direct write blocked. |
| R11 runtime tuning cleanup | non-override fields can survive override payloads. | only bounded numeric keys persist, bounds unchanged. |
| R12 PathB fill/remainder/EXPIRED | local inference can decide unresolved lifecycle states. | broker truth drives full/partial/cancel/remainder/expired outcomes and ops visibility. |

## Observe-Only Requirements

- Prompt overlay / PLAN_A: promotion requires enough trading days, trigger days, labeled outcomes, PF, and concentration gates.
- US KIS ranking: primary promotion requires at least 10 shadow trading days and 30 evaluated outcome rows.
- Raw-score shadow, KR confirmation/WATCH_TRIGGER changes, KR first-entry filters, and exit overlays remain shadow/replay until reviewed.

## Safety Rule

No requirement in this document authorizes live gate, order amount, hard stop, broker truth priority, PathB sizing, or runtime parameter changes. Any such change needs explicit operator review.
