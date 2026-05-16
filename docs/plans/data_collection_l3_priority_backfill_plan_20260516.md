# Data Collection L3 Priority Backfill Plan

작성일: 2026-05-16 KST

## 결론

L3는 지금 즉시 구현하지 않는다.

현재 확인된 주요 후보 유입 경로에서는 스크리너 실행 시점에 이미 `price_collection_priority_{MARKET}_{YYYYMMDD}.json`이 생성된다. 따라서 L1/L2 수정 이후에는 priority JSON에 들어간 신규 ticker가 정기 collector에서 조용히 탈락하지 않는다.

L3는 핀 종목 또는 수동 주입처럼 스크리너를 거치지 않는 후보의 보강 실패를 collector에 역전파하는 안전망이다. 현재는 당일 백그라운드 히스토리 보강 큐와 다음 session_open/rescreen 재시도로 완화되므로, 운영 증거가 나오기 전까지는 복잡도를 추가하지 않는다.

## 현재 구조

### On-demand 경로

```text
session_open / rescreen
→ _screen_market_candidates()
→ _prefill_history_sync()
→ _filter_candidates_by_history()
→ 정상 / WATCH_DATA_INSUFFICIENT / HISTORY_UNAVAILABLE
```

역할:
- 당일 필요한 후보를 즉시 보강한다.
- 상위 일부 후보를 제한 시간 안에 동기 fetch한다.
- 실패 또는 시간 초과분은 `_hist_fill_queue`로 넘긴다.
- 데이터 검증 전에는 매매 후보로 승격하지 않는다.

### Collector 정기 경로

```text
price_collector.py --update
→ all_tickers = 기본 티커 + 기존 CSV
→ _prioritize_ticker_map()
→ OHLCV fetch
→ CSV 저장
```

L1/L2 이후:
- priority JSON에 있는 신규 ticker도 collector 대상에 포함된다.
- KR collector도 priority 적용을 받는다.

## L3가 커버하는 케이스

L3는 다음 케이스만 추가로 커버한다.

- `_load_preopen_pin_candidates()`로 들어온 핀 종목
- 수동 주입 후보
- 기타 스크리너를 거치지 않는 후보

이 후보들이 CSV가 없고 `_prefill_history_sync()` 또는 백그라운드 보강까지 실패하면, 현재 collector는 별도 priority 기록이 없는 한 다음 정기 수집에서 모를 수 있다.

## 지금 구현하지 않는 이유

- 일반적인 스크리너 후보는 `_write_price_collection_priority()`를 통해 이미 priority JSON에 기록된다.
- L1/L2로 priority JSON 신규 ticker 탈락 문제는 해결됐다.
- L3를 넣으면 live 봇의 후보 보강 함수에 파일 쓰기 부작용이 생긴다.
- priority JSON 날짜 기준이 애매해진다. 예: 오늘 파일인지 다음 세션 파일인지.
- live 봇과 collector가 같은 파일을 동시에 읽고 쓸 가능성이 생긴다.
- 현재 커버할 잔여 갭은 핀/수동 주입 후보 중심이며, 빈도가 낮고 기존 백그라운드 큐로 일부 완화된다.

## 재검토 조건

다음 증거가 나오면 L3 구현을 재검토한다.

- 핀 종목 또는 수동 주입 종목이 반복적으로 `HISTORY_UNAVAILABLE` 상태로 남는다.
- `_hist_fill_queue` 재시도 후에도 CSV가 생성되지 않은 신규 후보가 다음 collector에서도 누락된다.
- 운영 로그에서 같은 신규 ticker가 2개 이상 세션 연속으로 데이터 부족 때문에 매매 후보에서 제외된다.
- 수동 핀 종목 사용 빈도가 늘어나고, 해당 후보의 CSV 안정화가 운영상 중요해진다.

## L3 구현안

필요해지면 다음 방식으로 구현한다.

1. 별도 inject 파일을 사용한다.

```text
state/price_collection_inject_{MARKET}_{YYYYMMDD}.json
```

2. live 봇은 `_prefill_history_sync()`에서 CSV 없음 또는 보강 실패 ticker를 inject 파일에 append/upsert한다.

3. collector는 시작 시 다음 목록을 병합한다.

```text
기본 티커
+ 기존 CSV ticker
+ price_collection_priority ticker
+ price_collection_inject ticker
```

4. 파일 쓰기는 atomic write로 처리한다.

5. inject row에는 최소 필드를 남긴다.

```json
{
  "ticker": "ABCX",
  "market": "US",
  "source": "live_prefill_failed",
  "first_seen_at": "2026-05-16T09:00:00+09:00",
  "last_seen_at": "2026-05-16T09:01:00+09:00",
  "attempt_count": 1
}
```

## 검증 계획

L3를 구현하는 경우 다음 테스트를 추가한다.

- 스크리너를 거치지 않은 신규 ticker가 prefill 실패 시 inject 파일에 기록되는지 확인
- collector가 inject ticker를 기존 map에 없어도 수집 대상으로 포함하는지 확인
- 동일 ticker 중복 기록 시 `attempt_count`와 `last_seen_at`만 갱신되는지 확인
- malformed inject JSON이 collector를 실패시키지 않고 무시되는지 확인

## 현재 상태

현재는 L3 보류.

운영 기준으로는 L1/L2가 우선 적용된 상태면 충분하다. L3는 핀/수동 주입 후보의 CSV 누락이 실제 운영 로그로 반복 확인될 때 진행한다.
