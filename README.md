# claudetrade

Claude AI를 이용한 한국/미국 주식 자동매매 봇.
3명의 AI 애널리스트(Bull/Bear/Neutral)가 매일 시장을 판단하고, 기술적 신호와 결합해 거래를 실행합니다.

---

## 목차

1. [구조 한눈에 보기](#구조-한눈에-보기)
2. [필요한 API 키](#필요한-api-키)
3. [설치 및 실행](#설치-및-실행)
4. [동작 방식](#동작-방식)
5. [전략 설명](#전략-설명)
6. [AI 판단 시스템](#ai-판단-시스템)
7. [리스크 규칙](#리스크-규칙)
8. [디렉토리 구조](#디렉토리-구조)

---

## 구조 한눈에 보기

```
시장 데이터 + 뉴스
        ↓
  digest_builder  ← 오늘의 브리핑 생성
        ↓
  3 Claude 판사 (Bull / Bear / Neutral)
        ↓
  합의 엔진 → 거래 모드 결정 (AGGRESSIVE ~ HALT)
        ↓
  기술 지표 신호 (RSI, MACD, BB, 거래량)
        ↓
  전략 필터 (갭눌림 / 모멘텀 / 평균회귀 / 변동성돌파)
        ↓
  주문 실행 (KIS API)  →  텔레그램 알림
        ↓
  장 마감 사후분석 → Brain 메모리 업데이트 → 내일 반영
```

---

## 필요한 API 키

> `.env.example` 을 복사해서 `.env` 로 저장 후 아래 값을 채워주세요.

```bash
cp .env.example .env
```

| 키 이름 | 필수 | 용도 | 발급처 |
|---------|------|------|--------|
| `KIS_APP_KEY` | ✅ | 한국투자증권 API 인증 | [apiportal.koreainvestment.com](https://apiportal.koreainvestment.com) |
| `KIS_APP_SECRET` | ✅ | 한국투자증권 API 인증 | 동일 |
| `KIS_ACCOUNT_NO` | ✅ | 계좌번호 (`12345678-01` 형식) | HTS/MTS에서 확인 |
| `KIS_IS_PAPER` | ✅ | `true` = 모의투자, `false` = 실계좌 | — |
| `ALPHA_VANTAGE_KEY` | ✅ | 미국주식 시세/캔들 데이터 | [alphavantage.co](https://www.alphavantage.co/support/#api-key) (무료) |
| `ANTHROPIC_API_KEY` | ✅ | Claude AI 판단 | [console.anthropic.com](https://console.anthropic.com/keys) |
| `TELEGRAM_TOKEN` | 선택 | 매매 알림 전송 | Telegram @BotFather |
| `TELEGRAM_CHAT_ID` | 선택 | 알림 수신 채팅 ID | Telegram @userinfobot |

> **주의**: `KIS_IS_PAPER=true` 상태에서 충분히 테스트한 뒤 실계좌로 전환하세요.
> 미국장은 현재 모의 모드만 지원됩니다 (`KIS_IS_PAPER=false` 시 자동 스킵).

---

## 설치 및 실행

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env
# .env 파일을 열어서 API 키 입력

# 3. 모의투자 모드로 실행 (기본값)
python trading_bot.py

# 4. 실계좌 모드로 실행 (확인 입력 필요)
python trading_bot.py --live
```

---

## 동작 방식

### 한국장 (KST 기준)

| 시각 | 동작 |
|------|------|
| **08:50** | 세션 시작 — 브리핑 생성, Claude 3명 판단 호출, 합의 도출, 텔레그램 아침 브리핑 발송, 웹소켓 시세 연결 |
| **09:00~15:50** | 5분마다 사이클 — 각 종목 시세 조회, 기술지표 계산, 전략 신호 확인, 조건 충족 시 매수 |
| **09:30, 10:00, ...** | 30분마다 튜닝 — Claude가 현재 상황 재검토, 필요 시 거래 모드 조정 |
| **16:00** | 세션 마감 — 잔여 포지션 강제 청산, 사후분석 실행, Brain 메모리 업데이트, 일일 결산 전송 |

> 거래 대상: **삼성전자(005930)**, **SK하이닉스(000660)**, **NAVER(035420)**

### 미국장 (KST 기준)

| 시각 | 동작 |
|------|------|
| **22:20** | 세션 시작 (동일 프로세스) |
| **22:30~04:50** | 5분 사이클 (폴링 방식, 웹소켓 없음) |
| **05:00** | 세션 마감 |

> 거래 대상: **NVDA**, **TSLA**, **AAPL**

### 진입 제한 (블랙아웃)

- 장 시작 후 **10분** 이내 신규 진입 금지 (변동성 안정 대기)
- 장 마감 **10분** 전 신규 진입 금지 (미청산 리스크)

---

## 전략 설명

### 한국장

| 전략 | 신호 조건 | TP | SL | 최대 보유 |
|------|-----------|----|----|----------|
| **갭 눌림 (gap_pullback)** | 갭 상승 >1% + 거래량 1.5배 + 눌림 확인 | +2.5% | -1.0% | 1일 |
| **모멘텀 (momentum)** | 정배열 + MACD 골든크로스 + 거래량 2배 + 20일 신고가 | +6.0% | -3.0% | 5일 |
| **평균회귀 (mean_reversion)** | RSI <32 + BB 하단 + MA60 위 | BB 중선 (ma20) | -2.0% | 7일 |

### 미국장

| 전략 | 신호 조건 | TP | SL | 최대 보유 |
|------|-----------|----|----|----------|
| **변동성 돌파 (volatility_breakout)** | 종가 > 시가 + 전일범위 × 0.45 + 거래량 2배 | +2.5% | -1.5% | 1일 |

> 우선순위: 갭눌림 → 모멘텀 → 평균회귀 순으로 신호 확인 (하나만 진입)

---

## AI 판단 시스템

### 3명의 Claude 애널리스트

매 장 시작 시 동일한 시장 브리핑을 받지만 서로 다른 관점으로 판단합니다.

| 애널리스트 | 역할 | 관점 |
|-----------|------|------|
| **Bull** | 상승론자 | 기회 포착, 상방 시나리오 중심 |
| **Bear** | 하락론자 | 리스크 식별, 하방 시나리오 중심 |
| **Neutral** | 중립 | 균형 잡힌 불확실성 인정 |

각 애널리스트 반환값: `stance`, `confidence`, `key_reason`, `top_risks`, `suggested_strategy`

### 합의 → 거래 모드

| 투표 결과 | 모드 | 포지션 크기 |
|-----------|------|------------|
| Bull × 3 | AGGRESSIVE | 100% |
| Bull × 2 + Neutral | MODERATE_BULL | 80% |
| Bull × 2 + Bear | CAUTIOUS | 60% |
| Bull + Neutral × 2 | MILD_BULL | 50% |
| Neutral × 3 | NEUTRAL | 40% |
| Bear + Neutral × 2 | MILD_BEAR | 30% |
| Bear × 2 + Neutral | CAUTIOUS_BEAR | 20% |
| Bear × 2 + Bull | DEFENSIVE | 10% |
| Bear × 3 | HALT | 0% (거래 중단) |

### 마이너리티 룰

Bear 애널리스트가 **공시, 급락, 세력, 서킷** 등 위험 키워드를 언급하고 신뢰도 > 70% 이면, 전체 투표와 무관하게 **DEFENSIVE** 모드로 강제 전환합니다.

### Brain 메모리 (학습)

매 장 마감 후 사후분석을 통해 `claude_memory/brain.json` 에 아래 내용을 누적합니다.

- **애널리스트 적중률**: 7일/30일 이동 적중률, 신뢰도 트렌드
- **모드별 성과**: 각 거래 모드의 평균 수익률, 승률
- **전략별 성과**: 전략별 체결 횟수, 평균 손익
- **패턴 기록**: 반복되는 시장 상황과 결과
- **보정 지침**: 다음 날 Claude 판단에 반영할 수정 사항

---

## 리스크 규칙

모든 규칙은 `risk_manager.py` 의 `HARD_RULES` 에 하드코딩되어 있으며 코드 수정 없이는 변경되지 않습니다.

| 규칙 | 값 |
|------|-----|
| 최대 동시 보유 종목 | 3개 |
| 종목당 최대 투자 비중 | 자산의 20% |
| 1회 최대 주문금액 | 500,000원 |
| 일일 최대 손실 | -3% (초과 시 당일 거래 중단) |
| 단일 종목 최대 손실 | -3% (자동 손절) |
| 기본 목표 수익 | +6% |

---

## 디렉토리 구조

```
claudetrade/
├── trading_bot.py              # 메인 실행 루프 (KR/US 스케줄 관리)
├── kis_api.py                  # KIS API + AlphaVantage 래퍼
├── risk_manager.py             # 포지션 관리, 손익 추적, 리스크 규칙
├── indicators.py               # 기술지표 계산 (RSI, MACD, BB, ATR 등)
├── logger.py                   # 로그 시스템
├── telegram_reporter.py        # 텔레그램 알림
├── requirements.txt            # Python 의존성
├── .env.example                # 환경변수 템플릿
│
├── strategy/                   # 매매 전략
│   ├── momentum.py             # 모멘텀 (한국장)
│   ├── mean_reversion.py       # 평균회귀 (한국장)
│   ├── gap_pullback.py         # 갭 눌림 (한국장)
│   └── volatility_breakout.py  # 변동성 돌파 (미국장)
│
├── minority_report/            # Claude AI 판단 시스템
│   ├── analysts.py             # Bull/Bear/Neutral 3명 판단 실행
│   ├── consensus.py            # 합의 엔진 + 마이너리티 룰
│   ├── postmortem.py           # 장 마감 사후분석
│   └── tuner.py                # 장 중 30분 튜닝
│
├── phase1_trainer/             # 데이터 수집 및 브리핑 생성
│   ├── price_collector.py      # 과거 OHLCV 수집
│   ├── kr_news_collector.py    # 한국 뉴스 수집
│   ├── us_news_collector.py    # 미국 뉴스 수집
│   ├── supplement_collector.py # 보조 데이터 수집
│   ├── digest_builder.py       # Claude 입력용 브리핑 생성
│   └── historical_sim.py       # 백테스트 프레임워크
│
├── claude_memory/              # AI 학습 메모리
│   ├── brain.py                # Brain 파일 읽기/쓰기/업데이트
│   └── brain.json              # 누적 학습 데이터
│
└── dashboard/                  # 웹 대시보드 (선택)
    ├── dashboard_server.py     # Flask 서버
    └── sample_data.py          # 샘플 데이터
```

---

## 로그 파일

실행 중 생성되는 로그는 `logs/` 디렉토리에 저장됩니다.

```
logs/
├── system/         # 시스템 전반 로그
├── brain/          # Brain 메모리 업데이트 로그
├── daily_judgment/ # 일자별 판단 + 실제 결과 JSON
└── daily/          # 일일 거래 결과
```
