# Always Analyze

Updated: 2026-05-27

Use this checklist before changing live behavior, market policy, prompts, learning data, or dashboard truth.

## Core

- Broker truth first: holdings, open orders, fills, quarantine, and ORDER_UNKNOWN beat local cache.
- Profitability work has top priority, but measurement/shadow gates come before live behavior changes. Do not improve returns by bypassing broker truth, hard stops, order sizing approval, or PathB live gates.
- Separate selection quality from execution/risk failures. Do not fix a selection issue by changing risk, and do not fix an execution issue by changing Claude selection.
- Keep Path A and Path B traceable through route, origin action, decision id, execution id, and fill status.
- Do not treat `state/brain.json` as runtime truth or an auto-learning target. New lessons go through candidate scoring or approval first.
- Do not change order sizing, hard stop behavior, PathB live gates, or broker-truth priority without explicit operator review.
- Provider outage is not market HALT. Unavailable analysts are excluded from consensus; quorum failure blocks new entries but existing position protection continues.

## Market Judgment

- KR baseline: KOSPI, KOSDAQ. Shadow additions: KOSPI200, KOSDAQ150, VKOSPI, KRX300.
- US baseline: S&P500, NASDAQ, VIX. Shadow additions: Russell2000, SOX, DXY, US10Y.
- Directional indexes and volatility indexes are separate axes. `VIX` and `VKOSPI` are risk/sizing context, not simple bullish/bearish direction.
- New market index data must run read-only first, then logs/dashboard, then shadow metrics, then policy review.
- Do not change market regime logic from a single day or a small concentrated sample.

## Data Truth

- V2 lifecycle/canonical performance is the preferred live fill/performance truth.
- Legacy `data/ml/decisions.db` can remain a signal/evaluation ledger, but it under-represents PathB fills unless linked or repaired.
- Candidate audit should expose `execution_decision_id`, `execution_event_id`, link source, actual prompt ticker/count, and source quality flags when filled/PnL data exists.
- Backfill commands that can reset sessions require backup and explicit reset intent. Prefer no-reset operational runs.
- Every policy analysis must declare raw versus deduped rows, market, period, source DB, and bucket definitions.
- Counterfactual outcome updates must preserve status/source/metadata quality; do not treat `PRICE_PENDING`, `DATA_MISSING`, and `PRICE_UNAVAILABLE` as equal evidence.

## Claude And Prompt

- Final prompt evidence alignment target is code-adjusted; if warnings remain, separate provider/cache/coverage failure from structural target shortage.
- KR late/close evidence alignment can increase KIS calls. Watch timeout/coverage before adding phase-specific caps.
- Prompt overlay stays shadow until the data gate passes: 10 trading days, 4 trigger days, 30m/60m PF > 1.0, top-day contribution < 40%, and added candidates outperform excluded candidates.
- PLAN_A is a prompt visibility signal, not automatic `trade_ready`, order, or hard override authority.
- `input_to_claude` in old reports is not enough; prefer actual prompt ticker/count fields from candidate audit.
- Use `selection_trace_id`, `actual_prompt_*`, bucket/source tags, and score components before judging whether good candidates are lost before Claude.
- Claude false positives and false negatives must be reviewed by market, route, action, and risk justification bucket.
- AI can propose watchlist/trade-ready candidates and HOLD/SELL opinions, but not final order amount, hard-stop overrides, or broker truth overrides.

## Candidate And Execution

- For KR, always split `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT`, and `ADD_READY` before policy changes.
- For PathB, distinguish wait-only plans, zone hits, expired plans, live orders, partial fills, sell remainders, and broker-truth entry blocks.
- For PathB pending buys, do not infer TTL from plan creation time when actual order sent/ACK time is available; exact `order_no` should win over same-ticker fallback when an execution id exists.
- For US, KIS ranking and projected dollar volume stay shadow until fallback safety and forward outcomes are sufficient for primary review.
- `DEGRADED_PRESERVED` and FMP fallback candidates can reach later audit stages; require source/data-quality tags before judging selection quality.
- KR `minute_complete` is a completed intraday evidence quality fix. Treating it as confirmed is a KR bug fix, not a general `data_quality` relaxation.
- `fade_recovered` must stay KR-only shadow until explicit live approval; do not relax US fade handling or PathB `PULLBACK_WAIT` negative-context routing by implication.
- Momentum, hybrid-lite, watch trigger, CandidateTierBook, and theme injection remain shadow until sample size and labels are sufficient.
- KR first-entry filters, raw-score shadow lanes, exit overlays, and WATCH_TRIGGER/confirmation changes remain shadow/replay until sample-size and top-day concentration gates pass.

## Runtime Safety

- `/setorder` must be atomic from the operator view: persist first, mutate runtime second.
- Preflight should warn when PathB KR/US live gates do not match the approved KR-on/US-on policy.
- Existing PathB operating parameters are operator-controlled. Do not silently modify `.env.live` or `config/v2_start_config.json`.
- Dashboard PnL and ML digest must label whether values come from broker, local, legacy ML DB, or V2 canonical truth.
- Profit-review timeout fallback is `advisor_unavailable` and `learning_excluded`; do not count it as a strategy HOLD judgment.
- Destructive reconcile after sell `insufficient_holding` requires fresh broker zero-holding truth and zero open remaining quantity.
- PathB sizing diagnostics may split `qty=0` reasons, but must not change fixed sizing, one-share-over-budget, early soft gate, or live submit policy without explicit operator review.
