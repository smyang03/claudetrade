# 시스템 품질 개선 구현/QA 리포트

작성일: 2026-05-16  
기준 요구서: `docs/reports/system_quality_improvement_report_20260516.md`

## 결론

요구서의 P0~P2 항목을 코드 기준으로 반영하고 QA를 완료했다. 핵심 변화는 “Claude가 매일 처음부터 판단하는 구조”를 줄이고, 이미 계산되던 KR quality/flow/RS/cohort 신호를 selection/trainer/관측 경로로 연결한 것이다.

운영 파일 기준으로 개선 파라미터는 모두 켜져 있다. 다만 현재 실행 중인 live bot은 2026-05-16 02:13 KST에 시작된 이전 스냅샷이라, 새 설정을 실제 루프에 완전히 반영하려면 운영 재시작이 필요하다. 재시작은 graceful stop 경로가 명확하지 않아 강제 종료하지 않았다.

## 재검수 반영

재검수에서 “코드에서 실제로 쓰는 개선 파라미터가 preflight effective values와 drift 감시에 모두 잡히는가”를 다시 확인했다. 아래 누락을 발견해 보강했다.

| 발견 | 조치 | 검증 |
|---|---|---|
| `ACTIVE_LESSONS_ANALYST_MAX_CHARS`가 preflight key에 없음 | `tools/live_preflight.py`의 `LIVE_CONFIG_KEYS`, `RUNTIME_CONFIG_DRIFT_KEYS`에 추가 | effective value `3000` 확인 |
| `KR_CANDIDATE_FLOW_PREFETCH_SLEEP_SEC`가 preflight key에 없음 | live/drift key에 추가 | effective value `0.2` 확인 |
| `KR_INDEX_CACHE_MAX_AGE_HOURS`, `KR_INDEX_CACHE_REQUIRE_PREWARM`가 preflight key에 없음 | live/drift key에 추가 | effective values `24`, `true` 확인 |
| `ENABLE_KR_CANDIDATE_QUALITY_SHADOW`는 코드에서 쓰지만 명시 config가 없음 | `.env.live`, `config/v2_start_config.json`, preflight key에 추가 | effective value `true` 확인 |
| `_build_selection_retry_prompt()`에 active lesson 500자 slice 잔존 | `_lesson_context_for_prompt(..., scope="selection")` 사용으로 통일 | code grep no-match, retry prompt marker 테스트 추가 |

재검수 후 결과:

```text
python -m pytest -q
1259 passed, 2 skipped, 2 warnings

live_preflight summary:
ok=true, fail_count=0, warn_count=11

live_guardian summary:
ok=true, gate=ALLOW_START, hard_fail=0, soft_fail=7, smoke_ok=true
```

## 개선 전/후 차이

| 영역 | 개선 전 | 개선 후 |
|---|---|---|
| Active Lessons | `ACTIVE_LESSONS_ENABLED=false`, shadow 상태. Claude가 lesson을 보지 못함 | enabled/shadow=false, max 3000자. R1/selection/R2에 lesson context 주입 |
| lesson truncation | lesson context 500자 fallback 경로 존재 | 공통 helper로 통일. 500자 hard slice 제거 |
| R2 debate | lesson context 인자 없음 | `call_analyst_debate(..., lesson_context=...)` 추가, R2 lesson section 저장 |
| Bear/Neutral 모델 | 공용 `R1_MODEL` 중심 | `BULL_R1_MODEL`, `BEAR_R1_MODEL`, `NEUTRAL_R1_MODEL` role-specific routing |
| US Bear persona | 별도 persona가 있으나 모델 분리 명세와 연결 약함 | US bear persona 보존 테스트 추가 |
| Neutral confidence | prompt 문구상 0.75 초과 금지 | 불명확 시 0.75 제한, 명확한 경우 0.85 허용 |
| KR quality/cohort | Claude quality hint에 cohort reliability 미노출 | `cohort=low/mid/high`, `n=`, `tier=` compact hint 추가 |
| KR flow | flow cache 코드 존재하나 후보 경로에서 미호출 | 후보 확정 전 `update_candidate_flow_cache()` prefetch 연결 |
| KR RS index | cache 미생성 시 live 중 fetch 위험 | `tools/prewarm_kr_index_cache.py` 추가 및 KOSPI/KOSDAQ 120 rows prewarm 확인 |
| trainer score | `candidate_quality_score`가 trainer score에 미반영 | env-gated quality bonus 추가, data gap 시 dampen |
| KR screener rank | `abs(change_rate)`로 급락 종목도 momentum 점수 상승 | signed change 기반 bonus, 하락은 `reversal_watch` metadata로 분리 |
| KR post-rank | quality/cohort가 raw screen 순서에 영향 없음 | shadow-only post-rank metadata 기록, enforce는 off |
| US quality | quality enrichment 없음 | US quality shadow metric 추가, rank/trainer에는 미반영 |
| US losers quota | mode별 quota가 기계적 | `US_DYNAMIC_LOSERS_QUOTA_ENABLED=true` 시 AGGRESSIVE losers 축소, DEFENSIVE losers 유지 |
| unanimous guard | mismatch는 있었지만 override count가 핵심 지표로 약함 | `unanimous_override_events/count` 기록, ops review metric/trigger 추가 |
| 운영 config 감시 | 신규 키가 preflight/drift 감시에 없음 | live preflight keys와 runtime drift keys에 추가 |

## 코드 레벨 반영

| 요구 | 파일 | 반영 |
|---|---|---|
| P0-1 Active lessons 통일 | `minority_report/active_lessons.py`, `minority_report/analysts.py` | max 3000자, R1/R2/selection helper, raw-call metadata |
| P0-2 model routing | `minority_report/analysts.py` | Bear/Neutral role env, US bear persona 보존 |
| P0-3 Neutral 완화 | `minority_report/analysts.py` | confidence prompt 문구 완화 |
| P0-4 quality/cohort hint | `minority_report/analysts.py` | cohort/tier/sample hint 추가 |
| P1-1 RS cache prewarm | `tools/prewarm_kr_index_cache.py` | 직접 실행 가능하도록 repo root import 보강 |
| P1-2 flow prefetch | `trading_bot.py` | KR 후보 경로에서 flow cache prefetch |
| P1-3 quality trainer 연결 | `runtime/candidate_quality_trainer.py` | env-gated quality score bonus |
| P1-4 consensus 관측 | `trading_bot.py` | unanimous override event/count + ops metric |
| P1-5 US quality shadow | `runtime/us_candidate_quality.py`, `trading_bot.py` | shadow-only metric |
| P2-1 signed change | `kis_api.py` | `abs(change_rate)` 제거, move bucket metadata |
| P2-2 post-rank shadow | `runtime/candidate_post_rank.py`, `trading_bot.py` | adjusted rank metadata, enforce off |
| P2-3 US quota | `kis_api.py` | dynamic losers quota |
| P2-4 PEAD gate | `phase1_trainer/digest_builder.py`, tests | 기존 CLAUDE.md gate와 일치 확인. surprise prompt leak 방지 테스트 유지 |
| Config/preflight | `.env.live`, `config/v2_start_config.json`, `tools/live_preflight.py` | 개선 파라미터 enabled 상태로 동기화 |

## Claude 데이터/토큰 검증

검증한 항목:

- R1 raw call `extra`에 `lesson_context`, `model_route`, `token_budget`, `persona`가 저장된다.
- R2 raw call `extra`에 `lesson_context`, `model_route`, `token_budget`, `persona`가 저장된다.
- R1 lesson context는 500자에서 잘리지 않고 3000자 한도 정책을 따른다.
- R2 lesson context는 `ACTIVE_LESSONS_DEBATE_MAX_CHARS=1200` 정책을 따른다.
- selection retry에도 active lesson metadata가 남는다.
- preflight에서 selection token budget이 충분함을 확인했다: `CLAUDE_SELECTION_MAX_TOKENS=6000`, `CLAUDE_SELECTION_RETRY_MAX_TOKENS=3500`.
- analyst token budget은 config 기준 R1 700, R2 900으로 raw-call metadata에 남는다.

관련 테스트:

- `tests/test_active_lessons.py`
- `tests/test_kr_candidate_quality_prompt.py`
- `tests/test_candidate_quality_trainer.py`
- `test_trading_improvements.py::TradingBotGateTests::test_apply_consensus_guards_normalizes_reused_opposite_consensus`
- `test_trading_improvements.py::OpsReviewSnapshotTests::test_build_ops_review_snapshot_aggregates_review_metrics`

## QA 결과

실행:

```text
python -m pytest -q
```

결과:

```text
1258 passed, 2 skipped, 2 warnings
```

추가 검증:

```text
python -m py_compile minority_report/analysts.py trading_bot.py tools/prewarm_kr_index_cache.py
python tools/prewarm_kr_index_cache.py --json
python tools/live_preflight.py --mode live --skip-dashboard --json
python tools/live_guardian.py --mode live --env .env.live --skip-dashboard --max-iterations 1 --json
```

결과 요약:

| 검증 | 결과 |
|---|---|
| py_compile | PASS |
| KR index prewarm | KOSPI 120 rows, KOSDAQ 120 rows, ok=true |
| live preflight | ok=true, fail_count=0, warn_count=11 |
| live guardian | gate=ALLOW_START, hard_fail=0, soft_fail=7, accepted_exception=4 |
| live smoke | KR/US smoke PASS |
| KR sizing smoke | max_positions=20, fixed_order_krw=300000, qty=12 at 25,000 KRW |
| US sizing smoke | max_positions=20, fixed_order_krw=300000, qty=8 at 33,750 KRW equivalent |

## 운영 상태와 주의사항

현재 운영 검수는 hard fail 없이 통과했다. 다만 아래 soft/warn은 실제 다음 장 전 정리 대상이다.

| 항목 | 상태 | 판단 |
|---|---|---|
| runtime config drift | WARN | 현재 live bot이 02:13 이전 설정으로 실행 중. 새 파라미터 적용에는 재시작 필요 |
| PathB KR live gate | WARN | `PATHB_KR_LIVE_ENABLED=false` 상태. 기존 정책으로 보임 |
| US-specific KIS credentials | WARN | US key/secret 없음, KR 공용 fallback 사용 중 |
| unresolved ORDER_UNKNOWN | WARN | 이전 세션 19건. current session은 0건 |
| stale PathB active runs | WARN | 이전 세션 active rows 23건 |
| WAIT_TIMING runtime evidence | WARN | enum은 있으나 runtime wiring 증거 부족 |
| market calendar | accepted | 2026-05-16은 토요일. 주말 경고 정상 |

## 요구서 대비 누락/차이

최종 대조 결과, 코드 반영 누락으로 남긴 항목은 없다. 대조 중 발견된 두 항목은 추가 보강했다.

| 발견 | 조치 |
|---|---|
| `tools/prewarm_kr_index_cache.py` 직접 실행 시 `bot` import 실패 | repo root를 `sys.path`에 추가, 실제 prewarm 실행 통과 |
| unanimous override count가 ops 핵심 지표로 약함 | `unanimous_override_events/count`, ops metric, trigger, 테스트 추가 |

구현 형태 차이는 아래와 같다.

| 요구서 표현 | 실제 구현 |
|---|---|
| RS cache prewarm은 CLI 또는 preflight helper | CLI로 구현하고 실제 prewarm까지 완료 |
| post-rank는 enforce 전 shadow | `KR_CANDIDATE_POST_RANK_ENFORCE=false`로 순서 변경 없이 metadata만 기록 |
| US quality rank 반영은 P2 이후 판단 | 현재는 shadow-only. trainer/rank 반영 없음 |
| PEAD live 전환 | 기존 `CLAUDE.md` manual gate를 유지. surprise prompt 노출은 checklist 통과 전 차단 |

## 운영 테스트 해석

오늘 실행한 것은 주문을 발생시키는 실거래 주문 테스트가 아니라 live 설정/DB/broker/sizing/smoke를 확인하는 운영 smoke 테스트다. 현재는 주말이고 running bot이 이전 설정으로 떠 있으므로, 다음 실제 장에서 이 개선 사항을 관찰하려면 live bot을 새 설정으로 재시작한 뒤 새 `effective_config_*.json`에서 아래 값이 반영됐는지 확인해야 한다.

필수 확인 키:

```text
ACTIVE_LESSONS_ENABLED=true
ACTIVE_LESSONS_SHADOW=false
ACTIVE_LESSONS_MAX_CHARS=3000
ACTIVE_LESSONS_DEBATE_ENABLED=true
BEAR_R1_MODEL=claude-sonnet-4-6
NEUTRAL_R1_MODEL=claude-sonnet-4-6
ENABLE_KR_CANDIDATE_QUALITY_PROMPT=true
ENABLE_KR_CANDIDATE_RS_INDEX_CACHE=true
ENABLE_KR_CANDIDATE_FLOW_PREFETCH=true
CANDIDATE_TRAINER_QUALITY_SCORE_ENABLED=true
US_QUALITY_SHADOW_ENABLED=true
KR_CANDIDATE_POST_RANK_ENABLED=true
KR_CANDIDATE_POST_RANK_ENFORCE=false
US_DYNAMIC_LOSERS_QUOTA_ENABLED=true
KR_DAILY_ENTRY_CAP=40
KR_MAX_POSITIONS=20
US_MAX_POSITIONS=20
```
