# P0 Follow-Up Backfill And Ops Verification Plan

Date: 2026-05-03
Status: Completed

## Purpose

P0 code and QA are complete. This plan lists the remaining operational work needed to make existing local data match the new P0 behavior.

Do not mix this work with P1 premarket provider work or `decisions.db` recovery.

## Current State

Completed:

- New supplement output uses `null` instead of `0` for missing VIX/DXY/VKOSPI/USD-KRW style metrics.
- New supplement output records `collected_at`, `sources`, `fallback_used`, and `data_quality_flags`.
- New digest output writes JSON-safe `breadth_summary`.
- KR/US risk display paths preserve unknown semantics.
- Full pytest passed: 481 passed, 2 skipped.

Remaining:

- Historical supplement files still contain old `0` values.
- Historical daily digest files can still be missing `breadth_summary`.
- Operational generation needs one live/dry-run verification after the next scheduled run.
- `decisions.db` has only 6 `decisions` rows and must be handled separately.

## Operating Constraints

This task is a data hygiene operation, not a new collector rollout.

Hard constraints:

- Default command must be dry-run.
- Write mode must require `--write`.
- Network calls must never happen unless `--online-refresh` is also provided.
- Backup must complete before any mutation.
- Existing valid historical values must be preserved.
- No trading state, orders, selections, or prompt behavior should be changed by the backfill script.
- Generated backup files must not be tracked by git.

Value validity rules:

| Field | Valid Offline Range | Invalid Values To Null |
|---|---:|---|
| `vix` | `> 0` | missing, non-numeric, `<= 0` |
| `dxy` | `> 0` | missing, non-numeric, `<= 0` |
| `vkospi` | `> 0` | missing, non-numeric, `<= 0` |
| `oil_wti` | `> 0` | missing, non-numeric, `<= 0` |
| `usd_krw` | `100 <= value <= 3000` | missing, non-numeric, `<= 0`, `< 100`, `> 3000` |

Rationale:

- `0` is never a valid value for these risk/macro inputs in this system.
- USD/KRW below `100` is a unit/source error.
- USD/KRW above `3000` is invalid for this operational script. If that range ever becomes plausible, the threshold should be changed deliberately.

## Development List

### 0. Gitignore And Safety Preflight

Before implementing the script, verify runtime output paths are ignored by git.

Required checks:

- `.gitignore` includes `data/backups/`.
- `.gitignore` already ignores `logs/`.
- `git status --short` does not show backup output after a local test.

Acceptance:

- `data/backups/` is ignored before any backup write path is implemented.
- A script-created backup cannot be accidentally staged unless explicitly forced.

### 1. Backfill Audit Script

Add a dry-run first script.

Proposed file:

- `tools/p0_data_quality_backfill.py`

Responsibilities:

- Scan `data/supplement/us/*.json`.
- Scan `data/supplement/kr/*.json`.
- Scan `data/daily_digest/*.json`.
- Support `--from YYYY-MM-DD` and `--to YYYY-MM-DD`.
- Support `--market KR|US|ALL`, default `ALL`.
- Support `--json` for machine-readable summary output.
- Report:
  - US supplement files with `vix == 0`, `dxy == 0`, `oil_wti == 0`.
  - KR supplement files with `vkospi == 0`, invalid `usd_krw`.
  - digest files missing `breadth_summary`.
  - digest files where `breadth_summary` is not JSON-serializable.
- Default mode must be dry-run.

Acceptance:

- Running without flags changes no files.
- Outputs file counts and exact paths.
- Produces a summary JSON under `logs/p0_backfill/`.
- Summary separates `needs_change`, `already_clean`, `parse_error`, and `skipped_by_date_range`.

### 2. Safe Backup Before Mutation

Before writing any historical data, create timestamped backups.

Proposed backup location:

- `data/backups/p0_backfill_YYYYMMDD_HHMMSS/supplement/...`
- `data/backups/p0_backfill_YYYYMMDD_HHMMSS/daily_digest/...`

Rules:

- Back up only files that will be changed.
- Preserve directory structure.
- Write a manifest:
  - original path
  - backup path
  - SHA256 before
  - SHA256 after, once written
  - intended mutation type
  - changed fields
  - timestamp
  - script args

Acceptance:

- Backfill refuses to run write mode unless backup succeeds.
- Manifest can be used to restore individual files manually.
- Manifest itself is written under the ignored backup directory and copied to `logs/p0_backfill/` as a summary only.

### 3. Offline Supplement Normalization

Mutation mode should first do offline normalization only.

US supplement:

- `vix: 0` -> `null`
- `dxy: 0` -> `null`
- `oil_wti: 0` -> `null`
- add missing:
  - `collected_at`
  - `sources`
  - `fallback_used`
  - `data_quality_flags`
  - `collection_errors`
- add flags:
  - `vix_missing`
  - `dxy_missing`
  - `oil_wti_missing`

KR supplement:

- `vkospi: 0` -> `null`
- invalid `usd_krw` values -> `null`
  - invalid means missing, non-numeric, `<= 0`, `< 100`, or `> 3000`
- add missing metadata fields.
- add flags:
  - `vkospi_missing`
  - `usd_krw_missing` only if invalid/missing.

Rules:

- Do not fetch network data in offline normalization.
- Do not infer historical values.
- Preserve existing valid values.
- Do not alter KR `flows` values in this backfill. Flow `0` can be a real net-flow value and needs a separate policy if we ever change it.
- Do not alter existing `date`, event flags, or unrelated fields.
- Add metadata only when missing; preserve existing `sources` and `fallback_used` entries unless the corresponding value is normalized to null.

Acceptance:

- Old `0` risk/macro placeholders become explicit nulls.
- Valid USD/KRW values are preserved.
- JSON formatting remains stable and readable.
- KR flow payloads are unchanged.

### 4. Optional Online Refresh Mode

Add later, behind an explicit flag.

Example:

```powershell
python tools/p0_data_quality_backfill.py --write --online-refresh --from 2026-04-25 --to 2026-05-02
```

Allowed fetches:

- DXY daily close.
- VKOSPI daily close.
- VIX fallback if missing.

Constraints:

- Must use daily call budget.
- Must sleep between provider calls.
- Must log provider, status, and fallback.
- Must not run by default.
- Must skip files outside explicit `--from/--to`.
- Must stop before exceeding configured call budget.
- Must support `--max-calls N`, default conservative.

Acceptance:

- Offline backfill works without network.
- Online refresh is opt-in and bounded by date range.
- Online refresh output records every provider call in `logs/p0_backfill/`.

### 5. Daily Digest Breadth Backfill

For each `data/daily_digest/YYYY-MM-DD_{KR,US}.json`:

- Load existing `context`.
- Load existing `technicals`.
- If `breadth_summary` missing, compute with `build_breadth_summary(market, technicals, context)`.
- If present, optionally verify JSON-safe serialization.
- Write only if changed.

Rules:

- No external API calls.
- Do not rebuild the entire digest.
- Do not change `top_news`, `technicals`, or `prev_result`.
- Do not overwrite an existing non-empty `breadth_summary` unless `--refresh-breadth` is explicitly passed.
- If `technicals` is empty or missing, record `digest_technicals_missing` and leave the file unchanged.
- If context has old placeholder zeros, `build_breadth_summary()` may emit data quality flags; do not mutate context in this step.

Acceptance:

- Historical digest files gain top-level `breadth_summary`.
- `digest_to_prompt()` can read morning breadth from old files.
- Backfilled summary includes current `data_quality_flags` based on existing context.

### 6. Backfill Verification

Add script verification mode:

```powershell
python tools/p0_data_quality_backfill.py --verify
```

Checks:

- No supplement risk/macro placeholder `0` remains for:
  - US: VIX, DXY, oil WTI
  - KR: VKOSPI
- No invalid USD/KRW remains in KR/US supplement files according to `100 <= value <= 3000`.
- No daily digest JSON is missing `breadth_summary`.
- All changed files parse as JSON.
- All changed digest files serialize with `json.dumps`.
- Backup manifest exists for write runs.
- `git status --short -- data/backups logs/p0_backfill` does not show backup files.

Acceptance:

- Verification exits `0` when clean.
- Verification exits non-zero with path list when gaps remain.

### 7. Operational Generation Verification

After the next scheduled or manual generation:

KR:

- Run or wait for KR supplement generation.
- Run or wait for KR digest generation.
- Check:
  - new KR supplement has `collected_at`.
  - `vkospi` is positive or `null` with `vkospi_missing`.
  - new KR digest has `breadth_summary`.

US:

- Run or wait for US supplement generation.
- Run or wait for US digest generation.
- Check:
  - new US supplement has `vix`, `dxy`, `oil_wti` as positive/null, not placeholder `0`.
  - new US digest has `breadth_summary`.

Acceptance:

- New files match P0 contract without manual patching.
- No unexpected API scan loop is introduced.
- API call count remains consistent with P0 budget: daily DXY/VKOSPI/VIX fallback only, no repeated scan behavior.

### 8. QA After Backfill

Run:

```powershell
python -m py_compile tools\p0_data_quality_backfill.py phase1_trainer\supplement_collector.py phase1_trainer\digest_builder.py
python -m pytest tests\test_p0_data_quality.py tests\test_market_breadth_prompt_contract.py -q
python tools\p0_data_quality_backfill.py --verify
python -m pytest -q
```

Acceptance:

- Focused tests pass.
- Backfill verify passes.
- Full pytest remains clean or any failure is triaged with exact cause.
- `git status --short` is reviewed so generated backups/logs are not accidentally included.

### 9. Documentation Update

Update:

- `docs/plans/p0_data_quality_breadth_qa_20260502.md`
- this plan file

Record:

- dry-run counts.
- write-run changed file counts.
- backup path.
- `.gitignore` status for `data/backups/`.
- verification result.
- full pytest result.
- any skipped files and reason.

Acceptance:

- MD reflects actual final state.
- Any deferred item is explicit.

## Deferred Work

### A. `decisions.db` Recovery

Handle separately.

Questions:

- Is current 6-row `data/ml/decisions.db` intentional?
- Should the 21,893-row backup be restored, merged, or archived?
- Should `param_sessions` be preserved from the current DB?

Do not combine with backfill.

### B. P1 Premarket Provider Adapter

Handle separately.

Do not add:

- repeated scans,
- provider adapters,
- quote loops,
- live prompt exposure,
- watch priority changes

inside this backfill task.

## Recommended Execution Order

1. Confirm `.gitignore` covers `data/backups/`.
2. Implement dry-run audit script.
3. Run dry-run and record counts.
4. Implement backup + offline write mode.
5. Run write mode on historical supplement/digest files.
6. Run verify mode.
7. Run focused tests.
8. Run full pytest.
9. Review `git status --short`.
10. Update MD with actual counts/results.
11. Then decide whether to start `decisions.db` recovery.

## Implementation Result

Completed on: 2026-05-03

Implemented files:

- `tools/p0_data_quality_backfill.py`
- `tests/test_p0_data_quality_backfill.py`
- `docs/plans/p0_followup_backfill_ops_dev_20260503.md`
- `docs/plans/p0_data_quality_breadth_qa_20260502.md`

Additional QA contract fixes made while closing the full suite:

- `telegram_reporter.py`
- `telegram_commander.py`
- `interface/v2_telegram.py`

Backfill scope:

- Date range: `2026-03-19` through `2026-05-02`
- Markets: `ALL`
- Network/API calls: none
- Online refresh: not implemented and explicitly blocked by `online_refresh_not_implemented`

Why the date range was used:

- A full dry-run found 328 older daily digest files from `2024-10-01` through `2026-01-01` with `technicals: {}`.
- Those files cannot produce a meaningful breadth summary without rebuilding historical inputs.
- Per this plan, digest files with missing/empty technicals were left unchanged instead of inventing breadth data.

Dry-run result:

- Command: `python tools\p0_data_quality_backfill.py --from 2026-03-19 --to 2026-05-02`
- Summary: `logs/p0_backfill/p0_backfill_20260503_003336_dry_run.json`
- Scanned: 477
- Needs change: 142
- Supplement needs change: 86
- Daily digest needs change: 56
- Skipped by date range: 335
- Parse errors: 0
- Blocked: 0

Write result:

- Command: `python tools\p0_data_quality_backfill.py --write --from 2026-03-19 --to 2026-05-02`
- Summary: `logs/p0_backfill/p0_backfill_20260503_003351_write.json`
- Changed: 142
- Supplement changed: 86
- Daily digest changed: 56
- Parse errors: 0
- Blocked: 0
- Backup manifest: `data/backups/p0_backfill_20260503_003351/manifest.json`
- Manifest entries: 142

Verification result:

- Command: `python tools\p0_data_quality_backfill.py --verify --from 2026-03-19 --to 2026-05-02`
- Summary: `logs/p0_backfill/p0_backfill_20260503_003909_verify.json`
- Verified: 142
- Verify errors: 0
- Parse errors: 0
- Blocked: 0

Post-write idempotency result:

- Command: `python tools\p0_data_quality_backfill.py --from 2026-03-19 --to 2026-05-02`
- Summary: `logs/p0_backfill/p0_backfill_20260503_003851_dry_run.json`
- Needs change: 0
- Already clean: 142

Gitignore verification:

- `.gitignore` includes `data/backups/`.
- `.gitignore` already includes `logs/`.
- `git status --short -- data/backups logs/p0_backfill` produced no output after the write and verify runs.

QA result:

- `python -m py_compile tools\p0_data_quality_backfill.py phase1_trainer\supplement_collector.py phase1_trainer\digest_builder.py strategy\cross_asset.py telegram_reporter.py trading_bot.py` passed.
- `python -m pytest tests\test_p0_data_quality_backfill.py tests\test_p0_data_quality.py tests\test_market_breadth_prompt_contract.py tests\test_preopen_shadow.py tests\test_shadow_audit.py -q` passed: 30 passed, 2 third-party deprecation warnings.
- `python -m pytest tests\test_telegram_positions.py tests\test_telegram_path_labels.py tests\test_pathb_control.py tests\test_v2_phase6.py -q` passed after restoring expected compatibility tokens in Telegram/V2 text.
- `python -m pytest -q` passed: 492 passed, 2 skipped, 2 third-party deprecation warnings.

Plan comparison:

| Plan Item | Status | Notes |
|---|---|---|
| `.gitignore` safety | Done | `data/backups/` and `logs/` are ignored. |
| Dry-run audit script | Done | Default command does not mutate tracked data files. |
| Backup before mutation | Done | Changed files are copied before writes; manifest records SHA256 before/after. |
| Offline supplement normalization | Done | US VIX/DXY/oil WTI and KR USD-KRW/VKOSPI placeholders are converted to `null`. |
| KR flows untouched | Done | Covered by unit test. |
| Online refresh mode | Deferred | Flag is accepted but returns an explicit unsupported status; no API calls are introduced. |
| Daily digest breadth backfill | Done | 56 operational digest files gained top-level `breadth_summary`. |
| Backfill verification | Done | Verify mode passed for the backfilled operational range. |
| Idempotent rerun | Done | Post-write dry-run reports `needs_change=0`. |
| Operational generation verification | Deferred | Requires the next scheduled/manual KR/US generation run. |
| QA after backfill | Done | Focused and full pytest passed. |
| Documentation update | Done | This section records counts, backup path, and deferred items. |
