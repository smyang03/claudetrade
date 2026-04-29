# Bucket Shadow Quality Plan - 2026-04-28

## Purpose

Candidate quality must be measured before changing live execution behavior.

This plan adds shadow bucket metadata, first-detection tracking, reporting, and dashboard visibility for KR/US screeners. It does not change buy/sell rules, sizing, Safety Gate, Path A timing, Path B price plans, or Claude prompts.

## Non-Goals

- No bucket-based auto buy.
- No bucket-based sizing, stop, target, or forced sell.
- No immediate replacement of current `screen_score`.
- No immediate Claude prompt routing by bucket.
- No parameter tuning from one or two trading days.
- No news, ML model, option flow, or new external data source.

## Core Decisions

- Bucket data is shadow-only.
- `first_bucket_detected_at` is the forward-measurement anchor.
- Repeated detection of the same `(session_date, market, ticker, primary_bucket)` keeps the first timestamp, updates `last_bucket_detected_at`, and increments `bucket_seen_count`.
- If the same ticker changes primary bucket during the session, each `(ticker, primary_bucket)` record is tracked separately.
- `primary_bucket` is a single value. `secondary_buckets` is a list.
- Dashboard and Telegram can display bucket information, but live order decisions remain unchanged.

## Bucket Types

| Bucket | Meaning |
|---|---|
| `momentum_now` | Already moving strongly now |
| `volume_surge` | Volume acceleration before or during move |
| `liquidity_leader` | High turnover and tradable liquidity |
| `prev_strength` | Previous-session strength continuing into today |
| `near_breakout` | Near prior high/resistance breakout area |
| `pullback_watch` | Pullback after strength, possible re-entry area |
| `pre_move_setup` | Not yet moved much, but setup is forming |
| `sector_lagging_leader` | Sector leader/laggard relationship, data-dependent |

## Primary Bucket Priority

When multiple buckets match, assign primary bucket by this priority:

1. `pre_move_setup`
2. `pullback_watch`
3. `volume_surge`
4. `near_breakout`
5. `prev_strength`
6. `momentum_now`
7. `liquidity_leader`
8. `sector_lagging_leader`

All other matched buckets go into `secondary_buckets`.

## Measurement Fields

Each candidate-quality row should include:

- `primary_bucket`
- `secondary_buckets`
- `bucket_reasons`
- `bucket_data_gaps`
- `first_bucket_detected_at`
- `last_bucket_detected_at`
- `bucket_seen_count`
- `earliest_bucket_detected_at`
- `score_current`
- `score_vol_ratio_capped`
- `score_vol_ratio_log`
- `score_turnover_weighted`

Forward fields can be null until a later measurement job fills them:

- `forward_30m_from_bucket`
- `forward_60m_from_bucket`
- `forward_close_from_bucket`
- `max_runup_30m_from_bucket`
- `max_runup_60m_from_bucket`
- `max_runup_close_from_bucket`
- `max_drawdown_60m_from_bucket`

## Winner Thresholds

KR:

- `winner_30m`: forward 30m >= +2.0%
- `winner_60m`: forward 60m >= +3.0%
- `winner_close`: close max runup >= +5.0% or close return >= +3.0%

US:

- `winner_30m`: forward 30m >= +1.0%
- `winner_60m`: forward 60m >= +1.5%
- `winner_close`: close max runup >= +2.5% or close return >= +1.5%

## Dashboard Requirements

Add bucket visibility without changing execution:

- Bucket summary by market/session.
- Counts by `primary_bucket`.
- Claude-input count, watch count, trade-ready count.
- Actual entries count, Path A count, Path B count.
- Winner/missed/bad-signal placeholders when forward data is not yet available.
- Candidate card fields: name/code, price, change rate, turnover, volume ratio, primary/secondary buckets, bucket reasons, Claude status, path status.
- Clear empty-state text when no bucket data exists.

## Phase Checklist

### Phase 0 - Plan Lock

- [x] Create this MD.
- [x] Confirm scope is shadow-only.
- [x] Confirm first-detection policy.

Verification:

- [x] MD exists under `docs/plans`.

### Phase 1 - Current Structure Inspection

- [x] Inspect `bot/screener_quality.py`.
- [x] Inspect `tests/test_screener_quality.py`.
- [x] Inspect `interface/v2_ops_summary.py`.
- [x] Inspect dashboard route/API shape.

Verification:

- [x] No code changes before inspection is complete.

### Phase 2 - Bucket Classifier

- [x] Add bucket classification module.
- [x] Add primary/secondary bucket logic.
- [x] Add data-gap reporting for unavailable inputs.
- [x] Add shadow-score calculation.
- [x] Add first/last detection state with atomic write.

Verification:

- [x] Unit tests cover primary priority.
- [x] Unit tests cover repeated detection preserving first timestamp.
- [x] Unit tests cover primary bucket change producing separate tracking.
- [x] Previous screener-quality tests still pass.

### Phase 3 - Candidate Quality Log Integration

- [x] Add bucket metadata to candidate-quality JSONL rows.
- [x] Keep legacy `bucket` field for compatibility.
- [x] Do not change candidate filtering, candidate ranking, or Claude input selection.
- [x] Ensure missing data does not crash logging.

Verification:

- [x] Candidate-quality write test includes new fields.
- [x] Existing tests still pass.

### Phase 4 - Bucket Summary API/Data

- [x] Add bucket summary reader.
- [x] Aggregate by market/session/primary bucket.
- [x] Include empty-state behavior.
- [x] Include placeholders for forward metrics.

Verification:

- [x] Summary test reads sample JSONL and returns expected counts.
- [x] Prior phase tests still pass.

### Phase 5 - Dashboard Visibility

- [x] Expose bucket summary to dashboard API or existing ops summary.
- [x] Add a visible bucket monitor section if current dashboard structure allows safe scoped edits.
- [x] Use Korean labels for operational readability.
- [x] Avoid blocking dashboard if bucket data is missing.

Verification:

- [x] API/summary function works with no file.
- [x] API/summary function works with sample rows.
- [x] Prior phase tests still pass.

### Phase 6 - Final QA

- [x] Run focused tests for bucket classifier, screener quality, and summary.
- [x] Run broader relevant tests if runtime allows.
- [x] Run syntax/compile check for touched Python files.
- [x] Compare implementation against this MD.
- [x] Update this MD with completion notes and omissions if any.

Go Conditions:

- [x] No execution-path behavior changed.
- [x] No Safety Gate behavior changed.
- [x] No Path A/B order behavior changed.
- [x] Candidate-quality logging does not crash on partial candidate fields.
- [x] Dashboard/summary handles empty data.

No-Go Conditions:

- [x] No order path changes were introduced.
- [x] No bucket value changes live buy/sell behavior.
- [x] Candidate logging is guarded and covered by tests.
- [x] First-detection state uses temp-file replace for atomic writes.

## Completion Notes

Implemented:

- Added `bot/bucket_classifier.py`.
- Added `interface/bucket_summary.py`.
- Extended `bot/screener_quality.py` JSONL rows with bucket metadata and shadow scores.
- Added `bucket_monitor` to `build_v2_ops_summary()`.
- Added a visible "후보 바구니 모니터" section on `/pathb`.
- Added tests:
  - `tests/test_bucket_classifier.py`
  - `tests/test_bucket_summary.py`
  - updated `tests/test_screener_quality.py`

Verification:

- `pytest tests/test_bucket_classifier.py tests/test_screener_quality.py -q` -> 6 passed.
- `pytest tests/test_bucket_classifier.py tests/test_screener_quality.py tests/test_bucket_summary.py -q` -> 8 passed.
- `python -m py_compile bot/bucket_classifier.py bot/screener_quality.py interface/bucket_summary.py interface/v2_ops_summary.py dashboard/dashboard_server.py` -> passed.
- `pytest tests/test_bucket_classifier.py tests/test_screener_quality.py tests/test_bucket_summary.py tests/test_v2_phase6.py tests/test_dashboard_pathb.py -q` -> 14 passed.
- `pytest tests -q` -> 135 passed, 2 existing eventlet deprecation warnings.

Post-review fixes:

- Clarified `shadow_scores()` uses absolute change as movement-intensity, not bullish-direction score.
- Changed opening-fresh top-N coverage ranking to use `screen_score` first, `change_rate` fallback second.
- Changed KR KOSDAQ warning to distinguish `MARKET_TYPE_MISSING` from true `KOSDAQ_BUCKET_ZERO`.
- Aligned bucket candidate display to 30 rows in the API and dashboard.
- Added `daily_judgment_fallback` so `/pathb` can show bucket data even before new `screener_quality` JSONL rows exist.

Post-review verification:

- `pytest tests/test_bucket_classifier.py tests/test_screener_quality.py tests/test_bucket_summary.py tests/test_v2_phase6.py tests/test_dashboard_pathb.py -q` -> 14 passed, 2 existing eventlet deprecation warnings.
- `python -m py_compile bot/bucket_classifier.py bot/screener_quality.py interface/bucket_summary.py dashboard/dashboard_server.py` -> passed.
- `pytest tests/test_bucket_summary.py tests/test_bucket_classifier.py tests/test_screener_quality.py tests/test_v2_phase6.py tests/test_dashboard_pathb.py -q` -> 15 passed, 2 existing eventlet deprecation warnings after fallback.
- Direct `build_bucket_summary(market="KR", session_date="2026-04-28", runtime_mode="live")` check -> `daily_judgment_fallback`, 20 candidates, 3 buckets.

Known intentional limitations:

- Forward return fields are present but remain `null` until a later measurement job fills them.
- `path_a_entries` and `path_b_entries` in bucket summary are placeholders because candidate-quality rows do not yet carry actual entry linkage.
- `sector_lagging_leader` and strict `pre_move_setup` depend on upstream sector/recent-strength fields. When data is unavailable, `bucket_data_gaps` records that instead of guessing.
