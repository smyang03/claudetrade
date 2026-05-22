# Documentation Inventory

Updated: 2026-05-22

## Remaining Structure

```text
docs/
  README.md
  archive/
  important/
    README.md
    ACTIVE_WORK.md
    ALWAYS_ANALYZE.md
    CLEANUP_REPORT_20260522.md
    CODE_LEVEL_REQUIREMENTS_20260522.md
    GIT_CODE_REVIEW_PLAN_20260522.md
    NOW_CODE_REQUIREMENTS_20260522.md
    core/
    source/
  plans/
    intraday_evidence_alignment_followup_20260522.md
    NOW_RECHECK_RISK_ANALYSIS_20260522.md
  reports/
    sub_screener_live_observation_20260522.md
```

## Core Documents

| File | Purpose |
| --- | --- |
| `ARCHITECTURE_MAP.md` | Runtime and storage map. |
| `TODO_ROADMAP.md` | Active backlog snapshot. |
| `DEVELOPED_WORK.md` | Completed work summary. |
| `DOCUMENTATION_INDEX.md` | Cleanup and reading rules. |
| `DOCUMENTATION_INVENTORY.md` | This inventory. |
| `trading_process.md` | Trade process reference. |
| `rsi_threshold_research.md` | Strategy research note. |
| `claude_selection_compact_output_report_20260512.md` | Claude selection output reference. |

## Immediate Analysis Documents

| File | Purpose |
| --- | --- |
| `NOW_CODE_REQUIREMENTS_20260522.md` | Immediate development requirements only. |
| `docs/plans/NOW_RECHECK_RISK_ANALYSIS_20260522.md` | Code-level recheck list, before/after state, quality impact, and risk mitigation for the immediate work. |

## Source Evidence Documents

| File | Why Retained |
| --- | --- |
| `kr_confirmation_fade_recovery_dev_requirements_20260522.md` | Completed KR-only confirmation data_quality bug fix and fade-recovered shadow source evidence. |
| `us_kis_ranking_screener_requirements_20260522.md` | Active US screener implementation requirements. |
| `live_config_safety_code_requirements_20260521.md` | Live config and PathB gate safety requirements. |
| `operational_db_code_recheck_20260521.md` | Operational DB truth and canonical performance evidence. |
| `market_index_watch_set_20260522.md` | Market index watch set for KR/US regime analysis. |
| `claude_misjudgments_20260520.md` | Claude selection error bucket evidence. |
| `kr_us_policy_action_review_20260520.md` | KR/US policy action review evidence. |
| `kr_selection_execution_trace_20260520.md` | KR selection versus execution trace evidence. |
| `momentum_shadow_final_judgment_data_20260520.md` | Momentum shadow judgment evidence. |
| `prompt_overlay_later_data_plan_20260520.md` | Prompt overlay shadow-to-live gate evidence. |
| `candidate_pipeline_root_cause_review_20260519.md` | Candidate pipeline root-cause evidence. |
| `codex_kr_us_db_reanalysis_20260519.md` | KR/US DB reanalysis evidence. |

## Legacy Cleanup Note

Most old dated plan/report files were removed. Current retained `docs/plans/` and `docs/reports/` files are narrow follow-up or observation records; recurring action and judgment content should still be represented in `ACTIVE_WORK.md`, `ALWAYS_ANALYZE.md`, or retained under `source/`.
