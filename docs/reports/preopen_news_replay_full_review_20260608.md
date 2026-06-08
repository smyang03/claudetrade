# Preopen News Replay Full Review

작성일: 2026-06-08  
범위: 장전 후보 뉴스 보강 원인 분석, replay 입력 개선, US 뉴스 포함 성능 측정

## 1. 작업 목적

장전 후보는 실제 매수 주문을 바로 넣기 위한 것이 아니라, 장 시작 시점에 Claude가 후보 중 어떤 종목을 `PROMOTE`할 만한지 판단하고 그 판단이 이후 수익률에 어떤 영향을 주는지 확인하기 위한 shadow/replay 실험이다.

초기 실험에서 KR은 일정 수준의 개선 가능성이 보였지만 US는 약했다. 사용자 가설은 "US도 뉴스 정보가 들어가면 KR처럼 개선될 수 있다"였고, 먼저 실제 코드에서 Naver/Google/뉴스 수집 경로가 있음에도 replay 입력에 뉴스가 왜 없어 보이는지 확인했다.

## 2. 코드 분석 결과

### 후보 수집 경로

- `tools/preopen_collector.py`
  - 후보를 수집한 뒤 `enrich_candidates_with_news()`를 호출한다.
  - 이후 `save_preopen_state()`와 `save_candidate_records()`로 state와 후보 JSONL을 저장한다.

### 뉴스 수집 경로

- `tools/collect_preopen_candidate_news.py`
  - 장전 후보 universe를 `load_preopen_news_targets()`로 읽는다.
  - KR은 `phase1_trainer.kr_news_collector.collect_day()`를 호출한다.
  - US는 `phase1_trainer.us_news_collector.collect_day()`를 호출한다.
  - `build_kr_digest()` / `build_us_digest()`를 갱신한다.
  - `save_preopen_news_snapshot()`으로 `data/news/<market>/<date>_preopen.json`을 저장한다.
  - `enrich_preopen_state()`로 state 안의 후보를 뉴스 보강한다.

### 뉴스 보강 경로

- `preopen/news_enrichment.py`
  - `load_preopen_news_payload()`는 `<date>_preopen.json`을 우선 읽고, 없으면 일반 `<date>.json`을 읽는다.
  - `build_news_index_with_summary()`가 ticker별 뉴스 index를 만든다.
  - `enrich_candidates_with_news()`가 후보에 아래 필드를 붙인다.
    - `news_or_earnings_flag`
    - `news_or_earnings_count`
    - `news_or_earnings_sources`
    - `news_or_earnings_sample_title`
    - `news_quality`
    - `news_date_quality`
    - `news_prompt_eligible`
    - `news_signal_type`
    - `news_score`
    - `news_prompt_summary`
    - `risk_news_summary`

### 스케줄러 경로

- `preopen/scheduler.py`
  - US 후보 수집 interval 기본값은 30분이다.
  - US 정규장 시작은 DST 기준 보통 22:30 KST다.
  - 뉴스 job은 `PREOPEN_NEWS_LEAD_MIN=20` 기본값으로 정규장 20분 전, 즉 22:10 KST 부근에 실행된다.
  - 후보 collector는 22:00 스냅샷 이후 다음 bucket이 22:30인데, collector window는 open-5분까지라 22:30 스냅샷이 없다.

## 3. 원인

뉴스가 없는 것이 아니라, US에서는 뉴스가 후보 JSONL보다 늦게 붙었다.

최근 US 파일 기준:

| date | latest candidate snapshot | preopen news written | candidate log news flagged | state/news available flagged |
|---|---|---|---:|---:|
| 2026-06-01 | 22:00:12 | 22:19:47 | 2 | 52 |
| 2026-06-02 | 22:00:10 | 22:18:58 | 3 | 57 |
| 2026-06-03 | 22:00:58 | 22:18:58 | 5 | 54 |
| 2026-06-04 | 22:00:13 | 22:20:30 | 5 | 51 |
| 2026-06-05 | 22:00:21 | 22:20:25 | 3 | 52 |

즉 replay가 `logs/preopen/*_US_candidates.jsonl`의 마지막 후보 스냅샷만 보면 뉴스가 거의 없는 것처럼 보인다. 하지만 `state/preopen_US_<date>.json`과 `data/news/us/<date>_preopen.json`에는 이미 상당량의 뉴스가 있었다.

## 4. Naver, Google, Yahoo 확인

### Naver

KR 경로에는 Naver Search API가 직접 붙어 있다.

- `phase1_trainer.kr_news_collector.fetch_naver_api_news()`
- KIS 뉴스가 부족하면 Naver API를 보조로 호출한다.
- legacy Naver finance scraper는 opt-in이다.

### Google

US/KR preopen news enrichment는 GoogleNews/Naver 형식의 외부 뉴스 DB bridge를 받을 수 있다.

- `preopen/investment_news_bridge.py`
- 기본 DB 경로: sibling repo의 `news/data/investment_news/investment_news.db`
- `GoogleNews`, `Naver` source row가 있으면 preopen payload로 변환된다.

### Yahoo

현재 repo에서 Yahoo/yfinance는 가격, 지수, 스크리너, exchange resolver에는 쓰이지만 preopen 뉴스 수집기에는 직접 연결되어 있지 않다.

작은 샘플에서 `yfinance.Ticker(ticker).news`는 `NVDA`, `TSLA`, `AAPL`, `SMCI`, `NFLX` 각각 10건을 반환했다. 다만 Yahoo Finance는 공식적으로 안정적인 뉴스 API quota를 보장하는 형태가 아니므로, live 기본 소스로 바로 넣기보다 opt-in cache source로 붙이는 편이 안전하다.

권장 조건:

- 티커당 최대 10건
- 날짜별 local cache
- 0.5~1초 throttle
- 실패 시 후보 생성/주문 경로 차단 금지
- preopen replay 실험용으로 먼저 검증

## 5. 구현한 개선

### 5.1 뉴스 수집 이후 후보 로그 append

수정 파일:

- `tools/collect_preopen_candidate_news.py`
- `tests/test_preopen_candidate_news_wrapper.py`

개선 내용:

- `collect_preopen_candidate_news()`가 `enrich_preopen_state()` 후 state의 보강된 후보를 다시 읽는다.
- `state_enrichment.applied_at`이 정규장 시작 전이면 후보 JSONL에 뉴스 포함 스냅샷을 append한다.
- `applied_at`이 없거나 정규장 이후면 append하지 않는다.

보호 이유:

- 장후 outcome이나 장중 가격 필드가 candidate log에 섞이면 replay가 미래 정보를 볼 수 있다.
- 그래서 정규장 전 timestamp가 확인되는 경우만 append한다.

### 5.2 replay 입력에서 preopen news overlay

수정 파일:

- `tools/preopen_candidate_replay.py`
- `tests/test_preopen_candidate_replay.py`

개선 내용:

- 과거처럼 후보 JSONL 마지막 스냅샷에 뉴스가 없더라도, `data/news/<market>/<date>_preopen.json`이 정규장 전 작성된 파일이면 replay 후보에 overlay한다.
- `allow_rank_reorder=False`로 overlay한다. 즉 replay 후보 순위 자체는 원 후보 스냅샷 기준을 유지한다.
- replay prompt 허용 필드에 뉴스 판단용 필드를 추가했다.

추가 허용 필드:

- `news_prompt_eligible`
- `news_signal_type`
- `news_score`
- `news_prompt_summary`
- `risk_news_summary`
- `scored_news_count`
- `excluded_news_counts`

## 6. 검증

실행한 검증:

```powershell
python -m py_compile tools/preopen_candidate_replay.py tools/collect_preopen_candidate_news.py
python -m pytest tests/test_preopen_candidate_replay.py tests/test_preopen_candidate_news_wrapper.py -q
python -m pytest tests/test_preopen_news_enrichment.py tests/test_selection_news_runtime.py tests/test_preopen_scheduler.py -q
python -m pytest tests/test_preopen_candidate_replay.py tests/test_preopen_candidate_news_wrapper.py tests/test_preopen_news_enrichment.py tests/test_selection_news_runtime.py tests/test_preopen_scheduler.py -q
git diff --check -- tools/preopen_candidate_replay.py tools/collect_preopen_candidate_news.py tests/test_preopen_candidate_replay.py tests/test_preopen_candidate_news_wrapper.py
```

최종 결과:

- 관련 테스트: `39 passed`
- `py_compile`: 통과
- `git diff --check`: 통과

실제 US 2026-06-01~2026-06-05 replay 입력 확인:

| date | overlay 후 news flagged | prompt eligible |
|---|---:|---:|
| 2026-06-01 | 34 | 19 |
| 2026-06-02 | 37 | 20 |
| 2026-06-03 | 30 | 13 |
| 2026-06-04 | 29 | 19 |
| 2026-06-05 | 27 | 7 |

state의 raw matching은 51~57개까지 있었지만, prompt-visible usable news는 quality filter 때문에 27~37개 수준으로 줄었다.

## 7. 뉴스 포함 Claude Replay 성능 측정

측정 조건:

- Market: US
- Dates: 2026-06-01 ~ 2026-06-05
- Candidate count: 60 per day
- Input: latest preopen candidate snapshot plus preopen news overlay
- Outcome: 장마감/최종 close proxy 기준
- 수수료/슬리피지/체결 가능성 제외

전체 후보 baseline:

- 전체 후보 300개 일별 close 평균: `-0.9007%`
- 전체 후보 close 승률 평균: `44.332%`

### prompt별 결과

| prompt_version | promotes | avg/day | 일별 평균, 무매매=0 | 추천 종목 가중 평균 | 추천 종목 승률 |
|---|---:|---:|---:|---:|---:|
| `strict_loss_filter_v1` | 12 | 2.4 | `+1.5976%` | `+2.2438%` | `58.33%` |
| `us_liquid_quality_v4` | 17 | 3.4 | `+0.7074%` | `+0.8138%` | `41.18%` |
| `us_slate_adaptive_v6` | 17 | 3.4 | `+0.5329%` | `+1.0202%` | `47.06%` |
| `us_edge_hunter_v5` | 25 | 5.0 | `-0.1124%` | `-0.1124%` | `44.00%` |
| `market_balanced_v2` | 25 | 5.0 | `-1.1594%` | `-1.1594%` | `44.00%` |
| `market_growth_tape_v3` | 20 | 4.0 | `-1.2390%` | `-1.5487%` | `40.00%` |

뉴스 포함 후 US 최선안은 `strict_loss_filter_v1`이다.

### `strict_loss_filter_v1` 날짜별 결과

| date | promotes | promote close | all-candidate close |
|---|---:|---:|---:|
| 2026-06-01 | 3 | `+7.1400%` | `+4.3009%` |
| 2026-06-02 | 5 | `+1.2700%` | `-0.1599%` |
| 2026-06-03 | 2 | `-1.6498%` | `-2.5647%` |
| 2026-06-04 | 2 | `+1.2279%` | `+1.8062%` |
| 2026-06-05 | 0 | `0.0000%` | `-7.8862%` |

핵심은 2026-06-05다. 그날 전체 후보 평균이 `-7.8862%`였는데 `strict_loss_filter_v1`은 추천을 0개로 비워 손실을 회피했다.

### `strict_loss_filter_v1` 추천 종목 상세

| date | ticker | close return | edge_code | news_signal |
|---|---|---:|---|---|
| 2026-06-01 | HPE | `+9.9895%` | EARNINGS_GAP_HIGH_LIQUIDITY | direct_catalyst |
| 2026-06-01 | IBM | `+6.2895%` | SPECIFIC_CATALYST_QUANTUM_BET | direct_catalyst |
| 2026-06-01 | FPS | `+5.1409%` | STRONG_PREOPEN_TAPE_HIGH_VOLUME | weak_generic |
| 2026-06-02 | HPE | `+18.5000%` | SPECIFIC_CATALYST_EARNINGS_BEAT_AI_DEMAND_UPGRADE | direct_catalyst |
| 2026-06-02 | PATH | `-8.2824%` | SPECIFIC_CATALYST_FIRST_GAAP_PROFIT_GUIDANCE_RAISE | direct_catalyst |
| 2026-06-02 | TMHC | `-0.0699%` | SPECIFIC_CATALYST_6B_ACQUISITION_ANNOUNCED | direct_catalyst |
| 2026-06-02 | POET | `+0.1440%` | SPECIFIC_CATALYST_LUMILENS_ORDER_CAPACITY_EXPANSION | direct_catalyst |
| 2026-06-02 | CRWV | `-3.9417%` | EXCEPTIONAL_PREOPEN_TAPE_HIGH_DOLLAR_VOLUME_AI_INFRA | direct_catalyst |
| 2026-06-03 | HPE | `-1.7631%` | SPECIFIC_CATALYST_EARNINGS_BLOWOUT | direct_catalyst |
| 2026-06-03 | TE | `-1.5365%` | EXCEPTIONAL_PREOPEN_TAPE_HIGH_DOLLAR_VOLUME | weak_generic |
| 2026-06-04 | TPL | `+0.9318%` | SPECIFIC_CATALYST_EARNINGS_BEAT_INSIDER_BUY | direct_catalyst |
| 2026-06-04 | NVTS | `+1.5240%` | EXCEPTIONAL_PREOPEN_TAPE_HIGH_VOL_RATIO_LARGE_GAP | direct_catalyst |

추천 종목 12개 단순 평균:

- Close: `+2.2438%`
- Win rate: `58.33%`

시간대별 평균:

| horizon | avg return |
|---|---:|
| 5m | `+1.8482%` |
| 30m | `+0.0747%` |
| 60m | `+1.0848%` |
| 120m | `+1.1610%` |
| close | `+2.2438%` ticker weighted |

## 8. 해석

US는 KR처럼 "뉴스가 있으면 무조건 많이 추천"이 답이 아니었다.

이번 결과에서 좋은 방향은 다음이다.

1. 구체 catalyst가 있거나 정말 강한 tape일 때만 추천한다.
2. 추천 개수를 채우지 않는다.
3. slate가 위험하면 0개 추천을 허용한다.
4. `KEEP_WATCH`를 trade처럼 취급하지 않는다.

뉴스를 넣으면 broad prompt는 오히려 후보를 너무 많이 골라 손실을 키웠다. 특히 2026-06-05 같은 약한 날에는 `market_balanced_v2`, `market_growth_tape_v3`, `us_edge_hunter_v5`가 5개를 채우며 큰 손실을 냈다.

반면 `strict_loss_filter_v1`은 뉴스가 없던 기존 입력에서는 US 5일 모두 추천 0개였지만, 뉴스 overlay 이후에는 12개를 추천하면서 최악일은 비웠다. 따라서 뉴스 정보는 "더 많이 사기"보다 "살 만한 날과 피해야 할 날을 구분"하는 데 효과가 있었다.

## 9. 운영/코드 영향

건드린 영역:

- preopen news collection wrapper
- preopen replay input builder
- preopen replay/news wrapper tests
- docs/reports 결과 문서

건드리지 않은 영역:

- 주문 실행
- PathB 진입/청산
- broker truth
- risk sizing
- `.env`, `.env.live`, `.env.paper`
- `config/v2_start_config.json`
- `state/brain.json`

주문/리스크/브로커 truth 영향:

- 없음. 이번 변경은 preopen shadow/replay와 후보 로그 보강 범위다.
- live order route, PathB route, broker truth fail-closed, sizing policy는 변경하지 않았다.

Claude 호출량 영향:

- replay 실험에서는 prompt당 US 5일 run에 약 127k~129k tokens가 들었다.
- 6개 prompt 전체 비교에 총 `770,278` tokens를 사용했다.
- 운영 runtime 호출량은 이번 코드 변경만으로 증가하지 않는다. 다만 뉴스가 prompt-visible이 되면 후보 1회당 input tokens는 증가한다.

## 10. 남은 리스크와 후속 개선

### 비차단 잔여 리스크

- 표본은 US 5거래일뿐이다. 최종 운영 반영 전 더 긴 기간 검증이 필요하다.
- outcome은 replay 기준이며 수수료, 슬리피지, 체결 가능성을 반영하지 않았다.
- Claude replay는 temperature 0이어도 모델/응답 drift 가능성이 있다.
- 일부 추천에는 `weak_generic` 뉴스도 포함됐다. 이 부분은 prompt를 더 엄격하게 만들 여지가 있다.
- HPE처럼 같은 종목이 여러 날 반복 추천될 수 있는데, 실제 운영에서는 재진입 쿨다운/보유 상태와 충돌 가능성을 따로 봐야 한다.

### 후속 개선 후보

1. `strict_loss_filter_v1`을 US news-aware prompt로 분리한다.
   - 예: `us_news_strict_catalyst_v1`
   - 단순 strict 재사용보다 US 뉴스 품질 필드를 명시적으로 사용한다.

2. weak/generic 뉴스 제한을 강화한다.
   - `news_prompt_eligible=false` 또는 `news_signal_type=weak_generic`이면 원칙적으로 PROMOTE 금지
   - 단, exceptional tape는 별도 제한 조건 필요

3. no-trade day를 정식 decision으로 다룬다.
   - PROMOTE 0개가 실패가 아니라 방어 신호라는 점을 metric에 반영한다.

4. 더 긴 기간으로 재측정한다.
   - 최소 KR/US 각각 10~20거래일
   - train/holdout 분리
   - 종목 가중, 일별 equal-weight, cash-empty, 수수료/슬리피지 포함 metric 병행

5. Yahoo/yfinance news는 opt-in cache source로만 추가 검토한다.
   - 공식 quota 보장이 약하므로 live 기본 경로보다 실험용 보조 소스가 적합하다.

## 11. 현재 결론

US도 뉴스가 들어가면 개선 여지가 있다. 다만 방향은 KR처럼 "성장/테마/tape를 넓게 잡기"가 아니라, 뉴스 기반으로 "추천하지 말아야 할 날과 약한 후보를 버리는" 쪽이다.

현재 기준 최선안:

- US: `strict_loss_filter_v1` 계열
- 핵심 정책: concrete catalyst 또는 exceptional tape만 PROMOTE, 확신 없으면 KEEP_WATCH/DROP, 약한 slate는 0개 추천 허용
- 추천 종목 평균 close return: `+2.2438%`
- 무매매일 cash 처리 일별 평균: `+1.5976%`

## 12. 2026-06-08 운영 반영 결과

적용 방향은 `strict_loss_filter_v1` 계열을 주문 직행이 아니라 후보 프롬프트 보존 정책으로 연결하는 것이다. 뉴스가 구체 촉매로 판정된 후보만 `preopen_news_edge=true`, `preopen_pin_tier=HARD`, `preopen_pin_source=news_strict_catalyst`로 표시한다. 이 표시는 Claude 후보 평가 기회를 보장하기 위한 것이며, `preopen_pin_require_confirmation=true`를 유지해 trade_ready 자동 승격이나 주문 제출을 의미하지 않는다.

변경한 코드 경로:

- `preopen/news_enrichment.py`: direct catalyst, earnings/guidance, material disclosure 뉴스만 `strict_loss_filter_v1` 정책으로 hard pin 표시한다. risk 뉴스가 있거나 broad/generic 뉴스면 승격하지 않는다. 이후 뉴스 매칭이 사라지면 stale news pin과 관련 quality tag를 제거한다.
- `runtime/candidate_prompt_pool.py`: trainer prompt pool에서 hard preopen pin을 hard cap 안에서 먼저 보존한다. 단, same-day stopped 후보는 기존 보호대로 regular 후보 뒤로 보낸다.
- `trading_bot.py`: selection meta, scorer snapshot, order fill metadata에 news edge/pin 필드를 보존해 감사 추적이 가능하게 했다.
- `tools/preopen_candidate_replay.py`: replay sanitize 단계에서 news edge/pin 필드를 보존한다.

운영성 테스트 결과:

- `python -m py_compile preopen/news_enrichment.py runtime/candidate_prompt_pool.py tools/preopen_candidate_replay.py trading_bot.py`
- `python -m pytest tests/test_preopen_news_enrichment.py tests/test_selection_news_runtime.py tests/test_candidate_quality_trainer.py tests/test_preopen_candidate_replay.py -q` → `53 passed`
- `python -m pytest tests/test_preopen_candidate_news_wrapper.py tests/test_preopen_pin_universe.py tests/test_dashboard_candidate_audit_api.py tests/test_investment_news_bridge.py -q` → `29 passed`
- 기존 데이터 read-only 확인:
  - US 2026-06-05: 후보 60개 중 news edge 4개(`AVGO`, `GOOG`, `POET`, `FIVE`)가 prompt pool 35개 안에 모두 보존됨.
  - KR 2026-06-08: 후보 60개 중 news edge 2개(`005930`, `290690`)가 prompt pool 32개 안에 모두 보존됨.
- `python tools/live_preflight.py --mode live --skip-dashboard --json` → `ok=true`, `fail_count=0`, `warn_count=16`.

운영 영향:

- 주문 제출, PathB 진입/청산, broker truth, risk sizing, `.env*`, `config/v2_start_config.json`, `state/brain.json`은 변경하지 않았다.
- 이 변경은 다음 라이브 프로세스 재시작 또는 해당 모듈 재로드 이후 적용된다. 현재 실행 중인 live 프로세스에는 코드 변경이 즉시 주입되지 않는다.
- 남은 경고는 preflight 기준 broker truth stale 등 현재 운영 상태 경고이며, 이번 뉴스 후보 승격 코드의 차단 실패는 아니다.

### 재검토 보강

재검토 중 `enrich_candidates_with_news()`가 state 후보에는 news hard pin을 붙이지만, `preopen.storage.load_preopen_pin_candidates()`가 기존 rank/score 기반 pin만 인정해 state에서 universe/pin 후보로 다시 끌어오는 경로에서는 news edge가 무시될 수 있음을 확인했다.

보강 내용:

- `preopen/storage.py` safe field에 news edge/pin 필드를 추가했다.
- `strict_loss_filter_v1` news edge 후보는 rank/score cutoff를 우회해 hard pin으로 로드할 수 있게 했다.
- 단, `news_prompt_eligible=true`, 허용 signal type, risk news 없음, seed-only 차단, turnover gate는 유지한다.
- stale news pin clear 시 quality tag가 전부 stale tag뿐이어도 빈 리스트로 정리되도록 수정했다.

추가 검증:

- `python -m pytest tests/test_preopen_news_enrichment.py tests/test_preopen_pin_universe.py -q` → `21 passed`
- `python -m pytest tests/test_preopen_news_enrichment.py tests/test_selection_news_runtime.py tests/test_candidate_quality_trainer.py tests/test_preopen_candidate_replay.py tests/test_preopen_pin_universe.py -q` → `65 passed`
- `python -m pytest tests/test_preopen_candidate_news_wrapper.py tests/test_dashboard_candidate_audit_api.py tests/test_investment_news_bridge.py -q` → `19 passed`
- live preflight 재실행 → `ok=true`, `fail_count=0`.
