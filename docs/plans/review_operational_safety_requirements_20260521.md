# 리뷰 지적사항 코드 레벨 개선 요구서 - 2026-05-21

## 목적

이번 수정의 목적은 PathB live gate, live preflight, Telegram 주문한도 변경, brain 정책 메모리 변경이 운영 상태를 오염시키지 않도록 막는 것이다. 핵심 원칙은 다음 네 가지다.

- live gate는 신규 진입 허용 여부만 제어한다. 이미 브로커에 접수된 주문의 lifecycle reconciliation을 끊으면 안 된다.
- preflight는 문서화된 운영 정책을 명시적으로 검증해야 한다. 2026-05-21 사용자 운영 의도는 KR/US PathB live on, KR Claude Price new-entry block off이다.
- Telegram에서 바꾸는 주문한도는 dashboard와 같은 live-safe 범위만 start config에 저장해야 한다.
- `state/brain.json`은 승인 없는 런타임 정책 메모리 변경으로 커밋하지 않는다.

## 범위

수정 대상:

- `runtime/pathb_runtime.py`
- `tools/live_preflight.py`
- `telegram_commander.py`
- `tests/test_pathb_runtime.py`
- `tests/test_live_config_sources.py`
- `tests/test_telegram_order_size.py`
- `state/brain.json`

## 코드 재검토 결과

2026-05-21 현재 작업트리 기준으로 리뷰 지적은 코드에서 재현된다.

- `runtime/pathb_runtime.py:1156-1161`: `scan_waiting_entries()`는 PathB 시장 live가 꺼졌고 shadow plan이 켜진 경우 `cancel_waiting(... include_shadow=False)`를 호출한 뒤 shadow scan으로 넘어간다.
- `runtime/pathb_runtime.py:1630-1655`: `cancel_waiting()`은 shadow 제외 여부만 구분하고, non-shadow 상태 중 `WAITING`, `HIT`, `ORDER_SENT`, `ORDER_ACKED`를 모두 `CANCELLED`로 바꾼다. 따라서 `ORDER_SENT`/`ORDER_ACKED` 브로커 접수 가능 주문이 local-only cancel될 수 있다.
- `tests/test_pathb_runtime.py:867`: `test_shadow_only_scan_cancels_live_waiting_runs_before_shadow_scan`은 `ORDER_SENT`, `ORDER_ACKED`까지 취소되는 기존 위험 동작을 기대값으로 고정하고 있다. 구현 수정과 함께 이 테스트 기대값을 반드시 바꿔야 한다.
- `tools/live_preflight.py:943-951`: `KR=false, US=true`만 정책 일치인데, 현재 `KR=true, US=true`는 `else` 경로에서 `PASS`가 된다.
- `telegram_commander.py:82-101`: start config read/parse 실패를 `{}`로 대체한 뒤 write하므로 기존 `env_overrides`를 잃을 수 있다. dashboard의 `_load_start_config_for_write()`/`_atomic_write_text()` 패턴과 맞지 않는다.
- `telegram_commander.py:1793-1805`: `/setorder`는 10,000-10,000,000원을 허용하고, start config 저장 전에 `bot.risk.max_order_krw`를 먼저 변경한다. 저장 실패도 command 실패가 아니라 경고 메시지로만 처리된다.
- `dashboard/dashboard_server.py:417`: dashboard order-size update는 이미 50,000-5,000,000원을 강제한다. Telegram도 이 범위와 동일해야 한다.
- `state/brain.json`: 현재 diff가 남아 있으며 `version`, `last_updated`, `trained_days_us` 같은 정책 메모리 필드가 변경되어 있다. 이번 안전 패치에서는 이 diff가 없어야 한다.

비범위:

- PathB KR live 정책 자체를 변경하지 않는다.
- 주문 수량 산식, 손절/익절 철학, Claude selection 전략을 바꾸지 않는다.
- `state/brain.json`에 새 lesson이나 intraday tuning 결과를 반영하지 않는다.

## 1. PathB live off 시 브로커 접수 주문을 로컬 취소하지 않기

### 왜 수정하는가

현재 `PathBRuntime.scan_waiting_entries()`는 `PATHB_*_LIVE_ENABLED=false`이고 shadow plan이 켜져 있을 때 다음 흐름을 탄다.

```python
if not self._market_live_enabled(market):
    if self._market_shadow_plan_enabled(market):
        self.cancel_waiting(market, reason="PATHB_MANUALLY_DISABLED", include_shadow=False)
        self._scan_shadow_waiting_entries(market)
        return
```

문제는 `cancel_waiting()`이 `WAITING`, `HIT`뿐 아니라 `ORDER_SENT`, `ORDER_ACKED`도 `CANCELLED`로 바꾼다는 점이다. `ORDER_SENT`와 `ORDER_ACKED`는 이미 브로커에 주문이 접수됐을 수 있는 상태다. 이 상태를 로컬에서만 취소하면 실제 주문은 나중에 체결될 수 있는데 PathB는 더 이상 그 주문을 reconciliation하지 않는다. 결과적으로 orphan order 또는 orphan position이 생긴다.

### 무엇을 수정하는가

live-disabled branch에서 취소 가능한 상태를 두 그룹으로 분리한다.

- 로컬 취소 가능: `WAITING`, `HIT`
- 로컬 취소 금지: `ORDER_SENT`, `ORDER_ACKED`, `ORDER_UNKNOWN`, `PARTIAL_FILLED`, `FILLED`, `SELL_SENT`, `CLOSED`

`PATHB_*_LIVE_ENABLED=false`는 신규 주문 제출을 막는 설정이지, 이미 접수된 주문의 추적을 중단하는 설정이 아니다.

### 개선 방향

`cancel_waiting()`을 그대로 재사용하지 말고, 브로커 미접수 상태만 취소하는 helper를 추가한다.

```python
UNSENT_ENTRY_STATUSES = {"WAITING", "HIT"}

def cancel_unsent_waiting(self, market: str, *, reason: str, include_shadow: bool = False) -> int:
    ...
    if status not in UNSENT_ENTRY_STATUSES:
        continue
    ...
```

그 다음 `scan_waiting_entries()`의 live-disabled branch는 다음 의미가 되도록 바꾼다.

```python
if not self._market_live_enabled(market):
    self.cancel_unsent_waiting(
        market,
        reason="PATHB_MANUALLY_DISABLED",
        include_shadow=False,
    )
    if self._market_shadow_plan_enabled(market):
        self._scan_shadow_waiting_entries(market)
        return
    self.cancel_unsent_waiting(
        market,
        reason="PATHB_MANUALLY_DISABLED",
        include_shadow=True,
    )
    return
```

기존 `cancel_waiting()`은 `pathb_kill` 같은 명시적 kill-switch 경로가 사용 중일 수 있으므로 의미를 무리하게 바꾸지 않는다. manual live gate off 경로만 더 좁은 helper를 쓰게 한다.

### 수용 기준

- live off + shadow on 상태에서 `WAITING`, `HIT` non-shadow run은 `CANCELLED`가 된다.
- live off + shadow on 상태에서 `ORDER_SENT`, `ORDER_ACKED`는 `CANCELLED`가 되지 않는다.
- live off + shadow on 상태에서 `SHADOW_WAITING`, `SHADOW_HIT`는 유지되고 shadow scan은 계속 실행된다.
- live off + shadow off 상태에서도 `ORDER_SENT`, `ORDER_ACKED`는 로컬 취소하지 않는다.
- pending/unknown 주문은 기존 `reconcile_order_unknowns()`와 broker truth reconciliation 경로에서 계속 처리된다.

### 테스트 요구

`tests/test_pathb_runtime.py`에 다음 케이스를 추가한다.

- 기존 `test_shadow_only_scan_cancels_live_waiting_runs_before_shadow_scan`은 이름과 기대값을 보정한다. `live_ids`에 `ORDER_SENT`, `ORDER_ACKED`를 함께 넣어 모두 `CANCELLED`를 기대하면 안 된다.
- `PATHB_KR_LIVE_ENABLED=false`, `PATHB_KR_SHADOW_PLAN_ENABLED=true` 같은 live-disabled 회귀 케이스에서 `WAITING`과 `HIT`만 취소되는지 검증한다.
- 같은 조건에서 `ORDER_SENT`, `ORDER_ACKED`가 active 상태로 남는지 검증한다.
- shadow run은 보존되고 `_scan_shadow_waiting_entries()`가 호출되는지 검증한다.
- `PATHB_KR_SHADOW_PLAN_ENABLED=false`에서도 브로커 접수 가능 상태는 취소하지 않는지 검증한다.

## 2. PathB live 정책 불일치 preflight 처리

### 왜 수정하는가

현재 `tools/live_preflight.py::_pathb_market_live_gate_check()`는 운영 정책을 코드로 명시해야 한다. 최신 운영 의도는 KR/US PathB live on이므로 `PATHB_KR_LIVE_ENABLED=true`, `PATHB_US_LIVE_ENABLED=true`만 정책 일치로 본다.

### 무엇을 수정하는가

정책 판정을 명시적으로 만든다.

- `KR=true`, `US=true`: `PASS`
- `KR=false`, `US=true`: `WARN`
- `KR=true`, `US=false`: `WARN`
- `KR=false`, `US=false`: `WARN`

최소 요구는 현재 운영 의도와 다른 조합을 명확히 표시해 operator가 잘못된 live gate를 놓치지 않게 하는 것이다.

### 개선 방향

`_pathb_market_live_gate_check()`에서 KR/US live gate를 현재 운영 정책 기준으로 검사한다.

```python
kr_live = _truthy(pathb_market_gates["KR"])
us_live = _truthy(pathb_market_gates["US"])

if not kr_live or not us_live:
    status = "WARN"
    detail = "Path B market live gates violate KR-on/US-on policy"
else:
    status = "PASS"
    detail = "Path B market live gates match KR-on/US-on policy"
```

`data`에는 `policy`, `policy_match`, `violations`를 넣어 dashboard/guardian이 원인을 그대로 표시할 수 있게 한다.

### 테스트 요구

`tests/test_live_config_sources.py`에 다음 케이스를 추가한다.

- KR true / US true -> `PASS`
- KR false / US true -> `WARN`
- KR true / US false -> `WARN`
- KR false / US false -> `WARN`

## 3. Telegram `/setorder` live-safe 범위 강제

### 왜 수정하는가

현재 `telegram_commander.py::_cmd_setorder()`는 10,000원부터 10,000,000원까지 허용한다. 그런데 이 값은 `config/v2_start_config.json`에 저장되고 다음 live 실행 때 적용될 수 있다. dashboard는 50,000원부터 5,000,000원까지 강제하므로, Telegram만 넓은 범위를 허용하면 paper/test에서 실행한 `/setorder 10000` 또는 `/setorder 6000000`이 다음 live 설정을 오염시킬 수 있다.

### 무엇을 수정하는가

Telegram `/setorder`의 허용 범위를 dashboard와 동일하게 맞춘다.

```python
COMMON_ORDER_MIN_KRW = 50_000
COMMON_ORDER_MAX_KRW = 5_000_000
```

`_cmd_setorder()`는 이 범위를 벗어나면 `bot.risk.max_order_krw`도 바꾸지 않고 start config도 쓰지 않는다.

### 개선 방향

검증 순서는 다음처럼 transaction에 가깝게 만든다.

1. 숫자 파싱
2. live-safe 범위 검증
3. start config 읽기/검증
4. start config atomic write 성공
5. `bot.risk.max_order_krw` 변경
6. 성공 메시지 반환

start config 저장 실패 후 in-memory만 바뀌는 부분 변경도 피해야 한다. 즉, persistence 실패는 command 실패로 취급한다.

### 테스트 요구

`tests/test_telegram_order_size.py`에 다음 케이스를 추가한다.

- `/setorder 10000`은 거부되고 `bot.risk.max_order_krw`와 config 파일이 변하지 않는다.
- `/setorder 5000001` 또는 더 큰 값은 거부되고 config 파일이 변하지 않는다.
- `/setorder 50000`, `/setorder 5000000`은 허용된다.
- 정상 저장 시 `MAX_ORDER_KRW`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`가 같은 값으로 저장된다.

## 4. Telegram start config read 실패 시 파일 보존

### 왜 수정하는가

현재 `_write_start_config_common_order_krw()`는 `v2_start_config.json` 읽기 또는 JSON parse에 실패하면 `data = {}`로 대체한 뒤 새 파일을 쓴다. 이 경우 기존 `env_overrides`에 있던 PathB gate, live cap, 정책 플래그가 모두 사라질 수 있다.

### 무엇을 수정하는가

write 전용 loader는 실패를 삼키지 말고 예외로 올려야 한다.

```python
def _load_start_config_for_update(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("start config is invalid JSON") from exc
    except OSError as exc:
        raise RuntimeError("start config is unreadable") from exc

    if not isinstance(data, dict):
        raise ValueError("start config must be a JSON object")

    overrides = data.get("env_overrides")
    if overrides is None:
        data["env_overrides"] = {}
    elif not isinstance(overrides, dict):
        raise ValueError("start config env_overrides must be a JSON object")

    return data
```

읽기 실패, invalid JSON, top-level non-object, `env_overrides` non-object는 모두 command 실패로 처리한다. 이때 파일을 절대 다시 쓰지 않는다.

### 개선 방향

`_write_start_config_common_order_krw()`는 정상 JSON object에 대해서만 변경된 payload를 만들고 atomic write를 수행한다. atomic write helper가 없다면 같은 모듈에 임시 파일 후 `replace()` 방식으로 추가한다.

`_cmd_setorder()`는 이 예외를 잡아 사용자에게 실패 메시지를 반환하고, in-memory risk 값도 변경하지 않는다.

### 테스트 요구

`tests/test_telegram_order_size.py`에 다음 케이스를 추가한다.

- invalid JSON config에서 `/setorder 500000` 실행 시 파일 내용이 byte-for-byte 유지된다.
- top-level list config에서 command가 실패하고 파일이 유지된다.
- `env_overrides`가 list/string이면 command가 실패하고 파일이 유지된다.
- unreadable path 또는 write 실패 시 in-memory 값이 변경되지 않는다.

## 5. `state/brain.json` 커밋 변경 제거

### 왜 수정하는가

`state/brain.json`은 정책 메모리이며 런타임 truth나 원장이 아니다. 현재 저장소 계약은 자동 brain 변경을 보류하고, 교훈 후보는 `state/lesson_candidates.json`에 append/score하는 흐름을 우선한다. 따라서 2026-05-21 intraday tuning insight, version/date 증가, analyst performance 증가 같은 로컬 실행 결과를 PR에 포함하면 승인되지 않은 전략 메모리가 live prompt에 섞인다.

### 무엇을 수정하는가

이번 패치에서 `state/brain.json` diff를 제거한다.

요구 상태:

```powershell
git diff --exit-code -- state/brain.json
```

위 명령이 통과해야 한다. 유효한 교훈 후보가 있다면 별도 승인 workflow로 `state/lesson_candidates.json`에 후보 형태로 기록하고, 이번 안전 패치에는 포함하지 않는다.

### 개선 방향

- `state/brain.json`을 직접 수정하는 runtime 산출물은 커밋 대상에서 제외한다.
- 자동 학습/반영이 필요하면 `lesson_candidates.json`에 candidate로 남기고 수동 승인 후 brain 반영한다.
- PR 설명에 "brain 정책 메모리 변경 없음"을 명시한다.

### 테스트/검증 요구

- `git diff --exit-code -- state/brain.json`
- `python tools/check_brain_quality.py`는 필요 시 parse 검증용으로만 실행한다. 실행 결과가 brain 수정을 요구하더라도 이번 패치에서 자동 반영하지 않는다.

## 구현 순서

1. `state/brain.json` diff를 먼저 제거해 정책 메모리 오염을 patch 범위에서 분리한다.
2. `PathBRuntime` live-disabled branch가 브로커 접수 상태를 취소하지 않도록 helper를 추가한다.
3. preflight의 PathB live policy mismatch를 현재 운영 정책 기준으로 명시한다.
4. Telegram `/setorder` 범위와 start config write transaction을 dashboard 기준으로 맞춘다.
5. 관련 테스트를 추가한다.
6. targeted test와 live preflight를 실행한다.

## 최종 검증 명령

```powershell
python -m pytest tests/test_pathb_runtime.py tests/test_live_config_sources.py tests/test_telegram_order_size.py -q
python tools/live_preflight.py --mode live --skip-dashboard --json
git diff --check
git diff --exit-code -- state/brain.json
```

## 완료 기준

- accepted broker order 상태(`ORDER_SENT`, `ORDER_ACKED`)가 live gate off 때문에 로컬 `CANCELLED`로 바뀌지 않는다.
- KR/US PathB live가 모두 켜진 설정은 preflight에서 통과한다.
- Telegram `/setorder`는 50,000원 이상 5,000,000원 이하만 start config에 저장한다.
- start config를 읽을 수 없거나 JSON이 깨진 경우 Telegram command는 실패하고 기존 파일을 보존한다.
- `state/brain.json` 변경이 PR diff에 남지 않는다.

## 구현 후 QA 기록

2026-05-21 코드 반영 후 요구서 대비 확인:

- PathB: `scan_waiting_entries()`의 live-disabled branch가 `cancel_unsent_waiting()`을 사용한다. `WAITING`/`HIT`만 취소하고 `ORDER_SENT`/`ORDER_ACKED`는 reconciliation 대상으로 유지한다.
- PathB tests: 기존 shadow-only scan 테스트 기대값을 보정했고, shadow off 상태에서도 브로커 접수 가능 상태가 유지되는 테스트를 추가했다.
- Preflight: `PATHB_KR_LIVE_ENABLED=true`, `PATHB_US_LIVE_ENABLED=true` 조합은 `PASS`이다. 비일치 조합은 `WARN`이다. `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK=false`가 현재 운영 기준이다.
- Telegram: `/setorder` 허용 범위는 50,000-5,000,000원이다. start config read/JSON/root/env_overrides 오류와 atomic write 실패 시 runtime 값과 파일을 변경하지 않는다.
- Brain: `git diff --exit-code -- state/brain.json` 통과. 정책 메모리 diff 없음.

실행한 검증:

```powershell
python -m pytest tests/test_pathb_runtime.py tests/test_live_config_sources.py tests/test_telegram_order_size.py -q
# 85 passed, 2 warnings

python -m py_compile runtime/pathb_runtime.py tools/live_preflight.py telegram_commander.py
git diff --check -- runtime/pathb_runtime.py tools/live_preflight.py telegram_commander.py tests/test_pathb_runtime.py tests/test_live_config_sources.py tests/test_telegram_order_size.py docs/plans/review_operational_safety_requirements_20260521.md
git diff --exit-code -- state/brain.json
```

비주문 live 운영 점검:

```powershell
python tools/live_preflight.py --mode live --skip-dashboard --json
# ok=True fail_count=0 warn_count=8
```

남은 경고는 이번 요구서 범위 밖이다: runtime snapshot drift, direct balance API probe skip, bot/dashboard PID lock active, KR/US price CSV integrity warning, ML decisions DB known gap, external data readiness.
