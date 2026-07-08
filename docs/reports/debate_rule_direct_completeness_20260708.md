# 토론 판정: rule_direct+구매력 게이트 전환의 완결성 — 누락 사냥 (2026-07-08)

## 명제
"오늘 배포된 전환(SELECTION_RULE_DIRECT_KR/US + ENTRY_CLAUDE_BUYING_POWER_GATE_KR/US, 커밋 9219aaf·f09ed68, 22:31 재시작 활성)은 논리적으로 완결이다 — 누락된 핵심 필요사항이 없다."

## 판정: **반대 (부분) — 극적 누락은 없으나 실증된 보수 항목 3건**

배포 자체는 라이브에서 정상 작동(rule_direct 4회 발동, 엔드투엔드 완주: 룰 watch → early_judge → DELL 플랜 등록, 에러 0). 그러나 "완결"은 아니다.

## 합의 (양측 수렴 + 사회자 재검증)

| 항목 | 판정 |
|---|---|
| trade_ready=[] → 매수 셧다운 | **기각** — REQUIRE_TRADE_READY_KR/US=false(시장키가 글로벌 true를 이김, pathb_runtime.py:4577·action_routing.py:547), early_judge BUY_READY 재충전 경로 생존 |
| 후보 데이터 축적 단절 | **기각** — 기록은 caller 소관(Claude 콜 비의존), 재시작 후 27분간 audit 602행+selection_log 87행 실측 |
| smart_skip 캐시로 구 Claude 선정 재사용 | **실질 기각** — actionable 재사용 차단+TTL 30분+rule_direct 미재충전, 재시작 후 reuse 0 실측 |
| reconcile이 기존 플랜 일괄 취소 | **기각(무조건부)** — watchlist=retained=KEEP. 단 조건부 결함은 P2 참조 |
| 재시작 후 발동/에러 | 정상 — rule_direct 4회, Claude selection 콜 0회, 에러 0 |

## 실증된 누락 (사회자 직접 재검증 완료)

### P1 [치명·조건부=창구일] 진입 플랜 생산 천장이 early_judge 쿼터로 하향 고정
- 6/24 이후 플랜 **187건 전원 `candidate_actions_wait_only`** — 플랜 생산은 사실상 selection 응답(candidate_actions PULLBACK_WAIT) 채널이었다(사회자 DB 재검증).
- planb_bridge는 **최근 5거래일 로그 0건**(사회자 재검증) + 시드가 selection의 `_pathb_wait_tickers`라 rule_direct 이후 구조적 0.
- 남은 파이프 = early_judge 단독: US 10/세션·글로벌 10·시간당 8·run당 2 (.env.live:412-415 실측).
- 스로틀 시대(7월) 페이스(1~7건/일)와는 동일하나, **창구일(멜트업)에 selection이 만들던 20~46건/일 천장이 10으로 고정** — "창구일에 시스템이 닫히면 안 된다"(R2)와 충돌.
- 보충: 창구일 전 early_judge 세션캡 상향 여부 = 운영자 파라미터 판단.

### P2 [중요·조건부=US 풀>30] US reconcile kill-zone
- 코드 확정: `reviewed(프롬프트 풀 전체≤40) − retained(watchlist≤30)` = **랭크 31~40 티커의 WAITING 플랜이 `INVALID_CANCEL reviewed_and_removed`로 취소**(pathb_runtime.py:2788-2795). 종전엔 Claude candidate_actions가 VALID_KEEP 방어를 제공했으나 rule_direct 메타는 watchlist뿐.
- 캡 실측: US watch_max=30(default)·프롬프트캡=40(default) → kill-zone 실재. KR은 캡 28<30이라 면역. 오늘밤 US 풀 29라 미발동.
- 유일 생산 파이프(judge)의 플랜을 룰 랭크 회전(30분마다 교체 실측)이 사살할 수 있는 상호작용 — 설계에 없던 누락.
- 보충: rule_direct 메타에 reconcile 취소 면제(rule 랭크 이탈≠검토 후 탈락) 또는 retained에 풀 포함. 소규모 패치.

### P3 [중요] 기록 내용 결손
- selection 유래 컬럼(veto_reason·risk_tags·recommended_strategy) 전부 NULL, selected_reason 전 종목 "rule_direct(screener_rank)" 획일 — 사후 분석 변별력 저하.
- smart_skip full_call 기록 영구 미도달 → 서브시스템이 죽은 무게(해시 계산만 낭비).
- `_watch_only_bucket` HARD veto 신호 소실(전 종목 SOFT).
- 보충: rule_direct 분기에서 룰 랭크·핵심 피처를 selected_reason에 구조화 기록 + SELECTION_SMART_SKIP_ENABLED=false.

## 사소 (기록만)
- evidence prefetch·프롬프트 조립이 rule_direct 분기 앞에서 그대로 실행 — Claude 콜만 절약(프롬프트 조립 CPU 낭비, prefetch는 audit 재사용이라 부분 낭비).
- 구매력 게이트 cash가 KR+US 합산 풀 — 통화 비대칭 시 under-block(과차단 아님·안전 방향). 게이트 라이브 차단 동작은 아직 무검증(현금 충분).
- hold advisor의 selected_reason 획일화 — entry thesis는 judge 플랜에서 생존하므로 부분 열화.

## 지뢰 (변경 아님, 인지 필수)
- 글로벌 `REQUIRE_TRADE_READY=true`가 장전된 채 잔존 — 시장키를 지우거나 true로 바꾸는 순간 전면 매수 차단. 건드리지 말 것(정리는 운영자 승인 사안).

## 보충 우선순위 (적용=운영자 승인)
1. **P2 패치** — 조건 발동 전(US 풀>30인 어느 날이든) 선제 수정 권고.
2. **P1 판단** — early_judge 캡 상향(예: US 세션 10→20)은 운영자 파라미터. 창구일 R2 보호 목적.
3. **P3 보강** — 기록 품질+smart_skip off. 급하지 않음.

— 사회자 (2026-07-08, 검증 쿼리·코드라인 세션 기록)
