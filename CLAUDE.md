# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 실행 명령

```bash
# 봇 실행 (모의투자 기본)
python trading_bot.py

# 시뮬레이션 (KR+US, 2022년~)
python -m phase1_trainer.sim_runner --market ALL --engine both --start 2022-01-01 --top 15

# 구문 검사
python -c "import ast; ast.parse(open('trading_bot.py', encoding='utf-8').read()); print('OK')"

# decisions.db 조회 예시
python -c "import sqlite3; conn=sqlite3.connect('data/ml/decisions.db'); print(conn.execute('SELECT count(*) FROM decisions').fetchone())"
```

## 환경변수 (.env)

```
# KIS API
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_IS_PAPER=true          # true=모의, false=실투
KIS_BASE_URL=...           # 미설정 시 IS_PAPER에 따라 자동 선택

# Claude API
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-6   # R2 토론/청산 판단
R1_MODEL=claude-haiku-4-5-20251001  # R1 1차 판단 (비용 절감)

# 기능 플래그
TRAILING_ANALYST_ENABLED=false  # true 시 TP 도달 시 hold_advisor 3명 투표
CLAUDE_PARAM_REVIEW=false       # true 시 adaptive_params 위에 Claude 4th 레이어 추가

# 리스크 (기본값이 있으므로 필요 시만)
MAX_DAILY_LOSS_PCT=-3.0
MAX_POSITIONS=3
MAX_ORDER_KRW=500000
PAPER_CASH=10000000
```

## 아키텍처 — 큰 그림

### 실행 흐름

```
main()
  └─ TradingBot.__init__()          # RiskManager KR/US 각각, 캐시 초기화
  └─ session_open()                 # 장 시작 시 1회
       ├─ KIS 토큰 갱신 / 잔고 동기화
       ├─ build_kr/us_digest()      # VIX/환율/뉴스/섹터 컨텍스트
       ├─ get_three_judgments()     # analysts.py → Bull/Bear/Neutral 2라운드 토론
       ├─ build_consensus()         # consensus.py → mode (AGGRESSIVE~HALT) + size 결정
       ├─ screen_market_kr/us()     # 1차 스크리닝
       └─ select_tickers()          # Claude 최종 종목 선택
  └─ run_cycle() (5분 주기)
       ├─ _sync_runtime_with_broker()
       └─ run_entry_scan()          # 종목별 신호 탐색 → 매수
  └─ _on_tick() (WebSocket 실시간)
       └─ _process_exit_candidates() # TP/SL/trailing/max_hold
  └─ session_close()
       └─ postmortem.run()          # Claude 사후 분석 → brain.json 업데이트
```

### 진입 결정 파이프라인 (`run_entry_scan` 내부)

```
OHLCV 로드 (_get_ohlcv_cached, TTL=60분)
  └─ 당일봉 임시 주입 (session_active 시, _vol_missing 플래그 설정)
  └─ calc_all() → RSI/BB/MA/MACD/ATR/vol_ratio/gap_pct
  └─ adaptive_params()              # 4단계 파라미터 결정
       1. base: strategy/*.params(mode, conf, market)
       2. regime: VIX/USD_KRW 보정 (_regime_overlay)
       3. perf: decisions.db 최근 성과 반영 (_perf_overlay)
       4. guard: base 대비 이동 범위 클리핑 (_guardrail)
  └─ 신호 체인 (KR)
       OR눌림 → 갭눌림 → 모멘텀(45분 이후) → 평균회귀 → 변동성돌파
       → continuation(10~45분, 1회, size_mult=0.5)
  └─ 신호 체인 (US)
       분석가 투표 우선순위 → 각 전략 순서 → continuation fallback
  └─ entry_priority.compute()       # 점수 산출, 설정 시 cutoff로 진입 차단 가능
  └─ ml/db_writer.write_decision()  # BUY_SIGNAL / NO_SIGNAL / BLOCKED 기록
```

### 주요 데이터 흐름

```
state/brain.json          ← postmortem / tuner 가 업데이트, analysts에게 요약 전달
data/ml/decisions.db      ← 매 사이클 write_decision(), 청산 시 update_exit()
                            adaptive_params의 _perf_overlay가 읽어서 파라미터 보정
state/open_positions.json ← 재시작 복구용 영속 파일
logs/daily_judgment/      ← 세션별 판단 전문 JSON (파인튜닝 raw 데이터)
```

### 모듈 역할

| 경로 | 역할 |
|------|------|
| `trading_bot.py` | 메인 루프, `TradingBot` 클래스 전체 |
| `kis_api.py` | KIS REST/WebSocket, Finnhub 시세, 스크리닝 함수 모음 |
| `indicators.py` | `calc_all()` — DataFrame에 모든 지표 추가 |
| `risk_manager.py` | 포지션/잔고/손익/리스크 상태, `HARD_RULES` 환경변수로 조정 |
| `strategy/*.py` | 각 전략의 `signal()`, `params()`, `diagnostics()` |
| `strategy/adaptive_params.py` | 4단계 파라미터 스택 진입점 `adaptive_params()` |
| `strategy/cross_asset.py` | VIX/환율/섹터 ETF 기반 추가 파라미터 보정 |
| `strategy/entry_priority.py` | 신호 강도 점수화 및 cutoff 판단 지원 |
| `minority_report/analysts.py` | Bull/Bear/Neutral 2라운드 Claude 토론 |
| `minority_report/consensus.py` | 분석가 적중률 가중치 합의 → mode/size 결정 |
| `minority_report/hold_advisor.py` | TP 시 HOLD/SELL 3명 투표 (TRAILING_ANALYST_ENABLED) |
| `minority_report/postmortem.py` | 장 마감 후 사후 분석 → brain.json |
| `minority_report/tuner.py` | 장중 brain.json 인트라데이 업데이트 |
| `claude_memory/brain.py` | brain.json 읽기/쓰기 인터페이스 |
| `ml/db_writer.py` | decisions.db 쓰기 인터페이스 |
| `ml/forward_updater.py` | 청산 후 forward_1d/3d/5d 후행 기록 |
| `ml/backfill.py` | 과거 price CSV → decisions.db 시뮬 행 생성 |
| `phase1_trainer/sim_runner.py` | 백테스트/그리드서치 실행기 |
| `universe_manager.py` | Core+Tier2 유니버스 스냅샷 관리 |
| `credit_tracker.py` | Anthropic API 토큰/비용 누적 추적 |
| `runtime_paths.py` | 런타임 경로 추상화 (state/, logs/ 등) |

## 작업 원칙

- KIS API TR코드, 파라미터, 응답 구조는 **반드시 공식 문서를 먼저 확인**한다. 추측하거나 유사 TR코드로 대체하지 않는다.
- 런타임 산출물(`state/`, `logs/`, `data/universe/`)은 운영 중 계속 바뀌므로, 버그 수정과 직접 관련 없는 변경은 커밋하지 않는다.
- 브로커/시세 연동 이슈는 감으로 수정하지 말고, 로그 확인 → 최소 수정 → 샘플 검증 순서로 진행한다.
- 전략/계측 수정 시 문자열 포맷에 의존하는 분류 로직이 있는지 함께 확인한다.

## 전략 추가/수정 시

1. `strategy/` 아래 새 파일에 `signal()`, `params()`, `diagnostics()` 구현
2. `strategy/adaptive_params.py`의 `_PARAMS_FN` dict에 등록
3. `trading_bot.py`에서 import 후 신호 체인에 삽입
4. `params()`에서 `disabled=True` 반환 조건을 명시적으로 정의할 것 (mode별 HALT/DEFENSIVE 최소)

## 계측 및 디버깅

- `rejection_reason` / `volume_state`는 `TradingBot._classify_rejection()`에서 `none_detail` 문자열 파싱으로 생성. 전략 detail 문자열 포맷 변경 시 반드시 같이 수정
- `_funnel[market]` dict: selected → signaled → ordered → filled 단계별 누적 카운터, 세션 종료 시 `logs/funnel/` 저장
- `none_detail` 구성 요소: OR눌림/갭눌림/모멘텀/평균회귀/변동성돌파/연속진입 각각 `f-string` 형식이 정해져 있음 — 정규식 패턴과 일치 필수

## 변경 후 확인

```bash
# 문서와 직접 관련 없는 런타임 파일이 섞였는지 먼저 확인
git status --short

# 진입/계측 관련 변경 후 자주 보는 로그 패턴
rg -n "rejection_reason|volume_state|entry_priority|continuation|hold_advisor" logs/analysis logs/system
```
