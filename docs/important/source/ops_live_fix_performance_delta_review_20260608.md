# Ops Live Fix Performance Delta Review

- 작성일: 2026-06-08
- 목적: 기존 DB 성과와 이번 live 반영 수정의 전/후 성과 차이, 수익 감소/개선/누락 여부 확인
- live 반영 수정: US PathB one-share-over-budget sizing fix
- live 미반영 항목: KR wait re-evaluation queue는 모니터링/리포트 전용

## 결론

이번 live 수정이 기존 수익 경로를 줄인다는 증거는 없다. 수정 범위는 US PathB 진입 sizing의 `HIGH_PRICE_BUDGET_BLOCK` 분류/재평가 경로이며, exit 정책, profit ladder, pre-close, hold advisor, target/stop은 변경하지 않았다.

기존 DB 기준으로는 오히려 high-price/size-gate 계열 후보가 수익성이 있는 그룹이었다. 실제 성과 테이블에 매칭된 high-price 관련 closed 6개는 평균 `+3.3163%`, median `+3.9473%`, win rate `83.33%`였다.

추가 개선 여지도 확인됐다. post-gate 가격테이프 기준 cancelled 3개 후보는 60분/EOD 모두 양수였고, 그중 실제 `HIGH_PRICE_BUDGET_BLOCK`로 취소된 `CLS`는 gate 이후 EOD `+2.7087%`였다.

## 기존 DB Actual Performance

`data/ml/decisions.db::v2_learning_performance` 기준 live filled/closed 성과:

| 구분 | count | avg pnl | median | best | worst | win rate |
|---|---:|---:|---:|---:|---:|---:|
| KR 전체 closed | 43 | -1.0546 | -1.7088 | 8.3744 | -9.8179 | 27.91% |
| US 전체 closed | 119 | +0.7495 | +0.0576 | 12.7610 | -9.2724 | 50.42% |
| US PathB claude_price closed | 114 | +0.7624 | +0.0959 | 12.7610 | -9.2724 | 50.88% |

US closed reason 중 주요 수익 경로:

| close reason | count | avg pnl | win rate |
|---|---:|---:|---:|
| `CLOSED_CLAUDE_PRICE_TARGET` | 9 | +5.5272 | 100.00% |
| `CLOSED_CLAUDE_SELL` | 6 | +3.9127 | 100.00% |
| `CLOSED_CLAUDE_PRICE_PRE_CLOSE` | 34 | +1.4424 | 55.88% |
| `CLOSED_PROFIT_LADDER` | 20 | +1.0972 | 65.00% |

판단: 이번 수정은 위 exit 경로를 바꾸지 않았으므로 기존 수익 경로 감소 요인은 확인되지 않았다.

## US High-price Fix 영향

기존 extended suite 기준:

| 항목 | 값 |
|---|---:|
| affected event count | 37 |
| unique path count | 16 |
| post-fix class | `post_fix_waiting_size_gate` 37건 |
| path status | CLOSED 13, CANCELLED 3 |

성과 테이블에 직접 매칭된 affected closed 6개:

| ticker | actual pnl | close reason |
|---|---:|---|
| MSFT | +9.6374 | `CLOSED_CLAUDE_PRICE_TARGET` |
| NBIS | +4.2972 | `CLOSED_CLAUDE_PRICE_TARGET` |
| ARM | +3.9854 | `CLOSED_CLAUDE_PRICE_TARGET` |
| AVGO | +3.9092 | `CLOSED_CLAUDE_PRICE_TARGET` |
| TSM | +0.1925 | `CLOSED_CLAUDE_PRICE_STOP` |
| MDB | -2.1240 | `CLOSED_LOSS_CAP` |

요약:

| subset | count | avg | median | best | worst | win rate |
|---|---:|---:|---:|---:|---:|---:|
| matched affected closed | 6 | +3.3163 | +3.9473 | +9.6374 | -2.1240 | 83.33% |

판단: 450k~700k 구간 후보를 영구 high-price block으로 죽이지 않는 방향은 기존 수익 후보를 줄이는 게 아니라, 수익성 있는 후보군의 재평가 기회를 보존하는 개선이다.

## Post-gate Price Replay

one-share-over-budget은 장초반 soft gate 중 즉시 매수하지 않는다. 따라서 block 시점이 아니라 `session_open + 60m` 이후 첫 가격을 기준으로 30m/60m/EOD replay를 계산했다.

전체 affected 16개:

| horizon | count | avg | median | best | worst | win rate |
|---|---:|---:|---:|---:|---:|---:|
| 30m | 16 | +0.2046 | +0.0275 | +1.7463 | -1.6889 | 50.00% |
| 60m | 16 | +0.5924 | +0.2844 | +3.4993 | -1.5974 | 62.50% |
| EOD | 16 | +0.6502 | +1.2099 | +5.5683 | -4.0859 | 62.50% |
| MFE to EOD | 16 | +2.3457 | +1.8119 | +7.7384 | +0.0608 | 100.00% |
| MAE to EOD | 16 | -1.2225 | -0.5140 | 0.0000 | -4.6427 | 0.00% |

cancelled 3개:

| ticker | cancel reason | post-gate 60m | post-gate EOD | 판단 |
|---|---|---:|---:|---|
| CLS | `HIGH_PRICE_BUDGET_BLOCK` | +1.9250 | +2.7087 | 놓친 수익 후보 |
| MSFT | `ALREADY_HOLDING` | +0.9290 | +1.9057 | 추가 주문 대상 아님 |
| RDDT | `operator_cancelled_unfilled_limit_order_broker_confirmed` | +1.5361 | +5.5683 | 체결/운영성 재검토 후보 |

cancelled 3개 요약:

| horizon | count | avg | median | best | worst | win rate |
|---|---:|---:|---:|---:|---:|---:|
| 60m | 3 | +1.4634 | +1.5361 | +1.9250 | +0.9290 | 100.00% |
| EOD | 3 | +3.3943 | +2.7087 | +5.5683 | +1.9057 | 100.00% |

## 수익 감소 여부

확인 결과:

- US exit replay에서 actual exit mix 평균 `+0.5623%`가 대안 최고 `+0.4182%`보다 높았다. 이번 수정은 exit 정책을 건드리지 않는다.
- high-price affected closed 후보는 실제 matched 기준 평균 `+3.3163%`로 양수다.
- post-gate replay도 전체 16개 기준 60m/EOD가 양수다.
- 기존 fixed budget, daily cap, max positions, broker truth, cash/risk gate는 유지된다.

따라서 현재 확인 범위에서는 기존 수익이 줄어드는 증거가 없다.

## 개선된 점

- 450k~700k KRW 구간 후보가 registration에서는 허용되지만 sizing/safety 단계에서 permanent high-price block으로 오인되던 문제를 줄였다.
- 장초반 soft gate 중에는 계속 기다리고, gate 해제 후에도 broker truth/cash/risk를 통과해야만 주문된다.
- CLS처럼 실제로 high-price block으로 취소됐고 이후 가격이 양수였던 후보를 놓칠 가능성을 줄인다.

## 아직 놓칠 수 있는 점

- STRL/LITE급 700k 초과 후보는 여전히 정상 차단이다. 이번 fix 대상이 아니다.
- 추가 진입 후보가 생기면 cash/position quota를 사용할 수 있으므로, 기존 PathB 고수익 후보를 밀어내는지 live 관찰이 필요하다.
- post-gate replay는 실제 target/stop/profit ladder 체결을 완전히 재현한 것이 아니라 가격테이프 근사다.
- 일부 report CLOSED path는 현재 `v2_learning_performance`에 직접 매칭되지 않았다. 이벤트 store와 performance sync 간 누락/시점 차이는 별도 audit 대상이다.

## KR Wait 구분

KR wait는 이번 live 성과 delta에 포함하지 않는다. 현재는 read-only monitoring/report 단계다.

관찰 후보:

- `session_open + confirmed + WATCH + OPEN_30_60/OPEN_60_90/OPEN_90_270`
- `analyst_reinvoke + partial + PROBE_READY + wait_30m + OPEN_30_60/OPEN_60_90/OPEN_90_270`

이 후보들은 30개 이상 신규 live-visible 표본이 쌓이기 전까지 live 주문 성과 개선으로 계산하지 않는다.

## 최종 판단

이번 수정은 기존 수익 경로를 줄이는 변경이 아니라, US PathB에서 고가 우량 후보를 잘못 영구 차단하던 경로를 완화하는 개선이다. 성과 테이프 기준으로도 missed 후보가 있었고, 기존 수익 경로인 target/pre-close/profit ladder는 변경하지 않았다.

다음 관찰 포인트는 live 재시작 후 `HIGH_PRICE_BUDGET_BLOCK`가 `temporary_early_entry_size_gate`/waiting 후 정상 재평가로 바뀌는지, 그리고 추가 진입이 기존 PathB 수익 후보를 밀어내지 않는지다.
