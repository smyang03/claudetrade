# ClaudeTrade 매매 프로세스

## 전체 흐름 개요

```text
[세션 시작]
session_open()
  -> 시장 환경 초기화 및 시장 다이제스트 생성
  -> AI 3자 토론으로 컨센서스 도출 (모드 결정)
  -> 스크리닝 후 종목 후보 선정
  -> Claude 최종 선택
  -> 실시간 WebSocket 구독 시작

[장중 주기 실행]
run_cycle()  # 5분마다 실행
  -> 브로커 잔고/포지션 동기화
  -> 종목별 신호 탐색
  -> adaptive_params -> signal() -> 매수 실행
  -> 포지션 모니터링

[실시간 가격 이벤트]
_on_tick()
  -> 가격 캐시 업데이트
  -> TP / SL / 트레일링 / max_hold 점검
  -> 필요 시 매도 실행
```

---

## 1단계: 세션 오픈 (`session_open`)

### 1-1. 환경 초기화
- 장 운영 가능 여부 확인
- KIS 토큰 갱신
- USD/KRW 환율 갱신
- 보유 종목 현재가 동기화
- 리스크 엔진 일일 상태 초기화

### 1-2. 기존 판단 재사용 여부 확인

해당 날짜 `logs/daily_judgment/YYYYMMDD_{MARKET}.json` 이 있으면:
- 기존 판단(`judgments`, `consensus`) 재사용
- 종목만 새로 스크리닝

### 1-3. 시장 다이제스트 생성

```text
build_kr_digest() / build_us_digest()
  -> 지수, VIX, USD/KRW, 섹터 ETF, 크레딧 스프레드 요약
  -> digest_prompt 생성
```

### 1-4. AI 3자 토론 (Brain Debate)

```text
get_three_judgments(digest_prompt, brain_summary, correction)
  -> Bull 분석가: stance, confidence, 전략 제안
  -> Bear 분석가: stance, confidence, 전략 제안
  -> Neutral 분석가: stance, confidence, 전략 제안
```

```text
build_consensus(judgments)
  -> mode 결정:
     AGGRESSIVE / MODERATE_BULL / MILD_BULL /
     NEUTRAL / MILD_BEAR / CAUTIOUS_BEAR /
     DEFENSIVE / HALT
  -> size 결정: 포지션 사이즈 비율
```

US 시장은 VIX 수준에 따라 size를 추가로 축소할 수 있습니다.

### 1-5. 종목 선택

```text
screen_market_kr() / screen_market_us()
  -> 유동성 / 모멘텀 중심 1차 스크리닝

_filter_candidates_by_history()
  -> 최근 실패/쿨다운 종목 제외

select_tickers(market, digest_prompt, mode, candidates)
  -> Claude가 후보 중 최종 N개 선택
```

### 1-6. WebSocket 구독 시작

선택된 종목의 실시간 체결가 수신을 시작하고 `_on_tick()` 트리거를 연결합니다.

---

## 2단계: 매수 사이클 (`run_cycle`, 5분마다)

### 2-1. 전처리
- HALT 상태 확인
- 브로커 잔고/포지션 동기화
- 분석가 평균 confidence가 너무 낮으면 신규 진입 차단

### 2-2. Cross-Asset 컨텍스트 준비

```python
_ca_context = today_judgment["digest_raw"]["context"]
# VIX, USD/KRW, 섹터 ETF, 크레딧 정보 포함
```

### 2-3. 종목별 신호 탐색 루프

각 감시 종목마다:

#### 가격 / 지표 계산

```text
get_price()    -> 현재가
get_ohlcv()    -> OHLCV 캔들
calc_all()     -> RSI, BB%, MA20/60, MACD, ATR, vol_ratio, gap_pct
```

#### 매수 차단 조건 확인
- 이미 보유 중인 종목
- 쿨다운 중인 종목
- 장 시작 직후 블랙아웃 구간
- 일일 손실 한도 초과

#### 적응형 파라미터 계산 (`_ap`)

```text
adaptive_params(strategy, market, mode, conf, context=_ca_context)
  -> base_params: strategy/*.py의 기본 테이블 값
  -> regime_overlay: VIX, USD/KRW, 모드 기반 조정
  -> perf_overlay: decisions.db 최근 성과 반영
  -> guardrail: 파라미터 이동 범위 제한
```

```text
apply_cross_asset_adjust()
  -> 섹터 ETF, 크레딧, 환율 기반 추가 보정
```

#### 전략별 신호 탐색

KR:

```text
gap_pullback
momentum
mean_reversion
volatility_breakout
```

US:

```text
분석가 투표 전략 우선
(momentum / mean_reversion / gap_pullback / volatility_breakout)
```

현재 정책상 비활성화/차단 예:
- `CAUTIOUS_BEAR / DEFENSIVE / HALT` 에서 `momentum` 차단
- `KR + MODERATE_BULL` 에서 `mean_reversion` 차단
- `KR` 의 `volatility_breakout` 비활성화 검토/적용 가능

#### 무신호 처리
- 연속 무신호가 길어지면 유니버스 내 종목 교체
- `NO_SIGNAL` 상세 사유 로그 기록
- decisions.db 에 `NO_SIGNAL` 행 저장

### 2-4. 매수 실행

신호 발생 시:

```text
RiskManager 검증
  -> 최대 포지션 수
  -> 최대 주문 금액
  -> 동일 종목 중복
  -> 일손실 한도

place_order(side="BUY")
  -> 브로커 주문 전송

_add_pending_order()
  -> 체결 대기 주문 등록

decisions.db
  -> BUY_SIGNAL 기록
```

TP / SL 설정:

```text
TP = entry_price * (1 + tp_pct)
SL = entry_price * (1 - sl_pct)
```

---

## 3단계: 포지션 모니터링 (`_on_tick`)

실시간 체결가 수신 시:

```text
price_cache 업데이트
risk.update_prices()
_process_exit_candidates()
  -> TP 도달
  -> SL 도달
  -> max_hold 초과
  -> trailing stop 조건 확인
```

---

## 4단계: 매도 실행

### 4-1. TP 도달

```text
enable_trailing_stop = True:
  -> hold_advisor로 HOLD / SELL 판단
  -> SELL이면 즉시 청산
  -> HOLD이면 trailing stop 활성화

enable_trailing_stop = False:
  -> 즉시 TP 청산
  -> TP 쿨다운 등록
```

### 4-2. SL / 트레일링 / max_hold

```text
_execute_sell()
  -> place_order(side="SELL")
  -> 텔레그램 알림
  -> 포지션 제거
  -> 쿨다운 등록
  -> decisions.db 결과 업데이트
```

---

## 5단계: 데이터 기록

### 5-1. decisions.db

한 행 = 한 사이클, 한 종목, 한 의사결정

기록 대상:
- `BUY_SIGNAL`
- `NO_SIGNAL`
- `BLOCKED`
- `SKIPPED`

주요 컬럼:
- 시장, 종목, 세션 날짜
- mode, confidence, context
- RSI, BB%, MACD, ATR, vol_ratio 등 feature
- 전략별 near-miss 정보
- 체결 결과
- forward_1d / 3d / 5d

### 5-2. forward_updater

price CSV 업데이트 후:
- `forward_1d`
- `forward_3d`
- `forward_5d`
를 후행으로 채웁니다.

### 5-3. backfill

과거 price CSV 기반으로:
- historical decision row 생성
- `data_source='backfill'`
- `is_simulated=1`
로 구분 저장

---

## 6단계: 향후 적응형 구조

```text
base_params(strategy, market, mode)
  -> regime_overlay(mode, vix, usd_krw, ...)
  -> performance_overlay(decisions.db)
  -> guardrail
  -> 최종 adaptive_params
```

향후 ML 적용 전까지는:
- 규칙 기반 adaptive overlay
- live/backfill 혼합 성과 반영
- 일 1회 업데이트
구조로 운영합니다.

이후 ML 적용 시:

```text
base_params
  -> adaptive overlay
  -> ML overlay
  -> final params
```

---

## 요약

현재 ClaudeTrade는 다음 구조로 동작합니다.

1. 세션 시작 시 AI 3자 토론으로 시장 모드 결정
2. 종목 후보 선택 후 5분마다 전략 신호 탐색
3. adaptive_params로 시장 상황에 맞게 파라미터 보정
4. 주문 / 체결 / 청산 / 쿨다운 관리
5. 모든 의사결정을 decisions.db 에 기록
6. forward return / backfill 로 ML 전단계 학습 자산 축적

