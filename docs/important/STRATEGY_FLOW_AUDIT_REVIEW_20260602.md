# Strategy Flow Audit Review - 2026-06-02

## 1. Scope

이 문서는 `STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md`를 기준으로 실제 코드, DB, 로그를 read-only로 대조한 검토 결과다.

검토 목적은 다음 네 가지다.

1. 전략 모드별 값 생성 지점부터 selection, routing, Path A/Path B, 주문, 청산, hold advisor, 성과 기록까지 흐름이 끊기지 않는지 확인한다.
2. 값 누락, data_quality mismatch, stale 값, broker truth block, config gate, shadow-only, affordability/risk block, order lifecycle ambiguity를 분리한다.
3. 코드에는 존재하지만 MD 요구서나 운영 리포트에서 빠진 확인 항목을 보강 후보로 올린다.
4. 각 항목이 실제 성과 지표까지 연결되는지 확인하고, 연결되지 않는 경우 개선 방향을 정리한다.

이번 검토는 코드/config/runtime state를 수정하지 않았다. DB 분석은 조회와 dry-run만 수행했다.

## 2. Evidence Sources

| Source | Scope | Result |
| --- | --- | --- |
| `data/audit/candidate_audit.db` | candidate audit rows/outcomes/counterfactual | 2026-04-20 to 2026-06-02 |
| `data/v2_event_store.db` | decisions/path_runs/lifecycle_events | 2026-04-27 to 2026-06-02 |
| `data/ml/decisions.db` | `v2_learning_performance`, `v2_canonical_performance` | latest KR 2026-05-27, latest US 2026-05-26 |
| `data/ticker_selection_log.db` | selection trace | 2026-04-07 to 2026-06-02 |
| `logs/funnel/action_routing_shadow_*.jsonl` | route/action observations | latest KR 2026-06-02, US 2026-06-01 |
| `logs/funnel/selection_intraday_evidence_coverage_*.jsonl` | intraday evidence coverage | latest KR 2026-06-02, US 2026-06-01 |
| `logs/hold_advisor/decisions_2026-06-02.jsonl` | hold advisor decisions | 65 records |
| `data/v2_reports/live_preflight_20260602_143240.json` | live preflight truth | KR WARN, US PASS with warning |

## 3. Executive Findings

| Priority | Finding | Status | Why It Matters |
| --- | --- | --- | --- |
| P0 | Candidate audit row-level `data_quality` columns are not synchronized with runtime `runtime_gate` payload. | `DATA_HANDOFF_BUG` | Runtime route may be correct, but DB-only audit can falsely report confirmed evidence as missing. |
| P0 | US previous-session PathB `ORDER_UNKNOWN` rows remain unresolved. | `ORDER_LIFECYCLE_AMBIGUOUS` | The system correctly refuses automatic cleanup, but performance and stale active state remain ambiguous until audited remediation. |
| P0 | V2 learning/canonical performance tables are stale versus event store decisions. | `PERFORMANCE_TRUTH_AMBIGUOUS` | Strategy performance decisions can be made on incomplete data if sync freshness is not restored. |
| P0 | KR PathB has broker-truth/capacity blockers independent of selection quality. | `OPERATING_POLICY_MISMATCH` | KR can produce ready candidates but still have zero executable fixed-order capacity. This must not be treated as a bad strategy signal. |
| P1 | KR intraday evidence coverage degraded and fail-closed was applied. | `BROKER_DATA_OR_PROVIDER_BLOCK` | The guard is intentional, but provider timeout and 50% coverage can suppress valid intraday candidates. |
| P1 | PathB missed/cancelled quality shows positive MFE in several blocked buckets. | `NEEDS_TEST` | Do not loosen gates yet, but split invalid-price/expired causes before deciding whether blocks are too conservative. |
| P1 | Hold advisor decisions have no linked outcome field in the observed log. | `PERFORMANCE_LINK_MISSING` | HOLD/SELL quality cannot be scored cleanly without subsequent PnL/close reason linkage. |

## 4. Report A - Flow Integrity Matrix

| Item | Expected Flow | Observed Flow | Break Type | Intent | Status |
| --- | --- | --- | --- | --- | --- |
| G1 Evidence handoff | `live evidence -> route context -> runtime_gate -> audit columns/payload` | Runtime payload has `minute_complete`, but audit row columns often remain blank. | audit column handoff bug | Not intentional | `DATA_HANDOFF_BUG` |
| G2 Candidate action/routing | Claude action becomes `BUY_READY`, `PULLBACK_WAIT`, `WATCH`, `HARD_BLOCK` with reason. | KR latest: 3 `BUY_READY`, 1 `PULLBACK_WAIT`, 178 watch/hard blocks. US latest: 39 `BUY_READY`, 14 `PULLBACK_WAIT`, 170 watch/hard blocks. | mostly normal route split | Intentional route contract | `OK_WITH_AUDIT_FIX` |
| G3 Strategy activation | Live allowlist/config decides whether code strategy is active. | US momentum is live-critical. KR Plan A/gates remain intentionally conservative. | config/live allowlist | Intentional guard | `INTENTIONAL_GUARD` |
| G4 New-buy/safety | Selection quality is separated from broker/risk/affordability. | KR preflight shows broker truth stale plus fixed-order capacity block. | capacity/broker truth | Safety guard | `OPERATING_POLICY_MISMATCH` |
| G5 PathB entry | `WAITING -> HIT -> ORDER_SENT -> ORDER_ACKED -> FILLED` or explicit block. | US has zone-hit/order flow. KR had high-price/capacity block and broker truth stale. | needs per-row entry trace | Guard plus capacity | `NEEDS_TEST` |
| G6 Pending buy/ORDER_UNKNOWN | Ambiguous orders are preserved until broker evidence/remediation. | 3 US unresolved previous-session rows: IBM, HPE, CRWV. | order lifecycle ambiguity | Intentional fail-safe | `ORDER_LIFECYCLE_AMBIGUOUS` |
| G7 Sizing/capacity | qty block reasons stay split: invalid price, size too small, high price, cash/cap. | KR fixed order 450,000 KRW, cash 1,003,079 KRW, gross remaining 351,077 KRW, today fixed orders 0. | capacity policy mismatch | Intentional but operationally limiting | `INTENTIONAL_GUARD_WITH_P1_ANALYSIS` |
| G8 Exit/hold advisor | Exit signal, hold review, close reason, PnL are linked. | US PathB pre-close/profit ladder are profitable historically, but latest hold-advisor log outcome is blank. | performance linkage missing | Guard must be preserved | `PERFORMANCE_TRUTH_AMBIGUOUS` |
| G9 Sell submit/sellability | SELL attempts are locked/reconciled without stuck duplicate state. | ORDER_UNKNOWN lifecycle includes unresolved sell/fill ambiguity reasons. | order lifecycle ambiguity | Safety guard | `ORDER_LIFECYCLE_AMBIGUOUS` |
| G10 Performance/learning | Event store, canonical performance, learning performance are fresh and broker-backed. | Event store has newer decisions than `v2_learning_performance`/`v2_canonical_performance`. | sync freshness gap | Not intentional | `PERFORMANCE_TRUTH_AMBIGUOUS` |

## 5. Report B - Reason And Performance Matrix

### Candidate Audit Latest Sessions

| Market | Session | Candidate Rows | Prompt Included | Executable/Ready | PathB Wait | Watch Route | Hard Block |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| KR | 2026-06-02 | 1,011 | 512 | 3 `BUY_READY` | 1 `PULLBACK_WAIT` | 178 | 9 |
| US | 2026-06-01 | 1,313 | 630 | 39 `BUY_READY` plus 2 `PROBE_READY` | 14 `PULLBACK_WAIT` | 170 | 16 |

### Candidate Forward Returns

These are candidate-audit forward returns, not realized broker PnL.

| Market | Session | Route/Reason | Count | 30m Avg Return | 60m Avg Return | Finding |
| --- | --- | --- | ---: | ---: | ---: | --- |
| KR | 2026-06-02 | `BUY_READY/buy_ready` | 3 | -1.4483% | -2.5053% | Latest KR ready candidates were weak after routing. Do not loosen KR entry gates from this sample. |
| KR | 2026-06-02 | `PULLBACK_WAIT/pullback_wait` | 1 | +0.9040% | -1.2914% | Single-row sample only. Needs trace-level review. |
| KR | 2026-06-02 | `WATCH/watch` | 156 | +0.1379% | -1.2415% | Watch was not obviously over-conservative at 60m on this session. |
| KR | 2026-06-02 | `WATCH/claude_avoid` | 8 | +4.1483% | +0.2738% | Small sample but possible false-negative bucket. Needs ticker-level review. |
| US | 2026-06-01 | `BUY_READY/buy_ready` | 26 | +1.0625% | +1.3070% | US ready routing remains directionally useful. Preserve US PathB/momentum live path. |
| US | 2026-06-01 | `PULLBACK_WAIT/pullback_wait` | 6 | +1.6195% | +3.5657% | PathB wait route has strong forward move. Entry/order lifecycle matters. |
| US | 2026-06-01 | `WATCH/watch` | 131 | +0.6198% | +1.4851% | Watch bucket also moved; selection/action threshold needs score-bucket analysis, not blanket loosening. |
| US | 2026-06-01 | `WATCH/pullback_wait_blocked_negative_context` | 3 | n/a | +1.9101% | Possible over-conservative negative-context blocker. Needs per-row evidence. |

### V2 Historical Close Reason PnL

| Market | Path | Close Reason | Count | Avg PnL | Finding |
| --- | --- | --- | ---: | ---: | --- |
| US | PathB | `CLOSED_CLAUDE_PRICE_PRE_CLOSE` | 33 | +1.5282% | Protected revenue path. Do not weaken. |
| US | PathB | `CLOSED_PROFIT_LADDER` | 19 | +1.1311% | Protected revenue path. Do not weaken. |
| US | PathB | `CLOSED_CLAUDE_SELL` | 3 | +5.6542% | Positive but small sample. Preserve attribution. |
| US | PathB | `CLOSED_LOSS_CAP` | 14 | -2.0262% | Loss control path. Review false positives separately. |
| KR | PathB | `CLOSED_USER_MANUAL` | 10 | -2.1043% | KR performance remains weak and manual effects must be separated. |
| KR | PathB | `CLOSED_LOSS_CAP` | 7 | -2.8381% | KR risk/offline behavior needs separate investigation before strategy loosen. |

### Strategy/Origin Historical Pattern

| Market | Path/Origin | Strategy | Filled/Closed Pattern | Avg Closed PnL | Finding |
| --- | --- | --- | --- | ---: | --- |
| KR | PathB | `gap_pullback` | 17 closed from 30 rows | -0.6506% | KR gap pullback is not yet a live expansion candidate. |
| KR | PathB | `momentum` | 6 closed from 19 rows | -2.1634% | KR momentum remains weak in current data. |
| US | PathB | `momentum` | 11 closed from 29 rows | +0.6551% | US momentum is profitable enough to keep live allowlist active. |
| US | PathB | `opening_range_pullback` | 6 closed from 21 rows | +0.6673% | Positive but smaller sample. |
| US | PathB/origin blank | `claude_price` | 24 closed from 38 rows | +1.5735% | Attribution needs cleanup, but realized bucket is strong. |
| US | PathB/PULLBACK_WAIT | `claude_price` | 29 closed from 37 rows | +1.1038% | Validates PathB wait as a core US path. |

## 6. Report C - Root Cause Patterns

### C1. Audit `data_quality` Column Mismatch

```text
expected: runtime_gate.data_quality=minute_complete -> audit_candidate_rows.data_quality
actual: runtime_gate payload contains data_quality, but row-level data_quality column is blank
root cause: trading_bot.py::_write_candidate_audit_live() writes runtime_gate into payload but does not promote runtime data_quality/post-open fields into audit columns
runtime result: route may still be correct, DB-only audit can falsely report missing data
performance impact: confirmed evidence rows can be misclassified as blocked/missing in performance analysis
```

Observed latest mismatch:

| Market | Session | Column Blank But Runtime Has `minute_complete` | Other Runtime Quality Mismatch |
| --- | --- | ---: | --- |
| KR | 2026-06-02 | 141 | 36 `first_observed`, 14 `minute_partial` |
| US | 2026-06-01 | 180 | 42 `minute_partial`, 3 `first_observed` |

Important distinction: this is primarily an audit-store handoff bug. It is not proof that live routing missed all these trades.

Relevant code:

- `trading_bot.py::_write_candidate_audit_live()` around runtime audit payload construction.
- `audit/candidate_audit_store.py::upsert_candidate()` supports extra columns, but the caller does not pass the runtime quality fields consistently.
- `runtime/action_routing.py::route_candidate_action()` consumes `data_quality` and `data_quality_missing` in routing decisions.

### C2. Hard-Block Runtime Payload Loses Evidence Detail

```text
expected: hard-block/runtime-filter updates preserve prior runtime evidence details
actual: 14 latest US confirmed hard-block rows have empty runtime evidence details after runtime_filter-style payload
root cause: later audit updates can store a thinner payload with runtime_filter fields only
runtime result: HARD_BLOCK rows remain blocked, usually for already_holding or same-day reentry
performance impact: not an immediate missed-order bug, but root-cause and performance attribution are degraded
```

The 14 US rows inspected were not route-candidate bad-data misses. They were hard-block rows such as `already_holding` or same-day reentry. The improvement is audit evidence preservation, not gate relaxation.

### C3. KR Intraday Evidence Coverage Fail-Closed

```text
expected: intraday provider fetches sufficient minute evidence for candidate pool
actual: latest KR mid-session coverage fetched 13 of 26 requested, coverage_ratio=0.5, fail_closed_applied=true
root cause: provider/prefetch timeout and missing minute evidence
runtime result: valid candidates can be demoted or capped by evidence ceiling
performance impact: requires blocked-route forward return by ticker; latest KR ready returns were weak, but avoid/watch false negatives exist
```

KR latest evidence coverage also showed initial opening records with complete=0, partial=0, missing=30. This is a provider/evidence-readiness axis, separate from strategy quality.

### C4. KR Fixed Order Versus Gross Capacity

```text
expected: ready candidate with good evidence can pass sizing if account capacity supports fixed order
actual: KR fixed_order_krw=450000, orderable_cash_krw=1003079, gross_exposure_remaining_krw=351077.65, today_affordable_fixed_orders=0
root cause: account/gross cap capacity below fixed order amount
runtime result: fixed-order entry can be blocked even when cash exists
performance impact: blocked execution must not be counted as selection failure
```

This is an operating policy mismatch. It may be acceptable, but it must be visible as capacity, not strategy rejection.

### C5. US PathB `ORDER_UNKNOWN` Lifecycle Ambiguity

```text
expected: broker order state is resolved through fresh holdings/open-orders/fills, then lifecycle and PathB rows converge
actual: previous-session ORDER_UNKNOWN rows remain for IBM, HPE, CRWV
root cause: ambiguous broker truth or unresolved sell/fill evidence at session end/TTL expiry
runtime result: preflight warns and suggests audited remediation, not automatic deletion
performance impact: PnL and active-run status remain ambiguous until remediation report is reviewed
```

Current preflight reports unresolved rows=3, current=0, previous=3. It also reports no local exposure and no broker position/open order/fill for those rows, with remediation allowed. This still requires an audited dry-run/apply workflow.

### C6. V2 Performance Sync Lag

```text
expected: v2_event_store decisions -> v2_learning_performance and v2_canonical_performance freshness
actual: learning/canonical tables stop at KR 2026-05-27 and US 2026-05-26, while event store has newer decisions
root cause: sync freshness lag, not necessarily missing event data
runtime result: latest realized performance dashboard/learning can be incomplete
performance impact: strategy allowlist decisions can use stale results if not guarded
```

Dry-run sync result:

```text
selected=512
written=0
filled=188
closed=165
quality grades: CLEAN=95, DIRTY=6, LEGACY_UNKNOWN=293, SUSPECT=118
```

Direct latest gaps:

| Market | Date | Event Decisions | Learning Rows | Gap |
| --- | ---: | ---: | ---: | ---: |
| KR | 2026-06-02 | 3 | 0 | 3 |
| KR | 2026-06-01 | 4 | 0 | 4 |
| US | 2026-06-01 | 14 | 0 | 14 |
| US | 2026-05-29 | 16 | 0 | 16 |
| US | 2026-05-28 | 18 | 0 | 18 |
| US | 2026-05-27 | 13 | 0 | 13 |

## 7. Report D - Item Evidence And Test Checklist

| Item | Code Path | Field Handoff Test | DB/Log Query | Performance Metric | Result | Next Action |
| --- | --- | --- | --- | --- | --- | --- |
| Audit data quality promotion | `trading_bot.py::_write_candidate_audit_live()`, `audit/candidate_audit_store.py` | runtime `data_quality` equals row column | mismatch counts by session | false missing count | `DATA_HANDOFF_BUG` | Promote runtime quality/post-open fields into audit columns and test with fixture. |
| Hard-block payload preservation | `audit/candidate_audit_store.py::_merge_payload()` and runtime-filter audit writes | earlier detailed `runtime_gate` survives later thinner update | hard-block rows with confirmed evidence but empty payload | attribution completeness | `DATA_HANDOFF_BUG` | Preserve runtime evidence keys across runtime_filter/hard-block updates. |
| PathB wait bad-data route | `runtime/action_routing.py::route_candidate_action()` | confirmed/minute_complete can satisfy `good_data` | routing shadow `pathb_waiting_kept_bad_data` | blocked forward return | `OK_LATEST` | Latest logs show zero; keep regression test and audit-column fix separate. |
| KR evidence fail-closed | `_prefetch_selection_intraday_evidence()`, evidence coverage logs | requested/fetched/complete coverage | coverage_ratio/fail_closed | blocked forward return | `NEEDS_TEST` | Split provider timeout, missing minute, and route demotion. |
| KR fixed order/gross cap | `runtime/pathb_runtime.py::_pathb_qty_with_context()`, safety gate | fixed order <= remaining gross capacity | preflight capacity fields | missed ready count | `OPERATING_POLICY_MISMATCH` | Add dashboard/report bucket for capacity block; do not classify as selection failure. |
| US `ORDER_UNKNOWN` | PathB pending buy/sell reconcile and preflight DB checks | broker truth can prove zero exposure before remediation | unresolved rows by path_run_id | realized/provisional PnL ambiguity | `ORDER_LIFECYCLE_AMBIGUOUS` | Run audited ORDER_UNKNOWN remediation workflow, review report before apply. |
| PathB missed/cancelled quality | PathB miss-quality tables/events | invalid price/expired subtype is preserved | miss reason by MFE/MAE | missed MFE, reentry rate | `NEEDS_TEST` | Split `INVALID_PRICE` into stale quote, missing quote, price band, budget. |
| Hold advisor outcome | `minority_report/hold_advisor.py`, `_run_pathb_sell_review_gate()` | decision links to subsequent close reason/PnL | hold advisor decision log plus lifecycle | hold-vs-sell subsequent PnL | `PERFORMANCE_LINK_MISSING` | Add outcome linkage and fallback/cooldown labels; preserve cooldown guard. |
| V2 performance freshness | `tools/sync_v2_learning_performance.py`, V2 DBs | event decisions have matching learning/canonical rows | date gap query | strategy PnL freshness | `PERFORMANCE_TRUTH_AMBIGUOUS` | Restore scheduled/manual sync freshness and dashboard warning. |
| Candidate action early-cycle contract | funnel snapshots | `candidate_actions` contract exists in early snapshots | `candidate_actions_missing_contract` count | early-cycle prompt/action completeness | `MD_GAP` | Add explicit requirement and visibility check. |

## 8. MD Gap Additions Found During Review

These items exist in code/logs but were not explicit enough in the requirements or active operational checklist.

| Missing/Under-Specified Item | Why It Should Be Added | Proposed Location |
| --- | --- | --- |
| Runtime hard-block payload preservation | Hard-block rows can lose detailed evidence even when route decision was intentional. | Report D checklist and G2/G4 audit requirements |
| Candidate action early-cycle missing contract | Latest funnel snapshots show early `candidate_actions_missing_contract` records. This affects prompt/action completeness monitoring. | G2 Candidate Action And Routing |
| Hold advisor outcome linkage | Decision logs have HOLD/SELL, but no outcome field in observed records. | G8 and performance metrics |
| PathB miss-quality subtype taxonomy | `INVALID_PRICE` and `EXPIRED` have positive MFE in missed buckets. Need subtype before gate change. | G5/G7 and Report B |
| V2 freshness gate for strategy allowlist decisions | Performance tables lag event store by several sessions. | G10 acceptance criteria |
| Capacity block reporting as operating policy | KR fixed-order capacity can be zero while cash is nonzero. | G4/G7 and dashboard/preflight visibility |

## 9. Improvement Direction List

### P0 - Fix Data And Truth Before Gate Changes

1. Candidate audit evidence-column handoff
   - Promote `runtime_gate.data_quality`, `evidence_data_state`, post-open quality, and confirmation snapshot into row-level audit columns.
   - Acceptance: latest-session mismatch count for `runtime_gate.data_quality != audit_candidate_rows.data_quality` becomes 0 for routed rows.
   - Test target: focused candidate audit writer fixture plus DB round-trip.

2. Hard-block/runtime-filter payload preservation
   - Preserve prior detailed `runtime_gate` evidence when a later hard-block/runtime-filter update writes a thinner payload.
   - Acceptance: `already_holding`, same-day reentry, and other hard-block rows keep evidence state, quality, route source, and blocker reason.
   - Test target: audit merge test with prompt payload, route payload, then hard-block update.

3. ORDER_UNKNOWN audited remediation
   - Keep fail-safe behavior. Do not auto-delete.
   - Run dry-run evidence report for IBM/HPE/CRWV, review broker truth fields, then apply only if report confirms zero exposure.
   - Acceptance: preflight unresolved previous-session count becomes 0 without changing broker truth priority.

4. V2 performance freshness
   - Restore sync freshness from event store to learning/canonical performance.
   - Acceptance: no date gap for latest KR/US event decisions, or dashboard explicitly shows stale performance warning.

### P1 - Diagnose Conservative Blocks With Per-Reason Metrics

1. KR intraday evidence provider/fail-closed analysis
   - Split by provider timeout, prefetch timeout, fetched missing, minute_partial, minute_missing.
   - Acceptance: KR fail-closed rows have reason counts and forward-return buckets.

2. PathB invalid price/expired miss analysis
   - Do not loosen `INVALID_PRICE` broadly.
   - Split into stale quote, missing quote, invalid price band, quote too old, budget/high-price, expired after no hit.
   - Acceptance: each subtype has count, reentry rate, 30m/60m MFE/MAE.

3. Hold advisor outcome linkage
   - Link decision to next lifecycle close reason and realized/provisional PnL.
   - Preserve AUTO_SELL_REVIEW HOLD cooldown guard.
   - Partial implementation: `trading_bot.py::_update_hold_advisor_jsonl_outcome()` now adds `pnl_at_close`, `hold_delta_pct`, and `hold_outcome` fields as of 2026-06-02.
   - Remaining acceptance: HOLD, SELL, fallback, and cooldown have separate outcome rows or labels.

4. Candidate action contract visibility
   - Track early-cycle snapshots where compact candidate actions are missing.
   - Acceptance: dashboard/log has count, first/last occurrence, and whether it affected prompt inclusion.

### P2 - Reporting And Dashboard Improvements

1. Add a strategy flow health panel with G1-G10 status.
2. Add data-quality mismatch count and V2 performance freshness age to preflight/dashboard.
3. Add KR capacity block as a separate operational policy row, not a selection failure row.
4. Add US PathB protected revenue path metrics by close reason: pre-close, profit ladder, Claude sell, loss cap.
5. Add a candidate missed-opportunity panel that separates filled PnL, watch forward return, hard-block forward return, and shadow would-have return.

## 10. Review Item Classification And Improvement Plan

### 10.1 No-Issue Or Intentional Guard Items

These items should be treated as normal or intentionally protected unless a new failing test, broker evidence, or live incident points directly to them.

| Item | Judgment | Why It Is Not A Bug | Keep/Monitor |
| --- | --- | --- | --- |
| Runtime routing bad-data path | No latest runtime break observed | Latest routing logs show `pathb_waiting_kept_bad_data=0`. The confirmed/missing issue is currently an audit-column mismatch, not a proven live routing miss. | Keep route regression tests and fix audit handoff separately. |
| `PULLBACK_WAIT` route contract | Intentional | `PULLBACK_WAIT` should register PathB wait, not become Path A immediate `trade_ready`. | Preserve `RouteDecision` Path A/B contract. |
| Broker truth fail-closed | Intentional safety guard | KR/US live entry should block when broker truth is stale/untrusted. | Do not relax `_entry_scan_broker_truth_gate()`. Improve visibility only. |
| `ORDER_UNKNOWN` auto-cleanup refusal | Intentional safety guard | Ambiguous order state must not be deleted without broker evidence. | Use audited remediation only. |
| US PathB pre-close/profit ladder | Protected revenue path | Historical US PathB `CLOSED_CLAUDE_PRICE_PRE_CLOSE` and `CLOSED_PROFIT_LADDER` are profitable. | Do not weaken exit priority, hold advisor review, or ladder env values. |
| US momentum live allowlist | Required operating path | US momentum has positive historical PathB performance and supports current revenue path. | Keep allowlist review tied to V2 performance freshness. |
| KR/US strategy split | Correct policy | Same strategy name has different KR/US outcomes. | Keep separate KR/US aggregation before changing shared strategy files. |
| KR capacity block separated from selection quality | Correct attribution | Capacity/fixed-order blocks are execution policy, not candidate quality failure. | Report as operating capacity, not selection miss. See 10.2 for visibility improvement. |
| `state/brain.json` not runtime truth | Correct truth contract | Policy memory must not override broker/event truth. | Keep auto memory promotion blocked. |
| AUTO_SELL_REVIEW HOLD cooldown | Protected safety/cost guard | Prevents repeated Claude calls and protects PathB sell review loop. | Do not remove or weaken without MD violation report. |

### 10.2 Problem Or Improvement Items

These items should remain active because they can distort operations, attribution, performance learning, or future strategy decisions.

| Priority | Item | Problem | Improvement Direction | Acceptance |
| --- | --- | --- | --- | --- |
| P0 | Audit `data_quality` column mismatch | `runtime_gate` has evidence quality but row-level audit columns can remain blank. DB-only analysis can falsely classify confirmed evidence as missing. | Promote runtime evidence fields into audit columns during live audit write. | Latest routed rows have `runtime_gate.data_quality == audit_candidate_rows.data_quality`; mismatch count 0. |
| P0 | Hard-block payload preservation | Later hard-block/runtime-filter updates can store thinner payloads and lose prior evidence details. | Deep-merge/preserve detailed `runtime_gate` evidence while updating hard-block reason. | Hard-block rows keep blocker reason plus prior evidence state, quality, route source, and snapshots. |
| P0 | Previous-session `ORDER_UNKNOWN` | US IBM/HPE/CRWV unresolved rows remain ambiguous. | Run audited dry-run evidence report, then apply only after report review confirms zero exposure. | Preflight unresolved previous-session `ORDER_UNKNOWN` count 0 without auto-delete behavior. |
| P0 | V2 performance freshness | Event store has newer decisions than learning/canonical performance tables. | Restore dry-run checked sync workflow and operator freshness warning. | Latest KR/US event dates match performance tables; if not, preflight has explicit `WARN` and dashboard exposes staleness age/date gap. |
| P0 visibility / P1 policy tuning | KR fixed order versus capacity | Cash can exist while gross remaining capacity is below fixed order amount, yielding 0 affordable fixed orders. | Show as operating capacity block in preflight/dashboard and reports before any sizing/policy change. | KR ready candidates blocked by capacity are not counted as selection failure; preflight/dashboard show fixed order, cash, gross remaining capacity, and affordable fixed-order count. |
| P1 | KR intraday evidence coverage | Provider/prefetch timeout and low coverage can trigger fail-closed demotion. | Split timeout, missing minute, partial minute, and fail-closed reasons. | KR evidence blocks have reason counts and forward-return metrics. |
| P1 | PathB `INVALID_PRICE`/`EXPIRED` broad buckets | Positive MFE exists in missed/cancelled buckets but root cause is too broad. | Add subtype taxonomy before considering gate changes. | Each subtype has count, MFE/MAE, reentry rate, and route impact. |
| P1 | Hold advisor outcome linkage | Partial: `pnl_at_close`, `hold_delta_pct`, and `hold_outcome` fields were added on 2026-06-02, but fallback/cooldown outcome separation is still pending. | Keep the new outcome fields and add fallback/cooldown labels or separate rows. | HOLD, SELL, fallback, and cooldown can be scored separately against subsequent close reason/PnL. |
| P1 | Candidate action early-cycle contract | Early snapshots can miss compact candidate action contract. | Add visibility for missing contract count and affected prompt inclusion. | Funnel logs/dashboard show first/last occurrence and impact. |
| P2 | Dashboard/preflight strategy-flow panel | Operators cannot see all G1-G10 status and freshness at once. | Add strategy-flow health summary with mismatch, capacity, ORDER_UNKNOWN, V2 freshness, hold outcome status. | One operator view separates data bug, policy guard, broker block, and performance stale state. |

### 10.3 Implementation Order

1. Audit handoff and hard-block payload preservation
   - Files: `trading_bot.py`, `audit/candidate_audit_store.py`, `tests/test_candidate_audit.py`.
   - Review scope: `runtime/action_routing.py` and `runtime/live_evidence_pack.py` should be checked for source-field mapping, but should not be changed unless the audit writer cannot receive the correct values from existing route/evidence payloads.
   - Scope: audit DB truth only. No strategy gate, sizing, broker truth, or PathB exit policy changes.
   - Tests: candidate audit round-trip, payload merge preservation, focused `py_compile`.

2. ORDER_UNKNOWN audited remediation
   - Files/tools: `tools/order_unknown_evidence.py`, `tools/order_unknown_remediation.py` if apply is needed.
   - Scope: broker-truth-backed cleanup only. No automatic stale row deletion.
   - Tests/checks: dry-run JSON report, broker holdings/open orders/fills evidence review, preflight after remediation.

3. V2 performance freshness
   - File/tool: `tools/sync_v2_learning_performance.py`.
   - Scope: sync event-store decisions into learning/canonical tables after dry-run review.
   - Tests/checks: dry-run count, date-gap query, preflight explicit WARN, dashboard staleness age/date-gap field.

4. Operational capacity block reporting
   - Files/surfaces: preflight, dashboard/ops summary, and reporting queries.
   - Scope: visibility and attribution only. No fixed-order amount, gross cap, max position, or sizing policy change.
   - Tests/checks: KR capacity block shows fixed order, cash, gross remaining capacity, affordable fixed-order count, and affected ready candidates.

5. P1 diagnostics before gate changes
   - KR evidence provider/fail-closed split.
   - PathB miss-quality subtype split.
   - Hold advisor outcome linkage.
   - Candidate action early-cycle visibility.

No live gate relaxation should be performed until P0 truth fixes are complete and the P1 diagnostics show reason-specific evidence.

## 11. Commands Run

Read-only DB/log analysis and dry-run sync were used. No runtime DB apply/remediation command was executed.

```powershell
python tools/sync_v2_learning_performance.py --market ALL --runtime-mode live --dry-run
```

Additional analysis used read-only SQLite queries against:

```text
data/audit/candidate_audit.db
data/v2_event_store.db
data/ml/decisions.db
data/ticker_selection_log.db
```

and read-only parsing of:

```text
logs/funnel/action_routing_shadow_20260602_KR.jsonl
logs/funnel/action_routing_shadow_20260601_US.jsonl
logs/funnel/selection_intraday_evidence_coverage_20260602_KR.jsonl
logs/funnel/selection_intraday_evidence_coverage_20260601_US.jsonl
logs/hold_advisor/decisions_2026-06-02.jsonl
data/v2_reports/live_preflight_20260602_143240.json
```

## 12. Implementation Pass 1 - 2026-06-02

### 12.1 Implemented

| Item | Status | Files | Notes |
| --- | --- | --- | --- |
| Audit `data_quality` column handoff | Implemented for new writes | `trading_bot.py`, `audit/candidate_audit_store.py` | Live selection audit now promotes runtime/post-open evidence into row columns instead of payload-only storage. |
| `data_quality_missing` audit visibility | Implemented for new writes | `audit/candidate_audit_store.py` | Added audit-only `data_quality_missing` column. Existing DB rows are not backfilled by this patch. |
| Hard-block/runtime-filter payload preservation | Implemented | `audit/candidate_audit_store.py` | Runtime-filter payload merge now preserves detailed `runtime_gate`/evidence keys from earlier payloads. |
| Writer-level regression coverage | Implemented | `tests/test_candidate_action_live_mapping.py` | Verifies live writer stores `data_quality`, `data_quality_missing`, `evidence_data_state`, post-open features, and KR confirmation snapshot columns. |
| Store-level regression coverage | Implemented | `tests/test_candidate_audit.py` | Verifies extra evidence columns round-trip and prompt/runtime-filter payload preservation. |

### 12.2 Not Changed

| Area | Reason |
| --- | --- |
| `runtime/action_routing.py::route_candidate_action()` `good_data` logic | Latest routing logs did not show runtime `pathb_waiting_kept_bad_data`; the confirmed issue was audit truth mismatch, not a proven route gate miss. |
| PathB broker-truth fail-closed | Protected safety behavior. Visibility can improve, but gate relaxation is not part of this pass. |
| PathB sizing/profit ladder/pre-close/hold advisor cooldown | Protected revenue/safety paths. No change in this pass. |
| `ORDER_UNKNOWN` remediation apply | Requires audited dry-run report review before any DB apply. |
| V2 performance sync apply | Dry-run only in this pass; no learning/canonical DB write. |
| `.env*`, `config/v2_start_config.json`, `state/brain.json` | No intentional config/state edits. |

### 12.3 Verification

| Check | Result |
| --- | --- |
| `python -m pytest tests/test_candidate_audit.py::CandidateAuditBackfillTests::test_candidate_audit_preserves_prompt_stage_source_json_and_payload tests/test_candidate_audit.py::CandidateAuditBackfillTests::test_candidate_audit_extra_evidence_columns_round_trip -q` | `2 passed` |
| `python -m pytest tests/test_candidate_action_live_mapping.py::CandidateActionLiveMappingTests::test_candidate_audit_live_write_promotes_runtime_evidence_columns -q` | `1 passed` |
| `python -m pytest tests/test_candidate_audit.py -q` | `32 passed` |
| `python -m pytest tests/test_candidate_action_live_mapping.py::CandidateActionLiveMappingTests::test_candidate_audit_call_prompt_count_uses_actual_prompt_count tests/test_candidate_action_live_mapping.py::CandidateActionLiveMappingTests::test_candidate_audit_live_write_promotes_runtime_evidence_columns tests/test_candidate_action_live_mapping.py::CandidateActionLiveMappingTests::test_candidate_audit_records_shadow_and_live_overlay_payloads -q` | `3 passed` |
| `python -m pytest tests/test_action_routing.py tests/test_live_evidence_pack.py tests/test_trading_bot_intraday_evidence.py -q` | `67 passed` |
| `python -m pytest tests/test_candidate_action_live_mapping.py -q` | `95 passed` |
| `python -m py_compile trading_bot.py audit/candidate_audit_store.py` | Passed |
| `python tools/sync_v2_learning_performance.py --market ALL --runtime-mode live --dry-run` | `selected=512`, `written=0`, `filled=188`, `closed=165` |
| `python tools/live_preflight.py --mode live --skip-dashboard --json` | `ok=true`, `fail_count=0`, `warn_count=15` |

### 12.4 Operational Recheck

| Item | Observation | Interpretation |
| --- | --- | --- |
| Existing historical audit DB mismatch | Latest existing rows still show runtime payload quality where audit row quality is blank: KR 2026-06-02 has 251 rows, US 2026-06-01 has 225 rows. | Expected. This patch fixes new writes only. Historical backfill would be a separate DB write/remediation step. |
| Existing audit DB schema | `data_quality_missing` is not present until `CandidateAuditStore` initializes/migrates the audit DB. | Expected. No live DB migration/backfill was executed in this pass. |
| `ORDER_UNKNOWN` | Preflight still reports 3 previous-session US rows with audited remediation allowed. | Expected. This pass did not apply remediation. |
| KR capacity | Preflight shows fixed order 450,000 KRW, gross remaining capacity 351,077.65 KRW, affordable fixed orders 0. | Expected. This is visibility/attribution work, not a sizing policy change. |
| Broker truth | KR/US broker truth is stale/untrusted but available; readiness stays PASS/WARN style depending state. | Expected. Fail-closed behavior was not relaxed. |
| V2 performance | Dry-run shows the sync candidate set but writes 0 rows. | Expected. Apply sync is a later step after review. |

### 12.5 MD Comparison After Implementation

| MD Requirement | Implementation Pass 1 Result | Remaining Gap |
| --- | --- | --- |
| Audit handoff and hard-block payload preservation first | Completed for new writes. | Historical DB backfill is not done. |
| Do not change strategy gates before truth fixes | Satisfied. | None for this pass. |
| Preserve protected PathB/hold advisor paths | Satisfied. | None for this pass. |
| `ORDER_UNKNOWN` audited remediation | Not executed. | Run remediation dry-run report and review before apply. |
| V2 freshness | Dry-run executed only. | Apply sync or add dashboard staleness age/date-gap field. |
| KR capacity block reporting | Confirmed by preflight. | Dashboard/reporting enhancement still pending. |
| Hold advisor fallback/cooldown separation | Not addressed in this pass. | Add labels/rows after P0 truth fixes. |

No MD file was deleted in this pass. The requirement/review documents remain needed until the P0/P1 implementation and operational verification are complete. Deletion should only happen after active lessons are absorbed into `ACTIVE_WORK.md`, `TODO_ROADMAP.md`, and durable core docs.

## 13. Implementation Pass 2 - 2026-06-02

### 13.1 Implemented / Applied

| Item | Status | Evidence |
| --- | --- | --- |
| Historical audit evidence backfill tool | Implemented | Added `tools/backfill_candidate_audit_runtime_evidence.py` with dry-run/apply split and no-overwrite default. |
| Historical audit evidence backfill apply | Applied to latest KR/US sessions | KR 2026-06-02: `applied=257`; US 2026-06-01: `applied=240`. |
| Audit schema migration | Applied | `audit_candidate_rows.data_quality_missing` now exists in `data/audit/candidate_audit.db`. |
| Audit mismatch recheck | Passed | KR 2026-06-02 and US 2026-06-01 now show `column_missing_runtime_present=0` and `missing_flag_not_backfilled=0`. |
| US previous-session `ORDER_UNKNOWN` remediation | Applied path-run by path-run | IBM, HPE, CRWV moved from `ORDER_UNKNOWN` to `CANCELLED` through `tools/order_unknown_remediation.py --path-run-id ... --apply`. |
| `ORDER_UNKNOWN` preflight recheck | Passed | `db.order_unknown_unresolved PASS no unresolved Path B ORDER_UNKNOWN rows`. |
| V2 performance freshness sync | Applied | `sync_v2_learning_performance.py` wrote `512` learning rows, `512` canonical rows, and `512` decision-link rows. |
| V2 freshness gap recheck | Passed | KR latest learning/canonical date `2026-06-02`; US latest `2026-06-01`; all checked event-vs-learning gaps from 2026-05-27 onward are `0`. |
| KR capacity reporting | Confirmed present | `interface/v2_ops_summary.py`, Telegram, dashboard, and preflight expose `execution_capacity`, `fixed_order_krw`, `gross_exposure_remaining_krw`, `today_affordable_fixed_orders`, and `capacity_block_reasons`. |

### 13.2 Remaining After Pass 2

| Item | Current State | Next Action |
| --- | --- | --- |
| PathB previous-session active `FILLED` rows | Pass 4 resolved EL through audited reconcile. NOK/MRVL remain previous-session `FILLED`, but both match current broker holdings and are normal overnight PathB holds. | No EL follow-up remains. Continue monitoring NOK/MRVL as live overnight holdings, not stale cleanup candidates. |
| Hold advisor fallback/cooldown separation | Pass 3 adds JSONL labels for `fallback`, `cooldown`, `decision_source`, `pending_outcome_label`, and outcome-level `outcome_label`/`advisor_fallback`/`advisor_cooldown`. Plan A/common hold-advisor rows are now separable. PathB cooldown guard itself was not modified. | If PathB cooldown rows must be created when Claude is not called, handle as a separate protected-path exception with MD violation reporting. |
| Dashboard V2 freshness age field | Pass 3 adds `canonical_sync_age_sec`, `canonical_latest_session_date`, `canonical_session_lag_days`, and `canonical_freshness_status` to the dashboard ML digest and displays sync age/date on the page. | Monitor live dashboard after next refresh; no trading-path change. |
| MD cleanup/deletion | Requirements/review docs still contain active residual work. | Do not delete yet; re-evaluate after stale active and hold advisor outcome work. |

### 13.3 Additional Verification

| Check | Result |
| --- | --- |
| `python tools/backfill_candidate_audit_runtime_evidence.py --mode live --market KR --date 2026-06-02 --dry-run` before apply | `eligible=257`, `conflicts=0`, `applied=0` |
| `python tools/backfill_candidate_audit_runtime_evidence.py --mode live --market US --date 2026-06-01 --dry-run` before apply | `eligible=240`, `conflicts=0`, `applied=0` |
| Same commands after apply | KR `eligible=0`; US `eligible=0` |
| `python tools/order_unknown_remediation.py --mode live --market US --session-before 2026-06-02 --dry-run --json` before apply | `eligible_count=3`, `blocked_count=0` |
| Path-run apply for IBM/HPE/CRWV | Each returned `applied_count=1`, `after_status=CANCELLED` |
| ORDER_UNKNOWN dry-run after apply | `total_count=0`, `eligible_count=0` |
| V2 sync dry-run before apply | `selected=512`, `filled=188`, `closed=165`, `learning_allowed=28` |
| V2 sync apply | `written=512`, `canonical_written=512`, `decision_links_written=512` |
| Preflight after pass 2 | `ok=True`, `fail_count=0`, `warn_count=13`; `ORDER_UNKNOWN` PASS |
| `python tools/live_maintenance.py status --mode live --json` | Writer freeze failed: active `live_bot` and `guardian` processes are running. |
| `python tools/live_maintenance.py reconcile-position --mode live --market US --ticker EL --path-run-id path_20260526_US_EL_claude_price_1f8be6e8 --dry-run --json` | `action=manual_review`, `reason_code=BROKER_TRUTH_MISSING_OR_STALE`, `status_before=FILLED`, `local_position_found=false`, no DB write. |

### 13.4 Implementation Pass 3 - 2026-06-02

| Item | Status | Evidence |
| --- | --- | --- |
| Hold advisor outcome labels | Implemented | `minority_report/hold_advisor.py::_log_decision` records `fallback`, `cooldown`, `decision_source`, and `pending_outcome_label`; `trading_bot.py::_update_hold_advisor_jsonl_outcome` carries those into outcome rows as `outcome_label`, `advisor_fallback`, and `advisor_cooldown`. |
| Dashboard V2 freshness age | Implemented | `dashboard/dashboard_server.py::_ml_db_digest` now exposes canonical sync age, latest session date, session lag days, and freshness status; the dashboard ML DB line displays sync age/date for V2 truth. |
| Protected PathB cooldown row creation | MD/test-only | The protected PathB AUTO_SELL_REVIEW cooldown guard was not modified. Existing guard regression `test_pathb_loss_cap_hold_respects_reask_cooldown` still passes and explicitly preserves one Claude call across cooldown rechecks. |
| Live audit drift backfill | Applied | While the live bot kept running, KR 2026-06-02 produced 15 new eligible audit rows. Applied `tools/backfill_candidate_audit_runtime_evidence.py --mode live --market KR --date 2026-06-02 --apply`; KR/US dry-run recheck then returned `eligible_count=0`. |

| Pass 3 Verification | Result |
| --- | --- |
| `python -m pytest tests/test_price_unit_normalization.py::PriceUnitNormalizationTests::test_hold_advisor_log_marks_fallback_for_outcome_linkage tests/test_price_unit_normalization.py::PriceUnitNormalizationTests::test_hold_advisor_outcome_preserves_fallback_and_cooldown_labels -q` | `2 passed` |
| `python -m pytest tests/test_dashboard_refresh_performance.py::DashboardRefreshPerformanceTests::test_ml_db_digest_prefers_canonical_fill_truth_when_available -q` | `1 passed` |
| `python -m pytest tests/test_price_unit_normalization.py tests/test_trading_decision_contract_improvements.py tests/test_dashboard_refresh_performance.py::DashboardRefreshPerformanceTests::test_ml_db_digest_prefers_canonical_fill_truth_when_available tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q` | `46 passed` |
| `python -m py_compile trading_bot.py minority_report/hold_advisor.py dashboard/dashboard_server.py` | Passed |
| `python -m pytest tests/test_candidate_audit.py tests/test_candidate_action_live_mapping.py tests/test_price_unit_normalization.py tests/test_dashboard_refresh_performance.py::DashboardRefreshPerformanceTests::test_ml_db_digest_prefers_canonical_fill_truth_when_available tests/test_order_unknown_remediation.py tests/test_pathb_legacy_remediation.py -q` | `148 passed` |
| `python tools/live_preflight.py --mode live --skip-dashboard --json` | `ok=True`, `fail_count=0`, `warn_count=14`; `ORDER_UNKNOWN` PASS; `db.pathb_stale_active_runs` remains WARN with 3 previous-session active PathB rows. |

### 13.5 Implementation Pass 4 - 2026-06-02

| Item | Status | Evidence |
| --- | --- | --- |
| EL stale `FILLED` reconcile semantics | Implemented | `tools/live_maintenance.py` now treats absent broker position/open order with prior `FILLED` or `PARTIAL_FILLED` run status as audited close, not order cancel. |
| EL audited reconcile apply | Applied | Fresh US broker truth showed no EL position, no EL open order, and no EL same-day fill. Live writers were frozen before apply. Backup: `data/backups/live_maintenance_20260602_080341_before_reconcile-position`. |
| EL final DB state | Verified | `path_20260526_US_EL_claude_price_1f8be6e8` is now `status=CLOSED`, `close_reason=CLOSED_AUDITED_BROKER_ABSENT`, `learning_excluded=true`, `pnl_pct=NULL`, `exit_fill_confirmed=false`, `broker_position_absent_after_fill_reconciled=true`. |
| EL lifecycle event | Verified | Latest EL event is `event_id=4409`, `event_type=CLOSED`, `reason_code=CLOSED_AUDITED_BROKER_ABSENT`, `source=tools.live_maintenance`, `learning_excluded=true`. |
| V2 performance sync after EL close | Applied | US sync wrote `346` learning rows, `346` canonical rows, and `346` decision-link rows; closed count `123`, filled count `140`, learning-allowed count `5`. |
| Live writer restart | Verified | After reconcile, live bot and live guardian were restarted. `tools/live_maintenance.py status --mode live --json` reports active live writer processes and `frozen=false`. |
| Preflight after Pass 4 | Verified | `python tools/live_preflight.py --mode live --skip-dashboard --json` returned `ok=True`, `fail_count=0`, `warn_count=12`; `db.order_unknown_unresolved` PASS; previous-session active rows dropped to 2 and are NOK/MRVL broker-held overnight positions. |

| Pass 4 Verification | Result |
| --- | --- |
| `python -m pytest tests/test_live_maintenance.py::LiveMaintenanceTests::test_msft_absent_broker_apply_removes_local_and_cancels_path_run tests/test_live_maintenance.py::LiveMaintenanceTests::test_pending_sell_without_broker_fill_is_manual_review tests/test_live_maintenance.py::LiveMaintenanceTests::test_absent_filled_pathb_run_closes_as_audited_learning_excluded -q` | `3 passed` |
| `python -m pytest tests/test_live_maintenance.py::LiveMaintenanceTests::test_msft_absent_broker_apply_removes_local_and_cancels_path_run tests/test_live_maintenance.py::LiveMaintenanceTests::test_pending_sell_without_broker_fill_is_manual_review tests/test_live_maintenance.py::LiveMaintenanceTests::test_absent_filled_pathb_run_closes_as_audited_learning_excluded tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown tests/test_price_unit_normalization.py tests/test_dashboard_refresh_performance.py::DashboardRefreshPerformanceTests::test_ml_db_digest_prefers_canonical_fill_truth_when_available -q` | `13 passed`, 2 third-party deprecation warnings |
| `python -m py_compile tools/live_maintenance.py` | Passed |
| `python -m py_compile tools/live_maintenance.py trading_bot.py minority_report/hold_advisor.py dashboard/dashboard_server.py` | Passed |
| `git diff --check -- tools/live_maintenance.py tests/test_live_maintenance.py tests/test_auto_sell_claude_gate.py docs/important/STRATEGY_FLOW_AUDIT_REVIEW_20260602.md docs/important/ACTIVE_WORK.md docs/important/core/TODO_ROADMAP.md` | Passed |
| DB query for EL run and lifecycle event | EL closed as audited broker-absent, learning excluded, no PnL truth fabricated. |

### 13.6 PathB Cooldown Row Decision

The operator direction for item 2 is MD/test-only for now. No direct `runtime/pathb_runtime.py` implementation is included in this pass.

Future direct implementation, if explicitly requested, must be handled as a protected-path exception because it touches the PathB `AUTO_SELL_REVIEW` HOLD cooldown guard. Acceptance criteria:

| Requirement | Acceptance |
| --- | --- |
| No extra Claude call | Cooldown recheck must keep advisor call count unchanged and record `claude_called=false`, `tokens=0`, or equivalent no-call telemetry. |
| Outcome separability | Cooldown rows/labels must be distinguishable from normal HOLD/SELL/fallback rows by `decision_source=auto_sell_review_cooldown` and `cooldown=true` or equivalent fields. |
| Guard preservation | `test_pathb_loss_cap_hold_respects_reask_cooldown` must continue to pass and prove one Claude call across repeated cooldown rechecks. |
| MD violation reporting | If runtime cooldown row creation is implemented, add an `MD violation` section explaining protected area touched, before/after behavior, Claude-call/token impact, and tests. |

### 13.7 Implementation Pass 5 - P0/P1 Execution Closure - 2026-06-02

| Item | Status | Evidence |
| --- | --- | --- |
| Actual-prompt profit visibility | Implemented | `tools/analyze_candidate_audit.py` now emits `actual_prompt_profit_visibility`, separating actual prompt included/not-included/unmeasured rows and their return/MFE/MAE metrics. This removes reliance on legacy `input_to_claude` or timestamp joins. |
| Bucket/source/score data quality | Implemented | `tools/analyze_candidate_audit.py` now emits `bucket_source_score_quality`, including blank bucket/source rates, raw/trainer score missing rates, bucket/source counts, and examples. |
| KR entry/exit shadow readiness | Implemented as observe gate | `tools/analyze_candidate_audit.py` now emits `entry_exit_shadow_readiness`, including filled/outcome rows, timing/post-open/MFE/MAE coverage, top-day concentration, sample gate status, and blockers. It does not change live entry/exit behavior. |
| KIS `EGW00133` ops status | Implemented | `tools/live_preflight.py` now emits `kis.token_rate_limit_cooldown`, scanning KIS token issue rate-limit marker files and surfacing active cooldown as WARN with operator action. |
| Broker-truth zero-holding fixtures | Implemented | Added Plan A zero-holding evidence tests for fresh KR zero-holding and US open remaining-qty block. Existing PathB zero-holding tests were re-run. |
| PathB broker-truth gate visibility | Verified | Existing PathB runtime and ops summary tests cover token/provider unavailable fail-closed, refresh-before-buy, stale truth warning, active-market hard block, and execution capacity visibility. |
| PathB pending-buy TTL/order matching | Verified | Existing tests cover sent/ACK timestamps, ACK-time TTL, mismatched fill/order refusal, unique open order fallback, and previous-session local holding exit scan. No runtime policy change. |
| US PathB sizing reason split | Verified | Existing sizing tests cover one-share early-gate floor, insufficient full budget, no-early-gate behavior, and live order safety reason split. No sizing policy change. |
| V2 canonical freshness/fallback exclusion | Verified | Existing sync/dashboard tests plus preflight report confirm canonical freshness is visible and learning-excluded/fallback rows are handled as exclusions. |
| Brain/sub-screener/runtime tuning guards | Verified | Existing tests cover brain JSON preflight guard, sub-screener market-scoped trigger behavior, and bounded runtime adjustment keys. |
| PathB fill truth / sell pending / EXPIRED monitoring | Verified | Existing reconcile and PathB runtime tests cover partial sell remaining qty, pending sell evidence, EXPIRED stop-breach sell, and stale closing cleanup. |
| PathB cooldown no-call row | Planned only | Kept as P1 protected-exception plan. No direct `runtime/pathb_runtime.py` cooldown branch modification was made. |

| Live Data Recheck | Result |
| --- | --- |
| `docs/reports/candidate_audit_analysis_KR_20260602.json` | KR candidate rows `122`; actual prompt measured `121`, unmeasured `1`; status `awaiting_outcomes`; bucket quality `ok`; blank bucket rate `0.82%`; shadow status `observe_only` because sample/feature gates are not mature. |
| `docs/reports/candidate_audit_analysis_US_20260601.json` | US candidate rows `162`; actual prompt measured `158`, unmeasured `4`; status `awaiting_outcomes`; bucket quality `ok`; blank bucket rate `2.47%`; shadow status `observe_only` because sample/feature gates are not mature. |
| `docs/reports/live_preflight_after_p0_p1_20260602.json` | `ok=true`, `fail_count=0`, `warn_count=14`; `kis.token_rate_limit_cooldown=PASS`; `db.order_unknown_unresolved=PASS`; stale active PathB rows remain `2` and are broker-held NOK/MRVL overnight positions. |

| Pass 5 Verification | Result |
| --- | --- |
| `python -m pytest tests/test_candidate_audit.py tests/test_candidate_action_live_mapping.py tests/test_screener_quality.py -q` | `143 passed`, 2 third-party deprecation warnings |
| `python -m pytest tests/test_pathb_runtime.py::PathBRuntimeTests::test_entry_scan_broker_truth_gate_blocks_when_token_unavailable ... test_cached_carry_does_not_block_stop_exit -q` | `11 passed` |
| `python -m pytest tests/test_live_order_safety.py tests/test_pathb_runtime.py::EarlyGateFloorOneShareTests tests/test_v2_learning_performance_sync.py tests/test_brain_execution_integrity.py tests/test_intraday_tuning_market_scope.py -q` | `46 passed`, 2 third-party deprecation warnings |
| `python -m pytest tests/test_live_preflight_credentials.py tests/test_kis_token_auto_refresh.py -q` | `13 passed` |
| `python -m pytest tests/test_broker_sync_metadata_integrity.py tests/test_pathb_runtime.py::PathBRuntimeTests::test_pathb_sell_zero_holding_fresh_broker_truth_closes_stale_run tests/test_pathb_runtime.py::PathBRuntimeTests::test_pathb_sell_zero_holding_stale_broker_truth_keeps_local_state -q` | `7 passed`, 2 third-party deprecation warnings |
| `python -m pytest tests/test_sub_screener.py tests/test_sub_screener_integration.py tests/test_live_preflight_ml_and_brain.py tests/test_effective_runtime_config.py tests/test_market_breadth_prompt_contract.py::TunePromptContractTests::test_tune_bounds_match_policy -q` | `35 passed`, 2 third-party deprecation warnings |
| `python -m pytest tests/test_live_preflight_credentials.py tests/test_live_preflight_ops_summary.py tests/test_v2_phase6.py::V2Phase6Tests::test_pathb_readiness_closed_market_reports_stale_truth_as_warning tests/test_v2_phase6.py::V2Phase6Tests::test_pathb_readiness_active_market_keeps_stale_truth_hard_block tests/test_v2_phase6.py::V2Phase6Tests::test_pathb_execution_capacity_uses_broker_orderable_cash tests/test_v2_phase6.py::V2Phase6Tests::test_buy_capacity_telegram_command_reports_capacity_snapshot -q` | `12 passed`, 2 third-party deprecation warnings |
| `python -m pytest tests/test_pathb_sell_reconcile.py tests/test_pathb_sell_reconcile_backfill.py tests/test_pathb_sell.py tests/test_pathb_runtime.py::PathBRuntimeTests::test_pathb_sell_in_flight_detects_pending_sell_evidence tests/test_pathb_runtime.py::PathBRuntimeTests::test_pathb_expired_policy_stop_breach_still_sells tests/test_pathb_runtime.py::PathBRuntimeTests::test_stale_pathb_closing_is_cleared_for_still_held_position -q` | `25 passed` |
| `python -m pytest tests/test_candidate_post_rank_and_us_quality.py tests/test_candidate_quality_trainer.py tests/test_candidate_trainer_replacement.py tests/test_report_claude_misjudgments.py -q` | `62 passed`, 2 third-party deprecation warnings |
| `python -m py_compile tools/analyze_candidate_audit.py tools/live_preflight.py trading_bot.py runtime/pathb_runtime.py` | Passed |

## 14. Bottom Line

P0/P1 실행 범위에서 가장 먼저 고쳐야 했던 것은 전략 게이트 완화가 아니라 감사/성과 truth의 연결과 운영 가시성이다.

Pass 1~5로 audit handoff, hard-block payload preservation, historical backfill, ORDER_UNKNOWN remediation, EL stale active cleanup, V2 freshness, hold advisor labels, actual-prompt visibility, bucket/source/score quality, KIS rate-limit ops status, zero-holding fixtures, and protected-path tests were implemented or verified.

아직 live 정책을 바꾸면 안 되는 항목은 sample gate가 부족한 entry/exit shadow, raw-score/multi-source promotion, KIS primary promotion, and PathB cooldown no-call row direct implementation이다. 이들은 observe/protected 상태로 두며, US PathB pre-close/profit ladder/claude_price 수익 경로는 보호 영역으로 유지하고, KR/US 성과를 같은 전략명으로 섞어 판단하면 안 된다.
