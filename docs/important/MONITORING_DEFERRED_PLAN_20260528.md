# Monitoring Deferred Plan

Updated: 2026-05-28

This plan holds the `▲` and `X` items removed from the immediate development requirements. These items may have report-only code written now, but they must not change live behavior until the listed gates pass.

## Classification

| Mark | Meaning | Current handling |
|---|---|---|
| ▲ | Conditional / deferred | Report-only instrumentation may be implemented. Live policy effect waits for gates. |
| X | Blocked | Do not implement without a new approval and safety document. |

## ▲ Items

| ID | Item | Report-only work allowed now | Waiting condition | Why it must wait |
|---|---|---|---|---|
| D1 | candidate bucket/source/score performance report | Join bucket/source/score to 30/60m and later 1D/3D outcomes; emit blockers. | 1D/3D non-null coverage passes threshold. | Coverage-only quality is safe, but performance conclusions can be wrong without long labels. |
| D2 | WATCH_TRIGGER policy judgment | Join shadow events to outcomes and show `policy_change_allowed=false`. | 1D/3D labels, matched rate, sample size, and concentration gates pass. | Route relaxation can increase live entries from weak shadow evidence. |
| D3 | Screener diversity `r31_60` challenger report | Add rank-band report, `rank_source`, and `promotion_gate_state`. | Phase 0 gates: `screen_rank` coverage >= 95%, actual prompt measured >= 90%, 1D/3D label non-null >= 80%, cohort sample gates. | Current evidence is mostly 30/60m and lacks implemented rank-band gate fields. |
| D4 | KR entry/exit counterfactual policy decision | Add blocker-aware counterfactual report. | 30 filled trades or 4 calendar weeks, top-day contribution < 40%, broker-fill-aware replay. | Current path metrics are not enough to change first-entry, stop, or exit behavior. |
| D5 | US projected dollar volume filter change | Analyze shadow JSONL and outcome linkage. | Outcome linkage, label coverage, and comparison against current filter pass. | Current shadow is intentionally `selection_behavior_changed=false`; filter changes need outcome evidence. |
| D6 | Hold advisor live cache enablement | Build simulator and report estimated hit/invalidated rates. | Separate approval after tests prove loss-cap, near-close, forced-sell, and position-identity protections. | Live cache changes sell review behavior and could suppress needed Claude rechecks. |
| D7 | US KIS ranking or intraday primary promotion | Add/repair shadow overlap, timestamp, latency, rate-limit, and outcome report. | 3-5 sessions shadow plus coverage/latency/rate-limit/outcome gates. | KIS primary can increase broker API load and affect broker truth stability. |
| D8 | PEAD prompt-visible surprise promotion | Generate manual review report. | `state/pead_shadow_state.json` checklist complete and explicit operator switch. | Surprise fields are guarded by policy and must not leak into prompts early. |
| D9 | V2 learning gate unlock | Report exclusion reasons. | Clean forward-complete rows and separate approval. | Learning gate protects against ORDER_UNKNOWN, forward-pending, and legacy unknown contamination. |

## X Items

| ID | Blocked action | Reason |
|---|---|---|
| X1 | Resolve ORDER_UNKNOWN or stale PathB rows from local DB only. | Broker positions, open orders, and fills are source of truth. |
| X2 | Weaken PathB broker-truth entry fail-closed behavior. | Live entry must block on token/provider unavailable, stale, or error broker truth. |
| X3 | Change PathB sizing, fixed order amount, max positions, daily entry cap, slippage, hard stops, or protective hold distances from monitoring evidence. | These are operating parameters and require operator approval. |
| X4 | Remove or relax PathB AUTO_SELL_REVIEW HOLD cooldown. | It prevents repeated Claude calls and token/cost spikes. |
| X5 | Force `learning_allowed=1` or bypass canonical quality gates. | It contaminates adaptive learning with unresolved or incomplete rows. |
| X6 | Set PEAD surprise prompt-visible fields without the manual review gate. | Existing policy requires 5 trading days plus manual checklist and explicit switch. |
| X7 | Promote `r31_60`, WATCH_TRIGGER, projected dollar volume, or KIS ranking directly to live trade_ready/primary provider. | Current evidence is report-only and lacks complete long-horizon gates. |
| X8 | Write `state/brain.json` from monitoring results. | `brain.json` is policy memory, not runtime truth. |

## Deferred Report Output Requirements

Every ▲ report must include:

- `promotion_gate_state`
- `promotion_gate_reason`
- sample count
- label coverage by horizon
- top-day concentration when performance is used
- source fields used for matching
- `policy_change_allowed=false` unless every gate is explicitly passed

## Return-To-Development Criteria

A ▲ item can move back to immediate development only when:

1. Required labels and sample gates pass.
2. The report has no unresolved broker-truth or data-quality blockers.
3. The requested implementation does not change protected runtime contracts.
4. If it changes live behavior, a new approval document lists risk, config/env impact, order/risk/broker-truth impact, Claude call impact, tests, and rollback.
