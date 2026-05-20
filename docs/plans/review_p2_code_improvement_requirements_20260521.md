# Review P2 코드레벨 개선 요구서

작성일: 2026-05-21

대상 리뷰 항목:

- PathB shadow-only scan 전 live plan 정리 누락
- 대시보드 live 주문금액 저장 시 start config 읽기 실패 처리
- 대시보드 주문금액 저장의 시장별 scope 오염
- counterfactual 분봉 entry/outcome의 세션 경계 오염

대상 파일:

- `runtime/pathb_runtime.py`
- `dashboard/dashboard_server.py`
- `tools/update_counterfactual_outcomes.py`
- `tests/test_pathb_runtime.py`
- `tests/test_dashboard_buy_range.py`
- `tests/test_update_counterfactual_outcomes.py`

## 0. 2026-05-21 주문금액 운영 결정 반영

주문금액 write scope 항목은 최초에는 시장별 격리 방향으로 정리했지만, 후속 운영 결정으로 `MAX_ORDER_KRW` 단일 공통 주문한도를 기준으로 삼는다. 대시보드는 "다음공통"으로 표시하고, 저장 시 `MAX_ORDER_KRW`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`를 같은 값으로 맞춘다. 텔레그램 `/setorder`도 같은 공통 기준을 표시하고 start config에 저장한다.

따라서 `trading_bot.py`와 `risk_manager.py`가 `MAX_ORDER_KRW`를 공통 주문 cap으로 읽는 경로는 현재 운영 의도와 일치한다. 아래 시장별 격리 문단은 리뷰 대응 과정의 대안 설계 기록으로 남기며, 현재 구현 기준은 공통 주문한도 단일화다.

## 1. 목적

이번 개선의 목적은 운영 중 토글, live 설정 저장, 주문금액 변경, 사후 outcome labeling에서 "부분적으로는 성공한 것처럼 보이지만 실제 운영 state를 오염시키는" P2급 엣지 케이스를 제거하는 것이다.

핵심 원칙은 네 가지다.

- live 비활성화는 non-shadow 활성 run을 반드시 정리해야 한다.
- 읽을 수 없는 live config는 절대 빈 dict로 재생성하지 않는다.
- 주문금액 UI/API는 `MAX_ORDER_KRW` 공통 cap과 보조 key들을 같은 값으로 맞춘다.
- counterfactual label은 같은 거래 세션의 가격 샘플로만 채운다.

비목표:

- 주문 수량/금액 산식을 새로 설계하지 않는다.
- `state/brain.json` 또는 운영 state 파일을 자동 수정하지 않는다.
- PathB KR live 정책을 켜거나 전략 철학을 바꾸지 않는다.

## 2. PathB shadow-only scan 전 live plan 정리

### 왜 수정하는가

현재 `runtime/pathb_runtime.py`의 `PathBRuntime.scan_waiting_entries()`는 시장 live가 꺼져 있고 shadow plan이 켜져 있으면 아래 흐름으로 바로 반환한다.

```python
if not self._market_live_enabled(market):
    if self._market_shadow_plan_enabled(market):
        self._scan_shadow_waiting_entries(market)
        return
    self.cancel_waiting(market, reason="PATHB_MANUALLY_DISABLED")
    return
```

이 상태에서 `PATHB_KR_LIVE_ENABLED=false`, shadow mode enabled 조합으로 전환되면 기존 non-shadow `WAITING`, `HIT`, `ORDER_SENT`, `ORDER_ACKED` run이 `v2_path_runs`에 남을 수 있다. 이 stale live run은 이후 동일 ticker/plan 등록을 막거나, 대시보드와 audit에서 실제 운영 상태를 잘못 보이게 만든다.

### 무엇을 수정하는가

`scan_waiting_entries()`의 live-disabled branch에서 shadow scan 여부와 무관하게 non-shadow 활성 run을 먼저 취소한다. 단, shadow run은 shadow scan을 계속해야 하므로 함께 취소하면 안 된다.

### 어떻게 수정하는가

권장 구현은 `cancel_waiting()`에 shadow 포함 여부를 명시하는 인자를 추가하는 방식이다.

```python
def cancel_waiting(self, market: str, *, reason: str, include_shadow: bool = True) -> int:
    ...
    if status in {"SHADOW_WAITING", "SHADOW_HIT"}:
        if not include_shadow:
            continue
        ...
```

그 다음 `scan_waiting_entries()`를 아래 흐름으로 바꾼다.

```python
if not self._market_live_enabled(market):
    self.cancel_waiting(
        market,
        reason="PATHB_MANUALLY_DISABLED",
        include_shadow=False,
    )
    if self._market_shadow_plan_enabled(market):
        self._scan_shadow_waiting_entries(market)
        return
    self.cancel_waiting(
        market,
        reason="PATHB_MANUALLY_DISABLED",
        include_shadow=True,
    )
    return
```

중복 loop를 피하려면 별도 helper를 두는 방식도 가능하다.

```python
def cancel_live_waiting(self, market: str, *, reason: str) -> int:
    return self.cancel_waiting(market, reason=reason, include_shadow=False)
```

구현 시 지켜야 할 점:

- non-shadow 취소 대상 status는 기존 `cancel_waiting()`과 동일하게 `WAITING`, `HIT`, `ORDER_SENT`, `ORDER_ACKED`를 유지한다.
- `SHADOW_WAITING`, `SHADOW_HIT`는 shadow mode가 켜져 있으면 취소하지 않는다.
- shadow mode가 꺼져 있으면 기존처럼 shadow run도 취소 가능해야 한다.
- `FILLED`, `CLOSED`, `SELL_SENT`, `ORDER_UNKNOWN`, `PARTIAL_FILLED` 등 기존 취소 제외 status의 의미를 바꾸지 않는다.

범위 한계:

- 이 경로는 현재 `cancel_waiting()` 구현처럼 `self._session_date(market)`의 현재 세션 run만 조회한다.
- 이전 세션에서 남은 stale live run은 이 branch의 책임으로 확장하지 않는다.
- 이전 세션 stale run 정리는 장 시작 reconciliation 경로인 `reconcile_order_unknowns_at_open` 또는 별도 stale-session cleanup의 책임으로 문서화한다.
- 테스트에는 이 한계를 주석으로 남겨, shadow-only scan fix가 cross-session cleanup까지 보장한다고 오해하지 않게 한다.

### 전후 개선

변경 전:

- KR PathB live를 끄고 shadow만 유지하면 기존 live run이 active 상태로 남을 수 있다.
- 운영자는 live가 꺼진 것으로 보지만 DB에는 live 진입 대기 상태가 남아 다음 등록이나 reconciliation에 영향을 준다.

변경 후:

- live-disabled 전환 시 non-shadow active run이 먼저 취소된다.
- shadow scan은 계속 수행되므로 관찰 목적의 shadow plan은 유지된다.
- live off, shadow on 상태의 의미가 "실주문 없음, shadow 관찰만 유지"로 명확해진다.

### 테스트 요구

`tests/test_pathb_runtime.py`에 다음 케이스를 추가하거나 기존 PathB control 테스트를 확장한다.

- `market_live_enabled=False`, `shadow_plan_enabled=True`에서 `WAITING`, `HIT`, `ORDER_SENT`, `ORDER_ACKED`가 cancel 처리된다.
- 같은 조건에서 `SHADOW_WAITING`, `SHADOW_HIT`는 cancel 처리되지 않고 `_scan_shadow_waiting_entries()`가 호출된다.
- `market_live_enabled=False`, `shadow_plan_enabled=False`에서는 기존처럼 shadow run까지 취소된다.
- 취소 제외 status는 변경되지 않는다.
- 특히 `ORDER_UNKNOWN`은 shadow 여부와 무관하게 이 경로에서 취소되지 않음을 검증한다. 이 상태는 별도 order-unknown reconciliation 경로가 담당한다.

## 3. start config 읽기 실패 시 저장 거부

### 왜 수정하는가

현재 `dashboard/dashboard_server.py`의 `_update_start_config_order_size()`는 `config/v2_start_config.json` 읽기 또는 JSON parse 실패를 `{}`로 대체한 뒤 다시 저장한다.

```python
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}
```

파일이 부분 write 중이거나 일시적으로 malformed 상태일 때 대시보드 저장이 들어오면 전체 live start config가 주문금액 override만 담은 새 파일로 덮인다. 이 경우 safety flag, env override, live 운영 설정이 유실될 수 있다.

### 무엇을 수정하는가

`v2_start_config.json`을 읽거나 검증할 수 없으면 `_atomic_write_text()`를 호출하지 않고 API error를 반환한다. 기존 파일은 byte 단위로 그대로 보존해야 한다.

### 어떻게 수정하는가

start config update 전용 loader를 추가한다.

```python
def _load_start_config_for_update(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("start config file is invalid JSON") from exc
    except OSError as exc:
        raise RuntimeError("start config file is not readable") from exc

    if not isinstance(data, dict):
        raise ValueError("start config file must be a JSON object")

    if "env_overrides" not in data:
        data["env_overrides"] = {}
    elif not isinstance(data.get("env_overrides"), dict):
        raise ValueError("start config env_overrides must be a JSON object")

    return data
```

`_update_start_config_order_size()`는 이 loader를 통해서만 config를 가져온다.

```python
path = _start_config_path()
data = _load_start_config_for_update(path)
overrides = data["env_overrides"]
```

API error mapping:

- JSON parse 실패, top-level object 아님, `env_overrides` 타입 오류: 400
- 파일 없음, 권한 오류, 읽기 실패: 500
- 두 경우 모두 파일 write 금지

### 전후 개선

변경 전:

- 손상된 config를 `{}`로 간주하고 정상 저장처럼 처리한다.
- 주문금액 변경 요청 하나가 live 시작 설정 전체를 삭제할 수 있다.

변경 후:

- 읽기 실패나 JSON 오류는 저장 실패로 노출된다.
- 기존 config 파일은 보존된다.
- 운영자는 대시보드 에러를 보고 config 복구 후 다시 저장할 수 있다.

### 테스트 요구

`tests/test_dashboard_buy_range.py`에 다음 케이스를 추가한다.

- invalid JSON 파일에서 `_update_start_config_order_size()` 호출 시 예외가 발생하고 파일 내용이 변경되지 않는다.
- top-level list 또는 string config는 저장되지 않는다.
- `env_overrides`가 dict가 아니면 저장되지 않는다.
- 정상 config에서는 기존 unrelated key와 env override가 보존된다.
- Flask endpoint `/api/control/order-size`가 invalid JSON에 대해 `ok=false`와 400을 반환한다.

## 4. 주문금액 write scope를 공통 cap으로 단일화

### 왜 수정하는가

현재 운영 결정은 `MAX_ORDER_KRW`를 KR/US 공통 주문 cap으로 쓰는 것이다. 따라서 대시보드 주문금액 컨트롤도 시장별 금액처럼 보이지 않고 "다음공통" 주문한도를 저장하는 기능으로 정리해야 한다.

```python
def _order_size_config_keys(market: str) -> list[str]:
    market_key = str(market or "").upper()
    if market_key in {"KR", "US"}:
        return [
            "MAX_ORDER_KRW",
            "KR_FIXED_ORDER_KRW",
            "US_FIXED_ORDER_KRW",
            "PATHB_FIXED_ORDER_KRW",
        ]
    raise ValueError("market must be KR or US")
```

`MAX_ORDER_KRW`는 `trading_bot.py`와 `risk_manager.py`에서 시장 구분 없는 공통 cap으로 읽힌다. 보조 key가 다른 값으로 남으면 대시보드/텔레그램/재시작 후 실제 적용값이 서로 다르게 보일 수 있으므로 저장 경로에서 같은 값으로 맞춘다.

### 무엇을 수정하는가

주문금액 endpoint는 선택 시장과 무관하게 공통 주문 cap과 보조 key를 같은 값으로 수정한다.

필수 요구:

- 대시보드 표시 문구는 "다음공통"으로 둔다.
- KR/US 어느 쪽에서 저장해도 `MAX_ORDER_KRW`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`를 같은 값으로 수정한다.
- `_summary_order_size_setting_krw()`는 `MAX_ORDER_KRW`를 우선 읽는다.
- `trading_bot.py`와 `risk_manager.py`의 `MAX_ORDER_KRW` 소비 경로는 유지한다.

PathB budget 요구:

- 현재 운영에서는 `PATHB_FIXED_ORDER_KRW`도 공통 주문한도 보조 key로 함께 동기화한다.
- PathB budget을 별도 금액으로 분리할 필요가 생기면 별도 control 또는 후속 요구사항으로 다룬다.

### 어떻게 수정하는가

수정안:

```python
def _order_size_config_keys(market: str) -> list[str]:
    market_key = str(market or "").upper()
    if market_key in {"KR", "US"}:
        return [
            "MAX_ORDER_KRW",
            "KR_FIXED_ORDER_KRW",
            "US_FIXED_ORDER_KRW",
            "PATHB_FIXED_ORDER_KRW",
        ]
    raise ValueError("market must be KR or US")
```

`_update_start_config_order_size()`는 반환된 공통 key를 `env_overrides`와 top-level mirror에 반영한다.

```python
for key in keys:
    overrides[key] = str(amount)
    if key in data:
        data[key] = amount
```

### 전후 개선

변경 전:

- 주문금액 control이 시장별 값처럼 보일 수 있다.
- `MAX_ORDER_KRW`와 보조 key들이 서로 다른 값으로 남아 재시작 후 적용 기준을 오해할 수 있다.

변경 후:

- 대시보드는 "다음공통" 주문한도를 저장한다.
- 공통 cap과 보조 key가 같은 값으로 맞춰진다.
- 재시작 후 KR/US 주문 cap은 `MAX_ORDER_KRW` 기준으로 일관되게 적용된다.

### 테스트 요구

`tests/test_dashboard_buy_range.py`의 기존 기대값을 갱신하고 아래를 검증한다.

- US 저장 후 `env_overrides["MAX_ORDER_KRW"]`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`가 모두 새 값으로 변경된다.
- KR 저장도 동일하게 네 개 key를 모두 새 값으로 변경한다.
- API 응답의 `updated_keys`는 네 개 공통 key를 반환한다.

## 5. counterfactual 분봉 entry/outcome을 같은 세션으로 제한

### 왜 수정하는가

현재 `tools/update_counterfactual_outcomes.py`의 `_infer_entry_price_from_minute()`는 trigger 시각 이후 첫 번째 분봉 샘플을 entry price로 사용한다.

```python
normalized = [{**sample, "dt": _compare_dt(sample["dt"], trigger_at)} for sample in samples]
observed = next((sample for sample in normalized if sample["dt"] >= trigger_at), None)
```

minute CSV에 여러 거래일이 들어 있고 target session의 trigger 이후 샘플이 없으면 다음 거래일 첫 bar가 선택될 수 있다. `_minute_outcomes()`의 30m/60m 관측값도 같은 방식으로 다음 세션 샘플을 사용할 수 있다. 이 경우 wait/reclaim counterfactual entry price와 outcome label이 다른 세션 가격으로 채워진다.

### 무엇을 수정하는가

분봉 샘플은 row의 `session_date`와 같은 trading session에 속할 때만 entry/outcome 계산에 사용한다. 같은 세션 샘플이 없거나 target 시각보다 너무 늦은 샘플만 있으면 값을 채우지 않고 missing reason을 남긴다.

### 어떻게 수정하는가

세션 판정 helper를 추가한다.

```python
MAX_ENTRY_SAMPLE_LATENCY = timedelta(minutes=5)
MAX_OUTCOME_SAMPLE_LATENCY = timedelta(minutes=5)

def _row_session_date(row: dict[str, Any], market: str, trigger_at: datetime) -> str:
    date_text = str(row.get("session_date") or "")[:10]
    if date_text:
        return date_text
    return resolve_session_date_str(market, trigger_at)

def _sample_session_date(market: str, sample_dt: datetime) -> str:
    if sample_dt.tzinfo is not None:
        sample_dt = sample_dt.astimezone(KST)
    return resolve_session_date_str(market, sample_dt)
```

샘플 normalize 단계에서 같은 세션만 남긴다.

```python
def _same_session_samples(
    samples: list[dict[str, Any]],
    *,
    market: str,
    trigger_at: datetime,
    target_session: str,
) -> list[dict[str, Any]]:
    normalized = []
    for sample in samples:
        dt = _compare_dt(sample["dt"], trigger_at)
        if _sample_session_date(market, dt) != target_session:
            continue
        normalized.append({**sample, "dt": dt})
    return normalized
```

entry inference는 같은 세션의 trigger 이후 샘플만 보되 latency를 제한한다.

```python
observed = next((sample for sample in normalized if sample["dt"] >= trigger_at), None)
if observed is None:
    return None, {**metadata, "entry_price_reason": "minute_entry_sample_missing_same_session"}, ["entry_price"]
if observed["dt"] - trigger_at > MAX_ENTRY_SAMPLE_LATENCY:
    return None, {**metadata, "entry_price_reason": "minute_entry_sample_too_late"}, ["entry_price"]
```

30m/60m outcome도 같은 세션 샘플만 사용하고 target 이후 latency를 제한한다.

```python
observed = next((sample for sample in normalized if sample["dt"] >= target), None)
if observed is None or observed["dt"] - target > MAX_OUTCOME_SAMPLE_LATENCY:
    missing.append(f"outcome_{horizon}m_pct")
    metadata[f"outcome_{horizon}m_reason"] = "minute_outcome_sample_missing_same_session"
    continue
```

MFE/MAE window도 같은 세션 샘플만 사용한다.

```python
window_60 = [sample for sample in normalized if trigger_at <= sample["dt"] <= target_60]
```

metadata 요구:

- entry sample을 거부한 경우 `entry_price_reason`에 세션 또는 latency 원인을 남긴다.
- outcome sample을 거부한 경우 `outcome_30m_reason`, `outcome_60m_reason` 중 해당 key를 남긴다.
- 실제 사용한 샘플은 기존처럼 `*_observed_at`에 기록한다.

### 전후 개선

변경 전:

- 2026-05-20 trigger의 entry가 없으면 2026-05-21 첫 분봉을 entry로 사용할 수 있다.
- outcome label이 실제 trigger 세션의 반응이 아니라 다음 세션 가격 변화로 기록될 수 있다.

변경 후:

- target session에 유효한 분봉 샘플이 없으면 outcome을 채우지 않는다.
- 잘못된 값보다 missing 상태를 택하므로 학습/리뷰 데이터 오염을 막는다.
- metadata reason으로 데이터 부족과 실제 손익 부진을 구분할 수 있다.

### 테스트 요구

`tests/test_update_counterfactual_outcomes.py`에 다음 케이스를 추가한다.

- 같은 ticker CSV에 2개 session이 있고 target session trigger 이후 샘플이 없을 때 다음 session bar를 entry로 쓰지 않는다.
- 같은 session에서 trigger 후 1분 샘플은 entry로 사용한다.
- 같은 session이지만 trigger 후 latency 한도를 넘은 샘플은 entry로 쓰지 않는다.
- 30m/60m target 이후 같은 session 샘플이 없으면 다음 session 샘플을 outcome으로 쓰지 않는다.
- MFE/MAE window는 같은 session 샘플만 계산한다.

## 6. 구현 순서

1. `dashboard/dashboard_server.py`의 start config loader를 먼저 분리하고 저장 실패 시 파일 보존 테스트를 추가한다.
2. `_order_size_config_keys()`에서 시장별 write set을 정리하고 기존 dashboard order-size 테스트 기대값을 갱신한다.
3. `PathBRuntime.cancel_waiting()` 또는 신규 helper로 non-shadow-only cancel 경로를 추가한 뒤 live-disabled shadow branch를 수정한다.
4. `tools/update_counterfactual_outcomes.py`에 세션 필터와 latency bound를 추가하고 counterfactual 테스트를 보강한다.

## 7. QA 명령

관련 테스트:

```powershell
python -m pytest tests/test_dashboard_buy_range.py tests/test_pathb_runtime.py tests/test_update_counterfactual_outcomes.py -q
```

문법 확인:

```powershell
python -m py_compile runtime/pathb_runtime.py dashboard/dashboard_server.py tools/update_counterfactual_outcomes.py
```

live 설정 경로를 건드렸으므로 가능하면 preflight도 확인한다.

```powershell
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## 8. 완료 기준

- KR PathB live off, shadow on 상태에서 non-shadow active run이 stale로 남지 않는다.
- invalid 또는 unreadable `config/v2_start_config.json`에 대해 order-size save가 파일을 덮어쓰지 않는다.
- US/KR order-size 변경이 `MAX_ORDER_KRW`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`를 같은 값으로 동기화한다.
- dashboard와 telegram의 order-size 표시/저장이 공통 주문한도 기준과 일치한다.
- counterfactual entry/outcome backfill이 다음 거래 세션 분봉을 사용하지 않는다.
- 모든 변경에 대해 관련 단위 테스트와 `py_compile`이 통과한다.
