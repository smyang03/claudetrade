# Architecture Map - 2026-05-01

## Runtime Shape

```text
trading_bot.py
  -> market/session orchestration
  -> candidate selection
  -> Claude analysts / consensus
  -> strategy and price plan generation
  -> order execution / PathB runtime
  -> postmortem and BrainDB learning
  -> dashboard / logs / reports
```

## Main Components

| 영역 | 주요 위치 | 역할 | 관련 문서 |
| --- | --- | --- | --- |
| 실행 루프 | `trading_bot.py` | KR/US 세션, 후보 탐색, 주문 흐름 | [trading_process.md](trading_process.md) |
| Claude 판단 | `minority_report/` | Bull/Bear/Neutral, 합의, postmortem | [claude_quality_contract_improvement_20260501.md](plans/claude_quality_contract_improvement_20260501.md) |
| 전략 | `strategy/` | gap, momentum, mean reversion, adaptive params | [PLAN_intraday_strategy_roadmap.md](plans/PLAN_intraday_strategy_roadmap.md) |
| PathB | `runtime/`, `decision/` | Claude price plan 기반 live runtime | [DUAL_RUNTIME_ARCHITECTURE.md](plans/DUAL_RUNTIME_ARCHITECTURE.md) |
| 브로커/KIS | `kis_api.py`, KIS 연동부 | 주문, 잔고, 체결, 시세 | [KIS_API_TODO.md](KIS_API_TODO.md), [KIS_WS_FILL_SYNC_PLAN.md](KIS_WS_FILL_SYNC_PLAN.md) |
| BrainDB | `claude_memory/brain.py`, `state/brain.json` | 판단 메모리, 성과, issue pattern | [brain_postmortem_cleanup_20260430.md](plans/brain_postmortem_cleanup_20260430.md) |
| ML/로그 | `ml/`, `data/*.db` | decision/event/ticker selection 기록 | [DATA.md](../DATA.md) |
| 대시보드 | `dashboard/` | 운영 상태 조회, live summary | [DASHBOARD_DEVLOG.md](archive/DASHBOARD_DEVLOG.md) |

## State Stores

| 저장소 | 성격 | 문서 위치 |
| --- | --- | --- |
| `state/brain.json` | 정책 메모리, postmortem 학습 결과 | [brain_issue_pattern_id_contiguity_20260501.md](plans/brain_issue_pattern_id_contiguity_20260501.md) |
| `data/ml/decisions.db` | 후보, 차단, 판단, 결과 이벤트 | [DATA.md](../DATA.md) |
| `data/ticker_selection_log.db` | ticker selection feedback | [candidate_health_tracker_20260429.md](plans/candidate_health_tracker_20260429.md) |
| `data/v2_event_store.db` | PathB/V2 event store | [DUAL_RUNTIME_ARCHITECTURE.md](plans/DUAL_RUNTIME_ARCHITECTURE.md) |
| `logs/`, raw Claude logs | Claude 호출 원문과 runtime 로그 | [claude_call_review_20260429.md](reports/claude_call_review_20260429.md) |

## Document Structure

```text
docs/
  README.md                         # documentation entry
  DOCUMENTATION_INDEX.md            # classification rules
  ARCHITECTURE_MAP.md               # this file
  DEVELOPED_WORK.md                 # completed and QA-backed work
  TODO_ROADMAP.md                   # future and in-progress work
  DOCUMENTATION_INVENTORY.md        # markdown inventory
  plans/                            # plans and active follow-ups
  reports/                          # QA and analysis reports
  archive/                          # older dev logs
data/
  backtest_audit/**/*.md            # generated backtest reports
  v2_reports/**/*.md                # generated V2/live reports
audit/
  *.md                              # repository audits and hotfix plans
```

## Operating Principle

운영 판단과 학습 데이터는 분리해서 관리합니다.

- `brain.json`은 정책 메모리입니다.
- DB는 실제 후보/주문/체결/결과 이벤트의 근거입니다.
- `docs/plans/`는 구현 의도와 변경 계획입니다.
- `docs/reports/`는 구현 또는 실험 결과를 검증한 흔적입니다.
- `data/**` Markdown은 자동 생성 산출물이라 보존하되, active plan과 섞지 않습니다.
