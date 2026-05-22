# Documentation Index

Updated: 2026-05-22

## Policy

- `docs/important/ACTIVE_WORK.md` is the single active work ledger.
- `docs/important/ALWAYS_ANALYZE.md` is the recurring decision checklist.
- `docs/important/core/` keeps durable references.
- `docs/important/source/` keeps current source evidence only.
- Raw dated reports and one-off plans should be summarized, then removed.

## Reading Order

1. [../README.md](../README.md) for the curated document map.
2. [TODO_ROADMAP.md](TODO_ROADMAP.md) for the active backlog snapshot.
3. [../ACTIVE_WORK.md](../ACTIVE_WORK.md) for current implementation/review actions.
4. [../ALWAYS_ANALYZE.md](../ALWAYS_ANALYZE.md) before market, live, prompt, learning, or dashboard policy changes.
5. [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md) when code ownership or runtime flow is unclear.
6. [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md) for the remaining docs tree.

## Classification

| Class | Location | Rule |
| --- | --- | --- |
| Core reference | `docs/important/core/` | Architecture, process, active-roadmap snapshot, completed-work summary, and research references. |
| Active work | `docs/important/ACTIVE_WORK.md` | Only unfinished work, with reason and completion condition. |
| Recurring analysis | `docs/important/ALWAYS_ANALYZE.md` | Checks that must be applied repeatedly before decisions. |
| Current evidence | `docs/important/source/` | Source documents still needed for a current implementation or policy decision. |
| Archive | `docs/archive/` | Old devlogs only. |
| Removed artifacts | deleted | Completed reports, repeated simulations, stale plans, and raw JSON outputs. |

## Maintenance Rule

When a new plan/report appears:

1. Extract its active work into `ACTIVE_WORK.md`.
2. Extract recurring rules into `ALWAYS_ANALYZE.md`.
3. Keep the raw file in `source/` only if it is still needed as evidence.
4. Delete one-off completed reports after summarizing them.
