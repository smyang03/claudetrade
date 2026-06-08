# Ops Simulation Follow-up Improvement Direction

- 작성일: 2026-06-08
- 목적: 추가 시뮬레이션 검토 결과를 live 개선 방향으로 정리
- 범위: KR wait 재평가 후보, US one-share-over-budget 수정 영향, price coverage 신뢰도
- live 오염 여부: 분석은 기존 DB/price file read-only, 결과는 `.runtime` 산출물 기준

## 결론 요약

1. US PathB 기존 수익 경로는 유지한다.
   - exit replay에서 실제 live exit mix 평균이 `+0.5623%`로 단순 대안 최고 `+0.4182%`보다 높았다.
   - profit ladder, pre-close, hold advisor, target/stop은 이번 개선 대상에서 제외한다.

2. US one-share-over-budget 수정은 live 개선으로 유지한다.
   - 기존 `HIGH_PRICE_BUDGET_BLOCK`/`ORDER_SIZE_TOO_SMALL_GATE` 37개 이벤트가 수정 후 `post_fix_waiting_size_gate`로 재분류된다.
   - AVGO/ARM/MSFT/TSLA/GOOGL 등 450k~700k KRW 구간 후보가 등록 후 sizing에서 구조적으로 막히던 문제를 줄인다.
   - 이 수정은 주문 강제가 아니라 broker truth, cash, position cap, daily cap, risk gate를 계속 통과해야 한다.

3. KR wait는 broad live 적용 금지, 제한형 재평가 큐만 검토한다.
   - broad wait 전체: count `3208`, avg `-0.4479%`, median `-0.3759%`, win rate `38.06%`.
   - filtered counterfactual wait 25개 baseline: avg `+9.5401%`, median `+8.3845%`, p10 `+4.1228%`, worst `+2.7140%`.
   - 표본은 25개 후보, 14개 ticker-date, 11개 ticker라 아직 작다.

4. KR buy-zone padding은 전역 적용하지 않는다.
   - wait 후보 25개에서는 buy_zone_padding 변화가 성과를 거의 바꾸지 않았다.
   - broader KR counterfactual에서는 3% padding이 좋아 보였지만 놓친 후보 편향이 크다.
   - 따라서 KR wait 큐 내부에서도 padding 자체보다 live route/gate 재평가와 stop/target guard가 우선이다.

5. Price coverage는 시뮬레이션 신뢰도 선행조건이다.
   - 7,920개 시뮬레이션 row 중 complete `6960`, partial `960`.
   - unique partial window는 20개다.
   - profitability 판단에는 complete row를 우선 사용하고 partial은 data-quality/audit 대상으로 분리한다.

## KR Wait 분포 재검토

### 전체 wait 후보

- 전체 broad wait: avg `-0.4479%`, median `-0.3759%`, worst `-20.1327%`.
- live-visible group 중 그나마 좋은 축:
  - `wait_30m / analyst_reinvoke / FRESH/partial`: count `26`, avg `+1.5223%`, median `+1.1082%`, win rate `61.54%`, worst `-3.7479%`.
  - `wait_60m / analyst_reinvoke / LIVE/confirmed`: count `34`, avg `+0.4681%`, median `+0.4563%`, win rate `61.76%`, worst `-3.7770%`.

판단: broad wait는 손실 평균이라 live 자동 진입 금지. analyst_reinvoke 계열과 신선도/증거 조건이 맞는 후보만 재평가 후보로 분리한다.

### Filtered counterfactual wait 25개

Baseline 파라미터 기준:

| metric | value |
| --- | ---: |
| count | 25 |
| avg | 9.5401 |
| median | 8.3845 |
| trim10_avg | 9.2370 |
| p10 | 4.1228 |
| p25 | 5.2525 |
| p75 | 13.1455 |
| p90 | 16.7933 |
| best | 19.2905 |
| worst | 2.7140 |
| win_rate | 100.00 |

Concentration:

| item | value |
| --- | ---: |
| unique ticker-date | 14 |
| unique tickers | 11 |
| top concentration | 2026-06-02 357880 4개, 2026-05-28 198940 6개 |

판단: 중앙값과 p10도 양호하므로 신호 자체는 가치가 있다. 다만 표본이 작고 일부 종목/일자에 집중되어 있어 live 전환은 제한형 큐가 맞다.

### Robust 후보

모든 스윕에서 worst가 양수이고 baseline도 양수인 후보는 12개다.

주요 예:

| date | ticker | path | baseline | worst_sweep | best | drawdown60 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 2026-06-02 | 357880 | wait_60m | 19.2905 | 0.2217 | 19.2905 | -0.3326 |
| 2026-06-02 | 357880 | wait_30m | 19.1584 | 0.2215 | 19.1584 | -0.4430 |
| 2026-06-02 | 357880 | wait_60m | 15.2034 | 15.2034 | 15.2034 | -1.7131 |
| 2026-06-02 | 242040 | wait_30m | 9.8784 | 5.9271 | 9.8784 | -1.9757 |
| 2026-05-28 | 001740 | wait_30m | 9.2511 | 7.0485 | 9.2511 | -0.3524 |
| 2026-05-29 | 457370 | wait_30m | 9.1225 | 6.1685 | 9.1225 | -0.0869 |
| 2026-05-27 | 261780 | wait_30m | 8.9362 | 6.6667 | 8.9362 | -0.1418 |

판단: robust 후보는 live 후보군으로 승격할 수 있는 1차 조건 후보지만, 바로 주문이 아니라 기존 route/gate 재평가 대상이다.

## Live 개선 방향

### KR 제한형 재평가 큐

목표:
- 놓친 고확신 KR 후보를 장중에 재평가하되 기존 live 운영/수익 경로를 오염시키지 않는다.

진입 조건:
- market = KR
- counterfactual path = `wait_30m` 또는 `wait_60m`
- freshness/evidence가 confirmed 또는 partial 이상
- route_source는 우선 `analyst_reinvoke` 계열
- observed drawdown 또는 live drawdown이 과도하지 않음
- 기존 `RouteDecision`, risk, affordability, broker truth, daily cap, max positions를 그대로 통과

보호 한도:
- 자동 주문 연결 금지로 시작하거나, live 적용 시 별도 quota 필요
- 일일 최대 1~2개 후보
- 동일 ticker 당 일일 1회
- 기존 PathB/고확신 후보 슬롯을 침범하지 않도록 낮은 priority 또는 별도 reserved quota
- 손실/미체결/ORDER_UNKNOWN 발생 시 당일 큐 중단

관찰 지표:
- queued_count
- re_evaluated_count
- route_pass_count
- route_block_reason
- order_sent_count
- fill_count
- pnl_30m/60m/eod
- 기존 PathB 후보가 큐 때문에 밀렸는지 여부

Live 전환 조건:
- shadow/report 기준 최소 30개 이상 재평가 후보 축적
- median > 0
- p25 >= 0 또는 worst drawdown 제한 가능
- 기존 PathB/PathA entry miss 증가 없음
- ORDER_UNKNOWN/미체결 증가 없음

### US one-share-over-budget 모니터링

목표:
- 이미 수정된 sizing bug가 live에서 의도대로 작동하는지 확인한다.

관찰 조건:
- 450k~700k KRW 후보가 `HIGH_PRICE_BUDGET_BLOCK`로 남는지 확인
- expected class는 permanent high-price block이 아니라 temporary size/cap gate
- 실제 주문은 broker truth/cash/risk gate 통과 시에만 발생해야 한다.

금지:
- global fixed order budget 증액
- US PathB buy-zone 전역 확장
- profit ladder/pre-close/target/stop 변경

### Price Coverage 개선

목표:
- profitability simulation이 partial tape에 끌리지 않도록 한다.

개선 방향:
- simulation report는 complete와 partial을 기본 분리한다.
- partial window 20개는 data-quality audit backlog로 남긴다.
- future simulation에서는 complete-only summary를 기본값으로 사용한다.
- 장기 보유/다일 포지션은 entry-to-exit 전체 tape 수집 여부를 우선 점검한다.

## 최종 판단

- 즉시 live에 유지할 개선: US one-share-over-budget sizing fix.
- live 정책 변경 금지: US exit, US buy-zone, global budget, KR broad wait.
- 다음 구현 후보: KR wait restricted re-evaluation queue/report.
- 선행 조건: 재평가 후보 30개 이상 추가 축적, complete-only 성과 재검증, 기존 수익 후보 슬롯 침범 방지 설계.
