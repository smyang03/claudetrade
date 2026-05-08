# Entry Risk Control QA - 2026-05-08

## Summary

이번 작업은 후보군/큐레이션 대수술 전에 바로 적용 가능한 손실 축소 로직을 먼저 반영한 것이다.

구현 완료:

- KR/US 일일 신규 진입 cap: 기본 2건, 시장별 env 우선
- KR `claude_price` 신규 진입 차단: PathB 신규 매수만 차단, 기존 waiting 관리/청산 유지
- KR `continuation` 신규 진입 차단: PlanA KR continuation 매수만 차단
- PlanA MFE breakeven 보호: MFE 2.5% 이상 후 본전권 훼손 시 `mfe_breakeven` 청산 후보 생성
- US broker_sync quarantine: US broker trust가 `degraded`/`untrusted`이면 신규 매수 차단
- 신규 env 문서와 회귀 테스트 추가

## Implemented Files

- `docs/plans/entry_risk_control_development_20260508.md`
  - 개발 리스트와 단계별 계획 문서화
- `runtime/v2_lifecycle_runtime.py`
  - `max_daily_entries(market)` 시장별 cap 지원
  - `KR_DAILY_ENTRY_CAP` / `US_DAILY_ENTRY_CAP` 우선, 없으면 전역 cap, 그것도 없으면 KR/US 기본 2
- `runtime/pathb_runtime.py`
  - PathB safety context에 market별 cap 전달
  - KR `claude_price` 신규 매수 차단
  - 단, waiting plan의 cancel 조건과 pending reconcile은 계속 실행
  - KR 신규 매수 차단 시 ticker별 blocked event를 남기고 cycle summary 로그에 count/sample을 남김
- `trading_bot.py`
  - KR `continuation` 신규 진입 차단
  - US broker quarantine을 `_new_buy_block_state()`와 broker entry gate에 반영
  - PlanA `mfe_breakeven` audit metadata, close owner, auto sell review bypass 처리
- `risk_manager.py`
  - PlanA 포지션용 MFE breakeven exit candidate 생성
  - KR 포지션의 `entry`가 비어 있으면 `avg_price` / `entry_price` / `buy_price` fallback 사용
  - PathB 중복 적용 제외는 `pathb_path_run_id` 또는 `path_type=claude_price` 기준으로 제한
  - hard stop / loss cap이 먼저 평가되도록 유지
- `execution/safety_gate.py`
  - US `degraded`/`untrusted` broker trust를 `BROKER_SYNC_QUARANTINE`으로 차단
  - 세부 정책명은 `policy_name=us_broker_trust_quarantine`으로 기록
- `config/v2.py`
  - 신규 safety/close reason code 등록
- `.env.example`
  - 신규 control env 문서화
- `tests/test_entry_risk_controls.py`
  - cap, quarantine, MFE 회귀 테스트 추가

## Verification

실행한 검증:

```powershell
python -m py_compile risk_manager.py trading_bot.py execution\safety_gate.py runtime\v2_lifecycle_runtime.py runtime\pathb_runtime.py config\v2.py
```

결과: pass

```powershell
python -m pytest tests\test_entry_risk_controls.py tests\test_v2_daily_loop.py tests\test_live_config_sources.py -q
```

결과: 12 passed

```powershell
python -m pytest tests\test_entry_risk_controls.py tests\test_pathb_runtime.py tests\test_order_equity_reconciliation_improvement.py tests\test_preopen_opening_role_separation.py -q
```

결과: 70 passed

```powershell
python -m pytest tests\test_auto_sell_claude_gate.py tests\test_soft_exit_arbitration.py tests\test_patha_contract.py tests\test_live_order_safety.py tests\test_pathb_safety.py tests\test_loss_cap_profit_floor.py -q
```

결과: 61 passed

공통 warning:

- `eventlet`의 `distutils Version classes are deprecated` 경고 2건
- 기능 실패는 아님

## Requirement Comparison

| 요구/계획 | 반영 상태 | 확인 내용 |
|---|---:|---|
| 상세 개발 리스트 MD 저장 | 완료 | `docs/plans/entry_risk_control_development_20260508.md` |
| 단계별 개발 진행 | 완료 | cap -> KR block -> PlanA MFE -> US quarantine 순서로 반영 |
| KR/US market cap 2 | 완료 | env 없을 때 KR/US 기본 2, 시장별 env 우선 |
| KR `claude_price` 신규 진입 동결 | 완료 | PathB KR 신규 매수만 차단, waiting cancel/reconcile 유지 |
| KR `continuation` 신규 진입 동결 | 완료 | KR PlanA continuation만 skip, 기존 청산 경로 영향 없음 |
| PlanA MFE 보호 | 완료 | `mfe_breakeven` candidate 생성, loss_cap이 우선 |
| US broker_sync quarantine | 완료 | US degraded/untrusted 신규 매수 차단, canonical reason `BROKER_SYNC_QUARANTINE` + policy alias `us_broker_trust_quarantine` |
| 기존 포지션 청산 유지 | 완료 | PathB exit scan 미변경, PlanA exit candidate는 보호 방향 추가 |
| QA 진행 및 MD 작성 | 완료 | 본 문서 |
| 기존 계획 대비 차이/누락 비교 | 완료 | 아래 "Differences / Gaps" 참고 |

## Differences / Gaps

계획 대비 구현 차이:

- PlanA MFE의 실제 후보 생성은 `trading_bot.py`가 아니라 `risk_manager.py`에 넣었다.
  - 이유: PlanA exit candidate의 원천이 `RiskManager.get_exit_candidates()`라서 중복 후보 주입보다 안전하다.
  - `trading_bot.py`는 priority/audit/close owner 처리만 담당한다.

- US broker quarantine은 절대 시각 `recheck_at` 대신 `recheck_after_seconds` 메타를 남긴다.
  - 자동 해제는 새 scheduler가 아니라 기존 broker sync가 `trust_level=trusted`로 회복되는 조건을 따른다.
  - 자동 재시도/절대 recheck timestamp는 다음 단계로 남긴다.
  - blocker reason code는 registry와 audit 일관성을 위해 `BROKER_SYNC_QUARANTINE`을 유지하고, 기존 설계명은 `policy_name=us_broker_trust_quarantine`으로 남긴다.

- KR `claude_price` block은 waiting plan 자체를 삭제하지 않는다.
  - 이유: 위쪽 가격 이탈 cancel, order unknown reconcile, 기존 exit는 계속 살아 있어야 한다.
  - 신규 buy signal이 난 경우에만 제출하지 않는다.

의도적으로 제외한 항목:

- WATCH_TRIGGER state machine
- fast lane
- DATA_GAP
- at_high 4분류
- overextended cap 조정
- low_liq_ignite60 live/probe
- prompt/deferred 로그 자동 리포트

## Operational Notes

기본 동작:

- `KR_DAILY_ENTRY_CAP` 또는 `US_DAILY_ENTRY_CAP`이 있으면 해당 값이 최우선이다.
- 시장별 cap이 없고 `V2_MAX_DAILY_ENTRIES`/`MAX_DAILY_ENTRIES`가 있으면 기존 전역 cap을 따른다.
- 아무 cap도 없으면 KR/US는 2건으로 제한한다.
- `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK`, `KR_CONTINUATION_NEW_ENTRY_BLOCK`, `PLANA_MFE_BREAKEVEN_ENABLED`, `US_BROKER_SYNC_QUARANTINE_ENABLED`는 기본 on이다.

다음 관측 포인트:

- cap2 적용 후 시장별 missed MFE와 realized PnL 비교
- `mfe_breakeven` 청산 후 재상승 missed opportunity
- US broker quarantine 발생 빈도와 recovery latency
- KR blocked strategy의 shadow PnL
