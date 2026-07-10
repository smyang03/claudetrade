# 후보 분별력·Claude 역할·Profit-first Trainer 보완 설계 (2026-07-10)

상태: **설계 및 사후 검증 완료 / 라이브 코드·설정 미변경**

연결 문서: [Gross-Alpha Enforce Engine](./design_enforce_gross_alpha_engine_20260710.md)

## 0. 결론

현재 시스템에 분별력이 완전히 없는 것은 아니다. 그러나 그 분별력은 다음 이유로 라이브 수익으로 연결되지 않는다.

1. `prompt_rank`는 일부 구간에서 상대적으로 상위 후보가 나았지만, 비용 허들을 안정적으로 넘는 절대 엣지가 아니다.
2. `candidate_quality_score`와 `trainer_prompt_score`는 최근에 높을수록 더 나쁜 구간이 반복됐다. 현 점수는 품질보다 **과열·추격 강도**를 보상하고 있을 가능성이 높다.
3. Claude의 `READY` 분별력은 4~5월에는 있었으나 6~7월에는 통계적으로 유의하게 역전됐다. 영구적으로 무능한 것이 아니라 **정책/시장 드리프트를 감지하고 권한을 회수하는 장치가 없었다.**
4. Claude의 시장 해석과 보유 조언은 참고 신호로는 남길 가치가 있지만, 현 증거로는 진입 크기나 청산의 최종 소유자가 될 수 없다.
5. 기존 trainer는 후보 점수 생성에 집중되어 있고, 실제로 필요한 `take/skip`, 비용 조정 확률, 기권, 시간 순서 라벨, purged walk-forward, calibration, drift kill-switch, champion/challenger가 빠져 있다.

따라서 Profit-first 구조는 아래와 같이 고정한다.

```text
규칙 기반 Primary 후보 생성
  -> 시점 고정(point-in-time) feature snapshot
  -> 전략별 Meta-labeler: TAKE / SKIP
  -> 확률 보정 + 불확실성/드리프트 검사
  -> ABSTAIN / BLOCK / PROBE / STANDARD / PRESS
  -> 비용 포함 alpha hurdle
  -> 결정론적 주문/체결
  -> 결정론적 손실방어·목표청산
  -> Claude는 가격계획·예외비평·정보구조화만 수행
  -> broker truth 기반 순수익 및 반사실 결과 저장
```

Claude는 **주식 예언자나 포트폴리오 매니저가 아니라, 구조화된 연구자·가격계획자·위험 비평가**로 제한한다. 자본 배분 권한은 재현 가능한 수치 모델과 강제 규칙에 둔다.

---

## 1. 이번 검토 범위와 데이터 주의사항

### 사용한 내부 자료

- `data/audit/candidate_audit.db`
  - `audit_candidate_rows`: 202,694행
  - 2026-04-20~2026-07-10, 60거래일, 2,184종목
  - prompt rank, 실제 prompt 포함 여부, Claude action, trainer score/tier/state, quality score, 실행 연결 정보 포함
  - outcome label: 30분 49,865건, 60분 49,873건, 1일 145,079건, 2일 137,680건, 3일 130,505건
- `data/ml/decisions.db`
  - 2026-07-07까지 실현 종료 315건
  - 거래별 gross/net, 시장, 전략, 종료 사유 검토
- 기존 선택·진입·보유 어드바이저 진단 도구와 보고서

### 해석 한계

- 후보 행은 동일 종목·동일 일자에 여러 번 생성되므로 행 단위 결과는 독립 표본이 아니다. 따라서 종목-일 최초 prompt 기준도 별도로 대조했다.
- 미거래 후보의 1일 수익률은 체결 가능한 실제 거래 수익이 아니다. limit fill, MFE/MAE 순서, 비용, 장중 stop 여부가 반영되지 않은 **분별력 진단용 proxy**다.
- `pnl_krw_net`가 203개 종료 거래에서 비어 있어 완전한 계좌 equity curve와 FX 포함 포트폴리오 수익률은 아직 계산할 수 없다.
- 아래 결과는 라이브 설정 변경 근거가 아니라, shadow/challenger와 전진 검증을 설계하는 근거다.

---

## 2. 후보군 분별력 감사

## 2.1 현재 quality score는 단조성이 없다

실제 prompt 포함 후보를 `candidate_quality_score` 5분위로 나누고 1일 후 수익을 비교하면, 최고 점수군 Q5가 반복적으로 최악이었다.

| 시장·월 | Q1 | Q2 | Q3 | Q4 | Q5(최고점) |
|---|---:|---:|---:|---:|---:|
| KR 6월 | -1.471% | -1.217% | -0.804% | -0.672% | **-1.991%** |
| KR 7월 | -2.408% | -2.457% | -1.099% | -2.098% | **-6.611%** |
| US 6월 | +0.092% | +0.147% | +0.522% | -0.677% | **-0.146%** |
| US 7월 | +0.664% | -1.340% | -0.665% | -1.936% | **-5.393%** |

종목-일 최초 prompt만 남겨 중복을 줄여도 Q5 역전은 유지됐다.

- KR 6월: Q4 -0.176%가 최선, Q5 -1.768%
- KR 7월: Q5 -4.828%
- US 6월: Q3 +0.050%가 최선, Q5 -0.873%
- US 7월: Q1 -0.380%가 최선, Q4 -4.406%, Q5 -3.726%

`trainer_prompt_score`도 유사하게 비단조 또는 역전됐다. 즉 현재 점수의 숫자 크기는 확률이나 기대수익으로 해석할 수 없다.

**판정:** 현 quality/trainer 점수는 live reorder, sizing, promotion에 사용하지 않는다. 저장과 shadow 비교만 유지한다.

## 2.2 prompt rank에는 약한 상대 분별력이 있으나 비용을 이기지 못한다

US 6월 1일 후 수익:

- rank 1~5: +0.256%
- rank 6~10: +0.239%
- rank 11~20: -0.329%
- rank 21+: +0.024%

상위 rank의 상대 우위는 보이지만 기존 US gross hurdle 약 0.75%보다 낮다. 7월에는 모든 rank 구간이 음수였다. KR도 상위가 하위보다 덜 나쁜 정도였으며 절대 수익은 음수였다.

**판정:** rank는 Claude에게 보여 줄 후보의 우선순위일 뿐, alpha나 주문 허가가 아니다.

## 2.3 Claude READY는 월별로 방향이 뒤집혔다

월별 bootstrap 검증에서 US READY-vs-pool spread는 다음과 같이 변했다.

- 4월 1일: +1.55%p, 95% CI +0.50~+2.72 — 유효
- 5월 1일: +1.38%p, 95% CI +0.68~+2.09 — 유효
- 6월 1일: **-2.49%p**, 95% CI -3.35~-1.65 — 유의한 역분별
- 7월 1일: **-4.34%p**, 95% CI -5.65~-2.91 — 유의한 역분별
- US 3일도 6월 -8.53%p로 유의한 역분별

행 단위 1일 수익에서도 `BUY_READY`는 일관되게 우월하지 않았다.

| 시장·월 | 상대적으로 나은 행동 | BUY_READY |
|---|---:|---:|
| KR 6월 | PULLBACK_WAIT -0.449% | -1.422% |
| KR 7월 | AVOID +0.726% | -2.045% |
| US 6월 | PULLBACK_WAIT +0.419% | -1.036% |
| US 7월 | PULLBACK_WAIT -1.127% | **-4.672%** |

이 결과는 Claude를 영구 폐기하라는 뜻이 아니다. READY 정책의 alpha가 시간에 따라 깨졌는데도 이를 자동 중단하지 않은 것이 구조적 결함이다.

**판정:** Claude READY는 독립 challenger로 내리고, 최근 전진 구간의 순수익 하한과 ready spread가 모두 통과할 때만 `PROBE` 후보를 제안할 수 있다. 단독 `STANDARD/PRESS` 승격 권한은 없다.

## 2.4 진입 직후 gross drift와 최종 net 변환은 다르다

격리된 진입 결과에서는 US Claude 진입 152건이 60분 +0.336%, EOD +0.910%로 단기 gross drift를 보였다. 그러나 전체 US 실현 253건은 gross 합 +42.147%에서 net 합 -57.814%로 전환됐다.

KR gap은 격리 진입에서 60분 +0.977%, EOD +1.429%였지만 실현 net bucket은 음수였다. 이는 후보가 전부 잘못이라기보다 다음 변환층이 alpha를 소모할 수 있음을 뜻한다.

- 진입 가격과 limit fill 품질
- stop/target 경로와 MFE-before-MAE 순서
- 보유 시간
- 세금·수수료·FX
- 동일 이벤트에서의 중복 노출

**판정:** trainer label을 단순 1일 수익률로 만들면 안 된다. 실제 stop/target/시간 장벽과 비용을 재현한 `tradable net label`이 필요하다.

## 2.5 실제 코드 배선 감사

현 설정과 코드를 대조하면 점수와 Claude 판단의 영향은 다음처럼 연결되어 있다.

| 배선 | 현재 상태 | 실제 영향 | 판단 |
|---|---|---|---|
| `CANDIDATE_PROMPT_POOL_REORDER_ENABLED` | true | trainer state/score로 prompt pool 순서와 cap 내 포함 후보가 바뀜 | **live 간접 자본 영향 있음** |
| duplicate 후보 병합 | trainer score 높은 행 승리 | Claude가 받는 대표 feature가 바뀜 | score 재구축 전 raw-rank 우선 권고 |
| sub-screener triage | trainer/quality score 내림차순 | 재검색 때 추가할 후보가 바뀜 | shadow 또는 규칙 rank로 복귀 |
| `CANDIDATE_QUALITY_TRAINER_PROMPT_HINT_ENABLED` | true | Claude prompt에 state, q, risk가 노출됨 | anchoring 위험, A/B hide 필요 |
| `ENABLE_KR_CANDIDATE_QUALITY_PROMPT` | true | KR prompt에 quality/RS/flow 정보 노출 | 원시 feature는 유지, 종합 q 숫자는 숨김 |
| `KR_CANDIDATE_POST_RANK_ENFORCE` | false | KR post-rank는 현재 shadow | 유지, 승격 금지 |
| `TRADE_READY_PRIORITY_SORT_ENABLED` | true | Claude confidence로 READY 후보의 slot 우선순위 결정 | live 직접 영향, 검증 전 비활성 설계 |
| `PATHB_READY_BOOST_MULT` | 1.0 | 현재 READY로 인한 주문금액 증액 없음 | 그대로 유지 |
| `REQUIRE_TRADE_READY_KR/US` | false | Path B 진입에 READY가 필수 조건은 아님 | 역할 분리에 유리, 유지 |

따라서 현 quality/trainer score가 주문금액을 직접 키우지는 않지만, **누가 Claude에게 보이고 누가 READY slot을 차지하는지**를 바꾸므로 이미 live 선택 편향을 만든다. 첫 조치는 점수 계산기를 삭제하는 것이 아니라, live prompt reorder/triage/종합점수 hint에서 분리해 동일 후보군의 shadow challenger로 돌리는 것이다.

---

## 3. Claude의 명확한 역할과 권한

| 기능 | Claude 역할 | 최종 소유자 | 현재 결정 |
|---|---|---|---|
| 전체 후보 랭킹 | challenger·설명 | 수치 ranker + enforce gate | live 자본 권한 제거 |
| 종목별 진입가/목표/무효화 가격 | 구조화된 계획 생성 | deterministic validator | 유지, A/B로 증분가치 측정 |
| 시장 모드 해석 | 문맥 feature·반대 논리 | regime engine | size 직접 변경 금지 |
| READY/AVOID | 연구용 제안 | meta-labeler + hurdle | PROBE 제안만 가능 |
| 보유 판단 | 예외 비평·뉴스/이벤트 구조화 | deterministic target/stop/time exit | 강제 HOLD/SELL 권한 제거 |
| 손실 제한 | 관여하지 않음 | risk engine | 절대 규칙 |
| 포트폴리오 크기 | 관여하지 않음 | calibrated sizing engine | 권한 없음 |
| 사후 분석 | hard-negative 설명, 가설 생성 | trainer pipeline | 적극 활용 |

### Claude가 잘할 가능성이 높은 일

1. 비정형 뉴스·공시·시장 문맥을 구조화된 feature로 변환
2. 종목별 entry zone, target, invalidation을 일관된 JSON 계약으로 생성
3. 기존 규칙이 놓친 반대 시나리오와 tail event를 지적
4. 고득점 손실·READY 손실의 공통 서사를 hard-negative tag로 분류
5. 모델 변경 전후의 실패 사례를 사람이 읽을 수 있게 요약

### Claude가 맡으면 안 되는 일

1. 자기 확신 점수를 승률로 간주해 size를 결정
2. 서로 다른 종목을 한 prompt 안에서 최종 자본 순위로 확정
3. 손실 중인 포지션에 재량 HOLD를 강제
4. 검증 없이 market mode 하나로 전략을 켜고 끄기
5. 사후 가격을 본 설명을 당시 의사결정 feature로 되돌려 쓰기

---

## 4. 다른 퀀트 트레이너 방식과 현재 누락 요소

### 4.1 Primary signal + Meta-labeling

Meta-labeling은 기존 전략이 방향과 후보를 만들고, 두 번째 모델이 거짓 양성을 걸러 `take/skip/size`를 담당한다. 이는 모든 주식의 미래수익을 맞히려는 현재의 단일 quality score보다 이 시스템에 잘 맞는다.

적용:

- primary: `claude_price`, ORP, gap, momentum 등 기존 setup
- meta: 이 setup을 **지금 이 시장·가격·비용에서 거래할 것인가**
- 시장·전략별 모델을 분리하거나 최소한 strategy interaction을 둔다.
- primary가 구조적으로 음수인 전략은 meta-model에 맡기지 않고 먼저 BLOCK한다.

### 4.2 Triple-barrier와 경로 순서 라벨

라벨은 미래 종가가 아니라 실제 거래 계약과 같아야 한다.

```text
upper = 실제 target 또는 전략별 목표 장벽
lower = 실제 stop 또는 loss-cap 장벽
vertical = 전략별 최대 보유 시간

positive:
  fill 가능
  AND upper가 lower보다 먼저 도달
  AND 예상/실현 net_return > alpha_hurdle

negative:
  lower가 먼저 도달
  OR 시간 만료 후 net hurdle 미달
  OR 미체결/추격 체결/데이터 불확실
```

MFE와 MAE의 숫자만 저장하는 것이 아니라 어느 장벽이 먼저 발생했는지와 시간까지 저장한다.

### 4.3 Learning-to-rank와 절대 alpha gate의 분리

cross-sectional trainer는 같은 시각의 후보끼리 순위를 학습해야 한다. 그러나 rank가 좋아도 전 후보가 음수일 수 있으므로 다음 두 검증을 동시에 요구한다.

- 상대 목표: session 내 NDCG, pairwise accuracy, top-k lift
- 절대 목표: top-k의 비용 조정 평균 순수익과 PF, downside LCB

rank 모델은 **무엇을 먼저 볼지** 정하고, meta-labeler와 hurdle은 **거래할지** 정한다.

### 4.4 Probability calibration + Selective prediction

높은 score를 곧바로 높은 확률로 쓰지 않는다. rolling validation에서 isotonic 또는 Platt calibration을 적용하고 다음을 본다.

- Brier score
- log loss
- reliability curve
- ECE 또는 calibration test
- risk-coverage curve/AURC

불확실하거나 학습 분포 밖이면 `ABSTAIN`한다. 거래 시스템에서는 거래하지 않음이 무료에 가까운 합법적 행동이므로 coverage 100%를 목표로 삼지 않는다.

### 4.5 Purged walk-forward + embargo

시간이 겹치는 label을 random split하면 미래 경로가 학습에 새어든다.

- train → validation → test를 시간순으로 고정
- 1일/3일 label 기간이 test와 겹치는 train sample purge
- 경계에 embargo
- 동일 종목-일/이벤트는 같은 fold
- 월별·regime별 성능을 별도로 보고 최악 구간도 통과
- 여러 후보 모델을 탐색했다면 PBO/deflated performance를 함께 점검

### 4.6 Champion / Challenger / Shadow

- champion: 현재 검증된 deterministic policy
- challenger: 새 meta-labeler, 새 Claude prompt, 새 score
- shadow: 주문 없이 동일 후보·동일 시각의 결정과 반사실 결과 기록
- 승격: 사전에 정한 기간/거래 수/하한 통과 후에만 PROBE
- 정책 버전, feature schema, 데이터 snapshot, 모델 hash를 의사결정마다 저장

### 4.7 Concept drift와 자동 권한 회수

이번 US READY의 4~5월 양호, 6~7월 역전은 전형적인 운영 drift 문제다.

필수 모니터:

- feature PSI/분포 변화
- score 분위별 monotonicity
- ready-vs-pool spread
- top-k lift
- calibration drift
- market·strategy별 net LCB

어느 모델도 영구 승인하지 않는다. 조건을 벗어나면 자동으로 shadow로 강등하고 deterministic fallback으로 복귀한다.

### 4.8 Hard-negative mining

현재 가장 가치 있는 학습 표본은 일반 손실이 아니라 다음이다.

- quality Q5인데 큰 손실
- Claude BUY_READY인데 stop 선도달
- 높은 confidence인데 비용 후 음수
- PULLBACK_WAIT가 맞았는데 추격 진입한 사례
- gross 양수였지만 net 음수로 전환된 사례

이들을 `chase`, `late_entry`, `regime_mismatch`, `event_gap`, `stop_overshoot`, `cost_fail`, `exit_conversion_fail` 등으로 분리한다. Claude는 이 태깅과 반대 논리 생성에 활용하되, 최종 label은 가격·broker truth로 확정한다.

### 4.9 Off-policy 평가와 제한적 탐색

미거래 후보의 진짜 체결 결과는 관측할 수 없으므로 단순 사후 수익 비교에는 selection bias가 있다.

향후 탐색 정책:

- enforce gate를 통과한 불확실 후보 중 5~10%를 소액 PROBE로 무작위 배정
- 각 결정의 선택확률(propensity)을 로그에 저장
- IPS/DR/SWITCH 계열로 새 정책의 반사실 가치를 추정
- propensity가 없는 과거 로그에는 가짜 off-policy 정밀도를 주장하지 않는다.

현재 표본과 시뮬레이터 정확도에서는 end-to-end RL을 사용하지 않는다. 먼저 contextual meta-decision과 deterministic execution을 완성한다.

---

## 5. 목표 아키텍처

## 5.1 책임 분리

```text
[A. Universe/Primary]
시장별 유동성·가격·이벤트 규칙
기존 setup이 방향과 가격 후보 생성
        |
        v
[B. Point-in-time Snapshot]
결정 시각 feature만 불변 저장
데이터 신선도·출처·누락 플래그 포함
        |
        v
[C. Relative Ranker]
동일 session 내 검토 순서
자본 권한 없음
        |
        v
[D. Strategy Meta-labeler]
P(target-before-stop), expected net, expected MAE, time-to-event
        |
        v
[E. Calibration / OOD / Drift]
확률 보정, 기권, 자동 강등
        |
        v
[F. Enforce Gate]
BLOCK / PROBE / STANDARD / PRESS
alpha hurdle + 포트폴리오 한도
        |
        v
[G. Execution]
deterministic limit/timeout/slippage 규칙
        |
        v
[H. Exit]
target/stop/time/tail 결정론적 소유
Claude는 예외 비평만 제출
        |
        v
[I. Truth & Trainer]
broker fill + 비용 + FX + 장벽 순서 + untraded outcome
```

## 5.2 모델 출력 계약

단일 `quality_score`를 다음 구조로 대체한다.

```json
{
  "policy_version": "meta_us_claude_price_v1",
  "decision_ts": "point-in-time timestamp",
  "market": "US",
  "strategy": "claude_price",
  "p_target_before_stop_raw": 0.64,
  "p_target_before_stop_calibrated": 0.57,
  "expected_gross_pct": 1.10,
  "expected_cost_pct_p75": 0.78,
  "expected_net_pct": 0.32,
  "expected_mae_pct": -0.65,
  "uncertainty": 0.19,
  "ood": false,
  "drift_state": "healthy",
  "action": "PROBE",
  "reason_codes": ["PULLBACK_FILL", "BREADTH_FOLLOWTHROUGH"],
  "propensity": 0.07
}
```

`expected_net_pct > 0`만으로는 부족하다. 기존 enforce 문서의 비용 p75와 안전 마진을 넘고, 불확실성 하한에서도 양수여야 한다.

## 5.3 학습 목적함수

초기에는 복잡한 딥러닝보다 재현 가능한 logistic/GAM/gradient boosting baseline을 우선한다.

```text
utility
= realized_net_return
- lambda_dd * adverse_excursion
- lambda_tail * stop_overshoot
- lambda_turnover * turnover_cost
```

분류 label, expected net regression, MAE regression을 multi-head 또는 독립 모델로 만들고 합의 gate를 쓴다. 단일 모델의 confidence에 모든 책임을 몰지 않는다.

---

## 6. Enforce 정책 보완안

### 즉시 설계상 고정

1. 기존 quality/trainer score는 live reorder와 sizing에서 제외한다.
2. Claude BUY_READY만으로 승격하지 않는다.
3. prompt rank는 화면/prompt 노출 순서로만 쓴다.
4. `ABSTAIN`을 정상 행동으로 추가한다.
5. KR/US와 strategy를 섞은 하나의 성능 숫자로 승인하지 않는다.
6. 모든 승인 성능은 gross가 아니라 비용·세금·FX·slippage 포함 net으로 판정한다.
7. deterministic target/stop/time exit가 기본 소유자다.

### 모델 상태 머신

```text
SHADOW
  -> PROBE: 최소 표본 + forward net LCB > 0 + calibration 통과
  -> STANDARD: 독립 forward 구간 2개 + 비용 후 PF/Drawdown 통과
  -> PRESS: 충분한 거래 수 + tail 제거 후에도 양수 + capacity 통과

어느 상태에서든
  drift / inverse lift / calibration failure / loss budget breach
  -> SHADOW 또는 BLOCK
```

### 자동 kill 조건 초안

다음 값은 운영 데이터 분포에 맞춰 사전 등록 후 고정한다.

- rolling event window 2개 연속 `ready/top-k spread < 0`
- score Q5가 Q1보다 나쁜 현상이 2개 window 연속
- calibrated ECE > 0.10 또는 calibration slope가 0.5~1.5 밖
- bootstrap 95% 순수익 하한 <= 0
- 비용 p75 + 안전마진 미달
- OOD 비율 급증 또는 feature freshness 실패
- stop overshoot/tail loss budget 초과

표본이 부족한 것은 통과가 아니라 `ABSTAIN/SHADOW`다.

---

## 7. 검증 프로토콜

## 7.1 오프라인

1. point-in-time snapshot 재구성 테스트
2. fill 가능성 및 barrier 순서 라벨 테스트
3. market × strategy × month purged walk-forward
4. rank metric과 absolute net metric 동시 평가
5. score 분위 단조성
6. calibration 및 risk-coverage
7. top 3 이익 제거, 최악 3 손실 제거, event-day equal weight 민감도
8. 비용 p50/p75/p90, FX shock, slippage shock
9. policy/model/prompt 버전별 재현성

## 7.2 Shadow forward

최소 보고 단위:

- 후보 수 / 거래 제안 수 / coverage / abstain율
- fill 수와 미체결 수
- target-first / stop-first / time-expiry
- gross, 비용 항목별, net
- net PF, median, downside, max drawdown
- ready-vs-pool 및 top-k spread
- Brier/ECE/calibration plot
- 전략·시장·regime별 표본 수와 confidence interval

## 7.3 승격 기준

정확한 숫자는 계좌 규모와 손실 예산으로 확정해야 하지만 방향은 다음과 같다.

- 거래 적중률이 아니라 **net expectancy 하한 > 0**
- 비용 후 PF > 1이며 tail 3개 제거 후에도 양수
- 두 개 이상의 비중첩 forward window에서 같은 방향
- calibration 통과
- 기존 champion 대비 drawdown 또는 net의 명시적 개선
- `pnl_krw_net` 결측을 먼저 해소해 계좌 단위 결과로 재검증

---

## 8. 구현 순서

### Phase 0 — 계측 신뢰성

- `pnl_krw_net` 203건 결측 원인과 FX ledger 보완
- 후보 snapshot의 immutable key와 decision timestamp 고정
- fill, target/stop 순서, 비용 breakdown, propensity schema 추가 설계

### Phase 1 — 권한 분리

- quality score/live sizing 연결 해제 여부 확인
- Claude role contract와 deterministic validator 정의
- ABSTAIN 및 model state machine 추가
- strategy/market별 kill-switch dashboard

### Phase 2 — Baseline trainer

- `claude_price`처럼 상대적으로 근거가 있는 primary부터 시작
- cost-aware triple-barrier label builder
- 단순 logistic/boosting baseline + calibration
- purged walk-forward report 자동화

### Phase 3 — Challenger와 탐색

- current policy, ranker-only, meta-labeler, Claude-critic A/B shadow
- 제한적 무작위 PROBE와 propensity logging
- hard-negative mining loop

### Phase 4 — 자본 승격

- PROBE부터 시작
- loss budget 내에서만 STANDARD
- PRESS는 tail 제거 후 성과와 계좌 단위 drawdown이 검증될 때만 허용

---

## 9. 최종 판단

현재 구조의 가장 큰 누락은 더 많은 전략 후보가 아니다. **점수가 틀릴 때 스스로 기권하고, 성과가 뒤집힐 때 권한을 자동 회수하며, 후보의 상대 순위와 비용 후 거래 가능성을 분리하는 trainer 운영체계**다.

현재 후보군은 완전 무작위가 아니지만, 그 약한 상대 분별력을 alpha로 과대평가했다. 특히 최고 quality 점수와 Claude BUY_READY가 최근 손실을 집중시켰다. 따라서 다음 수익 엔진은 후보를 많이 맞히는 시스템이 아니라 아래 원칙을 지켜야 한다.

> 좋은 후보를 전부 찾으려 하지 않는다. 비용을 이길 가능성이 보정된 소수만 거래하고, 나머지는 자신 있게 버린다.

Claude의 역할도 이 원칙에 맞춰 명확해진다. Claude는 비정형 정보와 가격 계획, 실패 설명에서 사용하고, 확률·사이징·손실 제한은 수치 모델과 enforce 규칙이 소유한다.

---

## 10. 외부 1차 자료

- Jacques Joubert, [Meta-Labeling: Theory and Framework](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4032018) — primary signal 위의 take/skip/size 계층
- Daniel Poh et al., [Building Cross-Sectional Systematic Strategies By Learning to Rank](https://arxiv.org/abs/2012.07149) — cross-sectional ranking 목적
- Aditya Gangrade et al., [Selective Classification via One-Sided Prediction](https://proceedings.mlr.press/v130/gangrade21a.html) — 오류와 coverage 사이의 기권 설계
- Yonatan Geifman, Ran El-Yaniv, [SelectiveNet](https://proceedings.mlr.press/v97/geifman19a) — reject option과 risk-coverage
- Alberto García-Galindo et al., [Conformal Risk Control for Selective Prediction](https://proceedings.mlr.press/v230/garcia-galindo24a.html) — abstention/coverage에서의 오류 통제
- Miroslav Dudík et al., [Doubly Robust Policy Evaluation and Learning](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/double_robust.pdf) — propensity가 있는 로그의 off-policy 평가
- Yu-Xiang Wang et al., [Optimal and Adaptive Off-policy Evaluation in Contextual Bandits](https://proceedings.mlr.press/v70/wang17a.html) — IPS/DR/SWITCH의 bias-variance 문제
- David H. Bailey et al., [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) — 반복 백테스트 선택 편향과 CSCV/PBO
- Xiao-Yang Liu et al., [FinRL-Meta](https://arxiv.org/abs/2112.06753) — 데이터 처리, 시장 환경, 전략 계층 분리와 재현 가능한 벤치마크
- Xiao-Yang Liu et al., [Qlib](https://arxiv.org/abs/2009.11189) — 데이터·모델·평가·workflow를 분리한 AI quant 연구 인프라
