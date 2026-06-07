# Developed Work

Updated: 2026-06-07

Completed work is summarized here so it does not reappear as active work. An item is treated as complete only when a commit exists and the current code path still contains the operating behavior. Working-tree-only implementations remain in `ACTIVE_WORK.md` until reviewed, tested, and committed.

## Commit-Verified Completed Work

| Area | Commit evidence | Current code/ops judgment | Active-work result |
| --- | --- | --- | --- |
| Actual prompt/audit count base | `f6a1390`, `a785741`, `b45eaf4` | Candidate audit and selection paths have prompt count/trace foundations; current working tree adds more `actual_prompt_*` and score metadata. | Base is complete, but latest-cycle actual prompt profit verification remains active. |
| KR `minute_complete` and `fade_recovered_shadow` | `6f8fdc1` | KR confirmation accepts `minute_complete` as an evidence-quality fix and emits `fade_recovered_shadow` as observation-only. | Removed from active implementation; KR confirmation promotion remains observe-gated. |
| Analyst outage core safety | `469be29`, `59a8c26` | Unavailable/quorum/learning-exclusion paths exist and debate metadata uses actual stance comparison. | Core implementation removed from active; UI polish remains P2. |
| US projected dollar volume and KIS ranking shadow | `56ddbf4` | Shadow collector/observability exists without primary source promotion. | Implementation removed from active; primary promotion remains observe-gated. |
| Hold advisor duration and audit linkage baseline | `5484e6a`, `980cc16` | Duration/audit linkage instrumentation exists before any TTL cache or model-tiering change. | Baseline removed from active; TTL/cache remains deferred P2. |
| Live guardian / ensure-bot safety | `6c63668`, `b2a4adb`, `f218cc1` | dry-run, duplicate/PID checks, mode mismatch fix, and US KIS shadow token failure isolation are committed. | Removed from active unless a new freshness/visibility requirement is opened. |
| Dashboard KIS period_profit daily realized PnL | `5f83189` | Dashboard has KIS `period_profit` integration for daily realized PnL. | Removed from active backlog. |
| PathB gain_lock/protective hold base | `d8d7d5a` | gain_lock floor, protective-hold validity, and reask improvements are committed. | Removed from active; fill truth/remainder/EXPIRED monitoring remains separate. |
| Position duplicate entry / US stop FX / analyst retry broker exposure check | `fdf36c8` | Duplicate position filter, US stop price FX conversion, and analyst retry broker exposure check are committed. | Removed from active backlog. |
| Trade-ready slot override / profit-review timeout base | `e62302c`, `1ce38b3` | slot env override and 30s profit-review timeout/fallback behavior are committed. | Base removed; fallback visibility/exclusion guard tests remain active. |

## Absorbed And Deleted

Old detailed plans, raw simulation reports, QA notes, prompt-overlay analyses, generated JSON artifacts, and stale PathB live plan text were removed after their unfinished work was absorbed into [../ACTIVE_WORK.md](../ACTIVE_WORK.md) and [TODO_ROADMAP.md](TODO_ROADMAP.md). Use Git history for raw forensic detail if needed.

## Working-Tree Verified, Not Commit-Complete

These are implemented and test-covered in the current working tree, but remain outside commit-verified completion until commit/QA and operating dry-run issues are closed:

| Area | Working-tree evidence | Remaining active-work result |
| --- | --- | --- |
| KIS `EGW00133` token rate-limit core | `KISTokenRateLimitError`, classifier/cooldown marker, shared KR/US marker, cached-token preservation, startup fail-closed tests. | Keep only preflight/dashboard operator-visible status and environment QA in active P0. |
| PathB entry broker-truth dependency fail-closed | live token/provider unavailable returns `BLOCKED_BROKER_TRUTH`; unit and PathB regression tests pass. | Keep only TTL/attempt/latency/skip reason ops visibility in active P0. |
| KR `NO_SIGNAL` / ORP timing read-only report | `tools/kr_nosignal_orp_report.py`, `tests/test_kr_nosignal_orp_report.py`, and read-only live DB run reproduce recent/primary/full_available counts. | Implementation is no longer active coding work; report-output review remains in `ACTIVE_WORK.md`. |
| PathB `INVALID_PRICE` miss diagnostics read-only report | `tools/pathb_invalid_price_miss_report.py`, `tests/test_pathb_invalid_price_miss_report.py`, and read-only live DB run reproduce US `INVALID_PRICE n=29`, `zone_reentered=26`, `avg_mfe_30m_pct=1.2224`. | Implementation is no longer active coding work; remediation design remains in `ACTIVE_WORK.md` and must preserve PathB protected safety gates. |

## Not Counted Complete Yet

These have code or document evidence in the current working tree, but are not treated as completed because commit/QA/operating proof is not yet sufficient:

- Candidate audit `INVALID_PRICE` reason decomposition and broader bucket/source/data-quality propagation.
- `runtime/tuning_bounds.py` single-source bounds migration and non-override key cleanup.
- PathB pending-buy TTL actual sent/ACK timestamp, exact order matching, and cancel-request follow-up.
- US PathB sizing context and reason split for `ORDER_SIZE_TOO_SMALL_GATE` / `HIGH_PRICE_BUDGET_BLOCK`.
- KR sector-play confirmation gate log/test clarity.
- Latest KR/US actual prompt visibility verification against fresh DB cycles.
- Broker-truth zero-holding realistic KR/US fixture tests.
- PathB entry broker-truth gate ops visibility beyond the implemented fail-closed branch.
- V2 canonical freshness warning, Brain direct-write guard, sub-screener trigger visibility, and profit-review fallback aggregate tests.
- KIS token `EGW00133` operator-visible status beyond the implemented classifier/cooldown branch.
- Prompt overlay, US KIS ranking, raw-score, KR confirmation/WATCH_TRIGGER, KR first-entry, and exit-overlay live promotions.
