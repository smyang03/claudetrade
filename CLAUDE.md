# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 실행 명령

```bash
# 봇 실행 (모의투자 기본, KIS_IS_PAPER=true)
python trading_bot.py

# 실거래 모드 (KIS_IS_PAPER=false + 실거래 APP_KEY 필요)
python trading_bot.py --live

# 브로커 동기화 단위 테스트
python -m unittest test_broker_sync_cash.py -v

# ML DB 기능 검증
python ml/test_full.py

# 의존성 설치
pip install -r requirements.txt

# 구문 검사
python -c "import ast; ast.parse(open('trading_bot.py', encoding='utf-8').read()); print('OK')"

# 시뮬레이션 (KR+US, 2022년~)
python -m phase1_trainer.sim_runner --market ALL --engine both --start 2022-01-01 --top 15

# decisions.db 조회
python -c "import sqlite3; conn=sqlite3.connect('data/ml/decisions.db'); print(conn.execute('SELECT count(*) FROM decisions').fetchone())"
```

## 환경변수 (.env)

`.env.example`이 정본. 핵심 항목:

```
# 모의투자 vs 실거래
KIS_IS_PAPER=true          # true=모의투자 서버, false=실거래 서버 (TR 코드도 달라짐)

# Claude 모델
ANTHROPIC_MODEL=claude-sonnet-4-6   # R2 토론/청산 판단
R1_MODEL=claude-haiku-4-5-20251001  # R1 1차 판단 (비용 절감)

# 기능 플래그 (기본 OFF)
TRAILING_ANALYST_ENABLED=false  # TP 도달 시 hold_advisor 3명 투표
CLAUDE_PARAM_REVIEW=false       # adaptive_params 위에 Claude 4th 레이어
ENABLE_DYNAMIC_UNIVERSE=true    # 매 세션 시장 스캔으로 유니버스 구성

# 리스크 한도
MAX_DAILY_LOSS_PCT=-8.0    # 일일 손실 halt 기준 (HARD_RULES 기본 -3.0 덮어씀)
MAX_POSITIONS=10
MAX_ORDER_KRW=12000000

# 미국장 계좌 분리 (비워두면 KR 설정 사용)
KIS_ACCOUNT_NO_US=         # 실거래 전환 시 별도 계좌번호 입력
KIS_IS_PAPER_US=           # KR과 독립 설정 가능
US_MAX_POSITIONS=3
```

## 아키텍처 — 큰 그림

### 실행 흐름

```
main()
  └─ TradingBot.__init__()
       ├─ RiskManager(market="KR"), RiskManager(market="US")  ← 시장별 분리 예정
       └─ 캐시, 스케줄러, Telegram 초기화
  └─ session_open(market)
       ├─ KIS 토큰 갱신 / 잔고 동기화
       ├─ build_kr/us_digest()      → VIX/환율/뉴스/섹터 컨텍스트
       ├─ get_three_judgments()     → Bull/Bear/Neutral 2라운드 토론 (analysts.py)
       ├─ build_consensus()         → mode + size 결정 (consensus.py)
       ├─ screen_market_kr/us()     → 1차 스크리닝
       └─ select_tickers()          → Claude 최종 종목 선택
  └─ run_cycle() [5분 주기, schedule]
       ├─ _sync_runtime_with_broker()   ← 브로커 잔고/포지션 덮어씀 (중요)
       └─ run_entry_scan()              → 종목별 신호 → 매수
  └─ _on_tick() [WebSocket 실시간]
       └─ _process_exit_candidates()   → TP/SL/trailing/max_hold
  └─ session_close()
       └─ postmortem.run()             → brain.json 업데이트
```

### 진입 결정 파이프라인 (`run_entry_scan` 내부)

```
OHLCV 로드 (_get_ohlcv_cached, TTL=60분)
  └─ 당일봉 임시 주입 (session_active 시, _vol_missing 플래그 설정)
  └─ calc_all() → RSI/BB/MA/MACD/ATR/vol_ratio/gap_pct (indicators.py)
  └─ adaptive_params()              [4단계 파라미터 결정]
       1. base : strategy/*.params(mode, conf, market)
       2. regime: VIX/USD_KRW 보정 (_regime_overlay)
       3. perf  : decisions.db 최근 성과 반영 (_perf_overlay)
       4. guard : base 대비 이동 범위 클리핑 (_guardrail)
  └─ 신호 체인 (KR)
       OR눌림 → 갭눌림 → 모멘텀(45분 이후) → 평균회귀 → 변동성돌파
       → continuation(10~45분, 1회, size_mult=0.5)
  └─ 신호 체인 (US)
       분석가 투표 우선순위 → 각 전략 순서 → continuation fallback
  └─ entry_priority.compute()       → 점수 산출, cutoff로 진입 차단 가능
  └─ ml/db_writer.write_decision()  → BUY_SIGNAL / NO_SIGNAL / BLOCKED 기록
```

### `_sync_runtime_with_broker()` 동작과 주의사항

매 `run_cycle` 시작 시 호출되어 `self.risk.cash`를 브로커 잔고로 **덮어씀**.
- 모의투자 중 US 잔고: `bal_us["cash"] == 0` 항상 반환 → KR 현금에서 US 포지션 원가를 차감하는 패치 적용
- `session_start_equity`는 `session_open()` 1회에만 설정 — 재시작 직후 US 포지션이 주입되면 기준값이 부풀어 **false halt** 유발 가능
- 브로커 동기화 이슈는 감으로 수정하지 말고 `logs/system/` → 최소 수정 → `test_broker_sync_cash.py` 순서로 검증

### 주요 데이터 흐름

```
state/brain.json          ← postmortem / tuner 업데이트, analysts 프롬프트에 요약 전달
data/ml/decisions.db      ← 매 사이클 write_decision(), 청산 시 update_exit()
                            adaptive_params _perf_overlay가 읽어서 파라미터 보정
state/open_positions.json ← 봇 재시작 시 포지션 복구
data/universe/{KR,US}/    ← 날짜별 유니버스 스냅샷 (ENABLE_DYNAMIC_UNIVERSE=true 시 매일 갱신)
logs/daily_judgment/      ← 세션별 판단 전문 JSON (파인튜닝 raw 데이터)
state/api_usage.json      ← credit_tracker.py가 토큰/비용 누적
```

### 모듈 역할

| 경로 | 역할 |
|------|------|
| `trading_bot.py` | 메인 루프, `TradingBot` 클래스 전체 (~4000줄) |
| `kis_api.py` | KIS REST/WebSocket, Finnhub/FMP 시세, KR/US 스크리닝 |
| `indicators.py` | `calc_all()` — DataFrame에 RSI/BB/MA/MACD/ATR 등 추가 |
| `risk_manager.py` | 포지션/잔고/손익/halt 상태, `HARD_RULES` 환경변수로 조정 |
| `strategy/*.py` | 각 전략의 `signal()`, `params()`, `diagnostics()` |
| `strategy/adaptive_params.py` | 4단계 파라미터 스택 (`_PARAMS_FN` dict에 전략 등록) |
| `strategy/cross_asset.py` | VIX/환율/섹터 ETF 기반 추가 파라미터 보정 |
| `strategy/entry_priority.py` | 신호 강도 점수화 및 cutoff 판단 |
| `minority_report/analysts.py` | Bull/Bear/Neutral 2라운드 Claude 토론 (R1=Haiku, R2=Sonnet) |
| `minority_report/consensus.py` | 분석가 적중률 가중치 합의 → mode/size 결정 |
| `minority_report/hold_advisor.py` | TP 시 HOLD/SELL/TRAIL 3명 투표 (`TRAILING_ANALYST_ENABLED`) |
| `minority_report/postmortem.py` | 장 마감 후 사후 분석 → brain.json |
| `minority_report/tuner.py` | 장중 brain.json 인트라데이 업데이트 (param_tuner.py 호출) |
| `claude_memory/brain.py` | `state/brain.json` 읽기/쓰기 인터페이스 |
| `ml/db_writer.py` | `data/ml/decisions.db` 쓰기 인터페이스 |
| `ml/forward_updater.py` | 청산 후 forward_1d/3d/5d 후행 결과 기록 |
| `universe_manager.py` | Core+Tier2 유니버스 스냅샷 관리 (`US_CORE_TICKERS`, `KR_CORE_TICKERS`) |
| `credit_tracker.py` | Anthropic API 토큰/비용 누적 (state/api_usage.json) |
| `telegram_commander.py` | 텔레그램 명령어 수신/처리 (`/status`, `/setorder`, `/trail` 등) |
| `telegram_reporter.py` | 텔레그램 알림 발송 헬퍼 |
| `runtime_paths.py` | `state/`, `logs/` 등 런타임 경로 추상화 |
| `phase1_trainer/sim_runner.py` | 백테스트/그리드서치 실행기 |
| `dashboard/dashboard_server.py` | Flask 기반 웹 대시보드 |

## KIS API 작업 원칙

- **TR 코드, 파라미터, 응답 구조는 반드시 공식 문서 먼저 확인** — 추측하거나 유사 TR코드로 대체하지 않는다.
- 모의투자 TR: `VTTC` 계열 / 실거래 TR: `TTTC` 계열 — `IS_PAPER` 플래그로 분기.
- `kis_api.py` 전역 자격증명(`APP_KEY`, `ACCOUNT_NO` 등)은 모듈 로드 시 1회만 읽음. US 전용 자격증명은 `KIS_APP_KEY_US`, `KIS_ACCOUNT_NO_US` 등 별도 env로 분리 예정.

## 전략 추가/수정 시

1. `strategy/` 아래 새 파일에 `signal()`, `params()`, `diagnostics()` 구현
2. `strategy/adaptive_params.py`의 `_PARAMS_FN` dict에 등록
3. `trading_bot.py`에서 import 후 신호 체인에 삽입
4. `params()`에서 `disabled=True` 반환 조건 명시 (mode별 HALT/DEFENSIVE 최소)

## 계측 및 디버깅

- `rejection_reason` / `volume_state`는 `TradingBot._classify_rejection()`이 `none_detail` 문자열을 파싱해서 생성 — 전략 detail 포맷 변경 시 반드시 같이 수정
- `_funnel[market]`: selected → signaled → ordered → filled 단계별 카운터, 세션 종료 시 `logs/funnel/` 저장
- `logs/system/trading_YYYYMMDD.log`: 주문/청산/오류 전체 기록 — 이슈 진단 시 우선 확인
- `logs/analysis/`: 전략별 `no_signal`, `opening_candidate`, 신호 상세

## Telegram 운영 명령어 (주요)

```
/status     현재 모드·포지션·손익
/pnl        오늘 손익 + 분석가 성과
/pos        보유 포지션 목록
/review     보유 포지션 즉시 Claude 재판단 (SELL이면 즉시 매도)
/setorder [금액]   최대 주문금액 변경
/setloss [%]       일일 손실 한도 변경
/trail on|off      트레일링 스탑 ON/OFF
/entry on|off      entry_priority cutoff 활성/비활성
/brain      누적 학습 요약
/credit     AI 토큰/비용 사용량
```

## 커밋 전 체크리스트

```bash
# 코드 변경 외 runtime 파일이 섞였는지 반드시 확인
git status --short
git diff --stat
```

아래 파일은 `.gitignore`로 추적 제외 — 커밋에 포함하지 말 것:
- `state/open_positions.json`, `state/claude_control.json`, `state/decisions.jsonl`, `state/pending_orders.json`
- `data/universe/**` — 재생성 가능한 스냅샷
- `logs/**`, `data/cache/**`, `data/ml/**`

**`state/brain.json`만 예외** — 분석가 적중률/전략 성과 등 누적 학습 자산이므로 의도적으로 버전 관리.  
brain.json 업데이트 커밋은 코드 변경과 분리해서 "chore: brain.json 학습 누적 업데이트" 로 따로 남길 것.

## 변경 후 확인

```bash
# 진입/계측 관련 변경 후 로그 패턴 확인
grep -rn "rejection_reason\|volume_state\|entry_priority\|continuation\|hold_advisor" logs/analysis/ logs/system/ 2>/dev/null | tail -30
```
