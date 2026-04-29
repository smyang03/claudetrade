# Live Preflight Checklist

Scope: production live operation for Path A and Path B. Profitability is not guaranteed or optimized here. This checklist verifies that features run, money is protected, data is stored in the right place, and operators can see/control/recover the system.

Date basis: 2026-04-28 live preparation.

## 0. Goal

- Feature correctness: Path A and Path B execute the intended lifecycle.
- Capital protection: invalid, duplicated, stale, or unknown orders are blocked.
- DB integrity: schemas, paths, writes, reads, and quality markings are correct.
- Operational clarity: final settings and their source are visible.
- Measurement readiness: performance can be tuned later from clean data.

## 1. Config And Operation Source

- Official live configuration source is explicitly known.
- `.env.live` is loaded for `python trading_bot.py --live`.
- `config/v2_start_config.json` override behavior is visible.
- Any duplicate key between `.env.live` and `v2_start_config.json` is reported.
- Config conflicts are a live preflight FAIL unless explicitly allowed.
- Dashboard displays final effective values, not only raw env values.
- Bot, dashboard, tools, and tests agree on effective values.
- `PATHB_MAX_POSITIONS`, `PATHB_MAX_DAILY_ENTRIES`, `PATHB_INTRADAY_ONLY`, fixed order sizes, and market limits are checked.
- Operator knows whether changing config requires bot restart, dashboard restart, or both.
- Hidden code defaults do not silently override live config.

## 2. DB Storage Paths

- Live event store path is printed and exists or can be created.
- Paper event store path is separate from live.
- Dashboard reads the same live event store as the bot.
- Daily loop reads the same live event store as the bot.
- Tests use temporary DBs and never write live truth.
- Brain store path is printed.
- Daily review path is printed.
- State files are printed: open positions, pending orders, order unknown, Path B control, token.
- Logs paths are printed: lifecycle, orders, daily review, system, error.

## 3. DB Schema Integrity

- `v2_decisions` exists with required columns.
- `lifecycle_events` exists with required columns.
- `v2_path_runs` exists with required columns.
- `phase_validation_runs` exists with required columns.
- Current code writes only columns that exist.
- Existing DBs are migrated or preflight fails.
- JSON columns can be decoded or safely marked invalid.
- WAL mode is enabled.
- DB writes are transactional.
- Schema mismatch is a live preflight FAIL.

Required `v2_path_runs` columns:

```text
path_run_id
decision_id
path_type
market
runtime_mode
session_date
ticker
status
plan_json
created_at
updated_at
```

Required `lifecycle_events` columns:

```text
event_id
event_type
market
runtime_mode
session_date
ticker
decision_id
execution_id
position_id
reason_code
prompt_version
brain_snapshot_id
payload_json
occurred_at
```

## 4. DB Read/Write Round Trip

- `CLAUDE_TRADE_READY` decision can be created and read after reopening DB.
- Lifecycle events can be appended and read after reopening DB.
- Path runs can be created, updated, and read after reopening DB.
- `plan_json` merge preserves existing fields.
- Dashboard `/api/v2/ops` sees newly written Path B runs.
- Daily review sees newly written events.
- Data written as paper never appears in live summary.
- Data written as live never appears in paper summary.
- Concurrent dashboard reads do not break bot writes.

## 5. DB Contamination Prevention

- Paper data is not fed into live brain by default.
- Research/backtest artifacts do not write live truth.
- Archive imports are blocked from live runtime.
- `CLOSED_BROKER_SYNC` is excluded from actual trade performance.
- Unresolved `ORDER_UNKNOWN` is excluded from learning.
- `DIRTY`, `SUSPECT`, and `FORWARD_PENDING_DATA` do not enter brain candidates.
- `CLEAN + live + forward complete` is the default learning input.

## 6. Order Safety

- `qty=0` never reaches broker order placement.
- Minimum order failures are blocked before order placement.
- Insufficient cash is blocked before order placement.
- Existing position blocks duplicate ticker entry.
- Pending order blocks duplicate ticker entry.
- Path A and Path B same ticker overlap is forbidden in live.
- Market max positions apply by market.
- Path B max positions apply to Path B.
- Global daily entry limit and Path B daily entry limit are both checked.
- Market closed, stale data, broker untrusted, and order unknown block new entries.
- Safety blocks record `SAFETY_BLOCKED` with reason code.

## 7. Order And Fill State

- `ORDER_SENT` is recorded with execution id.
- `ORDER_ACKED` is recorded after broker acceptance.
- `ORDER_SENT`/`ORDER_ACKED` stuck is recovered or escalated.
- Buy `PARTIAL_FILLED` creates/protects filled quantity.
- Buy partial remainder follows TTL cancel/unknown policy.
- `FILLED` creates position metadata and path ids.
- `SELL_SENT` is recorded for sell attempts.
- `SELL_ACKED` or equivalent sell acknowledgement is trackable.
- `SELL_PARTIAL_FILLED` has a real remainder fallback path.
- Sell reject/error does not leave a false closed state.
- `CLOSED` records close reason and PnL fields.
- Broker truth mismatch becomes `ORDER_UNKNOWN` if unresolved.

## 8. Path A Features

- Every trade-ready ticker receives a `decision_id`.
- Path B `price_targets` does not change Path A `trade_ready`.
- Fixed sizing overrides legacy ATR sizing when V2 fixed sizing is enabled.
- Safety passed/blocked events are recorded.
- Timing adapter wait/expired/unsupported events are recorded.
- Live entry does not use same-close/lookahead logic.
- Path A partial fill policy is preserved.
- Path A order unknown escalation works.
- Path B off/kill does not unintentionally disable Path A.

## 9. Path B Features

- Price target plan is parsed and validated.
- Missing or invalid price target blocks only Path B.
- Comma string prices parse correctly.
- Reversed stop/target is rejected.
- Reward/risk and confidence gates are enforced.
- Buy zone wait, hit, cancel, expire paths work.
- `cancel_if_open_above` cancels the plan.
- KR tick rounding and US cent/slippage calculations are correct.
- `limit_price < current_price` blocks order.
- Duplicate Path B plan for same session/ticker is blocked.
- Target, stop, hard stop, pre-close, off, kill, and closeall paths work.

## 10. KIS And Broker

- Live token file is used in live mode.
- Token `expires_at` and `issued_at` are visible in preflight.
- Token expiry triggers `get_access_token(force_refresh=True)`.
- Token refresh failure prevents live ordering.
- KR balance failure prevents live ordering.
- US balance failure handling is explicit.
- FX is refreshed before US sizing; fallback use is logged.
- Precheck failure prevents order placement.
- Order timeout/reject records unknown/reject state.
- Fill lookup failure keeps pending or escalates.
- KIS 429/rate limit is handled or blocks safely.

## 11. KR/US Separation

- KR and US sessions are separate.
- KR and US cash/sizing are calculated correctly.
- KR and US position counts are separated.
- KR and US daily entry counts are separated.
- Ticker normalization is correct: KR numeric, US uppercase.
- ORDER_UNKNOWN escalation scopes are explicit: ticker, market, global.
- KR halt impact on US is explicit.
- US failures do not accidentally block KR unless global safety requires it.

## 12. Session And Scheduler

- 08:00 startup preflight can complete.
- 09:00 KR session open sets session state correctly.
- Opening refresh is limited to configured window.
- Mid-session scout/JUDGE limits are respected.
- Hold review is separate from new candidate JUDGE.
- New entry cutoff blocks late entries.
- Intraday-only Path B closes before close.
- Waiting/Hit Path B plans expire at session close.
- Daily review JSON and markdown are generated.
- Forward measurement pending is scheduled for blocked/expired decisions too.

## 13. Dashboard And Telegram

- `/pathb` returns 200.
- Dashboard labels are readable Korean operation terms.
- Effective config source and conflicts are visible.
- Empty data renders as zero/empty state without JS crash.
- Buy/sell prices and Claude rationale are shown.
- Charts render for status, outcome, and cumulative PnL.
- `ORDER_UNKNOWN` is visible.
- Telegram `/health`, `/status`, `/positions`, `/pathb_status`, `/pathb_on`, `/pathb_off`, `/pathb_kill`, `/pathb_closeall`, `/halt`, `/resume`, `/panic` work.

## 14. Measurement Readiness

- Entry rate is recorded.
- Target hit rate is recorded.
- Stop hit rate is recorded.
- Expired/missed buys are recorded.
- Average PnL is calculated from closed trades.
- Entry delay is measurable.
- Exit efficiency is measurable.
- Slippage is measurable.
- Claude entry/exit rationale is stored.
- Claude cost adjusted PnL is measurable.
- Benchmarks are available for selection alpha.

## 15. Fault Injection

- Token expired.
- Token refresh failed.
- Balance failed.
- FX failed.
- Price failed/stale.
- Claude timeout.
- Claude malformed JSON.
- Precheck failed.
- Order timeout.
- Order reject.
- Fill callback missing.
- Buy partial.
- Sell partial.
- SQLite locked/write failed.
- Dashboard API exception.
- Telegram runtime unavailable.
- Corrupt state JSON.

## 16. Live Preflight Fail Conditions

Live new entries must be blocked if any of the following are true:

- Config conflict exists and is not explicitly allowed.
- Live DB path is unknown or mismatched.
- Required DB table/column is missing.
- DB write/read round trip fails.
- Token is expired and cannot be refreshed.
- KR balance cannot be retrieved in live mode.
- Quantity zero could reach order placement.
- Path A/B duplicate order guard fails.
- `ORDER_ACKED` stuck recovery is unverified.
- Buy partial filled quantity is not protected.
- Sell partial fallback is not implemented/verified and the residual warning is not explicitly accepted.
- `/pathb` or `/api/v2/ops` fails.
- Telegram `/health` fails.

## 17. Required Artifacts

- `tools/live_preflight.py`
- `tests/test_live_config_sources.py`
- `tests/test_live_db_integrity.py`
- `tests/test_live_order_safety.py`
- `tests/test_live_pathb_stuck.py`
- `tests/test_live_token_balance.py`
- `data/v2_reports/live_preflight_YYYYMMDD_HHMMSS.md`
- `data/v2_reports/live_preflight_YYYYMMDD_HHMMSS.json`

## 18. Execution Order

1. Resolve or explicitly allow config conflicts.
2. Verify DB paths and schemas.
3. Verify DB write/read round trip.
4. Verify token, balance, and FX handling.
5. Verify order safety.
6. Verify stuck/partial recovery.
7. Verify Path A.
8. Verify Path B.
9. Verify KR/US separation.
10. Verify dashboard and Telegram.
11. Run all unit tests.
12. Run phase gate.
13. Run live smoke.
14. Run daily loop dry run.
15. Generate live preflight report.
16. Re-read this checklist and check for omitted categories.
17. No code changes after final preflight unless a P0 blocker is fixed and full QA reruns.

## 19. Final Re-read Notes

Re-read performed after implementation and QA on 2026-04-27 01:01 KST.

Added after re-read:

- Dedicated live QA test files were added for config source, DB integrity, order safety, Path B stuck/partial states, and token refresh wiring.
- `tools/live_preflight.py` now verifies Telegram core commands as well as dashboard routes.
- Session-open price cache clearing was added so Path B does not act on stale prior-session prices.
- Path B buy `PARTIAL_FILLED` is now monitored by exit rules and pre-close exit checks.
- Path B cold-start brain snapshot fallback was added so lifecycle writes do not crash on an empty snapshot id.
- `.env.live` and `config/v2_start_config.json` were aligned to remove live config conflicts.

Final QA artifacts:

- Latest live preflight: `data/v2_reports/live_preflight_20260427_013806.md`
- Latest daily loop dry run: `data/v2_reports/v2_daily_loop_20260427_013805.md`
- Unit tests: 77 passed.
- Phase gate: Phase 1 through 6 PASS, QA PASS.
- KR/US live smoke: PASS.

Final sell partial hardening:

- Path B sell now force-refreshes broker balance after a sell attempt.
- Path B records `CLOSED` only when broker remaining quantity is zero.
- If broker truth is unavailable, Path B records `ORDER_UNKNOWN`.
- If broker still shows remaining quantity, Path B records `SELL_PARTIAL_FILLED`, restores the remaining local position, then records `ORDER_UNKNOWN` to block unsafe new entries.
- Final live preflight is `fail=0`, `warn=0`.
