# Architecture Map - 2026-05-13

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
| Claude 판단 | `minority_report/` | Bull/Bear/Neutral, 합의, postmortem | [Claude Call Review](reports/claude_call_review_20260429.md), [Market Analysis Audit](../audit/market_analysis_tune_prompt_audit_20260501.md) |
| 전략 | `strategy/` | gap, momentum, mean reversion, adaptive params | [TODO Roadmap](TODO_ROADMAP.md) |
| PathB | `runtime/`, `decision/`, `data/v2_event_store.db` | Claude price plan 기반 live runtime | [PathB Live Plan](../pathb_v2_live_plan.md), [TODO Roadmap](TODO_ROADMAP.md) |
| 브로커/KIS | `kis_api.py`, `runtime/broker_truth_snapshot.py` | 주문, 잔고, 체결, broker truth | [TODO Roadmap](TODO_ROADMAP.md) |
| BrainDB | `claude_memory/brain.py`, `state/brain.json` | 판단 메모리, 성과, issue pattern | [Brain Cleanup QA](reports/brain_json_postmortem_cleanup_20260430.md) |
| ML/로그 | `ml/`, `data/*.db` | decision/event/ticker selection 기록 | [DATA.md](../DATA.md), [TODO Roadmap](TODO_ROADMAP.md) |
| 대시보드 | `dashboard/` | 운영 상태 조회, live summary | [TODO Roadmap](TODO_ROADMAP.md) |

## State Stores

| 저장소 | 성격 | 문서 위치 |
| --- | --- | --- |
| `state/brain.json` | 정책 메모리, postmortem 학습 결과 | [Brain Cleanup QA](reports/brain_json_postmortem_cleanup_20260430.md) |
| `state/live_broker_truth_snapshot.json` | KR/US broker truth snapshot | [TODO Roadmap](TODO_ROADMAP.md) |
| `data/ml/decisions.db` | 후보, 차단, 판단, 결과 이벤트 | [DATA.md](../DATA.md), [Developed Work](DEVELOPED_WORK.md) |
| `data/ticker_selection_log.db` | ticker selection feedback | [Historical Candidate Review](reports/historical_candidate_execution_review_20260420_20260429.md) |
| `data/v2_event_store.db` | PathB/V2 event store | [PathB Live Plan](../pathb_v2_live_plan.md), [TODO Roadmap](TODO_ROADMAP.md) |
| `logs/`, raw Claude logs | Claude 호출 원문과 runtime 로그 | [Claude Call Review](reports/claude_call_review_20260429.md) |
| `data/v2_reports/` | guardian/live smoke 등 자동 생성 리포트 | [TODO Roadmap](TODO_ROADMAP.md) |

## Document Structure

```text
docs/
  README.md                         # documentation entry
  DOCUMENTATION_INDEX.md            # classification rules / cleanup policy
  ARCHITECTURE_MAP.md               # this file
  DEVELOPED_WORK.md                 # completed summary only
  TODO_ROADMAP.md                   # single active priority report
  DOCUMENTATION_INVENTORY.md        # markdown inventory
  reports/                          # QA and analysis reports
data/
  backtest_audit/**/*.md            # generated backtest reports
  v2_reports/**/*.md                # generated V2/live/guardian reports
audit/
  *.md                              # repository audits and hotfix plans
```

## Operating Principle

운영 판단과 학습 데이터는 분리해서 관리한다.

- `brain.json`은 정책 메모리다.
- DB는 실제 후보/주문/체결/결과 이벤트의 근거다.
- active plan은 `TODO_ROADMAP.md` 하나에 둔다.
- 완료된 plan 요약은 `DEVELOPED_WORK.md`에 둔다.
- `docs/reports/`는 구현 또는 실험 결과를 검증한 흔적이다.
- `data/**` Markdown은 자동 생성 산출물이라 보존하되 active plan과 섞지 않는다.
