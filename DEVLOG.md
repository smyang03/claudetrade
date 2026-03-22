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
