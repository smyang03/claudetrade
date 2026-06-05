# US 장전 Auto-Sell / Hold Advisor 구현 보고서 - 2026-06-05

작성 일자: 2026-06-05
기준 플랜: `docs/important/source/us_preopen_auto_sell_hold_advisor_plan_20260605.md`

## 1. 결론

구현 및 QA 완료. 플랜의 핵심 방향인 `AUTO_SELL 유지 + US 장전 얕은 stop만 개장 재검증으로 defer + 수익/중대 손실 경로 보존`을 코드에 반영했다.

운영 기본값은 `PATHB_PREOPEN_EXIT_POLICY_MODE=off`다. 따라서 배포 직후 live 동작은 기존과 동일하며, 운영 적용은 `shadow`로 먼저 켠 뒤 표본을 확인해야 한다.

## 2. 구현 범위

수정 범위:

- `runtime/pathb_runtime.py`
  - US PathB preopen exit policy mode 추가: `off|shadow|enforce`
  - preopen stop severity 분류: `shallow_loss_stop`, `severe_loss_stop`, `profit_protective_stop`, `boundary_loss_stop`
  - `DEFER_OPEN_RECHECK`, `WAIT_FRESH_OPEN_QUOTE`, `SELL_NOW_AFTER_OPEN_CONFIRM`, `CLEAR_DEFERRED_STOP` 기록
  - defer record는 PathB run plan metadata에 merge. 새 DB schema 없음
  - defer는 `AUTO_SELL_REVIEW` HOLD cooldown을 소비하지 않고 별도 defer throttle만 사용
  - stale/closed/pending sell 상태는 advisor 호출 전에 `SKIP_STALE_OR_CLOSED`로 차단
- `minority_report/hold_advisor.py`
  - hold advisor prompt context에 session/open/recheck/quote/severity/original thesis/pathb plan 정보를 추가
- `trading_bot.py`
  - intraday review 전 현재 position/run 상태 재확인
  - 이미 closed/sell-in-flight인 PathB position은 advisor 재호출 생략
- `interface/v2_ops_summary.py`, `dashboard/dashboard_server.py`
  - PathB active/recent payload와 화면에 `개장 재검증` 상태 노출
- `lifecycle/event_store.py`, `tools/live_preflight.py`
  - ops summary/preflight read path에서 EventStore read-only 연결 지원
- `tools/simulate_preopen_exit_policy.py`
  - 기존 DB/log 기반 read-only 정책 시뮬레이션 도구 추가

## 3. 요구서 대비 검토

| 항목 | 결과 | 비고 |
|---|---|---|
| R-01 exit signal 감지와 실행 분리 | 완료 | `_submit_sell()` 전 preopen policy gate 적용 |
| R-02 severity 분류 | 완료 | 중간 구간은 `boundary_loss_stop` + `SELL_NOW` |
| R-03 opening_confirm 재검증 | 완료 | 별도 phase 추가 없이 PathB run plan metadata로 관리 |
| R-04 hold advisor context 보강 | 완료 | session/open/recheck/quote/severity/original thesis 추가 |
| R-05 post-close 중복 리뷰 차단 | 완료 | advisor 호출 전 live position/run guard |
| R-06 로그/대시보드 가시성 | 완료 | plan metadata + PathB 화면 `개장 재검증` 컬럼 |
| R-07 shadow 우선 rollout | 완료 | 기본 off, shadow는 기록만 하고 기존 sell 진행 |

누락점: 없음. 운영 적용은 코드가 아니라 env/config 승인 단계가 남아 있다.

## 4. DB 시뮬레이션

명령:

```powershell
python tools/simulate_preopen_exit_policy.py --date 2026-06-04
```

결과:

- preopen stop cases: 4
- post-close review skips: 2
- policy decisions: `DEFER_OPEN_RECHECK=2`, `SELL_NOW=2`, `SKIP_STALE_OR_CLOSED=2`
- NVDA: shallow hard_stop, -0.3046% -> `DEFER_OPEN_RECHECK`
- GOOGL: shallow hard_stop, -0.4338% -> `DEFER_OPEN_RECHECK`
- HPE: severe claude_price_stop, -4.0067% -> `SELL_NOW`
- MSFT: profit-protective hard_stop, +1.2903% -> `SELL_NOW`
- HPE 22:22:43/22:22:52 duplicate reviews -> `SKIP_STALE_OR_CLOSED`

전체 기간 DB 보존 경로:

- `CLOSED_CLAUDE_PRICE_TARGET`: n=10, avg +5.3115%, wins 10
- `CLOSED_CLAUDE_SELL`: n=6, avg +3.9127%, wins 6
- `CLOSED_CLAUDE_PRICE_PRE_CLOSE`: n=34, avg +1.4424%, wins 19
- `CLOSED_PROFIT_LADDER`: n=20, avg +1.0972%, wins 13
- `CLOSED_TRAILING_STOP`: n=2, avg +3.7932%, wins 1

시뮬레이션 도구는 `data/ml/decisions.db`를 SQLite `mode=ro`로 열고, 결과 파일을 쓰지 않는다.

## 5. QA 결과

통과:

```powershell
python -m py_compile runtime/pathb_runtime.py minority_report/hold_advisor.py trading_bot.py interface/v2_ops_summary.py dashboard/dashboard_server.py lifecycle/event_store.py tools/live_preflight.py tools/simulate_preopen_exit_policy.py
python -m pytest tests/test_preopen_auto_sell_recheck.py -q
python -m pytest tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q
python -m pytest tests/test_preopen_opening_role_separation.py tests/test_candidate_action_live_mapping.py tests/test_pre_session_sell_queue.py -q
python -m pytest tests/test_auto_sell_claude_gate.py tests/test_pathb_profit_protection.py tests/test_claude_quality_contracts.py tests/test_plan_a_hold_policy.py tests/test_price_unit_normalization.py -q
python -m pytest tests/test_pathb_runtime.py -q
python -m pytest tests/test_v2_phase6.py::V2Phase6Tests::test_pathb_ops_summary_exposes_preopen_exit_policy_fields tests/test_dashboard_pathb.py::DashboardPathBTests::test_pathb_page_exposes_preopen_recheck_column -q
python -m pytest tests/test_preopen_auto_sell_recheck.py tests/test_v2_phase6.py tests/test_dashboard_pathb.py tests/test_live_preflight_ops_summary.py tests/test_auto_sell_claude_gate.py tests/test_pathb_runtime.py tests/test_pathb_profit_protection.py tests/test_claude_quality_contracts.py tests/test_plan_a_hold_policy.py tests/test_price_unit_normalization.py tests/test_preopen_opening_role_separation.py tests/test_candidate_action_live_mapping.py tests/test_pre_session_sell_queue.py -q
python -m pytest -q
```

최종 전체 QA:

- `2271 passed, 2 skipped, 2 warnings`

live preflight:

- `ok=True`, `fail=0`
- warnings: 14
- 주요 warning은 기존 운영/데이터 품질 상태: lifecycle consistency, dashboard pid lock, `state/brain.json` uncommitted, US broker truth stale, preopen scheduler heartbeat, price csv integrity, decisions DB known gap, external data readiness, candidate audit outcome, ticker selection attribution
- 이번 변경으로 인한 config conflict, schema fail, PathB ORDER_UNKNOWN fail은 없음

## 6. 운영 DB 오염 검토

중요 확인:

- 현재 `python trading_bot.py --live` 프로세스가 실행 중이다. 확인 PID: 44696
- 추가 운영 프로세스도 실행 중이다: `broker_truth_scheduler.py`, `live_guardian.py`, `claude_usage_monitor.py`, dashboard 등
- QA 중 `data/ml/decisions.db`와 `data/v2_event_store.db`의 timestamp/size 변화가 관측됐다.
- 최신 row 확인 결과, 변화 원인은 2026-06-05 11:58:55~11:59:00 KST에 live bot이 기록한 KR runtime 이벤트다.
  - `decisions.db`: KR `SKIPPED` rows 12건
  - `v2_event_store.db`: KR `005935` `ORDER_SENT` / `ORDER_ACKED`

판단:

- `tools/simulate_preopen_exit_policy.py`는 read-only이며 운영 DB에 쓰지 않았다.
- live bot이 켜져 있는 동안 DB mtime/size는 운영 기록으로 계속 바뀔 수 있으므로, 이 상태에서 "검증 도구가 DB를 오염시켰는지"를 mtime만으로 판정하면 안 된다.
- 새 코드 적용은 현재 실행 중인 live bot에는 아직 로드되지 않았다. 반영하려면 운영자가 승인한 재시작이 필요하다.

운영 권고:

- live 중에는 `PATHB_PREOPEN_EXIT_POLICY_MODE`를 바로 `enforce`로 켜지 않는다.
- 첫 적용은 `shadow`로 시작하고, 최소 5개 이상 preopen stop 표본을 수집한 뒤 `enforce` 전환 여부를 판단한다.
- live bot 재시작 전 `state/brain.json` uncommitted 상태와 preopen scheduler heartbeat warning을 별도로 정리한다.

## 7. MD 위반 사항

기록 일자: 2026-06-05
대상 작업: US preopen auto-sell hold advisor 개선 구현

- 건드린 보호 영역: `runtime/pathb_runtime.py`의 PathB `AUTO_SELL_REVIEW` 연결 흐름, `_run_pathb_sell_review_gate()` 입력 context, `_submit_sell()` sell 실행 전 gate, `trading_bot.py`의 intraday hold advisor 재검토 흐름. Dashboard/API read path와 EventStore read-only 조회 경로도 보강했다.
- 우회할 수 없었던 이유: 2026-06-04 NVDA/GOOGL/HPE/MSFT 사례에서 문제 원인이 PathB auto-sell 실행 직전 판단, hold advisor 입력 context 부족, post-close 중복 review였다. 원인 경로가 보호 영역과 직접 맞닿아 있어 문서/대시보드만으로는 재발을 막을 수 없었다.
- 변경 전 동작: US 장전 hard_stop/loss 계열 signal이 감지되면 장전 여부와 severity 구분 없이 `_submit_sell()`로 이어질 수 있었다. shallow stop도 advisor SELL 후 즉시 주문될 수 있었고, HPE처럼 이미 closed된 position에 대해 advisor SELL이 중복 발생할 수 있었다.
- 변경 후 동작: 기본값 off에서는 기존 동작을 유지한다. shadow/enforce에서 US preopen stop signal을 severity로 분류하고, shallow stop은 `DEFER_OPEN_RECHECK`, severe/profit-protective/boundary는 `SELL_NOW`로 분리한다. closed/missing/sell-in-flight 상태는 advisor 호출 전 `SKIP_STALE_OR_CLOSED`로 차단한다.
- 주문/리스크/브로커 truth/Claude/config/env 영향: 주문 수량, 주문금액, sizing, broker truth fail-closed, profit ladder tier/env, pre-close, target, `.env*`, `config/v2_start_config.json`, `state/brain.json` 변경 없음. `PATHB_PREOPEN_EXIT_POLICY_MODE` 기본값은 `off`라 live 주문 동작 변화는 없다. `shadow`는 기록만 하며 기존 sell path를 진행한다. `enforce`에서만 shallow preopen stop 주문이 개장 재검증으로 defer된다. shallow defer는 advisor 호출을 줄일 수 있고, opening recheck는 기존 HOLD cooldown이 아니라 별도 defer throttle을 쓴다.
- 대체 안전장치 또는 반복호출/오염 방지책: default-off flag, shadow-first rollout, severity boundary sell-now, PathB-owned defer metadata, defer record throttle, stale/closed pre-check, existing broker truth/sellability/safety precheck 유지, read-only simulation, EventStore read-only 조회 옵션.
- 실행한 테스트: 위 QA 명령 전체. 최종 `python -m pytest -q` 결과 `2271 passed, 2 skipped`.
- 남은 위험: preopen 표본이 아직 작다. live bot이 실행 중이면 DB는 운영 이벤트로 계속 갱신되므로, shadow 검증 결과를 볼 때 runtime write와 검증 도구 read를 분리해야 한다. 현재 live bot은 새 코드를 로드하지 않은 상태이며, 적용에는 운영자 승인 재시작이 필요하다.

## 8. 최종 판단

구현 적합. 운영 적용은 즉시 enforce가 아니라 `off -> shadow -> 표본 검토 -> 제한 enforce` 순서가 맞다.
