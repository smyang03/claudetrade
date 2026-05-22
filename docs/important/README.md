# Important Document Index

Updated: 2026-05-22

## How To Read

1. Use [ACTIVE_WORK.md](ACTIVE_WORK.md) for what still needs to be done.
2. Use [ALWAYS_ANALYZE.md](ALWAYS_ANALYZE.md) before changing live behavior, market policy, learning data, or dashboard truth.
3. Use [GIT_CODE_REVIEW_PLAN_20260522.md](GIT_CODE_REVIEW_PLAN_20260522.md) for the latest git/code review and before/after plan.
4. Use `core/` for durable architecture and operating references.
5. Use `source/` only when a decision needs the original detailed evidence.

## Core

| Document | Title | Summary |
| --- | --- | --- |
| [core/ARCHITECTURE_MAP.md](core/ARCHITECTURE_MAP.md) | Architecture Map | Runtime map for Path A, Path B, broker truth, risk, dashboard, DB, and logs. |
| [core/TODO_ROADMAP.md](core/TODO_ROADMAP.md) | TODO Roadmap | Single active backlog after absorbing old plan and report documents. |
| [core/DEVELOPED_WORK.md](core/DEVELOPED_WORK.md) | Developed Work | Completed work that is no longer an active plan. |
| [core/DOCUMENTATION_INDEX.md](core/DOCUMENTATION_INDEX.md) | Documentation Index | Current cleanup rules and reading order. |
| [core/DOCUMENTATION_INVENTORY.md](core/DOCUMENTATION_INVENTORY.md) | Documentation Inventory | Remaining docs tree and retained source list. |
| [core/trading_process.md](core/trading_process.md) | Trading Process | End-to-end trade flow reference. |
| [core/rsi_threshold_research.md](core/rsi_threshold_research.md) | RSI Research | RSI threshold research note retained as strategy reference. |
| [core/claude_selection_compact_output_report_20260512.md](core/claude_selection_compact_output_report_20260512.md) | Claude Selection Compact Output | Compact Claude selection output review retained for prompt/selection context. |

## Current Source Evidence

| Document | Category | Why It Remains |
| --- | --- | --- |
| [source/us_kis_ranking_screener_requirements_20260522.md](source/us_kis_ranking_screener_requirements_20260522.md) | Active Work | US screener should prefer KIS overseas ranking APIs while preserving Yahoo/FMP fallback and order/risk isolation. |
| [source/live_config_safety_code_requirements_20260521.md](source/live_config_safety_code_requirements_20260521.md) | Safety | `/setorder` must be fail-closed and PathB live gate policy must match KR-on/US-on. |
| [source/operational_db_code_recheck_20260521.md](source/operational_db_code_recheck_20260521.md) | Truth | V2 canonical performance is the better fill/performance truth; legacy `decisions.db` is not enough for PathB. |
| [source/market_index_watch_set_20260522.md](source/market_index_watch_set_20260522.md) | Market Judgment | Defines KR/US market index watch set and shadow-first expansion rule. |
| [source/claude_misjudgments_20260520.md](source/claude_misjudgments_20260520.md) | Selection Quality | Captures Claude false-positive and false-negative review buckets. |
| [source/kr_us_policy_action_review_20260520.md](source/kr_us_policy_action_review_20260520.md) | Policy Judgment | Separates KR/US policy action performance before changing gates. |
| [source/kr_selection_execution_trace_20260520.md](source/kr_selection_execution_trace_20260520.md) | Trace | Traces KR selection versus execution so selection quality is not mixed with execution/risk failures. |
| [source/momentum_shadow_final_judgment_data_20260520.md](source/momentum_shadow_final_judgment_data_20260520.md) | Shadow | Momentum remains a shadow/evidence item until labeled sessions justify live use. |
| [source/prompt_overlay_later_data_plan_20260520.md](source/prompt_overlay_later_data_plan_20260520.md) | Prompt Shadow | Prompt overlay must stay shadow until the data gate passes. |
| [source/candidate_pipeline_root_cause_review_20260519.md](source/candidate_pipeline_root_cause_review_20260519.md) | Candidate Pipeline | Root-cause review for candidate funnel quality and audit coverage. |
| [source/codex_kr_us_db_reanalysis_20260519.md](source/codex_kr_us_db_reanalysis_20260519.md) | DB Review | KR/US DB reanalysis that supports canonical performance and metric contract work. |

## Deleted Buckets

Old dated report files, simulation JSON, completed QA records, stale implementation plans, and repeated profitability review outputs were removed from `docs/`. Their active lessons are represented in [ACTIVE_WORK.md](ACTIVE_WORK.md), [ALWAYS_ANALYZE.md](ALWAYS_ANALYZE.md), or the retained source evidence above.
