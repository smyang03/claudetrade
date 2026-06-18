# 후보군 선정 품질 — 심층 후속 분석 리포트

- 생성시각: 2026-06-16
- 기반 리포트: `docs/reports/candidate_selection_quality_20260616_122910.md` (원본)
- 방식: 운영 DB 읽기 전용(`candidate_audit.db`, `decisions.db`, `ticker_selection_log.db`). 코드/config/brain/broker 미변경.
- 목적: 원본이 "후속 보강 후보"로 남긴 (1) missed-winner 좁은 포켓, (2) selection→실현 변환손실, (3) "시스템이 잘 골랐다" 판정의 측정 편향, (4) 보유기간 효과를 정량 분해.

---

## 0. 한 줄 결론

"후보군을 잘 골랐다"는 **절반만 맞다.** 시스템이 본 지표(forward / MFE)는 *후보가 준 기회*를 쟀고, 운영자가 원하는 *시스템이 챙긴 돈*은 못 쟀다. 시장별로:

- **US**: trade_ready로 좁힌 구간은 **1일 +1.17%**로 단기 상승주 선별 능력 있음(합격, 단 좁힌 구간 한정).
- **KR**: 어느 보유기간·어느 미선정 포켓도 음수. selection 순위 품질이 약하고, 더 큰 누수는 **capture/청산**.
- **공통**: 보유를 늘릴수록 대부분 **더 나빠진다**(MAE 복리). "며칠 보유가 낫다"는 데이터로 기각.

---

## 1. Missed-winner 분해 — "좁은 보강 포켓이 있나"

forward-1d(1440분) 라벨, (session_date,market,ticker) latest dedup 기준.

### 1.1 코호트 baseline

| 시장 | 코호트 | n | win% | avgRet | avgMFE | MFE≥5% |
|---|---|---|---|---|---|---|
| KR | selected | 264 | 37.9 | **-2.21** | 5.14 | 43.9 |
| KR | in_prompt_not_selected | 321 | 37.4 | -1.61 | 5.30 | 43.0 |
| KR | not_in_prompt | 494 | 36.6 | -1.44 | 5.59 | 40.3 |
| US | selected | 541 | 45.5 | **+0.33** | 3.92 | 27.5 |
| US | in_prompt_not_selected | 392 | 51.5 | -0.27 | 2.96 | 24.2 |
| US | not_in_prompt | 935 | 48.1 | -0.22 | 3.20 | 25.8 |

- KR은 selected(−2.21)가 미선정(−1.44)보다 **더 나쁘다** → "고른 게 안 고른 것보다 나쁨". KR 문제는 확대가 아니라 순위/변환.
- US selected(+0.33)가 미선정보다 좋다 → US selection 순위는 의미 있음.

### 1.2 미선정군 bucket 분해 (n≥25)

KR 미선정 (bucket: n / win% / avgRet / avgMFE / MFE≥5%):
- liquidity_leader 110 / 43.6 / **-0.36** / 3.80 / 34.5
- near_breakout 118 / 42.4 / -0.94 / 4.30 / 38.1
- unclassified 168 / 37.5 / -1.06 / 4.72 / 38.1
- volume_surge 285 / 35.8 / -1.73 / 8.04 / 53.0
- pullback_watch 56 / 33.9 / -2.49 / 4.62 / 35.7
- blank 74 / 25.7 / -3.31 / 2.86 / 25.7

→ **KR은 미선정 어느 버킷도 음수.** 좁히든 넓히든 KR 후보 확대 근거 없음.

US 미선정:
- **blank 176 / 67.6 / +2.38 / 4.60 / 38.1**  ← selected(+0.33) 능가
- liquidity_leader 170 / 48.8 / 0.00 / 3.93 / 35.3
- unclassified 181 / 51.9 / -0.09 / 3.32 / 28.7
- near_breakout 623 / 46.9 / -0.58 / 2.58 / 18.8
- pullback_watch 139 / 36.0 / -2.47 / 2.38 / 22.3

### 1.3 US blank 포켓 드릴다운 (promote 가능성 검증)

- n=176, win 67.6%, avg +2.38%.
- 147/176이 `not_in_prompt`(프롬프트에 들어가지도 않음), avg +2.63%.
- `recommended_strategy` 전부 없음(noStrat), primary_bucket 없음.
- **단 11개 세션에 편중**, 129개 distinct ticker.

→ **판정: promote 대상 아님.** robust한 재현 포켓이 아니라 소수 강세일의 unscreened 잔여물 가능성이 높다. 게이트 완화 근거로 부적합. 대신 **"왜 이 종목들이 스크리너/프롬프트에서 빠졌나" origin 조사**가 올바른 후속.

### 1.4 미선정군 score 밴드 (trainer_prompt_score, n≥25)

- KR: <50 −0.85 / 50-60 −0.66 / 60-70 −2.54 / 70-80 −1.47 → 전부 음수.
- US: <50 −0.70 / 50-60 −0.02 / 60-70 −0.41 / none +1.69(n=62) → 50-60만 본전 근처.

→ score 기준으로도 KR 확대 포켓 없음. US도 미세.

---

## 2. Selection → 실현 변환손실 (capture gap)

`v2_learning_performance`(실현 원장, closed=1):

| 시장 | n | win% | gross | net* | fee* | avgMFE | capture(pnl/mfe) |
|---|---|---|---|---|---|---|---|
| KR | 47 | 27.7 | **-1.00** | -1.58 | 0.21 | 2.93 | **0.01** |
| US | 190 | 43.7 | +0.44 | -0.25 | 0.50 | 3.01 | -0.64 |

(*net/fee는 KR 3건·US 33건만 채워짐 — 아래 §5 데이터 공백)

- **KR: 평균 MFE +2.93%를 만들고도 실현 −1.00%. capture ratio 0.01 = 우호적 변동을 사실상 0% 회수.**
- ticker_selection_log forward가 양수(+0.68/+2.54%)인데 실현이 음수인 괴리의 정체 = capture/청산.
- KR 전략별 capture: momentum 0.17, gap_pullback 0.00, opening_range_pullback −0.16 → 전부 회수 실패.
- MFE≥2% 건 중 실현≤0(러너 반납): KR 33%, US 33% (단 표본 각 6건, 매우 thin).

→ KR 문제는 selection이 아니라 **청산/capture**라는 원본 결론이 실현 원장에서 재확인.

---

## 3. "시스템이 잘 골랐다" 판정의 측정 편향

근거 코드: `ticker_selection_db.py:737 _calc_forward_return` — `base_close = 선정일 종가` → N일 뒤 종가.

시스템의 selection 품질 지표(forward_1d/3d/5d, max_runup/MFE)가 구조적으로 좋게 나오는 이유:

1. **기준가 = 선정일 종가, 실제 진입가 아님.** 봇은 장중 추격 진입 → 출발선부터 불리.
2. **"N일 버티기" 반사실 — 봇 실제 청산 무시.** loss_cap/stop/weak_mfe가 장중에 끊어 forward 러너를 실현 못 함. (KR capture 0.01의 직접 원인)
3. **수수료 없음.** forward는 gross 종가-종가, 실현 net은 왕복 ~0.5% 차감.
4. **MFE/runup = "움직였나"지 "먹었나"가 아님.** +5% 찍고 −2% 마감해도 "큰 MFE 감지 성공"으로 집계.
5. **측정 모집단 ≠ 실행 모집단.** KR trade_ready selection log 130건에 forward, 실제 거래는 20건.

→ 거짓이 아니라 **다른 축을 측정.** "선정일 종가에서 깨끗하게 N일, 수수료 없이, 후보 전체"의 성과 vs 실제 "추격 진입 + 이른 청산 + 수수료 + 일부 실행".

---

## 4. 보유기간 효과 (1d/3d/5d) — "며칠 보유가 낫나"

같은 모집단(셋 다 라벨된 행만) 비교:

| 출처 | 코호트 | n | 1일 | 3일 | 5일 | win% (1/3/5) |
|---|---|---|---|---|---|---|
| selection_log | US trade_ready | 421 | **+1.17** | +0.77 | +0.95 | 55/56/51 |
| selection_log | KR trade_ready | 130 | +0.68 | +2.54 | +1.52 | 39/38/32 |
| selection_log | KR 비선정 | 3807 | -2.31 | -5.87 | -8.73 | 34/25/25 |
| selection_log | US 비선정 | 4551 | +0.53 | +0.38 | +0.15 | 54/51/48 |
| decisions.db | KR 전체 | 21642 | -2.35 | -4.65 | -6.09 | 34/31/31 |
| decisions.db | US 전체 | 36047 | +0.32 | +0.27 | +0.84 | 51/49/51 |
| audit 반사실 | KR selected | 213 | -3.37 | -5.70 | - | - |
| audit 반사실 | US selected | 491 | +0.19 | -1.47 | - | - |

**판정: "며칠 보유가 낫다"는 일반적으로 기각.**

- 보유를 늘릴수록 대부분 **더 나빠진다**(MAE 복리). KR이 극단(3·5일 −6~9%).
- **최적은 오히려 1일.** US trade_ready 1일(+1.17) > 3·5일. US selected는 3일에 −1.47 음수 전환.
- 유일한 예외처럼 보이는 KR trade_ready 3일 +2.54%는 n=130·win% 38%로 **outlier-driven**, audit 반사실(−5.70)과 충돌 → 운영 근거로 부적합.

→ 방향: "보유 연장"이 아니라 **US 1일 edge를 진입가·수수료·청산에서 덜 깎이게 / KR은 보유 연장 금지**.

---

## 5. 잔여 리스크 / 데이터 공백 (정직 공개)

심층 드릴다운에서 부딪힌 커버리지 벽 — 그 자체가 발견:

- **실현 원장 net 필드 희소**: `pnl_pct_net`·`fee_pct_round_trip` KR 3건/US 33건, `mfe_pct` KR 12/US 11건만 채워짐. → capture gap의 정확한 크기·net 손익분기 처방을 표본으로 정밀화 불가. **net/fee/mfe backfill 또는 상시기록이 선행 과제.**
- **audit 행 entry_delay+pnl 동시충족 1건** → 진입지연별 누수는 audit 경로로 측정 불가. v2 원장 join 경로 필요.
- **counterfactual close 라벨은 2026-05-18 이후 중심**, 4~5월 초는 forward/selection_log로 보완.
- **US blank 포켓 11세션 편중** → 강세장 표본 위험.
- MFE≥2% 러너 반납 표본 각 6건 → 비율(33%) 신뢰도 낮음.

---

## 6. 다음 분석 후보 (이어서 할 것)

1. **US blank/not_in_prompt 포켓 origin 조사**: 왜 스크리너·프롬프트에서 빠졌나(11세션 편중 원인, 강세장 의존성 분리).
2. **실현 원장 net/fee/mfe backfill 가능성 점검**: v2_event_store에서 fill/close 이벤트로 net·fee·mfe 역산 가능한지 → capture gap 정밀화 선결.
3. **진입 타이밍 누수 정량화**: v2 원장 + entry_delay/entry_price_vs_first_ready_pct join으로 "선정→진입 사이 가격 미끄러짐"이 1일 edge를 얼마나 깎는지.
4. **selection 품질 지표 재정의 설계**(코드 변경 영역, 운영자 승인 필요): forward 기준가를 실제 진입가로, 종료를 봇 실제 청산 시점으로, net 수수료 차감. → 지표와 실현의 축 일치.
5. **KR capture 처방 검증**: 보유 연장 금지 + 빠른 익절(1일 edge 회수)이 KR 실현 net에 미치는 영향 시뮬(장중 데이터 품질 한계 인지).

---

## 8. 정밀 검증 (same-trade 분해, v2_event_store 실행 truth)

§5에서 "행동 전 검증 필요"라 한 항목을 v2_event_store의 실제 실행 이벤트 체인(CLAUDE_PRICE_HIT→FILLED→CLOSED, 238 closed)으로 재구성. **이 검증이 §6의 추천 일부를 수정/기각함.**

### 8.1 진입 슬리피지 — **누수 아님 (이전 추천 기각)**

의도 진입가(CLAUDE_PRICE_HIT.price) → 실제 체결가(FILLED.fill_price_native):

| 시장 | n | 평균 슬립% | 중앙 |
|---|---|---|---|
| KR | 25 | +0.110 | +0.121 |
| US | 173 | +0.027 | +0.032 |

→ **진입 미끄러짐은 0.03~0.11%로 미미.** §6의 "추격 진입 자제"는 **기각** — 실행에서 edge를 먹는 건 추격 진입이 아니다.

### 8.2 수수료 — US에선 실질적 (이전 추천 유지·강화)

실측 fee(round-trip, n: KR 3 / US 33): **US 0.500%**, KR 0.210%. US 실현 gross +0.46% 대비 **0.5% 수수료가 net을 본전 이하로 뒤집음**(net 채워진 33건 gross −0.12→net −0.25). → US의 지배적 friction은 슬리피지가 아니라 **수수료**. §6.2의 수수료 레버는 유지, 단 "최대 레버"는 아래 8.4로 한 단계 더 수정.

### 8.3 forward와 실현은 **disjoint 모집단**

FORWARD_MEASURED를 실제 closed 포지션과 (market,ticker,session_date)로 매칭 → **0건**. forward는 구조상 **체결되지 않은 trade_ready 후보**에 측정된다. 즉 "forward가 좋다"와 "실현"은 **서로 다른 종목군** 얘기 → forward를 selection/실행 품질 근거로 쓰면 안 된다는 §3을 한층 강하게 확정.

### 8.4 국면 의존성 — **US edge는 5월(강세장)빨, 6월 이미 음수 (가장 큰 수정)**

월별 실현 분해:

| 시장 | 2026-04 | 2026-05 | 2026-06 |
|---|---|---|---|
| US | +0.14 (win57, n21) | **+0.86 (win50, n103)** | **−0.09 (win32, n71)** |
| KR | −1.27 (win26, n23) | −0.69 (win33, n21) | −0.79 (win20, n5) |

→ **US "+0.46% 평균"은 5월 강세장(QQQ +11.76%)에 집중**, 6월(n=71)은 이미 음수·win 32%로 꺾임. **US의 1일 edge는 안정적 속성이 아니라 강세장 의존**일 가능성이 높다. KR은 매월 음수 → **국면 독립 구조적 손실**.

### 8.5 검증으로 바뀐 결론

- 이전: "US 1일 edge 있음 / 실행에서 뱉음(추격·수수료)". → **수정: 진입 슬립은 무관, 수수료만 실질, 그리고 US edge 자체가 5월빨이라 6월엔 사라짐.**
- 강화: KR은 어느 국면에도 손실 → 축소·관찰이 더 분명히 정당.
- 강화: forward는 미체결 후보 지표라 실행품질 근거 불가.

### 8.6 남은 미검증 (표본·데이터 한계)

- fee 실측 n=33(US)/3(KR), mfe_pct 23건 → capture·net 정밀화 여전히 부족.
- 6월 US 음수가 **국면 전환 신호인지 일시적 노이즈인지**는 7월 데이터로 재확인 필요(n=71은 의미 있으나 단월).
- KR slippage n=25로 0.11%도 표본 작음.

---

## 7. 작업 경계

- 이 리포트는 **분석만** 수행했다. gate, strategy, PathB profit ladder, broker truth, config/env, brain은 변경하지 않았다.
- §6의 1~3은 추가 read-only 분석으로 진행 가능. §6의 4~5는 코드/시뮬 변경 영역이라 운영자 승인 후 별도 작업.
