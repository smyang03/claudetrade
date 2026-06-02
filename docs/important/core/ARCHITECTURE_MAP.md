# Architecture Map

Updated: 2026-06-02

## Runtime Shape

```text
trading_bot.py
  -> session and market orchestration
  -> candidate selection
  -> Claude selection and judgment
  -> strategy signals and PathB price plans
  -> action routing
  -> affordability and risk checks
  -> broker order execution
  -> lifecycle, audit, ML, dashboard, logs
```

## Main Components

| Area | Main Paths | Role |
| --- | --- | --- |
| Main loop | `trading_bot.py` | KR/US session flow, candidate handling, route merge, order path. |
| Broker/KIS | `kis_api.py`, `runtime/broker_truth_snapshot.py` | Order, balance, fill, and broker truth integration. |
| Risk | `risk_manager.py`, `runtime/`, `execution/` | Affordability, exposure, halt, quarantine, and market risk gates. |
| Path A | `trading_bot.py` | Claude selection, strategy signal, affordability/risk, order creation. |
| Path B | `runtime/pathb_runtime.py` | Claude price-plan driven entry/exit, now live for KR and US by approved gate. |
| Routing | `runtime/action_routing.py` | RouteDecision merge point for Path A and Path B actions. |
| Strategy | `strategy/` | Signal logic, adaptive params, market policy inputs. |
| Audit | `audit/`, `data/audit/` | Candidate audit, counterfactual stores, traceability. |
| Lifecycle/ML | `lifecycle/`, `ml/`, `data/ml/` | V2 events, canonical performance, legacy decision logs. |
| Dashboard | `dashboard/` | Live truth, status, PnL, candidate audit, and operator views. |
| Tools | `tools/` | Preflight, guardian, sync, backfill, analysis, and operational scripts. |

## Truth Priority

1. Broker holdings, open orders, and fills.
2. V2 lifecycle and canonical performance for live fill/performance truth.
3. Candidate audit and ticker selection DB for candidate trace and quality.
4. Legacy `data/ml/decisions.db` for signal/evaluation history, not sole fill truth.
5. `state/brain.json` for policy memory only.

## Safety Principles

- Broker distrust or quarantine blocks new entries before local strategy preference.
- Selection quality and execution/risk failures must be diagnosed separately.
- PathB live gates and order-size settings are operator-controlled configuration.
- AI can advise selection and HOLD/SELL reasoning, but cannot override broker truth, hard stops, or final order amount.

## Code-Level Runtime Flow

This is the durable call-flow map for KR/US live runtime. Use it when checking
whether a strategy, selection, route, or hold-advisor change affects live orders.

```text
main()
  -> schedule session_open / run_entry_scan / intraday review / session_close

session_open(market)
  -> refresh token and runtime state
  -> _sync_runtime_with_broker()
  -> PathB refresh/reconcile/scan existing runs
  -> build universe, digest, Claude judgment, Claude selection
  -> _apply_selection_meta()
       -> _apply_candidate_action_live_routes()
            -> route_candidate_action()
                 BUY_READY / PROBE_READY / ADD_READY -> Path A trade_ready
                 PULLBACK_WAIT -> PathB _pathb_wait_tickers
                 WATCH / AVOID / HARD_BLOCK -> no live entry
       -> enforce trade_ready price targets
       -> attach affordability and evidence metadata
       -> register V2 trade_ready decision ids
       -> pathb.register_from_selection_meta()

run_entry_scan(market)
  -> run_cycle(market)
       -> PathB scan_waiting_entries()
       -> PathB scan_exits()
       -> Path A ticker loop
            -> trade_ready / broker / risk / timing gates
            -> strategy signal evaluation
            -> entry_priority scoring
            -> pending signals sorted by score
            -> Path A arbitration, safety, broker precheck
            -> place_order(..., "buy")
            -> _add_pending_order()
```

Important execution contracts:

- `PULLBACK_WAIT` is not Path A `trade_ready`. It is stored as `_pathb_wait_tickers`
  and registered through PathB with `_pathb_registration_scope =
  "candidate_actions_wait_only"`.
- Preopen selection may still pass through `_apply_selection_meta()`, but
  `_force_preopen_watch_only()` clears Path A `trade_ready`. Any PathB plan
  registered from preopen data still must pass market-open, broker-truth, and
  `_new_buy_block_state()` gates before an order can be submitted.
- PathB entry is fail-closed on live broker truth through
  `_entry_scan_broker_truth_gate()`. Token, provider, stale, or error states
  block live entry scan as `BLOCKED_BROKER_TRUTH`.
- US `momentum` is live-allowed only when `US_MOMENTUM_LIVE_ENABLED=true`, but
  it enters the Path A dispatch order only when analyst vote or selection
  recommended strategy places it into the strategy order.
- KR Path A immediate execution for `momentum`, `gap_pullback`, and
  `opening_range_pullback` is separately gated by
  `KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED`, `KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED`,
  and `KR_PLAN_A_ORP_SIGNAL_ENABLED`.

## Sell and Hold-Advisor Flow

```text
Path A exit:
  risk.get_exit_candidates()
    -> _process_exit_candidates()
       -> TP_REVIEW for take-profit/trailing candidates
       -> _execute_sell()
            -> _run_auto_sell_review_gate() when review is required
            -> broker precheck / place_order(..., "sell")

Path B exit:
  pathb.scan_exits()
    -> reconcile pending/filled PathB state
    -> evaluate policy stop, MFE breakeven, loss_cap, hard_stop,
       profit_ladder, PathB policy, Claude target/stop, pre-close force exit
    -> _submit_sell()
         -> _run_pathb_sell_review_gate()
              HOLD -> stop submit and save bounded hold/protective policy
              SELL -> broker precheck / place_order(..., "sell")

Hold advisor scheduled reviews:
  _pre_session_position_review() -> PRE_SESSION
  _intraday_position_review() -> INTRADAY_REVIEW
  session_close() -> PRE_CLOSE_CARRY
```

Hold advisor is connected to HOLD/SELL review and policy boundaries after a
position exists. It is not the final order sizing engine and does not replace
broker truth or hard risk gates.

## Flow-Break Audit Matrix

Use this section to debug "value should have flowed but did not" failures. A
flow break can be an actual missing field, a stale value that is intentionally
discarded, or a live gate that intentionally converts an executable action into
WATCH/shadow.

| Item | Expected value path | Break pattern | Runtime result | Current guard / proof |
| --- | --- | --- | --- | --- |
| Intraday evidence fetch | minute candles -> `compute_intraday_features()` -> `_prefetch_selection_intraday_evidence()` -> `_last_post_open_features_by_ticker` | provider disabled, session open resolve failure, timeout, no usable candles | `data_quality=minute_missing`, `fail_closed=true`, `evidence_data_state=missing`, action ceiling WATCH | `selection_intraday_evidence_coverage` funnel event; `tests/test_trading_bot_intraday_evidence.py` |
| `volume_ratio_open` | candle volume + avg daily volume -> `volume_ratio_open`; fallback from candidate `vol_ratio`/`volume_ratio` | avg volume missing and candidate ratio also missing | feature becomes `minute_partial`; KR volume confirmation can fail; live evidence ceiling can demote BUY_READY to PROBE/WATCH | fallback is applied in `_prefetch_selection_intraday_evidence()` before reclassifying quality; `tests/test_intraday_features.py`, `tests/test_live_evidence_pack.py` |
| Opening range cache | `opening_range_high/low` -> `_maybe_update_or_cache_from_post_open_feature()` -> `_or_high/_or_low/_or_formed` -> ORP strategy params | `data_quality=minute_missing`, missing/invalid high/low, or no WS/poll price during OR window | `_or_formed` stays false; ORP cannot produce a valid formed-range signal | `minute_partial` with high/low does form OR cache; `minute_missing` does not; WS/poll fallback can form after OR window if high/low were observed |
| Cached post-open features | same-session cached features -> candidate rows -> runtime execution context | previous-session cache or fail-closed replacement attempts | stale rows are replaced with `minute_missing` sentinel or protected from overwriting better partial data | `stale_cached_feature` and fail-closed replacement tests in `tests/test_trading_bot_intraday_evidence.py` |
| Live evidence pack | post-open features -> `build_live_evidence_pack()` -> `evidence_data_state/action_ceiling` -> `route_candidate_action()` | missing current price, momentum returns, OR/VWAP/volume confirmation fields | missing => WATCH ceiling; partial => PROBE ceiling unless partial grace allows early BUY_READY | `tests/test_live_evidence_pack.py`, `tests/test_action_routing.py` |
| KR confirmation gate | execution context `ret_3m/ret_5m`, `vwap`, `opening_range_high`, `volume_acceleration`, `spread_bps`, `data_quality` -> `_kr_confirmation_gate_state()` | `data_quality` missing/partial, returns missing, VI/spread veto, optional OR/VWAP/volume requirements not met | non-shadow gate demotes BUY_READY/PROBE_READY to WATCH with `kr_*_not_confirmed` reason | `tests/test_candidate_action_live_mapping.py::test_kr_confirmation_*` |
| Candidate action membership | Claude `candidate_actions` -> watchlist key normalization -> `_apply_candidate_action_live_routes()` | action ticker not present in normalized watchlist | `HARD_BLOCK`, reason `off_list_action` | candidate action route event and mapping tests |
| Candidate health/trainer gate | `_candidate_runtime_gate_info()` -> `_apply_candidate_action_live_routes()` before routing | same-day stopped, `FAILED_READY`, `WEAKENING_READY`, `ready_degraded`, trainer `QUARANTINE` | `HARD_BLOCK` / candidate quarantine before Path A or Path B execution | gate event payload includes health/trainer fields; candidate action and trainer tests |
| Route/order lock | active order route / PathB active order -> `route_candidate_action()` | same ticker already has active order, or PathB active order exists while Plan A entry is requested | WATCH, `active_order_lock:*` or `pathb_active_order_blocks_plana` | `runtime/action_routing.py`, `tests/test_action_routing.py` |
| New-buy global gate | Path A loop or PathB `_submit_buy()` -> `_new_buy_block_state()` | market closed, entry blackout, stop cluster, analyst new-buy block/gross cap, unresolved ORDER_UNKNOWN, US broker sync quarantine | no new entry; `buy_blocked`/`SAFETY_BLOCKED` with `MARKET_CLOSED`, `ENTRY_BLACKOUT`, `STOP_CLUSTER_*`, `ANALYST_NEW_BUY_BLOCK`, `ORDER_UNKNOWN_UNRESOLVED`, `BROKER_SYNC_QUARANTINE` | `trading_bot.py::_new_buy_block_state()`, `runtime/pathb_runtime.py::_new_buy_block_state()` |
| Path A `trade_ready` price targets | `trade_ready` + `price_targets` -> `_enforce_trade_ready_price_targets()` -> `trade_ready_tickers` | trade_ready ticker lacks price target and is not explicitly allowed by retry/soft promotion | removed from Path A executable set; `_runtime_filtered_trade_ready[ticker]=missing_price_target` | `tests/test_patha_contract.py` |
| Entry price cap / chase guard | `max_entry_price`, `cancel_if_open_above`, `buy_zone_high` -> `_resolve_entry_price_cap()` / `route_candidate_action()` | current price above cap, or KR BUY_READY lacks any cap source | WATCH `buy_ready_chase_blocked` / `buy_ready_price_cap_exceeded`; KR missing cap demotes BUY_READY to PROBE or keeps PathB wait | `tests/test_action_routing.py`; cap source is recorded in `entry_price_cap_candidates` |
| Path A strategy dispatch | `recommended_strategy` -> `_prioritize_strategy_order()` -> strategy signal loop -> pending signal | strategy missing, not live-allowed, not dispatchable, or KR Plan A signal gate disabled | recommendation may remain in audit, but no live signal/order; recorded as no-signal or strategy policy ignore | `US_MOMENTUM_LIVE_ENABLED` required for US momentum; KR Plan A momentum/gap/ORP gates are separate config |
| KR Path A order-time gate | pending Plan A signal -> `_kr_late_entry_order_time_gate()` before order submit | same-day stopped, stale late signal, price above cap, outside allowed order window | no broker order; recorded as late/order-time blocked signal | `KR_LATE_ENTRY_EXEC_GATE_ENABLED` path in `trading_bot.py` |
| V2 execution safety gate | final qty/price/cash/positions/pending/daily loss -> `execution.safety_gate.SafetyGate` | ORDER_UNKNOWN, market closed, broker untrusted/quarantine, invalid price/qty, stale data, same-day stop, already holding, pending order, max positions/daily entries, daily loss, min order, insufficient cash | hard block with explicit `SAFETY_REASON_CODES`; no order submit | `execution/safety_gate.py`, PathA/PathB safety payloads |
| Evidence ceiling | `build_live_evidence_pack()` -> `evidence_data_state/action_ceiling` -> `route_candidate_action()` | missing/fail-closed evidence, WATCH ceiling, or PROBE ceiling on BUY_READY | WATCH `data_fail_closed_watch_only` / `evidence_ceiling_watch`, or BUY_READY demoted to PROBE_READY | `tests/test_live_evidence_pack.py`, `tests/test_action_routing.py` |
| Soft-gate override validation | Claude `soft_gate_overrides` + runtime `soft_gates` -> `validate_soft_gate_override()` | override lacks fresh momentum, OR/VWAP/volume confirmation, or price-cap proof | WATCH `soft_gate_override_failed`; no local promotion without evidence | `runtime/action_routing.py::validate_soft_gate_override()`, routing tests |
| KR risk-combo gate | KR action `risk_tags` / `from_high_bucket` -> OR-missing + high-entry combo in `route_candidate_action()` | BUY_READY or PULLBACK_WAIT while OR is missing and entry is near/at high, without confirmation recovery | BUY_READY demoted to PROBE; PULLBACK_WAIT becomes WATCH `kr_risk_combo_confirmation_required` | `runtime/action_routing.py`, `tests/test_action_routing.py` |
| KR late-entry gate | `_kr_late_entry_gate_state()` -> `_apply_candidate_action_live_routes()` | late replacement, after probe window, stale late entry, missing fresh momentum, trainer BENCH/QUARANTINE | WATCH (`kr_late_*`) or BUY_READY demoted to PROBE_READY for fresh late entries | `KR_LATE_ENTRY_*` runtime config and candidate action mapping tests |
| PathA-vs-PathB wait arbitration | `BUY_READY`/`PROBE_READY` + existing PathB wait -> `route_candidate_action()` -> optional `cancel_pathb=True` | confidence below threshold, overextended, inside PathB buy zone, KR missing price cap, or bad/missing data | PathB wait is preserved as WATCH; cancellation only when confidence, price, momentum, and data checks all pass | `c7aab0f` added `minute_complete` to good-data vocabulary; remaining risk is confirmed evidence not being backfilled into routing `data_quality` |
| Partial historical data | candidate usable row count + recommended strategy + price target -> `_partial_data_trade_ready_decision()` | usable rows below min, strategy not allowlisted, missing price target | watch-only or capped partial-data execution; no normal-size promotion | partial data guard tests and `PARTIAL_DATA_*` config |
| PathB wait registration | `PULLBACK_WAIT` + complete `price_targets` -> `_pathb_wait_tickers/_pathb_price_targets` -> `register_from_selection_meta()` -> `parse_plan_from_claude()` | missing buy zone/target/stop/hold/confidence, invalid confidence, plan price above budget cap | route becomes WATCH (`missing_pullback_target`) or registration blocked (`CLAUDE_PRICE_INVALID`, `HIGH_PRICE_BUDGET_BLOCK`) | `runtime/action_routing.py::has_pullback_target()`, `tests/test_pathb_runtime.py` |
| PULLBACK negative context | `PULLBACK_WAIT` or WATCH reason/momentum/data quality -> `_negative_watch_context()` | fade/weak/direction-unconfirmed, bad/stale data, or negative text while PathB is waiting | PULLBACK_WAIT becomes WATCH; existing PathB wait can be suspended or preserved by hysteresis | `tests/test_action_routing.py`; `pathb_wait_negative_watch_count` in runtime gate |
| KR healthy pullback shadow | negative KR PULLBACK_WAIT context -> `_kr_healthy_pullback_shadow_payload()` | recovered pullback candidate has confirmed evidence, price inside cap, no VI/halt/spread/repeated-failed veto | shadow-only `kr_healthy_pullback_shadow`; no PathB wait registration and no order | `tests/test_action_routing.py::test_kr_pullback_*shadow*` |
| ADD/AVOID/EXPIRED routing | `ADD_READY`, `AVOID`, `EXPIRED` -> `route_candidate_action()` | ADD without broker/local position, add disabled, Claude AVOID, or expired action | WATCH `add_without_position` / `add_shadow_only` / `claude_avoid`, or `EXPIRED`; AVOID can shadow-suspend PathB wait | `tests/test_action_routing.py` |
| PathB control and registration | selection `PULLBACK_WAIT` -> `register_from_selection_meta()` / PathB control state | PathB disabled/emergency-disabled, market live disabled, shadow-only plan, invalid plan, low confidence, high price over registration cap | no live wait, cancelled wait, or shadow wait only; reasons include `PATHB_MANUALLY_DISABLED`, `PATHB_DISABLED`, `CLAUDE_PRICE_INVALID`, `HIGH_PRICE_BUDGET_BLOCK`, `PATHB_CONFIDENCE_TOO_LOW` | `PathBSafetyGate`, `_pathb_registration_price_gate()`, PathB registration tests |
| KR PathB shadow filters | `_pathb_wait_tickers` -> `_apply_kr_pathb_mode_gate()` / `_apply_kr_pathb_strategy_filter()` | mode/strategy not allowlisted, or missing strategy metadata | with current shadow config, only shadow diagnostics; if enabled, PathB wait is removed before registration | `_kr_pathb_*_shadow` payloads; current live config has these filters shadow-only |
| PathB entry scan | WAITING PathB run -> `scan_waiting_entries()` -> broker-truth gate -> buy-zone hit -> `_submit_buy()` | PathB disabled, market live disabled, broker truth unavailable/stale/error, new-buy block, `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK`, current price unavailable, risky-origin confirmation fail | no order submitted; wait can be preserved, cancelled, or shadow-scanned with block reason/audit event | fail-closed broker-truth contract and PathB runtime tests |
| PathB pending BUY reconcile | PathB `ORDER_SENT/ORDER_ACKED` -> broker open orders/fills -> `reconcile_buy_pending_cancel_above()` / TTL reconcile | cancel-above after ACK, filled/open-order mismatch, TTL expired, ambiguous/missing broker evidence | cancel confirmed, filled, still open, or `ORDER_UNKNOWN`; no blind retry | `runtime/pathb_runtime.py::_reconcile_buy_pending_*` |
| PathB miss-quality follow-up | cancelled/missed PathB plan -> `process_miss_quality_followups()` | no baseline/identity, due after close, no usable quote sample, quote error | records `insufficient_quotes`, `market_closed`, `quote_error`, or 30m MFE/MAE follow-up; no order side effect | `runtime/pathb_runtime.py::_fill_miss_quality_followup()` |
| PathB sizing/capacity | current native price -> KRW price, fixed order budget, cash/equity caps -> `_pathb_qty_with_context()` | invalid price, fixed budget below minimum, one-share cap exceeded, early-gate budget too small beyond tolerance | qty=0 with split reason (`INVALID_PRICE`, `ORDER_SIZE_TOO_SMALL_GATE`, `HIGH_PRICE_BUDGET_BLOCK`, etc.) | sizing reason split is protected; `tests/test_pathb_runtime.py`, `tests/test_live_order_safety.py` |
| PathB submit safety | `_submit_buy()` -> `PathBSafetyGate.evaluate()` after sizing | duplicate PathA/PathB holding, PathB max positions/daily entries, low confidence, order-unknown halt, manual/emergency disabled | wait may be cancelled or preserved for temporary early-size block; no broker order | `execution/safety_gate.py::PathBSafetyGate`, `_pathb_submit_safety_block_keeps_waiting()` |
| PathB broker BUY submit | safety-passed PathB hit -> `precheck_order()` -> `place_order()` -> pending order | precheck exception/fail, broker reject, order exception, missing order number | `BROKER_UNTRUSTED`, `precheck_failed`, or `ORDER_UNKNOWN`; successful submit becomes `ORDER_SENT/ORDER_ACKED` and pending order | `_submit_buy()`, order unknown reconcile tests |
| Plan A affordability | strategy signal -> risk price -> fixed order/budget/cash/market cap -> broker precheck | order cost above available budget, qty zero, minimum order not met | `TRADE_READY_NO_SUBMIT` / `buy_skipped` with affordability reason; no broker order | `plan_a_affordability` block meta and ML/audit no-submit rows |
| Broker truth sync | broker holdings/open orders/fills -> `_sync_runtime_with_broker()` -> risk positions and PathB metadata | empty/missing broker snapshot while local positions exist, untrusted snapshot, open order remaining qty not zero | protected `broker_missing_unconfirmed`; no destructive local removal until fresh zero-holding evidence | zero-holding stale reconcile contract; broker sync metadata tests |
| Pending/fill reconcile | order sent/acked -> broker fills/open orders with `remaining_qty` -> lifecycle/EventStore | mismatched fill order number, broker truth unavailable, partial fill remaining qty > 0 | remains pending or becomes `ORDER_UNKNOWN`; no false FILLED/CLOSED attribution | `remaining_qty` preservation tests, `tests/test_pathb_sell_reconcile.py` |
| PathB active-run recovery | startup/exit scan PathB rows -> local pending/position/broker truth -> recovery helpers | ORDER_UNKNOWN or entry-pending row has local PathB holding; filled row has no broker position and no causal sell fill | recover to `FILLED`, keep open locally, close with causal sell fill, or mark `ORDER_UNKNOWN` with ambiguity metadata | `_recover_order_unknown_local_holding()`, `_recover_entry_pending_local_holding()`, `_reconcile_filled_position_run()` |
| PathB exit trigger priority | `scan_exits()` -> policy stop breach, MFE breakeven, loss cap, hard stop, profit ladder, hold policy, Claude sell manager, profit review, pre-close review/force exit | current price missing, sell in-flight, no local position, policy skip, no trigger, review outside window | no sell submitted until a signal exists; strict stops/profit ladder/pre-close force can create `ExitSignal` | `runtime/pathb_runtime.py::scan_exits()`, profit/exit tests |
| Hold advisor AUTO_SELL_REVIEW | exit signal -> `_run_pathb_sell_review_gate()` -> `hold_advisor.ask()` -> SELL/HOLD policy | advisor unavailable, fallback HOLD, repeated HOLD review, untrusted sellable qty | hard stop/loss cap/profit ladder can fail-safe SELL; HOLD cooldown suppresses repeated calls; protective/target policy is bounded | cooldown guard is protected; `tests/test_auto_sell_claude_gate.py` |
| PathB profit review | MFE/profit ladder floor -> `_maybe_trigger_profit_protection_review()` -> hold advisor | missing `path_run_id`, invalid current price, market closed, protective hold active, peak below trigger, cooldown/per-scan cap | no advisor call; existing position management continues | profit protection tests and timeout/debounce payloads |
| PathB pre-close carry review | intraday-only PathB position -> `_maybe_run_pre_close_carry_review()` -> hold advisor -> `_pre_close_force_exit()` | not intraday-only, market closed, outside review window, inside force-exit window, already reviewed, broker truth untrusted, broker/local position missing | CARRY/SELL decision stored; SELL decision can later force `CLOSED_CLAUDE_PRICE_PRE_CLOSE` | `_pre_close_carry_gate()`, `_pre_close_force_exit()` |
| PathB sellability and SELL submit | exit signal -> `_submit_sell()` -> sellability observation/review/precheck/order | sell attempt lock, sellability untrusted, sell in-flight, qty<=0, invalid sell price, observation required, review HOLD, precheck fail, sellable qty reject, broker reject | no duplicate sell; may require manual reconcile, recover open sell order, mark sell pending, or mark ORDER_UNKNOWN on exception | `_submit_sell()`, sellable-qty reject evidence/recovery, sell reconcile tests |
| PathB sellable-qty reject evidence | broker reject message -> `_handle_pathb_sellable_qty_reject()` -> broker truth/open order/fill evidence | broker truth unavailable, no open order/fill/position, ambiguous sell evidence | recover existing sell order/fill when causal evidence exists; otherwise mark sellability untrusted/manual reconciliation | `_pathb_sellable_qty_reject_evidence()`, protected sell reconcile tests |
| Lifecycle/audit linkage | order/lifecycle payload -> V2 lifecycle, candidate audit, ML logs, dashboard | missing `path_run_id`, missing execution id, legacy rows without close/fill lifecycle | consistency warning or historical remediation candidate; not used as broker truth | `consistency_health()`, lifecycle gap QA, dashboard/preflight tests |
| Policy memory contamination | `state/brain.json` -> prompt policy context only | unapproved direct edit or auto-promotion of short-term lesson | can bias future prompts but is not runtime truth; should be reviewed via lesson candidates | live preflight warns on dirty brain; automatic promotion remains blocked by operating contract |

Current 2026-06-02 observations:

- The earlier class of root cause "missing `volume_ratio_open` -> `minute_partial`
  -> permanent `or_formed=false`" is not currently reproduced when OR high/low
  are present. `minute_partial` can now form OR cache; only `minute_missing` or
  missing/invalid OR high/low blocks OR cache formation.
- KR has an operational capacity mismatch in the latest live preflight snapshot:
  fixed PathB order is 450,000 KRW while the market gross exposure cap was about
  401,232 KRW, so `today_affordable_fixed_orders=0`. This is a conservative
  sizing/capacity issue, not a missing-value bug.
- Historical US PathB `ORDER_UNKNOWN` / stale-active rows still exist as audited
  remediation candidates, but current-session broker/local exposure was zero in
  the preflight snapshot. Do not auto-close without broker-truth review.
- KR PathB mode and strategy filters are currently shadow diagnostics when their
  live `*_ENABLED` flags are false. They should not remove live waits unless the
  live flags are enabled.
- `state/brain.json` has been seen dirty in preflight. Treat it as policy memory
  only; do not use it to reconcile orders, holdings, or fills.
- PathB wait-cancel log replay needs two buckets. `logs/funnel` contained 14
  `pathb_waiting_kept_bad_data` rows: 6 old `first_observed` rows with no
  evidence pack, and 8 US rows with `data_quality=minute_complete`,
  `data_quality_missing=false`, and confirmed evidence. Commit `c7aab0f`
  should address the 8 vocabulary-only historical rows by adding
  `minute_complete` to the good-data set. A separate remaining risk is
  `evidence_data_state=confirmed` while routing `data_quality` is still
  `missing`; in that case `not data_quality_missing` remains false and
  evidence confirmation is not enough to cancel PathB wait unless the context
  is backfilled or the predicate explicitly accepts confirmed evidence.

## Commit-Based Intent Notes

Use these notes when deciding whether a flow break is a deliberate guardrail or
an accidental value mismatch.

- OR cache: commit `b655674` explicitly fixed the `volume_ratio_open` ->
  `minute_partial` -> permanent `or_formed=false` failure by allowing
  `minute_partial` rows with valid OR high/low to update the OR cache.
- KR redesign gates: commit `dd11800` deliberately set KR Plan A signal gates
  false and KR PathB mode/strategy filters to shadow-first. That is intentional
  observation/defense, not accidental dead code.
- KR confirmation: commit `6f8fdc1` deliberately accepts `minute_complete` and
  still blocks `minute_partial`/`minute_missing`. It also keeps
  `fade_recovered` as KR-only shadow with WATCH ceiling.
- Capacity visibility: commit `1ce38b3` added gross-exposure capacity reporting,
  including `FIXED_ORDER_SIZE_EXCEEDS_TODAY_CAPACITY`. A fixed 450,000 KRW
  order above today's gross cap is an operating policy mismatch, not a missing
  field bug.
- ORDER_UNKNOWN cleanup: commits `5814592` and `dc26e5e` intentionally make
  stale PathB `ORDER_UNKNOWN` cleanup audited/manual and broker-truth gated.
  Historical eligible rows should not be auto-cleared without operator review.
- PathA-vs-PathB wait arbitration: commits `5d1e9fa`/`d23d0825` intentionally
  made PathB wait cancellation conservative, and `14654b7` made missing data
  fail closed. Commit `c7aab0f` propagated `minute_complete` into the routing
  good-data list. The remaining issue to test is not the set membership; it is
  the handoff where `live_pack.data_state=confirmed` may not backfill
  `context.data_quality`, leaving `data_quality_missing=true`.

## Logic Present But Newly Documented

The following live gates existed in code but were missing or under-specified in
the earlier architecture map:

- Same-ticker active order locks and PathB active-order locks.
- Candidate health/trainer quarantine before routing.
- Evidence pack ceiling and fail-closed demotion.
- Soft-gate override validation.
- KR OR-missing plus high-entry risk-combo demotion.
- KR late-entry freshness/probe-window gate.
- Entry price cap source resolution and KR missing-cap behavior.
- PathB negative-WATCH suspension hysteresis.
- KR healthy-pullback shadow diagnostics.
- ADD/AVOID/EXPIRED non-buy routing.

## Item-Level Operational Analysis

This table classifies each flow-break item as intentional guardrail, historical
bug already fixed, operating-policy mismatch, or unresolved follow-up.

| Item | Intent / current status | Operational finding |
| --- | --- | --- |
| Intraday evidence fetch | Intentional fail-closed guard | Missing provider/candles correctly become `minute_missing`; not data contamination. |
| `volume_ratio_open` | Historical bug class, now guarded | Missing avg volume plus missing candidate fallback can still create `minute_partial`, but fallback is now applied before quality reclass. |
| Opening range cache | Historical bug fixed by `b655674` | `minute_partial` with OR high/low can form cache; only `minute_missing` or invalid OR values should block. |
| Cached post-open features | Intentional stale-data guard | Same-session cache can be reused; stale/fail-closed replacement is prevented from overwriting better partial data. |
| Live evidence pack | Intentional action ceiling | Missing/partial evidence demotes actions by design. Cross-field risk remains when confirmed pack quality is not backfilled into routing `data_quality`. |
| KR confirmation gate | Intentional conservative gate; bug fixed by `6f8fdc1` | `minute_complete` is accepted; `minute_partial/missing` remain blocked on purpose. |
| Candidate action membership | Intentional hard guard | Off-list Claude actions are blocked to prevent untracked live entries. |
| Candidate health/trainer gate | Intentional quality/quarantine guard | `FAILED_READY`, degraded ready, same-day stop, and trainer quarantine block before route creation. Tune thresholds only with performance evidence. |
| Route/order lock | Intentional duplicate-order guard | Active orders or PathB active order state suppress same-ticker Plan A entries. |
| New-buy global gate | Intentional account/market safety guard | Current KR capacity issue is policy mismatch: fixed 450,000 KRW order can exceed gross cap, producing zero affordable fixed orders. |
| Path A price targets | Intentional executable-contract guard | Missing price target removes Path A executable status; root cause is prompt/selection payload completeness, not broker state. |
| Entry price cap / chase guard | Intentional chase-prevention guard | KR missing cap or price above cap demotes/blocks; false block risk comes from missing cap handoff. |
| Path A strategy dispatch | Intentional strategy allowlist/config gate | KR Plan A momentum/gap/ORP are disabled by redesign config; US momentum requires explicit live env. |
| KR Path A order-time gate | Intentional late-entry protection | Late/stale Plan A signals are blocked or demoted; missing age source can make it conservative. |
| V2 execution safety gate | Intentional hard safety layer | Blocks ORDER_UNKNOWN, broker distrust, invalid sizing, stale data, duplicate holding/order, caps, daily loss, min order, and cash. |
| Evidence ceiling | Intentional evidence guard | WATCH/PROBE ceilings are expected. Follow-up needed for confirmed evidence plus missing routing quality. |
| Soft-gate override validation | Intentional anti-promotion guard | Claude soft override is ignored unless fresh momentum and OR/VWAP/volume/price proof exist. |
| KR risk-combo gate | Intentional high-entry/OR-missing guard | OR missing plus near-high entry demotes/blocks; if OR missing is caused by data loss, root cause is upstream evidence. |
| KR late-entry gate | Intentional late-session guard | Late replacements and stale late entries become WATCH; fresh late BUY_READY demotes to PROBE. |
| PathA-vs-PathB wait arbitration | Partially fixed, follow-up required | `c7aab0f` fixes `minute_complete` vocabulary. Remaining test: `evidence_data_state=confirmed` while routing `data_quality_missing=true`. |
| Partial historical data | Intentional capped execution | Low usable rows/strategy not allowlisted/missing target keep watch-only or capped path. |
| PathB control and registration | Intentional operator/config guard | Disabled/emergency/shadow/high-price/invalid-plan states prevent live wait or create shadow-only rows. |
| KR PathB shadow filters | Intentional shadow-first redesign | Current config should diagnose only; live wait removal happens only when enabled flags are true. |
| PathB entry scan | Protected fail-closed path | Broker truth unavailable/stale/error blocks live entries; do not relax. |
| PathB pending BUY reconcile | Intentional broker-truth reconcile | Cancel-above/TTL/open-order/fill ambiguity becomes cancel/fill/still-open/ORDER_UNKNOWN; avoids blind duplicate orders. |
| PathB miss-quality follow-up | Observation-only analytics | Follow-up records MFE/MAE after missed/cancelled plans; it should not affect live orders. |
| PathB sizing/capacity | Protected sizing split | Invalid price, early gate, high-price budget, min order, one-share cap are separated. KR fixed-order/gross-cap mismatch is operating policy. |
| PathB submit safety | Intentional PathB-specific hard gate | Duplicate path holding, PathB max positions/daily entries, confidence, disabled state, and ORDER_UNKNOWN halt block submit. |
| PathB broker BUY submit | Intentional broker-truth boundary | Precheck/order errors become broker block or ORDER_UNKNOWN; broker result remains truth. |
| Plan A affordability | Intentional affordability guard | No-submit rows should be treated as execution/capital issue, not selection quality issue. |
| Broker truth sync | Protected broker-truth priority | Missing broker snapshot preserves local state until independent zero-holding evidence; avoids stale deletion. |
| Pending/fill reconcile | Protected attribution guard | Remaining qty and causal fill evidence prevent false FILLED/CLOSED attribution. |
| PathB active-run recovery | Intentional recovery guard | Local PathB holdings can recover ORDER_UNKNOWN/entry-pending rows; missing causal close evidence stays ambiguous. |
| PathB exit trigger priority | Intentional exit ordering | Policy stop, MFE, loss cap, hard stop, ladder, policy, Claude sell, profit/pre-close review are ordered to protect positions. |
| Hold advisor AUTO_SELL_REVIEW | Protected review/cooldown path | HOLD can preserve policy, but cooldown prevents repeated calls; hard-risk fail-safe remains. |
| PathB profit review | Intentional bounded review | No call when trigger/cooldown/protective/market gates fail; not a data-loss bug. |
| PathB pre-close carry review | Intentional carry/force-exit path | Intraday-only positions get carry review; broker truth failure defaults to no forced sell unless SELL decision exists. |
| PathB sellability and SELL submit | Protected duplicate-sell guard | Sell locks, in-flight state, untrusted sellability, HOLD review, precheck/order rejects stop duplicate/unsafe sells. |
| PathB sellable-qty reject evidence | Protected manual-reconcile path | Recover open sell/fill only with broker evidence; otherwise mark manual reconciliation. |
| Lifecycle/audit linkage | Intentional observability layer | Historical rows can be remediation candidates but are not broker truth. |
| Policy memory contamination | Intentional preflight warning | Dirty `brain.json` can bias prompts but must not drive order/fill reconciliation. |

Highest-priority unresolved follow-ups:

- Add a routing regression test for `pathb_waiting + BUY_READY +
  evidence_data_state=confirmed + evidence_pack.data_quality=minute_complete +
  context.data_quality=missing/data_quality_missing=true`. Decide whether to
  backfill `context.data_quality` from `live_pack.data_quality` or make
  `evidence_confirmed` part of `good_data`.
- Review live/preflight capacity policy for KR fixed 450,000 KRW order versus
  analyst/manual gross cap, because this can make all fixed PathB entries
  unaffordable without being a code bug.
- Treat unresolved historical `ORDER_UNKNOWN` rows as audited remediation only;
  never auto-clear them from local state without broker truth review.

## Current Docs

- Strategy flow audit requirements: [../STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md](../STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md)
- Active work: [../ACTIVE_WORK.md](../ACTIVE_WORK.md)
- Always analyze: [../ALWAYS_ANALYZE.md](../ALWAYS_ANALYZE.md)
- Inventory: [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md)
