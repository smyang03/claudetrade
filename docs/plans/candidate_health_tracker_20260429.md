# Candidate Health Tracker Improvement - 2026-04-29

## Goal

Track whether repeated watch/trade_ready candidates are strengthening or weakening inside the current market session.

This is a log-only first step. It must not block buys, alter `trade_ready`, change PathB plans, or change order sizing.

## Problem

Recent basket analysis showed that `trade_ready` has alpha, but repeated selection alone is not enough.

- Strong repeated ready examples: `006340`, `001440`, `006345`, `417200`
- Weak repeated ready examples: `058430`, `002780`, `209640`, `QCOM`

The useful distinction is:

- repeated ready + price strengthening = higher conviction
- repeated ready + price weakening + recovery failure = lower conviction

## Storage

Create one session-scoped state file per market:

- `state/candidate_health_KR_YYYYMMDD.json`
- `state/candidate_health_US_YYYYMMDD.json`

The session date must use the bot's active market session date. Do not use raw calendar `today()` for US sessions because KST 00:00-05:00 still belongs to the prior US trading session.

## Stored Raw Fields

Only raw state is persisted:

- `first_seen_at`
- `first_seen_price`
- `first_ready_at`
- `first_ready_price`
- `last_seen_at`
- `last_price`
- `seen_count`
- `ready_count`
- `mfe_pct`
- `mae_pct`
- `recovered_first_ready`
- `last_status`

## Derived Fields Not Stored

These are recalculated on load/use:

- `current_vs_first_ready_pct`
- `current_vs_first_seen_pct`
- `health_state`
- `weaken_flag`
- `score_penalty`
- `score_bonus`
- `zone_state`

Reason: storing derived decisions would make old files stale when thresholds change.

## Derived States

Initial v1 states:

- `STRONG_READY`
- `STABLE_READY`
- `WEAKENING_READY`
- `FAILED_READY`
- `WATCH_STRENGTHENING`
- `WATCH_WEAK`
- `OBSERVE`

Initial v1 thresholds:

- `WEAKENING_READY`
  - `ready_count >= 3`
  - `current_vs_first_ready_pct < 0`
  - `mfe_pct < +1.0`
  - `mae_pct <= -2.0` for KR, `<= -2.5` for US
- `FAILED_READY`
  - `ready_count >= 3`
  - `current_vs_first_ready_pct <= -3.0`
  - `recovered_first_ready == false`
- `STRONG_READY`
  - `ready_count >= 2`
  - `current_vs_first_ready_pct >= +2.0` for KR, `>= +1.5` for US
  - `mae_pct` is above the weakening threshold
- `WATCH_STRENGTHENING`
  - `ready_count == 0`
  - `seen_count >= 2`
  - `current_vs_first_seen_pct >= +2.0` for KR, `>= +1.5` for US
- `WATCH_WEAK`
  - `ready_count == 0`
  - `seen_count >= 2`
  - `current_vs_first_seen_pct <= -2.0` for KR, `<= -2.5` for US

KR/US thresholds must be declared separately even when values are similar.

## Update Point

Update after selection normalization, using the selected watchlist and applied `trade_ready`.

Update order:

1. Update all watchlist tickers as seen.
2. Update all trade_ready tickers as ready.
3. If a trade_ready ticker is missing from watchlist, count it as seen first.
4. Enforce `seen_count >= ready_count` before save.

## Price Source

Use native price consistently:

1. Candidate row price from the current screener/selection candidate list.
2. `price_cache_raw` fallback.
3. KR may use `price_cache` fallback because KR native and risk price are both KRW.
4. US must not use KRW-converted `price_cache` as fallback.

## PathB Zone

The tracker must not know PathB zones.

Tracker responsibility:

- price health only

Caller responsibility:

- combine candidate health with PathB zone later if needed

Example later integration:

```python
health = candidate_health.get_state(ticker)
plan = pathb_plan_for(ticker)
if health["health_state"] == "WEAKENING_READY" and price < plan.buy_zone_low:
    # future soft penalty or warning
```

This PR does not implement this trading effect.

## Rollout

Phase 1: log only

- Persist session candidate health JSON.
- Emit summary and interesting state logs.
- No changes to trade decisions.

Phase 2: soft penalty after observation

- `WEAKENING_READY`: reduce entry priority or PathB confidence.
- `STRONG_READY`: small priority bonus.

Phase 3: hard rules only after 3-5 sessions

- Only if historical/live evidence supports it.

## Implementation Checklist

- [x] Add `bot/candidate_health.py`
- [x] Add TradingBot tracker initialization/lazy session handling
- [x] Add price map builder with native price discipline
- [x] Update candidate health at session_open selection
- [x] Update candidate health at session_reuse_rescreen
- [x] Update candidate health at manual_rescreen
- [x] Add unit tests for raw persistence and derived states
- [x] Run py_compile
- [x] Run candidate health unit tests
- [x] Compare implementation against this document

## QA Result

- `python -m py_compile bot\candidate_health.py trading_bot.py tests\test_candidate_health_tracker.py` passed.
- `python -m unittest tests.test_candidate_health_tracker` passed, 7 tests.
- Additional existing contract smoke `python -m unittest tests.test_patha_contract tests.test_pathb_selection` was run for awareness. It failed in `tests.test_patha_contract` because current working tree returns `ORDER_UNKNOWN_UNRESOLVED` before `DAILY_LOSS_LIMIT`. This is outside candidate health and was not changed by this implementation.

## QA Notes

Expected live log shape:

```text
[candidate_health] KR session_open updated=13 states={...}
[candidate_health] KR 058430 WEAKENING_READY ready=6 current=-5.6 mae=-5.8 mfe=0.4 recovered=false
```

Expected JSON shape:

```json
{
  "schema_version": 1,
  "market": "KR",
  "session_date": "2026-04-29",
  "tickers": {
    "058430": {
      "first_seen_at": "...",
      "first_seen_price": 10010.0,
      "first_ready_at": "...",
      "first_ready_price": 10010.0,
      "last_seen_at": "...",
      "last_price": 9440.0,
      "seen_count": 6,
      "ready_count": 6,
      "mfe_pct": 0.4,
      "mae_pct": -5.8,
      "recovered_first_ready": false,
      "last_status": "TRADE_READY"
    }
  }
}
```
