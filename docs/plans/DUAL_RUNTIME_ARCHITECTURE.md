# Dual Runtime Architecture

> 목표: `모의투자(paper)`와 `실전투자(live)`를 동시에 운용하되,
> 전략 판단은 공통으로 1회만 수행하고 계좌/손익/원장은 완전히 분리한다.

> **운용 방침**: paper는 데이터 축적 엔진이다. live 금액이 커지고 전략이 검증되면
> paper를 종료하고 live 단독 운영으로 전환한다.
> 그 전까지는 paper + live 동시 운용이 기본 상태다.

---

## 1. 결론

이 구조는 `엔진 2개`가 아니다.

- `공통 전략 엔진 1개`
- `계좌 런타임 2개`
  - `paper runtime`
  - `live runtime`

즉 "판단은 하나, 실행은 둘" 구조로 간다.

```text
Shared Engine
  ├─ data collect
  ├─ screening
  ├─ Claude
  └─ decision list

Account Runtime: paper
  ├─ balance
  ├─ risk
  ├─ positions
  ├─ orders
  └─ ledger

Account Runtime: live
  ├─ balance
  ├─ risk
  ├─ positions
  ├─ orders
  └─ ledger
```

---

## 2. 왜 이렇게 가야 하는가

엔진을 둘로 분리하면 아래 문제가 생긴다.

- 같은 장에서 paper/live 판단이 달라진다.
- Claude 호출이 2배가 된다.
- 데이터 수집 시점이 어긋난다.
- "왜 paper와 live 결과가 달랐는지" 비교가 어려워진다.

반대로 공통 엔진 1개로 묶으면 아래가 가능하다.

- 같은 `decision_id` 기준 비교
- paper/live 체결 차이 추적
- 공통 데이터 수집
- 공통 Claude 비용/토큰 절감

---

## 3. 설계 원칙

### 공통 1회

- 시장 데이터 수집
- KR/US 스크리닝
- 뉴스/보조지표 계산
- Claude 판단
- 진입/청산 후보 생성
- 오늘의 watchlist 생성

### 완전 분리

- 토큰
- 계좌번호
- broker snapshot
- cash / equity
- risk manager
- positions
- pending orders
- trade ledger
- session baseline
- halt 상태
- dashboard live status

### 절대 금지

- paper/live가 같은 `RiskManager` 공유
- paper/live가 같은 `open_positions.json` 공유
- paper/live 손익 합산을 기본값으로 표시
- 한쪽 halt가 다른 쪽에 전염

---

## 4. 핵심 구조

### 4.1 Shared Engine

책임:

- 공통 시장 데이터 수집
- 공통 feature build
- 공통 스크리닝
- 공통 Claude 호출
- 공통 decision 생성

출력:

```text
decision_id
session_date
market
ticker
decision_type
strategy
reason
features_snapshot
claude_payload
created_at
```

### 4.2 Account Runtime

런타임은 mode별 독립 객체로 둔다.

```python
AccountRuntime(mode="paper")
AccountRuntime(mode="live")
```

필수 상태:

```python
mode
token
broker_client
risk
positions
pending_orders
daily_baseline
broker_state
trade_ledger
execution_log
```

책임:

- shared decision 소비
- 계좌별 precheck
- 계좌별 주문 실행
- 계좌별 체결 반영
- 계좌별 손익/원장 업데이트
- 계좌별 halt 판단

---

## 5. 실행 흐름

권장 흐름은 `공통 판단 후 병렬 실행`이다.

```text
run_cycle(market)
  -> shared collect
  -> shared screen
  -> shared Claude
  -> shared decisions[]
  -> asyncio.gather(
       paper.execute(decisions),
       live.execute(decisions),
     )
```

예시:

```text
09:32:10 [shared] NVDA buy decision created
09:32:11 [paper] order filled @ 201.50
09:32:11 [live]  order filled @ 201.52   ← 동시 실행, fill price는 각자
```

순차 실행(paper → live)은 live가 항상 몇 초 늦게 진입하므로 병렬 실행이 원칙.

이때 중요한 점:

- decision은 공통
- fill은 각각 다를 수 있음
- 미체결/현금부족/halt는 각 runtime에서 별도 판단

---

## 6. 진입 시점 설계

진입 시점 자체는 공통으로 본다.

즉:

- 같은 분봉
- 같은 스크리너 결과
- 같은 Claude 판단
- 같은 진입 후보

를 기준으로 두 runtime이 동시에 평가한다.

다만 실제 진입은 다를 수 있다.

예:

- paper: 3주 진입
- live: 1주 진입
- paper: 진입 성공
- live: 현금 부족으로 skip

따라서 저장 구조는 아래처럼 가야 한다.

```text
shared decision: "enter NVDA"
paper execution: filled
live execution: skipped(insufficient_cash)
```

---

## 7. 주문 수량/금액

수량 계산은 공통이 아니라 runtime별로 계산한다.

예:

```text
paper:
  KR max_order_krw = 500,000
  US budget_usd = 300

live:
  KR max_order_krw = 100,000
  US budget_usd = 30
```

즉 공통 decision 이후:

```python
paper_order = paper.build_order(decision)
live_order = live.build_order(decision)
```

로 분리한다.

---

## 8. halt / risk

halt는 반드시 분리한다.

- `paper_halt`
- `live_halt`

이유:

- 계좌 규모가 다름
- baseline이 다름
- 체결/슬리피지가 다름
- 손익률이 다름

공통 halt는 잘못된 설계다.

---

## 9. 원장 구조

원장은 3계층으로 나눈다.

### 9.1 Shared Decision Ledger

공통 판단 원장.

예:

```text
decision_id
date
market
ticker
side
reason
strategy
claude_summary
```

### 9.2 Paper Trade Ledger

```text
decision_id
mode=paper
market
ticker
side
qty
price
order_no
filled_at
fee
pnl
status
```

### 9.3 Live Trade Ledger

같은 스키마로 분리 저장.

이렇게 해야

- 판단은 합쳐 보고
- 실행은 따로 보고
- 필요 시 둘을 같이 비교

가 가능하다.

---

## 10. 대시보드

대시보드는 기본적으로 아래 3개 뷰를 가진다.

- `All`
- `Paper`
- `Live`

### 상단 토글

- 계좌 선택 버튼
- 기본값은 `All`보다 `Paper` 또는 `Live` 개별 뷰가 더 안전함

### 표시 카드

- 누적자산
- 당일손익
- 누적손익
- 보유 종목 수
- 승률
- MDD
- 오늘 매수/매도 건수

### 테이블

1. 공통 판단 원장
2. paper 매매원장
3. live 매매원장

권장 표현:

```text
NVDA BUY decision
  paper: filled 3 @ 201.50
  live:  filled 1 @ 201.70
```

즉 `판단 1줄 + 실행결과 2줄` 구조가 가장 보기 좋다.

---

## 11. Claude 사용 방식

Claude는 공통으로 1회만 호출한다.

입력:

- 장 상태
- 종목 후보
- 뉴스
- 기술지표
- 기존 포지션 맥락
- 전략 모드

출력:

- 진입 여부
- 우선순위
- 이유
- 리스크 메모
- exit 의견

주의:

- Claude는 계좌 자금관리 역할을 하지 않는다.
- 자금/수량/주문 가능 여부는 runtime executor에서 처리한다.

---

## 12. 데이터 수집

일일 수집은 공통으로 1회만 수행한다.

공통 수집 대상:

- KR/US 시세
- 스크리너 결과
- 뉴스
- 환율
- VIX/DXY
- 보조 수급/섹터 데이터
- Claude digest용 피처

paper/live 각각 따로 수집하면 안 된다.

이유:

- API 낭비
- 시간차 발생
- 같은 장을 다른 데이터로 해석하게 됨

---

## 13. 파일 구조 초안

```text
state/
  shared_decision_log.jsonl

  paper_open_positions.json
  live_open_positions.json

  paper_pending_orders.json
  live_pending_orders.json

  paper_daily_baseline.json
  live_daily_baseline.json

  paper_broker_snapshot.json
  live_broker_snapshot.json

  paper_trade_ledger.jsonl
  live_trade_ledger.jsonl

  paper_runtime_status.json
  live_runtime_status.json
```

추가로 dashboard 캐시도 분리:

```text
dashboard/
  paper_live_status.json
  live_live_status.json
```

---

## 14. 코드 구조 초안

### 공통

```python
class SharedEngine:
    def collect(self, market): ...
    def build_universe(self, market): ...
    def make_decisions(self, market): ...
```

### 계좌 런타임

```python
class AccountRuntime:
    def __init__(self, mode): ...
    def sync_broker(self, market): ...
    def precheck(self, decision): ...
    def execute(self, decision): ...
    def reconcile(self, market): ...
```

### 오케스트레이션

```python
class DualRuntimeCoordinator:
    def run_cycle(self, market):
        decisions = self.shared.make_decisions(market)
        for d in decisions:
            self.paper.execute(d)
            self.live.execute(d)
```

---

## 15. 단계별 구현 계획

현재 `TradingBot`은 7200줄 단일 클래스다.
`SharedEngine + AccountRuntime` 분리는 MODULARIZATION 완료 후에 안전하게 진행한다.

---

### Phase 0 — 프로세스 2개 (지금 ~ 다음 주)

**목표**: 코드 구조 변경 없이 paper/live 동시 운용 시작

구현:
- 상태 파일 경로를 mode별로 분리 (`paper_` / `live_` prefix)
- `--paper` / `--live` 실행 플래그로 봇 구분
- paper 봇: `KIS_IS_PAPER=true`, 기존 금액 유지
- live 봇: `KIS_IS_PAPER=false`, 소액

제약:
- Claude 2회 호출 (비용 감수)
- 두 프로세스가 같은 데이터를 독립적으로 수집

완료 조건: `paper_open_positions.json` / `live_open_positions.json` 분리 확인

---

### Phase 1 — MODULARIZATION 완료 후 SharedEngine 추출

**전제**: MODULARIZATION.md P1~P3 완료

구현:
- 기존 `TradingBot` 내부에서 공통 판단과 계좌 실행 분리
- `SharedEngine` 개념 도입 (데이터 수집 / 스크리닝 / Claude 판단)
- Claude 1회 호출로 통합
- `decision_id` 도입 (기존 `decisions.jsonl`과 통합)

---

### Phase 2 — AccountRuntime 분리

구현:
- `PaperRuntime`, `LiveRuntime` 클래스로 추출
- `run_cycle()` → shared decision → runtime execute 흐름 정리
- 단일 프로세스로 통합

---

### Phase 3 — Dashboard 재구성 + paper 종료

구현:
- dashboard `All / Paper / Live` 뷰
- live 안정화 확인 후 paper 종료
- live 단독 운영 전환

---

## 16. 구현 우선순위

### P0 — Phase 0 ✅ 완료 (2026-04-19)

- [x] 상태 파일 경로 분리 (`paper_` / `live_` prefix) — `bot/state.py`
  - `open_positions`, `pending_orders`, `daily_baseline`, `claude_control`
- [x] `--paper` / `--live` 실행 플래그 추가 + `.env.paper` / `.env.live` 분리
- [x] daily baseline 분리 (paper PnL이 live 기준을 오염하지 않도록)
- [x] dashboard status 분리 (`paper_live_status.json` / `live_live_status.json`)
- [x] `shared_judgment_cache.py` — 두 프로세스 간 아침 Claude 판단 1회 공유 (FileLock)
- [x] `telegram_reporter.py` — `[모의]`/`[실전]` 모드 라벨 추가
- [x] DB 4종 paper/live 분리:
  - `ticker_selection_db` / `intraday_strategy_db` — `bot_mode` 컬럼
  - `credit_tracker` — `{mode}_api_usage.json`
  - `decisions.db` — `is_simulated` / `data_source` 필드

### P1 — Phase 1 (MODULARIZATION P1~P3 완료 후)

- [ ] `SharedEngine` 추출 (데이터 수집 / 스크리닝 / Claude)
- [ ] `decision_id` 스키마 정의 (기존 decisions.jsonl과 통합)
- [ ] Claude 단일 호출로 통합
- [ ] halt 독립 보장 검증

### P2 — Phase 2

- [ ] `PaperRuntime`, `LiveRuntime` 클래스화
- [ ] 단일 프로세스 통합

### P3 — Phase 3

- [ ] dashboard `All / Paper / Live` 뷰
- [ ] paper/live 체결 차이 추적
- [ ] live 안정화 기준 정의 → paper 종료 절차

---

## 17. paper → live 전환 기준

paper를 종료하고 live 단독으로 전환하는 기준을 미리 정의한다.

필수 충족 조건:

- live 누적 거래 건수 충분 (KR/US 각 50건 이상)
- live 전략 승률이 paper 대비 크게 다르지 않음 (±10% 이내)
- live에서 halt / 회계 버그 발생 없음 (연속 4주)
- brain.json 학습 데이터가 live 기반으로 충분히 쌓임

전환 절차:

1. paper 봇 중지
2. live 봇의 상태 파일 prefix를 일반 경로로 변경
3. MODULARIZATION 완료된 단일 프로세스로 전환
4. dashboard paper 뷰 비활성화

---

## 18. 최종 판단

이 구조의 핵심은 아래 두 줄이다.

> `paper는 데이터 축적 엔진이다. live가 검증되면 paper를 끈다.`
> `전략 판단은 공유하고, 계좌 상태와 실행 결과는 분리한다.`

다음 작업 순서:

1. **지금 바로**: 상태 파일 경로 분리 (`--paper` / `--live` 플래그)
2. **MODULARIZATION 완료 후**: SharedEngine + AccountRuntime 추출
3. **live 안정화 후**: paper 종료, live 단독 전환

---

## 관련 문서

- `MODULARIZATION.md` — Phase 1 전제 조건 (Mixin 분리)
- `CLAUDE.md` — 전체 아키텍처 원칙
