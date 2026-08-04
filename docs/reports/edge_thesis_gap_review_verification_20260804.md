# 엣지 thesis 갭 리뷰 검증 + 계약 정합화 (2026-08-04)

대상: `docs/reports/edge_thesis_gap_review_20260804.md`
방식: 리포트의 각 주장을 원장·코드·config로 실측 대조. 이후 P0/P1 수정 및 라이브 재시작.

## 1. 갭 리뷰 판정

### 1.1 맞은 것 (실측 일치)

| 주장 | 실측 |
|---|---|
| `news_or_earnings_flag` 저장만, 후보 제외 미사용 | `us_swing_shadow_runner.py` 단 1곳(저장). eligible 조건은 가격·거래대금·변동폭·소스뿐 |
| earnings 차단이 US swing handoff에 없음 | 확인 |
| execution shadow budget 50,000원 | 확인 |
| `breadth_context_state=MISSING` | 확인 (diagnostic 50건 중 45건) |
| sector_map 비활성·KR 매핑 없음 | 확인 (US 486종목, KR `{}`) |
| profit_path_US AUC 0.638 / ECE 0.0096 / healthy / selected_n=0 / promotion False | 4개 전부 정확 |
| tail_capture는 PathB·hold_advisor에만 | 확인 |
| KR R1/R2/R3 후보 0·정산 0 | 도구 재실행 확인 |
| status artifact 시점 비동기 | 확인 |
| rank1_skip capacity 55건 +2.09% PF 1.51 | `slip=0.5` 시나리오 값과 정확히 일치 |
| KR earnings 21종목 | `kr_by_code` 21건 확인 |
| 30만원 capacity는 아직 주장 불가 | 기존 시뮬 계약이 `order_cap 50,000·slot 1`이라 정확한 지적 |

### 1.2 틀린 것

**① "authority `allowed_to_emit_orders=false` → 실매수 표현이 원장과 불일치"**

`runtime/us_swing_order_bridge.py:95-124`에 운영자 오버라이드 경로가 있다. `US_SWING_OPERATOR_MICRO_OVERRIDE_ACK`가 일치하고 blocker가 forward 4종뿐이면 런타임에서 `effective_mode=micro`, `allowed_to_emit_orders=True`, 슬롯3/일1로 승격된다.

```
logs/system/live_trading_20260803.log:7967
  22:35:12 [LIVE MICRO_PROBE BUY] FRMI 38@5.5225 | source=us_swing_5d | order_no=0030153071
  22:36:11 [주문 체결 반영] FRMI 38주
브로커 truth: FRMI 38주 avg 5.52
```
`state/us_swing_status.json`의 false는 오버라이드 적용 **전** base authority 스냅샷이다.

**② "5만원 shadow라 30만원 forward 표본이 아니다"** — 절반 틀림. `US_SWING_ORDER_MAX_KRW=300000`으로 실제 30만원이 집행된다. 5만원은 별개 가상 원장.

**③ "정산 2건 평균 −1.80%, PF 0.76 → 확대 근거 없음"** — 계약 변경 전후 혼합.

| 신호일 | 티커 | 소스 | net |
|---|---|---|---|
| 07-10 | SMCI | most_actives | −14.80% |
| 07-17 | NVTS | day_losers | **+11.21%** |

현재 계약은 `US_SWING_ALLOWED_SOURCES=day_losers`이고 SMCI는 후보에 들어오지 못한다. 현 계약 기준 정산은 1건(+11.21%).

**④ "sealed OOS 293세션·879행, +0.72%, PF 1.30, LCB −0.375%"** — rank1/top3 혼용.

```
rank1 tp12_sl25 : rows 293 | mean +1.402 | PF 1.367 | LCB -0.171   ← 현재 계약
top3  tp12_sl25 : rows 879 | mean +0.721 | PF 1.297 | LCB -0.375   ← 인용된 값
```

**⑤ "FRMI vs shadow active NVTS → 귀속 재조정 필요"** — 오류가 아니라 계약 차이. 08-03 FRMI의 `execution_shadow_reason=slot_occupied_pending:2026-07-29`(shadow는 슬롯1, 실주문은 슬롯3).

**⑥ "3,000건 replay KR actionable 0 → KR R2 미연결 근거"** — 근거 연결 오류(replay는 PathA/PathB 라우팅 축, KR R2는 kr_fallen 별도 레인). 결론 자체는 맞다.

### 1.3 갭 리뷰가 놓친 것

- **5만원 예산의 배제 편향 방향**: 07-24 WEX, 07-27 AXTI(rank1 day_losers **+20.90%**)가 `micro_budget_cannot_buy_one_share`로 배제. 주당 $35 이상이 원천 제외되어 승자 쪽으로 편향된 표본이 쌓였다.
- **`model_forward_diagnostic_only` 50건 / −6.33% / 승률 10%**: status에 있으나 미인용. rank1~5 + 소스필터 이전 혼합이라 현 계약 forward가 아니지만, thesis의 +6.94와 충돌하는 숫자이므로 배제 사유를 명시했어야 한다.

## 2. 수행한 수정

### 2.1 실행 계약 단일화 (신규 `runtime/us_swing_execution_contract.py`)

정책 파일은 sealed evidence의 `policy_sha256`과 묶여 있어 수정 불가(`historical_policy_hash_mismatch` 유발). 오버라이드 계약을 별도 모듈에서 합성해 shadow와 실주문이 같은 값을 읽게 했다. 실측:

```
오버라이드 ON  : 300,000원 / 슬롯3 / 일1건 / day_losers  → contract_id 711ab418d4a15072
오버라이드 OFF :  50,000원 / 슬롯1 / 일1건 / day_losers  → contract_id ac96f4d19d808124
```

- `annotate_execution_shadow`: 예산·다중슬롯·소스 화이트리스트를 계약에서 읽는다. 기존에는 직전 1건만 보던 슬롯 판정을 미청산 전체 집계로 일반화.
- `signals`에 `execution_shadow_contract_id` / `_max_open_slots` / `_allowed_sources` 컬럼 추가.
- `summarize_forward_evidence`: 현재 계약과 같은 행만 집계. 다른 계약 행은 `excluded_legacy_contract_matured`로 분리 보고.

### 2.2 status 오독 차단

`us_swing_status.json`에 `effective_authority`(오버라이드 적용 후)와 `execution_contract`를 함께 기록. `model_forward_diagnostic_only`에 `scope_note` + 소스별 분해(`candidate_source_diagnostic_only`) 추가.

### 2.3 정보성 하락 3-arm shadow (관찰 전용)

`news_or_earnings_flag`를 3-state(있음/없음/unknown)로 복원. 기존 `bool()` 캐스팅은 unknown을 "뉴스 없음"으로 접는 fail-open이었다. `current / exclude_flagged / unknown_abstain` 세 arm의 rank1을 `data/shadow/us_swing_news_arm_shadow.jsonl`에 기록. top_k로 자르기 전 전체 랭킹을 사용(상위 제외 시 top_k 밖 후보가 rank1로 올라오는 경우 포착). **선정에는 개입하지 않는다.**

### 2.4 breadth 복구 (신규 `tools/backfill_us_breadth_proxy.py`)

CSV가 2026-07-09에서 멈춰 매 세션 MISSING이었다. RSP/SPY만 있으면 상태가 결정되므로 그 둘을 백필(17행 추가, 08-03까지).

```
2026-07-31 → NARROW (narrow_excess -0.892)
2026-08-03 → NARROW (narrow_excess -0.448)
```

### 2.5 KR 게이트 리포트 대조 축

원장은 관측 전용이라 규칙 미통과 건도 가상 정산한다. 이 구분이 리포트에 없어 오독을 유발했다(002995 `pass_all=False`·R2 미통과인데 net +11.75로 SETTLED). 대조 축을 추가:

```
[대조] 원장 전체(규칙 무관, 관측 전용·실매수 아님) 정산 1건 | 평균 +11.75% | 그중 어떤 규칙도 통과 못한 건 1건
```

## 3. 30만원 계약 capacity 실측 (신규)

`tools/us_swing_capacity_counterfactual.py`를 30만원 계약으로 재실행. **one-slot·일봉 open proxy·historical**이며 forward가 아니다.

| 예산 | slip | n | mean | PF | 승률 | 미체결(예산부족) 세션 |
|---|---:|---:|---:|---:|---:|---:|
| 5만원 | 0.5 | 55 | +2.090 | 1.514 | 0.545 | 69 |
| 5만원 | 1.0 | 55 | **+0.155** | 1.031 | 0.473 | 63 |
| 30만원 | 0.5 | **71** | +1.920 | 1.477 | **0.592** | **3** |
| 30만원 | 1.0 | 71 | **+1.246** | 1.282 | 0.577 | 2 |

- 5만원은 예산 부족으로 세션의 상당수를 버렸다(63~69회 → 2~3회). 표본이 55→71로 회복.
- **슬리피지 내성이 30만원 쪽이 크게 강하다.** slip 1.0%에서 5만원은 +0.155로 붕괴, 30만원은 +1.246 유지.
- 평균은 5만원이 근소 우위지만 이는 저가·고변동주 편중의 결과이고, 승률은 30만원이 높다.

historical 기준으로 30만원 계약이 5만원보다 견고하다. forward 판정은 별개로 필요하다.

## 4. 라이브 재시작에서 드러난 결함

봇 종료 후 22분간 재기동이 되지 않았다. 원인은 코드 변경이 아니다.

```
broker_truth 스냅샷 stale(마지막 성공 18:28, TTL 180초)
  → 가디언 market gate KR/US 모두 BLOCK_START
  → bot_launch_allowed=False → 봇 기동 차단
```

`broker_truth_scheduler`는 `--preopen-min 20`이라 개장 20분 전부터만 갱신한다. 그 사이 시간대에는 TTL이 계속 만료되므로 **장 사이에 봇이 죽으면 스스로 복구하지 못한다.** 오늘은 수동 강제 갱신으로 해소했다. 구조적 결함이며 별도 과제.

## 5. 하지 않은 것

- **sector_map 활성화** — 고장이 아니라 의도적 비활성이다. `universe_manager.py:100-104`: 캡이 US 3/KR 2로 타이트한데 실제 체결은 Technology 54.5%라 값 공급 시 후보 구성이 급변하고, 후보 랭킹 변경은 역효과 이력이 있는 영역(screener 리랭킹 backfire, 2026-06-27). `SECTOR_MAP_ENABLED` + 파일명 이중 잠금이며 **운영자 승인 사항**이라 켜지 않았다.
- **KR earnings 수집기 신규 작성** — 불필요. `runtime/earnings_calendar.py`(DART 기반)가 이미 작동 중이며 08-04 갱신됨. KR은 사전 공시 제도가 없어 D-1 감지가 불가하고 당일 감지만 가능하다는 문서화된 한계가 있다.
- **슬롯3 capacity 시뮬** — `us_swing_capacity_counterfactual.py`가 one-slot 전용 도구라 예산 축만 측정했다.

## 6. 추가 검증 — "운인가" 3갈래 (같은 날 저녁)

### 6.1 월별 차이는 우연이 아니다 (핵심)

rank1 293건 전체는 평균 +1.402%, p=0.034로 겨우 유의하다. 그런데 **2026-03(22건, 평균 +16.33%)을 빼면 271건 평균 +0.19%, p=0.754**로 사라진다. 큰 양수 달은 14개월 중 3개뿐이다(2025-09 +6.14, 2026-01 +4.62, 2026-03 +16.33).

여기서 "운이냐"를 가르는 검정을 돌렸다. 건당 std 11.26%, 월평균 20.8건이면 월평균이 우연히 흔들릴 폭은 ±2.47%다.

```
월평균의 기대 표준편차(순수 우연)  2.474%
월평균의 실제 표준편차            5.259%   (2.13배)
ANOVA(월별 평균 동일):  F=5.620  p=0.00000
[2026-03 제외]         F=2.050  p=0.02072  (1.41배)
```

**월별 평균이 같다는 가설이 기각된다. 이상치를 빼도 유의하다.** 순수 운이라면 이 검정을 통과했어야 한다. 전체 평균의 유의성은 약하지만, 시기에 따라 엣지가 켜지고 꺼진다는 것은 강하게 유의하다.

다만 그 스위치는 **시장 국면이 아니다**:

```
2025-09  SPY +4.05%  일변동성 0.45  월중낙폭 -3.98%   조용한 상승장
2026-01  SPY +1.29%  일변동성 0.65  월중낙폭 -2.58%   완만한 상승
2026-03  SPY -5.25%  일변동성 1.15  월중낙폭 -7.93%   급락장
```
조용한 상승장과 급락장이 같은 칸에 들어간다. "약세 국면이 사냥철"은 2026-03만 설명한다.

### 6.2 확률 역지표 — 기각

월별 집계에서 `probability` 평균과 실현수익이 r=−0.660(p=0.010)으로 보여 사전 지표 가능성을 검토했다. 자기 신호를 제외한 직전 N일 이동평균(no-lookahead)으로 재검증:

```
MA10: 저확신 +1.608% vs 고확신 +1.252%   p=0.790
MA20: 저확신 +1.035% vs 고확신 +1.842%   p=0.553  (역방향)
MA40: 저확신 +3.676% vs 고확신 -0.219%   p=0.004
사분위(MA20): Q1최저 +0.62% / Q4최고 +4.01%  (역지표와 반대)
```

유일하게 유의한 MA40을 분해하니 **2026-03의 22건이 통째로 저확신 구간**에 있었다. 그 달 제외 시 저확신 +1.256%(n=115) vs 고확신 −0.219%(n=136), **p=0.242**. 월별 r=−0.660도 같은 이상치가 만든 것이다. 윈도우 3개 중 1개 유의는 다중검정의 전형. **가설 폐기.**

### 6.3 TP12는 갭 건에만 "하한"이다

```
TP12 청산 건이 5일 보유였다면
  TP     n=72  +11.81% -> +13.37%  (+1.55%p 포기, 더 갔을 확률 50%)
  TP_GAP n=21  +20.78% -> +22.66%  (+1.88%p 포기, 57%)

계약 비교 (rank1 n=293)
  TP12/SL25(현행)  +1.402%  PF 1.367  승률 51.2%  std 11.26
  TP12/무손절       +1.470%  PF 1.392  승률 51.2%  std 11.13
  무TP/5일종가       +1.987%  PF 1.509  승률 48.1%  std 13.80
  차이(무TP-TP12) +0.585%p,  p=0.175
```

thesis의 "TP12는 상한이 아니라 하한"은 **갭 건에만 맞다** — TP_GAP 21건 평균 +20.78%, 12% 초과분 +8.78%p(최고 +49.33%). 반면 장중 도달 72건(25%)에서는 +12%가 명백한 상한이고 평균 1.55%p를 남긴다.

무TP가 평균·PF 모두 우위지만 p=0.175로 유의하지 않고 변동성이 커지며 승률은 떨어진다. **지금 데이터로 TP12를 걷어낼 근거는 없다.** 문서의 "하한" 서술은 갭 건 한정으로 수정이 필요하다. 유망한 절충은 TP 도달 후 트레일링(tail_capture) counterfactual이다.

### 6.4 소수 건 의존

```
상위  1건 제외: 평균 +1.238%  (합 기여 12.0%)
상위  5건 제외: 평균 +0.708%  (합 기여 50.3%)
상위 10건 제외: 평균 +0.412%  (합 기여 71.6%)
```

효과크기 d=0.125 → 검정력 80%/유의 5%로 확증하려면 **약 506건**이 필요하다. 일 1건이면 2년, 일 3건이어도 8개월이다. **8월말 30건은 엣지 확증이 아니라 체결·비용·귀속의 결함 부재 확인용이다.**

## 7. 검증

- `py_compile`: 신규·수정 5개 파일 PASS
- 관련 테스트(us_swing/swing/handoff/bridge/contract/authority): **202 passed**
- 한국어 인코딩 검사: 신규·수정 파일 이상 없음
- 재시작 후 effective-config 실측: `US_SWING_ORDER_MAX_KRW=300000`, `ALLOWED_SOURCES=day_losers`, 오버라이드 ACK 존재, `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true`
- 봇 PID 31468(21:57:50), API health 11/11 OK, 가디언 게이트 `ALLOW_START`
