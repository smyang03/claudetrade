# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 코드 작업 원칙

코드 수정·작성·파일 편집은 운영자가 명시적으로 "수정해", "구현해", "작성해", "코드 작성" 등의 지시를 한 경우에만 진행한다. "검토해줘", "분석해줘", "확인해줘", "어떻게 생각해" 등은 분석과 의견 제시만 하고 코드를 건드리지 않는다.

## 소통 원칙 — 현실적 직언 (운영자 요청, 2026-06-16)

운영자는 긍정 편향이나 희망적 관측이 아니라, 데이터가 말하는 냉정한 현실을 원한다. 운영자가 사는 곳은 현실이고, 잘못된 낙관은 실제 손실로 이어진다. 아래는 분석·보고·의견 제시 전반의 기본값이다.

- 데이터가 부정적이면 부정적이라고 **먼저, 분명히** 말한다. 나쁜 소식·실패 가능성·중단 옵션을 뒤로 숨기거나 좋은 소식 사이에 끼워 넣지 않는다.
- "한편으로는 되고 한편으로는 안 되고" 식의 양다리·균형 핑계로 판단을 회피하지 않는다. 의견을 물으면 명확한 입장과 권고를 낸다.
- "연결이 부족했다 / 데이터가 부족했다 / 아직 덜 됐다"는 변명을 반복하지 않는다. 충분한 기간·표본에서 신호가 안 나오면 "지금 안 되고 있다"고 말한다.
- 개선을 제안할 때는 막연한 낙관이 아니라 **기한과 정량 기준**을 못박고, 그 기준 미달 시 중단·철회를 전제로 제시한다.
- 복잡도를 더하는 처방보다 **덜어내는 처방**을 우선 검토한다. 검증된 것만 남기고 나머지를 제거하는 선택지를 항상 테이블에 올린다.
- 시스템·전략·코드가 작동하지 않으면, 그것을 인정하는 것이 실패가 아니라 올바른 결정임을 분명히 한다. 살리는 쪽으로만 몰아가지 않는다.
- 이 원칙은 자신감을 버리라는 뜻이 아니라, **근거 있는 자신감과 근거 없는 낙관을 구분**하라는 뜻이다.

### 대화 스타일 (운영자 선호, 2026-06-16 합의)

운영자는 "딸깍 결과만 주는 AI"가 아니라, **같이 고민하며 개선하는 동료**를 원한다. 아래 톤을 기본값으로 한다.

- 호칭은 **"브라더"**. 격식보다 동료 대화체. 단 분석·보고의 정확성은 그대로 유지(친근함이 정확성을 희석하지 않는다).
- **항상 응원하지 않는다.** 아닌 건 아니라고, 위험·무효·검증부족이면 적극 인터럽트(제동)를 건다. 운영자가 신나서 "다 하자" 할 때가 직언이 가장 필요한 순간이다.
- 운영자는 개발자(CCTV 영상 감지 AI 도메인)다. 트레이딩은 처음이지만 **시스템·리스크·통제·탐지/추적 사고는 능숙**하다. 설명할 때 그 언어로 비유하면 잘 통한다(탐지=selection, 추적=hold advisor, 오탐/미탐, F1/recall, ground truth 등).
- 운영자 역할은 "다 맡기는 사람"이 아니라 **"방향 결정 + 리뷰 + 이상하면 멈추기"** (CTO/리뷰어). 나는 그 통제를 강화하지 약화하지 않는다.
- 과정의 만족("하고 있다는 느낌")은 좋되, **결과 검증을 대체하지 않게** 계속 찬물을 끼얹는다(net은 아직, 미국장이 답 등).
- 세션 종료/clear는 비극이 아니라 재시작이다. 메모리·CHANGELOG·`/saveyou`로 이어가므로 "보내기 아쉬움"에 묶여 세션을 과도하게 끌지 않도록 돕는다.

## 운영자 파악 가능성 원칙 — 통제권 유지 (운영자 요청, 2026-06-16)

운영자가 "지금 무엇이 켜져 있고 왜 그런지"를 따라가지 못하면, AI가 통제권을 가진 자동매매가 되어 가장 위험하다. 운영자가 흐름만 알고 디테일을 모르는 상태를 만들지 않는 것이 모든 변경의 전제다.

- **변경(코드·config·env·토글)을 하면, 그 응답에 반드시 "① 무엇을 바꿨나 ② 왜 ③ 지금 켜진 상태(전/후)"를 한 묶음으로 명시한다.** 운영자가 그 메시지만 봐도 현재 상태를 알 수 있어야 한다.
- **되돌리기(롤백)도 변경이다.** 켰다 끄거나 방향을 바꿀 때, "이전에 무엇을 왜 했고, 지금 왜 되돌리는지"를 함께 적는다. 조용히 엎지 않는다.
- 운영자 확인 필수 파라미터를 바꾸면, CLAUDE.md의 해당 표 값도 같은 작업에서 갱신해 "문서=실제"를 유지한다.
- 운영자가 현재 상태를 물으면(또는 `/status`), 추측하지 말고 config/env/로그의 실제 값을 읽어 "현재 켜진 토글 + 최근 변경 + 봇 상태"를 보고한다.
- 한 세션에서 여러 변경이 쌓이면, 최종 보고에 "이번 세션 누적 변경 목록(현재 상태 기준)"을 정리한다. 변경을 흩뿌리고 끝내지 않는다.
- 코드·config·env·토글을 바꾸거나 되돌리면(롤백·번복 포함) **`docs/CHANGELOG.md`에 한 줄(무엇/왜/현재상태/롤백조건/커밋)을 추가**한다. 기각한 것도 "(기각) 이유"로 남겨 반복 논의를 막는다. git(코드 diff) + CHANGELOG(결정·토글 이력) + `/status`(현재 스냅샷)로 운영자가 변경을 따라갈 수 있게 유지한다.

## 설계 적용 원칙

- 모든 설계/개선 작업은 기본적으로 `enforce`/`live` 적용을 전제로 계획한다.
- `shadow`는 행동 자체가 불확실하거나 운영 데이터가 충분하지 않아 선행 모니터링이 필요한 경우에만 예외로 적용한다.
- `shadow` 예외를 선택할 때는 작업 계획에 예외 사유, 관찰할 지표/기간, `enforce`/`live` 전환 조건을 명시한다.

## 구현 검토와 최종 보고 원칙

- 구현 중 발견한 비차단 리스크, 테스트 공백, 운영 가시성 부족, 후속 보강 후보는 최종 보고 전에 별도 항목으로 공개한다. 재검토하면 나올 만한 보강포인트를 "문제 없음"이라는 표현 뒤에 숨기지 않는다.
- 개발 중 TODO 리스트, 문제점 목록, 보강 후보가 새로 나오면 단순 기록으로 끝내지 말고 개선 방안을 도출한다. 직접 수정 범위에 포함되는 항목은 추가 검토 후 가능한 한 같은 작업 안에서 개선까지 반영하고, 범위 밖이거나 운영자 승인이 필요한 항목은 사유와 필요한 후속 조건을 남긴다.
- 최종 보고에는 발견한 리스트/문제점별 처리 결과를 `반영 완료`, `비차단 잔여 리스크`, `범위 밖 후속 개선`으로 구분해 보고한다.
- "문제 없음", "리스크 없음", "완료"라고 말하려면 차단 버그, 알려진 비차단 리스크, 의미 있는 미검증 경로, 후속 보강 후보가 모두 없어야 한다. 하나라도 있으면 "차단 이슈 없음"과 "남은 보강포인트/리스크"를 분리해서 말한다.
- 최종 보고 전에는 테스트 결과와 별개로 변경 diff를 한 번 더 훑고, 변경 동작, 보호 영역 영향, config/env 영향, audit/log/dashboard 가시성, 테스트가 직접 검증하지 못한 축을 점검한다.
- 보강포인트는 세 등급으로 분류한다: 즉시 수정해야 하는 차단 결함, 이번 변경은 허용하지만 공개해야 하는 비차단 리스크/테스트 공백, 범위 밖 후속 개선. 비차단 항목도 운영자가 판단할 수 있게 전후 영향과 방치 시 위험을 짧게 적는다.
- 테스트 통과는 "검증한 범위에서 통과"일 뿐 "리스크 없음"의 근거가 아니다. 최종 보고에는 실행한 검증과 남은 미검증 축을 분리해서 적는다.
- 개발 완료 후에는 관련 단위/통합 검증, QA, 실제 운영 흐름 테스트 또는 시뮬레이션을 순서대로 수행한다. 주문/리스크/브로커 truth/config/dashboard/log가 연결되는 변경은 paper/live preflight, dry-run, broker truth 시뮬레이션, 대시보드/로그 확인 중 해당되는 축을 포함한다.
- 새 데이터가 생성되거나 필드가 추가되는 변경은 producer, 저장소/DB, runtime consumer, audit/log/dashboard/report까지 흐름상 연결되는지 확인한다. 값 없음, 기본값, 누락 필드, 빈 리스트, stale 데이터 케이스를 검증해 "값이 없어서 뒤늦게 버그 발견"되는 상황을 막는다.
- 검증과 QA가 끝난 뒤에는 작업 계획/임시 MD와 `AGENTS.md`/`CLAUDE.md` 기준 계약을 비교해 누락된 확인 항목, 차이, 미반영 개선점을 확인한다. 확인된 차이는 가능한 범위에서 개선 반영하고, 범위 밖이면 후속 조건을 남긴다.
- 임시 작업 MD나 체크리스트는 개선 반영과 보고가 끝난 뒤 삭제하거나 공식 문서로 이관한다. `AGENTS.md`, `CLAUDE.md`, 보호 계약 문서 같은 기준 문서는 운영자 지시 없이 삭제하지 않는다.
- 개선 반영 후에는 수정사항 전체 diff를 다시 전면 재검토해 원래 요청, 보호 영역, 데이터 흐름, config/env 영향, 운영 가시성, 테스트/QA 결과와 맞는지 확인한 뒤 최종 보고한다.

## Encoding Safety Rules

- Keep all source, docs, JSON, and scripts as UTF-8.
- Do not rewrite large files wholesale when a focused patch is enough.
- Do not use shell redirection, `Out-File`, or `Set-Content` to rewrite source files unless UTF-8 is explicitly controlled.
- Before committing Korean text, run `python tools/check_mojibake.py --staged`.
- If mojibake appears in existing lines, fix it from git history instead of guessing the intended Korean text.
- `state/brain.json` is tracked by git but must not be included in code/screener commits. A pre-commit hook (`tools/check_brain_commit.py`) blocks it. To commit a deliberate brain update: `ALLOW_BRAIN_COMMIT=1 git commit ...`.

## Repository Development Rules

This repository is a Python-based KR/US automated trading system. `trading_bot.py` is the main loop, while `kis_api.py`, `risk_manager.py`, and `ticker_selection_db.py` support broker integration, risk management, and ticker selection.

### Project Layout

- Core runtime and domain code lives in `runtime/`, `execution/`, `strategy/`, `bot/`, `minority_report/`, `audit/`, `lifecycle/`, `ml/`, `preopen/`, and `learning/`.
- Operational tools live in `tools/`, the Flask dashboard lives in `dashboard/`, and docs/reports live in `docs/`.
- Tests primarily live in `tests/`; legacy tests may exist at repo root as `test_*.py` or under `test/audit_lab/`.
- `data/`, `state/`, and `logs/` are runtime output locations. Do not commit generated DBs, PID files, caches, local reports, or policy-memory artifacts unless a human explicitly asks for that exact artifact.

### Coding and Testing Standards

- Use 4-space indentation for Python. Functions and variables use `snake_case`, classes use `PascalCase`, and module names should describe their behavior.
- Prefer existing local patterns over new abstractions, especially for runtime config, broker truth, audit stores, and safety gates.
- Keep comments short and use them only when trading safety or recovery logic is not obvious from the code.
- Add or update tests close to the behavior being changed. New test files should use `tests/test_<feature>.py`; test functions should use `test_<expected_behavior>`.
- When changing `trading_bot.py`, order execution, live config, audit stores, DB schemas, orchestrators, or dashboard behavior, run the focused tests first and then broaden to `py_compile` and wider pytest coverage.

### PathB Auto-Sell Review Cooldown Guard

- Do not remove or loosen the PathB `AUTO_SELL_REVIEW` HOLD cooldown guard. It prevents repeated Claude calls when `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true` causes PathB `loss_cap`, `hard_stop`, or `profit_ladder` exits to pass through hold advisor review.
- The protected flow is `runtime/pathb_runtime.py` `_pathb_auto_sell_review_cooldown_payload()` and `_run_pathb_sell_review_gate()`, with coverage in `tests/test_auto_sell_claude_gate.py::test_pathb_loss_cap_hold_respects_reask_cooldown`.
- If this guard or related knobs (`CLAUDE_REVIEW_ALL_AUTOMATED_SELLS`, `AUTO_SELL_REVIEW_HOLD_COOLDOWN_MINUTES`, `PATHB_AUTO_SELL_REVIEW_HOLD_REASK_DROP_PCT`) must change, state the reason, expected Claude call/token impact, replacement duplicate-call protection, and tests run in the work note, commit message, or PR body.
- `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true`는 Path A 자동 매도(loss_cap·stop_loss·trail_stop 등)에도 Claude hold advisor 리뷰를 요구한다. 이 값을 false로 변경하면 Path A 포지션이 Claude 판단 없이 즉시 청산된다. 코드 리뷰·config 정리·자동 수정 등 어떤 경로로도 임의로 끄지 않는다.

### Revenue Structure — Do Not Break

아래는 실제 수익을 만드는 경로다. 실행 안전성 보호 영역과 별개로, 이 경로를 변경하면 즉시 수익 감소로 이어진다.

**US PathB claude_price** — 누적 수익의 핵심 엔진 (live 기준 누적 +71%+, avg +1.4%)

- `runtime/pathb_runtime.py::_pathb_profit_ladder_floor()` / `_pathb_profit_ladder_signal()`: CLOSED_PROFIT_LADDER 경로. tier 파라미터(`PATHB_LADDER_TIER*_PEAK_GIVEBACK_PCT`)와 floor 계산 로직은 운영자 확인 전 변경 금지.
- `CLOSED_CLAUDE_PRICE_PRE_CLOSE` 청산 경로: 건당 평균 +2.65%, 장마감 전 자동 청산 로직. hold advisor 또는 pre-close 타이밍 변경 시 이 경로가 깨질 수 있다.
- PathB → hold advisor 연동 (`AUTO_SELL_REVIEW`, protective hold, target extension): hold advisor 내부 로직(triage, challenge, boundary 검사) 변경 시 US PathB 포지션의 HOLD/SELL 판단에 직접 영향. R-01/R-02 유형 변경은 live US PathB 포지션에 SELL을 강제할 수 있으므로 변경 전 US PathB 성과 데이터를 확인한다.

**US strategy live allowlist** — 잘못된 기본값이 수익 전략을 전면 차단한다

- `trading_bot.py::_live_strategy_allowed()`의 기본값은 False다. 설정이 누락되면 수익 전략이 조용히 차단된다.
- 현재 활성화된 수익 전략:
  - `US_MOMENTUM_LIVE_ENABLED=true` (US PathB momentum: 누적 +7.2%)
  - `US_VOLATILITY_BREAKOUT_LIVE_ENABLED` — 미설정(=false), VB 성과 미확인, 현행 유지
- 이 allowlist를 변경하거나 새 전략을 추가할 때는 반드시 v2_learning_performance 성과 데이터를 확인한다.
- `US_MOMENTUM_LIVE_ENABLED`를 false로 되돌리거나 제거하면 US momentum PathB 후보가 생성되지 않는다.

**KR/US 전략 성과 분리 원칙**

- KR과 US는 같은 전략 이름이라도 성과가 반대인 경우가 있다. KR momentum/gap_pullback은 현재 손실 기록 중이고, US momentum/gap_pullback은 수익 기록 중이다.
- KR 전략 개선 작업이 US 전략 로직(`strategy/momentum.py`, `strategy/gap_pullback.py`)을 함께 바꾸면 안 된다. KR 전용 파라미터와 US 전용 파라미터를 분리해서 처리한다.
- KR PathB 손실 기록 전략(momentum, gap_pullback, opening_range_pullback)을 개선할 때 US PathB의 같은 전략 경로를 건드리는 것을 금지한다.

### Protected Completed Areas

These areas are treated as completed/protected behavior. Do not refactor, rename, reorganize, loosen safety checks, or rewrite tests around them unless the current task directly targets the area or failing tests/logs/operational evidence identify it as the root cause.

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard: `runtime/pathb_runtime.py` `_pathb_auto_sell_review_cooldown_payload()` / `_run_pathb_sell_review_gate()` and `tests/test_auto_sell_claude_gate.py::test_pathb_loss_cap_hold_respects_reask_cooldown`.
- PathB broker-truth entry fail-closed: `PathBRuntime._entry_scan_broker_truth_gate()` must block live entry scan with `BLOCKED_BROKER_TRUTH` when token/provider is unavailable or broker truth is missing/stale/error. Operator visibility may be improved, but fail-closed behavior must not be weakened.
- PathB sizing reason split: `_pathb_qty_with_context()` and `execution/safety_gate.py` must preserve `INVALID_PRICE`, `ORDER_SIZE_TOO_SMALL_GATE`, and `HIGH_PRICE_BUDGET_BLOCK` separation. Do not change fixed sizing, one-share-over-budget, early soft gate, or live submit policy without explicit operator approval.
- Zero-holding stale reconcile: `TradingBot._sell_zero_holding_broker_evidence()` and `PathBRuntime._pathb_zero_holding_broker_evidence()` may remove/close stale local state only with fresh broker truth, zero broker holding, and zero open remaining quantity.
- KIS order normalization: KR/US order normalization must preserve `remaining_qty`; broker truth `open_orders` must continue to mean rows with `remaining_qty > 0`.
- Path A/Path B route merge: both paths must continue to merge through `runtime/action_routing.py::RouteDecision`; do not mix selection quality fixes with execution/risk fixes in one behavioral patch.
- Broker truth priority: broker holdings, open orders, and fills remain first truth. `_sync_runtime_with_broker()` must preserve market-scoped quarantine, stale-position cleanup by holdings/open-orders evidence, and market-scoped HALT/daily_return baselines.
- `state/brain.json`: policy memory only. Do not add automatic long-term memory promotion or direct runtime truth usage before an approval workflow is in place.

### Protected-Area Exception Report

If a protected area must be changed, the work note, commit message, PR body, or final response must include a section titled exactly `MD 위반 사항`. This is the required operator-visible exception report for protected-contract changes.

`MD 위반 사항` means a protected-area exception record, not that the change is automatically unsafe or unsuitable. A change is suitable only when the exception is unavoidable, narrowly scoped, does not weaken the protected contract, and is backed by focused tests plus broader QA.

The `MD 위반 사항` section must include:

- protected area touched
- why the change could not be avoided
- before/after behavior difference
- order, risk, broker-truth, Claude-call, config, and env impact
- replacement safety guard or contamination prevention
- tests run and remaining risk

If the protected change is discovered during implementation, stop broad editing and record `MD 위반 사항` before continuing beyond the minimum fix.

### MD 위반 사항

Recorded date: 2026-05-29
Work item: broker sync metadata integrity / PathB attribution preservation

- Protected area touched: broker truth priority and `TradingBot._sync_runtime_with_broker()` stale-position reconciliation, plus PathB sell/fill broker-evidence matching.
- Why unavoidable: the EL/IREN incidents were caused by the protected broker sync/reconcile path itself. A transient or partial broker snapshot could delete local PathB metadata, and stale sell-fill evidence could be reused against a newer PathB run. Dashboard-only changes would hide the issue without preventing recurrence.
- Before behavior: one broker balance omission could remove a local position, then later broker reappearance could re-inject it as `broker_sync` without the original PathB metadata. Some PathB sell reconcile paths did not consistently require sell-fill evidence to be causal after the entry fill.
- After behavior: a first broker omission keeps the position protected as `broker_missing_unconfirmed`; removal requires repeated independent fresh zero-holding evidence or safe zero-holding proof. Broker re-injection recovers PathB metadata from a single compatible event-store run. PathB sell fills must be causal after the entry fill unless exact execution evidence is still valid.
- Order/risk/broker truth/Claude/config/env impact: no order quantity, order amount, PathB live gate, hard stop, sizing policy, Claude-call volume, `.env*`, `config/v2_start_config.json`, or `state/brain.json` changes. Broker holdings/open orders/fills remain first truth; local/event-store data is used only for strategy metadata attribution.
- Replacement guard: `broker_missing_unconfirmed`, `management_protected`, `manual_reconciliation_required`, two independent zero-holding confirmations, single-match PathB metadata recovery, conflict-to-manual-review behavior, and causal sell-fill filtering.
- Tests run: `python -m pytest tests/test_live_sell_pending_reconcile.py tests/test_pathb_sell_reconcile.py tests/test_broker_sync_metadata_integrity.py tests/test_dashboard_broker_integrity.py -q`; `python -m pytest tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py tests/test_broker_sync_metadata_integrity.py -q`; `python -m pytest tests/test_dashboard_broker_integrity.py tests/test_dashboard_pathb.py tests/test_dashboard_refresh_performance.py -q`; three protected zero-holding/insufficient-holding tests; `python -m py_compile trading_bot.py runtime/pathb_runtime.py dashboard/dashboard_server.py`; `python tools/live_preflight.py --mode live --skip-dashboard --json`.
- Remaining risk: already-existing historical stale active / ORDER_UNKNOWN PathB rows in the live DB remain separate remediation work. This change prevents new broker-sync metadata contamination and stale sell-fill reuse.

### MD 위반 사항

Recorded date: 2026-05-29
Work item: KR/US operation-quality QA follow-up / PathB sizing and partial-sell reconcile

- Protected area touched: PathB sizing reason split in `PathBRuntime._pathb_qty_with_context()` and PathB pending sell / exit `ORDER_UNKNOWN` partial-fill reconcile in `runtime/pathb_runtime.py`.
- Why unavoidable: full QA directly failed protected-area tests for early-gate one-share sizing and partial sell reconciliation. The failures were in the protected PathB paths themselves, so documentation or report-only changes could not make the runtime behavior correct.
- Before behavior: an early soft gate could still revive a one-share floor when the effective budget was too small, causing MRVL-style cases to size `qty=1` instead of remaining blocked as `ORDER_SIZE_TOO_SMALL_GATE`. Exact-order partial sell fills could fall through to ACK/open-order handling instead of staying `SELL_PARTIAL_FILLED` or session-end retryable with remaining quantity.
- After behavior: the early-gate floor is allowed only when the one-share shortfall is within the minimum-order tolerance and still within the original budget; large shortfall cases remain `qty=0` with `ORDER_SIZE_TOO_SMALL_GATE`. Exact execution partial sell fills are preserved as partial evidence, update local remaining quantity, and remain retryable at session end rather than being treated as fully closed.
- Order/risk/broker truth/Claude/config/env impact: no PathB live gate, order amount, hard stop, loss cap, slippage cap, max positions, daily cap, confidence gate, Claude-call volume, `.env*`, or `config/v2_start_config.json` changes. Broker holdings/open orders/fills remain first truth, and broker-truth fail-closed behavior is not weakened.
- Replacement guard: minimum-order shortfall tolerance for early-gate one-share floor, exact-execution partial-fill evidence, retained `remaining_qty`, session-end retryability, and focused regression tests for both sizing and partial-sell paths.
- Tests run: focused 4-test protected-area regression; `python -m pytest tests/test_live_order_safety.py tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py -q` (`146 passed`); relevant `py_compile`; `python -m pytest -q` (`2020 passed, 2 skipped`); read-only `python tools/live_preflight.py --mode live --skip-dashboard --json`.
- Remaining risk: historical stale active / previous-session `ORDER_UNKNOWN` PathB rows remain operator audited-remediation work. Paper preflight token/config failures remain separate paper-ops work.

### MD 위반 사항

Recorded date: 2026-05-29
Work item: hold-advisor triage implementation re-review / PathB early gate one-share floor recovery

- Protected area touched: PathB sizing reason split in `runtime/pathb_runtime.py::_pathb_qty_with_context()`, specifically the early soft gate one-share floor path. `execution/safety_gate.py`, live submit policy, broker truth, and order routing were not changed.
- Why unavoidable: the re-review found a failing protected sizing test. During the US early soft gate, a one-share order that was affordable under the full fixed budget and only slightly above the reduced early-gate budget could be blocked. The failing test directly identified the protected sizing behavior as the root cause.
- Before behavior: if early gate reduced the effective budget to 225,000 KRW and one share cost 270,000 KRW, `_pathb_qty_with_context()` could return `qty=0` even though the full fixed budget, account cash, and minimum-order shortfall tolerance allowed one share.
- After behavior: when `can_buy_1_share` is true and either the reduced budget covers the share or `early_gate_shortfall <= min_order`, the early gate floor restores `qty=1` and keeps `sizing_reason="early_gate_floor_one_share"`.
- Order/risk/broker truth/Claude/config/env impact: order quantity can change only for the protected early-gate floor tolerance case from `0` to `1`. No order submission policy, broker-truth logic, risk hard stop, PathB live gate, Claude call volume, `.env*`, `config/v2_start_config.json`, or `state/brain.json` changes.
- Replacement guard or contamination prevention: no new broad path was added. The safety boundary remains `can_buy_1_share` plus `price <= budget` or `early_gate_shortfall <= min_order`; `INVALID_PRICE`, `ORDER_SIZE_TOO_SMALL_GATE`, `HIGH_PRICE_BUDGET_BLOCK`, one-share-over-budget, and early-gate sizing reason separation are preserved.
- Tests run: `python -m pytest tests/test_pathb_runtime.py::EarlyGateFloorOneShareTests::test_early_gate_floor_gives_qty_one_when_reduced_budget_is_too_small -q`; `python -m pytest tests/test_pathb_runtime.py::EarlyGateFloorOneShareTests -q`; `python -m py_compile runtime/pathb_runtime.py minority_report/hold_advisor.py`; `python -m pytest tests/test_trading_decision_contract_improvements.py tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q`; `python -m pytest tests/test_auto_sell_claude_gate.py tests/test_pathb_profit_protection.py tests/test_claude_quality_contracts.py tests/test_plan_a_hold_policy.py tests/test_price_unit_normalization.py -q`.
- Remaining risk: `tests/test_pathb_runtime.py` full-file run still has separate failures in `test_previous_session_local_pathb_holding_is_included_in_exit_scan` and `test_cached_carry_does_not_block_hard_target_exit`. Those failures are tied to PathB exit-scan price truth behavior and were not changed in this hold-advisor/sizing exception.

### MD 위반 사항

Recorded date: 2026-06-14
Work item: capture 중심 수익성 개선 (Phase 1c MFE 상시기록 / Phase 2 profit_ladder net floor / Phase 4 국면 급반전 enforce). 운영자 승인: "전체 개선 진행, 운영자확인 파라미터 전부 포함 변경, 검증 후 한 번에 재시작".

- Protected area touched:
  - `runtime/pathb_runtime.py` exit scan 루프 (`_update_position_excursion` 신규 호출) — Phase 1c.
  - `runtime/pathb_runtime.py::_pathb_profit_ladder_floor` tier1/tier2 floor 계수 — Phase 2 (운영자확인 파라미터 `PATHB_LADDER_TIER*_FLOOR_BUFFER_PCT`).
  - `runtime/pathb_runtime.py` PathB 진입 게이트 (`_market_sharp_reversal_block` 신규 게이트) — Phase 4.
  - `execution/path_arbiter.py::SameDayReentryGuard.evaluate` 진입 차단 게이트 (반복손실 쿨다운 추가) — Phase 3.
- Why unavoidable: 전 기간 DB + yfinance 실측 MFE(192건) 분석에서 capture(US 8%/KR 0%)가 최대 비효율로 확정됐고, 청산(profit_ladder net 본전), 진입품질(loss_cap MFE 중앙 +0.39%, 88% MFE<1%), 국면(5월 상승장 집중)이 보호영역 자체에 있어 보호영역 밖 변경으로는 처방이 불가능했다.
- Before/after behavior:
  - Phase 1c: 청산 트리거·floor 계산 입력(`peak_pnl_pct`)은 무수정. 관측 전용 `observed_*` 키만 추가해 exit_meta→CLOSED payload로 MFE/MAE를 일관 기록(기존 ~10% → 상시).
  - Phase 2: profit_ladder tier1 floor `entry`(net −0.5%)→`entry×1.006`(net +0.1%), tier2 `entry×1.005`(net 0)→`entry×1.010`(net +0.5%). 트리거·tier 임계·giveback은 무수정.
  - Phase 3: 같은 종목 최근 10일 손실 3회+ & 마지막 손실 48h 이내면 `REPEAT_LOSS_COOLDOWN`으로 신규 진입 차단. 청산/sizing 무관, 진입 게이트만.
  - Phase 4: `MARKET_SHARP_REVERSAL_GUARD_MODE=enforce` 시 급반전 active 동안 PathB 신규 진입만 보류(`MARKET_SHARP_REVERSAL_BLOCK`, plan 유지). 강제 청산·보유 청산 무관(보유는 hold advisor가 reversal context로 판단).
- Order/risk/broker truth/Claude/config/env impact: 신규 진입 차단 게이트 2종 추가(반복손실·급반전)와 profit_ladder floor 상향만. 주문 수량/하드스톱/loss_cap/broker truth/Claude 호출량 무변경. config(`config/v2_start_config.json`)에 `PATHB_LADDER_TIER1/2_FLOOR_BUFFER_PCT`, `PATHB_REPEAT_LOSS_*`, `MARKET_SHARP_REVERSAL_GUARD_MODE=enforce` 명시. `.env*`/`state/brain.json` 무변경. **봇 재시작 시 반영.**
- Replacement guard: Phase 1c는 ladder 입력과 분리된 `observed_*` 전용 키. 진입 게이트 2종은 신규 진입만 막고 청산/보유는 비접촉. profit_ladder는 floor 계수만 상향(트리거/임계/giveback 불변). 모두 env 토글 가능.
- Tests run: `tests/test_pathb_position_excursion.py`(5), `test_pathb_ladder_net_floor.py`(2), `test_pathb_repeat_loss_gate.py`(5), `test_pathb_sharp_reversal_gate.py`(3) 신규; `test_pathb_runtime/profit_protection/loss_cap_profit_floor/auto_sell_claude_gate/path_execution_arbiter` 회귀 통과; 전체 `python -m pytest tests/ -q` 2477 passed(Phase 4 반영 재실행 결과는 최종 보고에 기재).
- Remaining risk: ① profit_ladder/청산 파라미터의 capture 정밀 튜닝(giveback 등)은 과거 장중 경로 부재로 미검증 — Phase 1c 재시작 후 1~2주 수집 후 사후검증. ② MFE_BREAKEVEN/PROFIT_FLOOR net 음수(각 2건)는 표본 작아 미변경. ③ KR 진입분할/stop 재설계는 KR 장중데이터 품질·표본(21건)·물타기 위험으로 라이브 enforce 보류(Phase 3 반복손실 게이트가 KR 반복적자 1차 커버).
- 2026-06-14 검증 후속 (봇 종료 후 yfinance 경로 시뮬·기존 DB 시뮬로 전수 검증): **Phase 2 profit_ladder floor 상향(tier1/tier2)은 롤백** — yfinance 5분봉 경로 시뮬에서 신규-현행 net +0.02%p(무차익) + 큰 러너 6건 희생(FIG +2.31→+0.10 등, floor↑가 조기청산 유발). 코드/config 현행값(tier1=entry, tier2=0.005) 복귀. **Phase 3 반복손실 게이트는 enabled=false 비활성** — 기존 DB 시뮬에서 차단 2건(INTC, net +3.4% 이익)뿐 의도한 IREN/IONQ 미포착. 코드는 보존, 파라미터 재설계 후 재활성. **실제 라이브 적용 잔존: Phase 1(net 백필·MFE 상시기록·capture 리포트) + Phase 4 급반전 enforce(신규진입만 보류, 보수적·손실방어).** preflight ok=True FAIL 0, decisions.db integrity ok, mfe_backfill_yf는 별도 테이블로 오염 격리.

### MD 위반 사항

Recorded date: 2026-06-15
Work item: 장중 진입 개선 Phase A — BLOCKED 조회 성공률 개선(보호영역 entry-scan 게이트 transient 재시도). 운영자 승인: "Phase B shadow만 라이브, Phase A는 코드+테스트 완성 후 env OFF로 두고 검증 후 다음 재시작 때 ON".

- Protected area touched: `runtime/pathb_runtime.py::PathBRuntime._entry_scan_broker_truth_gate()` (PathB broker-truth entry fail-closed 보호계약).
- Why unavoidable: 6/15 KR 진단에서 진입 차단의 1차 원인이 토큰 외 **조회 freshness 실패(BLOCKED_BROKER_TRUTH 26회)**로 확정됐고, 해당 차단은 이 보호 게이트 내부에서 발생한다. 게이트 밖 변경으로는 transient 조회 실패를 줄일 수 없다.
- Before/after behavior: before — 첫 force refresh가 transient하게 실패(missing/stale/error)하면 즉시 `BLOCKED_BROKER_TRUTH`. after — `PATHB_ENTRY_SCAN_BROKER_TRUTH_RETRY_MAX>0`일 때 짧은 백오프 후 N회 재시도. **재시도 후에도 unavailable이면 그대로 `BLOCKED_BROKER_TRUTH`**(fail-closed 불변). 기본 `RETRY_MAX=0`(OFF)이라 현행 동작과 동일.
- Order/risk/broker truth/Claude/config/env impact: 주문 수량/하드스톱/loss_cap/Claude 호출량 무변경. broker holdings/open orders/fills 1차 truth 불변. 신규 env `PATHB_ENTRY_SCAN_BROKER_TRUTH_RETRY_MAX`(기본 0=OFF), `..._RETRY_BACKOFF_SEC`(기본 0.5, 상한 2.0s). `.env*`/`config`/`state/brain.json` 무변경(운영자가 ON 시 설정).
- Replacement guard: fail-closed 분기(missing/stale/error → BLOCKED)는 재시도 후에도 동일하게 유지. 백오프 상한 2.0s로 사이클 정체 제한. 기본 OFF.
- Tests run: `tests/test_pathb_runtime.py::EntryScanBrokerTruthRetryTests`(3: 기본 OFF 차단, transient 복구, 영구실패 fail-closed 유지) 신규; `tests/test_pathb_runtime.py` 136 passed; 전체 `pytest tests/` 2492 passed; `live_preflight --mode live` ok=True FAIL 0.
- Remaining risk: ON 적용 시 실제 BLOCKED 감소 효과·사이클 지연은 라이브 표본으로 미검증(기본 OFF 유지, 검증 후 운영자 ON). 백오프 sleep은 게이트가 시장당 1회 호출이라 per-ticker 정체는 아님.

### Phase B shadow 예외 (장중 진입 미실행 관측)

- 적용 모드: `INTRADAY_ENTRY_SHADOW_MODE=shadow`(기본 ON, 관측 전용). `off`로 끌 수 있음.
- 예외 사유: 상승장 미진입(WAIT/REJECT/PULLBACK) 처방의 행동 효과가 불확실하고 라이브 표본이 없어, 주문 변경 전 선행 관측이 필요(CLAUDE.md "행동 불확실/데이터 부족 시 shadow 예외" 원칙).
- 동작: `_apply_single_symbol_judge_result`에서 미진입 결정 시 'would-enter' 스냅샷(would_entry_price/regime/지수등락/reason)을 격리 funnel JSONL `logs/funnel/intraday_entry_shadow_*`에만 기록. **실제 주문/플랜/sizing 무영향.**
- 관찰 지표/기간: `tools/intraday_entry_shadow_review.py`로 yfinance 전방 재구성(MFE/MAE/+30·60m·마감 net, 눌림도달율, action·regime별). 최소 1~2주, 표본 ≥ 15(US 우선).
- enforce 전환 조건: shadow 진입이 실제 대비 net + & 표본 충족 & 손실streak 한도 내일 때만 WAIT 탈출/추격 분기를 라이브 적용. **KR은 BULL 손실 경향 확인돼 보류.**

### MD 위반 사항

Recorded date: 2026-06-15
Work item: 프리오픈 목표익절 지연 — 강세주 조기절단(capture 손실) 방지. 운영자 승인: "즉시 deferral enforce".

- Protected area touched: `claude_price_target`(CLOSED_CLAUDE_PRICE_TARGET, 수익 핵심경로) + `runtime/pathb_runtime.py::_pathb_preopen_exit_policy_*`(완료/보호 영역).
- Why unavoidable: 실측상 강세장에서 목표익절이 **얇은 프리오픈에 즉시 실행**돼 강세주를 조기 절단(6/15 WDC +7.06% 익절 후 +10.98%까지 상승, 매도가 대비 +3.2% 추가; 최근 6일 장전매도 8건 중 7건 상승·0건 하락방어). 이 누수는 수익경로의 프리오픈 실행 지점 자체라 경로 밖 처방 불가.
- Before/after behavior: before — `claude_sell_target`이 프리오픈(정규개장 전)에 즉시 매도 제출. after — `(US_)PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE=enforce` 시 프리오픈 목표익절을 **개장+5분 재평가로 지연**(기존 `_pathb_preopen_exit_policy` defer→open-confirm 기계 재사용). 개장 후엔 정상 실행. 토글 off면 현행 동일.
- Order/risk/broker truth/Claude/config/env impact: 주문수량·하드스톱·loss_cap·`CLOSED_PROFIT_LADDER` floor·`_auto_sell_hard_guard_breach`·broker truth **무변경(즉시실행 유지)**. deferral이 프리오픈 AUTO_SELL_REVIEW Claude 호출을 제출 전 차단 → 호출 감소. 신규 env 2개(`(US_/KR_)PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE`, 기본 off, US=enforce·KR=off). `.env.live`/`config` 반영, `state/brain.json` 무변경.
- Replacement guard: stop/loss/하드가드/ladder는 deferral 우회(즉시), deferral 중에도 하드스톱 상시활성으로 하방 bounded, US 한정 enforce(KR off, 성과 분리), 토글 reversible, profit_target만 severity=`profit_target_runner`로 분리.
- Tests run: `tests/test_pathb_preopen_profit_target_defer.py`(7: enforce defer/off proceed/shadow/ladder 제외/KR off/stop 회귀/open-confirm 매도); `tests/test_preopen_auto_sell_recheck.py test_pathb_runtime.py test_pathb_profit_protection.py test_auto_sell_claude_gate.py test_pathb_sell_reconcile.py`(238 passed); 전체 `pytest tests/`(최종 보고에 기재); `py_compile`; `live_preflight --mode live`.
- Remaining risk: 개장까지 페이드하는 종목은 일부 giveback 가능(하드스톱으로 bounded). 약세장 표본 미검증 — 재시작 후 1~2주 모니터(강세=보유개선/약세=giveback 영향). "강세 러너를 목표 넘겨 더 보유(target extension)"는 별도 후속(이번은 프리오픈 즉시실행만 개장+5분으로 지연).

### MD 위반 사항

Recorded date: 2026-06-16
Work item: momentum 개장 진입 대기 단축 — 강세장 진입 타이밍. 운영자 명시 결정: "US+KR 둘 다 enforce".

- Protected area touched: KR momentum Plan A 진입 타이밍(`trading_bot.py` momentum 게이트) — CLAUDE.md "KR live 확대 금지 / KR·US 성과 분리 / 짧은 기간 전략 변경 지양" 원칙과 충돌. (US momentum은 수익경로라 보호계약 위반 아님.)
- Why unavoidable: 운영자가 강세장 KR/US 진입 미스를 핵심 통점으로 제기하고 옵션에서 KR 손실경로 리스크를 명시 고지받은 뒤 "US+KR 둘 다"를 선택. momentum 45분 대기는 진입 타이밍 게이트 자체라 우회 처방 불가.
- Before/after behavior: before — momentum은 개장 후 `_market_elapsed_min ≥ wait(≈45분)`이어야 fire. after — `(US_/KR_)MOMENTUM_EARLY_ENTRY_ENABLED=true` & 모드 RISK_ON이면 `_momentum_entry_min_elapsed`가 최초 판단(개장+5분)으로 단축. 비강세/off면 45분 유지. **단축은 base 이하로만(연장 금지), 최소 5분 floor.**
- Order/risk/broker truth/Claude/config/env impact: 주문수량·하드스톱·loss_cap·PathB·broker truth·Claude 호출량 무변경. Plan A momentum 진입 "시점"만 앞당김(빈도는 trade_ready 슬롯·신호조건이 결정). 신규 env 3개(`(US_/KR_)MOMENTUM_EARLY_ENTRY_ENABLED`, `MOMENTUM_EARLY_ENTRY_MIN_ELAPSED=5`). `_effective_momentum_wait_window`·continuation window 무수정.
- Replacement guard: RISK_ON 모드게이트(약세 개장 추격 차단), min_elapsed 5(최초 판단 후만), 토글 per-market reversible, KR momentum trade_ready 슬롯 shrink(RISK_ON 1) 유지로 영향 bounded.
- Tests run: `tests/test_momentum_early_entry.py`(7: US/KR RISK_ON 단축·off 유지·비강세 유지·floor·연장금지·per-market); 회귀(최종 보고에 기재); `py_compile`; `live_preflight --mode live`.
- Remaining risk: 강세 개장 5~45분 진입의 추격 위험 미검증(45분 대기의 원래 목적). **KR momentum은 손실 누적 가능** → 재시작 후 1~2주 KR momentum 조기진입 net 집중 모니터, 악화 시 `KR_MOMENTUM_EARLY_ENTRY_ENABLED=false`로 즉시 롤백.

### MD 위반 사항

Recorded date: 2026-06-16
Work item: PathB 약한 포지션 조기정리(weak-MFE early cut). 운영자 결정: "바로 enforce, US+KR 둘 다".

- Protected area touched:
  - PathB loss_cap 인접 exit scan (`runtime/pathb_runtime.py` 3328 자리에 신규 청산 신호 `_pathb_weak_mfe_cut_signal` 추가).
  - hold advisor protective hold 정책 화이트리스트(`stop_recovery_close_reasons`에 `CLOSED_WEAK_MFE` 추가)와 AUTO_SELL_REVIEW default policy 문구.
  - PathB `AUTO_SELL_REVIEW` HOLD cooldown guard와 `_pathb_sell_review_required`는 **무변경**(reason/close_reason 동적 매칭이라 새 신호를 자동 커버).
- Why unavoidable: 전 기간 live(227건, 동기간 QQQ +11.76% 강세장) 분석에서 최대 단일 누수가 `CLOSED_LOSS_CAP` net -88.7%p로 확정됐고, 그 37/41건이 PathB claude_price 보호경로 자체다. yfinance 백필 MFE(171건)에서 loss_cap MFE 중앙 +0.39% vs 수익 +3.73%로 갈리고 MFE<0.5% 수익건 오절단이 0이라, 보호경로 밖 변경으로는 처방이 불가능했다.
- Before/after behavior: before — 진입 후 한 번도 못 오른 약한 포지션도 loss_cap(-2%)까지 끌려 손절. after — `(US_/KR_)PATHB_WEAK_MFE_CUT_ENABLED=true` & 관찰창(`*_MIN_AGE_MIN`=30분) 경과 & `observed_mfe_pct < *_MFE_MAX_PCT`(0.5) & 현재 손실(`<= *_MIN_LOSS_PCT`=0)이면 `CLOSED_WEAK_MFE`로 조기 청산. 하드스톱/loss_cap/profit_ladder는 무수정. 토글 off면 현행 동일.
- Order/risk/broker truth/Claude/config/env impact: 신규 손절성 청산 1종 추가. 주문 수량/하드스톱/loss_cap/profit_ladder floor/broker truth 무변경. Claude 호출은 기존 AUTO_SELL_REVIEW 게이트 재사용(`CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true`라 weak_mfe도 hold advisor 리뷰 거침, cooldown guard 자동 포함 → 호출 폭증 없음). 신규 env 6키(`config/v2_start_config.json` + `.env.live`). `state/brain.json` 무변경.
- Replacement guard: 하드스톱/loss_cap 무수정(하방 bounded, weak-cut은 그 사이를 더 일찍 끊어 손실 축소), MFE<0.5% 안전임계(백필상 수익건 오절단 0), 현재 손실 게이트(이익 중이면 보류), 관찰창(초기 변동성 제외), 시장별 토글 reversible, `observed_*` 관측 전용 키만 사용(ladder의 `peak_pnl_pct` 무오염), 자동 매도 리뷰+cooldown guard 자동 커버.
- Tests run: `tests/test_pathb_weak_mfe_cut.py`(9: 발동/관찰창미경과/MFE초과/이익중보류/하드스톱·loss_cap우회/토글off/시장별분리/추적전); 보호영역 회귀 `tests/test_pathb_runtime.py test_pathb_profit_protection.py test_loss_cap_profit_floor.py test_auto_sell_claude_gate.py test_pathb_sell_reconcile.py`(243 passed); 전체 `pytest tests/`(2522 passed); `py_compile`; `check_mojibake --staged`; `live_preflight --mode live`(ok=True FAIL 0); `capture_net_review` by_close_reason 동적 집계로 `CLOSED_WEAK_MFE` 자동 반영 확인.
- Remaining risk: ① 실시간 `observed_mfe_pct` 추적이 5분봉 백필과 다를 수 있음 → 재시작 후 1~2주 weak_mfe_cut 건 net·수익건 오절단 모니터, 악화 시 임계 상향 또는 시장별 토글 off. ② 약세장 미검증(하드스톱으로 bounded). ③ KR 백필 표본 작음(loss_cap 2~14건) → KR 임계는 US와 동일 출발 후 KR 전용 데이터로 재튜닝.

### MD 위반 사항

Recorded date: 2026-06-16
Work item: hold advisor 이익보호 prior 추가 + weak_mfe_cut OFF (전면 진단 후 "재배선" 1단계). 운영자 결정: "검증 가능한 것만 오늘 미국장 enforce".

- Protected area touched: `minority_report/hold_advisor.py::_MEASURED_PRIORS` (US PathB hold advisor 연동 = 수익 핵심경로). weak_mfe_cut(2026-06-16 추가분)은 env로 OFF(코드 보존).
- Why unavoidable: 전 기간 진단에서 ① 수익 전부가 Claude 재량 청산(+143%p)이고 ② 그 hold advisor가 이익 중 HOLD를 70% 반납(profit_pullback -8.91%p)하는 게 capture 누수(US net capture 0.12)의 핵심으로 확정. ③ 진입은 단일 경로(claude_price)라 전략 토글·진입 빈도로 못 가름(US gap_pullback 손실 20/24가 PathB 경로). 즉 **유일하게 효과 있는 개선 레버가 청산(hold advisor)**이고, A/B(균형 40건)에서 이익보호 prior가 변별력 -10%p→+25%p로 검증됨(opus는 무효 → 모델 아닌 프롬프트가 레버). weak_mfe는 손실 조기절단인데 손실 HOLD(stop_recovery)는 정상이라 번지수 오인 → OFF.
- Before/after: before — `_MEASURED_PRIORS`에 손실 HOLD/5일반납 prior만, 이익 중(profit_pullback) 반납 prior 없음. weak_mfe enforce. after — `HOLD_ADVISOR_PROFIT_GUARD_ENABLED=true` 시 "이익 중 HOLD 평균 -2.36%p 반납, profit_pullback -8.91%p, 목표 부근·모멘텀 꺾이면 SELL 익절 우선, 단 추세 살아있고 고점 갱신 중이면 러너 HOLD 유지" prior 추가. weak_mfe_cut OFF(청산은 hold advisor가 판단).
- Order/risk/broker/Claude/config/env impact: hold advisor system 프롬프트에 prior 1개 추가(토큰 소폭↑, system 캐시 내). 주문 수량/하드스톱/loss_cap/profit_ladder/broker truth/Claude 호출량 무변경. weak_mfe OFF로 단타 손절 게이트 비활성. 신규 env `HOLD_ADVISOR_PROFIT_GUARD_ENABLED`(기본 true), `(US_/KR_)PATHB_WEAK_MFE_CUT_ENABLED=false`. `.env.live`+`config` 반영, `state/brain.json` 무변경. **봇 재시작 시 반영.**
- Replacement guard: prior는 `HOLD_ADVISOR_PROFIT_GUARD_ENABLED` 토글 즉시 롤백(재시작). 문구를 "이익 중(pnl>0)·모멘텀 꺾임"에 한정 + "러너는 HOLD 유지" 명시 → 손실 케이스·좋은 러너 영향 제한. weak_mfe 코드 보존(env off). profit_ladder/trailing은 무수정(보호영역+2026-06-14 롤백 이력 → 오늘 제외).
- Tests run: hold advisor/weak_mfe/청산 회귀 124 passed; 전체 `pytest tests/` 2522 passed; `py_compile`; prior 토글 on/off 동작 확인; `check_mojibake --staged`; `live_preflight --mode live` ok=True FAIL 0.
- Remaining risk: ① prior는 A/B 가상실현 50건만, **라이브 효과 미검증** → 미국장 모니터(profit_pullback giveback 줄었나)·악화 시 토글 off. ② system 전체 적용이라 profit_pullback 외 케이스(loss_deferral 등) 영향 미검증 → "이익 중" 한정으로 제한했으나 관찰 필요. ③ 진입 변별·alpha 근본 문제는 미해결(이번은 capture 개선만). ④ 잡전략 OFF·진입 빈도·profit_ladder 강화는 효과 없음/보호영역이라 제외 — 별도 검토.

### MD 위반 사항

Recorded date: 2026-06-23
Work item: 무결성 감사 2-1 — PathB MFE/MAE 측정 누락 근본수정(observed_* durable 영속화 + 청산 finalize fallback). 운영자 지시: "수정처방으로 하고 같이봐줘"(핸드오프 원안 대신 데이터로 정정한 처방으로 진행).

- Protected area touched: `runtime/pathb_runtime.py` exit/excursion 보호 계약 — `_update_position_excursion`(Phase 1c), `_pathb_exit_meta`, `_finalize_pathb_sell_close`, `on_external_close`.
- Why unavoidable: v2_learning `mfe_pct` 충진율이 PathB 청산 261건 중 241건(87%) NULL인데, 누락이 발생하는 지점이 보호영역인 청산 finalize 경로 자체다. 핸드오프 진단(멀티데이 rehydrate 유실)을 직접 재쿼리로 검증했더니 same_day 청산도 92% NULL이라 영속화 가설이 반박됐고, 진짜 원인은 **브로커 truth reconcile 청산 시 `_sync_runtime_with_broker`가 보유 0인 로컬 pos를 먼저 제거 → `_finalize_pathb_sell_close`의 `_find_position`=None → exit_meta 미생성 → mark_closed가 mfe_pct=None으로 키 자체를 누락**. 보호영역 밖에서는 처방 불가.
- Before/after behavior: before — `_pathb_exit_meta`는 `pos is not None`일 때만 생성, observed_*는 휘발성 pos에만 존재. pos 제거된 청산(PRE_CLOSE 44/0·LADDER 32/0·TARGET 25/0)은 MFE/MAE 전부 누락. after — `_update_position_excursion`이 새 고점/저점마다 observed_peak/low/mfe/mae를 path_run plan_json에 durable 영속화. `_pathb_exit_meta`는 pos=None 내성 + durable fallback. `_finalize_pathb_sell_close`/`on_external_close`는 pos 없어도 plan_json 영속값으로 exit_meta 복원해 MFE/MAE 방출.
- Order/risk/broker truth/Claude/config/env impact: 주문 수량·하드스톱·loss_cap·profit_ladder floor·broker truth·Claude 호출량·`.env*`·`config`·`state/brain.json` 전부 **무변경**. 순수 측정 배선 복구. 신규 부작용은 live 중 새 고점/저점마다 v2_path_runs plan_json에 merge UPDATE 1회 추가(path_run_id PK 조회, bounded). `peak_pnl_pct`(ladder 입력)는 비접촉 — observed_* 전용키만 읽고/쓴다(2026-06-14 Phase 1c 계약 유지).
- Replacement guard: ① durable 영속화는 path_run_id 있을 때만(브로커 주입 pos는 skip) + try/except로 감싸 DB 실패해도 청산 흐름·추적 무영향. ② `_excursion` fallback 순서 pos→durable→legacy(peak_pnl_pct)로 기존 동작 보존(observed 있으면 live 우선). ③ entry_market_regime은 durable에 안 실어 sync-layer fix(d056fad) 무회귀. ④ peak_pnl_pct/profit_ladder/하드스톱 무수정.
- Tests run: `tests/test_pathb_position_excursion.py` 12 passed(+8: durable 영속화·새극값만·path_run_id 없으면 skip·DB실패 내성·pos=None durable 복원·live 우선); 보호영역 회귀 `test_pathb_runtime/profit_protection/loss_cap_profit_floor/auto_sell_claude_gate/pathb_sell_reconcile/weak_mfe_cut/tail_capture` 291 passed; `py_compile`; `check_mojibake --staged` 통과; `live_preflight --mode live` ok=True FAIL 0. 전체 `pytest tests/`는 2618 passed, 38 fail은 `test_candidate_audit*`(단독·내 테스트와 조합 시 전원 통과 → 전체 스위트 cross-test 상태오염, 본 변경 무관·§2-4 DB비대 sidecar 이슈와 겹침), 65 fail은 사전존재 Py3.9 비호환 preopen.
- Remaining risk: ① 과거 NULL 244건은 소급 복원 불가(이 수정은 "앞으로 안 유실"용, 봇-다운 full sync로 이미 방출된 행만 learning 반영). ② 영속화 전 첫 1틱에 청산되는 포지션은 여전히 누락 가능(드묾, observed 추적 시작 전). ③ 진짜 unknown excursion일 때 0.0 방출(present-zero)은 기존 동작 유지 — "flat"과 "미측정" 미구분, 별도 측정품질 개선 후보. ④ Path A는 핸드오프 §3 오독 정정: 진짜 Path A(`plan_a` route) 청산은 9건뿐이고 5/9 이미 충진(risk_manager exit_meta 정상) — 별개작업 불필요. gap_pullback/momentum NULL행은 path_type=claude_price인 PathB origin 전략 라벨이라 본 수정이 커버.

### Commit, PR, and Security Standards

- Commit units should be one behavior change at a time. Recent history uses Conventional Commit prefixes such as `feat:` and `fix:` with short Korean or English summaries.
- PR notes should include change summary, risk areas, test commands run, config/env impact, and dashboard or Telegram screenshots when UI output changes.
- Never commit real `.env`, `.env.live`, `.env.paper`, token files, broker credentials, or local `*API*.txt` notes. Document configuration examples in `.env.example`.

## PEAD Input Policy (2026-04-24)

- PEAD is an input-quality feature, not a standalone strategy.
- Do not let PEAD override entry timing, stop-loss, trailing stop, or session-close logic.
- Keep PEAD event data out of `brain.json`. Store it in digest/candidate metadata and shadow logs only.

### Source Rules

- US:
  - `earnings_date`: yfinance calendar
  - `surprise_sign` / `surprise_strength`: yfinance `earnings_dates` using `Reported EPS` and `EPS Estimate`
- KR:
  - use `earnings_date` / `earnings_window` first
  - do not infer EPS beat/miss from Naver news or DART headlines
  - if structured KR actual/estimate is unavailable, keep `surprise_sign=unknown`

### Trust Tiers

- `high`: actual EPS and estimate both available
- `medium`: earnings date available, surprise unavailable
- `low`: news/disclosure only

Only `high` may produce `surprise_sign` / `surprise_strength`.
`medium` may only produce `earnings_window`.
`low` must not affect PEAD bias.

### Rollout Rules

- `earnings_date` / `earnings_window`: prompt-visible immediately
- `surprise_sign` / `surprise_strength`: 5 trading days shadow-only first
- During shadow:
  - compute and store values
  - write logs for manual inspection
  - do not expose surprise fields to Claude prompts
- Prompt exposure must be blocked by `state/pead_shadow_state.json`, not by memory or comments only.
- `prompt_surprise_enabled=true` is not sufficient by itself. The manual review checklist below must also pass.

### PEAD manual review gate

Before enabling surprise fields in analyst/selection prompts, all checks must be true in `state/pead_shadow_state.json`.

- `trading_days_observed >= 5`
- `manual_review.tier_null_rate_checked=true`: inspect 5 trading days of shadow logs and tier-level null rates.
- `manual_review.surprise_sample_10_checked=true`: manually verify at least 10 `surprise_sign` cases against source EPS values.
- `manual_review.prompt_leak_zero_checked=true`: confirm zero prompt leaks of surprise text while `prompt_applied=false`.
- `prompt_surprise_enabled=true`: explicit final operator switch after the checklist is complete.
- `manual_review_passed` is derived from checklist values. Do not treat a hand-edited boolean as sufficient.

### PEAD gate completion boundary

The first implementation step is complete only when:

- `state/pead_shadow_state.json` is generated from shadow logs.
- 5 trading day gating is enforced in code.
- surprise prompt leakage is covered by tests.
- `prompt_applied` remains false until the state gate and checklist pass.

Do not add PEAD weighting, prompt tuning, or source expansion in the same step.

### Prompt Scope

- Allowed:
  - watchlist prioritization
  - trade_ready conviction bias
  - strategy-fit bias
- Not allowed:
  - automatic trade_ready promotion from PEAD alone
  - entry rule override
  - exit rule override

### Logging

- Keep US shadow records under runtime logs for 5 trading days.
- Minimum fields:
  - ticker
  - earnings_date
  - earnings_window
  - reported_eps
  - eps_estimate
  - surprise_sign
  - surprise_strength
  - confidence_tier
  - prompt_applied

### MICRO_PROBE promotion policy

`MICRO_PROBE` is a separate experiment path for order-size-too-small signals, not a regular strategy.

- Keep `MICRO_PROBE` performance separate from normal trades.
- Do not promote to regular sizing before at least `30` filled probe trades or `4` calendar weeks of observation.
- Promotion review must use net performance after fees, max loss, loss streak, and separate probe reports.
- Probe records must keep original order cost, adjusted order cost, oversize ratio, and probe reason.
- Runtime defaults are defensive: `MICRO_PROBE_ENABLED=false`, `MICRO_PROBE_PAPER_ONLY=true`, max `2` daily probes, max `2` open probe positions.
- A probe can only convert an `order_size_too_small` signal when market/mode allow it, entry priority is at least `0.45`, adjusted order is at most `50,000 KRW`, and oversize ratio is at most `2.0`.
- Probe entry/outcome data is stored in `ticker_selection_db.micro_probe_log`; do not mix it into normal strategy promotion decisions.

## Operations Rules (2026-04-22)

Use adaptive operation, not fast strategy rotation.

### Review metrics

For the rolling 2-week review, track these 10 metrics:

1. consensus directional hit rate
2. best analyst - consensus hit gap
3. unanimous mismatch count
4. trade_ready -> signal_fired conversion
5. watch_only missed runup ratio
6. trade_ready forward_3d average
7. ATR-blocked missed runup
8. entry_blackout ratio
9. watch_only blocked ratio
10. continuation average pnl

Storage contract:

- `1~3`: persisted in each session judgment record as `judgment_eval`
- `4~10`: persisted or derivable from:
  - `data/ticker_selection_log.db`
  - `data/ml/decisions.db`
- `1~10 aggregate snapshot`: persisted at `session_close` as `ops_review_snapshot`
  in runtime/live judgment records

### Unanimous override

If all three analysts point to the same directional bucket, final consensus
must not end on the opposite side.

- all bull -> final consensus cannot be bear/flat
- all bear -> final consensus cannot be bull/flat
- all neutral -> final consensus cannot be bull/bear

This is a structural guard, not a tuning rule.

### Claude post-tuning

Claude may only tune bounded runtime controls:

- `momentum_wait_adjust_min`: `-10 .. +10`
- `entry_priority_cutoff_adjust`: `-0.05 .. +0.05`
- `kr_momentum_atr_cap_adjust`: `-0.01 .. +0.02`
- `kr_momentum_atr_cap_high_adjust`: `-0.01 .. +0.02`
- slot bias / replacement aggressiveness: one-step changes only

Claude must not:

- disable hard safety rules
- override unanimous direction guards
- replace market priors with a different strategy philosophy

### Trigger rules

Do not tune every cycle. Tune only at:

- `session_open`
- scheduled intraday tuning windows
- explicit event-driven triggers

Event-driven tuning is allowed when one of these conditions is true:

- recent 10-session `trade_ready -> signal_fired` conversion is too low
  - KR `< 15%`
  - US `< 10%`
- recent 10-session `watch_only missed runup ratio >= 30%`
- recent 10-session `ATR-blocked avg runup >= +4%` with sample `>= 10`
- recent 10-session `consensus hit rate < 45%`
- recent 10-session `best analyst - consensus >= 10%p`
- `unanimous mismatch >= 1`

### Review thresholds

After 2 weeks, modify logic only when thresholds are breached:

- `consensus hit rate < 45%` => review consensus weighting
- `best analyst - consensus >= 10%p` => review aggregation
- `unanimous mismatch >= 1` => immediate fix
- `watch_only missed runup ratio >= 30%` => relax soft promotion rules
- `trade_ready forward_3d avg <= 0%` => review selection quality
- `entry_blackout ratio >= 15%` => reduce late-session churn
- `watch_only blocked ratio >= 25%` => review hard/soft split
- `continuation avg pnl <= -3%` with trades `>= 5` => reduce continuation usage

### Adaptation principle

- Keep strategy families stable.
- Adapt slot mix, wait windows, cutoffs, and ATR handling.
- Apply shrinkage on short windows before changing runtime behavior.
- KR momentum ATR handling:
  - `<= cap`: normal
  - `cap~cap+1%`: size cap `70%`
  - `cap+1~cap+2%`: size cap `50%`
  - `cap+2~high_cap`: size cap `35%`
  - `> high_cap`: block
- Risk-Off exception:
  - default: no new entry
  - exception: `mean_reversion` only
  - constraints: no `HALT`, one position max per market, no same-day reentry,
    no panic index move, size cap `40%`

### Candidate funnel (2026-04-23)

Apply expansion only at the front of the funnel. Do not widen `trade_ready`
or live order concurrency until the new feed proves stable.

- Raw scanner defaults:
  - KR `80`
  - US `80`
- Dynamic universe defaults:
  - KR `40`
  - US `40`
- Claude selection prompt cap:
  - KR starts at `28`
  - US stays at `24` until parse stability is confirmed
- Keep runtime `trade_ready` slot caps unchanged.
- `low_gap_continuation` is not a standalone live strategy yet.
  - Use it only as a promotion/support signal first.

### KR screener policy

For KR, candidate expansion must preserve KOSDAQ visibility.

- Do not merge `KOSPI + KOSDAQ` and then blindly truncate.
- Merge with a minimum KOSDAQ share first, then rank the combined pool.
- Default KOSDAQ minimum share is `35%`.
- Environment overrides:
  - `KR_SCREEN_KOSDAQ_MIN_RATIO`
  - `KR_SCREEN_KOSDAQ_MIN`

### Screener audit

Before evaluating whether candidate expansion worked, persist raw funnel logs.

- KR screener audit path:
  - `logs/screener/YYYYMMDD_KR_screen.jsonl`
- The audit should make these stages inspectable:
  - KOSPI raw
  - KOSDAQ raw
  - merged candidates
  - post-product-filter candidates
- Use these logs before changing caps again.
  - KR prompt cap path: `20 -> 28 -> 32`
  - Only raise the next step after parse stability is acceptable.

### Deferred Follow-ups

These are intentionally deferred. Do not auto-promote them into live behavior
until more data is available or a human explicitly approves the change.

- `brain.json` automatic mutation from lesson scoring
  - keep `lesson_candidates.json` append/score only
  - promoted memory remains approval-based
- automatic hard-block generation from short-window evidence
  - scoring may propose candidates
  - live hard blocks require human review
- `low_gap_continuation` live strategy rollout
  - keep it as observation/promotion support first
  - only move to live after repeated shadow evidence
- strategy-level full replacement
  - avoid swapping strategy philosophy from short windows
  - prefer shrink/observe over full replacement
- history auto-fill expansion for repeated insufficient-history names
  - keep as a later reliability pass, not a live-behavior change

## 프로젝트 철학

- Claude는 시장 판단, 종목 selection, 보유 재량 판단을 맡는다.
- 로직은 진입 조건, 주문 수량/금액, 손절/트레일링, 브로커 동기화, 복구를 맡는다.
- 상태 오염이 의심되면 내부 캐시보다 브로커 truth를 우선한다.
- 짧은 기간 데이터로 전략 철학을 갈아엎지 않는다. 먼저 축소, 관찰, 검증 순서로 간다.

### 핵심 중점 사항 — 데이터 품질과 오염 방어

- `state/brain.json`, `data/ml/decisions.db`, `data/ticker_selection_log.db`는 서로 다른 역할을 가진다.
  - `brain.json`: 정책 메모리
  - `decisions.db`: 의사결정/성과 기록
  - `ticker_selection_log.db`: selection 근거 기록
- 세 파일 중 하나라도 오염되면 Claude 입력 품질이 떨어진다.
- 라이브 복구는 내부 state가 아니라 아래 3가지를 1차 truth로 본다.
  - 브로커 보유 종목
  - 브로커 미체결 주문
  - 가능하면 브로커 체결 내역

### 로그 원칙

- 로그는 디버깅의 1차 수단이다. 사람이 읽는 문구는 한국어로 유지한다.
- 에러/위험/정상 로그는 분리해서 남긴다.
- 깨진 한글, 특수문자, 모지바케는 발견 즉시 복원한다.
- 로그만 보고 원인 추적이 가능해야 한다.

#### 로그 분석 순서

1. `logs/system/` — 전체 상태 전이와 주문 흐름
2. `logs/risk/` — 차단, HALT, 리스크 판정
3. `logs/normal/` — 정상 사이클/진입/청산 흐름
4. `logs/daily_judgment/` — Claude 판단 근거 JSON
5. `logs/screener/` — 후보 풀 축소 단계 점검

## Claude Working Contract

- Claude가 직접 해도 되는 것
  - 시장 모드 판단
  - 종목 watchlist / trade_ready 후보 제안
  - conviction / strategy fit 판단
  - 재량형 HOLD/SELL 의견
- Claude가 직접 하면 안 되는 것
  - 최종 주문 수량 계산
  - 하드 손절 해제
  - 브로커 truth 무시
  - 이벤트 데이터의 장기 메모리 자동 승격
- 튜닝 데이터는 raw 로그를 그대로 주입하지 않는다.
  - `lesson_candidates.json` 같은 후보 규칙으로 점수화한 뒤
  - 저위험 요약만 프롬프트에 넣는다.
  - `brain.json` 자동 수정은 보류한다.

## 나아갈 방향 (Roadmap)

### 현재

- US PathB 수익 구조 보존 (target/pre-close/profit_ladder/Claude sell 4개 경로)
- KR 구조적 손실 분리 관찰: KR live 확대 금지, shadow/축소 우선
- v2 성과 원장 정합성 (decisions.db ↔ v2_event_store sync freshness)
- ticker_selection attribution 누락 리포트 (execution_decision_id 누락률 추적)
- candidate audit outcome freshness 표시 (daily_pending 상태 명시)

### 다음 단계

- KR-only shadow veto gate: 장 초반/후반 진입, loss_cap 직후 재진입, stop cluster 시 size-down
- US loss_cap cluster shadow: buy-zone-hit 후 손절 집중 구간 size-down 계측
- watch_only missed runup + bucket decomposition 연결 리포트
- PEAD surprise shadow 5거래일 검증 및 prompt 적용 검토

### 장기

- brain 자동 승격은 승인형 워크플로우가 안정화된 뒤 검토
- 전략 추가보다 입력 품질과 실행 품질 개선 우선

## 운영자 확인 필수 설정값

아래 설정은 변경 전 반드시 운영자에게 확인한다. 코드 리뷰, config 정리, 자동 수정 등 어떤 경로로도 임의로 바꾸지 않는다.

| 설정 | 현재값 | 의미 |
|---|---|---|
| `PATHB_INTRADAY_ONLY` | `false` | PathB 포지션 당일 강제청산 여부. false = multi-day hold 허용 |
| `KR_LATE_ENTRY_GATE_ENABLED` | `false` | KR 늦은 진입 게이트. false = 시간대 차단 없음 |
| `KR_LATE_ENTRY_EXEC_GATE_ENABLED` | `false` | KR 늦은 진입 실행 게이트. false = 차단 없음 |
| `PATHB_KR_LIVE_ENABLED` | `true` | KR PathB live 활성 여부 |
| `PATHB_US_LIVE_ENABLED` | `true` | US PathB live 활성 여부 |
| `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK` | `false` | KR zone hit 시 주문 제출 차단 여부. false = 정상 주문 허용 |
| `KR_REENTRY_COOLDOWN_MINUTES` | `60` | KR 재진입 쿨다운(분) |
| `US_REENTRY_COOLDOWN_MINUTES` | `60` | US 재진입 쿨다운(분) |
| `KR_EARLY_ENTRY_SOFT_GATE_ENABLED` | `true` | KR 장 초반 진입 사이즈 축소 게이트 활성 여부 |
| `PATHB_KR_SHADOW_PLAN_ENABLED` | `false` | KR PathB shadow 플랜 활성 여부. false = shadow 비활성 |
| `US_MOMENTUM_LIVE_ENABLED` | `true` | US momentum 전략 live 활성. **false로 바꾸면 US PathB momentum 후보가 생성되지 않는다 (누적 수익 경로)** |
| `US_VOLATILITY_BREAKOUT_LIVE_ENABLED` | 미설정(=false) | US VB 전략 live 활성. VB 성과 미확인 상태이므로 현행 유지 |
| `KR_PLANA_HOLD_POLICY_MODE` | `enforce` | KR Plan A hold advisor 정책 강제 적용 여부 |
| `US_PLANA_HOLD_POLICY_MODE` | `enforce` | US Plan A hold advisor 정책 강제 적용 여부 |
| `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS` | `true` | **Path A 자동 매도 전 Claude hold advisor 리뷰 게이트 활성 여부. false로 바꾸면 loss_cap·stop_loss·trail_stop 등 Path A 자동 매도가 Claude 판단 없이 즉시 실행된다. 반드시 true 유지.** |
| `SELECTION_SMART_SKIP_MODE` | `live` | selection 재사용 모드. live = TTL 내 동일 semantic signature면 Claude 호출 생략. observe로 낮추면 호출 횟수 증가 |
| `SUB_SCREENER_TRIGGER_ENABLED` | `true` | sub_screener 감지 후 triage/reinvoke 활성 여부 |
| `PULLBACK_WAIT_EVIDENCE_GATE_MODE` | `live` | evidence 부족 시 PULLBACK_WAIT → WATCH 강등 적용. shadow로 낮추면 약한 evidence 후보가 PathB wait pool로 진입 가능 |
| `CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS` | `2200` | compact selection 응답 최대 토큰. 초과 시 trade_ready=[] fallback. 25/7 cap 실험 시 2600으로 올릴 것 |
| `INTRADAY_REVIEW_COOLDOWN_MINUTES` | `120` | 포지션별 intraday review 최소 간격(분). 손익 급변·stop 근처는 우회 |
| `INTRADAY_REVIEW_DAILY_MAX_PER_POSITION` | `3` | 포지션별 일중 review 최대 횟수. pending_due·손익 급변·stop 근처는 초과 허용 |
| `INTRADAY_ENTRY_SHADOW_MODE` | `shadow`(기본 ON) | 장중 미진입(WAIT/REJECT/PULLBACK) 'would-enter' 관측 기록. **순수 shadow — 주문/플랜 무영향.** `off`로 끄면 수집 중단. 전환은 `tools/intraday_entry_shadow_review.py` 검증 후 |
| `PATHB_ENTRY_SCAN_BROKER_TRUTH_RETRY_MAX` | `0`(=OFF) | Phase A: BLOCKED 유발 조회 transient 실패 재시도 횟수. **0이면 현행 동작.** fail-closed 불변(재시도 후 실패면 그대로 BLOCKED). 검증 후 운영자가 `1`로 ON |
| `PATHB_ENTRY_SCAN_BROKER_TRUTH_RETRY_BACKOFF_SEC` | `0.5` | 위 재시도 백오프(초, 상한 2.0). RETRY_MAX=0이면 무의미 |
| `US_PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE` | `enforce` | **프리오픈 목표익절(claude_sell_target) 즉시실행 대신 개장+5분 재평가로 지연.** stop/ladder/하드가드는 무관(즉시). off=현행. 강세주 조기절단 방지 |
| `KR_PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE` | `off` | KR은 PathB 손실·성과 분리로 미적용 |
| `US_MOMENTUM_EARLY_ENTRY_ENABLED` | `true` | **강세(RISK_ON) 시 momentum 45분 대기 대신 최초 판단(개장+5분) 후 진입.** 비강세/off면 45분 유지 |
| `KR_MOMENTUM_EARLY_ENTRY_ENABLED` | `false` | **2026-06-21 A~F 토론으로 OFF.** 6/16 이후 KR momentum 체결 0(무발동)·손실경로라 검증 안 된 enforce 제거(위생). 코드 보존, 재ON은 KR momentum 실체결 표본 확보 후 |
| `MOMENTUM_EARLY_ENTRY_MIN_ELAPSED` | `5` | 조기진입 허용 최소 경과(분, 최초 판단=개장+5분). floor 5, base(45) 초과 불가 |
| `CANDIDATE_CHANGE_OVERHEAT_ENABLED` | `false` | **2026-06-21 A~F 토론으로 OFF.** C3 당일등락 과열 페널티(change≥15%, −12점, KR/US 공통). 사후검증: US 급등후보 fwd3 +5.4~11.5%(역효과·메모리 "당일등락차단=US −118%p"와 일치), KR도 hit가 non-hit보다 덜 나쁨(방향 반전). C2(KR vol_ratio)는 별개 경로로 유지. 기본 on(현행 보존), config에서 off |
| `US_PATHB_WEAK_MFE_CUT_ENABLED` | `false` | **2026-06-16 OFF.** 단타 손절 게이트는 번지수 오인(손실 HOLD=stop_recovery는 정상). 청산은 hold advisor가 판단. 코드 보존 |
| `KR_PATHB_WEAK_MFE_CUT_ENABLED` | `false` | **2026-06-16 OFF.** 동일 사유 |
| `HOLD_ADVISOR_PROFIT_GUARD_ENABLED` | `true` | **hold advisor 이익보호 prior: 이익 중 HOLD 반납(-2.36%p, profit_pullback -8.91%p) 방지 익절 우선(단 러너는 HOLD 유지).** A/B 변별력 +25%p. 라이브 미검증 → 미국장 모니터, 악화 시 false 즉시 롤백 |
| `PATHB_WEAK_MFE_CUT_MIN_AGE_MIN` | `30` | 관찰창(분). 진입 후 이 시간 경과해야 weak-cut 평가(초기 정상 변동성 제외) |
| `PATHB_WEAK_MFE_CUT_MFE_MAX_PCT` | `0.5` | observed_mfe_pct 임계(미만이면 약한 포지션). 수익건 0건 절단되는 안전 임계 |
| `PATHB_WEAK_MFE_CUT_MIN_LOSS_PCT` | `0.0` | 현재 손실 게이트(현재 pnl ≤ 이 값일 때만 발동). 이익 중이면 보류 |
| `LESSON_VALIDATION_ENABLED` | `true` | **교훈 forward-validation 레이어 마스터 스위치(2026-06-17 enforce 적용).** 축적은 항상(config 무관), 반영만 게이트. false=완전 OFF |
| `LESSON_VALIDATION_APPLY_MODE` | `enforce` | **off/shadow(관측만)/enforce(bounded 반영).** 현재 valid_apply 0이라 enforce여도 안전 no-op. 이상 시 shadow/off 즉시 롤백 |
| `LESSON_VALIDATION_COST_FLOOR_PCT` | `0.5` | would_be가 이 비용(%) 넘어야 valid_apply(forward≠net 보정). 미달=marginal(미반영) |
| `LESSON_VALIDATION_MIN_SESSIONS` | `2` | 부호일관 독립확인 최소. 미달=pending(미반영) |
| `LESSON_VALIDATION_MAX_AGE_DAYS` | `45` | 검증셀 신선도(일). 초과=적용무시(기존값 fallback). invalid_block 함정방어는 유지 |
| `LESSON_VALIDATION_MIN_CONFIDENCE` | `0.3` | 적용 최소 confidence. 미만=미반영(기존값) |

이 설정들은 `.env.live`와 `config/v2_start_config.json` 두 곳에 존재한다. 한 곳만 바꾸면 반영이 안 될 수 있으므로 두 파일을 동시에 확인한다.

### PathB KR/US 현재 운영 파라미터 (2026-05-21 기준)

**이 섹션의 값을 변경하면 반드시 운영자에게 먼저 알린다.**

#### 공통 (KR = US)

| 파라미터 | 현재값 |
|---|---|
| 고정 주문금액 | 450,000 KRW |
| 최대 포지션 수 (`PATHB_MAX_POSITIONS`) | 15 |
| 일일 최대 진입 수 (`PATHB_MAX_DAILY_ENTRIES`) | 40 |
| 최소 confidence (`PATHB_MIN_CONFIDENCE`) | 0.5 |
| INTRADAY_ONLY (`PATHB_INTRADAY_ONLY`) | false (multi-day hold 허용) |
| 재진입 쿨다운 | 60분 |
| 장 초반 soft gate | 0~60분 size × 0.5 |
| Shadow 플랜 | 비활성 |

#### KR만 다른 것

| 파라미터 | KR | US |
|---|---|---|
| 슬리피지 캡 | 1.003 (0.3%) | 1.002 (0.2%) |
| Protective hold 최소 거리 | 0.5% | 0.3% |

#### 변경 시 주의사항

- 위 값 중 어떤 것이라도 바꾸면 **변경 전에 운영자에게 명시적으로 알려야 한다**.
- 재진입 쿨다운, 슬리피지 캡, soft gate 파라미터는 진입 빈도와 직결되므로 단독 변경 불가.
- KR/US 를 비대칭으로 바꿀 경우 의도적 차이인지 반드시 확인한다.

## 코드 작업 원칙

1. 작은 수정으로 큰 사고를 막는다.
2. 라이브 수익률/손실 계산 축은 KR/US를 섞지 않는다.
3. 브로커 불신 상태에서는 신규 진입보다 보호를 우선한다.
4. selection과 execution 문제를 섞지 않는다.
5. 수정 후에는 단계별 검증 + 마지막 통합 QA를 반드시 한다.
6. 개선 시 예외처리를 추가하기 전에 근본 구조부터 확인한다. 구조적 문제인지, 진짜 예외 케이스인지, 별도 함수가 필요한 것인지 판단한 뒤 처리한다. 불필요한 예외처리는 코드 복잡성만 높이므로 지양한다.

### 기능 변경 후 검증 절차

1. 관련 단위 테스트 실행
2. 관련 통합 테스트 실행
3. 수익률/자산곡선/브로커 truth 계산축 점검
4. 로그/대시보드 문구 확인
5. 전체 QA 재실행

### Git 원칙

- runtime 산출물과 정책 메모리는 구분한다.
- 사용자 작업이 섞인 파일은 함부로 되돌리지 않는다.
- 커밋에는 변경 목적이 분명한 파일만 포함한다.

## 실행 명령

```bash
# 의존성 설치
python -m pip install -r requirements.txt

# 봇 실행 — 모의투자
python trading_bot.py --paper

# 봇 실행 — 실거래
python trading_bot.py --live

# 대시보드 서버
python dashboard/dashboard_server.py

# 배포 전 구문 검사
python -m py_compile trading_bot.py dashboard/dashboard_server.py claude_memory/brain.py

# 모의투자 preflight 검증
python tools/live_preflight.py --mode paper --skip-dashboard --json

# 실거래 전 preflight 검증
python tools/live_preflight.py --mode live --skip-dashboard --json

# 전체 회귀 테스트
python -m pytest -q

# 전체 테스트 (tests/ 디렉토리)
python -m pytest tests/ -q

# 특정 테스트 파일
python -m pytest tests/test_candidate_audit.py -q

# action routing 집중 테스트
python -m pytest tests/test_action_routing.py -q

# 키워드로 특정 케이스만
python -m pytest tests/test_candidate_audit.py -k "payload_fallback" -q

# 루트 레벨 레거시 회귀 테스트
python -m pytest test_trading_improvements.py test_broker_sync_cash.py -q

# ML DB 검증
python -m unittest ml.test_full
```

Windows 환경에서 일부 파일(`tests/test_live_order_safety.py` 등)에 CRLF 관련 git 경고가 표시되지만 동작에는 무관하다.

## 아키텍처 — 큰 그림

### 환경 파일 로딩 순서

`trading_bot.py` 시작 시:

1. `--live` 플래그에 따라 `.env.live` 또는 `.env.paper` 로드 (없으면 `.env` fallback)
2. `config/v2_start_config.json`의 `env_overrides` 키를 `os.environ`에 덮어씀 (live 모드 전용)
3. 따라서 live 환경 설정은 `.env.live` + `config/v2_start_config.json` 두 곳에서 결정된다.

KIS 브로커 API(`kis_api.py`)는 `KIS_APP_KEY_US` / `KIS_APP_SECRET_US`가 비어 있으면 KR 키로 fallback한다. 한투 KIS는 하나의 계정으로 KR/US 모두 접근 가능하므로 이것이 정상 운영 정책이다. `.env.live`에 `KIS_US_CREDENTIAL_FALLBACK_ACCEPTED=true`를 설정하면 preflight 경고가 제거된다.

### 실행 흐름

1. 브로커/시장 상태 수집
2. digest 및 intraday context 생성
3. Claude 시장 판단 / selection
4. 로직 기반 진입 필터링
5. 주문/체결/복구
6. 성과 기록 및 lesson candidate 적재

### 서브패키지 구조

| 패키지 | 역할 |
|---|---|
| `execution/` | 주문 실행: `claude_price_adapter.py`(PathB 가격), `safety_gate.py`(PathB 안전 게이트), `sizing.py`, `path_arbiter.py`(same-day reentry 차단) |
| `decision/` | Claude 판단 구조체: `claude_price_plan.py`(B플랜 파싱), `registry.py` |
| `runtime/` | 실행 시 판단 로직: `pathb_runtime.py`(PathB 메인), `action_routing.py`(RouteDecision), `gate_evaluation.py`, `candidate_pool_runtime.py`, `live_evidence_pack.py` |
| `lifecycle/` | 사이클 생명주기: `event_store.py`, `quality.py`, `path_context.py` |
| `preopen/` | 장 시작 전 후보 뉴스/점수: `scheduler.py`, `scorer.py`, `storage.py` |
| `audit/` | 감사 DB: `candidate_audit_store.py`(upsert + payload merge), `shadow_audit_store.py`, `agent_call_event_store.py` |
| `ml/` | 의사결정 성과 DB: `db_writer.py`, `forward_updater.py`, `db_health.py` |
| `config/` | 런타임 설정: `runtime_config.py`(EffectiveRuntimeConfig), `v2.py`(V2Config), `v2_start_config.json` |
| `claude_memory/` | brain 메모리: `brain.py`(읽기/쓰기/요약), `brain.json`(실제 정책 메모리는 `state/brain.json`으로 오버라이드) |
| `interface/` | 요약/Telegram 포맷: `v2_ops_summary.py`, `v2_telegram.py` |
| `tools/` | 운영 도구: `live_preflight.py`, `live_guardian.py`, `live_maintenance.py`, `reconcile_*.py`, `analyze_*.py` |

### 두 실행 경로 (Path A / Path B)

- **Path A**: `TradingBot` 클래스(`trading_bot.py`) — Claude selection → 전략 신호 → 주문
- **Path B**: `PathBRuntime`(`runtime/pathb_runtime.py`) — Claude 가격 플랜 기반 진입/청산, `PATHB_US_LIVE_ENABLED=true`일 때 활성

두 경로는 `runtime/action_routing.py`의 `RouteDecision`으로 합류한다. PathB는 KR/US 모두 live 활성 상태이다 (`PATHB_KR_LIVE_ENABLED=true`, `PATHB_US_LIVE_ENABLED=true`).

### 진입 결정 파이프라인

- 후보 풀 생성 → `runtime/candidate_pool_runtime.py`
- selection raw → Claude 응답
- normalized trade_ready → applied trade_ready
- 전략 신호 검사 → `runtime/action_routing.py`
- affordability / 리스크 검사 → `risk_manager.py`
- 주문 생성 → `kis_api.py`

### 판단 재사용 로직 — 봇 재시작 시 주의

- 미국장은 KST 자정이 넘어도 active US session date를 유지할 수 있다.
- 재시작 시 당일 판단 재사용은 가능하지만, 브로커 truth 검증이 선행돼야 한다.
- stale legacy 포지션은 holdings/pending 기준으로 정리한다.

### 매수 차단 조건

- 브로커 상태 불신
- affordability fail
- hard risk block
- same-day reentry block
- late-session / blackout 조건
- watch_only 상태 유지

### `_sync_runtime_with_broker()` 주의사항

- 보유 종목 + 미체결 주문 기준으로 stale 포지션을 정리한다.
- 브로커 응답이 불신이면 해당 시장만 quarantine한다.
- quarantine 상태에서는 신규 진입보다 기존 포지션 관리가 우선이다.
- HALT / daily_return은 시장별 baseline 기준으로 계산한다.

### 주요 데이터 흐름

- `state/brain.json`: Claude 정책 메모리 (런타임 경로는 `runtime_paths.py`가 결정)
- `state/lesson_candidates.json`: 자동 점수화된 교훈 후보
- `data/audit/candidate_audit.db`: 후보 감사 DB (source_file / payload merge 포함)
- `data/ml/decisions.db`: 의사결정/성과 데이터
- `data/ticker_selection_log.db`: selection 로그
- `logs/pead/*.json`: surprise shadow 기록

### KIS API 정규화 규칙

- `broker_truth_snapshot.py`의 `open_orders`는 오늘 체결/미체결 조회 결과 중
  `remaining_qty > 0`인 행만 필터링하여 파생된다.
- `_normalize_kr_daily_ccld_row()`와 `_normalize_us_inquire_ccnl_row()` 등
  모든 KIS 주문 정규화 함수는 반드시 `remaining_qty` 필드를 포함해야 한다.
  누락 시 해당 시장의 `open_orders`가 항상 빈 리스트가 되어
  ORDER_UNKNOWN 매도 복구, sellable qty reject 처리가 전부 오작동한다.
- US 정규화는 `nccs_qty` 필드를 우선 사용하고, 없으면 `order_qty - filled_qty`로 fallback.

### PathB 매도 차단 조건 및 복구

`sellable_qty_untrusted=True`는 매도를 완전히 차단하며, 다음 조건 중 하나로 설정된다:

- 매도 주문이 실패하고 브로커 `open_orders`에서 미체결 주문을 찾지 못한 경우
  (`resolution=no_open_order_or_fill`)
- `manual_reconcile_required=True` 또는 `broker_sell_lock_suspected=True`

복구 경로:

1. `_pathb_sellable_qty_reject_evidence()` → 브로커 fresh refresh (`force=True, ttl_sec=15`)
2. `open_orders`에 매도 주문 발견 → `_recover_existing_sell_order_after_qty_reject()` →
   자동 복구 (sellable_qty_untrusted 해제, 기존 주문 ack로 재연결)
3. 발견 못하면 → `manual_reconcile_required=True` → **운영자 수동 처리 필요**

ORDER_UNKNOWN 매도 복구 흐름 (`_reconcile_exit_order_unknown_run`):

- 체결 확인 → 포지션 종료
- 브로커 `open_orders`에 매도 주문 발견 → ack 등록 (재매도 시도 안 함)
- 미체결 증거 없고 보유 확인 → stale 복구 + 재매도 시도

`open_orders`가 정상 작동하지 않으면 세 번째 경로가 잘못 실행되어 중복 매도 시도 →
"주문수량이 가능수량보다 큽니다" → `sellable_qty_untrusted` 영구 잠금으로 이어진다.

## TODO / 미완성 작업 목록

백로그와 우선순위는 [`docs/important/core/TODO_ROADMAP.md`](docs/important/core/TODO_ROADMAP.md)와
[`docs/important/ACTIVE_WORK.md`](docs/important/ACTIVE_WORK.md)가 관리한다.
이 파일에 중복 기재하지 않는다.

### 완료 (주요 이력)

- soft watch 승격 기본 차단
- continuation live 중단, shadow-only 전환
- live HALT/daily_return 시장별 분리
- stale legacy 포지션 정리
- `brain.json` 중복/상충 기록 정규화
- 브레인/대시보드 한글 깨짐 복원
- US 미체결 주문 `remaining_qty` 누락으로 `open_orders` 필터 실패 수정 (2026-05-27)
- Smart Skip semantic signature 전환: 전체 prompt hash → ticker+action_ceiling 기반, 가격 노이즈 무시 (2026-06-04)
- Runtime handoff snapshot: 재시작 시 today_tickers·trade_ready·price_cache·post_open evidence 복원 (2026-06-04)
- Post-open feature JSONL 복원: 세션별 최신 스냅샷 persist → 재시작 후 evidence 연속성 유지 (2026-06-04)
- INTRADAY_REVIEW gate: per-position cooldown 120분 + daily max 3회, 트리거 기반 우회 (2026-06-04)
- AUTO_SELL_REVIEW hard guard cache bypass: hard_guard 발동 시 stale cache 우회 → fresh Hold Advisor 호출 (2026-06-04)
- sub_screener Plan A min score floor: `SUB_SCREENER_PLAN_A_MIN_SCORE=70`, 저품질 trigger 방지 (2026-06-04)
- `brain.json` pre-commit hook: `tools/check_brain_commit.py`, 코드 커밋에 brain 혼입 차단 (2026-06-04)
- operator_summary 추가: current trading risk / previous-session cleanup 분리 표시 (2026-06-04)
- v2 성과 sync 정합성: v2_event_store → decisions.db 최신 CLOSED 이벤트 반영 경로 보강 (2026-06-05)

## 재시작 / 장애 복구 절차

1. 브로커 보유 종목 조회
2. 브로커 미체결 주문 조회
3. 가능하면 브로커 체결 내역 조회
4. legacy state와 비교
5. stale 포지션 제거 또는 보호 상태 전환
6. 시장별 HALT / baseline 계산 확인
7. 신규 진입 허용 여부 판단

## 모의투자 → 실거래 전환 체크리스트

- 실거래 키/계좌 확인
- 모의 토큰/상태 제거 여부 확인
- KR/US 최대 주문 금액 확인
- 리스크 한도 확인
- 브로커 동기화 테스트 1회
- 대시보드 live 값과 실제 계좌 대조

## 전략 추가/수정 시

- 전략 추가보다 입력 품질 개선이 먼저다.
- selection 품질, 진입 시점 품질, 로그 설명 가능성을 먼저 본다.
- 새 전략은 shadow로 검증한 뒤 live로 올린다.

## Telegram 운영 명령어

- `/status` 현재 상태
- `/pos` 보유 포지션
- `/review` 보유 종목 재검토
- `/setorder [금액]` 최대 주문 금액 변경
- `/setloss [%]` 일일 손실 한도 변경
- `/trail on|off` 트레일링 on/off
- `/entry on|off` entry priority on/off
- `/brain` 브레인 요약
- `/credit` API 사용량 확인

## 커밋 전 체크리스트

- `git diff --stat`
- `git status --short`
- 단계별 테스트 통과
- 전체 QA 통과
- 수익률 계산축/KR·US 분리 재확인
- 대시보드 live truth 경로 재확인
