# US KIS Ranking Screener 코드 레벨 개발 요구서

작성일: 2026-05-22

대상 범위:

- `kis_api.py`
- `trading_bot.py`
- `tests/test_screener_quality.py`
- `tests/test_sub_screener_integration.py`
- 필요 시 `.env.example`

참조:

- KIS Developers API 문서: https://apiportal.koreainvestment.com/apiservice
- 공식 샘플 `trade_vol.py`: https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/trade_vol/trade_vol.py
- KIS MCP API 목록: https://github.com/koreainvestment/koreainvestment-mcp

## 1. 목적

현재 US 스크리너는 `Yahoo Finance -> FMP -> fallback` 순서로 후보를 수집한다. KIS 공식 API에 해외주식 ranking 계열 엔드포인트가 있으므로, US 스크리너의 1차 데이터 소스를 KIS로 전환한다.

목표 동작:

- US 후보 스크리닝 1차 소스는 KIS `overseas-stock/v1/ranking/*`를 사용한다.
- KIS 실패, 토큰 없음, 응답 공백, 품질 미달 시 기존 Yahoo/FMP/fallback 경로를 그대로 유지한다.
- 기존 `US_SCREEN_CACHE_TTL_SEC` 기반 캐시와 sub-screener `force_refresh` 동작은 깨지지 않아야 한다.
- 주문, risk, PathB, broker truth 로직은 변경하지 않는다.

## 2. 현재 코드 진단

KR은 `kis_api.py`의 `_kis_volume_rank()`가 KIS 국내 거래량순위 API를 직접 호출한다.

```python
url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
headers=_headers(token, "FHPST01710000")
```

US는 `kis_api.py`의 `screen_market_us()`에서 다음 순서로만 동작한다.

```python
_yf_screen_candidates()
_fmp_screen_candidates()
_US_FALLBACK_UNIVERSE
```

`trading_bot.py`의 `_screen_market_candidates()`는 현재 US 호출 시 token을 넘기지 않는다.

```python
raw_candidates = screen_market_us(top_n=top_n, mode=mode)
```

따라서 구현이 필요한 핵심은 다음 두 가지다.

- `kis_api.py`에 KIS US ranking 수집/정규화 함수 추가
- `trading_bot.py`에서 US token을 `screen_market_us()`로 전달

## 3. KIS API 범위

P0에서 구현할 엔드포인트:

| 목적 | KIS URL | TR ID | 기존 US category 대응 |
|---|---|---|---|
| 거래량순위 | `/uapi/overseas-stock/v1/ranking/trade-vol` | `HHDFS76310010` | `most_actives` |
| 상승율/하락율 | `/uapi/overseas-stock/v1/ranking/updown-rate` | `HHDFS76290000` | `day_gainers`, `day_losers` |

P1 후보 엔드포인트:

| 목적 | KIS URL | TR ID |
|---|---|---|
| 거래대금순위 | `/uapi/overseas-stock/v1/ranking/trade-pbmn` | `HHDFS76320010` |
| 거래량급증 | `/uapi/overseas-stock/v1/ranking/volume-surge` | `HHDFS76270000` |
| 매수체결강도상위 | `/uapi/overseas-stock/v1/ranking/volume-power` | `HHDFS76280000` |
| 시가총액순위 | `/uapi/overseas-stock/v1/ranking/market-cap` | `HHDFS76350100` |

P0는 현재 후보 구성 parity를 우선한다. `trade-pbmn`, `volume-surge`, `market-cap`은 별도 shadow 검증 후 확장한다.

## 4. 비범위

이번 변경에서 하지 않는다.

- 주문 수량/금액 계산 변경
- hard risk block, 손절, PathB live gate 변경
- `state/brain.json` 변경
- `.env.live`, `config/v2_start_config.json`의 운영 파라미터 자동 변경
- Yahoo/FMP 완전 제거
- KIS ranking 후보를 trade_ready로 자동 승격하는 정책 변경

## 5. 설정값

새 환경변수:

| 변수 | 기본값 | 의미 |
|---|---:|---|
| `US_KIS_SCREEN_ENABLED` | `true` | KIS US ranking 1차 사용 여부 |
| `US_KIS_SCREEN_EXCHANGES` | `NAS,NYS,AMS` | 조회 거래소. NAS=나스닥, NYS=뉴욕, AMS=아멕스 |
| `US_KIS_SCREEN_NDAY` | `0` | KIS N분전/일자 콤보값. P0 기본은 당일 |
| `US_KIS_SCREEN_VOL_RANG` | `0` | 거래량조건. 0=전체 |
| `US_KIS_SCREEN_PRC1` | empty | KIS 서버측 가격 하한. 기본은 비움, 기존 post-filter가 가격 필터 수행 |
| `US_KIS_SCREEN_PRC2` | empty | KIS 서버측 가격 상한 |
| `US_KIS_SCREEN_TIMEOUT_SEC` | `2.0` | KIS ranking GET timeout |
| `US_KIS_SCREEN_MAX_RETRIES` | `1` | KIS ranking 재시도 횟수 |
| `US_KIS_SCREEN_RETRY_BACKOFF_SEC` | `1.0` | 재시도 sleep |

기존 설정 유지:

- `US_SCREEN_CACHE_TTL_SEC`
- `US_SCREEN_MIN_CACHE_CANDIDATES`
- `US_SCREEN_MIN_CACHE_RATIO`
- `US_SCREEN_MIN_PRICE`
- `US_SCREEN_MAX_CHG_PCT`
- `US_SCREEN_MIN_DOLLAR_VOL`
- `US_QUOTA_ACTIVES`
- `US_QUOTA_GAINERS`
- `US_QUOTA_LOSERS`
- `US_DYNAMIC_LOSERS_QUOTA_ENABLED`

`US_KIS_SCREEN_ENABLED=true`여도 token이 없으면 KIS branch는 건너뛰고 기존 Yahoo/FMP 경로를 사용한다. 이 조건은 단위 테스트와 직접 호출 호환성을 위해 필요하다.

## 6. Public 함수 시그니처

`kis_api.py`의 `screen_market_us()` 시그니처를 token optional keyword로 확장한다.

```python
def screen_market_us(
    top_n: int = 30,
    mode: str = "NEUTRAL",
    *,
    token: str | None = None,
) -> list:
    ...
```

호환성 조건:

- 기존 호출 `screen_market_us(top_n=30, mode="NEUTRAL")`는 계속 동작해야 한다.
- token이 없는 기존 테스트는 KIS branch를 타지 않고 Yahoo/FMP 테스트를 유지해야 한다.
- `trading_bot.py`에서는 US일 때 `self._token_for_market("US")`를 넘긴다.

변경 대상:

```python
if market_key == "KR":
    raw_candidates = screen_market_kr(self._token_for_market("KR"), top_n=top_n, mode=mode)
else:
    raw_candidates = screen_market_us(
        top_n=top_n,
        mode=mode,
        token=self._token_for_market("US"),
    )
```

## 7. 신규 helper 설계

`kis_api.py`에 아래 helper를 추가한다.

### 7.1 설정 parser

```python
def _csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [part.strip().upper() for part in raw.split(",") if part.strip()]
```

### 7.2 KIS ranking GET

```python
def _kis_us_ranking_get(
    token: str,
    *,
    path: str,
    tr_id: str,
    params: dict,
    label: str,
) -> dict:
    payload = _kis_market_data_get(
        token=token,
        market="US",
        path=path,
        tr_id=tr_id,
        params=params,
        timeout_env="US_KIS_SCREEN_TIMEOUT_SEC",
        retry_env="US_KIS_SCREEN_MAX_RETRIES",
        backoff_env="US_KIS_SCREEN_RETRY_BACKOFF_SEC",
    )
    _require_kis_success(payload, label)
    return payload
```

주의:

- `_kis_market_data_get()`는 `_kis_get()`을 통해 토큰 만료 1회 자동 refresh를 사용한다.
- JSON body의 `rt_cd != "0"`은 `_require_kis_success()`로 명시 처리한다.
- 실패는 caller가 잡아서 Yahoo/FMP fallback으로 넘긴다.

### 7.3 응답 row 추출

```python
def _kis_rank_rows(payload: dict) -> list[dict]:
    rows = payload.get("output2") or payload.get("output") or []
    if isinstance(rows, dict):
        return [rows]
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []
```

KIS 샘플은 `output1`, `output2`를 사용한다. 일부 API가 `output`을 쓸 가능성에 대비해 방어적으로 처리한다.

### 7.4 숫자 parser

기존 `_safe_float_value()`, `_safe_int()`가 있으면 재사용한다. 없거나 위치상 부적합하면 US ranking helper 가까이에 private parser를 추가한다.

```python
def _kis_num(value: object, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default
```

### 7.5 row 정규화

```python
def _normalize_kis_us_rank_row(
    row: dict,
    *,
    category: str,
    exchange: str,
) -> dict | None:
    ticker = str(
        row.get("symb")
        or row.get("rsym")
        or row.get("ovrs_pdno")
        or row.get("pdno")
        or ""
    ).strip().upper()
    if not ticker:
        return None

    price = _kis_num(
        row.get("last")
        or row.get("stck_prpr")
        or row.get("ovrs_now_pric")
        or row.get("prpr")
    )
    volume = int(_kis_num(row.get("tvol") or row.get("acml_vol") or row.get("vol")))
    avg_volume = _kis_num(row.get("avol") or row.get("avg_vol"))
    trade_value = _kis_num(row.get("tamt") or row.get("acml_tr_pbmn") or price * volume)
    change_rate = _kis_num(
        row.get("rate")
        or row.get("prdy_ctrt")
        or row.get("chgrate")
        or row.get("diff_rate")
    )
    rank = int(_kis_num(row.get("rank") or row.get("data_rank") or row.get("rnk"), 0))

    return {
        "ticker": ticker,
        "name": row.get("name") or row.get("ename") or row.get("kor_name") or ticker,
        "price": price,
        "change_rate": change_rate,
        "volume": volume,
        "vol_ratio": round(volume / avg_volume, 4) if avg_volume > 0 else 1.0,
        "trade_value": trade_value,
        "rank": rank,
        "category": category,
        "exchange": exchange,
        "source": "kis_us_ranking",
        "provider": "kis",
        "volume_missing": False,
    }
```

필드명은 실제 응답 샘플에 따라 보강한다. 단위 테스트는 최소 `symb`, `name`, `last`, `rate`, `tvol`, `tamt` 조합과 대체 필드 조합을 모두 검증한다.

## 8. KIS 후보 수집 함수

### 8.1 거래량순위

```python
def _kis_us_trade_vol_candidates(token: str, *, exchanges: list[str]) -> list[dict]:
    out = []
    for excd in exchanges:
        payload = _kis_us_ranking_get(
            token,
            path="/uapi/overseas-stock/v1/ranking/trade-vol",
            tr_id="HHDFS76310010",
            label=f"US trade-vol {excd}",
            params={
                "EXCD": excd,
                "NDAY": os.getenv("US_KIS_SCREEN_NDAY", "0"),
                "VOL_RANG": os.getenv("US_KIS_SCREEN_VOL_RANG", "0"),
                "KEYB": "",
                "AUTH": "",
                "PRC1": os.getenv("US_KIS_SCREEN_PRC1", ""),
                "PRC2": os.getenv("US_KIS_SCREEN_PRC2", ""),
            },
        )
        for row in _kis_rank_rows(payload):
            normalized = _normalize_kis_us_rank_row(row, category="most_actives", exchange=excd)
            if normalized:
                out.append(normalized)
    return out
```

P0에서는 무한/재귀 연속조회는 구현하지 않는다. 한 번의 호출 결과로 top_n 구성 가능 여부를 확인한다. 연속조회가 필요하다고 판단되면 P1에서 `KEYB`/`tr_cont` 계약을 실제 응답 header 기준으로 별도 구현한다.

### 8.2 상승율/하락율

```python
def _kis_us_updown_candidates(
    token: str,
    *,
    exchanges: list[str],
    category: str,
    gubn: str,
) -> list[dict]:
    out = []
    for excd in exchanges:
        payload = _kis_us_ranking_get(
            token,
            path="/uapi/overseas-stock/v1/ranking/updown-rate",
            tr_id="HHDFS76290000",
            label=f"US updown-rate {category} {excd}",
            params={
                "EXCD": excd,
                "NDAY": os.getenv("US_KIS_SCREEN_NDAY", "0"),
                "GUBN": gubn,
                "VOL_RANG": os.getenv("US_KIS_SCREEN_VOL_RANG", "0"),
                "KEYB": "",
                "AUTH": "",
            },
        )
        for row in _kis_rank_rows(payload):
            normalized = _normalize_kis_us_rank_row(row, category=category, exchange=excd)
            if normalized:
                out.append(normalized)
    return out
```

`GUBN` 값은 공식 샘플과 실제 응답으로 검증한다. 요구값은 다음으로 둔다.

- 상승: `GUBN="1"`
- 하락: `GUBN="0"`

실제 KIS 문서에서 값이 다르면 구현 시 문서값을 우선하고 테스트 fixture도 함께 수정한다.

### 8.3 통합 수집

```python
def _kis_us_screen_candidates(token: str, *, top_n: int, mode: str, quota: dict) -> tuple[list[dict], dict]:
    exchanges = _csv_env("US_KIS_SCREEN_EXCHANGES", "NAS,NYS,AMS")
    raw_by_cat = {
        "most_actives": _kis_us_trade_vol_candidates(token, exchanges=exchanges),
        "day_gainers": _kis_us_updown_candidates(token, exchanges=exchanges, category="day_gainers", gubn="1"),
        "day_losers": _kis_us_updown_candidates(token, exchanges=exchanges, category="day_losers", gubn="0"),
    }
    ...
    return merged[:top_n], stats
```

정렬 요구:

- 카테고리 내부는 KIS rank가 있으면 `rank ASC`, 없으면 `volume DESC`, `abs(change_rate) DESC` 순으로 정렬한다.
- exchange fan-out 결과는 ticker 기준 중복 제거한다.
- 동일 ticker가 여러 category에 나오면 먼저 채택된 category를 유지하되 `source_categories` list에 추가 category를 남긴다.
- quota 적용은 기존 `screen_market_us()`의 `_quota` 계산 결과를 그대로 사용한다.

## 9. `screen_market_us()` 흐름 변경

기존 흐름을 아래 순서로 바꾼다.

1. 기존 캐시 확인
2. KIS fresh 수집
3. Yahoo Finance fresh 수집
4. FMP fallback
5. hardcoded fallback

KIS branch 진입 조건:

```python
kis_enabled = str(os.getenv("US_KIS_SCREEN_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
if kis_enabled and token:
    ...
```

KIS 성공 조건:

- merged 후보가 1개 이상이어야 한다.
- `_us_screen_quality_state(source="kis", ...)` 결과가 `OK`거나, 기존 YF와 동일하게 degraded라도 fresh result로는 반환할 수 있다.
- degraded 결과는 기존 정책대로 캐시에 저장하지 않는다.

KIS 실패 처리:

```python
except Exception as e:
    _logger.warning(f"[KIS US 스크리너] 실패: {e} -> Yahoo/FMP fallback")
```

로그 문구는 한국어로 유지한다.

## 10. post-filter 수정

`_us_post_filter_with_stats()`의 dollar volume 필터를 `trade_value` 우선으로 바꾼다.

현재:

```python
if not vol_miss and min_dollar_vol > 0 and price * volume < min_dollar_vol:
    ...
```

변경:

```python
dollar_volume = float(c.get("trade_value") or c.get("dollar_volume") or (price * volume) or 0)
if not vol_miss and min_dollar_vol > 0 and dollar_volume < min_dollar_vol:
    ...
```

이유:

- KIS ranking 응답은 거래대금 필드를 줄 수 있다.
- price * volume 계산보다 provider의 거래대금 필드가 있으면 우선한다.
- 기존 YF/FMP 후보는 `trade_value`가 없으므로 기존 계산과 동일하게 동작한다.

## 11. cache 계약

`_US_SCREEN_CACHE_SCHEMA`를 `3 -> 4`로 올린다.

`_cache_preset`에 아래 필드를 추가한다.

```python
{
    ...
    "schema": _US_SCREEN_CACHE_SCHEMA,
    "kis_enabled": kis_enabled,
    "kis_exchanges": _csv_env("US_KIS_SCREEN_EXCHANGES", "NAS,NYS,AMS"),
}
```

캐시 reuse 조건에 `source == "kis"`를 추가한다.

```python
elif source == "kis" and _has_meaningful_candidate_volume(cands):
    ...
```

캐시 저장 payload:

```python
{
    "date": today,
    "candidates": merged,
    "source": "kis",
    "cached_at": _time.time(),
    "mode": _cache_mode,
    "preset": _cache_preset,
    "schema": _US_SCREEN_CACHE_SCHEMA,
    "quality": quality,
}
```

force refresh 계약:

- `trading_bot._screen_market_candidates(..., force_refresh=True)`가 US에서 `US_SCREEN_CACHE_TTL_SEC=0`을 임시 설정하는 기존 방식은 유지한다.
- KIS 캐시도 같은 TTL 우회 영향을 받아야 한다.

## 12. quality 계약

KIS fresh 결과에도 기존 quality 필드를 동일하게 붙인다.

필수 필드:

- `screener_quality_state`
- `screener_degraded`
- `screener_degraded_reason`
- `screener_cache_used`
- `screener_cache_saved`
- `screener_cache_skipped_reason`
- `fresh_count`
- `min_cache_count`
- `source`

KIS raw stats:

```python
raw_count_by_category = {
    "most_actives": ...,
    "day_gainers": ...,
    "day_losers": ...,
}
filtered_count_by_category = {...}
dollar_reject_count_by_category = {...}
```

후보 row에도 아래 provider 표시를 남긴다.

```python
"source": "kis_us_ranking"
"provider": "kis"
"screener_source": "kis"
```

## 13. trading_bot 변경

`trading_bot.py`의 `_screen_market_candidates()`만 수정한다.

요구:

- KR token 전달 로직은 변경하지 않는다.
- US일 때 token 획득 실패가 전체 selection crash로 이어지지 않게 한다.
- token 획득 실패 시 `screen_market_us(..., token=None)`로 호출하거나 기존 fallback 경로가 실행되도록 한다.

권장 구현:

```python
else:
    us_token = ""
    try:
        us_token = self._token_for_market("US")
    except Exception as exc:
        log.warning(f"[US 스크리너] KIS token unavailable -> external fallback: {exc}")
    raw_candidates = screen_market_us(top_n=top_n, mode=mode, token=us_token or None)
```

주의:

- token 실패 때문에 KR/US 공통 selection state를 오염시키지 않는다.
- broker 불신/quarantine 판단은 기존 risk/execution 경로의 책임이며, 스크리너에서 신규 정책을 만들지 않는다.

## 14. 테스트 요구

### 14.1 `tests/test_screener_quality.py`

추가 테스트:

1. `test_kis_us_trade_vol_normalizes_output2`
   - `_kis_us_ranking_get` mock payload의 `output2`를 normalized candidate로 변환한다.
   - `symb`, `name`, `last`, `rate`, `tvol`, `tamt`, `rank` 필드 fixture 사용.
   - 결과의 `ticker`, `price`, `change_rate`, `volume`, `trade_value`, `category`, `provider` 검증.

2. `test_screen_market_us_prefers_kis_when_token_present`
   - `US_KIS_SCREEN_ENABLED=true`
   - `_kis_us_screen_candidates`가 충분한 후보 반환
   - `_yf_screen_candidates`는 호출되지 않아야 한다.
   - 결과 `provider == "kis"`, cache source `kis` 검증.

3. `test_screen_market_us_falls_back_to_yf_when_kis_errors`
   - `_kis_us_screen_candidates`가 `RuntimeError` 발생
   - `_yf_screen_candidates` 결과가 반환됨
   - 로그/quality source는 YF로 남는다.

4. `test_screen_market_us_skips_kis_without_token`
   - token 없이 호출
   - `_kis_us_screen_candidates` 미호출
   - 기존 YF path 유지.

5. `test_us_screener_reuses_kis_cache`
   - cache source `kis`, schema 4, quality OK, meaningful volume
   - fresh KIS/YF 호출 없이 cache 반환.

6. `test_us_post_filter_uses_trade_value_before_price_times_volume`
   - `price * volume`은 하한 미달이지만 `trade_value`는 하한 이상인 후보가 통과해야 한다.

7. `test_legacy_us_cache_schema3_is_not_reused_after_kis_schema_bump`
   - schema 3 cache 작성
   - `screen_market_us()` 호출 시 fresh fetch 수행.

### 14.2 `tests/test_sub_screener_integration.py`

추가/수정 테스트:

1. `test_us_force_refresh_bypasses_kis_cache`
   - 기존 `US_SCREEN_CACHE_TTL_SEC` 임시 0 처리 테스트를 KIS cache source에도 적용한다.

2. `test_screen_market_candidates_passes_us_token`
   - `TradingBot._screen_market_candidates("US", ...)`
   - `_token_for_market("US")` 호출 확인
   - `screen_market_us(..., token="token-US")` 호출 확인

### 14.3 선택 테스트

필요 시 `tests/test_kis_market_data_wrappers.py`에 `_kis_us_ranking_get()`의 `_require_kis_success` 처리 테스트를 추가한다.

테스트 명령:

```bash
python -m pytest tests/test_screener_quality.py tests/test_sub_screener_integration.py tests/test_kis_market_data_wrappers.py -q
python -m py_compile kis_api.py trading_bot.py
```

## 15. 운영 확인 절차

코드 merge 전:

1. `git diff --stat`로 범위 확인
2. 위 단위 테스트 실행
3. `python -m py_compile kis_api.py trading_bot.py`
4. paper 환경에서 US screener smoke 실행
5. `logs/screener/` 또는 system log에서 아래 문구 확인

성공 로그 예:

```text
[KIS US 스크리너] mode=NEUTRAL exchanges=NAS,NYS,AMS ...
[KIS US 스크리너] 통과=30종목 actives=15 gainers=10 losers=5 ...
```

fallback 로그 예:

```text
[KIS US 스크리너] 실패: ... -> Yahoo/FMP fallback
```

주의:

- live 주문 실행으로 검증하지 않는다.
- `.env.live`와 `config/v2_start_config.json`의 PathB 운영 파라미터는 변경하지 않는다.
- 실제 API field 이름이 샘플과 다르면 정규화 helper만 수정하고, downstream 후보 계약은 유지한다.

## 16. 완료 기준

완료로 인정하는 조건:

- US 스크리너가 token이 있을 때 KIS ranking을 1차로 호출한다.
- KIS 실패 시 기존 Yahoo/FMP/fallback이 정상 동작한다.
- KIS 후보가 기존 후보 계약 `{ticker, name, price, change_rate, volume, vol_ratio, category}`를 만족한다.
- cache source `kis`가 저장/재사용된다.
- `force_refresh=True`가 KIS cache에도 적용된다.
- 기존 YF/FMP 관련 테스트가 깨지지 않는다.
- 주문/risk/PathB/brain/state 운영 파일 변경이 없다.

## 17. 롤백

문제 발생 시 코드 배포를 되돌리지 않고도 아래 설정으로 KIS branch를 비활성화할 수 있어야 한다.

```text
US_KIS_SCREEN_ENABLED=false
```

이 경우 `screen_market_us()`는 기존 `Yahoo Finance -> FMP -> fallback` 흐름으로 동작해야 한다.
