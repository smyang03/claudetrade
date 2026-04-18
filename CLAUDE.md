# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 프로젝트 철학

**"Claude가 판단하고, 규칙이 집행하고, 데이터가 기억한다"**

- Claude는 시장 분위기, 종목 선택, 청산 판단을 담당한다. 신호 발화 자체는 규칙 기반 전략이 한다.
- 판단 결과는 무조건 `brain.json` / `decisions.db`에 누적된다. 쌓인 데이터가 다음 판단을 보정한다.
- 모의투자로 먼저 검증하고, 구조가 안정되면 실거래로 전환한다. **안전 우선**.
- 실거래는 KR/US 계좌 분리 구조로 진화할 예정이다.

이 저장소의 목표는 "Claude가 그럴듯해 보이는 코드를 빠르게 만드는 것"이 아니다.
목표는 **실거래 전환 전까지 모의투자 환경에서 데이터, 회계, 판단 흐름을 안정적으로 검증하고, 그 결과를 누적 가능한 형태로 남기는 것**이다.

우선순위는 아래와 같다.

1. 수익률보다 안전성
2. 속도보다 재현성
3. 추측보다 로그와 수치
4. 큰 리팩토링보다 최소 수정
5. 단발성 수정보다 회귀 방지

### 핵심 중점 사항 — 데이터 품질과 오염 방어

이 시스템의 본질은 **Claude가 좋은 판단을 내릴 수 있도록 좋은 데이터를 주는 것**이다.

Claude의 판단 품질은 입력 데이터의 품질에 직결된다.
- 시장 컨텍스트(digest), 분석가 적중률(brain.json), 전략 성과(decisions.db)가 정확해야 판단이 정확하다
- 잘못된 데이터로 판단한 결과가 다시 brain.json에 쌓이면 **오염이 복리로 확산**된다

**데이터 오염은 절대적으로 방어한다.** 구체적으로:

| 오염 경로 | 방어 방법 |
|-----------|-----------|
| 브로커 잔고 오계산 → session_start_equity 왜곡 | `_sync_runtime_with_broker` 수정 시 수치 검증 필수 |
| 잘못된 체결가/수량 → forward return 오기록 | `update_exit()` 전 포지션 데이터 일관성 확인 |
| False HALT 후 복구 → 손익 기준선 재설정 오류 | `reset_daily_state()` 호출 시점 명시적 로깅 |
| brain.json 적중률 계산 오류 → 분석가 가중치 왜곡 | `claude_memory/data_integrity.py` 자동 점검 |
| 모의투자 데이터가 실거래 학습에 혼입 | `data_source` 컬럼으로 항상 구분, 혼용 금지 |

코드 수정 시 아래를 반드시 먼저 생각한다.

- 이 변경이 회계 수치에 어떤 영향을 주는가
- 이 변경이 누적 데이터에 어떤 흔적을 남기는가
- 이 변경이 오늘만 맞고 내일 다시 깨질 가능성은 없는가

### 로그는 분석의 원천 — 충분히 남긴다

**에러 로그와 실행 로그는 충분히 작성되어야 한다. 로그가 없으면 분석할 수 없다.**

- 예외 발생 시 `log.exception(...)` 또는 `log.error(..., exc_info=True)`로 스택트레이스를 남긴다
- 브로커 API 호출 결과(성공/실패/응답값)는 반드시 로깅한다
- 진입 차단 사유(`rejection_reason`), 신호 발화 조건, 파라미터 결정 과정은 `logs/analysis/`에 기록한다
- 판단 로그(`logs/daily_judgment/`, `logs/judgment/`)는 Claude가 왜 그 결정을 했는지 추적할 수 있어야 한다
- 로그를 줄이는 방향으로 수정하지 않는다. 운영 중 이슈를 사후에 재현할 수 있어야 한다
- 로그 레벨 기준: `DEBUG` 반복 루프 내부 / `INFO` 주요 이벤트 / `WARNING` 비정상 but 복구 가능 / `ERROR` 즉시 확인 필요
- **로그는 추가하든 삭제하든, 먼저 왜 해야 하는지 개발자에게 물어보고 확인받은 후 작성한다**
  - "불필요해 보이는 로그"도 Claude의 사후 분석·파라미터 보정·데이터 수집에 쓰일 수 있다
  - 삭제하기 전에 "이 로그가 decisions.db / brain.json / postmortem 어디에도 안 쓰이는가"를 먼저 확인한다

---

## Claude Working Contract

Claude는 이 저장소에서 아래 원칙을 항상 따른다.

1. **추측으로 수정하지 않는다**
   로그, 수치, 재현 입력 없이 "이럴 것 같다"는 가정으로 패치하지 않는다.

2. **증상이 아니라 원인을 고친다**
   로그를 조용히 만들거나 조건문으로 우회하는 방식으로 끝내지 않는다.

3. **변경 범위를 최소화한다**
   요청받지 않은 리팩토링, 스타일 통일, 구조 재배치는 하지 않는다.

4. **숫자 버그는 숫자 하네스로 막는다**
   `cash`, `equity`, `daily_pnl`, `session_start_equity`, `halt` 같은 수치 버그는 반드시 assertion으로 고정한다.

5. **런타임 상태와 코드 자산을 섞지 않는다**
   runtime 파일, 로그, 캐시, 세션 산출물은 코드 커밋에 섞지 않는다.

6. **문서보다 실제 동작을 먼저 확인한다**
   `CLAUDE.md`, 주석, README가 실제 코드와 다르면 코드를 기준으로 확인하고 문서를 맞춘다.

7. **검증 없는 커밋은 하지 않는다**
   변경 범위에 맞는 하네스 또는 검증 절차 없이 커밋하지 않는다.

---

## 나아갈 방향 (Roadmap)

### 현재 (모의투자 안정화)
- RiskManager KR/US 분리 (`risk_kr` / `risk_us`) — 단일 풀에서 별도 계좌 구조로
- `kis_api.py` US 전용 자격증명 분리 (`KIS_ACCOUNT_NO_US`, `KIS_APP_KEY_US`)
- `trading_bot.py` 모듈화 — 4000줄 단일 파일을 역할별로 분리 (토큰 효율 + 가독성)

### 다음 단계 (실거래 전환)
- `decisions.db` 데이터가 충분히 쌓이면 `entry_priority` 점수에 ML 모델 연결 (Phase 2)
- 분석가 적중률 가중치 → 실거래 성과 데이터로 재보정
- KR/US 독립 세션 스케줄링 (현재는 동일 루프 내 분기)

### 장기
- `brain.json` 학습 루프 자동화 (postmortem → 다음 세션 프롬프트 자동 반영)
- 진입 신호 ML화: rule-based signal → ML score로 점진적 전환

---

## Claude에게 — 반드시 지킬 것

### 코드 작업 원칙

1. **KIS API TR 코드·파라미터는 반드시 공식 문서 먼저 확인한다**
   추측하거나 유사 TR코드로 대체하지 않는다. 모의투자 TR: `VTTC` 계열 / 실거래 TR: `TTTC` 계열.

2. **버그는 증상이 아니라 근본 원인을 고친다**
   로그 확인 → 수치 검증 → 최소 수정 → 테스트 순서. 두 번 같은 곳을 고치면 처음에 잘못 고친 것이다.

3. **기존 패턴을 우선 따른다**
   `trading_bot.py`, `risk_manager.py`, `kis_api.py`, `strategy/*`에 이미 있는 흐름을 우선 따른다. 새 추상화나 구조를 만들기 전에 기존 패턴에 맞춰 최소 수정으로 해결한다.

4. **KR/US, paper/live, runtime/config 경계를 흐리지 않는다**
   이 프로젝트는 아래 경계가 자주 깨지면서 버그가 발생했다.
   - KR cash와 US position 회계 혼선
   - paper와 live 응답 형식 차이 무시
   - config 파일과 runtime 산출물 혼입
   - 내부 상태와 broker sync 상태 충돌
   새 코드는 반드시 이 경계를 명확히 유지해야 한다.

5. **회계 관련 코드는 보수적으로 작성한다**
   `cash`, `equity`, `daily_pnl`, `session_start_equity`, `positions`에 닿는 변경은 일반 기능 수정이 아니라 **회계 로직 수정**으로 취급한다.
   - 계산 근거를 설명할 수 있어야 한다
   - before/after 수치를 비교할 수 있어야 한다
   - 대응 하네스가 있어야 한다

6. **전략 detail 포맷을 바꾸면 `_classify_rejection()`도 같이 수정한다**
   `rejection_reason` / `volume_state`는 `none_detail` 문자열 파싱으로 생성된다.

7. **정상 동작하는 코드는 임의로 수정하지 않는다**
   요청받은 기능 외의 코드를 "개선"하거나 "정리"하지 않는다. 건드려야 할 이유가 생기면 먼저 개발자에게 물어본다.
   리팩토링·최적화·스타일 수정은 명시적으로 요청받은 경우에만 한다.

8. **runtime 파일은 읽기/복구 대상이지 배포 자산이 아니다**
   예외적으로 `state/brain.json`만 버전 관리 자산으로 취급한다. 그 외 운영 상태 파일은 재시작 복구를 위한 runtime 데이터이며 코드 커밋에 포함하지 않는다.

9. **.env.example에 환경변수를 추가할 때, 코드에서 실제로 읽는지 먼저 확인한다**
   코드 연결 없이 설정만 늘리면 운영자에게 잘못된 기대를 심어준다.
   연결 전이면 반드시 주석에 `※ 코드 미연결 — [작업명] 완료 후 동작` 을 명시한다.

### 하네스 원칙

이 저장소에서 "하네스(harness)"는 단순 테스트 스크립트가 아니다.
하네스는 **실제 버그나 리스크를 재현 가능한 입력으로 다시 실행하고, 기대 수치와 상태를 고정하는 회귀 장치**다.

하네스는 아래 조건을 만족해야 한다.

1. **입력이 고정되어야 한다**
   - mock broker response
   - 저장된 balance snapshot
   - 특정 로그 기반 fixture
   - synthetic OHLCV
   - 명시적인 env / state 초기값

2. **기대 결과가 명시되어야 한다**
   - `cash` / `equity` / `daily_pnl`
   - `halted` 여부
   - baseline 값
   - position 변화
   - decision / rejection reason

3. **외부 환경에 의존하지 않아야 한다**
   실시간 시세, 실제 계좌 상태, 현재 장중 시각, 우연한 파일 상태에 의존하면 하네스가 아니라 manual check다.

4. **실패 원인이 바로 드러나야 한다**
   "실패함"만 출력하지 말고 어떤 값이 기대와 달랐는지 보여줘야 한다.

### 하네스 계층

검증은 아래 3계층으로 구분한다.

1. **Targeted Harness**
   함수/메서드 단위의 빠른 회귀 검증. mock input과 fixed state로 핵심 상태를 확인한다.

2. **Replay Harness**
   실제 장애 로그나 응답을 fixture로 고정해서 재현하는 하네스. 운영 이슈를 고쳤다면 가능하면 이 계층까지 남긴다.
   - 장애 1건당 회귀 케이스 1개 추가
   - 수동 확인으로 끝내지 않는다

3. **Simulation / Manual Check**
   `sim_runner` 같은 과거 데이터 재생이나 실 API 확인은 유용하지만 하네스 대체재는 아니다.
   - Harness: 자동 반복 가능, 입력 고정
   - Simulation: 과거 데이터 재생
   - Live Check: 실제 환경 sanity check

### 현재 하네스 기준

- `test_broker_sync_cash.py`
  - broker accounting 관련 **targeted harness**
  - US paper cash=0 처리
  - broker sync 시 cash overwrite 문제
  - injected position cost 보정
  - session baseline 고정
  - halt double-check

- `test_kr_trade.py`
  - 하네스가 아니다
  - 실데이터 / KIS / 시장 상태에 의존하므로 manual diagnostic 또는 live sanity check로 취급한다

### 버그 수정 절차

버그를 수정할 때 Claude는 아래 순서를 따른다.

1. 로그와 수치로 현상을 확인한다
2. 재현 입력을 확보한다
3. 하네스에서 먼저 실패를 만든다
4. 최소 수정으로 원인을 고친다
5. 하네스를 다시 실행한다
6. 필요하면 simulation 또는 manual check를 추가한다
7. `git diff --stat`로 runtime 파일 혼입을 확인한다

재현 없이 바로 패치하는 것은 예외적으로만 허용한다.

### 기능 변경 후 검증 절차

기능 변경 후에는 아래 순서를 따른다.

1. 문법 / 컴파일 확인
2. 변경 범위에 맞는 targeted harness 실행
3. 관련 장애 이력이 있으면 replay harness 실행
4. 필요 시 simulation 실행
5. `git diff --stat` 확인
6. runtime 파일 혼입 여부 확인

예시:

```bash
python -m py_compile trading_bot.py risk_manager.py test_broker_sync_cash.py
python -m unittest test_broker_sync_cash.py -v
git diff --stat
git status --short
```

검증 없이 "동작할 것 같다"는 가정으로 커밋하지 않는다.

### Git 원칙

1. **커밋 전 반드시 `git diff --stat`으로 내용을 확인한다**
   `git status`는 파일 목록만 보여준다. 무엇이 왜 바뀌었는지는 diff로 확인한다.

2. **코드 커밋에 runtime 파일을 섞지 않는다**
   아래 파일은 `.gitignore` 추적 제외 대상이다. 절대 코드 커밋에 포함하지 않는다:
   - `state/open_positions.json` — 운영 포지션, 배포 시 덮어쓰면 포지션 오염
   - `state/claude_control.json` — 환경 간 복제 금지
   - `state/decisions.jsonl` — 장중 누적 로그
   - `state/pending_orders.json` — 미체결 주문 상태
   - `data/universe/**` — 날짜별 재생성 스냅샷
   - `logs/**`, `data/cache/**`, `data/ml/**`, `__pycache__/**`

3. **`state/brain.json`만 버전 관리 예외다**
   분석가 적중률·전략 성과 누적 자산이므로 의도적으로 추적한다.
   단, brain.json 업데이트는 코드 변경과 **반드시 별도 커밋**으로 분리한다:
   ```
   chore: brain.json 학습 누적 업데이트 (YYYY-MM-DD)
   ```

4. **버그 하나 = 커밋 하나**
    여러 버그를 한 커밋에 묶으면 나중에 원인 추적(bisect)이 불가능해진다.

5. **`git rm --cached` 전에 `git ls-files <경로>`로 tracked 목록을 먼저 확인한다**

---

## 과거 실수 기록 — 반복하지 않기 위해

| # | 실수 | 커밋 | 교훈 |
|---|------|------|------|
| 1 | API 키 공개 노출 후 3회에 걸쳐 수습 | `3bacc00~ad0bfa5` | `.env`는 절대 커밋 안 함. `.gitignore` 먼저 |
| 2 | `__pycache__` / `logs/` / `data/price/**` 155개 파일 커밋 | `3bacc00` `4aa34fb` | `.gitignore` 없이 코드 시작 금지 |
| 3 | `data/universe/`를 "코드성 데이터"로 오분류해 track 시작 | `a36a450` | 날짜별로 갱신되는 파일은 runtime |
| 4 | KR/US 예산 분리 구현 → 3일 후 "미적용이었음"으로 폐기 | `fec3731`→`7a9645d` | 동작 검증 없이 merge 금지 |
| 5 | False HALT 근본 원인을 두 번에 걸쳐 수정 | `309942b`→`11faf95` | 증상이 아닌 원인을 고칠 것 |
| 6 | `_classify_rejection` 같은 함수 2회 연속 수정 | `3ccf95e`→`5ecc1d7` | 수정 전 전체 범위 파악 |
| 7 | f-string syntax error 상태로 배포 | `65b5fe5` | 배포 전 구문 검사 필수 |
| 8 | 여러 버그를 "전체 수정 통합" 하나로 묶음 | `d229799` `79b993d` | 버그 하나 = 커밋 하나 |
| 9 | runtime state를 코드 커밋에 혼합 | `9c43144` | `git diff --stat` 먼저 |
| 10 | CLAUDE.md에 "runtime 커밋 금지" 쓰면서 같은 커밋에 runtime 포함 | `9c43144`→`4a48246` | 문서보다 행동이 먼저 정렬되어야 |

---

## 실행 명령

```bash
# 봇 실행 (모의투자 기본, KIS_IS_PAPER=true)
python trading_bot.py

# 실거래 모드
python trading_bot.py --live

# 배포 전 구문 검사 (필수)
python -c "import ast; ast.parse(open('trading_bot.py', encoding='utf-8').read()); print('OK')"

# 브로커 동기화 단위 테스트
python -m unittest test_broker_sync_cash.py -v

# ML DB 기능 검증
python ml/test_full.py

# 시뮬레이션
python -m phase1_trainer.sim_runner --market ALL --engine both --start 2022-01-01 --top 15

# decisions.db 조회
python -c "import sqlite3; conn=sqlite3.connect('data/ml/decisions.db'); print(conn.execute('SELECT count(*) FROM decisions').fetchone())"
```

---

## 아키텍처 — 큰 그림

### 실행 흐름

```
main()
  └─ TradingBot.__init__()
       ├─ RiskManager(market="KR"), RiskManager(market="US")  ← 분리 작업 진행 중
       └─ 캐시, 스케줄러, Telegram 초기화
  └─ session_open(market)
       ├─ KIS 토큰 갱신 / 잔고 동기화
       ├─ build_kr/us_digest()      → VIX/환율/뉴스/섹터 컨텍스트
       ├─ get_three_judgments()     → Bull/Bear/Neutral 2라운드 토론 (analysts.py)
       ├─ build_consensus()         → mode + size 결정 (consensus.py)
       ├─ screen_market_kr/us()     → 1차 스크리닝
       └─ select_tickers()          → Claude 최종 종목 선택
  └─ run_cycle() [5분 주기]
       ├─ _sync_runtime_with_broker()   ← 브로커 잔고 덮어씀 (주의)
       └─ run_entry_scan()              → 종목별 신호 → 매수
  └─ _on_tick() [WebSocket 실시간]
       └─ _process_exit_candidates()   → TP/SL/trailing/max_hold
  └─ session_close()
       └─ postmortem.run()             → brain.json 업데이트
```

### 진입 결정 파이프라인

```
OHLCV 로드 → calc_all() [indicators.py]
  └─ adaptive_params() [4단계]
       1. base  : strategy/*.params(mode, conf, market)
       2. regime: VIX/USD_KRW 보정
       3. perf  : decisions.db 최근 성과 반영
       4. guard : base 대비 이동 범위 클리핑
  └─ 신호 체인 (KR): OR눌림→갭눌림→모멘텀→평균회귀→변동성돌파→continuation
  └─ 신호 체인 (US): 분석가 투표 우선순위 → 각 전략 → continuation fallback
  └─ entry_priority.compute() → cutoff 판단
  └─ ml/db_writer.write_decision() → BUY_SIGNAL / NO_SIGNAL / BLOCKED
```

### 판단 재사용 로직 — 봇 재시작 시 주의

`session_open()` 실행 시 해당 날짜의 `logs/daily_judgment/YYYYMMDD_{MARKET}.json` 이 존재하면:
- 기존 `judgments` / `consensus` (모드, 분석가 판단) 를 **그대로 재사용**
- 종목만 새로 스크리닝

즉 봇을 장중에 재시작해도 Claude 토론은 다시 하지 않는다. 이 파일이 오염되거나 잘못된 상태면 잘못된 모드로 하루 종일 운영된다. 수동으로 판단을 초기화하려면 해당 파일을 삭제 후 재시작.

### 매수 차단 조건

아래 조건 중 하나라도 해당하면 해당 종목은 진입하지 않는다:
- 이미 보유 중인 종목 (중복 매수)
- 쿨다운 중인 종목 (최근 손절/TP 후 대기)
- 장 시작 직후 블랙아웃 구간 (`NO_NEW_ENTRY_MIN`)
- 장 마감 전 블랙아웃 구간 (`CLOSE_BEFORE_MIN`)
- 일일 손실 한도 초과 (HALT 상태)
- 분석가 평균 confidence 기준 미달

### `_sync_runtime_with_broker()` 주의사항

매 `run_cycle` 시작 시 `self.risk.cash`를 브로커 잔고로 **덮어씀**.
- 모의투자 US 잔고: `bal_us["cash"] == 0` 항상 반환 → KR 현금에서 US 포지션 원가 차감 패치 적용됨
- `session_start_equity`는 `session_open()` 1회에만 설정 — 재시작 직후 US 포지션 주입 시 기준값 부풀어 false halt 유발 가능
- 이슈 발생 시: `logs/system/` 확인 → 최소 수정 → `test_broker_sync_cash.py` 검증 순서

### 주요 데이터 흐름

```
state/brain.json               ← postmortem/tuner 업데이트, analysts 프롬프트에 요약 전달
data/ml/decisions.db           ← 매 사이클 write_decision(), 청산 시 update_exit()
                                  adaptive_params _perf_overlay가 읽어서 파라미터 보정
data/ticker_selection_log.db   ← Claude 종목 선택 이력 추적
state/open_positions.json      ← 봇 재시작 시 포지션 복구 (runtime, git 추적 제외)
logs/daily_judgment/           ← 세션별 판단 전문 JSON — 봇 재시작 시 판단 재사용 소스
```

**운영 이슈 분석 시 반드시 3종 세트를 함께 본다:**
`state/brain.json` + `data/ml/decisions.db` + `data/ticker_selection_log.db`
하나만 보면 전체 그림이 안 나온다.

**`data_source` 컬럼 구분 — 데이터 오염 방어:**
- `data_source='live'`: 실제 장중 실행 데이터
- `data_source='backfill'`: 과거 가격 CSV 기반 시뮬레이션 (`is_simulated=1`)

adaptive_params의 `_perf_overlay`는 기본적으로 `live` 데이터만 참조한다.
backfill 데이터를 live 성과에 섞으면 파라미터 보정이 오염된다. 절대 혼용 금지.

### 모듈 역할

| 경로 | 역할 |
|------|------|
| `trading_bot.py` | 메인 루프, `TradingBot` 클래스 (~4000줄, 모듈화 예정) |
| `kis_api.py` | KIS REST/WebSocket, Finnhub/FMP 시세, KR/US 스크리닝 |
| `indicators.py` | `calc_all()` — RSI/BB/MA/MACD/ATR 등 |
| `risk_manager.py` | 포지션/잔고/손익/halt, `HARD_RULES` 환경변수로 조정 |
| `strategy/*.py` | 각 전략의 `signal()`, `params()`, `diagnostics()` |
| `strategy/adaptive_params.py` | 4단계 파라미터 스택 (`_PARAMS_FN` dict에 전략 등록) |
| `minority_report/analysts.py` | Bull/Bear/Neutral 2라운드 Claude 토론 (R1=Haiku, R2=Sonnet) |
| `minority_report/consensus.py` | 분석가 적중률 가중치 합의 → mode/size |
| `minority_report/hold_advisor.py` | TP 시 HOLD/SELL/TRAIL 3명 투표 |
| `minority_report/postmortem.py` | 장 마감 사후 분석 → brain.json |
| `claude_memory/brain.py` | `state/brain.json` 읽기/쓰기 인터페이스 |
| `ml/db_writer.py` | `data/ml/decisions.db` 쓰기 인터페이스 |
| `universe_manager.py` | Core+Tier2 유니버스 스냅샷 관리 |
| `credit_tracker.py` | Anthropic API 토큰/비용 누적 |
| `telegram_commander.py` | 텔레그램 명령어 수신/처리 |
| `runtime_paths.py` | `state/`, `logs/` 경로 추상화 |
| `phase1_trainer/sim_runner.py` | 백테스트/그리드서치 실행기 |
| `dashboard/dashboard_server.py` | Flask 웹 대시보드 |

---

## 문서 구조

| 경로 | 용도 |
|------|------|
| `README.md` | 프로젝트 개요, 실행 예시 |
| `DATA.md` | 상태/로그/DB 파일 전체 설명 |
| `docs/trading_process.md` | 세션 시작·장중·청산 흐름 기준 문서 |
| `docs/KIS_API_TODO.md` | KIS API 확인/보완 작업 목록 |
| `docs/plans/` | **아직 적용하지 않은 설계안** — 코드에 반영된 것이 아님 |
| `docs/archive/` | 과거 개발 로그 (DEVLOG, DEBUG 기록 등) |

`docs/plans/` 안의 문서는 "구현 예정 아이디어"이지 현재 코드 동작이 아니다. 혼동 주의.

## TODO / 미완성 작업 목록

> 완료된 항목은 즉시 이 목록에서 삭제한다. 이 섹션이 현재 진행 중인 작업의 단일 기준이 된다.

### 진행 중

- [ ] **RiskManager KR/US 분리** — `self.risk` 단일 풀 → `risk_kr` / `risk_us` 분리
  - `kis_api.py` US 전용 자격증명 추가 (`KIS_ACCOUNT_NO_US`, `KIS_APP_KEY_US`)
  - `trading_bot.py` `__init__` / `_sync_runtime_with_broker` / `run_cycle` / `session_open` / `session_close` 전체 분기
  - 완료 전까지 `_sync_runtime_with_broker` 임시 패치 유지

### 예정

- [ ] **trading_bot.py 모듈화** — 7261줄 단일 파일을 13개 모듈로 분리 (토큰 효율 + 유지보수)
  → 상세 계획: [`docs/plans/MODULARIZATION.md`](docs/plans/MODULARIZATION.md)
- [ ] **entry_priority ML 연결** — `decisions.db` 데이터 충분히 쌓이면 ML 점수로 전환 (Phase 2)
- [ ] **KIS API 토큰 만료 대응** — 갱신 실패 시 동작 명세 및 안전장치
- [ ] **Claude 호출 비용 임계값** — 일일 이상 비용 기준선 및 알림

---

## 현재 미완 상태 — 작업 중인 구조 변경

### RiskManager KR/US 분리 (진행 중)

현재 `TradingBot`은 **단일 `self.risk` (RiskManager)** 로 KR/US를 공유 풀로 운영 중이다.

```python
# trading_bot.py __init__ — 현재 상태
self.risk = RiskManager(init_cash=init_cash, max_order_krw=max_order, market="KR")
```

이로 인해 존재하는 제약:
- 모의투자 중 US 잔고가 항상 0 반환 → KR 현금에서 US 포지션 원가를 차감하는 패치(`_sync_runtime_with_broker`)가 임시로 적용되어 있음
- KR/US 손익이 단일 `daily_return()`으로 합산되어 시장별 독립 리스크 관리 불가

목표 구조:
```python
self.risk_kr = RiskManager(market="KR", ...)
self.risk_us = RiskManager(market="US", ...)
# _rm(market) 헬퍼로 분기
```

**이 작업이 완료되기 전까지는 `self.risk` 관련 코드를 임의로 수정하지 않는다.**  
브로커 동기화·손익 계산 쪽을 건드려야 할 때는 반드시 개발자에게 현재 패치 상태를 확인 후 진행한다.

---

## 재시작 / 장애 복구 절차

봇이 비정상 종료되었을 때 아래 순서로 복구한다:

```
1. logs/system/trading_YYYYMMDD.log 확인
   → 마지막 정상 동작 시점, 에러 원인 파악

2. state/open_positions.json 확인
   → 내부 포지션 기록과 KIS 실제 잔고 대조
   → 불일치 시 KIS 앱에서 실제 체결 내역 기준으로 수정

3. state/pending_orders.json 확인
   → 미체결 주문이 남아 있으면 KIS에서 직접 취소 여부 확인

4. state/daily_baseline.json 확인 (존재 시)
   → session_start_equity 기준값 오염 여부 점검

5. 봇 재시작
   → logs/daily_judgment/YYYYMMDD_{MARKET}.json 이 있으면 Claude 토론 재사용
   → 없으면 session_open() 에서 새로 토론 시작
```

**장중 재시작 시 주의**: `_sync_runtime_with_broker()`가 첫 사이클에서 브로커 잔고를 덮어씀.  
내부 포지션과 브로커 잔고가 다르면 equity 오계산 → false halt 유발 가능. 반드시 2~3번 확인 후 재시작.

---

## 모의투자 → 실거래 전환 체크리스트

전환 전 아래 항목을 순서대로 확인한다. **하나라도 빠지면 실계좌에서 모의 설정으로 동작할 수 있다.**

```
[ ] 1. KIS 개발자 포털에서 실거래 APP_KEY / APP_SECRET 새로 발급
[ ] 2. .env 수정
       KIS_APP_KEY=<실거래 키>
       KIS_APP_SECRET=<실거래 시크릿>
       KIS_ACCOUNT_NO=<실거래 계좌번호>
       KIS_IS_PAPER=false

[ ] 3. state/kis_token.json 삭제 (모의투자 토큰 캐시 제거)

[ ] 4. 리스크 파라미터 재확인
       MAX_ORDER_KRW — 실거래 적정 금액으로 재설정
       MAX_DAILY_LOSS_PCT — 실거래 손실 한도 재설정
       MAX_POSITIONS — 실거래 포지션 수 재설정

[ ] 5. state/open_positions.json 초기화 (모의 포지션 제거)
[ ] 6. state/decisions.jsonl 초기화 (당일 모의 기록 제거)

[ ] 7. 소액 주문 1건으로 실체결 확인
       → KIS 앱에서 체결 내역 확인
       → 텔레그램 알림 정상 수신 확인

[ ] 8. MAX_ORDER_PCT 확인 — 총자산 대비 5% 캡이 실거래에서 자동 적용됨
```

전환 후 첫 세션은 반드시 대시보드와 텔레그램을 모니터링하며 시작한다.

---

## 전략 추가/수정 시

1. `strategy/` 아래 새 파일에 `signal()`, `params()`, `diagnostics()` 구현
2. `strategy/adaptive_params.py`의 `_PARAMS_FN` dict에 등록
3. `trading_bot.py`에서 import 후 신호 체인에 삽입
4. `params()`에서 `disabled=True` 반환 조건 명시 (mode별 HALT/DEFENSIVE 최소)

## Telegram 운영 명령어

```
/status          현재 모드·포지션·손익
/pos             보유 포지션 목록
/review          보유 포지션 즉시 Claude 재판단
/setorder [금액] 최대 주문금액 변경
/setloss [%]     일일 손실 한도 변경
/trail on|off    트레일링 스탑 ON/OFF
/entry on|off    entry_priority cutoff 활성/비활성
/brain           누적 학습 요약
/credit          AI 토큰/비용 사용량
```

## 커밋 전 체크리스트

```bash
git diff --stat        # 변경 규모와 파일 성격 확인
git status --short     # untracked 파일 확인
```

파일 성격이 "코드"인지 "runtime 산출물"인지 판단한 후 커밋한다.
판단 기준: **재시작/배포 시 이 파일이 덮어씌워지면 운영에 문제가 생기는가?** → Yes면 runtime.
