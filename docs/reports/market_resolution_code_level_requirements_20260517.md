# Market Resolution Code-Level Requirements - 2026-05-17

## Goal

Remove duplicated KR/US market inference logic from operational paths and make live state, safety gates, maintenance checks, dashboards, and Telegram views resolve markets consistently.

## Root Problems

1. Multiple files infer market from ticker strings with different rules.
2. Several safety paths ignore an existing `market` field on position dictionaries.
3. Live maintenance process detection misses default-live processes when `--mode` is omitted.

## Common Resolver Contract

Create `runtime/market_resolver.py` with low-level helpers and no app-level dependencies.

Required functions:

- `normalize_market(value) -> str`
  - Return `"KR"` or `"US"` for valid values.
  - Return `""` otherwise.

- `infer_ticker_market(ticker, *, unknown="KR") -> str`
  - Empty ticker returns `unknown`.
  - Numeric ticker returns `"KR"`.
  - US-style ticker returns `"US"` when it starts with a letter and contains only letters, digits, `.`, or `-`.
  - Otherwise return `unknown`.

- `resolve_position_market(pos, *, unknown="KR") -> str`
  - Use `pos["market"]` first when it is `KR` or `US`.
  - Then use `display_currency` or `currency`: `USD -> US`, `KRW -> KR`.
  - Then fall back to `infer_ticker_market(pos["ticker"], unknown=unknown)`.

Operational safety paths may pass `unknown=""` when ambiguity should not be silently treated as KR.

## Required Code Changes

### Core state and execution

- `bot/state.py`
  - Replace `_saved_position_market()` market fallback with `resolve_position_market(..., unknown="KR")`.

- `trading_bot.py`
  - Replace `_ticker_market()` with `infer_ticker_market(..., unknown="KR")`.
  - Replace `_market_position_count()` inline ticker inference with `resolve_position_market(pos, unknown="")`.

- `execution/safety_gate.py`
  - Replace `_infer_market(ticker)` with position-aware resolution.
  - `_market_position_count()` must inspect `pos["market"]` before ticker fallback.
  - `_has_position()` must inspect `pos["market"]` before ticker fallback.

- `risk_manager.py`
  - `can_open()` must inspect `position["market"]` before ticker fallback for market counts and same-ticker checks.

- `runtime/pathb_runtime.py`
  - Fallback `_ticker_market()` must use `infer_ticker_market(..., unknown="KR")`.

### Operational checks and UI

- `tools/live_preflight.py`
  - `_position_market_from_ticker()` must use the common ticker resolver.

- `dashboard/dashboard_server.py`
  - `_ticker_market()` must use the common ticker resolver.
  - Any inline fallback in bucket enrichment should call `_ticker_market()`.

- `interface/v2_ops_summary.py`
  - Position market fallback must use `resolve_position_market()`.

- `telegram_commander.py`
  - Position display, review, and local match market fallback must use the common resolver.

- `telegram_reporter.py`
  - Position display market fallback must use the common resolver.

### Live maintenance process discovery

- `tools/live_maintenance.py`
  - `live_guardian.py --watch` is live by default unless `--mode paper` or `--mode=paper` is present.
  - `preopen_scheduler.py --loop` is live by default unless `--mode paper` or `--mode=paper` is present.
  - Continue detecting explicit `--mode live` and `--mode=live`.

## Out of Scope

- `kis_api.py` and `phase1_trainer/price_collector.py` candidate filters remain a separate P3 follow-up because changing them affects universe selection and historical data collection policy.

## Tests

Required regression coverage:

- `BRK.B`, `BRK-B`, `AAPL`, `005930` market resolver cases.
- `market="US"` on a position overrides ticker fallback.
- `display_currency="USD"` overrides ticker fallback.
- `SafetyGate` blocks same-ticker `BRK-B` holding as `ALREADY_HOLDING`.
- `RiskManager.can_open()` blocks same-ticker `BRK-B` holding as `already_holding`.
- `TradingBot._market_position_count("US")` counts `BRK-B` with `market="US"`.
- `live_guardian.py --watch` without `--mode` is discovered as guardian live.
- `preopen_scheduler.py --loop` without `--mode` is discovered as preopen scheduler live.
- `--mode paper` and `--mode=paper` are not treated as live writer processes.

## Acceptance Criteria

- No operational market classification path listed above uses direct `isalpha()` ticker inference.
- Existing relevant tests pass.
- New regression tests fail on the old logic and pass with the new resolver.
