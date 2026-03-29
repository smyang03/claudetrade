# DATA.md — claudetrade 저장 데이터 전체 목록

> 이 문서는 봇이 생성·유지하는 모든 데이터 파일의 역할, 구조, 갱신 주기를 정리한 레퍼런스입니다.
> 마지막 업데이트: 2026-03-27

---

## 목차

1. [state/ — 런타임 상태](#1-state--런타임-상태)
2. [logs/raw_calls/ — Claude 원문 호출 로그](#2-logsraw_calls--claude-원문-호출-로그)
3. [logs/daily_judgment/ — 일별 판단 원본](#3-logsdaily_judgment--일별-판단-원본)
4. [logs/judgment/ — 판단 이벤트 스트림](#4-logsjudgment--판단-이벤트-스트림)
5. [logs/analysis/ — 분석 이벤트 스트림](#5-logsanalysis--분석-이벤트-스트림)
6. [logs/system/ — 시스템·거래 실행 로그](#6-logssystem--시스템거래-실행-로그)
7. [data/price/ — 일봉 OHLCV](#7-dataprice--일봉-ohlcv)
8. [data/cache/ — 기술지표 캐시](#8-datacache--기술지표-캐시)
9. [data/daily_digest/ — 시장 컨텍스트 스냅샷](#9-datadaily_digest--시장-컨텍스트-스냅샷)
10. [data/news/ — 뉴스·공시](#10-datanews--뉴스공시)
11. [데이터 흐름 요약](#11-데이터-흐름-요약)

---

## 1. state/ — 런타임 상태

세션 간 유지되는 **라이브 상태** 파일들. 봇 재시작 시 여기서 복구.

---

### `state/brain.json`

| 항목 | 내용 |
|------|------|
| **역할** | Claude 분석가 누적 학습 메모리. 매일 postmortem 결과가 쌓이며 다음 날 프롬프트에 주입됨 |
| **갱신** | 매 session_close (하루 2회: KR 16:00, US 05:00) |
| **형식** | JSON |

```
brain.json
├── meta
│   ├── version           — 저장 횟수 카운터
│   ├── last_updated      — 마지막 갱신 날짜
│   ├── trained_days_kr   — KR 누적 학습일
│   └── trained_days_us   — US 누적 학습일
│
├── markets.KR / markets.US
│   ├── trained_days              — 해당 마켓 학습 일수
│   ├── analyst_performance
│   │   ├── bull  {total, hit, miss, rate, recent_7d, recent_30d, trend}
│   │   ├── bear  {같은 구조}
│   │   └── neutral {같은 구조}
│   ├── mode_performance          — 모드별 승률 {NEUTRAL: {win, total, ...}, ...}
│   ├── strategy_performance      — 전략별 평균 PnL
│   ├── current_beliefs
│   │   ├── market_regime         — 현재 장세 설명 (예: "변동성장")
│   │   └── learned_lessons[]     — 누적 학습 교훈 목록
│   ├── recent_days[]             — 최근 60일 일별 기록 (아래 상세)
│   │   ├── date, mode, pnl_pct, market_change, win
│   │   ├── bull/bear/neutral_result (HIT/MISS/PARTIAL)
│   │   ├── bull/bear/neutral_stance  — 당일 각 분석가 판단
│   │   ├── bull/bear/neutral_reason  — 핵심 근거 한 줄
│   │   ├── key_lesson               — postmortem 도출 교훈
│   │   ├── issue_type               — 패턴 분류
│   │   ├── best_trade / worst_trade  — 당일 최고/최악 종목
│   │   ├── worst_trade_reason
│   │   └── trades                   — 체결 건수
│   ├── issue_patterns[]          — 반복 이슈 패턴 기록
│   ├── debate_history[]          — 2라운드 토론 이력
│   └── correction_guide          — 내일 Claude 보정 지침
│
└── cross_market                  — KR/US 상관관계 메모
```

---

### `state/open_positions.json`

| 항목 | 내용 |
|------|------|
| **역할** | 현재 보유 포지션 영속성 (봇 재시작 시 복구) |
| **갱신** | 매수/매도 체결마다 |
| **형식** | JSON 배열 |

```json
[
  {
    "ticker": "005930",
    "entry": 70000,
    "qty": 10,
    "current_price": 71500,
    "strategy": "momentum",
    "tp": 74200,
    "sl": 68950,
    "max_hold": 1,
    "held_days": 0,
    "entry_date": "2026-03-27",
    "trailing": false,
    "trail_sl": 0.0,
    "trail_pct": 0.03
  }
]
```

---

### `state/pending_orders.json`

| 항목 | 내용 |
|------|------|
| **역할** | KIS 주문 후 체결 대기 중인 주문 목록. `_reconcile_pending_orders`가 5분마다 체결 확인 |
| **갱신** | 주문 생성/체결마다 |
| **형식** | JSON 배열 |

```json
[
  {
    "ticker": "NVDA",
    "order_no": "0000040194",
    "qty": 10,
    "price": 175.2,
    "side": "buy",
    "strategy": "volatility_breakout",
    "market": "US",
    "placed_at": "2026-03-27T01:00:45"
  }
]
```

---

### `state/live_status_KR.json` / `live_status_US.json`

| 항목 | 내용 |
|------|------|
| **역할** | 대시보드가 읽는 실시간 세션 상태 스냅샷 |
| **갱신** | 매 run_cycle (5분마다) + 매수/매도 이벤트 |
| **형식** | JSON |

```json
{
  "market": "KR",
  "updated_at": "2026-03-27T09:30:00",
  "trading_date": "2026-03-27",
  "mode": "DEFENSIVE",
  "session_active": true,
  "daily_pnl": -1200,
  "daily_pnl_pct": -0.003,
  "cash": 47107618,
  "total_equity": 48300000,
  "positions": [...],
  "pending_orders": [...]
}
```

---

### `state/api_usage.json`

| 항목 | 내용 |
|------|------|
| **역할** | Anthropic API 누적 사용량 및 비용 추적 |
| **갱신** | 매 Claude API 호출마다 |
| **형식** | JSON |

```
api_usage.json
├── total
│   ├── input_tokens   — 누적 입력 토큰 (현재 744,280)
│   ├── output_tokens  — 누적 출력 토큰 (현재 123,047)
│   └── cost_usd       — 누적 비용 달러 (현재 $4.08)
├── daily
│   └── {YYYY-MM-DD}: {input_tokens, output_tokens, calls, cost_usd}
└── sessions[]         — 최근 100건 호출 기록
    └── {ts, date, label, input_tokens, output_tokens, cost_usd}
```

**label 종류**: `analyst_bull_r1`, `analyst_bear_r2`, `postmortem`, `select_tickers`, `tune_30min`, `hold_advisor`, etc.

---

### `state/brain.json` ← (위 참조)
### `state/kis_token.json`

| 항목 | 내용 |
|------|------|
| **역할** | KIS API OAuth 토큰 캐시 |
| **갱신** | 토큰 만료(24시간)마다 갱신 |

```json
{ "access_token": "...", "expires_at": "2026-03-28T08:50:00" }
```

---

### `state/us_screen_cache.json`

| 항목 | 내용 |
|------|------|
| **역할** | US 스크리너 결과 일간 캐시 (FMP API). 당일 중복 호출 방지 |
| **갱신** | 매 US session_open (하루 1회) |

```json
{
  "date": "2026-03-27",
  "candidates": [
    { "ticker": "NVDA", "name": "NVIDIA", "price": 175.2,
      "change_rate": -0.25, "volume": 45000000, "vol_ratio": 0.7 }
  ],
  "source": "fmp"
}
```

---

## 2. logs/raw_calls/ — Claude 원문 호출 로그

| 항목 | 내용 |
|------|------|
| **역할** | Claude API에 보낸 **프롬프트 전문** + **응답 원문** 보존. 파인튜닝·프롬프트 품질 분석용 |
| **갱신** | 매 Claude API 호출마다 (세션당 6~12개 파일) |
| **형식** | JSON (호출 1건 = 파일 1개) |
| **파일명** | `YYYYMMDD_{MARKET}_{LABEL}_{HHMMSS}.json` |

```
logs/raw_calls/
  20260328_KR_analyst_bull_r1_085012.json    ← Bull 분석가 1라운드
  20260328_KR_analyst_bull_r2_085025.json    ← Bull 분석가 2라운드 토론
  20260328_KR_analyst_bear_r1_085015.json
  20260328_KR_analyst_bear_r2_085028.json
  20260328_KR_analyst_neutral_r1_085018.json
  20260328_KR_analyst_neutral_r2_085031.json
  20260328_KR_select_tickers_085045.json     ← 종목 선택
  20260328_KR_postmortem_160530.json         ← 사후 분석
  20260328_US_tune_30min_001220.json         ← 장중 튜닝
  20260328_US_hold_advisor_bull_013415.json  ← TP 시 HOLD/SELL 판단
```

```json
{
  "timestamp": "2026-03-28T08:50:12",
  "date": "2026-03-28",
  "market": "KR",
  "label": "analyst_bull_r1",
  "model": "claude-sonnet-4-6",
  "prompt": "...Claude에게 보낸 프롬프트 전문...",
  "raw_response": "...Claude가 반환한 JSON 원문...",
  "parsed": { "stance": "MILD_BULL", "confidence": 0.65, "key_reason": "..." },
  "tokens": { "input": 1843, "output": 312 }
}
```

**label 종류**:

| label | 설명 | 발생 시점 |
|-------|------|-----------|
| `analyst_{type}_r1` | 분석가 1라운드 독립 판단 | session_open |
| `analyst_{type}_r2` | 분석가 2라운드 토론 후 최종 | session_open |
| `select_tickers` | 종목 선택 (3~5개) | session_open / 재선택 |
| `postmortem` | 장 마감 사후 분석 | session_close |
| `tune_{N}min` | 장중 30분 주기 튜닝 | run_tuning |
| `hold_advisor_{type}` | TP 도달 시 HOLD/SELL 판단 | TP 발동 시 |

---

## 3. logs/daily_judgment/ — 일별 판단 원본

| 항목 | 내용 |
|------|------|
| **역할** | 하루 세션의 **판단 + 결과 전체**를 하나의 파일로 보존. brain_backfill, 대시보드 이력 조회 기반 |
| **갱신** | session_open에 생성, session_close에 actual_result·trades 추가 |
| **형식** | JSON |
| **파일명** | `YYYYMMDD_{KR|US}.json` |

```
logs/daily_judgment/
  20260327_KR.json
  20260327_US.json
  ...
  20241001_KR.json   ← 2024-10부터 보존
```

```json
{
  "date": "2026-03-27",
  "market": "KR",
  "consensus": {
    "mode": "DEFENSIVE",
    "size": 9,
    "tp_mult": 0.8,
    "weighted_score": -1.2,
    "vote": ["bear", "bear", "bull"]
  },
  "judgments": {
    "bull": {
      "stance": "DEFENSIVE",
      "confidence": 0.72,
      "key_reason": "전 종목 MACD 데드크로스...",
      "full_reasoning": "...(2~3문장 상세 분석)...",
      "top_risks": ["위험1", "위험2"],
      "suggested_strategy": "관망",
      "changed": false,
      "change_reason": null
    },
    "bear": { ... },
    "neutral": { ... }
  },
  "digest_prompt": "...당일 시장 데이터 요약 (지수, RSI, MACD 등)...",
  "selected_tickers": ["105560", "055550", "068270"],
  "trades": [
    { "side": "sell", "ticker": "SRPT", "price": 32583.7, "qty": 74,
      "strategy": "broker_sync", "pnl": -162297, "pnl_pct": -6.31 }
  ],
  "actual_result": {
    "market_change": -0.40,
    "pnl_pct": -0.0025,
    "pnl_krw": -1182,
    "win": false,
    "trades": 0,
    "cumulative": 47107618
  },
  "round1_judgments": { ... },
  "debate_changes": [ ... ]
}
```

---

## 4. logs/judgment/ — 판단 이벤트 스트림

| 항목 | 내용 |
|------|------|
| **역할** | 판단 관련 이벤트를 시계열로 기록 (파인튜닝 raw 데이터의 구조화 버전) |
| **갱신** | 판단 이벤트 발생 시마다 (append) |
| **형식** | JSONL (`.jsonl`) + 텍스트 요약 (`.log`) |
| **파일명** | `judgment_YYYYMMDD.jsonl` |

**주요 이벤트 타입 (`extra.event`)**:

| event | 내용 |
|-------|------|
| `open` | 세션 시작 — consensus, judgments 전체 |
| `close` | 세션 종료 — actual_result, postmortem, trades |
| `judgments_final` | 2라운드 완료 — bull/bear/neutral 최종 stance + full_reasoning |
| `analyst_response_r1` | 분석가 1라운드 응답 |
| `postmortem` | 사후 분석 전체 — bull/bear/neutral 적중 여부, key_lesson, brain_updates, correction_guide |
| `reinvoke` | 긴급 재판단 — trigger, old_mode → new_mode |
| `select` | 종목 선택 결과 |
| `rescreen` | 종목 재선택 (장중) |

---

## 5. logs/analysis/ — 분석 이벤트 스트림

| 항목 | 내용 |
|------|------|
| **역할** | 매매 신호·진입 스킵 이유 등 분석 레이어 이벤트 |
| **갱신** | run_cycle마다 (5분 주기) |
| **형식** | JSONL + .log |
| **파일명** | `analysis_YYYYMMDD.jsonl` |

**주요 이벤트 타입**:

| event | 내용 |
|-------|------|
| `session_start` | 세션 시작 마커 |
| `analyst_response_r1` | 분석가 응답 (stance, confidence, key_reason, top_risks) |
| `entry_skip` | 진입 스킵 이유 — reason: `max_positions`, `precheck_failed`, `no_signal`, `HALT`, `us_order_blocked` 등 |
| `entry_signal` | 매수 신호 발생 — strategy, price, qty, mode |
| `entry_failed` | 매수 실패 — reason |

---

## 6. logs/system/ — 시스템·거래 실행 로그

| 항목 | 내용 |
|------|------|
| **갱신** | 실시간 |
| **형식** | JSONL + .log |

### `trading_YYYYMMDD.jsonl`

거래 실행 전체 기록. 주요 메시지:

| 패턴 | 내용 |
|------|------|
| `[PAPER BUY] NVDA 10@175.2` | 모의 매수 체결 |
| `[LIVE SELL] 005930 5@70000` | 실거래 매도 |
| `[stop_loss] SLND -415,882 (-100.00%)` | SL 발동 손절 |
| `[trailing] KOD trail_sl=52,387` | 트레일링 스탑 활성화 |
| `[브로커 런타임 동기화] SLND 수량 보정` | 브로커 잔고 동기화 |
| `[US 사이클 01:05] 모드:CAUTIOUS_BEAR` | 주기 실행 로그 |
| `daily loss limit reached (-32.49%) -> halt` | HALT 발동 |

### `minority_YYYYMMDD.jsonl`

분석가·postmortem 판단 요약 로그.

### `error_YYYYMMDD.jsonl`

에러·경고 전체 기록.

---

## 7. data/price/ — 일봉 OHLCV

| 항목 | 내용 |
|------|------|
| **역할** | 전략 신호 계산(지표 산출)에 사용되는 과거 가격 데이터 |
| **갱신** | 매일 KR 08:30 / US 22:00 자동 업데이트 |
| **형식** | CSV |

```
data/price/
  kr/
    kr_005930.csv   ← 삼성전자
    kr_068270.csv   ← 셀트리온
    kr_035420.csv   ← NAVER
    ...
  us/
    us_NVDA.csv
    us_TSLA.csv
    us_AAPL.csv
    ...
```

```csv
date,open,high,low,close,volume,change
2024-11-15,53500,54200,53100,53500,46700000,0.93
```

---

## 8. data/cache/ — 기술지표 캐시

| 항목 | 내용 |
|------|------|
| **역할** | `calc_all()` 결과 캐시 — MA5/20/60, MACD, RSI, Bollinger, ATR, 거래량비율 등 |
| **갱신** | session_open 직후 (KR 08:50 / US 22:20) |
| **형식** | Pickle (`.pkl`) |

```
data/cache/
  KR_005930_indicators.pkl   (~62 KB)
  KR_068270_indicators.pkl
  US_NVDA_indicators.pkl
  US_TSLA_indicators.pkl
  ...
```

---

## 9. data/daily_digest/ — 시장 컨텍스트 스냅샷

| 항목 | 내용 |
|------|------|
| **역할** | Claude 분석가에게 전달하는 `digest_prompt` 원본 데이터. 지수·환율·개별종목 지표를 하루치로 압축 |
| **갱신** | session_open 직전 생성 |
| **형식** | JSON |
| **파일명** | `YYYY-MM-DD_KR.json` / `_US.json` |

```json
{
  "date": "2026-03-27",
  "market": "KR",
  "context": {
    "kospi_change": -0.40,
    "usd_krw": 1509.16,
    "vkospi": 22.3
  },
  "technicals": {
    "005930": { "close": 55900, "rsi": 41.2, "macd_cross": "death",
                "vol_ratio": 0.8, "bb_pct": 35, "week52_pct": 72 },
    ...
  },
  "prev_result": { "mode": "NEUTRAL", "pnl_pct": 0.44, "win": true }
}
```

---

## 10. data/news/ — 뉴스·공시

| 항목 | 내용 |
|------|------|
| **역할** | 시장 뉴스·종목 공시·SEC 공시 기록. digest_prompt 구성 재료 |
| **갱신** | 매일 크롤 (DART / SEC API) |
| **형식** | JSON |

```
data/news/
  kr/  ← DART 공시
    2026-03-27.json
    ...
  us/  ← SEC Edgar / FMP 뉴스
    2026-03-27.json
    ...
```

```json
{
  "date": "2026-03-27",
  "market_news": ["Fed 의장 발언...", "관세 우려..."],
  "corp_news": {
    "NVDA": { "name": "NVIDIA", "items": ["실적 발표..."], "count": 1 }
  }
}
```

---

## 11. 데이터 흐름 요약

```
매일 아침 (KR 08:50 / US 22:20)
─────────────────────────────────────────────────────────────
  data/price/*.csv 업데이트
  data/cache/*_indicators.pkl 갱신
  data/daily_digest/YYYYMMDD.json 생성
           ↓
  Claude 분석가 3명 × 2라운드 호출
  ├── logs/raw_calls/YYYYMMDD_{KR|US}_analyst_*_r1.json  ← NEW
  ├── logs/raw_calls/YYYYMMDD_{KR|US}_analyst_*_r2.json  ← NEW
  ├── logs/raw_calls/YYYYMMDD_{KR|US}_select_tickers.json ← NEW
  ├── logs/judgment/judgment_YYYYMMDD.jsonl (이벤트 append)
  └── logs/daily_judgment/YYYYMMDD_KR.json (생성)

장중 (5분 주기)
─────────────────────────────────────────────────────────────
  run_cycle 실행
  ├── state/live_status_{KR|US}.json 갱신
  ├── logs/analysis/analysis_YYYYMMDD.jsonl (신호/스킵 기록)
  └── logs/system/trading_YYYYMMDD.jsonl (매수/매도 기록)
  state/open_positions.json 갱신
  state/pending_orders.json 갱신

장중 튜닝 (30분 주기, run_tuning)
  └── logs/raw_calls/YYYYMMDD_{KR|US}_tune_{N}min.json   ← NEW

TP 도달 시 (hold_advisor)
  └── logs/raw_calls/YYYYMMDD_{KR|US}_hold_advisor_*.json ← NEW

장 마감 (KR 16:00 / US 05:00)
─────────────────────────────────────────────────────────────
  Claude postmortem 호출
  ├── logs/raw_calls/YYYYMMDD_{KR|US}_postmortem.json    ← NEW
  ├── logs/daily_judgment/YYYYMMDD_KR.json (actual_result, trades 추가)
  ├── logs/judgment/judgment_YYYYMMDD.jsonl (close 이벤트 append)
  └── state/brain.json 갱신 (upsert by date)
  state/api_usage.json 갱신
```

---

## 부록: 파일 크기 현황 (2026-03-27 기준)

| 위치 | 크기 |
|------|------|
| `state/` | ~186 KB |
| `logs/judgment/` | ~817 KB |
| `logs/analysis/` | ~924 KB |
| `logs/system/` | ~5.2 MB |
| `logs/daily_judgment/` | ~1.7 MB |
| `logs/phase1/` | ~31 MB (과거 데이터) |
| `data/price/` | ~628 KB |
| `data/cache/` | ~388 KB |
| `data/daily_digest/` | ~504 KB |
| `data/news/` | ~4.2 MB |
| **합계** | **~46 MB** |

---

## 부록: API 비용 현황 (2026-03-27 기준)

| 항목 | 수치 |
|------|------|
| 누적 입력 토큰 | 744,280 |
| 누적 출력 토큰 | 123,047 |
| 누적 비용 | $4.08 |
| 일평균 비용 | $0.14 ~ $0.75 |
| 하루 호출 횟수 | 20 ~ 72회 |

*가격 기준: claude-sonnet-4-6 입력 $3/1M, 출력 $15/1M*
