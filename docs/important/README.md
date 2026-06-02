# Important Document Index

Updated: 2026-06-02

## How To Read

1. Use [ACTIVE_WORK.md](ACTIVE_WORK.md) for the only active implementation/backlog ledger, now ordered by 수익성/운영/버그/데이터베이스 priority with before/after acceptance per item.
2. Use [ALWAYS_ANALYZE.md](ALWAYS_ANALYZE.md) before changing live behavior, market policy, learning data, prompt behavior, or dashboard truth.
3. Use [NOW_CODE_REQUIREMENTS_20260522.md](NOW_CODE_REQUIREMENTS_20260522.md) for the current immediate development scope.
4. Use [P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md](P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md) for detailed P0/P1 code-level development requirements.
5. Use [P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md](P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md) for the latest code-level implementation recheck and remaining dev order.
6. Use [CODE_LEVEL_REQUIREMENTS_20260522.md](CODE_LEVEL_REQUIREMENTS_20260522.md) for code-level status, remaining improvements, and acceptance gates.
7. Use [STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md](STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md) for the code-level strategy-flow audit requirements.
8. Use [STRATEGY_FLOW_AUDIT_REVIEW_20260602.md](STRATEGY_FLOW_AUDIT_REVIEW_20260602.md) for the latest DB/log-backed strategy-flow review and improvement priorities.
9. Use `core/` for durable architecture and operating references.
10. Use `source/` only when a decision needs the original detailed evidence.

One-off plan/report files have been absorbed into ACTIVE_WORK/TODO_ROADMAP/DEVELOPED_WORK and removed. Do not recreate `docs/plans/` or `docs/reports/` as active backlogs.

## Core

| Document | Title | Summary |
| --- | --- | --- |
| [core/ARCHITECTURE_MAP.md](core/ARCHITECTURE_MAP.md) | Architecture Map | Runtime map for Path A, Path B, broker truth, risk, dashboard, DB, and logs. |
| [core/TODO_ROADMAP.md](core/TODO_ROADMAP.md) | TODO Roadmap | Compact backlog snapshot; details live in ACTIVE_WORK. |
| [core/DEVELOPED_WORK.md](core/DEVELOPED_WORK.md) | Developed Work | Completed/absorbed work that is no longer an active plan. |
| [core/DOCUMENTATION_INDEX.md](core/DOCUMENTATION_INDEX.md) | Documentation Index | Current cleanup rules and reading order. |
| [core/DOCUMENTATION_INVENTORY.md](core/DOCUMENTATION_INVENTORY.md) | Documentation Inventory | Remaining docs tree and retained source list. |
| [core/trading_process.md](core/trading_process.md) | Trading Process | End-to-end trade flow reference. |
| [core/rsi_threshold_research.md](core/rsi_threshold_research.md) | RSI Research | RSI threshold research note retained as strategy reference. |
| [core/claude_selection_compact_output_report_20260512.md](core/claude_selection_compact_output_report_20260512.md) | Claude Selection Compact Output | Compact Claude selection output review retained for prompt/selection context. |

## Current Status Documents

| Document | Summary |
| --- | --- |
| [ACTIVE_WORK.md](ACTIVE_WORK.md) | Remaining work only, ordered by 수익성/운영/버그/데이터베이스 impact with per-item before/after comparison. |
| [ALWAYS_ANALYZE.md](ALWAYS_ANALYZE.md) | Recurring decision checks for broker truth, prompt gates, source quality, and runtime safety. |
| [NOW_CODE_REQUIREMENTS_20260522.md](NOW_CODE_REQUIREMENTS_20260522.md) | Immediate P0/P1 scope after commit/code cleanup: profit visibility, candidate data quality, KR entry/exit shadow, KIS token backoff, broker-truth fixtures, PathB visibility, PathB TTL/order matching, US PathB sizing reason split, runtime cleanup, and guard tests. |
| [P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md](P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md) | Detailed development requirements for P0/P1 items, including why, code targets, before/after, acceptance, and tests. |
| [P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md](P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md) | Latest code-level recheck of P0/P1 requirements against current implementation, with complete/partial/missing status and remaining development order. |
| [CODE_LEVEL_REQUIREMENTS_20260522.md](CODE_LEVEL_REQUIREMENTS_20260522.md) | Code-level judgment and before/after acceptance matrix across absorbed plans/reports. |
| [STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md](STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md) | Code-level audit requirements for strategy flow integrity, missing handoffs, live gate intent, and per-item performance metrics. |
| [STRATEGY_FLOW_AUDIT_REVIEW_20260602.md](STRATEGY_FLOW_AUDIT_REVIEW_20260602.md) | Latest read-only DB/log/code review against the strategy-flow audit requirements, with root-cause patterns and improvement priorities. |
| [ANALYST_OUTAGE_HANDLING_REQUIREMENTS_20260522.md](ANALYST_OUTAGE_HANDLING_REQUIREMENTS_20260522.md) | Historical/source requirement for analyst outage handling; core code is implemented, UI polish remains in ACTIVE_WORK. |

## Current Source Evidence

| Document | Category | Why It Remains |
| --- | --- | --- |
| [source/kr_confirmation_fade_recovery_dev_requirements_20260522.md](source/kr_confirmation_fade_recovery_dev_requirements_20260522.md) | Completed Source | KR-only confirmation data_quality bug fix and fade-recovered shadow requirements; retained for the shadow observation rule. |
| [source/us_kis_ranking_screener_requirements_20260522.md](source/us_kis_ranking_screener_requirements_20260522.md) | Source Evidence | KIS ranking endpoints and fallback requirements; shadow is implemented, primary promotion remains gated in ACTIVE_WORK. |
| [source/live_config_safety_code_requirements_20260521.md](source/live_config_safety_code_requirements_20260521.md) | Safety | `/setorder` fail-closed and PathB live gate policy source evidence. |
| [source/operational_db_code_recheck_20260521.md](source/operational_db_code_recheck_20260521.md) | Truth | V2 canonical performance is the better fill/performance truth; supports guardian freshness warning work. |
| [source/market_index_watch_set_20260522.md](source/market_index_watch_set_20260522.md) | Market Judgment | Defines KR/US market index watch set and shadow-first expansion rule. |
| [source/claude_misjudgments_20260520.md](source/claude_misjudgments_20260520.md) | Selection Quality | Captures Claude false-positive and false-negative review buckets. |
| [source/kr_us_policy_action_review_20260520.md](source/kr_us_policy_action_review_20260520.md) | Policy Judgment | Separates KR/US policy action performance before changing gates. |
| [source/kr_selection_execution_trace_20260520.md](source/kr_selection_execution_trace_20260520.md) | Trace | Traces KR selection versus execution so selection quality is not mixed with execution/risk failures. |
| [source/momentum_shadow_final_judgment_data_20260520.md](source/momentum_shadow_final_judgment_data_20260520.md) | Shadow | Momentum remains a shadow/evidence item until labeled sessions justify live use. |
| [source/candidate_pipeline_root_cause_review_20260519.md](source/candidate_pipeline_root_cause_review_20260519.md) | Candidate Pipeline | Root-cause review for candidate funnel quality and audit coverage. |
| [source/codex_kr_us_db_reanalysis_20260519.md](source/codex_kr_us_db_reanalysis_20260519.md) | DB Review | KR/US DB reanalysis that supports canonical performance and metric contract work. |
| [source/debate_watchdog_safety_dev_requirements_20260525.md](source/debate_watchdog_safety_dev_requirements_20260525.md) | Safety | Debate/watchdog safety requirements retained as source evidence. |
| [source/debate_watchdog_change_summary_20260525.md](source/debate_watchdog_change_summary_20260525.md) | Safety | Debate/watchdog change summary retained as source evidence. |
| [source/pathb_ttl_profit_cleanup_followup_plan_20260527.md](source/pathb_ttl_profit_cleanup_followup_plan_20260527.md) | Runtime Bug | PathB pending-buy TTL/order matching follow-up evidence; code path is present but commit/QA and monitoring remain active. |

## Deleted Buckets

Dated implementation plans, duplicated risk-review plans, stale PathB live plan text, prompt-overlay one-off notes, raw simulations, QA reports, and generated JSON artifacts were absorbed into ACTIVE_WORK, TODO_ROADMAP, ALWAYS_ANALYZE, CODE_LEVEL_REQUIREMENTS, DEVELOPED_WORK, or Git history and removed.
