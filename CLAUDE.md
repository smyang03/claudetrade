# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 코드 작업 원칙

코드 수정·작성·파일 편집은 운영자가 명시적으로 "수정해", "구현해", "작성해", "코드 작성" 등의 지시를 한 경우에만 진행한다. "검토해줘", "분석해줘", "확인해줘", "어떻게 생각해" 등은 분석과 의견 제시만 하고 코드를 건드리지 않는다.

## Encoding Safety Rules

- Keep all source, docs, JSON, and scripts as UTF-8.
- Do not rewrite large files wholesale when a focused patch is enough.
- Do not use shell redirection, `Out-File`, or `Set-Content` to rewrite source files unless UTF-8 is explicitly controlled.
- Before committing Korean text, run `python tools/check_mojibake.py --staged`.
- If mojibake appears in existing lines, fix it from git history instead of guessing the intended Korean text.
- `state/brain.json` is tracked by git but must not be included in code/screener commits. A pre-commit hook (`tools/check_brain_commit.py`) blocks it. To commit a deliberate brain update: `ALLOW_BRAIN_COMMIT=1 git commit ...`.

## Repository Development Rules

This repository is a Python-based KR/US automated trading system. `trading_bot.py` is the main loop, while `kis_api.py`, `risk_manager.py`, and `ticker_selection_db.py` support broker integration, risk management, and ticker selection.

### Project Layout

- Core runtime and domain code lives in `runtime/`, `execution/`, `strategy/`, `bot/`, `minority_report/`, `audit/`, `lifecycle/`, `ml/`, `preopen/`, and `learning/`.
- Operational tools live in `tools/`, the Flask dashboard lives in `dashboard/`, and docs/reports live in `docs/`.
- Tests primarily live in `tests/`; legacy tests may exist at repo root as `test_*.py` or under `test/audit_lab/`.
- `data/`, `state/`, and `logs/` are runtime output locations. Do not commit generated DBs, PID files, caches, local reports, or policy-memory artifacts unless a human explicitly asks for that exact artifact.

### Coding and Testing Standards

- Use 4-space indentation for Python. Functions and variables use `snake_case`, classes use `PascalCase`, and module names should describe their behavior.
- Prefer existing local patterns over new abstractions, especially for runtime config, broker truth, audit stores, and safety gates.
- Keep comments short and use them only when trading safety or recovery logic is not obvious from the code.
- Add or update tests close to the behavior being changed. New test files should use `tests/test_<feature>.py`; test functions should use `test_<expected_behavior>`.
- When changing `trading_bot.py`, order execution, live config, audit stores, DB schemas, orchestrators, or dashboard behavior, run the focused tests first and then broaden to `py_compile` and wider pytest coverage.

### PathB Auto-Sell Review Cooldown Guard

- Do not remove or loosen the PathB `AUTO_SELL_REVIEW` HOLD cooldown guard. It prevents repeated Claude calls when `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true` causes PathB `loss_cap`, `hard_stop`, or `profit_ladder` exits to pass through hold advisor review.
- The protected flow is `runtime/pathb_runtime.py` `_pathb_auto_sell_review_cooldown_payload()` and `_run_pathb_sell_review_gate()`, with coverage in `tests/test_auto_sell_claude_gate.py::test_pathb_loss_cap_hold_respects_reask_cooldown`.
- If this guard or related knobs (`CLAUDE_REVIEW_ALL_AUTOMATED_SELLS`, `AUTO_SELL_REVIEW_HOLD_COOLDOWN_MINUTES`, `PATHB_AUTO_SELL_REVIEW_HOLD_REASK_DROP_PCT`) must change, state the reason, expected Claude call/token impact, replacement duplicate-call protection, and tests run in the work note, commit message, or PR body.
- `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true`는 Path A 자동 매도(loss_cap·stop_loss·trail_stop 등)에도 Claude hold advisor 리뷰를 요구한다. 이 값을 false로 변경하면 Path A 포지션이 Claude 판단 없이 즉시 청산된다. 코드 리뷰·config 정리·자동 수정 등 어떤 경로로도 임의로 끄지 않는다.

### Revenue Structure — Do Not Break

아래는 실제 수익을 만드는 경로다. 실행 안전성 보호 영역과 별개로, 이 경로를 변경하면 즉시 수익 감소로 이어진다.

**US PathB claude_price** — 누적 수익의 핵심 엔진 (live 기준 누적 +71%+, avg +1.4%)

- `runtime/pathb_runtime.py::_pathb_profit_ladder_floor()` / `_pathb_profit_ladder_signal()`: CLOSED_PROFIT_LADDER 경로. tier 파라미터(`PATHB_LADDER_TIER*_PEAK_GIVEBACK_PCT`)와 floor 계산 로직은 운영자 확인 전 변경 금지.
- `CLOSED_CLAUDE_PRICE_PRE_CLOSE` 청산 경로: 건당 평균 +2.65%, 장마감 전 자동 청산 로직. hold advisor 또는 pre-close 타이밍 변경 시 이 경로가 깨질 수 있다.
- PathB → hold advisor 연동 (`AUTO_SELL_REVIEW`, protective hold, target extension): hold advisor 내부 로직(triage, challenge, boundary 검사) 변경 시 US PathB 포지션의 HOLD/SELL 판단에 직접 영향. R-01/R-02 유형 변경은 live US PathB 포지션에 SELL을 강제할 수 있으므로 변경 전 US PathB 성과 데이터를 확인한다.

**US strategy live allowlist** — 잘못된 기본값이 수익 전략을 전면 차단한다

- `trading_bot.py::_live_strategy_allowed()`의 기본값은 False다. 설정이 누락되면 수익 전략이 조용히 차단된다.
- 현재 활성화된 수익 전략:
  - `US_MOMENTUM_LIVE_ENABLED=true` (US PathB momentum: 누적 +7.2%)
  - `US_VOLATILITY_BREAKOUT_LIVE_ENABLED` — 미설정(=false), VB 성과 미확인, 현행 유지
- 이 allowlist를 변경하거나 새 전략을 추가할 때는 반드시 v2_learning_performance 성과 데이터를 확인한다.
- `US_MOMENTUM_LIVE_ENABLED`를 false로 되돌리거나 제거하면 US momentum PathB 후보가 생성되지 않는다.

**KR/US 전략 성과 분리 원칙**

- KR과 US는 같은 전략 이름이라도 성과가 반대인 경우가 있다. KR momentum/gap_pullback은 현재 손실 기록 중이고, US momentum/gap_pullback은 수익 기록 중이다.
- KR 전략 개선 작업이 US 전략 로직(`strategy/momentum.py`, `strategy/gap_pullback.py`)을 함께 바꾸면 안 된다. KR 전용 파라미터와 US 전용 파라미터를 분리해서 처리한다.
- KR PathB 손실 기록 전략(momentum, gap_pullback, opening_range_pullback)을 개선할 때 US PathB의 같은 전략 경로를 건드리는 것을 금지한다.

### Protected Completed Areas

These areas are treated as completed/protected behavior. Do not refactor, rename, reorganize, loosen safety checks, or rewrite tests around them unless the current task directly targets the area or failing tests/logs/operational evidence identify it as the root cause.

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard: `runtime/pathb_runtime.py` `_pathb_auto_sell_review_cooldown_payload()` / `_run_pathb_sell_review_gate()` and `tests/test_auto_sell_claude_gate.py::test_pathb_loss_cap_hold_respects_reask_cooldown`.
- PathB broker-truth entry fail-closed: `PathBRuntime._entry_scan_broker_truth_gate()` must block live entry scan with `BLOCKED_BROKER_TRUTH` when token/provider is unavailable or broker truth is missing/stale/error. Operator visibility may be improved, but fail-closed behavior must not be weakened.
- PathB sizing reason split: `_pathb_qty_with_context()` and `execution/safety_gate.py` must preserve `INVALID_PRICE`, `ORDER_SIZE_TOO_SMALL_GATE`, and `HIGH_PRICE_BUDGET_BLOCK` separation. Do not change fixed sizing, one-share-over-budget, early soft gate, or live submit policy without explicit operator approval.
- Zero-holding stale reconcile: `TradingBot._sell_zero_holding_broker_evidence()` and `PathBRuntime._pathb_zero_holding_broker_evidence()` may remove/close stale local state only with fresh broker truth, zero broker holding, and zero open remaining quantity.
- KIS order normalization: KR/US order normalization must preserve `remaining_qty`; broker truth `open_orders` must continue to mean rows with `remaining_qty > 0`.
- Path A/Path B route merge: both paths must continue to merge through `runtime/action_routing.py::RouteDecision`; do not mix selection quality fixes with execution/risk fixes in one behavioral patch.
- Broker truth priority: broker holdings, open orders, and fills remain first truth. `_sync_runtime_with_broker()` must preserve market-scoped quarantine, stale-position cleanup by holdings/open-orders evidence, and market-scoped HALT/daily_return baselines.
- `state/brain.json`: policy memory only. Do not add automatic long-term memory promotion or direct runtime truth usage before an approval workflow is in place.

### Protected-Area Exception Report

If a protected area must be changed, the work note, commit message, PR body, or final response must include a section titled exactly `MD 위반 사항`. This is the required operator-visible exception report for protected-contract changes.

`MD 위반 사항` means a protected-area exception record, not that the change is automatically unsafe or unsuitable. A change is suitable only when the exception is unavoidable, narrowly scoped, does not weaken the protected contract, and is backed by focused tests plus broader QA.

The `MD 위반 사항` section must include:

- protected area touched
- why the change could not be avoided
- before/after behavior difference
- order, risk, broker-truth, Claude-call, config, and env impact
- replacement safety guard or contamination prevention
- tests run and remaining risk

If the protected change is discovered during implementation, stop broad editing and record `MD 위반 사항` before continuing beyond the minimum fix.

### MD 위반 사항

Recorded date: 2026-05-29
Work item: broker sync metadata integrity / PathB attribution preservation

- Protected area touched: broker truth priority and `TradingBot._sync_runtime_with_broker()` stale-position reconciliation, plus PathB sell/fill broker-evidence matching.
- Why unavoidable: the EL/IREN incidents were caused by the protected broker sync/reconcile path itself. A transient or partial broker snapshot could delete local PathB metadata, and stale sell-fill evidence could be reused against a newer PathB run. Dashboard-only changes would hide the issue without preventing recurrence.
- Before behavior: one broker balance omission could remove a local position, then later broker reappearance could re-inject it as `broker_sync` without the original PathB metadata. Some PathB sell reconcile paths did not consistently require sell-fill evidence to be causal after the entry fill.
- After behavior: a first broker omission keeps the position protected as `broker_missing_unconfirmed`; removal requires repeated independent fresh zero-holding evidence or safe zero-holding proof. Broker re-injection recovers PathB metadata from a single compatible event-store run. PathB sell fills must be causal after the entry fill unless exact execution evidence is still valid.
- Order/risk/broker truth/Claude/config/env impact: no order quantity, order amount, PathB live gate, hard stop, sizing policy, Claude-call volume, `.env*`, `config/v2_start_config.json`, or `state/brain.json` changes. Broker holdings/open orders/fills remain first truth; local/event-store data is used only for strategy metadata attribution.
- Replacement guard: `broker_missing_unconfirmed`, `management_protected`, `manual_reconciliation_required`, two independent zero-holding confirmations, single-match PathB metadata recovery, conflict-to-manual-review behavior, and causal sell-fill filtering.
- Tests run: `python -m pytest tests/test_live_sell_pending_reconcile.py tests/test_pathb_sell_reconcile.py tests/test_broker_sync_metadata_integrity.py tests/test_dashboard_broker_integrity.py -q`; `python -m pytest tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py tests/test_broker_sync_metadata_integrity.py -q`; `python -m pytest tests/test_dashboard_broker_integrity.py tests/test_dashboard_pathb.py tests/test_dashboard_refresh_performance.py -q`; three protected zero-holding/insufficient-holding tests; `python -m py_compile trading_bot.py runtime/pathb_runtime.py dashboard/dashboard_server.py`; `python tools/live_preflight.py --mode live --skip-dashboard --json`.
- Remaining risk: already-existing historical stale active / ORDER_UNKNOWN PathB rows in the live DB remain separate remediation work. This change prevents new broker-sync metadata contamination and stale sell-fill reuse.

### MD 위반 사항

Recorded date: 2026-05-29
Work item: KR/US operation-quality QA follow-up / PathB sizing and partial-sell reconcile

- Protected area touched: PathB sizing reason split in `PathBRuntime._pathb_qty_with_context()` and PathB pending sell / exit `ORDER_UNKNOWN` partial-fill reconcile in `runtime/pathb_runtime.py`.
- Why unavoidable: full QA directly failed protected-area tests for early-gate one-share sizing and partial sell reconciliation. The failures were in the protected PathB paths themselves, so documentation or report-only changes could not make the runtime behavior correct.
- Before behavior: an early soft gate could still revive a one-share floor when the effective budget was too small, causing MRVL-style cases to size `qty=1` instead of remaining blocked as `ORDER_SIZE_TOO_SMALL_GATE`. Exact-order partial sell fills could fall through to ACK/open-order handling instead of staying `SELL_PARTIAL_FILLED` or session-end retryable with remaining quantity.
- After behavior: the early-gate floor is allowed only when the one-share shortfall is within the minimum-order tolerance and still within the original budget; large shortfall cases remain `qty=0` with `ORDER_SIZE_TOO_SMALL_GATE`. Exact execution partial sell fills are preserved as partial evidence, update local remaining quantity, and remain retryable at session end rather than being treated as fully closed.
- Order/risk/broker truth/Claude/config/env impact: no PathB live gate, order amount, hard stop, loss cap, slippage cap, max positions, daily cap, confidence gate, Claude-call volume, `.env*`, or `config/v2_start_config.json` changes. Broker holdings/open orders/fills remain first truth, and broker-truth fail-closed behavior is not weakened.
- Replacement guard: minimum-order shortfall tolerance for early-gate one-share floor, exact-execution partial-fill evidence, retained `remaining_qty`, session-end retryability, and focused regression tests for both sizing and partial-sell paths.
- Tests run: focused 4-test protected-area regression; `python -m pytest tests/test_live_order_safety.py tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py -q` (`146 passed`); relevant `py_compile`; `python -m pytest -q` (`2020 passed, 2 skipped`); read-only `python tools/live_preflight.py --mode live --skip-dashboard --json`.
- Remaining risk: historical stale active / previous-session `ORDER_UNKNOWN` PathB rows remain operator audited-remediation work. Paper preflight token/config failures remain separate paper-ops work.

### MD 위반 사항

Recorded date: 2026-05-29
Work item: hold-advisor triage implementation re-review / PathB early gate one-share floor recovery

- Protected area touched: PathB sizing reason split in `runtime/pathb_runtime.py::_pathb_qty_with_context()`, specifically the early soft gate one-share floor path. `execution/safety_gate.py`, live submit policy, broker truth, and order routing were not changed.
- Why unavoidable: the re-review found a failing protected sizing test. During the US early soft gate, a one-share order that was affordable under the full fixed budget and only slightly above the reduced early-gate budget could be blocked. The failing test directly identified the protected sizing behavior as the root cause.
- Before behavior: if early gate reduced the effective budget to 225,000 KRW and one share cost 270,000 KRW, `_pathb_qty_with_context()` could return `qty=0` even though the full fixed budget, account cash, and minimum-order shortfall tolerance allowed one share.
- After behavior: when `can_buy_1_share` is true and either the reduced budget covers the share or `early_gate_shortfall <= min_order`, the early gate floor restores `qty=1` and keeps `sizing_reason="early_gate_floor_one_share"`.
- Order/risk/broker truth/Claude/config/env impact: order quantity can change only for the protected early-gate floor tolerance case from `0` to `1`. No order submission policy, broker-truth logic, risk hard stop, PathB live gate, Claude call volume, `.env*`, `config/v2_start_config.json`, or `state/brain.json` changes.
- Replacement guard or contamination prevention: no new broad path was added. The safety boundary remains `can_buy_1_share` plus `price <= budget` or `early_gate_shortfall <= min_order`; `INVALID_PRICE`, `ORDER_SIZE_TOO_SMALL_GATE`, `HIGH_PRICE_BUDGET_BLOCK`, one-share-over-budget, and early-gate sizing reason separation are preserved.
- Tests run: `python -m pytest tests/test_pathb_runtime.py::EarlyGateFloorOneShareTests::test_early_gate_floor_gives_qty_one_when_reduced_budget_is_too_small -q`; `python -m pytest tests/test_pathb_runtime.py::EarlyGateFloorOneShareTests -q`; `python -m py_compile runtime/pathb_runtime.py minority_report/hold_advisor.py`; `python -m pytest tests/test_trading_decision_contract_improvements.py tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q`; `python -m pytest tests/test_auto_sell_claude_gate.py tests/test_pathb_profit_protection.py tests/test_claude_quality_contracts.py tests/test_plan_a_hold_policy.py tests/test_price_unit_normalization.py -q`.
- Remaining risk: `tests/test_pathb_runtime.py` full-file run still has separate failures in `test_previous_session_local_pathb_holding_is_included_in_exit_scan` and `test_cached_carry_does_not_block_hard_target_exit`. Those failures are tied to PathB exit-scan price truth behavior and were not changed in this hold-advisor/sizing exception.

### Commit, PR, and Security Standards

- Commit units should be one behavior change at a time. Recent history uses Conventional Commit prefixes such as `feat:` and `fix:` with short Korean or English summaries.
- PR notes should include change summary, risk areas, test commands run, config/env impact, and dashboard or Telegram screenshots when UI output changes.
- Never commit real `.env`, `.env.live`, `.env.paper`, token files, broker credentials, or local `*API*.txt` notes. Document configuration examples in `.env.example`.

## PEAD Input Policy (2026-04-24)

- PEAD is an input-quality feature, not a standalone strategy.
- Do not let PEAD override entry timing, stop-loss, trailing stop, or session-close logic.
- Keep PEAD event data out of `brain.json`. Store it in digest/candidate metadata and shadow logs only.

### Source Rules

- US:
  - `earnings_date`: yfinance calendar
  - `surprise_sign` / `surprise_strength`: yfinance `earnings_dates` using `Reported EPS` and `EPS Estimate`
- KR:
  - use `earnings_date` / `earnings_window` first
  - do not infer EPS beat/miss from Naver news or DART headlines
  - if structured KR actual/estimate is unavailable, keep `surprise_sign=unknown`

### Trust Tiers

- `high`: actual EPS and estimate both available
- `medium`: earnings date available, surprise unavailable
- `low`: news/disclosure only

Only `high` may produce `surprise_sign` / `surprise_strength`.
`medium` may only produce `earnings_window`.
`low` must not affect PEAD bias.

### Rollout Rules

- `earnings_date` / `earnings_window`: prompt-visible immediately
- `surprise_sign` / `surprise_strength`: 5 trading days shadow-only first
- During shadow:
  - compute and store values
  - write logs for manual inspection
  - do not expose surprise fields to Claude prompts
- Prompt exposure must be blocked by `state/pead_shadow_state.json`, not by memory or comments only.
- `prompt_surprise_enabled=true` is not sufficient by itself. The manual review checklist below must also pass.

### PEAD manual review gate

Before enabling surprise fields in analyst/selection prompts, all checks must be true in `state/pead_shadow_state.json`.

- `trading_days_observed >= 5`
- `manual_review.tier_null_rate_checked=true`: inspect 5 trading days of shadow logs and tier-level null rates.
- `manual_review.surprise_sample_10_checked=true`: manually verify at least 10 `surprise_sign` cases against source EPS values.
- `manual_review.prompt_leak_zero_checked=true`: confirm zero prompt leaks of surprise text while `prompt_applied=false`.
- `prompt_surprise_enabled=true`: explicit final operator switch after the checklist is complete.
- `manual_review_passed` is derived from checklist values. Do not treat a hand-edited boolean as sufficient.

### PEAD gate completion boundary

The first implementation step is complete only when:

- `state/pead_shadow_state.json` is generated from shadow logs.
- 5 trading day gating is enforced in code.
- surprise prompt leakage is covered by tests.
- `prompt_applied` remains false until the state gate and checklist pass.

Do not add PEAD weighting, prompt tuning, or source expansion in the same step.

### Prompt Scope

- Allowed:
  - watchlist prioritization
  - trade_ready conviction bias
  - strategy-fit bias
- Not allowed:
  - automatic trade_ready promotion from PEAD alone
  - entry rule override
  - exit rule override

### Logging

- Keep US shadow records under runtime logs for 5 trading days.
- Minimum fields:
  - ticker
  - earnings_date
  - earnings_window
  - reported_eps
  - eps_estimate
  - surprise_sign
  - surprise_strength
  - confidence_tier
  - prompt_applied

### MICRO_PROBE promotion policy

`MICRO_PROBE` is a separate experiment path for order-size-too-small signals, not a regular strategy.

- Keep `MICRO_PROBE` performance separate from normal trades.
- Do not promote to regular sizing before at least `30` filled probe trades or `4` calendar weeks of observation.
- Promotion review must use net performance after fees, max loss, loss streak, and separate probe reports.
- Probe records must keep original order cost, adjusted order cost, oversize ratio, and probe reason.
- Runtime defaults are defensive: `MICRO_PROBE_ENABLED=false`, `MICRO_PROBE_PAPER_ONLY=true`, max `2` daily probes, max `2` open probe positions.
- A probe can only convert an `order_size_too_small` signal when market/mode allow it, entry priority is at least `0.45`, adjusted order is at most `50,000 KRW`, and oversize ratio is at most `2.0`.
- Probe entry/outcome data is stored in `ticker_selection_db.micro_probe_log`; do not mix it into normal strategy promotion decisions.

## Operations Rules (2026-04-22)

Use adaptive operation, not fast strategy rotation.

### Review metrics

For the rolling 2-week review, track these 10 metrics:

1. consensus directional hit rate
2. best analyst - consensus hit gap
3. unanimous mismatch count
4. trade_ready -> signal_fired conversion
5. watch_only missed runup ratio
6. trade_ready forward_3d average
7. ATR-blocked missed runup
8. entry_blackout ratio
9. watch_only blocked ratio
10. continuation average pnl

Storage contract:

- `1~3`: persisted in each session judgment record as `judgment_eval`
- `4~10`: persisted or derivable from:
  - `data/ticker_selection_log.db`
  - `data/ml/decisions.db`
- `1~10 aggregate snapshot`: persisted at `session_close` as `ops_review_snapshot`
  in runtime/live judgment records

### Unanimous override

If all three analysts point to the same directional bucket, final consensus
must not end on the opposite side.

- all bull -> final consensus cannot be bear/flat
- all bear -> final consensus cannot be bull/flat
- all neutral -> final consensus cannot be bull/bear

This is a structural guard, not a tuning rule.

### Claude post-tuning

Claude may only tune bounded runtime controls:

- `momentum_wait_adjust_min`: `-10 .. +10`
- `entry_priority_cutoff_adjust`: `-0.05 .. +0.05`
- `kr_momentum_atr_cap_adjust`: `-0.01 .. +0.02`
- `kr_momentum_atr_cap_high_adjust`: `-0.01 .. +0.02`
- slot bias / replacement aggressiveness: one-step changes only

Claude must not:

- disable hard safety rules
- override unanimous direction guards
- replace market priors with a different strategy philosophy

### Trigger rules

Do not tune every cycle. Tune only at:

- `session_open`
- scheduled intraday tuning windows
- explicit event-driven triggers

Event-driven tuning is allowed when one of these conditions is true:

- recent 10-session `trade_ready -> signal_fired` conversion is too low
  - KR `< 15%`
  - US `< 10%`
- recent 10-session `watch_only missed runup ratio >= 30%`
- recent 10-session `ATR-blocked avg runup >= +4%` with sample `>= 10`
- recent 10-session `consensus hit rate < 45%`
- recent 10-session `best analyst - consensus >= 10%p`
- `unanimous mismatch >= 1`

### Review thresholds

After 2 weeks, modify logic only when thresholds are breached:

- `consensus hit rate < 45%` => review consensus weighting
- `best analyst - consensus >= 10%p` => review aggregation
- `unanimous mismatch >= 1` => immediate fix
- `watch_only missed runup ratio >= 30%` => relax soft promotion rules
- `trade_ready forward_3d avg <= 0%` => review selection quality
- `entry_blackout ratio >= 15%` => reduce late-session churn
- `watch_only blocked ratio >= 25%` => review hard/soft split
- `continuation avg pnl <= -3%` with trades `>= 5` => reduce continuation usage

### Adaptation principle

- Keep strategy families stable.
- Adapt slot mix, wait windows, cutoffs, and ATR handling.
- Apply shrinkage on short windows before changing runtime behavior.
- KR momentum ATR handling:
  - `<= cap`: normal
  - `cap~cap+1%`: size cap `70%`
  - `cap+1~cap+2%`: size cap `50%`
  - `cap+2~high_cap`: size cap `35%`
  - `> high_cap`: block
- Risk-Off exception:
  - default: no new entry
  - exception: `mean_reversion` only
  - constraints: no `HALT`, one position max per market, no same-day reentry,
    no panic index move, size cap `40%`

### Candidate funnel (2026-04-23)

Apply expansion only at the front of the funnel. Do not widen `trade_ready`
or live order concurrency until the new feed proves stable.

- Raw scanner defaults:
  - KR `80`
  - US `80`
- Dynamic universe defaults:
  - KR `40`
  - US `40`
- Claude selection prompt cap:
  - KR starts at `28`
  - US stays at `24` until parse stability is confirmed
- Keep runtime `trade_ready` slot caps unchanged.
- `low_gap_continuation` is not a standalone live strategy yet.
  - Use it only as a promotion/support signal first.

### KR screener policy

For KR, candidate expansion must preserve KOSDAQ visibility.

- Do not merge `KOSPI + KOSDAQ` and then blindly truncate.
- Merge with a minimum KOSDAQ share first, then rank the combined pool.
- Default KOSDAQ minimum share is `35%`.
- Environment overrides:
  - `KR_SCREEN_KOSDAQ_MIN_RATIO`
  - `KR_SCREEN_KOSDAQ_MIN`

### Screener audit

Before evaluating whether candidate expansion worked, persist raw funnel logs.

- KR screener audit path:
  - `logs/screener/YYYYMMDD_KR_screen.jsonl`
- The audit should make these stages inspectable:
  - KOSPI raw
  - KOSDAQ raw
  - merged candidates
  - post-product-filter candidates
- Use these logs before changing caps again.
  - KR prompt cap path: `20 -> 28 -> 32`
  - Only raise the next step after parse stability is acceptable.

### Deferred Follow-ups

These are intentionally deferred. Do not auto-promote them into live behavior
until more data is available or a human explicitly approves the change.

- `brain.json` automatic mutation from lesson scoring
  - keep `lesson_candidates.json` append/score only
  - promoted memory remains approval-based
- automatic hard-block generation from short-window evidence
  - scoring may propose candidates
  - live hard blocks require human review
- `low_gap_continuation` live strategy rollout
  - keep it as observation/promotion support first
  - only move to live after repeated shadow evidence
- strategy-level full replacement
  - avoid swapping strategy philosophy from short windows
  - prefer shrink/observe over full replacement
- history auto-fill expansion for repeated insufficient-history names
  - keep as a later reliability pass, not a live-behavior change

## 프로젝트 철학

- Claude는 시장 판단, 종목 selection, 보유 재량 판단을 맡는다.
- 로직은 진입 조건, 주문 수량/금액, 손절/트레일링, 브로커 동기화, 복구를 맡는다.
- 상태 오염이 의심되면 내부 캐시보다 브로커 truth를 우선한다.
- 짧은 기간 데이터로 전략 철학을 갈아엎지 않는다. 먼저 축소, 관찰, 검증 순서로 간다.

### 핵심 중점 사항 — 데이터 품질과 오염 방어

- `state/brain.json`, `data/ml/decisions.db`, `data/ticker_selection_log.db`는 서로 다른 역할을 가진다.
  - `brain.json`: 정책 메모리
  - `decisions.db`: 의사결정/성과 기록
  - `ticker_selection_log.db`: selection 근거 기록
- 세 파일 중 하나라도 오염되면 Claude 입력 품질이 떨어진다.
- 라이브 복구는 내부 state가 아니라 아래 3가지를 1차 truth로 본다.
  - 브로커 보유 종목
  - 브로커 미체결 주문
  - 가능하면 브로커 체결 내역

### 로그 원칙

- 로그는 디버깅의 1차 수단이다. 사람이 읽는 문구는 한국어로 유지한다.
- 에러/위험/정상 로그는 분리해서 남긴다.
- 깨진 한글, 특수문자, 모지바케는 발견 즉시 복원한다.
- 로그만 보고 원인 추적이 가능해야 한다.

#### 로그 분석 순서

1. `logs/system/` — 전체 상태 전이와 주문 흐름
2. `logs/risk/` — 차단, HALT, 리스크 판정
3. `logs/normal/` — 정상 사이클/진입/청산 흐름
4. `logs/daily_judgment/` — Claude 판단 근거 JSON
5. `logs/screener/` — 후보 풀 축소 단계 점검

## Claude Working Contract

- Claude가 직접 해도 되는 것
  - 시장 모드 판단
  - 종목 watchlist / trade_ready 후보 제안
  - conviction / strategy fit 판단
  - 재량형 HOLD/SELL 의견
- Claude가 직접 하면 안 되는 것
  - 최종 주문 수량 계산
  - 하드 손절 해제
  - 브로커 truth 무시
  - 이벤트 데이터의 장기 메모리 자동 승격
- 튜닝 데이터는 raw 로그를 그대로 주입하지 않는다.
  - `lesson_candidates.json` 같은 후보 규칙으로 점수화한 뒤
  - 저위험 요약만 프롬프트에 넣는다.
  - `brain.json` 자동 수정은 보류한다.

## 나아갈 방향 (Roadmap)

### 현재

- US PathB 수익 구조 보존 (target/pre-close/profit_ladder/Claude sell 4개 경로)
- KR 구조적 손실 분리 관찰: KR live 확대 금지, shadow/축소 우선
- v2 성과 원장 정합성 (decisions.db ↔ v2_event_store sync freshness)
- ticker_selection attribution 누락 리포트 (execution_decision_id 누락률 추적)
- candidate audit outcome freshness 표시 (daily_pending 상태 명시)

### 다음 단계

- KR-only shadow veto gate: 장 초반/후반 진입, loss_cap 직후 재진입, stop cluster 시 size-down
- US loss_cap cluster shadow: buy-zone-hit 후 손절 집중 구간 size-down 계측
- watch_only missed runup + bucket decomposition 연결 리포트
- PEAD surprise shadow 5거래일 검증 및 prompt 적용 검토

### 장기

- brain 자동 승격은 승인형 워크플로우가 안정화된 뒤 검토
- 전략 추가보다 입력 품질과 실행 품질 개선 우선

## 운영자 확인 필수 설정값

아래 설정은 변경 전 반드시 운영자에게 확인한다. 코드 리뷰, config 정리, 자동 수정 등 어떤 경로로도 임의로 바꾸지 않는다.

| 설정 | 현재값 | 의미 |
|---|---|---|
| `PATHB_INTRADAY_ONLY` | `false` | PathB 포지션 당일 강제청산 여부. false = multi-day hold 허용 |
| `KR_LATE_ENTRY_GATE_ENABLED` | `false` | KR 늦은 진입 게이트. false = 시간대 차단 없음 |
| `KR_LATE_ENTRY_EXEC_GATE_ENABLED` | `false` | KR 늦은 진입 실행 게이트. false = 차단 없음 |
| `PATHB_KR_LIVE_ENABLED` | `true` | KR PathB live 활성 여부 |
| `PATHB_US_LIVE_ENABLED` | `true` | US PathB live 활성 여부 |
| `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK` | `false` | KR zone hit 시 주문 제출 차단 여부. false = 정상 주문 허용 |
| `KR_REENTRY_COOLDOWN_MINUTES` | `60` | KR 재진입 쿨다운(분) |
| `US_REENTRY_COOLDOWN_MINUTES` | `60` | US 재진입 쿨다운(분) |
| `KR_EARLY_ENTRY_SOFT_GATE_ENABLED` | `true` | KR 장 초반 진입 사이즈 축소 게이트 활성 여부 |
| `PATHB_KR_SHADOW_PLAN_ENABLED` | `false` | KR PathB shadow 플랜 활성 여부. false = shadow 비활성 |
| `US_MOMENTUM_LIVE_ENABLED` | `true` | US momentum 전략 live 활성. **false로 바꾸면 US PathB momentum 후보가 생성되지 않는다 (누적 수익 경로)** |
| `US_VOLATILITY_BREAKOUT_LIVE_ENABLED` | 미설정(=false) | US VB 전략 live 활성. VB 성과 미확인 상태이므로 현행 유지 |
| `KR_PLANA_HOLD_POLICY_MODE` | `enforce` | KR Plan A hold advisor 정책 강제 적용 여부 |
| `US_PLANA_HOLD_POLICY_MODE` | `enforce` | US Plan A hold advisor 정책 강제 적용 여부 |
| `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS` | `true` | **Path A 자동 매도 전 Claude hold advisor 리뷰 게이트 활성 여부. false로 바꾸면 loss_cap·stop_loss·trail_stop 등 Path A 자동 매도가 Claude 판단 없이 즉시 실행된다. 반드시 true 유지.** |
| `SELECTION_SMART_SKIP_MODE` | `live` | selection 재사용 모드. live = TTL 내 동일 semantic signature면 Claude 호출 생략. observe로 낮추면 호출 횟수 증가 |
| `SUB_SCREENER_TRIGGER_ENABLED` | `true` | sub_screener 감지 후 triage/reinvoke 활성 여부 |
| `PULLBACK_WAIT_EVIDENCE_GATE_MODE` | `live` | evidence 부족 시 PULLBACK_WAIT → WATCH 강등 적용. shadow로 낮추면 약한 evidence 후보가 PathB wait pool로 진입 가능 |
| `CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS` | `2200` | compact selection 응답 최대 토큰. 초과 시 trade_ready=[] fallback. 25/7 cap 실험 시 2600으로 올릴 것 |
| `INTRADAY_REVIEW_COOLDOWN_MINUTES` | `120` | 포지션별 intraday review 최소 간격(분). 손익 급변·stop 근처는 우회 |
| `INTRADAY_REVIEW_DAILY_MAX_PER_POSITION` | `3` | 포지션별 일중 review 최대 횟수. pending_due·손익 급변·stop 근처는 초과 허용 |

이 설정들은 `.env.live`와 `config/v2_start_config.json` 두 곳에 존재한다. 한 곳만 바꾸면 반영이 안 될 수 있으므로 두 파일을 동시에 확인한다.

### PathB KR/US 현재 운영 파라미터 (2026-05-21 기준)

**이 섹션의 값을 변경하면 반드시 운영자에게 먼저 알린다.**

#### 공통 (KR = US)

| 파라미터 | 현재값 |
|---|---|
| 고정 주문금액 | 450,000 KRW |
| 최대 포지션 수 (`PATHB_MAX_POSITIONS`) | 15 |
| 일일 최대 진입 수 (`PATHB_MAX_DAILY_ENTRIES`) | 40 |
| 최소 confidence (`PATHB_MIN_CONFIDENCE`) | 0.5 |
| INTRADAY_ONLY (`PATHB_INTRADAY_ONLY`) | false (multi-day hold 허용) |
| 재진입 쿨다운 | 60분 |
| 장 초반 soft gate | 0~60분 size × 0.5 |
| Shadow 플랜 | 비활성 |

#### KR만 다른 것

| 파라미터 | KR | US |
|---|---|---|
| 슬리피지 캡 | 1.003 (0.3%) | 1.002 (0.2%) |
| Protective hold 최소 거리 | 0.5% | 0.3% |

#### 변경 시 주의사항

- 위 값 중 어떤 것이라도 바꾸면 **변경 전에 운영자에게 명시적으로 알려야 한다**.
- 재진입 쿨다운, 슬리피지 캡, soft gate 파라미터는 진입 빈도와 직결되므로 단독 변경 불가.
- KR/US 를 비대칭으로 바꿀 경우 의도적 차이인지 반드시 확인한다.

## 코드 작업 원칙

1. 작은 수정으로 큰 사고를 막는다.
2. 라이브 수익률/손실 계산 축은 KR/US를 섞지 않는다.
3. 브로커 불신 상태에서는 신규 진입보다 보호를 우선한다.
4. selection과 execution 문제를 섞지 않는다.
5. 수정 후에는 단계별 검증 + 마지막 통합 QA를 반드시 한다.

### 기능 변경 후 검증 절차

1. 관련 단위 테스트 실행
2. 관련 통합 테스트 실행
3. 수익률/자산곡선/브로커 truth 계산축 점검
4. 로그/대시보드 문구 확인
5. 전체 QA 재실행

### Git 원칙

- runtime 산출물과 정책 메모리는 구분한다.
- 사용자 작업이 섞인 파일은 함부로 되돌리지 않는다.
- 커밋에는 변경 목적이 분명한 파일만 포함한다.

## 실행 명령

```bash
# 의존성 설치
python -m pip install -r requirements.txt

# 봇 실행 — 모의투자
python trading_bot.py --paper

# 봇 실행 — 실거래
python trading_bot.py --live

# 대시보드 서버
python dashboard/dashboard_server.py

# 배포 전 구문 검사
python -m py_compile trading_bot.py dashboard/dashboard_server.py claude_memory/brain.py

# 모의투자 preflight 검증
python tools/live_preflight.py --mode paper --skip-dashboard --json

# 실거래 전 preflight 검증
python tools/live_preflight.py --mode live --skip-dashboard --json

# 전체 회귀 테스트
python -m pytest -q

# 전체 테스트 (tests/ 디렉토리)
python -m pytest tests/ -q

# 특정 테스트 파일
python -m pytest tests/test_candidate_audit.py -q

# action routing 집중 테스트
python -m pytest tests/test_action_routing.py -q

# 키워드로 특정 케이스만
python -m pytest tests/test_candidate_audit.py -k "payload_fallback" -q

# 루트 레벨 레거시 회귀 테스트
python -m pytest test_trading_improvements.py test_broker_sync_cash.py -q

# ML DB 검증
python -m unittest ml.test_full
```

Windows 환경에서 일부 파일(`tests/test_live_order_safety.py` 등)에 CRLF 관련 git 경고가 표시되지만 동작에는 무관하다.

## 아키텍처 — 큰 그림

### 환경 파일 로딩 순서

`trading_bot.py` 시작 시:

1. `--live` 플래그에 따라 `.env.live` 또는 `.env.paper` 로드 (없으면 `.env` fallback)
2. `config/v2_start_config.json`의 `env_overrides` 키를 `os.environ`에 덮어씀 (live 모드 전용)
3. 따라서 live 환경 설정은 `.env.live` + `config/v2_start_config.json` 두 곳에서 결정된다.

KIS 브로커 API(`kis_api.py`)는 `KIS_APP_KEY_US` / `KIS_APP_SECRET_US`가 비어 있으면 KR 키로 fallback한다. 한투 KIS는 하나의 계정으로 KR/US 모두 접근 가능하므로 이것이 정상 운영 정책이다. `.env.live`에 `KIS_US_CREDENTIAL_FALLBACK_ACCEPTED=true`를 설정하면 preflight 경고가 제거된다.

### 실행 흐름

1. 브로커/시장 상태 수집
2. digest 및 intraday context 생성
3. Claude 시장 판단 / selection
4. 로직 기반 진입 필터링
5. 주문/체결/복구
6. 성과 기록 및 lesson candidate 적재

### 서브패키지 구조

| 패키지 | 역할 |
|---|---|
| `execution/` | 주문 실행: `claude_price_adapter.py`(PathB 가격), `safety_gate.py`(PathB 안전 게이트), `sizing.py`, `path_arbiter.py`(same-day reentry 차단) |
| `decision/` | Claude 판단 구조체: `claude_price_plan.py`(B플랜 파싱), `registry.py` |
| `runtime/` | 실행 시 판단 로직: `pathb_runtime.py`(PathB 메인), `action_routing.py`(RouteDecision), `gate_evaluation.py`, `candidate_pool_runtime.py`, `live_evidence_pack.py` |
| `lifecycle/` | 사이클 생명주기: `event_store.py`, `quality.py`, `path_context.py` |
| `preopen/` | 장 시작 전 후보 뉴스/점수: `scheduler.py`, `scorer.py`, `storage.py` |
| `audit/` | 감사 DB: `candidate_audit_store.py`(upsert + payload merge), `shadow_audit_store.py`, `agent_call_event_store.py` |
| `ml/` | 의사결정 성과 DB: `db_writer.py`, `forward_updater.py`, `db_health.py` |
| `config/` | 런타임 설정: `runtime_config.py`(EffectiveRuntimeConfig), `v2.py`(V2Config), `v2_start_config.json` |
| `claude_memory/` | brain 메모리: `brain.py`(읽기/쓰기/요약), `brain.json`(실제 정책 메모리는 `state/brain.json`으로 오버라이드) |
| `interface/` | 요약/Telegram 포맷: `v2_ops_summary.py`, `v2_telegram.py` |
| `tools/` | 운영 도구: `live_preflight.py`, `live_guardian.py`, `live_maintenance.py`, `reconcile_*.py`, `analyze_*.py` |

### 두 실행 경로 (Path A / Path B)

- **Path A**: `TradingBot` 클래스(`trading_bot.py`) — Claude selection → 전략 신호 → 주문
- **Path B**: `PathBRuntime`(`runtime/pathb_runtime.py`) — Claude 가격 플랜 기반 진입/청산, `PATHB_US_LIVE_ENABLED=true`일 때 활성

두 경로는 `runtime/action_routing.py`의 `RouteDecision`으로 합류한다. PathB는 KR/US 모두 live 활성 상태이다 (`PATHB_KR_LIVE_ENABLED=true`, `PATHB_US_LIVE_ENABLED=true`).

### 진입 결정 파이프라인

- 후보 풀 생성 → `runtime/candidate_pool_runtime.py`
- selection raw → Claude 응답
- normalized trade_ready → applied trade_ready
- 전략 신호 검사 → `runtime/action_routing.py`
- affordability / 리스크 검사 → `risk_manager.py`
- 주문 생성 → `kis_api.py`

### 판단 재사용 로직 — 봇 재시작 시 주의

- 미국장은 KST 자정이 넘어도 active US session date를 유지할 수 있다.
- 재시작 시 당일 판단 재사용은 가능하지만, 브로커 truth 검증이 선행돼야 한다.
- stale legacy 포지션은 holdings/pending 기준으로 정리한다.

### 매수 차단 조건

- 브로커 상태 불신
- affordability fail
- hard risk block
- same-day reentry block
- late-session / blackout 조건
- watch_only 상태 유지

### `_sync_runtime_with_broker()` 주의사항

- 보유 종목 + 미체결 주문 기준으로 stale 포지션을 정리한다.
- 브로커 응답이 불신이면 해당 시장만 quarantine한다.
- quarantine 상태에서는 신규 진입보다 기존 포지션 관리가 우선이다.
- HALT / daily_return은 시장별 baseline 기준으로 계산한다.

### 주요 데이터 흐름

- `state/brain.json`: Claude 정책 메모리 (런타임 경로는 `runtime_paths.py`가 결정)
- `state/lesson_candidates.json`: 자동 점수화된 교훈 후보
- `data/audit/candidate_audit.db`: 후보 감사 DB (source_file / payload merge 포함)
- `data/ml/decisions.db`: 의사결정/성과 데이터
- `data/ticker_selection_log.db`: selection 로그
- `logs/pead/*.json`: surprise shadow 기록

### KIS API 정규화 규칙

- `broker_truth_snapshot.py`의 `open_orders`는 오늘 체결/미체결 조회 결과 중
  `remaining_qty > 0`인 행만 필터링하여 파생된다.
- `_normalize_kr_daily_ccld_row()`와 `_normalize_us_inquire_ccnl_row()` 등
  모든 KIS 주문 정규화 함수는 반드시 `remaining_qty` 필드를 포함해야 한다.
  누락 시 해당 시장의 `open_orders`가 항상 빈 리스트가 되어
  ORDER_UNKNOWN 매도 복구, sellable qty reject 처리가 전부 오작동한다.
- US 정규화는 `nccs_qty` 필드를 우선 사용하고, 없으면 `order_qty - filled_qty`로 fallback.

### PathB 매도 차단 조건 및 복구

`sellable_qty_untrusted=True`는 매도를 완전히 차단하며, 다음 조건 중 하나로 설정된다:

- 매도 주문이 실패하고 브로커 `open_orders`에서 미체결 주문을 찾지 못한 경우
  (`resolution=no_open_order_or_fill`)
- `manual_reconcile_required=True` 또는 `broker_sell_lock_suspected=True`

복구 경로:

1. `_pathb_sellable_qty_reject_evidence()` → 브로커 fresh refresh (`force=True, ttl_sec=15`)
2. `open_orders`에 매도 주문 발견 → `_recover_existing_sell_order_after_qty_reject()` →
   자동 복구 (sellable_qty_untrusted 해제, 기존 주문 ack로 재연결)
3. 발견 못하면 → `manual_reconcile_required=True` → **운영자 수동 처리 필요**

ORDER_UNKNOWN 매도 복구 흐름 (`_reconcile_exit_order_unknown_run`):

- 체결 확인 → 포지션 종료
- 브로커 `open_orders`에 매도 주문 발견 → ack 등록 (재매도 시도 안 함)
- 미체결 증거 없고 보유 확인 → stale 복구 + 재매도 시도

`open_orders`가 정상 작동하지 않으면 세 번째 경로가 잘못 실행되어 중복 매도 시도 →
"주문수량이 가능수량보다 큽니다" → `sellable_qty_untrusted` 영구 잠금으로 이어진다.

## TODO / 미완성 작업 목록

백로그와 우선순위는 [`docs/important/core/TODO_ROADMAP.md`](docs/important/core/TODO_ROADMAP.md)와
[`docs/important/ACTIVE_WORK.md`](docs/important/ACTIVE_WORK.md)가 관리한다.
이 파일에 중복 기재하지 않는다.

### 완료 (주요 이력)

- soft watch 승격 기본 차단
- continuation live 중단, shadow-only 전환
- live HALT/daily_return 시장별 분리
- stale legacy 포지션 정리
- `brain.json` 중복/상충 기록 정규화
- 브레인/대시보드 한글 깨짐 복원
- US 미체결 주문 `remaining_qty` 누락으로 `open_orders` 필터 실패 수정 (2026-05-27)
- Smart Skip semantic signature 전환: 전체 prompt hash → ticker+action_ceiling 기반, 가격 노이즈 무시 (2026-06-04)
- Runtime handoff snapshot: 재시작 시 today_tickers·trade_ready·price_cache·post_open evidence 복원 (2026-06-04)
- Post-open feature JSONL 복원: 세션별 최신 스냅샷 persist → 재시작 후 evidence 연속성 유지 (2026-06-04)
- INTRADAY_REVIEW gate: per-position cooldown 120분 + daily max 3회, 트리거 기반 우회 (2026-06-04)
- AUTO_SELL_REVIEW hard guard cache bypass: hard_guard 발동 시 stale cache 우회 → fresh Hold Advisor 호출 (2026-06-04)
- sub_screener Plan A min score floor: `SUB_SCREENER_PLAN_A_MIN_SCORE=70`, 저품질 trigger 방지 (2026-06-04)
- `brain.json` pre-commit hook: `tools/check_brain_commit.py`, 코드 커밋에 brain 혼입 차단 (2026-06-04)
- operator_summary 추가: current trading risk / previous-session cleanup 분리 표시 (2026-06-04)
- v2 성과 sync 정합성: v2_event_store → decisions.db 최신 CLOSED 이벤트 반영 경로 보강 (2026-06-05)

## 재시작 / 장애 복구 절차

1. 브로커 보유 종목 조회
2. 브로커 미체결 주문 조회
3. 가능하면 브로커 체결 내역 조회
4. legacy state와 비교
5. stale 포지션 제거 또는 보호 상태 전환
6. 시장별 HALT / baseline 계산 확인
7. 신규 진입 허용 여부 판단

## 모의투자 → 실거래 전환 체크리스트

- 실거래 키/계좌 확인
- 모의 토큰/상태 제거 여부 확인
- KR/US 최대 주문 금액 확인
- 리스크 한도 확인
- 브로커 동기화 테스트 1회
- 대시보드 live 값과 실제 계좌 대조

## 전략 추가/수정 시

- 전략 추가보다 입력 품질 개선이 먼저다.
- selection 품질, 진입 시점 품질, 로그 설명 가능성을 먼저 본다.
- 새 전략은 shadow로 검증한 뒤 live로 올린다.

## Telegram 운영 명령어

- `/status` 현재 상태
- `/pos` 보유 포지션
- `/review` 보유 종목 재검토
- `/setorder [금액]` 최대 주문 금액 변경
- `/setloss [%]` 일일 손실 한도 변경
- `/trail on|off` 트레일링 on/off
- `/entry on|off` entry priority on/off
- `/brain` 브레인 요약
- `/credit` API 사용량 확인

## 커밋 전 체크리스트

- `git diff --stat`
- `git status --short`
- 단계별 테스트 통과
- 전체 QA 통과
- 수익률 계산축/KR·US 분리 재확인
- 대시보드 live truth 경로 재확인
