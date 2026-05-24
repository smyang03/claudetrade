# Debate Metadata and Watchdog Safety 수정 내역 정리

작성일: 2026-05-25

관련 요구서:

- `docs/important/source/debate_watchdog_safety_dev_requirements_20260525.md`

## 1. 수정한 내용

### 1.1 Debate metadata 소비/저장 로직 보강

대상:

- `claude_memory/brain.py`
- `tests/test_analyst_unavailable_quorum.py`

수정 내용:

- `r1/r2` 실제 stance를 비교하는 `_actual_debate_changes()` helper를 추가했다.
- `save_debate_result()`가 `r2.changed=true`만으로 `changes`를 만들지 않도록 변경했다.
- `get_debate_summary()`가 저장된 `changes`와 `consensus_shifted`를 그대로 신뢰하지 않고, 실제 `r1/r2` stance 비교 결과로 changed/kept 통계와 표시를 계산하도록 변경했다.
- 깨진 과거 entry가 있어도 summary에서 stale `NEUTRAL -> MILD_BULL` 같은 잘못된 변경 표시가 나오지 않는 테스트를 추가했다.
- `r2.changed=true`지만 stance가 유지된 경우 `changes=[]`, `consensus_shifted=false`가 되는 테스트를 추가했다.

왜 했는가:

- 리뷰 지적처럼 `changes`, `r2.*.stance`, `consensus_shifted`가 서로 다르면 future prompt의 debate hit-rate/keep-rate 컨텍스트가 왜곡될 수 있다.
- 상태 파일 하나를 고치는 것만으로는 다음 partial write나 수동 편집에서 같은 문제가 재발할 수 있다.
- 따라서 저장 시 오염을 줄이고, 요약 시에도 실제 stance 비교를 truth로 삼는 방어 로직을 넣었다.

### 1.2 Watchdog guardian start guard 보강

대상:

- `auto_start_if_missing.bat`
- `tools/live_guardian.py`
- `tools/live_preflight.py`
- `tests/test_live_guardian.py`
- `tests/test_live_process_inventory.py`
- `tests/test_watchdog_launcher.py`

수정 내용:

- `auto_start_if_missing.bat`를 placeholder 템플릿(`YourProgram.exe`, `C:\Path\To\YourProgram.exe`)에서 실제 ClaudeTrade watchdog launcher로 바꿨다.
- 배치 파일이 `tasklist /FI "IMAGENAME ..."`로 직접 process image를 검사하지 않고, `tools/live_guardian.py --watch --ensure-bot`으로 위임하도록 했다.
- 배치 파일에 `paper|live` mode 인자, `CLAUDETRADE_BOT_MODE`, `CLAUDETRADE_PYTHON`, `CLAUDETRADE_WATCHDOG_INTERVAL_SEC` override를 추가했다.
- guardian에 `--ensure-bot` 흐름을 추가/검증했다. 이미 실행 중이면 정상 상태로 보고 start를 skip하며, 없을 때만 시작한다.
- `run_guardian_once()`가 PID lock뿐 아니라 `runtime.process_inventory`의 command-line process rows도 확인하도록 보강했다.
- 현재 mode와 맞는 bot process가 이미 있으면:
  - `--ensure-bot`: `start_bot` action을 `SKIP` 처리한다.
  - `--start-bot`: 중복 시작 위험으로 hard fail 처리한다.
- live mode에서 process inventory를 사용할 수 없으면 bot start를 hard fail로 막는다.
- `python trading_bot.py`처럼 `--paper`가 없는 기본 실행도 paper bot으로 분류하도록 `_classify_repo_process_role()`을 수정했다.
- launcher 테스트에 `tasklist /FI "IMAGENAME` 패턴이 남아 있지 않은지 확인하는 assertion을 추가했다.

왜 했는가:

- Windows `tasklist`의 image name은 `trading_bot.py`가 아니라 `python.exe`이므로 image-name 기반 watchdog은 Python 봇에 안전하지 않다.
- PID 파일만 보면 `PID 파일 없음 + 실제 봇 프로세스 실행 중` 상황에서 중복 봇을 시작할 수 있다.
- command line inventory에서 `trading_bot.py`와 mode를 같이 확인해야 무관한 Python 프로세스와 실제 봇을 구분할 수 있다.

### 1.3 개발 요구서 작성

대상:

- `docs/important/source/debate_watchdog_safety_dev_requirements_20260525.md`

수정 내용:

- debate metadata와 watchdog 안전성 개선 요구사항을 문서화했다.
- 구현 범위, 비범위, 테스트 요구사항, 완료 기준을 명시했다.

왜 했는가:

- 리뷰 이슈를 임시 패치가 아니라 재발 방지 요구사항으로 남기기 위해서다.
- 이후 운영/리뷰에서 “어떤 조건을 만족하면 완료인지”를 확인할 수 있게 하기 위해서다.

## 2. 수정하지 않은 내용

### 2.1 `state/brain.json`

수정하지 않은 내용:

- 문제 entry의 `changes`를 직접 삭제하지 않았다.
- `consensus_shifted`도 직접 수정하지 않았다.

왜 수정하지 않았는가:

- `state/brain.json`은 정책 메모리이며 런타임 truth나 원장이 아니다.
- 저장소 운영 가이드에서 `state/brain.json` 자동 변경은 승인형 워크플로우 안정화 전까지 보류하라고 되어 있다.
- 이번에는 소비 코드가 깨진 metadata를 신뢰하지 않도록 방어했기 때문에 prompt 오염은 코드 레벨에서 차단된다.

수정이 필요한가:

- 필수는 아니다. 코드 방어만으로 기능상 문제는 막힌다.
- 리뷰 closure를 더 깔끔하게 하려면 해당 US `2026-05-22` entry만 수동 정합성 보정하는 것은 가능하다.
- 보정 범위는 `changes=[]`, `consensus_shifted=false`처럼 실제 `r2.neutral.stance=NEUTRAL`과 metadata를 맞추는 수준이어야 한다.

### 2.2 운영 파라미터와 live 설정

수정하지 않은 내용:

- `.env.live`
- `.env.paper`
- `config/v2_start_config.json`
- PathB live gate
- 주문 금액/수량
- hard stop, stop loss, trailing stop
- affordability/risk 정책

왜 수정하지 않았는가:

- 리뷰 이슈는 debate metadata와 watchdog process detection 문제다.
- 운영 파라미터 변경은 이 이슈의 해결 범위가 아니며, AGENTS.md에서 운영자 확인 없이 변경 금지로 분류된 값이 포함된다.

### 2.3 주문/브로커 truth/전략 로직

수정하지 않은 내용:

- broker truth reconciliation
- order execution
- Path A/Path B routing
- candidate selection scoring
- hold/sell 판단 로직

왜 수정하지 않았는가:

- 이번 이슈는 prompt 통계 오염과 watchdog 중복 시작 위험이다.
- 주문, 리스크, selection 품질 문제를 섞으면 원인과 수정 범위가 흐려진다.

### 2.4 현재 워크트리에 이미 있던 별도 변경

현재 `git status`에는 이번 debate/watchdog 범위 외 변경이 남아 있다.

| 파일 | 확인된 변경 성격 | 이번 문서의 처리 |
|---|---|---|
| `minority_report/hold_advisor.py` | hold advisor Claude 호출/전체 판단에 `duration_ms` 측정 및 JSONL/return payload 기록 추가 | debate/watchdog 범위 밖 별도 변경 |
| `trading_bot.py` | candidate audit `config_hash`/`feature_flags_json` 기록, `execution_decision_id` 연결, entry timing fallback snapshot 보강 | debate/watchdog 범위 밖 별도 변경 |
| `tests/test_candidate_action_live_mapping.py` | candidate audit/config hash 및 linkage 관련 테스트 보강 | debate/watchdog 범위 밖 별도 변경 |
| `tests/test_claude_quality_contracts.py` | Claude quality/cost contract 관련 테스트 보강 | debate/watchdog 범위 밖 별도 변경 |
| `docs/reports/*` | screener/candidate/prompt overlay/cost 품질 리포트 산출물 | debate/watchdog 범위 밖 별도 산출물 |
| `docs/reports/screener_live_shadow_execution_plan_20260525.md` | screener live shadow execution plan 문서 | debate/watchdog 범위 밖 별도 문서 |

왜 수정하지 않았는가:

- 이 파일들은 이번 리뷰 이슈와 직접 관련이 없다.
- 이미 워크트리에 있던 변경을 되돌리거나 섞어 고치면 사용자 작업을 침범할 수 있다.
- 이번 작업에서는 관련 파일만 수정하고, 별도 변경은 그대로 보존했다.

## 3. 검증한 내용

실행한 검증:

```bash
python -m pytest tests/test_analyst_unavailable_quorum.py -q
python -m pytest tests/test_watchdog_launcher.py tests/test_live_guardian.py tests/test_live_process_inventory.py -q
python -m py_compile claude_memory/brain.py tools/live_guardian.py tools/live_preflight.py
```

결과:

- `tests/test_analyst_unavailable_quorum.py`: 15 passed
- `tests/test_watchdog_launcher.py tests/test_live_guardian.py tests/test_live_process_inventory.py`: 28 passed
- `py_compile`: passed

## 4. 남은 선택지

선택지 1: 코드 방어만 유지

- 현재 상태.
- prompt 오염과 watchdog 중복 시작 위험은 코드 레벨에서 차단된다.
- `state/brain.json`의 과거 stale metadata는 남아 있지만 summary에서 신뢰하지 않는다.

선택지 2: `state/brain.json` 단일 entry 수동 보정

- US `2026-05-22` debate entry의 stale `changes`만 삭제한다.
- 실제 stance와 metadata가 맞아져 데이터 자체도 깨끗해진다.
- 단, 정책 메모리 직접 수정이므로 운영 승인 또는 명시 지시 후 수행하는 것이 맞다.
