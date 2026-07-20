# 수익 청사진 v2 — 4광맥 발굴 종합 (2026-07-21)

작성: Claude. 병렬 4에이전트 발굴(숨은 흑자포켓·시간축·미탐색 DB/원장·LOSS_CAP 분해) 종합.
원칙: 부정 진단으로 끝내지 않고 **모든 발견을 개선안·수익경로로** 맺는다([[ai-behavior-improve-not-abandon-20260721]]).
재현 스크립트: scratchpad(edge_hunt·time_axis_edge·losscap_decomp·mine) + docs/reports/verify_20260719/lever_validation_full.py.

## 0. 한 줄
할 게 없는 게 아니다. 복잡성을 다 파니 **대표본(candidate_audit 876k) 근거의 뉴스 촉매 신호 + 무후회 anti-chase 상향 + 장초반 진입 + TARGET capture 확대** 4개의 실행 가능한 개선이 나왔다. 예측이 아니라 관리·선별·타이밍으로 수익을 낸다.

## 1. ★정정 (사실대로)
- **"TSMOM sleeve = 유일 검증 흑자구조"는 실물 원장 근거 없음.** tsmom_sleeve_*·sleeve_forward_labels = 2026-07-08 day-0 착수만, forward 성과 0건. 근거는 과거 백테스트/토론. → 장기 볼록트랙은 "검증됨"이 아니라 **"관측 시작·축적 필요"**. 살아있는 실물=core_shadow_mtm 1주(US arm net+ 초기신호, KR arm net−, nav버그 의심).

## 2. 발굴된 개선안 (전부 수익경로로)

### A. [무후회·즉시] anti-chase 임계 20→25%
- 근거: LOSS_CAP 분해 — MAX≥25% 배제 시 LOSS_CAP 19건(−54) 제거, **TARGET 흑자 0건 손실**(흑자군 최대스파이크 24.5%), 전체 net +87. 20~25 구간은 wash(LC−21 vs TARGET+19.7).
- 액션: 현재 라이브 `ANTI_CHASE_MAX_THRESHOLD=20` → **25**. 순개선, TARGET 무손실. **가장 확실한 즉시 레버.**

### B. [대표본·강함] 뉴스 촉매 진입 선별
- 근거: candidate_audit 876k — no_news 코호트가 **양 시장·양 horizon 최악**(US3d news −0.33 vs no_news −4.40, KR3d −1.75 vs −6.20). 단조·일관.
- 액션: no_news 후보 강등 + news>0 가점(기존 catalyst 레인 강화). 소스=audit_candidate_rows.news_in_prompt. shadow→forward→enforce.
- ★이게 **KR·US 공통 유일 강한 진입 신호** — 우리가 못 찾던 selection 엣지의 후보.

### C. [깨끗·양시장] 장초반 30분 진입 집중
- 근거: 시간축 — first_30m +0.15 vs after_30m −0.37, KR·US 동일 방향(역인과 아님, 출구와 독립).
- 액션: 진입을 개장 30분에 집중(soft gate 반대 방향 검토). shadow 관찰.

### D. [이익엔진 확대] TARGET 볼록출구 capture
- 근거: TARGET +5.07%·승률100%·합계+137(전체 이익). 시간 줄수록 커짐(멀티데이 +5.78). 단 전체 9%에서만 발생.
- 액션: (a) TARGET 향하는 것 빨리 반납 말고 시간 허용, (b) LOSS_CAP 최대덩어리(mfe≥2% 반납 31건 −81)가 여기 = 출구 capture로만 공략(진입필터 불가). **mfe_time 백필이 열쇠.**

### E. [손실원 축소] CAUTIOUS·momentum 진입 축소
- 근거: CAUTIOUS 37건 net −29(TARGET 0), momentum 24건 −21(TARGET 0), MILD_BEAR −0.75.
- 액션: 방어국면·momentum 진입 빈도↓. gap_pullback은 이미 차단(중복).

### F. [씨앗·관측] KR measured 흑자 + core_shadow_mtm 축적
- 근거: KR measured net +0.948(t=1.25, 백필착시 제거 후 유일 생존 진입측 흑자). core_shadow_mtm US arm net+.
- 액션: forward 관측 지속. KR은 "풀을 사라"가 아니라 선별(뉴스·volume_surge). nav버그 수정 후 arm 판정.

## 3. 장기/단기 분리
- **단기(당일)**: A(anti-chase 25) + C(장초반 진입) + E(나쁜장/momentum 축소) + B(뉴스 선별). churn −141 손실원 차단.
- **장기(멀티데이/코어)**: D(TARGET 시간허용) + F(core_shadow_mtm 축적, US 볼록arm). 멀티데이 +1.67% 트랙.

## 4. 실행 우선순위
| 순위 | 개선 | 근거강도 | 상태 | net영향 |
|---|---|---|---|---|
| 1 | **anti-chase 20→25** | 확실(무후회) | 즉시 config | +87 상당 |
| 2 | **뉴스 no_news 강등** | 대표본 강함 | shadow | 미측정, 최대 잠재 |
| 3 | CAUTIOUS·momentum 축소 | 확실 | shadow | +50 상당 |
| 4 | 장초반 30분 진입 | 깨끗·소표본 | shadow | +0.5%p/건 |
| 5 | mfe_time 백필→TARGET capture | 열쇠 | 백필+관측 | −81 덩어리 공략 |
| 6 | KR씨앗·core_mtm 축적 | 미확정 | 관측 | forward |

## 5. 규율
- 표본 2개월·소수셀 → shadow 필수, 반사실≠미래. candidate_audit=gross(우리 net 아님) → 방향단서로만, 우리 net 재확인 후 enforce.
- A(anti-chase 25)만 무후회 확실 — 즉시. B~F는 shadow→forward→운영자 승인→enforce.
- **결론: 예측 없이 관리·선별·타이밍으로 버는 구조. 손실원(LOSS_CAP·나쁜장·no_news) 막고 이익엔진(TARGET) 키우면 흑자.**

관련: [[regime-is-the-edge-not-selection-20260721]] [[profit_blueprint_20260721]] [[creative-strategy-extreme-spike-exclusion-20260719]] [[ai-behavior-improve-not-abandon-20260721]]
