# 시스템 기반 전략 전수 탐색 보고서 — 2026-07-15

## 결론

이번 검증은 기존 PathB의 출구 개선에 한정하지 않고, 현재 시스템이 실제로 만들고 체결할 수 있는
롱 전략 공간을 시장·보유기간·신호·체결·출구·포트폴리오 단위로 다시 열었다. 라이브 주문이나 설정은
변경하지 않았다.

가장 중요한 결론은 하나의 전략으로 합치면 안 된다는 것이다.

1. **PathB 일중 엔진**은 현재 구현된 모든 진입 경로의 비용 후 기대값이 음수다. 빈도 확대 대상이 아니다.
2. **5일 확인형 모멘텀 엔진**은 US와 KR 모두에서 유망한 독립 후보가 나왔다. D 종가까지의 정보로
   고르고 D+1 시가에 진입하며, PathB와 다른 출구와 자금 슬리브를 써야 한다.
3. **KR 다음날 1일 momentum carry**는 기존 장중 momentum 신호를 당일 추격하지 않고 다음날 시가에
   진입해 하루만 보유하는 후보로 살아남았다.
4. **KR 갭 지속과 KR 뉴스 촉매**는 가능성은 있으나 각각 체제 의존과 비실행 가능 라벨 문제 때문에
   데이터 shadow 단계다.

따라서 새 방향은 `선별 진입 유지 + 조기익절 하나`가 아니라 아래의 다중 엔진 구조다.

```text
기존 PathB intraday        : 짧은 관찰/실행 엔진, 확대 금지
기존 US_SWING_5D_TOP_RANK : ML OOS 비교군, shadow 유지
신규 US_MOMENTUM_CONFIRM_5D: 규칙 기반 5일 challenger
신규 KR_MOMENTUM_CONFIRM_5D: 규칙 기반 5일 challenger
신규 KR_MOMENTUM_NEXTDAY_1D: 기존 momentum 신호의 다음날 하루 carry challenger
KR_GAP_FOLLOW_1D / KR_NEWS_OPEN: 데이터 shadow
```

## 공통 검증 계약

- 신호 시점: D 종가까지 알 수 있는 값만 사용한다.
- 진입: D+1 정규장 시가. 시가가 D 종가보다 0.5% 초과 상승하면 미체결 처리하며 대체 종목을 고르지 않는다.
- 비용: US 왕복 0.50%, KR 왕복 0.21%. US 수익은 USDKRW 변화까지 반영한다.
- 포트폴리오: 전략별 한 슬롯, 동일 종목 20거래세션 재진입 금지.
- 경로 장벽: 일봉에서 TP와 SL이 같은 날 모두 닿으면 SL 우선, 갭으로 장벽을 통과하면 시가 체결로 계산한다.
- 불확실성: 시간 분할, 5거래 블록 bootstrap 하한, 상위 종목 제거, 추가 비용, 추격 한도·cooldown·순위 교란을 본다.
- 모든 결과는 historical discovery다. 탐색한 규칙 중 좋은 것을 고른 것이므로 live forward 이전에는 주문 근거가 아니다.

## 1. 기존 일중 진입 경로

`data/audit/candidate_audit.db`에서 `runtime_authoritative`이면서 실제 close 라벨이 있는 행을
`시장×날짜×종목×경로` 최초 관측으로 중복 제거했다. US 0.50%, KR 0.21% 비용을 차감했다.

| 시장 | 경로 | 표본 | 60분 net 평균 | 최근 40% 평균 | 판정 |
| --- | --- | ---: | ---: | ---: | --- |
| US | immediate | 1,879 | -0.614% | -0.682% | 기각 |
| US | or_break | 894 | -0.582% | -0.479% | 기각 |
| US | pullback_reclaim | 879 | -0.620% | -0.593% | 기각 |
| US | volume_surge | 1,623 | -0.607% | -0.611% | 기각 |
| US | vwap_reclaim | 1,426 | -0.603% | -0.594% | 기각 |
| US | wait_30m / wait_60m | 1,822 / 1,759 | -0.638% / -0.621% | -0.615% / -0.503% | 기각 |
| KR | immediate | 1,516 | -0.651% | -0.503% | 기각 |
| KR | or_break | 460 | -0.994% | -1.023% | 기각 |
| KR | pullback_reclaim | 802 | -0.949% | -0.784% | 기각 |
| KR | volume_surge | 1,290 | -0.762% | -0.690% | 기각 |
| KR | vwap_reclaim | 860 | -1.106% | -1.013% | 기각 |
| KR | wait_30m / wait_60m | 1,497 / 1,476 | -0.453% / -0.730% | -0.617% / -1.005% | 기각 |

orderbook, VI, premarket 전용 경로는 trigger/가격 라벨이 없어 검증할 수 없었다. 이는 성과 0이 아니라
데이터 계약 미완료다. 확인 가능한 일중 경로는 하나도 양수 하한을 만들지 못했다.

## 2. US 멀티데이 전략 전수 비교

### 데이터와 공통 결과

- `data/analysis/us_yahoo_point_in_time.db`: 13,672행, 426세션, 128종목,
  2024-07-23~2026-04-02.
- 가격 피처는 point-in-time이며 D+1 시가부터 1/3/5일 종가까지 US 비용과 FX를 반영했다.
- 단, 후보 우주는 과거 `backfill/is_simulated` 행에 조건부다. 현재 라이브 후보 우주와 같다는 보장이 없고,
  현재 구성 종목 중심의 survivorship/selection bias가 남는다.

고정 규칙군으로 상대강도 5/20/60일, 확인형 momentum, 20일 신고가, 거래량 돌파, 저변동 추세,
상승 추세 눌림, 과매도 반전, 갭 상승 지속, 갭 하락 반전, 1일 반전, 비추격 추세를 1/3/5일에 적용했다.

- 1일: 14개 규칙군 중 양수 하한 없음. ML purged walk-forward도 top1 평균 -0.210%.
- 3일: 일부 평균은 양수지만 하한과 기간 안정성이 약했다. ML top1 +0.174%, 하한 -0.964%.
- 5일: 규칙 기반 확인형 momentum과 RS20 추세가 살아남았다. ML top1도 +1.782%로 같은 보유기간 구조를 지지했으나 하한은 -0.061%였다.

### 우선 challenger: `US_MOMENTUM_CONFIRM_5D`

고정 계약 초안:

1. QQQ 20일 momentum > 0.
2. 종목의 QQQ 대비 20일 상대강도 > 0, 종가 > MA60.
3. 종목 5일·20일 momentum > 0, MACD > signal.
4. 통과 종목 중 RS20 1위만 선택.
5. D+1 시가 +0.5% 추격금지, 한 슬롯, 5세션, 동일 종목 cooldown 20세션.

| 항목 | 5일 종가 청산 | SL 8% + TP 10% |
| --- | ---: | ---: |
| 체결 수 | 50 | 50 |
| 평균 / 중앙값 net | +3.423% / +1.464% | +3.552% / +5.443% |
| PF / 승률 | 2.461 / 62.0% | 3.177 / 72.0% |
| 최악 / 최선 | -20.046% / +44.419% | -8.864% / +14.027% |
| 초기 30건 평균 | +3.022% | +3.716% |
| 후반 20건 평균 | +4.024% | +3.307% |
| 상위 기여 3종목 제거 평균 | +0.694% | +2.744% |
| 5거래 블록 5% 하한 | +1.322% | +2.340% |
| 추가 비용 0.50% 후 평균 | +2.923% | +3.052% |

SL 6% + TP 10%도 평균 +3.561%, PF 3.201, 후반 +3.030%로 비슷했다. 6%와 8% 중 하나를
historical 최고값으로 고르면 안 되며, 두 계약을 별도 shadow arm으로 동결해야 한다.

중요한 반증도 있다. 장벽 없는 5일 보유는 상위 대형 승자 의존도가 높고, 수익을 5%에서 잘라 버리면
평균이 음수가 된다. 이 전략에 PathB의 early-tier를 그대로 적용하면 안 된다. 다만 TP10/SL6~8 arm은
초기·후반과 상위 종목 제거 후에도 양수여서 별도 검증 가치가 생겼다.

### 비교군

- `US_DAILY_TREND_RANK_5D`: 49건, 평균 +3.109%, PF 1.971. 상위 3개 기여 종목 제거 후 +0.665%.
  확인형 momentum보다 약해 backup이다.
- 기존 `US_SWING_5D_TOP_RANK`: 293 OOS 세션의 ML rank 비교군. top3 평균 +1.357%, PF 1.512,
  하한 -0.152%. 현재 shadow이며 forward matured 0건이다. 규칙 기반 challenger와 같은 ledger에서
  비교해야 한다.
- ML 모델 자체는 1/3일을 살리지 못했고 5일만 지지했다. 복잡한 예측 모델을 추가해야 한다는 근거는 아니다.

## 3. KR 멀티데이·다음날 전략

### 데이터

`data/ml/decisions.db`의 KR backfill 후보와 `data/price/kr` 일봉을 결합해 D+1 시가 체결을 다시 만들었다.
4,386행, 58종목, 2025-05-30~2026-04-03이다. KR 비용 0.21%를 차감했다.

가격 경로는 point-in-time이지만 후보 우주가 58개 backfill 종목에 조건부라서 US보다도 우주 편향 위험이
크다. 아래 수치가 현재 라이브의 수백 종목 풀에서 재현되는지는 별도 문제다.

### 우선 challenger: `KR_MOMENTUM_CONFIRM_5D`

계약은 US와 동일하되 시장 benchmark를 KOSPI200/KOSDAQ150 ETF 20일 momentum 평균으로 사용한다.
시장 20일 momentum > 0, benchmark 대비 RS20 > 0, 종가 > MA60, 5/20일 momentum > 0,
MACD > signal을 만족한 RS20 1위를 고른다.

| 항목 | 5일 종가 청산 | SL 6% + TP 10% |
| --- | ---: | ---: |
| 체결 수 | 33 | 33 |
| 평균 / 중앙값 net | +5.679% / +2.830% | +4.088% / +5.289% |
| PF / 승률 | 4.452 / 75.8% | 4.474 / - |
| 최악 | -20.743% | -6.644% |
| 초기 / 후반 평균 | +3.744% / +8.656% | 별도 forward 필요 |
| 상위 기여 3종목 제거 평균 | +1.752% | - |
| 5거래 블록 5% 하한 | +3.027% | - |
| 추가 비용 1.00% 후 평균 | +4.679% | - |

표본이 33건뿐이고 후보 우주 편향이 크므로 수치가 강하다는 이유로 live 승격할 수 없다.

### 보조 challenger: `KR_MOMENTUM_NEXTDAY_1D`

현재 backfill의 `mom_fired=1` 후보를 change와 volume ratio 순으로 정렬해 다음날 시가에 진입하고
당일 종가에 청산한다.

- 45건, 평균 +1.877%, 중앙값 +1.762%, PF 2.716, 최악 -9.231%.
- 초기 +1.085%, 후반 +2.571%.
- 상위 기여 3종목 제거 후 +0.865%.
- 5거래 블록 하한 +0.797%, 추가 비용 1.00% 후 +0.877%.
- intraday SL/TP를 촘촘히 붙이면 성과가 감소했다. 기본 검증 arm은 다음날 종가 청산이고, 8%는
  수익 최적화용 stop이 아니라 catastrophe cap arm으로만 비교한다.

이 후보는 현재 일중 momentum 전략과 다르다. 당일 급등을 추격하지 않고 다음 세션 시가에서 새로
평가하는 overnight reset 전략이다.

### 데이터 shadow: `KR_GAP_FOLLOW_1D`

종가 > MA60, D 갭 >= 2%, D change > 0인 종목 중 갭 1위를 다음날 하루 보유했다.

- 50건, 평균 +2.332%, PF 4.152, 하한 +0.718%.
- 그러나 초기 23건 -0.055%, 후반 27건 +4.365%로 장세 의존성이 매우 크다.
- 추격금지를 제거하면 84건 +0.153%까지 붕괴한다.

따라서 주전략이 아니라 regime stability를 확인하는 shadow arm이다.

## 4. 이벤트·뉴스·PEAD·섹터

### KR 뉴스 촉매

candidate audit에서 `news_prompt_eligible=1`의 비용 후 60분 평균은 170건 +1.316%였고 최근 구간도
양수였다. 그러나 상위 사례의 기준가격이 08:53경 전일 종가성 가격이고 실제 관측은 개장 뒤였다.
예를 들어 +29% 사례들은 실제 시가 급등을 진입가에 반영하지 않았다. 목표 60분보다 이른 sparse 관측도
섞여 있다. 이는 개장 갭을 공짜로 산 비실행 가능 라벨이다.

판정: 신호 가능성은 있으나 현재 성과 증거는 무효다. `08:59 동결 신호 → 실제 시가/첫 체결가 →
시가 갭 상한 → 정확한 30/60분` 계약을 새로 기록해야 한다.

### US 뉴스

비용 후 뉴스 eligible 성과는 60분 -0.484%, 1일 -0.041%, 3일 -0.536%였다. 신규 진입 알파로 기각한다.

### PEAD와 섹터

- PEAD: 관측 58일이지만 최신 shadow에서 surprise known이 0건이다. 실적 surprise와 known-at 계약을
  복구하기 전에는 전략이 아니라 데이터 수선 항목이다.
- KR sector play: 코드와 gate는 있으나 독립 실체결/forward 표본이 부족하다. 단독 주문 엔진으로 승격하지 않는다.
- preopen KR 촉매의 기존 holdout은 2일뿐이다. 탐색 힌트이지 검증이 아니다.

## 5. 시장상태·포트폴리오·기타 가능성

| 축 | 판정 | 근거 |
| --- | --- | --- |
| QQQ 20일 상승 filter | 5일 momentum의 구성요소 | 독립 알파라기보다 위험 체제 필터다. |
| QQQ 5일, broad breadth, QQQ60, 저변동 추가 filter | 채택 안 함 | US momentum 평균/표본을 일관되게 개선하지 못했다. |
| VIX term / 좁은 breadth | 진단 전용 | 과거·최근 방향 충돌. gate/size 근거 없음. |
| top2~5 분산 | 기각 | US/KR 모두 1위 이후 순위의 성과가 급격히 약해졌다. 한 슬롯이 안전하다. |
| 3일 fallback 결합 | 기각 | US 5일 momentum에 3일 anti-chase를 섞으면 평균과 최악 손실이 악화했다. |
| TSMOM / ETF 회전 | benchmark | 장기 방향성은 있으나 생존편향과 약한 유의성. 현금 benchmark로만 사용. |
| mean reversion / pullback | 기각 | US 1/3/5일과 일중 경로에서 하한이 음수. KR pullback도 후반 음수. |
| 신고가 breakout / 거래량 breakout | 보조 연구 | 일부 5일 평균은 양수지만 하한·기간·순위 안정성이 주 후보보다 약하다. |
| short / pairs / stat-arb | 현 시스템에서 불가 | borrow, margin, 두 leg 원자 체결, short mark-to-market 계약이 없다. |
| 초단타 / scalp | 기각 | US 0.5% 비용과 10초 polling이 예상 edge보다 크다. |
| inverse/leveraged ETF hedge | 보류 | 후보 우주·상품 위험·별도 권한 계약이 없다. |

## 6. 권장 시스템 구성

### 완전히 분리할 것

- PathB와 swing의 candidate ID, 자금 sleeve, 포지션 슬롯, exit owner, 성과 ledger를 공유하지 않는다.
- PathB early-tier를 5일 momentum에 적용하지 않는다.
- US 기존 ML swing과 규칙 기반 momentum은 같은 종목을 낼 수 있으므로 동시 주문하지 않고 shadow에서
  `winner-takes-one` 비교만 한다.
- KR 5일 momentum과 KR 1일 carry도 같은 계좌에서 먼저 신호 충돌/기회비용을 기록한다.

### 동결할 shadow arm

1. US: `US_SWING_5D_TOP_RANK` 기존 비교군.
2. US: `US_MOMENTUM_CONFIRM_5D_SL6_TP10`.
3. US: `US_MOMENTUM_CONFIRM_5D_SL8_TP10`.
4. KR: `KR_MOMENTUM_CONFIRM_5D_SL6_TP10`.
5. KR: `KR_MOMENTUM_NEXTDAY_1D_CLOSE`.
6. KR 데이터 arm: `KR_GAP_FOLLOW_1D_CLOSE`, `KR_NEWS_OPEN_60M`.

각 arm은 D 시점 후보 전체, 선택 점수, 다음 시가, 미체결 사유, 실제 비용, MAE/MFE, 장벽 체결 순서,
종가와 benchmark를 기록해야 한다. 과거 DB 결과를 forward 표본에 섞지 않는다.

### 승격 관문

다음 조건을 모두 만족하기 전에는 micro 주문도 허용하지 않는다.

- 현재 라이브 후보 풀에서 독립 matured 60건 또는 최소 40세션.
- 실제 체결 가능 시가와 비용 기준 평균 net > 0, PF >= 1.20.
- 5거래 블록 5% 하한 > 0.
- 상위 3종목과 상위 3거래일 제거 후 평균 > 0.
- discovery 기간과 반대 방향의 forward 체제에서도 평균 부호 유지.
- 단일 vendor 결과가 아닌 KIS 가격/체결가 교차검증.
- 동시에 열릴 수 있는 기존 PathB·swing 포지션을 포함한 계좌 최대 손실 한도 통과.
- 자동 승격 금지. 운영자 승인 후에도 한 슬롯·작은 sleeve의 micro부터 시작.

## 최종 판정 지도

| 전략 | 현재 판정 | 다음 행동 |
| --- | --- | --- |
| US/KR 기존 일중 진입 경로 | 기각/확대 금지 | 운영 안정성만 유지, 빈도 완화 금지 |
| US 5일 확인형 momentum | **우선 shadow challenger** | SL6/SL8 두 arm 동결, 기존 ML swing과 forward 비교 |
| KR 5일 확인형 momentum | **우선 shadow challenger** | 현재 라이브 후보 풀에서 재현성 검증 |
| KR 다음날 1일 momentum carry | **보조 shadow challenger** | 종가 청산 arm으로 실제 다음 시가 기록 |
| KR 갭 다음날 지속 | Data shadow | 체제별 안정성 확인 |
| KR 뉴스 시가 전략 | Data repair + shadow | 비실행 가능 라벨 제거 후 재측정 |
| 기존 US ML swing | Shadow comparator | forward matured 표본 축적 |
| mean reversion/pullback/US news/초단타 | 기각 | 재활성화 금지 |
| PEAD/sector/short/pairs | 데이터 또는 실행 계약 미완료 | 인프라 없이 전략 주장 금지 |

핵심은 거래 수를 억지로 늘리는 것이 아니다. 현재 시스템에서 새로 확인된 가능성은 **당일 예측보다
다음 시가에서 추격을 거부하고 1~5일 동안 확인된 방향성을 보유하는 것**이다. 다만 발견 표본은 후보
우주 편향과 다중 탐색을 포함하므로, 수익 전략으로 확정하는 마지막 단계는 오직 현재 라이브 풀의
독립 forward shadow다.
