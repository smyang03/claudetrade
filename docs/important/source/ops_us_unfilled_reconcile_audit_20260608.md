# US unfilled / fill attribution audit - 2026-06-08

## 결론

기존 시뮬레이션에서 `tape_fill_possible_without_fill_event`로 보였던 US 2건은 주문 체결 기회를 놓친 문제가 아니었다.

- `SMCI 2026-05-29`: 실제 PathB 매수/매도 완료, `CLOSED_CLAUDE_PRICE_TARGET`, `pnl_pct=+3.3698`. 다만 entry `FILLED` lifecycle event가 없어 학습 DB에서 `CLOSED_WITHOUT_FILL`, `learning_allowed=0`으로 분류됐다.
- `FTNT 2026-05-07`: 같은 decision/ticker에 `CLOSED_HARD_STOP`, `pnl_pct=-1.2979`가 있으나 close event payload의 `path_run_id`가 비어 있고 path run은 이후 manual cleanup에서 `EXPIRED` 처리됐다.
- 추가 재감사에서 `MSFT 2026-05-15`도 `closed_without_fill_event`로 분리됐다.
- 실제 미체결 후보는 `RDDT 2026-05-28` 1건뿐이며, limit never touched로 slippage 확대 개선 대상이 아니다.

따라서 slippage cap 확대는 여전히 금지하고, 개선 대상은 fill attribution / learning sync 품질이다.

## 반영 완료

- `runtime/pathb_runtime.py`
  - `_recover_entry_pending_local_holding()`
  - `_recover_order_unknown_local_holding()`
  - 위 두 경로에서 local PathB holding 증거로 path run을 `FILLED`로 복구할 때 lifecycle `FILLED` event도 함께 append하도록 보강했다.
  - 중복 fill event 방지를 위해 같은 `decision_id/path_run_id`의 기존 entry `FILLED/PARTIAL_FILLED` event를 먼저 확인한다.

- `tools/ops_next_wave_policy_simulation.py`
  - US unfilled audit가 `CLOSED` 거래를 slippage/미체결 후보로 잘못 세지 않도록 분류를 분리했다.
  - 새 분류:
    - `closed_without_fill_event`
    - `closed_event_unlinked_to_path_run`
    - `closed_path_run_without_fill_event`
  - `unfilled_order_count`는 실제 미체결 후보만 세고, 전체 감사 row는 `audit_row_count`로 분리한다.

- 테스트 보강
  - PathB local holding recovery 후 lifecycle `FILLED` event가 남는지 확인.
  - closed-without-fill event가 unfilled 후보에서 제외되는지 확인.

## 재감사 결과

출력:

- `.runtime/ops_simulation_analysis/next_wave_policy_audit_fix_20260608/ops_next_wave_policy_simulation.json`
- `.runtime/ops_simulation_analysis/next_wave_policy_audit_fix_20260608/us_unfilled_audit.csv`

US unfilled audit:

- `audit_row_count=4`
- `unfilled_order_count=1`
- classification:
  - `closed_without_fill_event`: 2 (`SMCI`, `MSFT`)
  - `closed_event_unlinked_to_path_run`: 1 (`FTNT`)
  - `limit_never_touched`: 1 (`RDDT`)

Slippage replay:

- 0/5/10/20/30bp 모두 추가 fill count 0
- 결론: slippage 확대는 개선안 아님

## 과거 데이터 처리 판단

이번 작업은 과거 live DB를 직접 수정하지 않았다.

과거 `SMCI`, `MSFT`, `FTNT`는 audited backfill 후보지만, broker truth / event causality를 다시 확인한 뒤 별도 repair 스크립트로 처리해야 한다. 지금 즉시 live DB를 쓰면 운영 원장을 오염시킬 수 있으므로 보류한다.

## 운영 영향

- 주문 제출, 수량 계산, broker truth fail-closed, safety gate, slippage cap, profit ladder, pre-close exit 정책 변경 없음.
- 새 주문을 만들지 않음.
- `.env*`, `config/v2_start_config.json`, `state/brain.json` 변경 없음.
- future runtime에서만 local holding recovery가 학습 가능한 canonical fill evidence를 남긴다.

## MD 위반 사항

기록 일자: 2026-06-08  
대상 작업: US unfilled audit / PathB recovered entry fill attribution

- 건드린 보호 영역: `runtime/pathb_runtime.py`의 PathB entry pending / ORDER_UNKNOWN local holding recovery 경로. broker/local truth 기반 복구 흐름에 속하므로 보호 영역 예외로 기록한다.
- 우회할 수 없었던 이유: 실제 체결/청산된 PathB 거래가 lifecycle `FILLED` event 없이 `CLOSED`만 남아 `v2_learning_performance`에서 `CLOSED_WITHOUT_FILL`, `learning_allowed=0`으로 떨어졌다. 대시보드나 리포트 표시만 수정하면 future 동일 결함을 막지 못한다.
- 변경 전 동작: local holding 증거로 path run status와 plan은 `FILLED`로 복구됐지만 lifecycle `FILLED` event가 append되지 않았다. 이후 learning sync는 close가 있어도 canonical fill이 없다고 판단했다.
- 변경 후 동작: 같은 복구 조건에서 path run을 `FILLED`로 바꾼 뒤, 중복 fill event가 없을 때만 `FILLED` lifecycle event를 append한다. payload에는 `recovered_fill=true`, `recovered_fill_source=local_pathb_holding`, `side=buy`, `qty`, `price`를 남긴다.
- 주문/리스크/브로커 truth/Claude/config/env 영향: 주문 제출 정책, 주문 수량, safety gate, broker truth fail-closed, hard stop, profit ladder, Claude 호출량, `.env*`, `config/v2_start_config.json`, `state/brain.json` 영향 없음.
- 대체 안전장치: 기존 local holding recovery 조건을 완화하지 않았고, 동일 decision/path_run의 기존 `FILLED/PARTIAL_FILLED` event가 있으면 새 event를 쓰지 않는다.
- 실행한 테스트:
  - `python -m pytest tests\test_pathb_runtime.py::PathBRuntimeTests::test_recover_on_startup_promotes_acked_run_with_local_position_to_filled tests\test_pathb_runtime.py::PathBRuntimeTests::test_order_unknown_local_pathb_holding_recovers_to_filled -q`
  - `python -m pytest tests\test_ops_next_wave_policy_simulation.py tests\test_ops_us_high_price_simulation.py -q`
  - `python -m pytest tests\test_pathb_runtime.py -q`
  - `python -m pytest tests\test_v2_learning_performance_sync.py -q`
  - `python -m pytest tests\test_pathb_sell_reconcile.py tests\test_pathb_sell_reconcile_backfill.py -q`
  - `python -m pytest tests\test_path_execution_arbiter.py tests\test_reconcile_order_truth.py tests\test_reconcile_live_truth.py -q`
  - `python -m py_compile runtime\pathb_runtime.py tools\ops_next_wave_policy_simulation.py`
- 남은 위험: 과거 `SMCI/MSFT/FTNT`의 dirty learning rows는 아직 live DB에 남아 있다. 별도 audited backfill 없이 자동 수정하지 않는다.
