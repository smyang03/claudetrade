# [제안 — 미승인 draft] US 브리지 rank2 폴백 사전등록 문안 (2026-08-12)

**상태: 운영자 미승인. 코드 변경 없음. 승인 시 이 문안이 사전등록 정본
(`preregistered_falsification_criteria_20260804.md`)에 개정 행으로 편입된다.**

## 1. 문제 — 판정 페이스

- 브리지 가동 7세션 제출 3건 = **0.43건/세션** → 30건 판정 도달 10월 말~11월
  (night_check_20260812.md §4).
- 차단 내역(7세션): 허들 2(구계약 — 08-08 폐지로 소멸) · chase 1 · slot 1 · rank캡 2 ·
  authority 1.
- 현 구조: `load_handoff_signals(limit=max_new_per_day=1)` — **rank1 단 한 개만 로드**
  (`runtime/us_swing_order_bridge.py:228`). rank1이 종목 고유 사유(chase 등)로 차단되면
  그날 기회가 통째로 소멸한다. STEP 08-10이 실측 사례: chase 가드 정상 차단(창 전체
  시가+0.62~2.52%)이었지만, 그날 rank2는 평가 기회 자체가 없었다.

## 2. 근거 — 왜 rank2가 품질 저하가 아닌가

1. **알파=용량 4중 확인**(tp-capture 08-11): US 조건 통과 **전량** n=177 net +1.92% vs
   모델 rank1 +0.12% — 랭킹 상위 고집이 오히려 역선별이었다. rank2는 같은 조건 통과분이다.
2. **허들 폐지와 동일한 순환 구조**: 판정 표본 축적을 스스로 깎는 가드는 자기 검증도
   지연시킨다(08-08 C안 논리 재사용). 리스크 관리는 계약(TP12/SL25/D5)이 담당한다.
3. **리스크 불변**: 일1건×고정 30만·슬롯 3·계약 동일. 선별 폭만 rank1→rank1~2.
   최악 노출은 지금과 정확히 같다(하루 최대 1건).

## 3. 설계 (승인 시 구현 사양)

- 로드: `limit=2` (rank1·rank2).
- **이양 조건 — 종목 고유 guard 차단일 때만**: rank1의 최종 사유가
  `price_chase_above_contract` / `open_gap_outside_contract` / `open_fade_below_contract` /
  `provider_fresh_quote_incomplete` / `already_holding` / `pending_order_exists` /
  `same_day_reentry_blocked` 중 하나면 같은 창 안에서 rank2를 평가한다.
- **이양 금지 — 일 공통 차단**: `strategy_open_slot_cap_reached` / `entry_window_expired` /
  `broker_truth_not_trusted` / budget·authority 계열은 rank2도 동일하게 막히므로 이양 없음.
- 일1건 유지: 첫 제출 즉시 종료. rank3 이하 이양 없음(폭 1→2만).
- 스위치: `US_SWING_RANK2_FALLBACK_ENABLED`(기본 false), `.env.live` +
  `config/v2_start_config.json` 두 소스 동시 변경, 재시작 후 effective-config 실측.
- 원장: 제출 행에 `entry_rank` 태그(이미 `selected_reason=us_swing_5d_rank_{n}`에 기록됨 —
  실측 `us_swing_order_bridge.py:351`). contract_id 산식 변경 없음(허들과 달리 계약
  지문 항목이 아님 — 편입 시 재검토).

## 4. 사전등록 — 판정·반증 (승인 시 이대로 등록)

- **코호트**: 30건 카운트에 rank2_fallback 제출 건 포함(코호트 정의 2항 "실주문 전건"
  그대로), 단 rank1 / rank2_fallback 분해를 판정 리포트에 병기(허들 개정 규약과 동일).
- **반증(중단 조건)**: rank2_fallback 정산 **10건 net ≤ 0 → 폴백 제거, rank1 전용 복귀**
  + 원인 분해. 이 판정에 rank1 성과는 섞지 않는다.
- **다중검정 방어**: 새 조건 신설이 아니라 기존 랭킹의 소비 순서 변경. 성과를 보고
  고른 것이 아님(제안 시점 forward 정산 3건뿐, 페이스 문제로 제안).
- **기대의 한계(정직 고지)**: 7세션 표본에서 종목-guard성 차단은 chase 1건 — 페이스
  개선 기대치는 +0.1~0.2건/세션 수준이지 배가가 아니다. 주 효과는 "guard 차단일의
  기회 소멸 제거"이고, 효과가 작으면 그것도 실측으로 남는다.

## 5. 승인 요청 문구

"rank2 폴백 승인" 한마디면 위 §3 사양대로 구현하고 §4를 사전등록 정본에 편입한다.
보류 시 이 문서는 제안 기록으로만 남는다.
