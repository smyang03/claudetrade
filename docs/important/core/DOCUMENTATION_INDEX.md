# Documentation Index

Updated: 2026-06-02

## Policy

- `docs/important/ACTIVE_WORK.md` is the single active work ledger, ordered by 수익성/운영/버그/데이터베이스 priority with before/after acceptance.
- `docs/important/ALWAYS_ANALYZE.md` is the recurring decision checklist.
- `docs/important/core/` keeps durable references.
- `docs/important/source/` keeps current source evidence only.
- Raw dated reports, generated simulations, QA notes, and one-off plans should be summarized, then removed.
- Do not use `docs/plans/` or `docs/reports/` as an active backlog; absorb remaining items into `ACTIVE_WORK.md` and `TODO_ROADMAP.md`.

## Reading Order

1. [../README.md](../README.md) for the curated document map.
2. [TODO_ROADMAP.md](TODO_ROADMAP.md) for the active backlog snapshot.
3. [../ACTIVE_WORK.md](../ACTIVE_WORK.md) for current implementation/review actions.
4. [../P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md](../P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md) for detailed P0/P1 code-level development requirements.
5. [../P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md](../P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md) for the latest code-level recheck and remaining dev order.
6. [../STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md](../STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md) for code-level strategy flow audit, missing handoff checks, and per-item performance metrics.
7. [../STRATEGY_FLOW_AUDIT_REVIEW_20260602.md](../STRATEGY_FLOW_AUDIT_REVIEW_20260602.md) for the latest DB/log-backed strategy flow review and improvement priorities.
8. [../ALWAYS_ANALYZE.md](../ALWAYS_ANALYZE.md) before market, live, prompt, learning, or dashboard policy changes.
9. [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md) when code ownership or runtime flow is unclear.
10. [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md) for the remaining docs tree.

## Classification

| Class | Location | Rule |
| --- | --- | --- |
| Core reference | `docs/important/core/` | Architecture, process, active-roadmap snapshot, completed-work summary, and research references. |
| Active work | `docs/important/ACTIVE_WORK.md` | Only unfinished work, ordered by 수익성/운영/버그/데이터베이스 priority and live risk, with before/after comparison per item. |
| Recurring analysis | `docs/important/ALWAYS_ANALYZE.md` | Checks that must be applied repeatedly before decisions. |
| Current evidence | `docs/important/source/` | Source documents still needed for a current implementation or policy decision. |
| Archive | `docs/archive/` | Old devlogs only. |
| Removed artifacts | deleted | Completed reports, repeated simulations, stale plans, raw JSON outputs, and QA notes after summarization. |

## Maintenance Rule

When a new plan/report appears:

1. Extract unfinished work into `ACTIVE_WORK.md`.
2. Extract recurring rules into `ALWAYS_ANALYZE.md`.
3. Keep the raw file in `source/` only if it is still needed as evidence.
4. Delete one-off completed reports after summarizing them.
