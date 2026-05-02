# Preopen Scheduler Automation Plan - 2026-05-03

## Decision

Preopen collection must be automated before live operation.

The current implementation has:

- `tools/preopen_collector.py`
- `tools/preopen_outcome_updater.py`
- dashboard command guidance

It does not have an execution scheduler. That means the operator must run commands manually, which is not acceptable for a 24-hour bot workflow.

## Architecture

Use an independent sidecar scheduler:

- `preopen/scheduler.py`
- `tools/preopen_scheduler.py`

Do not embed preopen scheduling into `trading_bot.py` in this phase.

Reason:

- Preopen is still shadow-only research data.
- A collector failure must not affect order loops.
- Scheduler state can be monitored independently.
- This keeps the safety boundary clear: no order path, no candidate reordering, no fast lane.

## Market Schedule

### Common

- Use `bot.session_date.resolve_session_date()` for session date.
- Skip non-trading days using exchange calendars when available.
- Use idempotent job ids so restarting the scheduler does not duplicate records.
- Write scheduler state and event logs.
- Show scheduler state on the dashboard.

### US

- Collector window: `17:00-22:25 KST`
- Collector cadence: every 30 minutes by default
- Outcome updates: regular open + `5m`, `30m`, `60m`
- Current default regular open anchor: `22:30 KST`
- If the scheduler restarts after KST midnight but before US close, the outcome catch-up must keep the prior US session date.
- This is shadow-only. No premarket buy and no fast lane.

### KR

- Collector window: `08:00-09:00 KST`
- Collector cadence: every 15 minutes by default
- Outcome updates: `09:05`, `09:30`, `10:00 KST`
- Indicative/preopen data is collector responsibility.
- Actual post-open behavior is outcome updater responsibility.

## Development Items

| Step | Item | Detail |
| --- | --- | --- |
| 1 | `preopen/scheduler.py` | Pure schedule planner, job ids, trading-day skip, KST windows. |
| 2 | `tools/preopen_scheduler.py` | Sidecar CLI with `--loop`, `--once`, `--dry-run`, `--force`, subprocess timeout. |
| 3 | `preopen/storage.py` scheduler helpers | Persist scheduler state and recent events. |
| 4 | Dashboard payload | Include scheduler status in `/api/preopen`. |
| 5 | Dashboard UI | Show automatic scheduler status, last tick, last job, and next command. |
| 6 | Tests | Planner, idempotency, dry-run state, dashboard payload. |

## Safety Requirements

- Scheduler must call only:
  - `tools/preopen_collector.py`
  - `tools/preopen_outcome_updater.py`
- Scheduler must not import or instantiate `TradingBot`.
- Scheduler must not call `place_order`.
- Collector remains read-only for KIS token.
- Failed jobs must be logged but must not crash the trading bot.
- `--dry-run` must show due jobs without executing subprocesses.

## QA

- `python -m pytest tests/test_preopen_scheduler.py tests/test_preopen_shadow.py -q`
- `python -m py_compile preopen/scheduler.py preopen/storage.py tools/preopen_scheduler.py dashboard/dashboard_server.py`
- `python tools/preopen_scheduler.py --mode live --markets US,KR --once --dry-run`
- `git diff --check`

## Completion Check

- Dashboard no longer only tells the operator to run manual commands.
- Dashboard shows whether the scheduler is missing, active, or stale.
- Scheduler can run as a separate terminal/process until Windows Task Scheduler or guardian integration is added.
- No order path is modified.
