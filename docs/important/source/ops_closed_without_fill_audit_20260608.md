# CLOSED_WITHOUT_FILL 전체 감사 - 2026-06-08

## 결론

`v2_learning_performance` 기준 live `CLOSED_WITHOUT_FILL`는 총 3건이며 모두 US PathB `claude_price` 계열이다.

성과 왜곡은 크지 않다.

- 현재 US PathB filled+closed: 115건, 평균 `+0.7558%`, 승률 `50.43%`
- high-confidence 2건을 포함하면: 117건, 평균 `+0.7584%`, 승률 `50.43%`
- manual-review 1건까지 포함하면: 118건, 평균 `+0.7410%`, 승률 `50.00%`

즉, 지금까지의 US PathB 수익성 결론을 뒤집을 정도는 아니다. 다만 학습/리포트 정합성 문제이므로 future fix와 별도 backfill 검토는 필요하다.

## 케이스 분류

| 일자 | 종목 | 분류 | 판단 | PnL |
|---|---|---|---|---:|
| 2026-05-07 | FTNT | `closed_event_unlinked_manual_cleanup_conflict` | 수동 검토 필요 | `-1.2979%` |
| 2026-05-15 | MSFT | `linked_closed_without_fill_event` | high-confidence backfill 후보 | `-1.5493%` |
| 2026-05-29 | SMCI | `path_run_attribution_mismatch` | high-confidence attribution repair 후보 | `+3.3698%` |

## 추가로 확인한 실제 버그

`SMCI`는 같은 decision 안에 두 개 path run이 있었다.

- 실제 거래/청산 path run: `path_20260529_US_SMCI_claude_price_ab9a08a5`
- 나중에 생성 후 취소된 path run: `path_20260529_US_SMCI_claude_price_c92d2503`

기존 `sync_v2_learning_performance.py`는 entry fill event가 없을 때 generic fallback으로 뒤쪽 path run payload를 잡아 cancelled run에 성과를 귀속할 수 있었다.

## 반영 완료

- `tools/audit_closed_without_fill.py`
  - read-only 전체 감사 도구 추가
  - `CLOSED_WITHOUT_FILL` row를 분류하고 성과 왜곡량을 계산
  - live DB write 없음

- `tools/sync_v2_learning_performance.py`
  - fill event가 없을 때 generic fallback보다 `CLOSED` event의 `path_run_id`를 우선하도록 수정
  - close 이후 재등록/취소된 path run으로 성과가 drift되는 문제를 방지

- 테스트
  - `tests/test_audit_closed_without_fill.py`
  - `tests/test_v2_learning_performance_sync.py::test_sync_prefers_close_payload_path_run_over_later_cancelled_run_when_fill_is_missing`

## 산출물

- `.runtime/ops_simulation_analysis/closed_without_fill_audit_20260608/closed_without_fill_audit.json`
- `.runtime/ops_simulation_analysis/closed_without_fill_audit_20260608/closed_without_fill_audit.md`
- `.runtime/ops_simulation_analysis/closed_without_fill_audit_20260608/closed_without_fill_cases.csv`

## 과거 데이터 처리 판단

이번 작업에서는 과거 live DB를 수정하지 않았다.

권장 순서:

1. `MSFT`, `SMCI`만 high-confidence audited backfill 후보로 분리
2. `FTNT`는 manual cleanup conflict가 있으므로 broker/order evidence를 운영자가 확인하기 전까지 자동 backfill 금지
3. backfill 전/후 `sync_v2_learning_performance` dry-run 결과 비교
4. 승인 후 별도 repair script로 live DB 수정

## 운영 영향

- 주문, sizing, slippage, broker truth, exit 정책 변경 없음
- `.env*`, `config/v2_start_config.json`, `state/brain.json` 변경 없음
- 기존 live DB write 없음
- future sync와 future runtime attribution 품질만 개선

## 검증

- `python -m py_compile tools\audit_closed_without_fill.py`
- `python -m pytest tests\test_audit_closed_without_fill.py -q`
- `python -m pytest tests\test_v2_learning_performance_sync.py::V2LearningPerformanceSyncTests::test_sync_prefers_close_payload_path_run_over_later_cancelled_run_when_fill_is_missing tests\test_v2_learning_performance_sync.py::V2LearningPerformanceSyncTests::test_sync_prefers_fill_payload_path_run_over_first_registered_run -q`
- `python tools\audit_closed_without_fill.py --output-dir closed_without_fill_audit_20260608 --json`
- `python tools\sync_v2_learning_performance.py --market US --start-date 2026-05-29 --end-date 2026-05-29 --dry-run`

## 남은 리스크

- `MSFT`, `SMCI`, `FTNT` 과거 row는 아직 live DB에 남아 있다.
- audited backfill은 별도 승인/스크립트 없이 자동 수행하지 않는다.
- `FTNT`는 close event와 manual cleanup 이력이 충돌하므로 자동 복구 대상이 아니다.
