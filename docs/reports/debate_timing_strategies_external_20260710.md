# 토론 판정: 외부 진입/청산 타이밍 후보 5개 옥석 판별 (2026-07-10)

## 명제
"외부 리서치가 추린 진입/청산 타이밍 후보(진입 ①ATR buy_zone ②종목추세게이트 ③지수200일선 / 청산 A Chandelier B MFE breakeven C 다일barrier) 중 우리 실측 위에서 검증에 올릴 가치가 있는 것을 가려낸다."

## 판정: **부분 성립 — 신규 축 1개(①ATR buy_zone, 측정 shadow), 기존기능 캘리브레이션 1개(B), 나머지 4개 기각**

양측이 핵심 사실에서 수렴: **B는 새 후보가 아니라 이미 라이브 enforce 중.** 리서치의 "기각목록에 없는 최강 신후보"가 실은 기존 기능이었다.

## 사회자 직접 검증

### V1. B(MFE breakeven) = 이미 라이브 enforce (확정)
- 배선: `pathb_runtime.py:6609` `PATHB_MFE_BREAKEVEN_ENABLED` default **True**, trigger 2.5%, `:3736 exit_signal=mfe_signal`(실매도). exit_lifecycle 등재.
- 라이브 발화 **2건 전부 net 음수**: NVTS(5/26) −0.9, AAOI(6/8) −0.1.
- → "검증에 올릴 신규 축"이 아님. **기존 기능의 캘리브레이션 대상**(trigger 2.5%, 6/23 observed_peak 배관 가동 후 실발동 2건뿐이라 표본 얇음).

### V2. B 러너 학살 위험 = 실재하나 상한 (순서 불명)
- TARGET 승자 26건 중 **17건이 무장(MFE≥2.5%)+되돌림(MAE≤−2%)**, net 합 +98.6 — 우리 유일 수익원의 오른쪽 꼬리(MRVL +17.2·WDC +7.3·001780 +7.7 등)가 위험 구간.
- ★단 mfe_pct/mae_pct는 스칼라라 **무장이 되돌림보다 먼저인지 순서 불명** = 17건은 kill 개수가 아니라 상한. 실제 kill은 분봉 replay로만. 이미 라이브라 **전진 실적이 최우선 판독**(2/2 음수는 loss-floor 구제이지 runner-kill 아닐 수 있음).

### V3. ①ATR buy_zone = zone_pos와 다른 knob, 미구현 (확정)
- `claude_price_plan.py`: 존 폭은 Claude 입력값 그대로, **ATR 배관 "신규 데이터 배관 없음"** 명시(존폭 스케일 미구현).
- zone_pos(존 내 위치=상단 추격 회피, c36eebd 배포)와 다름 — ATR-zone은 존 **폭 스케일**. 재포장 아님.
- **유일한 미구현 새 축.** 단 CON 지적 유효: fill 28~36% 베이스에서 존 확대가 미체결↑ 위험 → **측정 먼저**(net 상방 근거 없이 fill만 깎을 하방).

## 후보별 판정표

| 후보 | 판정 | 근거 |
|---|---|---|
| **① ATR buy_zone** | **검증 큐 (측정 shadow)** | 유일 미구현 새 축. fill 재형성 가설. ★fill 여는지/깎는지 동시측정 필수(총 체결수 증감이 판정 절반) |
| **B MFE breakeven** | **캘리브레이션 (기존 live)** | 이미 enforce. trigger 2.5%→? A/B는 내부 파라미터 스윕. winner-retracement replay로 러너킬 순비용 확인 |
| **C 다일 barrier** | 기각 (net 무관) | 가장 느린 승자=가장 큰 승자(MSFT+9.1·RBRK+7.1, 3.95d). 짧으면 팻테일 절단. 회수=회전개선≠net. multi-day 철학 긴장 |
| **A Chandelier** | 기각 (재포장) | 리서처 자인 ATR peak-trail. give확장 Δ≤0 기각. ATR우위 in-repo 증거 0 |
| **③ 지수200일선** | 기각 (중복·반증불가) | red-tape(live enforce) 중복. 강세단일국면 always-ON |
| **② 종목추세게이트** | 기각 (fill감소·중복) | 진입7경로 무차별. 강세과보수 open-thread 중복 |

## 결론 (검증 큐)

1. **①ATR buy_zone shadow** — 체결별 진입할인폭 ATR-정규화 로그 + **fill rate 동시 측정**. 유일한 진짜 신규 실험. net 상방 미확인이라 측정만.
2. **B trigger 캘리브레이션** — 이미 라이브이므로 신규 아님. winner-retracement 분봉 replay(TARGET 17건 중 실제 순서상 kill 수) + 전진 실적 누적. trigger 2.5%→1.5% 검토는 replay 통과 후.
3. 나머지 4개 기각 — 3개는 이미 배포/기각 재포장, ②는 게이트.

## 정직한 총평
"외부 최신 전략"의 실체 대부분이 **우리가 이미 배포(B)했거나 실측 기각(A·③)한 메커니즘의 재명명**이었다. 이는 결함이 아니라 확증 — 우리 execution 스택이 이미 실무 표준(ATR trailing·breakeven·regime filter)을 구현/검증했다는 뜻. 진짜 미개척은 **①존 폭의 변동성 적응** 하나이며, 그마저 fill 하방 때문에 측정부터다. enforce는 전부 검증 통과+운영자 승인 후.

— 사회자 (2026-07-10, 검증 쿼리 세션 기록)
