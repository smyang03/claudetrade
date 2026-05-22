# Docs Cleanup Report

Date: 2026-05-22

## What Changed

- Created `docs/important/` as the curated documentation area.
- Moved durable references into `docs/important/core/`.
- Moved current source evidence into `docs/important/source/`.
- Replaced the root docs hub with a short pointer to the curated set.
- Removed `docs/plans/` after absorbing remaining actions into active work.
- Removed `docs/reports/` after preserving current lessons in summaries or retained source evidence.

## What Remains

| Area | Purpose |
| --- | --- |
| `docs/README.md` | Entry point only. |
| `docs/important/README.md` | Curated title summary and reading order. |
| `docs/important/ACTIVE_WORK.md` | Current todo and active implementation/review items. |
| `docs/important/ALWAYS_ANALYZE.md` | Recurring analysis checklist for market, truth, prompt, and runtime safety decisions. |
| `docs/important/core/` | Durable architecture, process, developed work, roadmap, and research docs. |
| `docs/important/source/` | Current evidence reports that should not be deleted yet. |
| `docs/archive/` | Old devlogs retained as archive. |

## Removed Buckets

- Dated simulation outputs.
- Profitability review repetitions.
- Live improvement simulation repetitions.
- Market policy review repetitions.
- Web-inspired policy search repetitions.
- QA smoke outputs that only proved an already completed patch.
- JSON artifacts paired with reports.
- Completed or absorbed plan files.

## Retained Source Evidence

- US KIS ranking screener requirements.
- Live config safety requirements.
- Operational DB truth code recheck.
- KR/US market index watch set.
- Claude misjudgment review.
- KR/US policy action review.
- KR selection/execution trace.
- Momentum shadow final judgment data.
- Prompt overlay later-data plan.
- Candidate pipeline root-cause review.
- KR/US DB reanalysis.

## Cleanup Rule Going Forward

- New active work goes into `docs/important/ACTIVE_WORK.md`.
- Recurring decision criteria go into `docs/important/ALWAYS_ANALYZE.md`.
- A one-off report stays only if it is still current source evidence. Otherwise summarize it and delete the raw file.
- Do not create a new `docs/plans/` or `docs/reports/` backlog unless the owner explicitly asks for raw report retention.
