# TODO Roadmap

Updated: 2026-06-02

Implementation delta: P0 KIS `EGW00133` classifier/cooldown/shared marker and PathB broker-truth dependency fail-closed behavior are now code/test covered. Keep only their operator-visible preflight/dashboard status and environment QA in P0.

Data provider decision: keep the US Yahoo/FMP/AV and KIS role split. KIS remains broker truth and pre-order quote priority, while Yahoo/FMP/AV remain live US screener/history/context/fallback sources. Do not switch US intraday evidence or US screener to KIS live primary until smoke/shadow coverage, latency, rate-limit, overlap, and outcome gates pass.

Compact backlog snapshot after plan/report cleanup. The detailed source of truth is [../ACTIVE_WORK.md](../ACTIVE_WORK.md). The latest working-tree code recheck is [../P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md](../P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md). The strategy flow audit requirement is [../STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md](../STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md), and the latest DB/log-backed review is [../STRATEGY_FLOW_AUDIT_REVIEW_20260602.md](../STRATEGY_FLOW_AUDIT_REVIEW_20260602.md). Do not create separate active plan files for these items.

## Priority Rule

Active work is ordered by category and impact: 수익성 P0 first, then live 운영/버그 P0, then 데이터베이스 and guard work that protects future profitability analysis. Items marked `코드 확인` in the recheck report can still remain here until commit/QA or live DB verification is complete.

Execution scope:

- Active implementation proceeds through P0 and P1.
- P2 is a planning/visibility/observe bucket, not a mandate to change live trading behavior.
- There is no P3. Items after P2 are either Observe Gate conditions or Protected boundaries.

## P0 / Do First

Current status override - 2026-06-02 Pass 4:

- EL stale PathB row is no longer an active P0 item. It is closed as `CLOSED_AUDITED_BROKER_ABSENT`, learning-excluded, and excluded from fabricated PnL truth.
- Remaining previous-session active PathB rows are NOK/MRVL broker-held overnight positions.
- PathB cooldown no-call row creation is now a planned P1 protected-exception candidate. Direct implementation still requires an explicit operator request and MD violation report.
- P0/P1 implementation/verification scope is complete as of Pass 5. What remains is not a P0/P1 code gap: actual-prompt outcomes and entry/exit shadow need more labeled data, and direct cooldown no-call row creation needs a protected-path request.

| 카테고리 | 항목 | 개선 전 | 개선 후 |
| --- | --- | --- | --- |
| 수익성/운영 | Strategy flow code-level evidence audit | Report A-D plus Implementation Pass 1~5 exist. New/historical audit handoff, ORDER_UNKNOWN remediation, EL audited broker-absent close, V2 freshness, KR capacity reporting, hold-advisor outcome labels, dashboard V2 freshness age, actual-prompt visibility, bucket/source/score quality, KIS rate-limit ops status, and zero-holding fixtures are verified. NOK/MRVL stale-active warnings match real overnight holdings. | P0/P1 implementation is complete for the current scope. Next review is data-gated: actual-prompt outcomes must mature from `awaiting_outcomes`, entry/exit shadow stays observe-only until sample gates pass, and PathB cooldown no-call rows require an explicit protected-path request. |
| 수익성 | Latest KR/US actual-prompt profit visibility | legacy `input_to_claude` or timestamp join can misread prompt inclusion. | measured `actual_prompt_v1` rows separate included/missing candidates and 30/60m outcomes. |
| 데이터베이스 | Candidate bucket/source/score data quality | blank bucket/source and broad `INVALID_PRICE` hide root cause. | audit/outcome rows expose bucket, source quality, raw/trainer score, and concrete invalid-price reason. |
| 수익성 | KR entry/exit shadow instrumentation | first-entry and exit-overlay hypotheses rely on sparse, concentrated samples. | 30 fills or 4 weeks of broker-fill-aware replay with MFE/MAE, OR/VWAP, cap-hit data. |
| 운영 | KIS `EGW00133` token ops status | classifier/cooldown exists, but operator-visible preflight/dashboard status still needs closure. | rate-limit/backoff is classified, throttled, and visible while real credential failure stays fail-closed. |
| 버그 | Broker-truth zero-holding fixture tests | destructive stale-position cleanup depends on assumed KIS row shapes. | KR/US fixture coverage proves only fresh zero holding with zero open remainder is removable. |
| 운영 | PathB entry broker-truth gate visibility | fail-closed exists, but live entry blocks still need clearer operator-visible detail. | preflight/ops shows TTL, attempts, failure, latency, last error, block reason, and paper skip. |
| 버그 | PathB pending-buy TTL/order matching | plan-created age and same-ticker fallback can cancel/recover the wrong order. | actual sent/ACK timestamp and exact `order_no` matching drive TTL/fill/cancel decisions. |

## P1 / Develop Next

| 카테고리 | 항목 | 개선 전 | 개선 후 |
| --- | --- | --- | --- |
| 버그 | US PathB sizing context/reason split | `qty=0` can collapse to `INVALID_QTY`. | MRVL-style early-gate shrink and APP-style high-price budget block are separated without changing qty policy. |
| 데이터베이스 | V2 canonical freshness and fallback exclusion | stale canonical truth or timeout fallback can pollute analysis. | freshness is operator-visible and `advisor_unavailable`/`learning_excluded` rows are excluded from learning/canonical aggregates. |
| 운영/보호 | PathB cooldown no-call outcome row plan | PathB `AUTO_SELL_REVIEW` cooldown correctly suppresses repeat Claude calls, but the skipped-review branch does not create a separate outcome row/label for later HOLD-vs-cooldown scoring. | If explicitly requested, add a no-call cooldown row/label with `decision_source=auto_sell_review_cooldown`, `cooldown=true`, `claude_called=false`, `tokens=0`; preserve one Claude call across cooldown rechecks and include an MD violation report because this touches a protected PathB guard. |
| 운영 | Brain/sub-screener/operator-visible guard tests | hidden scoped triggers or policy-memory writes can mislead operators. | direct brain writes are blocked and effective trigger state is visible. |
| 버그 | Runtime tuning override cleanup | non-tuning fields can remain in runtime override payloads. | only bounded numeric keys from `RUNTIME_ADJUSTMENT_BOUNDS` persist. |
| 수익성 | Raw-score shadow and multi-source consensus | good missing candidates versus source noise is unclear. | labeled shadow outcomes compare added candidates against excluded candidates. |
| 운영 | PathB fill truth / sell pending / EXPIRED | `ORDER_UNKNOWN`, partial remainder, and stale plans can rely on local inference. | broker-truth-backed fill/remainder/waiting-plan state is visible in ops. |

## P2

| 카테고리 | 항목 | 개선 전 | 개선 후 |
| --- | --- | --- | --- |
| 운영 | Hold advisor TTL/cache and low-risk model tiering | cost/latency optimization could weaken sell protection. | baseline-driven shadow proves cost reduction without degrading HOLD/SELL quality. |
| 운영 | Analyst outage UI polish | core safety exists but outage can still be visually ambiguous. | dashboard/API separates provider unavailable, partial consensus, and quorum failure. |
| 운영 | Intraday evidence alignment review | code/test fix exists, but provider/cache/KR timeout pressure remains an operational risk. | remaining warnings are attributed to provider/cache/coverage rather than structural target shortage. |
| 데이터베이스 | Residual degraded/FMP source-quality observability | degraded/FMP outcome grouping can be confused with P0 audit contract. | source-quality grouping is queryable after the P0 bucket/source/score contract is covered. |
| 데이터베이스 | US Yahoo/KIS provider role split and intraday shadow | KIS-only conversion could reduce US coverage and compete with broker truth API capacity. | KIS intraday/ranking stays smoke/shadow until source, fallback, overlap, latency, and outcome evidence supports staged promotion. |
| 후보품질 | US universe filter bypass / unclassified bucket | screener universe 3건 vs 후보 75건 → keep_ratio 37% < min_ratio 50% → bypass 발동, unclassified 18건이 bucket 없이 prompt 진입. 거래 안전에 영향 없으나 prompt 슬롯 낭비. | ① `UNIVERSE_FILTER_MIN_RATIO` config 조정 (50%→30%), ② unclassified prompt pool 후순위 penalty 추가. KR trainer prior 작업과 같은 사이클에 묶어 처리. |
| 관찰/보호 | Observe Gate bundle | Observe items were listed after P2 and could look like another scattered priority group. | Treat Prompt overlay/PLAN_A, US KIS ranking primary, US intraday KIS primary, KR confirmation/WATCH_TRIGGER, and KR first-entry/exit overlay as observe-only until their sample gates pass. No live behavior change from P2. |
| 보호 | Protected revenue/safety boundaries | Protected items were split across AGENTS.md and audit review docs, which made the plan look incomplete. | P2 must explicitly preserve US PathB pre-close/profit ladder, AUTO_SELL_REVIEW HOLD cooldown, broker-truth fail-closed entry, PathB sizing reason split, zero-holding reconcile, KIS `remaining_qty`, RouteDecision contract, and `state/brain.json` no-direct-write policy unless an explicit MD violation report is approved. |

## Observe Gates

These are P2 planning boundaries, not P0/P1 implementation work.

- Prompt overlay / PLAN_A: shadow only until enough trading days, trigger days, labeled outcomes, PF, and concentration gates pass.
- US KIS ranking primary: shadow only until at least 10 shadow trading days and 30 evaluated outcome rows.
- US intraday KIS primary: keep `INTRADAY_EVIDENCE_PROVIDER_US=yfinance` until small-ticker smoke and 3-5 trading sessions of KIS shadow prove coverage, timestamp quality, latency, close-diff, and rate-limit safety; broker truth must not fall back to Yahoo/FMP/AV.
- KR confirmation / WATCH_TRIGGER: no live demotion change until kept/demoted labels are sufficient.
- KR first-entry / exit overlay: replay/shadow only until sample size and broker-fill-aware review pass.

## Protected Boundaries

These are P2 planning boundaries and require a dedicated MD violation report before any direct runtime change:

- US PathB `CLOSED_CLAUDE_PRICE_PRE_CLOSE` and `CLOSED_PROFIT_LADDER` revenue paths.
- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard.
- PathB broker-truth entry fail-closed behavior.
- PathB sizing reason split and one-share/early-gate sizing policy.
- Zero-holding stale reconcile.
- KIS order normalization with `remaining_qty` preservation.
- Path A/Path B `RouteDecision` contract.
- Broker truth priority and market quarantine behavior.
- `state/brain.json` no direct automatic policy write.

## Removed From Active

- Completed implementation details belong in [DEVELOPED_WORK.md](DEVELOPED_WORK.md) or Git history.
- Raw dated plans, simulations, QA notes, generated JSON reports, and stale PathB plan text are deleted after their unfinished work is absorbed here and in `ACTIVE_WORK.md`.
