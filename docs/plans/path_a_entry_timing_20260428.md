# Path A Entry Timing Improvement Plan - 2026-04-28

## Goal

Path A 진입이 늦어지는 문제를 줄인다. 이번 라운드는 구조를 크게 흔들지 않고 운영 리스크가 낮은 개선만 적용한다.

## Current Scan Structure

Before this change:

- KR opening window: first 30 minutes, scan every 2 minutes.
- KR regular window: scan every 10 minutes.
- US opening window: first 30 minutes, scan every 2 minutes.
- US regular window: scan every 5 minutes.
- Candidate rescreen: every 60 minutes.
- Every entry scan still runs broker sync before signal checks.
- Path A signal checks use REST `get_price()` data, not WebSocket-only data.

After this change:

- KR opening window: unchanged, first 30 minutes every 2 minutes.
- KR regular window: every 5 minutes.
- US opening window: unchanged, first 30 minutes every 2 minutes.
- US regular window: unchanged, every 5 minutes.
- Candidate rescreen: unchanged at 60 minutes in this phase.
- Broker sync remains in the scan cycle.
- WebSocket price-cache replacement is not enabled in this phase.

## Non-Goals

- Do not replace Path A REST quote data with WebSocket tick data yet.
- Do not split broker sync out of `run_cycle()`.
- Do not add tick-triggered Path A immediate entry yet.
- Do not reduce rescreen interval to 45 minutes yet.
- Do not change Path A strategy math or Safety Gate logic.

## Phase 1 - Scan Interval

### Changes

- Set `ENTRY_SCAN_REGULAR_INTERVAL_MIN=5` for KR regular scan.
- Keep `US_ENTRY_SCAN_REGULAR_INTERVAL_MIN=5`.
- Keep opening scan settings at `ENTRY_SCAN_OPENING_MIN=30`, `ENTRY_SCAN_OPENING_INTERVAL_MIN=2`.

### Verification

- Confirm `.env.live` has the intended scan settings.
- Confirm `trading_bot.py` reads the KR regular interval from env/default as 5.
- Confirm US regular interval remains 5.

## Phase 2 - Path A Entry Timing Metrics

### Data To Capture

For Path A selected tickers:

- `candidate_detected_at`: first time the ticker entered `today_tickers` for that market/session.
- `candidate_source`: `session_open`, `session_reuse_rescreen`, `manual_rescreen`, `inline_replacement`, or other explicit source.
- `candidate_detected_price`: first known native price at candidate detection time when available.
- `first_signal_checked_at`: first valid Path A signal check time.
- `last_signal_checked_at`: latest valid Path A signal check time.
- `signal_check_count`: count of valid Path A signal checks.
- `signal_fired_at`: first time a Path A strategy signal fired.
- `signal_fired_price`: native price when signal fired.
- `signal_strategy`: strategy that fired.
- `signal_reason`: compact reason/detail available at signal time.
- `order_sent_at`: successful Path A buy order send time.
- `order_sent_price`: native price used for order decision.
- `filled_at`: confirmed fill time.
- `filled_price`: native fill price.

Derived metrics:

- `candidate_to_signal_delay_min`
- `signal_to_order_delay_min`
- `candidate_to_order_delay_min`
- `order_to_fill_delay_sec`
- `price_change_candidate_to_order_pct`
- `price_change_signal_to_order_pct`
- `entry_vs_intraday_high_pct`

### Storage

- Write JSONL events to `logs/entry_timing/{runtime_mode}_YYYYMMDD_{market}.jsonl`.
- Keep events small and append-only.
- Log first signal check once; keep later check count in memory and write it with signal/order/fill events.
- Do not write every no-signal scan row.

### Verification

- Unit test timing calculations.
- Unit test JSONL append format.
- Compile `trading_bot.py`.
- Run focused tests for the new timing tracker.

## Phase 3 - Minimal Dashboard/API Visibility

### Changes

- Add `entry_timing` summary to `/api/v2/ops`.
- Add a compact Path A timing section to the Path B operations page because that page is currently the live operations view.

Summary fields:

- event counts
- average `candidate_to_order_delay_min`
- average `signal_to_order_delay_min`
- average `order_to_fill_delay_sec`
- recent signal/order/fill rows

### Verification

- Build summary with missing log file: should return `missing=true`, no crash.
- Build summary with sample JSONL: averages and recent rows should be populated.
- Dashboard JS should render empty state without error.

## Phase 4 - QA

Run:

- focused unit tests for entry timing
- dashboard/pathb tests if available
- Python compile for changed Python files
- live preflight if it can run without broker/network dependency in current environment

## Phase 5 - Plan Comparison

After implementation:

- Re-open this MD.
- Check every phase item against actual changes.
- Add a short completion note if anything was intentionally deferred.

## Risk Notes

- KR 5-minute scan doubles regular KR scan attempts, but broker sync remains intact and this is lower risk than changing quote source.
- WebSocket tick data currently lacks open/high/low/accumulated volume context needed by Path A strategy calculations; it must not replace REST quote data in this phase.
- This change improves maximum polling delay, not candidate discovery delay. Rescreen interval remains a separate decision after timing data is collected.

## Completion Notes

- Phase 1 completed: `.env.live` and `trading_bot.py` now use 5 minutes for KR regular Path A entry scans. US remains 5 minutes.
- Phase 2 completed: `bot/entry_timing.py` records candidate, first signal check, signal fired, order sent, and fill events to JSONL.
- Phase 3 completed: `/api/v2/ops` exposes `entry_timing`, and `/pathb` renders a compact A-plan timing section.
- Phase 4 completed:
  - `python -m py_compile bot\entry_timing.py interface\v2_ops_summary.py dashboard\dashboard_server.py trading_bot.py`
  - `python -m pytest tests/test_entry_timing.py`
  - `python -m pytest tests/test_dashboard_pathb.py tests/test_bucket_summary.py`
  - `python -m pytest tests/test_patha_contract.py`
  - `python -m pytest tests`
  - `python tools\live_preflight.py --mode live --skip-dashboard --json`
- QA result: 138 tests passed, py_compile passed, live preflight returned `ok=true` with 0 failures.
- Remaining operational warnings are pre-existing state items: unresolved ORDER_UNKNOWN rows, stale broker truth snapshot due local network access, token near-expiry warning, and calendar/manual verification warnings.
