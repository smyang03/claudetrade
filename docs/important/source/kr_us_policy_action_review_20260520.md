# 2026-05-20 KR/US 정책 수정 판단 리뷰

## 결론

KR과 US는 같은 방식으로 풀면 안 된다.

- KR은 selection/signal의 forward edge는 있으나 실제 체결 PnL이 약하다. 따라서 자동 완화보다 `active re-ask + fresh plan`을 먼저 실전 경로에 붙이는 것이 맞다.
- US는 selection, trade_ready, signal, partial/rescreen, 실제 PathB 성과가 모두 KR보다 좋다. US는 조건부 실전 진입을 더 적극적으로 연결해도 된다.
- 두 시장 모두 `raw READY`와 `normalized/applied trade_ready`, `route final action`을 분리 기록해야 한다.
- `evidence_action_ceiling`만 보고 자동 승격하는 것은 KR/US 모두 부적절하다.

## 전체 기간 성과 요약

### KR selection 성과

`data/ticker_selection_log.db` 기준 KR `2026-04-08` ~ `2026-05-20`.

| group | rows | 1D avg | 3D avg | 5D avg | 3D max runup avg | 3D max drawdown avg | realized pnl avg |
|---|---:|---:|---:|---:|---:|---:|---:|
| all KR | 1,370 | -0.09% | -0.20% | -0.36% | +12.24% | -8.18% | -2.51% |
| `trade_ready=1` | 112 | +0.95% | +4.97% | +3.44% | +17.19% | -8.15% | -0.76% |
| `signal_fired=1` | 52 | +3.18% | +4.27% | +5.15% | +17.95% | -7.88% | -2.51% |
| `traded=1` | 41 | +2.46% | +4.39% | +3.91% | +18.63% | -8.66% | -2.51% |
| `partial` | 70 | +2.62% | +7.84% | +6.18% | +16.98% | -4.45% | -2.70% |
| `preopen_watch` | 141 | -1.15% | -3.80% | -8.64% | +12.00% | -11.48% | -7.25% |

해석:

- KR은 `trade_ready`, `signal_fired`, `partial`에 forward edge가 있다.
- 하지만 realized PnL은 음수다.
- 즉 selection이 완전히 틀린 것이 아니라, 진입 타이밍, 주문 경로, 청산/손절, stale plan 처리에서 edge를 수익으로 전환하지 못하고 있다.

### US selection 성과

`data/ticker_selection_log.db` 기준 US `2026-04-07` ~ `2026-05-19`.

| group | rows | 1D avg | 3D avg | 5D avg | 3D max runup avg | 3D max drawdown avg | realized pnl avg |
|---|---:|---:|---:|---:|---:|---:|---:|
| all US | 2,296 | +0.65% | +1.65% | +2.09% | +6.91% | -4.40% | -1.09% |
| `trade_ready=1` | 176 | +1.57% | +3.65% | +4.92% | +8.62% | -3.51% | +0.79% |
| `signal_fired=1` | 44 | +2.17% | +4.10% | +3.79% | +9.72% | -3.25% | -1.09% |
| `traded=1` | 30 | +2.24% | +3.58% | +2.19% | +9.66% | -3.37% | -1.09% |
| `partial` | 94 | +1.28% | +4.00% | +4.28% | +8.05% | -3.70% | +2.68% |
| `rescreen` | 1,757 | +0.84% | +1.88% | +2.34% | +7.14% | -4.22% | -1.84% |
| `preopen_watch` | 221 | -0.65% | -1.30% | -1.52% | +4.85% | -6.47% | +7.84% |

US realized PnL 주의:

- raw realized PnL 평균은 `-1.09%`.
- `2026-04-10 MRVL`에 `-99.93%` outlier가 있다.
- 이 outlier를 제외하면 US realized PnL은 `27건 평균 +2.58%`, median `+0.13%`, `15승 12패`다.

V2/PathB:

- `v2_learning_performance` US closed non-DIRTY: `19건 평균 +1.31%`, `14승 5패`.
- 최근 US PathB/PlanA는 KR보다 실제 수익 전환이 잘 된다.
- 다만 `quality_grade=SUSPECT`가 많아서 learning 자동 반영은 조심해야 한다.

## 시장별 판단

### KR

KR은 “선정은 일부 맞는데 실행이 못 먹는다”에 가깝다.

근거:

- `trade_ready=1`은 3D `+4.97%`, 5D `+3.44%`.
- `signal_fired=1`은 1D `+3.18%`, 3D `+4.27%`, 5D `+5.15%`.
- `partial`은 3D `+7.84%`, 5D `+6.18%`로 가장 강하다.
- 그러나 실제 PnL은 평균 `-2.51%`.

따라서 KR은 조건을 풀어서 더 많이 사는 것보다, 다음이 먼저다.

1. temporal blocker 해소 후 active re-ask.
2. stale plan이면 자동 매수 금지, fresh plan 재수령.
3. partial 후보 fast-lane으로 장중 재평가.
4. preopen_watch 단독 매수 금지.
5. realized PnL을 갉아먹는 entry/exit/late chase/same-day reentry 원인 분리.

KR에서 바로 live 완화하면 안 되는 것:

- `evidence_action_ceiling`만 보고 자동 승격.
- `kr_data_quality_not_confirmed`를 무조건 PROBE 허용.
- `structural blocker` 무시.
- `preopen_watch` 단독 진입.
- stale target/stop을 유지한 채 자동 진입.

KR에서 바로 개발/운영 반영할 것:

- raw compact `tr` 보존.
- normalized/applied `trade_ready` 분리 기록.
- blocker taxonomy 추가.
- temporal blocker 해소 시 active re-ask.
- partial 후보의 재평가 우선순위 상향.

### US

US는 KR보다 확실히 더 적극적으로 갈 수 있다.

근거:

- all US selection이 1D/3D/5D 모두 양수다.
- `trade_ready=1`은 1D `+1.57%`, 3D `+3.65%`, 5D `+4.92%`.
- `signal_fired=1`은 1D `+2.17%`, 3D `+4.10%`, 5D `+3.79%`.
- `partial`도 3D `+4.00%`, 5D `+4.28%`.
- US realized PnL은 outlier 제외 시 평균 `+2.58%`.
- V2/PathB closed non-DIRTY는 `+1.31%`, `14승 5패`.

따라서 US는 다음을 실전 경로로 연결해도 된다.

1. active re-ask 결과가 fresh `BUY_READY`/`PROBE_READY`.
2. evidence complete.
3. OR/VWAP/volume 중 최소 확인 조건 충족.
4. stale 아님.
5. `risk_off_regime` hard block 아님.
6. same-day reentry block 없음.
7. broker truth 정상.

US에서 강화할 것:

- `partial`/`rescreen` 후보 fast-lane.
- evidence complete + fresh re-ask 후보의 실전 연결.
- PathB live 유지 또는 제한적 확대.
- PlanA와 PathB 간 active order lock은 유지.

US에서 풀면 안 되는 것:

- `risk_off_regime` hard block 무시.
- `fade`, `deep_from_high`, `same_day_reentry` 무시.
- `preopen_watch` 단독 진입.
- `evidence_action_ceiling`만 보고 로컬 자동 승격.

## 2026-05-20 KR 사례 재해석

오늘 KR 사례는 정책 판단의 좋은 예다.

| ticker | 시나리오 | 결과 | 판단 |
|---|---|---:|---|
| `142280` | 09:04 temporal blocker 무시 진입 | 종가 기준 +18.44% | missed opportunity |
| `142280` | 09:16 OR 재확인 후 진입 | 종가 기준 +7.05% | 가격상 유효하나 raw target 초과라 re-ask 필요 |
| `080220` | 같은 blocker 완화 적용 | 종가 기준 -5.20%, stop close 터치 | blanket 완화 위험 |
| `456010` | partial-data PROBE 허용 | 종가 기준 -7.72% | 보수 필터가 손실 방어 |
| `032580` | partial-data PROBE 허용 | 종가 기준 -1.18% | 보수 필터가 손실 방어 |

결론:

- `142280` 때문에 temporal 재평가는 필요하다.
- `080220` 때문에 blocker 무시는 안 된다.
- `456010`, `032580` 때문에 data-quality gate를 무조건 풀면 안 된다.
- 올바른 수정은 “temporal 해소 후 active re-ask + fresh plan일 때만 실전 연결”이다.

## 추가 의문점 검증

### 1. KR 선정-실행 괴리의 실제 누수 지점

KR `realized PnL -2.51%`는 selection이 전부 틀렸다는 뜻이 아니다. DB를 다시 보면 손실은 주로 `intraday_review_sell`과 `stop_loss`에 몰려 있다.

| exit_reason | rows | realized pnl avg | 1D avg | 3D avg | 5D avg | 해석 |
|---|---:|---:|---:|---:|---:|---|
| `intraday_review_sell` | 18 | -1.58% | +4.65% | +8.34% | +12.85% | forward edge가 큰데 조기/리뷰 청산으로 수익 전환 실패 |
| `stop_loss` | 8 | -7.09% | +5.73% | +5.49% | +5.15% | 손절 후 재상승 또는 진입 타이밍/stop 폭 문제 가능성 |
| `trail_stop` | 4 | +1.87% | -5.03% | -15.24% | -22.14% | trailing은 오히려 방어 기능이 있음 |
| `loss_cap` | 2 | -3.00% | +15.84% | +5.42% | -3.95% | 1D 강세를 먹지 못하고 손실 제한으로 종료 |

특히 “forward는 양수인데 realized가 음수”인 케이스가 19건이다. 대표 사례는 다음과 같다.

| date | ticker | source | exit_reason | realized pnl | 1D | 3D | 5D | 3D runup |
|---|---|---|---|---:|---:|---:|---:|---:|
| 2026-04-16 | `069540` | partial | `stop_loss` | -13.32% | +29.93% | +36.59% | +11.75% | +57.87% |
| 2026-04-17 | `010820` | partial | `stop_loss` | -2.56% | +16.47% | +21.37% | +10.76% | +38.95% |
| 2026-05-04 | `003470` | partial | `intraday_review_sell` | -1.81% | +29.85% | +18.91% | +8.62% | +39.30% |
| 2026-04-28 | `001440` | rescreen | `intraday_review_sell` | -2.01% | +11.55% | +32.24% | +39.65% | n/a |
| 2026-04-30 | `006340` | rescreen | `intraday_review_sell` | -1.94% | +15.28% | +15.88% | +14.22% | n/a |
| 2026-05-08 | `010170` | initial | `loss_cap` | -2.80% | +25.06% | +10.74% | +2.91% | n/a |

판단:

- KR은 “더 많이 사면 해결”이 아니다.
- 그러나 “계속 shadow만 보자”도 아니다. 이미 forward edge가 있는 그룹이 확인된다.
- 먼저 `execution_leak` 라벨을 만들어야 한다. 기준은 `realized pnl < 0`이고 `forward_1d` 또는 `forward_3d > 0`인 체결이다.
- `execution_leak` 체결은 entry time/entry price, plan target/stop, exit time/exit reason, 이후 max runup을 한 묶음으로 저장해야 한다.
- KR active re-ask는 실전 연결하되, `stop_loss`와 `intraday_review_sell` 누수 원인 분석 없이는 진입 수 확대나 자동 완화로 가면 안 된다.

### 2. KR partial fast-lane은 필요하지만 바로 매수 경로는 아니다

KR partial은 `3D +7.84%`, `5D +6.18%`로 가장 강한 forward edge를 보였다. 동시에 실제 realized PnL은 `-2.70%`다.

이는 partial 자체가 나쁘다는 뜻보다, partial 후보가 실전 경로에 들어올 때 타이밍과 plan freshness가 무너진다는 신호에 가깝다.

따라서 partial 정책은 다음 순서가 맞다.

1. partial 후보를 장중 재평가 fast-lane에 올린다.
2. re-ask 시점의 가격이 기존 target을 초과하면 반드시 retarget한다.
3. fresh plan의 stop 폭이 opening volatility를 감당하지 못하면 주문하지 않는다.
4. partial이라는 이유만으로 `PROBE_READY`를 자동 부여하지 않는다.

### 3. US MRVL -99.93% outlier 재평가

`2026-04-10 MRVL`의 realized PnL `-99.93%`는 정상 손절이나 정상 broker event로 보기 어렵다.

확인 결과:

- `ticker_selection_log.db` row id `203`.
- market `US`, bot_mode `paper`, source_type `rescreen`.
- signal/trade 시각은 `2026-04-10 22:40 KST` 부근.
- exit_reason은 `intraday_review_sell`.
- realized PnL은 `-99.93%`.
- 그러나 forward는 `1D +2.19%`, `3D +4.76%`, `5D +8.72%`.
- max drawdown도 3D `+0.37%`, 5D `-0.05%`로 거의 손실이 없다.
- V2/PathB 체결/청산 성과에는 동일한 `MRVL -99.93%` 실현손익이 붙어 있지 않다.

판단:

- 이 건은 US 전략 실패라기보다 legacy paper realized PnL 산식 또는 가격 기준 오염 가능성이 높다.
- US 성과 판단에서는 outlier 제외가 타당하다.
- 다만 learning에는 절대 자동 반영하면 안 된다. `MRVL` row는 anomaly/quarantine 대상으로 남겨야 한다.
- US 확대 판단의 선행 조건은 “MRVL 때문에 전면 보류”가 아니라 “MRVL 같은 paper/accounting outlier를 성과 집계와 learning에서 격리”다.

### 4. US preopen_watch +7.84% 역설

US `preopen_watch`는 forward가 모두 음수인데 realized PnL만 `+7.84%`로 보였다. 재확인 결과 realized PnL이 붙은 건은 단 1건이다.

| metric | value |
|---|---:|
| US preopen_watch rows | 221 |
| forward_1d avg | -0.65% |
| forward_3d avg | -1.30% |
| forward_5d avg | -1.52% |
| realized PnL rows | 1 |
| realized PnL avg | +7.84% |

실현손익 1건은 `2026-05-07 NVDA`이고, realized PnL은 `+7.84%`다. 표본 1건이므로 정책 완화 근거로 쓰면 안 된다.

판단:

- US `preopen_watch` 단독 진입 금지는 유지한다.
- `preopen_watch`는 post-open revalidation 후보군으로만 사용한다.
- realized PnL `+7.84%`는 좋은 사례지만, 정책 통계로 취급하지 않는다.

### 5. KR/US realized PnL 비교 기준

기존 비교는 방향성 판단에는 유효하지만 완전히 같은 기준의 비교는 아니다.

- `ticker_selection_log.db`의 realized PnL은 KR/US 모두 `paper`와 `live`가 섞여 있다.
- KR은 `initial`, `rescreen`, `partial`, `preopen_watch`와 다양한 exit_reason이 섞여 있다.
- US도 `paper`와 `live`가 섞이고, raw 평균은 `MRVL -99.93%` 하나에 크게 왜곡된다.
- `v2_learning_performance`는 route/quality 정보가 더 좋지만 표본이 작다.

V2 기준으로 다시 보면:

| group | rows | avg pnl | win/loss | 해석 |
|---|---:|---:|---:|---|
| KR V2 closed CLEAN | 3 | -1.07% | 1승 2패 | KR 부진 방향은 맞지만 표본이 작음 |
| US V2 closed non-DIRTY | 19 | +1.31% | 14승 5패 | US 실행 성과는 상대적으로 양호 |
| US V2 closed CLEAN only | 1 | -1.32% | 0승 1패 | CLEAN-only 판단은 아직 불가 |
| US V2 closed SUSPECT | 17 | +1.08% | 13승 4패 | 운영 참고는 가능, learning 자동 승격은 금지 |

판단:

- “US가 KR보다 낫다”는 방향성은 유지된다.
- 다만 US의 sizing/learning 확대는 clean quality backfill 이후가 맞다.
- KR/US 정책 비교 보고서에는 앞으로 `ticker_selection_log` 기준과 `v2_learning_performance` 기준을 분리해서 적어야 한다.

### 6. 아직 미확인인 핵심 질문

`signal_fired=1` 52건에서 실제 entry price가 plan target/stop 대비 stale이었는지는 현재 평면화된 `ticker_selection_log`만으로는 충분히 판별되지 않는다.

필요한 추가 backfill:

- execution decision id 또는 path run id.
- re-ask plan json의 target/stop.
- 실제 entry price와 entry time.
- exit reason과 exit time.
- 이후 1D/3D max runup/max drawdown.

이 backfill이 생기면 `stale plan`, `late chase`, `too-tight stop`, `premature review sell`을 분리할 수 있다.

## 바로 적용할 정책

### 공통

1. raw compact `tr`를 audit row 또는 derived table에 보존한다.
2. normalized `trade_ready`, applied `trade_ready`, route final action을 분리 저장한다.
3. blocker token을 canonical taxonomy로 정리한다.
4. stale plan이면 자동 주문 금지.
5. `candidate_counterfactual_paths` outcome backfill을 수행한다.
6. realized 손실인데 forward가 양수인 체결은 `execution_leak`로 별도 라벨링한다.
7. `MRVL -99.93%` 같은 paper/accounting anomaly는 성과 집계와 learning에서 quarantine한다.
8. `ticker_selection_log` 성과와 V2/PathB 성과를 같은 표에서 섞지 말고 기준을 분리한다.

### KR

1. temporal blocker 해소 후 active re-ask는 실전 경로에 붙인다.
2. fresh re-ask에서 READY가 다시 나오고 risk/execution gate를 통과하면 주문 경로로 보낸다.
3. partial 후보는 재평가 우선순위를 올린다.
4. `kr_data_quality_not_confirmed` 단독 완화는 하지 않는다. 단, KR intraday evidence의 완성 품질값인 `minute_complete`를 confirmation data-quality check에서 인정하는 것은 정책 완화가 아니라 버그 수정으로 분리한다.
5. `preopen_watch` 단독 진입은 금지 유지.
6. `stop_loss`/`intraday_review_sell` 누수 체결은 entry/exit/forward runup을 묶어서 매일 리포트한다.
7. KR 진입 수 확대는 execution leak 원인 분리 이후로 미룬다.

### US

1. active re-ask + evidence complete + fresh READY는 실전 연결한다.
2. partial/rescreen fast-lane을 강화한다.
3. PathB live는 유지한다.
4. V2/PathB 확대는 `risk_off_regime`과 same-day reentry block을 유지한 조건부 확대가 맞다.
5. quality `SUSPECT`는 운영 참고에는 사용하되, learning 자동 승격에는 사용하지 않는다.
6. `preopen_watch`는 post-open revalidation 전까지 단독 진입 금지로 유지한다.
7. US 성과 확대 판단에서는 MRVL anomaly 제외 기준을 명시한다.

## 최종 판단

수정은 해야 한다.

다만 시장별로 강도가 다르다.

- KR: `active re-ask`는 실전 연결, 자동 완화는 금지.
- KR 추가 조건: `execution_leak` 원인 분리 전까지 진입 수 확대는 금지.
- US: `active re-ask + evidence complete + fresh READY`는 실전 진입까지 연결 가능.
- US 추가 조건: `MRVL` 같은 anomaly는 quarantine하고, `preopen_watch` 단독 진입은 계속 금지.
- 공통: raw/normalized/applied/route 로그 분리와 blocker taxonomy는 즉시 수정.

현재 데이터 기준으로 가장 실용적인 정책은 “전부 shadow”가 아니라, **KR은 재질문을 실전 경로에 붙이되 실행 누수를 먼저 잡고, US는 anomaly 격리와 evidence 조건을 전제로 조건부 실전 진입까지 더 적극적으로 열어두는 것**이다.
