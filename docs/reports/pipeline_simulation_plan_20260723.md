# 전면 파이프라인 시뮬레이션 설계 (2026-07-23 착수용)

운영자 지시: **단편 분석이 아니라 다양한 종목 × 다양한 시나리오를 US/KR 모두 돌려
전체 문제점과 개선점을 한 번에 도출한다. Claude 판단이 필요하면 API가 아니라 코드로
재현해서 결론까지 낸다.**

## 0. 왜 이 방식이어야 하나

2026-07-22 하루에 파이프라인 누수 3건이 나왔는데 **전부 실전에서만 드러났다.**
코드 grep으로는 "배선이 있다"로 보이고, 한 건씩 파고들어야 겨우 잡혔다.

```
rel_vol       계산 O → 원장 저장 X → 랭킹이 거래량을 못 봄
BUY_READY     judge 판정 O → evidence 결측으로 강등 → 배선 미매칭 → 매수 0
코어 sleeve   주문 O → strategy 빈 값 → 성과 추적 불가
```

한 건씩 잡으면 다음 누수는 또 실전에서 돈을 잃고 나서야 보인다.
**대량 케이스를 통과시켜 어디서 몇 %가 죽는지를 매트릭스로 뽑아야 한다.**

## 1. 시뮬레이션 대상 (시드)

### A. 실패 케이스 — 이미 원장에 실값이 남아 있다
| 케이스 | 시드 | 기대 |
|---|---|---|
| DELL 2026-07-22 23:41 (conf 0.72) | route/evidence 실값 | 강등 재현 → 무엇을 고치면 통과하는지 |
| DELL 2026-07-22 23:53 (conf 0.76) | 동일, rel_vol 2.77 근거 포함 | 필드 이원화 영향 격리 |
| WDC 2026-07-21 (BUY_READY 2건) | 어제 regime 누락으로 사멸 | 수정 후 통과 확인(회귀) |

### B. "매수가 아쉬웠던" 케이스 — 사후에 좋았던 미진입 건
- `logs/funnel/intraday_entry_shadow_*.jsonl`의 `would_entry_price` 보유 건
- 조건: 이후 MFE >= 3% 도달했는데 진입 안 된 건
- US/KR 각각 상위 30~50건

### C. 정상 통과 케이스 — 대조군
- 실제 filled → closed 된 건(US 255 / KR 62)
- 이들이 지금 파이프라인을 다시 통과하는지(회귀 검증)

## 2. 통과시킬 파이프라인 단계

각 케이스를 아래 순서로 흘리고 **단계별 통과/차단 사유**를 기록한다.

```
[1] 후보 생성      screener → universe 필터 → history 필터(anti-chase 등)
[2] 프롬프트 진입   trainer 점수 → prompt_rank → hard_cap
[3] judge 판정     _immediate_buy_allowed(게이트) → 프롬프트 노출 → 판정
[4] evidence       live_evidence_pack → data_state → action_ceiling
[5] route          action_routing → final_action / route 문자열
[6] 진입 배선      run_entry_scan → _buy_ready_immediate_enabled → route 매칭
[7] 안전 게이트     affordability / 포지션 상한 / 일일 상한 / 국면 게이트
[8] 주문           kis_api 주문 → 체결
[9] 청산           risk_manager 후보 → Claude 검토 → CLOSED_* 매핑
```

★ [3] judge는 **API를 호출하지 않는다.** 원장에 남은 과거 판정을 재생하거나,
   판정 규칙(RVOL/ORB/추격 조건)을 코드로 근사해 결론을 낸다.

## 3. 산출물 — 매트릭스

```
행: 케이스(종목 × 세션 × 시나리오)
열: 파이프라인 9단계
값: 통과 / 차단(사유코드)

집계:
  - 단계별 생존율 (US/KR 분리)
  - 차단 사유 빈도 top-N
  - "고치면 몇 건이 살아나는가" — 단일 필드 결측 해소 시 통과 증가분
```

오늘 수동으로 뽑은 게 정확히 이 형태였고 끊긴 지점이 한 번에 나왔다:
```
[생성] judge BUY_READY 2건
[저장] trade_ready US=['DELL']
[소비] route="PlanA.probe" ≠ "PlanA.buy"   ← 끊김
[결과] 진입 0
```

## 4. 우선 검증할 가설 (오늘 발견에서 도출)

| # | 가설 | 검증 방법 |
|---|---|---|
| H1 | `volume_ratio_open` 결측만 해소하면 강등 상당수가 풀린다 | 결측 필드를 하나씩 채워 ceiling 재계산 |
| H2 | `time_normalized_rvol`을 evidence가 인정하면 H1이 즉시 해결된다 | 필드 이원화 통합 시뮬 |
| H3 | route가 `PlanA.probe`여도 즉시매수가 가능해야 한다(강등≠금지) | 배선 조건 완화 시뮬 |
| H4 | KR은 evidence가 아니라 Claude 판정에서 막힌다 | KR ceiling BUY_READY 2,809건의 후속 추적 |
| H5 | 랭킹에 rel_vol을 넣으면 judge가 고RVOL을 먼저 본다 | 랭킹 재정렬 후 judge 노출 순서 재현 |

## 5. 주의 (오늘 배운 것)

- **세션 단위로 볼 것.** 거래 단위 개선이 세션 단위에서 소멸한 사례가 오늘만 13건.
- **외부 데이터로 내부 계산값을 검증하지 말 것.** 소스가 다르면 애초에 안 맞는다.
- **평균 뒤 분포를 볼 것.** 꼬리가 결론을 뒤집는다.
- **지표 정의를 먼저 확인할 것.** `net_return` 합계 vs `nav` 기준처럼 함정이 있다.
- 시뮬은 전부 **읽기 전용**으로. 라이브 상태·주문에 영향을 주지 않는다.

## 6. 기존 도구 재사용

```
tools/pipeline_integrity_audit.py       4축 감사 — 이걸 시뮬 하네스로 확장
tools/shadow_observation_review.py      shadow 관측 즉시 판정
tools/early_path_our_net_validation.py  초기경로 검증 + 출구 차등화 시뮬
tools/judge_capacity_drop_counterfactual.py  반사실(꼬리·비대칭 축)
tools/measure_actual_fees.py            실측 수수료(캐시 토큰)
```
