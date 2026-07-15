# 수익성 극대화 통합 전략 최종안 — 2026-07-15

## 0. 최종 결론

현재 시스템은 한 전략으로 수익을 만들려 하면 안 된다. 검증된 수익 구조와 실제 계좌 실행성을 기준으로
다음 네 엔진을 분리한다.

1. **현재 자본용 US 코어:** `US_SCHG_BIL_TREND_V1`
2. **충분한 자본용 장기 코어:** `GQMT_CORE_V1`
3. **KR 경로 수확기:** `PATHB_KR_SPLIT_RUNNER_V1`
4. **독립 알파 challenger:** 기존 `US_SWING_5D`, US zone/gap shadow

핵심 변화는 PathB 단타에 모든 수익 책임을 지우지 않는 것이다. 저회전 코어가 시장·팩터 수익을 만들고,
PathB는 검증된 경로 수확만 담당하며, swing은 코어를 이기는지 독립 원장으로 경쟁한다.

이 보고서의 신규 전략은 모두 2026-07-15 결과를 본 뒤 선택한 post-selection 결과다. 따라서
`SHADOW_ONLY`이며 라이브 설정·주문·프로세스는 변경하지 않았다.

## 1. 검증 원칙

- 월말 `t`의 확정 종가만 신호로 사용하고 실제 보유수익은 `t+1`부터 계산한다.
- 미국 자산은 `KRW=X`를 결합한 원화 총수익으로 계산한다.
- 기본 미국 비용은 편도 0.25%, 스트레스는 편도 0.50%다.
- 현재 미완료 월인 2026-07은 결과에서 제외한다.
- discovery와 OOS를 시간으로 분리한다.
- 상위 3개월 제거, 6개월 이동블록 bootstrap 5% 하한, 인접 파라미터, 비용 스트레스를 함께 본다.
- PathB 반실험은 실제 체결 원장의 정수수량과 기록 MFE를 사용한다.
- 전략별 `strategy_id`, 자금, 슬롯, exit owner, 손익원장을 분리한다.
- 역사적 MFE 반실험 수치는 항상 `optimistic reach ceiling`으로 표기하고 forward 기대수익으로 인용하지 않는다.
- 신규 출구 정책의 최종 목적은 enforce지만, 현재 라이브 기준군과 격리된 paired shadow 및 운영자 승인을
  통과하기 전에는 주문 경로에 연결하지 않는다.

## 2. 미국 신규전략 — `US_SCHG_BIL_TREND_V1`

### 2.1 전략 계약

- 위험자산: `SCHG` — 미국 대형 성장주 ETF
- 방어자산: `BIL` — 미국 1~3개월 T-Bill ETF
- 리밸런스: 월 1회
- 위험선호 조건: 월말 SCHG 종가가 10개월 SMA 위이고 12개월 모멘텀이 양수
- 다음 달 보유: 조건 충족 시 SCHG, 아니면 BIL
- 주문: 지정가 1슬롯, PathB와 별도 자금·exit owner
- 손절/조기익절: 없음. 월간 신호만 exit owner다.
- 현재 2026-07 신호: `SCHG`

현재 가격 기준 SCHG 약 $34.58, BIL 약 $91.51이다. 기존 US 주문상한 20만원과 최근 시스템 환율
약 1,500.59원을 적용하면 SCHG 3주 약 15.57만원, BIL 1주 약 13.73만원이다. 따라서 별도 슬리브
기준금 16만원이면 두 상태 모두 정수주 실행이 가능하다. 실제 승격 전에는 주문 시점 KIS 가격·환율·수수료로
수량을 다시 계산하고 잔여현금을 원장에 기록한다.

### 2.2 성과

중앙 계약 `SMA10 + MOM12`:

| 구간 | 월수 | CAGR | Sharpe | MDD | 블록 하한 | 상위 3개월 제거 연평균 |
|---|---:|---:|---:|---:|---:|---:|
| Discovery, 2018 이전 | 83 | +9.33% | 0.82 | -14.33% | +3.44% | +6.00% |
| OOS, 2018~2026-06 | 102 | **+19.78%** | **1.28** | **-16.09%** | **+11.86%** | **+16.72%** |
| OOS 비용 스트레스 | 102 | **+19.09%** | 1.25 | -16.52% | +11.08% | +16.17% |
| 최근 2022+ | 54 | +17.96% | 1.19 | -16.09% | +8.92% | +13.25% |

인접 파라미터 6개(SMA 8/10/12 × momentum 9/12개월)는 OOS에서 전부 생존했다.

| 지표 | 6개 규칙 범위 |
|---|---:|
| CAGR | +16.52% ~ +19.78% |
| Sharpe | 1.06 ~ 1.28 |
| MDD | -17.54% ~ -16.09% |
| 블록 하한 | +8.54% ~ +11.86% |

### 2.3 단순보유와의 정직한 비교

같은 OOS에서 SCHG 단순보유는 CAGR +23.02%, Sharpe 1.26, MDD -27.77%였다.

- 절대수익만 최대화하면 단순보유가 더 높다.
- 추세형은 약 3.24%p CAGR을 포기하고 MDD를 11.68%p 줄였다.
- Calmar 근사치는 단순보유 약 0.83, 추세형 약 1.23이다.
- 최근 2022+ Sharpe는 단순보유 1.05, 추세형 1.19다.

따라서 이 전략의 정체는 초과수익 예측이 아니라 **성장 베타를 유지하면서 큰 하락국면의 복리 훼손을 줄이는
소액 실행형 코어**다. 공격형 운영자는 SCHG 보유비중을 높일 수 있지만, 시스템 기본값은 추세형이 더 맞다.

### 2.4 위험과 승격조건

- SCHG는 대형 성장주 집중이므로 독립적인 다자산 분산이 아니다.
- Yahoo 조정주가와 KIS 체결·배당·세후 원장이 다를 수 있다.
- 16만원 정수주 구성은 SCHG/BIL 상태별 자본사용률이 다르다.
- 오늘 발견 후 선택한 규칙이므로 forward 전에는 실주문 근거가 아니다.

승격 게이트:

1. 월간 scheduler·heartbeat·stale-signal 경보
2. 최소 3회 월 신호의 KIS 가격·배당·FX 교차검증
3. 정수주 모델과 목표수익의 추적오차 기록
4. 세후·실수수료 shadow 원장 양수
5. 운영자 승인 후 16만원 MICRO, 자동 승격 금지

## 3. 미국 장기 목표 — `GQMT_CORE_V1`

충분한 자본이 생기면 단일 SCHG 코어를 다음 구조로 교체한다.

| 블록 | 목표비중 | 역할 |
|---|---:|---|
| QUAL + MTUM | 32% | US 품질·모멘텀 팩터 |
| 9자산 역변동성 추세 | 32% | 주식·채권·금·원자재 분산 |
| QQQ | 16% | 성장 베타 |
| KR 모멘텀·우량주 추세 | 20% | 한국 팩터 분산 |

| 구간 | CAGR | Sharpe | MDD | 블록 하한 |
|---|---:|---:|---:|---:|
| Discovery 2018~2021 | +16.69% | 1.48 | -10.81% | +9.80% |
| OOS 2022~2025 | +14.02% | 1.32 | -9.80% | +6.29% |
| 비용·세금 스트레스 OOS | +12.83% | 1.21 | -10.72% | +4.95% |

US 12종 1주 구성에 약 464만원이 필요해 현재 계좌에는 맞지 않는다. 그러므로 지금은
`US_SCHG_BIL_TREND_V1`을 실행 가능한 proxy challenger로 두고, GQMT는 봉인된 shadow 원장으로 유지한다.

## 4. 미국 개별종목 전략 판정

### 4.1 `US_SWING_5D`

- 별도 ML swing 엔진과 order handoff fail-closed 구조는 이미 존재한다.
- 현재 상태 파일 기준 신규 신호 5개, 성숙 표본 0건이다.
- `data/analysis/us_swing_shadow.db`는 7/11 이후 갱신되지 않았고 5개가 모두 `PENDING`, entry_date도 비어 있다.
  즉 현재 0건은 전략 반증이 아니라 shadow tracker가 일일 materialize를 이어가지 못한 운영 결함이다.
- 주문 권한은 0, 슬롯 0, size multiplier 0으로 안전하게 봉인돼 있다.
- 따라서 전략이 기각된 것은 아니지만 아직 수익전략으로 승격할 증거도 없다.

조치: shadow runner의 일일 스케줄·heartbeat를 복구하고 5일 성숙 표본을 계속 쌓는다. 최소 20건,
비용 후 평균·블록 하한·상위 3건 제거·KIS 가격 교차검증이 모두 양수일 때만 MICRO를 논의한다.

### 4.2 US PathB

현재 PathB US를 넓혀서는 안 된다.

- 실제 청산 240건 net 합: -43.60%p
- US 부분익절 반실험: -7.66%p
- 상위 3건 제거: -46.23%p
- 6월: -50.69%p

US에서 유지할 연구 arm은 두 개뿐이다.

1. `PATHB_US_ZONE_QUALITY_SHADOW_V1`
   - 존 상단 67% 이상·목표거리 5% 이상 48건 평균 -1.03%
   - 하드차단이 아니라 0.25배 가상비중과 passive limit을 비교
2. `PATHB_GAP_ESCAPE_CONTINUATION_SHADOW_V1`
   - `cancel_if_open_above` 후 US 8건 평균 +3.53%
   - 표본이 작아 0.10배 독립 shadow만 허용

Profit-ladder A/B는 계속 관측하되 전략 간 표본을 섞지 않는다.

## 5. 한국장 전략

### 5.1 KR 저회전 팩터 코어

KODEX 모멘텀주(275280)와 KODEX 우량주(275300)를 절반씩 배정하고, 각 ETF가 10개월 SMA 위이면서
12개월 momentum이 양수일 때 보유한다. 꺼진 절반은 KODEX 단기채권(153130)에 둔다.

| 구간 | CAGR | Sharpe | MDD |
|---|---:|---:|---:|
| 2018~2021 | +7.29% | 0.75 | -8.15% |
| OOS 2022~2025 | +12.90% | 0.81 | -16.59% |
| 같은 기간 KODEX200 보유 | +13.59% | 0.67 | -27.94% |

초과 CAGR 전략이라기보다 손실 효율 개선 전략이다. 3종 1주 구성 약 19만원으로 현재 KR 50만원 예산에서
실행 가능하지만, GQMT와 마찬가지로 3회 월간 KIS cross-check 전에는 shadow다.

### 5.2 `PATHB_KR_SPLIT_RUNNER_V1`

이 정책은 현재 live early-tier 위에 단순 추가하지 않는다. 현재 정책 A와 Split-Runner B를 같은 실시간 가격
경로에서 paired shadow로 비교한다. A는 목표거리 40%에서 무장되는 early-tier 전량청산을 그대로 복제한다.
B는 +3.6% 전에는 early/tier1~3의 profit-side 전량청산 권한을 보류하되 hard stop·loss cap 등 손실보호를
유지한다. +3.6% 도달 시 정수수량의 50%를 한 번만 가상익절하고, 나머지를 tier3/tier4·기존 target·pre-close에
맡긴다. early-target은 B 잔량의 전량청산 소유자로 복귀하지 않는다.

| 검증 | 결과 |
|---|---:|
| 기존 51건 net 합 | -11.10%p |
| Split-Exit 역사적 MFE 낙관 상한 | **+38.84%p** |
| 정수수량 실행 가능 | 34/51건 |
| 상위 3건 제거 | **+21.73%p** |
| 상·하위 3건 제거 | **+31.63%p** |
| 추가 0.30% 슬리피지 스트레스 | **+33.74%p** |

월별 합계는 4월 +10.33, 5월 +5.34, 6월 +22.34, 7월 +0.83%p였다. 활성점을
3.0/3.6/4.0/4.5%로 바꿔도 50% 분할은 모두 월별 양수였다.

중요한 한계는 기록 MFE가 도달 가능성의 낙관적 상한이라는 점이다. 따라서 실제 주문 전에 같은 규칙을
실시간 분봉 순서·정수수량·비용·슬리피지를 반영한 paired 가상주문으로 배선하고, `n>=15`, 비용 후 paired
delta>0, 블록 하한>0, 상위 3건 제거 후 양수를 요구한다. B 관측기는 `runtime/intraday_minute_cache`를
read-only로 소비하며 라이브 출구 함수에 분기·콜백을 추가하지 않는다. 모든 행에 A/B `exit_owner`와 cache
watermark를 기록한다.

역사 51건 중 1주 포지션은 4건(7.8%)이고, 17건은 분할 불가가 아니라 +3.6% 미도달이었다. 도달 34건은
모두 정수 부분청산 가능했다. enforce 후 1주 포지션은 `A_FALLBACK_QTY1`으로 현재 early-tier 정책을
유지한다. 성과는 전체 포지션·qty>=2 분할 가능 포지션·실제 +3.6% trigger 포지션의 세 분모로 보고하며,
이미 미도달 거래를 포함한 역사 전체 반사실에 2/3 할인을 다시 적용하지 않는다.

### 5.3 KR 보조 신호

- 외국인·기관 수급 shadow는 117일 데이터가 있어 판독 비용이 낮다. enforce 전 독립 판독한다.
- volume_rank는 17건 +10.26%p지만 상위 3건 제거 후 -8.29%p라 확대하지 않는다.
- 5일 KR momentum은 정제 OOS에서 반증됐으므로 폐기한다.
- NEXTDAY_1D는 신호 공급 복구 전 보류한다.

## 6. 통합 자본 구조

### 현재 소액계좌 단계

| 레인 | 상태 | 주문권한 | 목적 |
|---|---|---:|---|
| US SCHG/BIL 추세 | 신규 shadow | 0 | 현재 자본용 미국 코어 |
| KR 팩터 추세 | shadow | 0 | 한국 저회전 코어 |
| KR Split Runner | forward shadow | 0 | 기존 PathB 봉우리 반납 회수 |
| US swing | 기존 shadow | 0 | 독립 5일 알파 challenger |
| US zone/gap | 관측 shadow | 0 | 체결품질·갭지속 연구 |
| GQMT | 봉인 shadow | 0 | 자본확대 후 최종 코어 |

### 충분한 자본·승격 후 목표 위험예산

| 레인 | 목표 위험예산 | 전제 |
|---|---:|---|
| GQMT Core | 70% | 최소 정수주 자본·3회 교차검증 |
| US swing | 15% | forward 승격 게이트 통과 |
| 섹터 회전 challenger | 10% | discovery 약점 해소·forward 양수 |
| PathB convex capture | 5% | 전체 PF≥1.2·capture forward 양수 |

현재 자본에서 70% GQMT를 억지로 복제하지 않는다. US SCHG/BIL과 KR 팩터는 GQMT를 대체 확정하는
것이 아니라, 정수주 실행 가능한 단계적 proxy다.

## 7. 중앙 자본배분기 계약

각 전략의 주문 제안에 다음을 의무화한다.

- `strategy_id`, 시장, 신호시점, 목표보유기간, exit owner
- 기대 순수익, 왕복비용, 최대손실, 필요 주문가능금
- 실제 정수수량, 자본사용률, 잔여현금
- 동일 종목·섹터·성장베타 중복노출
- 상태: `SHADOW → PROBE → MICRO → STANDARD → CORE`

PathB가 새 후보를 만들었다는 이유만으로 코어를 매도하지 않는다. 서로 다른 레인의 주문가능금과 슬롯을
미리 예약하고, 총노출·동일 베타 중복을 중앙에서 제한한다.

## 8. 명시적으로 기각한 방향

- US/KR 일중 immediate·VWAP·OR·volume·pullback 확대
- US에 KR Split-Exit 복제
- US SL6/SL8 + TP10 5일 전략
- KR_CONFIRM_5D
- profit_path tier-relabel ML 현 버전
- 고정 QQQ/tape/repeat-loss 위험점수의 즉시 enforce
- 부분 2R 회수·본전스톱
- auto-sell 검토 우회
- 교차시장 단순 선행, 평균회귀, 뉴스 단독 추격

이들은 데이터가 없어서 포기한 것이 아니라, 기존 DB·가격 백필·비용·OOS·데이터 위생 검사를 거쳐
현재 형태가 반증된 것이다. 이후 새 데이터 계약이나 다른 호라이즌이 생기면 새 전략 ID로 다시 경쟁시킨다.

profit_path tier-relabel의 재현 근거는 `tools/profit_path_tier_relabel_lab.py`,
`reports/profit_path_tier_relabel_lab_20260715.json`,
`docs/reports/profit_path_tier_relabel_lab_20260715.md`다. 실행 가능한 sequential p80에서 US 평균 -0.537%,
합성 tier -0.659%; KR raw 평균 +0.253%지만 합성 tier -0.804%, 상위 3건 제거 합 -40.27%p였다.

## 9. 실행 우선순위

1. early-tier A와 Split-Runner B의 출구 소유권·read-only 데이터 접근·낙관 상한 계약 고정
2. KR 플랜·체결 스루풋과 paired 신규 n/주를 `/monitor`에 표시하고 시험 starvation을 P0로 연결
3. `PATHB_KR_SPLIT_RUNNER_V1` 실시간 paired 가상 부분체결 forward 배선
4. US SCHG/BIL·KR 팩터·index benchmark를 하나의 코어 shadow tracker·원장·heartbeat로 통합
5. `/monitor`에 코어 tracker·US swing·paired observer 갱신시각, n=15 ETA, stale 판정 추가
6. US swing tracker의 성숙 표본 수집 복구
7. KIS 가격·FX·배당·세금 truth 원장 통합
8. paired 게이트 통과 후 `.env.live`와 start-config 동시변경, 재시작, runtime snapshot 실측을 거쳐
   운영자 승인으로 KR `SPLIT_RUNNER_V1` MICRO enforce
9. 중앙 자본배분기는 별도 승인 단위로 설계·shadow 노출 시뮬레이션 후 주문경로 연결

## 10. 재현 산출물

- `tools/us_affordable_trend_lab.py`
- `reports/us_affordable_trend_lab_20260715.json`
- `reports/us_affordable_trend_ledger_20260715.csv`
- `tools/split_exit_runner_lab.py`
- `reports/split_exit_runner_lab_20260715.json`
- `tools/profit_path_tier_relabel_lab.py`
- `reports/profit_path_tier_relabel_lab_20260715.json`
- `docs/reports/profit_path_tier_relabel_lab_20260715.md`
- `docs/reports/enforce_transition_delta_20260715.md`
- `tools/strategy_frontier_lab.py`
- `tools/integrated_core_strategy_lab.py`
- `tests/test_us_affordable_trend_lab.py`
- `tests/test_current_system_strategy_labs.py`

최종 철학은 단순하다. **저회전 코어가 기본 복리를 만들고, PathB는 봉우리 수확만 하며, swing과 신규
아이디어는 코어를 실제 forward에서 이긴 뒤에만 자본을 받는다.**
