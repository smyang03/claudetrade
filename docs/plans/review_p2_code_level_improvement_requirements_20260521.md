# P2 리뷰 지적 코드레벨 개선 요구서

작성일: 2026-05-21

대상 리뷰:

- `tools/update_counterfactual_outcomes.py`: wait-path `entry_price` 분봉 추론이 같은 세션으로 제한되지 않음
- `dashboard/dashboard_server.py`: 대시보드 주문금액 저장이 `MAX_ORDER_KRW`를 함께 변경해 KR/US 주문상한이 오염될 수 있음

대상 파일:

- `tools/update_counterfactual_outcomes.py`
- `dashboard/dashboard_server.py`
- `tests/test_update_counterfactual_outcomes.py`
- `tests/test_dashboard_buy_range.py`

## 0. 2026-05-21 운영 결정 반영

주문금액 항목 B는 최초 리뷰의 "KR/US 시장별 격리" 방향을 검토했으나, 후속 운영 결정으로 `MAX_ORDER_KRW`를 단일 공통 주문한도 기준으로 유지하는 방향을 채택했다. 따라서 대시보드와 텔레그램 `/setorder`는 "다음공통" 주문한도를 표시하고, 저장 시 `MAX_ORDER_KRW`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`를 같은 금액으로 동기화한다.

이 결정에 따라 `trading_bot.py`와 `risk_manager.py`가 `MAX_ORDER_KRW`를 공통 cap으로 읽는 현재 구조는 의도된 동작이다. 아래의 시장별 격리 설계 문단은 당시 리뷰 지적을 해소하기 위한 대안 설계 기록이며, 현재 구현 기준으로는 "공통 주문한도 단일화"가 우선한다.

## 1. 목적

이번 개선의 목적은 counterfactual 라벨의 세션 오염을 차단하고, 주문금액 설정은 운영 결정에 맞게 공통 주문한도 기준으로 일관되게 유지하는 것이다.

첫 번째 이슈는 실제 체결되지 않은 counterfactual wait 경로의 진입가를 분봉 CSV에서 추론할 때, 트리거 당일 분봉이 없으면 다음 거래일 분봉을 진입가로 잘못 채택할 수 있는 문제다. 이 상태에서 당일 종가 PnL을 계산하면 잘못된 진입가와 다른 날짜의 종가가 결합되어 라벨이 오염된다.

두 번째 이슈는 최초에는 US 주문금액 저장이 `MAX_ORDER_KRW`를 함께 변경해 KR cap에 영향을 줄 수 있다는 시장별 격리 관점으로 제기되었다. 후속 운영 결정에서는 `MAX_ORDER_KRW`를 공통 주문한도로 명시하고, 대시보드/텔레그램 저장 경로가 공통 cap과 보조 key들을 같은 값으로 동기화하도록 정리한다.

## 2. 개선 항목 A - Counterfactual 분봉 진입가를 같은 세션으로 제한

### 왜 수정하는가

`candidate_counterfactual_paths`는 후보 경로의 품질을 사후 평가하는 데이터다. 이 데이터의 `entry_price`, `outcome_close_pct`, `outcome_30m_pct`, `outcome_60m_pct`가 다른 세션 가격을 섞어 계산되면 wait 경로의 성과 라벨이 왜곡된다.

문제 예시는 다음과 같다.

- row의 `session_date`: `2026-05-19`
- row의 `trigger_time`: `2026-05-19T09:35:00+09:00`
- 티커 분봉 CSV: `2026-05-19` 트리거 이후 샘플 없음, `2026-05-20T09:00:00+09:00` 샘플 존재
- 현재 로직: `dt >= trigger_at`인 첫 샘플을 찾으므로 `2026-05-20` 분봉 가격을 `entry_price`로 채택 가능
- 이후 로직: `2026-05-19` 일봉 종가와 `2026-05-20` 진입가를 결합해 `outcome_close_pct`를 계산 가능

이는 단순 결측 처리 문제가 아니라 평가 라벨 오염이다. 잘못 채워진 라벨은 이후 후보 경로 분석, shadow 승격 판단, 전략 품질 진단에 영향을 준다.

### 현재 코드 흐름

`update_counterfactual_outcomes()`는 row에 `entry_price`가 없고 `trigger_time`이 있으면 `_infer_entry_price_from_minute()`를 호출한다.

현재 `_infer_entry_price_from_minute()`의 핵심 흐름:

```python
normalized = [{**sample, "dt": _compare_dt(sample["dt"], trigger_at)} for sample in samples]
observed = next((sample for sample in normalized if sample["dt"] >= trigger_at), None)
```

문제는 이 선택 조건이 `row["session_date"]`를 보지 않는다는 점이다. 티커별 분봉 CSV에 여러 세션이 누적되어 있으면, 트리거 세션의 샘플이 없을 때 다음 세션 샘플이 선택될 수 있다.

같은 계열의 방어 로직은 `_minute_outcomes()`에도 적용하는 것이 안전하다. 현재 30분/60분 outcome도 전체 분봉 샘플에서 `dt >= target`을 찾기 때문에, target 이후 같은 세션 샘플이 없으면 다음 세션 샘플을 outcome으로 사용할 여지가 있다.

### 무엇을 수정하는가

`tools/update_counterfactual_outcomes.py`에 세션 필터 헬퍼를 추가하고, 분봉 기반 선택은 모두 row의 `session_date`와 같은 시장 세션 안에서만 수행한다.

필수 수정 범위:

- `_infer_entry_price_from_minute()`에서 `observed` 후보를 같은 세션 샘플로 제한한다.
- 같은 세션 샘플이 없으면 `entry_price`를 채우지 않는다.
- row의 `session_date` 또는 `market`이 비어 있으면 분봉 샘플 전체를 차단한다. 이 경우 임의 fallback으로 KR 세션처럼 해석하지 않는다.
- trigger 직후 샘플만 진입가로 인정하기 위해 entry sample latency 상한을 둔다. 권장 기본값은 `MAX_ENTRY_SAMPLE_LATENCY = timedelta(minutes=5)`다.
- 결측 사유는 기존 retry 흐름과 호환되도록 transient reason으로 남긴다.
- metadata에 세션 필터 적용 사실과 대상 세션을 남긴다.

권장 수정 범위:

- `_minute_outcomes()`에서도 같은 세션 샘플만 사용한다.
- 30분/60분 target 이후 같은 세션 샘플이 없으면 해당 minute outcome은 결측으로 남긴다.
- MFE/MAE 60분 window도 같은 세션 안에서만 계산한다.

### 어떻게 수정하는가

예상 헬퍼 형태:

```python
MAX_ENTRY_SAMPLE_LATENCY = timedelta(minutes=5)


def _row_session_date(row: dict[str, Any]) -> str:
    return str(row.get("session_date") or "")[:10]


def _row_market(row: dict[str, Any]) -> str:
    market = str(row.get("market") or "").upper()
    return market if market in {"KR", "US"} else ""


def _sample_session_date(market: str, sample_dt: datetime, reference_dt: datetime) -> str:
    if market not in {"KR", "US"}:
        return ""
    compared = _compare_dt(sample_dt, reference_dt)
    if compared.tzinfo is None:
        compared = compared.replace(tzinfo=KST)
    else:
        compared = compared.astimezone(KST)
    return resolve_session_date_str(market, compared)


def _same_row_session_samples(
    *,
    market: str,
    session_date: str,
    samples: list[dict[str, Any]],
    reference_dt: datetime,
) -> list[dict[str, Any]]:
    target_session = str(session_date or "")[:10]
    if market not in {"KR", "US"} or not target_session:
        return []
    filtered = []
    for sample in samples:
        dt = _compare_dt(sample["dt"], reference_dt)
        if _sample_session_date(market, dt, reference_dt) == target_session:
            filtered.append({**sample, "dt": dt})
    return filtered
```

`session_date`가 없는 row는 같은 세션 여부를 증명할 수 없으므로 보수적으로 분봉 샘플 전체를 차단한다. `market`도 마찬가지로 `KR` 또는 `US`가 아니면 `resolve_session_date_str("", ...)` 같은 암묵 fallback을 사용하지 않는다.

`_infer_entry_price_from_minute()`는 다음처럼 같은 세션 샘플만 대상으로 진입가를 찾는다.

```python
market = _row_market(row)
session_date = _row_session_date(row)
normalized = _same_row_session_samples(
    market=market,
    session_date=session_date,
    samples=samples,
    reference_dt=trigger_at,
)
latest_entry_sample_at = trigger_at + MAX_ENTRY_SAMPLE_LATENCY
observed = next(
    (
        sample
        for sample in normalized
        if trigger_at <= sample["dt"] <= latest_entry_sample_at
    ),
    None,
)
if observed is None:
    return None, {
        **metadata,
        "entry_price_reason": "minute_entry_sample_missing",
        "entry_price_session_date": session_date,
        "entry_price_session_filter": "same_session",
        "entry_price_max_latency_minutes": MAX_ENTRY_SAMPLE_LATENCY.total_seconds() / 60,
    }, ["entry_price"]
```

latency 상한의 목적은 같은 세션 안이라도 트리거 후 수십 분 또는 수 시간 뒤 첫 샘플을 진입가로 쓰는 것을 막는 것이다. `minute_entry_sample_missing`은 "같은 세션, trigger 이후, latency 상한 안의 샘플이 없음"을 뜻하도록 정의한다. 구현자가 더 세분화하고 싶다면 `minute_entry_sample_too_late`를 추가할 수 있지만, 그 경우 `ENTRY_PRICE_TRANSIENT_REASONS`에도 반드시 포함해야 한다.

주의할 점:

- `resolve_session_date_str()`를 사용해 US 장의 KST 자정 이후, 05:00 이전 샘플이 전일 US 세션으로 유지되도록 한다.
- 기존 `_compare_dt()`의 timezone 정렬 규칙을 유지한다.
- 같은 세션 샘플이 없을 때 다음 세션으로 fallback하지 않는다.
- 같은 세션 샘플이 있더라도 `MAX_ENTRY_SAMPLE_LATENCY`를 넘으면 진입가로 사용하지 않는다.
- `session_date` 또는 `market`이 없으면 분봉 inference는 실패 처리한다.
- 기존 상태 전이와 retry 정책을 깨지 않도록 `minute_entry_sample_missing`은 transient reason으로 유지한다.

### 전후 개선

변경 전:

- `entry_price`가 없는 wait-path row에서 다음 거래일 첫 분봉 가격이 진입가로 들어갈 수 있다.
- 당일 종가와 다음날 진입가가 섞여 `outcome_close_pct`가 채워질 수 있다.
- 한 번 채워진 잘못된 라벨은 이후 retry에서 자연스럽게 교정되지 않는다.

변경 후:

- row의 `session_date`와 같은 세션의 분봉만 진입가 후보가 된다.
- 같은 세션 분봉이 없으면 `PRICE_PENDING` 또는 `PRICE_UNAVAILABLE` 상태로 남아 라벨을 채우지 않는다.
- 같은 세션 데이터가 나중에 들어오면 retry로 정상 보강할 수 있다.
- counterfactual 라벨의 날짜 정합성이 보장된다.

### 테스트 요구사항

`tests/test_update_counterfactual_outcomes.py`에 최소 다음 테스트를 추가하거나 기존 테스트를 확장한다.

1. 다음 세션 분봉을 진입가로 사용하지 않는지 검증
   - row `session_date=2026-05-19`
   - `trigger_time=2026-05-19T09:35:00+09:00`
   - 분봉 CSV에는 `2026-05-20T09:35:00+09:00` 샘플만 존재
   - 일봉 CSV에는 `2026-05-19` 종가 존재
   - 기대값: `entry_price is None`, `outcome_close_pct is None`, `filled == 0`
   - 기대값: metadata reason은 `minute_entry_sample_missing`
   - 기대값: status는 과거 세션이면 `PRICE_UNAVAILABLE`, 현재/미래 세션이면 `PRICE_PENDING`

2. 같은 세션 분봉은 기존처럼 진입가와 minute outcome을 채우는지 회귀 검증
   - 기존 `test_update_counterfactual_outcomes_infers_wait_entry_from_minute_csv`는 계속 통과해야 한다.

3. 같은 세션이지만 latency 상한을 넘은 샘플을 진입가로 사용하지 않는지 검증
   - row `trigger_time=2026-05-19T09:35:00+09:00`
   - 분봉 CSV에는 `2026-05-19T11:35:00+09:00` 샘플만 존재
   - 기대값: `entry_price is None`, metadata reason은 `minute_entry_sample_missing`
   - 기대값: close outcome도 채우지 않는다.

4. `session_date` 또는 `market`이 없는 row에서 분봉 inference를 하지 않는지 검증
   - session을 증명할 수 없으면 전체 샘플을 차단한다.
   - 기대값: 다음 세션뿐 아니라 같은 날짜처럼 보이는 샘플도 사용하지 않는다.

5. 권장: minute outcome도 다음 세션 샘플로 채우지 않는지 검증
   - 같은 세션의 30분/60분 target 샘플이 없고 다음 세션 샘플만 있을 때 `outcome_30m_pct`, `outcome_60m_pct`, MFE/MAE는 결측으로 남아야 한다.

6. 권장: US KST 자정 이후 세션 경계 검증
   - US 샘플 시간이 KST 00:00-04:59 사이일 때 `resolve_session_date_str("US", sample_dt)` 기준으로 전일 세션에 포함되는지 검증한다.

## 3. 개선 항목 B - 대시보드 주문금액 저장을 공통 cap으로 단일화

현재 구현 기준은 다음과 같다.

- `MAX_ORDER_KRW`가 실주문 공통 cap의 기준이다.
- 대시보드 주문금액 저장은 선택 시장과 무관하게 `MAX_ORDER_KRW`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`를 같은 금액으로 동기화한다.
- `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`는 보조/호환 key로 남기며, 서로 다른 시장별 금액을 표현하지 않는다.
- `trading_bot.py`의 `MAX_ORDER_KRW` 소비 경로는 공통 주문한도 정책과 일치하므로 시장별 cap resolver를 추가하지 않는다.

### 왜 수정하는가

대시보드 주문금액 컨트롤은 실제로는 다음 재시작 시 적용될 공통 주문 cap을 바꾸는 기능이다. UI가 시장별 값처럼 보이거나 보조 key가 서로 다른 값으로 남아 있으면 운영자가 실제 적용 기준을 오해할 수 있다.

`MAX_ORDER_KRW`는 `trading_bot.py`와 `risk_manager.py`에서 공통 주문상한으로 읽힌다. 따라서 현재 운영 결정에서는 `MAX_ORDER_KRW`를 기준값으로 두고, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`를 같은 값으로 동기화해 재시작 후 적용값을 명확히 한다.

이 문제는 시장별 분리가 아니라 "공통 cap임을 명시하고 모든 저장 경로를 같은 의미로 맞추는" 설정 일관성 문제다.

### 현재 코드 흐름

`dashboard/dashboard_server.py`의 `_order_size_config_keys()`는 현재 다음 공통 write set을 반환한다.

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

`_update_start_config_order_size()`는 이 목록의 모든 key를 `config/v2_start_config.json`의 `env_overrides`에 기록한다.

```python
keys = _order_size_config_keys(market_key)
for key in keys:
    overrides[key] = str(amount)
```

결과적으로 대시보드 저장 API는 전역 공통 cap과 보조 key를 함께 동기화한다.

### 무엇을 수정하는가

주문금액 저장 의미를 "시장별 금액"이 아니라 "공통 주문한도"로 명확히 한다.

요구사항:

- 대시보드는 주문금액 입력을 "다음공통"으로 표시한다.
- 저장 시 `MAX_ORDER_KRW`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`를 같은 값으로 맞춘다.
- `_summary_order_size_setting_krw()`는 `MAX_ORDER_KRW`를 우선 읽고, 없을 때만 legacy 보조 key로 fallback한다.
- `trading_bot.py`와 `risk_manager.py`는 `MAX_ORDER_KRW` 공통 cap 소비 경로를 유지한다.
- API 응답의 `updated_keys`는 공통 동기화 대상 key 전체를 반환한다.

PathB 주문금액도 현재 운영에서는 공통 주문한도와 같이 맞춘다. 이후 KR/US 또는 PathA/PathB 금액을 다시 분리하려면 별도 요구사항으로 다룬다.

### 어떻게 수정하는가

수정 예시:

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

`_update_start_config_order_size()`는 기존 구조를 유지하되 공통 key를 모두 같은 값으로 쓴다.

```python
keys = _order_size_config_keys(market_key)
for key in keys:
    overrides[key] = str(amount)
    if key in data:
        data[key] = amount
```

주의할 점:

- 기존 unrelated config 값은 보존한다.
- 저장 후 응답의 `updated_keys`에 네 개 공통 key가 모두 있어야 한다.
- `tests/test_dashboard_buy_range.py`의 저장 테스트 기대값을 공통 동기화 기준으로 변경한다.
- 텔레그램 `/setorder`와 대시보드가 같은 start config 기준을 쓰는지 함께 검증한다.

### 전후 개선

변경 전:

- 대시보드가 시장별 주문금액처럼 보이면서 실제로는 `MAX_ORDER_KRW` 공통 cap에도 영향을 줄 수 있다.
- `MAX_ORDER_KRW`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`가 서로 다른 값으로 남아 재시작 후 적용 기준을 오해할 수 있다.

변경 후:

- 대시보드는 "다음공통" 주문한도를 표시한다.
- 저장 시 공통 cap과 보조 key가 같은 값으로 맞춰진다.
- `TradingBot` 재시작 후 `MAX_ORDER_KRW` 기준으로 실제 실행 주문한도가 일관되게 적용된다.

### 테스트 요구사항

`tests/test_dashboard_buy_range.py`에 최소 다음 테스트를 반영한다.

1. US 저장 시 공통 key 동기화
   - config에 기존 `env_overrides["MAX_ORDER_KRW"] = "300000"` 존재
   - `_update_start_config_order_size("US", 650000)` 호출
   - 기대값: `env_overrides["MAX_ORDER_KRW"] == "650000"`
   - 기대값: `env_overrides["KR_FIXED_ORDER_KRW"] == "650000"`
   - 기대값: `env_overrides["US_FIXED_ORDER_KRW"] == "650000"`
   - 기대값: `env_overrides["PATHB_FIXED_ORDER_KRW"] == "650000"`
   - 기대값: `result["updated_keys"]`에 네 개 공통 key 모두 포함

2. KR 저장 시 공통 key 동기화
   - `_update_start_config_order_size("KR", 550000)` 호출
   - 네 개 공통 key가 모두 `550000`으로 변경되는지 확인

3. 요약 표시 기준 검증
   - `MAX_ORDER_KRW`와 시장별 보조 key가 서로 다르면 `MAX_ORDER_KRW`가 우선되는지 확인

4. invalid start config 거부
   - JSON parse 실패, top-level object 아님, `env_overrides` object 아님 케이스에서 기존 파일을 재작성하지 않는지 확인

## 4. 구현 순서

1. 실패 재현 테스트를 먼저 추가한다.
   - counterfactual 다음 세션 분봉 오염 테스트
   - dashboard 공통 주문한도 동기화 테스트

2. `tools/update_counterfactual_outcomes.py`를 수정한다.
   - row session helper 추가
   - sample session resolver 추가
   - `_infer_entry_price_from_minute()`에 same-session filter 적용
   - 권장 범위로 `_minute_outcomes()`에도 same-session filter 적용

3. `dashboard/dashboard_server.py`를 수정한다.
   - `_order_size_config_keys()`가 네 개 공통 key를 반환하도록 유지
   - `_summary_order_size_setting_krw()`가 `MAX_ORDER_KRW`를 우선 읽도록 유지
   - `_update_start_config_order_size()` 응답의 `updated_keys`가 공통 write set을 반영하도록 유지

4. 테스트 기대값을 갱신한다.
   - 기존 US 주문금액 저장 테스트의 `updated_keys`와 env override 기대값 변경
   - KR/US 어느 쪽에서 저장해도 공통 key가 모두 동기화되는 테스트 추가

5. 회귀 검증을 실행한다.

## 5. 검증 명령

우선 실행:

```powershell
python -m pytest tests/test_update_counterfactual_outcomes.py tests/test_dashboard_buy_range.py -q
python -m py_compile tools/update_counterfactual_outcomes.py dashboard/dashboard_server.py
```

운영 설정 영향 확인:

```powershell
python tools/live_preflight.py --mode paper --skip-dashboard --json
python tools/live_preflight.py --mode live --skip-dashboard --json
```

변경 범위가 `trading_bot.py`, risk cap resolver, live config 로딩까지 확장되면 추가로 실행:

```powershell
python -m pytest tests/test_action_routing.py -q
python -m pytest -q
```

## 6. 완료 기준

Counterfactual:

- `entry_price`가 없는 wait-path row에서 다음 세션 분봉 샘플을 진입가로 사용하지 않는다.
- `session_date` 또는 `market`이 없는 row는 분봉 진입가 추론을 하지 않는다.
- 같은 세션 샘플이라도 `MAX_ENTRY_SAMPLE_LATENCY`를 넘으면 진입가로 사용하지 않는다.
- 같은 세션 샘플이 없으면 close outcome을 채우지 않는다.
- 같은 세션 데이터가 존재하면 기존처럼 entry, close outcome, minute outcome을 채운다.
- metadata에 분봉 source, sample count, session filter 정보가 남는다.

Dashboard:

- 대시보드는 "다음공통" 주문한도를 표시한다.
- US/KR 어느 쪽에서 저장해도 `MAX_ORDER_KRW`, `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`가 같은 값으로 동기화된다.
- API 응답 `updated_keys`가 공통 write set과 일치한다.
- `_summary_order_size_setting_krw()`는 `MAX_ORDER_KRW`를 우선 읽고, 없을 때만 legacy 보조 key로 fallback한다.

최종적으로 두 개선은 다음 상태를 보장해야 한다.

- counterfactual 라벨은 row의 시장 세션과 같은 가격 데이터로만 계산된다.
- dashboard/telegram 주문금액 저장은 공통 주문한도의 다음 재시작 설정을 같은 기준으로 변경한다.

## 7. 재검토 결과

피드백 반영 후 문서를 다시 검토한 결과, 구현자가 혼동할 수 있던 세 지점은 요구사항으로 명확해졌다.

반영 사항:

- `market`을 row 내부에서 암묵적으로 읽어 `""` 상태로 세션 계산에 넘기지 않도록 했다. helper는 `market`과 `session_date`를 명시 파라미터로 받고, 둘 중 하나라도 유효하지 않으면 빈 샘플 목록을 반환한다.
- `session_date`가 없는 row는 같은 세션 여부를 증명할 수 없으므로 분봉 inference를 하지 않는다고 명시했다.
- `MAX_ENTRY_SAMPLE_LATENCY = timedelta(minutes=5)`를 권장 기본값으로 추가했다. 같은 세션 안이라도 trigger 이후 너무 늦은 첫 샘플을 진입가로 쓰지 않는다.
- `entry_price_reason`과 status의 역할을 분리했다. reason은 `minute_entry_sample_missing`, status는 row 시점에 따라 `PRICE_PENDING` 또는 `PRICE_UNAVAILABLE`로 기록한다.
- latency 초과, session/market 누락 테스트를 추가 요구사항에 포함했다.

남은 구현 선택 사항:

- `_minute_outcomes()`까지 same-session filter를 적용하는 것은 권장 범위였고, 라벨 오염 방지 관점에서 구현에 포함했다.
- `PATHB_FIXED_ORDER_KRW`는 현재 운영 결정에 따라 공통 주문한도 저장 시 함께 동기화한다. PathB 금액을 별도로 분리하려면 후속 요구사항으로 다룬다.

최종 판단:

- 문서는 P2 리뷰 두 건을 코드 수정으로 바로 전환할 수 있는 수준이다.
- 구현 시 첫 커밋 범위는 테스트 추가 후 `tools/update_counterfactual_outcomes.py`, `dashboard/dashboard_server.py`, 관련 테스트 수정으로 제한하는 것이 적절하다.
