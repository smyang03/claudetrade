# Shadow Audit Infrastructure Plan - 2026-04-30

## Goal

Add an isolated shadow audit layer that records why the bot bought, skipped, blocked, held, or sold without changing live trading behavior.

## Non-Negotiable Safety Rules

- No change to trading decisions, order submission, fills, exits, or sizing.
- No schema changes to operating databases.
- No additional broker, Claude, or market data API calls.
- `SHADOW_AUDIT_ENABLED=false` must bypass the audit path.
- Every runtime hook must be non-blocking.
- Audit failures must not propagate to trading code.
- Audit data must be stored in separate DBs:
  - `data/audit/live_shadow_audit.db`
  - `data/audit/paper_shadow_audit.db`

## Existing Data Gap Audit

Create `tools/shadow_audit_gap.py` and produce `docs/reports/shadow_audit_gap_20260430.md`.

Check:

- `data/ml/decisions.db`
- `data/v2_event_store.db`
- `data/ticker_selection_log.db`
- `data/intraday_strategy_log.db`
- `logs/entry_timing/*.jsonl`
- `state/candidate_health_*.json`

Questions:

- Is `decision_id` available?
- Is `path_run_id` available?
- Are signal time, price, strategy, score, and block reason available?
- Are order/fill/exit events connectable?
- Is intraday price sample density enough for +5m/+15m/+30m/+60m outcomes?

## IDs

- `decision_id`: existing V2 ticker/session key.
- `path_run_id`: existing PathB run key.
- `signal_id`: deterministic key for one signal.
- `episode_id`: deterministic key for one blocking/error episode.

Rules:

- `signal_at_bucket`: 1 minute, `YYYYMMDDTHHMM`.
- `signal_id = sha1(runtime|market|session_date|ticker|strategy|signal_at_bucket|normalized_price|source)`.
- `episode_id = sha1(runtime|market|session_date|episode_type|scope|started_at_bucket|reason)`.
- KR price normalization: integer.
- US price normalization: 4 decimal places.

## Schema

Tables:

- `audit_signals`
- `audit_signal_events`
- `audit_episodes`
- `audit_signal_episode_links`
- `audit_price_samples`
- `audit_signal_outcomes`
- `audit_trade_links`
- `audit_writer_health`

## Writer

Use the existing runtime style:

- `threading.Thread`
- `queue.Queue(maxsize=SHADOW_AUDIT_QUEUE_MAX)`
- batch SQLite writes
- `INSERT OR IGNORE` / UPSERT
- hook sites call only a wrapper like `_audit_try_emit(...)`

Queue rules:

- `put(..., block=False)` only.
- `queue.Full` is swallowed.
- Price samples may be dropped first.
- Signal/block/trade/episode events should be preserved as much as possible.
- Health counters record drops/errors.

## Passive Price Samples

Store only prices already observed by the bot. Do not request new data.

Allowed when at least one context exists:

- `signal_id`
- `decision_id`
- `path_run_id`
- active position
- pending order
- active ORDER_UNKNOWN episode

Sources:

- `run_cycle()` after successful `get_price`
- `_entry_timing_signal_fired()`
- `_entry_timing_order_sent()`
- `_entry_timing_filled()`
- PathB current price reads
- buy/sell/order/fill prices

## Path A Hooks

Candidate hook sites:

- `_pending_signals` append
- `_entry_timing_signal_fired()`
- V2 `ORDER_UNKNOWN_BLOCKED` branch
- Path Arbiter blocked branch
- Reentry Cooldown blocked branch
- V2 Safety blocked branch
- `BUY_SIGNAL` before/after order
- `_entry_timing_order_sent()`
- `_entry_timing_filled()`

Events:

- `signal_fired`
- `signal_ranked`
- `safety_blocked`
- `order_sent`
- `filled`

## PathB Hooks

Candidate hook sites:

- `scan_waiting_entries()`
- entry-gate blocked return
- waiting run current price observation
- `_record_blocked()`
- `_submit_buy()`
- `on_buy_fill()`
- `scan_exits()`
- `_submit_sell()`
- `reconcile_order_unknowns_at_open()`
- `finalize_order_unknowns_at_session_close()`

Events:

- `pathb_waiting_seen`
- `pathb_entry_scan_blocked`
- `pathb_price_seen`
- `pathb_zone_hit`
- `pathb_order_sent`
- `pathb_filled`
- `pathb_exit_signal`
- `pathb_closed`

## ORDER_UNKNOWN Episodes

Events:

- `ORDER_UNKNOWN_PAUSE_STARTED`
- `ORDER_UNKNOWN_SIGNAL_BLOCKED`
- `ORDER_UNKNOWN_PATHB_BLOCKED`
- `ORDER_UNKNOWN_PAUSE_CLEARED`
- `ORDER_UNKNOWN_AUTO_CLEARED`
- `ORDER_UNKNOWN_STILL_UNRESOLVED`
- `ORDER_UNKNOWN_SESSION_END_UNRESOLVED`

Fields:

- `scope`
- `reason`
- `broker_trust`
- `checked_count`
- `auto_cleared_count`
- `blocked_signal_count`
- `affected_tickers`
- `clear_source`
- `blocked_minutes`

## Outcome Updater

Phase 1 outcomes:

- `return_5m`
- `return_15m`
- `return_30m`
- `return_60m`
- `return_to_close`
- `max_runup_until_close`
- `max_drawdown_until_close`

Rules:

- Use stored price samples only.
- No interpolation.
- Missing prices are recorded as `missing_price`.
- Session close flushes pending outcomes.
- Restart may recompute pending outcomes from existing samples.
- `avoided_loss_pnl` is Phase 2.

## Reports

Create:

- `tools/shadow_audit_report.py`

Report sections:

- ORDER_UNKNOWN episode summary
- blocked signal list
- block reason outcome buckets
- score buckets
- time buckets
- Path A vs PathB
- missing price ratio
- writer health

## QA

Required checks:

- `SHADOW_AUDIT_ENABLED=false` leaves existing behavior unchanged.
- Audit DB missing/deleted does not break trading.
- Audit DB locked does not break trading.
- Queue full does not block trading.
- Writer exceptions do not propagate.
- Existing operating DB schema is unchanged.
- No extra broker/Claude/API calls are introduced.

## Implementation Checklist

- [x] Gap audit tool and report.
- [x] ID helpers.
- [x] DB schema and store.
- [x] Non-blocking writer.
- [x] Passive price sample support.
- [x] Path A hooks.
- [x] ORDER_UNKNOWN episode hooks.
- [x] PathB hooks.
- [x] Outcome updater.
- [x] Report CLI.
- [x] Unit/integration tests.
- [x] Final QA.
- [x] MD comparison and omission review.
