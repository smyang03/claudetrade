# P0 Data Quality And Breadth Implementation Plan

Date: 2026-05-02
Scope: KR/US common P0 data quality, market breadth persistence, and QA comparison.

## Goal

Improve market judgment inputs without materially increasing API usage.

The P0 change must:

- Preserve current live trading behavior.
- Stop representing missing risk indicators as `0`.
- Persist `breadth_summary` in new daily digest files.
- Add enough source and quality metadata to make missing/stale data explicit.
- Keep incremental API usage near zero, except one daily DXY/VKOSPI style lookup when configured.

## API Budget Target

| Area | Expected Additional Calls |
|---|---:|
| `breadth_summary` calculation | 0 |
| `0 -> None` data-quality handling | 0 |
| US DXY supplement | 0-1 per US day |
| VIX fallback | 0-1 per US day only on primary miss |
| KR VKOSPI supplement | 0-1 per KR day |
| Preopen current seed-only collector | 0 |

P0 must not introduce repeated intraday scanning or per-candidate quote loops.

## Stage 1. Documentation Baseline

Create this plan before code edits and use it as the final comparison baseline.

Acceptance:

- The plan lists implementation scope.
- The plan lists verification and QA checks.
- The plan explicitly separates P0 from future P1 premarket/provider work.

## Stage 2. Supplement Data Quality

Files:

- `phase1_trainer/supplement_collector.py`

Required changes:

- Initialize risk/macro fields as `None`, not `0`.
- Return `None` on failed VIX/USD-KRW/DXY/VKOSPI collection.
- Add `data_quality_flags`.
- Add `sources`.
- Add `collected_at`.
- Add `fallback_used`.
- Add DXY collection with a low-call fallback path.
- Add VKOSPI collection for KR supplement as a daily lookup.

Rules:

- `0` is invalid for VIX, DXY, VKOSPI, USD/KRW, and oil unless explicitly allowed.
- A failed collection stores `None` and a quality flag.
- Existing files are not rewritten automatically unless a backfill script is run separately.

Acceptance:

- New supplement JSON can represent missing values as `null`.
- Missing values have explicit quality flags.
- No retry loop or schedule frequency increase is introduced.

## Stage 3. Digest Breadth Persistence

Files:

- `phase1_trainer/digest_builder.py`

Required changes:

- Ensure `breadth_summary` is JSON-safe before writing.
- Persist `breadth_summary` in KR and US digest files.
- Keep calculation based on existing `technicals/context`; no new API calls.
- Add a write-time serialization guard so future numpy/pandas scalar leaks do not break digest output.

Acceptance:

- Building a digest writes a top-level `breadth_summary` key.
- `breadth_summary.data_quality_flags` marks missing VIX/DXY/VKOSPI.
- JSON serialization succeeds with numpy/pandas scalar-like values.

## Stage 4. Consumers Of Risk Context

Files:

- `strategy/cross_asset.py`
- `telegram_reporter.py`
- selected `trading_bot.py` risk display paths

Required changes:

- Internal numeric calculations may coerce missing values to `0` only after preserving unknown semantics.
- User-facing/reporting paths must show unknown/missing as `N/A`, not `0`.
- Cross-asset risk labels should keep returning `VIX=unknown` / `VKOSPI=unknown`.

Acceptance:

- Missing VIX/DXY/VKOSPI does not appear as a stable low-risk numeric value.
- Existing risk gates still skip risk adjustment when values are missing.

## Stage 5. Focused Verification

Run focused tests:

- `python -m pytest tests/test_market_breadth_prompt_contract.py`
- New or updated supplement/data-quality tests if added.
- Import/compile checks for changed modules.

Manual checks:

- Generate or simulate a digest and verify top-level `breadth_summary`.
- Verify sample supplement payload uses `null` for failed values.
- Verify no preopen provider calls were added.

## Stage 6. Full Relevant QA

Run broader checks that are realistic for this repo state:

- `python -m pytest tests/test_market_breadth_prompt_contract.py tests/test_preopen_shadow.py tests/test_shadow_audit.py`
- `python -m py_compile phase1_trainer/supplement_collector.py phase1_trainer/digest_builder.py strategy/cross_asset.py telegram_reporter.py trading_bot.py`

If full suite is too slow or blocked, record the exact blocker.

## Stage 7. Plan Comparison And Gap Closure

After implementation and QA:

- Compare completed changes against this MD.
- List any missing acceptance item.
- Fix small omissions immediately.
- Defer larger P1/P2 items explicitly instead of mixing them into P0.

## Explicitly Out Of P0

- US premarket provider adapter.
- Repeated premarket ranking scans.
- Per-candidate quote loops.
- Live gate changes based on premarket movement.
- Restoring or migrating `decisions.db` history.
- New hard gates except missing/stale/spread handling already present elsewhere.

## Final Completion Criteria

P0 is complete when:

- `0` no longer means missing risk/macro data in new supplement output.
- New digest output includes JSON-safe `breadth_summary`.
- Missing risk values are visible as missing in prompts/reports.
- Focused tests and compile checks pass.
- This document is compared against the implemented changes and no P0 acceptance gap remains.

## Implementation Result

Completed on: 2026-05-02

Implemented files:

- `phase1_trainer/supplement_collector.py`
- `phase1_trainer/digest_builder.py`
- `strategy/cross_asset.py`
- `telegram_reporter.py`
- `trading_bot.py`
- `tests/test_p0_data_quality.py`

Verification run:

- `python -m py_compile phase1_trainer\supplement_collector.py phase1_trainer\digest_builder.py strategy\cross_asset.py telegram_reporter.py trading_bot.py`
- `python -m pytest tests\test_p0_data_quality.py tests\test_market_breadth_prompt_contract.py tests\test_preopen_shadow.py tests\test_shadow_audit.py -q`
- `python -m pytest -q`
- Digest serialization simulation using existing `2026-05-01_US.json` technical/context data.

Verification result:

- Compile checks passed.
- Relevant pytest checks passed: 25 passed, 2 third-party deprecation warnings.
- Full repo pytest passed: 481 passed, 2 skipped, 2 third-party deprecation warnings.
- Digest simulation wrote a top-level `breadth_summary` with `universe_count=33`.

Full repo pytest follow-up:

- An intermediate full-suite run showed 7 broker/token-related failures, so those tests were isolated and rerun.
- The 7 isolated tests passed after current token test fixtures were confirmed to provide `bot.tokens` for both KR and US.
- A subsequent full-suite rerun passed cleanly.
- The worktree already contained unrelated broker/token/live-guardian edits; those were preserved and not reverted.

Plan comparison:

| Plan Item | Status | Notes |
|---|---|---|
| Supplement `0 -> None` | Done | New KR/US supplement payloads initialize risk/macro metrics as `None`. |
| `source`, `collected_at`, `fallback_used` | Done | Stored in new supplement payloads. |
| `data_quality_flags` | Done | Missing VIX/DXY/VKOSPI/USD-KRW/oil flags are explicit. |
| DXY collection | Done | Added yfinance daily DXY lookup. |
| VKOSPI collection | Done | Added yfinance daily VKOSPI lookup. |
| JSON-safe breadth summary | Done | Added numpy/pandas scalar normalization before digest write. |
| Missing supplement must not suppress live fallback | Done | Missing positive metrics no longer override live context values. |
| Consumer display must not show missing risk as zero | Done | Cross-asset, Telegram briefing, and selected bot display paths now preserve unknown semantics. |
| Preopen/provider API changes | Not included | Intentionally deferred; P0 keeps preopen seed-only and adds no repeated scans. |
| `decisions.db` restore/migration | Not included | Confirmed out of P0 scope; should be handled as a separate data recovery task. |

Remaining follow-up:

- Historical operational supplement/digest files for `2026-03-19` through `2026-05-02` were backfilled on 2026-05-03.
- Backfill details, backup manifest path, and QA results are recorded in `docs/plans/p0_followup_backfill_ops_dev_20260503.md`.
- Backfill follow-up full-suite QA passed: 492 passed, 2 skipped, 2 third-party deprecation warnings.
- Older daily digest files from `2024-10-01` through `2026-01-01` still have empty `technicals: {}` and were not changed because breadth cannot be computed without rebuilding historical inputs.
- Existing unrelated worktree edits in `trading_bot.py` were preserved and not reverted.
