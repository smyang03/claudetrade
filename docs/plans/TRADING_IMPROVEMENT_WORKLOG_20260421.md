# Trading Improvement Worklog - 2026-04-21

> 2026-05-05 정리 메모: 이 문서는 과거 작업 로그로만 보관한다. 실행 가능한 최신 우선순위는 `docs/TODO_ROADMAP.md` 기준으로 본다. 필요한 항목이 있으면 이 파일을 직접 이어서 수정하지 말고 새 plan으로 재작성한다.

## 작업 중단 지점

사용자 요청에 따라 2026-04-21 현재 적용한 수정까지만 종료한다. 추가 구현, 테스트 확장, 시뮬레이션은 진행하지 않았다.

현재 검증 완료 범위:

- `python -m py_compile trading_bot.py minority_report\analysts.py bot\candidate_policy.py bot\log_sanitizer.py kis_api.py strategy\gap_pullback.py strategy\continuation.py strategy\param_tuner.py risk_manager.py telegram_reporter.py`
- 위 문법 컴파일은 통과했다.

아직 완료하지 않은 검증:

- 단위 테스트 실행
- 장중 루프 통합 테스트
- 백테스트/리플레이
- 한글 깨짐 전체 스캔
- 실제 paper/live 주문 경로 검증

## 이번 작업 목표

분석 결과의 우선순위는 다음이었다.

1. 판단 구조 개선: Claude 판단이 실제 종목 선택, 진입 전략 순서, 매수 권한에 연결되도록 개선
2. 데이터 구조 개선: 후보군/매수 후보/점수/전략별 성과가 나중에 학습 가능하게 남도록 개선
3. 수익률 개선: 추격 매수, 실행 불가 후보, 수익 방치, 낮은 점수 진입을 줄이는 방향
4. 버그/운영 리스크 개선: 파생 ETF 주문 실패, Telegram 토큰 노출 로그, param_tuner 오학습 방지

`docs/plans/MODULARIZATION.md` 기준으로 대규모 mixin 분리는 하지 않고, 기능 단위 보조 모듈을 먼저 추가하는 방식으로 진행했다.

## 완료한 수정

### 1. 후보군/상품 필터

추가 파일:

- `bot/candidate_policy.py`

적용 내용:

- KR 파생/레버리지/인버스 ETF 후보를 스크리너와 히스토리 필터 단계에서 제외하도록 추가
- 기본 제외 종목:
  - `114800`
  - `252670`
  - `252710`
  - `412570`
  - `462330`
- 키워드 제외:
  - `인버스`
  - `레버리지`
  - `선물`
  - `2X`
  - `곱버스`
  - `ETN`
- 환경변수:
  - `KR_UNTRADABLE_TICKERS`
  - `KR_BLOCK_ALL_ETF_PRODUCTS`

수정 파일:

- `kis_api.py`
- `trading_bot.py`

목적:

- 계좌가 살 수 없는 상품이 후보군에 들어와 반복 주문 실패하는 문제 차단

### 2. WATCH / TRADE_READY 분리

추가 파일:

- `bot/candidate_policy.py`

수정 파일:

- `minority_report/analysts.py`
- `trading_bot.py`

적용 내용:

- Claude 종목 선택 결과를 `watchlist`와 `trade_ready`로 분리
- `watchlist`는 넓은 감시 목록
- `trade_ready`는 실제 신규 매수 권한 목록
- 기존 `tickers`만 반환하던 legacy 응답도 호환
- `trade_ready`가 비어 있으면 신규 매수는 하지 않고 감시만 수행
- 기본 제한:
  - KR `KR_WATCHLIST_MAX=20`, `KR_TRADE_READY_MAX=10`
  - US `US_WATCHLIST_MAX=30`, `US_TRADE_READY_MAX=12`

목적:

- 후보군을 무조건 줄이지 않고, 넓은 감시는 유지하되 매수 권한만 좁히는 구조로 변경

### 3. Claude 종목 선택 프롬프트 개선

수정 파일:

- `minority_report/analysts.py`

적용 내용:

- 종목 선택 프롬프트에 다음 정보를 추가
  - BrainDB 요약
  - correction guide
  - 최근 param_tuner 전략 성과
- Claude에게 좋은 후보가 부족하면 `trade_ready`를 0개로 둘 수 있다고 명시
- 파생/레버리지/인버스 ETF, 저유동성, 초과열, 손절폭 과대 후보는 `trade_ready` 제외하도록 명시

목적:

- 장세 판단에만 쓰이던 학습 데이터를 종목 선택 단계에도 전달

### 4. 실제 매수 루프에 TRADE_READY gate 추가

수정 파일:

- `trading_bot.py`

적용 내용:

- `run_cycle()`에서 `today_tickers` 전체를 순회하되, `trade_ready_tickers`에 없는 종목은 `watch_only`로 신규 진입 차단
- 재시작 저장 파일에도 `selection_meta`, `trade_ready_tickers` 저장/복원
- 수동 재선택, 세션 시작, 재스크리닝, 튜닝 재선택, 부분 교체, 긴급 재판단 경로에 selection meta 연결

목적:

- “넓게 보되 아무거나 사지 않는” 구조 구현

### 5. KR 전략 순서에 Claude 판단 반영

수정 파일:

- `trading_bot.py`

적용 내용:

- 기존 KR 전략 순서:
  - `opening_range_pullback`
  - `gap_pullback`
  - `momentum`
  - `mean_reversion`
  - `volatility_breakout`
- 변경 후:
  - 분석가 3명 중 2명 이상이 같은 `suggested_strategy`를 제시하면 해당 전략을 먼저 시도
  - 나머지는 기존 순서 유지
- `관망`은 전략 없음으로 처리
- 다수결이 없으면 기존 순서 유지

목적:

- Claude 판단이 장세/종목 선정에만 머물지 않고 실제 진입 신호 순서에 반영되도록 개선

### 6. continuation 제한 강화

수정 파일:

- `strategy/continuation.py`

적용 내용:

- `MILD_BULL` 미만 모드에서는 continuation 비활성화
- 비활성 모드:
  - `HALT`
  - `DEFENSIVE`
  - `CAUTIOUS_BEAR`
  - `MILD_BEAR`
  - `NEUTRAL`
  - `CAUTIOUS`
  - `CAUTIOUS_BULL`

목적:

- CAUTIOUS 장세에서 장초 강한 갭을 눌림 없이 따라가는 손실 구조 축소

### 7. gap_pullback 눌림 조건 강화

수정 파일:

- `strategy/gap_pullback.py`
- `trading_bot.py`

적용 내용:

- 기존 조건:
  - `low >= open * 0.995`
- 변경 조건:
  - 고점 대비 실제 눌림 깊이 필요
  - 과도한 시가 이탈 제한
  - 저점 이후 회복 필요
- 추가 파라미터:
  - `pullback_min_pct`
  - `pullback_max_pct`
  - `opening_pullback_min_pct`
  - `opening_pullback_max_pct`
  - `open_drawdown_max_pct`
  - `recovery_min_pct`
  - `open_reclaim_buffer_pct`

목적:

- gap_pullback이 고점 추격 매수로 변질되는 문제 완화

### 8. entry_priority 실차단 및 DB 저장

수정 파일:

- `trading_bot.py`

적용 내용:

- `ENTRY_PRIORITY_CUTOFF_ENABLED` 기본값을 `false`에서 `true`로 변경
- cutoff 기본값은 기존처럼 `ENTRY_PRIORITY_CUTOFF=0.20`
- `BUY_SIGNAL`, `entry_priority_cutoff` 기록 시 `entry_priority_score` 컬럼에 실제 값 저장

목적:

- 낮은 점수 진입을 기본적으로 막고, 나중에 “몇 점 이상이 기대값이 좋은가”를 분석 가능하게 함

### 9. 수익 보호 자동 트레일링

수정 파일:

- `risk_manager.py`

적용 내용:

- 미실현 수익이 기본 `+3%` 이상이면 자동 트레일링 전환
- 본전 위 stop 보장
- 기본 trailing 폭 `4%`
- US 포지션은 USD 기준 `trail_sl_usd`도 함께 설정
- 환경변수:
  - `AUTO_PROFIT_TRAILING_ENABLED`
  - `AUTO_TRAIL_TRIGGER_PCT`
  - `AUTO_TRAIL_PCT`
  - `AUTO_BREAKEVEN_BUFFER_PCT`

목적:

- +5% 이상 수익 포지션이 trailing 없이 방치되는 문제 완화

### 10. param_tuner 전략별 성과 업데이트

수정 파일:

- `strategy/param_tuner.py`
- `trading_bot.py`

적용 내용:

- 기존에는 세션 전체 성과를 모든 전략 row에 동일하게 기록
- 변경 후 전략별 청산 결과가 있으면 해당 전략 row에만 해당 성과 기록
- 거래 없는 전략은 entries/wins/losses를 0으로 기록 가능

목적:

- Claude 파라미터 튜너가 momentum/gap_pullback/continuation 성과를 섞어서 잘못 학습하는 문제 완화

### 11. Telegram 토큰 로그 마스킹

추가 파일:

- `bot/log_sanitizer.py`

수정 파일:

- `telegram_reporter.py`
- `trading_bot.py`

적용 내용:

- Telegram URL의 `/bot...` 토큰 패턴 마스킹
- `token=`, `crtfc_key=` query 값 마스킹
- Telegram 전송 실패/재시도 로그와 startup health check에 적용

목적:

- 에러 로그에 봇 토큰이 그대로 노출되는 운영 리스크 제거

## 아직 해야 할 작업

### 1. 검증

- 단위 테스트 추가 및 실행
  - `bot/candidate_policy.py`
  - `bot/log_sanitizer.py`
  - `strategy/gap_pullback.py`
  - `strategy/continuation.py`
  - `risk_manager.py`
  - `strategy/param_tuner.py`
- 기존 테스트 실행
  - `test_broker_sync_cash.py`
  - `test_kr_trade.py`
  - `ml/test_full.py`
- 장중 루프 dry-run
- paper 모드 1세션 검증
- 실제 주문 전 `trade_ready` gate 로그 확인

### 2. 백테스트/리플레이

- 강화된 gap_pullback 조건으로 KR/US 백테스트 재실행
- continuation 제한 전후 비교
- entry_priority cutoff `0.20`, `0.35`, `0.50` 구간별 기대값 비교
- WATCH는 넓게, TRADE_READY는 좁게 했을 때 기회 손실과 손실 감소 비교

### 3. ticker_selection_log 확장

현재 `ticker_selection_db.py`에는 선택 종목과 signal score 중심으로 남는다.

추가 검토 필요:

- `watchlist_rank`
- `trade_ready`
- `veto_reason`
- `risk_tags`
- `recommended_strategy`
- `max_position_pct`

목적:

- Claude가 골랐지만 매수 권한을 주지 않은 종목의 사후 성과도 비교 가능하게 만들기

### 4. 알림/대시보드 개선

- watchlist alert에서 `WATCH`와 `TRADE_READY`를 분리 표시
- `watch_only` skip 카운트 표시
- product filter 제외 종목 별도 로그/알림 요약
- entry_priority cutoff 차단 종목 요약

### 5. sector play 예외 검토

현재 Tier2 sector play는 별도 경로다.

추가 검토 필요:

- KR sector ETF가 계좌 권한 문제를 다시 일으킬 수 있는지 확인
- sector play도 product filter 또는 별도 권한 체크를 타야 하는지 검토
- 일반 종목 전략과 ETF/헤지 전략 분리 여부 결정

### 6. 한글 인코딩 점검

아직 전체 완료하지 못했다.

해야 할 것:

- Python/Markdown 전체에서 mojibake 패턴 검색
- PowerShell 출력 인코딩 문제와 실제 파일 깨짐을 구분
- 발견 시 UTF-8 기준으로 수정

추천 검색 예:

```powershell
rg -n "�|遺|醫|援|湲|寃|濡|嫄|鍮|猷|洹|留|吏|理|媛" -g "*.py" -g "*.md" .
```

### 7. 모듈화 후속

이번 작업은 대형 분리가 아니라 기능별 작은 모듈 추가만 수행했다.

후속 모듈화 후보:

- candidate/selection policy 전용 패키지 확장
- entry gate를 `bot/entry_gate.py`로 분리
- strategy order resolver를 `bot/strategy_router.py`로 분리
- risk trailing policy를 `bot/risk_policy.py` 또는 `risk_manager` 하위 모듈로 분리
- Telegram/log sanitizer 공통 유틸 확장

## 주의할 변경점

- `ENTRY_PRIORITY_CUTOFF_ENABLED` 기본값이 `true`가 되어 거래 수가 줄 수 있다.
- `gap_pullback` 조건이 강화되어 기존 백테스트보다 진입 수가 줄 가능성이 높다.
- `continuation`은 MILD_BULL 이상에서만 살아남으므로 CAUTIOUS/NEUTRAL 장세의 장초 추격 매수는 크게 줄어든다.
- `trade_ready`가 0개면 watchlist가 있어도 신규 진입하지 않는다.
- 수익 +3% 이상 자동 트레일링이 켜지므로 일부 종목은 이전보다 빨리 trail_stop으로 청산될 수 있다.

## 다음 작업 우선순위

1. 현재 변경분 단위 테스트 작성 및 실행
2. 한글 깨짐 전체 스캔
3. paper 모드에서 `WATCH_ONLY`, `TRADE_READY`, `entry_priority_cutoff` 로그 확인
4. gap_pullback/continuation 백테스트 재실행
5. ticker_selection_log에 trade_ready/veto/risk_tags 저장 확장
6. 알림/대시보드에 watch/trade_ready 분리 표시
7. sector play ETF 권한/필터 정책 정리
