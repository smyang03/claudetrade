# Broker Truth Live Plan - 2026-04-27

Purpose: make live dashboard, Telegram, and ORDER_UNKNOWN reconciliation use broker truth first. Local lifecycle/event-store data remains the explanation layer, not the source of current account state.

## Operating Rules

- Live current state is broker truth first.
- Local DB is used for reasoning, Claude rationale, path identity, and history.
- Path B price waiting is local truth until an order is sent.
- Ambiguous broker evidence must not auto-recover local state.
- KIS lookup failures must not mutate existing local state.
- Dashboard and Telegram must not call KIS directly; they read the broker truth snapshot.
- Snapshot is for display/reconciliation. Order safety checks keep their direct KIS/broker verification path.
- Do not add new `v2_path_runs.status` enum values for reconciliation labels.
- ORDER_UNKNOWN reconciliation results are stored in `plan_json.order_unknown_resolution`.

## Phase 1. BrokerTruthSnapshot Foundation

- Add `runtime/broker_truth_snapshot.py`.
- Snapshot file: `state/live_broker_truth_snapshot.json`.
- Snapshot root fields:
  - `generated_at`
  - `runtime_mode`
  - `markets.KR`
  - `markets.US`
- Per-market fields:
  - `missing`
  - `stale`
  - `last_success_at`
  - `last_attempt_at`
  - `ttl_sec`
  - `error`
  - `account_summary`
  - `positions`
  - `open_orders`
  - `today_fills`
- KR/US stale states are independent.
- Missing snapshot and stale snapshot are distinct:
  - missing file: account lookup pending
  - file exists but TTL expired: account lookup stale
- Use atomic file writes: write temp file then replace target on the same filesystem.
- Mask account numbers, tokens, app keys, and secrets before writing.
- TTL is passed by caller:
  - active session: 30 seconds
  - inactive session: 60 seconds
  - startup/session_open/ORDER_UNKNOWN reconciliation: `force=True`

## Phase 2. KIS Truth Collection

- Connect KR positions.
- Connect US positions.
- Connect KR open orders.
- Connect US open orders.
- Connect KR same-day fills.
- Connect US same-day fills.
- Normalize common fields:
  - `ticker`
  - `name`
  - `qty`
  - `avg_price`
  - `current_price`
  - `eval_amount`
  - `pnl`
  - `pnl_pct`
  - `order_no`
  - `side`
  - `order_status`
  - `filled_qty`
  - `remaining_qty`
  - `order_time`
  - `fill_time`
- If KIS lookup fails:
  - do not stop order runtime
  - mark only that market stale/error
  - preserve previous successful market data
- Public API:
  - `refresh_market(market, force=False, ttl_sec=30)`
  - `refresh_all(force=False, ttl_by_market=None)`
  - `load_snapshot()`
  - `market_snapshot(market)`
  - `is_market_stale(market)`

## Phase 3. ORDER_UNKNOWN Reconciliation

- Triggers:
  - startup
  - session_open
  - immediately after ORDER_UNKNOWN is recorded
  - 5-minute retry through `next_broker_truth_recheck_at`
  - 10-minute periodic check while unresolved ORDER_UNKNOWN exists
  - final check before session_close
- No `threading.Timer`.
- Store retry time in `plan_json.next_broker_truth_recheck_at`.
- Main loop/scheduler processes due reconciliation work.
- Inputs:
  - Path B `ORDER_UNKNOWN` run
  - broker snapshot positions
  - broker snapshot open_orders
  - broker snapshot today_fills
  - lifecycle_events
  - bot in-memory `pending_orders`
- Path A/B disambiguation order:
  1. Check same ticker/session Path A `ORDER_SENT/FILLED/CLOSED`.
  2. Check bot in-memory `pending_orders` for same ticker Path A order.
  3. If Path A evidence exists, do not recover Path B. Set `path_a_origin_possible`.
  4. If no Path A evidence and existing `_find_recent_order_truth_kr/us` criteria match a broker fill, recover candidate:
     - full fill -> existing status `FILLED`
     - partial fill -> existing status `PARTIAL_FILLED`
  5. If no Path A evidence and broker open order matches, recover candidate:
     - existing status `ORDER_ACKED` or `ORDER_SENT`
  6. If no broker positions/fills/open_orders evidence, keep `ORDER_UNKNOWN` and set `broker_no_evidence`.
  7. If broker lookup failed, keep `ORDER_UNKNOWN` and set `broker_truth_unavailable`.
  8. If evidence is ambiguous, keep `ORDER_UNKNOWN` and set `ambiguous_broker_truth`.
- Do not add new `v2_path_runs.status` values.
- Reconciliation labels only in `plan_json.order_unknown_resolution`:
  - `path_a_origin_possible`
  - `broker_no_evidence`
  - `broker_truth_unavailable`
  - `ambiguous_broker_truth`
  - `pathb_fill_recovered`
  - `pathb_open_order_recovered`
  - `session_end_unresolved`
- Session close:
  - keep status `ORDER_UNKNOWN`
  - set `plan_json.session_end_unresolved=true`
  - set `plan_json.order_unknown_resolution="session_end_unresolved"`

## Phase 4. Runtime Integration

- Startup:
  - force refresh KR/US broker snapshot
  - reconcile unresolved ORDER_UNKNOWN
- `session_open(market)`:
  - force refresh that market snapshot
  - reconcile that market ORDER_UNKNOWN
- After ORDER_UNKNOWN is recorded:
  - force refresh market snapshot
  - reconcile immediately
  - set 5-minute retry timestamp if unresolved
- Main loop/scheduler:
  - run `reconcile_order_unknowns(market)` every 10 minutes
  - handle due `next_broker_truth_recheck_at`
- Before `session_close(market)`:
  - force refresh market snapshot
  - final reconciliation
  - mark unresolved as `session_end_unresolved`
- Do not use snapshot as the only entry permission source.

## Phase 5. Dashboard

- Add `broker_truth` to `/api/v2/ops`.
- Include per-market snapshot status:
  - `missing`
  - `stale`
  - `last_success_at`
  - `last_attempt_at`
  - `ttl_sec`
  - `error`
  - `positions`
  - `open_orders`
  - `today_fills`
- `/pathb` separates:
  - broker account state
  - local Path A state
  - local Path B state
  - Claude plan/rationale
- Positions prefer broker snapshot.
- If local fallback is used, label it explicitly.
- ORDER_UNKNOWN card shows:
  - broker position evidence
  - broker open order evidence
  - broker today fill evidence
  - Path A origin possibility
  - reconciliation result
  - last broker truth time
- Snapshot display:
  - missing: account lookup pending
  - stale: account lookup stale
  - KR/US stale separated
- Dashboard API must not call KIS directly.

## Phase 6. Telegram

- `/positions` uses broker snapshot first.
- `/positions` labels missing/stale/fallback clearly.
- `/pathb_status`:
  - local plan for price waiting
  - broker truth first for order/fill/holding/closed state
  - Path A/B separated
- `/errors` shows ORDER_UNKNOWN reconciliation result.
- `/health` shows last broker snapshot refresh, KR/US stale state, and snapshot error.
- Telegram must not call KIS directly.

## Phase 7. Preflight / QA

- Add checks to `tools/live_preflight.py`:
  - `broker_truth.snapshot_file_valid`
  - `broker_truth.snapshot_missing_or_present`
  - `broker_truth.kr_stale_state`
  - `broker_truth.us_stale_state`
  - `broker_truth.atomic_write_marker`
  - `broker_truth.positions_from_broker`
  - `broker_truth.open_orders_from_broker`
  - `broker_truth.today_fills_from_broker`
  - `broker_truth.dashboard_uses_snapshot`
  - `broker_truth.telegram_uses_snapshot`
  - `order_unknown.path_a_b_disambiguation`
  - `order_unknown.in_memory_pending_checked`
  - `order_unknown.session_recheck_wired`
  - `order_unknown.session_end_unresolved_marking`
- Criteria:
  - missing snapshot file: WARN
  - broken snapshot JSON: FAIL
  - stale is per market
  - market stale over 180 seconds: WARN or FAIL
  - dashboard/telegram direct KIS calls: FAIL

## Phase 8. Tests

- BrokerTruthSnapshot create/load round-trip.
- Atomic write.
- Missing snapshot handling.
- Broken snapshot JSON handling.
- KR-only stale and US-only stale.
- KIS lookup failure preserves previous snapshot.
- Sensitive-data masking.
- `refresh_market()` TTL behavior.
- `refresh_market(force=True)` behavior.
- ORDER_UNKNOWN + Path A lifecycle -> no Path B recovery.
- ORDER_UNKNOWN + in-memory Path A pending -> no Path B recovery.
- ORDER_UNKNOWN + no Path A + broker fill match -> recovery candidate.
- ORDER_UNKNOWN + broker open order match -> pending-order recovery candidate.
- ORDER_UNKNOWN + no broker evidence -> `broker_no_evidence`.
- ORDER_UNKNOWN + broker lookup failure -> `broker_truth_unavailable`.
- ORDER_UNKNOWN + ambiguous evidence -> `ambiguous_broker_truth`.
- session_close unresolved marking.
- Dashboard API `broker_truth`.
- Dashboard Path B state separation.
- Telegram `/positions` snapshot display.
- Telegram `/pathb_status` Path A/B separation.
- Telegram `/errors` ORDER_UNKNOWN reconciliation result.
- Telegram `/health` snapshot stale display.
- Existing full test suite.

## Phase 9. Final Verification

- Re-read this plan.
- Compare each phase against implemented code.
- Fix missing items.
- Run `python tools/live_preflight.py --mode live`.
- Run targeted tests.
- Run `python -m unittest discover tests`.
- Write final QA report with:
  - implemented items
  - omitted items, if any
  - test result
  - preflight result
  - remaining operational warnings
