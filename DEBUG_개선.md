# DEBUG 개선 이력

실제 로그와 코드 분석으로 발견한 버그 수정 이력.
다음에 같은 문제를 중복 수정하지 않도록 근거 로그와 수정 위치를 함께 기록한다.

---

## [2026-03-28] 손절 후 즉시 재매수 / 중복 매수

### 근거 로그
`logs/system/trading_20260327.log`
```
23:09:45 [stop_loss] VSA  -4.61%
23:09:49 [PAPER BUY] VSA 108@2.595   ← 4초 후 재매수
23:14:53 [stop_loss] VSA
23:14:57 [PAPER BUY] VSA 110@2.545   ← 또 4초 후
```
`logs/system/trading_20260326.log` — 매수 107회 vs 매도 11회
```
SRPT 14회, CORT 14회, PAYS 13회 매수 (같은 종목 매 사이클 반복)
OLPX 결국 1,820주 보유 후 청산
```

### 원인
- `close_position()` 후 `risk.positions`에서 제거됨 → `_has_open_position()` False → 즉시 재진입 가능
- KIS VTS 체결 반영 지연으로 pending reconcile 전에 same-ticker 재주문

### 수정 위치
`trading_bot.py`
- `_entry_blocked: dict[str, float]` 추가 (`__init__`)
- `_block_entry(ticker, minutes, reason)` / `_is_entry_blocked(ticker)` 메서드 추가
- `_process_exit_candidates()`: stop_loss/trail_stop 청산 후 **20분** 차단
- `run_cycle()`: `_has_open_position()` 체크 직전에 `_is_entry_blocked()` 체크 추가
- 매수 주문 성공 후 `_block_entry(ticker, 15, "buy_placed")` 추가
- `session_open()`: 새 세션 시작 시 `_entry_blocked = {}` 초기화
- 환경변수: `STOP_COOLDOWN_MIN` (기본 20), `BUY_COOLDOWN_MIN` (기본 15)

---

## [2026-03-28] trade_log 중복 기록

### 근거 로그
`logs/daily_judgment/20260326_KR.json`
```json
trades: [
  { "ticker": "SRPT", "pnl_pct": 3.06 },
  { "ticker": "BRZE", "pnl_pct": 1.91 },
  ...
  { "ticker": "SRPT", "pnl_pct": 3.06 },  ← 완전 동일 중복
  { "ticker": "BRZE", "pnl_pct": 1.91 }   ← 완전 동일 중복
]
```

### 원인
재시작 시 이전 session의 trade_log가 그대로 이어져 동일 체결이 2회 기록

### 수정 위치
`trading_bot.py` — `session_close()` 내
```python
# order_no 기준 dedup 후 session_trades 사용
```

---

## [2026-03-28] order_no lstrip("0") 매칭 버그

### 근거 코드
`kis_api.py:986, 1092`
```python
target = str(order_no).strip().lstrip("0")
# "0000035528".lstrip("0") → "35528"  (의도대로)
# "0".lstrip("0")          → ""       (빈 문자열 — 모든 행과 매칭 위험)
# "0000000".lstrip("0")    → ""       (동일)
```
`lstrip("0")`은 앞에 붙은 0을 전부 제거하는데, order_no가 `"0"` 계열이면
target이 `""` (빈 문자열)이 되어 조건 `row_no.lstrip("0") == target` 이
모든 "0"계 order_no 행과 매칭되는 오버매칭 발생.

### 수정 위치
`kis_api.py` — `get_order_fill_kr()`, `get_order_fill_us()`
```python
# lstrip("0") 비교 → int() 변환 후 정수 비교로 교체
try:
    target_int = int(order_no_s) if order_no_s else None
except ValueError:
    target_int = None
# row_no도 int() 변환 후 비교
```

---

## [2026-03-28] PnL% ZeroDivision

### 근거 코드
`risk_manager.py:243`
```python
pnl_pct = pnl / (pos["entry"] * pos["qty"]) * 100
```
`pos["entry"] == 0` 이거나 `pos["qty"] == 0` 인 상태로 close_position()이
호출되면 ZeroDivisionError → 청산 루틴 중단 → 포지션 미제거.

entry=0은 체결 확인 실패 시 avg_price 폴백이 0으로 저장되는 경우
(pending order → get_order_fill 실패 → avg_price=0)에 발생 가능.

### 수정 위치
`risk_manager.py:240-243`
```python
cost_basis = pos["entry"] * pos["qty"]
pnl_pct    = (pnl / cost_basis * 100) if cost_basis else 0.0
```

---

## [2026-03-28] can_open() 수수료 미포함

### 근거 코드
`risk_manager.py:96`
```python
if self.cash < price:          # can_open — price만 체크
    return False, "insufficient cash"
```
`risk_manager.py:138`
```python
if total_cost > self.cash:     # open_position — cost + fee 체크
    return False
```
can_open()=True 반환 후 open_position()에서 fee 포함 total_cost가
cash를 초과하면 False 반환. 주문 시도 후 실패하는 케이스 발생.

### 수정 위치
`risk_manager.py:97`
```python
if self.cash < price + self._fee("buy", price):
    return False, "insufficient cash"
```
1주 분 수수료를 포함한 최소 필요 현금 기준으로 사전 차단.

---

## [2026-03-28] Claude JSON 응답 nan/inf 파싱 실패

### 근거
Claude가 간헐적으로 JSON 표준 외 리터럴 포함 응답 생성:
- `"confidence": nan` — JSON 비표준, `json.loads()` 실패
- `"score": Infinity` — 동일
- `"value": -inf` — 동일

파싱 실패 → `_extract_json()` ValueError → fallback 판단 사용
→ 분석가 판단 누락, brain 학습 데이터 품질 저하.

`logs/raw_calls/` 파일에서 raw_response에 nan 포함된 케이스 확인 가능.

### 수정 위치
`minority_report/analysts.py` — `_extract_json()` 내 `_fix()` 함수
```python
s = re.sub(r'\bNaN\b',       '"NaN"', s)
s = re.sub(r'\bInfinity\b',  '999',   s)
s = re.sub(r'\b-Infinity\b', '-999',  s)
s = re.sub(r'\bnan\b',       '0',     s)
s = re.sub(r'\binf\b',       '999',   s)
s = re.sub(r'\b-inf\b',      '-999',  s)
```

---

---

## [2026-03-28] pending 체결가 0 → entry=0 포지션 생성

### 근거 코드
`trading_bot.py:918` — `fill.get("fill_price", 0) or 0` → fill_price=0 그대로 저장
`trading_bot.py:844` — `filled_price_native or avg_price or 0` → 둘 다 0이면 entry=0
`trading_bot.py:936` — 동일 패턴

entry=0인 포지션이 생성되면:
- `risk_manager.py:243` 에서 `pnl / (entry * qty)` ZeroDivision (PnL Fix와 연결)
- `sl = entry * (1 - sl_pct) = 0` → 즉시 손절 트리거 가능
- TP/SL 계산 전체 무력화

### 수정 위치
`trading_bot.py`
- `:918` fill_price > 0 일 때만 `filled_price_native` 세팅 (0이면 폴백 체인에 맡김)
- `:844` (`_make_position_from_broker`) `filled_price_native or avg_price or raw_price or 0`
- `:936` (체결 이벤트 기록) 동일 폴백 체인 추가
- `raw_price`: 주문 접수 시 `_add_pending_order()`에 저장된 원래 주문가

---

## [2026-03-28] 낮은 분석가 confidence 시 신규 진입 스킵

### 근거
분석가 3명의 평균 confidence가 0.4 미만일 때도 신규 진입을 시도해
불확실한 시장에서 포지션을 잡는 사례 확인.

### 수정 위치
`trading_bot.py` — `run_cycle()` 내 ticker 루프

```python
# 루프 진입 전 (한 번만 계산)
_avg_conf = sum(conf for analyst in judgments) / 3.0
_low_conf = _avg_conf < _MIN_ENTRY_CONF   # 기본 0.4

# ticker 루프 내 진입 차단 직후
if _low_conf:
    log.debug(f"  [{ticker}] confidence 부족 ({_avg_conf:.2f}) → 신규 진입 스킵")
    continue
```

환경변수 `MIN_ENTRY_CONF` (기본 0.4)로 임계값 조정 가능.

---

## [2026-03-28] 분석가 합의 강도에 따른 포지션 사이즈 보정

### 근거
3:0 만장일치일 때와 1:1:1 완전분열일 때 동일한 size를 사용해
확신도가 낮은 날에도 과도한 사이즈로 진입하는 문제.

### 수정 위치
`minority_report/consensus.py` — `build_consensus()` 내 analyst_sizes 블렌딩 직후

```python
_vote_cats = [_cat(bull["stance"]), _cat(bear["stance"]), _cat(neut["stance"])]
_n_unique = len(set(_vote_cats))
if _n_unique == 1:      # 3:0 만장일치 → x1.3
    size = max(0, min(100, int(size * 1.3)))
elif _n_unique == 3:    # 1:1:1 완전분열 → x0.75
    size = max(0, min(100, int(size * 0.75)))
else:                   # 2:1 분열 → x0.85
    size = max(0, min(100, int(size * 0.85)))
```

---

## [2026-03-28] TP 청산 후 즉시 재진입 방지

### 근거
TP 청산 후 바로 다음 사이클에서 동일 종목을 재매수 — 수익 확정 후
조정 구간에 재진입해 수익 일부 반납.

### 수정 위치
`trading_bot.py` — `_process_exit_candidates()` 내 take_profit 청산 분기

```python
self._execute_sell(cand, market, reason="take_profit")
self._block_entry(cand["ticker"], _TP_COOLDOWN_MIN, "take_profit")
```

환경변수 `TP_COOLDOWN_MIN` (기본 10분).

---

## [2026-03-28] 분석가 연속 실패 미감지 → 잘못된 피드백 전달

### 근거
brain.json US Bull 분석가: 최근 7일 0/7 전패.
`generate_analyst_summary`는 누적 rate와 trend만 비교해 `"stable"` 반환 →
Claude Bull 분석가에게 "현재 기준 유지" 라고 피드백 → 매일 같은 실수 반복.

`trend` 판단 로직: `recent_7d.rate > recent_30d.rate + 0.05` 기준인데
0/7 vs 0/11 둘 다 0%이므로 "stable" 출력. 연속 실패가 감지 불가 구조.

### 수정 위치
`claude_memory/brain.py`

- `_count_consecutive_result(recent_days, analyst_type, target)` 헬퍼 추가
- `generate_analyst_summary()` 내:
  - 연속 5일+ 실패: ⛔ 경고 + NEUTRAL 이하 강제 권고
  - 연속 3~4일 실패: ⚠️ 주의 + 1~2단계 보수적 조정
  - 연속 3일+ 성공: ✅ 신뢰 유지
  - `trend == "stable"` 이어도 연속 실패 있으면 override
  - 전체 적중률 25% 미만 구간 추가 (기존엔 40% 이하만)
- `generate_prompt_summary()` 분석가 신뢰도 라인에 `⛔N연속실패` 배지 추가

---

## [2026-03-28] VIX 무시 → 변동성 장세에서 사이즈 과다

### 근거
VIX=31 (2026-03-28) 상황에서도 consensus size가 그대로 사용됨.
Bear 분석가가 VIX를 stance에 반영하지만 size 계산에는 직접 영향 없음.
digest context에 `"vix": 31.14` 이미 존재 — 활용하지 않는 상태.

### 수정 위치
`trading_bot.py`

- 상수 `_VIX_SIZE_TIERS` 추가 (환경변수 `VIX_MULT_20/25/30` 조정 가능):
  - VIX 20+: ×0.85 / 25+: ×0.70 / 30+: ×0.55
- `session_open()`: `consensus = build_consensus()` 직후 US 세션에만 적용
- `_reinvoke_analysts()` 재판단 경로에도 동일 적용
- `today_judgment["digest_raw"]` 추가 — 재판단 경로에서 VIX 원본 접근용

---

## [2026-03-28] US 전략 단일화 — 분석가 suggested_strategy 미사용

### 근거
- US 세션은 `volatility_breakout` **단 1개 전략만** 사용
- 분석가 3명이 매일 `suggested_strategy` 필드를 출력하지만 코드에서 한 번도 읽지 않음
- KR은 gap_pullback → momentum → mean_reversion 3개 순차 시도
- US 야간장은 모멘텀(추세 추종)이 더 잘 맞는 경우가 있으나 해당 전략 자체가 배제됨

### 수정 위치
`trading_bot.py`

- `_STRATEGY_NAME_MAP` 상수 추가 (한글→코드명 매핑)
- `_analyst_strategy_vote(judgments)` 함수 추가
  - 3명 분석가 `suggested_strategy` 다수결 → 코드 전략명
  - 2표 이상 없으면 `"volatility_breakout"` 기본값
- `run_cycle()` confidence 계산 직후:
  - `_voted_strat` 계산
  - `_us_strat_list = [voted, "volatility_breakout"]` (중복 제거)
  - 매 사이클마다 1번 계산, 전체 티커 루프에서 공유
- US 전략 블록 교체:
  - 기존: `vb_sig` 단일 호출
  - 변경: `_us_strat_list` 순서로 각 전략 시도, 첫 신호 발생 전략 채택

```python
# 예: 분석가 2명이 "모멘텀" → momentum 먼저 시도, 실패 시 volatility_breakout
_us_strat_list = ["momentum", "volatility_breakout"]
```

---

## 미수정 (확인됨, 향후 대응)

| 항목 | 파일 | 우선순위 | 상태 | 비고 |
|------|------|----------|------|------|
| pending 체결가 0 → entry=0 저장 | trading_bot.py:918, 936 | HIGH | ✅ 수정완료 | raw_price 폴백 추가. 2026-03-28 |
| can_open max_pyramid 시장 구분 없음 | risk_manager.py:91 | MEDIUM | ✅ 수정완료 | pyramid 카운트에 market 필터 추가. 2026-03-28 |
| consensus 30일 가중치 임계값 낮음 | consensus.py:88 | MEDIUM | ✅ 수정완료 | 5/10/20건 단계적 가중치(30/45/60%)로 교체. 2026-03-28 |
| get_usd_krw 폴백 없음 | kis_api.py | LOW | 미수정 | Alpha Vantage 실패 시 US 세션 시작 불가 |
| trigger_words 대소문자 | consensus.py | LOW | 오진 (코드 정상) | bear_reason에 .lower() 이미 적용됨. trigger_words도 소문자. 버그 아님 |
| _in_entry_blackout US 자정 경계 | trading_bot.py:1097 | — | 오진 (코드 정상) | now < close_t 분기로 자정 경계 이미 처리됨. 버그 아님 |
