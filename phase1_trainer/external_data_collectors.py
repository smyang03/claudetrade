from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from phase1_trainer.external_data_store import DEFAULT_DB_PATH, ExternalDataStore


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / ".env.live"

DART_COMPANY_ENDPOINT = "https://opendart.fss.or.kr/api/company.json"
DART_LIST_ENDPOINT = "https://opendart.fss.or.kr/api/list.json"
DATA_GO_KR_ENDPOINTS = {
    "krx_listed": "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo",
    "stock_price": "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo",
    "etf_price": "https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETFPriceInfo",
}
FRED_OBSERVATIONS_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"

DEFAULT_DART_CORP_CODE = "00126380"
DEFAULT_DART_STOCK_CODE = "005930"
DEFAULT_PUBLIC_STOCK_CODE = "005930"
DEFAULT_PUBLIC_ETF_CODE = "091160"
DEFAULT_FRED_SERIES = ("CPIAUCSL", "UNRATE", "DGS10")


def load_external_env(env_path: str | Path | None = None, *, override: bool = False) -> Path | None:
    path = Path(env_path) if env_path else DEFAULT_ENV_PATH
    if path.exists():
        load_dotenv(path, override=override)
        return path
    load_dotenv(override=override)
    return None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _yyyymmdd(value: str | date | None) -> str:
    if value is None:
        return date.today().strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "")[:8]


def _default_start_end(target_date: str | date | None = None, lookback_days: int = 30) -> tuple[str, str]:
    end = datetime.strptime(_yyyymmdd(target_date), "%Y%m%d").date()
    start = end - timedelta(days=lookback_days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text or text == ".":
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _fields(rows: list[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for row in rows:
        found.update(str(key) for key in row.keys())
    return sorted(found)


def _check_columns(rows: list[dict[str, Any]], expected: list[str]) -> dict[str, Any]:
    received = _fields(rows)
    missing = [field for field in expected if field not in received]
    return {
        "expected_fields": expected,
        "received_fields": received,
        "missing_fields": missing,
        "ok": not missing and bool(rows),
    }


_SECRET_QUERY_RE = re.compile(
    r"(?i)((?:crtfc_key|serviceKey|api_key|apikey)=)([^&\s)'\"]+)"
)


def _redact_error(text: str) -> str:
    return _SECRET_QUERY_RE.sub(r"\1***", text)


def _truncate_error(exc: Exception | str) -> str:
    return _redact_error(str(exc))[:500]


def _risk_tags_for_report(report_name: str) -> tuple[str, list[str]]:
    text = report_name or ""
    high_rules = {
        "capital_increase": ("\uc720\uc0c1\uc99d\uc790",),
        "capital_reduction": ("\uac10\uc790",),
        "trading_halt": ("\ub9e4\ub9e4\uac70\ub798\uc815\uc9c0", "\uac70\ub798\uc815\uc9c0"),
        "delisting": ("\uc0c1\uc7a5\ud3d0\uc9c0",),
        "audit_opinion": ("\uac10\uc0ac\uc758\uacac", "\ud55c\uc815", "\uac70\uc808"),
        "bankruptcy": ("\ud30c\uc0b0", "\ud68c\uc0dd\uc808\ucc28"),
    }
    medium_rules = {
        "convertible_bond": ("\uc804\ud658\uc0ac\ucc44",),
        "bond_with_warrant": ("\uc2e0\uc8fc\uc778\uc218\uad8c\ubd80\uc0ac\ucc44",),
        "merger": ("\ud569\ubcd1",),
        "split": ("\ubd84\ud560",),
        "major_shareholder_change": ("\ucd5c\ub300\uc8fc\uc8fc\ubcc0\uacbd", "\ucd5c\ub300\uc8fc\uc8fc \ubcc0\uacbd"),
        "asset_transfer": ("\uc601\uc5c5\uc591\uc218", "\uc601\uc5c5\uc591\ub3c4", "\uc790\uc0b0\uc591\uc218", "\uc790\uc0b0\uc591\ub3c4"),
    }
    positive_rules = {
        "buyback": ("\uc790\uae30\uc8fc\uc2dd\ucde8\ub4dd", "\uc790\uae30\uc8fc\uc2dd \ucde8\ub4dd"),
        "dividend": ("\ubc30\ub2f9", "\ud604\uae08\u318d\ud604\ubb3c\ubc30\ub2f9"),
        "contract_win": ("\ub2e8\uc77c\ud310\ub9e4", "\uacf5\uae09\uacc4\uc57d"),
    }
    tags: list[str] = []
    for tag, needles in high_rules.items():
        if any(needle in text for needle in needles):
            tags.append(tag)
    if tags:
        return "high", tags
    for tag, needles in medium_rules.items():
        if any(needle in text for needle in needles):
            tags.append(tag)
    if tags:
        return "medium", tags
    for tag, needles in positive_rules.items():
        if any(needle in text for needle in needles):
            tags.append(tag)
    if tags:
        return "positive", tags
    return "low", []


def _data_go_kr_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("response", {}) if isinstance(payload, dict) else {}
    body = response.get("body", {}) if isinstance(response, dict) else {}
    items = body.get("items", {}) if isinstance(body, dict) else {}
    item = items.get("item", []) if isinstance(items, dict) else items
    if isinstance(item, dict):
        return [item]
    if isinstance(item, list):
        return [row for row in item if isinstance(row, dict)]
    return []


def _data_go_kr_status(payload: dict[str, Any]) -> tuple[str, str]:
    header = payload.get("response", {}).get("header", {}) if isinstance(payload, dict) else {}
    return str(header.get("resultCode", "")), str(header.get("resultMsg", ""))


def _get_session(session: requests.Session | None = None) -> requests.Session:
    return session or requests.Session()


def fetch_dart_disclosures(
    *,
    corp_code: str = DEFAULT_DART_CORP_CODE,
    stock_code: str = DEFAULT_DART_STOCK_CODE,
    target_date: str | date | None = None,
    lookback_days: int = 30,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = os.getenv("DART_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DART_API_KEY is missing")
    start, end = _default_start_end(target_date, lookback_days)
    http = _get_session(session)
    resp = http.get(
        DART_LIST_ENDPOINT,
        params={
            "crtfc_key": key,
            "corp_code": corp_code,
            "bgn_de": start,
            "end_de": end,
            "page_no": "1",
            "page_count": "100",
        },
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") not in ("000", "013"):
        raise RuntimeError(f"DART status={payload.get('status')} message={payload.get('message', '')}")
    fetched_at = _now_iso()
    rows = []
    for item in payload.get("list", []) or []:
        report_name = str(item.get("report_nm", ""))
        risk_level, risk_tags = _risk_tags_for_report(report_name)
        rcept_no = str(item.get("rcept_no", ""))
        rows.append(
            {
                "rcept_no": rcept_no,
                "stock_code": stock_code,
                "corp_code": corp_code,
                "corp_name": item.get("corp_name", ""),
                "report_name": report_name,
                "rcept_dt": item.get("rcept_dt", ""),
                "risk_level": risk_level,
                "risk_tags": risk_tags,
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else "",
                "fetched_at": fetched_at,
                "raw": item,
            }
        )
    meta = {
        "http_status": resp.status_code,
        "target": f"{stock_code}:{corp_code}:{start}-{end}",
        "dart_status": payload.get("status", ""),
        "dart_message": payload.get("message", ""),
        "columns": _check_columns(
            payload.get("list", []) or [],
            ["corp_name", "rcept_no", "rcept_dt", "report_nm"],
        ),
    }
    return rows, meta


def _fetch_data_go_kr(
    endpoint_key: str,
    *,
    params: dict[str, Any] | None = None,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = os.getenv("DATA_GO_KR_KEY", "").strip()
    if not key:
        raise RuntimeError("DATA_GO_KR_KEY is missing")
    endpoint = DATA_GO_KR_ENDPOINTS[endpoint_key]
    query = {
        "serviceKey": key,
        "pageNo": 1,
        "numOfRows": 10,
        "resultType": "json",
    }
    query.update(params or {})
    http = _get_session(session)
    resp = http.get(endpoint, params=query, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    result_code, result_msg = _data_go_kr_status(payload)
    if result_code and result_code != "00":
        raise RuntimeError(f"DATA_GO_KR resultCode={result_code} resultMsg={result_msg}")
    rows = _data_go_kr_items(payload)
    meta = {
        "endpoint": endpoint,
        "http_status": resp.status_code,
        "result_code": result_code,
        "result_msg": result_msg,
        "fields": _fields(rows),
    }
    return rows, meta


def _public_base(row: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    return {
        "base_date": str(row.get("basDt", "")),
        "stock_code": str(row.get("srtnCd", "")),
        "isin_code": str(row.get("isinCd", "")),
        "market": str(row.get("mrktCtg", "")),
        "item_name": str(row.get("itmsNm", "")),
        "fetched_at": fetched_at,
        "raw": row,
    }


def normalize_public_krx_listed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fetched_at = _now_iso()
    normalized = []
    for row in rows:
        item = _public_base(row, fetched_at)
        item.update(
            {
                "corp_name": str(row.get("corpNm", "")),
                "corp_reg_no": str(row.get("crno", "")),
            }
        )
        if item["base_date"] and item["stock_code"]:
            normalized.append(item)
    return normalized


def normalize_public_quotes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fetched_at = _now_iso()
    normalized = []
    for row in rows:
        item = _public_base(row, fetched_at)
        item.update(
            {
                "open": _to_float(row.get("mkp")),
                "high": _to_float(row.get("hipr")),
                "low": _to_float(row.get("lopr")),
                "close": _to_float(row.get("clpr")),
                "change_pct": _to_float(row.get("fltRt")),
                "volume": _to_float(row.get("trqu")),
                "amount": _to_float(row.get("trPrc")),
            }
        )
        if item["base_date"] and item["stock_code"]:
            normalized.append(item)
    return normalized


def normalize_public_products(rows: list[dict[str, Any]], product_type: str) -> list[dict[str, Any]]:
    normalized = normalize_public_quotes(rows)
    for row in normalized:
        row["product_type"] = product_type
    return normalized


def fetch_public_krx_listed(
    *,
    stock_code: str = DEFAULT_PUBLIC_STOCK_CODE,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, meta = _fetch_data_go_kr(
        "krx_listed",
        params={"likeSrtnCd": stock_code, "numOfRows": 10},
        session=session,
    )
    meta["columns"] = _check_columns(rows, ["basDt", "srtnCd", "isinCd", "mrktCtg", "itmsNm"])
    return normalize_public_krx_listed(rows), meta


def fetch_public_stock_quotes(
    *,
    stock_code: str = DEFAULT_PUBLIC_STOCK_CODE,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, meta = _fetch_data_go_kr(
        "stock_price",
        params={"likeSrtnCd": stock_code, "numOfRows": 10},
        session=session,
    )
    meta["columns"] = _check_columns(rows, ["basDt", "srtnCd", "itmsNm", "clpr", "trqu", "fltRt"])
    return normalize_public_quotes(rows), meta


def fetch_public_etf_quotes(
    *,
    stock_code: str = DEFAULT_PUBLIC_ETF_CODE,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, meta = _fetch_data_go_kr(
        "etf_price",
        params={"likeSrtnCd": stock_code, "numOfRows": 10},
        session=session,
    )
    meta["columns"] = _check_columns(rows, ["basDt", "srtnCd", "itmsNm", "clpr", "trqu", "fltRt"])
    return normalize_public_products(rows, "ETF"), meta


def fetch_fred_observations(
    series_id: str,
    *,
    limit: int = 5,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = os.getenv("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FRED_API_KEY is missing")
    http = _get_session(session)
    resp = http.get(
        FRED_OBSERVATIONS_ENDPOINT,
        params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": int(limit),
        },
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    observations = payload.get("observations", []) or []
    fetched_at = _now_iso()
    rows = []
    for obs in observations:
        value_text = str(obs.get("value", ""))
        rows.append(
            {
                "series_id": series_id,
                "observation_date": str(obs.get("date", "")),
                "value": _to_float(value_text),
                "value_text": value_text,
                "realtime_start": str(obs.get("realtime_start", "")),
                "realtime_end": str(obs.get("realtime_end", "")),
                "fetched_at": fetched_at,
                "raw": obs,
            }
        )
    meta = {
        "http_status": resp.status_code,
        "target": series_id,
        "columns": _check_columns(observations, ["date", "value", "realtime_start", "realtime_end"]),
    }
    return rows, meta


def _source_result(
    *,
    source: str,
    endpoint: str,
    target: str,
    status: str,
    rows: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    rows = rows or []
    meta = meta or {}
    columns = meta.get("columns") or {"received_fields": _fields(rows), "missing_fields": [], "ok": bool(rows)}
    return {
        "source": source,
        "endpoint": endpoint,
        "target": target,
        "status": status,
        "row_count": len(rows),
        "http_status": meta.get("http_status"),
        "columns_ok": bool(columns.get("ok")),
        "received_fields": columns.get("received_fields", []),
        "missing_fields": columns.get("missing_fields", []),
        "error": error,
    }


def collect_ready_sources_dry_run(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    env_path: str | Path | None = DEFAULT_ENV_PATH,
    target_date: str | date | None = None,
    session: requests.Session | None = None,
    write_db: bool = True,
) -> dict[str, Any]:
    load_external_env(env_path, override=True)
    store = ExternalDataStore(db_path)
    if write_db:
        store.init_schema()
    checks: list[dict[str, Any]] = []

    def record(result: dict[str, Any]) -> None:
        checks.append(result)
        if write_db:
            store.record_run(
                source=result["source"],
                endpoint=result["endpoint"],
                target=result["target"],
                status=result["status"],
                http_status=result.get("http_status"),
                row_count=result["row_count"],
                fields=result.get("received_fields", []),
                error=result.get("error", ""),
                fetched_at=_now_iso(),
            )

    try:
        rows, meta = fetch_dart_disclosures(target_date=target_date, session=session)
        if write_db:
            store.upsert_dart_disclosures(rows)
        record(
            _source_result(
                source="DART",
                endpoint="list",
                target=meta.get("target", DEFAULT_DART_STOCK_CODE),
                status="ok" if meta.get("columns", {}).get("ok") else "empty_or_schema_warning",
                rows=rows,
                meta=meta,
            )
        )
    except Exception as exc:
        record(
            _source_result(
                source="DART",
                endpoint="list",
                target=DEFAULT_DART_STOCK_CODE,
                status="failed",
                error=_truncate_error(exc),
            )
        )

    public_calls = [
        ("DATA_GO_KR", "krx_listed", fetch_public_krx_listed, store.upsert_public_krx_listed),
        ("DATA_GO_KR", "stock_price", fetch_public_stock_quotes, store.upsert_public_stock_quotes),
        ("DATA_GO_KR", "etf_price", fetch_public_etf_quotes, store.upsert_public_securities_products),
    ]
    for source, endpoint, fetcher, writer in public_calls:
        try:
            rows, meta = fetcher(session=session)
            if write_db:
                writer(rows)
            record(
                _source_result(
                    source=source,
                    endpoint=endpoint,
                    target=DEFAULT_PUBLIC_STOCK_CODE if endpoint != "etf_price" else DEFAULT_PUBLIC_ETF_CODE,
                    status="ok" if meta.get("columns", {}).get("ok") else "empty_or_schema_warning",
                    rows=rows,
                    meta=meta,
                )
            )
        except Exception as exc:
            record(
                _source_result(
                    source=source,
                    endpoint=endpoint,
                    target=DEFAULT_PUBLIC_STOCK_CODE if endpoint != "etf_price" else DEFAULT_PUBLIC_ETF_CODE,
                    status="failed",
                    error=_truncate_error(exc),
                )
            )

    for series_id in DEFAULT_FRED_SERIES:
        try:
            rows, meta = fetch_fred_observations(series_id, session=session)
            if write_db:
                store.upsert_fred_observations(rows)
            record(
                _source_result(
                    source="FRED",
                    endpoint="observations",
                    target=series_id,
                    status="ok" if meta.get("columns", {}).get("ok") else "empty_or_schema_warning",
                    rows=rows,
                    meta=meta,
                )
            )
        except Exception as exc:
            record(
                _source_result(
                    source="FRED",
                    endpoint="observations",
                    target=series_id,
                    status="failed",
                    error=_truncate_error(exc),
                )
            )

    return {
        "db_path": str(Path(db_path)),
        "env_path": str(Path(env_path)) if env_path else "",
        "write_db": bool(write_db),
        "checks": checks,
        "table_counts": store.table_counts() if write_db else {},
    }
