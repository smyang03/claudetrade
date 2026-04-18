# Intraday Strategy Roadmap

## 지금 구현

### 1. opening_range_pullback
- 목적: 장초 OR(Opening Range) 형성 후 눌림 구간 진입
- 적용 시장: KR 우선
- 데이터 소스:
  - WS tick 기반 `_or_high`, `_or_low`, `_or_formed`
  - 기존 `_intraday_high`, `_intraday_low`와 분리
- signal guard 순서:
  1. `or_formed == False`면 진입 금지
  2. OR 진행 중이면 진입 금지
  3. `or_range_pct` 품질 필터
  4. 진입 윈도우 경과 시 금지
  5. 눌림 위치 확인
  6. 거래량 확인

### 초기 파라미터
- KR
  - `or_minutes = 10`
  - `or_min_range_pct = 0.003`
  - `or_max_range_pct = 0.030`
  - `pullback_min_pct = 0.002`
  - `pullback_max_pct = 0.010`
  - `vol_mult = 1.3`
  - `entry_window_min = 60`
  - `tp_pct = 0.030`
  - `sl_pct = 0.012`
  - `max_hold = 1`
- US
  - 초기 구현 보류

## 지금부터 쌓을 데이터

### intraday_strategy_log.db
- `strategy_name`
- `stage`
- `or_formed`
- `or_high`
- `or_low`
- `or_range_pct`
- `pullback_depth_pct`
- `entry_window_elapsed_min`
- `price`
- `volume`
- `vol_ratio`
- `from_high_pct`
- `signal_fired`
- `traded`
- `blocked_reason`
- `pnl_pct`

## 1~2주 관찰 포인트
- OR 전략 발동 빈도
- 장초 실제 진입 건수 증가 여부
- 기존 `gap_pullback` 대비 성과
- 허위 돌파 / 고점 추격 비율
- 종목군별 성과 차이

## 2주 뒤 구현

### 2. vwap_reclaim
- 목적: VWAP 하회 후 회복하는 추세 재개 구간 진입
- 전제 조건:
  - KR/US 분봉 API 연결
  - VWAP 계산 및 세션 중 갱신 파이프라인
- 필요 데이터:
  - `vwap`
  - `vwap_deviation_pct`
  - `vwap_confirm_candles`
  - `reclaim_volume_ratio`

### 3. vwap_reversion
- 목적: VWAP 기준 과도 이탈 후 평균 회귀
- 상태: 설계만 보관
- 우선순위: 가장 뒤

## 분봉/VWAP 체크리스트
- KR 분봉 API 연결
- US 분봉 API 연결
- 분봉 캐시 / 갱신 정책
- 세션별 VWAP reset
- `vwap_reclaim` 파라미터 검증
- `vwap_reversion` 별도 검증 후 진입
