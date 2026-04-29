# Path B Claude Price Live Plan

This is production live work, not a test harness. Starting from the next live run, `python trading_bot.py --live` must be able to run Path A and Path B together.

## Fixed Operating Policy

```text
Path A = existing timing_adapter live path
Path B = Claude price live min-size path
runtime_mode = live
Path B default = enabled
Path B fixed budget = 100000 KRW source size
Path B max open positions = 1
Path B max daily entries = 1
Same ticker Path A/B live overlap = forbidden
Path B emergency kill = required
```

Required env defaults:

```text
PATHB_MODE=min_size_live
PATHB_ENABLED=true
PATHB_TELEGRAM_CONTROL_ENABLED=true
PATHB_FIXED_ORDER_KRW=100000
PATHB_MAX_POSITIONS=1
PATHB_MAX_DAILY_ENTRIES=1
PATHB_MIN_CONFIDENCE=0.5
PATHB_INTRADAY_ONLY=true
PATHB_ALLOW_STOP_LOSS_LOWERING=false
PATHB_ALLOW_SAME_TICKER_WITH_PATHA=false
PATHB_ORDER_UNKNOWN_HALTS_ENTRY=true
PATHB_KR_SLIPPAGE_CAP=1.003
PATHB_US_SLIPPAGE_CAP=1.002
PATHB_SELL_PARTIAL_WAIT_SEC=10
PATHB_PRE_CLOSE_MARKET_FALLBACK=true
PATHB_PRE_CLOSE_TIMEOUT_MINUTES=5
PATHB_EMERGENCY_DISABLE=false
```

## Runtime Flow

```text
SCOUT
-> JUDGE/select_tickers
-> shared watchlist/trade_ready
   -> Path A timing_adapter
   -> Path B Claude price plan
-> shared Safety Gate
-> shared Order Executor
-> shared Lifecycle DB with path_type/path_run_id
```

Path B uses the existing `select_tickers()` Claude call. It does not add an extra Claude call in v1.

## Phase 1: Documentation, Config, Selection JSON

Files:

- `pathb_v2_live_plan.md`
- `config/v2.py`
- `.env.live`
- `config/v2_start_config.json`
- `minority_report/analysts.py`
- `bot/candidate_policy.py`

Work:

- Add Path B live env defaults.
- Add Path B config fields and reason/close codes.
- Extend ticker selection prompt with `price_targets`.
- Preserve normalized `price_targets` only for `trade_ready` tickers.
- Keep old Path A behavior working when `price_targets` is missing.

Validation:

- `price_targets` survives `normalize_selection_result()`.
- Existing selection tests and V2 tests still pass.
- `US_FIXED_ORDER_USD` remains unused for Path B sizing.

## Phase 2: Path Foundation

Files:

- `lifecycle/models.py`
- `lifecycle/event_store.py`
- `tests/test_pathb_foundation.py`

Work:

- Add `PathType`.
- Add Path B event types.
- Add `make_path_run_id()`.
- Add `v2_path_runs` table.
- Add `create_path_run`, `update_path_run`, `find_path_run`, `path_runs_for_session`, `active_path_runs_for_ticker`.
- Do not alter `lifecycle_events` schema in v1.
- Store `path_type`, `path_run_id`, and `parent_decision_id` in lifecycle event payload.

Path run statuses:

```text
WAITING
HIT
ORDER_SENT
ORDER_ACKED
PARTIAL_FILLED
FILLED
SELL_SENT
SELL_ACKED
SELL_PARTIAL_FILLED
CLOSED
EXPIRED
CANCELLED
ORDER_UNKNOWN
```

Validation:

- Same `decision_id` can have independent Path A and Path B runs.
- Active lookup returns only live active statuses.
- Existing event store behavior is unchanged.

## Phase 3: Claude Price Plan

Files:

- `decision/claude_price_plan.py`
- `tests/test_pathb_plan.py`

Work:

- Add `PricePlan`.
- Parse Claude `price_targets`.
- KR prices are KRW native, US prices are USD native.
- Validate price order, confidence, prompt stage, and reward/risk.
- Preserve `hold_days` as storage-only while `PATHB_INTRADAY_ONLY=true`.
- Use `invalidation_conditions` at plan time; actual `invalidation_reason` is recorded on cancel/close events.

Validation:

- Numeric strings like `"52,000"` parse.
- Reversed target/stop is rejected.
- Missing fields do not crash Path A.

## Phase 4: Path B Safety And Buy Adapter

Files:

- `execution/safety_gate.py`
- `execution/claude_price_adapter.py`
- `tests/test_pathb_safety.py`
- `tests/test_pathb_adapter.py`

Work:

- Add `PathBSafetyGate`.
- Reuse existing `SafetyGate` first, then apply Path B-specific blocks.
- Add KR tick rounding helper using KRX tick bands.
- Add US cent rounding helper.
- Detect buy-zone touch and produce an order intent.

Buy limit rule:

```text
slippage_price = round_up_to_tick(current_price * cap)
zone_cap = round_down_to_tick(buy_zone_high)
limit_price = min(slippage_price, zone_cap)

if limit_price < current_price:
    block with ZONE_EDGE_NO_VALID_LIMIT
```

Validation:

- `CLAUDE_PRICE_HIT` is distinct from `FILLED`.
- `ZONE_EDGE_NO_VALID_LIMIT` blocks non-executable edge cases.
- Existing SafetyGate tests still pass.

## Phase 5: Path B Sell Manager

Files:

- `execution/claude_price_sell_manager.py`
- `tests/test_pathb_sell.py`

Work:

- Add hard stop, Claude stop, Claude target, and pre-close exit detection.
- Do not record target/stop hit until sell is actually closed.
- For sell partial fills: wait `PATHB_SELL_PARTIAL_WAIT_SEC`, then market fallback the remainder.
- For pre-close: try close 10 minutes before close; market fallback at 5 minutes if needed.
- Stop revision may only tighten stop loss.

Exit priority:

```text
1. ORDER_UNKNOWN blocks new action
2. Hard Stop
3. Claude stop_loss
4. Claude sell_target
5. Pre-close / time stop
```

Validation:

- Hard stop wins over Claude stop.
- Target touch emits a sell signal, not a completed close.
- Stop loss lowering is rejected.

## Phase 6: PathBRuntime Production Wiring

Files:

- `runtime/pathb_runtime.py`
- `trading_bot.py`
- `tests/test_pathb_runtime.py`

Work:

- Initialize `self.pathb` in live runtime.
- Register price plans after `_apply_selection_meta()`.
- Scan waiting plans in the existing intraday run cycle.
- Use shared order functions for buy/sell.
- Track order sent/fill/unknown in `v2_path_runs`.
- Recover active path runs on startup.
- Expire/cancel waiting plans on session close.

Validation:

- `trading_bot.py --live` loads Path B enabled by default.
- Path A continues to work if Path B registration fails.
- Path B can be disabled without stopping Path A.

## Phase 7: Telegram Control

Files:

- `interface/v2_telegram.py`
- `telegram_commander.py`
- `runtime/pathb_runtime.py`
- `tests/test_pathb_control.py`

Commands:

```text
/pathb_on
/pathb_off
/pathb_kill
/pathb_status
/pathb_closeall
```

Semantics:

```text
/pathb_off  = no new plan or buy; existing FILLED positions remain protected by sell manager
/pathb_kill = no new plan or buy; WAITING/HIT cancel; FILLED positions market-close
```

Validation:

- Default state is enabled.
- `/pathb_off` does not disable protective selling.
- `/pathb_kill` changes state and exposes emergency action status.

## Phase 8: Dashboard And Daily Review Minimum

Files:

- `dashboard/dashboard_server.py`
- `review/daily_review.py`
- `interface/v2_ops_summary.py`

Work:

- Expose Path B status endpoint/summary.
- Show enabled/off/kill state.
- Show counts by WAITING/HIT/FILLED/CLOSED/ORDER_UNKNOWN.
- Add daily review Path B summary.

Validation:

- Current day Path B state can be understood without raw logs.

Implemented minimum:

- `interface/v2_ops_summary.py` exposes `Path Comparison`.
- `trading_bot.py` writes Path B status and path metadata into live status JSON.
- `review/daily_review.py` writes Path A/Path B comparison counts.

## Phase 9: Final QA, Simulation, Report

Work:

- Re-read this MD and update missing/incorrect items.
- Run py_compile.
- Run existing V2 tests.
- Run Path B tests.
- Run phase QA.
- Run live smoke with KR/US and Path B enabled.
- Run daily loop dry-run and simulation/report generation.
- Write final implementation report covering:
  - runtime configuration
  - Claude prompt shape
  - buy/sell path behavior
  - kill switch behavior
  - known limitations

Final acceptance:

```text
python trading_bot.py --live
```

must be ready to start Path A and Path B together on the next market session, with Path B on by default and controllable from Telegram.

## Implementation Status On 2026-04-26

Completed:

- Phase 1 through Phase 8 minimum live implementation.
- Path B is enabled by default through config and `.env.live`.
- Claude `select_tickers()` now asks for `price_targets` without adding an extra Claude call.
- Path B registers `claude_price` path runs from `trade_ready` tickers.
- Path B live buys use fixed 100,000 KRW source sizing, shared broker order functions, shared Safety Gate, and `v2_path_runs`.
- Fixed sizing now rounds quantity up when needed to meet the configured minimum order amount, if cash is sufficient.
- Path B live sells use the existing production sell executor, with Path B target/stop/pre-close signals and lifecycle close marking.
- Telegram control is available through `/pathb_status`, `/pathb_on`, `/pathb_off`, `/pathb_kill`, `/pathb_closeall`.
- Daily review includes minimum Path A vs Path B comparison.

Verified:

- `python -m py_compile runtime\pathb_runtime.py trading_bot.py interface\v2_telegram.py interface\v2_ops_summary.py review\daily_review.py runtime\v2_lifecycle_runtime.py`
- `python -m unittest discover tests`
- `python -m unittest tests.test_pathb_claude_contract`
- `python tools\v2_daily_loop.py --runtime-mode live --dry-run`
- `python tools\v2_phase_gate.py --phase 6 --qa --simulation-report`
- `python tools\v2_live_smoke.py`

Additional QA result:

- Mocked Claude API-shape payloads were injected with normal, malformed, and legacy JSON.
- Normal payload completed Path B buy intent, fill marking, and target close marking without external broker/Claude calls.
- Malformed payload was blocked as `CLAUDE_PRICE_INVALID` without crashing.
- Legacy payload with no `price_targets` left Path A behavior intact and did not register Path B.
- Order exception/rejection handling now records `ORDER_UNKNOWN` through the adapter's `detail=` contract.
- Startup recovery now reattaches active Path B state and escalates missing order/position truth to `ORDER_UNKNOWN`.

Known live-minimum limitation:

- `ClaudePriceSellManager` contains the sell partial TTL contract and event model, but live sell execution currently reuses the existing `_execute_sell()` behavior. Dedicated sell pending-order reconciliation and automatic remainder re-ordering should be implemented before increasing Path B size above min-size live.
