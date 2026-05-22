# Immediate Recheck And Risk Analysis

Date: 2026-05-22

Scope: code-level recheck for the immediate work only. This document does not authorize config changes or live behavior changes.

## Verdict

The sub-screener is already implemented. Do not build it again.

The urgent work is:

1. Make the effective sub-screener trigger state visible without changing config.
2. Add broker-truth zero-holding fixture tests before changing reconcile logic.
3. Keep commit staging clean because the working tree contains runtime artifacts and policy memory.

The buy/sell quality risk is not from the immediate read-only visibility work. Quality degradation risk starts when candidate source, order sizing, PathB gates, stops, or live/shadow config are changed. Those changes are deferred unless separately approved.

## Code Evidence

| Area | Code Evidence | Current Judgment |
| --- | --- | --- |
| Sub-screener trigger resolution | `trading_bot.py:634` `_sub_screener_trigger_enabled()` | Market-scoped trigger flags override the global flag. If scoped and global flags are absent, trigger defaults to live. |
| Current tracked config | `config/v2_start_config.json:303` to `config/v2_start_config.json:306` | `SUB_SCREENER_ENABLED=true`, global trigger is `false`, KR/US scoped triggers are `true`, so KR/US effective trigger is live. |
| Sub-screener execution | `trading_bot.py:21880` `maybe_run_sub_screener()` | Scan, state record, rate limit, reinvoke, and rescreen path exist. |
| Sub-screener counters | `runtime/sub_screener.py:62` to `runtime/sub_screener.py:69` | `scan_count`, `detection_count`, `attempt_count`, `success_count`, and last timestamps already exist in state. |
| Sub-screener tests | `tests/test_sub_screener_integration.py:77`, `:98`, `:123` | Shadow/global and scoped override behavior has tests. Preflight/ops effective-state display is missing. |
| Plan A zero-holding reconcile | `trading_bot.py:19387`, `:19401`, `:19485` | Stale local position can be removed only after fresh broker zero-holding evidence. Existing tests are narrow and mostly synthetic. |
| PathB zero-holding reconcile | `runtime/pathb_runtime.py:3991`, `:4084` | PathB can close stale local run after fresh broker zero-holding evidence. Existing tests cover US synthetic payloads. |
| PathB entry broker-truth gate | `runtime/pathb_runtime.py:702` | Gate code and metrics exist, but ops visibility remains deferred. |
| Profit review timeout fallback | `runtime/pathb_runtime.py:3425` | Timeout HOLD fallback is marked `advisor_unavailable` and `learning_excluded`; display work remains deferred. |
| US KIS ranking screener | `kis_api.py:4360` | `screen_market_us()` still has no token parameter and uses Yahoo/FMP/fallback. This is deferred because candidate-source changes can affect buy quality. |

## Recheck List

### R0 - Commit Hygiene

Before:

- `git status --short` shows `state/brain.json`, generated state files, DB sidecars, screenshots, and temp browser profiles mixed with docs/source changes.
- A broad `git add -A` can accidentally stage runtime truth, policy memory, or local artifacts.

After:

- Only intentional docs/source/test paths are staged.
- `state/brain.json` and runtime/generated artifacts remain unstaged unless explicitly approved.

Risk:

- Shipping policy memory or runtime state can corrupt future analysis and operations.

Mitigation:

- Stage explicit paths only.
- Run `git status --short` and `git diff --stat` before any commit.

### R1 - Sub-Screener Effective Trigger Visibility

Before:

- Operators may read `SUB_SCREENER_TRIGGER_ENABLED=false` as shadow mode.
- Code actually resolves KR/US as live because `SUB_SCREENER_KR_TRIGGER_ENABLED=true` and `SUB_SCREENER_US_TRIGGER_ENABLED=true` exist.
- If both scoped and global trigger flags are absent, `_sub_screener_trigger_enabled()` defaults to live.

After:

- Preflight or ops summary shows per-market:
  - `enabled`
  - `global_trigger`
  - `scoped_trigger`
  - `effective_trigger`
  - `resolution_source`: `scoped`, `global`, or `default_live`
  - interval/rate-limit settings
  - state-file counters and last timestamps
- Text explicitly states that scoped trigger overrides global trigger.
- No config value changes as part of this work.

Risks:

- Hidden live trigger: operator thinks the sub-screener is shadow while reinvoke/rescreen can run.
- Default-live edge case: missing config may become live by fallback.
- False confidence from counters: no state file can mean no run yet, not necessarily disabled.

Mitigation:

- Implement a shared read-only resolver that follows the exact code precedence.
- Label the source as `scoped`, `global`, or `default_live`.
- Include `state_file_exists` so an empty counter is not mistaken for observed zero activity.
- Keep policy/config changes behind operator confirmation.

Acceptance checks:

- Global false + scoped true -> effective true, `resolution_source=scoped`.
- Global true + scoped false -> effective false, `resolution_source=scoped`.
- Global false + scoped absent -> effective false, `resolution_source=global`.
- Global absent + scoped absent -> effective true, `resolution_source=default_live`.
- Disabled trigger records scan only and does not call `record_attempt()`, `_reinvoke_analysts()`, or `manual_rescreen()`.

### R2 - Broker-Truth Zero-Holding Fixture Tests

Before:

- Destructive reconcile behavior exists in both Plan A and PathB.
- Current tests prove the broad behavior, but do not cover realistic KR/US KIS row shapes deeply enough.
- KR-specific payload shape, open-order remaining quantity, side fields, ticker key variants, and unsafe sell-fill-only cases need fixture coverage.

After:

- Fixture tests cover realistic KR/US broker payloads before any reconcile parser change.
- Parser changes are allowed only if the fixtures expose a real mismatch.
- Fresh zero position and no open remaining quantity can reconcile stale local state.
- Stale/error/missing broker truth, open remaining quantity, or ambiguous ticker matching keeps local state and requires manual reconciliation.

Risks:

- False zero-holding: malformed or unrecognized broker row fields could make a held position look absent.
- Open-order miss: remaining sell/buy order not detected could remove local state too early.
- Ticker collision: KR numeric tickers and US uppercase tickers must not cross-match incorrectly.
- Sell-fill evidence confusion: a sell fill alone is not enough if position/open-order truth is unsafe.

Mitigation:

- Add KR and US fixture rows for `ticker`, `pdno`, `ovrs_pdno`, quantity, remaining quantity, side, and fills.
- Assert both safe and unsafe outcomes.
- Do not call live broker APIs in tests.
- Preserve current fail-closed behavior when truth is stale, missing, errored, or open order exists.

Acceptance checks:

- KR fresh zero position + no open order -> stale local position can be removed.
- US fresh zero position + no open order -> stale local position can be removed.
- KR/US open order with remaining quantity -> local position is not removed.
- Stale broker truth -> local position is not removed.
- Broker error/missing snapshot -> local position is not removed.
- Unrecognized ticker key -> no false ticker match.
- Sell fill evidence exists but position/open-order condition is unsafe -> manual reconciliation remains required.

## Deferred Recheck Items

These are important but not urgent for the current pass:

| Item | Reason To Defer | Recheck Trigger |
| --- | --- | --- |
| PathB entry broker-truth gate ops visibility | Gate exists and blocks on untrusted truth. It is observability work, not the first ambiguity. | Do after R1/R2 or when live entry blocks need dashboard diagnosis. |
| Profit review timeout fallback display | Safety fallback exists and is learning-excluded. Display separation is useful but less urgent. | Do after R1/R2 or when reviewing advisor quality. |
| US KIS ranking screener | Candidate-source change can affect buy quality. Needs shadow quality metrics first. | Start only with KIS-vs-Yahoo/FMP raw and final candidate quality logging. |
| V2 canonical truth runbook | Important for analysis freshness, not immediate live safety. | Do before relying on adaptive/canonical performance decisions. |
| Counterfactual outcome schedule | Analysis quality improvement, not a live-path blocker. | Do with daily/weekly review workflow. |
| KR fade recovery live promotion | Shadow signal is implemented, but live promotion can alter entry quality. | Require observation data and operator approval. |

## Buy/Sell Quality Impact

Immediate R1 work should not change buy or sell quality because it is read-only visibility.

Immediate R2 fixture work should not change buy or sell quality because it only adds tests. If the tests reveal a parser mismatch, a later code fix can improve sell safety by preventing stale local holdings from being removed incorrectly or by allowing confirmed stale local records to be cleaned safely.

Deferred R5 can change buy quality because it changes the US candidate source. That work must start in shadow comparison mode and log:

- raw candidate overlap
- final screened candidate overlap
- price distribution
- volume or dollar-volume pass/fail
- change_pct distribution
- category counts
- fallback reason

## Development Sequence

1. Add a read-only sub-screener effective-state helper, preferably near `runtime/sub_screener.py`, so preflight/ops do not drift from runtime behavior.
2. Add a preflight check that reports the KR/US effective state and state-file counters.
3. Add tests for the effective-state matrix and current default-live edge case.
4. Add KR/US zero-holding fixture tests for Plan A and PathB.
5. Only if fixtures fail, adjust broker row parsing in the smallest possible scope.

## Do Not Change In This Pass

- Do not change `SUB_SCREENER_*` config values.
- Do not change PathB live gates, order sizing, stop logic, broker-truth priority, or affordability logic.
- Do not implement US KIS ranking as first source yet.
- Do not promote `fade_recovered_shadow` to live action.
- Do not edit `state/brain.json`.
