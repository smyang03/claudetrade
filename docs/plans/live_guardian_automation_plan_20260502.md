# Live Guardian Automation Plan - 2026-05-02

## Goal

Build a conservative live-operation guardian that can run preflight and smoke checks, classify failures by operational risk, apply only safe automatic fixes, and allow live start/restart only after the gate is clean.

The goal is not to keep trading at all costs. The goal is to keep the live system either safely running or safely blocked with clear operator evidence.

## Background

Recent live readiness work exposed several operational issues:

- `broker_truth.atomic_write_marker` was a static-code marker false positive. The implementation already used `os.replace(tmp, self.path)`.
- Windows temporary SQLite files could remain locked because `sqlite3.Connection` context managers commit/rollback but do not close by default.
- `v2_live_smoke.py` originally ran with default config and a hardcoded `2026-04-26` session date instead of the effective live env/session.
- `live_preflight.py` did not expose bot/dashboard PID lock state, even though stale PID cleanup and duplicate-process blocking are required for automated operation.
- Static code marker checks are useful as regression tripwires, but they are not equivalent to broker/account/order-state failures.

## Non-Goals

- Do not auto-delete or rewrite `ORDER_UNKNOWN` rows.
- Do not mark positions, fills, or Path B runs as closed without broker evidence.
- Do not start or restart `trading_bot.py --live` when broker truth is stale or unavailable.
- Do not ignore preflight hard failures.
- Do not increase order size, position limits, or daily entry limits.
- Do not place test orders.
- Do not do market-degraded live start in this phase. If any enabled market has a hard fail, block the whole bot start/restart.
- Do not send Telegram notifications in Phase 1/2. Reports are file/stdout only. Telegram can be added in a later phase.

## Guardian Modes

### Check Mode

Runs checks only and writes a guardian report.

Command:

```powershell
python tools\live_guardian.py --mode live --json
```

### Auto-Fix Mode

Runs checks, applies safe fixes, then reruns checks.

Allowed fixes:

- Remove stale bot/dashboard PID files after process liveness check.
- Refresh token when token is near expiry or expired.
- Use current market-aware smoke `session_date`.

Command:

```powershell
python tools\live_guardian.py --mode live --auto-fix --json
```

### Start Gate Mode

Starts or restarts processes only if the gate is clean.

Initial implementation should expose the gate decision first. Bot start can be enabled only behind an explicit flag.

```powershell
python tools\live_guardian.py --mode live --auto-fix --start-dashboard --start-bot --json
```

### Watch Mode

Repeatedly runs the same gate and reports state. It must not perform unsafe restart loops.

```powershell
python tools\live_guardian.py --mode live --auto-fix --watch --interval-sec 60 --json
```

Watch restart guardrails:

- Minimum bot restart cooldown: `300` seconds.
- Maximum consecutive bot start failures: `3`.
- After the limit is reached, guardian must keep reporting but must stop attempting bot restarts until the operator intervenes or the guardian process is restarted.
- These limits apply only to process start attempts. They do not relax broker truth, token, or ORDER_UNKNOWN hard gates.

## Failure Classification

### Hard Fail

Blocks live start/restart.

- Any non-code-marker preflight `FAIL`
- Broker truth missing, stale, or API-error for enabled markets
- Current-session `ORDER_UNKNOWN`
- Expired token or token refresh failure
- Path B emergency disabled
- DB schema/integrity failure
- Smoke failure
- Any enabled-market hard fail blocks the whole bot start/restart. Market-specific degraded start is out of scope.

### Soft Fail

Requires visibility but should not be treated like account/order-state failure.

- Static code marker `FAIL`
- Static code marker `WARN`
- Dashboard unavailable when `--start-dashboard` is not requested
- Historical previous-session `ORDER_UNKNOWN` warnings
- Previous-session stale active rows, unless current-session exposure is detected

### Auto-Fixable

Can be fixed by the guardian and then rechecked.

- Stale bot PID lock
- Stale dashboard PID lock
- Token near expiry
- Missing dashboard process when `--start-dashboard` is explicitly requested
- Smoke stale session date, now handled by dynamic session-date injection

## Required Preflight Support

Preflight must expose machine-readable data for guardian classification:

- `runtime.bot_pid_lock`
  - `data.category="runtime_pid_lock"`
  - `data.auto_fix=true` for stale PID
  - `data.alive=true` for active PID

- `runtime.dashboard_pid_lock`
  - same structure as bot PID lock

- Static marker checks
  - `data.category="code_marker"`
  - `data.guardian_severity="soft_fail"`

- Broker truth checks
  - `broker_truth.kr_stale_state`
  - `broker_truth.us_stale_state`
  - include `last_success_at`, `last_attempt_at`, `ttl_sec`, `error`

- ORDER_UNKNOWN check
  - `db.order_unknown_unresolved`
  - split `current_session` and `previous_session`

## Required Smoke Support

`v2_live_smoke.py` must support:

- `--env`
- `--session-date`
- dynamic market-aware session date by default
- actual runtime env values via `V2Config.from_env()`
- JSON output containing `session_date`

## Implementation Phases

### Phase 1. Preflight and Smoke Foundation

- Add PID lock checks to preflight.
- Add code-marker classification metadata.
- Fix static marker false positives for token refresh and broker truth atomic write.
- Make smoke load `.env.live`/`.env.paper`.
- Add smoke `--env` and `--session-date`.
- Use dynamic session date by default.

Validation:

- `python -m py_compile tools\live_preflight.py tools\v2_live_smoke.py`
- PID lock unit tests
- smoke CLI for KR/US
- preflight JSON parses with Python

### Phase 2. Guardian Report and Classifier

- Add `tools/live_guardian.py`.
- Import and run `run_preflight()`.
- Import and run `run_live_smoke()` for enabled markets.
- Classify checks into `hard_fail`, `soft_fail`, `auto_fixable`, and `pass`.
- Write JSON/MD guardian reports to `data/v2_reports`.

Validation:

- Unit tests for classifier.
- CLI check mode returns a clear gate decision.

### Phase 3. Safe Auto-Fix

- Remove stale PID locks only when `data.auto_fix=true` and the process is not alive.
- Refresh token only in `--auto-fix` mode.
- Rerun preflight after fixes.
- Record every action and error in the guardian report.

Validation:

- Unit test stale PID cleanup with temporary files.
- Auto-fix dry run and real auto-fix paths.
- No DB state mutation except PID files and token cache.

### Phase 4. Start/Watch Gate

- Implement explicit `--start-dashboard`.
- Implement explicit `--start-bot`.
- Block bot start if any hard fail remains.
- In watch mode, use `300` second minimum bot restart cooldown.
- Stop bot restart attempts after `3` consecutive bot start failures.
- Keep reporting after restart attempts are suppressed.
- Keep Telegram notification out of scope for this phase; use JSON/MD reports and stdout.

Validation:

- Start command construction tests.
- Watch one-iteration smoke test.
- Watch cooldown and max-start-failure behavior is visible in report actions.
- Manual operator test with `--no-start-bot` or default check mode.

### Phase 5. Full QA and MD Comparison

Run focused and full QA:

- `python -m py_compile ...`
- focused pytest for guardian/preflight/smoke/pid/event-store
- `python tools\live_preflight.py --mode live --skip-dashboard --json`
- `python tools\v2_live_smoke.py --market ALL --runtime-mode live --env .env.live --json`
- `python tools\live_guardian.py --mode live --json`

Then compare implementation against this MD:

- Every Required Preflight Support item present
- Every Required Smoke Support item present
- Each failure class implemented
- Each auto-fix has a test or explicit non-implemented note
- Non-goals are not violated
- Reports include enough evidence for operator decisions

## Acceptance Criteria

- Guardian can produce a single JSON/MD decision report.
- Guardian separates hard failures from code-marker false positives.
- Guardian can identify stale PID files as auto-fixable.
- Guardian uses current live env and current session date for smoke.
- Guardian blocks live start when broker truth is stale/unavailable.
- Guardian blocks the whole bot start when any enabled market has a hard fail.
- Watch mode has a minimum `300` second restart cooldown and a `3` consecutive start failure cap.
- Phase 1/2 reporting is file/stdout only, not Telegram.
- All implemented safe fixes are visible in the report.
- Focused QA passes.
- Full pytest QA passes.

## Progress Log

- Completed Phase 1:
  - Added preflight PID lock checks for bot/dashboard.
  - Added code-marker guardian metadata.
  - Fixed token-refresh and broker-truth atomic-write marker false positives.
  - Fixed Windows SQLite temp cleanup by closing `EventStore` context-manager connections.
  - Updated smoke to load runtime env, accept `--env`, accept `--session-date`, and default to market-aware session date.

- Completed Phase 2:
  - Added `tools/live_guardian.py`.
  - Guardian runs preflight and KR/US smoke, classifies findings, and writes JSON/MD reports.
  - Code-marker failures are soft findings, not broker/order hard failures.

- Completed Phase 3:
  - Stale PID cleanup is guarded by process liveness checks.
  - Token refresh is only attempted with `--auto-fix`.
  - Dashboard start is only attempted with explicit `--start-dashboard`.
  - Actions are recorded in guardian reports.

- Completed Phase 4:
  - Bot start is behind explicit `--start-bot`.
  - Any hard fail blocks bot start/restart.
  - Watch mode has `300` second default restart cooldown and `3` consecutive start-failure cap.
  - Market-degraded start remains out of scope.
  - Telegram remains out of scope.

- Completed Phase 5:
  - `python -m py_compile tools\live_guardian.py tools\v2_live_smoke.py tools\live_preflight.py` passed.
  - Focused guardian/preflight/smoke/pid/event-store tests passed: `22 passed, 2 warnings`.
  - `python tools\v2_live_smoke.py --market ALL --runtime-mode live --session-date 2026-05-02 --env .env.live --json` passed for KR and US.
  - Guardian check generated JSON/MD reports and correctly returned `BLOCK_START` when broker truth was stale/unavailable in this environment.
  - Direct fail-case run found `state/live_kis_token.json` missing and KIS TCP access blocked with `WinError 10013` on ports `9443` and `29443`.
  - Token refresh was hardened so `force_refresh=True` does not delete the existing token file before a replacement token is successfully issued.
  - Guardian auto-fix now attempts token refresh for missing-token hard failures when `--auto-fix` is used, while still blocking bot start if refresh fails.
  - Added preflight `network.kis_rest_python_socket.*` checks so Python-process outbound blocks are visible with `sys.executable`, host/port, `Test-NetConnection`, and `New-NetFirewallRule` hints.
  - Telegram command marker failures are soft guardian findings; account/order-state failures remain hard.
  - Full QA passed: `496 passed, 2 skipped, 2 warnings`.

## MD Comparison Result

- Required Preflight Support: complete.
- Required Smoke Support: complete.
- Failure classification: complete for hard, soft, auto-fixable, and pass.
- Safe auto-fix coverage: complete for stale PID, token refresh, and explicit dashboard start.
- Non-goals: preserved. No ORDER_UNKNOWN mutation, no broker-state rewriting, no test orders, no market-degraded start, no Telegram notification path.
- Remaining operational note: actual `trading_bot.py --live` start was not executed during QA because it can place real orders. Use guardian check/auto-fix reports first, then pass `--start-bot` only after the gate is clean.
