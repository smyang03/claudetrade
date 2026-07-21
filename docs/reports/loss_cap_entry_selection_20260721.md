# LOSS_CAP 축소 — 진입선별 실증 + dip_entry_gate 설계 (2026-07-21)

재현: `docs/reports/verify_20260721/loss_cap_entry_discrim.py`, `loss_cap_rules.py`
데이터: v2_learning_performance live closed 5/15~ (US 195·KR 22), 피처는 가격 CSV에서
진입시점(no-lookahead: session_date 이전 봉만) 직접 계산.

## 핵심 실증 — 손실원이 시장별로 정반대

**US (n=195, 통산 net −66.5%p)**: 손실은 **낙폭 반등 베팅**에 집중.
- 진입시점 ret_5d 중앙값: LOSS_CAP군 −1.37% vs 승자군 +4.42%.
- ret_5d 4분위: q3[+0.9..+8.3] **net +39.5 유일 흑자**(LC7/T19), q1[−5.8↓] −45.1, q4[+8.9↑] −34.3 — 역U자(중간 모멘텀만 흑자). anti-chase 비단조 발견과 동형.
- dist_20d_high q1(−16.8%↓ 낙폭과대) net −45.7 (LC 19).

**KR (n=22, 통산 +11.7%p)**: 손실원은 급등 추격 — LOSS_CAP군 ret_5d med +10.3 vs
승자 −0.55, max21 med 29.9. **anti-chase 25 enforce가 이미 담당**(기배제군 −18.6%p).
KR에 낙폭 배제를 걸면 TARGET 희생(+14.8) — **KR 적용 금지**.

## 룰 반사실 (anti-chase 25 통과분에서의 증분, US n=182 net −55.8)

| 룰 | 배제 n | 배제군 net | TARGET 희생 | 잔존 net |
|---|---|---|---|---|
| **A: ret_5d<−5 배제** | 51 | **−42.2%p** | +11.6 (10건) | **−13.6%p** |
| B: gap>+7 배제 | 43 | −27.4 | +11.1 | −28.3 |
| A∪B | 82 | −49.8 | +23.9 | −5.9 |
| A∪B∪C(dist<−17) | 90 | −56.2 | +24.9 | +0.4 |

- 월별 안정(A∪B 배제군): 5월 −2.2 · 6월 −45.0 · 7월 −2.6 — 부호역전 없음.
- 채택: **룰 A 단독**(가장 단순·희생 최소·진입가 무관 피처). B/C는 관측 지속 후 확장 검토.
- 한계: 임계 −5는 같은 표본 4분위에서 왔음(경계 과적합 위험) → shadow로 forward 확인 후 enforce.

## 배포 (기본 shadow — 매수 차단 게이트, enforce는 운영자 확인 필수)

- `bot/dip_entry_gate.py` (off/shadow/enforce, fail-open, **기본 US 전용**).
- 피처: `pool_quality_features.ret_5d_pct_pool` (일봉, lookahead 없음).
- 훅: `_filter_candidates_by_history` anti-chase 직후. shadow는 `dip_entry_would_block`
  플래그로 screener_quality 원장에 기록.
- 토글(두 소스 일치): `DIP_ENTRY_GATE_MODE=shadow`, `DIP_ENTRY_RET5D_THRESHOLD=-5`,
  `DIP_ENTRY_GATE_MARKETS=US`. 롤백=MODE=off.
- enforce 전환 조건: shadow would_block 코호트의 forward net이 반사실과 방향 일치
  (US 세션 5~10회 관측) + 운영자 승인.

판정 규율: 우리 net 기준. LOSS_CAP 임계 자체(손절 완화)는 건드리지 않음 — cap-widen
기각 이력·멀티데이 반사실(LOSS_CAP 연장 median 음수)과 정합.
