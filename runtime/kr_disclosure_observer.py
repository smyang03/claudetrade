from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Any

from runtime_paths import get_runtime_path


AUTHORITY = "SHADOW_ONLY_NO_ORDER_AUTHORITY"
RIGHTS_OFFERING_PATTERNS = (
    "유상증자결정",
    "유상증자 결정",
    "증권신고서(지분증권)",
)
SUPPLY_CONTRACT_PATTERNS = (
    "단일판매ㆍ공급계약체결",
    "단일판매·공급계약체결",
    "단일판매 공급계약체결",
)
_CACHE_LOCK = threading.Lock()
_MEM_CACHE: dict[str, Any] = {"path": "", "mtime": None, "payload": {}}


def _cache_path() -> Path:
    return get_runtime_path("data", "kr_disclosure_observer.json")


def classify_report_name(report_name: str) -> list[str]:
    name = str(report_name or "").strip()
    tags: list[str] = []
    if any(pattern in name for pattern in RIGHTS_OFFERING_PATTERNS):
        tags.append("KR_RIGHTS_OFFERING_OBSERVER")
    if any(pattern in name for pattern in SUPPLY_CONTRACT_PATTERNS):
        tags.append("KR_SUPPLY_CONTRACT_OBSERVER")
    return tags


def _dart_page(
    *,
    api_key: str,
    begin: str,
    end: str,
    disclosure_type: str,
    page: int,
    timeout_sec: float,
    attempts: int = 3,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "crtfc_key": api_key,
            "bgn_de": begin,
            "end_de": end,
            "pblntf_ty": disclosure_type,
            "page_count": 100,
            "page_no": page,
        }
    )
    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            request = urllib.request.Request(
                f"https://opendart.fss.or.kr/api/list.json?{query}",
                headers={"User-Agent": "claudetrade-disclosure-observer/1.0"},
            )
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                return json.loads(response.read())
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max(1, int(attempts)):
                time.sleep(min(2.0, 0.5 * (attempt + 1)))
    raise RuntimeError(str(last_error or "dart_request_failed"))


def refresh_kr_disclosure_observer(
    *,
    days_back: int = 7,
    max_pages_per_type: int = 8,
    timeout_sec: float = 15.0,
) -> dict[str, Any]:
    """Refresh a research-only DART disclosure cache.

    This function is intentionally not called by order or candidate-selection
    paths.  A scheduler/tool may refresh it before the session; readers only
    consume the local cache.
    """

    api_key = str(os.getenv("DART_API_KEY", "") or "").strip()
    if not api_key:
        return {"ok": False, "reason": "no_api_key", "authority": AUTHORITY}
    begin = (date.today() - timedelta(days=max(0, int(days_back)))).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    by_code: dict[str, list[dict[str, Any]]] = {}
    request_count = 0
    try:
        for disclosure_type in ("B", "I"):
            for page in range(1, max(1, int(max_pages_per_type)) + 1):
                payload = _dart_page(
                    api_key=api_key,
                    begin=begin,
                    end=end,
                    disclosure_type=disclosure_type,
                    page=page,
                    timeout_sec=timeout_sec,
                )
                request_count += 1
                items = payload.get("list") or []
                for item in items:
                    report_name = str(item.get("report_nm") or "")
                    tags = classify_report_name(report_name)
                    stock_code = str(item.get("stock_code") or "").strip()
                    if not tags or not stock_code:
                        continue
                    raw_date = str(item.get("rcept_dt") or "")
                    disclosure_date = (
                        f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                        if len(raw_date) == 8
                        else ""
                    )
                    by_code.setdefault(stock_code, []).append(
                        {
                            "date": disclosure_date,
                            "report_name": report_name[:160],
                            "receipt_no": str(item.get("rcept_no") or ""),
                            "disclosure_type": disclosure_type,
                            "is_correction": any(
                                marker in report_name
                                for marker in ("[기재정정]", "[첨부정정]", "[정정]")
                            ),
                            "tags": tags,
                        }
                    )
                if len(items) < 100:
                    break
    except Exception as exc:
        return {
            "ok": False,
            "reason": str(exc)[:200],
            "request_count": request_count,
            "authority": AUTHORITY,
        }

    for code, rows in by_code.items():
        deduped = {
            (row.get("receipt_no"), row.get("report_name")): row
            for row in rows
        }
        by_code[code] = sorted(
            deduped.values(),
            key=lambda row: (str(row.get("date") or ""), str(row.get("receipt_no") or "")),
            reverse=True,
        )
    output = {
        "schema_version": "kr_disclosure_observer_v1",
        "authority": AUTHORITY,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from": begin,
        "to": end,
        "request_count": request_count,
        "ticker_count": len(by_code),
        "by_code": by_code,
    }
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)
    return {
        "ok": True,
        "ticker_count": len(by_code),
        "request_count": request_count,
        "path": str(path),
        "authority": AUTHORITY,
    }


def load_observer_cache(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else _cache_path()
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return {}
    with _CACHE_LOCK:
        if _MEM_CACHE["path"] == str(target) and _MEM_CACHE["mtime"] == mtime:
            return dict(_MEM_CACHE["payload"])
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("authority") != AUTHORITY:
        return {}
    with _CACHE_LOCK:
        _MEM_CACHE.update({"path": str(target), "mtime": mtime, "payload": dict(payload)})
    return payload


def disclosure_observer_tags(
    ticker: str,
    *,
    session_date: str,
    cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return non-authoritative tags known by the candidate session date."""

    try:
        session = date.fromisoformat(str(session_date)[:10])
    except Exception:
        return []
    payload = cache if isinstance(cache, dict) else load_observer_cache()
    rows = (payload.get("by_code") or {}).get(str(ticker or "").strip()) or []
    tags: list[dict[str, Any]] = []
    for row in rows:
        try:
            disclosed = date.fromisoformat(str(row.get("date") or "")[:10])
        except Exception:
            continue
        age_days = (session - disclosed).days
        if age_days < 0:
            continue
        row_tags = set(row.get("tags") or [])
        if "KR_RIGHTS_OFFERING_OBSERVER" in row_tags and age_days <= 5:
            tags.append(
                {
                    "tag": "KR_RIGHTS_OFFERING_D0_D5",
                    "date": disclosed.isoformat(),
                    "age_days": age_days,
                    "report_name": row.get("report_name", ""),
                    "is_correction": bool(row.get("is_correction")),
                    "authority": AUTHORITY,
                }
            )
        if "KR_SUPPLY_CONTRACT_OBSERVER" in row_tags and age_days == 1:
            tags.append(
                {
                    "tag": "KR_SUPPLY_CONTRACT_NEXT_SESSION",
                    "date": disclosed.isoformat(),
                    "age_days": age_days,
                    "report_name": row.get("report_name", ""),
                    "is_correction": bool(row.get("is_correction")),
                    "authority": AUTHORITY,
                }
            )
    return tags
