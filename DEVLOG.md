# DEVLOG — claudetrade 개발 맥락 핸드오프 문서

> 이 파일은 다른 Claude 인스턴스(또는 미래의 세션)가 개발 맥락을 그대로 이어받을 수 있도록 작성된 **개발 컨텍스트 전달 문서**입니다.
> README.md는 사용자용 아키텍처 문서이고, 이 파일은 **개발자/AI용 변경 이력 + 의사결정 로그**입니다.

---

## 아키텍처 핵심 전환 (2026-03 기준)

### 이전 방식 (Phase 1 사전학습)
```
가격 데이터 수집 → historical_sim.py 시뮬레이션 → brain.json 사전학습
→ 그 데이터 기반으로 실거래 시작
```

### 현재 방식 (Run-First, 운영 우선)
```
봇 바로 실행 (paper 모드) → 매 세션마다 Claude 판단 + 실제 결과 기록
→ brain.json / JSONL 로그에 누적 → fine-tuning / 프롬프트 개선 재료
```

**왜 바꿨나**: historical_sim의 시뮬레이션 데이터 품질 문제 (pnl 100% 0, mode 100% NEUTRAL)로 사전학습 신뢰도가 없음. 실제 봇이 판단한 데이터만이 의미 있는 학습 재료.

---

## 변경 이력

### [2026-03] trading_bot.py — 5개 기능 추가/수정

#### 1. USD/KRW 자동 갱신 (`session_open`)
- **변경 전**: `.env`의 `USD_KRW_RATE=1350` 고정값 사용
- **변경 후**: `session_open()`에서 `kis_api.get_usd_krw()` 호출 → 실시간 환율 자동 주입
- **왜**: 실제 환율이 1,504원인데 1,350원으로 계산하면 원화 환산 PnL 오차 발생
- **코드 위치**: `trading_bot.py` → `session_open()` → USD/KRW auto-refresh 블록

```python
# session_open() 내부
try:
    from kis_api import get_usd_krw
    live_rate = get_usd_krw()
    if live_rate > 100:
        self.usd_krw = live_rate
        log.info(f"USD/KRW 자동 갱신: {live_rate:,.2f}")
except Exception as e:
    log.warning(f"USD/KRW 갱신 실패, 기존값 유지: {e}")
```

#### 2. 2라운드 토론 메타데이터 보존 (`session_open`)
- **변경 전**: `debate_meta` 팝 후 버림 → training record에서 R1 판단 / 토론 변화 손실
- **변경 후**: `self.today_judgment`에 `round1_judgments`, `debate_changes` 저장
- **왜**: 학습 데이터 완성도 — "왜 판단이 바뀌었나"를 추적해야 fine-tuning에 가치 있음

```python
debate_meta = judgments.pop("_debate", {})
self.today_judgment = {
    ...
    "round1_judgments": debate_meta.get("r1", {}),
    "debate_changes":   debate_meta.get("changes", []),
}
```

#### 3. 긴급 재판단 시스템 (`_should_reinvoke_analysts`, `_reinvoke_analysts`)
- **변경 전**: 없음
- **변경 후**: 3가지 조건에서 애널리스트 3명 재소집
- **왜**: 장 중 급변 상황에서 아침 판단이 그대로 유지되는 문제

**트리거 조건**:
1. `index_change ≤ -2.0%` (지수 급락)
2. `action == "REVERSE"` (튜너 방향 전환 권고)
3. `action == "TIGHTEN" AND warning 존재` (경고 동반 긴축)

**쿨다운**: `_REINVOKE_COOLDOWN_CYCLES = 2` (약 60분, 튜닝 사이클 기준)

```python
_REINVOKE_INDEX_THRESHOLD = -2.0
_REINVOKE_COOLDOWN_CYCLES = 2

def _should_reinvoke_analysts(self, result, current_state):
    if self.tuning_count - self._last_reinvoke_tuning < self._REINVOKE_COOLDOWN_CYCLES:
        return False, ""
    if result.get("action") == "REVERSE":
        return True, f"REVERSE 권고: {result.get('reason','')[:60]}"
    if current_state.get("index_change", 0) <= self._REINVOKE_INDEX_THRESHOLD:
        return True, f"지수 급락 {current_state['index_change']:+.2f}%"
    if result.get("warning") and result.get("action") == "TIGHTEN":
        return True, f"튜너 경고: {result['warning']}"
    return False, ""
```

#### 4. `_session_events` 추적
- **변경 전**: 장 중 이벤트(튜닝, 재판단) 기록 없음
- **변경 후**: `_session_events: list` — 모든 튜닝 결과 + 재판단 이벤트 기록
- **왜**: training record에 "왜 포지션이 변했나" 맥락 보존
- `session_open()`에서 `self._session_events = []` 리셋
- `run_tuning()`에서 각 결과마다 append
- `session_close()`의 training record에 포함

#### 5. `session_close()` training record 완성화
- **변경 전**: 아침 판단 파일 덮어쓰기 → `round1_judgments`, `debate_changes` 손실
- **변경 후**: `{**self.today_judgment}` 언팩으로 모든 필드 포함

**Training Record 최종 스키마 (13개 필드)**:
```json
{
  "date": "YYYY-MM-DD",
  "market": "KR|US",
  "digest_prompt": "아침 시장 데이터 요약 (INPUT)",
  "round1_judgments": { "bull": {...}, "bear": {...}, "neutral": {...} },
  "debate_changes": ["변화 설명 문자열"],
  "judgments": { "bull": {...}, "bear": {...}, "neutral": {...} },
  "consensus": { "mode": "...", "weighted_score": 0.0, "... " },
  "tickers": { "ticker": "이름" },
  "actual_result": { "pnl_pct": 0.0, "win": false, "... " },
  "postmortem": { "bull_result": "HIT|MISS|PARTIAL", "key_lesson": "..." },
  "trades": [ { "side": "buy|sell", "ticker": "...", "pnl": 0 } ],
  "session_events": [ { "type": "tuning|reinvoke", "... " } ],
  "mode": "paper|live"
}
```

---

### [2026-03] postmortem.py — 전면 개편

#### 변경 내용
- `run()` 파라미터에 `trade_log: list = None` 추가
- `_format_trade_log()` 헬퍼 추가 — 체결 내역을 Claude 프롬프트용 텍스트로 변환
- `_strategy_pnl()` 헬퍼 추가 — 전략별 PnL 집계 `{strategy: [pnl_pct, ...]}`
- HALT / 판단 없는 날 조기 리턴 안전장치 추가
- Claude 프롬프트에 `[오늘 체결 내역]` 섹션 추가 (최고/최악 거래 포함)
- Claude 응답에 `best_trade`, `worst_trade`, `worst_trade_reason` 필드 추가
- brain 업데이트 후 `BrainDB.update_strategy_performance()` 전략별 호출
- judgment_log에 `trade_log` + `strategy_pnl` 원본 보존 (fine-tuning raw data)
- `max_tokens` 600 → 700

**왜**: postmortem이 학습 루프의 핵심. 체결 내역 없이는 "왜 틀렸나"를 분석할 수 없음.

---

### [2026-03] telegram_reporter.py — `analyst_reinvoke_alert()` 추가

```python
def analyst_reinvoke_alert(trigger: str, old_mode: str, new_mode: str,
                            judgments: dict, consensus: dict):
    """🚨 긴급 재판단 텔레그램 알림"""
```

- R1 판단 섹션, 토론 변화, 최종 판단 섹션 포함
- 트리거 사유, old → new 모드 전환 표시

---

### [2026-03] kis_api.py — `get_usd_krw()` 추가

```python
def get_usd_krw() -> float:
    """
    실시간 USD/KRW 환율
    1차: yfinance USDKRW=X
    2차: AlphaVantage CURRENCY_EXCHANGE_RATE
    3차: .env USD_KRW_RATE 기본값
    """
```

**실증**: 테스트 결과 1,504.83 반환 확인 (기존 하드코딩 1,350 대비 +11%)

---

### [2026-03] indicators.py — pandas 2.1+ 호환

- **변경 전**: `d["close"].pct_change(fill_method=None) * 100`
- **변경 후**: `d["close"].pct_change() * 100`
- **왜**: pandas 2.1부터 `fill_method` 파라미터 deprecated → FutureWarning 발생

---

### [2026-03] phase1_trainer/historical_sim.py — 3개 버그 수정

**버그 1: dict iteration**
```python
# 전
for ticker in tickers:
# 후
for ticker in (tickers.keys() if isinstance(tickers, dict) else tickers):
```

**버그 2: date type mismatch**
```python
# CSV 날짜가 string이면 Timestamp 비교 실패
if df.index.dtype == 'object':
    mask = df.index == target_date  # string 비교
else:
    mask = df.index == pd.Timestamp(target_date)
```

**버그 3: change_pct 컬럼 없음**
```python
# CSV에는 change(절대값)만 있고 change_pct가 없음
if "change_pct" in row:
    change_pct = row["change_pct"]
elif "change" in row and row.get("close", 0) > 0:
    change_pct = row["change"] / row["close"] * 100
```

**근본 원인**: Price CSV가 2026-01-02부터 시작, sim은 2024-10-01부터 → 데이터 없음 → pnl=0, mode=NEUTRAL 100%

---

### [2026-03] phase1_trainer/supplement_collector.py — `fetch_usd_krw()` 수정

- **변경 전**: AlphaVantage FX_DAILY → 실패 시 0.0 반환
- **변경 후**: AlphaVantage 우선 → yfinance USDKRW=X 폴백
- **왜**: AlphaVantage 무료 플랜은 FX_DAILY rate limit이 잦음

---

### [2026-03] brain.json — 누락 모드 패치

**패치 대상**: `state/brain.json`, `claude_memory/brain.json`

**추가된 `mode_performance` 키**:
```json
"MILD_BULL": {"count": 0, "win_count": 0, "avg_pnl": 0.0},
"MILD_BEAR": {"count": 0, "win_count": 0, "avg_pnl": 0.0},
"CAUTIOUS_BEAR": {"count": 0, "win_count": 0, "avg_pnl": 0.0},
"NEUTRAL": {"count": 0, "win_count": 0, "avg_pnl": 0.0}
```

**추가된 필드**:
- `debate_history: []` (양 시장)
- 영문 전략명: `momentum`, `mean_reversion`, `gap_pullback` (KR), `volatility_breakout` (US)

**왜 누락됐나**: claude_memory.py의 `_init_market()` 초기값에 해당 모드들이 없었음.
**다음 Claude가 할 일**: `claude_memory.py`의 `_init_market()` 함수에 위 모드들을 기본값으로 추가하면 새 brain.json 생성 시 자동 포함됨.

---

## 현재 알려진 미해결 문제

### 1. historical_sim 데이터 무효
- **상태**: 수정 완료 (코드 버그), 데이터 재수집 미완료
- **필요 작업**:
  ```bash
  python phase1_trainer/price_collector.py --lookback 550
  python phase1_trainer/historical_sim.py --market KR --start 2025-01-01 --no-resume
  python phase1_trainer/historical_sim.py --market US --start 2025-01-01 --no-resume
  ```
- **중요도**: 낮음 (Run-First 방식으로 전환해서 historical sim은 선택 사항)

### 2. claude_memory.py `_init_market()` 에 누락 모드
- **상태**: 미수정 (brain.json 직접 패치로 임시 해결)
- **필요 작업**: `_init_market()` 반환값에 `MILD_BULL`, `MILD_BEAR`, `CAUTIOUS_BEAR`, `NEUTRAL` 추가
- **중요도**: 중간 — brain.json 삭제 후 재생성 시 또 누락됨

### 3. 가중 합의 cold-start
- **상태**: 설계 완료, 데이터 축적 대기
- **설명**: 10 영업일 이상 운영 전까지 analyst weight = 1:1:1 동등 가중
- **중요도**: 없음 (의도된 동작)

### 4. 미국장 프리마켓 데이터 없음
- **상태**: 구조적 맹점으로 인식, 수정 계획 없음
- **설명**: Alpha Vantage 무료 플랜에 pre-market 데이터 없음

---

## 시스템 주요 파일 맵

```
claudetrade/
├── trading_bot.py          # 메인 봇 — session_open/close, tuning loop
├── kis_api.py              # KIS API 래퍼 + get_usd_krw()
├── indicators.py           # 기술적 지표 계산
├── telegram_reporter.py    # 텔레그램 알림 (analyst_reinvoke_alert 포함)
├── claude_memory/
│   ├── brain.py            # BrainDB — brain.json 읽기/쓰기 인터페이스
│   └── brain.json          # 초기값 (실행 시 state/brain.json으로 복사)
├── minority_report/
│   ├── analysts.py         # 3명 애널리스트 (Bull/Bear/Neutral) 2라운드 토론
│   ├── tuner.py            # 장 중 튜닝 (MAINTAIN/TIGHTEN/REVERSE)
│   └── postmortem.py       # 장 마감 후 분석 + brain 업데이트 (학습 루프 핵심)
├── phase1_trainer/
│   ├── price_collector.py  # 가격 데이터 수집
│   ├── historical_sim.py   # 과거 데이터 기반 시뮬레이션 (선택 사항)
│   └── supplement_collector.py  # VIX, 환율, 수급 데이터
├── state/
│   └── brain.json          # 런타임 brain (항상 이걸 사용)
├── data/
│   ├── prices/             # OHLCV CSV
│   └── supplement/         # VIX, 환율, 수급
└── logs/
    ├── minority/           # 장 중 로그
    └── judgment/           # JSONL 학습 로그 (fine-tuning 원본)
```

---

## 학습 데이터 흐름 (Run-First)

```
매일 session_open()
    └─ analysts.py → R1 판단 → R2 토론 → judgments + debate_meta
    └─ self.today_judgment에 round1_judgments, debate_changes 저장

장 중 run_tuning() (30분마다)
    └─ tuner.py → MAINTAIN|TIGHTEN|REVERSE
    └─ _session_events에 append
    └─ _should_reinvoke_analysts() → 조건 충족 시 재판단

session_close()
    └─ postmortem.run(trade_log=self.risk.trade_log)
        └─ brain.json 업데이트 (analyst hit rate, mode performance, lessons)
        └─ JSONL 로그 저장 (프롬프트 + 응답 + 체결 원본)
    └─ training record 저장 (13개 필드 완전한 daily_judgment JSON)
```

---

## 다음 Claude 인스턴스가 알아야 할 것

1. **Run-First가 핵심**: historical_sim은 선택 사항. 봇을 돌리는 게 학습 데이터 만드는 방법.
2. **brain.json 경로**: `state/brain.json`이 항상 우선. `claude_memory/brain.json`은 초기값.
3. **_REINVOKE_COOLDOWN_CYCLES = 2**: 30분 튜닝 사이클 기준 60분 쿨다운.
4. **training record 위치**: `data/daily_judgments/YYYYMMDD_{market}.json`
5. **JSONL 학습 로그**: `logs/judgment/judgment_YYYYMMDD.jsonl` — fine-tuning raw data
6. **claude_memory.py `_init_market()`**: 신규 brain 생성 시 MILD_BULL 등 모드 누락 버그 미수정

---

## [2026-03-22] 수수료 반영 + 예산 계산 개선 + 텔레그램 강화

### 1. 수수료 시스템 (risk_manager.py)

- `FEE_RATES` 상수 추가: KR 매수 0.015%, KR 매도 0.195%(증권거래세 포함), US 0.015%
- `_fee(side, amount)` 메서드 추가
- `open_position`: 매수 수수료 즉시 `cash` 차감 + `daily_pnl` 반영
- `close_position`: 매도 수수료 차감 후 `pnl` 계산 (gross_pnl → net_pnl)
- `total_fee` 필드 추가 — 세션별 누적 수수료 추적
- `reset_daily_state`: `total_fee = 0.0` 초기화
- `get_status`: `total_fee` 포함

### 2. 예산 계산 단순화 (risk_manager.py)

**변경 전**:
```python
budget = cash * max_position_pct(20%) * mode_pct
budget = min(budget, max_order_krw)
budget = min(budget, cash * 0.5)
```

**변경 후**:
```python
budget = max_order_krw * mode_pct   # 단일 기준
budget = min(budget, cash)          # 현금 부족 시 잔액 전부
```

**왜**: `max_position_pct=20%`가 남은 현금 기준으로 계산되어 현금이 43만원 남으면 예산이 6만원으로 쪼그라드는 문제. `MAX_ORDER_KRW` 하나로 단순화하고 현금 부족 시 잔액 전부 활용.

### 3. 텔레그램 수수료 표시 (telegram_reporter.py)

- `trade_alert()`: `market` 파라미터 추가, 총금액·수수료 표시
- `dashboard_push()`: `max_order_krw`, `total_fee` 파라미터 추가 후 표시

### 4. 텔레그램 명령어 강화 (telegram_commander.py)

- `/setorder [금액]`: 장중 최대 주문금액 실시간 변경 (10,000원 ~ 1,000만원)
- `/status` (`/s`): 최대주문·수수료 합계 표시
- `/pnl` (`/p`): 수수료 합계·최대주문 표시
- HELP_TEXT에 `/setorder` 추가

### 5. 봇 시작 알림 개선 (trading_bot.py)

- 시작 시 초기자금·최대주문·KR할당 표시
- `session_open`에서 `self.risk.market = market` 설정

### 6. 버그 수정 (trading_bot.py)

- `action_changed` 미정의 변수 → `action != "MAINTAIN"` 으로 수정 (NameError 버그)

---

## [2026-03-22] 머지 충돌 해결 + 검증

### 배경
로컬 브랜치(Run-First 아키텍처 변경)와 원격 브랜치(PR #4~#7 버그 수정)가 diverge → 5개 파일 충돌 발생

### 충돌 해결 전략
- **HEAD(로컬) 우선**: 2라운드 토론, 가중 합의, 체결 내역 분석 등 핵심 기능
- **Remote 통합**: 동적 유니버스 빌더, 세션 자동 감지, 타입 안전 코드, NaN 처리

### 파일별 충돌 해결 내용

| 파일 | 로컬(HEAD) 보존 | Remote 통합 |
|------|-----------------|-------------|
| `analysts.py` | 2라운드 토론, 강화된 한국어 페르소나 | `_sanitize_analyst_result()`, 타입 안전 select_tickers |
| `consensus.py` | 가중 합의 엔진(`_get_weights`) | `TRIGGER_WORDS_KR/US` 시장별 분리 |
| `postmortem.py` | HALT 가드, `_format_trade_log`, `_strategy_pnl` | 개선된 ━━━ 포맷 프롬프트, `add_daily_record` 필드 추가 |
| `digest_builder.py` | 동적 롤링 윈도우(`min_periods=5`) | NaN 안전 처리(`denom52`) |
| `trading_bot.py` | 긴급 재판단, 환율 자동화, `round1_judgments` 보존 | 동적 유니버스 빌더, KR/US 세션 명시적 시간 체크 |

### 추가 삭제
- `__pycache__` 전체 (원격에서 이미 .gitignore 처리됨)
- 오래된 날짜 로그 파일들
- 이상한 `main` 파일 (git rename 오류로 생성된 빈 파일)

---

## [2026-03-22] Python 3.9 호환성 버그 수정

### 문제
시스템 Python이 3.9.12인데 코드에 3.10+ 전용 union type hint(`X | None`) 사용 → `TypeError` 발생

### 수정 파일
- `phase1_trainer/digest_builder.py`: `list[str] | None` → `Optional[List[str]]`, `from typing import Optional, List` 추가
- `phase1_trainer/historical_sim.py`: 동일 처리
- `trading_bot.py`: `date | None` → 타입 힌트 제거 (기본값 `None` 유지)
- `risk_manager.py`: `from __future__ import annotations`로 이미 호환 — 수정 불필요

---

## [2026-03-22] 전체 시스템 검증 결과

모든 핵심 기능을 API 호출 없이 구조적으로 검증 완료:

| 검증 항목 | 결과 |
|-----------|------|
| 핵심 모듈 임포트 9개 | 전부 OK |
| brain.json 모드 완전성 (KR/US) | 전부 OK (9개 모드 존재) |
| 가중 합의 3케이스 | 정상 (mixed→CAUTIOUS, bull→MODERATE_BULL, 마이너리티룰 발동) |
| postmortem 체결 내역 파싱 | 정상 |
| HALT 스킵 안전장치 | 정상 |
| USD/KRW 자동 갱신 | 1,504.83 정상 반환 |
| TradingBot 필수 메서드 6개 | 전부 존재 |
| `_session_events`, `_last_reinvoke_tuning`, `usd_krw` | `__init__`에 존재 확인 |
| 긴급 재판단 트리거 로직 5케이스 | 전부 정상 |
| Training Record 13개 필드 | 전부 존재 |
| BrainDB 메서드 14개 | 전부 존재 |
| 2라운드 토론 구조 (`_debate`, r1, r2) | 확인 |

**검증 환경**: Python 3.9.12, Windows 11

---

## 현재 시스템 상태 (2026-03-22 기준)

- **코드**: 머지 완료, 검증 통과, main 브랜치 최신
- **brain.json**: cold-start 상태 (실 운영 데이터 0일)
- **가중 합의**: 10 영업일 이상 운영 전까지 1:1:1 균등 가중치
- **다음 단계**: paper 모드 실행 → 매 세션 brain.json 축적 시작

*Last updated: 2026-03-22*
*Context session: 머지 충돌 해결 + Python 3.9 호환성 수정 + 전체 검증*

---

## [2026-03-22] 트레일링 스탑 + hold_advisor + 텔레그램 UI 개선 + 버그 8개 수정

### 배경

모의투자 시작 전 전체 기능 사전 점검 요청. 수수료 반영 실제 손익 표시, 날짜별 매매 원장, 분석가 성공률 표시, TP 이후 추가 수익 추구(트레일링 스탑) 기능 추가. 이후 종합 버그 시뮬레이션으로 8개 버그 발견 및 수정 완료.

---

### 1. hold_advisor 시스템 (minority_report/hold_advisor.py)

**신규 파일 추가**

- TP(목표가) 도달 시 분석가 3명(Bull/Bear/Neutral)에게 HOLD/SELL 의견 수집
- HOLD confidence 합산 > SELL confidence 합산이면 트레일링 스탑 전환, 아니면 즉시 청산
- 각 분석가의 `trail_pct` 제안값 평균 → 트레일링 폭 결정 (2~5% 범위 강제)
- `_log_decision()`: 결정 시점 JSONL 기록 (`logs/hold_advisor/decisions_YYYY-MM-DD.jsonl`)

```python
PERSONAS = {
    "bull":    "15년 성장주 모멘텀 트레이더 — 추세 살아있으면 보유 선호",
    "bear":    "헤지펀드 리스크 매니저 — 이익 실현 타이밍, 욕심 경계",
    "neutral": "퀀트 통계 분석가 — 데이터 기반 냉정 판단",
}
```

**환경변수**:
- `TRAILING_STOP_ENABLED=true`: TP 도달 시 트레일링 스탑 전환 활성화 (기본 false)
- `TRAILING_ANALYST_ENABLED=true`: hold_advisor 분석가 합의 사용 (기본 false → 즉시 트레일링)
- `TRAIL_PCT=0.03`: 트레일링 폭 기본값

---

### 2. brain.py — `update_hold_advisor_performance()` 신규

```python
def update_hold_advisor_performance(market, ticker, decision, success, extra_pnl_pct):
    # hold_advisor_performance 필드 생성/누적
    # decision: "HOLD"|"SELL"
    # success: HOLD → 추가 수익 달성 여부, SELL → TP 직후 하락 여부
    # recent: 최근 20건 이력 유지
```

brain.json에 `hold_advisor_performance` 키로 성과 누적:
- `total`, `hold_count`, `hold_success`, `sell_count`, `hold_avg_extra_pnl`, `recent`

---

### 3. risk_manager.py — 트레일링 스탑 필드 추가

`open_position()` 포지션 dict에 신규 필드 추가:
```python
"trailing":      False,   # 트레일링 모드 여부
"trail_sl":      0.0,     # 트레일링 SL 가격
"trail_pct":     0.03,    # 트레일링 폭
"tp_triggered":  False,   # TP 도달 여부 (중복 방지)
"hold_advice":   None,    # hold_advisor 결과 {action, trail_pct, votes}
"tp_price":      0.0,     # TP 도달 당시 가격
```

`activate_trailing(ticker, trail_pct, hold_advice=None)` 신규:
- 포지션을 트레일링 모드로 전환
- `trail_sl = current_price × (1 - trail_pct)` 초기 설정
- `tp_triggered = True` (중복 TP 방지)
- `hold_advice` 보존

`update_prices()`: 트레일링 모드에서 현재가 상승 시 `trail_sl` 자동 상향 (래칫 방식)

`get_exit_candidates()`: 트레일링 모드에서는 `trail_sl` 발동 여부만 체크

---

### 4. trading_bot.py — 트레일링 스탑 + hold_advisor 통합

**`_handle_tp_trailing()`** 신규:
- TP 도달 시 `TRAILING_STOP_ENABLED` 확인
- `TRAILING_ANALYST_ENABLED` 이면 `hold_advisor.ask()` 호출
- HOLD 결정 → `risk.activate_trailing(hold_advice=...)` 호출
- SELL 결정 → 즉시 `_execute_sell()`

**`_execute_sell(hold_advice=None)`** 개선:
- 청산 후 `_record_hold_advisor_outcome()` 호출

**`_record_hold_advisor_outcome()`** 신규:
- 청산된 포지션의 `hold_advice` 확인
- HOLD 결정이었으면 TP 가격 대비 청산 가격으로 추가 수익 계산
- JSONL outcome 필드 업데이트 + brain.json 성과 누적

**`_update_hold_advisor_jsonl_outcome()`** 신규:
- `logs/hold_advisor/decisions_YYYY-MM-DD.jsonl`에서 해당 ticker/ts 행을 찾아 `outcome` 필드 기록

---

### 5. telegram_reporter.py — 대시보드 + 알림 개선

**`dashboard_push()`**:
- "수수료 차감 후" 명시: `오늘 순손익(수수료 차감 후): {pnl_pct:+.2f}%`
- 보유 포지션 섹션 분리 (`📌 보유 포지션`)
- `/pnl  전체: /trades` 유도 문구 추가

**`pnl_alert()`**:
- "(수수료 차감 후)" 문구 추가

---

### 6. telegram_commander.py — `/pnl` 개선 + `/trades` 신규

**`_cmd_pnl()` 전면 개선**:
```
🟢 오늘 순손익: +1.23%  +6,150원
  · 실현손익(수수료 전): +7,125원
  · 수수료(누적): -975원

📋 청산 내역 (3건)
  POSCO홀딩스 | 매수 78,000 → 매도 83,200 | +5.26% | +2,080원

🧠 hold_advisor 성과
  HOLD 결정: 2건 (성공 1건 50.0%)
  평균 추가수익: +0.8%

📊 분석가 성과 (오늘)
  bull: HIT  bear: MISS  neutral: HIT
```

**`_cmd_trades()` 신규** (`/trades [인수]`):
- 기본 20건, 날짜 내림차순, 날짜별 그룹화
- 인수 판단: `isdigit() and 1 ≤ n ≤ 999` → 건수, 4자리 이상 숫자 → 종목코드
- KR: 원화(원), US: 달러(USD) 자동 단위 구분
- 매도 건에 순손익(pnl) 표시

---

### 7. 버그 8개 수정

| # | 심각도 | 파일 | 내용 | 원인 | 수정 |
|---|--------|------|------|------|------|
| BUG-01 | Critical | trading_bot.py | `reused=True`일 때 `debate_meta` NameError | 중복 블록 존재, `if not reused:` 밖에서 사용 | 중복 블록(L773~821) 제거, `if not reused:` 내부에서만 정의/사용 |
| BUG-02 | Critical | trading_bot.py | `select_tickers()` 이중 실행 | `if not reused:` 블록 안팎에 두 번 호출 | 중복 블록 제거 |
| BUG-03 | High | trading_bot.py | `tuning_report(prev_mode=이미_변경된_모드)` | 모드 변경 후 `self.mode`를 prev_mode로 전달 | `old_mode` 변수 분리해서 전달 |
| BUG-04 | Medium | trading_bot.py | `actual["trades"]` buy+sell 합산 → 청산 건수 2배 보고 | `trade_log`에 buy+sell 모두 포함 | `_sell_log` 분리해서 청산 건수만 카운트 |
| BUG-05 | Medium | trading_bot.py | session_close 강제청산 시 hold_advisor 결과 기록 누락 | 강제청산 경로에 `_record_hold_advisor_outcome` 미호출 | `force_close_all` 후 결과 기록 추가 |
| BUG-06 | Medium | trading_bot.py | `pnl_pct`를 `init_cash` 기준 계산 | `session_start_equity` 대신 `init_cash` 사용 | `pnl_pct = daily_pnl / max(session_start_equity, 1) * 100` |
| BUG-07 | Medium | telegram_commander.py | `/trades 005930` 종목코드를 건수(5930)로 오인 | `isdigit() and len < 4` 조건 미비 | `isdigit() and 1 <= int(n) <= 999` 조건으로 수정 |
| BUG-08 | Low | hold_advisor.py | `logs/hold_advisor/` 디렉토리 미생성 오류 | `get_runtime_path`가 부모 디렉토리만 생성 | `log_dir.mkdir(parents=True, exist_ok=True)` 명시적 추가 |

---

### 8. 제거된 환경변수

- **`KR_ALLOC_PCT`**: KR/US 공유 풀 방식으로 전환되어 불필요. `.env` 예시 및 README 환경변수 표에서 제거.

---

### 9. 모의투자 시작 전 종합 검증 결과 (2026-03-22)

버그 수정 후 8개 핵심 항목 시뮬레이션 검증 통과:

| 검증 항목 | 결과 |
|-----------|------|
| BUG-01,02: reused=True 경로 debate_meta 없이 정상 흐름 | 통과 |
| BUG-03: tuning_report에 old_mode 전달 | 통과 |
| BUG-04: _sell_log 분리, trades 청산 건수만 | 통과 |
| BUG-05: 강제청산 경로 hold_advisor 결과 기록 | 통과 |
| BUG-06: pnl_pct = daily_pnl / session_start_equity | 통과 |
| BUG-07: /trades 005930 종목코드 정상 인식 | 통과 |
| BUG-08: hold_advisor JSONL 디렉토리 생성 | 통과 |
| /pnl 수수료 역산 (gross_pnl = daily_pnl + total_fee) | 통과 |

**시스템 상태**: 모든 버그 수정 완료, 모의투자 시작 준비 완료.

*Last updated: 2026-03-22*
*Context session: 트레일링 스탑 + hold_advisor + 텔레그램 UI 개선 + 버그 8개 수정 + 모의투자 사전 검증*

---

## [2026-03-22] 웹 대시보드 4페이지 개편 (dashboard/dashboard_server.py)

### 배경

기존 단일 페이지 대시보드에 기간별 승률, 날짜 범위 매매 내역, 수익 곡선 차트, 국장/미장 분리 기능 추가 요청.

### 페이지 구조

| URL | 페이지 | 주요 내용 |
|-----|--------|-----------|
| `/` | 오늘 현황 | 요약 카드 5개, 분석가 판단 3명, 누적 자산 곡선, 크레딧 차트 |
| `/history` | 기간별 성과 | 기간 승률 카드, 수익 곡선, 월별 손익 바 차트, 월별 상세 테이블 |
| `/trades` | 매매 원장 | 날짜/종목/전략/매수-매도 필터, 날짜 그룹 테이블 |
| `/analytics` | 분석 | 분석가 적중률 추이, 모드별 성과, 전략별 성과, 교훈 패턴, Brain 상태 |

### 신규 Python 헬퍼

- `_parse_date(s)` — ISO 날짜 안전 파싱
- `load_records_filtered(market, period, start, end)` — week/month/3month/all/custom 기간 필터
- `group_by_month(records)` — `{YYYY-MM: [records]}` 그룹화
- `PAPER_CASH` 환경변수 기반 기준 자산

### 신규 API 엔드포인트

| 엔드포인트 | 설명 |
|-----------|------|
| `GET /api/stats/period` | 기간별 승률/손익/거래수 집계 |
| `GET /api/history/monthly` | 월별 그룹 요약 (최고/최악일 포함) |
| `GET /api/history/equity` | 기간 필터 적용 수익 곡선 |
| `GET /api/trades/list` | 날짜범위/종목/전략/매수-매도 필터 원장 |

기존 `/api/chart/equity`도 `period/start/end` 파라미터 추가.

### 공통 UI

- 헤더: 로고 + 네비게이션 + KR/US 마켓 토글 (localStorage 유지)
- 기간 필터 바: 이번주/이번달/3개월/전체 + 날짜 직접입력
- KST 실시간 시계, 30초 자동 새로고침
- Chart.js 4.4.0, JetBrains Mono + Noto Sans KR

### 버그 수정 2건

**BUG-A**: 오늘 현황 누적 자산 곡선이 `period=all` 기본값으로 전체 기간 로드 → `period=3month` 고정으로 수정.

**BUG-B (Critical)**: `COMMON_JS_BLOCK`(MARKET 초기화)이 페이지별 JS(loadAll 호출) **뒤**에 배치됨 → `loadAll()` 실행 시 `MARKET=undefined` → 모든 API가 `?market=undefined`로 호출 → 분석가 판단, 요약 카드 등 전체 미표시.

```
수정 전: _head + _header + PAGE_HTML(loadAll 호출) + COMMON_JS(MARKET 정의)
수정 후: _head + _header + COMMON_JS(MARKET 정의) + PAGE_HTML(loadAll 호출)
```

4개 라우트 전부 순서 변경 후 검증 통과 (MARKET 정의 위치 < loadAll 호출 위치 확인).

**BUG-C**: 대시보드 누적 자산이 10,000,000으로 표시되고 실제 설정값 30,000,000이 반영 안 됨.
- **원인**: `dashboard_server.py`가 별도 프로세스로 실행될 때 `.env` 미로드 → `PAPER_CASH` 기본값 10,000,000 사용
- **수정**: 파일 상단에 `load_dotenv(Path(__file__).parent.parent / ".env")` 추가

```python
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass
PAPER_CASH = float(os.getenv("PAPER_CASH", "10000000"))
```

*Last updated: 2026-03-22*
*Context session: 웹 대시보드 4페이지 개편 + MARKET 초기화 버그 수정 + PAPER_CASH .env 로드 버그 수정*

---

## [2026-03-22] 판단 기록 파이프라인 점검

### 점검 배경

모의투자 시작 후 `logs/daily_judgment/` paper 파일 4개 실제 내용 검토. "분석·판단·로직이 잘 기록되고 있는지" 확인 요청.

### 점검 결과

| 필드 | 상태 | 비고 |
|------|------|------|
| `digest_prompt` | ✅ 정상 | 330~360자 시장 데이터 요약 기록됨 |
| `round1_judgments` | ⚠️ 레거시 파일 공백 | 코드 정상, 기존 파일 레거시 문제 (아래 참조) |
| `debate_changes` | ⚠️ 레거시 파일 0건 | 동일 이유 |
| `judgments` (R2 최종) | ✅ 정상 | bull/bear/neutral stance+confidence+key_reason 기록 |
| `consensus` | ✅ 정상 | mode/size/weighted_score 포함 |
| `tickers` | ✅ 정상 | 4~5종목 기록됨 |
| `actual_result` | ✅ 정상 | pnl/trades 기록, cumulative 일부 0 (초기 세션 정상) |
| `trades` | ✅ 정상 | 휴장일 0건 (정상) |
| `session_events` | ✅ 정상 | 휴장일 스킵으로 이벤트 없음 (정상) |
| `postmortem` | ✅ 정상 | 세션 종료 후 기록됨 |
| `brain.json` 토론 기록 | ✅ 정상 | `save_debate_result()` 로 `debate_history` 별도 저장 |
| `judgment_log` JSONL | ✅ 정상 | R1/R2 raw 데이터 `logs/judgment/` 에 별도 보존 |

### round1_judgments 공백 원인 분석

**결론: 코드 버그 아님. 레거시 파일 문제.**

`get_three_judgments()` (`analysts.py:325`) 는 항상 `"_debate": {"r1": {...}, "changes": [...]}` 를 반환하고, `trading_bot.py:762` 에서 올바르게 분리해 `round1_judgments` 에 저장함.

**실제 원인**:
1. 점검한 4개 파일은 `round1_judgments` 기능 추가 **이전**에 생성됨
2. 봇 재시작 시 `reused=True` 경로에서 `saved.get("round1_judgments", {})` → 구 파일엔 없으므로 `{}`
3. 재저장 시 `{}` 유지 → 순환

**자동 복구 조건**: 해당 날짜 파일 없이 새로 `session_open()` 이 실행되면 정상 기록됨. 다음 영업일부터 신규 생성 파일에는 정상 포함.

### 판단 기록 전체 흐름 확인

```
analysts.py get_three_judgments()
    └─ R1 독립 판단 → r1 dict
    └─ R2 토론 판단 → r2 dict
    └─ brain.save_debate_result(r1, r2) → state/brain.json debate_history
    └─ judgment_log JSONL (round1, round2, changes 원본)
    └─ return {bull/bear/neutral: r2, "_debate": {r1, changes}}

trading_bot.py session_open()
    └─ debate_meta = judgments.pop("_debate", {})
    └─ today_judgment["round1_judgments"] = debate_meta["r1"]
    └─ today_judgment["debate_changes"]   = debate_meta["changes"]
    └─ live_path 즉시 저장 (대시보드용)

trading_bot.py session_close()
    └─ {**today_judgment, actual_result, postmortem, trades, session_events}
    └─ data/daily_judgments/YYYYMMDD_{market}.json 저장 (training record)
```

**결론**: 파이프라인 전체 정상. 신규 세션부터 13개 필드 완전 기록 확인.

*Last updated: 2026-03-22*
*Context session: 판단 기록 파이프라인 점검 + round1_judgments 레거시 원인 분석*

---

## [2026-03-24] 매매 0건 원인 분석 + 버그 5개 수정

### 배경

모의투자 3일 동안 매수/매도 0건. 로그 전수 분석을 통해 원인 파악 및 수정 완료.

### 근본 원인: 매수 파이프라인 전면 차단

**KR**: `get_price()` 가 모든 티커에서 `price=0` 반환 → `can_open()` `invalid price` → 전 종목 스킵
**US**: HALT/DEFENSIVE 모드로 진입 코드 차단 + 인버스 ETF `vol_ratio > 2.0` 조건 미충족 → 신호 zero

### 수정 내역

#### BUG-09 (Critical): KR 현재가 TR 코드 오류

- **파일**: `kis_api.py:119`
- **원인**: `VTTC8434R`은 모의투자 체결조회 TR, 시세조회 TR이 아님 → API `output: {}` → `price=0`
- **수정**: `tr_id = "FHKST01010100"` 단일값으로 변경 (시세조회는 모의/실거래 공통 TR)

```python
# 수정 전
tr_id = "VTTC8434R" if IS_PAPER else "FHKST01010100"
# 수정 후
tr_id = "FHKST01010100"  # 시세 조회는 모의/실거래 공통 TR
```

#### BUG-10 (High): python-dotenv 스케줄러 Python 미설치

- **증거**: `update_data` 매일 08:30, 22:00 `No module named 'dotenv'` → 가격 데이터 3일 미갱신
- **수정**: `py -3 -m pip install python-dotenv` → `python-dotenv 1.2.2` 설치

#### BUG-11 (High): tuner max_tokens=256 부족 → JSON 파싱 오류 + 임의 모드명 저장

- **파일**: `minority_report/tuner.py:38`
- **증거**: 3일 연속 `Unterminated string` 오류, 3/24 US 판단파일에 `mode=Bull_Confirmed` 비정상값 저장
- **수정 1**: `max_tokens=256` → `max_tokens=400`
- **수정 2**: `json.loads(raw)` 이후 `VALID_MODES` 검증 추가 → 유효하지 않은 mode는 `prev_mode`로 대체

```python
VALID_MODES = {
    "AGGRESSIVE","MODERATE_BULL","MILD_BULL","CAUTIOUS",
    "NEUTRAL","MILD_BEAR","CAUTIOUS_BEAR","DEFENSIVE","HALT"
}
if result.get("mode") not in VALID_MODES:
    result["mode"] = prev_mode
```

#### BUG-12 (Medium): US DEFENSIVE/HALT 모드에서 인버스 ETF만 선택 → vb_sig 신호 불발

- **파일**: `minority_report/analysts.py` `select_tickers()`
- **원인**: DEFENSIVE 모드에서 Claude가 TZA, SPDN, NVD 같은 인버스 ETF만 선택 → `vol_ratio > 2.0` 조건 미충족 → 신호 never fire
- **수정**: US DEFENSIVE/HALT 모드에서 인버스 ETF만 선택된 경우 안정 종목(T, VZ, KO 등) 자동 보완

```python
US_INVERSE_ETFS = {"TZA", "SPDN", "NVD", "SQQQ", "SDOW", "SPXU", "SH", "PSQ", "MYY"}
US_STABLE_ANCHORS = ["T", "VZ", "XLU", "KO", "JNJ", "PG", "O", "VYM", "SCHD"]
# 인버스만 선택된 경우: 인버스 1개 + 안정 종목으로 보완
```

#### BUG-13 (확인): postmortem max_tokens — 이미 800, 수정 불필요

- `minority_report/postmortem.py:162` — `max_tokens=800` 이미 올바름

### 검증 결과

| 검증 항목 | 결과 |
|-----------|------|
| `VTTC8434R` 제거, `FHKST01010100` 단일값 | ✅ |
| python-dotenv `from dotenv import load_dotenv` | ✅ |
| tuner `max_tokens=400`, `VALID_MODES` 검증 존재 | ✅ |
| analysts.py `US_INVERSE_ETFS` 보완 로직 | ✅ |
| 4개 파일 py_compile 통과 | ✅ |
| 전체 import 테스트 통과 | ✅ |

*Last updated: 2026-03-24*
*Context session: 매매 0건 원인 분석(KR TR코드 버그 + US 인버스ETF + tuner JSON 오류) + 버그 5개 수정*

---

## [2026-03-25] 실시간 신호 피드 + 모니터링 종목 대시보드 + 텔레그램 신호 알림

### 목적
"봇이 뭘 보고 있는지, 신호가 났는지 안 났는지 모르겠다"는 문제 해결.
신호 발생/차단 시 텔레그램 즉시 알림 + 대시보드에 종목별 상태 실시간 표시.

### 2차 판단 버그 조사 결과
로그 3/24 `judgment_20260324.jsonl` 직접 확인 → **버그 아님**.
```
R1: Bull=MILD_BULL(62%) Bear=DEFENSIVE(82%) Neut=MILD_BEAR(62%)
R2: Bull=MILD_BULL(62%) Bear=DEFENSIVE(85%) Neut=MILD_BEAR(62%)
changes=0 (전원 의견 유지, Bear 확신도만 82→85% 소폭 강화)
```
3/21 이전 로그에 round1/round2 데이터가 없는 건 당시 코드가 해당 필드를 저장하지 않았기 때문 (레거시).
3/24부터 정상 기록됨.

### 변경 파일

#### `telegram_reporter.py` — `signal_alert()` 신규 추가
- **진입신호** (`entry_signal`): 🟢 종목/전략/가격/주문금액 전송
- **신호차단** (`signal_blocked`): 🚫 종목/전략/모드 전송 (HALT/DEFENSIVE 억제 시)
- **보유중 스킵** (`entry_skip` + `already_holding`): 🔵 중복진입 차단 알림
- 기타 skip(예산부족, 슬리피지 등)은 알림 제외 (노이즈)

#### `trading_bot.py` — signal_alert 호출 3곳 추가
| 위치 | 이벤트 | 조건 |
|------|--------|------|
| `signal_blocked` 직후 | signal_blocked | HALT/DEFENSIVE 모드 |
| `entry_signal` 직후 | entry_signal | 신호 발생 + 주문 실행 전 |
| `can_open()` 실패 직후 | entry_skip(already_holding) | 보유중 중복진입 시만 |

#### `dashboard/dashboard_server.py` — 3개 기능 추가

**1. `/api/tickers/today` 엔드포인트**
- `logs/daily_judgment/YYYYMMDD_KR.json`에서 `tickers` 필드 읽기
- 오늘 analysis 로그 집계: 종목별 최근 이벤트/가격/신호횟수
- 반환: `{market, mode, tickers:[{ticker, last_event, last_ts, last_price, sig_count}], universe_count}`

**2. "오늘 모니터링 종목" UI 섹션 (15초 갱신)**
- 카드형 표시: 종목코드 + 마지막 이벤트(⏳대기중/🟢진입신호/🚫차단/🟠스킵/⬜신호없음)
- 신호 발생 횟수 배지 표시
- 후보 총 N개 중 선택 표시

**3. `/api/signals/recent` + 신호 피드 UI (10초 갱신)**
- analysis JSONL에서 entry_signal/entry_skip/signal_blocked/signal_check(none) 읽기
- 이벤트별 색상: 🟢초록/⬜회색/🔴빨강/🟠주황/🔵파랑

**4. `/api/judgments` — round1 비교 데이터 추가**
- `r1_stance` 필드 추가 → 판단 카드에 "💬 토론 변경/유지" 표시
- `debate_changes` 배열 반환

### 검증
| 항목 | 결과 |
|------|------|
| py_compile 3파일 | ✅ |
| signal_alert import/callable | ✅ |
| /api/signals/recent HTTP 200 | ✅ |
| /api/tickers/today HTTP 200 | ✅ (tickers/mode 정상 반환) |

---

## [2026-03-25] 모니터링 종목 고정 버그 수정 (BUG-14 ~ BUG-15)

### 증상
대시보드에서 TZA, SPDN, 038110이 계속 동일하게 표시됨. 장세/모드가 바뀌어도 종목이 고정.

### 원인 분석
**BUG-14 (High): reused=True 시 tickers 고정**
- `session_open()`에서 당일 판단 파일이 존재하면 `reused=True`로 판단을 재사용
- 이때 `tickers`도 저장된 것을 그대로 복원 (`saved.get("tickers", [])`)
- 봇이 하루 중 여러 번 재시작되어도 아침에 선택된 종목이 하루 종일 고정됨
- 3/23 DEFENSIVE 모드로 선택된 TZA/SPDN이 3/24 재시작 후에도 계속 사용됨

**BUG-15 (Medium): 튜너 모드 변경 후 종목 미갱신**
- 튜너가 DEFENSIVE→MODERATE_BULL 등으로 모드를 바꿔도 `today_tickers`가 그대로 유지됨
- 모드와 종목이 불일치: BULL 모드인데 인버스 ETF(TZA)를 보고 있는 상태 발생

### 수정 (`trading_bot.py`)

**BUG-14 수정**: `session_open()` else 분기 추가
```
reused=True → 판단(get_three_judgments) 재사용 유지 (크레딧 절약)
             + 종목(screen + select_tickers)은 항상 새로 실행
```
- `ticker_rescreen` 이벤트로 judgment 로그에 기록됨

**BUG-15 수정**: `run_tuning()`에서 모드 변경 시 종목 재선택
- `old_mode != new_mode and action != "REVERSE"` → screener + select_tickers 재실행

**BUG-15 수정 2**: `_reinvoke_analysts()`에서 모드 플립 시 종목 재선택
- BEAR→BULL or BULL→BEAR 방향 전환 시 즉시 종목 갱신

### 검증
| 항목 | 결과 |
|------|------|
| py_compile | ✅ |
| 키 코드 패턴 확인 (종목 재스크리닝/튜너 종목갱신/신호 알림) | ✅ |

*Last updated: 2026-03-25*
*Context session: 신호 피드 대시보드 + 텔레그램 신호 알림 + 모니터링 종목 표시 + 2차 판단 버그 조사 + 종목 고정 버그 수정*

---

## [2026-03-25] US 스크리너 후보 선정 기준 + AV API 캐싱 (BUG-16)

### 배경
대시보드에서 TSLA/NVDA/AAPL이 매일 동일하게 나와 하드코딩 의심 → 실제로는 AV API 실패 후 폴백 사용 중이었음.

### US 스크리너 정상 동작 기준
`screen_market_us()` — Alpha Vantage `TOP_GAINERS_LOSERS` API
| 섹션 | 의미 |
|------|------|
| `most_actively_traded` | 당일 거래량 상위 종목 |
| `top_gainers` | 당일 상승률 상위 종목 |
| `top_losers` | 당일 하락률 상위 종목 |

세 섹션 합쳐서 최대 30개 후보 → Claude가 consensus_mode + RSI/MACD/BB 근거로 3~5개 선택 후 이유 반환.

KR 스크리너는 KIS API `거래량 순위` (FID_VOL_CNT=100000 이상 필터, 상위 30개).

### BUG-16 원인 (High): AV API 무료 25회/일 한도 초과
- 봇 재시작 + `reused=True` 재스크리닝 + 튜너 모드 변경 재선택이 모두 AV API를 개별 호출
- 하루 25회 금방 소진 → `Information` 메시지 반환 → 빈 결과 → 폴백 유니버스 사용
- 폴백 = `_US_FALLBACK_UNIVERSE` (하드코딩 15개, 가격/거래량 0) → Claude가 선택 근거 없음

### BUG-16 수정 (`kis_api.py`)
- `state/av_screen_cache.json`에 당일 AV API 결과 캐시
- 동일 날짜 캐시 존재 시 API 호출 없이 재사용 → 하루 1회만 소진
- `Information`/`Note` 메시지 감지 시 `[AV API 한도]` 경고 로그 출력
- 폴백 유니버스는 여전히 유지 (AV 전면 장애 또는 키 없을 때)

### 선택 이유 대시보드 표시
- `select_tickers()` 응답의 `reasons` 필드가 이미 analysis 로그에 기록됨 (기존 코드)
- `/api/tickers/today` — `select_reason` 필드 추가, `candidates`/`not_selected` 목록 반환
- 대시보드 종목 카드에 Claude 선택 이유 표시
- "후보 N개 중 M개 선택 · 제외된 후보: ..." 표시

### 검증
| 항목 | 결과 |
|------|------|
| py_compile kis_api.py | ✅ |
| AV 한도 초과 메시지 감지 | ✅ (로그 출력 확인) |
| 캐시 파일 생성 로직 | ✅ |
| /api/tickers/today select_reason 반환 | ✅ |

### 현재 시스템 상태 (2026-03-25 기준)
| 구분 | 상태 |
|------|------|
| KR 스크리너 | 정상 (KIS 거래량 순위 API) |
| US 스크리너 | 오늘 AV 한도 소진 → 폴백 사용 중 / 내일부터 캐싱으로 정상화 |
| 신호 피드 대시보드 | 신규 추가 완료 |
| 텔레그램 신호 알림 | 신규 추가 완료 (entry_signal / signal_blocked / already_holding) |
| 종목 고정 버그 | BUG-14/15 수정 완료 |
| 2차 판단 | 정상 동작 확인 (버그 아님) |

*Last updated: 2026-03-25*
*Context session: US 스크리너 후보 선정 기준 조사 + AV API 캐싱(BUG-16) + 선택 이유 대시보드 표시*
## [2026-03-25] 장중 live judgment 동기화 + 재스크리닝 후보 로그 보강

### 배경
코드 리뷰에서 다음 두 문제가 확인됨.

- `run_tuning()` / `_reinvoke_analysts()`가 장중 모드·종목을 바꿔도 `logs/daily_judgment/YYYYMMDD_{market}.json`을 다시 쓰지 않음
- 장중 재스크리닝이 발생해도 `analysis` 로그에 새 `screen_candidates` 이벤트가 남지 않아 대시보드 후보 목록이 아침 스캔 기준으로 고정됨

이 상태에서는 프로세스 재시작 시 `session_open()`이 아침 판단을 다시 재사용하고, `/api/judgments` 및 `/api/tickers/today`도 종가 전까지 최신 장중 상태를 반영하지 못함.

### 수정 내용

#### 1. `trading_bot.py` 공통 헬퍼 추가
- `TradingBot._persist_live_judgment(market)`
  - 현재 `self.today_judgment`를 즉시 `logs/daily_judgment/YYYYMMDD_{market}.json`에 다시 저장
  - 장중 모드/종목 변경 후 재시작해도 최신 판단이 유지되도록 보장
- `TradingBot._log_screen_candidates(market, candidates, source)`
  - 재스크리닝 결과를 `analysis` 로그의 `screen_candidates` 이벤트로 기록
  - `source` 필드로 `session_open`, `session_reuse_rescreen`, `tuning_rescreen`, `analyst_reinvoke_rescreen` 구분 가능

#### 2. `run_tuning()` 장중 변경 즉시 영속화
- 튜너가 `MAINTAIN`이 아닌 결정을 내리면:
  - 모드 변경 시 재스크리닝 후보를 `screen_candidates`로 기록
  - 새 종목을 뽑으면 `tickers`, `universe_tickers`를 함께 갱신
  - 처리 종료 전에 live judgment 파일을 다시 저장

#### 3. `_reinvoke_analysts()` 재판단 결과 완전 반영
- 긴급 재판단 후:
  - `judgments`, `consensus`뿐 아니라 `round1_judgments`, `debate_changes`도 `self.today_judgment`에 반영
  - 모드 변경으로 재스크리닝하면 최신 후보 집합을 `screen_candidates`로 기록
  - 새 `tickers`, `universe_tickers`를 저장 후 live judgment 파일을 다시 저장

#### 4. `session_open()` 재사용 분기 보강
- 재시작 후 `reused=True` 경로에서도 종목을 새로 스캔할 때 최신 `screen_candidates` 이벤트를 남기도록 변경
- 이제 대시보드는 "현재 세션에서 마지막으로 수행한 후보 스캔"을 기준으로 `candidates` / `not_selected`를 계산함

### 영향
- 장중 모드 변경 후 프로세스가 재시작돼도 최신 판단이 유지됨
- `/api/judgments`가 장중 재판단의 최신 합의/토론 메타데이터를 즉시 보여줌
- `/api/tickers/today`가 최신 재스크리닝 후보 집합과 제외 종목 목록을 보여줌

### 검증
- `python -m py_compile trading_bot.py dashboard/dashboard_server.py`

*Last updated: 2026-03-25*
*Context session: review 지적사항 반영 - 장중 live judgment 재저장 + 대시보드 후보 로그 동기화*

## [2026-03-25] US API 마이그레이션 회귀 수정

### 배경
리뷰에서 US API 전환 이후 다음 회귀 2건이 확인됨.

- `get_daily_ohlcv(..., market="US")`가 yfinance 예외 발생 시 Alpha Vantage 폴백까지 도달하지 못함
- FMP 스크리너 후보가 `volume: 0`으로 저장되어 `ENABLE_DYNAMIC_UNIVERSE` 경로에서 US 유니버스가 전부 필터링됨

### 수정 내용

#### 1. US OHLCV 폴백 체인 복구
- `kis_api._daily_ohlcv_us_yf()`에서 yfinance `history()` 예외를 빈 DataFrame으로 정규화
- `kis_api.get_daily_ohlcv()`에서도 yfinance 호출을 한 번 더 감싸 예외가 나더라도 AV 폴백으로 이어지게 보강
- 결과적으로 우선순위는 `yfinance -> Alpha Vantage`를 유지하면서, yfinance transient error 시에도 US 세션 시작/지표 계산이 계속 진행됨

#### 2. FMP 거래량 파싱 보강
- `kis_api._safe_int()` 추가: 콤마 포함 문자열/숫자형 거래량을 안전하게 정수 변환
- `kis_api._extract_us_volume()` 추가: `volume`, `avgVolume`, `avgVolume3m`, `averageVolume`, `volumeAverage`, `sharesVolume` 순으로 거래량 추출
- FMP/AV 스크리너 모두 이 공통 헬퍼를 사용하도록 변경

#### 3. 잘못 생성된 FMP zero-volume 캐시 자동 무효화
- `screen_market_us()`가 당일 캐시를 읽을 때:
  - `source == "fmp"` 이고
  - 모든 후보 거래량이 0이면
  - 기존 캐시를 그대로 쓰지 않고 즉시 재조회
- 이 보강으로 오늘 이미 생성된 잘못된 `us_screen_cache.json`도 다음 호출에서 자동 복구 가능

### 영향
- US 일봉 조회가 yfinance 일시 오류에도 더 이상 바로 실패하지 않음
- US 동적 유니버스가 zero-volume 후보 때문에 비어 버리는 회귀가 해소됨
- 잘못된 FMP 캐시가 남아 있어도 런타임에서 자동으로 재조회함

### 검증
- `python -m py_compile kis_api.py trading_bot.py dashboard/dashboard_server.py universe_manager.py`
- inline 검증:
  - yfinance 예외 시 `get_daily_ohlcv(..., market='US')`가 AV 폴백을 호출하는지 확인
  - 거래량 추출 헬퍼가 `volume`/`avgVolume`/`sharesVolume`를 정상 파싱하는지 확인
  - zero-volume FMP 캐시가 있을 때 `screen_market_us()`가 캐시를 무시하고 재조회하는지 확인

*Last updated: 2026-03-25*
*Context session: review 지적사항 반영 - US OHLCV 폴백 복구 + FMP 거래량 파싱/캐시 보강*

## [2026-03-25] entry_failed 현금부족 분류 보정

### 배경
리뷰에서 `open_position()` 실패 시 사유 분류가 실제 `RiskManager.open_position()` 판정과 어긋나는 문제가 확인됨.

- 기존 로직은 `self.risk.cash < risk_price`만 비교해서 `현금부족` / `내부오류`를 분기
- 하지만 실제 주문 거절 조건은 `price * qty + buy_fee > cash`
- 따라서 잔액이 주가 자체는 감당하지만 매수 수수료까지 포함한 총 주문비용은 못 감당하는 경우가 정상적인 잔고 부족임에도 `내부오류`로 기록됨

### 수정 내용
- `trading_bot.py`의 `open_position()` 실패 후처리를 다음 기준으로 변경
  - `required_cash = (risk_price * qty) + self.risk._fee("buy", risk_price * qty)`
  - `cash < required_cash` 이면 `현금부족`
  - 그 외만 `내부오류`
- 실패 메시지에 `필요 금액`과 `잔액`을 함께 기록
  - 예: `현금부족(필요:1,000.15원,잔액:1,000.00원)`

### 영향
- 수수료 포함 주문 총액 부족이 더 이상 내부 오류로 오분류되지 않음
- 대시보드/분석 로그의 `entry_failed` 사유가 실제 거래 거절 원인과 일치함

### 검증
- `python -m py_compile trading_bot.py risk_manager.py`
- inline 검증:
  - KR 수수료율 기준으로 `cash=1000`, `price=1000`, `qty=1`일 때
  - `현금부족(필요:1,000.15원,잔액:1,000.00원)`으로 분류되는지 확인

*Last updated: 2026-03-25*
*Context session: review 지적사항 반영 - entry_failed 수수료 포함 현금부족 분류 보정*

---

## [2026-03-25] US API 전면 전환 — Finnhub + FMP + yfinance (BUG-16 근본 수정)

### 배경

Alpha Vantage 무료 25회/일 한도가 실제 봇 운영 시 필요 호출 수(하루 240+회)에 턱없이 부족.
캐싱으로 1차 대응했으나 가격 조회(`_get_price_us_alpha`)도 AV를 사용 중이라 근본 해결 필요.

**1일 AV 호출 분석**:
| 호출처 | 빈도 | 건수/일 |
|--------|------|---------|
| `get_price()` US | 매 사이클(5분) × 3종목 | ~240회 |
| `get_daily_ohlcv()` US | 세션 초반 | ~3회 |
| `screen_market_us()` | 1회 (캐시 없을 때) | 1회 |
| `get_usd_krw()` | 세션당 1회 | 1회 |
| **합계** | | **~245회** (한도 25회의 10배) |

### 신규 API 스택

| 역할 | API | 무료 한도 | 비고 |
|------|-----|-----------|------|
| US 현재가 | **Finnhub** `/quote` | 60회/분, 일 한도 없음 | 주력 |
| US OHLCV | **yfinance** | 무제한 | 주력 |
| US 스크리너 | **FMP** `/stable/biggest-gainers` 등 | 250회/일 | 주력 |
| USD/KRW 환율 | **yfinance** `USDKRW=X` | 무제한 | 기존 1차 유지 |
| 위 모두 실패 시 | **Alpha Vantage** (KEY-1 → KEY-2 자동 전환) | 25회/일 × 2키 | 최후 폴백 |

### 수정 내용 (`kis_api.py`)

#### 환경변수 추가
```python
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "")
FMP_KEY     = os.getenv("FMP_API_KEY", "").strip()
AV_KEY_2    = os.getenv("ALPHA_VANTAGE_KEY_2", "")  # AV 2번째 키
```

#### `_get_price_us_finnhub()` 신규
- Finnhub `/quote` 엔드포인트 호출
- 실패 시 상위에서 yfinance → AV 순으로 폴백

#### `get_price()` US 분기 변경
```
Finnhub → yfinance → Alpha Vantage (레거시 최후 폴백)
```

#### `get_daily_ohlcv()` US 분기 변경
```
yfinance (무제한) → Alpha Vantage (레거시 폴백)
```

#### `_fmp_screen_candidates()` 신규
- FMP `/stable/biggest-gainers`, `/stable/most-actives`, `/stable/biggest-losers` 3개 엔드포인트 호출
- 섹션 합쳐서 중복 제거 후 후보 반환

#### `screen_market_us()` 완전 재작성
```
FMP (3개 엔드포인트) → Alpha Vantage (KEY-1→KEY-2 자동 전환) → 하드코딩 폴백
```
- 당일 캐시 파일 `state/us_screen_cache.json` 유지 (기존 `av_screen_cache.json`과 동일 경로)
- `source` 필드 추가 (`"fmp"` / `"av"`)

#### `get_usd_krw()` 간소화
- AV 2차 폴백 제거 (yfinance가 안정적이므로 불필요)
```
yfinance → .env USD_KRW_RATE 기본값
```

#### `_av_get()` 헬퍼 신규 — AV 이중 키 자동 전환
```python
def _av_get(params: dict, timeout: int = 15) -> dict:
    # KEY-1 호출 → Information/Note(한도초과) 감지 → KEY-2 재시도
    # 두 키 모두 한도 초과 시 RuntimeError
```
- 기존 AV 호출 3곳(`_get_price_us_alpha`, `_daily_ohlcv_us_alpha`, `screen_market_us`)이 모두 이 헬퍼 사용

### .env 변경
```
FINNHUB_API_KEY=...         # 신규 추가
FMP_API_KEY=...             # 신규 추가
ALPHA_VANTAGE_KEY=...       # 기존 유지 (KEY-1)
ALPHA_VANTAGE_KEY_2=...     # 신규 추가 (KEY-2 자동 전환)
```

### 실제 테스트 결과
| 테스트 | 결과 |
|--------|------|
| Finnhub TSLA quote | ✅ `$382.96` |
| FMP screen_market_us top-5 | ✅ 5개 후보 반환 |
| yfinance USD/KRW | ✅ `1500.19` |
| yfinance TSLA OHLCV 200일 | ✅ 정상 |
| py_compile | ✅ |

### 현재 AV 상태
- KEY-1 (`6Q9MBA3E...`): 오늘 한도 소진
- KEY-2 (`9f6eS3D9...`): 오늘 한도 소진 (신규 발급 직후 테스트로 소진)
- 내일부터 두 키 모두 초기화되어 자동 전환 로직 정상 동작 예정
- 실운영 중에는 Finnhub/FMP/yfinance가 먼저 처리하므로 AV는 거의 호출되지 않음

*Last updated: 2026-03-25*
*Context session: US API Finnhub+FMP+yfinance 전환 + AV 이중 키 자동 전환*

---

## [2026-03-25] 미체결 이유 대시보드 표시 + 라이브 손익 실시간 반영

### 배경
- 진입신호(🟢)가 찍혔는데 실제 매수가 안 된 이유를 대시보드에서 알 수 없었음
- 누적 자산 / 오늘 손익이 `session_close()` 이후에만 갱신되어 장 중에는 항상 초기값(30,000,000원)으로 고정

### 1. `entry_failed` 이벤트 신규 추가 (`trading_bot.py`)

`open_position()` 실패 시 기존엔 `log.error`만 찍고 끝났음. 이제 JSONL에 기록:

```python
{
  "event": "entry_failed",
  "ticker": "GOCO",
  "reason": "현금부족(필요:800,000원,잔액:712,000원)",
  "strategy": "volatility_breakout",
  "mode": "MILD_BEAR"
}
```

실패 사유 분류 기준:
- `required_cash = risk_price × qty + buy_fee`
- `cash < required_cash` → `현금부족(필요:N원,잔액:M원)`
- 그 외 → `내부오류`

### 2. 미체결 이유 누적 표시 (`dashboard_server.py`)

종목 카드에 오늘 발생한 모든 미체결 이유를 누적 표시:

```
GOCO  [신호 1회]
$2.09
🟢 진입신호
⚠ 미체결: 예산 소진 / 이미 보유중
```

- `entry_skip` / `signal_blocked` / `entry_failed` 이유를 종목별로 리스트로 집계
- 마지막 이벤트가 `entry_signal`로 덮어써져도 이유가 사라지지 않음
- 한글 변환: `already_holding`→이미 보유중, `budget_exhausted`→예산 소진, `HALT`→HALT 모드 등
- EVENT_MAP에 `entry_failed` (❌ 주문실패) 추가

### 3. `select_tickers` 종목 선택 이유 한글화 (`minority_report/analysts.py`)

```python
# 변경 전
"rules: Return JSON only. reasons: short reason in English"

# 변경 후
"규칙: reasons는 반드시 한국어로 작성 (30자 이내)"
```

### 4. 라이브 상태 파일 (`trading_bot.py` + `dashboard_server.py`)

**문제**: 대시보드 손익/자산이 EOD 파일 기준 → 장 중 항상 초기값

**수정**:

`_write_live_status(market)` 신규 — `run_cycle()` 완료 후 매 5분마다 저장:
```json
// state/live_status_US.json
{
  "market": "US",
  "updated_at": "03:41:50",
  "trading_date": "2026-03-25",
  "mode": "MILD_BEAR",
  "daily_pnl": -1250.0,
  "daily_pnl_pct": -0.42,
  "cash": 29197355.0,
  "total_equity": 29732000.0,
  "positions": [
    {"ticker": "CVV", "qty": 105, "avg_price": 7643, "pnl_pct": -0.32, "strategy": "volatility_breakout"}
  ],
  "position_count": 1
}
```

`_load_live_status(market)` — 대시보드 `/api/summary`에서 라이브 파일 우선 읽기:
- `session_active == true` 이고 `trading_date == today_rec.date` 일 때만 라이브 값 반영
- 조건 불일치 시: 기존 EOD 판단 파일 기준으로 즉시 폴백
- 파일 없으면: 기존 EOD 판단 파일 기준 유지 (하위 호환)

**반영 항목**:
| 항목 | 이전 | 이후 |
|------|------|------|
| 오늘 손익 | session_close 후에만 | 매 5분 실시간 |
| 누적 자산 | 30,000,000 고정 | 현금 + 포지션 평가액 |
| 모드 | EOD 파일 | 현재 운영 모드 |
| 보유 포지션 | 없음 | positions 배열 추가 |

### 검증
| 항목 | 결과 |
|------|------|
| py_compile 2파일 | ✅ |
| CVV 실제 매수 체결 확인 (첫 US 매수) | ✅ `105@$5.09 volatility_breakout` |

*Last updated: 2026-03-25*
*Context session: 미체결 이유 대시보드 표시 + select_tickers 한글화 + 라이브 손익 실시간 반영*

---

## [2026-03-25] 라이브 상태 파일 버그 수정 — positions 타입 오류

### 증상
봇 재시작 후 대시보드 누적 자산이 여전히 30,000,000원 고정. `state/live_status_US.json` 파일이 생성되지 않음.

### 원인
`_write_live_status()` 에서 `self.risk.positions`를 딕셔너리로 가정해 `.items()` 호출:
```python
# 잘못된 코드
for ticker, pos in self.risk.positions.items():
```

그러나 `risk_manager.py:36`에서 `self.positions = []` — **리스트** 자료구조.

`.items()` 호출 시 `AttributeError` 발생 → `except` 블록에서 `log.debug`로 묻혀 로그에 보이지 않음.

### 수정 (`trading_bot.py`)
```python
# 수정 후
positions = [
    {
        "ticker":        pos.get("ticker", ""),
        "qty":           pos.get("qty", 0),
        ...
    }
    for pos in self.risk.positions   # list 순회
]
# total_equity 계산도 동일하게 수정
```

- `log.debug` → `log.warning` 으로 격상 (이후 저장 실패 시 로그에 표시됨)

### 교훈
`positions` 구조를 딕셔너리로 잘못 가정. `risk_manager.py`의 실제 타입(`list[dict]`)과 불일치. 향후 `positions` 접근 시 리스트 순회 방식으로 일관되게 사용.

---

## [2026-03-25] 라이브 상태 회귀 수정 — PnL 퍼센트/세션 신선도 검증

### 증상
- `live_status_{market}.json` 의 `daily_pnl_pct` 가 항상 `0`으로 기록되어 대시보드 장중 수익률이 0%로 고정됨
- 날짜가 바뀌었거나 재시작 직후 새 사이클이 돌기 전에도 이전 세션 라이브 상태가 `/api/summary`에 반영될 수 있음

### 원인
- `RiskManager.get_status()` 는 퍼센트 손익을 `daily_return` 으로 제공하는데 `_write_live_status()` 는 존재하지 않는 `daily_pnl_pct` 키를 읽고 있었음
- 라이브 상태 파일에는 `updated_at` 만 `HH:MM:SS` 형식으로 기록되고, 대시보드는 `session_active` 나 거래일 일치 여부를 확인하지 않고 파일 존재만으로 우선 적용했음

### 수정
- `trading_bot.py`
- `daily_pnl_pct` 기록 소스를 `status["daily_return"]` 으로 변경
- `updated_at` 을 KST 기준으로 기록하고, 같은 기준의 `trading_date(YYYY-MM-DD)` 필드 추가
- `dashboard/dashboard_server.py`
- `_is_fresh_live_status()` 추가
- `session_active == true` 이고 `live.trading_date == today_rec.date` 일 때만 라이브 상태로 오늘 요약 덮어쓰기
- 조건 불일치 시 `today_rec.actual_result` 로 폴백

### 검증
| 항목 | 결과 |
|------|------|
| `python -m py_compile trading_bot.py dashboard/dashboard_server.py` | ✅ |
| `daily_pnl_pct` 기록 키 확인 (`daily_return`) | ✅ |
| stale live status 차단 조건 추가 (`session_active`, `trading_date`) | ✅ |

*Last updated: 2026-03-25*
*Context session: live_status 회귀 수정 (daily_pnl_pct 키/세션 신선도 검증)*

---

## [2026-03-25] 대시보드 시장 컨텍스트 차트 추가

### 요청
- 메인 대시보드에서 한국/미국 장의 대표 지수 흐름을 일별 그래프로 보고 싶음
- 환율(`USD/KRW`)과 리스크 지표(`VKOSPI` / `VIX`)도 함께 확인하고 싶음

### 구현
- `dashboard/dashboard_server.py`
- `/api/chart/market-context` 신규 추가
- 메인 대시보드에 차트 2개 추가
- `시장 지수 일별 흐름`
- `환율 / 리스크 지표`

### 데이터 소스
- `data/daily_digest/*_{market}.json` 를 읽어 기간별 시계열 구성
- KR:
- `KOSPI`, `KOSDAQ`, `USD/KRW`, `VKOSPI`
- US:
- `S&P500`, `NASDAQ`, `USD/KRW`, `VIX`

### digest 확장
- `phase1_trainer/digest_builder.py`
- KR live context 에 `kosdaq` 추가
- US live context 에 `usd_krw` 추가
- KR/US 주요 지수에 `close` 필드도 함께 저장하도록 확장

### 참고
- 기존 과거 digest 에는 `kosdaq` 또는 US 쪽 `usd_krw` 가 없을 수 있어 일부 과거 구간은 차트가 비어 보일 수 있음
- 최근 digest 부터는 새 필드가 채워짐

### 검증
| 항목 | 결과 |
|------|------|
| `python -m py_compile dashboard/dashboard_server.py phase1_trainer/digest_builder.py` | ✅ |
| `/api/chart/market-context` 추가 | ✅ |
| 메인 대시보드 차트 2종 추가 | ✅ |

*Last updated: 2026-03-25*
*Context session: 대시보드 시장 컨텍스트 차트 추가 (KR/US 지수 + USD/KRW + VIX/VKOSPI)*

---

## [2026-03-26] 매수가 표시 정합성 수정 — 텔레그램 / 웹 대시보드 / live_status

### 점검 결과
- 텔레그램 매수 체결 알림이 실제 포지션 진입가(`entry`)와 다른 가격을 표시할 수 있었음
- `live_status_{market}.json` 는 포지션 원본 구조가 `entry` 를 쓰는데 `avg_price` 를 읽고 있어 매수가가 `0`으로 저장될 수 있었음
- 텔레그램 상태 보고 / 대시보드 푸시는 현재가와 수익률만 보여주고 매수가는 표시하지 않았음
- 웹 대시보드도 보유 포지션 카드가 없거나, 있더라도 잘못된 `avg_price` 값에 의존할 여지가 있었음

### 원인
- `trade_alert("buy")` 호출 시 실제 포지션 저장가 `risk_price` 대신 `price` 를 전달
- `_write_live_status()` 가 `pos["entry"]` 대신 `pos.get("avg_price", 0)` 를 기록
- 텔레그램/웹 표시 로직이 `entry` 기준 진입가 표시를 일관되게 사용하지 않음

### 수정
- `trading_bot.py`
- 매수 알림 가격, TP/SL 계산 기준을 `risk_price` 로 통일
- `live_status` 포지션 직렬화 시 `avg_price = entry` 로 저장
- `telegram_reporter.py`
- `status_report()` / `dashboard_push()` 를 `entry` 기준 `매수가 / 현재가 / 수익률` 표시로 재정의
- `dashboard/dashboard_server.py`
- 오늘 화면 `loadSummary()` 에서 보유 포지션 카드를 `매수가 / 현재가 / 수익률` 기준으로 렌더링
- 보유 포지션 개수와 live 업데이트 시각도 함께 표시

### 효과
- 텔레그램 체결 알림과 시스템 내부 포지션 진입가가 동일한 기준을 사용
- 웹 대시보드가 보유 종목별 매수가를 정상 표시
- 다음 라이브 상태 저장 사이클부터 `state/live_status_{market}.json` 의 `avg_price` 가 정상값으로 갱신

### 검증
| 항목 | 결과 |
|------|------|
| `python -m py_compile trading_bot.py telegram_reporter.py dashboard/dashboard_server.py` | ✅ |
| `trade_alert("buy")` 가격 기준 `risk_price` 로 통일 | ✅ |
| `live_status` 의 `avg_price` 를 `entry` 기준으로 저장 | ✅ |
| 텔레그램 상태/대시보드에 `매수가` 표시 추가 | ✅ |
| 웹 대시보드 보유 포지션 카드에 `매수가 / 현재가 / 수익률` 표시 | ✅ |

*Last updated: 2026-03-26*
*Context session: 매수가 표시 정합성 수정 (텔레그램 + 웹 대시보드 + live_status)*

---

## [2026-03-25] 이슈 2건 수정 — Bear DEFENSIVE 고착 + KR 후보 부족

### 이슈 1: Bear 항상 DEFENSIVE (`minority_report/analysts.py:95`)

**증상**: Bear 분석가가 시장과 무관하게 항상 DEFENSIVE 판단 → consensus에서 DEFENSIVE 우세

**원인**: `환율 1,450 이상 → 기본값 DEFENSIVE` 하드코딩. 2025~2026년 상시 환율(1,480~1,510원)이 임계값을 항시 초과 → Bear가 매일 DEFENSIVE 고착.

**수정**:
```python
# 수정 전
• VIX 25 이상 or 환율 1,450 이상 → 기본값 DEFENSIVE

# 수정 후
• VIX 25 이상 or 환율 당일 ±1.5% 이상 급변 → 기본값 DEFENSIVE
```

**임계값 근거**: USD/KRW 평상시 일중 변동폭 0.5~1%, 1.5% 이상은 실제 이벤트성 급변 수준 (위기 시 3%+). 1%는 평상시와 구분이 안 되어 1.5%로 설정.

---

### 이슈 2: KR 후보 1개 고착 (`kis_api.py`)

**증상**: `session_open(08:50)` 실행 시 KR 스크리너가 1개 종목만 반환 → 하루 종일 해당 종목만 모니터링

**원인**: KR 장 개시(09:00) 전에 `screen_market_kr()` 호출 → `FID_VOL_CNT=100000` 필터 통과 종목 거의 없음 → 1~2개만 반환. MAINTAIN 튜너는 재스크리닝 안 하므로 하루 내내 고착.

**수정**: `kis_api.py`에 KR 블루칩 폴백 유니버스 추가

```python
_KR_FALLBACK_UNIVERSE = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "005380",  # 현대차
    "051910",  # LG화학
    "035720",  # 카카오
    "000270",  # 기아
    "068270",  # 셀트리온
    "105560",  # KB금융
    "055550",  # 신한지주
]
```

`screen_market_kr()` 로직:
- API 결과 < 5개 → 폴백 종목으로 10개까지 보완
- API 자체 실패 → 전체 폴백 10종목 반환 (기존: 빈 리스트)

---

### 모의투자 구조 확인 (현황)

**현재 봇은 한투 API가 아닌 자체 내부 시뮬레이션으로 모의투자 중.**

| 항목 | 상태 |
|------|------|
| `KIS_IS_PAPER` | `true` |
| 매수/매도 실행 | 내부 메모리(`self.risk.positions`) + `open_positions.json`만 기록, 한투 서버에 주문 전송 없음 |
| 한투 앱 | 변동 없음 (정상) |
| 한투 모의투자 서버 | 미사용 |

**대시보드 손익 0% 원인**: `_write_live_status()` 에서 `live_status 저장 실패: 'TradingBot' object has no attribute 'mode'` 에러가 하루 종일 발생 → `live_status_KR.json` / `live_status_US.json` 파일이 비어 있음 (0 bytes). 봇 재시작 후 해소 예정 (코드는 이미 수정됨).

*Last updated: 2026-03-25*
*Context session: Bear DEFENSIVE 고착 수정 + KR 후보 부족 폴백 추가 + 모의투자 구조 분석*

---

## [2026-03-26] KIS API 전면 정비 + 텔레그램 watchlist 알림

### 1. KIS API 실거래 정합성 수정 (`kis_api.py`)

#### 해외주식 주문 — `ORD_SVR_DVSN_CD: "0"` 필드 추가
- **문제**: US 모의투자 주문 시 `IGW00036` 에러 발생
- **원인**: 공식 문서 필수 파라미터 `ORD_SVR_DVSN_CD` 누락
- **수정**: `_place_order_us()` body에 `"ORD_SVR_DVSN_CD": "0"` 추가
- **결과**: NVDA 주문 `order_no: 0000033495` 성공 확인

#### 국내주식 주문 — 구TR → 신TR 교체
- **변경 전**: `VTTC0802U` / `VTTC0801U` (구TR)
- **변경 후**: `VTTC0012U` (매수) / `VTTC0011U` (매도)
- **실거래**: `TTTC0012U` / `TTTC0011U`
- **왜**: KIS 문서에서 신TR 확인 후 교체. 구TR은 deprecated 가능성.

#### `_get_ovrs_excg_cd` ValueError 제거
- **변경 전**: 거래소 맵에 없는 종목(SRPT/CORT 등) → `ValueError` 발생 → 주문 실패
- **변경 후**: 맵에 없으면 `"NASD"` 기본값 반환
```python
def _get_ovrs_excg_cd(ticker: str) -> str:
    for exch, tickers in _US_EXCHANGE_MAP.items():
        if ticker.upper() in tickers:
            return exch
    return "NASD"   # 기본값: 나스닥
```

#### 해외주식 잔고 조회 — 문서 기반 재구현 (`VTTS3012R`)
- 공식 문서 파라미터 기준으로 `_get_balance_us()` 구현
- `get_balance(market="US")` → `_get_balance_us()` 호출

### 2. 가짜 시뮬 포지션 초기화 + `_verify_live_positions` KR+US 동시 검증 (`trading_bot.py`)
- SRPT/PAYS/CDLX 포지션이 내부에만 존재(한투 서버에 없음) → max_positions 한도 채워 모든 신규 매수 차단
- `_verify_live_positions()` 를 KR + US 동시 검증으로 개선
  - `get_balance(market="KR")` + `get_balance(market="US")` 각각 호출
  - 브로커 잔고에 없는 포지션 자동 제거

### 3. ENV 변수 분리 (`.env` / `risk_manager.py` / `consensus.py`)
```env
MAX_POSITIONS=10
MAX_PYRAMID=8
SIZE_AGGRESSIVE=100
SIZE_CAUTIOUS_BULL=60
SIZE_NEUTRAL=40
SIZE_CAUTIOUS_BEAR=20
SIZE_DEFENSIVE=10
```
- `risk_manager.py`: `HARD_RULES["max_positions"]`, `["max_pyramid"]` → env 읽기
- `consensus.py`: `_e()` 헬퍼로 모드별 size env 읽기, `CONSENSUS_MAP` 전체 env 반영

### 4. 분석가 포트폴리오 현황 인지 + `suggested_size_pct` (`analysts.py` / `consensus.py`)
- `call_analyst()` / `get_three_judgments()`에 `portfolio_info` 파라미터 추가
- 분석가 프롬프트에 잔고/맥스 주문 규모 컨텍스트 제공
- 분석가 JSON 응답에 `suggested_size_pct` 필드 추가
- `build_consensus()` 에서 분석가 제안 size와 룰 기반 size를 50:50 혼합

### 5. 대시보드 스테일 데이터 제거 (`dashboard_server.py` / `trading_bot.py`)
- **문제**: 봇 재시작 후에도 이전 세션 rejection 이유("max positions 3" 등)가 대시보드에 남음
- **수정**:
  - `main()` 기동 시 `session_start` 이벤트를 KR/US 각각 analysis_log에 기록
  - `dashboard_server.py`에서 `session_start` 이벤트 감지 시 모든 누적 데이터 초기화
    - `ticker_last`, `ticker_sig_count`, `ticker_skip_reasons`, `selection_reasons`, `candidates_list` 전부 clear

### 6. 텔레그램 watchlist 알림 (`telegram_reporter.py` / `trading_bot.py`)
- `watchlist_alert()` 함수 신규 추가
  - trigger: `session_open` | `rescreen` | `reuse`
  - 선택 종목 + 제외 종목 + 선택 이유(reasons dict) 포함
- `select_tickers()` 반환값 `list` → `(tickers, reasons)` 튜플로 변경
- `trading_bot.py` 4개 호출부 전부 튜플 언패킹 + `watchlist_alert` 호출로 업데이트
  | 경로 | trigger |
  |------|---------|
  | session_open 신규 판단 | `session_open` |
  | 재스크리닝 (판단 재사용) | `rescreen` |
  | 튜너 모드 변경 | `rescreen` |
  | analyst_reinvoke 재선택 | `rescreen` |

---

*Last updated: 2026-03-26*
*Context session: KIS API 정합성 수정 + ENV 분리 + 분석가 포트폴리오 인지 + watchlist 텔레그램 알림*
---

## [2026-03-26] KIS 브로커 포지션 기준 대시보드 반영 + 미국 주문 경로 재점검

### 1. 대시보드 보유 포지션을 `live_status` 대신 KIS 직접조회 우선으로 변경
- 문제:
  - 대시보드 `/api/summary` 가 `state/live_status_{market}.json` 의 `positions` 를 그대로 표시
  - 코드 밖에서 테스트 주문을 넣거나 내부 포지션과 브로커 잔고가 어긋나면 실제 한투 앱과 다른 포지션이 노출됨
- 수정:
  - `dashboard/dashboard_server.py`
  - `_load_broker_positions(market)` 추가
  - `get_access_token()` + `get_balance(market)` 로 브로커 `stocks` 를 직접 읽어 `positions` 로 변환
  - 브로커 조회 성공 시 `today.positions` / `position_count` 는 KIS 잔고 기준
  - 조회 실패 시에만 기존 `live_status.positions` 로 폴백
- 검증:
  - `/api/summary?market=US` 실조회 결과 `position_count: 0`, `positions: []` 확인

### 2. 미국 모의주문 매수/매도 실제 테스트로 문서 기준 재검증
- 테스트 결과:
  - `NVDA` 1주 모의 매수 성공
  - `NVDA` 1주 모의 매도도 문서 기준 조합으로 성공
- 핵심 정정:
  - 모의 미국 매수 TR ID: `VTTT1002U`
  - 모의 미국 매도 TR ID: `VTTT1001U`
  - 실전 미국 매도 TR ID: `TTTT1006U`
- 추가 반영:
  - 해외 주문 body 에 `CTAC_TLNO`, `MGCO_APTM_ODNO` 추가
  - `SLL_TYPE` 는 매수 `""`, 매도 `"00"` 유지
- 결론:
  - 기존 `500` 일부는 잘못된 바디 조합 영향이 있었고
  - 문서 기준 조합으로는 미국 모의 매도도 정상 응답 확인

### 3. 국내 주문 바디를 문서 기준으로 보강
- `kis_api.py` `order-cash`
- 매도 주문 시 `SLL_TYPE: "01"` 명시
- 국내 주문 TR 은 이미 신TR 기준 유지
  - 모의 매수 `VTTC0012U`
  - 모의 매도 `VTTC0011U`
  - 실전 매수 `TTTC0012U`
  - 실전 매도 `TTTC0011U`

### 4. 미국 거래소 코드 자동 판별 추가
- 문제:
  - `SRPT/CORT/PAYS/BRZE/ARM` 등 스크리닝 후보가 `_US_EXCHANGE_MAP` 에 없으면 `ValueError`
  - KIS 미국 현재가/기간시세/주문이 모두 거래소 미정의로 막힘
- 수정:
  - `_US_QUOTE_CODE_MAP`, `_US_EXCHANGE_CACHE` 추가
  - `_probe_us_exchange_code(ticker, token)` 추가
  - KIS 해외 현재가 API를 `NAS/NYS/AMS` 순으로 호출해 가격이 뜨는 거래소를 자동 판별
  - `_get_ovrs_excg_cd(ticker, token=None)` 가 캐시/하드코딩/자동판별 순으로 동작
  - `_get_price_us_kis`, `_daily_ohlcv_us_kis`, `_place_order_us` 모두 token 기반 자동 판별 사용
  - 즉시 매핑에도 `SRPT/CORT/PAYS/BRZE/ARM` 를 `NASD` 로 추가
- 실확인:
  - `SRPT NASD`, `CORT NASD`, `PAYS NASD`, `BRZE NASD`, `ARM NASD` 해석 성공

### 5. 미국 VTS 종목별 주문 실패 캐시 추가
- 문제:
  - 같은 미국 종목이 VTS `500` 을 내도 세션 동안 매 사이클 재시도
  - 로그 오염 + 불필요한 주문 재시도 반복
- 수정:
  - `trading_bot.py`
  - `_us_order_supported`, `_us_order_blocked` 세션 캐시 추가
  - US 세션 시작 시 캐시 초기화
  - 미국 모의 신규 매수에서 주문 예외/거절 발생 시 해당 티커를 당일 차단
  - 같은 세션에서는 `entry_skip` (`us_order_blocked`) 로 바로 건너뜀
  - 성공 티커는 지원 캐시로 기록
- 범위:
  - 신규 진입만 차단
  - 기존 보유 포지션 청산 경로는 막지 않음

### 6. 미국 종목 실주문 가능 여부 확인
- `BRZE`: 모의 매수 성공
- `PAYS`: VTS 주문 `500`
- `CORT`: VTS 주문 `500`
- 해석:
  - 거래소 미정의 문제는 해결됨
  - 다만 문서 경고대로 VTS 는 일부 미국 종목만 매매 가능
  - 따라서 종목별 주문 가능 여부 캐시가 필요했고, 위 5번으로 반영

### 7. 미국 현재가 OHLC 보정 유지
- KIS 미국 현재가 응답에서 `open/high/low` 가 비정상(`price` 와 동일)인 경우
- 당일 `dailyprice` 마지막 행으로 O/H/L 보정
- 일부 종목은 VTS `dailyprice` 가 `500` 을 내므로 보정 실패 로그만 남기고 현재가 자체는 유지

### 검증 요약
- `python -m py_compile E:\code\claudetrade\dashboard\dashboard_server.py E:\code\claudetrade\kis_api.py E:\code\claudetrade\trading_bot.py`
- 미국 모의 주문 실테스트:
  - `NVDA` 매수 성공
  - `NVDA` 매도 성공
  - `BRZE` 매수 성공
  - `PAYS`, `CORT` 는 VTS `500` 재현
---

## [2026-03-27] 주문체결조회 기반 가격 정합성 보강

### 1. 보유 포지션 표시 가격을 브로커 기준으로 통일
- `trading_bot.py`
- 장중 브로커 동기화 시 내부 포지션의 표시용 가격을 `display_avg_price`, `display_current_price`, `display_currency`로 별도 유지
- `price_source`를 넣어 `broker_balance` / `order_fill` 출처를 구분
- `live_status_{market}.json`도 표시용 가격과 통화 정보를 같이 기록
- 효과:
  - 대시보드와 텔레그램이 예전 내부 원화 진입가 대신 브로커 잔고 기준 가격을 우선 표시
  - US는 달러, KR은 원화로 분리 표시

### 2. pending 주문 체결 확인 시 매수 원장 buy leg 복구
- `trading_bot.py`
- pending 주문이 잔고로 확인되면 `risk.trade_log` / `all_trade_log`에 `buy` 이벤트를 다시 기록
- 기록 필드:
  - `order_no`
  - `price_source`
  - `currency`
  - `display_price`
  - `fill_time`
- 효과:
  - 매매원장에 매수 leg가 다시 나타남
  - 매수 가격의 출처를 후속 분석에서 추적 가능

### 3. 국내 주문체결조회 연결
- `kis_api.py`
- 추가 함수:
  - `inquire_daily_ccld_kr()`
  - `get_order_fill_kr()`
- 사용 API:
  - `/uapi/domestic-stock/v1/trading/inquire-daily-ccld`
  - TR ID: `VTTC8001R` / `TTTC8001R`
- pending 주문이 잔고로 확인될 때 `order_no` 기준으로 같은 날 체결내역을 다시 조회
- 체결가가 있으면:
  - 포지션 진입가
  - 매매원장 매수가
  - 텔레그램 체결 확인 메시지
  에 `filled_price_native`를 우선 반영

### 4. 해외 주문체결조회 연결
- `kis_api.py`
- 추가 함수:
  - `inquire_ccnl_us()`
  - `get_order_fill_us()`
- 사용 API:
  - `/uapi/overseas-stock/v1/trading/inquire-ccnl`
  - TR ID: `VTTS3035R` / `TTTS3035R`
- 모의투자는 주문번호 직접 조건 제한이 있어 날짜 구간 조회 후 `order_no/ticker` 후처리 매칭
- pending US 주문도 체결단가/체결시각이 확인되면 `filled_price_native`, `fill_time`으로 보존

### 5. 화면 반영
- `dashboard/dashboard_server.py`
- 매매원장 가격은 `display_price` 우선 사용
- 메타 표시 추가:
  - `브로커평균단가`
  - `체결조회단가`
  - `주문번호`
  - `체결시각`
- `telegram_reporter.py`
- 최신 `status_report()` / `dashboard_push()`를 파일 하단에서 재정의
- 보유 포지션 가격을 KR=원화, US=달러로 표시

### 6. 현재 상태
- KR:
  - 주문번호 기준 체결조회 연결됨
  - 체결조회가 성공하면 `price_source=order_fill`
- US:
  - 주문체결조회 경로 연결됨
  - 모의투자 제약 때문에 일부 종목/일자에서는 후처리 매칭에 의존
- 공통:
  - 미체결 주문은 주문단가
  - 보유 포지션은 브로커 확인 가격
  - 체결조회 성공 건은 체결단가를 우선 유지

### 7. 검증
- `python -m py_compile E:\code\claudetrade\kis_api.py E:\code\claudetrade\trading_bot.py E:\code\claudetrade\dashboard\dashboard_server.py E:\code\claudetrade\telegram_reporter.py`

### 8. 남은 점
- 해외 체결조회는 공식 스펙상 모의투자 검색 조건 제약이 있어, 실운영 로그에서 `order_fill`로 안정적으로 매칭되는지 계속 확인 필요
- 장기적으로는 KIS 주문체결조회 응답 원문을 별도 디버그 로그로 1회 남겨 필드명을 더 좁히는 것이 좋음

### 9. 미체결 주문 중복 적재 방지
- 증상:
  - 대시보드 `미체결 주문`에 `OLPX`, `NAVN` 같은 동일 티커가 여러 주문번호로 반복 노출
  - 실제 원인은 같은 티커가 아직 `pending_orders`에 남아 있는데 다음 사이클에서 다시 주문되던 구조
- 원인:
  - `risk.can_open()`은 보유 포지션만 보고 `pending_orders`는 검사하지 않음
  - 따라서 미체결 상태에서는 `already_holding`에 걸리지 않아 재주문 가능
- 수정:
  - `trading_bot.py`
  - `_has_pending_order(ticker, market)` 추가
  - 진입 직전 같은 시장/같은 티커의 미체결 주문이 있으면 `entry_skip(reason="pending_order")`
  - `_normalize_pending_orders()` 추가
    - 전일 pending 제거
    - 동일 시장/동일 티커 pending은 최신 1건만 유지
  - `_save_pending_orders()` / `_restore_pending_orders()`에서 정규화 실행
  - `_add_pending_order()`도 동일 티커 기존 pending을 교체 후 최신 주문만 유지
- 추가 정리:
  - `state/pending_orders.json`에 남아 있던 전일 KR stale pending (`038110`) 3건 제거
- 기대 효과:
  - 같은 종목이 미체결 상태로 대시보드에 줄줄이 쌓이는 문제 방지
  - `미체결 주문` 패널이 현재 유효 주문만 보여줌

### 10. 재시작 직후 중복매수 / 0가 청산 / 잔고없는 매도 반복 방지
- 증상:
  - 재시작 후 `US` 세션이 `포지션:0개`로 시작하면서 브로커에 이미 있던 보유 종목을 다시 매수
  - 예:
    - `OLPX 140주 -> 1540주 -> 1680주 -> 1820주`
    - `NDLS 30주 -> 205주 -> 264주 -> 294주`
  - `SLND 169@0` 같은 0가 내부 청산 발생
  - `LOVE`에 대해 `모의투자 잔고내역이 없습니다` 매도 실패 반복
- 원인:
  - `session_open()`에서 `session_open_reset`으로 당일 pending 주문을 먼저 정리
  - 재시작 직후 브로커 실제 보유는 있었지만 내부 런타임 포지션이 비어 있는 상태로 시작
  - `_sync_runtime_with_broker()`는 기존 포지션 보정은 했지만, 브로커에만 있고 내부에 없는 포지션을 새로 주입하지 못함
  - 매도 경로는 `exit_price` 또는 `raw_price`가 0이어도 내부 청산이 진행될 수 있었음
- 수정:
  - `trading_bot.py`
  - `session_open()`에서 더 이상 같은 날 pending 주문을 `session_open_reset`으로 삭제하지 않음
  - 대신 pending 정규화만 수행
  - `_sync_runtime_with_broker()`에 브로커 보유 포지션 주입 로직 추가
    - `KR`/`US` 모두 내부에 없는 브로커 보유를 런타임 포지션으로 생성
    - pending/order_fill 메타가 있으면 같이 이어받음
  - `_execute_sell()`에서
    - `exit_price <= 0` 또는 `raw_price <= 0` 이면 매도 자체 스킵
    - `잔고내역이 없습니다` 실패 시 내부 포지션 제거 + 저장/라이브상태 갱신
- 기대 효과:
  - 재시작 직후 브로커 기존 보유를 먼저 인식하므로 같은 종목 재매수 방지
  - `0원 청산 -> -100%` 같은 내부 손익 오염 방지
  - 브로커 미보유 종목에 대한 매도 실패 반복 감소

### 11. 오늘 손익 퍼센트/원화 불일치 보정
- 증상:
  - 대시보드 `오늘 손익`이 `+55.77% / -1,716,210원`처럼 서로 양립할 수 없는 값으로 표시
  - `live_status_US.json`에는 동일 티커가 중복으로 들어가 있었음
    - `NDLS` 2개
    - `OLPX` 2개
    - `KOD` 2개
- 원인:
  - `daily_pnl_pct`는 `risk.equity()` 기반 `daily_return()`을 사용
  - 오늘 새벽 중복 포지션/재매수/0가 청산 영향으로 `equity()`가 오염됨
  - 반면 `daily_pnl`은 매도 이벤트 기반 누적이라 음수로 남아 `%`와 원화가 서로 다른 경로로 틀어짐
  - `live_status` 저장 시점에도 중복 포지션이 그대로 기록되어 대시보드 표시를 추가로 왜곡
- 수정:
  - `trading_bot.py`
  - `_daily_pnl_pct()` 추가
    - `daily_pnl / session_start_equity * 100` 기준으로 퍼센트 계산
  - `_sync_runtime_with_broker()` 결과를 티커별 1건으로 병합
    - 중복 포지션 발견 시 `중복 포지션 병합` 로그 기록
  - `_write_live_status()`에서도 저장 전에 티커별 dedupe 수행
  - `position_count`도 dedupe 이후 개수 기준으로 저장
- 수동 정리:
  - `state/live_status_US.json`
  - `daily_pnl_pct`를 `+55.7697%` → `-3.6432%`로 교정
  - `positions`를 중복 8건 → 고유 5건으로 정리
- 결과:
  - `%` 손익과 원화 손익이 최소한 같은 방향으로 맞춰짐
  - 대시보드 포지션 카드도 고유 종목 기준으로 보이게 수정
- 주의:
  - 오늘 새벽 오염된 거래 흐름 자체가 있었기 때문에 `2026-03-27` 원화 손익값은 여전히 완전한 진실값으로 단정하기 어려움
  - 이번 수정은 “표시값이 명백히 충돌하는 상태”를 우선 해소한 것

---

## [2026-03-27] brain.json 학습 루프 복구

### 배경
봇 운영 6일차에도 `brain.json`의 `learned_lessons = []`, `correction_guide = {}` — postmortem이 매번 `except`로 빠져 학습이 전혀 안 되고 있었음.

### 원인 분석 (3중 버그)

| # | 원인 | 증상 |
|---|------|------|
| 1 | `max_tokens=800` 부족 | 한국어 응답이 길어 JSON이 중간에 잘림 → 파싱 실패 |
| 2 | JSON 파싱 로직 취약 | `split("```")[1]` → 중첩 `{}` 있으면 첫 `}`에서 잘림 |
| 3 | 거래 없는 날 프롬프트 과중 | 빈 거래 내역에도 15개 필드 요구 → 응답 길어짐 |

### 수정 내용 (`minority_report/postmortem.py`)

#### 1. `_extract_json()` 함수 신규 추가 (취약 파싱 대체)
```python
def _extract_json(text: str) -> dict:
    def _fix(s): return re.sub(r",(\s*[}\]])", r"\1", s)  # trailing comma 제거
    # 1) ```json...``` 블록 (탐욕적 매칭으로 중첩 {} 포함)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m: return json.loads(_fix(m.group(1)))
    # 2) 첫 { 부터 마지막 } 까지 직접 추출
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(_fix(text[start:end+1]))
    raise ValueError(...)
```
- 동일 함수를 `analysts.py` 3개 파싱 위치에도 적용

#### 2. `max_tokens` 증가
- `800` → `2500` (한국어 응답 토큰 여유 확보)

#### 3. 거래 없는 날 간소 프롬프트 분기
```python
if not sells:   # 매도 체결 없는 날
    prompt = ...  # 판단 적중 + 보정 지침만 (필드 간소화)
else:
    prompt = ...  # 거래 있는 날 전체 분석
```
- 모든 문자열 값 "20~30자 이내" 지시 추가

#### 4. `new_lesson` fallback 저장
```python
lesson_to_save = bu.get("new_lesson") or pm.get("key_lesson")
if lesson_to_save not in ("오류로 자동 판정", "HALT 세션 — 거래 없음"):
    BrainDB.update_beliefs(market, {"new_lesson": lesson_to_save})
```

#### 5. `brain_summary[:300]` 잘림 제거
- postmortem Claude가 과거 교훈 없이 판단하던 문제 해결

### `brain_backfill.py` 신규 생성
기존 `logs/daily_judgment/202603*.json` 파일로 brain 소급 갱신 스크립트.

```
python brain_backfill.py           # 실행
python brain_backfill.py --dry-run # 대상 확인만
```

### 검증 결과 (`state/brain.json`)
```
KR learned_lessons (6개):
  - 저거래량 골든크로스 과신 금지
  - USD/KRW 1450 초과시 Bear단독 거부권 부여
  - 코스피-6%급락익일반등확률높음
  - 환율1500근접시도반등장존재
  - 하락장에서변동성돌파매도전략은수익가능
  - 시장-3%에서도 변동성돌파매도로+수익 가능

market_regime: 변동성장
correction_guide: MACD데드크로스+환율1500초과시 Bull신호 완전무시 (내일 분석가에게 전달됨)
analyst_performance: Bull 16% / Bear 70% / Neutral 5% (37일)
```

### 미해결 — US session_close 미실행
- US `logs/daily_judgment/202603*_US.json` 전부 `actual_result` 없음
- 봇이 US 장 종료(05:00) 전에 재시작되어 `session_close`가 호출 안 됨
- 다음 확인 필요: 봇 재시작 시 이전 세션 session_close 트리거 여부

---

## [2026-03-27] 중복 매수 버그 수정 — `_verify_live_positions` / `_sync_runtime_with_broker`

### 증상
- OLPX 140주 → 1820주, NDLS 30주 → 294주 (봇 재시작마다 재매수 반복)
- pending_orders 초기화로 US 포지션 추적 불가

### 근본 원인
`_verify_live_positions`와 `_sync_runtime_with_broker` 두 곳 모두 동일한 버그:

```python
# 버그: US API 실패 시 broker_us = {} → 모든 US 포지션이 "브로커에 없음"으로 제거됨
broker_us = {}
try:
    ...
except:
    pass  # us_ok 플래그 없음

for pos in self.risk.positions:
    broker_pos = broker_us.get(key)  # 항상 None
    if not broker_pos:
        removed.append(ticker)  # US 포지션 전부 삭제
```

### 수정 (`trading_bot.py`)

**① `_verify_live_positions`** — `kr_ok` / `us_ok` 플래그 추가
```python
kr_ok = False
us_ok = False
try:
    ...
    kr_ok = True
except: ...

try:
    ...
    us_ok = True
except: ...

# 해당 마켓 조회 실패 시 포지션 제거 금지
if market == "US" and not us_ok:
    verified.append(pos)
    continue
if market == "KR" and not kr_ok:
    verified.append(pos)
    continue
```

**② `_sync_runtime_with_broker`** — 동일 패턴으로 `us_ok` 추가
```python
broker_us: dict = {}
us_ok = False
try:
    ...
    us_ok = True
except: ...

# 루프 내
if market == "US" and not us_ok:
    synced_positions[(market, key)] = pos  # 그대로 유지
    continue
```

### 사용자가 이미 추가한 부분 (이번 세션 검토 완료)
- `pending_orders.json` 영속성: `_restore_pending_orders()` + `_save_pending_orders()`
- `_normalize_pending_orders()`: 전날 주문 폐기, 종목별 최신 1건 유지
- `_has_pending_order()` 매수 전 중복 체크 (이미 존재)
- `_reconcile_pending_orders()`: `broker_pos = None`이면 `remaining`으로 유지 (안전)

### 남은 미해결 이슈
- **현금 47M 고정**: KIS 모의투자가 US 매수 후 KR 현금 즉시 반영 안 함 → KIS API 한계
- **LOVE 매도 실패**: `모의투자 잔고내역이 없습니다.` → KIS에 해당 종목 없음, 수동 정리 필요
- **US session_close 미실행**: 봇 재시작으로 session_close 미호출 → US brain 학습 누락

---

*Last updated: 2026-03-27*
*Context session: 중복 매수 버그 루트 코즈 분석 + _verify_live_positions / _sync_runtime_with_broker 수정*

---

## [2026-03-27] CRITICAL BUG FIX — SLND price=0 강제 stop_loss

### 증상 (2026-03-27 01:06, 01:32 로그)
- `[PAPER SELL] SLND 169@0` → `-415,882 (-100.00%)`
- `[PAPER SELL] SLND 511@0` → `-1,259,794 (-100.00%)`
- HALT 발동: `daily loss limit reached (-32.49%)`

### 근본 원인
`run_cycle`에서 `get_price(SLND)` 가 `price=0`을 반환하거나 실패했을 때,
`price_cache_raw["SLND"] = 0`과 `price_cache["SLND"] = 0`이 그대로 기록됨.
이후 `risk.update_prices()` → `pos["current_price"] = 0` → `0 <= pos["sl"]` → `reason="stop_loss"`.
`_execute_sell` 호출 → 0원에 전량 매도.

### 수정 (trading_bot.py)

**1. `run_cycle` 가격 업데이트 (lines ~1518)**
```python
# 수정 전
self.price_cache_raw[ticker] = price
self.price_cache[ticker] = risk_price
self.risk.update_prices(self.price_cache)
self._process_exit_candidates()

# 수정 후
if price <= 0:
    log.warning(f"[skip {market}] {ticker} invalid price={price} — price_cache 업데이트 생략")
    continue
self.price_cache_raw[ticker] = price
self.price_cache[ticker] = risk_price
self.risk.update_prices(self.price_cache)
self._process_exit_candidates()
```

**2. `_on_tick` WebSocket 핸들러 (lines ~1441)**
```python
# 수정 후
if not raw_price or raw_price <= 0:
    log.warning(f"[WS tick] {ticker} invalid price={raw_price} — 무시")
    return
```

**3. `_process_exit_candidates` 2중 방어 (lines ~1004)**
```python
# 수정 후 — 첫 번째 체크
for cand in candidates:
    if float(cand.get("exit_price") or 0) <= 0:
        log.error(f"[exit skip] {cand['ticker']} exit_price={cand.get('exit_price')} <= 0 — 매도 차단")
        continue
```

### 방어 레이어 (3중)
1. `run_cycle`: `price<=0`이면 `price_cache` 업데이트 자체를 건너뜀
2. `_on_tick`: WS 틱에서 `price<=0`이면 즉시 return
3. `_process_exit_candidates`: `exit_price<=0`이면 매도 시도 자체를 차단
4. `_execute_sell`: `exit_price<=0` 또는 `raw_px<=0`이면 return (기존 코드 유지)
## [2026-03-27] 대시보드 기간별 성과 / 매매원장 복구

### 문제
- `기간별 성과`는 숫자가 비거나 과거 오염된 `actual_result`를 그대로 읽고 있었음
- `매매원장`은 `daily_judgment.trades`가 KR/US 혼입으로 깨진 날짜에서 아무 행도 안 나왔음
- 전일 `live_status`가 오늘 요약 카드까지 덮어쓰는 경우가 있었음

### 수정 (`dashboard/dashboard_server.py`)
- `_parse_trade_log_lines()` 추가
  - `logs/system/trading_YYYYMMDD.log`에서
  - `[PAPER BUY]`
  - `[PAPER SELL]`
  - 뒤이은 `close_position` 손익 로그
  를 읽어 보조 원장 행으로 복구
- `_trades_for_record()` 추가
  - 정상 `daily_judgment.trades`가 있으면 우선 사용
  - 비어 있거나 시장 필터 후 남는 게 없으면 시스템 로그 복구행 사용
- `_record_metrics()` 추가
  - 로그 복구된 `sell` 행 기준으로 일자별 `trades / pnl_krw / pnl_pct / win` 재계산
  - `api/stats/period`, `api/history/monthly`, `api/history/equity`, summary period 집계가 이 메트릭을 사용하도록 변경
- `0원 매도`는 로그 복구 대상에서 제외
  - 예전 `SLND 0가 청산` 같은 손상 행을 대시보드 원장에 다시 노출하지 않도록 방어
- `_is_fresh_live_status()` 강화
  - `trading_date == today_rec.date == 오늘 날짜`일 때만 live status를 요약 카드에 반영

### 확인 결과
- `KR 매매원장` 복구
  - `038110` 3건 표시
- `KR 기간별 성과` 복구
  - `/api/stats/period?market=KR&period=all` 기준 `trades=3`, `total_pnl=0.44`
- `US 기간별 성과`도 로그 기준 재집계
  - `/api/stats/period?market=US&period=all` 기준 `trades=19`, `total_pnl=1.6`
- 전일 `live_status_KR.json`이 오늘 카드에 덮어쓰는 현상 제거

### 한계
- 이 단계는 `대시보드 복구` 중심
- 손상된 `daily_judgment/*.json` 원본 자체를 재작성한 것은 아님
- 따라서 장기적으로는 손상일자 원본 로그 재생성 또는 저장 단계 추가 보정이 여전히 필요

## [2026-03-27] KIS 주문 사전체크 / 전역 레이트리밋 / 주문 응답 정규화

### 배경
- `kis-agent` 참고 포인트를 검토하던 중, 지금 코드에는
  - 주문 전 사전체크
  - 전역 KIS 요청 속도 제어
  - KR/US 주문 응답의 공통 정규화
  가 부족하다고 판단
- 특히 주문 실패를 API 응답 이후에만 처리하고 있었고, KIS 호출도 함수별로 흩어져 있었음

### 수정 (`kis_api.py`, `trading_bot.py`)
- `_rate_limit_wait()`, `_kis_get()`, `_kis_post()` 추가
  - `KIS_RATE_RPS` 환경변수 기준으로 KIS REST 호출 간 최소 간격 강제
  - 토큰/시세/잔고/주문 등 KIS 경로가 전역 래퍼를 타도록 정리 시작
- `precheck_order()` 추가
  - `KR 매수`: 현금 기준 가용 수량/주문 가능 여부 확인
  - `KR 매도`: 보유 수량 기준 주문 가능 여부 확인
  - `US 매도`: 해외 잔고 기준 보유 수량 확인
  - `US 매수`: 통합계좌 구조를 감안해 KR 원화 가용현금 기준 1차 방어
- `_normalize_order_result()` 추가
  - KR/US 주문 응답을 공통 포맷으로 반환
  - `success`, `msg`, `order_no`, `market`, `side`, `ticker`, `qty`, `price`, `price_type`, `raw`
- `trading_bot.py` 매수/매도 경로에 `precheck_order()` 연결
  - 사전체크 실패 시 주문 API 호출 전에 스킵
  - 매도 사전체크에서 `insufficient_holding`이면 내부 stale 포지션 정리까지 수행

### 효과
- 주문 불가 케이스를 KIS 주문 API 호출 전에 더 일찍 차단
- KR/US 주문 결과 형식이 통일돼 후속 처리와 로그 해석이 쉬워짐
- KIS REST 호출이 전역 속도 제어를 타기 시작해서 급격한 연속 호출 리스크 완화

### 확인
- `python -m py_compile E:\\code\\claudetrade\\kis_api.py E:\\code\\claudetrade\\trading_bot.py E:\\code\\claudetrade\\dashboard\\dashboard_server.py` 통과

### 아직 안 한 것
- 국내 `주식정정취소가능주문조회`
- 국내/해외 정정·취소 주문 API
- 주문 가능/정정 가능 수량을 이용한 자동 정정·취소 루프

### 메모
- 현재 텔레그램/대시보드의 값은 `전부 KIS만` 보는 구조는 아님
- 보유 포지션은 KIS 잔고 직접조회 우선
- 총자산은 KIS 동기화값 사용
- 현금/일부 라이브 상태는 아직 런타임 값이 섞여 있음

## [2026-03-27] stale 잔고 캐시로 인한 매도 precheck 오판 방어

### 문제
- 매수 직후 짧은 시간 안에 TP/SL이 발동하면 `sell precheck`가 `get_balance()` 캐시를 탈 수 있음
- `KIS_CACHE_TTL_SEC=120` 구간에서 아직 매수 전 잔고가 남아 있으면
  - `held_qty=0`
  - `insufficient_holding`
  으로 잘못 판단 가능
- 기존 코드는 이 경우 내부 포지션을 즉시 제거하고 있었음

### 수정 (`kis_api.py`, `trading_bot.py`)
- `get_balance(..., force_refresh=False)` 추가
  - `force_refresh=True`면 `_BALANCE_CACHE`를 무효화하고 KIS 잔고를 재조회
- `precheck_order(..., force_refresh=False)` 추가
  - 매도 사전체크에서 강제 재조회 경로 사용 가능
- `_execute_sell()` 보강
  - `insufficient_holding`이면 바로 내부 포지션 삭제하지 않음
  - 같은 티커 `pending buy`가 없을 때만 `force_refresh=True`로 잔고 재조회 1회
  - 재조회 후에도 여전히 보유수량 부족일 때만 stale 포지션 정리
  - 같은 티커 `pending buy`가 있으면 브로커 미반영 가능성을 감안해 내부 포지션 제거를 보류

### 효과
- stale 잔고 캐시 한 번 때문에 내부 포지션이 잘못 삭제되는 위험 완화
- 방금 체결 중인 종목을 `보유 없음`으로 오인해서 정리하는 케이스 차단

### 확인
- `python -m py_compile E:\\code\\claudetrade\\kis_api.py E:\\code\\claudetrade\\trading_bot.py` 통과

## [2026-03-28] 대시보드 총자산/크레딧/Claude 타임라인 UI 보강

### 배경
- 오늘 페이지에서 `누적 자산 곡선` 마지막 점이 실시간 브로커 총자산과 어긋날 수 있었음
- `AI 크레딧`은 누적 사용량만 보이고, 사용자가 원하는 `Claude 잔여 크레딧` 관점이 없음
- `Claude 판단 타임라인`이 계속 쌓이면 페이지가 과도하게 길어질 위험이 있었음

### 수정
- `dashboard/dashboard_server.py`
  - `/api/history/equity`에서 오늘 장중 `live_status`가 신선하면 마지막 equity 점을 `KR + US 평가환산` 브로커 총자산으로 보정
  - 오늘 페이지 `Claude 판단 타임라인` 카드를 내부 스크롤 방식으로 변경
  - `AI 크레딧` 카드에 `예산 기준 잔여` 행 추가
  - `/api/credits` 응답의 `budget` 필드를 읽어 일/월 잔여 예산 표시
- `credit_tracker.py`
  - `CLAUDE_DAILY_BUDGET_USD`
  - `CLAUDE_MONTHLY_BUDGET_USD`
  설정값이 있으면 `daily_remaining_usd`, `monthly_remaining_usd` 계산

### 효과
- 누적 자산 곡선의 마지막 점이 오늘 실시간 총자산과 더 가깝게 맞음
- Claude 비용은 단순 사용량뿐 아니라 `설정 예산 대비 잔여` 관점으로도 확인 가능
- Claude 판단 타임라인이 길어져도 오늘 페이지 전체 길이를 과도하게 늘리지 않음

### 참고
- Anthropic 계정의 실제 `남은 크레딧`을 직접 조회하는 API는 현재 프로젝트에 없음
- 따라서 대시보드에 표시하는 잔여값은 `실제 계정 잔액`이 아니라 `.env` 예산 설정 기준 잔여량임

### 확인
- `python -m py_compile E:\\code\\claudetrade\\dashboard\\dashboard_server.py E:\\code\\claudetrade\\credit_tracker.py` 통과

## [2026-03-29] KR Core 유니버스 재편 — SK하이닉스 → 셀트리온

### 배경
- SK하이닉스(000660) Win Rate 43% — KR Core 6종목 중 최저
- 셀트리온(068270) Sharpe 7.08, Win Rate 75% — 동기간 비교우위 명확
- IT 반도체 집중도 완화 + 헬스케어 섹터 다변화 목적

### 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `phase1_trainer/digest_builder.py` | KR_TICKERS `000660 SK하이닉스` → `068270 셀트리온` |
| `phase1_trainer/kr_news_collector.py` | TARGET_CORPS 동일 교체 |
| `trading_bot.py` | `_DEFAULT_KR_TICKERS` 동일 교체 |

### 비변경 파일
- `phase1_trainer/price_collector.py` — 이미 068270 포함된 넓은 수집 풀 (변경 불필요)
- `phase1_trainer/supplement_collector.py` — KR_FLOW_TICKERS 12종목 풀, 000660 유지 (투자자 흐름 참고용)
- `data/price/kr/kr_068270.csv` — 이미 존재 (2024-11-14 ~ 2026-03-27, 331행)

### 확인
- digest_builder KR_TICKERS: 068270 셀트리온 ✓
- kr_news_collector TARGET_CORPS: 068270 셀트리온 ✓
- trading_bot _DEFAULT_KR_TICKERS: 068270 ✓
- 000660 핵심 3파일에서 완전 제거 ✓
