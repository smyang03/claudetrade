# KR/US Live Ops QA Plan - 2026-04-27

Purpose: verify live-operation correctness before the US session. This is not a profitability tuning checklist. It is intended to catch configuration drift, order-safety regressions, DB/state contamination, and A/B path visibility gaps before live trading.

## Scope

- Markets: KR and US.
- Runtime: live.
- Paths: Path A `timing_adapter`, Path B `claude_price`.
- Data sources: `.env.live`, `config/v2_start_config.json`, SQLite event store, state files, dashboard API, Telegram command handlers, KIS order helpers.

## 1. Market And Session

- `market.session_calendar`
  - Confirm KR/US trading-day assumptions.
  - Confirm US regular-session window.
  - Confirm no holiday or early-close assumption is blocking startup.
- `session.us_session_date_logic_present`
  - Confirm code has explicit US session-date handling.
  - Print expected US `session_date` for the current KST time.
  - Confirm US session date is not blindly forced to the KST calendar date across KST midnight.

## 2. Config Sources

- `config.env_live_loaded`
  - `.env.live` is readable.
- `config.start_config_applied`
  - `config/v2_start_config.json` is applied when enabled.
- `config.no_unapproved_conflicts`
  - `.env.live` and start-config conflicts are reported.
- `config.kr_us_enabled`
  - `ENABLED_MARKETS` includes both `KR` and `US`.
- `config.pathb_limits`
  - `PATHB_ENABLED`
  - `PATHB_MODE`
  - `PATHB_MAX_POSITIONS`
  - `PATHB_MAX_DAILY_ENTRIES`
  - `PATHB_INTRADAY_ONLY`
  - `PATHB_EMERGENCY_DISABLE`
- `config.us_sizing_fx`
  - `US_FIXED_ORDER_KRW`
  - `US_MIN_ORDER_KRW`
  - `USD_KRW_RATE`
  - FX fallback remains available if live FX refresh fails.

## 3. KIS, Accounts, Tokens

- `kis.kr_token_refresh`
  - Expired KR token triggers forced refresh.
- `kis.us_token_refresh`
  - US balance/order calls use the same safe token-refresh path.
- `kis.us_credentials`
  - `KIS_ACCOUNT_NO_US` present.
  - `KIS_APP_KEY_US` and `KIS_APP_SECRET_US` fallback behavior is explicit.
  - `KIS_IS_PAPER_US=false` for live US.
- `kis.balance_probe`
  - KR balance probe succeeds before KR live.
  - US balance probe succeeds before US live.
- `broker_truth_query_available`
  - KR `inquire_daily_ccld_kr` exists and is callable by order recovery.
  - US `inquire_ccnl_us` exists and is callable by order recovery.
  - Both KR/US recovery paths can query by ticker/side when order number is unknown.

## 4. Order Safety

- `code.kr_order_payload_normalized`
  - KR `ORD_QTY` is an integer string.
  - KR `ORD_UNPR` is an integer string, not a float string.
  - KR payload includes `EXCG_ID_DVSN_CD="KRX"`.
  - KR payload includes `CNDT_PRIC=""`.
- `code.kr_order_500_recovery_wired`
  - KR HTTP 500 triggers broker-truth query.
  - Truth-query failure skips retry and keeps state unknown.
  - Truth success recovers order state.
  - No broker truth permits one retry only.
  - KR order is not wrapped by blind `_retry_kis(... retries=5)`.
- `code.us_order_500_recovery_wired`
  - US HTTP 500 triggers broker-truth query.
  - Truth-query failure skips retry and keeps state unknown.
  - Truth success recovers order state.
  - No broker truth permits one retry only.
  - US order is not wrapped by blind `_retry_kis(... retries=5)`.
- `code.order_error_raw_response_logged`
  - HTTP order errors log status code.
  - HTTP order errors log response body.
  - Order body is logged only after account masking.
- `code.us_exchange_map_coverage`
  - Every `_US_FALLBACK_UNIVERSE` ticker has a known order exchange code.
  - Missing ticker list is reported.

## 5. DB And State Integrity

- `db.event_store_schema`
  - Required tables exist:
    - `lifecycle_events`
    - `v2_decisions`
    - `v2_path_runs`
    - `phase_validation_runs`
- `db.wal_enabled`
  - SQLite WAL mode is enabled.
- `db.path_run_plan_json_valid`
  - Recent `v2_path_runs.plan_json` rows parse cleanly.
- `db.order_unknown_unresolved`
  - Current-session ORDER_UNKNOWN rows are reported.
  - Previous-session unresolved ORDER_UNKNOWN rows are reported.
  - Report includes market, ticker, path_run_id, updated_at.
- `db.pathb_stale_active_runs`
  - Previous-session active Path B rows are reported:
    - `WAITING`
    - `HIT`
    - `ORDER_SENT`
    - `ORDER_ACKED`
    - `PARTIAL_FILLED`
    - `SELL_SENT`
    - `SELL_ACKED`
    - `SELL_PARTIAL_FILLED`
    - `ORDER_UNKNOWN`
- `db.pathb_lifecycle_consistency`
  - Path B `FILLED` run has matching lifecycle `FILLED`.
  - Path B `CLOSED` run has matching lifecycle `CLOSED`.
  - Path B lifecycle events include `path_run_id`.
- `db.market_runtime_isolation`
  - KR live and US live events do not get mixed under the wrong market/runtime.

## 6. Path A Wiring

- `code.path_a_entry_flow_present`
  - Flow exists: Claude trade_ready -> PathExecutionArbiter -> SafetyGate -> TimingAdapter -> OrderExecutor.
- `code.path_arbiter_wired`
  - PathExecutionArbiter is called before Path A order placement.
  - Path B ORDER_UNKNOWN same-ticker conflict can block Path A.
- `code.same_day_reentry_guard_wired`
  - SameDayReentryGuard is called before Path A order placement.
  - KR cooldown defaults to 120 minutes.
  - US cooldown defaults to 90 minutes.
- `code.wait_timing_recorded`
  - WAIT_TIMING and TIMING_EXPIRED recording paths exist.

## 7. Path B Wiring

- `code.pathb_runtime_ready`
  - PathBRuntime is created.
  - Startup recovery is called.
- `code.pathb_plan_registration`
  - Claude `price_targets` are converted into `v2_path_runs`.
- `code.pathb_buy_scan`
  - WAITING plan buy-zone scan exists.
  - `cancel_if_open_above` is handled.
  - limit edge guard is handled.
- `code.pathb_sell_scan`
  - `FILLED` and `PARTIAL_FILLED` are both exit-monitored.
  - hard stop, Claude stop, and Claude target priority is stable.
- `code.pathb_preclose_fallback`
  - intraday-only positions have pre-close exit logic.
  - unfilled pre-close sell can fall back to market handling.
- `code.pathb_kill_switch`
  - `PATHB_EMERGENCY_DISABLE` is wired.
  - `/pathb_kill` is wired.
  - waiting plans are cancelled.
  - open Path B positions are requested to close.

## 8. Fills, Partial Fills, Pending Orders

- `code.buy_partial_fill_exit_protected`
  - Buy partial fills are exit-monitored.
- `code.sell_partial_fallback`
  - Sell partial fills have fallback handling.
- `code.order_acked_stuck_recovery`
  - ORDER_ACKED stuck states are recovered or escalated at startup.
- `code.pending_order_session_close`
  - Pending orders at session close become ORDER_UNKNOWN or another explicit cleanup state.

## 9. Brain And Claude Input

- `state.brain_json_valid`
  - Brain file, if present, parses as JSON.
  - Root structure is an object.
- `claude.price_targets_required`
  - trade_ready tickers require `price_targets`.
- `claude.retry_prompt_price_targets`
  - retry/fallback prompts also require `price_targets`.
- `claude.max_tokens_sufficient`
  - primary and retry max token values are high enough for price targets.
- `claude.no_same_session_watch_chase`
  - same-session watch-only moves must not be used as a chase-buy instruction.

## 10. Dashboard

- `dashboard.pathb_page`
  - `/pathb` loads.
- `dashboard.pathb_api`
  - `/api/v2/ops` returns valid JSON.
- `dashboard.path_comparison`
  - `path_b_live.path_comparison` exists.
  - A/B closed count, average PnL, and realized PnL are available.
- `dashboard.pathb_state_truth`
  - B-plan state is based on `v2_path_runs`.
- `dashboard.order_unknown_visibility`
  - ORDER_UNKNOWN ticker/name/path_run_id are visible.
- `dashboard.candidate_funnel`
  - Claude input candidates, watchlist, raw trade_ready, applied trade_ready, price_targets, and registered plans are visible.

## 11. Telegram

- `telegram.core_commands`
  - `/status`
  - `/health`
  - `/positions`
  - `/errors`
- `telegram.pathb_commands`
  - `/pathb_status`
  - `/pathb_on`
  - `/pathb_off`
  - `/pathb_kill`
  - `/pathb_closeall`
- `telegram.path_label_alerts`
  - Buy-order alert includes A/B path.
  - Buy-fill alert includes A/B path.
  - Sell-fill alert includes A/B path.
  - PnL close alert includes A/B path.
- `telegram.timeout_nonblocking`
  - Telegram timeout does not terminate trading loops.

## 12. US-Specific

- `us.exchange_map_coverage`
  - US fallback universe has exchange mapping.
- `us.order_price_format`
  - `OVRS_ORD_UNPR` uses two decimal places.
- `us.cash_sizing`
  - KRW budget converts to USD cash sizing.
  - zero-quantity orders are blocked.
- `us.market_session_cutoff`
  - US new-entry cutoff exists.
  - US pre-close fallback exists.
- `us.pathb_intraday_only`
  - Path B US positions follow intraday-only policy if enabled.
- `us.order_unknown_scope`
  - US ORDER_UNKNOWN blocks US as configured without incorrectly blocking KR.

## 13. KR Carryover State

- `kr.today_order_unknown_review`
  - Today's KR ORDER_UNKNOWN rows are listed.
- `kr.closed_positions_review`
  - Today's KR closed positions are listed.
- `kr.pathb_no_closed_explained`
  - If B closed count is zero, confirm this is consistent with `v2_path_runs`.
  - Confirm any apparent B fill was not actually Path A.

## 14. Go / No-Go Criteria

Go is allowed only when:

- FAIL count is zero.
- Schema mismatch count is zero.
- Config conflict count is zero.
- US exchange unresolved count is zero.
- Dashboard crash count is zero.
- Telegram command crash count is zero.
- KR/US order APIs are not blind-retied after HTTP 500.
- Broker truth query failure skips retry.
- Unresolved ORDER_UNKNOWN has an explicit block/escalation state.
- US balance failure blocks US live start.
- Path B emergency-disabled state blocks B live start.
- A/B path visibility exists in dashboard and Telegram alerts.

## Execution Notes

- First implement preflight checks that can be run before bot startup.
- Runtime-only items should be reported as runtime-health follow-up, not faked as preflight certainty.
- After implementation and verification, compare the final report against this document and add missing checks or document justified exclusions.
