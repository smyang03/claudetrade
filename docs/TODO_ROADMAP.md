# TODO Roadmap - 2026-05-01

이 문서는 아직 끝났다고 보기 어려운 MD를 기준으로 정리했습니다. Git 상태가 `Modified` 또는 `Untracked`이면 커밋 전까지 진행 중으로 봅니다.

## P0 - 정리와 안정화

| 상태 | 문서 | 해야 할 일 |
| --- | --- | --- |
| Untracked | [Recovery and Follow-up TODO](plans/recovery_and_followup_todo_20260501.md) | 손상 복구 범위 확정, mojibake 재발 방지, 운영 관찰, DB maintenance |
| Modified | [Claude Quality Contract Improvement](plans/claude_quality_contract_improvement_20260501.md) | raw log, credit tracker, postmortem, PathB price contract, hold advisor 안정화 범위 확인 |
| Untracked | [Encoding/Mojibake Scan Report](../audit/encoding_mojibake_report_20260501.md) | 탐지 결과 중 실제 수정 대상 선별 |

## P1 - 주문/체결/운영 정확성

| 상태 | 문서 | 해야 할 일 |
| --- | --- | --- |
| Live Verify | [KIS WebSocket Fill Sync Plan](KIS_WS_FILL_SYNC_PLAN.md) | 구현/로컬 테스트 완료. 남은 일은 `KIS_HTS_ID`, `pycryptodome`, 모의/실전 notice 수신, REST fallback 중복 반영 확인 |
| Open | [KIS API TODO](KIS_API_TODO.md) | API 확인 항목과 보완 TODO 정리 |
| Mixed | [Order / Equity Reconciliation Improvement](plans/order_equity_reconciliation_improvement_20260429.md) | 완료된 항목과 남은 reconciliation 항목 분리 |
| Mixed | [ORDER_UNKNOWN Auto-Resolve and PathB Pre-Close Carry](plans/order_unknown_preclose_carry_plan_20260430.md) | pending 항목 검증과 운영 적용 여부 확인 |
| Mixed | [Path B Sell Truth Reconcile](plans/pathb_sell_truth_reconcile_20260428.md) | sell truth reconcile 후속 검증 |
| Open | [Soft Exit Arbitration Follow-up](plans/soft_exit_arbitration_followup_20260501.md) | follow-up QA와 runtime 적용 여부 확인 |

## P2 - 전략 품질과 관찰

| 상태 | 문서 | 해야 할 일 |
| --- | --- | --- |
| Deferred | [Trading Decision Contract Improvement](plans/trading_decision_contract_improvement_20260501.md) | F1: `market_regime`, `data_quality`, `new_buy_permission`, `max_gross_exposure_pct`를 live gate에 바로 연결하지 말고 log-only/shadow로 관찰 |
| Open | [Execution Audit Observability Plan](plans/execution_audit_observability_plan_20260430.md) | 후보/실행/청산 관찰 지표 정리 |
| Open | [KR Opening Quality / Ops Correctness Round 1](plans/kr_opening_quality_ops_round1_20260428.md) | KR opening 품질 개선 항목 적용 여부 확인 |
| Open | [KR/US Live Ops QA Plan](plans/kr_us_live_ops_qa_20260427.md) | live ops QA 항목 최신화 |
| Open | [Soft Exit Arbitration Plan](plans/soft_exit_arbitration_20260430.md) | 완료 리포트와 남은 plan 항목 대조 |
| Open | [Broker Truth Live Plan](plans/broker_truth_live_plan_20260427.md) | broker truth live 적용 상태 재확인 |
| Open | [PLAN Momentum Opening Gate](plans/PLAN_momentum_opening_gate.md) | 보류된 momentum opening gate 설계 재평가 |
| Open | [Intraday Strategy Roadmap](plans/PLAN_intraday_strategy_roadmap.md) | intraday 전략 roadmap 최신화 |

## P3 - 구조 개선과 장기 과제

| 상태 | 문서 | 해야 할 일 |
| --- | --- | --- |
| Deferred | [Trading Decision Contract Improvement](plans/trading_decision_contract_improvement_20260501.md) | F2: prompt 파일 외부화는 현재 in-code contract 안정화 후 진행 |
| Deferred | [Trading Decision Contract Improvement](plans/trading_decision_contract_improvement_20260501.md) | F3: selection rank / execution plan 2-call 분리는 shadow-only 비교 후 검토 |
| Open | [MODULARIZATION](plans/MODULARIZATION.md) | `trading_bot.py` 모듈화 계획 재검토 |
| Open | [Dual Runtime Architecture](plans/DUAL_RUNTIME_ARCHITECTURE.md) | 남은 TODO와 실제 PathA/PathB 상태 대조 |
| Open | [Brain Train TODO](plans/BRAIN_TRAIN_TODO.md) | Brain 학습 데이터 품질/가중치 설계 |
| Reference | [V2 Production Design](../v2.md) | 현재 구현과 설계 차이 갱신 |
| Reference | [Path B Claude Price Live Plan](../pathb_v2_live_plan.md) | live 적용 상태와 후속 TODO 갱신 |

## Documentation Cleanup Tasks

- `trading_decision_contract_improvement_20260501.md`의 P0-P9는 완료/QA 문서로 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)에 넣고, 하단 Deferred Follow-Up Plan의 F1-F3은 이 문서의 P2/P3에 별도 TODO로 남겼습니다.
- `docs/archive/`는 보관 문서로 유지하고 active plan 목록에서 제외합니다.
- `data/backtest_audit/**/*.md`와 `data/v2_reports/**/*.md`는 자동 생성 산출물로 유지합니다.
- mojibake가 남은 문서는 새 허브 문서에서 대체 링크를 제공하고, 본문 수정은 별도 작업으로 분리합니다.
- `docs/plans/` 안에서 완료된 문서는 `DEVELOPED_WORK.md`로 연결하고, 남은 항목만 `TODO_ROADMAP.md`에 둡니다.
