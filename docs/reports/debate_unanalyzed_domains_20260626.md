# 토론 리포트 — 미분석 도메인 전수 분석 + 발견 검증 (2026-06-26)

> 운영자 지시: 전체 시스템 미분석 항목 점검 → 알파/개선/수정/보존 발견 → 항목별 토론.
> 방식: 4개 병렬 분석 sweep → PRO(발견 실재)/CON(소표본·아티팩트) 토론 → 사회자 직접 net 구성 검증. READ-ONLY.

---

## 1. 분석 sweep 발견 맵

| 도메인 | 실측 | 성격 |
|---|---|---|
| CLOSED_USER_MANUAL | −68.1%p(broker 포함, N=39), 04말 클러스터, 전부 learning_allowed=0 | catch-all fallback 라벨 결함(attribution) |
| AUDITED_BROKER_SELL | 05-27 단일 추적공백 N=5 | 운영(모니터링 공백) |
| Claude 가격플랜 RR | 선언 RR~2.0, TARGET 청산 8%, RR tier net 비단조(고RR 음수), MFE/계획 0.17 | 구조(RR 장식적) |
| US 0~30분 soft gate | avg +0.238% **but median −0.545%·win38%·단일 outlier 의존** | 기각 |
| 정적 리스크 캡 | gross_exposure +13%·risk_off +6.6%·reentry +8.2% (N=11~12, proxy) | 누수 의심(약) |
| 애널리스트 합의 모드 | MODERATE_BULL 최악·CAUTIOUS 최선 (역상관) | 비레버 확인 |
| param tuner/lesson_validation | 447세션/6진입·교훈 1개 월별반전 | 무효과(닫기) |
| INVALID_PRICE | pathb:4280만 signal.price fallback 누락 (N=3) | 코드 비대칭(소수정) |

---

## 2. 사회자 직접 net 구성 검증 (판정 기반)

decisions.db v2_learning_performance, live closed, net(measured+backfill):

| 버킷 | N | net 합 | avg |
|---|---:|---:|---:|
| **전체** | 310 | **−80.2%p** | −0.259% |
| legacy(manual+broker) | 39 | −68.1 | −1.745% |
| **loss_cap (좌측꼬리)** | 74 | **−193.6** | −2.617% |
| **TARGET (우측꼬리)** | 27 | **+138.5** | **+5.131%** |
| legacy 제외 | 271 | **−12.1 (near breakeven)** | −0.045% |
| **legacy+loss_cap 제외** | 197 | **+181.5 (강한 양수)** | +0.921% |
| CLEAN US | 222 | +5.1 (flat) | +0.023% |
| CLEAN KR | 49 | −17.2 (음수) | −0.351% |

→ **시스템 net = loss_cap(−193.6) vs TARGET(+138.5)의 싸움.** legacy(−68)는 실손이나 헤드라인을 부풀리는 2차 요인(학습제외). **좌측꼬리·legacy 둘 다 빼면 핵심 엔진은 +181.5 강한 양수.**

---

## 3. 명제별 판정

### 명제1 — "헤드라인 적자 = attribution 아티팩트" → **부분 찬성(과대표시는 사실, 단 near-breakeven)**
- legacy 제외 시 −80.2 → **−12.1 (near breakeven)** — 헤드라인이 legacy로 부풀려진 건 사실. attribution 분리표기 정당.
- **단 "양수 전환" 아님:** US CLEAN flat(+5.1), KR CLEAN 여전히 음수(−17.2). 적자의 진짜 driver는 attribution(legacy 19% of 손실)이 아니라 **loss_cap 좌측꼬리(56%)** — 이미 알던 것(A1 처리중).
- **수정:** legacy 버킷 헤드라인 분리표기 + "복구 후 near-breakeven, CLEAN 양수는 US 5월 편중"으로 정직 보고. **새 레버 아님.**

### 명제2 — "가격플랜 target 과대, 좁혀라" → **좁히기 기각, RR 장식적은 인정**
- **TARGET이 우측꼬리 본체**(+138.5, avg +5.13%, 전 버킷 최고). 좁히면 +5%짜리를 ladder(+0.26%)로 강등 = 우측꼬리 절단(직전 capture 토론과 동일 결론).
- **RR 게이트는 net 무예측(장식적)은 실측 사실** — 단 그건 target 결함이 아니라 selection 무엣지 재확인.
- PRE_CLOSE(+0.67%)·ladder가 target 미도달분을 소폭 양수로 회수 = 안전판 작동.
- **수정:** target폭 **보존**. "1차 부분익절을 실현 MFE 분포에 더 가깝게"만 shadow 관찰 여지(양측 PRO 수정안) — 단 ladder 이중수확 충돌 주의.

### 명제3 — "US 0~30분 soft gate 완화(alpha)" → **기각**
- avg +0.188%가 **단일 outlier(+11.34) 100% 의존** — 빼면 음수(−1.82). median −0.545%, win 38%, trim → 음수.
- 완화 = net 음수에 사이즈↑(직전 자본 스케일업과 동일 함정). **soft gate ×0.5 유지.**

---

## 4. 종합 — 전체 sweep의 메타 결론

미분석 도메인을 전수로 팠으나, 결론은 **다시 같은 곳**으로 수렴한다:
- **net 레버 = loss_cap 좌측꼬리(−193.6, 이미 A1 78% 처리) vs TARGET 우측꼬리(+138.5, 보존).** 핵심 엔진(둘 제외)은 +181.5 양수.
- 새 도메인들은 (a) 헤드라인을 부풀리는 attribution 아티팩트(legacy), (b) **보존 대상**(TARGET·PRE_CLOSE/ladder·sharp_reversal 게이트), (c) 기각(soft gate·target 좁히기·conviction·모드)뿐.

**살아남은 실행 항목 (작음, 위생/소수정):**
| # | 항목 | 형태 |
|---|---|---|
| 1 | legacy 버킷 attribution 분리표기 + 청산사유 보존 파이프라인 | 측정/위생 |
| 2 | INVALID_PRICE — pathb:4280 `signal.price` fallback 정합화(코드 일관성, ROI 아님) | 소수정(shadow 계측 후) |
| 3 | param tuner/lesson_validation **닫기**(447세션/6진입, 무효과 — 관찰만) | 닫기 |
| 4 | 정적 리스크 캡(gross_exposure/risk_off/reentry) 승자 차단 — N=11~12 약함 | shadow 측정만(행동변경 X) |

**보존(살려야 할 것):** TARGET 우측꼬리(+138.5), PRE_CLOSE/ladder 안전판, sharp_reversal 게이트(N=349 손실 정확 차단), CLEAN 핵심 엔진(+181.5 ex-tail).

**기각:** soft gate 완화, target 좁히기, 자본 스케일업, conviction/모드 신호, 스크리너 소스 축소.

---

## 5. 한 줄 결론
미분석 도메인 전수 분석 끝 — **새 알파는 없다.** 헤드라인 −80%p는 legacy 아티팩트(−68)로 부풀려졌고(제외 시 near-breakeven), 시스템 net의 진짜 축은 **loss_cap 좌측꼬리(−193.6, A1 처리중) vs TARGET 우측꼬리(+138.5, 보존)**다. 미분석 항목에서 나온 건 알파가 아니라 **위생(attribution 분리)·소수정(INVALID_PRICE)·닫기(param tuner)·보존(우측꼬리·안전판)**이다. target 좁히기·soft gate 완화는 둘 다 우측꼬리/outlier를 끊어 net을 악화시킨다(기각).
