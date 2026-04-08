# DATA.md

`claudetrade`가 생성하거나 유지하는 주요 데이터 파일 설명입니다.

## 1. state/

런타임 상태 파일입니다. 봇 재시작 시 복구에 사용됩니다.

- `state/brain.json`
  - 분석가 적중률, 모드 성과, 전략 성과, 교훈, 최근 기록
- `state/live_status_KR.json`
- `state/live_status_US.json`
  - 현재 모드, 감시 종목, 포지션, 예산, 최근 신호 요약
- `state/pending_orders.json`
  - 미체결 주문 추적
- `state/us_screen_cache.json`
  - US 스크리너 후보 캐시
- `state/kis_token.json`
  - KIS 인증 토큰

## 2. logs/

운영 로그입니다.

- `logs/system/`
  - 세션 시작, 주문, 청산, 오류, 재선정
- `logs/analysis/`
  - 전략별 `no_signal`, `opening_candidate`, 신호 상세
- `logs/judgment/`
  - Claude 판단/토론 이벤트
- `logs/daily_judgment/`
  - 일별 판단 결과 요약 JSON

## 3. data/price/

가격 데이터 저장소입니다.

- `data/price/kr/`
  - KR 일봉 CSV
- `data/price/us/`
  - US 일봉 CSV

## 4. data/cache/

지표 계산 캐시입니다.

- 종목별 indicator pickle
- 재계산 비용을 줄이기 위한 임시 저장

## 5. data/daily_digest/

세션 오픈 전에 만드는 시장 컨텍스트 스냅샷입니다.

- KR/US별 daily digest JSON
- 뉴스, 지수, 변동성, 환율, 섹터 흐름 요약

## 6. data/news/

일자별 뉴스/공시 원본 저장소입니다.

- `data/news/kr/`
- `data/news/us/`

## 7. data/ml/

학습/분석용 DB와 부가 산출물입니다.

- `data/ml/decisions.db`
  - `BUY_SIGNAL`, `NO_SIGNAL`, `BLOCKED`, 체결 결과 등
  - `entry_priority_score` 포함
- `data/ticker_selection_log.db`
  - 선택 종목 추적용 DB
- `ml/analysis_outputs/`
  - 후처리 분석 결과

## 8. data/backtest/

백테스트, 그리드서치, 시뮬레이션 산출물입니다.

- `grid_*`
- `adaptive_*`
- `sim_*`
- `sim_summary_*`

운영 필수 데이터는 아니고 연구/튜닝용입니다.

## 9. docs/

문서 저장소입니다.

- `docs/trading_process.md`
  - 운영 플로우 기준 문서
- `docs/archive/`
  - 과거 개발 로그
- `docs/plans/`
  - 아직 적용하지 않은 설계안

## 10. 정리 원칙

- `state/`, `logs/`, `data/backtest/`, `data/cache/`는 자주 변합니다
- Git에는 보통 소스/문서만 올리고, 생성 산출물은 제외합니다
- 운영 판단은 `brain.json`, `decisions.db`, `ticker_selection_log.db`를 함께 봐야 합니다
