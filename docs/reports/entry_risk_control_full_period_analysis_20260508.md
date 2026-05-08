# Entry Risk Control Full-Period Analysis - 2026-05-08

## Summary

이번 분석은 최근 2일치 한국장만 보고 내린 판단이 전체 로컬 데이터에서도 유지되는지 확인하기 위해 진행했다.

핵심 결론은 다음과 같다.

- Claude는 관심종목 발굴에는 유효하다.
- KR에서는 `watchlist` 품질은 좋지만 `trade_ready`를 바로 신규 진입으로 연결하는 것은 위험 신호가 있다.
- US `claude_price`는 현재 표본에서 유효하므로 차단 대상이 아니다.
- KR `claude_price`, KR `continuation`, US `broker_sync` 리스크는 전체 데이터에서도 방어 필요성이 확인된다.
- 일일 신규 진입 cap은 수익 창출 장치라기보다 손실 확산 방지 장치로 보는 것이 맞다.
- PlanA MFE breakeven은 효과 후보가 있으나, 현재 기록상 1순위 방어책은 아니다.

근거 강도는 항목별로 다르다. KR `claude_price`는 청산 11건으로 상대적으로 신뢰할 만하지만, KR `trade_ready` 매칭 3건, KR `continuation` 3건, MFE breakeven 후보 3건은 강한 결론이 아니라 위험 신호와 실험 가설로 봐야 한다.

## Why This Was Analyzed

직전 분석은 `2026-05-07 KR`과 `2026-05-08 KR` 중심이었다. 이 범위만 보면 특정 장세나 당일 운영 이슈에 결론이 과하게 끌릴 수 있다.

따라서 이번에는 로컬에 남아 있는 전체 분석 가능 데이터를 기준으로 다음 질문을 검증했다.

1. KR `claude_price` 신규 진입 차단이 단기 표본만의 착시인지
2. KR `continuation` 차단이 실제 손실 데이터로도 정당한지
3. Claude 프롬프트의 `watchlist`와 `trade_ready` 품질이 어떻게 다른지
4. US와 KR을 같은 기준으로 막아도 되는지
5. 일일 신규 진입 cap, MFE breakeven, broker quarantine이 실제로 어떤 역할을 하는지

## Data Scope

분석 기준 시각: `2026-05-08 16:39 KST`

| 데이터 | 범위 | 용도 |
|---|---:|---|
| `logs/daily_judgment/*.json` | 403개, `2024-10-01 ~ 2026-05-08` | 장 판단 장기 분포 확인 |
| `logs/daily_judgment/live_*.json` | 27개, `2026-04-20 ~ 2026-05-08` | live Claude 판단/선정 품질 확인 |
| `state/preopen_*.json` | 10개 | 후보군의 장중 사후 성과 확인 |
| `state/live_decisions.jsonl` | 207건 | 실제 진입/청산/전략별 손익 확인 |
| `logs/funnel/*.jsonl` | 32개 | gate, funnel, 실행 가능성 차단 원인 확인 |
| `logs/analysis/live_analysis_*.jsonl` | 18개 | runtime skip/reason 분포 확인 |

주의할 점:

- 2024~2025 `daily_judgment`는 historical/paper 성격이 많아 실제 실행 품질 분석에는 제한적으로만 사용했다.
- 후보/진입/성과까지 연결 가능한 데이터는 주로 `2026-04-20` 이후 live 기록이다.
- `preopen` 성과 데이터는 KR 4일, US 5일 수준이라 방향성 검증용으로 봐야 한다.

## Evidence Strength

| 항목 | 표본 | 근거 강도 | 해석 |
|---|---:|---|---|
| KR `claude_price` 청산 성과 | 11건 | 중간 | 평균 -2.532%, PF 0.094로 신규 진입 축소/차단 근거가 비교적 명확하다. |
| KR `momentum` 청산 성과 | 27건 | 중간 | 평균 -0.373%, PF 0.794로 손실 기여가 크다. 다만 `claude_price`보다 구조가 덜 훼손되어 즉시 차단보다 별도 튜닝 대상이다. |
| KR `trade_ready` preopen 매칭 | 3건 | 약함 | 즉시 진입 위험 신호는 있으나, 이 표본만으로 출력 계약 변경을 확정하기에는 부족하다. |
| KR `continuation` 청산 성과 | 3건 | 약함 | 전패지만 장세/시간대 편중 가능성이 있어 "전략 폐기"가 아니라 임시 차단 후 shadow 검증이 맞다. |
| US `claude_price` 청산 성과 | 20건 | 중간 | 평균 +0.245%, PF 1.354로 차단 근거가 없다. |
| US `broker_sync` 청산 성과 | 4건 | 약함+운영상 강함 | 표본은 작지만 broker trust 문제는 손익 이전의 운영 안정성 이슈라 신규 진입 차단 근거가 있다. |
| MFE breakeven 후보 | 3건 | 약함 | 적용 후보는 있으나 효과는 실험 후 재측정해야 한다. |

## Method

분석은 데이터 성격별로 분리했다.

1. `daily_judgment` 전체 기간에서 시장 판단 모드와 손익 분포를 확인했다.
2. `live_*.json`의 `tickers`, `trade_ready_tickers`, `selection_meta`를 추출했다.
3. `preopen_*.json` 후보 성과와 live Claude 선정 결과를 ticker 기준으로 교차 매칭했다.
4. `live_decisions.jsonl`에서 실제 entry/closed 기록을 전략별로 집계했다.
5. `gate_evaluation_*.jsonl`과 `candidate_funnel_snapshot_*.jsonl`에서 Claude 판단이 runtime에서 왜 걸러졌는지 확인했다.
6. 일일 cap 효과는 같은 날 entry 순서와 closed 기록을 근사 매칭해 추정했다.
7. MFE breakeven 후보는 `position_mfe_pct >= 2.5`이고 최종 `pnl_pct <= 0`인 청산 기록으로 추렸다.

## Preopen Candidate Result

### KR

| 구분 | 표본 | 유효 | 평균 최종수익률 | 승률 | 평균 MFE | 평균 MAE |
|---|---:|---:|---:|---:|---:|---:|
| 전체 후보 | 185 | 176 | -0.786% | 37.5% | +5.947% | -5.132% |
| preopen top15 | 51 | 43 | -2.711% | 27.9% | +4.091% | -6.523% |
| Claude watch 매칭 | 20 | 20 | +3.276% | 65.0% | +12.269% | -4.563% |
| Claude trade_ready 매칭 | 3 | 3 | -3.080% | 33.3% | +5.463% | -7.412% |

KR에서 가장 중요한 결과는 `watchlist`와 `trade_ready`의 품질이 반대로 나온다는 점이다.

Claude가 고른 watch 후보는 전체 후보보다 훨씬 좋았다. 평균 최종수익률, 승률, MFE 모두 개선됐다. 반면 `trade_ready`는 표본은 작지만 평균 최종수익률이 음수이고 MAE가 더 나빴다.

즉 KR에서는 Claude를 "좋은 종목을 찾는 분석가"로 쓰는 것은 유효하지만, "지금 바로 매수 가능한 실행자"로 쓰는 것은 현재 표본에서 위험 신호가 있다. 다만 KR `trade_ready` 매칭은 3건뿐이므로 이 결론은 확정이 아니라 보수적 운영 가설로 둔다.

### US

| 구분 | 표본 | 유효 | 평균 최종수익률 | 승률 | 평균 MFE | 평균 MAE |
|---|---:|---:|---:|---:|---:|---:|
| 전체 후보 | 181 | 150 | -0.615% | 40.0% | +3.590% | -3.554% |
| preopen top15 | 75 | 60 | -0.696% | 38.3% | +5.037% | -4.437% |
| Claude watch 매칭 | 33 | 33 | +0.728% | 51.5% | +5.796% | -3.407% |
| Claude trade_ready 매칭 | 5 | 5 | +6.558% | 100.0% | +12.183% | -0.198% |

US는 KR과 다르다. 현재 표본에서는 Claude `trade_ready`가 유효했다. 특히 `US claude_price`는 실제 청산 성과에서도 양호하게 나온다.

따라서 KR에서 성과가 나쁘다는 이유로 US `claude_price`까지 같이 막으면 안 된다.

## Live Decision Result

`state/live_decisions.jsonl` 기준:

- 전체 기록: 207건
- 신규 진입: 55건
- 청산: 81건

### Strategy Performance

| 시장 | 전략 | 청산 수 | 평균 PnL | 합산 손익 | 승률 | PF |
|---|---:|---:|---:|---:|---:|---:|
| KR | `claude_price` | 11 | -2.532% | -36,719원 | 18.2% | 0.094 |
| KR | `continuation` | 3 | -6.920% | -5,614원 | 0.0% | 0.000 |
| KR | `momentum` | 27 | -0.373% | -31,988원 | 33.3% | 0.794 |
| KR | `gap_pullback` | 2 | -0.378% | -509원 | 50.0% | 0.033 |
| US | `claude_price` | 20 | +0.245% | +25,757원 | 45.0% | 1.354 |
| US | `broker_sync` | 4 | -3.590% | -29,588원 | 0.0% | 0.000 |
| US | `gap_pullback` | 6 | -0.469% | -10,981원 | 33.3% | 0.743 |
| US | `momentum` | 4 | +1.707% | -2,104원 | 50.0% | 5.333 |
| US | `mean_reversion` | 2 | +0.284% | +536원 | 100.0% | 999.000 |

이 결과는 현재 개발 계획의 방향을 지지한다.

- KR `claude_price` 신규 진입 차단은 타당하다.
- KR `continuation` 신규 진입 차단은 임시 방어 조치로 타당하다. 다만 표본이 3건뿐이므로 shadow 관찰을 반드시 유지해야 한다.
- US `broker_sync`는 broker truth/trust 문제가 있을 때 강하게 막아야 한다.
- US `claude_price`는 현재 데이터상 차단 대상이 아니다.

추가로 KR `momentum`은 별도 검토 대상이다. 청산 27건, 합산 손실 -31,988원으로 KR 손실 기여가 크다. 다만 평균 손실과 PF는 KR `claude_price`보다 덜 나쁘고, 많은 손실이 `intraday_review_sell`과 trailing/profit-floor 경로에서 발생한다. 따라서 이번 작은 방어 패치에서는 즉시 차단하지 않고, 다음 단계에서 entry 조건/ATR cap/청산 타이밍을 별도로 분석하는 것이 맞다.

## Gate And Funnel Result

`gate_evaluation` 기준:

| 시장 | 평가 수 | 통과 수 | 통과율 |
|---|---:|---:|---:|
| KR | 203 | 123 | 60.6% |
| US | 120 | 0 | 0.0% |

상위 차단 원인:

| 시장 | blocker | 횟수 |
|---|---|---:|
| US | `judgment_not_executable` | 105 |
| KR | `judgment_not_executable` | 48 |
| KR | `candidate_quarantine` | 22 |
| US | `off_list_action` | 15 |
| KR | `off_list_action` | 10 |

이 결과는 프롬프트 품질 문제가 "종목을 못 찾는다"가 아니라 "실행 가능 상태를 충분히 구분하지 못한다"는 쪽임을 보여준다.

Claude가 후보를 내도 runtime은 `judgment_not_executable`로 막는 경우가 많다. 따라서 프롬프트에는 종목 매력도뿐 아니라 실행 가능성 계약이 들어가야 한다.

단, US gate 통과율 0.0%는 이상값이다. 정상 운영 중의 순수 후보 품질 문제일 수도 있지만, 비운영 시간대, off-list action, 특정 gate 설정, 또는 당시 US 실행 비활성 상태가 섞였을 가능성이 있다. 따라서 US gate 0%는 "프롬프트 계약 개선 필요"의 보조 근거로만 사용하고, 독립 결론으로 쓰면 안 된다.

필요한 필드:

- `or_formed`
- `momentum_signal_now`
- `gap_pullback_valid`
- `same_day_block`
- `affordable`
- `atr_cap`
- `pending_order`
- `already_holding`
- `strategy_enabled`
- `broker_trust_level`

## Daily Entry Cap Simulation

같은 날 신규 진입 순서와 청산 기록을 근사 매칭해 cap 효과를 추정했다. 이 값은 정확한 백테스트가 아니라 방향성 확인용이다.

### KR

| 조건 | 표본 | 평균 PnL | 승률 | PF |
|---|---:|---:|---:|---:|
| 전체 | 30 | -1.962% | 26.7% | 0.103 |
| cap 1 | 10 | -1.850% | 40.0% | 0.164 |
| cap 2 | 18 | -2.808% | 22.2% | 0.067 |
| cap 3 | 24 | -2.315% | 20.8% | 0.066 |
| cap 4 | 27 | -2.085% | 25.9% | 0.106 |
| cap 5 | 29 | -2.003% | 27.6% | 0.104 |

이전 좁은 표본에서는 `cap1`이 매우 좋아 보였지만, 전체 live 기록 기준으로는 수익 전략이라기보다 손실 노출을 줄이는 장치로 해석하는 것이 맞다.

cap 수치별 PF 비교는 신뢰도가 낮다. entry와 closed를 근사 매칭했기 때문에 cap2가 cap1보다 나빠지는 역전 현상이 실제 전략 효과인지, 매칭 한계인지 분리할 수 없다. 따라서 이 표에서는 "cap을 낮추면 노출 건수가 줄어 손실 확산을 제한한다"는 방향성만 사용하고, cap1/cap2/cap3 중 어느 값이 최적인지는 별도 재현 백테스트로 봐야 한다.

### US

| 조건 | 표본 | 평균 PnL | 승률 | PF |
|---|---:|---:|---:|---:|
| 전체 | 7 | -1.332% | 28.6% | 0.085 |
| cap 1 | 6 | -1.536% | 33.3% | 0.086 |
| cap 2 | 7 | -1.332% | 28.6% | 0.085 |

US cap 효과는 현재 표본에서 뚜렷하지 않다. US는 cap보다 broker trust와 전략별 허용/차단이 더 중요해 보인다.

## MFE Breakeven Review

조건:

- `position_mfe_pct >= 2.5`
- 최종 `pnl_pct <= 0`
- broker_sync 제외

후보:

| 시장/전략 | 건수 | 현재 손익 | 평균 최종 PnL | 평균 MFE |
|---|---:|---:|---:|---:|
| KR `claude_price` | 1 | -7,191원 | -3.648% | +2.679% |
| KR `momentum` | 1 | -1,938원 | -0.992% | +3.792% |
| US `momentum` | 1 | -238원 | -0.111% | +3.162% |

MFE breakeven은 손실 전환을 줄일 수 있는 후보가 있으나, 현재 기록상 표본은 작다. 또한 과거 청산 기록에는 MFE 값이 비어 있는 경우가 많아 실제 효과는 과소집계됐을 수 있다.

결론적으로 MFE breakeven은 넣을 만하지만, KR `claude_price`/`continuation` 차단이나 US broker quarantine보다 우선순위는 낮다.

## Prompt Quality Finding

프롬프트 길이 자체는 과하지 않다.

| 시장 | live 일수 | 평균 prompt 길이 | 평균 watchlist | 평균 trade_ready |
|---|---:|---:|---:|---:|
| KR | 13 | 3,218자 | 11.5개 | 2.5개 |
| US | 14 | 4,956자 | 11.8개 | 2.6개 |

문제는 길이가 아니라 출력 계약이다.

현재 Claude는 종목의 매력도를 보고 `trade_ready`를 주는 경향이 있다. 하지만 runtime 입장에서는 지금 매수 가능한지 확인해야 하는 조건이 더 많다.

KR에서는 특히 다음 구조가 필요하다.

1. `watch_candidate`: 관심종목
2. `setup_ready`: 구조는 좋지만 아직 진입 조건 대기
3. `execution_ready`: 현재 runtime 조건까지 통과한 실제 매수 가능 상태

현재 KR에서는 `watch_candidate` 품질은 좋지만, `execution_ready` 판단 품질이 약하다.

이 3단계 분리는 확정 요구사항이 아니라 다음 실험 가설이다. 출력 형식 변경은 파싱 안정성, 기존 `selection_meta` 호환성, 대시보드 표시, runtime routing에 영향을 준다. 따라서 바로 강제 적용하기보다 shadow 필드로 먼저 추가해 기존 `trade_ready`와 비교하는 방식이 안전하다.

## Development Implications

이번 전체 기간 분석을 기준으로 현재 개발 계획은 다음처럼 해석한다.

### Strongly Supported

1. KR PathB `claude_price` 신규 진입 차단
   - KR `claude_price`: 평균 -2.532%, PF 0.094
   - 기존 보유 포지션 청산은 유지해야 한다.

2. US broker trust quarantine
   - US `broker_sync`: 평균 -3.590%, PF 0.000
   - 성과 표본은 작지만 broker truth/trust 문제는 운영 리스크이므로 trust가 `degraded` 또는 `untrusted`이면 신규 매수는 막는 것이 타당하다.

3. 시장별 일일 신규 진입 cap 분리
   - KR에서는 cap이 수익 전략은 아니지만 손실 확산 방지 장치로 의미가 있다.
   - KR/US 성격이 다르므로 전역 cap 하나로 묶으면 안 된다.

### Moderately Supported

1. KR PlanA `continuation` 신규 진입 임시 차단
   - KR `continuation`: 3건 전패, 평균 -6.920%
   - 표본이 작으므로 "전략 폐기"가 아니라 신규 진입 임시 차단 + shadow logging 유지가 맞다.

2. PlanA MFE breakeven
   - 효과 후보는 확인됐다.
   - 다만 표본이 작고 MFE 누락 기록이 많다.
   - 적용 후 별도 funnel/log로 효과를 다시 검증해야 한다.

3. KR `momentum` 별도 튜닝 검토
   - KR `momentum`: 27건, 합산 손실 -31,988원
   - 당장 차단하기보다는 ATR cap, 진입 시간대, intraday review 청산 품질을 별도 리포트로 분해해야 한다.

### Experimental / Hypothesis

1. Claude `trade_ready` 의미 축소
   - KR에서는 `trade_ready`를 즉시 주문 신호로 쓰면 위험하다.
   - 다만 KR preopen 매칭 표본이 3건이므로 `execution_ready` 분리는 shadow 실험부터 시작하는 것이 맞다.

### Not Supported

1. US `claude_price` 차단
   - US `claude_price`: 평균 +0.245%, PF 1.354
   - 현재 전체 live 기록에서는 차단 근거가 없다.

2. 후보군 확장 우선 개발
   - watchlist 후보 발굴은 이미 상대적으로 좋다.
   - 지금 병목은 후보 부족이 아니라 실행 전환 품질과 손실 방어다.

## Recommended Priority

1. KR `claude_price` 신규 진입 차단 유지/검증
2. US broker trust `degraded/untrusted` 신규 매수 차단
3. KR `continuation` 신규 진입 임시 차단 + shadow 검증
4. `KR_DAILY_ENTRY_CAP`, `US_DAILY_ENTRY_CAP` 시장별 분리
5. PlanA MFE breakeven 추가
6. KR `momentum` 손실 구조 별도 분석
7. 프롬프트 출력 계약을 `watch_candidate/setup_ready/execution_ready` shadow 필드로 실험
8. gate 결과를 다음 Claude 프롬프트에 피드백

## Limitations

- `preopen` 성과 파일은 최근 며칠치만 있어 표본이 작다.
- KR `trade_ready`, KR `continuation`, MFE breakeven은 각각 n=3 수준이므로 강한 결론으로 쓰면 안 된다.
- 일부 과거 live 청산 기록에는 `position_mfe_pct`가 비어 있어 MFE breakeven 효과가 과소집계될 수 있다.
- 일일 cap 분석은 entry와 closed 기록을 근사 매칭한 것이므로 정확한 재현 백테스트가 아니다. cap별 순위 비교보다 노출 축소 방향성만 참고해야 한다.
- US gate 통과율 0.0%는 비운영/설정 상태가 섞였을 가능성이 있어 별도 원인 분석 전까지는 이상값으로 취급해야 한다.
- 2024~2025 `daily_judgment`는 historical/paper 성격이 많아 실제 live 실행성과와 직접 비교하면 안 된다.

## Final Conclusion

전체 기간으로 넓혀도 결론은 바뀌지 않는다. 다만 의미가 더 명확해졌다.

KR의 문제는 Claude가 종목을 못 찾는 것이 아니다. Claude가 찾은 watch 후보는 오히려 좋다. 다만 그 후보를 너무 빨리 `trade_ready`로 승격해 실제 신규 진입으로 연결하는 구조에는 위험 신호가 있다. 이 판단은 현재 표본이 작으므로 확정 결론이 아니라 보수적 운영 가설로 둔다.

따라서 현재 작업은 후보 확장보다 신규 진입 방어가 우선이다. KR `claude_price`, US broker trust quarantine, 시장별 cap 분리는 데이터와 운영 리스크 양쪽에서 지지된다. KR `continuation` 차단과 PlanA MFE breakeven은 표본 한계가 있으므로 임시 방어/실험 기능으로 적용하고, 적용 후 효과를 별도로 추적해야 한다. KR `momentum`은 손실 기여가 커서 다음 분석 대상으로 분리한다.
