# Profit Trainer v2 — 경로 라벨 개선 검증 (2026-07-10)

상태: **trainer 개선 구현·검증 완료 / shadow 연구 적합 / enforce 미승격**

## 답

수익예측을 빼는 것이 아니다. 기존 1일 종가 trainer가 못한 원인을 바꾸기 위해 경로 기반 trainer v2를 추가했다.

```text
기존: 후보 feature -> 다음 거래일 종가
v2: 후보 feature + 실제 trigger context + entry path
    -> 60분 비용 후 수익 + MAE 제한
```

## 데이터 결합

미래 feature 사용을 막기 위해 counterfactual path의 `known_at`보다 같거나 이전인 후보 snapshot만 사용하고, 허용 시간차를 2분으로 제한했다.

| 시장 | 60분 path 행 | backward 결합 | prompt 포함 usable | 결합률 | 중앙/p95 시간차 |
|---|---:|---:|---:|---:|---:|
| KR | 39,676 | 32,707 | 32,643 | 82.27% | 0초 / 7초 |
| US | 67,419 | 55,755 | 55,684 | 82.59% | 0초 / 15초 |

Nearest join 96%를 사용할 수 있었지만 미래 1~117초 snapshot이 섞일 수 있어 폐기하고 backward-only 82.6%만 사용했다.

## 라벨

Positive path:

```text
outcome_60m_pct - cost_p75 >= +0.25%
AND max_drawdown_60m_pct > -2.5%
```

비용:

- KR 0.21%
- US 0.50%

MFE/MAE에 first-hit 순서가 없으므로 exact triple-barrier라고 부르지 않고 보수적 `path_quality` 라벨로 정의했다.

## Feature

- 기존 후보: price, change, volume, from-high, raw screener score, bucket, strategy, source
- trigger 시점: entry delay, entry-vs-candidate price
- post-open: ret 3/5/10/30m, open volume ratio, VWAP distance, pullback from high
- path: immediate, wait30, wait60, volume surge, VWAP reclaim, OR break, pullback reclaim

제외:

- candidate quality/trainer score
- Claude action/confidence
- 미래 outcome/fill/exit field

## Purged walk-forward 결과

분할:

```text
expanding train -> calibration fit -> independent validation
-> 1-session purge -> next-session test
```

| 시장 | test 창 | test path 행 | validation AUC 범위 | 승격 창 | enforce 허용 |
|---|---:|---:|---:|---:|---:|
| KR | 17 | 20,324 | 0.457~0.694 | 0 | 0 |
| US | 16 | 35,938 | 0.435~0.664 | 0 | 0 |

기존 1일 모델보다 AUC는 분명히 개선됐다. 그러나 보정확률, expected net, uncertainty, OOD, validation net LCB를 동시에 통과한 창은 없었다.

## Rank lane 진단

완전한 out-of-sample test에서 종목-일별 최고 점수 path를 하나만 남기고 raw classifier score 상위 cohort를 측정했다. 이는 어떤 path가 최종 관측됐는지를 이용하는 optimistic ranking ceiling이며 live execution replay는 아니다.

### KR

| cohort | n | 평균 net60 | PF | 95% LCB | top3 제거 | 6월 | 7월 |
|---|---:|---:|---:|---:|---:|---:|---:|
| top 1% | 17 | +0.570% | 1.631 | -0.828% | -0.409% | -0.081% | +1.500% |
| top 5% | 40 | +0.850% | 1.784 | -0.488% | -0.316% | -0.538% | +2.933% |
| top 10% | 73 | +0.388% | 1.391 | -0.453% | -0.249% | -0.352% | +1.510% |

KR에는 상대 rank 분별력이 생겼지만 top3 의존, 6월 음수/7월 양수, LCB 음수라 아직 자본 승격 근거가 아니다.

### US

| cohort | n | 평균 net60 | PF | 95% LCB |
|---|---:|---:|---:|---:|
| top 1% | 16 | +0.119% | 1.174 | -0.799% |
| top 5% | 50 | -0.077% | 0.899 | -0.590% |
| top 10% | 92 | -0.389% | 0.602 | -0.754% |

US는 절대·상대 gate 모두 아직 부족하다.

## 판정

```text
기존 1일 trainer: 폐기 대상이 아니라 baseline/shadow
경로 trainer v2: 분별력 개선 확인
KR rank lane: 연구 지속 가치 있음, shadow만
US rank lane: 재설계 필요
profit enforce: 아직 금지
```

## 다음 개선 계약

1. path별 `profit_evidence`를 결정 시점에 저장해 사후 backfill이 아닌 진짜 forward 표본을 만든다.
2. target-first/stop-first timestamp, fill 가능 여부, 실제 비용을 label에 추가한다.
3. KR은 ranker와 absolute meta-gate를 분리한다.
4. US는 60분보다 30분/close/전략별 horizon을 따로 비교한다.
5. 최소 2개 비중첩 forward regime에서 top3 제거 후 LCB > 0일 때만 PROBE 승격한다.

