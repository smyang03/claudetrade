# Path A 침묵의 뿌리 — 발견 지연 → 개장 분봉 부재 → 전략 신호 0 (2026-07-13)

**결론: 매수가 없는 근본 원인은 게이트도 출구도 아니다. 즉시매수 엔진(Path A)이 682번의 기회에서 단 한 번도 신호를 내지 못했고, 그 원인은 "후보를 늦게 발견해서 개장 구간 분봉이 없다"는 것이다.**

## 1. Path A는 막혀 있지 않다 — 그냥 발화하지 않는다

`TRADE_READY_NO_SUBMIT` 682건(NO_SIGNAL) 실측:

| signal_flag | True |
|---|---|
| `plan_a_signal_allowed` | **682/682** (허용됨) |
| `momentum` / `gap_pullback` / `mean_reversion` / `volume_surge` / `opening_range_pullback` | **0**/682 |
| `strategy_used` | 682건 전부 **빈값** |

- `cash_krw > 0` (자금 있음), `route: PlanA.buy` (라우팅 정상), `final_action: BUY_READY`.
- 전략 배정은 다양하다: ORP 290 / gap_pullback 156 / momentum 125 / mean_reversion 99 / volatility_breakout 9.
- **즉 허용·자금·라우팅·전략배정 전부 정상인데 신호가 0이다.**

## 2. 왜 0인가 — NO_SIGNAL 682건의 사유

| 사유 | 건수 | 의미 |
|---|---|---|
| `orp_entry_window_expired` | **438 (64%)** | 개장 후 진입창(US 15+60분) 만료 |
| `orp_not_formed` (range=**0.00%**) | **129 (19%)** | 코드상 `opening_window_rows_missing` = **개장 구간 분봉 없음** |
| 조건 미달 (range/pullback/volume) | 109 (16%) | 데이터 있고 조건 안 맞음 |

**실제로 전략 조건을 평가라도 해본 건 16%뿐이다.**

## 3. ★뿌리 — 후보 발견 지연

`post_open_features.data_quality` (2026-06-25~):

| | US (n=52,068) | KR (n=19,589) |
|---|---|---|
| **`first_observed`** (첫 관측 = 개장구간 분봉 없음) | **52%** | 38% |
| `minute_complete` | 43% | 49% |
| **`minute_missing`** (실제 수집 실패) | **0%** | 6% |
| **→ OR(오프닝 레인지) 없음** | **57%** | **55%** |

- **수집 배관은 정상이다** (`minute_missing` US 0% / KR 6%).
- 분봉이 **있는** 종목은 데이터가 충분하다 (US 중앙 **156개** 바, OR 형성 불가 9.6%).
- 문제는 **분봉이 없는 종목이 절반**이고, 그 이유가 `first_observed` — **그 세션에 그 종목을 처음 본 시점이라 개장 구간 시계열이 애초에 없다.**

`_or_formed`는 `post_open_features.opening_range_high/low`에서 오고(`trading_bot.py:14777`),
`data_quality == "minute_missing"`이면 스킵된다. OR이 없으면 ORP는 `orp_not_formed`(range=0.00%)를 반환한다.

## 4. 백필은 있는데 전략 경로에 안 붙어 있다

`_backfill_post_open_minutes`(`trading_bot.py:14815`)가 존재하고 `POST_OPEN_MINUTE_BACKFILL_ENABLED=true`다.
그런데 **호출부가 2곳뿐**이다:
- `trading_bot.py:7237` — `reason="evidence_pack_sparse"`
- `trading_bot.py:31511` — `reason="judge_input_sparse"`

**둘 다 judge/evidence 경로다. Path A 전략 신호 계산 경로에서는 호출하지 않는다.**
최근 세션 로그(7/9·7/10·7/13) 백필 호출 **0건**.

## 5. 두 경로가 하나의 뿌리에서 같이 죽는다

- **Path A (즉시매수)**: 발견 지연 → 개장 분봉 없음 → OR 미형성 → **전략 신호 0** → 주문 0.
- **Path B (눌림대기)**: 설계상 눌림 대기 전용(전체 이력 579/580이 `PULLBACK_WAIT`, `BUY_READY`는 1건). 이미 급등한 뒤 발견하니 "눌림을 기다리는" 플랜만 나오고, 눌림이 안 오면 만료·취소.

**같은 병이다: 늦게 본다.** 2026-07-13 KR 실측이 이를 그대로 보여준다 — 사후 승자 4종목을 **우리가 처음 본 시점에 이미 +14.80~+18.93%** 급등 상태였다.

## 6. 이건 게이트 완화가 아니다 (중요)

앞서 검증한 반사실은 여전히 유효하다:
- RR이 **거부한** 플랜을 살렸다면: US −2.05% / KR −1.82%
- **취소된** 플랜을 즉시매수했다면: US −0.117%(A1 제외) / KR −2.30%
→ **게이트를 푸는 것은 여전히 근거가 없다.**

그러나 그것들은 전부 **Path B 플랜**에 대한 것이다.
**Path A는 발화가 0이라 수익성을 검증한 적조차 없다.** 우리는 Path A가 돈을 버는지 **모른다**.

전략이 "평가된 뒤 거부"하는 것과 **"평가 자체를 못 하는"** 것은 다르다. 후자를 고치는 것은 게이트 완화가 아니라 **계측 복구**다.

## 7. 처방 (수익 방향, 우선순위)

### P1 — 분봉 백필을 Path A 전략 경로에 연결
후보 발견 시점에 개장 구간 분봉을 소급 확보해 OR을 형성시킨다.
→ **전략이 최소한 "평가라도" 되게 만든다.** 지금은 평가 자체가 불가능하다.
- 기존 함수(`_backfill_post_open_minutes`)를 재사용하면 되고, TTL 가드가 이미 있어 KIS 부하는 제한된다.
- 이건 **매수를 늘리는 조치가 아니라, 전략이 판단할 입력을 주는 조치**다. 신호가 나도 기존 게이트(RR·confidence·affordability)는 그대로 작동한다.

### P2 — 발견 지연 자체를 측정·축소
스크리너가 종목을 개장 구간(US 15분 / KR 10분) 안에 잡는 비율을 KPI로 세운다.
※ 스크리너 **랭킹**을 손대는 것이 아니다(그건 기각된 축). **타이밍**의 문제다.

### P3 — 진입 창 재검토
발견이 늦으면 진입창(US 75분)이 이미 닫혀 있다(만료 64%). 발견 지연을 줄인 뒤 창을 재평가한다.

### 유지
- 게이트(RR·confidence)와 취소 로직은 **그대로 둔다** — 반사실이 손실을 확인했다.
- 조기익절(출구)은 별도 축으로 유효하다([[target-calibration-lever-20260711]]). 단 **포지션이 있어야** 작동하므로 P1 이후에 의미를 갖는다.

관련: [[entry-pipeline-collapse-rr-gate-20260713]], [[target-calibration-lever-20260711]], [[goal-profit-first-mindset]]
