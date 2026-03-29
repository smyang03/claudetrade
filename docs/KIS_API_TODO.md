# KIS API 검토 및 TODO
> 작성: 2026-03-26 | 내일 이어서 진행

---

## 현재 사용 중인 KIS API

| 기능 | TR ID | 상태 |
|------|-------|------|
| OAuth 토큰 | `/oauth2/tokenP` | ✅ |
| 해시키 | `/uapi/hashkey` | ✅ |
| 국내 현재가 | `FHKST01010100` | ✅ |
| 국내 일봉 | `FHKST03010100` | ✅ |
| 국내 거래량순위 스크리너 | `FHPST01710000` | ✅ |
| 국내 잔고 조회 | `VTTC8908R` / `TTTC8908R` | ✅ (구TR 여부 확인 필요) |
| 국내 매수 | `VTTC0012U` / `TTTC0012U` | ✅ 신TR |
| 국내 매도 | `VTTC0011U` / `TTTC0011U` | ✅ 신TR |
| 해외 잔고 조회 | `VTTS3012R` / `TTTS3012R` | ✅ |
| 해외 매수 | `VTTT1002U` / `TTTT1002U` | ✅ |
| 해외 매도 | `VTTT1001U` / `TTTT1006U` | ✅ |
| WebSocket 실시간 시세 | `H0STCNT0` | ✅ KR만 |

---

## 내일 확인할 것들

### 1. 국내 잔고 조회 TR 코드 확인 필요
- 현재: `VTTC8908R` (구TR 가능성 있음)
- 주문은 신TR(`VTTC0012U`)로 바꿨는데 잔고는 미확인
- **API 문서 받아서 신TR로 교체 필요**
- URL: `/uapi/domestic-stock/v1/trading/inquire-balance`

### 2. 해외주식 현재가 조회 API
- 현재: **Finnhub** 사용 중 (60회/분 무료)
- KIS에도 해외주식 현재가 API 있음 → Finnhub 대체 가능한지 확인
- 예상 TR: `HHDFS00000300` 계열 (확인 필요)
- **API 문서 받아서 구현하면 외부 API 의존도 줄일 수 있음**

### 3. 국내주식 기술적 지표 API
- KIS에 RSI, 이동평균, 볼린저밴드 등 지표 제공 API 있는지 확인
- 현재는 pandas로 직접 계산 중
- 있으면 계산 로직 단순화 가능

### 4. 국내주식 조건 검색
- 현재: 거래량 순위(`FHPST01710000`)로만 스크리닝
- KIS 조건 검색 API 있으면 더 정밀한 스크리너 가능
- HTS 조건식 연동 여부 확인

### 5. 해외주식 일봉/분봉
- 현재: Alpha Vantage / yfinance 사용
- KIS 해외주식 차트 API로 대체 가능한지 확인
- 대체 시 AV 일일 한도(25회) 제약 해소

### 6. GitHub 샘플 코드 검토
- https://github.com/koreainvestment/open-trading-api
- Python 샘플에서 우리 구현과 다른 파라미터/패턴 확인
- 특히 `examples/` 폴더 내 국내/해외주식 샘플

---

## 오늘 완료한 작업

- [x] KIS 해외주식 주문 `ORD_SVR_DVSN_CD: "0"` 필드 추가 → US 모의투자 주문 성공
- [x] 국내주식 주문 구TR → 신TR 교체 (`VTTC0802U` → `VTTC0012U`)
- [x] `get_balance(market="US")` 문서 기반 구현 (`VTTS3012R`)
- [x] 가짜 시뮬 포지션 (SRPT/PAYS/CDLX) 초기화
- [x] `_verify_live_positions` KR+US 동시 검증으로 개선
- [x] `MAX_POSITIONS`, `MAX_PYRAMID` env로 분리
- [x] 모드별 `SIZE_*` env로 분리 (SIZE_CAUTIOUS_BEAR 등)
- [x] 분석가에게 포트폴리오 현황 전달 + `suggested_size_pct` 반영

---

## 주의사항

- KIS API 작업 시 반드시 **공식 문서 먼저 받고** 구현 (TR코드/파라미터 추측 금지)
- 모의투자 → 실거래 전환 시 `.env`에서 `KIS_IS_PAPER=false` + 실거래 APP_KEY로 교체
- US 모의투자 주문은 미국 장 시간(한국 23:30~06:00)에만 체결됨
---

## 2026-03-27 업데이트

### 반영 완료
- KR 주문체결조회 연결
  - API: `/uapi/domestic-stock/v1/trading/inquire-daily-ccld`
  - TR ID: `VTTC8001R` / `TTTC8001R`
  - 함수: `inquire_daily_ccld_kr()`, `get_order_fill_kr()`
- US 주문체결조회 연결
  - API: `/uapi/overseas-stock/v1/trading/inquire-ccnl`
  - TR ID: `VTTS3035R` / `TTTS3035R`
  - 함수: `inquire_ccnl_us()`, `get_order_fill_us()`
- pending 주문 체결 확인 시:
  - `filled_price_native`
  - `fill_time`
  - `price_source`
  저장 및 화면 반영

### 현재 진실값 우선순위
1. 주문체결조회 성공 시 체결단가 (`price_source=order_fill`)
2. 브로커 잔고 평균단가 (`price_source=broker_balance`)
3. 미체결 주문은 주문단가 (`raw_price`)

### 남은 TODO
- US 체결조회 응답 원문을 1회 로깅해서 필드명 확정
- 모의투자에서 주문번호 직접 검색 제한이 있는 경우 더 안정적인 후처리 키 조합 검토
- 필요 시 KR/US 체결조회 결과를 별도 캐시 파일로 저장해 당일 대시보드 재기동 시 재사용
