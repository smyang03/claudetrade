# Entry Risk Control Development Plan - 2026-05-08

## Current Status - 2026-05-08

Implementation complete.

Code check:

- `runtime/v2_lifecycle_runtime.py` has market-specific `max_daily_entries(market)` and reads `KR_DAILY_ENTRY_CAP` / `US_DAILY_ENTRY_CAP`; KR/US default to 2 when no market/global cap is configured.
- `runtime/pathb_runtime.py` passes market into `_base_max_daily_entries(market)` and blocks KR `claude_price` new buys without blocking waiting-plan management.
- `trading_bot.py` blocks KR `continuation` new entries and blocks US new buys when broker trust is `degraded` or `untrusted`.
- `risk_manager.py` adds PlanA MFE breakeven exit candidates with entry price fallback and only excludes explicit PathB-managed positions; `trading_bot.py` records the MFE audit metadata and maps the exit owner.
- `execution/safety_gate.py` and `config/v2.py` include `BROKER_SYNC_QUARANTINE`; details keep `policy_name=us_broker_trust_quarantine`.
- `tests/test_entry_risk_controls.py` covers market cap, US broker quarantine, and PlanA MFE behavior.

QA report:

- `docs/reports/entry_risk_control_qa_20260508.md`

Recheck:

- `python -m pytest tests/test_entry_risk_controls.py -q` -> `6 passed, 2 warnings`.
- `python -m py_compile trading_bot.py runtime/v2_lifecycle_runtime.py runtime/pathb_runtime.py` -> pass.
- This plan can be removed from the active TODO list once the QA report and `DEVELOPED_WORK.md` summary are kept.

## Background

오늘 분석에서 가장 강한 신호는 후보 확장보다 신규 진입/손익 보호 쪽이었다.

- `per_market_cap_1`: PF 1.42, avg +0.517%
- `cap2 + MFE`: 전체 PF 0.58 -> 1.54
- KR `claude_price`: PF 0.11
- KR `continuation`: 전패 구간 확인

따라서 이번 개발은 `WATCH_TRIGGER` 전체 설계보다 먼저, 확실한 손실 절감 변경만 작은 범위로 적용한다.

## Scope

### In Scope

1. 시장별 일일 신규 진입 cap 분리
   - `KR_DAILY_ENTRY_CAP`
   - `US_DAILY_ENTRY_CAP`
   - fallback: 기존 `V2_MAX_DAILY_ENTRIES` / `MAX_DAILY_ENTRIES`

2. KR 신규 진입 전략 차단
   - PathB `claude_price` 신규 매수 차단
   - PlanA `continuation` 신규 매수 차단
   - 기존 보유 포지션 청산은 유지

3. PlanA MFE breakeven 보호
   - PathB `_pathb_mfe_breakeven_signal()`과 같은 개념을 PlanA 보유 포지션 청산 경로에 추가
   - 기본 공통 env로 시작
   - 추후 KR/US 분리는 별도 단계

4. US broker_sync quarantine 기반 정리
   - broker trust가 `degraded` 또는 `untrusted`이면 신규 매수 차단
   - `recheck_at` / trust recovery 개념을 로그와 상태에 분리 가능하게 남김

5. QA 문서 작성
   - 구현 내용
   - 검증 명령
   - 원 요구사항 대비 반영/누락 비교

### Out of Scope

이번 단계에서는 아래를 구현하지 않는다.

- WATCH_TRIGGER full state machine
- fast lane
- DATA_GAP 상태
- trigger timeout
- at_high 4분류
- overextended cap 조정
- low_liq_ignite60 live/probe 활성화

이 항목들은 prompt/deferred 로그가 며칠 누적된 뒤 별도 개발한다.

## Development Steps

### Step 1. Market-Specific Daily Entry Cap

대상:

- `runtime/v2_lifecycle_runtime.py`
- `runtime/pathb_runtime.py`

작업:

- `V2LifecycleRuntime.max_daily_entries(market)`가 시장별 env를 먼저 읽도록 변경한다.
- `SafetyContext.max_daily_entries`에 시장별 cap이 들어가게 한다.
- PathB `_base_max_daily_entries(market)`도 같은 값을 쓰게 한다.

검증:

- KR env만 있을 때 KR cap 적용
- US env만 있을 때 US cap 적용
- 시장별 env가 없으면 기존 전역 cap 유지

### Step 2. KR Strategy New Entry Block

대상:

- `runtime/pathb_runtime.py`
- `trading_bot.py`

작업:

- KR PathB `claude_price` 신규 진입 차단
- KR PlanA `continuation` 신규 진입 차단
- 기존 PathB exit scan은 유지

검증:

- PathB waiting scan에서 KR만 차단
- US PathB는 영향 없음
- PlanA KR continuation만 차단
- continuation shadow logging은 유지

### Step 3. PlanA MFE Breakeven

대상:

- `risk_manager.py`
- `trading_bot.py`

작업:

- PlanA position의 `peak_pnl_pct` / `position_mfe_pct` 기준으로 breakeven exit 조건 추가
- 기본 env:
  - `PLANA_MFE_BREAKEVEN_ENABLED=true`
  - `PLANA_MFE_BREAKEVEN_TRIGGER_PCT=2.5`
  - `PLANA_MFE_BREAKEVEN_BUFFER_PCT=0.001`
- 기존 hard stop / loss cap보다 우선하지 않도록 한다.

검증:

- MFE가 trigger 미만이면 신호 없음
- MFE가 trigger 이상이고 현재가 breakeven 이하이면 close reason `mfe_breakeven`
- PathB 포지션에는 중복 적용하지 않음

### Step 4. US Broker Sync Quarantine

대상:

- `trading_bot.py`
- `execution/safety_gate.py`
- `config/v2.py`

작업:

- US broker trust가 `degraded` 또는 `untrusted`이면 신규 매수 차단
- 상태에는 `recheck_at`을 남길 수 있도록 설계하되, 자동 해제는 `trust_level=trusted` 조건으로만 본다.

검증:

- US degraded/untrusted 신규 매수 차단
- KR은 기존 정책 유지
- broker_sync 기존 보유 포지션 청산은 유지

## QA Checklist

- `python -m py_compile trading_bot.py runtime/v2_lifecycle_runtime.py runtime/pathb_runtime.py`
- 관련 unit test 실행
- 수동 grep으로 차단 경로 확인
- 신규 env 문서 반영 여부 확인
- 요구안 대비 누락점 비교 문서 작성

## Risk Control

- 모든 변경은 신규 진입에만 적용한다.
- 기존 보유 포지션 청산, stop, broker recovery는 막지 않는다.
- env fallback을 유지해 기존 설정과 호환되게 한다.
- WATCH_TRIGGER 계열 변경은 이번 PR/작업 범위에서 제외한다.
