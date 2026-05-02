# Preopen Shadow Basket Execution Plan - 2026-05-03

## Decision

Run the existing live trading system unchanged, and put preopen signals into a shadow basket only.

This plan must not:

- Buy during premarket.
- Enable fast-lane automatic entries.
- Reorder Claude selection candidates.
- Change PathA, PathB, sizing, exits, or safety gates.

The purpose is to collect evidence before deciding whether preopen information improves regular-session execution.

## Operating Policy

| Area | Policy |
| --- | --- |
| Token, fill ledger, broker truth stability patches | Allowed in live |
| Existing selection / entry / exit / sizing | No behavior change |
| Preopen shadow basket | Record only |
| Preopen sort / fast lane / premarket buy | Disabled until review |

## Safety Flags

Default behavior:

- `PREOPEN_SHADOW_ENABLED=true`
- `PREOPEN_SORT_ENABLED=false`
- `FAST_LANE_ENABLED=false`
- `PREMARKET_BUY_ENABLED=false`

Phase 1 code should not route these flags into order placement. Tests must show that shadow collection does not call `place_order`.

## Development Order

| Step | Item | Reason |
| --- | --- | --- |
| 1 | `preopen/models.py`, `preopen/storage.py`, `preopen/scorer.py` | Shared shadow schema, storage, and score/rank calculation must exist before collector and rank diff logging. |
| 2 | `tools/preopen_collector.py` | Independent CLI writes shadow candidates using the scorer. |
| 3 | Rank diff logger | Records `shadow_preopen_rank` vs Claude actual rank. |
| 4 | `tools/preopen_outcome_updater.py` | Adds post-open outcome rows without bot coupling. |
| 5 | Dashboard shadow section | Exposes collector quality, rank diff, and outcome data. |
| 6 | Hard guard tests | Proves shadow mode cannot call order paths. |
| 7 | `bot/session_date.py` refactor | Keep as separate patch unless a date mismatch is found. |

### 1. `preopen/models.py`, `preopen/storage.py`, `preopen/scorer.py`

Reason: new research-only files with no direct trading side effects.

Required fields:

- `market`
- `session_date`
- `captured_at`
- `provider`
- `ticker`
- `shadow_preopen_rank`
- `preopen_score`
- `preopen_grade`
- `gap_pct`
- `volume_ratio`
- `price`
- `data_quality`
- `stale`
- `actual_selection_rank`
- `rank_delta`
- `actual_selected`
- `actual_trade_ready`
- `actual_rejection_reason`
- `post_open_5m_return_pct`
- `post_open_30m_return_pct`
- `post_open_60m_return_pct`
- `max_runup_pct`
- `max_drawdown_pct`
- `open_to_high_pct`
- `open_to_close_pct`

Storage:

- `state/preopen_{MARKET}_{YYYYMMDD}.json`
- `logs/preopen/{YYYYMMDD}_{MARKET}_candidates.jsonl`
- `logs/preopen/{YYYYMMDD}_{MARKET}_rank_diff.jsonl`
- `logs/preopen/{YYYYMMDD}_{MARKET}_outcome.jsonl`

### 2. `tools/preopen_collector.py`

Reason: independent CLI; bot does not import or schedule it.

Behavior:

- `--market US|KR`
- `--mode paper|live`
- `--once` for scheduled execution.
- `--loop` optional for manual shadow collection.
- Seed-only fallback is allowed.
- KIS token must be read-only.
- Collector must not refresh KIS token.
- Token missing or expired must write explicit `token_status`.
- If token is expired, KIS enrichment is skipped and the state must show stale/unavailable status instead of silently looking healthy.

KR timing split:

- `08:00-09:00 KST`: indicative/preopen data belongs to collector.
- `09:05+ KST`: actual traded-value/outcome belongs to outcome updater.

US timing split:

- Premarket data belongs to collector.
- Regular-session 5/30/60 minute behavior belongs to outcome updater.

### 3. Rank Diff Logger

Reason: this is the most important evidence for deciding whether preopen data is useful.

After Claude selection, record:

- `shadow_preopen_rank`
- `actual_selection_rank`
- `rank_delta`
- `actual_selected`
- `actual_trade_ready`
- `actual_ordered`
- `actual_rejection_reason`
- Claude reason text if available

This data answers:

- Did preopen rank disagree with Claude?
- Was the disagreement useful?
- Did preopen rank identify names Claude missed?
- Did preopen rank promote noisy gap names?

### 4. `tools/preopen_outcome_updater.py`

Reason: independent CLI; no bot coupling.

Suggested scheduler:

- KR: `09:05`, `09:30`, `10:00 KST`
- US: regular open + `5m`, `30m`, `60m`

Record:

- `post_open_5m_return_pct`
- `post_open_30m_return_pct`
- `post_open_60m_return_pct`
- `max_runup_pct`
- `max_drawdown_pct`
- `open_to_high_pct`
- `open_to_close_pct`

Phase 1 may write `pending_price_provider` until reliable price data is connected.

### 5. Dashboard Shadow Section

Reason: observe data quality and rank disagreements without reading raw logs.

Display:

- market/date/provider
- collector status
- token status
- data freshness
- preopen rank
- Claude actual rank
- rank delta
- selected / trade_ready flags
- rejection reason
- 5m/30m/60m outcomes

### 6. Hard Guard Tests

Tests must verify:

- Collector does not call `place_order`.
- Enabling shadow mode does not change candidate order.
- `PREOPEN_SORT_ENABLED=false`, `FAST_LANE_ENABLED=false`, and `PREMARKET_BUY_ENABLED=false` remain non-operational in Phase 1.
- Missing/corrupt/stale preopen state does not break bot selection.

### 7. Session Date Refactor

This already exists as `bot/session_date.py` in the current tree.

Do not expand this refactor as part of preopen work unless tests show a date mismatch. If further changes are needed, handle them as a separate patch because session-date bugs can affect production records.

## Performance Measurement Plan

Collect at least 5-10 sessions before enabling any behavior change.

Primary metrics:

- Preopen rank vs Claude rank correlation.
- Average 5m/30m/60m return by preopen rank bucket.
- Average 5m/30m/60m return by rank_delta bucket.
- Hit rate of top-3 preopen candidates becoming Claude-selected.
- Hit rate of top-3 preopen candidates becoming `trade_ready`.
- Missed opportunity count: preopen top candidate not selected, but strong post-open outcome.
- Noise count: preopen top candidate selected or promoted, but post-open fade.

Decision gates:

- Enable only dashboard and logging until enough sessions are collected.
- Consider `PREOPEN_SORT_ENABLED` only if preopen rank improves outcomes without increasing fade/noise.
- Consider fast lane only after sort/shadow evidence is positive.
- Premarket buying remains out of scope.

## QA Commands

- `python -m pytest tests/test_preopen_shadow.py -q`
- `python -m py_compile preopen/models.py preopen/storage.py preopen/scorer.py tools/preopen_collector.py tools/preopen_outcome_updater.py trading_bot.py dashboard/dashboard_server.py`
- `python tools/preopen_collector.py --market US --mode live --once --tickers AAPL,MSFT`
- `python tools/preopen_outcome_updater.py --market US --offset-min 5 --once`
- `python -m pytest tests/test_pathb_runtime.py tests/test_kis_ws_fill_notice.py -q`
- `git diff --check`

## Completion Checklist

- Compare implementation against this plan.
- Patch any missing safety or measurement item.
- Keep this plan as the reference for later 5-10 session performance review.
