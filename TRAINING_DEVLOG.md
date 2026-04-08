# TRAINING DEVLOG

`claudetrade`의 실행, 매매, 브레인 학습, KIS 연동 관련 변경 이력을 모은 문서다.

범위:
- `trading_bot.py`
- `kis_api.py`
- `risk_manager.py`
- `minority_report/*`
- `claude_memory/*`
- `telegram_reporter.py`
- `telegram_commander.py`
- `phase1_trainer/*`

참고:
- 원본 전체 로그: [DEVLOG.md](/E:/code/claudetrade/DEVLOG.md)
- 대시보드 전용 로그: [DASHBOARD_DEVLOG.md](/E:/code/claudetrade/DASHBOARD_DEVLOG.md)

## 구조 요약

기존:
- 가격 데이터 수집
- `historical_sim.py` 시뮬레이션
- `brain.json` 사전학습

현재:
- 봇 즉시 실행
- `session_open` / `run_cycle` / `session_close` 단위로 실제 판단과 결과 누적
- `brain.json`, `daily_judgment`, `trade_log`, `decision_event_log`를 다음 세션 학습 재료로 사용

핵심 파일:
- [trading_bot.py](/E:/code/claudetrade/trading_bot.py)
- [kis_api.py](/E:/code/claudetrade/kis_api.py)
- [minority_report/postmortem.py](/E:/code/claudetrade/minority_report/postmortem.py)
- [claude_memory/brain.py](/E:/code/claudetrade/claude_memory/brain.py)

## 주요 변경 이력

### 2026-03 실행 루프 / 판단
- `session_open()`에서 USD/KRW 자동 갱신 추가.
- 2라운드 토론 메타데이터를 `today_judgment`와 training record에 보존.
- 긴급 재판단(`_should_reinvoke_analysts`, `_reinvoke_analysts`) 추가.
- `session_events`를 장중 누적 후 `session_close()` 결과물에 포함.
- `session_close()` training record를 실제 운영 데이터 중심으로 정리.

### 2026-03 Postmortem / Brain
- `postmortem.py` 전면 개편.
- 체결 내역 기반 `strategy_pnl`, `best_trade`, `worst_trade`, `correction_guide` 정비.
- `brain.json` 누락 모드 보강.
- `brain_backfill.py`로 과거 `daily_judgment` 파일에서 brain 소급 갱신 가능하게 함.
- `decision_event_log`를 postmortem과 Brain 학습 재료에 포함.
- `execution_patterns`, `execution_lessons`, `execution_stats` 축 누적 시작.

### 2026-03 KIS API 정비
- KR/US 주문 TR 및 바디를 공식 문서 기준으로 재점검.
- KR/US 잔고, 시세, 체결 조회 경로 보강.
- 주문 응답 정규화 추가.
- 주문 사전체크(`precheck_order`)와 전역 레이트리밋 도입.
- stale 잔고 캐시로 인한 매도 오판을 `force_refresh` 재조회로 보완.

### 2026-03 포지션 / pending / 체결 정합성
- 브로커 잔고 기준 포지션 주입 및 런타임 동기화 강화.
- `pending_orders` 영속화 및 동일 티커 최신 1건 유지.
- 재시작 시 pending을 무작정 삭제하지 않고 정규화만 수행.
- `already_holding`, `pending_order` 진입 차단 강화.
- `exit_price <= 0` 방어 추가.
- `모의투자 잔고내역이 없습니다` 매도 실패 시 stale 내부 포지션 정리 강화.
- KR/US 주문번호 기준 체결 조회 연결.

### 2026-03 텔레그램
- `status_report()` / `dashboard_push()`를 브로커 기준 가격 우선으로 재정의.
- `signal_alert`, `watchlist_alert`, `decision_event_alert` 추가.
- 실시간 판단 이유, 매수/매도/실패 이유를 텔레그램으로 전송.
- `/claude` 제어 상태를 파일 기반 제어와 연동.

## 운영 중 핵심 이슈와 조치

### 분석가 연속 실패 피드백 강화 (2026-03-28 추가)
- `generate_analyst_summary()` — 연속 실패 3일 이상 시 강도 높은 경고 메시지 전달.
- 기존 `trend: "stable"` 오판 방지 (0/7 vs 0/11 → 동일 base rate, trend 미감지 구조).
- 연속 5일+ 실패: "체계적으로 틀리고 있음, 반드시 NEUTRAL 이하" 강제 지시.
- `generate_prompt_summary()` 분석가 신뢰도 라인에 `⛔N연속실패` 배지 추가.
- `_count_consecutive_result()` 헬퍼 함수 추가 (`brain.py`).

### VIX 동적 포지션 사이즈 (2026-03-28 추가)
- VIX가 임계값 초과 시 US 세션 consensus size 자동 축소.
- 기준: VIX 20+ →×0.85 / 25+ →×0.70 / 30+ →×0.55 (`VIX_MULT_20/25/30` 환경변수).
- session_open 및 _reinvoke 경로 모두 적용.
- `today_judgment["digest_raw"]` 저장으로 재판단 시 원본 VIX 접근 보장.

### 손절 후 즉시 재매수 (2026-03-28 수정)
- 손절/트레일링 청산 직후 같은 종목을 4~5초 만에 재매수하던 문제 수정.
- `_entry_blocked` dict로 티커별 차단 만료 시각을 관리.
- 손절(`stop_loss` / `trail_stop`) 후 **20분** 재진입 금지 (`STOP_COOLDOWN_MIN` 환경변수로 조정).
- 세션 시작 시 `_entry_blocked` 초기화 — 전 세션 쿨다운이 이월되지 않음.

### TP 청산 후 즉시 재진입 방지 (2026-03-28 수정)
- TP 청산 직후 동일 종목 **10분** 재진입 차단 추가.
- 수익 확정 후 조정 구간 재진입으로 수익을 반납하던 패턴 차단.
- `TP_COOLDOWN_MIN` 환경변수로 조정 가능 (기본 10분).

### 분석가 confidence 필터 (2026-03-28 추가)
- 3명 분석가 평균 confidence가 0.4 미만이면 해당 사이클 전 종목 신규 진입 스킵.
- 불확실 시장에서 억지로 진입하던 동작 차단.
- `MIN_ENTRY_CONF` 환경변수로 임계값 조정 가능.

### US 전략 다양화 — 분석가 다수결 전략 우선 (2026-03-28 추가)
- US 세션에 volatility_breakout 단일 전략만 쓰던 구조를 다전략으로 확장.
- 분석가 3명의 `suggested_strategy` 다수결(2표 이상) → 해당 전략 우선 시도.
- 다수결 없으면 volatility_breakout 기본 유지.
- 투표 전략이 volatility_breakout이 아니면 [voted, volatility_breakout] 순서로 시도.
- `_STRATEGY_NAME_MAP`, `_analyst_strategy_vote()` 추가 (`trading_bot.py`).

### max_pyramid 마켓 구분 (2026-03-28 수정)
- `can_open()` 피라미딩 카운트에 market 필터 추가 (`risk_manager.py:91`).
- 기존: 전체 포지션에서 동일 티커 수 카운트 → KR/US 합산 오판 가능.
- 변경: market 파라미터 있으면 해당 마켓 포지션만 카운트.

### consensus 30일 가중치 점진 적용 (2026-03-28 수정)
- `consensus.py:_get_weights()` recent_30d 혼합 가중치를 데이터 건수 기반 점진 적용.
- 기존: 5건 이상이면 무조건 60% — 7일 데이터에도 60% 반영돼 노이즈 과반영.
- 변경: 5~9건 30% / 10~19건 45% / 20건 이상 60%.

### 합의 강도 기반 사이즈 자동 조정 (2026-03-28 추가)
- 3:0 만장일치 → size x1.3 (확신 높음)
- 2:1 분열 → size x0.85
- 1:1:1 완전분열 → size x0.75 (확신 낮음)
- `minority_report/consensus.py` `build_consensus()` 내 적용.

### 중복 매수 / 재시작
- 재시작 후 pending 초기화로 브로커 체결분을 못 보고 재매수하던 문제 수정.
- 브로커 보유를 런타임 포지션에 먼저 주입하도록 보강.
- 매수 주문 접수 즉시 동일 종목 **15분** 재주문 차단 추가 (2026-03-28).
  - VTS 체결 반영 지연으로 pending reconcile 전에 재주문되던 문제 근본 차단.
  - `BUY_COOLDOWN_MIN` 환경변수로 조정 가능.

### 0원 청산
- `price=0` 상태에서 내부 손절이 발생하던 문제 수정.
- 현재가가 0이면 청산 로직을 진행하지 않도록 방어.

### 시장 혼선
- KR 세션이 US 포지션을 청산하거나, 반대로 잘못된 주문 경로를 타던 문제 수정.
- 현재 시장 포지션만 청산/관리하도록 분리.

### VTS 불안정
- KR/US 잔고조회, 미국 `dailyprice` 500 대응 캐시와 폴백 강화.
- 미국 일부 종목 주문 실패는 VTS 지원 편차를 고려해 차단/캐시.

### 주문 접수와 체결 구분
- 과거엔 주문 접수만으로 내부 포지션 생성.
- 지금은 pending → 잔고/체결 조회 확인 후 반영 구조로 이동.

### trade_log 중복 기록 (2026-03-28 수정)
- 재시작 등으로 동일 체결이 `trade_log`에 2회 기록되던 문제 수정.
- `session_close()` 시 `order_no` 기준으로 dedup 후 저장.

## 현재 기준 소스 오브 트루스

체결/보유:
- 브로커 잔고 + 주문번호 체결조회

학습:
- `daily_judgment/*.json`
- `trade_log`
- `decision_event_log`
- `brain.json`

세션:
- `session_open`
- 장중 `run_cycle`
- `session_close`

## 남은 TODO

- US 체결조회 응답 필드 추가 검증 로그 축적
- 취소/정정 주문 API 연결
- 주문 가능/정정 가능 사전체크 확대
- 장 종료 미실행 시 이전 세션 `session_close` 자동 복구 검토
- 프리마켓 등락 신뢰도 개선 (yfinance preMarketPrice 지연 이슈)
- 시장 폭 지표 (A/D ratio, 신고가/신저가) 수집 경로 확보
- momentum 전략 파라미터 추가 최적화 (현재 vol_mult 1.5×)

---

## [2026-03-28] 전략 / 학습 / 시장정보 대규모 개선

### digest_builder.py
- **뉴스 폴백**: 당일 뉴스 파일 없으면 최근 5일 내 파일 자동 사용 (기존 `top_news: 0` 버그 수정)
- **영문 뉴스 스코어링 개선**: AlphaVantage 영문 제목 키워드 대폭 확장, 펀드 보유 공시 패널티 강화
- **중복 뉴스 제거**: `filter_top_news()` 에 60자 기준 dedup 추가
- **섹터 ETF**: `fetch_live_context_us()` 에 XLK/XLF/XLE/XLY 등락 추가
- **채권/신용**: 10년 국채금리(`^TNX`) + 하이일드 ETF(`HYG`) 추가
- **ATR 포함**: `layer_b`에 `atr_pct` 추가 (기존 계산했으나 프롬프트 미포함)
- **5일 추세**: `trend_5d` (선형회귀 기울기 %/일) 추가
- **실적 발표일**: `_yf_earnings_date()` 추가, `layer_b.earnings_date`
- **프리마켓**: `_yf_premarket()` 추가, `layer_b.premarket_pct`
- **시장 레짐 자동 감지**: `detect_market_regime()` → `context.regime` (trending_bull/trending_bear/ranging/high_vol/crash)
- **유니버스 확대**: US (MSFT/META/AMZN/GOOGL 추가), KR (카카오/현대차/LG화학 추가)
- **인버스 ETF**: SQQQ/KODEX인버스(114800) 추가 — VIX 25+/VKOSPI 20+ 구간만 유니버스에 포함

### trading_bot.py
- **US CAUTIOUS_BEAR 이하 진입 차단**: `US_BEAR_BLOCK_MODES` 환경변수로 설정 가능 (기본: HALT,DEFENSIVE,CAUTIOUS_BEAR)
- **인버스 ETF 진입 제한**: SQQQ/114800은 MILD_BEAR 이하 모드에서만 매수 허용

### minority_report/analysts.py
- **R1 분석가 Haiku**: R1 (1라운드) 콜을 Haiku(`claude-haiku-4-5-20251001`)로 교체 — 비용 절감
- `R1_MODEL` 환경변수로 오버라이드 가능

### minority_report/postmortem.py
- **HIT/MISS 코드 판정**: `_code_judge_hit_miss()` 추가 — Claude 자기평가 편향 제거
- 분석가 스탠스 + 실제 시장 등락률 비교로 객관 판정
- Claude 응답의 bull_result/bear_result/neutral_result를 코드 판정으로 덮어씌움

### strategy/momentum.py
- **vol_mult 완화**: 2.0× → 1.5× (기본), 모드별 차등 적용
- `vol_mult` 파라미터화 → params dict에 포함

### phase1_trainer/backtester.py (신규)
- 전략 백테스트 프레임워크 구축
- `indicators.calc_all()` 사용 (trading_bot과 동일 컬럼)
- 데이터 부족 시 graceful 스킵 (MIN_ROWS=60 미만 → 스킵)
- 성과 지표: n_trades, win_rate, avg_pnl, profit_factor, max_drawdown, sharpe
- CLI: `python phase1_trainer/backtester.py --market US --start 2025-01-01`
- 백테스트 결과: US(VB +1.11%, MR +1.49%), KR(gap_pullback +1.81% 승률69.6%)

---

## [2026-03-28] Claude 재판단 시작 시 자동 ON

봇 시작 시 `claude_control` 복구 단계에서 `enabled=true`를 강제로 적용하도록 변경했다.

- 목적:
  - 재시작 후 의도치 않게 OFF 상태로 남는 상황 방지
  - 자동 재판단 운영 기본값을 ON으로 고정

적용 위치:
- `trading_bot.py`
  - `_restore_claude_control()`

주의:
- 수동으로 OFF를 눌러도, 봇을 다시 시작하면 기본값은 다시 ON이다.
## [2026-04-01] 후보군 확장 및 히스토리 보강 정리

### trading_bot.py
- **히스토리 보강 큐 추가**: `_hist_fill_queue`, `_hist_fill_queued`, `_hist_fill_inflight`, `_hist_fill_last_ts`
- **백그라운드 히스토리 수집**: `_history_fill_worker()`가 데이터 부족 종목의 OHLCV를 365일치 보강
- **동기 보강 추가**: `session_open` 직전 `_prefill_history_sync()`로 부족 종목 일부를 즉시 보강 시도
- **히스토리 필터 강화**: `_filter_candidates_by_history()`는 데이터 부족 종목을 로그로 남기고 보강 큐에 등록
- **부분 재선택 추가**: `_partial_reselect()`가 120분마다 하위 2~3개 종목을 교체
- **무신호 추적**: `_ticker_no_signal_cycles`로 연속 무신호 사이클을 누적
- **재진입 쿨다운**: `_get_cooldown_excluded()`로 제외 종목의 단기 재편입을 제한
- **consensus 접근 방어 보강**: `run_tuning()` 내 일부 직접 접근을 `.get()`/`setdefault()` 패턴으로 정리

### minority_report/analysts.py
- **최종 선택 수 확대**: Claude 최종 선택을 `최소 5개, 최대 8개`로 확장
- **상한 반영**: 결과 슬라이스를 `[:8]`로 변경
- **보충 로직 추가**: 5개 미만이면 거래량 상위 후보로 보충
- **주석 정리**: `select_tickers()` 설명을 실제 동작(`최소 5개, 최대 8개`)과 맞춤

### 비고
- `_filter_candidates_by_history()`는 현재 `filtered`만 반환하고, 탈락 종목은 로그/큐 등록에 사용한다.
- 즉시 선택 전에 모든 부족 종목을 다 채우는 구조는 아니고, 일부 동기 보강 + 나머지 백그라운드 보강 구조다.
