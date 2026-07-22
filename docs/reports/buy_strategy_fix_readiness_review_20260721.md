# 매수 전략 수정 필요성 점검 리포트 (2026-07-21)

## 결론

수정이 필요한 항목은 있다. 다만 전부 즉시 enforce로 바꾸는 것이 아니라, 손실이 확인된 권한 경로부터 좁게 수정해야 한다.

우선순위는 다음과 같다.

1. US 뉴스/catalyst 가점은 꺼진 상태를 유지한다. 추가로 재발 방지 계약이 필요하다.
2. KR PathB는 `claude_price` 중심으로 allowlist enforce 검토가 필요하다.
3. US PathB는 비용 후 edge와 시간대 게이트를 강화해야 한다.
4. `profit_evidence`는 아직 과거 evidence 저장이 없어 enforce 전환 근거가 부족하다. 먼저 snapshot 생산/저장 개선이 필요하다.
5. 후보군 reorder와 Claude `BUY_READY` 직접매수 확대는 현재 근거가 부족하므로 금지한다.

## 1. US catalyst/news 가점

### 현재 상태

현재 effective source인 `config/v2_start_config.json` 기준:

```text
US_CATALYST_SCORE_BONUS_ENABLED=false
US_CATALYST_BONUS=10
KR_CATALYST_SCORE_BONUS_ENABLED=true
KR_CATALYST_BONUS=12
```

`config/env_overrides`가 `.env.live`를 덮기 때문에 live 판단은 start config를 기준으로 봐야 한다.

### 데이터 판정

2026-06-08 이후 후보 60분 forward:

| 시장 | 뉴스 bucket | n | avg ret60 | 판정 |
|---|---:|---:|---:|---|
| KR | news_prompt_eligible | 2,236 | +0.2516% | 양호 |
| US | news_prompt_eligible | 9,802 | -0.0211% | 약함 |
| US | news_but_not_eligible | 5,065 | +0.1506% | eligible보다 우수 |

실제 체결 연결:

| 시장 | 뉴스 bucket | n | avg pnl | avg net | win rate |
|---|---:|---:|---:|---:|---:|
| KR | news_in_prompt | 8 | +0.9321% | +0.8945% | 50.00% |
| US | news_in_prompt | 45 | -0.6757% | -0.8666% | 22.22% |

### 판정

US catalyst 가점은 꺼진 것이 맞다. 추가 수정은 `false` 유지와 회귀 방지 테스트/프리플라이트 경고다.

KR catalyst 가점은 실제 체결 표본은 좋지만 n=8로 작고, catalyst shadow review 기준 placebo 대비 우위가 충분하지 않다. 즉시 확대 금지, 유지하더라도 shadow 리포트로 계속 검증해야 한다.

## 2. Catalyst shadow review

도구:

```powershell
python tools\screener_catalyst_shadow_review.py --market US --since 2026-06-08 --horizons 60 --placebo-iters 200
python tools\screener_catalyst_shadow_review.py --market KR --since 2026-06-08 --horizons 60 --placebo-iters 200
```

결과:

| 시장 | catalyst Δ | placebo Δ | 차이 | 판정 |
|---|---:|---:|---:|---|
| US | -0.040%p | -0.068%p | +0.028%p | 착시/불충분 |
| KR | -0.058%p | -0.094%p | +0.036%p | 착시/불충분 |

도구 기준은 catalyst-placebo가 +0.1%p 이상 다국면 지속될 때 enforce 검토다. 현재는 미달이다.

## 3. PathB 전략 allowlist

실제 체결 기준:

| 시장 | route/strategy | n | avg pnl | avg net | 판정 |
|---|---|---:|---:|---:|---|
| KR | path_b / claude_price | 23 | +0.9662% | +0.8930% | 유지 후보 |
| KR | path_b / gap_pullback | 17 | -0.6506% | -0.8606% | 차단/강등 후보 |
| KR | path_b / momentum | 6 | -2.1634% | -2.3734% | 차단 후보 |
| KR | path_b / opening_range_pullback | 5 | -1.6423% | -1.8523% | 차단 후보 |
| US | path_b / claude_price | 208 | +0.1664% | -0.2054% | 비용 후 약함 |
| US | path_b / gap_pullback | 20 | -0.2050% | -0.7050% | 차단/강등 후보 |
| US | path_b / momentum | 11 | +0.6551% | +0.1551% | 표본 작음, probe/shadow |

현재 설정:

```text
KR_PATHB_STRATEGY_FILTER_ENABLED=false
KR_PATHB_STRATEGY_FILTER_SHADOW=true
KR_PATHB_STRATEGY_ALLOWLIST=claude_price
```

### 판정

KR은 allowlist enforce 근거가 충분히 있다. `claude_price` 외 PathB 전략은 손실이 일관된다.

US는 전체 PathB가 비용 후 약하므로 전략 allowlist보다 net hurdle/시간대/뉴스 기반 등록 제한이 먼저다. 다만 US `gap_pullback`은 신규 매수 권한 축소 후보다.

## 4. US 비용 후 edge

실제 체결:

| 시장 | gross avg | net avg | fee round trip |
|---|---:|---:|---:|
| US | +0.1642% | -0.2285% | 약 0.5% |

### 판정

US는 gross edge가 비용을 넘지 못한다. 매수 수를 늘리면 손실이 누적되는 구조다.

수정 방향:

- US 신규매수 expected net hurdle 상향
- reward/risk 기준 강화
- MFE 기대치가 낮은 후보는 micro/probe 또는 abstain
- `profit_evidence`를 경로별로 저장하고 would_block 성과를 검증

## 5. Profit evidence gate

도구:

```powershell
python tools\profit_evidence_db_replay.py --mode shadow --market US
python tools\profit_evidence_db_replay.py --mode shadow --market KR
```

결과:

| 시장 | eligible rows | historical profit evidence rows | would_block rows | baseline 1d mean |
|---|---:|---:|---:|---:|
| US | 3,121 | 0 | 3,121 | -0.4686% |
| KR | 2,511 | 0 | 2,511 | -1.4216% |

### 판정

현재 과거 row에는 저장된 `profit_evidence`가 없어 enforce replay가 의미 있게 작동하지 않는다. 따라서 바로 enforce할 근거가 아니라, evidence snapshot 생산/저장/coverage 개선이 먼저다.

## 6. US 시간대 게이트

US PathB 시간대별 실제 체결:

| UTC hour | n | avg pnl | avg net | 판정 |
|---:|---:|---:|---:|---|
| 13 | 40 | +0.5726% | +0.1040% | 상대적 양호 |
| 14 | 77 | +0.1215% | -0.2058% | 약함 |
| 15 | 52 | +0.2277% | -0.2287% | 약함 |
| 16 | 20 | -0.7327% | -1.1810% | 차단 후보 |
| 18 | 14 | -0.6756% | -0.9669% | 차단 후보 |
| 19 | 9 | +0.7178% | +0.3495% | 표본 작음 |

현재 코드의 `US_MIDDAY_ENTRY_BLOCK_UTC_HOUR`는 단일 hour만 차단한다.

### 판정

16 UTC 차단은 맞다. 18 UTC도 차단 또는 reduce 후보다. 다만 과거/현재 설정 시점 혼재가 있으므로 live 로그에서 현재 차단 작동 여부를 먼저 확인해야 한다.

## 7. Claude BUY_READY / PULLBACK_WAIT

2026-07-01 이후 후보 60분 forward:

| 시장 | action | n | avg ret60 | 판정 |
|---|---:|---:|---:|---|
| US | BUY_READY | 76 | -0.3049% | 직접매수 근거 부족 |
| US | PULLBACK_WAIT | 33 | -0.8023% | PathB 등록 근거 부족 |

### 판정

Claude action은 매수권한이 아니라 intent로 유지해야 한다. `BUY_READY` 즉시매수 확대와 `PULLBACK_WAIT` 무조건 PathB 등록은 금지한다.

## 8. 후보군 reorder

현재:

```text
CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false
```

2026-07-10 이후 trainer sort cap 근사 비교는 평균이 조금 나아 보였지만, 일자별 trainer better rate가 KR/US 모두 42.86%로 과반 미달이었다.

### 판정

전면 true 전환 근거 없음. shadow reorder 이벤트를 누적한 뒤 tail reorder만 검토한다.

## 9. 수정 필요성 최종 판정

| 항목 | 수정 필요 | 권장 조치 |
|---|---|---|
| US catalyst bonus | 이미 수정됨/유지 필요 | `false` 유지, start config와 `.env.live` 일치 확인 |
| US news direct_catalyst 승격 | 필요 | score 66 기본 통과형 뉴스는 매수권한 상승 금지 |
| KR catalyst bonus | 보류 | 유지 가능하나 enforce 근거 약함, shadow review 지속 |
| KR PathB strategy filter | 필요 | `claude_price` allowlist enforce 검토 |
| US PathB gap_pullback | 필요 | 신규매수 권한 축소/차단 검토 |
| US cost/net hurdle | 필요 | expected net/reward-risk 기준 강화 |
| profit evidence | 개선 필요 | enforce 전환보다 snapshot coverage 확보가 먼저 |
| US time gate | 필요 | 16 UTC 차단 확인, 18 UTC reduce/차단 검토 |
| BUY_READY 직접매수 | 수정 금지/확대 금지 | intent로만 사용 |
| prompt reorder | 수정 금지/확대 금지 | false 유지, shadow 누적 |

## 다음 개선 순서

1. US catalyst `false` 상태를 프리플라이트/테스트로 고정한다.
2. US `direct_catalyst score=66`이 PathB 등록/매수권한을 올리지 못하게 한다.
3. KR PathB strategy filter를 `claude_price` 중심으로 enforce하는 변경을 검토한다.
4. US PathB `gap_pullback`과 16/18 UTC 진입을 차단/감액하는 shadow-to-enforce 리포트를 만든다.
5. profit evidence snapshot coverage를 높이고, would_block vs allowed 성과 리포트를 만든다.
