# DASHBOARD DEVLOG

`claudetrade`의 대시보드 API, 화면, 시각화 관련 변경 이력을 모은 문서다.

범위:
- `dashboard/dashboard_server.py`
- Today / History / Trades / Analytics 페이지
- 요약 카드, 차트, 브레인 표시, 신호 피드, 포지션/미체결 표시

참고:
- 원본 전체 로그: [DEVLOG.md](/E:/code/claudetrade/DEVLOG.md)
- 봇/트레이닝 전용 로그: [TRAINING_DEVLOG.md](/E:/code/claudetrade/TRAINING_DEVLOG.md)

## 구조 요약

페이지:
- `/` 오늘 현황
- `/history` 기간별 성과
- `/trades` 매매 원장
- `/analytics` 분석

주요 API:
- `/api/summary`
- `/api/history/*`
- `/api/trades/list`
- `/api/patterns`
- `/api/brain`
- `/api/signals/recent`
- `/api/tickers/today`
- `/api/claude/*`

## 주요 변경 이력

### 2026-03-22 4페이지 대시보드 개편
- 오늘 현황 / 기간별 성과 / 매매 원장 / 분석 페이지 분리.
- 기간 필터, KR/US 분리, 수익 곡선 및 월별 차트 추가.
- `.env` 기반 `PAPER_CASH` 로딩 문제 수정.

### 2026-03-25 실시간 신호 피드 + 모니터링 종목
- `entry_signal`, `entry_skip`, `signal_blocked` 등의 장중 상태를 대시보드에 표시.
- 선택 종목 카드에 Claude 선택 이유 표시.
- 후보 목록과 최종 선택 목록을 장중 재스크리닝 기준으로 동기화.

### 2026-03-25 미체결 이유 + 라이브 손익
- `entry_failed`, `pending_order`, `already_holding` 같은 미체결 이유 표시.
- `live_status`를 읽어 장중 손익과 자산을 실시간 반영.
- stale `live_status`가 오늘 값을 덮지 않도록 거래일/세션 검증 추가.

### 2026-03-25 시장 컨텍스트 차트
- KR/US 대표 지수, USD/KRW, VIX/VKOSPI를 시계열 차트로 추가.

### 2026-03-26 매수가/포지션 정합성
- 보유 포지션 카드에 `매수가 / 현재가 / 수익률` 표시.
- 대시보드 포지션을 KIS 브로커 잔고 직접조회 우선으로 전환.
- 원장 가격은 체결조회 / 브로커 기준 가격 우선.

### 2026-03-27 기간별 성과 / 매매원장 복구
- 오염된 `daily_judgment.trades`를 로그 기반으로 복구.
- `0원 매도` 같은 손상 행은 제외.
- KR/US 시장 혼입을 필터링.
- 오늘 세션은 live pending/position도 원장에 보조 반영.

### 2026-03-28 총자산 / 손익 / Brain / 타임라인 UI 보강
- 총자산을 `KR + US 평가환산` 기준으로 보정.
- 오늘 손익을 `실현 / 미실현`으로 분리 표시.
- `누적 자산` 아래에 `누적 손익` 줄 추가.
- `Brain` 장세 표시를 `current_beliefs.market_regime` 우선으로 변경.
- `모드별 성과`는 원본 값이 비어 있으면 Brain `mode_performance`로 폴백.
- Claude 판단 타임라인을 내부 스크롤 카드로 변경.
- Claude 제어 ON/OFF 및 즉시 실행 버튼 추가.

### 2026-03-28 용어 한글화
- 모드:
  - `CAUTIOUS_BEAR` → `신중약세`
  - `DEFENSIVE` → `방어`
  - `NEUTRAL` → `중립`
  - `MILD_BULL` → `완만상승`
  - `MILD_BEAR` → `완만약세`
  - `Bull_Confirmed` → `상승확인`
- 전략:
  - `volatility_breakout` → `변동성돌파`
  - `gap_pullback` → `갭눌림`
  - `momentum` → `모멘텀`
  - `mean_reversion` → `평균회귀`
- 장세:
  - `SIDEWAYS_BEAR` → `횡보약세`
  - `SIDEWAYS_BULL` → `횡보강세`

### 2026-03-28 오늘 종목 카드 개선
- `보유 n회 / 수량 n / 최대 m회 / 미체결 n건` 표시.
- `매수가 / 현재가`를 종목 카드에 노출.
- `already_holding`, `pending_order`를 raw reason 나열이 아닌 설명 문장으로 표시.
- `trailing` 이벤트 가격 대신 브로커/라이브 기준 `display_price` 우선 사용.

## 현재 기준 표시 원칙

오늘 현황:
- 총자산: KR + US 평가환산
- 손익: 실현 / 미실현 분리
- 포지션: 브로커 잔고 우선
- 미체결: live pending 기준

기간별 성과:
- 원본 `actual_result` 우선
- 필요 시 로그 복구 및 Brain 폴백

매매 원장:
- `daily_judgment.trades`
- 부족하면 시스템 로그 복구
- 오늘 세션은 live position / pending도 보조 반영

분석:
- Brain 상태
- 모드별 성과
- 전략별 성과
- Claude 판단 타임라인

## 남은 TODO

- 실행 중 대시보드 프로세스 stale 응답 문제를 줄이기 위한 재시작/배포 절차 정리
- 일부 인코딩 깨진 문자열 정리
- 신호 카드와 포지션 카드의 시각적 구분 강화
- 원장/성과 복구 로직을 별도 서비스 계층으로 분리
## [2026-03-28] 매매 원장 금액 표시 확장

매매 원장 `/trades` 표시를 보강했다.

- 매수 행:
  - `매수총액` 표시
- 매도 행:
  - `매수가`
  - `매수총액`
  - `매도총액`
  - `원화손익`
  표시

서버 응답 `/api/trades/list`도 같이 확장했다.

- `trade_total_native`
- `buy_price_native`
- `buy_total_native`
- `sell_total_native`
- `pnl_krw`

이제 매도 행에서 “얼마에 사서 얼마에 팔았는지”, “총 얼마를 투입했고 지금 손익이 얼마인지”를 테이블 안에서 바로 읽을 수 있다.

## [2026-03-28] Claude 재판단 효율 표시

`Claude 재판단 제어` 카드에 오늘 재판단 성과를 같이 표시하도록 확장했다.

- 오늘 재판단 횟수
- 모드 변경 횟수
- 모드 유지 횟수
- `HIT / MISS / FLAT`
- 재판단 승률
- 재판단 이후 누적 손익

집계 기준은 `재판단 완료 시점`부터 `다음 재판단 전`까지의 매도 손익 로그다.
완전한 백테스트 통계는 아니지만, 운영 중 `ON/OFF` 판단을 위한 실전 지표로 사용한다.
