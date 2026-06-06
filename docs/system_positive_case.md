# 자동매매 시스템 긍정적 평가와 유지해야 할 강점

작성일: 2026-06-07 KST  
관점: 현재 시스템에서 실제로 작동하는 부분, 보호할 설계, 확장 가능한 장점을 정리  
전제: 성과 한계와 운영 리스크를 숨기지 않고, 긍정적인 부분만 분리해서 평가

## 1. 한 줄 긍정 평가

이 시스템은 아직 거칠지만, 단순한 AI 자동주문기가 아니다. Claude 판단, 전략 신호, risk gate, broker truth, PathA/PathB routing, lifecycle/audit/learning DB가 분리되어 있어 "무엇이 돈을 벌고 무엇이 망가지는지"를 추적할 수 있는 구조를 갖고 있다. 특히 US PathB `claude_price`는 이미 live에서 수익 구조를 보여줬다.

## 2. 가장 큰 장점은 AI와 주문 권한의 분리다

이 시스템은 Claude가 종목을 고른다고 바로 주문하지 않는다.

```text
Claude selection
-> candidate action normalize
-> RouteDecision
-> strategy signal
-> risk / affordability / broker truth gate
-> order
```

이 분리는 매우 중요하다.

좋은 점:

- AI가 최종 주문 수량/금액을 직접 정하지 않는다.
- broker truth가 AI 판단보다 우선한다.
- hard stop/loss cap 같은 보호 장치가 AI 의견보다 우선한다.
- `WATCH`, `BUY_READY`, `PULLBACK_WAIT`, `AVOID`가 라우팅 단계에서 분리된다.
- selection 품질과 execution/risk 문제를 따로 분석할 수 있다.

자동매매에서 가장 위험한 구조는 "AI가 좋다고 했으니 산다"다. 이 시스템은 적어도 그런 구조는 아니다.

## 3. US PathB는 실제 수익 엔진으로 볼 만하다

live closed `pnl_pct` 기준 US 성과:

| 기준 | 거래 수 | 승률 | 평균 pnl% | 합산 pnl% |
|---|---:|---:|---:|---:|
| US 전체 | 122 | 50.00% | +0.735% | +89.708% |
| US `claude_price` PathB | 117 | 50.43% | +0.747% | +87.440% |

US 전략별 성과:

| 전략 | 거래 수 | 승률 | 평균 pnl% | 합산 pnl% |
|---|---:|---:|---:|---:|
| claude_price | 79 | 54.43% | +1.047% | +82.685% |
| opening_range_pullback | 7 | 57.14% | +1.692% | +11.847% |
| momentum | 11 | 45.45% | +0.655% | +7.206% |
| gap_pullback | 24 | 37.50% | -0.403% | -9.675% |

해석:

- US는 우연히 한두 거래가 아니라 122 closed row에서 평균 플러스다.
- 특히 `claude_price`가 성과 대부분을 만든다.
- `US_MOMENTUM_LIVE_ENABLED=true` 상태에서 US momentum도 평균 플러스다.
- gap_pullback은 개선 대상이지만 US 전체 구조를 부정할 정도는 아니다.

## 4. US PathB 청산 구조는 강점이다

US close reason 기준:

| close reason | 거래 수 | 승률 | 평균 pnl% | 합산 pnl% |
|---|---:|---:|---:|---:|
| `CLOSED_CLAUDE_PRICE_TARGET` | 10 | 100.00% | +5.311% | +53.115% |
| `CLOSED_CLAUDE_SELL` | 6 | 100.00% | +3.913% | +23.476% |
| `CLOSED_CLAUDE_PRICE_PRE_CLOSE` | 34 | 55.88% | +1.442% | +49.040% |
| `CLOSED_PROFIT_LADDER` | 20 | 65.00% | +1.097% | +21.945% |
| `CLOSED_HARD_STOP` | 6 | 50.00% | +0.197% | +1.180% |

긍정적으로 보면 이 시스템은 "진입만 잘하는 시스템"이 아니라 "수익을 닫는 경로"도 가지고 있다.

특히:

- target close는 매우 강하다.
- pre-close 청산은 장마감 리스크를 수익으로 닫는 역할을 한다.
- profit ladder는 peak giveback을 제한한다.
- hard stop이 평균 플러스에 가까운 것은 손실 제한과 recover가 일부 작동한다는 신호다.

이 3개 경로는 지금 시스템의 핵심 자산이다.

## 5. Broker truth fail-closed는 불편하지만 좋은 안전장치다

broker truth가 stale하거나 provider/token이 불안정할 때 신규 진입을 막는 것은 수익 기회를 놓칠 수 있다. 하지만 자동매매에서는 이게 맞다.

좋은 이유:

- local position 오염보다 broker holdings/open orders/fills를 우선한다.
- broker truth 불신 상태에서 신규 진입보다 기존 포지션 보호를 우선한다.
- PathB live entry scan이 `BLOCKED_BROKER_TRUTH`로 fail-closed된다.
- stale local position cleanup도 fresh broker evidence를 요구한다.
- zero-holding reconcile도 broker evidence를 조건으로 둔다.

검토 시점 snapshot에서는 local live open positions와 broker positions/open orders가 모두 0이었다. 이 상태가 항상 유지된다는 뜻은 아니지만, 적어도 broker truth를 중심으로 운영 상태를 확인할 수 있는 구조는 존재한다.

## 6. PathA/PathB 분리는 확장 가능한 설계다

PathA와 PathB는 성격이 다르다.

| 경로 | 역할 |
|---|---|
| Path A | 즉시 trade_ready + strategy signal 기반 진입 |
| Path B | Claude price plan + pullback/buy zone 기반 대기 진입 |

이 분리는 장점이다.

- 즉시 진입과 가격 대기 진입을 섞지 않는다.
- `RouteDecision`으로 candidate action을 추적할 수 있다.
- PathB wait-only plan이 PathA trade_ready와 분리된다.
- PathB는 buy zone hit, expired, cancelled, filled, closed가 lifecycle로 남는다.
- 나중에 개선할 때 "selection 문제인지 PathB execution 문제인지"를 분리할 수 있다.

자동매매 시스템에서 이런 추적 가능성은 나중에 수익률 자체만큼 중요해진다.

## 7. 로그와 DB가 이미 강력한 분석 기반이다

현재 확인된 주요 저장소:

| 저장소 | 역할 |
|---|---|
| `data/v2_event_store.db` | decisions, lifecycle events, path runs |
| `data/ml/decisions.db` | v2 learning/canonical performance |
| `data/ticker_selection_log.db` | selection -> trade_ready -> signal -> traded |
| `data/audit/candidate_audit.db` | candidate source/prompt/action/audit/outcome |
| `data/intraday_strategy_log.db` | strategy signal/block 이유 |
| `logs/system`, `logs/risk`, `logs/analysis`, `logs/funnel` | 운영 관측 |
| dashboard | broker/account/PnL/PathB/audit 표시 |

물론 현재 DB 품질은 완벽하지 않다. 하지만 중요한 것은 "분석할 수 있는 자료가 이미 있다"는 점이다.

많은 자동매매 시스템은 실패 후에 원인을 물으면 "로그가 없다"로 끝난다. 이 시스템은 최소한 원인을 추적할 흔적을 남기고 있다.

## 8. 보호 계약이 있는 것은 큰 장점이다

이 repo는 다음 보호 영역을 명시적으로 둔다.

- US PathB profit ladder.
- US PathB pre-close 청산.
- hold advisor 연동.
- AUTO_SELL_REVIEW HOLD cooldown.
- broker truth entry fail-closed.
- PathB sizing reason split.
- zero-holding stale reconcile.
- KIS order normalization.
- PathA/PathB `RouteDecision` 계약.
- `state/brain.json` 자동 정책 메모리 승격 금지.

이는 운영적으로 성숙한 접근이다.

좋은 점:

- 수익 엔진을 리팩터링 충동으로 망가뜨릴 가능성을 줄인다.
- 손실 개선과 안전장치 완화를 혼동하지 않게 한다.
- 코드 변경 시 MD 위반 사항을 남기게 해 의사결정 흔적이 생긴다.
- "왜 이 보호 영역을 건드렸는지"를 나중에 검토할 수 있다.

## 9. KR이 약한 것도 오히려 개선 방향을 선명하게 만든다

KR 성과는 나쁘다. 하지만 긍정적으로 보면 문제가 어느 정도 좁혀지고 있다.

최근 KR 상태:

- 2026-06-01 ~ 2026-06-05 `trade_ready=8`.
- `signal_fired=0`.
- `traded=0`.
- trade_ready 전략은 `gap_pullback=5`, `opening_range_pullback=2`, `mean_reversion=1`.
- intraday ORP block은 `orp_entry_window_expired=395`가 압도적이다.

이건 막연한 문제가 아니다.

개선 질문이 구체화된다.

- Claude가 실제 strategy window가 지난 후보를 trade_ready로 올리는가?
- ORP entry window와 selection 시점이 어긋나는가?
- KR intraday evidence가 늦거나 부족한가?
- gap_pullback은 signal threshold가 너무 좁은가?
- KR 후보 carry가 no-signal만 늘리는가?

즉 KR은 "뭐가 문제인지 모르는 상태"에서 "어느 연결부를 먼저 봐야 하는지 보이는 상태"로 가고 있다.

## 10. 안전장치가 수익 기회를 포기하는 것도 장점일 수 있다

PathB miss quality에서 `INVALID_PRICE`나 broker truth block은 답답하다. 하지만 이것은 시스템이 아무 가격에나 주문하지 않는다는 뜻이기도 하다.

긍정적인 해석:

- 가격이 이상하면 주문하지 않는다.
- broker truth가 불신이면 신규 진입하지 않는다.
- 고가 예산 초과는 `HIGH_PRICE_BUDGET_BLOCK`으로 분리된다.
- 최소 주문/수량 문제는 `ORDER_SIZE_TOO_SMALL_GATE`로 분리된다.
- early gate sizing도 별도 context를 남긴다.

물론 이로 인해 놓친 기회가 있다. 하지만 자동매매에서 "놓친 수익"보다 더 나쁜 것은 "잘못된 상태에서 체결된 주문"이다.

## 11. 현재 시스템을 계속 개선할 가치가 있는 이유

이 시스템은 아직 완성도가 낮은 부분이 많지만, 폐기보다 개선이 맞는 이유가 있다.

1. US PathB라는 실제 수익축이 있다.
2. 수익축이 어떤 close reason에서 나오는지 식별된다.
3. KR 손실도 시장/전략/단계별로 분리 가능하다.
4. Claude가 직접 주문하지 않는 안전한 구조다.
5. broker truth 우선 원칙이 있다.
6. lifecycle/event/audit/log가 충분히 쌓여 있다.
7. 보호 계약이 문서화되어 있다.
8. 개선 backlog가 이미 data/code evidence와 연결되어 있다.

즉, 지금 필요한 것은 새 시스템이 아니라 "이미 돈을 버는 부분은 보호하고, 측정과 원인 분해를 더 강하게 만드는 것"이다.

## 12. 계속 살릴 구조

반드시 유지할 것:

| 구조 | 이유 |
|---|---|
| US PathB `claude_price` | 현재 핵심 수익축 |
| `CLOSED_CLAUDE_PRICE_TARGET` | 강한 수익 실현 경로 |
| `CLOSED_CLAUDE_PRICE_PRE_CLOSE` | 장마감 리스크를 수익으로 닫는 경로 |
| `CLOSED_PROFIT_LADDER` | peak giveback 제한 |
| broker truth fail-closed | 상태 오염 방지 |
| `RouteDecision` | PathA/PathB/action traceability |
| candidate audit/event store | 개선 근거 |
| sizing reason split | 주문 실패 원인 분리 |
| AUTO_SELL_REVIEW cooldown | Claude 반복 호출 방지 |

## 13. 긍정적 결론

이 시스템은 아직 불안정하고 복잡하지만, 핵심 설계 방향은 좋다. 특히 "AI 판단을 주문 권한과 분리하고, broker truth와 risk gate를 우선하며, PathB price plan을 lifecycle로 추적한다"는 점은 유지할 가치가 크다.

가장 좋은 부분은 이미 숫자로도 보인다.

- US PathB `claude_price`: 117건, 승률 50.43%, 평균 +0.747%.
- US `CLOSED_CLAUDE_PRICE_TARGET`: 10건, 승률 100%, 평균 +5.311%.
- US `CLOSED_CLAUDE_PRICE_PRE_CLOSE`: 34건, 평균 +1.442%.
- US `CLOSED_PROFIT_LADDER`: 20건, 평균 +1.097%.

따라서 이 시스템의 긍정적 방향은 명확하다.

> US PathB 수익 엔진은 보호하고, KR과 learning/reporting/operational truth를 단계적으로 보강하면 된다.

지금은 무리하게 새 전략을 붙일 때가 아니라, 이미 작동하는 수익 구조를 망가뜨리지 않으면서 실패 원인을 더 잘 보이게 만들 때다.
