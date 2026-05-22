# Always Analyze

Updated: 2026-05-22

Use this checklist before changing live behavior, market policy, prompts, learning data, or dashboard truth.

## Core

- Broker truth first: holdings, open orders, fills, quarantine, and ORDER_UNKNOWN beat local cache.
- Separate selection quality from execution/risk failures. Do not fix a selection issue by changing risk, and do not fix an execution issue by changing Claude selection.
- Keep Path A and Path B traceable through route, origin action, decision id, execution id, and fill status.
- Do not treat `state/brain.json` as runtime truth or an auto-learning target. New lessons go through candidate scoring first.
- Do not change order sizing, hard stop behavior, or PathB live gates without explicit operator review.

## Market Judgment

- KR baseline: KOSPI, KOSDAQ. Shadow additions: KOSPI200, KOSDAQ150, VKOSPI, KRX300.
- US baseline: S&P500, NASDAQ, VIX. Shadow additions: Russell2000, SOX, DXY, US10Y.
- Directional indexes and volatility indexes are separate axes. `VIX` and `VKOSPI` are risk/sizing context, not simple bullish/bearish direction.
- New market index data must run read-only first, then logs/dashboard, then shadow metrics, then policy review.
- Do not change market regime logic from a single day or a small concentrated sample.

## Data Truth

- V2 lifecycle/canonical performance is the preferred live fill/performance truth.
- Legacy `data/ml/decisions.db` can remain a signal/evaluation ledger, but it under-represents PathB fills unless linked or repaired.
- Candidate audit should expose `execution_decision_id`, `execution_event_id`, and link source when filled/PnL data exists.
- Backfill commands that can reset sessions require backup and explicit reset intent. Prefer no-reset operational runs.
- Every policy analysis must declare raw versus deduped rows, market, period, source DB, and bucket definitions.

## Claude And Prompt

- Final prompt evidence must match the execution candidate pool closely enough to make selection judgments meaningful.
- Prompt overlay stays shadow until the data gate passes: 10 trading days, 4 trigger days, PF > 1.0, top-day contribution < 40%.
- Claude false positives and false negatives must be reviewed by market, route, action, and risk justification bucket.
- AI can propose watchlist/trade-ready candidates and HOLD/SELL opinions, but not final order amount, hard-stop overrides, or broker truth overrides.

## Candidate And Execution

- For KR, always split `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT`, and `ADD_READY` before policy changes.
- For PathB, distinguish wait-only plans, zone hits, expired plans, live orders, partial fills, and sell remainders.
- For US, KIS ranking can become the first screener source only if token failure, empty response, and quality fallback preserve Yahoo/FMP behavior.
- KR `minute_complete` is a completed intraday evidence quality. Treating it as confirmed is a KR bug fix, not a general `data_quality` relaxation.
- `fade_recovered` must stay KR-only shadow until explicit live approval; do not relax US fade handling or PathB `PULLBACK_WAIT` negative-context routing by implication.
- Momentum, hybrid-lite, watch trigger, CandidateTierBook, and theme injection remain shadow until sample size and labels are sufficient.

## Runtime Safety

- `/setorder` must be atomic from the operator view: persist first, mutate runtime second.
- Preflight should warn when PathB KR/US live gates do not match the approved KR-on/US-on policy.
- Existing PathB operating parameters are operator-controlled. Do not silently modify `.env.live` or `config/v2_start_config.json`.
- Dashboard PnL and ML digest must label whether values come from broker, local, legacy ML DB, or V2 canonical truth.
