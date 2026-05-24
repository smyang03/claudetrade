# Screener Live/Shadow Implementation QA

- 작성일: 2026-05-25 KST
- 기준 계획: `docs/reports/screener_live_shadow_execution_plan_20260525.md`
- 대체 목적: 임시 실행 계획 MD 삭제 후 구현/검증/운영 테스트 기록으로 보존

## 1. 구현 요약

### Live Observability

selection, `trade_ready`, 주문 수량, PathB, daily entry cap은 변경하지 않고 관측/감사 필드만 추가했다.

- 실제 Claude prompt 최종 순서 저장:
  - call payload: `actual_prompt_ranked_tickers`
  - candidate row: `prompt_rank_after_trim`
- audit schema 확장:
  - `prompt_rank_after_trim`
  - `bucket_reasons_json`
  - `bucket_data_gaps_json`
  - `bucket_seen_count`
  - `first_bucket_detected_at`
  - `last_bucket_detected_at`
  - `earliest_bucket_detected_at`
- `_write_candidate_audit_live()`에서 audit-only bucket enrichment 적용:
  - prompt/excluded row에 `primary_bucket`이 없거나 `unclassified`이면 `classify_candidate_bucket()`으로 감사 row만 보강
  - trainer ranking, prompt cap, Claude 입력 후보, `trade_ready` 승격에는 사용하지 않음
- screener quality log에 projected volume/KIS overlap 관측 필드 보존.

### US Projected Dollar Volume Shadow

기존 dollar-volume reject 판단은 그대로 유지하고, reject된 후보만 shadow로 기록한다.

- 기록 필드:
  - `current_dollar_vol`
  - `projected_dollar_vol`
  - `elapsed_session_fraction`
  - `would_pass_projected_dollar_vol`
  - `current_filter_rejected_reason`
- 로그:
  - `logs/screener/YYYYMMDD_US_projected_dollar_volume_shadow.jsonl`
- 운영 영향:
  - 기존 필터 통과/탈락 결과 변경 없음
  - projected 값으로 후보를 통과시키지 않음

### US KIS Ranking Shadow

KIS 해외 ranking은 기본 비활성 shadow로 추가했다.

- opt-in:
  - `US_KIS_RANKING_SHADOW_ENABLED=true`
  - trading bot은 이 값이 켜졌을 때만 US token을 `screen_market_us()`에 전달
- 수집 대상:
  - `/uapi/overseas-stock/v1/ranking/trade-vol`
  - `/uapi/overseas-stock/v1/ranking/updown-rate`
- 로그:
  - `logs/screener/YYYYMMDD_US_kis_ranking_shadow.jsonl`
- 기록 내용:
  - Yahoo/KIS category별 overlap
  - KIS-only / Yahoo-only sample
  - heuristic suffix product-filter shadow count
  - KIS shadow collection failure reason
- 운영 영향:
  - Yahoo/FMP/fallback 반환 후보 변경 없음
  - KIS 실패 시 기존 Yahoo path 유지
  - KIS 데이터를 primary로 사용하지 않음

## 2. Live/Shadow 경계 재검토

Live로 들어간 항목은 metadata 저장과 로그 확장뿐이다.

- ranking score 변경 없음
- prompt cap 변경 없음
- `trade_ready` 승격 변경 없음
- PathB/order/risk 설정 변경 없음
- `.env.live`, `.env.paper`, `config/v2_start_config.json` 변경 없음

Shadow 항목은 outcome 축적 전까지 행동 변경에 쓰지 않는다.

- projected dollar volume은 `would_pass`만 기록
- KIS ranking은 source overlap만 기록
- product filter는 suffix heuristic count만 기록

## 3. 검증 결과

실행한 검증:

```text
python -m py_compile trading_bot.py kis_api.py audit/candidate_audit_store.py bot/screener_quality.py dashboard/dashboard_server.py claude_memory/brain.py
python -m pytest tests/test_screener_quality.py tests/test_candidate_action_live_mapping.py tests/test_candidate_audit.py tests/test_dashboard_candidate_audit_api.py -q
python tools/live_preflight.py --mode paper --skip-dashboard --json
```

결과:

- py_compile 통과
- 관련 테스트 109 passed
- paper preflight `ok=true`, `fail_count=0`
- preflight warning은 13건이며, PathB intraday policy mismatch/runtime snapshot/broker truth stale/data freshness 같은 기존 운영 경고다. 이번 스크리너 관측 변경으로 인한 failure는 없다.

## 4. 계획 대비 차이/누락점

구현 완료:

- 실제 prompt ticker/count 저장 확인
- `prompt_rank_after_trim` 저장 확인
- bucket metadata audit/log 보존 확인
- US projected dollar volume shadow 확인
- US KIS ranking shadow overlap 로그 확인
- dashboard/audit API 관련 테스트 확인

의도적으로 제외:

- KIS ranking primary 전환
- projected dollar volume 기반 reject 완화
- bucket metadata trainer score 반영
- near-cap overlay live 승격
- `trade_ready`/주문 cap/PathB 정책 변경

현재 계획 범위 기준 미해결 누락은 없다. 행동 변경 항목은 별도 shadow outcome 축적 후 다시 판단한다.

## 5. 운영 테스트 후 재검토 결론

현재 변경은 운영상 live observability로 적용 가능하다. 단, 아래는 즉시 live behavior로 승격하지 않는다.

- projected dollar volume으로 US early reject 통과
- KIS US ranking primary 전환
- bucket metadata trainer 가중 반영
- PLAN_A/near-cap 자동 우대

다음 판단 기준:

- projected dollar volume: 최소 5거래일 reject 후보 outcome 비교
- KIS ranking: Yahoo/KIS overlap, KIS-only 품질, fallback 안정성 확인
- bucket metadata: `primary_bucket`별 forward outcome과 Claude 선택/route/fill 성과 분리 확인
