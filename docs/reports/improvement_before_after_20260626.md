# 개선 적용 전후 차이 + 장점 리포트 (2026-06-26)

> 토론(`debate_system_improvement_20260626.md`) 결론 → 운영자 승인(다 적용·enforce·초기 손실 감수, A2는 타겟 예외) → 구현 완료.
> 검증: py_compile PASS / pytest 2722 passed / mojibake PASS / live preflight ok=True·FAIL 0.
> **라이브 행동 변경은 운영자 운영테스트로 최종 확인 후 진행(운영자 지시).**

---

## 0. 한눈에 — 무엇이 바뀌었나

| # | 개선 | 토글/파일 | 형태 | 전 → 후 |
|---|---|---|---|---|
| A1 | ready=0 신규 진입 완전 차단 | `REQUIRE_TRADE_READY=true` | enforce | ready=0도 진입 → **ready=1만 진입** |
| A2 | ready=1 양수 셋업 자본 ×1.5 | `PATHB_READY_BOOST_MULT=1.5` | enforce | 전 셋업 50만 고정 → **ready=1 PathB 75만** |
| A3 | 4·5월 net 측정 백필 | `tools/backfill_net_apr_may.py` | 적용됨(측정) | net 6월만 → **4·5·6월 전부** |
| A4 | boost net 가드 | `tools/improvement_net_monitor.py` | 관측 | 없음 → **net 음전 감시** |
| #2 | 트렌드 방어 오버레이 | `TREND_OVERLAY_GATE_MODE=shadow` | shadow | off → **shadow(net A/B 누적)** |
| #4 | hold forward-validation 편입 | `HOLD_ADVISOR_EXIT_LESSON_ENABLED=true` | 관측 | 미검증 영구주입 → **자기 verdict 누적** |

전부 `.env.live` + `config/v2_start_config.json` 두 소스에 동기화 기록(CLAUDE.md 규율).

---

## 1. 항목별 전후 차이 (코드 레벨)

### A1 — ready=0 신규 진입 완전 차단 (enforce)
- **전:** ready=0 후보도 진입. Path A는 `PROBE_READY`(trade_ready 미만 탐색 진입)로, PathB는 `PULLBACK_WAIT`/PROBE 출신(`not_patha_trade_ready=True`)으로 매수까지 감. 라이브 표본에서 traded의 절반 이상이 ready=0.
- **후:**
  - Path A: `runtime/action_routing.py` `_decision()` 단일 출구에서 `REQUIRE_TRADE_READY` 시 `PROBE_READY → WATCH` 강등(`runtime_gate_reason=require_trade_ready`). `BUY_READY`/`ADD_READY`(ready=1)는 통과.
  - PathB: `runtime/pathb_runtime.py` `_submit_buy()`에서 `not_patha_trade_ready=True` 진입을 `REQUIRE_TRADE_READY_BLOCK`으로 차단 + plan 취소.
- **신호 매핑(정직):** 런타임의 `not_patha_trade_ready`(PathA trade_ready 출신 vs PULLBACK_WAIT/PROBE)를 selection_log의 `trade_ready` 프록시로 사용. 정확히 동일 라벨은 아니므로 운영테스트에서 차단 대상이 의도(ready=0 출혈군)와 맞는지 funnel 로그로 확인 필요.

### A2 — ready=1 양수 셋업 자본 ×1.5 (enforce, 타겟 예외)
- **전:** 모든 PathB 진입 = `pathb_fixed_order_krw`(50만) 고정.
- **후:** `runtime/pathb_runtime.py` `_pathb_qty_with_context(..., boost_eligible)` 추가. `boost_eligible = not plan.not_patha_trade_ready`(=ready=1 PathA 출신). 활성 시 `budget *= PATHB_READY_BOOST_MULT(1.5)` → 75만. early_gate(장초반 ×0.5)와 곱연산 공존. `sizing_context`에 `ready_boost_mult`/`ready_boost_applied` 관측 필드 추가.
- **타겟 예외 성립 근거:** `MAX_ORDER_KRW`(50만)는 Path A `calc_order_budget` 경로 캡이고 `can_open`은 이를 검사하지 않음. PathB는 `pathb_fixed_order_krw`를 직접 써서 75만이 잘리지 않음 → 보호 파라미터(50만)는 일반 주문엔 유지, 양수 셋업만 75만.

### A3 — 4·5월 net 측정 백필 (적용 완료)
- **전:** `net_basis='measured'`는 라이브 비용메타가 붙은 6/9 이후만 존재 → 4·5월 net NULL. 모든 net 판정이 6월 단일국면.
- **후:** `tools/backfill_net_apr_may.py --apply` 실행. v2_learning_performance·v2_canonical_performance의 4·5월 closed 164건에 `pnl_pct_net`/`fee_pct_round_trip`/`net_basis` 기록.
  - **KR:** 환전 없음 → `net = gross − 0.5%` **정확**. `net_basis='backfilled_exact'`.
  - **US:** 4·5월 진입/청산 환율 미기록(복구 불가) → 수수료만 차감 **근사**. `net_basis='backfilled_fee_only'`(FX 0.2% 미반영).
  - 라이브 `'measured'`와 **다른 basis 라벨** → 클린 measured 집합 오염 금지(정직 규율).

### A4 — boost net 가드 (관측)
- `tools/improvement_net_monitor.py`: A2 boost-eligible 모집단(PathB claude_price)의 net 추적, 음전 시 `PATHB_READY_BOOST_MULT` 롤백 신호. (현재는 전체 모집단 proxy — boost 적용분만 보려면 운영 후 이벤트스토어 `ready_boost_applied` 태그 분리.)

### #2 — 트렌드 방어 오버레이 shadow + net A/B (shadow)
- **전:** `TREND_OVERLAY_GATE_MODE=off`(코드 기본값, config 미기재) → 미가동.
- **후:** `shadow`로 설정. `runtime/pathb_runtime.py:4160`가 `_runtime_value`로 읽어 활성. 하락추세(`below_sma`) 진입을 `would_skip`로 표시·로깅(차단 안 함). `tools/improvement_net_monitor.py`가 funnel 로그를 net에 조인해 would_skip vs allowed net A/B + kill 권고 산출.

### #4 — hold advisor forward-validation 편입 (관측)
- **전:** `HOLD_ADVISOR_EXIT_LESSON_ENABLED=false`. profit_guard prior가 자기 후속결과로 재검증 안 되는 유일 경로.
- **후:** `true`. `collect_exit_outcomes → backfill_forward → rescore`가 `hold_profit_guard_exit` 셀 verdict를 `lesson_validation` store에 축적. **행동 변경 아님**(실제 적용은 `LESSON_VALIDATION_APPLY_MODE=enforce`일 때만). 미검증 영구주입 종식.

---

## 2. 구현이 드러낸 새 사실 — A3 백필이 명제1을 결판냄

백필 직후 측정값(수수료 후):

| market | 4·5월 net (백필) | 6월 net (라이브) |
|---|---|---|
| **US** | **+0.263%** (N=121) | −0.561% (N=86) |
| **KR** | −1.555% (N=43) | +0.955% (N=17) |

- **US selection은 무엣지가 아니라 국면 의존이다.** 3개월 중 2개월(4·5월) net 양수, 6월(MILD_BEAR)만 음수. 토론의 "명제1 미결"이 **"selection 엣지는 존재하나 국면(MILD_BEAR)에서 죽는다"**로 결판. → #2 트렌드 오버레이(적대 국면 진입 차단)가 데이터상 정확한 처방임을 뒷받침.
- **KR은 단조 개선**(4·5월 −1.55% → 6월 +0.96%)이 measured+backfilled로 재확인.

---

## 3. 개선의 장점 (왜 이게 이득인가)

### A1 (ready=0 차단)
- **출혈 싱크 제거.** ready=0는 양 시장 net 음수(KR −3.73%/PF0.08, US −1.97%) — 가장 확실한 손실원을 직접 끊는다. 틀려도 거래량만 줄 뿐 위험 증폭 없음(가장 안전한 공격).
- **자본을 ready=1로 재배치.** 같은 자본이 검증된 등급에만 들어감.

### A2 (양수 셋업 ×1.5)
- **이긴 패에 더 건다.** KR claude_price net PF 2.11(살아있는 양수 셀)에 자본 집중. 고정 50만이 100% 승률 셋업과 동전던지기에 같은 돈을 넣던 비효율 해소.
- **보호 파라미터는 유지.** 타겟 예외라 일반 주문 50만 캡은 그대로 — 리스크 표면을 양수 셋업으로만 한정.

### A3 (net 백필)
- **결판 능력 회복.** "selection 엣지 있나"를 6월 한 달이 아니라 3개월로 판단 가능 → US 엣지가 국면 의존임을 즉시 확인(위 §2). 측정 인프라라 위험 0, ROI 최고.
- **정직한 basis 분리.** 근사(US)와 정확(KR)을 라벨로 구분해 향후 분석 오염 차단.

### A4 / #2 / #4 (측정·검증 장치)
- **#2:** net 최대 레버(loss_cap 좌측꼬리 = US net −48% 전부)를 *진입 단계에서* 막는 오버레이의 효과를 라이브 net으로 검증 후 enforce — whipsaw 위험을 shadow로 흡수. US 국면 의존(§2)에 직접 대응.
- **#4:** hold advisor가 처음으로 자기 판단을 forward-validation 받음 → "검증된 것만" 원칙 위반(미검증 prior 영구주입) 종식.
- **A4:** A2가 틀렸을 때 net 음전을 조기 포착 → 초기 손실 감수를 *무한정*이 아니라 *감시 하의* 감수로 바꿈.

### 공통
- **운영 통제권 강화.** 6개 모두 토글 1개로 즉시 on/off — 운영테스트에서 문제 시 즉시 롤백.
- **두 소스 동기화 + 검증 통과** — 조용히 안 깨지는 변경.

---

## 4. 운영테스트 시 확인할 것 (운영자 점검 포인트)

1. **A1:** funnel/blocked 로그에서 `REQUIRE_TRADE_READY_BLOCK`(PathB)·`require_trade_ready`(Path A) 차단 대상이 의도한 ready=0 출혈군과 일치하는지. 진입 빈도 급감 폭 확인(거래량↓ 정상).
2. **A2:** PathB ready=1 진입의 `sizing_context.ready_boost_applied=true`·주문금액 75만 확인. `improvement_net_monitor.py`로 boost 모집단 net 감시.
3. **A3:** 완료. `net_basis IN ('backfilled_exact','backfilled_fee_only')` 164건 기록됨.
4. **#2:** `logs/funnel/trend_overlay_*.jsonl` 누적 시작 → `improvement_net_monitor.py`로 would_skip net A/B 확인(표본 쌓인 뒤).
5. **#4:** `data/lesson_validation.db`에 `hold_profit_guard_exit` 셀 verdict 누적 확인(verdict ~7/21).

**한 줄:** 6개 개선 모두 구현·검증 통과. 손실원(ready=0)을 끊고 양수 셀(ready=1)에 자본을 모으며, 측정 공백(4·5월 net)을 메워 US 엣지가 국면 의존임을 결판냈고, 나머지 레버(트렌드 오버레이·hold)는 라이브 net으로 검증받도록 장치를 깔았다. 라이브 행동 변경은 운영테스트로 최종 확인.
