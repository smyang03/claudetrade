# 월요일(8/3) 준비 완료 보고 — 전체 재구성 1단계

2026-08-01(토). 운영자 결정 실행: "모든 매수를 급락 반등 레인에 집중, 기존 방식 폐기, 코어 보유분만 유지."

---

## 1. 오늘 실행 완료된 것 (전부 실측 검증됨)

### 1-1. 기존 매수 전면 차단 — 3중 방벽 (봇 PID 7544 effective-config 실측)
| 방벽 | 값 | 효과 |
|---|---|---|
| `LEGACY_NEW_BUY_DISABLED=true` (신설) | safety_gate 최상단 차단 | PathA·PathB·전략신호 **모든 표준 신규매수** 차단. 매도·보유관리 무영향. us_swing(micro probe)은 이 게이트를 거치지 않아 생존 |
| `PATHB_KR/US_LIVE_ENABLED=false` | PathB 엔진 정지 | 2차 방벽 |
| `PROFIT_STRATEGY_ENABLED_IDS=` (비움) | 코어 신규신호 차단 | 보유 3종목(SCHG·275280·275300)은 유지 |

- 신설 코드: `execution/safety_gate.py` LEGACY_BUY_DISABLED (기본 false=기존동작), 사유코드 등록. 검증: 스위치 3케이스 + safety 테스트 60개 통과.
- 새 레인 확인: `US_SWING_ORDER_SUBMIT_ENABLED=true`, `ALLOWED_SOURCES=day_losers`, TP 0.12/SL 0.25.
- 롤백 맵: 네 값 원복(`false`/`true`/`true`/기존 ids) → 재시작.

### 1-2. "Claude가 저점을 잘 고르나" — DB 실측 답 (API 호출 없음)
selection 이력 6,747건 + decisions forward 라벨로 검증:

| 방식 | 급락(−3%↓) 상태에서 고른 종목의 forward 5d |
|---|---|
| Claude selection (KR, rank1-3) | **−4.65%**, 승률 24.6% (rank4-8은 −9.34%) |
| Claude selection (US, rank1-3) | +0.89%, 승률 39.8% — rank 순서 무의미 |
| 전략신호 발화(US 대조군) | −0.74% vs 모집단 +4.00 → **p=0.99 역선별** |
| (참고) us_swing 알파모델 | +6.94% vs 모집단 −1.48 → p=0.0000 선별력 |

**결론: 기존 Claude 방식은 저점 선정에 실패했거나 역선별했다. 선별은 모델이 하고 Claude는 veto만 — 재설계 방향이 데이터로 재확정.**

### 1-3. KR 알파 모델 오프라인 검증 — 정직한 부정 결과
US 모델 구조를 KR에 이식해 walk-forward(34,203행 학습, 시험 80일, 누출버퍼 5일):
- 모델 top5: 평균 **−3.58%** vs 날짜구조 보존 무작위 **−3.28%** → **p=0.70, 리프트 없음**
- 원인 후보: 6개월 데이터로는 부족 / 유니버스 편향 / KR 급락은 다른 피처 필요
- **결론: KR은 월요일 실주문 없음. 8조건 규칙 shadow만 가동**(8조건은 검증기간 +3.35/건이지만 누출 경고가 있어 shadow forward가 최종 판정)

### 1-4. 준비된 관측 인프라 (전부 주문 무영향)
- `tools/us_swing_shadow_runner.py`: **하드필터 5조건 shadow 기록** 추가 → `data/shadow/us_hard_filter_shadow.jsonl` (US 세션마다 자동 축적)
- `tools/kr_fallen_shadow_scan.py` (신설): KR 8조건 일일 스캔 + `--settle`로 D5 결과 자동 채움 → `data/shadow/kr_fallen_shadow.jsonl`
- pnl 정합성 감사기 `tools/audit_pnl_consistency.py` (기존)

---

## 2. 월요일 동작 시나리오

**KR (09:00~)**: 신규매수 0 (전 경로 차단). 보유 코어 2종목 그대로. 장 마감 후 `kr_fallen_shadow_scan.py --date 20260803` 실행 → KR shadow 표본 1일차.
**US (22:30~)**: us_swing이 day_losers 후보 스코어링 → **micro 1슬롯 내에서 실주문 가능**(TP12/SL25/D5). 그 외 신규매수 0. 하드필터 shadow 자동 기록.
**감시 포인트**: `SAFETY_BLOCKED` 사유에 `LEGACY_BUY_DISABLED`가 찍히는지(차단 작동 증거), us_swing handoff 로그, guardian 하트비트.

---

## 3. 운영자 선택 필요 항목 (지시한 대로 정리)

**(A) us_swing 사이징 — "예산 모두 활용" vs 검증 규율의 충돌 지점**
현재 micro 1슬롯 × 30만원 = 자본의 0.8%. 확장 옵션:
| 옵션 | 기대(신호17건 시뮬) | 최악 시나리오 | 성격 |
|---|---|---|---|
| 유지 1슬롯 | +4.4%p 구간 | SL−25% × 30만 = −7.5만 | 허들 준수, 표본 느림 |
| **3슬롯/일1건** | +30.5%p 구간 | 3포지션 동시 SL = −22.5만 | 허들(probe 조건) 선적용 — 우회 |
| 5슬롯/일2건 | +63.9%p 구간 | −37.5만 | 적극 우회 |
검증 표본이 17건뿐이므로 제 권고는 **1~3슬롯 사이**이고, "예산 전량"은 forward 허들 통과 후가 원칙이다. **결정은 운영자.**

**(B) Claude API 호출 축소** — 기존 레인이 죽었으므로 selection/judge/analyst 호출은 이제 shadow 데이터만 만든다. 유지(관측 계속) / 축소(비용 절감, 어차피 선별은 모델) 중 선택. 축소 시 별도 작업 필요.

**(C) KR shadow 스캔 자동화** — 수동 실행(내가 매일) vs preopen_scheduler 등록(코드 변경 1건). 
**(D) 잔여 설정 대청소 시점** — PathB 관련 수십 개 키가 무해하게 남아 있다. 즉시 정리 vs 새 레인 안정 후 정리.

---

## 4. 남은 리스크 (정직하게)
- us_swing 검증 표본 17건. day_losers 전용 전환 후 forward는 이제부터 쌓인다.
- KR 8조건에는 피처 선정 누출이 있어 shadow forward가 실질 첫 검증이다.
- `LEGACY_NEW_BUY_DISABLED`가 모든 표준 경로를 막는지는 코드 검증 기준이다 — 월요일 첫 세션에서 `LEGACY_BUY_DISABLED` 차단 로그를 실측으로 재확인해야 한다.
- 월경계 재시작 결함 수정은 여전히 실행 미검증(다음 전체 재시작 때 확인).

*관련 설계: design_fallen_rebound_lane_20260801.md, design_candidate_selection_and_exit_20260801.md*
