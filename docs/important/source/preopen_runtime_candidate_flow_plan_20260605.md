# 장전/장후 스크리너 후보 승격 흐름 개선 요구서

작성일: 2026-06-05  
대상: US 장전 스크리너 후보 DB 유지 및 장후 실행 후보 승격 흐름  
상태: 요구서 완성 / 구현 전 검토본  

## 1. 목적

장전 스크리너 후보는 운영 체크와 사후 검증을 위해 반드시 DB로 보존한다. 다만 장전 후보가 곧바로 매매 후보 티커 또는 `trade_ready`로 승격되면 개장 직후 갭 되돌림, 유동성 왜곡, 프리마켓 과열을 그대로 실행 경로에 태우는 위험이 있다.

따라서 목표 흐름은 다음과 같다.

1. 장전 후보는 DB와 shadow 평가 대상으로 유지한다.
2. 장후 스크리너와 개장 후 evidence를 실제 후보 판단의 1차 기준으로 둔다.
3. 장전 후보는 장후 재확인 전까지 `WATCH` 또는 `DISCOVERY` 역할로만 사용한다.
4. 실제 주문 후보는 장후 Claude `trade_ready`와 evidence/risk/broker truth를 모두 통과한 종목만 인정한다.

## 2. 직접 수정 범위

이번 요구서는 설계 문서다. 구현 시 직접 수정 가능한 범위는 아래로 제한한다.

- 장전 후보 DB/리포트: `preopen/continuation_shadow.py`, `tools/preopen_continuation_shadow.py`, `tools/preopen_continuation_shadow_report.py`
- 장전 후보 origin 태그/프롬프트 노출이 필요한 경우: `trading_bot.py`의 candidate preparation 주변부
- 테스트: `tests/test_preopen_continuation_shadow.py`, `tests/test_preopen_pin_universe.py`, `tests/test_preopen_opening_role_separation.py`, 필요 시 candidate action ceiling 관련 테스트
- 문서/리포트: `docs/important/source/`, `docs/reports/`

## 3. 건드리지 않을 보호 영역

아래는 이번 요구 범위에서 변경하지 않는다.

- PathB entry/exit, profit ladder, pre-close 청산, hold advisor
- broker truth fail-closed, KIS order normalization, zero-holding stale reconcile
- 주문 수량/예산/리스크 계산
- `DISCOVERY_ALLOW_BUY_READY`, `DISCOVERY_ALLOW_PROBE_READY`, `DISCOVERY_ALLOW_PULLBACK_WAIT` 승인 없는 변경
- `.env*`, `config/v2_start_config.json`, `state/brain.json`
- 장전 후보를 `trade_ready`, `_pathb_wait_tickers`, PathB plan registration으로 직접 연결하는 경로

## 4. 현재 구조 검토

| 항목 | 현재 확인 내용 | 판단 |
|---|---|---|
| 장전 후보 저장 | `state/preopen_US_YYYYMMDD.json`, `logs/preopen/*_US_candidates.jsonl` 존재 | 유지 필요 |
| 장전 후보 DB 초안 | `preopen/continuation_shadow.py`가 `data/preopen_continuation.db` 스키마와 collect/feature/eval/backfill 제공 | 활용 가능 |
| 장전 selection | `PREOPEN_WATCH` phase는 `_force_preopen_watch_only()`로 `trade_ready=[]` 강제 | 안전장치 적합 |
| 장 시작 후 스크리너 | `_screen_market_candidates()` 후 `select_tickers()`가 장후 후보 판단 수행 | 실행 후보 기준으로 적합 |
| preopen hard pin | 장 시작 후 일정 시간 universe/prompt에 병합될 수 있음 | role/ceiling 경계 필요 |
| DISCOVERY ceiling | `candidate_pool_role=DISCOVERY`는 `_apply_candidate_pool_role_ceiling()`에서 WATCH로 강등 가능 | 기존 구조 재사용 적합 |
| 운영 config | `DISCOVERY_ALLOW_*` 3종 false, `INTRADAY_EVIDENCE_FAIL_CLOSED=true` | 현재 요구와 일치 |

## 5. 요구사항

### REQ-01 장전 후보 DB는 필수다

장전 스크리너 결과는 매 세션 DB에 보존해야 한다.

- 필수 필드: `session_date`, `market`, `ticker`, `preopen_rank`, `gap_pct`, `extended_dollar_volume`, `preopen_score`, `eligible`, `exclusion_reason`, `source_row_hash`
- 권장 DB: `data/preopen_continuation.db`
- source state/log는 계속 보존한다.

검토 결과: 맞음. 운영 체크와 사후 비교에 장전 후보 DB가 필요하고, 기존 shadow 모듈이 이 역할을 이미 대부분 제공한다.

### REQ-02 장전 후보는 직접 실행 후보가 아니다

장전 후보 DB에 있는 종목은 그 사실만으로 `today_tickers`, `trade_ready`, `_pathb_wait_tickers`, 주문 후보가 되면 안 된다.

허용:

- 장전 watch seed
- 장후 비교/리포트
- 장후 프롬프트에 `DISCOVERY/WATCH`로 제한 노출

금지:

- 장전 DB 후보를 `trade_ready`로 직접 삽입
- 장전 DB 후보를 PathB wait/price plan으로 직접 등록
- 장전 premarket price target을 live 주문 근거로 전달

검토 결과: 맞음. 현재 preopen phase 강제 watch-only와도 일치한다.

### REQ-03 실행 후보의 1차 기준은 장후 스크리너다

장후 또는 장중 `_screen_market_candidates()` 결과를 실제 실행 후보 pool의 1차 기준으로 둔다.

실행 후보로 인정되는 기본 조건:

- 장후 fresh screener에 포함
- intraday evidence가 fresh 또는 complete
- Claude selection에서 watchlist/trade_ready 판단
- 이후 risk/broker truth gate 통과

검토 결과: 맞음. 2026-06-01~2026-06-04 비교에서도 장전 Top15와 장중 `trade_ready` 일치율은 낮고 불안정했다.

### REQ-04 장전 후보 승격은 재확인 조건을 통과해야 한다

장전 후보가 장후 후보로 올라오려면 최소 하나의 재확인 조건이 필요하다.

승격 후보 조건:

- 장후 스크리너에 다시 등장
- 개장 후 30분 feature snapshot에서 continuation 확인
- 장전 continuation shadow eval이 `PROMOTE`이고, 최소 샘플 기준을 충족한 정책이 승인됨

최소 샘플 기준:

- 운영 기간: 7거래일 이상
- eligible 장전 후보: 50개 이상
- Claude 평가 후보: 35개 이상
- `PROMOTE` 표본: 8개 이상
- `DROP` 표본: 8개 이상
- outcome complete 비율: 90% 이상
- parse success rate: 95% 이상

단, 위 조건을 만족해도 기본 권한은 `DISCOVERY/WATCH`다. 주문 권한은 별도 승인 전까지 없다.

검토 결과: 맞음. 장전 후보 중 개장 직후 되돌림이 큰 케이스가 반복되므로 post-open confirmation이 필요하다.

### REQ-05 `candidate_pool_role=DISCOVERY`를 기본 승격 역할로 사용한다

장전 후보가 장후 프롬프트에 보조 후보로 들어갈 때 새 role을 만들지 않고 기존 `DISCOVERY` 역할을 사용한다.

필수 태그:

- `candidate_pool_role=DISCOVERY`
- `discovery_signal_family=preopen_continuation`
- `discovery_reason=preopen_post_open_confirmed` 또는 `preopen_shadow_promote`
- `discovery_action_ceiling=WATCH`

검토 결과: 맞음. 기존 audit, prompt, ceiling, learning DB가 이미 `DISCOVERY` 필드를 알고 있다.

충돌 검토: 기존 DISCOVERY 경로와 같은 role을 쓰지만 `discovery_signal_family=preopen_continuation`으로 출처를 구분한다. 현재 ceiling 동작은 signal family가 아니라 `candidate_pool_role=DISCOVERY`와 `DISCOVERY_ALLOW_*` 설정을 기준으로 적용한다. 따라서 signal family는 audit/log/prompt attribution 용도이며 주문 권한을 열지 않는다.

### REQ-06 DISCOVERY 후보는 주문 권한이 없다

아래 config가 false인 동안 DISCOVERY 후보는 `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT`, `ADD_READY`로 실행될 수 없다.

- `DISCOVERY_ALLOW_BUY_READY=false`
- `DISCOVERY_ALLOW_PROBE_READY=false`
- `DISCOVERY_ALLOW_PULLBACK_WAIT=false`

검토 결과: 맞음. 현재 `config/v2_start_config.json`도 false이며, 기존 ceiling guard와 일치한다.

테스트 연결: `tests/test_candidate_action_live_mapping.py`는 `DISCOVERY_ALLOW_*` 기본 false 설정과 DISCOVERY action ceiling 적용을 검증한다. `tests/test_candidate_discovery_overlay.py`와 `tests/test_candidate_quality_trainer.py`는 DISCOVERY 후보가 프롬프트에 role/ceiling 태그로 노출되는 계약을 검증한다. 구현 착수 전후로 이 테스트들을 REQ-06 검증 범위에 포함한다.

### REQ-07 hard pin은 execution grant가 아니라 confirmation hint다

현재 preopen hard pin은 장 시작 후 일정 시간 universe에 섞일 수 있다. 이 동작은 "후보를 보게 하는 기능"이지 "매수 권한"이 아니다.

구현 시 요구:

- hard pin row에 `preopen_pin_require_confirmation=True` 유지
- Claude 프롬프트에는 confirmation-required 힌트로만 표시
- 장후 fresh evidence 없으면 WATCH 유지
- hard pin이 top-N을 밀어내는 경우 audit/log에 `pin_displaced_tickers` 기록 유지

검토 결과: 맞음. 기존 `test_preopen_pin_universe.py`는 hard pin이 core 뒤에 들어가고 top-N을 유지하는 현재 계약을 검증한다. 다만 이 계약을 실행 권한으로 오해하면 안 된다.

### REQ-08 장전 후보의 장후 outcome을 별도 측정한다

장전 후보마다 개장 후 성과를 최소 30분, 가능하면 60/120/close까지 기록한다.

필수 outcome:

- `ret_30m`
- `mfe_from_open_pct`
- `mae_from_open_pct`
- `ret_close`
- 실제 selection/watch/trade_ready/ordered 여부

검토 결과: 맞음. `preopen_continuation_shadow.py`는 `preopen_feature_snapshots`, `preopen_outcomes`, backfill 구조를 제공한다.

### REQ-09 Claude shadow eval은 미래 정보를 보지 않는다

장전 후보 continuation 평가용 Claude 호출은 visible early-session feature만 사용한다.

금지:

- close return, future high/low, actual ordered outcome을 prompt에 제공
- live selection 결과를 정답처럼 제공
- shadow eval 결과를 즉시 주문 경로에 반영

검토 결과: 맞음. 현재 parser/strict JSON/test 구조가 shadow-only 방향과 일치한다.

### REQ-10 장후 실행 후보와 장전 후보의 명칭을 분리한다

운영 용어를 아래처럼 고정한다.

| 용어 | 의미 |
|---|---|
| `preopen_candidates` | 장전 DB 후보, 검증/관찰용 |
| `runtime_candidates` | 장후 스크리너 기반 후보 |
| `discovery_watch` | 장전 후보가 장후 참고용으로 재노출된 상태 |
| `watchlist` | Claude가 현재 보고 싶은 후보 |
| `trade_ready` | 실행 검토 후보 |

검토 결과: 맞음. 용어 분리가 없으면 "장전 후보 DB"와 "매수 후보 티커"가 혼동된다.

### REQ-11 대시보드는 세 버킷을 분리 표시한다

대시보드 개선 시 아래를 섞지 않는다.

- 장전 DB 후보 수
- 장후 runtime 후보 수
- 현재 `trade_ready` 수

권장 표시:

- `preopen candidates`: count, eligible, top tickers
- `runtime screen`: latest count/source/freshness
- `discovery watch`: count, ceiling=WATCH
- `trade_ready`: actual executable review candidates

검토 결과: 맞음. 표시 개선은 가능하지만 주문 경로 변경은 아니다.

acceptance 기준: Phase 1에서는 로그/리포트에서 세 버킷이 분리되면 완료로 본다. 대시보드 분리 표시는 Phase 2 후속 작업이며, Phase 1 배포 차단 조건으로 두지 않는다.

### REQ-12 스케줄은 단계별로 분리한다

권장 운영 스케줄:

| 시점 | 작업 | 주문 영향 |
|---|---|---|
| 장전 | collect preopen candidates | 없음 |
| 개장 직후 | preopen watch-only selection | 없음 |
| 개장 +30분 | feature snapshot | 없음 |
| 장후/장중 | runtime screener + Claude selection | 있음, 기존 gate 통과 시 |
| 장마감 후 | outcome backfill/report | 없음 |

검토 결과: 맞음. 현재 CLI dry-run과 테스트는 가능하고, scheduler 연동은 별도 구현 단계로 둔다.

### REQ-13 구현 단계는 shadow-first로 진행한다

구현 순서:

1. 문서/요구 확정
2. 장전 후보 DB collect/feature/backfill 운영
3. 7거래일 이상 리포트로 `shadow_continue`, `consider_discovery_watch`, `block_or_discard` 판단
4. 승인 후 `DISCOVERY/WATCH` 프롬프트 노출 검토
5. 별도 승인 전까지 주문 권한 확대 금지

검토 결과: 맞음. 기존 수익 엔진과 주문 safety를 건드리지 않고 검증할 수 있다.

### REQ-14 acceptance criteria

요구 구현이 완료되려면 아래를 만족해야 한다.

- 장전 후보 DB row가 세션별로 생성된다.
- 장전 후보 DB row가 직접 `trade_ready`/PathB wait/order로 연결되지 않는다.
- 장후 screener 결과가 actual runtime candidate source로 기록된다.
- 장전 후보가 장후 프롬프트에 들어가도 `DISCOVERY/WATCH` ceiling이 적용된다.
- Phase 1에서는 장전/장후/실행 후보 count가 로그 또는 리포트에서 분리된다.
- 대시보드 분리 표시는 Phase 2 후속 acceptance로 둔다.
- 관련 테스트가 통과한다.

검토 결과: 맞음. 현재 코드 구조와 운영 안전 계약을 모두 만족하는 완료 기준이다.

## 6. 항목별 재검토 결론

| ID | 1차 요구 | 검토 결과 | 재검토 결론 |
|---|---|---|---|
| REQ-01 | 장전 후보 DB 필수 | 맞음 | 확정 |
| REQ-02 | 장전 후보 직접 실행 금지 | 맞음 | 확정 |
| REQ-03 | 장후 스크리너가 실행 후보 기준 | 맞음 | 확정 |
| REQ-04 | 장전 후보 승격에는 재확인 필요 | 맞음, 최소 표본 기준 숫자화 필요 | 7거래일/50 eligible/35 eval/8 PROMOTE/8 DROP 기준으로 확정 |
| REQ-05 | DISCOVERY role 재사용 | 맞음 | 확정 |
| REQ-06 | DISCOVERY 주문 권한 없음 | 맞음, 테스트 연결 명시 필요 | `test_candidate_action_live_mapping.py` 포함 확정 |
| REQ-07 | hard pin은 confirmation hint | 맞음, 현재 universe pin 계약 주의 | 확정 |
| REQ-08 | outcome 별도 측정 | 맞음 | 확정 |
| REQ-09 | shadow eval 미래정보 금지 | 맞음 | 확정 |
| REQ-10 | 용어 분리 | 맞음 | 확정 |
| REQ-11 | 대시보드 분리 표시 | 맞음, 구현은 후순위 | Phase 1 로그/리포트, Phase 2 대시보드로 확정 |
| REQ-12 | 스케줄 분리 | 맞음, scheduler 연동은 별도 | 확정 |
| REQ-13 | shadow-first | 맞음 | 확정 |
| REQ-14 | acceptance criteria | 맞음, count 표시 범위 명확화 필요 | Phase 1 로그/리포트 기준으로 확정 |

재검토 후 변경한 핵심 표현:

- "장전 후보를 티커로 올린다"는 표현을 제거하고, "장후 재확인된 경우 `DISCOVERY/WATCH`로 노출"로 제한했다.
- "hard pin"을 후보 승격 권한이 아니라 confirmation hint로 명시했다.
- "실행 후보"의 기준을 장후 screener + Claude `trade_ready` + evidence/risk/broker truth로 고정했다.
- 기존 DISCOVERY ceiling guard를 재사용하는 방향으로 확정했다.
- 최소 샘플 기준을 거래일 수와 건수로 명시했다.
- Phase 1 acceptance는 로그/리포트 분리로 충족하고, 대시보드는 Phase 2로 분리했다.
- DISCOVERY signal family는 출처 태그이며 ceiling은 role/config 기준으로 동작한다고 명시했다.

## 7. 구현 시 검증 명령

문서 검토 중 이미 실행한 관련 검증:

```powershell
python -m pytest tests/test_preopen_continuation_shadow.py tests/test_preopen_pin_universe.py tests/test_preopen_opening_role_separation.py -q
```

결과: `52 passed`

Dry-run 확인:

```powershell
python tools/preopen_continuation_shadow.py --market US --date 2026-06-04 --step all --dry-run --no-claude
```

확인 내용:

- source 후보 60개
- deterministic eligible 23개
- dry-run이라 DB write 없음

구현 후 추가 권장 검증:

```powershell
python -m pytest tests/test_preopen_continuation_shadow.py tests/test_preopen_pin_universe.py tests/test_preopen_opening_role_separation.py tests/test_candidate_action_live_mapping.py -q
python -m pytest tests/test_candidate_discovery_overlay.py tests/test_candidate_quality_trainer.py -q
python -m py_compile trading_bot.py preopen/continuation_shadow.py tools/preopen_continuation_shadow.py
```

## 8. 최종 요구 결론

장전 후보 DB는 유지한다.  
장전 후보는 직접 실행 후보가 아니다.  
장후 스크리너 이후 재확인된 후보만 runtime candidate로 본다.  
장전 후보를 장후에 참고로 노출할 경우에도 기본 권한은 `DISCOVERY/WATCH`다.  
실제 매매 후보는 장후 Claude `trade_ready`와 기존 safety gate를 모두 통과한 종목만 인정한다.

## 9. 운영 DB 오염 방지 확정

운영 시 preopen continuation shadow가 쓸 수 있는 출력 DB는 `preopen_continuation*.db` 이름으로 제한한다.
`ticker_selection_log.db`, `candidate_audit.db`, `decisions.db`, `v2_event_store.db`, `intraday_strategy_log.db`,
`agent_call_events.db`는 출력 DB로 지정되면 즉시 fail-closed 처리한다.

backfill source DB는 SQLite `mode=ro&immutable=1` 및 `PRAGMA query_only=ON`으로만 연다.
따라서 이 플로우는 기존 운영 DB를 수정하거나 WAL/SHM 생성이 필요한 연결을 만들지 않는다.

운영 중 전체 `python -m pytest -q`는 금지한다. 전체 QA에는 repo `data/` 산출물을 쓰는 테스트가 포함되어
있으므로 운영 DB 무오염 검증은 targeted regression과 temp DB 시뮬레이션으로만 진행한다.
