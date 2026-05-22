# KR/US Market Index Watch Set

작성일: 2026-05-22

## 목적

KR/US 자동매매 운영에서 시장 regime, 변동성, 섹터 리스크를 판단할 때 볼 지수 후보를 정리한다. 현재 시스템은 이미 KR `KOSPI`/`KOSDAQ`, US `S&P500`/`NASDAQ`/`VIX` 축을 사용하고 있으므로, 우선 기존 축을 유지하고 보조 지수는 단계적으로 추가한다.

## 최소 운영 세트

### 한국장

필수 관찰 지수:

- `KOSPI`: 한국 대형주 및 대표 시장 방향.
- `KOSDAQ`: 성장주, 중소형주, 테마장 체감 방향.
- `KOSPI200`: 선물, 기관 수급, 대형주 리스크 판단.
- `KOSDAQ150`: KOSDAQ 주도 성장주, 바이오, 테크 흐름 판단.
- `VKOSPI`: 한국장 변동성 및 공포 지표. 가능하면 US `VIX`와 같은 risk sizing 보조 축으로 사용.

보조 관찰 지수:

- `KRX300`: KOSPI와 KOSDAQ을 합친 통합 시장 체감 지표.

### 미국장

필수 관찰 지수:

- `S&P500` / `SPX`: 미국 전체 시장 regime 기준.
- `NASDAQ Composite` 또는 `NASDAQ-100` / `NDX`: 기술주 및 성장주 방향.
- `Russell2000` / `RUT`: 중소형주 risk-on/risk-off 판단.
- `VIX`: 변동성 및 공포 지수. 현재 시스템의 US 사이즈 축소 로직에 이미 사용 중.
- `SOX` / PHLX Semiconductor: 반도체, AI, 하드웨어 후보가 많을 때 섹터 리스크 판단.

보조 관찰 지표:

- `Dow Jones`: 헤드라인성 대형 우량주 흐름 확인. 매매 판단 가중치는 낮게 둔다.
- `DXY`, `US10Y`: 주식 지수는 아니지만 US 성장주와 한국장 외국인 수급 판단 보조 지표로 사용한다.

## 현재 시스템과의 연결

현재 코드에서 이미 확인된 축:

- KR 주 지수: `KOSPI`
- KR 보조 지수: `KOSDAQ`
- US 주 지수: `S&P500`
- US 보조 지수: `NASDAQ`
- US 변동성 지수: `VIX`

다음 개선 후보:

- KR: `KOSPI200`, `KOSDAQ150`, `VKOSPI`
- US: `Russell2000`, `SOX`

## 운영 적용 방향

1. 현재 prompt/context에는 기존 축을 유지한다.
2. 신규 지수는 먼저 read-only 수집 및 로그 노출부터 붙인다.
3. 데이터 결측, 지연, 공급원 불안정성이 확인되기 전까지 주문 판단 gate에는 직접 연결하지 않는다.
4. 최소 2주 이상 shadow 관찰 후 market mode, sizing, entry block에 반영할지 결정한다.
5. `VIX`/`VKOSPI`는 방향성 지수와 분리해서 변동성 리스크 축으로 취급한다.

## 참고 출처

- KRX 주요 지수: https://indices.krx.co.kr/main/main.jsp
- Global KRX index overview: https://global.krx.co.kr/contents/GLB/02/0202/0202010101/GLB0202010101.jsp
- SEC market indices overview: https://www.sec.gov/answers/indices.htm
- Nasdaq Composite: https://www.nasdaq.com/solutions/global-indexes/nasdaq-composite
- Nasdaq SOX overview: https://indexes.nasdaq.com/Index/Overview/sox
- Cboe indices/VIX: https://www.cboe.com/us/indices/indicessearch/
