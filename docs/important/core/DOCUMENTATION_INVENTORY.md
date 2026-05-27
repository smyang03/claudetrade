# Documentation Inventory

Updated: 2026-05-27

## Remaining Structure

```text
docs/
  README.md
  archive/
  important/
    README.md
    ACTIVE_WORK.md
    ALWAYS_ANALYZE.md
    ANALYST_OUTAGE_HANDLING_REQUIREMENTS_20260522.md
    CLEANUP_REPORT_20260522.md
    CODE_LEVEL_REQUIREMENTS_20260522.md
    NOW_CODE_REQUIREMENTS_20260522.md
    P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md
    P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md
    core/
    source/
```

`docs/plans/` and `docs/reports/` are intentionally removed after the 2026-05-27 plan cleanup. Active work must be represented in `ACTIVE_WORK.md` and `core/TODO_ROADMAP.md`.

## Core Documents

| File | Purpose |
| --- | --- |
| `ARCHITECTURE_MAP.md` | Runtime and storage map. |
| `TODO_ROADMAP.md` | Compact active backlog snapshot. |
| `DEVELOPED_WORK.md` | Completed work summary. |
| `DOCUMENTATION_INDEX.md` | Cleanup and reading rules. |
| `DOCUMENTATION_INVENTORY.md` | This inventory. |
| `trading_process.md` | Trade process reference. |
| `rsi_threshold_research.md` | Strategy research note. |
| `claude_selection_compact_output_report_20260512.md` | Claude selection output reference. |

## Active Status Documents

| File | Purpose |
| --- | --- |
| `ACTIVE_WORK.md` | Remaining work only, ordered by 수익성/운영/버그/데이터베이스 priority with before/after comparison. |
| `ALWAYS_ANALYZE.md` | Recurring safety/analysis checklist. |
| `NOW_CODE_REQUIREMENTS_20260522.md` | Immediate code requirements after plan cleanup. |
| `P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md` | Detailed P0/P1 code-level development requirements with why/before/after/acceptance/tests. |
| `P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md` | Latest code-level recheck with complete/partial/missing status and remaining development order. |
| `CODE_LEVEL_REQUIREMENTS_20260522.md` | Code-level judgment and remaining requirements. |
| `ANALYST_OUTAGE_HANDLING_REQUIREMENTS_20260522.md` | Source requirement for analyst outage handling; not the active backlog. |

## Source Evidence Documents

| File | Why Retained |
| --- | --- |
| `kr_confirmation_fade_recovery_dev_requirements_20260522.md` | Completed KR-only confirmation data_quality bug fix and fade-recovered shadow source evidence. |
| `us_kis_ranking_screener_requirements_20260522.md` | KIS US ranking endpoint/fallback source evidence; shadow implemented, primary deferred. |
| `live_config_safety_code_requirements_20260521.md` | Live config and PathB gate safety requirements. |
| `operational_db_code_recheck_20260521.md` | Operational DB truth and canonical performance evidence. |
| `market_index_watch_set_20260522.md` | Market index watch set for KR/US regime analysis. |
| `claude_misjudgments_20260520.md` | Claude selection error bucket evidence. |
| `kr_us_policy_action_review_20260520.md` | KR/US policy action review evidence. |
| `kr_selection_execution_trace_20260520.md` | KR selection versus execution trace evidence. |
| `momentum_shadow_final_judgment_data_20260520.md` | Momentum shadow judgment evidence. |
| `candidate_pipeline_root_cause_review_20260519.md` | Candidate pipeline root-cause evidence. |
| `codex_kr_us_db_reanalysis_20260519.md` | KR/US DB reanalysis evidence. |
| `debate_watchdog_safety_dev_requirements_20260525.md` | Debate/watchdog safety source requirement. |
| `debate_watchdog_change_summary_20260525.md` | Debate/watchdog change summary retained as source evidence. |
| `pathb_ttl_profit_cleanup_followup_plan_20260527.md` | PathB pending-buy TTL/order matching follow-up evidence; commit/QA and monitoring remain active. |

## Cleanup Note

One-off plan/report content should be summarized, then removed. Keep raw dated reports only when the owner explicitly needs original evidence; otherwise move active lessons to `ACTIVE_WORK.md`, recurring rules to `ALWAYS_ANALYZE.md`, and source evidence to `important/source/`.
