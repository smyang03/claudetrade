# Git And Code Review Plan

Date: 2026-05-22

## Scope

Reviewed:

- Current working tree via `git status --short`, `git diff --stat`, and `git diff --name-status`.
- Recent commits via `git log --oneline -12` and `git show --stat HEAD~5..HEAD`.
- Code paths in `trading_bot.py`, `runtime/pathb_runtime.py`, `runtime/sub_screener.py`, `interface/v2_ops_summary.py`, `tools/live_guardian.py`, `logger.py`, `strategy/adaptive_params.py`, `dashboard/dashboard_server.py`, and `kis_api.py`.

No code changes were made in this review. This document updates the plan based on the current code state.

## Current Git State

Recent commit theme:

| Commit | Meaning |
| --- | --- |
| `7845f91` | Prompt pool hard cap adjusted to KR 32 / US 35. |
| `71f0cd6` | Trainer prompt pool structure expanded. |
| `cdbfbd4` | PathB readiness/guardian path improved; KR entry block reads runtime config first. |
| `5728f82` | KR early-entry soft gate added; KR/US re-entry cooldown unified to 60 minutes. |
| `77ec1e0` | KR re-entry cooldown changed to 60 minutes. |

Working-tree code theme:

- `runtime/sub_screener.py` added.
- `trading_bot.py` integrates sub-screener, evidence alignment diagnostics, data-insufficient backfill cooldown, sell safety reconcile, and risk event logging.
- `runtime/pathb_runtime.py` adds PathB entry broker-truth refresh gate, profit-review timeout HOLD fallback, zero-holding sell reconcile, and risk-event payloads.
- `interface/v2_ops_summary.py` distinguishes entry waiting plans and broker-truth warning state.
- `dashboard/dashboard_server.py` mostly has analyst-card layout fixes plus candidate audit link fields already exposed.
- `strategy/adaptive_params.py` already prefers `v2_canonical_performance` when available.
- `kis_api.py` still uses Yahoo/FMP/fallback for US screening; KIS overseas ranking is not implemented.

Commit hygiene risk:

- Working tree includes intentional code/docs plus `state/brain.json`, generated state JSON, DB sidecars, temp browser/profile files, screenshots, and runtime data.
- `state/brain.json` is policy memory and should not be included without explicit approval.

## Priority Plan

### P0-0 - Commit Hygiene Before Staging

Before:

- Source changes, docs cleanup, runtime outputs, temp browser artifacts, generated state files, and policy memory are mixed in one working tree.
- A broad `git add -A` would accidentally include files that should not be committed.

After:

- Commit contains only intentional source/docs/config/test changes.
- `state/brain.json` and generated runtime artifacts are excluded unless separately approved.
- Reviewers can reason about behavior changes without noise.

Action:

- Use `git status --short` and stage by path.
- Exclude `state/brain.json`, `state/*202605*.json`, `data/*-shm`, `data/*-wal`, `tmp_*`, and local screenshots unless explicitly needed.

### P0-1 - Sub-Screener Live Trigger Safety

Before:

- `config/v2_start_config.json` enables `SUB_SCREENER_ENABLED=true` and `SUB_SCREENER_TRIGGER_ENABLED=true`.
- `maybe_run_sub_screener()` can force-refresh screening, reinvoke analysts, and call `manual_rescreen()` every 15 minutes, up to 5 attempts per session.
- This can change the live candidate pool and Claude call volume before enough shadow evidence exists.

After:

- Sub-screener runs shadow-first by default, or live trigger is enabled only after explicit operator approval.
- Preflight/guardian/dashboard show trigger state, attempt count, success count, last detection, and call budget.
- Live candidate changes are traceable as `sub_screener_rescreen` and can be separated from normal selection quality.

Action:

- Decide whether tracked config should set `SUB_SCREENER_TRIGGER_ENABLED=false` until shadow evidence exists.
- Add `.env.example` entries and preflight visibility for all `SUB_SCREENER_*` values.
- Keep existing unit tests and add one ops/status test for exposed counters.

### P0-2 - Broker-Truth Destructive Reconcile Verification

Before:

- Plan A and PathB now handle sell `insufficient_holding` by refreshing broker truth and removing/closing local positions if broker positions and open orders are zero.
- This reduces stale local position risk, but real KIS KR/US payload shapes must match the row parsers.

After:

- Local position removal happens only with verified fresh broker truth and no open order.
- Stale/untrusted broker truth creates a risk event and leaves local state intact for manual reconciliation.
- KR/US row parsing is covered by realistic payload tests for positions, open orders, fills, side, and remaining quantity.

Action:

- Add fixture tests around `_sell_zero_holding_broker_evidence()` and `_pathb_zero_holding_broker_evidence()`.
- Run live preflight/guardian before any live session that includes these changes.

### P0-3 - PathB Entry Broker-Truth Gate Validation

Before:

- PathB entry scan now refreshes broker truth and blocks on stale/untrusted truth.
- The behavior defaults from code and is not yet documented in `.env.example`.
- Preflight/guardian do not clearly surface the refresh TTL/min-interval knobs.

After:

- Entry blocking caused by broker truth is visible as an operator state, not just a log line.
- Refresh frequency and failure counts are visible in ops summary or guardian output.
- Token/provider unavailable cases are explicitly reviewed so they cannot accidentally allow a live entry path that should be blocked.

Action:

- Document `PATHB_ENTRY_SCAN_BROKER_TRUTH_REFRESH_ENABLED`, TTL, and min interval.
- Add preflight/ops summary visibility for refresh metrics and last error.

### P0-4 - Profit Review Timeout Fallback Audit

Before:

- PathB profit review timeout now writes a HOLD fallback with `advisor_unavailable=true` and `learning_excluded=true`.
- Without dashboard/canonical labeling, an operator could misread this as a genuine HOLD judgment.

After:

- Timeout fallback is displayed as advisor outage/fallback, not strategy judgment.
- Learning/performance pipelines exclude timeout HOLDs from strategy quality.
- Repeated timeouts are visible by ticker and market.

Action:

- Surface `profit_review_fallback`, reason, timeout count, and advisor-unavailable status in ops/dashboard.
- Add a canonical/reporting assertion that `learning_excluded=true` keeps these rows out of performance learning.

### P1-1 - Prompt Pool Hard-Cap And Evidence Alignment Validation

Before:

- Latest commits changed prompt-pool cap and trainer pool structure.
- Runtime now records overlap ratio, missing evidence tickers, missing exec tickers, and universe filter bypass state, but the next-session metric has not been reviewed.

After:

- KR/US prompt candidates, evidence prefetch, execution candidate pool, and READY count are measured after the hard-cap change.
- If overlap falls below target, the adjustment is backed out or target limits are tuned with evidence.

Action:

- After the next paper/live session, review `evidence_prompt_overlap_ratio`, `missing_evidence_tickers`, `missing_exec_tickers`, READY count, and Claude call volume.

### P1-2 - US KIS Ranking Screener

Before:

- `kis_api.screen_market_us()` still uses Yahoo Finance, FMP, and fallback.
- `trading_bot._screen_market_candidates()` calls US screener without a token.
- The KIS ranking requirement document remains source evidence, not implemented behavior.

After:

- US screener uses KIS overseas ranking endpoints first.
- Token missing, API error, empty response, or quality failure falls back to the current Yahoo/FMP/cache behavior.
- Order, risk, PathB, and broker truth logic remain untouched.

Action:

- Implement optional `token` in `screen_market_us()`.
- Add KIS `trade-vol` and `updown-rate` ranking helper/normalizer.
- Add tests for token absent fallback, API failure fallback, cache compatibility, and category quota parity.

### P1-3 - V2 Canonical Truth Runbook

Before:

- Canonical performance support exists.
- Adaptive params and dashboard can prefer canonical truth.
- Candidate audit live link fields are written and dashboard rows expose them.
- The operational daily command and repair policy still need to be fixed into a runbook.

After:

- Daily sync produces `v2_canonical_performance` and `v2_decision_fill_links` consistently.
- Legacy `decisions.db` is clearly labeled as signal/evaluation history, not sole fill truth.
- Dashboard, adaptive params, and analysis reports use the same truth source.

Action:

- Document exact sync command, including when `--repair-decisions` is allowed.
- Add a daily-loop/guardian check for canonical sync freshness.

### P1-4 - Counterfactual Outcome Schedule

Before:

- Counterfactual store, updater, analyzer, and tests exist.
- Policy decisions still require regular outcome filling for blocked/watch-only rows.

After:

- 30m/60m/close returns, MFE/MAE, status, price source, and metadata quality are filled on a schedule.
- Gate changes can be reviewed by opportunity cost and risk, not by isolated examples.

Action:

- Add a daily command to run `tools/update_counterfactual_outcomes.py`.
- Review analyzer output by market and bucket before changing gates.

## Summary Judgment

The most urgent issue is not missing code. It is safe promotion and clean commit scope.

- New safety code is generally moving in the right direction: broker truth is used before destructive reconciliation, and timeout/advisor failures are marked as non-learning execution safety events.
- The largest live behavior risk is the newly enabled sub-screener trigger because it can alter live candidate flow and Claude call volume.
- The largest remaining feature gap is US KIS ranking screener integration.
- The largest analysis gap is operationalizing canonical performance and counterfactual outcomes as recurring truth, not one-off reports.
