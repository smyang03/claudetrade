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
