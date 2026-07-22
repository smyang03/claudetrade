# ultimate_profitability_review 검토 메모

작성일: 2026-07-21  
검토 대상:

- `docs/reports/ultimate_profitability_review_20260721.md`
- `docs/reports/loss_cap_entry_selection_20260721.md`
- `docs/reports/us_multiday_convex_track_design_20260721.md`
- `docs/reports/verify_20260721/loss_cap_entry_discrim.py`
- `docs/reports/verify_20260721/loss_cap_rules.py`
- `tools/us_multiday_counterfactual.py`
- `tools/regime_entry_gate_review.py`
- `tools/candidate_consensus_outcome_review.py`

## 1. 총평

다른 AI의 최종 리포트 방향은 대체로 맞다. 특히 다음 3개 결론은 현재 DB 재현 결과와 일치한다.

1. US 손실은 “낙폭 반등 베팅/나쁜 국면/당일 청산”에 크게 묶여 있다.
2. KR과 US는 같은 룰로 다루면 안 된다.
3. US 멀티데이는 “전체 연장”이 아니라 이익 계열 청산만 runner로 분리해야 한다.

다만 몇 가지는 표현을 더 엄격하게 바꾸는 게 좋다.

- `regime gate enforce가 역사상 가장 큰 단일 개선`이라는 표현은 방향은 맞지만, 현재 prospective `regime_entry_gate` 로그는 0행이다. 즉 enforce는 바로 결론내기보다 shadow 안전확인 후 운영자 승인이라는 현재 로드맵 조건을 반드시 유지해야 한다.
- 월별 KRW 손익 표는 현재 DB의 단순 `pnl_krw_net` 집계와 일부 불일치한다. 특히 US 5월 KRW net은 현재 DB에 NULL이 섞여 있어 별도 보정 원장 또는 best-available 계산 기준을 명시해야 한다.
- KR “출구 near-optimal”은 설득력은 있지만, 해당 KR 5일 연장 반사실 재현 스크립트가 `verify_20260721/`에는 없다. 최종 리포트의 중요한 문장이므로 재현 도구를 남기는 편이 맞다.

결론: 리포트는 방향성 채택 가능. 단, 바로 코드 enforce할 항목과 shadow 검증 항목을 분리해야 한다.

## 2. 재현 확인 결과

### 2.1 loss_cap_entry_discrim.py

실행:

```text
python docs/reports/verify_20260721/loss_cap_entry_discrim.py
```

결과는 보조 리포트와 일치했다.

US:

- n=195
- LOSS_CAP=48
- net_sum=-66.5%p
- LOSS_CAP군 ret_5d median=-1.37%
- winner군 ret_5d median=+4.42%
- ret_5d q3만 net +39.5%p
- dist_20d_high q1 net -45.7%p

KR:

- n=22
- net_sum=+11.7%p
- LOSS_CAP군 ret_5d median=+10.27%
- winner군 ret_5d median=-0.55%
- max_daily_ret_21d 상위 구간에서 손실 집중

판단:

- US에는 `ret_5d < -5` dip gate shadow가 타당하다.
- KR에는 US식 dip gate를 적용하면 안 된다.
- KR 손실원은 낙폭 반등이 아니라 급등 추격 쪽이라는 해석이 맞다.

### 2.2 loss_cap_rules.py

실행:

```text
python docs/reports/verify_20260721/loss_cap_rules.py
```

결과는 보조 리포트와 일치했다.

US anti-chase 통과 잔여:

- n=182
- net=-55.8%p
- A `ret_5d<-5` 배제: 배제 n=51, 배제군 net=-42.2%p, 잔존 net=-13.6%p
- A|B: 배제 n=82, 배제군 net=-49.8%p, 잔존 net=-5.9%p
- A|B|C: 배제 n=90, 배제군 net=-56.2%p, 잔존 net=+0.4%p

KR anti-chase 통과 잔여:

- n=13
- net=+30.2%p
- C `dist20h<-17` 배제 시 TARGET net +14.8%p 희생

판단:

- 채택 후보를 A 단독으로 둔 것은 보수적이고 맞다.
- A|B|C는 반사실상 좋아 보이나 과적합/희생 위험이 있어 바로 enforce하면 안 된다.
- KR 적용 금지는 강하게 동의한다.

### 2.3 us_multiday_counterfactual.py

실행:

```text
python tools/us_multiday_counterfactual.py --start 2026-06-01
```

결과는 보조 리포트와 일치했다.

US 6/1 이후 closed 129건:

- 전체 plus5_close mean +1.009%, median -0.162%, pos 49%
- PROFIT_LADDER n=13, plus5_close mean +4.620%, median +6.640%, pos 62%
- CLAUDE_PRICE_TARGET n=12, plus5_close mean +3.085%, median +3.905%, pos 67%
- WEAK_MFE n=6, plus5_close mean -5.341%
- CLAUDE_SELL n=7, plus5_close mean -2.894%
- LOSS_CAP n=36, plus5_close mean +1.862%, median -0.486%

판단:

- “US 멀티데이 연장”은 전체 포지션 보유가 아니라 winner-runner carry로만 해석해야 한다.
- LOSS_CAP 완화 근거로 쓰면 안 된다.
- 이익 계열 일부 수량 runner carry shadow는 타당하다.

### 2.4 regime 관련

실행:

```text
python tools/regime_entry_gate_review.py
```

결과:

```text
regime_entry_gate 기록 없음 — PathB 진입 시도가 쌓이면 채워짐(prospective).
```

즉 `ultimate`의 regime 수치는 현재 prospective shadow 로그 기준으로는 아직 검증된 것이 아니다. 다만 2026-07-19 검증 도구에서는 regime edge 방향이 재현된다.

실행:

```text
python docs/reports/verify_20260719/alpha_hunt.py
python docs/reports/verify_20260719/lever_validation_full.py
```

주요 결과:

- 전체: gross +0.056%, net -0.293%, netKRW -331,635원
- MODERATE_BULL+CAUTIOUS_BEAR만 거래 시: -10,383원, 96건
- 좋은장(MOD_BULL+NEUTRAL): n=127, avg +0.012%, 합계 +1.5%p
- 나쁜장(MILD_BULL+BEAR+CAUT): n=184, avg -0.487%, 합계 -89.6%p

판단:

- regime gate 방향은 타당하다.
- 하지만 `regime_entry_gate`의 forward shadow 로그가 아직 0행이므로, `enforce`는 CAUTIOUS부터 단계 적용해야 한다.
- “나쁜장 3국면 스킵”은 사후 반사실 성격이므로 live order gate 전환 전 prospective 확인이 필요하다.

### 2.5 월별 손익 표

현재 DB 단순 집계:

`v2_learning_performance` 기준:

| 월 | 시장 | closed | net_krw | avg_net |
| --- | --- | ---: | ---: | ---: |
| 2026-05 | KR | 21 | -48,530 | -0.8983% |
| 2026-05 | US | 101 | NULL | +0.4248% |
| 2026-06 | KR | 18 | +46,229 | +1.0667% |
| 2026-06 | US | 130 | -272,467 | -0.6507% |
| 2026-07 | KR | 1 | +4,060 | +0.8299% |
| 2026-07 | US | 3 | -22,647 | -2.5255% |

`ultimate` 표:

- 5월 KR -40,548 / US +36,555
- 6월 KR +47,098 / US -306,645
- 7월 KR +4,060 / US -22,880

판단:

- 월별 방향과 거래 수는 대체로 맞다.
- KRW 금액은 현재 DB 단순 집계와 일부 다르다.
- 차이는 `pnl_krw_net` 결측/보정 원장/coverage 차이 때문으로 보인다.
- 최종 리포트에는 “best available KRW ledger 기준” 같은 기준 설명이 있으면 좋다.

## 3. 내 기존 분석과의 충돌/보완

### 일치하는 부분

- Claude가 고른 종목을 바로 사면 안 된다.
- KR/US 정책은 분리해야 한다.
- US catalyst/news bonus는 off 유지가 맞다.
- US는 당일 churn 구조가 문제다.
- KR은 PathB/Claude-price 또는 막힌 좋은 후보 쪽을 봐야 한다.
- 검증은 live 진입에만 종속되면 너무 느리다. 후보 전체 forward ledger/오프라인 반사실이 필요하다.

### 보완되는 부분

내 이전 리포트는 API 비용과 전략별 net expectancy를 강조했다. `ultimate`는 여기에 다음을 더 명확히 추가한다.

- regime switch가 가장 큰 레버일 가능성
- US는 runner carry로 구조 전환해야 한다는 점
- 규모 문제가 마지막 관문이라는 점
- KR은 US식 멀티데이 carry를 복제하면 안 된다는 점

이 보완은 유효하다.

### 주의할 부분

1. KR `BUY_READY→WATCH` 과차단 해소와 `ultimate`의 “퍼널 수리 배포 완료”는 같은 방향이지만, 아직 forward 실측이 필요하다.
2. US runner carry는 winner 계열에만 적용해야 한다.
3. Regime gate는 통계상 유망하지만 prospective 로그가 아직 없으므로 바로 전면 enforce하면 안 된다.
4. 월별 KRW 실측 표는 재현 기준을 문서화해야 한다.

## 4. 실행 권고

### 바로 유지/채택

- US dip gate `ret_5d < -5`: shadow 유지
- KR dip gate 적용 금지
- US catalyst score bonus off 유지
- CANDIDATE_PROMPT_POOL_REORDER_ENABLED false 유지
- 손실 포지션 멀티데이 연장 금지

### shadow 후 enforce 후보

- Regime gate: CAUTIOUS부터 단계 적용
- US winner-runner carry: PROFIT_LADDER / CLAUDE_PRICE_TARGET 계열 일부 수량
- KR demotion audit: `BUY_READY/PULLBACK_WAIT → WATCH`
- KR PathB/Claude-price allowlist 강화

### 추가해야 할 재현 도구

- KR multiday counterfactual script
  - `ultimate`의 KR 5일 연장 반증은 중요한 결론이므로 `tools/kr_multiday_counterfactual.py` 또는 `tools/us_multiday_counterfactual.py --market KR` 형태로 재현 가능하게 남겨야 한다.

- 월별 손익 표 재현 script
  - `ultimate`의 첫 표는 운영 판단의 기준이므로 `docs/reports/verify_20260721/monthly_net_reconcile.py` 같은 스크립트로 기준 원장을 고정해야 한다.

- regime 사후 반사실과 prospective shadow 분리 리포트
  - 사후 반사실: 이미 유망
  - prospective shadow: 현재 0행
  - 이 둘을 같은 “검증 완료”로 부르면 안 된다.

## 5. 최종 판단

다른 AI의 `ultimate_profitability_review_20260721.md`는 전략 방향으로는 채택 가능하다. 특히 “국면 스위치 + 전환율 + US runner carry + 검증속도 + 규모” 5요소 프레임은 이 시스템의 현재 병목을 잘 잡았다.

다만 실행 순서는 엄격해야 한다.

```text
1. 사후 반사실로 유망한 항목 식별
2. forward/shadow로 같은 방향 재확인
3. 시장별로 제한 enforce
4. net/coverage/규모 침식 확인
5. 그 후 주문단위 상향
```

즉 지금 당장 해야 할 것은 새 아이디어 추가가 아니라, 다음 3개를 재현 가능한 운영 지표로 고정하는 것이다.

1. regime gate가 정말 live forward에서도 손실일을 막는지
2. KR에서 막힌 BUY_READY가 실제로 다음 세션에서도 좋은지
3. US winner runner carry가 수수료/FX 포함 후에도 살아남는지

이 3개가 확인되면 `ultimate`의 로드맵은 실행해도 된다.
