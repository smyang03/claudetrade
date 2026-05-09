# Hybrid-Lite Deferred Plan - 2026-05-09

This document keeps the deferred A/B hybrid-lite roadmap.

Immediate implementation work has been completed and QA'd in:

- `docs/reports/hybrid_lite_immediate_qa_20260509.md`

The temporary immediate checklist was deleted after implementation, validation, QA, and checklist comparison. Deferred live behavior changes remain here.

## Current Decision

Do not merge Plan A and Plan B yet.

Near-term work should first improve:

- A/B route attribution.
- PathB miss-quality measurement.
- Plan A gap_pullback observe-only gate structure.
- KR KOSDAQ ratio config visibility, without changing the default.

Only after these foundations produce clean data should live behavior changes be enabled.

## Evidence Summary

Primary live evidence:

- `data/v2_event_store.db`
  - Period: 2026-04-27 to 2026-05-08
  - US: 10 sessions
  - KR: 8 sessions

Longer candidate evidence:

- `data/ticker_selection_log.db`
  - Period: 2026-04-07 to 2026-05-08

Main conclusions:

- US Plan B is the stronger live edge and should be preserved.
- KR Plan A and Plan B are both weak in recent data, so KR needs market/strategy diagnosis rather than a broad hybrid rewrite.
- Plan A gap_pullback overextension is the first hybrid-lite target.
- PathB zone-hit misses need quality labeling before any re-activation or zone adjustment.
- Current A/B attribution is not clean enough for aggressive tuning.

## Deferred Live Changes

These items should not be enabled until the immediate observability work has produced enough clean live data.

### 1. US Plan A Gap-Pullback Hard Skip

Candidate rule:

```text
If market == US
and entry_route == plan_a
and strategy == gap_pullback
and current_price > buy_zone_high * 1.05
then skip entry.
```

Why deferred:

- Current simulated improvement is mostly one QCOM case.
- The direction is sensible, but repeated evidence is not yet sufficient.

Enable conditions:

- At least 3 to 5 live sessions of observe-only data.
- Extended `> +5%` Plan A gap_pullback cases show repeat negative expectancy or materially worse MAE.
- No evidence that the rule blocks a major positive outlier pattern.

Pros:

- Directly targets the strongest observed Plan A failure pattern.
- Does not alter Plan B.

Cons:

- Can overfit QCOM if enabled too early.
- May miss rare strong continuation winners.

### 2. Cancel-If-Open-Above Re-Activation

Candidate idea:

- Do not widen the initial buy zone.
- If a PathB plan is cancelled because open/current price is above the cancel threshold, keep a watch record.
- If price later re-enters the original buy zone during the same valid window, allow controlled re-activation.

Why deferred:

- Existing DB cannot measure how often cancelled plans re-entered the zone.
- Implementing re-activation without miss-quality evidence may increase bad entries.

Enable conditions:

- Miss-quality data shows a material number of `cancel_if_open_above` cases re-enter the original zone.
- Re-entered cases have acceptable MFE/MAE and are not just falling-knife behavior.

Pros:

- Can improve zone-hit rate without chasing above-zone prices.
- Preserves B's price discipline better than simple zone widening.

Cons:

- More state management.
- Re-entry can signal weakness rather than opportunity.
- Needs strict duplicate-order and same-day re-entry protection.

### 3. KR Momentum Size Cap

Candidate rule:

```text
If market == KR
and strategy family == momentum
then reduce size or require stronger confirmation.
```

Why deferred:

- KR momentum recent losses are severe, but the sample is small.
- KR behavior may be regime-dependent.

Enable conditions:

- More KR momentum samples confirm negative expectancy.
- Losses remain concentrated after attribution cleanup.
- Split by KOSPI/KOSDAQ and liquidity does not explain the loss cluster.

Pros:

- Targets a loss-heavy KR bucket.
- Safer than broad KR shutdown.

Cons:

- May cut the few KR large winners.
- Needs split by market type and liquidity.

### 4. KR KOSDAQ Ratio Live Increase

Candidate change:

```text
KR_SCREEN_KOSDAQ_MIN_RATIO=45
```

Possible later aggressive setting:

```text
KR_SCREEN_KOSDAQ_MIN_RATIO=50
```

Why deferred:

- KOSPI looked weak in the current window, but this may be regime-specific.
- Raising the ratio can introduce lower-quality KOSDAQ candidates if the KOSDAQ pool is thin.

Enable conditions:

- KR KOSPI continues to underperform in candidate forward metrics and live trades.
- KOSDAQ candidate quality remains acceptable after the ratio switch is simulated or observed.
- Screener logs show the ratio is not forcing weak KOSDAQ fillers.

Pros:

- Addresses candidate-pool composition at the screener level.
- More direct than trying to fix KOSPI entries with execution gates.

Cons:

- Can reduce diversification.
- Can overfit a short KR regime.

### 5. Full A/B Candidate State Unification

Why deferred:

- Current urgent problem is attribution and observability, not a full router rewrite.
- PathB has a state machine with WAITING/FILLED/CLOSED semantics that should not be merged into Plan A without more evidence.

Potential future target:

- Unified candidate state manager with candidate tiers:
  - `CORE_READY`
  - `SIGNAL_READY`
  - `PRICE_WAIT`
  - `WATCH_ONLY`
  - `BLOCKED`

Pros:

- Cleaner long-term architecture.
- Reduces stale `today_tickers` vs `selection_meta` drift.

Cons:

- Larger blast radius.
- Requires dashboard, router, PathB runtime, and tests to move together.
- Risky while live data quality is still being fixed.

## Not Recommended Now

### Broad Plan B Zone Widening

Reason:

- Zone-hit rate would increase, but B's price discipline may be diluted.
- Added trades are marginal trades and should not be assumed to earn the same average as existing filled B trades.

### RR Hard Gate

Reason:

- Existing data does not show strong enough predictive power.
- Fat-tail winners can look unattractive by simple entry-time RR.

### Broad Plan A Hybrid Gate

Reason:

- The evidence points first to gap_pullback, not all Plan A strategies.
- Applying the same zone logic to momentum or opening-range entries may block valid setups.

## Revisit Criteria

Revisit this deferred plan after:

- Route attribution is present on new live orders.
- PathB miss-quality rows have at least 3 to 5 sessions of follow-up data.
- Hybrid gap_pullback observe-only logs have enough US samples to check extended-entry expectancy.
- KR screener summaries clearly show KOSPI/KOSDAQ composition and outcomes.
