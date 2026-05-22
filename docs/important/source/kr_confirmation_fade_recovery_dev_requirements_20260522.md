# KR confirmation data_quality 및 fade recovery 개발 요구서

작성일: 2026-05-22

## 목적

KR 장중 후보가 실제로는 분봉 evidence가 완성된 상태인데도 `KR confirmation gate`에서 `data_quality`를 미확정으로 판단해 `BUY_READY`/`PULLBACK_WAIT`가 `WATCH`로 강등되는 문제를 수정한다.

또한 `momentum_state=fade`가 모든 경우에 `WATCH`로 고정되는 현재 정책을 바로 완화하지 않고, KR 전용 `fade_recovered` shadow 판정으로 먼저 관찰한다.

## 범위

### 시장 범위

이번 요구서는 한국장(KR) 전용이다.

- KR: `minute_complete` data quality 버그 수정 대상
- KR: `fade_recovered`는 shadow 관찰만 추가
- US: 코드 동작 변경 없음
- US: `fade` 완화 없음
- 공통 PathB 운영 파라미터 변경 없음

### 포함

- `trading_bot.py`의 KR confirmation gate에서 `minute_complete`를 정상 품질로 인정
- `confirmed`를 raw `data_quality`로 추가하지 않는 계약 명시
- KR-only `fade_recovered` shadow 판정 요구사항 정의
- `live_evidence_pack.py`, `adaptive_live_condition.py`, `action_routing.py`의 영향 지점 정리
- 테스트 요구사항 정의

### 제외

- US fade 완화
- KR fade 후보의 즉시 live 주문 허용
- `PULLBACK_WAIT`를 통한 PathB live 등록 예외 허용
- PathB 운영 파라미터 변경

## 현상 요약

2026-05-22 KR 재판단에서 Claude는 일부 종목을 `BUY_READY`로 판단했지만 최종 `trade_ready`는 비었다.

대표 케이스:

- `456010`: `data_quality=minute_complete`, `evidence_data_state=confirmed`, `momentum_state=fade`, `pullback_from_high_pct=-13.37`
- `036540`: `data_quality=minute_complete`, `evidence_data_state=confirmed`, `momentum_state=fade`, `pullback_from_high_pct=-5.04`, OR/VWAP 회복

현재 `trading_bot.py::_kr_confirmation_gate_state()`는 아래 값만 정상 품질로 인정한다.

```python
{"good", "normal", "ok"}
```

하지만 KR intraday feature 생성 경로의 실제 반환값은 다음과 같다.

```python
# runtime/intraday_features.py
minute_missing   # bar_count <= 0 또는 current_price 누락
minute_complete  # 필수 필드 완성
minute_partial   # 일부 필드 누락
```

따라서 `minute_complete`를 인정하지 않는 것은 버그다.

## 1단계: data_quality 버그 수정

### 수정 파일

- `trading_bot.py`
- `tests/test_candidate_action_live_mapping.py` 또는 KR confirmation gate 테스트가 있는 기존 테스트 파일

### 코드 변경 요구

`TradingBot._kr_confirmation_gate_state()` 내부의 `data_quality_ok` 판단을 수정한다.

현재:

```python
"data_quality_ok": (not data_quality_missing) and data_quality in {"good", "normal", "ok"},
```

변경:

```python
KR_CONFIRMED_DATA_QUALITIES = {"good", "normal", "ok", "minute_complete"}

"data_quality_ok": (
    not data_quality_missing
    and data_quality in KR_CONFIRMED_DATA_QUALITIES
),
```

상수 위치는 함수 내부 로컬 상수여도 된다. 전역으로 뺄 경우 KR confirmation 전용임을 이름에 드러낸다.

### 명시적 비허용

아래 값은 계속 차단해야 한다.

```python
{"minute_partial", "minute_missing", "first_observed", "unknown", "missing", ""}
```

`confirmed`는 `runtime/live_evidence_pack.py`의 `data_state` 값이지 KR raw `data_quality` 값이 아니다. 따라서 `data_quality` 허용 세트에 `confirmed`를 추가하지 않는다.

### 테스트 요구

필수 테스트:

1. KR confirmation gate가 `data_quality=minute_complete`를 정상 품질로 인정한다.
2. `data_quality=minute_partial`은 여전히 `kr_data_quality_not_confirmed`로 차단한다.
3. `data_quality=minute_missing`은 여전히 차단한다.
4. 기존 `good`/`normal`/`ok` 테스트는 유지한다.

권장 테스트 이름:

```python
def test_kr_confirmation_accepts_minute_complete_quality(self): ...
def test_kr_confirmation_blocks_minute_partial_quality(self): ...
```

### 수용 기준

- `036540`과 같은 KR 분봉 완성 후보에서 `data_quality=minute_complete`만을 이유로 `kr_confirmation_reason=kr_data_quality_not_confirmed`가 발생하지 않는다.
- 1단계 이후에도 해당 후보는 `momentum_state=fade` 또는 `evidence_action_ceiling=WATCH` 때문에 최종 `WATCH`로 남을 수 있다.
- 즉, 이 단계의 성공 기준은 "데이터 품질 차단 제거"이지 "진입 허용"이 아니다. fade 정책은 열지 않는다.

## 1.1단계: data_quality 일관성 점검

`runtime/action_routing.py`에도 `good_data` 판정이 있다.

```python
good_data = (not data_quality_missing) and str(data_quality or "").lower() in {"good", "normal", "ok"}
```

이 판정은 PathB waiting 상태에서 `BUY_READY`가 기존 PathB wait를 취소할 수 있는지 판단하는 보조 경로다.

요구사항:

- 이번 1단계의 필수 수정 범위는 아니다.
- 다만 KR `minute_complete`를 시스템 전반에서 정상 품질로 취급하려면 후속 PR에서 동일하게 반영한다.
- 반영 시에는 KR/US 공통 부작용을 줄이기 위해 helper 함수로 분리한다.

권장 helper:

```python
def _is_confirmed_runtime_quality(data_quality: str, *, market: str = "") -> bool:
    quality = str(data_quality or "").strip().lower()
    if quality in {"good", "normal", "ok"}:
        return True
    if str(market or "").upper() == "KR" and quality == "minute_complete":
        return True
    return False
```

## 2단계: KR-only fade_recovered shadow

### 목적

`momentum_state=fade`가 항상 나쁜 상태는 아니다. 고점 대비 깊게 밀린 종목은 계속 차단해야 하지만, OR/VWAP을 재회복하고 pullback이 제한적인 종목은 관찰 대상으로 분리한다.

단, 이 단계에서는 주문 가능 경로를 열지 않는다.

### 수정 후보 파일

- `runtime/live_evidence_pack.py`
- `runtime/adaptive_live_condition.py`
- 필요 시 `trading_bot.py`의 route/evidence context 로그 보강
- 테스트:
  - `tests/test_live_evidence_pack.py`
  - `tests/test_candidate_action_live_mapping.py`
  - 필요 시 `tests/test_action_routing.py`

### KR-only 격리 가능성

`build_live_evidence_pack()`는 이미 `market` 파라미터를 받는다.

```python
def build_live_evidence_pack(*, market: str, ticker: str, ...):
    market_key = str(market or "").upper()
```

따라서 `market_key == "KR"` 조건으로 KR 전용 shadow 판정을 넣을 수 있다. 시그니처 변경은 필요 없다.

### fade_recovered 판정 조건

필수 조건:

```python
market_key == "KR"
momentum_state == "fade"
data_quality == "minute_complete"
data_state == "confirmed"
pullback_from_high_pct is not None
pullback_from_high_pct >= -7.0
vi_active is not True
spread_bps is None or spread_bps <= KR_CONFIRMATION_MAX_SPREAD_BPS
```

회복 조건:

```python
or_recovered = opening_range_break is True
vwap_recovered = vwap_distance_pct is not None and vwap_distance_pct >= 0

or_recovered or vwap_recovered
```

`opening_range_break`는 `current_price >= opening_range_high`의 정규화된 boolean으로 본다. `live_evidence_pack.py`에서 `opening_range_high` 원시값이 항상 보장되지 않으므로 shadow 판정의 기본 입력은 `opening_range_break`를 사용한다.

`vwap_distance_pct >= 0`은 `current_price >= vwap`의 정규화된 수치 표현으로 본다. `vwap` 원시값이 없더라도 `vwap_distance_pct`가 있으면 VWAP 회복 판정에 사용한다.

권장 최소 조건은 OR 회복과 VWAP 회복 중 1개 이상이다. 라이브 승격 검토 시에는 둘 다 요구하는 방향이 더 안전하다.

명시 차단:

```python
pullback_from_high_pct < -7.0
data_quality != "minute_complete"
data_state != "confirmed"
vi_active is True
hard_blocks exists
```

주의:

- `pullback_from_high_pct`는 고점 대비 하락률이며 보통 음수다.
- `from_open_high_pct`는 장중 고점 상승폭 계열 값이다.
- 이 요구사항의 `-7.0` 조건에 사용할 값은 반드시 `pullback_from_high_pct`다.

### live_evidence_pack.py 요구

현재:

```python
if hard_blocks or momentum_state == "fade":
    action_ceiling = "WATCH"
```

shadow 단계에서는 이 ceiling을 바꾸지 않는다.

대신 pack에 아래 필드를 추가한다.

```python
"fade_recovered_shadow": bool(fade_recovered),
"fade_recovered_reason": "kr_or_vwap_recovered_pullback" 또는 "",
"fade_recovered_checks": {
    "market_kr": bool,
    "quality_complete": bool,
    "data_confirmed": bool,
    "pullback_ok": bool,
    "or_recovered": bool,
    "vwap_recovered": bool,
    "vi_safe": bool,
    "spread_ok": bool,
},
```

shadow 단계의 `action_ceiling`은 계속 `WATCH`여야 한다.

`summarize_live_evidence()`에는 운영 관찰을 위해 summary 필드를 추가한다.

```python
"fade_recovered_shadow": {
    "count": int,
    "tickers": list[str],  # 최대 20개로 cap
}
```

주의: `trading_bot.py::_record_candidate_funnel_snapshot()`는 현재 내부 meta의 `_live_evidence.packs`를 제외한 summary만 `candidate_funnel_snapshot` payload의 `live_evidence` 필드에 기록한다. 따라서 per-ticker 관찰이 필요하면 아래 관찰/저장 계약을 함께 구현해야 한다.

### adaptive_live_condition.py 요구

현재:

```python
if momentum_state == "fade":
    action_ceiling = "WATCH"
    blockers.append("fade")
```

shadow 단계에서는 이 로직을 바꾸지 않는다.

대신 결과 row에 아래 값을 추가한다.

```python
"fade_recovered_shadow": bool(fade_recovered),
"fade_recovered_suggested_action": "PROBE_READY" if fade_recovered else "",
```

단, `claude_reask=True`로 만들지 않는다. 즉, shadow 로그에만 남긴다.

### shadow 관찰/저장 계약

2단계 구현 시 `fade_recovered_shadow`는 반드시 사람이 확인 가능한 로그에 남긴다.

최소 요구:

- `logs/funnel/candidate_funnel_snapshot_<session_date>_KR.jsonl`
  - payload의 `live_evidence.fade_recovered_shadow.count`와 `live_evidence.fade_recovered_shadow.tickers`를 남긴다.
- `logs/funnel/live_evidence_shadow_<session_date>_KR.jsonl`
  - `fade_recovered_shadow=True`인 ticker별 상세 row를 남긴다.

권장 상세 row:

```python
{
    "ticker": str,
    "market": "KR",
    "data_quality": str,
    "data_state": str,
    "momentum_state": str,
    "action_ceiling": str,
    "fade_recovered_shadow": bool,
    "fade_recovered_reason": str,
    "fade_recovered_checks": dict,
    "pullback_from_high_pct": float | None,
    "opening_range_break": bool | None,
    "vwap_distance_pct": float | None,
    "spread_bps": float | None,
    "vi_active": bool,
}
```

구현 위치는 `trading_bot.py::_record_candidate_funnel_snapshot()`가 적절하다. 이미 `_live_evidence.packs`를 읽을 수 있고 `_write_funnel_event()`를 통해 `logs/funnel` JSONL에 남기는 패턴이 있다.

### action_routing.py 요구

현재 `PULLBACK_WAIT`는 `_negative_watch_context()`에서 `fade`면 최종 `WATCH`가 된다.

```python
if momentum in {"fade", "fading", "weak", "weakening", "direction_unconfirmed"}:
    return True
```

shadow 단계에서는 이 로직을 바꾸지 않는다.

이유:

- `PULLBACK_WAIT`는 PathB price plan 등록으로 이어질 수 있다.
- PathB live는 buy zone 진입 시 실제 주문 가능 경로다.
- 따라서 shadow 단계에서 route 예외를 열면 안 된다.

## 3단계: KR fade_recovered live 승격 검토

이 단계는 별도 승인 후 진행한다.

승격 가능 형태:

- `BUY_READY` 복원 금지
- 최대 `PROBE_READY`
- `PULLBACK_WAIT` 허용은 별도 승인 필요

필수 추가 안전장치:

```python
KR_FADE_RECOVERY_LIVE_ENABLED=false
KR_FADE_RECOVERY_MAX_PER_SESSION=2
KR_FADE_RECOVERY_PULLBACK_MIN=-7.0
KR_FADE_RECOVERY_REQUIRE_OR=true
KR_FADE_RECOVERY_REQUIRE_VWAP=true
```

`PULLBACK_WAIT`를 열 경우 추가 필요:

```python
KR_FADE_RECOVERY_PATHB_WAIT_ENABLED=false
```

기본값은 반드시 `false`다.

## 테스트 계획

### 단위 테스트

```powershell
python -m pytest tests/test_candidate_action_live_mapping.py -q
python -m pytest tests/test_live_evidence_pack.py -q
python -m pytest tests/test_action_routing.py -q
```

### 필수 테스트 케이스

1. `minute_complete` KR confirmation 통과
2. `minute_partial` KR confirmation 차단
3. `456010` 유사 케이스:
   - `momentum_state=fade`
   - `pullback_from_high_pct=-13.37`
   - `fade_recovered_shadow=False`
   - `action_ceiling=WATCH`
4. `036540` 유사 케이스:
   - `momentum_state=fade`
   - `pullback_from_high_pct=-5.04`
   - OR/VWAP 회복
   - `fade_recovered_shadow=True`
   - shadow 단계에서는 `action_ceiling=WATCH`
   - summary에 `fade_recovered_shadow.count >= 1`
5. US fade 케이스:
   - `market=US`
   - `momentum_state=fade`
   - `fade_recovered_shadow=False`
   - 기존 WATCH 유지
6. `PULLBACK_WAIT + fade`는 shadow 단계에서도 `WATCH` 유지

### QA 명령

```powershell
python -m py_compile trading_bot.py runtime/live_evidence_pack.py runtime/adaptive_live_condition.py runtime/action_routing.py
python -m pytest tests/test_candidate_action_live_mapping.py tests/test_live_evidence_pack.py tests/test_action_routing.py tests/test_claude_trade_quality_rework.py -q
```

## 운영 관찰 포인트

1단계 배포 후:

- `kr_confirmation_reason=kr_data_quality_not_confirmed`가 `minute_complete` 후보에서 사라지는지 확인
- 대신 `evidence_ceiling_watch`, `kr_momentum_not_confirmed`, `kr_fast_trigger_not_confirmed` 등 실제 다른 차단 사유가 남는지 확인

2단계 shadow 배포 후:

- `logs/funnel/candidate_funnel_snapshot_<session_date>_KR.jsonl`의 `live_evidence.fade_recovered_shadow.count`
- `logs/funnel/live_evidence_shadow_<session_date>_KR.jsonl`의 ticker별 `fade_recovered_shadow=True` 상세 row
- 해당 종목의 5분/15분/30분 후 수익률
- `456010`처럼 깊은 fade가 계속 false인지 확인
- `036540`처럼 OR/VWAP 회복 fade가 shadow true로 분리되는지 확인

## 최종 판단

즉시 개발 대상은 1단계 `minute_complete` 버그 수정이다.

`fade_recovered`는 주문 경로를 열지 않는 KR-only shadow로 먼저 개발한다. 실제 `PROBE_READY` 또는 `PULLBACK_WAIT` 승격은 shadow 관찰 결과를 본 뒤 별도 승인으로 진행한다.
