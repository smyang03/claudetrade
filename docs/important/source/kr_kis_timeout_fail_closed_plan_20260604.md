# KR KIS Timeout / Evidence Fail-Closed Plan

작성일: 2026-06-04

## 목적

US/yfinance 경로는 2026-06-02 및 2026-06-03 US 세션에서 `coverage_ratio=1.0`, `fail_closed_applied=false`로 안정적이었다. 현재 품질 저하 위험은 US가 아니라 KR/KIS intraday evidence fetch timeout에서 발생한다.

이 문서는 KR/KIS timeout 때문에 정상 후보까지 과도하게 WATCH로 묶이는 문제를 줄이기 위한 개선 방향만 다룬다.

## 현재 판단

- US/yfinance는 현 구조 유지가 적합하다.
- KR/KIS는 `provider_timeout`, `prefetch_timeout`, 일부 KIS 500 응답이 반복되며 coverage가 threshold 아래로 내려갈 수 있다.
- coverage below threshold 상황에서 `coverage_below_threshold` fail-closed가 발생하면 후보 품질이 과하게 보수화될 수 있다.
- 전역 fail-closed 완화는 금지한다. provider 장애 중 매수 경로를 열면 더 위험하다.

## 핵심 원칙

1. provider disabled, session open resolve 실패, complete 0개 수준의 전면 장애는 hard fail-closed를 유지한다.
2. 일부 ticker만 timeout인 경우에는 실패 ticker만 fail-closed 처리한다.
3. `minute_complete` ticker는 `confirmed` 상태를 보존하고, 세션 coverage 저하는 별도 경고 메타데이터로 남긴다.
4. US/yfinance 정책은 변경하지 않는다.
5. 주문 수량, PathB submit, broker truth, hard risk gate는 이 개선 범위에서 제외한다.

## 개선 방향

### P0: 관측 분리

- KR evidence coverage 리포트에 다음 카운트를 분리한다.
  - `complete_count`
  - `partial_count`
  - `missing_count`
  - `provider_timeout_count`
  - `prefetch_timeout_count`
  - `kis_500_count`
  - `complete_but_session_degraded_count`
- `fail_closed_applied=true`일 때 complete ticker가 hard-block으로 바뀌었는지 별도 집계한다.

### P1: fail-closed 의미 분리

현재 구조는 session coverage 문제와 ticker-level evidence 부재가 섞일 수 있다. 다음 두 플래그로 분리한다.

- `fail_closed=true`: 해당 ticker 자체가 hard block 대상일 때만 사용한다.
- `session_evidence_degraded=true`: 세션/provider coverage가 낮다는 경고로만 사용한다.

권장 처리:

| 상황 | 처리 |
|---|---|
| provider disabled | 전체 hard fail-closed |
| session open resolve 실패 | 전체 hard fail-closed |
| complete 0개 | 전체 hard fail-closed 가능 |
| coverage below threshold + 일부 complete 존재 | missing/partial ticker만 fail-closed |
| minute_complete ticker + session degraded | confirmed 유지, 경고만 부여 |

### P2: KR KIS fetch 안정화

- 요청 ticker 수를 phase/priority 기준으로 줄인다.
- position, entry_ready, pathb_wait, watch_strengthening 순서로 우선순위를 둔다.
- timeout ticker는 즉시 전체 fail-closed로 몰지 않고 retry queue로 분리한다.
- 최근 complete snapshot은 짧은 TTL 안에서 재사용한다.
- KIS 500과 timeout은 원인을 분리해서 기록한다.

## Replay 검증

live 주문 없이 replay로 충분하다.

1. KR `selection_intraday_evidence_coverage` JSONL에서 timeout/coverage timeline을 읽는다.
2. 현재 로직 기준 fail-closed ticker 수와 complete ticker 과차단 수를 계산한다.
3. 개선 로직 기준으로 missing/partial ticker만 fail-closed 처리했을 때 WATCH demotion 수가 얼마나 줄어드는지 비교한다.
4. demotion에서 살아난 ticker가 이후 30/60/180분에 실제로 움직였는지 outcome만 확인한다.

## 수용 기준

- US/yfinance 지표 변화 없음.
- provider 전면 장애에서는 기존 hard fail-closed 유지.
- KR partial timeout 상황에서 `minute_complete` ticker는 `data_state=confirmed`를 유지.
- fail-closed 로그에서 ticker-level hard block과 session-level degraded warning이 분리되어 보임.
- 주문/수량/브로커 truth/PathB live submit 동작 변화 없음.
