# Active Lesson Quality Filter Implementation QA (2026-05-16)

## 1. 구현 요약

요구서 `active_lesson_quality_filter_development_requirements_20260516.md` 기준으로 active lesson 생성/소비 경로를 분리했다.

- `minority_report/lesson_quality.py` 추가
  - `lesson_quality_fields()`
  - `apply_lesson_conflict_guards()`
  - `KNOWN_METRIC_KEYS`
- `trading_bot.py`
  - `_build_ops_review_snapshot()` selection metric을 `ticker|date` DISTINCT 기준으로 계산
  - `watch_only_missed_rows` 분자는 `forward_3d IS NOT NULL`을 같이 요구해 분모/분자 기준을 일치
  - `trade_ready_forward_3d_average`는 `trade_ready_forward_dedup` CTE로 ticker/day 단위 평균을 계산
  - `atr_blocked_rows`, `atr_blocked_runup_avg`는 `ticker|date` dedup CTE 기준으로 계산
  - `_build_lesson_candidates()`에 `quality_version`, `claude_actionable`, `ops_flag`, `action_hint`, `min_sample` 추가
  - `affordability_fail_cluster` 직접 dict 경로에도 동일 quality field 적용
  - watch_only/trade_ready 동시 breach conflict guard 적용
  - legacy lesson summary 로더도 `action_hint`/quality filter 기준으로 보강
- `minority_report/active_lessons.py`
  - `ops_flag`, `claude_actionable=false`, execution/consensus/strategy scope, action_hint 없음, min_sample 미달 차단
  - lesson candidate text는 `summary`가 아니라 `action_hint` 사용
  - lesson candidate path는 `text_limit=500` 적용
  - `execution_lessons` prompt 주입 차단
  - recent day source cap을 market별 최대 2개로 적용
  - `ignored_reasons` metadata 추가
- `tools/backfill_lesson_candidate_quality.py` 추가
  - 기존 `state/lesson_candidates.json`에 quality field 1회성 보강
  - dry-run 기본, write 시 backup 생성
- `minority_report/analysts.py`
  - market judgment R1/R2도 `build_active_lesson_context()`를 직접 사용하도록 통합
  - raw call metadata에 active lesson source metadata를 같이 저장

## 2. Before / After

Artifacts:

- Before: `docs/reports/active_lesson_quality_before_20260516.json`
- After: `docs/reports/active_lesson_quality_after_20260516.json`
- Prompt smoke: `docs/reports/active_lesson_quality_prompt_smoke_20260516.json`

### KR

Before selected 5개:

1. `KR_lesson_candidates_watch_only_missed_runup_review`
2. `KR_lesson_candidates_affordability_fail_cluster`
3. `KR_recent_day_2026-05-14`
4. `KR_execution_lessons_0`
5. `KR_execution_lessons_1`

After selected 2개:

1. `KR_lesson_candidates_watch_only_missed_runup_review`
2. `KR_recent_day_2026-05-14`

검증 결과:

- `affordability_fail_cluster` 제거됨
- `execution_lessons` 제거됨
- `ignored_reasons`: `ops_flag=1`, `execution_scope_excluded=8`, `execution_learning_excluded=1`
- active lesson chars: `600 -> 420`

### US

Before selected 5개:

1. `US_lesson_candidates_watch_only_missed_runup_review`
2. `US_recent_day_2026-05-15`
3. `US_recent_day_2026-05-14`
4. `US_recent_day_2026-05-12`
5. `US_recent_day_2026-05-11`

After selected 3개:

1. `US_lesson_candidates_watch_only_missed_runup_review`
2. `US_recent_day_2026-05-15`
3. `US_recent_day_2026-05-14`

검증 결과:

- recent day source cap으로 recent day 2개만 유지됨
- `ignored_reasons`: `source_cap=2`, `execution_scope_excluded=8`, `execution_learning_excluded=1`
- active lesson chars: `1111 -> 698`

## 3. DB 검증

`data/ticker_selection_log.db` live 14일 window 기준 raw vs DISTINCT:

| market | raw watch n | distinct watch n | raw missed | distinct missed | raw ratio | distinct ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| KR | 242 | 161 | 162 | 102 | 66.9% | 63.4% |
| US | 378 | 221 | 186 | 99 | 49.2% | 44.8% |

Trade-ready forward average raw vs DISTINCT:

| market | raw forward n | distinct forward n | raw avg | dedup avg |
| --- | ---: | ---: | ---: | ---: |
| KR | 40 | 33 | -0.201% | -0.169% |
| US | 38 | 25 | 3.714% | 2.456% |

판단:

- 요구서의 중복 row 과대 계산 문제를 코드 레벨에서 차단했다.
- 기존 DB row는 삭제하지 않았다.
- 현재 `state/lesson_candidates.json`의 기존 metric value/sample_count는 backfill에서 재계산하지 않는다. 다음 session close 또는 별도 regenerate 시 `_build_ops_review_snapshot()`의 DISTINCT 계산이 반영된다.

## 4. Backfill 검증

실행:

```text
python tools/backfill_lesson_candidate_quality.py --path state/lesson_candidates.json --dry-run
python tools/backfill_lesson_candidate_quality.py --path state/lesson_candidates.json --write
python tools/backfill_lesson_candidate_quality.py --path state/lesson_candidates.json --dry-run
```

결과:

- 최초 dry-run change_count: `7`
- write backup: `state/lesson_candidates.backup_20260516_210904.json`
- write 이후 dry-run change_count: `0`

현재 active lesson에는 `action_hint`가 주입되고, ops/execution 교훈은 prompt 후보에서 제외된다.

## 5. Claude Prompt Smoke

활성 파라미터:

```text
ACTIVE_LESSONS_ENABLED=true
ACTIVE_LESSONS_SHADOW=false
ACTIVE_LESSONS_MAX_ITEMS=5
ACTIVE_LESSONS_MAX_CHARS=3000
ACTIVE_LESSONS_ANALYST_MAX_CHARS=3000
ACTIVE_LESSONS_DEBATE_ENABLED=true
ACTIVE_LESSONS_DEBATE_MAX_CHARS=1200
```

결과:

| market | scope | chars | max_chars | omitted_chars | injected | contamination |
| --- | --- | ---: | ---: | ---: | --- | --- |
| KR | selection | 420 | 3000 | 0 | true | 없음 |
| KR | r1 | 420 | 3000 | 0 | true | 없음 |
| KR | r2 | 420 | 1200 | 0 | true | 없음 |
| US | selection | 698 | 3000 | 0 | true | 없음 |
| US | r1 | 698 | 3000 | 0 | true | 없음 |
| US | r2 | 698 | 1200 | 0 | true | 없음 |

판단:

- Claude selection/R1/R2 경로 모두 active lesson을 받을 수 있다.
- 실제 `get_three_judgments()` R1/R2 경로도 `build_active_lesson_context()`를 사용한다.
- 기존 `_load_lesson_candidate_summary()` fallback 문자열은 active lesson 로드 실패 시에만 쓰이며, 정상 경로에서는 selection/R1/R2가 같은 필터와 같은 metadata를 쓴다.
- 이번 active lesson 길이는 R2 debate limit `1200` 안에 들어가며 잘림 없음.
- `affordability`, `intraday_review_sell`, `loss_cap`, `execution_lessons` 계열 오염 문구 없음.

## 6. 테스트 결과

전체 테스트:

```text
python -m pytest -q
1266 passed, 2 skipped, 2 warnings in 55.66s
```

추가/보강된 테스트 범위:

- DISTINCT selection metric
- quality field 생성
- affordability 직접 dict 경로 보강
- conflict guard
- action_hint 필수화
- action_hint 500자 limit
- execution lesson 차단
- recent day source cap
- backfill dry-run/write/backup

## 7. 운영성 테스트

Live preflight:

- report: `docs/reports/active_lesson_quality_live_preflight_20260516.json`
- result: `ok=true`
- fail_count: `0`
- warn_count: `11`
- effective config에 active lesson 및 이번 개선 파라미터 반영 확인

Live guardian smoke:

- report: `data/v2_reports/live_guardian_20260516_211605.json`
- latest report: `data/v2_reports/live_guardian_20260516_214359.json`
- gate: `ALLOW_START`
- hard_fail: `0`
- soft_fail: `7`
- smoke_ok: `true`
- KR/US sizing smoke 통과

운영 주의:

- 현재 실행 중인 live bot은 `2026-05-16 02:13:00 KST` runtime snapshot 기준으로 떠 있어 새 파일 설정과 drift가 있다.
- 파일 기준 preflight는 통과했지만, 실제 실행 프로세스에 `ACTIVE_LESSONS_ENABLED=true`, `ACTIVE_LESSONS_SHADOW=false`, `ACTIVE_LESSONS_MAX_CHARS=3000`, role model 설정을 반영하려면 live bot 재시작이 필요하다.
- 기존 이슈인 과거 `ORDER_UNKNOWN` 19건, US credential shared fallback, PathB KR live disabled, WAIT_TIMING evidence 부족은 이번 active lesson 작업 범위 밖이다.

## 8. 요구서 대비 누락 확인

| DR | 상태 | 확인 |
| --- | --- | --- |
| DR-0 ops review DISTINCT dedup | 완료 | SQL 및 테스트 반영 |
| DR-1 lesson candidate schema 확장 | 완료 | runtime/backfill 공통 helper 사용 |
| DR-2 metric별 생성 규칙 | 완료 | watch_only/trade_ready/affordability/ops metric 분류 |
| DR-3 active lesson 소비 필터 | 완료 | ops/actionable/scope/action_hint/min_sample 필터 |
| DR-4 brain execution lesson 차단 | 완료 | ignored reason 기록 |
| DR-5 recent day 최소 품질 기준 | 완료 | source cap P0 적용 |
| DR-6 legacy backfill 도구 | 완료 | dry-run/write/backup/idempotency 확인 |
| DR-7 운영 가시성 | 완료 | `ignored_reasons` metadata 추가 |

누락 없음.

의도적으로 남긴 범위 밖 항목:

- `ticker_selection_log` 물리 row 삭제/복구
- rescreen 중복 INSERT 원인 수정
- same ticker/day의 watch_only/trade_ready representative row 정책
- `trades < 3` recent day hard exclude

## 9. 최종 판단

구현, 테스트, DB 검증, Claude prompt smoke, 운영 smoke 모두 요구서 기준을 충족한다.

실운영 반영의 마지막 조건은 live bot 재시작이다. 재시작 후 새 `effective_config_*.json`에서 active lesson 파라미터 drift가 사라지면 실제 Claude 호출 경로도 이번 품질 필터를 사용한다.

## 10. 재검토 보강

재검토 중 backfill 리포팅 경계 조건 1건을 보강했다.

- 문제: 기존 quality field가 이미 붙어 있고 watch_only/trade_ready가 동시에 breached=true인 경우, `apply_lesson_conflict_guards()`가 loser를 suppress해도 dry-run `change_count`가 0으로 나올 수 있었다.
- 조치: backfill 도구가 quality field 적용과 conflict guard 적용을 모두 끝낸 뒤 before/after를 비교하도록 변경했다.
- 조치: `apply_lesson_conflict_guards()`가 기존 `quality_conflict_*` marker를 먼저 지우고 현재 상태 기준으로 다시 계산하도록 변경했다.
- 테스트: `tests/test_lesson_quality_backfill.py::test_conflict_guard_changes_are_reported` 추가.
- 재검증: `python -m pytest -q` 결과 `1266 passed, 2 skipped`.

## 11. 코드 재검토 추가 보강

추가 코드 리뷰에서 3건을 더 확인했고 반영했다.

1. `watch_only_missed_rows` 분자 조건 보강
   - 문제: 분모는 `forward_3d IS NOT NULL`인데 분자는 `max_runup_3d >= 5.0`만 보아, 최근 outcome 미완성 row가 분자에만 들어갈 수 있었다.
   - 조치: 분자에도 `forward_3d IS NOT NULL`을 추가했다.
   - 테스트: outcome 미완성 watch_only row를 fixture에 추가해 ratio가 부풀지 않는지 확인.

2. ATR blocked metric dedup 보강
   - 문제: `atr_blocked_rows`와 `atr_blocked_runup_avg`가 row count/row avg 기준으로 남아 있었다.
   - 조치: `atr_dedup` CTE를 추가하고 `ticker|date` 단위로 ATR blocked sample을 계산한다.
   - 테스트: duplicate ATR blocked row를 fixture에 추가하고 `atr_blocked_rows == 1` 확인.

3. `trade_ready_forward_3d_average` dedup 보강
   - 문제: trade_ready count는 DISTINCT였지만 forward average는 row AVG로 남아 있어 duplicate ticker/day가 평균에 가중치로 들어갈 수 있었다.
   - 조치: `trade_ready_forward_dedup` CTE를 추가하고 unique ticker/day 단위 forward average를 계산한다.
   - 테스트: 같은 ticker/day forward row를 중복 추가해도 `trade_ready_forward_3d_average`가 부풀지 않는지 확인.

4. R1/R2 active lesson 경로 통합
   - 문제: selection은 `build_active_lesson_context()`를 쓰지만 R1/R2는 `_load_lesson_candidate_summary()` 문자열을 받아 별도 경로로 동작했다.
   - 조치: `get_three_judgments()` 내부에서 직접 `build_active_lesson_context()`를 호출하도록 변경했다.
   - 결과: selection/R1/R2가 active lesson enable/shadow/source cap/ignored_reasons를 같은 방식으로 쓴다.
   - 테스트: `test_get_three_judgments_uses_active_lesson_context_for_r1_and_r2` 추가.

최종 재검증:

```text
python -m pytest -q
1266 passed, 2 skipped, 2 warnings in 55.66s
```
