# Code-Level Requirements

Date: 2026-05-22

Purpose: judge what is completed versus not completed from the current code, then define the remaining implementation requirements.

## Code-Level Judgment

| Item | Code Status | Evidence | Judgment |
| --- | --- | --- | --- |
| P0-1 sub-screener base | Partially complete | `runtime/sub_screener.py`, `TradingBot.maybe_run_sub_screener()`, `tests/test_sub_screener*.py` | Scanner, counters, rate limit, reinvoke/rescreen path, and tests exist. Effective live/shadow policy is still ambiguous because market-scoped flags override the global flag. |
| P0-2 zero-holding reconcile | Partially complete | `TradingBot._sell_zero_holding_broker_evidence()`, `PathBRuntime._pathb_zero_holding_broker_evidence()` | Code blocks stale/untrusted broker truth and can remove local stale positions only after fresh zero-holding truth. Still needs real KIS payload fixture coverage and operator visibility. |
| P0-3 PathB entry broker-truth gate | Partially complete | `PathBRuntime._entry_scan_broker_truth_gate()` | Entry scan can refresh broker truth and block on stale/untrusted state. Still needs preflight/ops visibility and config documentation. |
| P0-4 profit-review timeout fallback | Partially complete | `PathBRuntime._profit_review_timeout_payload()` and timeout/debounce state | Timeout is converted to HOLD fallback with `advisor_unavailable` and `learning_excluded`. Still needs dashboard/canonical/report visibility so it is not read as strategy HOLD. |
| P1-1 prompt pool/evidence alignment | Instrumented, not validated | `selection_trace_id`, `missing_evidence_tickers`, `missing_exec_tickers`, overlap metrics | Runtime instrumentation exists. Needs next-session metric review after prompt hard-cap changes. |
| P1-2 US KIS ranking screener | Not complete | `kis_api.screen_market_us()` still has no `token` parameter and no KIS overseas ranking branch | Implement KIS ranking first-source path with safe fallback. |
| P1-3 V2 canonical truth | Code mostly complete, ops incomplete | `tools/sync_v2_learning_performance.py`, `strategy/adaptive_params.py`, dashboard digest, candidate audit link fields | Canonical and link code exists. Needs runbook, freshness check, and daily operation rule. |
| P1-4 counterfactual outcomes | Code mostly complete, ops incomplete | `tools/update_counterfactual_outcomes.py`, `tools/analyze_counterfactual_paths.py`, tests | Store/updater/analyzer exist. Needs scheduled run and review contract. |
| P1-5 KR confirmation/fade shadow | Complete at code level | `6f8fdc1`, `minute_complete` accepted, `runtime/live_evidence_pack.py`, `runtime/adaptive_live_condition.py`, related tests | No longer active implementation work. Keep KR fade-recovered as P2 observation only. |

## Requirement R1 - Sub-Screener Effective Trigger Contract

Current code truth:

- `SUB_SCREENER_ENABLED=true` enables scanning.
- `SUB_SCREENER_TRIGGER_ENABLED=false` does not necessarily mean shadow.
- `_sub_screener_trigger_enabled()` uses `SUB_SCREENER_KR_TRIGGER_ENABLED` or `SUB_SCREENER_US_TRIGGER_ENABLED` when those env vars exist.
- If both scoped trigger and global trigger are absent, `_sub_screener_trigger_enabled()` defaults to live trigger because the global fallback default is `True`.
- Current tracked config has both market-scoped trigger flags set to `true`.

Required behavior:

- Expose effective trigger state per market using the same resolution rule as `_sub_screener_trigger_enabled()`.
- Effective-state visibility can be implemented before policy confirmation because it is read-only.
- Config changes that alter live/shadow trigger behavior require operator confirmation first.
- Preflight or ops summary must report:
  - global trigger flag
  - KR scoped trigger flag
  - US scoped trigger flag
  - effective KR trigger
  - effective US trigger
  - max attempts per session
  - min interval
  - last detection and last success if state file exists
- If the intended policy is shadow, config must set market-scoped trigger flags false or remove them.
- If the intended policy is selective live trigger, document that scoped flags intentionally override global false.
- Decide whether absent scoped/global trigger config should remain fail-open (`True`) or be changed to fail-closed (`False`). Until that decision is made, preflight must surface the defaulted-live state.

Operator confirmation gate:

```text
Current code-level effective state:
SUB_SCREENER_TRIGGER_ENABLED=false
SUB_SCREENER_KR_TRIGGER_ENABLED=true
SUB_SCREENER_US_TRIGGER_ENABLED=true
=> KR effective trigger=true, US effective trigger=true

Before changing config, confirm intended policy:
1. KR/US both shadow: set market-scoped flags false or remove scoped overrides.
2. KR/US both live trigger: keep scoped true and document override as approved.
3. Split policy: keep only the approved market scoped true.
```

Tests:

- Global false + scoped absent -> effective false.
- Global false + scoped true -> effective true and preflight shows override.
- Global true + scoped false -> effective false for that market.
- Global absent + scoped absent -> effective true under current code, and preflight/ops must label it as `default_live`.
- Trigger disabled records scan only and does not call `record_attempt()`, `_reinvoke_analysts()`, or `manual_rescreen()`.

Done when:

- Operator can see the effective state without reading code.
- No one can infer live/shadow status from `SUB_SCREENER_TRIGGER_ENABLED` alone.

## Requirement R2 - Broker-Truth Zero-Holding Reconcile Fixtures

Required behavior:

- Local position removal or PathB run closure is allowed only when all are true:
  - broker truth is fresh
  - broker position quantity for ticker is zero
  - broker open remaining quantity for ticker is zero
  - no stale/error/missing broker truth marker exists
- If broker truth is stale, missing, errored, or open order exists, keep local position and emit risk event.
- The risk event must include broker truth source fields and mark the event as execution safety, not strategy signal.

Tests:

- KR position payload with zero qty and no open order removes stale local position.
- US position payload with zero qty and no open order removes stale local position.
- Open order with remaining quantity prevents removal.
- Stale broker truth prevents removal.
- Unrecognized KIS row key does not falsely match ticker.

Done when:

- The destructive reconcile path is covered by realistic KR/US broker payload fixtures.

## Requirement R3 - PathB Entry Broker-Truth Gate Ops Visibility

Required behavior:

- Preflight or ops summary must expose:
  - `PATHB_ENTRY_SCAN_BROKER_TRUTH_REFRESH_ENABLED`
  - TTL and min interval
  - last refresh attempted
  - last success/failure
  - last latency
  - last error
  - current block reason when blocked
- Token/provider unavailable behavior must be explicit:
  - paper mode can skip refresh
  - live token unavailable must not silently create a false-safe path

Tests:

- Refresh failure blocks live entry and reports `BLOCKED_BROKER_TRUTH`.
- Paper mode skip is reported as skipped, not success.
- Ops summary includes refresh metrics after one failed and one successful refresh.

Done when:

- A PathB entry block from broker truth can be diagnosed from preflight/ops output without log scraping.

## Requirement R4 - Profit Review Timeout Fallback Visibility

Required behavior:

- Dashboard/ops must show timeout fallback separately from advisor HOLD.
- Fields to expose:
  - `profit_review_fallback`
  - `profit_review_fallback_reason`
  - `profit_review_timeout`
  - `timeout_count`
  - `advisor_unavailable`
  - `learning_excluded`
- Canonical/learning reports must not count timeout fallback HOLD as strategy quality.

Tests:

- Timeout row appears as fallback/advisor unavailable in ops output.
- `learning_excluded=true` prevents inclusion in learning/performance aggregate where applicable.
- Debounce rows do not create repeated strategy HOLD judgments.

Done when:

- Operator can distinguish "Claude/Hold advisor said HOLD" from "advisor timed out, system held safely."

## Requirement R5 - US KIS Ranking Screener

Current code truth:

- `kis_api.screen_market_us(top_n, mode)` has no `token` parameter.
- US screening source remains Yahoo Finance -> FMP -> fallback.

Required behavior:

- Extend signature:

```python
def screen_market_us(top_n: int = 30, mode: str = "NEUTRAL", *, token: str | None = None) -> list:
    ...
```

- `trading_bot._screen_market_candidates()` passes `self._token_for_market("US")`.
- KIS overseas ranking is first source when enabled and token exists.
- Initial implementation should support shadow comparison before first-source promotion.
- Required P0 endpoints:
  - `/uapi/overseas-stock/v1/ranking/trade-vol`
  - `/uapi/overseas-stock/v1/ranking/updown-rate`
- Token missing, API failure, empty response, or quality failure falls back to existing Yahoo/FMP/cache path.
- Shadow comparison log must include quality metrics, not only ticker overlap:
  - KIS raw candidate count and Yahoo/FMP raw candidate count
  - raw overlap count and raw overlap ratio
  - final screened overlap count and `final_candidate_overlap_ratio`
  - price distribution: min, median, max, min-price fail count
  - volume or dollar-volume pass/fail count
  - change_pct distribution and max-change fail count
  - category counts: `most_actives`, `day_gainers`, `day_losers`
  - fallback reason: `token_missing`, `api_error`, `empty_response`, `quality_fail`, or `disabled`
- No order, risk, PathB, broker truth, sizing, or stop logic changes.

Tests:

- Existing no-token calls keep old behavior.
- KIS success normalizes actives/gainers/losers into existing candidate schema.
- KIS failure falls back without raising.
- Cache TTL and force-refresh behavior remain compatible.
- Shadow comparison records raw overlap and final screened overlap separately.
- Quality failure can be detected when overlap is acceptable but price/volume/change filters are broken.

Done when:

- US screener can prefer KIS ranking while preserving current fallback and safety behavior.

## Requirement R6 - V2 Canonical Truth Runbook And Freshness Check

Required behavior:

- Document the daily sync command for `tools/sync_v2_learning_performance.py`.
- Define when `--repair-decisions` is allowed.
- Add a guardian/preflight or dashboard freshness check for `v2_canonical_performance` sync time.
- Label legacy `decisions.db` as signal/evaluation history when canonical truth is unavailable.

Done when:

- Daily operations can tell whether canonical truth is fresh before using dashboard/adaptive performance judgments.

## Requirement R7 - Counterfactual Outcome Schedule

Required behavior:

- Define scheduled command for `tools/update_counterfactual_outcomes.py`.
- Run analyzer after updater and review by market/bucket.
- Keep status/source/metadata quality visible:
  - `PRICE_PENDING`
  - `DATA_MISSING`
  - `PRICE_UNAVAILABLE`
  - `OUTCOME_PARTIAL`
  - close outcome filled
- Gate changes require outcome evidence, not one-off examples.

Done when:

- Blocked/watch-only candidates have recurring 30m/60m/close outcomes available for policy review.

## Completed Item Rule - KR Fade Recovered

`P1-5` is complete at code level. Do not keep it as pending implementation.

Remaining rule:

- Keep `fade_recovered_shadow` in observation.
- Do not enable live `PROBE_READY`, US fade relaxation, or PathB wait exception from this work.
- Promotion requires separate approval, sample review, and a new requirements document.
