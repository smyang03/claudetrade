# 라이브 봇 메모리 증가 진단 (2026-10-02, 정적 분석 + 오프라인 실측)

대상: `trading_bot.py --live` PID 9168 (09-30 18:14 기동). 코드·config 변경, 봇 접근 없음.

## 결론

**1순위 원인은 분봉 히스토리 RVOL 캐시다** (`runtime/time_normalized_rvol.py`). 세션이 바뀔 때마다 장중 evidence 대상 종목의 **분봉 CSV 전체**가 메모리에 새로 올라가고, 지난 세션 사본은 지워지지 않는다. 오프라인 실측으로 1세션에 **KR 약 110~185MB, US 약 135~330MB(단일 사본)**이다. 관측된 하루 약 400MB 증가를 이 하나로 대부분 설명할 수 있다. 2·3순위(`kis_api._INTRADAY_CACHE`, `IntradayMinuteCache._bar_cache`)는 같은 장중 evidence 경로에서 나오며, 합쳐서 하루 수십 MB 수준이다. WS 재연결, Telegram, 주문·리스크 컨테이너는 원인이 아니다.

관측치와의 대조:
- 09-30 18h에서 10-02 01h(31h)까지 +360MB, 10-02 01h에서 22h(21h)까지 +390MB였다.
- 이 구간에 새 세션 키는 약 4~5개 생겼다(US 09-30, KR 10-01, US 10-01, KR 10-02, US 10-02 프리오픈). 세션당 약 150MB로 맞아떨어진다.
- 증가 속도는 일정하지 않고 **매일 커진다**. CSV가 종목당 하루 약 390행씩 늘고, 캐시가 그 전체를 올리기 때문이다.

## 실측 근거 (읽기 전용 오프라인 재현)

1. **장중 evidence provider 호출 수**: `logs/funnel/selection_intraday_evidence_coverage_*.jsonl`의 `requested − used_cache` 합이다.

   | 날짜 | KR | US |
   |---|---|---|
   | 09-30 | 483 | 706 |
   | 10-01 | 490 | 793 |
   | 10-02 (22h 기준) | 479 | 169 |

   하루 약 1,200~1,300회다.

2. **RVOL 캐시 크기**: `LocalTimeNormalizedRvolStore._history()`를 실제 CSV로 그대로 호출해 tracemalloc으로 쟀다.
   - 하한: 이벤트 로그의 target 샘플 종목(이벤트당 10개만 기록됨)만 넣은 값

     | 날짜 | KR | US |
     |---|---|---|
     | 10-01 | 45종목, 106MB | 31종목, 186MB |
     | 10-02 | 39종목, 118MB | 21종목, 135MB |

   - 상한 근사: 그날 `run_cycle` tickers 합집합을 넣은 값

     | 날짜 | KR | US |
     |---|---|---|
     | 10-01 | 67종목, 177MB | 80종목, 326MB |
     | 10-02 | 67종목, 185MB | 76종목, 329MB |

   - 종목당 KR 약 2.4~3MB, US 약 4.4~6.4MB다. 행당 약 370B로, CSV 크기의 약 3.7배다.
   - 런타임 루트는 effective-config 기준 `E:\code\claudetrade`이므로 측정에 쓴 CSV(`data/price/minute/{kr,us}`, 475MB, 1,769파일)가 봇이 읽는 것과 같다.

3. **private bytes(2.6GB)와 working set(1.65GB)의 차이**는 `{"ts","volume"}` 소형 dict 수십만 개가 pymalloc arena를 파편화하는 패턴과 맞는다(보조 정황).

4. **스레드 8개, 핸들 약 1,055개로 안정적이다**. WS 재연결 시 `_supervise` 스레드가 고아로 남는 경합(아래 기타 항목)은 지금 일어나지 않고 있다는 증거다.

## 상위 5 용의자

### 1. `LocalTimeNormalizedRvolStore._cache`: 분봉 CSV 전체를 세션마다 새로 캐시함 (추정 하루 250~500MB, 계속 증가)

- **정의**: `runtime/time_normalized_rvol.py:167`. 키는 `(market, ticker, session_date)`, 값은 `(mtime, list[{"ts","volume"}])`이다.
- **증가 지점**: `runtime/time_normalized_rvol.py:204` (`_history`)
- **왜 커지나**
  - 필터는 `ts.date() >= session_date`만 버리기 때문에, **5월 이후 전 기간 행을 전부** 적재한다(`:193`). 실제 계산은 `lookback_sessions=20`만 쓴다.
  - 키에 session_date가 들어 있어서 새 세션마다 종목별로 새 사본이 생긴다. TTL·상한·clear가 하나도 없다.
- **사본이 두 개다**
  - `IntradayMinuteCache.rvol_store`(`runtime/intraday_minute_cache.py:69`)를 `_fetch_one`이 `:309`에서 **무조건** 호출한다.
  - 모듈 싱글턴 `runtime/intraday_features.py:16-23` `_RVOL_STORE`는 `compute_intraday_features`의 `:262`에서 호출된다(usable bar가 있을 때).
  - 따라서 bar가 있는 종목은 같은 히스토리가 **두 번** 올라간다.
- **트리거**: `trading_bot._prefetch_selection_intraday_evidence`(`trading_bot.py:16293` 이하)가 `get_many`를 부르고, `_fetch_one`을 거쳐 `_history`에 도달한다. selection phase마다 최대 30종목(`INTRADAY_EVIDENCE_MAX_TICKERS=30`)이며, KR·US 각각 하루 약 30회다. PathB도 같은 경로를 쓴다(`runtime/pathb_runtime.py:1678`).
- **확인 방법**
  - 계측 없이 바로 할 수 있는 것: `logs/system/bot_mem_sample.csv`(10분 간격)를 보면 된다. 이 누수는 **세션 첫 prefetch 시점의 계단**으로 나타난다(KR 약 08:50~09:00, US 약 22:30 전후). 계단 높이는 KR 약 100~180MB, US 약 130~330MB다.
  - 장 사이 유휴 시간에 평탄하면 이 항목이 확정이다.
  - 계측 제안: 1시간마다 `len(store._cache)`, `{k[2] for k in store._cache}`(session_date 종류), `sum(len(v[1]) for v in store._cache.values())`를 로그에 남긴다. 대상은 두 인스턴스(`bot._intraday_minute_cache.rvol_store`, `runtime.intraday_features._RVOL_STORE`) 모두다. session_date가 2개 이상이면 누수가 확정이다.
- **최소 수정안**
  - ① 인스턴스를 하나로 공유한다. `IntradayMinuteCache`가 `intraday_features._rvol_store()`를 쓰게 한다.
  - ② 삽입할 때 session_date가 다른 같은 (market, ticker) 키를 먼저 버린다. 또는 키를 `(market, ticker)`로 바꾸고 session_date를 값에 둔다.
  - ③ 마지막 `lookback_sessions`(20) 세션 행만 유지한다. 더 줄이려면 세션별 누적거래량 프로파일(분→누적량)만 보관한다.
  - ①+② 만으로 상주량은 "당일 대상 종목 × 1사본"으로 고정되고, ③을 더하면 한 자릿수 MB대로 떨어진다.

### 2. `kis_api._INTRADAY_CACHE`: 키에 초 단위 `end_at`이 들어가 매 호출이 새 엔트리가 됨 (추정 하루 30~60MB)

- **정의**: `kis_api.py:101`. 쓰기는 `kis_api.py:1832` `_cache_set(_INTRADAY_CACHE, cache_key, df.copy())`, 키 생성은 `:1773-1781`이다.
- **왜 커지나**
  - 키가 `(…, session_date, start_at[:19], end_at[:19])`이고, `end_at=known_at`은 `datetime.now()`를 초 단위로 자른 값이다(`trading_bot.py:16282`, `16318`).
  - 그래서 캐시 적중은 사실상 0이고, 매 provider 호출마다 분봉 DataFrame 사본 하나가 영구히 남는다.
  - `_cache_get`(`:334`)은 TTL이 지났는지 판정만 하고 삭제하지 않는다.
- **크기**
  - 390봉 DataFrame 1개의 deep 크기는 약 71KB(실측)다. 세션 평균은 약 30~40KB로 본다.
  - 하루 호출 수 약 1,200~1,300을 곱하면 **하루 약 40~50MB**다. 세션 후반으로 갈수록 엔트리가 커지므로 증가가 선형 램프로 나타난다.
- **확인 방법**: 1시간마다 `len(kis_api._INTRADAY_CACHE)`를 로그에 남긴다. 하루 약 1.2k씩 단조 증가하면 확정이다.
- **최소 수정안**
  - 키에서 `end_at`을 빼거나 분 단위로 내림한다(TTL이 30초이므로 분 단위로 충분하다).
  - `_cache_set` 시점에 만료 엔트리를 정리하거나 크기 상한(예: 512)을 둔다.
  - `_cache_get`에서 만료를 확인하면 그 자리에서 pop한다.

### 3. `IntradayMinuteCache._bar_cache` / `_cache`: 지난 세션 키가 남음 (추정 하루 10~25MB)

- **정의**: `runtime/intraday_minute_cache.py:70`, `:73`. 봇이 `trading_bot.py:1144`에서 보유한다.
- **증가 지점**: `_store`의 `:144-153`. 당일 전체 bar를 `tuple(tuple(sorted(row.items())))`로 보관한다. 390봉 기준 약 190KB(실측)이고, 정규화 필드를 포함하면 종목·세션당 150~300KB다.
- **왜 커지나**
  - `_cached()`(`:106-116`)는 TTL이 지나면 `_cache`만 pop하고, `_bar_cache`는 건드리지 않는다.
  - `clear()`는 retry 경로(`trading_bot.py:16309`)와 `_reset_session_live_caches`(`trading_bot.py:19493-19506`)에서만 불리는데, 두 곳 모두 **현재 session_date와 해당 ticker 목록만** 지운다.
  - 그래서 이전 세션 키와, prefetch는 됐지만 selected가 아닌 종목의 키가 영구히 남는다.
- **확인 방법**: `len(bot._intraday_minute_cache._bar_cache)`와 키의 session_date 종류(`k[1]`)를 1시간마다 기록한다.
- **최소 수정안**
  - `_cached()`에서 만료 시 `_bar_cache`도 함께 pop한다.
  - session_open(또는 `_store` 진입)에서 session_date가 오늘이 아닌 키를 전부 제거한다.

### 4. `TradingBot._ohlcv_cache` / `kis_api._OHLCV_CACHE`: 일봉 DataFrame, 퇴출 없음 (상한형, 수십 MB에서 포화)

- **정의**: `trading_bot.py:965-966`(`_ohlcv_cache`, `_ohlcv_cache_time`), 쓰기는 `trading_bot.py:35782`, `35815`. `kis_api.py:100`(`_OHLCV_CACHE`), 쓰기는 `:2033`이다.
- **왜 커지나**
  - 키가 ticker(또는 `(KR, ticker, lookback)`)라서 시간에 따라 무한히 커지지는 않는다.
  - 다만 손대는 유니버스(KR 그물 약 800 + US)만큼 쌓이고, 오래된 종목은 빠지지 않는다. 일봉 DataFrame이 종목당 5~50KB이므로 포화 시 약 20~60MB다.
  - 기동 직후 며칠 동안의 증가분 중 일부가 이것일 수 있다. 다만 누수형은 아니다.
- **확인 방법**: `len(bot._ohlcv_cache)`와 `len(kis_api._OHLCV_CACHE)`를 기록한다. 며칠 안에 평탄해지면 정상이다.
- **최소 수정안**: session_open에서 당일 후보·보유에 없는 키를 정리하거나, LRU 상한(예: 300)을 둔다.

### 5. 소형 무한 증가 컨테이너 묶음 (각 하루 1MB 미만)

수치상 원인은 아니다. 수정 1~3을 할 때 같은 패치에서 정리할 후보로 둔다.

| 컨테이너 | 위치 | 쌓이는 단위 |
|---|---|---|
| `_profit_evidence_shadow_seen` | `trading_bot.py:10961-10971` | 키에 분 단위 `decision_ts`가 들어 있음. 하루 수 건 |
| `_order_sent_emitted` | `trading_bot.py:25872` | 주문번호당 1개 |
| `risk.all_trade_log` | `risk_manager.py:344`(append `:557`, `:1861`, `:1928`) | 매매 1건당 0.5~3KB, 리셋 없음 |
| `KISWebSocket._seen_fills` | `kis_api.py:6110` | 체결통보당 1개. WS 재시작 시 폐기 |
| PathB `_waiting_scan_price_missing_warned` | `runtime/pathb_runtime.py:3410-3416` | ticker×session_date |
| `bot._rehearsal_notified` | `runtime/us_swing_order_bridge.py:457-466` | ticker×session_date |

같은 묶음에 넣을 것으로, **호출마다 새 Anthropic 클라이언트를 만들고 `close()`를 하지 않는 경로**가 있다.
- 해당 위치: `minority_report/quick_exit_check.py:45-52`, `execution/single_symbol_judge.py:707`, `execution/buy_time_confirm_judge.py:159`, `runtime/kr_event_lane.py:306`, `preopen/continuation_shadow.py:1847`
- 순환참조라 cyclic GC가 돌 때까지 httpx 풀과 SSLContext가 남는다. 상주 증가보다는 churn과 파편화 쪽 문제이고, 하루 20MB 미만으로 추정한다.
- 수정안: 모듈 싱글턴 클라이언트를 재사용한다. `analysts.py:40` 방식과 같게 하면 된다.

## 원인이 아닌 것 (점검 완료)

- **KIS WebSocket 재연결**(`kis_api.py:6095-6453`, `trading_bot.py:1314-1379`)
  - 재연결할 때 `self.ws`를 새 `WebSocketApp`으로 교체하고, 옛 객체는 참조가 끊긴다.
  - 콜백 클로저는 `start()` 한 번에 공유된다. 재연결마다 스레드를 추가로 만들지 않는다(ping thread 없음).
  - 봇 레벨에서는 `_stop_ws_for_market`이 먼저 실행된 뒤 교체된다.
  - 유일한 위험은 드문 경합이다. 연결이 맺어지기 전에 `stop()`이 들어오면 `_supervise` 스레드가 고아로 남는다. 이건 중복 틱이라는 정합성 문제이고 메모리 문제는 아니다. 현재 스레드 수가 8로 고정되어 있어 발생하지 않은 상태다.
  - 틱 경로(`_on_tick`, `trading_bot.py:32771`)의 컨테이너는 ticker 키이거나 240개 trim이 걸려 있다(`trading_bot.py:15426`, `19568`).
- **Telegram**: 송신 카운터는 파일에 저장하고, commander는 long-poll 단일 스레드다. 누적이 없다.
- **WS 틱 원장**: `ws_tick_ledger._BUF`는 200행 또는 2초마다 flush한다.
- **세션·결정 리스트**
  - `decision_event_log`는 200개로 trim한다(`trading_bot.py:41583`).
  - `_session_events`와 `_entry_blocked`는 session_open에서 리셋한다(`trading_bot.py:31684-31687`).
  - `_ticker_exclude_log`는 cutoff로 정리한다(`trading_bot.py:41023`).
- **기타 점검 결과**
  - `lru_cache`는 `selection_shadow_adapter.py:33`(maxsize=2) 하나뿐이다.
  - sqlite 연결은 전부 닫힌다.
  - executor는 shutdown된다.
  - pandas concat 누적과 matplotlib 사용은 없다.

## 과거 재시작 이력

- memory/·docs/에서 메모리 때문에 재시작한 기록은 찾지 못했다.
  - 관련 기록은 07-13 `live_preflight` MemoryError(preflight가 `lifecycle_events` payload 94MB를 fetchall한 건, `bbe3857`) 하나뿐이고, 봇 본체와는 무관하다.
  - 07-16 감사 시점에 RSS는 약 256MB(재시작 직후)였다.
- 이 누수 경로(`time_normalized_rvol`, `intraday_minute_cache`의 rvol)는 07-16 `10295dc`, 07-17 `1f095ea`에 들어왔다.
- 이후 표면화되지 않은 이유는 실행 길이에 있다. 08-01 이후 `=== Trading Bot Start ===` 85회 기준 **중앙 실행 길이는 7.5시간**이고, 48시간을 넘긴 실행은 7회다.
  - 최장 실행 294.8h(09-15~09-27)는 추석 연휴(KR 휴장)가 끼어 신규 세션 키가 적었다.
  - 당시에는 CSV도 지금보다 작았다(행 수가 매일 늘어남).
  - 이번 실행(09-30~)은 연속 영업일이고 CSV가 커진 상태라 처음으로 눈에 띄었다고 본다.

## 확인 절차 제안 (구현하지 않음)

1. **즉시, 무계측**: `bot_mem_sample.csv`에서 KR 08:50~09:10, US 22:20~22:40 구간의 계단(1순위)과, 장중 선형 램프(2·3순위), 장 사이 평탄 여부를 본다.
2. **경량 계측**: 기존 1시간 주기 작업에 아래 값을 1줄로 로그한다.
   - `len()`: 두 rvol `_cache`, `kis_api._INTRADAY_CACHE`, `_intraday_minute_cache._bar_cache`, `_ohlcv_cache`, `kis_api._OHLCV_CACHE`
   - rvol 행 합계
   - 각 캐시의 session_date 종류 수
3. **정밀 확인(선택)**: 시작할 때 `tracemalloc.start(1)`을 켜 두고, 텔레그램 명령이나 파일 플래그가 있을 때 `snapshot.statistics("lineno")` 상위 20개를 덤프한다. `time_normalized_rvol.py:195-199`(row dict 생성)이 최상위로 나오면 확정이다.
   - tracemalloc은 CPU와 메모리 오버헤드가 있으므로 라이브에는 플래그로만 켠다.

## 수정 우선순위 (텍스트 제안)

- 1순위(rvol) 수정이 효과의 대부분이다. 공유 인스턴스 + 비당일 키 제거 + 20세션 제한을 적용한다.
- 2·3순위는 같은 경로이므로 한 패치로 묶을 수 있다. `_INTRADAY_CACHE`는 키를 분 단위로 바꾸고 만료 엔트리를 정리한다. `_bar_cache`는 만료와 세션 교체 시 정리한다.
- 수정 전 임시 운용: 메모리는 세션 키가 바뀔 때만 계단식으로 오른다. 재시작하면 즉시 원복되므로, 주말 등 유휴 시간의 안전 재시작으로 버틸 수 있다(재시작 여부는 운영자 결정).
