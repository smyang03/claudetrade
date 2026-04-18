# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 프로젝트 철학

**"Claude가 판단하고, 규칙이 집행하고, 데이터가 기억한다"**

- Claude는 시장 분위기·종목 선택·청산 판단을 담당한다. 신호 발화 자체는 규칙 기반 전략이 한다
- 판단 결과는 무조건 `brain.json` / `decisions.db`에 누적된다. 쌓인 데이터가 다음 판단을 보정한다
- 모의투자로 먼저 검증하고, 구조가 안정되면 실거래로 전환한다. **안전 우선**
- 실거래는 KR/US 계좌 분리 구조로 진화할 예정이다

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

코드 수정 시 "이 변경이 누적 데이터에 어떤 영향을 주는가"를 반드시 먼저 생각한다.

### 로그는 분석의 원천 — 충분히 남긴다

**에러 로그와 실행 로그는 충분히 작성되어야 한다. 로그가 없으면 분석할 수 없다.**

- 예외 발생 시 `log.exception(...)` 또는 `log.error(..., exc_info=True)`로 스택트레이스를 남긴다
- 브로커 API 호출 결과(성공/실패/응답값)는 반드시 로깅한다
- 진입 차단 사유(`rejection_reason`), 신호 발화 조건, 파라미터 결정 과정은 `logs/analysis/`에 기록한다
- 판단 로그(`logs/daily_judgment/`, `logs/judgment/`)는 Claude가 왜 그 결정을 했는지 추적할 수 있어야 한다
- 로그를 줄이는 방향으로 수정하지 않는다. 운영 중 이슈를 사후에 재현할 수 있어야 한다
- 로그 레벨 기준: `DEBUG` 반복 루프 내부 / `INFO` 주요 이벤트 / `WARNING` 비정상 but 복구 가능 / `ERROR` 즉시 확인 필요
- **코드 작성/수정 중 로그를 건드릴 때**: 추가는 자유롭게, 삭제·축소는 반드시 개발자에게 먼저 확인한다
  - "불필요해 보이는 로그"도 Claude의 사후 분석·파라미터 보정·데이터 수집에 쓰일 수 있다
  - 삭제하기 전에 "이 로그가 decisions.db / brain.json / postmortem 어디에도 안 쓰이는가"를 먼저 확인한다

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

3. **구현 전에 동작을 검증한다**
   코드를 추가하고 "동작할 것"이라 가정하지 않는다. 특히 브로커 연동은 반드시 실제 응답으로 확인.

4. **배포 전 구문 검사를 반드시 실행한다**
   ```bash
   python -c "import ast; ast.parse(open('trading_bot.py', encoding='utf-8').read()); print('OK')"
   ```

5. **전략 detail 포맷을 바꾸면 `_classify_rejection()`도 같이 수정한다**
   `rejection_reason` / `volume_state`는 `none_detail` 문자열 파싱으로 생성된다.

6. **.env.example에 환경변수를 추가할 때, 코드에서 실제로 읽는지 먼저 확인한다**
   코드 연결 없이 설정만 늘리면 운영자에게 잘못된 기대를 심어준다.
   연결 전이면 반드시 주석에 `※ 코드 미연결 — [작업명] 완료 후 동작` 을 명시한다.

### Git 원칙

7. **커밋 전 반드시 `git diff --stat`으로 내용을 확인한다**
   `git status`는 파일 목록만 보여준다. 무엇이 왜 바뀌었는지는 diff로 확인한다.

8. **코드 커밋에 runtime 파일을 섞지 않는다**
   아래 파일은 `.gitignore` 추적 제외 대상이다. 절대 코드 커밋에 포함하지 않는다:
   - `state/open_positions.json` — 운영 포지션, 배포 시 덮어쓰면 포지션 오염
   - `state/claude_control.json` — 환경 간 복제 금지
   - `state/decisions.jsonl` — 장중 누적 로그
   - `state/pending_orders.json` — 미체결 주문 상태
   - `data/universe/**` — 날짜별 재생성 스냅샷
   - `logs/**`, `data/cache/**`, `data/ml/**`, `__pycache__/**`

9. **`state/brain.json`만 버전 관리 예외다**
   분석가 적중률·전략 성과 누적 자산이므로 의도적으로 추적한다.
   단, brain.json 업데이트는 코드 변경과 **반드시 별도 커밋**으로 분리한다:
   ```
   chore: brain.json 학습 누적 업데이트 (YYYY-MM-DD)
   ```

10. **버그 하나 = 커밋 하나**
    여러 버그를 한 커밋에 묶으면 나중에 원인 추적(bisect)이 불가능해진다.

11. **`git rm --cached` 전에 `git ls-files <경로>`로 tracked 목록을 먼저 확인한다**

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
