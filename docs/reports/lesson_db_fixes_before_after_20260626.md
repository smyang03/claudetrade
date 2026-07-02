# 교훈 DB·운영 개선 — 전후 리포트 (2026-06-26)

> 토론: `debate_lesson_db_operation_20260626.md`. 운영자 "진행해" → 구현.
> 검증: py_compile PASS / mojibake PASS / preflight ok=True·FAIL 0 / 교훈 pytest 80 passed. 커밋 안 함.

---

## 항목별 전후

### #1 APPLY_MODE enforce → shadow (검증 전 무장해제)
- **전:** `.env.live`/`v2_start_config.json` `LESSON_VALIDATION_APPLY_MODE=enforce` (2026-06-17). docstring은 "기본 OFF"라 운영-문서 불일치. 입력 오염(4.14x dedup) 위 무장 → valid_apply=0은 "보호 아닌 운"(오염 표본이 게이트 넘으면 entry_priority_cutoff[net 역상관] 자동조정).
- **후:** `APPLY_MODE=shadow` (두 소스 동기화). `ENABLED=true` 유지 → 축적·채점·would_apply 로깅 계속, **라이브 적용 0**(`get_runtime_adjustments`는 enforce 아니면 {}). 효능은 계속 관측, 무장만 해제.
- **재켜는 조건:** dedup 수정(#2) + valid 셀의 net delta 측정 후 enforce 재검토.

### #2 lesson_scoring dedup (입력 오염 제거)
- **전:** `rescore_lessons`가 `ticker_selection_log` forward 행을 dedup 없이 버킷 append. **raw 15,633행, (market,date,ticker) distinct 3,778 = 4.14x 중복** + trade_ready flip 194키(같은 키가 tr/wo 양쪽 동시기여). 표본게이트(n_wo·confidence)가 중복으로 부풀려져 무력화.
- **후:** `minority_report/lesson_scoring.py` — ticker 포함 쿼리 + (market,date,ticker)당 1행 축약(trade_ready=max → 한번이라도 ready=1이면 trade_ready arm). **4.14x→1x, flip 194키 해소.**
- **효과(rescore 재실행):** verdict가 부풀린 invalid_block/marginal에서 **neutral/pending로 정직화**(작은 실표본 → 과신 verdict 사라짐). 표본게이트가 비로소 의미.

### #3 docstring 동기화 (감사 리스크 제거)
- **전:** `lesson_validation.py` docstring "기본 OFF(ENABLED=false·APPLY_MODE=off)" — 실제 운영(enforce)과 정면 모순.
- **후:** 코드 기본값 OFF + **현재 라이브 운영값(ENABLED=true·APPLY_MODE=shadow) 명시** + 강등 사유·재켜기 조건 기록.

### #4 brain.json 비활성 명시 (오인 방지)
- **전후:** 코드는 이미 `V2_BRAIN_POLICY=fresh_v2`로 brain을 judge/selection 프롬프트에서 **skip**(능동 기여 0). historical_sim 주입 가드는 오늘(#4 위생) 추가됨. 본 리포트·docstring·메모리에 "brain 현재 비활성, 전망적 적재일 뿐" 명시 → "교훈 메모리 작동 중" 오인 방지. (코드 추가 변경 불요 — 이미 올바르게 skip.)

---

## 검증
| 단계 | 결과 |
|---|---|
| py_compile (lesson_scoring·lesson_validation) | ✅ PASS |
| JSON (v2_start_config) | ✅ valid |
| mojibake | ✅ PASS |
| live preflight | ✅ ok=True, FAIL 0 |
| pytest (lesson/validation/tuner) | ✅ **80 passed** |
| rescore_lessons 스모크 (dedup 실행) | ✅ 6셀, 오류 없음, verdict 정직화 |

---

## 보존 (손대지 않음)
forward-only 채점(lookahead 0)·invalid_block 함정차단·cost_floor 게이트 — 무손실 안전장치. 설계 골격은 깨끗하므로 보존.

## 한 줄 결론
교훈 시스템을 **검증 전 무장해제(enforce→shadow)** 하고 **입력 오염 제거(dedup 4.14x→1x, flip 194 해소)** 했다. 이제 valid_apply가 "운"이 아니라 **정직한 표본 위 판정**이 되고, shadow로 효능은 계속 관측한다. docstring·brain 상태도 진실과 일치시켰다. 설계 골격(forward-only·함정차단)은 보존. dedup 후 효능이 측정되면 enforce 재검토.
