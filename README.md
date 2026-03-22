# claudetrade

Claude AI 기반 KR/US 자동매매 봇.
Bull·Bear·Neutral 3명의 Claude 분석가가 매일 아침 시장을 판단하고,
2라운드 토론으로 합의한 결과로 매매한다.
매 세션 종료 후 실제 결과를 Claude가 스스로 평가해 다음날 판단에 반영한다.

---

## 목차

1. [핵심 설계 철학](#1-핵심-설계-철학)
2. [전체 아키텍처](#2-전체-아키텍처)
3. [일일 데이터 흐름](#3-일일-데이터-흐름)
4. [파일 구조](#4-파일-구조)
5. [AI 판단 시스템 상세](#5-ai-판단-시스템-상세)
6. [학습 누적 구조](#6-학습-누적-구조)
7. [리스크 관리](#7-리스크-관리)
8. [모드 표](#8-모드-표)
9. [알려진 맹점 및 한계](#9-알려진-맹점-및-한계)
10. [초기 설정](#10-초기-설정)
11. [봇 실행](#11-봇-실행)
12. [환경변수](#12-환경변수-env)
13. [텔레그램 명령어](#13-텔레그램-명령어)
14. [자주 쓰는 명령어](#14-자주-쓰는-명령어)

---

## 1. 핵심 설계 철학

### 운영 우선 (Run-First) 접근

과거 데이터로 사전 학습하는 방식을 **제거**했다.
봇이 실제로 paper/live 거래를 돌면서 `Claude 판단 → 실제 결과` 쌍을 매 세션마다 기록한다.
이 데이터가 brain.json에 누적되고, 나중에 fine-tuning이나 프롬프트 개선의 재료가 된다.

```
과거 방식 (폐기):  역사 데이터 사전 수집 → historical_sim → brain.json 학습
현재 방식:         봇 실행 → 매일 Claude 판단 + 실제 결과 → brain.json 점진 누적
```

### 3명 분석가 토론 합의

단순 다수결이 아닌 **2라운드 구조**:
- R1: 각자 독립 판단 (서로 의견 모름)
- R2: 상대 의견 공개 → 설득/유지 선택
- 적중률 기반 가중치로 합의 (10일 이상 데이터 쌓이면 자동 활성화)

### 이상 신호 시 긴급 재판단

튜너가 30분마다 장황 모니터링 → 아래 조건 감지 시 분석가 3명 즉시 재호출:
- 지수 -2% 이상 급락
- REVERSE 권고
- TIGHTEN + 경고 동시 발령

---

## 2. 전체 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│                         매일 반복 루프                              │
│                                                                    │
│  08:50 session_open                                                │
│    ├─ digest 생성 (주가 + 뉴스 + VIX/환율/수급 → 텍스트)             │
│    ├─ R1: Bull/Bear/Neutral 독립 판단 (Claude × 3)                 │
│    ├─ R2: 상대 의견 공개 → 토론 → 의견 변경 또는 유지               │
│    ├─ 합의: 가중 점수 → 모드(AGGRESSIVE~HALT) + 포지션 크기          │
│    ├─ 스크리너 → Claude 종목 선택                                   │
│    └─ 텔레그램 브리핑 (R1 판단 + R2 토론 + 최종 합의)               │
│                                                                    │
│  매 5분 run_cycle                                                  │
│    ├─ 현재가 조회 → TP/SL 체크 → 청산                              │
│    └─ 전략 시그널 → 진입 (갭눌림/모멘텀/평균회귀/변동성돌파)          │
│                                                                    │
│  매 30분 run_tuning                                                │
│    ├─ 지수 등락 + 보유 포지션 → Claude 재분석                       │
│    ├─ 이상 신호 감지 → 긴급 재판단 (분석가 3명 재호출)               │
│    └─ 모드 조정 + SL 갱신                                          │
│                                                                    │
│  16:00 session_close                                               │
│    ├─ 포지션 정리 (당일청산 / 이월)                                  │
│    ├─ postmortem: 아침판단 + 체결내역 → Claude 사후 분석             │
│    ├─ brain.json 업데이트 (분석가 적중률, 전략 성과, 보정 가이드)     │
│    └─ daily_judgment JSON 저장 (파인튜닝 training record)           │
│                                                                    │
│  ↓ 다음날                                                          │
│  brain.json의 교훈이 아침 판단 프롬프트에 자동 반영                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. 일일 데이터 흐름

### 매일 쌓이는 학습 데이터

```
logs/daily_judgment/YYYYMMDD_KR.json   ← 완전한 training record (1일 1파일)
logs/judgment/judgment_YYYYMMDD.jsonl  ← 프롬프트 + 응답 원본 (파인튜닝 직접 재료)
state/brain.json                       ← 누적 통계 (분석가 적중률, 전략 승률, 교훈)
```

### daily_judgment JSON 스키마 (1개 = 1일치 학습 쌍)

```json
{
  "date": "2026-03-22",
  "market": "KR",
  "mode": "paper",

  "digest_prompt": "...",          // INPUT: Claude가 본 시장 데이터

  "round1_judgments": {...},       // R1 독립 판단 (서로 모를 때)
  "debate_changes": [...],         // R2 토론에서 의견 바뀐 분석가 목록
  "judgments": {...},              // 최종 판단 (R2 반영)
  "consensus": {
    "mode": "NEUTRAL",
    "size": 40,
    "weighted_score": -0.05,       // 가중 합산 점수
    "weights": {...}               // 각 분석가 가중치
  },

  "tickers": [...],                // 당일 모니터링 종목

  "actual_result": {               // OUTPUT: 실제 결과
    "market_change": 0.31,
    "pnl_pct": 0.15,
    "win": true,
    "trades": 2
  },

  "trades": [...],                 // 실제 체결 내역 (종목/수량/가격/전략/PnL)
  "session_events": [...],         // 튜닝/긴급재판단 전체 이력

  "postmortem": {                  // EVALUATION: Claude 사후 평가
    "bull_result": "MISS",
    "bear_result": "HIT",
    "key_lesson": "환율 영향 과소평가",
    "best_trade": "...",
    "worst_trade": "...",
    "worst_trade_reason": "..."
  }
}
```

### 학습 데이터가 다음날 판단에 반영되는 경로

```
postmortem 완료
  → brain.json 업데이트
      ├─ analyst_performance: bull/bear/neutral 적중률 갱신
      ├─ mode_performance: 모드별 평균 PnL/승률 갱신
      ├─ strategy_performance: 전략별 성과 갱신
      ├─ correction_guide: 내일 Claude에게 전달할 보정 지침
      └─ current_beliefs.learned_lessons: 누적 교훈

  다음날 session_open
  → generate_prompt_summary()로 brain 요약 → 판단 프롬프트에 삽입
  → correction_guide → 분석가 프롬프트에 삽입
```

---

## 4. 파일 구조

```
claudetrade/
├── trading_bot.py              # 메인 — 스케줄러, 세션 관리, 긴급 재판단
├── kis_api.py                  # KIS(KR) + AlphaVantage/yfinance(US) + 환율 자동조회
├── risk_manager.py             # 포지션 관리, TP/SL, HARD_RULES
├── indicators.py               # 기술적 지표 (MA, BB, RSI, ATR, 거래량비율 등)
├── telegram_reporter.py        # 모든 텔레그램 알림 (브리핑/체결/결산/긴급재판단)
├── logger.py                   # 로그 설정 (JSONL 포함)
├── credit_tracker.py           # Claude API 비용 추적
├── update_data.py              # 일일 데이터 최신화 (Windows 스케줄러용)
├── runtime_paths.py            # 런타임 경로 해석
│
├── minority_report/
│   ├── analysts.py             # Bull·Bear·Neutral 2라운드 판단 + 종목 선택
│   ├── consensus.py            # 가중 점수 합의 + 마이너리티 룰
│   ├── tuner.py                # 장중 30분 재검토
│   └── postmortem.py           # 장마감 사후 분석 + 전략별 성과 집계
│
├── strategy/
│   ├── momentum.py             # 모멘텀 (KR) — MA정배열 + MACD골든크로스 + 거래량
│   ├── mean_reversion.py       # 평균회귀 (KR) — RSI과매도 + BB하단
│   ├── gap_pullback.py         # 갭+눌림 (KR) — 갭 1% + 거래량 1.5배 + 반등
│   └── volatility_breakout.py  # 변동성돌파 (US) — (시가 + 전일범위×k)
│
├── claude_memory/
│   ├── brain.py                # brain.json 읽기/쓰기/요약 생성
│   └── brain.json              # 초기값 (실제 누적은 state/brain.json)
│
├── phase1_trainer/             # 데이터 수집 도구 (학습은 더 이상 사용 안 함)
│   ├── price_collector.py      # 주가 CSV 수집 (KIS + yfinance 폴백)
│   ├── kr_news_collector.py    # KR 뉴스/공시
│   ├── us_news_collector.py    # US 뉴스
│   ├── supplement_collector.py # VIX, 환율, 외국인수급
│   ├── digest_builder.py       # 수집 데이터 → Claude 입력 텍스트
│   └── historical_sim.py       # (비권장) 과거 시뮬레이션 — 현재는 사용 안 함
│
├── data/
│   ├── price/kr/               # KR 일봉 CSV
│   ├── price/us/               # US 일봉 CSV
│   ├── news/                   # 뉴스 JSON (날짜별)
│   └── supplement/             # VIX, 환율, 수급 JSON
│
├── state/                      # 런타임 상태 (brain.json, 토큰, 포지션)
├── logs/
│   ├── daily_judgment/         # training record (YYYYMMDD_KR.json)
│   ├── judgment/               # JSONL 원본 로그 (프롬프트+응답)
│   ├── system/                 # 봇 실행 로그
│   └── analysis/               # 신호 분석 로그
│
└── DEVLOG.md                   # 개발 맥락 로그 (Claude 인수인계용)
```

---

## 5. AI 판단 시스템 상세

### 분석가 페르소나

| 분석가 | 성향 | 전문 지표 | 금지 행동 |
|--------|------|-----------|-----------|
| **Bull** | 15년 성장주 모멘텀 트레이더 | RSI 과매도, MACD 골든크로스, 거래량 1.5배+ | 환율/VIX만으로 하락 판단 금지 |
| **Bear** | 헤지펀드 리스크 매니저 | VIX 20+, 환율 1400+, 외국인 순매도 | 단일 긍정 신호로 상승 판단 금지 |
| **Neutral** | 퀀트 통계 분석가 | 통계적 엣지, 변동성 조정 수익 | confidence 0.75 초과 금지 |

### 2라운드 토론 구조

```
R1 (독립 판단)
  Bull:    MODERATE_BULL 78% — "RSI 31.7 과매도 반등 예상"
  Bear:    CAUTIOUS_BEAR 68% — "VIX 23.5 상승, 환율 1504원"
  Neutral: MILD_BULL 60%     — "기술적 신호 우세하나 거래량 부족"

R2 (상대 의견 공개 → 토론)
  Bull:    MILD_BULL로 하향 조정 — "Bear의 VIX 급등 지적 수용"
  Bear:    CAUTIOUS_BEAR 유지   — "환율 구조적 문제 지속"
  Neutral: MILD_BULL 유지

최종 합의: 가중 점수 = Bull×1.0 + Bear×0.8 + Neutral×1.0 → MILD_BULL
```

### 합의 가중치

```python
# 기본: 1:1:1 균등 (데이터 10일 미만)
# 10일 이상: 적중률 기반 자동 조정

weights = {
    "bull":    0.5 + (bull_hit_rate × 1.0),   # 0.5 ~ 1.5
    "bear":    0.5 + (bear_hit_rate × 1.0),
    "neutral": 0.5 + (neutral_hit_rate × 1.0),
}
weighted_score = Σ(stance_score × weight) / 3
```

### 마이너리티 룰

Bear가 "공시·급락·세력·서킷·이탈" 키워드 언급 + confidence > 0.7 이면
투표 결과 무관하게 **DEFENSIVE 강제 적용** (TP 0.8배 축소, 포지션 절반)

---

## 6. 학습 누적 구조

### brain.json 주요 필드

```json
{
  "markets": {
    "KR": {
      "analyst_performance": {
        "bull": {"total": 30, "hit": 18, "rate": 0.6, "trend": "improving"},
        "bear": ..., "neutral": ...
      },
      "mode_performance": {
        "NEUTRAL": {"count": 15, "avg_pnl": 0.12, "win_rate": 0.53},
        ...
      },
      "strategy_performance": {
        "momentum":       {"count": 20, "win_rate": 0.55, "avg_pnl": 0.21},
        "mean_reversion": {"count": 8,  "win_rate": 0.62, "avg_pnl": 0.18},
        "gap_pullback":   {"count": 12, "win_rate": 0.50, "avg_pnl": 0.08}
      },
      "current_beliefs": {
        "market_regime": "변동성장",
        "learned_lessons": ["환율 1500원 이상에서 Bull 과신 주의", ...]
      },
      "correction_guide": {
        "bull_adjustments": ["고환율 시 포지션 크기 -20% 조정"],
        "tuning_rules": ["지수 -1.5% 이하 시 즉시 SL 강화"]
      },
      "debate_history": [...],       // 토론에서 의견 바꾼 이력 + 정답 여부
      "recent_days": [...]           // 최근 60일 날짜별 요약
    }
  }
}
```

### 데이터 축적 속도

| 항목 | 1일 추가량 | 10일 후 | 30일 후 |
|------|-----------|---------|---------|
| training records | 2개 (KR+US) | 20개 | 60개 |
| 분석가 적중률 데이터 | 2건/분석가 | 20건 | 60건 |
| 전략 성과 데이터 | 거래 수 × 2 | 누적 | 누적 |
| **가중 합의 활성화** | — | **10일 달성** | 안정화 |

---

## 7. 리스크 관리

### HARD_RULES (risk_manager.py)

| 규칙 | 값 | 설명 |
|------|-----|------|
| `max_daily_loss_pct` | -3.0% | 일일 손실 -3% 초과 → 자동 HALT |
| `max_single_loss_pct` | -3.0% | 포지션당 -3% → 강제 청산 |
| `take_profit_pct` | 6.0% | 목표 수익 도달 → 청산 |
| `max_positions` | 3 | 동시 보유 최대 3개 |
| `max_order_krw` | .env 설정 | 1회 주문 한도 (모드별 70~100% 적용) |
| `no_new_entry_min` | 10분 | 개장 직후 신규 진입 금지 |
| `close_before_min` | 10분 | 마감 10분 전 전량 청산 |

### 주문 예산 계산

```
예산 = MAX_ORDER_KRW × mode_size_pct%
      → 현금 부족 시 남은 현금 전부 사용
```

예: `MAX_ORDER_KRW=500,000`, CAUTIOUS 모드 → `500,000 × 70% = 350,000원`

### 수수료

| 구분 | 매수 | 매도 |
|------|------|------|
| KR | 0.015% | 0.015% + 증권거래세 0.18% = **0.195%** |
| US | 0.015% | 0.015% |

수수료는 `daily_pnl`에 즉시 반영된다. 텔레그램 `/p` 명령어로 당일 누적 수수료 확인 가능.

### 포지션 전략별 보유 기간

| 전략 | max_hold | 마감 시 처리 |
|------|----------|-------------|
| gap_pullback | 1일 | 강제 청산 |
| momentum | 5일 | 이월 |
| mean_reversion | 3일 | 이월 |
| volatility_breakout (US) | 3일 | 이월 |

---

## 8. 모드 표

단순 다수결이 아닌 **가중 점수 연속 스펙트럼**으로 결정된다.

| 모드 | 점수 범위 | 포지션 크기 | 의미 |
|------|----------|------------|------|
| AGGRESSIVE | ≥ +0.85 | 100% | 강한 상승 신호 |
| MODERATE_BULL | +0.55 ~ +0.85 | 80% | 상승 우세 |
| MILD_BULL | +0.28 ~ +0.55 | 50% | 소폭 상승 |
| CAUTIOUS | +0.08 ~ +0.28 | 60% | 조심스러운 진입 |
| NEUTRAL | -0.20 ~ +0.08 | 40% | 관망 |
| MILD_BEAR | -0.55 ~ -0.20 | 30% | 소폭 하락 예상 |
| CAUTIOUS_BEAR | -0.80 ~ -0.55 | 20% | 하락 우세 |
| DEFENSIVE | -0.95 ~ -0.80 | 10% | 강한 하락 위험 |
| HALT | < -0.95 | 0% | 진입 전면 중단 |

---

## 9. 알려진 맹점 및 한계

### 구조적 맹점

| 항목 | 내용 | 완화책 |
|------|------|--------|
| **초반 cold start** | brain 데이터 10일 미만 → 가중치 무효, 균등 합의 | 3~4주 paper 운영 후 패턴 형성 |
| **분석가 획일화** | Claude 모델 동일 → 진짜 독립 판단 아님 | 페르소나 강제 + 금지 행동 명시로 완화 |
| **프롬프트 의존성** | digest_prompt 품질에 판단 정확도 직결 | 뉴스/수급/지표 전처리 계속 개선 필요 |
| **마이너리티 룰 단어 한정** | "급락·서킷" 등 키워드만 감지 | 주관적 서술은 감지 불가 |

### 데이터 맹점

| 항목 | 내용 |
|------|------|
| **US 잔고 조회 미구현** | get_balance(market='US') 스텁 반환 — 모의투자만 가능 |
| **US 실계좌 지원 없음** | KIS 해외주식 API 미통합 |
| **뉴스 감성 분석 부재** | 텍스트 그대로 전달 → Claude가 직접 해석 |
| **실시간 호가 부재** | 5분마다 현재가 폴링 — 틱 단위 진입 불가 |
| **환율 15분 지연** | yfinance USDKRW=X → 장중 실시간 아님 |

### 전략 맹점

| 항목 | 내용 |
|------|------|
| **KR 3전략 중복 진입** | 같은 종목에 여러 전략 시그널 동시 발화 가능 |
| **US 단일 전략** | volatility_breakout 외 대안 없음 |
| **이월 포지션 SL 갱신 없음** | 다음날 시가 급락 시 SL 발동 전까지 손실 노출 |
| **섹터 집중 위험** | 동일 섹터 최대 2개 제한이지만 섹터 분류가 단순 |

### AI API 맹점

| 항목 | 내용 |
|------|------|
| **Claude API 응답 지연** | 판단 생성 30~60초 → 개장 직전 타이밍 리스크 |
| **JSON 파싱 실패** | Claude가 JSON 이외 텍스트 포함 시 fallback 자동 적용 |
| **max_tokens 제한** | 응답이 잘리면 JSON 불완전 → fallback |
| **비용 확인** | state/api_usage.json, 텔레그램 브리핑에 당일/누적 비용 표시 |

---

## 10. 초기 설정

### 패키지 설치

```bash
pip install anthropic requests pandas python-dotenv schedule websocket-client \
            beautifulsoup4 yfinance exchange-calendars
```

### .env 파일 작성

```env
# Anthropic (필수)
ANTHROPIC_API_KEY=sk-ant-...

# KIS 한국투자증권 (필수)
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ACCOUNT_NO=12345678-01
KIS_IS_PAPER=true

# 텔레그램 (권장)
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...

# Alpha Vantage (선택 — 없으면 yfinance 자동 사용)
ALPHA_VANTAGE_KEY=...

# DART 공시 (선택)
DART_API_KEY=...

# 투자 설정
PAPER_CASH=10000000        # 모의 가상자금 (기본 1000만원)
MAX_ORDER_KRW=500000       # 1회 최대 주문금액
KR_ALLOC_PCT=60            # KR 예산 비율 % (나머지 US)
# USD_KRW_RATE는 세션 시작 시 yfinance로 자동 갱신됨 (기본 1350)
```

### 데이터 수집 (최초 1회)

```bash
# 주가 데이터만 수집 (뉴스 없이 최소 운영 가능)
python phase1_trainer/price_collector.py --lookback 90

# 뉴스 + 보조 데이터까지 수집
python update_data.py --market KR
python update_data.py --market US
```

> historical_sim은 더 이상 필수가 아니다.
> brain.json 없이 봇을 시작해도 균등 가중치(1:1:1)로 동작한다.
> 10일 이상 실제 운영 후 적중률 기반 가중치가 자동 활성화된다.

### Windows 작업 스케줄러 등록 (관리자 권한)

```bat
REM KR 데이터: 장 시작 20분 전 (08:30)
schtasks /create /tn "claudetrade_kr" /tr "python E:\code\claudetrade\update_data.py --market KR" /sc daily /st 08:30

REM US 데이터: 장 시작 20분 전 (22:00)
schtasks /create /tn "claudetrade_us" /tr "python E:\code\claudetrade\update_data.py --market US" /sc daily /st 22:00
```

---

## 11. 봇 실행

```bash
# 모의투자 (기본)
python trading_bot.py

# 실계좌 (KR만 지원)
python trading_bot.py --live
```

봇은 내부 스케줄러로 동작한다. 종료 없이 24시간 켜두면 된다.
재시작 시 당일 판단이 이미 저장되어 있으면 Claude 재호출 없이 재사용한다.

### 스케줄 (KST)

| 시각 | 이벤트 |
|------|--------|
| 08:30 | update_data (Windows 스케줄러) |
| 08:50 | KR session_open — 판단+토론+합의+브리핑 |
| 매 5분 | KR run_cycle — 시그널/청산 |
| 매 30분 | KR run_tuning — 장중 재검토 (이상 시 긴급 재판단) |
| 매 60분 | heartbeat — 텔레그램 강제 전송 |
| 16:00 | KR session_close — 청산+postmortem+brain 업데이트 |
| 22:00 | update_data (Windows 스케줄러) |
| 22:20 | US session_open |
| 매 5분 | US run_cycle |
| 매 30분 | US run_tuning |
| 05:00 | US session_close |

---

## 12. 환경변수 (.env)

| 변수 | 필수 | 설명 |
|------|------|------|
| `ANTHROPIC_API_KEY` | ✅ | Claude API 키 |
| `KIS_APP_KEY` | ✅ | KIS 앱키 |
| `KIS_APP_SECRET` | ✅ | KIS 앱시크릿 |
| `KIS_ACCOUNT_NO` | ✅ | 계좌번호 (형식: 12345678-01) |
| `KIS_IS_PAPER` | ✅ | 모의투자 여부 (true/false) |
| `TELEGRAM_TOKEN` | ☑ | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | ☑ | 텔레그램 채팅 ID |
| `ALPHA_VANTAGE_KEY` | ☑ | 없으면 yfinance 자동 사용 |
| `DART_API_KEY` | ☑ | DART 공시 API |
| `PAPER_CASH` | - | 모의 가상자금 (기본: 10,000,000) |
| `MAX_ORDER_KRW` | - | 1회 최대 주문금액 (기본: 500,000) |
| `KR_ALLOC_PCT` | - | KR 예산 비율 % (기본: 60) |
| `ANTHROPIC_MODEL` | - | Claude 모델 (기본: claude-sonnet-4-6) |
| `CLAUDETRADE_RUNTIME_DIR` | - | 런타임 파일 저장 경로 |

---

## 13. 텔레그램 명령어

봇 실행 중 텔레그램에서 바로 제어 가능하다.

| 명령어 | 설명 |
|--------|------|
| `?` / `/help` | 명령어 목록 |
| `/s` / `/status` | 현재 상태 (모드·포지션·손익·수수료) |
| `/p` / `/pnl` | 오늘 손익 상세 + 수수료 합계 |
| `/pos` | 보유 포지션 목록 |
| `/mode` | 현재 합의 모드 |
| `/judge` | 오늘 아침 판단 요약 |
| `/brain` | 누적 학습 요약 |
| `/credit` | Claude API 비용 조회 |
| `/setorder [금액]` | 1회 최대 주문금액 변경 (세션 내 임시) |
| `/claude` | Claude 긴급 재판단 트리거 |
| `/close [종목]` | 특정 종목 즉시 청산 (예: `/close 005930`) |
| `/closeall` | 전체 포지션 청산 ⚠️ |

> `/setorder`는 현재 세션에만 적용된다. 영구 변경은 `.env`의 `MAX_ORDER_KRW` 수정.

---

## 14. 자주 쓰는 명령어

```bash
# ── 봇 실행 ────────────────────────────────────────────────────
python trading_bot.py            # 모의투자
python trading_bot.py --live     # 실계좌

# ── 데이터 최신화 ───────────────────────────────────────────────
python update_data.py
python update_data.py --market KR
python update_data.py --market US

# ── 주가 데이터만 수집 ──────────────────────────────────────────
python phase1_trainer/price_collector.py --update        # 누락분만
python phase1_trainer/price_collector.py --lookback 550  # 과거 550일치

# ── brain 상태 확인 ─────────────────────────────────────────────
python -c "
import sys; sys.path.insert(0,'E:/code/claudetrade')
from claude_memory import brain as B
B.print_status()
print(B.generate_prompt_summary('KR'))
"

# ── API 비용 확인 ────────────────────────────────────────────────
python -c "
import sys; sys.path.insert(0,'E:/code/claudetrade')
from credit_tracker import summary
import json; print(json.dumps(summary(), indent=2, ensure_ascii=False))
"

# ── Windows 작업 스케줄러 등록 (관리자 권한) ────────────────────
schtasks /create /tn "claudetrade_kr" /tr "python E:\code\claudetrade\update_data.py --market KR" /sc daily /st 08:30
schtasks /create /tn "claudetrade_us" /tr "python E:\code\claudetrade\update_data.py --market US" /sc daily /st 22:00
```

---

## 데이터 소스 요약

| 데이터 | 소스 | 비고 |
|--------|------|------|
| KR 주가 | KIS API | 일봉, 무료 |
| US 주가 | yfinance | 무료 폴백 자동 |
| KR 뉴스 | 네이버금융 크롤링 | 무료 |
| KR 공시 | DART API | opendart.fss.or.kr 무료 발급 |
| VIX | Alpha Vantage | AV 키 필요 |
| USD/KRW 환율 | yfinance USDKRW=X | 세션 시작 시 자동 갱신 |
| KR 수급 | KIS API | 외국인·기관 매매동향 |
| KR 스크리너 | KIS API 거래량순위 | 매일 장 시작 전 |
| US 스크리너 | AlphaVantage TOP_GAINERS | 없으면 기본 15종목 |
| 거래일 캘린더 | exchange-calendars | XKRX(한국), XNYS(미국) |
