# 라이브 봇 43408 통제 재시작 준비 (2026-07-10)

**목적:** 오늘 커밋한 profit evidence 게이트 + profit path shadow + preopen 스케줄러 재시작 견고화를
프로덕션에 활성화. 실행은 운영자, 준비·검증은 AI.

## 왜 재시작이 필요한가
- 봇 43408은 09:44 기동 = 오후 코드/config 이전. profit 계열 전부 **inert**(env UNSET·`state/profit_evidence_*.json` 미생성·shadow 축적 0·20세션 승격시계 미작동).
- hot-patch 안 됨. 다음 정상 재시작(start_live_stack.bat)부터 적용.
- 오늘 4커밋 전부 반영됨(709bd51 preopen-fix·73ca2dd us_swing·43f6e68 profit gate·95e24c4 스크리너 위생).

## 재시작 안전성 (실측)
| 항목 | 상태 |
|---|---|
| 현재 실제 보유 | **6종** (state/open_positions.json) |
| 미체결 주문(pending_orders) | **0** — 재시작으로 끊길 in-flight 주문 없음 |
| 상태 영속성 | 플랜(v2_path_runs + recover_on_startup)·스크리너 캐시·brain·screen baseline = 디스크 영속 → 재시작 생존 |
| 휘발(무해) | 지수히스토리 버퍼(30~60분 재적립)·튜닝카운터·soft캐시·당일 judgment(analyst 재생성) |
| 재시작 절차 | start_live_stack.bat = 기존 스택 kill + broker truth refresh + trading_bot/dashboard/guardian/broker_truth_scheduler/preopen_scheduler/counterfactual/integrity 재기동 |

**타이밍 권고:** 미체결 0 + 보유 6종이라 리스크 낮음. 단 재시작 갭 동안 출구관리(손절/청산)가 잠시 오프라인 →
**KR·US 양 시장 장중이 아닐 때**(또는 보유 포지션이 급소 아닐 때) 실행 권장. 최종 타이밍은 운영자.

## 재시작 후 검증 (순서대로)
1. **프로세스 생존**: trading_bot·preopen_scheduler·broker_truth_scheduler PID 새로 뜨는지.
2. **env 로드 확인**(psutil): 새 trading_bot PID에 `PROFIT_EVIDENCE_GATE_MODE=shadow`, 새 preopen_scheduler PID에 `US_SWING_SHADOW_SCHEDULER_ENABLED=true`.
3. **profit 게이트 활성**: 첫 신규매수 판단 시 `PROFIT_EVIDENCE_SHADOW` 이벤트 로그 + `state/profit_evidence_{market}.json` 생성.
4. **forward monitor**: 60분 후 `python tools/profit_path_forward_monitor.py --market US` 의 `matched_n` 증가.
5. **us_swing 스케줄러**: 다음 US 개장 −10분에 swing_shadow job 자동 발화 → `data/analysis/us_swing_shadow.db` 신규 신호.
6. **포지션 정합**: 재시작 후 broker reconcile로 6종 보유 유지, 유령 청산·중복 진입 없음.
7. **에러 baseline**: `logs/system/`·`logs/risk/`에 재시작 전 대비 신규 에러 유형 없는지.

## 재시작 전 preflight (실측 완료)
- `python tools/profit_evidence_preflight.py --markets KR,US` → ok=True (게이트 준비됨).
- `python tools/us_swing_preflight.py` → ok=True, ledger pending 5/matured 0, micro 미허용(정상).
- full live preflight의 유일 예상 FAIL = `config.runtime_snapshot_drift` (재시작이 해소).

## 재시작이 시작시키는 승격 시계
- profit_path shadow: 최소 20세션·60 matched·2국면 top3제거 LCB>0 전 enforce 금지.
- us_swing: forward 5세션·15건 성숙(~7/16) + top3 게이트 → ~7/23 MICRO 최단.
