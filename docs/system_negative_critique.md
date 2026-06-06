# 자동매매 시스템 부정적 비판

작성일: 2026-06-07 KST  
관점: 현실적이고 보수적이며, 실패 가능성을 과장 없이 강하게 드러내는 비판  
전제: 코드/config/runtime/DB는 수정하지 않고 현재 증거만 기준으로 판단

## 1. 한 줄 비판

이 시스템은 US PathB 일부 경로에서는 돈을 벌고 있지만, 전체적으로 보면 아직 "수익 시스템"이라기보다 "복잡한 실전 실험 장치"에 가깝다. 수익이 나는 부분은 분명히 존재하지만, 성과 ledger, source attribution, learning loop, broker truth freshness, config drift, KR signal conversion이 모두 완전히 닫히지 않았기 때문에 자신 있게 확장할 단계는 아니다.

## 2. 수익률 숫자가 이미 위험하게 착시를 만든다

겉으로 보면 live closed `pnl_pct` 기준 전체 평균은 +0.269%이고, US는 +0.735%다. 하지만 이 숫자는 계좌 수익률이 아니다.

문제는 다음과 같다.

| 문제 | 실제 상태 |
|---|---|
| 계좌 단위 portfolio return | 현재 문서 작성 기준 직접 산출 불가 |
| `pnl_pct` closed row | 165건 |
| `pnl_krw` closed row | 45건 |
| `portfolio_realized` 컬럼 | live DB에 없음 |
| `strategy_attribution` 컬럼 | live DB에 없음 |
| `CLOSED_AUDITED_BROKER_SELL` | 5건, `exit_price=NULL`, `pnl_krw=NULL`, `learning_allowed=0` |

즉, 현재 성과 숫자는 "거래별 퍼센트 평균"으로는 의미가 있지만 "실제 계좌가 얼마나 벌었는가"라는 질문에는 약하다. 특히 PathB는 1주 주문, 고정 KRW 예산, 미국 주식 환율 변환, manual/audited broker sell이 섞이므로 capital-weighted 성과가 아니면 착시가 커진다.

가장 나쁜 결론은 다음이다.

> 지금 성과 숫자를 근거로 전략을 키우면, 실제로는 계좌 수익률이 아니라 metadata coverage가 좋은 거래만 확대하는 꼴이 될 수 있다.

## 3. KR live 성과는 변명하기 어렵다

KR은 live closed `pnl_pct` 기준 43건, 승률 27.91%, 평균 -1.055%다. 이건 단순히 표본이 적어서 생긴 노이즈라고 보기 어렵다.

| KR 전략 | 거래 수 | 승률 | 평균 pnl% | 합산 pnl% |
|---|---:|---:|---:|---:|
| momentum | 11 | 18.18% | -1.955% | -21.507% |
| opening_range_pullback | 6 | 16.67% | -1.320% | -7.919% |
| gap_pullback | 20 | 35.00% | -0.693% | -13.866% |
| mean_reversion | 1 | 0.00% | -0.707% | -0.707% |

KR은 현재 "조금만 다듬으면 바로 잘 될 것" 같은 상태가 아니다. 더 냉정하게 말하면, KR 쪽은 selection, signal, execution 중 어디서 깨지는지조차 아직 운영자가 한눈에 확신하기 어렵다.

최근 2026-06-01부터 2026-06-05까지 KR은 `trade_ready=8`, `signal_fired=0`, `traded=0`이다. 후보를 만들었는데 신호가 하나도 안 나간다. 이건 "AI가 좋은 종목을 못 고른다"보다 더 복잡하다.

가능한 최악의 해석:

- Claude가 strategy가 실제로 실행할 수 없는 후보를 고른다.
- strategy signal 조건이 시장 현실과 맞지 않는다.
- intraday evidence가 늦거나 부정확해서 signal이 사라진다.
- KR 후보 carry가 늘어나도 결국 no-signal만 늘 수 있다.

이 상태에서 KR live를 확장하면 손실 표본을 더 빠르게 수집할 가능성이 높다.

## 4. 시스템이 너무 복잡해서 실패 원인이 잘 숨는다

현재 흐름은 구조적으로는 훌륭하지만 운영 관점에서는 매우 복잡하다.

```text
candidate source
-> prompt pool
-> Claude response
-> normalized meta
-> RouteDecision
-> PathA/PathB 분기
-> strategy signal
-> broker/risk/sizing gate
-> order
-> fill/reconcile
-> exit manager
-> hold advisor
-> lifecycle event
-> learning sync
-> dashboard/report
```

이 구조의 문제는 한 단계만 깨져도 최종 결과는 똑같이 "안 샀다", "못 팔았다", "손실났다"로 보인다는 점이다.

예:

- selection이 나빠도 `NO_SIGNAL`로 보일 수 있다.
- broker truth가 stale해도 selection 품질 문제처럼 보일 수 있다.
- sizing이 막아도 Claude 판단 실패처럼 보일 수 있다.
- PathB price plan이 틀려도 strategy 성과가 나쁜 것처럼 보일 수 있다.
- manual/audited broker sell이 섞이면 strategy learning이 오염될 수 있다.

이 정도 복잡도에서는 리포트가 코드만큼 중요하다. 그런데 현재 리포트와 DB는 아직 그 복잡도를 충분히 설명하지 못한다.

## 5. Selection funnel은 토큰을 많이 쓰고 실행 전환은 낮다

전체 ticker selection log 기준:

| 시장 | rows | trade_ready | signal_fired | traded | trade_ready rate | traded rate |
|---|---:|---:|---:|---:|---:|---:|
| KR | 3,982 | 130 | 54 | 43 | 3.26% | 1.08% |
| US | 4,958 | 426 | 59 | 34 | 8.59% | 0.69% |

최근 Claude call audit도 많은 후보와 watchlist를 만들지만 실제 trade_ready는 제한적이다.

| 날짜 | 시장 | calls | prompt candidates | watchlist | trade_ready |
|---|---|---:|---:|---:|---:|
| 2026-06-05 | KR | 28 | 448 | 212 | 1 |
| 2026-06-05 | US | 27 | 512 | 225 | 14 |
| 2026-06-04 | KR | 28 | 896 | 617 | 2 |
| 2026-06-04 | US | 26 | 455 | 204 | 7 |

부정적으로 보면 이렇다.

> 후보를 많이 만들고, Claude를 많이 호출하고, 로그를 많이 남기지만, 실제 주문과 검증 가능한 학습으로 닫히는 비율은 낮다.

이 구조는 방치하면 비용과 복잡도만 늘고, 수익 개선은 "뭔가 많이 하고 있다"는 착시 뒤에 묻힌다.

## 6. Learning loop는 아직 닫히지 않았다

closed row 자체는 꽤 있지만, learning에 쓸 수 있는 row는 매우 적다.

기존 active work와 DB 점검 기준:

- live `v2_learning_performance`는 512 rows.
- closed row는 166 rows.
- `learning_allowed=1` closed row는 KR 3, US 1 수준으로 매우 낮다.
- `CLOSED_AUDITED_BROKER_SELL`은 portfolio realized에는 중요하지만 strategy learning에는 섞으면 안 된다.
- candidate audit outcome에는 `daily_pending=1551`, `audit_sparse=19716`, `insufficient_samples=62810`가 남아 있다.

이 상태에서 자동 교훈, strategy promotion, prompt tuning을 강하게 돌리면 "학습"이 아니라 "오염된 기록의 재해석"이 된다.

가장 위험한 실패 모드는 다음이다.

1. 특정 전략이 운 좋게 몇 번 수익을 낸다.
2. ledger가 incomplete라 손실 close나 manual/audited close가 분리되지 않는다.
3. 시스템이 그 전략을 좋은 전략으로 착각한다.
4. live sizing이나 exposure가 커진다.
5. 실제로는 오염된 성과였다는 것을 손실 후에 알게 된다.

## 7. Candidate audit은 많지만 아직 신뢰하기 어렵다

candidate audit DB는 53,123 rows로 크다. 하지만 핵심 품질 필드가 비어 있다.

| 항목 | 값 |
|---|---:|
| `candidate_source` blank | 53,055 / 53,123 |
| `primary_bucket` blank | 19,648 |
| `actual_prompt_included IS NULL` | 33,008 |
| `daily_pending` outcomes | 1,551 |

이건 치명적이다. candidate audit의 목적은 "왜 이 후보가 올라왔고, Claude가 봤고, 실행됐고, 결과가 어땠는지"를 설명하는 것이다. 그런데 source와 prompt inclusion이 비어 있으면 다음 질문에 답하기 어렵다.

- 이 종목은 어떤 screener/source에서 왔는가?
- Claude가 실제 prompt에서 봤는가?
- watch_only였는데 나중에 체결된 것인가?
- 실행 row와 selection row가 causal하게 연결됐는가?
- 좋은 후보를 놓친 것인가, 애초에 좋은 후보가 아니었는가?

데이터는 많은데 provenance가 약하면, 분석은 날카로워지는 게 아니라 더 자신감 있게 틀린다.

## 8. PathB miss quality는 돈이 새는 구멍을 보여준다

PathB miss quality에서 US `INVALID_PRICE`는 29건이고, 이 중 26건이 cancel 이후 zone에 다시 들어왔다. 평균 30분 MFE는 +1.222%다.

이건 꽤 불편한 숫자다.

좋게 보면 실행 품질 개선 여지가 있다. 나쁘게 보면 시스템이 "들어갈 수 있었던 가격 계획"을 만들어 놓고도 가격/상태 관리 실패로 수익 기회를 버렸다는 뜻이다.

하지만 여기서 더 위험한 유혹은 safety gate를 완화하는 것이다.

- broker truth fail-closed를 완화하면 상태 오염 위험이 커진다.
- sizing reason split을 흐리면 고가/최소주문/예산 차단 원인을 잃는다.
- `INVALID_PRICE`를 무시하면 엉뚱한 가격으로 주문이 나갈 수 있다.

즉 이 문제는 execution diagnostics로 풀어야지, safety 완화로 풀면 안 된다.

## 9. Broker truth와 config drift가 운영 신뢰를 흔든다

최근 monitor report에는 broker truth stale/untrusted가 반복적으로 나온다. 2026-06-06 overnight progress 기준으로도 broker truth stale이 action_required였고, entry scan blocked가 다수 발생했다.

파일 기준으로는 `KR_PATHB_SELECTION_RECONCILE_MODE=enforce`인데, 과거 runtime effective snapshot에는 `KR_PATHB_SELECTION_RECONCILE_MODE=shadow`가 남아 있었다.

이건 작은 문제가 아니다. 자동매매에서 운영자가 가장 싫어해야 하는 문장은 다음이다.

> "파일에는 enforce인데, 실제 runtime이 무엇을 쓰는지 확신이 없다."

자동매매 시스템의 위험은 보통 전략 아이디어보다 operational truth mismatch에서 온다. 코드가 맞아도, 실행 중인 프로세스가 다른 env/config를 읽으면 결과는 틀린다.

## 10. Claude 의존성은 아직 비용과 품질 리스크가 크다

최근 US overnight monitor 예시:

- 2026-06-06 session monitor 중 Claude calls 23.
- input tokens 115,093.
- output tokens 15,698.
- labels: `select_tickers`, `hold_advisor_triage`, `hold_advisor_challenge`, `param_tuner`, analyst rounds 등.

2026-06-04 monitor에서는 calls 63, input tokens 267,628, output tokens 42,200이었다.

이 숫자 자체가 곧 문제는 아니다. 문제는 호출량이 많을수록 다음 리스크가 커진다는 점이다.

- hold advisor 반복 호출 비용.
- strict JSON contract 위반.
- prompt가 길어지면서 핵심 evidence가 묻힘.
- latency로 인한 매도/보호 판단 지연.
- fallback/HOLD 편향.
- "AI가 의견을 냈다"와 "실제 주문 조건이 충족됐다"의 혼동.

AI가 종목과 HOLD/SELL 의견을 낼 수 있다는 점은 장점이지만, 이 시스템은 AI 출력 품질을 runtime truth처럼 취급하면 바로 위험해진다.

## 11. Worktree와 runtime 산출물 위생도 약하다

현재 worktree에는 여러 tracked diff와 untracked report/state 파일이 남아 있다. 그중에는 `trading_bot.py`, `runtime/pathb_runtime.py`, `config/v2_start_config.json`, `state/brain.json` 같은 민감한 파일도 포함된다.

이 문서 작업에서는 그 파일들을 수정하지 않았지만, 운영 관점에서는 이 상태 자체가 리스크다.

위험:

- 어떤 변경이 live behavior에 실제 반영됐는지 추적이 어려워진다.
- 테스트 통과가 어떤 코드 상태에 대한 것인지 애매해진다.
- state/brain/config/runtime diff가 섞이면 원인 분리가 어렵다.
- 문서/리포트/임시 state가 쌓여 다음 분석의 노이즈가 된다.

실전 자동매매 repo는 "나중에 치우자"가 오래 누적되면, 결국 사고 때 원인을 못 찾는다.

## 12. 가장 비관적인 실패 시나리오

이 시스템이 실패한다면 전략 아이디어 하나가 틀려서라기보다 다음 순서일 가능성이 높다.

1. US PathB 수익이 보여서 시스템 전체에 대한 신뢰가 커진다.
2. KR도 비슷하게 좋아질 것이라고 보고 live 후보를 늘린다.
3. 하지만 KR은 `trade_ready -> signal_fired`가 계속 막히거나, 손실 전략만 체결된다.
4. 성과 ledger가 incomplete라 어떤 전략이 진짜 손실을 만들었는지 늦게 확인된다.
5. Candidate audit source가 비어 있어 selection 원인도 흐려진다.
6. broker truth stale/config drift가 섞여 execution 실패와 selection 실패가 뒤엉킨다.
7. 개선이라는 이름으로 shared strategy나 PathB safety를 건드린다.
8. 그 결과 KR 손실을 줄이려다 US PathB 수익 엔진까지 망가진다.

이게 가장 피해야 할 경로다.

## 13. 부정적 결론

현재 시스템의 가장 큰 단점은 "못 버는 시스템"이라는 점이 아니다. 더 정확한 단점은 다음이다.

- 수익이 나는 부분과 손실이 나는 부분이 한 repo 안에 같이 있다.
- 수익 경로는 보호 대상인데, 개선 욕구가 그 경로까지 건드릴 위험이 있다.
- KR 손실은 명확한데 원인 분해가 아직 충분하지 않다.
- performance ledger가 portfolio realized와 learning attribution을 완전히 분리하지 못한다.
- candidate audit은 양은 많지만 source/freshness가 약하다.
- broker truth/config/runtime drift가 operational trust를 흔든다.

따라서 지금 필요한 것은 더 똑똑한 AI prompt나 더 공격적인 전략이 아니다.

지금 필요한 것은 냉정하게 다음을 끝내는 것이다.

1. 성과 ledger를 믿을 수 있게 만든다.
2. KR no-signal 원인을 숫자로 분해한다.
3. US PathB 수익 경로는 건드리지 않는다.
4. execution miss와 selection miss를 분리한다.
5. live runtime truth가 파일/config와 일치하는지 검증한다.

이 작업 없이 live 개선을 밀어붙이면, 시스템은 더 좋아지는 게 아니라 더 복잡하게 틀릴 가능성이 높다.
