# A2 shadow 강등 + add-only 재설계 스펙 (2026-06-26)

> 핸드오프 §5-A DO#1 실행 기록. 운영자 "권고대로 진행" 승인.

## 1. 적용한 변경 (라이브)
- `PATHB_READY_BOOST_MULT` **1.5 → 1.0** (`.env.live` L320 + `config/v2_start_config.json` L442 일치).
- 코드 동작: `runtime/pathb_runtime.py:12581` `ready_boost_mult=max(1.0,min(3.0,...))`,
  `12587` `if ready_boost_mult > 1.0: budget *= ready_boost_mult`.
  → **1.0이면 부스트 미적용**(budget 변화 없음). 단 `boost_eligible` 판정과
  `ready_boost_mult/ready_boost_applied` 태깅은 유지 → **모집단 측정은 계속**(shadow).
- 검증: `v2_start_config.json` JSON 유효, `live_preflight.py` ok=True fail=0 (warn 18 전부 기존 무관 항목).

## 2. 강등 근거 (6인 패널 2라운드 만장일치)
- 부스트 모집단(ready=1 claude_price) N=189 net **−0.26%** = breakeven 책.
- 음EV/breakeven 모집단에 ×1.5 레버 → maxDD −42 → **−63%p**, 6/15-16 19연패 −22.9 → **−34.4%p**.
- ex-ante 식별 신호 없음(BULL regime target율 16.8% = 틸트, 알파 아님). capture 1.11 = C급(N16·MFE 추정·생존편향).
- 결론: "양수 셋업에 자본 집중"의 전제(ex-ante 양수 식별)가 미입증 → 무차별 ×1.5는 파산확률만 올림.

## 3. add-only 재설계 방향 (구현 보류 — 선행조건 있음)
무차별 사이즈업(진입 시점 ×1.5)을 **실현 확인 후 add-only**로 교체한다. 핵심: 진입은 1.0,
포지션이 *실제로* 유리하게 풀린 뒤에만 추가.

**트리거 조건(모두 충족 시 add 1회):**
1. claude_price target 부근 MFE 도달 — 진입 후 native MFE ≥ (target까지 거리 × k), k≈0.5.
2. loss_cap 미발생 — 진입 후 좌측꼬리(캡 근접) 이력 없음.
3. 잔여 상방 — 현재가 < target × (1 − margin), add 후에도 target까지 여유.

**사이즈:** add 1회 = fixed_budget × 0.5 (총 노출 ≤ ×1.5 상한 유지, 단 진입집중이 아니라 확인후).

**선행조건(DO#8, 미배선):** 런타임에서 포지션별 native MFE 실시간 관측이 필요.
현재 MFE는 CLOSED 시점 사후 기록(`position_mfe_pct`) 위주 — 진입~보유 중 실시간 MFE 트랙이
PathB 런타임에 노출돼야 트리거 평가 가능. 이 배선 전에는 add-only 구현 불가 → **1.0 유지**.

**측정 게이트:** add-only 켜기 전, 위 트리거가 과거 데이터에서 +EV였는지 backtest 필요
(이벤트 스토어 MFE 백필 전구간 완료 후). 그 전엔 shadow.

## 4. 롤백/복귀 조건
- 1.5 복귀는 **ex-ante 양수 식별 신호가 A급으로 입증**되거나 add-only가 backtest +EV일 때만.
- 그 전까지 1.0 고정. `improvement_net_monitor.py` (A4 가드)로 모집단 net 지속 관측.
