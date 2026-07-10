# 설계: 비용을 초과하는 Gross-Alpha Enforce Engine (2026-07-10)

요청: "부족한 단계를 채워 넣어서 최대한 enforce 형태로 검증까지 설계. 비용·세금·FX는 수익률로 이긴다."

상태: **enforce-ready 설계**. 이 문서는 무엇을 실제 주문 차단/허용/사이징에 연결할지와 검증·롤백 계약을 확정한다. 라이브 config와 코드는 이 문서 작성만으로 변경하지 않는다.

---

## 0. 최종 결정

목표는 회전이나 비용 자체를 최소화하는 것이 아니다.

> **실현 Gross Alpha가 수수료·세금·FX·슬리피지·미체결 기회비용을 초과하고도 안전마진을 남기는 거래만 자본을 받는다.**

패시브는 자본배분안이 아니라 비교 기준이다. 시스템의 최종 판정식은 다음이다.

```text
net_edge
= realized_gross_return
- commission
- tax
- fx_cost
- adverse_slippage
- opportunity_cost
```

운영 구조:

```text
시장 사실(red/green/reversal)
    -> 전략/원천 필터
    -> empirical alpha hurdle
    -> PROBE / STANDARD / PRESS 사이징
    -> 지정가 주문
    -> 기존 손실방어 + tail 청산
    -> 비용 후 portfolio ledger
    -> 자동 유지/증액/롤백
```

LLM은 시장·이벤트 정보 구조화, 가설 생성, 반대검토, 포지션 관리 설명을 맡는다. 최종 차단·수량·손실한도는 결정론적 엔진이 소유한다.

---

## 1. 현재 기준선과 검증 결과

데이터: `data/ml/decisions.db`, live/closed/portfolio_realized, 2026-04-27~2026-07-07.

### 1.1 전수 기준선

| 시장 | n | gross 합 | net 합 | net 평균 | PF |
|---|---:|---:|---:|---:|---:|
| KR | 62 | -24.47%p | -34.35%p | -0.554% | 0.66 |
| US | 253 | +42.15%p | -57.81%p | -0.229% | 0.81 |

주의: 위 합은 거래별 수익률 단순합이지 계좌수익률이 아니다. `pnl_krw_net` 203건 결측 때문에 자본가중 equity curve는 아직 불완전하다. 이 결측 복구는 enforce 성과판정의 P0 선결이다.

### 1.2 확인된 구조

1. **US는 gross 양수지만 비용 허들을 못 넘었다.** 평균 gross 약 +0.17%, 평균 net -0.23%로 약 0.40%p가 all-in 마찰/환율 효과에 소모됐다.
2. **KR은 전체 gross부터 음수**다. 비용절감보다 진입원천 정리가 먼저다.
3. 손익은 tail에 의존한다. TARGET/Claude 관리 청산은 강하고, 손실은 LOSS_CAP 모집단에 집중한다. LOSS_CAP을 늦추는 것이 아니라 LOSS_CAP으로 갈 진입을 줄여야 한다.
4. 고상관 클러스터는 주범이 아니다. 3종목+ 동시청산일 28일 모두 평균상관 0.5 미만이었다. 종목쌍 상관보다 시장 공통노출을 제어한다.
5. 매수·매도 발주가→체결가 슬리피지는 평균적으로 불리하지 않았다. 손실 오버슈트는 주문 체결방식보다 트리거 감지/갭 구간 문제다.

### 1.3 전략/국면 필터의 재생 결과

과거 거래에 다음 정책을 **중복 제거 후** 재생했다.

- KR: `strategy == claude_price`만 허용.
- US: `gap_pullback`, `mean_reversion`, `RECOVERY_MICRO` 차단.
- US: `market_regime in {CAUTIOUS, MILD_BEAR}` 차단.

| 시장 | 구분 | n | gross 합 | net 합 | net 평균 | PF |
|---|---|---:|---:|---:|---:|---:|
| KR | 허용 | 23 | +22.22 | **+20.54** | +0.893 | **2.14** |
| KR | 차단 | 39 | -46.69 | **-54.88** | -1.407 | 0.35 |
| US | 허용 | 152 | +89.03 | **+27.77** | +0.183 | **1.17** |
| US | 차단 | 101 | -46.89 | **-85.58** | -0.847 | 0.35 |

강건성:

- KR 허용군: top-1 제거 후 +13.74, top-3 제거 후 +0.98. 얇지만 양수 유지.
- US 허용군: top-1 제거 후 +10.60, **top-3 제거 후 -13.00**. tail 의존이 남는다.
- US 허용군 월별 net: 4월 -0.20, 5월 +41.48, 6월 -8.46, 7월 -5.05. 즉 **통산 양수만으로 PRESS 증액 금지**다.

이 재생은 같은 표본에서 규칙을 찾고 다시 적용한 in-sample 상한이다. 따라서 필터의 차단은 enforce할 수 있지만, 양수 잔존군을 곧바로 최대 사이즈로 증액하는 근거는 아니다.

### 1.4 개별 후보 판정

| 후보 | 실측 | 판정 |
|---|---|---|
| US red-tape `< -0.3%` | 기존 다각도 검증, 현재 enforce | **유지 enforce** |
| 시장 sharp reversal | 현재 enforce | **유지 enforce** |
| judge cap 확대 | dropped 35건 근사 net -55.50, 중앙 -1.72 | **확대 금지 enforce** |
| 광범위 repeat-loss bucket 차단 | 2회 후 196건 -108.94지만 US 5월 +20.69 | **전역 차단 금지** |
| ticker 2회 손실 차단 | n=24 -13.27, US 5월 +1.25/6월 -11.68 | **조건부 설계 필요** |
| KR `volume_surge` bucket 2회 손실 후 | 4월 -4.47, 5월 -17.07 | **조건부 enforce 후보** |
| US `momentum_now` bucket 2회 손실 후 | 4월 -1.67, 5월 -3.76, 6월 -7.13 | **조건부 enforce 후보** |
| US `opening_range_pullback` | 4월 +2.43(n4), 5월 +5.91(n3), tail 의존 | **micro-enforce 유지/확대 금지** |
| MFE peak-trail/free-carry | 사후 MFE 사용으로 순서 look-ahead | **시간축 n 선결** |

---

## 2. Enforce 등급 체계

`shadow 또는 full-live`의 이분법을 폐기하고 실제 주문을 내되 위험을 다르게 주는 네 등급을 사용한다.

### 2.1 등급

| 등급 | 행동 | 1회 위험 |
|---|---|---:|
| `BLOCK` | 주문 차단, forward 측정은 지속 | 0R |
| `PROBE` | 실제 소액 주문, alpha 발견 단계 | 0.25R |
| `STANDARD` | 검증된 기본 주문 | 0.50R |
| `PRESS` | 독립 OOS 재현 후 증액 | 1.00R |

`1R = 시장별 active equity의 0.50%`를 초기값으로 한다. 운영자가 R을 바꾸더라도 등급 배수와 승격조건은 유지한다.

```text
risk_krw       = active_equity_krw * risk_pct * grade_multiplier
stop_distance  = abs(entry - effective_stop) / entry
order_notional = min(
    risk_krw / stop_distance,
    active_equity_krw * single_name_cap,
    liquidity_cap,
    available_cash
)
```

초기 cap:

- KR single-name cap: active equity 25%.
- US single-name cap: active equity 15%.
- 총 open risk: 시장별 2R, 전체 3R.
- 일일 신규 위험: 시장별 3R.
- PROBE는 기존 US 20만원 예산을 넘지 않는다.
- 현재 KR 고정 50만원은 위험공식과 25% cap보다 우선할 수 없다.

### 2.2 비용 허들

고정 비용표만 쓰지 않고 최근 60일 실현 all-in cost의 p75를 사용한다. 표본이 없으면 config 상한을 사용한다.

```text
alpha_hurdle_market
= p75(all_in_cost_pct, trailing_60d)
+ safety_margin
```

초기 safety margin:

- US: +0.25%p. 현재 명목 허들 약 `0.50 + 0.25 = 0.75%` gross/거래.
- KR: +0.15%p. 현재 명목 허들 약 `0.21 + 0.15 = 0.36%` gross/거래.

비용이 커져도 임계를 낮추지 않는다. 신호 gross가 허들을 넘지 못하면 `BLOCK` 또는 `PROBE`다.

---

## 3. Alpha Hurdle Gate

신규 모듈 제안: `decision/alpha_hurdle_gate.py`.

### 3.1 입력 컨텍스트

결정시점에 다음을 불변 스냅샷으로 저장한다.

```text
market
strategy_at_entry
path_type
primary_bucket_at_entry
timing_style_at_entry
session_phase_at_entry
risk_mode_at_entry
consensus_mode_at_entry
index_change_from_open
index_change_from_peak
breadth_source
breadth_universe_count
breadth_advance_ratio
breadth_delta_15m
candidate_vwap_distance_pct
candidate_pullback_from_high_pct
candidate_volume_ratio_open
candidate_spread_bps
planned_stop_pct
planned_reward_pct
expected_all_in_cost_pct
strategy_version
experiment_id
```

현재 `ready`는 `not_patha_trade_ready 미표기 ≈ ready`인 proxy라 enforce 입력으로 금지한다. `patha_trade_ready_at_entry: true/false`를 명시적으로 배선한 뒤에만 쓴다.

### 3.2 통계 계약

- 오늘 거래에는 어제까지 확정된 결과만 사용한다.
- expanding 또는 rolling window 모두 session 1일 embargo를 둔다.
- 동일 일자·동일 시장 거래는 한 event cluster로 묶는다.
- 종목 거래 수와 독립 event-day 수를 함께 기록한다.
- 후보 조합을 바꾸면 `strategy_version`과 `experiment_id`를 새로 만든다.
- 시험한 모든 조합 수를 registry에 남긴다. 선택된 최적 조합만 기록하는 행위를 금지한다.

평가식:

```text
expected_gross = mean(realized_gross_pct in comparable prior context)
expected_net   = expected_gross - expected_all_in_cost
```

부트스트랩은 거래행이 아니라 event cluster 단위로 수행한다.

### 3.3 등급 승격

`PROBE`:

- 신규/표본부족 가설 또는 내부 양수이나 OOS 미확정.
- hard block에 걸리지 않음.
- 거래 n<30 또는 독립 event-day<10.

`STANDARD`:

- OOS 거래 n≥30, 독립 event-day≥10, 달력월≥2.
- event-cluster bootstrap 80% 하한 `expected_net > 0`.
- net PF≥1.10.
- top-1 제거 net≥0.
- 데이터 완전성≥95%, 주문/브로커 불일치 0.

`PRESS`:

- OOS 거래 n≥60, 독립 event-day≥20, 달력월≥3.
- micro-live 거래 n≥20 포함.
- event-cluster bootstrap 90% 하한 `expected_net > 0`.
- 실제 gross 평균이 시장 alpha hurdle 초과.
- top-3 제거 net≥0 또는 서로 다른 tail episode≥3이고 단일 episode가 총이익의 50% 미만.
- rolling 20거래 net>0, PF≥1.20.

---

## 4. 시장별 즉시 Enforce 정책

### 4.1 KR

#### 허용

- `strategy_at_entry == claude_price`: **초기 PROBE(0.25R)**. 과거 잔존군은 좋지만 n=23, 6월 집중이다. §3.3 조건을 채우면 즉시 STANDARD(0.50R)로 승격한다.
- 새 breadth reaction lane: §5 조건 충족 시 `PROBE`.

#### 차단

- legacy `gap_pullback`, `momentum`, `mean_reversion`, `opening_range_pullback`: `BLOCK`.
- `primary_bucket == volume_surge`이고 최근 10일 동일 bucket 손실 event 2회 이상: 48시간 `BLOCK`.
- 현재 시장 sharp reversal active: 기존 차단 유지.

#### 보류

- KR red-tape 정적 차단 금지. 최신 재측정 red n=4로 부족하고 6월 일부 양수다.
- KR LLM mode 기반 bull-only gate 금지. 4월/6월 부호가 뒤집혔다.

### 4.2 US

#### 허용

- `opening_range_pullback`: `PROBE`. n=7 양월 양수지만 tail 의존이라 STANDARD 금지.
- `momentum`: `PROBE`. 4월 음수, 5·6월 양수로 전환됐으나 안정성 부족.
- `claude_price`: 기본 `PROBE`; Alpha Hurdle Gate를 통과한 context만 `STANDARD`.

#### 차단

- `gap_pullback`, `mean_reversion`, `RECOVERY_MICRO`: `BLOCK`.
- `consensus/market_regime in {CAUTIOUS, MILD_BEAR, CAUTIOUS_BEAR, DEFENSIVE, HALT}` 또는 `risk_mode in {RISK_OFF, HALT}`: 신규진입 `BLOCK`.
  - CAUTIOUS는 5월 -2.86, 6월 -15.19.
  - MILD_BEAR는 5월 -9.03, 6월 -31.98.
  - CAUTIOUS_BEAR 이하와 RISK_OFF는 MILD_BEAR보다 방어적인 상태라는 단조 위험계약으로 포함한다. 별도 green recovery override가 검증되기 전에는 예외를 두지 않는다.
- red-tape `< -0.3%`: 기존 enforce 유지.
- market sharp reversal: 기존 enforce 유지.
- `primary_bucket == momentum_now`이고 최근 10일 동일 bucket 손실 event 2회 이상: 48시간 `BLOCK`.

#### 주의

기존 `PATHB_REPEAT_LOSS_GATE_ENABLED`는 ticker 전역 차단이고 bucket/regime 조건을 모른다. 현 상태로 단순 on 하지 않는다. 위 조건을 지원하는 `CONDITIONAL_REPEAT_LOSS_GATE`를 별도로 만든다.

### 4.3 judge 용량

- 현재 cap 10 유지.
- green-tape라는 이유만으로 cap 확대 금지.
- 측정 유니버스는 넓게 유지하되 실행 유니버스만 좁힌다.
- dropped 후보는 forward label을 계속 남긴다.

---

## 5. Gross Alpha를 새로 만드는 Reaction Lane

정적 예측이 아니라 시장이 실제로 열린 뒤 확인되는 확산·지속성에 참여한다. 새 lane은 shadow가 아니라 **PROBE micro-enforce**로 시작한다.

### 5.1 시장 증거

KR은 KIS KOSPI/KOSDAQ advancers/decliners를 사용한다. 후보 스크린 breadth는 유니버스 편향이 있으므로 보조값이다.

US는 거래소 전체 breadth 소스가 준비되기 전 이 lane을 live로 열지 않는다. S&P500/NASDAQ 지수만으로 breadth를 대체하지 않는다.

데이터 신선도:

- snapshot age≤120초.
- valid universe coverage≥80%.
- source와 universe_count가 직전 snapshot과 동일한 계열.
- 결측이면 fail-closed: reaction lane만 차단하고 기존 lane은 자체 정책을 따른다.

### 5.2 Green follow-through v1

초기 pre-registered 조건:

```text
market_advance_ratio >= 0.65
breadth_delta_15m    >= +0.10
primary_index_change_from_open > 0
primary_index_change_from_peak > -0.50
sharp_reversal == false
red_tape == false
```

종목 조건:

```text
post_open.data_quality == minute_complete
current_price >= VWAP
pullback_from_open_high in [-2.5%, -0.5%]
volume_ratio_open >= 1.5
momentum_state in {sustained, early_strength}
not overextended
spread_bps <= market p75 spread cap
empirical gross hurdle 통과 또는 PROBE 허용
```

행동:

- 첫 돌파 추격 금지. pullback 후 VWAP/reclaim 지정가만 허용.
- 15분당 신규 PROBE 최대 1개.
- green 유지 중 judge cap을 늘리지 않는다. 기존 cap 안에서 우선순위만 올린다.
- breadth 조건이 해제되면 미체결 주문 취소, 신규 제출 중단. 체결 포지션은 기존 exit engine이 관리한다.
- 시작 사이즈 `0.25R`, 일일 최대 2건.

승격:

- OOS n≥30, 독립 green event-day≥8, 달력월≥2.
- gross 평균이 시장 alpha hurdle 초과.
- net PF≥1.10, top-1 제거 net≥0.
- 조건 통과 시 `0.50R STANDARD`.

kill:

- rolling 15건 net≤-3R 또는 PF<0.70.
- 서로 다른 green day 5회 중 4회 음수.
- breadth data quality<95%.

---

## 6. 진입 플랜과 체결

### 6.1 Claude target을 RR 검증값으로 직접 사용하지 않는다

과거 plan reward는 실측 MFE보다 과대여서 reward/risk가 거의 항상 통과했다. 다음으로 교체한다.

```text
effective_reward_pct
= min(
    planned_reward_pct,
    context_empirical_mfe_p50_or_p60
)

effective_rr
= (effective_reward_pct - expected_all_in_cost_pct)
  / abs(effective_stop_pct)
```

- conditional MFE n<20이면 RR은 `unknown`; PROBE만 가능.
- `effective_rr`가 시장별 최소값 미만이면 BLOCK.
- planned target이 높다는 이유로 등급을 올리지 않는다.

### 6.2 지정가 정책

- zone 안 지정가/peg 유지.
- spread가 넓을수록 시장가 전환이 아니라 대기시간을 늘린다.
- fill rate만 최적화하지 않고 implementation shortfall을 기록한다.
- 미체결 뒤 상승한 가격은 opportunity cost, 하락한 가격은 avoided loss로 같은 ledger에 남긴다.

### 6.3 추격 한도

- plan 생성가 대비 max chase 1% 기존 상한은 유지하되 reaction lane은 0.5%로 강화.
- zone 상단 초과 시 cancel/replan, 즉시 marketable 주문 금지.

---

## 7. 청산 Enforce 설계

### 7.1 즉시 유지

- LOSS_CAP/HARD_STOP 완화 금지.
- TARGET/Claude 관리 tail 유지.
- US ladder A/B enforce 유지하되 독립 성과 태그를 계속 기록.
- 자동매도 Claude review 계약 유지.

### 7.2 스톱 오버슈트

발주가→체결가 loss-cap 슬리피지는 US n=53 평균 -0.151%로 불리하지 않았다. 주문유형 변경은 우선순위가 아니다.

`stop_trigger_price/at` primary 표본은 n=2이고 시간차가 약 9시간으로 나타나 timezone/필드 의미 오염 가능성이 있다. 다음을 enforce 전에 수리한다.

- 모든 trigger/sent/fill 시각을 UTC ISO + timezone-aware로 통일.
- `detected_at`, `review_started_at`, `order_sent_at`, `filled_at` 분리.
- trigger→detect, detect→review, review→sent, sent→fill 네 구간을 따로 측정.

측정이 정상화되기 전 stop 임계와 청산 지연을 변경하지 않는다.

### 7.3 Risk-recovery runner

원금 전액회수 free-carry는 소액·정수주 계좌에서 잔량이 거의 남지 않는다. 대신 **초기 위험금액 회수 + 잔량 runner**를 검증한다.

전방 판정 선결:

- `mfe_time`와 `mae_time` 모두 존재 n≥30.
- MFE가 MAE보다 먼저 발생한 순서 확인.
- 서로 다른 tail event-day≥3.

후보 규칙:

```text
if MFE >= 2R
and MFE occurred before MAE
and qty >= 4
and market red/sharp reversal is false:
    초기 risk_krw만큼 이익을 확정하도록 일부매도
    residual stop = max(entry + all_in_cost, current protective floor)
    residual target cap 제거
```

첫 적용은 `PROBE_EXIT`로 기존 ladder와 50/50 결정적 A/B 배정한다. 전방 Δnet>0, tail episode 보존, giveback 증가가 risk budget 이내일 때만 enforce 전환한다.

---

## 8. 검증·승격·자동 롤백

### 8.1 반드시 기록할 이벤트

```text
ALPHA_GATE_EVALUATED
ALPHA_GATE_BLOCKED
ALPHA_GRADE_ASSIGNED
MARKET_EVIDENCE_SNAPSHOT
ORDER_INTENT_CREATED
IMPLEMENTATION_SHORTFALL_FINALIZED
EXPERIMENT_ARM_ASSIGNED
EXPERIMENT_OUTCOME_FINALIZED
```

모든 이벤트에 `strategy_version`, `experiment_id`, `decision_id`, `market`, `ticker`, `known_at`, `data_quality`를 강제한다.

### 8.2 primary 지표

1. 자본가중 `pnl_krw_net` equity curve.
2. gross/net expectancy per trade 및 per independent event-day.
3. all-in cost와 gross-cost margin.
4. PF, MDD, turnover, fill rate, implementation shortfall.
5. top-1/top-3 및 단일 event 기여도.
6. 동기간 동일 위험 benchmark excess.

거래별 수익률 합은 진단용 보조지표로만 쓴다.

### 8.3 자동 롤백

각 rule/strategy_version 단위로 다음 중 하나면 한 단계 강등한다.

- rolling 20 live trades net<0.
- rolling 20 PF<0.80.
- 최근 독립 event-day 5개 중 4개 음수.
- 실현 gross가 해당 시장 alpha hurdle 아래로 10거래 지속.
- 데이터 완전성<95%.
- 브로커/로컬 포지션 불일치 또는 잘못된 중복주문 1건: 즉시 BLOCK, 운영자 확인.

`PRESS -> STANDARD -> PROBE -> BLOCK` 순으로 강등한다. 자동 재승격은 금지하고, 승격은 확정 리포트와 운영자 승인으로만 한다.

---

## 9. 구현 순서

### Phase E0 — 측정과 기존 필터 (즉시 구현 가능)

1. `pnl_krw_net` 결측 복구와 portfolio ledger 단일화.
2. `strategy_at_entry`, `primary_bucket_at_entry`, exact trade-ready durable 배선.
3. KR legacy 전략 차단, US 음수 전략/CAUTIOUS/MILD_BEAR 차단.
4. judge cap 10 유지 고정.
5. 위험기반 등급 사이징으로 KR 고정 50만원 우선권 제거.
6. 관련 단위·통합·replay 테스트.

### Phase E1 — Alpha Hurdle Gate (E0 직후)

1. trailing all-in cost p75.
2. lagged comparable-context expectancy.
3. BLOCK/PROBE/STANDARD/PRESS 결정.
4. event-cluster 통계와 자동 강등.

### Phase E2 — Reaction Lane micro-enforce

1. KR KIS breadth snapshot durable 저장.
2. pre-registered green follow-through 조건.
3. 일 2건, 0.25R micro-live.
4. US는 exchange-wide breadth 소스 확보 후 동일 계약으로 시작.

### Phase E3 — Tail 증액

1. MFE/MAE 시간축 n≥30.
2. runner A/B.
3. 독립 tail episode≥3 후 exit enforce.

---

## 10. 구현용 config 초안

```text
ALPHA_HURDLE_GATE_MODE=enforce
ALPHA_HURDLE_COST_LOOKBACK_DAYS=60
ALPHA_HURDLE_US_MARGIN_PCT=0.25
ALPHA_HURDLE_KR_MARGIN_PCT=0.15

ALPHA_GRADE_RISK_PCT=0.50
ALPHA_GRADE_PROBE_MULT=0.25
ALPHA_GRADE_STANDARD_MULT=0.50
ALPHA_GRADE_PRESS_MULT=1.00
KR_ALPHA_SINGLE_NAME_CAP_PCT=25
US_ALPHA_SINGLE_NAME_CAP_PCT=15

KR_PATHB_STRATEGY_ALLOWLIST=claude_price
US_PATHB_STRATEGY_DENYLIST=gap_pullback,mean_reversion,RECOVERY_MICRO
US_PATHB_MODE_DENYLIST=CAUTIOUS,MILD_BEAR,CAUTIOUS_BEAR,DEFENSIVE,HALT
US_PATHB_RISK_MODE_DENYLIST=RISK_OFF,HALT

CONDITIONAL_REPEAT_LOSS_GATE_MODE=enforce
CONDITIONAL_REPEAT_LOSS_LOOKBACK_DAYS=10
CONDITIONAL_REPEAT_LOSS_COUNT=2
CONDITIONAL_REPEAT_LOSS_COOLDOWN_HOURS=48
KR_REPEAT_LOSS_BUCKETS=volume_surge
US_REPEAT_LOSS_BUCKETS=momentum_now

GREEN_REACTION_KR_MODE=micro_enforce
GREEN_REACTION_US_MODE=off
GREEN_REACTION_ADVANCE_RATIO_MIN=0.65
GREEN_REACTION_BREADTH_DELTA_15M_MIN=0.10
GREEN_REACTION_MAX_TRADES_PER_DAY=2
GREEN_REACTION_RISK_GRADE=PROBE

EARLY_JUDGE_GREEN_CAP_EXPANSION_ENABLED=false
```

실제 config 이름은 기존 규약과 충돌 검사를 거쳐 확정한다. `.env.live`와 `config/v2_start_config.json` 양쪽 일치가 필수다.

---

## 11. 테스트 계약

### 단위 테스트

- 시장/전략 denylist 경계.
- CAUTIOUS/MILD_BEAR/CAUTIOUS_BEAR/DEFENSIVE/HALT 및 RISK_OFF 차단과 그 외 모드 통과.
- 비용 p75 + margin 계산.
- 등급별 수량 및 single-name cap 우선권.
- conditional repeat-loss: 대상 bucket만 차단, 비대상/기간초과 통과.
- breadth 신선도/coverage fail-closed.
- green condition 경계값.
- auto downgrade 상태전이.

### replay 테스트

- 2026-04~07 closed 전체에 E0 정책 재생 결과가 본 문서 표와 일치.
- 같은 decision이 두 필터에 걸려도 1회만 차단 집계.
- 과거 시점 이후 데이터가 gate 입력에 섞이지 않음.
- 비용 모델 measured/backfilled별 중복차감 없음.

### live preflight

- 브로커 포지션 0, 미체결 0 확인.
- enforce config 두 소스 일치.
- 새 이벤트 1회 dry-run 생성 및 schema 확인.
- BLOCK 후보 주문 0건, PROBE 수량 cap 확인.
- kill/revert 토글 동작 확인.

---

## 12. 하지 않을 것

- 수익이 부족하다는 이유로 judge cap/후보 수를 확대하지 않는다.
- LLM confidence를 확률로 사용하거나 confidence만으로 증액하지 않는다.
- 동일 데이터에서 찾은 최적 조합을 곧바로 PRESS로 올리지 않는다.
- loss cap을 늦춰 gross를 만드는 척하지 않는다.
- 사후 MFE를 보고 peak exit를 가정한 결과를 enforce하지 않는다.
- 비용을 낮춰 보이게 net 모델을 바꾸지 않는다.

---

## 13. 외부 근거와의 정합성

- 고회전 anomaly는 거래비용 후 소멸하기 쉽고 buy/hold band가 단순 비용완화 중 가장 효과적이었다: [Novy-Marx & Velikov, NBER](https://www.nber.org/system/files/working_papers/w20721/w20721.pdf).
- 긴/넓은 평가에서 LLM 투자 우위가 약해지고 regime 위험통제가 중요했다: [FINSABER](https://arxiv.org/abs/2505.07078).
- 다중시험은 일반 holdout만으로 제거되지 않는다. 모든 trial registry와 cluster OOS가 필요한 이유다: [Bailey et al., UC/LBNL](https://escholarship.org/content/qt4hn4t174/qt4hn4t174.pdf).
- market breadth는 광범위 표본에서 설명력이 보고됐지만, 본 시스템에서는 예측신호가 아니라 reaction exposure gate로 검증한다: [Market breadth, Economic Modelling](https://www.sciencedirect.com/science/article/pii/S0264999319312982).
- VIX term structure는 VIX9D/VIX/VIX3M 등 만기별 시장상태 입력으로만 사용한다: [Cboe](https://www.cboe.com/tradable-products/vix/term-structure).

---

## 14. 최종 판정

가장 공격적인 안전 설계는 **모든 것을 shadow로 미루는 것**도, **통산 양수 반사실을 곧바로 최대 사이즈로 켜는 것**도 아니다.

1. 반복 손실이 검증된 경로는 즉시 BLOCK enforce.
2. 가능성이 있으나 표본이 작은 전략은 실제 주문을 내는 PROBE enforce.
3. 비용 허들을 OOS로 넘긴 전략만 STANDARD.
4. 서로 다른 시장 창구에서 tail이 반복된 전략만 PRESS.

이 구조는 비용을 회피하지 않는다. **비용을 이길 gross alpha가 입증될 때 자본을 빠르게 집중하고, 입증되지 않은 경로에는 자본을 주지 않는다.**
