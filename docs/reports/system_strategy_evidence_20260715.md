# 시스템 기반 수익전략 생성·검증 보고서 — 2026-07-15

## 결론

근거가 없던 전략은 실제 라이브 후보 DB와 외부 장기 가격으로 근거를 새로 만들었다. 라이브 주문·게이트는 변경하지 않았다.

검증 결과 시스템은 하나의 초단기 후보 엔진으로만 운용하기보다 아래 세 레인으로 분리하는 편이 수익성과 지속성에 맞는다.

1. **저회전 지수 추세 코어**: 월 1회 QQQ/SPY/KODEX200 추세·현금 전환. 장기 및 2020년 이후 OOS 생존.
2. **후보 convex overlay**: 시장 risk-on일 때만 후보 한 종목을 진입하고 비대칭 TP/SL로 장중 봉우리를 실현. US에서 방향 생존, 아직 꼬리 의존.
3. **기존 PathB 출구 개선**: early-tier/tail-capture의 동일 진입 paired-net을 계속 측정. 코어와 자금·슬롯·exit owner 분리.

즉시 라이브 승격이 아니라, 생존 전략을 독립 shadow 원장으로 전환했다.

## 1. 실제 후보 기반 다전략 검증

도구: `tools/system_strategy_lab.py`

### 계약

- 원천: `data/ticker_selection_log.db`, live 후보만 사용.
- 같은 날짜·시장·종목의 반복 rescreen을 하나로 집계.
- D일 후보는 D 종가까지 알려진 정보만 사용하고 **D+1 실제 시가** 진입.
- US 비용 0.70% 및 USD/KRW 반영, PathB 20만원 정수주 체결 가능성 적용.
- KR 비용 0.21%, PathB 50만원 정수주 체결 가능성 적용.
- 종목당 20세션 쿨다운, 하루 신규 한 종목, 최대 5슬롯.
- 레버리지·인버스는 core stock arm에서 제외하고 별도 arm으로 격리.
- 거래량 0, 비정상 OHLC, 35% 초과 중간 갭은 제외.
- 발견 구간 2026-04~05, OOS 2026-06~07.
- OOS 비용 +0.50%p 스트레스, 상위 3거래 제거, 5거래 블록 bootstrap.

### 커버리지

| 항목 | 결과 |
|---|---:|
| 원 후보 행 | 28,424 |
| 날짜·시장·종목 중복 제거 후 | 4,905 |
| 실제 D+1 경로 구성 | 4,671 |
| US | 2,505행 / 58세션 / 608종목 |
| KR | 2,166행 / 57세션 / 476종목 |
| 제외 | 다음 세션 없음 130, 날짜 불일치 83, 진입 불가 12, 거래량 0 9 |

### 사전 고정 전략 38개 결과

- 현재 형태 기각: 28개.
- forward 부족: 10개.
- 전통적인 추세·거래량·눌림·고점돌파만으로는 6~7월 후보 풀의 음의 드리프트를 이기지 못했다.
- 후보를 억지로 늘리는 방식은 다시 기각됐다.

US에서 다음 두 비대칭 출구가 연구 lead로 남았다.

| 전략 | OOS n | 평균 net | PF | 상위 3건 제거 |
|---|---:|---:|---:|---:|
| risk-on + TP5%/SL2%, 5세션 | 12 | +0.757% | 1.56 | -0.582% |
| risk-on + TP8%/SL4%, 5세션 | 12 | +1.300% | 1.59 | -0.935% |

두 arm은 수익 방향은 생존했지만 꼬리 의존이 남았다. 따라서 MICRO가 아니라 shadow overlay다. KR 후보 기반 arm은 현재 국면에서 유지할 공격 전략이 없었다.

## 2. 외부 장기 데이터 기반 지수 추세 슬리브

도구: `tools/index_trend_strategy_lab.py`

원천은 Yahoo adjusted monthly OHLC와 일봉에서 월말로 재구성한 `KRW=X`다. 처음 받은 월별 FX에는 2015~2017년 단위 오류가 있어 결과를 폐기하고 일봉 월말값으로 다시 받았다. 정제 후 FX 범위는 898.69~1541.73원이다.

### 계약

- 월말 t 신호는 t+1월에만 적용.
- US는 KRW 총수익으로 계산.
- US 왕복 0.70%, KR 왕복 0.21%; 자산 교체는 양쪽 turnover 반영.
- 현금수익 0으로 보수적 계산.
- 장기 전체, 2020년 이후 OOS, 2024년 이후 최근 구간 분리.
- 6개월 블록 bootstrap, 상위 3개월 제거, 최대낙폭·turnover 포함.
- 미완료 2026년 7월은 성과에서 제외.

### US 결과

| 전략 | 전체 CAGR / Sharpe / MDD | 2020+ CAGR / Sharpe / MDD | 판정 |
|---|---|---|---|
| SPY 보유 기준선 | 13.09 / 1.02 / -22.30 | 20.68 / 1.34 / -17.00 | benchmark |
| QQQ 보유 기준선 | 17.71 / 1.09 / -28.59 | 27.28 / 1.39 / -28.59 | benchmark |
| SPY 10개월 SMA/현금 | 9.63 / 0.94 / -21.93 | 14.65 / 1.20 / -21.93 | shadow ready |
| **QQQ 10개월 SMA/현금** | **12.20 / 0.97 / -21.86** | **24.70 / 1.62 / -12.53** | **shadow ready** |
| **QQQ SMA + 12% 변동성 목표** | **8.48 / 0.88 / -18.12** | **13.68 / 1.37 / -8.67** | **저위험 shadow ready** |
| SPY/QQQ dual momentum | 10.42 / 0.85 / -23.76 | 19.49 / 1.36 / -12.86 | shadow ready |

QQQ SMA는 단순보유보다 OOS CAGR이 2.58%p 낮았지만 MDD를 16.06%p 줄이고 Sharpe를 0.23 높였다. 변동성 목표형은 수익을 더 낮추는 대신 MDD를 -8.67%까지 줄였다. 코어 전략은 수익 최대화형과 저위험형을 별도 arm으로 유지한다.

### KR 결과

| 전략 | 전체 CAGR / Sharpe / MDD | 2020+ CAGR / Sharpe / MDD | 판정 |
|---|---|---|---|
| KODEX200 보유 기준선 | 13.59 / 0.68 / -34.32 | 29.31 / 0.97 / -34.32 | benchmark |
| KOSDAQ150 보유 기준선 | 6.76 / 0.37 / -45.96 | 8.03 / 0.41 / -37.36 | research only |
| **KODEX200 10개월 SMA/현금** | **10.27 / 0.60 / -39.61** | **31.09 / 1.08 / -19.86** | regime-dependent shadow |
| **KODEX200 SMA + 12% 변동성 목표** | **4.70 / 0.50 / -34.39** | **14.28 / 1.10 / -8.99** | 저위험 regime-dependent shadow |
| KODEX200/KOSDAQ150 dual momentum | 18.99 / 0.78 / -27.57 | 22.52 / 0.81 / -27.57 | regime-dependent shadow |

KR SMA는 2020년 이후 강하지만 2020년 이전 bootstrap 하한이 음수다. 그래서 US와 달리 정식 shadow ready가 아니라 체제 의존 arm으로 분리했다.

## 3. 2026년 7월 shadow 목표

도구: `tools/index_trend_shadow_tracker.py`

6월 월말 신호를 7월 forward로 기록했다. 주문 권한은 없다.

| arm | 목표 |
|---|---|
| US SPY SMA | SPY 100% |
| US QQQ SMA | QQQ 100% |
| US QQQ SMA+VOL12 | QQQ 59.95% / 현금 40.05% |
| US dual momentum | QQQ 100% |
| KR KODEX200 SMA | 069500 100% |
| KR KODEX200 SMA+VOL12 | 069500 20.98% / 현금 79.02% |
| KR dual momentum | 069500 100% |

원장: `data/shadow/index_trend_shadow_signals.jsonl`

## 4. 권장 시스템 전략 구성

### Core — 저회전 월간 슬리브

- US 기본 비교: QQQ SMA/현금.
- US 저위험 비교: QQQ SMA+VOL12.
- KR 기본 비교: KODEX200 SMA/현금.
- KR 저위험 비교: KODEX200 SMA+VOL12.
- PathB와 후보 ID·자금·슬롯·exit owner를 공유하지 않는다.

### Overlay — 저빈도 후보 convex 수확

- US risk-on에서만 TP5/SL2와 TP8/SL4를 shadow A/B.
- 한 세션 한 종목, 레버리지 ETF 제외, 정수주 가능 종목만.
- 상위 기여 제거가 양수가 될 때까지 MICRO 금지.
- 기존 early-tier/tail-capture와 효과가 겹치므로 동일 진입 paired-net으로 비교한다.

### Cash/defense

- 월말 추세가 음수면 코어는 현금.
- 후보 엔진 신호 부족을 오류로 보지 않고 코어 슬리브가 자본을 일하게 한다.
- 단, 코어와 후보 overlay의 총노출 상한은 실제 주문 연결 전에 별도 포트폴리오 시뮬레이션으로 고정한다.

## 5. 남은 승격 조건

1. 지수 shadow를 최소 3개월 기록하고 Yahoo와 KIS 월말 가격 교차검증.
2. 후보 TP/SL overlay는 독립 forward 30건, 상위 3건 제거 후 양수, 추가 비용 후 양수.
3. index core + candidate overlay 동시 보유의 총노출·월간 MDD·현금 사용률 시뮬레이션.
4. 운영자 승인 전 주문 브리지·스케줄러 연결 금지.

## 산출물

- `tools/system_strategy_lab.py`
- `tools/index_trend_strategy_lab.py`
- `tools/index_trend_shadow_tracker.py`
- `reports/system_strategy_lab_20260715.json`
- `reports/system_strategy_lab_events_20260715.csv`
- `reports/system_strategy_lab_trades_20260715.csv`
- `reports/index_trend_strategy_lab_20260715.json`
- `reports/index_trend_ledger_20260715.csv`
- `reports/index_trend_prices_20260715.csv`
- `data/shadow/index_trend_shadow_signal_202607.json`
- `data/shadow/index_trend_shadow_signals.jsonl`

관련 단위 테스트 10건 통과.
