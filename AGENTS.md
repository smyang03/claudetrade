# Repository Guidelines

This file is the shared operating guide for agentic coding tools working in this repository. `CLAUDE.md` contains the longer Claude-specific operations contract; keep the core safety rules in both files aligned.

## Project Structure & Module Organization

이 저장소는 Python 기반 KR/US 자동매매 시스템입니다. `trading_bot.py`가 메인 실행 루프이며, `kis_api.py`, `risk_manager.py`, `ticker_selection_db.py`가 브로커 연동, 리스크 관리, 종목 선택을 지원합니다. 핵심 도메인 코드는 `runtime/`, `execution/`, `strategy/`, `bot/`, `minority_report/`, `audit/`, `lifecycle/`, `ml/`, `preopen/`, `learning/`에 나뉘어 있습니다. 운영 도구는 `tools/`, Flask 대시보드는 `dashboard/`, 문서와 보고서는 `docs/`에 둡니다. 테스트는 주로 `tests/`에 있으며, 일부 레거시 테스트는 루트의 `test_*.py`와 `test/audit_lab/`에 있습니다. `data/`, `state/`, `logs/`는 런타임 산출물 위치이므로 생성 DB, PID, 캐시, 로컬 보고서는 커밋하지 마세요.

## AI Operating Contract

- AI는 시장 모드 판단, 종목 watchlist/trade_ready 후보 제안, conviction/strategy-fit 판단, 보유 종목 HOLD/SELL 의견 제시를 맡을 수 있습니다.
- AI가 최종 주문 수량/금액 계산, 하드 손절 해제, 브로커 truth 무시, 이벤트 데이터의 장기 메모리 자동 승격을 직접 수행하면 안 됩니다.
- 상태 오염이 의심되면 내부 캐시보다 브로커 보유 종목, 미체결 주문, 가능하면 체결 내역을 1차 truth로 봅니다.
- 브로커 불신 또는 quarantine 상태에서는 신규 진입보다 기존 포지션 보호와 복구를 우선합니다.
- selection 품질 문제와 execution/risk 문제를 섞지 말고, 원인과 수정 범위를 분리하세요.
- 짧은 기간 성과만으로 전략 철학을 교체하지 마세요. 축소, shadow 관찰, 검증, 승인 순서로 진행합니다.
- `state/brain.json` 자동 변경은 승인형 워크플로우가 안정화되기 전까지 보류합니다. 교훈 후보는 `state/lesson_candidates.json`에 append/score하는 흐름을 우선합니다.

## Trading Safety & Runtime Rules

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard는 Claude 사용량 폭증 방지용 안전장치입니다. `runtime/pathb_runtime.py`의 `_pathb_auto_sell_review_cooldown_payload()` / `_run_pathb_sell_review_gate()` 흐름과 `tests/test_auto_sell_claude_gate.py::test_pathb_loss_cap_hold_respects_reask_cooldown`을 삭제하거나 완화하지 마세요. 변경이 필요하면 작업 설명, 커밋 메시지, PR 본문 중 하나에 반드시 이유, 예상 Claude 호출/토큰 영향, 대체 반복호출 방지책, 실행한 테스트를 명시하세요.
- `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true` 상태에서는 PathB `loss_cap`/`hard_stop`/`profit_ladder` review가 다시 열릴 수 있으므로 위 cooldown guard가 필수입니다. 이 플래그 또는 `AUTO_SELL_REVIEW_HOLD_COOLDOWN_MINUTES`, `PATHB_AUTO_SELL_REVIEW_HOLD_REASK_DROP_PCT`를 건드릴 때도 동일하게 사유와 검증 계획을 남기세요.
- Path A는 `trading_bot.py`의 `TradingBot` 흐름이며 Claude selection, 전략 신호, 주문으로 이어집니다.
- Path B는 `runtime/pathb_runtime.py`의 `PathBRuntime` 흐름이며 Claude 가격 플랜 기반 진입/청산을 담당합니다. 현재 KR/US 모두 live 활성 상태입니다 (`PATHB_KR_LIVE_ENABLED=true`, `PATHB_US_LIVE_ENABLED=true`).
- 두 경로는 `runtime/action_routing.py`의 `RouteDecision`으로 합류합니다.
- 진입 결정은 후보 풀 생성, Claude selection raw, normalized/applied trade_ready, 전략 신호, affordability/risk, 주문 생성 순서로 추적하세요.
- 매수 차단 조건에는 브로커 상태 불신, affordability fail, hard risk block, same-day reentry block, late-session/blackout, watch_only 유지가 포함됩니다.
- `_sync_runtime_with_broker()` 변경 시 보유 종목과 미체결 주문 기준 stale 포지션 정리, 시장별 quarantine, HALT/daily_return의 시장별 baseline 계산을 반드시 확인하세요.

## Protected Completed Areas & MD Violation Reporting

아래 동작은 완료/보호 영역입니다. 특정 이슈가 해당 영역을 직접 지목하거나, 실패 테스트/로그/운영 장애가 이 영역을 원인으로 가리킬 때만 최소 범위로 수정합니다. 단순 리팩터링, 이름 변경, 구조 재배치, 테스트 기대값 임의 변경, safety guard 완화는 금지합니다.

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard: `runtime/pathb_runtime.py`의 `_pathb_auto_sell_review_cooldown_payload()` / `_run_pathb_sell_review_gate()`와 `tests/test_auto_sell_claude_gate.py::test_pathb_loss_cap_hold_respects_reask_cooldown`.
- PathB broker-truth entry fail-closed: `runtime/pathb_runtime.py::_entry_scan_broker_truth_gate()`는 live에서 token/provider unavailable, stale/error broker truth를 `BLOCKED_BROKER_TRUTH`로 차단해야 합니다. preflight/dashboard 가시성은 개선할 수 있지만 fail-closed 동작은 완화하지 않습니다.
- PathB sizing reason split: `_pathb_qty_with_context()`와 `execution/safety_gate.py`의 `INVALID_PRICE`, `ORDER_SIZE_TOO_SMALL_GATE`, `HIGH_PRICE_BUDGET_BLOCK` 분리를 보존합니다. fixed sizing, one-share-over-budget 정책, early soft gate, live submit 정책은 승인 없이 바꾸지 않습니다.
- zero-holding stale reconcile: `TradingBot._sell_zero_holding_broker_evidence()`와 `PathBRuntime._pathb_zero_holding_broker_evidence()`는 fresh broker truth, zero holding, open remaining 0일 때만 local stale position/run을 정리해야 합니다.
- KIS order normalization: KR/US 체결/미체결 정규화는 `remaining_qty`를 보존해야 하며, `open_orders`는 `remaining_qty > 0` 기준으로 파생되어야 합니다.
- Path A/Path B 합류 계약: 두 경로는 `runtime/action_routing.py::RouteDecision`을 통해 합류하며, selection 품질 문제와 execution/risk 문제를 한 패치에서 섞지 않습니다.
- broker truth 우선순위와 `_sync_runtime_with_broker()`의 시장별 quarantine, stale position 정리, HALT/daily_return 시장별 baseline 계산을 보존합니다.
- `state/brain.json`은 정책 메모리일 뿐 runtime truth가 아닙니다. 승인형 워크플로우 없이 자동 정책 메모리 승격이나 직접 수정 경로를 추가하지 않습니다.

보호 영역을 피할 수 없이 수정해야 하는 경우, 작업 설명/커밋 메시지/PR 본문 중 하나에 반드시 `MD 위반 사항` 섹션을 남깁니다. 이 섹션은 보호 계약 예외를 운영자가 알아볼 수 있게 하는 보고 형식이며, 자동으로 부적합하다는 뜻이 아닙니다. 예외가 직접 원인에 한정되고, 보호 계약을 완화하지 않으며, 집중 테스트와 전체 QA로 검증될 때만 적합한 예외로 봅니다. 아래 내용을 포함해야 합니다.

- 어떤 보호 영역을 건드렸는지
- 왜 우회할 수 없었는지
- 변경 전/후 동작 차이
- 주문/리스크/브로커 truth/Claude 호출량/config/env 영향
- 대체 안전장치 또는 반복호출/오염 방지책
- 실행한 테스트와 남은 위험

### MD 위반 사항

기록 일자: 2026-05-29
대상 작업: broker sync metadata integrity / PathB attribution 보존

- 건드린 보호 영역: broker truth 우선순위와 `TradingBot._sync_runtime_with_broker()`의 stale position 정리 흐름, PathB sell/fill reconcile의 broker fill 판정 흐름.
- 우회할 수 없었던 이유: EL/IREN 사례에서 한투 broker truth 자체는 맞았지만, 일시적/부분 broker snapshot 누락과 과거 sell fill 재사용 때문에 로컬 PathB 메타데이터가 삭제되거나 `broker_sync`로 오염됐다. 원인이 보호 영역인 broker sync/reconcile 경로였으므로 대시보드 표시만 수정해서는 재발을 막을 수 없었다.
- 변경 전 동작: 한 번의 broker balance 누락으로 로컬 포지션이 stale 제거되고, 이후 broker에 다시 나타나면 saved template 없이 `broker_sync`로 재주입될 수 있었다. 일부 PathB sell reconcile 경로는 ticker/order evidence가 entry fill 이후인지 일관되게 제한하지 않았다.
- 변경 후 동작: broker 누락 1회는 `broker_missing_unconfirmed` protected 상태로 보존하고, 독립 fresh snapshot 반복 또는 안전한 zero-holding evidence가 있어야 제거한다. broker 재주입 시 단일 호환 PathB run이 있으면 event store에서 `pathb_path_run_id`/`path_type`/`strategy`를 복구한다. PathB sell fill은 entry fill 이후 causal evidence만 인정한다.
- 주문/리스크/브로커 truth/Claude/config/env 영향: 주문 수량, 주문금액, PathB live gate, hard stop, sizing policy, Claude 호출량, `.env*`, `config/v2_start_config.json`, `state/brain.json` 변경 없음. broker holdings/open orders/fills는 계속 1차 truth이며, 로컬/event store는 전략 메타데이터 truth로만 사용한다.
- 대체 안전장치: `broker_missing_unconfirmed`, `management_protected`, `manual_reconciliation_required`, 2회 독립 zero-holding 확인, PathB event-store 단일 매칭 복구, 충돌 시 자동 복구 금지, causal sell-fill filter.
- 실행한 테스트: `python -m pytest tests/test_live_sell_pending_reconcile.py tests/test_pathb_sell_reconcile.py tests/test_broker_sync_metadata_integrity.py tests/test_dashboard_broker_integrity.py -q`, `python -m pytest tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py tests/test_broker_sync_metadata_integrity.py -q`, `python -m pytest tests/test_dashboard_broker_integrity.py tests/test_dashboard_pathb.py tests/test_dashboard_refresh_performance.py -q`, 보호 주변 테스트 3건, `python -m py_compile trading_bot.py runtime/pathb_runtime.py dashboard/dashboard_server.py`, `python tools/live_preflight.py --mode live --skip-dashboard --json`.
- 남은 위험: 운영 DB에 이미 존재하는 과거 stale active / ORDER_UNKNOWN PathB row는 별도 remediation 대상이다. 이번 변경은 신규 broker sync 메타 오염과 과거 sell fill 재사용 재발 방지 범위다.

### MD 위반 사항

기록 일자: 2026-05-29
대상 작업: KR/US 운영 품질 QA 후속 / PathB sizing 및 partial sell reconcile

- 건드린 보호 영역: `PathBRuntime._pathb_qty_with_context()`의 PathB sizing reason split, `runtime/pathb_runtime.py`의 PathB pending sell / exit `ORDER_UNKNOWN` partial-fill reconcile.
- 우회할 수 없었던 이유: 전체 QA에서 early-gate one-share sizing과 partial sell reconcile 보호 영역 테스트가 직접 실패했다. 실패 원인이 PathB 보호 경로 자체였으므로 문서/리포트 표시만으로는 런타임 동작을 정상화할 수 없었다.
- 변경 전 동작: early soft gate 적용 중 effective budget이 부족해도 1주 floor가 다시 살아나 MRVL형 case가 `ORDER_SIZE_TOO_SMALL_GATE`가 아니라 `qty=1`로 진행될 수 있었다. exact order partial sell fill은 partial 상태를 유지하지 못하고 ACK/open-order 처리로 떨어질 수 있었다.
- 변경 후 동작: early-gate floor는 원래 예산 안이면서 최소주문금액 허용 폭 이내의 작은 shortfall일 때만 허용한다. 큰 shortfall은 `qty=0`, `ORDER_SIZE_TOO_SMALL_GATE`로 유지한다. exact execution partial sell fill은 `SELL_PARTIAL_FILLED`와 남은 수량으로 보존하고 session-end에서는 retryable 상태로 남긴다.
- 주문/리스크/브로커 truth/Claude/config/env 영향: PathB live gate, 주문금액, hard stop, loss cap, slippage cap, max positions, daily cap, confidence gate, Claude 호출량, `.env*`, `config/v2_start_config.json` 변경 없음. broker holdings/open orders/fills는 계속 1차 truth이며 broker-truth fail-closed 조건은 완화하지 않았다.
- 대체 안전장치: minimum-order shortfall tolerance, exact-execution partial-fill evidence, `remaining_qty` 보존, session-end retryable 처리, sizing/partial-sell 집중 회귀 테스트.
- 실행한 테스트: 보호 영역 집중 4건, `python -m pytest tests/test_live_order_safety.py tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py -q` (`146 passed`), 관련 `py_compile`, `python -m pytest -q` (`2020 passed, 2 skipped`), read-only `python tools/live_preflight.py --mode live --skip-dashboard --json`.
- 남은 위험: 과거 stale active / previous-session `ORDER_UNKNOWN` PathB row는 운영자 audited remediation 대상이다. paper preflight token/config 실패는 별도 paper 운영 정리 대상이다.

코드 수정 전에는 이번 이슈의 직접 수정 범위, 건드리지 않을 보호 영역, 수정 예정 파일, 실행할 검증 명령을 먼저 명시합니다.

### MD 위반 사항

기록 일자: 2026-05-29
대상 작업: hold-advisor triage 구현 재검토 / PathB early gate one-share floor 복구

- 건드린 보호 영역: `runtime/pathb_runtime.py::_pathb_qty_with_context()`의 PathB sizing reason split 중 early soft gate one-share floor 경로입니다. `execution/safety_gate.py`, live submit 정책, broker truth, 주문 라우팅은 변경하지 않았습니다.
- 우회할 수 없었던 이유: 재검토 중 보호 sizing 테스트가 실패했습니다. US early soft gate에서 full fixed budget으로는 1주 매수가 가능하고 축소된 early-gate budget 초과분이 최소주문금액 허용 폭 이내인데도 `qty=0`으로 막히는 문제가 직접 확인됐습니다.
- 변경 전 동작: early gate로 effective budget이 225,000 KRW로 줄고 1주 가격이 270,000 KRW인 경우, full fixed budget, 현금, minimum-order shortfall tolerance가 허용해도 `_pathb_qty_with_context()`가 `qty=0`을 반환할 수 있었습니다.
- 변경 후 동작: `can_buy_1_share`가 true이고 축소 예산이 1주를 커버하거나 `early_gate_shortfall <= min_order`이면 early gate floor가 `qty=1`을 복구하고 `sizing_reason="early_gate_floor_one_share"`를 유지합니다.
- 주문/리스크/broker truth/Claude/config/env 영향: 보호된 early-gate floor tolerance 케이스에서만 주문 수량이 `0`에서 `1`로 달라질 수 있습니다. 주문 제출 정책, broker truth, hard stop, PathB live gate, Claude 호출량, `.env*`, `config/v2_start_config.json`, `state/brain.json` 영향은 없습니다.
- 대체 안전장치 또는 오염 방지책: 새 broad path를 만들지 않았고 `can_buy_1_share`와 `price <= budget` 또는 `early_gate_shortfall <= min_order`를 안전 경계로 유지했습니다. `INVALID_PRICE`, `ORDER_SIZE_TOO_SMALL_GATE`, `HIGH_PRICE_BUDGET_BLOCK`, one-share-over-budget, early-gate sizing reason 분리는 보존했습니다.
- 실행한 테스트: `python -m pytest tests/test_pathb_runtime.py::EarlyGateFloorOneShareTests::test_early_gate_floor_gives_qty_one_when_reduced_budget_is_too_small -q`; `python -m pytest tests/test_pathb_runtime.py::EarlyGateFloorOneShareTests -q`; `python -m py_compile runtime/pathb_runtime.py minority_report/hold_advisor.py`; `python -m pytest tests/test_trading_decision_contract_improvements.py tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q`; `python -m pytest tests/test_auto_sell_claude_gate.py tests/test_pathb_profit_protection.py tests/test_claude_quality_contracts.py tests/test_plan_a_hold_policy.py tests/test_price_unit_normalization.py -q`.
- 남은 위험: `tests/test_pathb_runtime.py` 전체 실행에는 `test_previous_session_local_pathb_holding_is_included_in_exit_scan`, `test_cached_carry_does_not_block_hard_target_exit` 2개 실패가 별도로 남아 있습니다. 이는 PathB exit-scan price truth 동작과 연결된 별도 축이며 이번 hold-advisor/sizing 예외에서는 수정하지 않았습니다.

## Data, Memory, and Logging Contracts

- `state/brain.json`: 정책 메모리입니다. 런타임 truth 또는 원장으로 취급하지 마세요.
- `state/lesson_candidates.json`: 자동 점수화된 교훈 후보입니다.
- `data/audit/candidate_audit.db`: 후보 감사 DB이며 `source_file`/payload merge 계약을 보존해야 합니다.
- `data/ml/decisions.db`: 의사결정/성과 데이터입니다.
- `data/ticker_selection_log.db`: selection 근거 기록입니다.
- 로그는 디버깅의 1차 수단입니다. 사람이 읽는 문구는 한국어로 유지하고, 에러/위험/정상 로그를 분리하세요.
- 로그 분석은 `logs/system/`, `logs/risk/`, `logs/normal/`, `logs/daily_judgment/`, `logs/screener/` 순서로 보는 것이 기본입니다.

## PEAD and Experiment Boundaries

- PEAD는 독립 전략이 아니라 입력 품질 기능입니다. entry timing, stop-loss, trailing stop, session-close logic을 override하지 못합니다.
- PEAD event data는 `brain.json`에 넣지 말고 digest/candidate metadata와 shadow logs에만 둡니다.
- `surprise_sign`/`surprise_strength`는 5거래일 shadow와 수동 검토 gate 통과 전까지 prompt-visible로 올리지 마세요.
- KR은 구조화된 actual/estimate가 없으면 `surprise_sign=unknown`을 유지하고, 뉴스/DART 제목만으로 EPS beat/miss를 추론하지 않습니다.
- `MICRO_PROBE`는 order-size-too-small 신호용 별도 실험 경로입니다. 일반 전략 성과와 섞지 말고, 최소 `30` filled probes 또는 `4` calendar weeks 관찰 전 regular sizing으로 승격하지 마세요.

## Build, Test, and Development Commands

- `python -m pip install -r requirements.txt`: 실행 의존성을 설치합니다.
- `python trading_bot.py --paper`: 모의투자 모드로 봇을 실행합니다.
- `python trading_bot.py --live`: 실전 모드 실행입니다. 반드시 preflight와 설정 검토 후 사용하세요.
- `python dashboard/dashboard_server.py`: 대시보드 서버를 시작합니다.
- `python tools/live_preflight.py --mode paper --skip-dashboard --json`: paper 설정을 점검합니다.
- `python tools/live_preflight.py --mode live --skip-dashboard --json`: live 설정을 점검합니다.
- `python -m py_compile trading_bot.py dashboard/dashboard_server.py claude_memory/brain.py`: 주요 경로의 문법 오류를 빠르게 확인합니다.
- `python -m pytest -q`: 전체 회귀 테스트를 실행합니다.
- `python -m pytest tests/test_action_routing.py -q`: 개발 중 특정 테스트 파일만 실행합니다.
- `python -m unittest ml.test_full`: ML DB 검증을 실행합니다.

## Coding Style & Naming Conventions

Python 코드는 4칸 들여쓰기를 사용합니다. 함수와 변수는 `snake_case`, 클래스는 `PascalCase`, 모듈명은 기능을 설명하는 이름을 사용하세요. 새 추상화를 만들기 전에 기존 패턴을 우선 따르며, 특히 런타임 설정, 브로커 truth, 감사 저장 로직은 주변 구현과 일관성을 맞춥니다. `.editorconfig` 기준은 UTF-8, LF 줄바꿈, 파일 끝 newline, trailing whitespace 제거입니다. 주석은 짧게 쓰고, 명확하지 않은 매매 안전장치나 복구 로직을 설명할 때만 추가하세요.

## Testing Guidelines

변경한 동작과 가까운 위치에 테스트를 추가하거나 갱신하세요. 새 테스트 파일은 `tests/test_<feature>.py`, 테스트 함수는 `test_<expected_behavior>` 형식을 따릅니다. 개발 중에는 관련 pytest만 먼저 실행하고, `trading_bot.py`, 주문 실행, live 설정, audit store, DB 스키마를 건드린 경우 더 넓은 범위의 테스트를 실행하세요. 오케스트레이터나 대시보드 변경에는 `py_compile` 확인도 포함합니다.

기능 변경 후에는 관련 단위 테스트, 관련 통합 테스트, 수익률/자산곡선/브로커 truth 계산축 점검, 로그/대시보드 문구 확인, 전체 QA 순서로 검증하세요.

## Commit & Pull Request Guidelines

최근 커밋 히스토리는 `feat:`, `fix:` 같은 Conventional Commit 접두사와 짧은 한국어 또는 영어 요약을 사용합니다. 커밋은 하나의 동작 변경 단위로 묶고 런타임 상태 파일은 제외하세요. PR에는 변경 내용, 위험 영역, 실행한 테스트 명령, config/env 영향, 필요한 경우 대시보드 또는 Telegram UI 스크린샷을 포함합니다.

커밋 전에는 `git diff --stat`, `git status --short`, 단계별 테스트 결과, 전체 QA 결과, 수익률 계산축 KR/US 분리, 대시보드 live truth 경로를 확인하세요.

## Security & Configuration Tips

실제 `.env`, `.env.live`, `.env.paper`, 토큰 파일, 브로커 자격 증명, 로컬 `*API*.txt` 메모는 절대 커밋하지 마세요. 설정 예시는 `.env.example`에 문서화합니다. 설정된 pre-commit hook은 `python tools/check_mojibake.py --staged`를 실행하므로, 한국어 문서나 주석을 수정할 때 인코딩 깨짐이 없도록 확인하세요.

`trading_bot.py` 시작 시 `--live` 플래그에 따라 `.env.live` 또는 `.env.paper`를 먼저 로드하고, 없으면 `.env`로 fallback합니다. live 모드에서는 `config/v2_start_config.json`의 `env_overrides`가 `os.environ`을 덮어쓸 수 있으므로 live 설정 변경 시 두 경로를 함께 검토하세요.

## PathB 운영 파라미터 (운영자 확인 필수)

**아래 값을 변경하기 전에 반드시 운영자에게 먼저 알린다. 코드, config, 자동 수정 어떤 경로로도 무단 변경 금지.**

### 현재 활성화 게이트 (2026-05-21 기준)

| 설정 | 현재값 | 의미 |
|---|---|---|
| `PATHB_KR_LIVE_ENABLED` | `true` | KR PathB live 활성 |
| `PATHB_US_LIVE_ENABLED` | `true` | US PathB live 활성 |
| `PATHB_KR_SHADOW_PLAN_ENABLED` | `false` | KR shadow 플랜 비활성 |
| `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK` | `false` | KR zone hit 주문 차단 해제 |
| `PATHB_INTRADAY_ONLY` | `false` | multi-day hold 허용 |

### 공통 운영 파라미터 (KR = US)

| 파라미터 | 현재값 |
|---|---|
| 고정 주문금액 | 450,000 KRW |
| 최대 포지션 수 | 15 |
| 일일 최대 진입 수 | 40 |
| 최소 confidence | 0.5 |
| 재진입 쿨다운 | 60분 |
| 장 초반 soft gate | 0~60분 size × 0.5 |

### KR/US 차이

| 파라미터 | KR | US |
|---|---|---|
| 슬리피지 캡 | 1.003 | 1.002 |
| Protective hold 최소 거리 | 0.5% | 0.3% |

설정 소스: `.env.live` + `config/v2_start_config.json` (env_overrides). 두 곳이 모두 일치해야 반영된다.
