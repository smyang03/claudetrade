"""
kis_api.py
KIS API (KR) + Finnhub/FMP/yfinance (US quote/candles/screener).
"""

import os
import json
import time
import logging
import hashlib
import math
import requests
import threading
import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv
from runtime_paths import get_runtime_path

load_dotenv()
log = logging.getLogger("trading")

APP_KEY = os.getenv("KIS_APP_KEY", "")
APP_SECRET = os.getenv("KIS_APP_SECRET", "")
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
IS_PAPER = os.getenv("KIS_IS_PAPER", "true").lower() == "true"

# US 전용 자격증명 — 비어있으면 KR 설정 그대로 fallback
ACCOUNT_NO_US   = os.getenv("KIS_ACCOUNT_NO_US", "").strip() or ACCOUNT_NO
APP_KEY_US      = os.getenv("KIS_APP_KEY_US",    "").strip() or APP_KEY
APP_SECRET_US   = os.getenv("KIS_APP_SECRET_US", "").strip() or APP_SECRET
_IS_PAPER_US_RAW = os.getenv("KIS_IS_PAPER_US", "").strip()
IS_PAPER_US     = (_IS_PAPER_US_RAW.lower() == "true") if _IS_PAPER_US_RAW else IS_PAPER

AV_KEY       = os.getenv("ALPHA_VANTAGE_KEY", "")
AV_KEY_2     = os.getenv("ALPHA_VANTAGE_KEY_2", "")
FINNHUB_KEY  = os.getenv("FINNHUB_API_KEY", "")
FMP_KEY      = os.getenv("FMP_API_KEY", "").strip()

# 계좌번호 포맷 검증: "XXXXXXXXXX-XX" 형태여야 함
if ACCOUNT_NO and "-" not in ACCOUNT_NO:
    raise ValueError(
        f"KIS_ACCOUNT_NO 포맷 오류: '{ACCOUNT_NO}' — 'XXXXXXXXXX-XX' 형식으로 입력하세요."
    )

def _default_base_url(is_paper: bool) -> str:
    return (
        "https://openapivts.koreainvestment.com:29443"
        if is_paper
        else "https://openapi.koreainvestment.com:9443"
    )


def _default_ws_url(is_paper: bool) -> str:
    return (
        "ws://ops.koreainvestment.com:31000"
        if is_paper
        else "ws://ops.koreainvestment.com:21000"
    )


BASE_URL = os.getenv("KIS_BASE_URL", _default_base_url(IS_PAPER))
BASE_URL_US = os.getenv("KIS_BASE_URL_US", "").strip() or (
    BASE_URL if IS_PAPER_US == IS_PAPER else _default_base_url(IS_PAPER_US)
)
WS_URL = _default_ws_url(IS_PAPER)
WS_URL_US = os.getenv("KIS_WS_URL_US", "").strip() or (
    WS_URL if IS_PAPER_US == IS_PAPER else _default_ws_url(IS_PAPER_US)
)
TOKEN_FILE = get_runtime_path("state", f"{'paper' if IS_PAPER else 'live'}_kis_token.json")
KIS_HTTP_TIMEOUT = float(os.getenv("KIS_HTTP_TIMEOUT", "10"))
KIS_TOKEN_RETRY = int(os.getenv("KIS_TOKEN_RETRY", "3"))
KIS_QUERY_RETRY = int(os.getenv("KIS_QUERY_RETRY", "3"))
KIS_CACHE_TTL_SEC = int(os.getenv("KIS_CACHE_TTL_SEC", "120"))
KIS_RATE_RPS = float(os.getenv("KIS_RATE_RPS", "12"))

_BALANCE_CACHE = {}
_PRICE_CACHE = {}
_INDEX_CACHE = {}
_OHLCV_CACHE = {}
_INTRADAY_CACHE = {}
_CACHE_LOG_TS = {}
_KIS_HTTP_LOCK = threading.Lock()
_KIS_LAST_CALL_TS = 0.0
_TOKEN_ALIAS_LOCK = threading.Lock()
_TOKEN_ALIAS: dict[str, str] = {}
_TOKEN_MARKET: dict[str, str] = {}


@dataclass(frozen=True)
class KISMarketProfile:
    market: str
    account_no: str
    app_key: str
    app_secret: str
    is_paper: bool
    base_url: str
    ws_url: str
    token_file: str
    credential_mode: str
    shared_with_kr: bool


def _normalize_market(market: str = "KR") -> str:
    market_key = str(market or "KR").strip().upper()
    return "US" if market_key == "US" else "KR"


def _fingerprint(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:12]


def _market_profile_values(market: str) -> tuple[str, str, str, bool, str, str]:
    market_key = _normalize_market(market)
    if market_key == "US":
        return ACCOUNT_NO_US, APP_KEY_US, APP_SECRET_US, bool(IS_PAPER_US), BASE_URL_US, WS_URL_US
    return ACCOUNT_NO, APP_KEY, APP_SECRET, bool(IS_PAPER), BASE_URL, WS_URL


def _profile_shares_kr_token(market: str) -> bool:
    market_key = _normalize_market(market)
    if market_key == "KR":
        return True
    _, kr_key, kr_secret, kr_paper, kr_base, _ = _market_profile_values("KR")
    _, us_key, us_secret, us_paper, us_base, _ = _market_profile_values("US")
    return (
        kr_key == us_key
        and kr_secret == us_secret
        and kr_paper == us_paper
        and kr_base == us_base
    )


def _token_file_for_market(market: str):
    market_key = _normalize_market(market)
    if _profile_shares_kr_token(market_key):
        return TOKEN_FILE
    _, _, _, is_paper, _, _ = _market_profile_values(market_key)
    mode = "paper" if is_paper else "live"
    return get_runtime_path("state", f"{mode}_kis_token_{market_key.lower()}.json")


def get_kis_market_profile(market: str = "KR") -> KISMarketProfile:
    market_key = _normalize_market(market)
    account_no, app_key, app_secret, is_paper, base_url, ws_url = _market_profile_values(market_key)
    shared = _profile_shares_kr_token(market_key)
    if market_key == "KR":
        mode = "primary"
    elif shared:
        mode = "fallback_shared_kr"
    else:
        mode = "separate_us"
    return KISMarketProfile(
        market=market_key,
        account_no=account_no,
        app_key=app_key,
        app_secret=app_secret,
        is_paper=bool(is_paper),
        base_url=base_url,
        ws_url=ws_url,
        token_file=str(_token_file_for_market(market_key)),
        credential_mode=mode,
        shared_with_kr=shared,
    )


def get_kis_profile_summary() -> dict:
    profiles = {market: get_kis_market_profile(market) for market in ("KR", "US")}
    return {
        market: {
            "market": profile.market,
            "account_present": bool(profile.account_no),
            "app_key_fingerprint": _fingerprint(profile.app_key) if profile.app_key else "",
            "is_paper": profile.is_paper,
            "base_url": profile.base_url,
            "token_file": profile.token_file,
            "credential_mode": profile.credential_mode,
            "shared_with_kr": profile.shared_with_kr,
        }
        for market, profile in profiles.items()
    }


def _base_url(market: str = "KR") -> str:
    return get_kis_market_profile(market).base_url


def _ws_url(market: str = "KR") -> str:
    return get_kis_market_profile(market).ws_url


def _remember_token_market(token: str, market: str) -> None:
    token_s = str(token or "").strip()
    if not token_s:
        return
    with _TOKEN_ALIAS_LOCK:
        _TOKEN_MARKET[token_s] = _normalize_market(market)

# ── US 거래소 코드 영속 캐시 ────────────────────────────────────────────────────
from pathlib import Path as _Path
_EXCHANGE_CACHE_FILE = _Path(__file__).resolve().parent / "data" / "exchange_cache.json"

_FINNHUB_EXCHANGE_MAP = {
    "NEW YORK STOCK EXCHANGE": "NYSE",
    "NYSE":                    "NYSE",
    "NASDAQ NMS":              "NASD",
    "NASDAQ":                  "NASD",
    "NASDAQ CAPITAL MARKET":   "NASD",
    "NASDAQ GLOBAL MARKET":    "NASD",
    "NASDAQ GLOBAL SELECT":    "NASD",
    "NYSE AMERICAN":           "AMEX",
    "AMERICAN STOCK EXCHANGE": "AMEX",
    "NYSE MKT":                "AMEX",
    "NYSE ARCA":               "AMEX",
    "AMEX":                    "AMEX",
}

_YAHOO_US_EXCHANGE_MAP = {
    "NMS": "NASD",
    "NGM": "NASD",
    "NCM": "NASD",
    "NAS": "NASD",
    "NYQ": "NYSE",
    "NYS": "NYSE",
    "ASE": "AMEX",
    "AMX": "AMEX",
    "PCX": "AMEX",
}


def _load_exchange_cache() -> None:
    """data/exchange_cache.json → _US_EXCHANGE_CACHE 로드 (모듈 init 시 1회 호출)"""
    try:
        with open(_EXCHANGE_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _US_EXCHANGE_CACHE.update(data)
        log.debug(f"[exchange_cache] {len(data)}종목 로드")
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"[exchange_cache] 로드 실패: {e}")


def _save_exchange_cache() -> None:
    """_US_EXCHANGE_CACHE → data/exchange_cache.json 저장"""
    try:
        _EXCHANGE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_EXCHANGE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_US_EXCHANGE_CACHE, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning(f"[exchange_cache] 저장 실패: {e}")


def _map_us_exchange_name(exchange_name: str) -> Optional[str]:
    exch_name = str(exchange_name or "").upper()
    for key, code in sorted(_FINNHUB_EXCHANGE_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if key in exch_name:
            return code
    return None


def _map_yahoo_us_exchange(exchange_code: str, exchange_name: str = "") -> Optional[str]:
    code = str(exchange_code or "").strip().upper()
    if code in _YAHOO_US_EXCHANGE_MAP:
        return _YAHOO_US_EXCHANGE_MAP[code]
    return _map_us_exchange_name(exchange_name) or _map_us_exchange_name(code)


def _resolve_us_exchange_finnhub(ticker: str) -> str:
    """Finnhub profile2 → KIS 거래소 코드 (NASD/NYSE/AMEX) 반환. 실패 시 ValueError."""
    if not FINNHUB_KEY:
        raise RuntimeError("FINNHUB_API_KEY 없음")
    resp = requests.get(
        "https://finnhub.io/api/v1/stock/profile2",
        params={"symbol": ticker, "token": FINNHUB_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    exch_name = (resp.json().get("exchange") or "").upper()
    code = _map_us_exchange_name(exch_name)
    if code:
        return code
    raise ValueError(f"Finnhub 거래소 매핑 불가: {ticker} exchange='{exch_name}'")


def _resolve_us_exchange_yahoo(ticker: str) -> str:
    """Yahoo search metadata → KIS 거래소 코드. Finnhub 심볼 충돌/지연 보조용."""
    normalized = str(ticker or "").strip().upper()
    if not normalized:
        raise ValueError("ticker empty")
    resp = requests.get(
        "https://query1.finance.yahoo.com/v1/finance/search",
        params={"q": normalized, "quotesCount": 10, "newsCount": 0},
        headers={"User-Agent": "Mozilla/5.0 (compatible; trading-bot/1.0)"},
        timeout=10,
    )
    resp.raise_for_status()
    quotes = resp.json().get("quotes", [])
    for q in quotes:
        if str(q.get("symbol", "")).strip().upper() != normalized:
            continue
        code = _map_yahoo_us_exchange(q.get("exchange"), q.get("exchDisp"))
        if code:
            return code
        raise ValueError(
            f"Yahoo 거래소 매핑 불가: {normalized} "
            f"exchange='{q.get('exchange', '')}' exchDisp='{q.get('exchDisp', '')}'"
        )
    raise ValueError(f"Yahoo 심볼 검색 실패: {normalized}")


def _cache_get(cache: dict, key):
    item = cache.get(key)
    if not item:
        return None
    if (datetime.now() - item["ts"]).total_seconds() > KIS_CACHE_TTL_SEC:
        return None
    return item["value"]


def _cache_set(cache: dict, key, value):
    cache[key] = {"value": value, "ts": datetime.now()}
    return value


def _cache_invalidate(cache: dict, key):
    cache.pop(key, None)


def _log_cache_use_throttled(key: str, message: str, interval_sec: int = 300):
    now = time.monotonic()
    last_ts = _CACHE_LOG_TS.get(key, 0.0)
    if now - last_ts >= interval_sec:
        log.debug(message)
        _CACHE_LOG_TS[key] = now


def _retry_kis(label: str, fn, retries: int = None, delay_sec: float = 0.6):
    attempts = max(1, KIS_QUERY_RETRY if retries is None else retries)
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt < attempts:
                time.sleep(delay_sec * attempt)
    raise last_error


def _rate_limit_wait():
    global _KIS_LAST_CALL_TS
    min_gap = 1.0 / max(KIS_RATE_RPS, 1.0)
    with _KIS_HTTP_LOCK:
        now = time.monotonic()
        wait_sec = min_gap - (now - _KIS_LAST_CALL_TS)
        if wait_sec > 0:
            time.sleep(wait_sec)
        _KIS_LAST_CALL_TS = time.monotonic()


def _bearer_from_headers(headers: Optional[dict]) -> str:
    if not isinstance(headers, dict):
        return ""
    raw = str(headers.get("authorization") or headers.get("Authorization") or "")
    prefix = "Bearer "
    return raw[len(prefix):].strip() if raw.startswith(prefix) else ""


def _set_bearer(headers: dict, token: str) -> None:
    if "Authorization" in headers and "authorization" not in headers:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["authorization"] = f"Bearer {token}"


def _market_from_headers(headers: Optional[dict], token: str = "") -> str:
    if isinstance(headers, dict):
        raw = str(headers.get("x-kis-market") or headers.get("X-KIS-Market") or "").strip().upper()
        if raw in {"KR", "US"}:
            return raw
        app_key = str(headers.get("appkey") or "").strip()
        us_profile = get_kis_market_profile("US")
        kr_profile = get_kis_market_profile("KR")
        if app_key and app_key == us_profile.app_key and not us_profile.shared_with_kr:
            return "US"
        if app_key and app_key == kr_profile.app_key:
            return "KR"
    token_s = str(token or "").strip()
    if token_s:
        with _TOKEN_ALIAS_LOCK:
            market = _TOKEN_MARKET.get(token_s)
        if market in {"KR", "US"}:
            return market
    return "KR"


def _response_is_token_expired(resp) -> bool:
    if getattr(resp, "status_code", 0) != 500:
        return False
    try:
        body = resp.json()
    except Exception:
        return False
    return body.get("msg_cd") == "EGW00123"


def _kis_request(method: str, url: str, **kwargs):
    _rate_limit_wait()
    timeout = kwargs.pop("timeout", KIS_HTTP_TIMEOUT)
    headers = kwargs.get("headers")
    if isinstance(headers, dict):
        headers = dict(headers)
        old_token = _bearer_from_headers(headers)
        request_market = _market_from_headers(headers, old_token)
        if old_token:
            with _TOKEN_ALIAS_LOCK:
                aliased = _TOKEN_ALIAS.get(f"{request_market}:{old_token}") or _TOKEN_ALIAS.get(old_token)
            if aliased:
                _set_bearer(headers, aliased)
                kwargs["headers"] = headers
    else:
        old_token = ""
        request_market = "KR"
    requester = requests.get if method == "GET" else requests.post
    resp = requester(url, timeout=timeout, **kwargs)
    if old_token and "/oauth2/tokenP" not in url and _response_is_token_expired(resp):
        fresh_token = get_access_token(force_refresh=True, market=request_market)
        with _TOKEN_ALIAS_LOCK:
            _TOKEN_ALIAS[f"{request_market}:{old_token}"] = fresh_token
            _TOKEN_ALIAS[old_token] = fresh_token
        retry_headers = dict(kwargs.get("headers") or {})
        _set_bearer(retry_headers, fresh_token)
        kwargs["headers"] = retry_headers
        _rate_limit_wait()
        resp = requester(url, timeout=timeout, **kwargs)
    return resp


def _kis_get(url: str, **kwargs):
    return _kis_request("GET", url, **kwargs)


def _kis_post(url: str, **kwargs):
    return _kis_request("POST", url, **kwargs)


def _token_cache_context(market: str = "KR") -> dict:
    profile = get_kis_market_profile("KR" if _profile_shares_kr_token(market) else market)
    return {
        "market": profile.market,
        "base_url": profile.base_url,
        "app_key_fingerprint": _fingerprint(profile.app_key),
        "is_paper": bool(profile.is_paper),
        "credential_mode": profile.credential_mode,
    }


class KISTokenExpiredError(RuntimeError):
    """KIS API가 EGW00123(토큰 만료)을 반환한 경우."""


class KISOrderHTTPError(RuntimeError):
    """KIS 주문 HTTP 오류. 주문은 중복 위험이 있어 일반 조회처럼 blind retry하지 않는다."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        response_text: str = "",
        order_body: Optional[dict] = None,
    ):
        self.status_code = int(status_code or 0)
        self.response_text = str(response_text or "")
        self.order_body = order_body or {}
        super().__init__(message)


def load_token(market: str = "KR"):
    profile = get_kis_market_profile(market)
    token_file = _token_file_for_market(profile.market)
    if not token_file.exists():
        return None
    with open(token_file, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("context") != _token_cache_context(profile.market):
        return None
    # 발급일이 오늘이 아니면 재발급 (KIS 토큰은 당일 자정 이후 무효화됨)
    issued_at = data.get("issued_at", "")
    if issued_at[:10] != datetime.today().strftime("%Y-%m-%d"):
        return None
    if datetime.now() < datetime.fromisoformat(data["expires_at"]) - timedelta(minutes=10):
        _remember_token_market(data.get("access_token", ""), profile.market)
        return data
    return None


def save_token(token, expires_in, market: str = "KR"):
    profile = get_kis_market_profile(market)
    token_file = _token_file_for_market(profile.market)
    expires_at = (datetime.now() + timedelta(seconds=expires_in)).isoformat()
    issued_at = datetime.now().isoformat()
    with open(token_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "access_token": token,
                "expires_at": expires_at,
                "issued_at": issued_at,
                "context": _token_cache_context(profile.market),
            },
            f,
        )
    _remember_token_market(token, profile.market)


def get_access_token(force_refresh: bool = False, market: str = "KR"):
    profile = get_kis_market_profile(market)
    token_file = _token_file_for_market(profile.market)
    cached = None if force_refresh else load_token(profile.market)
    if cached:
        return cached["access_token"]
    if not profile.app_key or not profile.app_secret:
        raise RuntimeError(f"KIS {profile.market} APP_KEY/APP_SECRET 값이 비어 있습니다. .env를 확인하세요.")

    last_error = None
    last_status_detail = ""
    for attempt in range(1, max(1, KIS_TOKEN_RETRY) + 1):
        try:
            resp = _kis_post(
                f"{profile.base_url}/oauth2/tokenP",
                json={"grant_type": "client_credentials", "appkey": profile.app_key, "appsecret": profile.app_secret},
            )
            resp.raise_for_status()
            data = resp.json()
            save_token(data["access_token"], int(data.get("expires_in", 86400)), market=profile.market)
            return data["access_token"]
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < KIS_TOKEN_RETRY:
                time.sleep(1.5 * attempt)
        except requests.exceptions.RequestException as e:
            last_error = e
            response = getattr(e, "response", None)
            if response is not None:
                body = str(getattr(response, "text", "") or "").replace("\n", " ")[:300]
                last_status_detail = f" HTTP status={response.status_code}, body={body}"
            break

    mode_hint = ""
    if last_status_detail.startswith(" HTTP status=403"):
        mode_hint = (
            " 403은 네트워크 timeout보다 KIS 실거래/모의투자 앱키 불일치, 앱 승인 상태, "
            "계좌 권한, 또는 BASE_URL 설정 오류 가능성이 큽니다."
        )
    raise RuntimeError(
        f"KIS {profile.market} 토큰 발급 연결 실패. "
        f"URL={profile.base_url}/oauth2/tokenP, timeout={KIS_HTTP_TIMEOUT}s, retries={KIS_TOKEN_RETRY}.{last_status_detail}{mode_hint} "
        "망/방화벽에서 KIS 도메인(210.107.75.32) 29443/9443 포트 차단 여부를 확인하고, "
        "필요 시 KIS_BASE_URL/KIS_HTTP_TIMEOUT 값을 조정하세요."
    ) from last_error


def _headers(token, tr_id="", market: str = "KR"):
    profile = get_kis_market_profile(market)
    token_s = str(token or "").strip()
    if not token_s:
        token_s = get_access_token(market=profile.market)
    _remember_token_market(token_s, profile.market)
    h = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token_s}",
        "appkey": profile.app_key,
        "appsecret": profile.app_secret,
        "custtype": "P",
    }
    if tr_id:
        h["tr_id"] = tr_id
    return h


def get_hashkey(body, token, market: str = "KR"):
    profile = get_kis_market_profile(market)
    resp = _kis_post(f"{profile.base_url}/uapi/hashkey", headers=_headers(token, market=profile.market), json=body, timeout=10)
    resp.raise_for_status()
    return resp.json()["HASH"]


def _get_price_kr(ticker, token):
    cache_key = ("KR", ticker)

    def _fetch():
        url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
        tr_id = "FHKST01010100"  # 시세 조회는 모의/실거래 공통 TR
        resp = _kis_get(
            url,
            headers=_headers(token, tr_id),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            timeout=10,
        )
        resp.raise_for_status()
        o = resp.json().get("output", {})
        return {
            "ticker": ticker,
            "name": o.get("hts_kor_isnm", ""),
            "price": int(o.get("stck_prpr", 0)),
            "change": int(o.get("prdy_vrss", 0)),
            "change_rate": float(o.get("prdy_ctrt", 0)),
            "volume": int(o.get("acml_vol", 0)),
            "open": int(o.get("stck_oprc", 0)),
            "high": int(o.get("stck_hgpr", 0)),
            "low": int(o.get("stck_lwpr", 0)),
        }

    try:
        return _cache_set(_PRICE_CACHE, cache_key, _retry_kis(f"KR price [{ticker}]", _fetch))
    except Exception:
        cached = _cache_get(_PRICE_CACHE, cache_key)
        if cached is not None:
            log.warning(f"KIS KR 현재가 캐시 사용 [{ticker}]")
            return cached
        raise


def _pick_first(mapping: dict, keys, default=None):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


_US_QUOTE_CODE_MAP = {
    "NASD": "NAS",
    "NYSE": "NYS",
    "AMEX": "AMS",
}

_US_EXCHANGE_CACHE: dict[str, str] = {}
_US_DAILYPRICE_FALLBACK = set()
_load_exchange_cache()  # data/exchange_cache.json → _US_EXCHANGE_CACHE


def _probe_us_exchange_code(ticker: str, token: str):
    normalized = ticker.upper()
    for order_exch, quote_exch in _US_QUOTE_CODE_MAP.items():
        try:
            resp = _kis_get(
                f"{_base_url('US')}/uapi/overseas-price/v1/quotations/price",
                headers=_headers(token, "HHDFS00000300", market="US"),
                params={"AUTH": "", "EXCD": quote_exch, "SYMB": normalized},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                continue
            out = data.get("output", {})
            price = _to_float(_pick_first(out, ["last", "ovrs_nmix_prpr", "stck_prpr", "clos"]), 0)
            if price > 0:
                _US_EXCHANGE_CACHE[normalized] = order_exch
                return order_exch
        except Exception:
            continue
    raise ValueError(
        f"미국 거래소 코드 미정의 티커: {normalized}. "
        "KIS 시세 조회로도 거래소 판별 실패"
    )


def _hardcoded_us_exchange_code(ticker: str) -> Optional[str]:
    normalized = ticker.upper()
    for exch, tickers in _US_EXCHANGE_MAP.items():
        if normalized in tickers:
            return exch
    return None


def _get_ovrs_excg_cd(ticker: str, token: str = None) -> str:
    """
    KIS 거래소 코드 반환 순서:
    1. _US_EXCHANGE_MAP (하드코딩 고정 종목, 캐시보다 우선)
    2. _US_EXCHANGE_CACHE (메모리 + 파일 로드분)
    3. Finnhub profile2 resolve
    4. KIS VTS probe (보조)
    실패 시 ValueError — 침묵 NASD fallback 없음.
    """
    normalized = ticker.upper()
    cached = _US_EXCHANGE_CACHE.get(normalized)

    # 1. 하드코딩 맵은 사람이 검증한 override다. 캐시와 충돌하면 캐시 파일까지 교정한다.
    hardcoded = _hardcoded_us_exchange_code(normalized)
    if hardcoded:
        if cached != hardcoded:
            if cached:
                log.warning(
                    f"[exchange_resolve] {normalized} cache conflict: "
                    f"cache={cached} hardcoded={hardcoded}; using hardcoded"
                )
            _US_EXCHANGE_CACHE[normalized] = hardcoded
            _save_exchange_cache()
        return hardcoded

    # 2. 메모리/파일 캐시
    if cached:
        return cached

    # 3. Finnhub profile resolve
    try:
        code = _resolve_us_exchange_finnhub(normalized)
        _US_EXCHANGE_CACHE[normalized] = code
        _save_exchange_cache()
        log.info(f"[exchange_resolve] {normalized} → {code} (Finnhub)")
        return code
    except Exception as e:
        log.debug(f"[exchange_resolve] Finnhub 실패 [{normalized}]: {e}")
    # 4. Yahoo metadata fallback. Some newly listed ADRs/foreign issuers lag in Finnhub profile2.
    try:
        code = _resolve_us_exchange_yahoo(normalized)
        _US_EXCHANGE_CACHE[normalized] = code
        _save_exchange_cache()
        log.info(f"[exchange_resolve] {normalized} → {code} (Yahoo)")
        return code
    except Exception as e:
        log.debug(f"[exchange_resolve] Yahoo 실패 [{normalized}]: {e}")
    # 5. KIS VTS probe (보조)
    if token:
        try:
            code = _probe_us_exchange_code(normalized, token)
            _US_EXCHANGE_CACHE[normalized] = code
            _save_exchange_cache()
            log.info(f"[exchange_resolve] {normalized} → {code} (KIS probe)")
            return code
        except Exception as e:
            log.debug(f"[exchange_resolve] KIS probe 실패 [{normalized}]: {e}")
    raise ValueError(
        f"[exchange_resolve] {normalized}: exchange code unknown. "
        "Run collect_screener_pool or add to _US_EXCHANGE_MAP."
    )


def _get_us_quote_codes(ticker: str, token: str) -> tuple[str, str]:
    order_exch = _get_ovrs_excg_cd(ticker, token=token)
    return order_exch, _US_QUOTE_CODE_MAP[order_exch]


def _get_price_us_kis(ticker: str, token: str) -> dict:
    _, quote_exch = _get_us_quote_codes(ticker, token)
    resp = _kis_get(
        f"{_base_url('US')}/uapi/overseas-price/v1/quotations/price",
        headers=_headers(token, "HHDFS00000300", market="US"),
        params={
            "AUTH": "",
            "EXCD": quote_exch,
            "SYMB": ticker.upper(),
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _require_kis_success(data, f"해외현재가 조회 [{ticker}]")
    out = data.get("output", {})

    price = _to_float(_pick_first(out, ["last", "ovrs_nmix_prpr", "stck_prpr", "clos"]), 0)
    prev_close = _to_float(_pick_first(out, ["base", "prev", "prdy_vrss_sign", "tomv"]), price)
    open_price = _to_float(_pick_first(out, ["open", "t_open", "ovrs_oprc"]), price)
    high_price = _to_float(_pick_first(out, ["high", "t_high", "ovrs_hgpr"]), price)
    low_price = _to_float(_pick_first(out, ["low", "t_low", "ovrs_lwpr"]), price)
    change = _to_float(_pick_first(out, ["diff", "t_xdif", "prdy_vrss"]), price - prev_close)
    change_rate = _to_float(
        _pick_first(out, ["rate", "t_xrat", "prdy_ctrt"]),
        (change / prev_close * 100.0) if prev_close else 0.0,
    )
    volume = int(_to_float(_pick_first(out, ["tvol", "acml_vol", "volume"]), 0))

    suspicious_ohlc = (
        price > 0 and (
            open_price <= 0 or high_price <= 0 or low_price <= 0 or
            (open_price == high_price == low_price == price)
        )
    )
    if suspicious_ohlc:
        try:
            latest = _daily_ohlcv_us_kis(ticker, token, lookback_days=1)
            if not latest.empty:
                row = latest.iloc[-1]
                open_price = _to_float(row.get("open"), open_price)
                high_price = _to_float(row.get("high"), high_price)
                low_price = _to_float(row.get("low"), low_price)
                log.info(
                    f"KIS US 현재가 OHLC 보정 [{ticker}] "
                    f"keys={sorted(out.keys())[:12]}"
                )
        except Exception as e:
            log.warning(f"KIS US 현재가 OHLC 보정 실패 [{ticker}]: {e}")

    if price <= 0:
        raise ValueError(f"KIS US price=0 [{ticker}] EXCD={quote_exch} — Finnhub 폴백 전환")

    return {
        "ticker": ticker.upper(),
        "name": _pick_first(out, ["e_hname", "hts_kor_isnm", "ovrs_item_name"], ticker.upper()),
        "price": round(price, 4),
        "change": round(change, 4),
        "change_rate": round(change_rate, 2),
        "volume": volume,
        "open": round(open_price, 4),
        "high": round(high_price, 4),
        "low": round(low_price, 4),
    }


def _get_price_us_finnhub(ticker: str) -> dict:
    """Finnhub /quote — 무료 60회/분 (일 한도 없음)"""
    if not FINNHUB_KEY:
        raise RuntimeError("FINNHUB_API_KEY 없음")
    resp = requests.get(
        "https://finnhub.io/api/v1/quote",
        params={"symbol": ticker, "token": FINNHUB_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    q = resp.json()
    price = float(q.get("c", 0))
    if not price:
        raise ValueError(f"Finnhub: {ticker} 가격 없음")
    prev = float(q.get("pc", price))
    change = price - prev
    return {
        "ticker": ticker, "name": ticker,
        "price": round(price, 4),
        "change": round(change, 4),
        "change_rate": round(change / prev * 100 if prev else 0, 2),
        "volume": 0,
        "open": round(float(q.get("o", price)), 4),
        "high": round(float(q.get("h", price)), 4),
        "low": round(float(q.get("l", price)), 4),
    }


def _get_price_us_alpha(ticker):
    """Alpha Vantage GLOBAL_QUOTE — 레거시 폴백 (KEY_1 소진 시 KEY_2 자동 전환)"""
    if not AV_KEY and not AV_KEY_2:
        return _get_price_us_yf(ticker)
    try:
        data = _av_get({"function": "GLOBAL_QUOTE", "symbol": ticker})
        q = data.get("Global Quote", {})
        if not q or not q.get("05. price"):
            raise ValueError("빈 응답")
        return {
            "ticker": ticker, "name": ticker,
            "price": round(float(q.get("05. price", 0)), 4),
            "change": round(float(q.get("09. change", 0)), 4),
            "change_rate": float(q.get("10. change percent", "0%").replace("%", "")),
            "volume": int(float(q.get("06. volume", 0))),
            "open": round(float(q.get("02. open", 0)), 4),
            "high": round(float(q.get("03. high", 0)), 4),
            "low": round(float(q.get("04. low", 0)), 4),
        }
    except Exception:
        return _get_price_us_yf(ticker)


def _get_price_kr_yf(ticker: str) -> dict:
    """KIS API 실패 시 yfinance로 KR 현재가 폴백"""
    try:
        import yfinance as yf
        suffix = ".KQ" if ticker.startswith(("09", "27", "29", "33")) else ".KS"
        t = yf.Ticker(f"{ticker}{suffix}")
        info = t.fast_info
        price = int(info.last_price or 0)
        if price == 0:
            hist = t.history(period="2d")
            if not hist.empty:
                price = int(hist["Close"].iloc[-1])
        return {
            "ticker": ticker, "name": ticker,
            "price": price,
            "change": 0, "change_rate": 0.0,
            "volume": 0, "open": 0, "high": price, "low": price,
        }
    except Exception:
        return {
            "ticker": ticker, "name": ticker,
            "price": 0, "change": 0, "change_rate": 0.0,
            "volume": 0, "open": 0, "high": 0, "low": 0,
        }


def _parse_intraday_dt(value, default=None) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return default


def _intraday_provider_for_market(market: str, provider: str = "") -> str:
    market_key = _normalize_market(market)
    raw = str(provider or "").strip().lower()
    if raw in {"", "auto"}:
        raw = str(os.getenv(f"INTRADAY_EVIDENCE_PROVIDER_{market_key}", "") or "").strip().lower()
    if not raw:
        return "kis" if market_key == "KR" else "disabled"
    return raw


def _intraday_empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "source"])


def _intraday_filter_frame(df: pd.DataFrame, *, start_at=None, end_at=None, source: str = "") -> pd.DataFrame:
    if df is None or df.empty:
        return _intraday_empty_frame()
    work = df.copy()
    if "ts" not in work.columns:
        return _intraday_empty_frame()
    work["ts"] = pd.to_datetime(work["ts"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["ts", "open", "high", "low", "close"])
    start_dt = _parse_intraday_dt(start_at)
    end_dt = _parse_intraday_dt(end_at)
    if start_dt is not None:
        work = work[work["ts"] >= pd.Timestamp(start_dt)]
    if end_dt is not None:
        work = work[work["ts"] <= pd.Timestamp(end_dt)]
    if work.empty:
        return _intraday_empty_frame()
    work["source"] = source or work.get("source", "")
    return work[["ts", "open", "high", "low", "close", "volume", "source"]].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def _intraday_ohlcv_kr_kis(
    ticker: str,
    token: str,
    *,
    session_date: str,
    start_at=None,
    end_at=None,
    deadline: Optional[float] = None,
    request_timeout: Optional[float] = None,
) -> pd.DataFrame:
    """KR 당일 1분봉 조회.

    KIS 공개 샘플 기준:
    - path: /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice
    - tr_id: FHKST03010200

    실제 rate limit은 구현/운영 전 KIS Developers 최신 문서로 재확인해야 한다.
    기본 호출 정책은 보수적으로 순차 호출에서 사용된다.
    """
    ticker_key = str(ticker or "").strip().zfill(6)
    if not ticker_key:
        return _intraday_empty_frame()
    session_text = str(session_date or datetime.now().date().isoformat())[:10]
    start_dt = _parse_intraday_dt(start_at) or datetime.fromisoformat(f"{session_text}T09:00:00")
    end_dt = _parse_intraday_dt(end_at) or datetime.now()
    end_hms = end_dt.strftime("%H%M%S")
    max_pages = max(1, int(os.getenv("KR_INTRADAY_KIS_MAX_PAGES", "16")))
    rows_all: list[dict] = []
    seen_times: set[str] = set()

    for _ in range(max_pages):
        timeout = float(request_timeout if request_timeout is not None else 10)
        if deadline is not None:
            remaining = float(deadline) - time.time()
            if remaining <= 0:
                raise TimeoutError("provider_timeout")
            timeout = max(0.05, min(timeout, remaining))
        resp = _kis_get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=_headers(token, "FHKST03010200", market="KR"),
            params={
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker_key,
                "FID_INPUT_HOUR_1": end_hms,
                "FID_PW_DATA_INCU_YN": "N",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("output2") or data.get("output") or []
        if not rows:
            break
        parsed_times: list[datetime] = []
        for item in rows:
            date_raw = str(item.get("stck_bsop_date") or session_text.replace("-", ""))
            time_raw = str(item.get("stck_cntg_hour") or "").zfill(6)
            ts = _parse_intraday_dt(f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}T{time_raw[:2]}:{time_raw[2:4]}:{time_raw[4:6]}")
            if ts is None:
                continue
            key = ts.isoformat(timespec="seconds")
            if key in seen_times:
                continue
            seen_times.add(key)
            parsed_times.append(ts)
            rows_all.append(
                {
                    "ts": ts,
                    "open": _to_float(item.get("stck_oprc")),
                    "high": _to_float(item.get("stck_hgpr")),
                    "low": _to_float(item.get("stck_lwpr")),
                    "close": _to_float(item.get("stck_prpr")),
                    "volume": _to_float(item.get("cntg_vol")),
                    "source": "kis",
                }
            )
        if not parsed_times:
            break
        earliest = min(parsed_times)
        if earliest <= start_dt:
            break
        next_end = earliest - timedelta(minutes=1)
        next_hms = next_end.strftime("%H%M%S")
        if next_hms == end_hms:
            break
        end_hms = next_hms
        sleep_sec = float(os.getenv("KR_INTRADAY_KIS_PAGE_SLEEP_SEC", "0.05"))
        if deadline is not None:
            remaining = float(deadline) - time.time()
            if remaining <= 0:
                raise TimeoutError("provider_timeout")
            sleep_sec = min(sleep_sec, max(0.0, remaining))
        if sleep_sec > 0:
            time.sleep(sleep_sec)

    if not rows_all:
        return _intraday_empty_frame()
    return _intraday_filter_frame(pd.DataFrame(rows_all), start_at=start_dt, end_at=end_dt, source="kis")


def _intraday_ohlcv_us_yf(
    ticker: str,
    *,
    session_date: str,
    start_at=None,
    end_at=None,
    include_extended: bool = False,
) -> pd.DataFrame:
    try:
        import yfinance as yf
        from zoneinfo import ZoneInfo
    except ImportError as exc:
        raise RuntimeError("yfinance 미설치: pip install yfinance") from exc
    start_dt = _parse_intraday_dt(start_at) or (datetime.now() - timedelta(days=1))
    end_dt = _parse_intraday_dt(end_at) or datetime.now()
    symbol = str(ticker or "").strip().upper()
    df = yf.Ticker(symbol).history(
        start=(start_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
        end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
        interval="1m",
        prepost=bool(include_extended),
        auto_adjust=True,
    )
    if df is None or df.empty:
        return _intraday_empty_frame()
    frame = df.reset_index()
    frame.columns = [str(col).lower() for col in frame.columns]
    ts_col = "datetime" if "datetime" in frame.columns else ("date" if "date" in frame.columns else frame.columns[0])
    ts = pd.to_datetime(frame[ts_col], errors="coerce")
    try:
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
        else:
            ts = ts.dt.tz_localize(ZoneInfo("America/New_York")).dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    except Exception:
        ts = pd.to_datetime(frame[ts_col], errors="coerce").dt.tz_localize(None)
    out = pd.DataFrame(
        {
            "ts": ts,
            "open": frame.get("open"),
            "high": frame.get("high"),
            "low": frame.get("low"),
            "close": frame.get("close"),
            "volume": frame.get("volume", 0),
            "source": "yfinance_intraday",
        }
    )
    return _intraday_filter_frame(out, start_at=start_dt, end_at=end_dt, source="yfinance_intraday")


def _intraday_ohlcv_us_finnhub(
    ticker: str,
    *,
    start_at=None,
    end_at=None,
) -> pd.DataFrame:
    if not FINNHUB_KEY:
        raise RuntimeError("FINNHUB_API_KEY 없음")
    start_dt = _parse_intraday_dt(start_at) or (datetime.now() - timedelta(hours=8))
    end_dt = _parse_intraday_dt(end_at) or datetime.now()
    resp = requests.get(
        "https://finnhub.io/api/v1/stock/candle",
        params={
            "symbol": str(ticker or "").strip().upper(),
            "resolution": "1",
            "from": int(start_dt.timestamp()),
            "to": int(end_dt.timestamp()),
            "token": FINNHUB_KEY,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("s") != "ok":
        raise RuntimeError(f"Finnhub candle status={data.get('s')}")
    from zoneinfo import ZoneInfo

    ts = pd.to_datetime(data.get("t", []), unit="s", utc=True).tz_convert("Asia/Seoul").tz_localize(None)
    out = pd.DataFrame(
        {
            "ts": ts,
            "open": data.get("o", []),
            "high": data.get("h", []),
            "low": data.get("l", []),
            "close": data.get("c", []),
            "volume": data.get("v", []),
            "source": "finnhub_intraday",
        }
    )
    return _intraday_filter_frame(out, start_at=start_dt, end_at=end_dt, source="finnhub_intraday")


def get_intraday_candles(
    ticker: str,
    token: Optional[str] = None,
    market: str = "KR",
    *,
    session_date: Optional[str] = None,
    start_at=None,
    end_at=None,
    interval: str = "1m",
    provider: str = "",
    deadline: Optional[float] = None,
    request_timeout: Optional[float] = None,
) -> pd.DataFrame:
    market_key = _normalize_market(market)
    if str(interval or "1m").lower() not in {"1m", "1min", "1"}:
        raise ValueError(f"unsupported intraday interval: {interval}")
    session_text = str(session_date or datetime.now().date().isoformat())[:10]
    provider_key = _intraday_provider_for_market(market_key, provider)
    if provider_key in {"disabled", "none", "off", "false"}:
        raise RuntimeError(f"{market_key} intraday provider disabled")
    ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip().zfill(6)
    cache_key = (
        "INTRADAY",
        market_key,
        provider_key,
        ticker_key,
        session_text,
        str(start_at or "")[:19],
        str(end_at or "")[:19],
    )
    cached = _cache_get(_INTRADAY_CACHE, cache_key)
    if cached is not None:
        return cached.copy()

    if market_key == "KR":
        if provider_key != "kis":
            raise RuntimeError(f"unsupported KR intraday provider: {provider_key}")
        df = _retry_kis(
            f"KR intraday ohlcv [{ticker_key}]",
            lambda: _intraday_ohlcv_kr_kis(
                ticker_key,
                token or get_access_token(market="KR"),
                session_date=session_text,
                start_at=start_at,
                end_at=end_at,
                deadline=deadline,
                request_timeout=request_timeout,
            ),
            retries=0 if deadline is not None else 2,
            delay_sec=float(os.getenv("KR_INTRADAY_KIS_RETRY_AFTER_SEC", "1.0")),
        )
    elif provider_key == "yfinance":
        include_extended = str(os.getenv("US_INTRADAY_INCLUDE_EXTENDED", "false")).strip().lower() in {"1", "true", "yes", "y", "on"}
        df = _intraday_ohlcv_us_yf(
            ticker_key,
            session_date=session_text,
            start_at=start_at,
            end_at=end_at,
            include_extended=include_extended,
        )
    elif provider_key == "finnhub":
        df = _intraday_ohlcv_us_finnhub(ticker_key, start_at=start_at, end_at=end_at)
    elif provider_key == "kis":
        raise RuntimeError("KIS US intraday candle provider not verified; set INTRADAY_EVIDENCE_PROVIDER_US=yfinance/finnhub after policy approval")
    else:
        raise RuntimeError(f"unsupported {market_key} intraday provider: {provider_key}")

    return _cache_set(_INTRADAY_CACHE, cache_key, df.copy())


def is_trading_halted(ticker: str, token) -> bool:
    """종목 거래 정지 여부 확인 (KR 전용)
    iscd_stat_cls_code 코드:
      00=정상, 51=관리, 52=투자주의, 53=투자경고, 54=투자위험예고,
      55=투자위험, 56=정리매매, 57=단기과열, 58=거래정지
    실제 매도 불가 상태는 58(거래정지)만 해당.
    API 실패 시 False 반환 (보수적 처리 — 진입 차단하지 않음).
    """
    _HALT_CODES = {"58"}  # 거래정지만 실제 매도 불가
    try:
        resp = _kis_get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=_headers(token, "FHKST01010100"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            timeout=5,
        )
        resp.raise_for_status()
        stat = resp.json().get("output", {}).get("iscd_stat_cls_code", "00")
        if stat in _HALT_CODES:
            log.warning(f"[거래 상태] {ticker} iscd_stat_cls_code={stat} → 거래정지")
            return True
        if stat not in ("00", ""):
            log.debug(f"[거래 상태] {ticker} iscd_stat_cls_code={stat} → 경고/주의 (거래 가능)")
        return False
    except Exception as e:
        log.debug(f"[거래 상태 확인 실패] {ticker}: {e}")
        return False


def get_price(ticker, token, market="KR"):
    if market == "US":
        if IS_PAPER:
            # 모의투자: KIS VTS 미지원(실시간 WS 없음) → Finnhub 1차
            try:
                result = _get_price_us_finnhub(ticker)
                log.info(f"US 현재가 Finnhub 성공 [{ticker}]")
                return result
            except Exception as e:
                log.warning(f"US 현재가 Finnhub 실패 [{ticker}] → 폴백: {e}")
        else:
            # 실투자: KIS WebSocket tick이 있지만 REST도 1차로 유지
            try:
                result = _retry_kis(
                    f"US price [{ticker}]",
                    lambda: _get_price_us_kis(ticker, token),
                    retries=3, delay_sec=0.5,
                )
                log.info(f"KIS US 현재가 성공 [{ticker}]")
                return result
            except Exception as e:
                log.warning(f"KIS US 현재가 실패 [{ticker}] → 폴백 전환: {e}")
            # 실투자 2차: Finnhub
            try:
                result = _get_price_us_finnhub(ticker)
                log.info(f"US 현재가 Finnhub 폴백 성공 [{ticker}]")
                return result
            except Exception as e:
                log.warning(f"US 현재가 Finnhub 실패 [{ticker}]: {e}")
        # 공통 폴백: yfinance → Alpha Vantage
        try:
            result = _get_price_us_yf(ticker)
            log.info(f"US 현재가 yfinance 폴백 성공 [{ticker}]")
            return result
        except Exception as e:
            log.warning(f"US 현재가 yfinance 실패 [{ticker}]: {e}")
        result = _get_price_us_alpha(ticker)
        log.info(f"US 현재가 Alpha Vantage 폴백 성공 [{ticker}]")
        return result
    try:
        return _get_price_kr(ticker, token)
    except Exception as e:
        log.warning(f"KIS 가격 조회 실패 [{ticker}] → yfinance 폴백: {e}")
        return _get_price_kr_yf(ticker)


def _daily_ohlcv_kr(ticker, token, lookback_days=200):
    cache_key = ("KR", ticker, int(lookback_days))

    def _fetch():
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=max(lookback_days, 30) * 2)

        def _fetch_range(range_start: datetime, range_end: datetime) -> pd.DataFrame:
            url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
            headers = _headers(token, "FHKST03010100")
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": range_start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": range_end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            }
            resp = _kis_get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            rows = resp.json().get("output2", [])
            if not rows:
                return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

            frame = pd.DataFrame(rows).rename(
                columns={
                    "stck_bsop_date": "date",
                    "stck_oprc": "open",
                    "stck_hgpr": "high",
                    "stck_lwpr": "low",
                    "stck_clpr": "close",
                    "acml_vol": "volume",
                }
            )
            keep = ["date", "open", "high", "low", "close", "volume"]
            frame = frame[[c for c in keep if c in frame.columns]].copy()
            for c in ("open", "high", "low", "close", "volume"):
                frame[c] = pd.to_numeric(frame[c], errors="coerce")
            frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="coerce")
            frame = frame.dropna(subset=["date", "open", "high", "low", "close", "volume"])
            return frame.sort_values("date").reset_index(drop=True)

        if int(lookback_days or 0) <= 120:
            return _fetch_range(start_dt, end_dt).tail(lookback_days).reset_index(drop=True)

        parts: list[pd.DataFrame] = []
        chunk_end = end_dt
        min_needed = int(lookback_days or 200)
        while chunk_end >= start_dt:
            chunk_start = max(start_dt, chunk_end - timedelta(days=140))
            df_chunk = _fetch_range(chunk_start, chunk_end)
            if df_chunk.empty:
                break
            parts.append(df_chunk)
            merged_count = len(pd.concat(parts, ignore_index=True).drop_duplicates("date"))
            if merged_count >= min_needed:
                break
            next_end = df_chunk["date"].min().to_pydatetime() - timedelta(days=1)
            if next_end >= chunk_end:
                break
            chunk_end = next_end
            time.sleep(0.05)

        if parts:
            df = (
                pd.concat(parts, ignore_index=True)
                .drop_duplicates("date")
                .sort_values("date")
                .tail(lookback_days)
                .reset_index(drop=True)
            )
            return df

        url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        headers = _headers(token, "FHKST03010100")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": start_dt.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end_dt.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        resp = _kis_get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get("output2", [])
        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rows).rename(
            columns={
                "stck_bsop_date": "date",
                "stck_oprc": "open",
                "stck_hgpr": "high",
                "stck_lwpr": "low",
                "stck_clpr": "close",
                "acml_vol": "volume",
            }
        )
        keep = ["date", "open", "high", "low", "close", "volume"]
        df = df[[c for c in keep if c in df.columns]].copy()
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["date", "open", "high", "low", "close", "volume"])
        return df.sort_values("date").tail(lookback_days).reset_index(drop=True)

    try:
        return _cache_set(_OHLCV_CACHE, cache_key, _retry_kis(f"KR daily ohlcv [{ticker}]", _fetch))
    except Exception:
        cached = _cache_get(_OHLCV_CACHE, cache_key)
        if cached is not None:
            log.warning(f"KIS KR 일봉 캐시 사용 [{ticker}]")
            return cached
        raise


_KR_YF_SUFFIX_CACHE: dict[str, str] = {}


def _daily_ohlcv_kr_yf(ticker: str, lookback_days: int = 365) -> pd.DataFrame:
    """yfinance KR daily OHLCV fallback.

    Yahoo uses .KS for KOSPI and .KQ for KOSDAQ. KRX codes are unique enough for
    a cached suffix probe to be safe in live fallback use.
    """
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    ticker_key = str(ticker or "").strip()
    if ticker_key.isdigit():
        ticker_key = ticker_key.zfill(6)
    if not ticker_key:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    cached_suffix = _KR_YF_SUFFIX_CACHE.get(ticker_key)
    suffixes = [cached_suffix] if cached_suffix else []
    suffixes += [s for s in (".KS", ".KQ") if s not in suffixes]

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=max(int(lookback_days or 365), 90) * 2)
    for suffix in suffixes:
        symbol = f"{ticker_key}{suffix}"
        try:
            df = yf.Ticker(symbol).history(
                start=start_dt.strftime("%Y-%m-%d"),
                end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
                auto_adjust=True,
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        date_col = "date" if "date" in df.columns else ("datetime" if "datetime" in df.columns else "")
        if not date_col:
            continue
        df["date"] = pd.to_datetime(df[date_col], errors="coerce").dt.tz_localize(None)
        keep = ["date", "open", "high", "low", "close", "volume"]
        if not all(c in df.columns for c in keep):
            continue
        df = df[keep].copy()
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date")
        if df.empty:
            continue
        _KR_YF_SUFFIX_CACHE[ticker_key] = suffix
        return df.tail(int(lookback_days or 365)).reset_index(drop=True)

    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _daily_ohlcv_us_kis(ticker: str, token: str, lookback_days: int = 200) -> pd.DataFrame:
    _, quote_exch = _get_us_quote_codes(ticker, token)
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=max(lookback_days, 30) * 3)
    resp = _kis_get(
        f"{_base_url('US')}/uapi/overseas-price/v1/quotations/dailyprice",
        headers=_headers(token, "HHDFS76240000", market="US"),
        params={
            "AUTH": "",
            "EXCD": quote_exch,
            "SYMB": ticker.upper(),
            "GUBN": "0",
            "BYMD": end_dt.strftime("%Y%m%d"),
            "MODP": "1",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _require_kis_success(data, f"해외기간시세 조회 [{ticker}]")
    rows = data.get("output2", []) or data.get("output", [])
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    normalized = []
    for row in rows:
        date_raw = _pick_first(row, ["xymd", "date", "bas_dt"])
        normalized.append(
            {
                "date": date_raw,
                "open": _to_float(_pick_first(row, ["open", "ovrs_oprc"])),
                "high": _to_float(_pick_first(row, ["high", "ovrs_hgpr"])),
                "low": _to_float(_pick_first(row, ["low", "ovrs_lwpr"])),
                "close": _to_float(_pick_first(row, ["clos", "last", "ovrs_clpr"])),
                "volume": _to_float(_pick_first(row, ["tvol", "acml_vol", "volume"])),
            }
        )
    df = pd.DataFrame(normalized)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close"])
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = df[df["date"] >= pd.Timestamp(start_dt.date())]
    return df.sort_values("date").tail(lookback_days).reset_index(drop=True)


def _daily_ohlcv_us_yf(ticker: str, lookback_days: int = 200) -> pd.DataFrame:
    """yfinance US OHLCV 폴백 (AV 키 없거나 실패 시)"""
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    try:
        start = (datetime.now() - timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")
        df = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    except Exception:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    return df.sort_values("date").tail(lookback_days).reset_index(drop=True)


def _get_price_us_yf(ticker: str) -> dict:
    """yfinance US 현재가 폴백"""
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance 미설치: pip install yfinance")
    hist = yf.Ticker(ticker).history(period="2d")
    if hist.empty:
        raise RuntimeError(f"yfinance: {ticker} 데이터 없음")
    row = hist.iloc[-1]
    prev_close = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else float(row["Close"])
    price = float(row["Close"])
    change = price - prev_close
    return {
        "ticker": ticker, "name": ticker,
        "price": round(price, 4),
        "change": round(change, 4),
        "change_rate": round(change / prev_close * 100 if prev_close else 0, 2),
        "volume": int(row["Volume"]),
        "open": round(float(row["Open"]), 4),
        "high": round(float(row["High"]), 4),
        "low": round(float(row["Low"]), 4),
    }


def _daily_ohlcv_us_alpha(ticker, lookback_days=200):
    if not AV_KEY and not AV_KEY_2:
        return _daily_ohlcv_us_yf(ticker, lookback_days)
    try:
        outputsize = "full" if lookback_days > 100 else "compact"
        data = _av_get({"function": "TIME_SERIES_DAILY", "symbol": ticker,
                        "outputsize": outputsize}, timeout=20)
        ts = data.get("Time Series (Daily)", {})
        if not ts:
            raise ValueError("빈 응답")
        rows = [
            {"date": d, "open": float(v["1. open"]), "high": float(v["2. high"]),
             "low": float(v["3. low"]), "close": float(v["4. close"]),
             "volume": float(v["5. volume"])}
            for d, v in ts.items()
        ]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date").tail(lookback_days).reset_index(drop=True)
        # compact 100일보다 lookback이 길면 yfinance로 보완
        if len(df) < min(lookback_days, 80):
            return _daily_ohlcv_us_yf(ticker, lookback_days)
        return df
    except Exception:
        return _daily_ohlcv_us_yf(ticker, lookback_days)


def get_daily_ohlcv(ticker, token, lookback_days=200, market="KR"):
    if market == "US":
        normalized = ticker.upper()
        if normalized in _US_DAILYPRICE_FALLBACK:
            try:
                df = _daily_ohlcv_us_yf(ticker, lookback_days=lookback_days)
                if not df.empty:
                    log.info(f"US 기간시세 캐시된 외부 폴백 사용 [{ticker}] rows={len(df)}")
                    return df
            except Exception:
                df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
            df = _daily_ohlcv_us_alpha(ticker, lookback_days=lookback_days)
            if not df.empty:
                log.info(f"US 기간시세 Alpha Vantage 폴백 성공 [{ticker}] rows={len(df)}")
            else:
                log.error(f"US 기간시세 모든 소스 실패 [{ticker}]")
            return df
        # 1차: KIS 해외 기간시세
        try:
            df = _daily_ohlcv_us_kis(ticker, token, lookback_days=lookback_days)
            if not df.empty:
                log.info(f"KIS US 기간시세 성공 [{ticker}] rows={len(df)}")
        except Exception as e:
            df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
            _US_DAILYPRICE_FALLBACK.add(normalized)
            log.warning(f"KIS US 기간시세 실패 [{ticker}] → 외부 폴백 고정: {e}")
        if not df.empty:
            return df
        # 2차: yfinance → AV 레거시 폴백
        try:
            df = _daily_ohlcv_us_yf(ticker, lookback_days=lookback_days)
            if not df.empty:
                log.info(f"US 기간시세 yfinance 폴백 성공 [{ticker}] rows={len(df)}")
        except Exception:
            df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
            log.warning(f"US 기간시세 yfinance 실패 [{ticker}]")
        if not df.empty:
            return df
        df = _daily_ohlcv_us_alpha(ticker, lookback_days=lookback_days)
        if not df.empty:
            log.info(f"US 기간시세 Alpha Vantage 폴백 성공 [{ticker}] rows={len(df)}")
        else:
            log.error(f"US 기간시세 모든 소스 실패 [{ticker}]")
        return df
    return _daily_ohlcv_kr(ticker, token, lookback_days=lookback_days)


def _signed_kis_float(value, sign_code="", default=0.0) -> float:
    try:
        num = float(str(value or "").replace(",", ""))
    except Exception:
        return float(default)
    sign = str(sign_code or "").strip()
    if sign in ("4", "5"):
        return -abs(num)
    if sign in ("1", "2"):
        return abs(num)
    return num


def _yf_index_snapshot(index_key: str) -> dict:
    """US index snapshot via yfinance. Used for S&P500/NASDAQ/VIX intraday context."""
    import yfinance as yf

    key = str(index_key or "SP500").upper().replace(" ", "").replace("&", "")
    symbol_map = {
        "SP500": "^GSPC",
        "SPX": "^GSPC",
        "GSPC": "^GSPC",
        "^GSPC": "^GSPC",
        "NASDAQ": "^IXIC",
        "NAS100": "^IXIC",
        "IXIC": "^IXIC",
        "^IXIC": "^IXIC",
        "VIX": "^VIX",
        "^VIX": "^VIX",
    }
    label_map = {
        "^GSPC": "S&P500",
        "^IXIC": "NASDAQ",
        "^VIX": "VIX",
    }
    symbol = symbol_map.get(key, key if key.startswith("^") else f"^{key}")
    cache_key = ("US_INDEX", symbol)
    cached = _cache_get(_INDEX_CACHE, cache_key)
    if cached is not None:
        return cached

    ticker = yf.Ticker(symbol)
    price = 0.0
    prev_close = 0.0
    source = "yfinance"

    try:
        intraday = ticker.history(period="1d", interval="1m")
        if intraday is not None and not intraday.empty:
            closes = intraday["Close"].dropna()
            if not closes.empty:
                price = float(closes.iloc[-1])
                source = "yfinance_intraday"
    except Exception:
        pass

    try:
        fast_info = getattr(ticker, "fast_info", {}) or {}
        prev_close = float(fast_info.get("previous_close") or fast_info.get("regular_market_previous_close") or 0.0)
        if not price:
            price = float(fast_info.get("last_price") or fast_info.get("regular_market_price") or 0.0)
    except Exception:
        pass

    if not price or not prev_close:
        daily = ticker.history(period="5d", interval="1d")
        if daily is not None and not daily.empty:
            closes = daily["Close"].dropna()
            if len(closes) >= 1 and not price:
                price = float(closes.iloc[-1])
                source = "yfinance_daily"
            if len(closes) >= 2 and not prev_close:
                prev_close = float(closes.iloc[-2])

    change = price - prev_close if price and prev_close else 0.0
    change_pct = (change / prev_close * 100.0) if prev_close else 0.0
    snap = {
        "market": "US",
        "index": label_map.get(symbol, key),
        "symbol": symbol,
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "prev_close": prev_close,
        "source": source,
    }
    return _cache_set(_INDEX_CACHE, cache_key, snap)


def get_index_snapshot(market: str = "KR", index: str = "KOSPI", token: str = "") -> dict:
    """Live index snapshot. KR uses KIS index quote, fallback callers may use get_index_change."""
    market = str(market or "KR").upper()
    index_key = str(index or "KOSPI").upper()
    if market != "KR":
        return _yf_index_snapshot(index_key)

    code_map = {
        "KOSPI": "0001",
        "KS11": "0001",
        "0001": "0001",
        "KOSDAQ": "1001",
        "KQ11": "1001",
        "1001": "1001",
        "KOSPI200": "2001",
        "2001": "2001",
    }
    code = code_map.get(index_key, index_key)
    cache_key = ("KR_INDEX", code)
    cached = _cache_get(_INDEX_CACHE, cache_key)
    if cached is not None:
        return cached

    def _fetch():
        url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
        resp = _kis_get(
            url,
            headers=_headers(token or get_access_token(market="KR"), "FHKUP03500100"),
            params={"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if str(body.get("rt_cd", "0")) != "0":
            raise RuntimeError(f"KIS index quote failed {code}: {body.get('msg1') or body}")
        o = body.get("output") or {}
        sign = o.get("prdy_vrss_sign", "")
        name = o.get("hts_kor_isnm") or ("KOSDAQ" if code == "1001" else "KOSPI")
        snap = {
            "market": "KR",
            "index": name,
            "code": code,
            "price": _signed_kis_float(o.get("bstp_nmix_prpr"), ""),
            "change": _signed_kis_float(o.get("bstp_nmix_prdy_vrss"), sign),
            "change_pct": _signed_kis_float(o.get("bstp_nmix_prdy_ctrt"), sign),
            "open": _signed_kis_float(o.get("bstp_nmix_oprc"), ""),
            "high": _signed_kis_float(o.get("bstp_nmix_hgpr"), ""),
            "low": _signed_kis_float(o.get("bstp_nmix_lwpr"), ""),
            "advancers": int(float(o.get("ascn_issu_cnt") or 0)),
            "decliners": int(float(o.get("down_issu_cnt") or 0)),
            "unchanged": int(float(o.get("stnr_issu_cnt") or 0)),
            "volume": int(float(o.get("acml_vol") or 0)),
            "trade_value": int(float(o.get("acml_tr_pbmn") or 0)),
            "source": "kis_index_price",
        }
        return snap

    return _cache_set(_INDEX_CACHE, cache_key, _retry_kis(f"KR index quote [{code}]", _fetch))


def get_index_change(market: str) -> float:
    """당일 지수 등락율 (%). KR은 KIS 지수 현재가를 우선 사용하고 yfinance는 폴백."""
    market_key = str(market or "").upper()
    if market_key in {"KR", "US"}:
        try:
            index = "KOSPI" if market_key == "KR" else "SP500"
            return float(get_index_snapshot(market_key, index).get("change_pct", 0.0) or 0.0)
        except Exception as exc:
            log.debug(f"{market_key} index quote fallback to yfinance: {exc}")
    try:
        import yfinance as yf
        symbol = "^KS11" if market_key == "KR" else "^GSPC"
        df = yf.Ticker(symbol).history(period="2d")
        if len(df) < 2:
            return 0.0
        prev = float(df["Close"].iloc[-2])
        last = float(df["Close"].iloc[-1])
        return (last - prev) / prev * 100 if prev else 0.0
    except Exception:
        return 0.0


def _av_get(params: dict, timeout: int = 15) -> dict:
    """
    Alpha Vantage GET 헬퍼 — KEY_1 한도 소진 시 KEY_2로 자동 전환.
    Information/Note 감지 시 RuntimeError 발생 (한도 초과).
    """
    import logging as _log
    _logger = _log.getLogger("trading_system")
    keys = [k for k in (AV_KEY, AV_KEY_2) if k]
    if not keys:
        raise RuntimeError("ALPHA_VANTAGE_KEY 없음")
    for i, key in enumerate(keys, 1):
        p = {**params, "apikey": key}
        resp = requests.get("https://www.alphavantage.co/query", params=p, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if "Information" in data or "Note" in data:
            msg = (data.get("Information") or data.get("Note", ""))[:80]
            _logger.warning(f"[AV KEY-{i} 한도 초과] {msg}")
            continue   # 다음 키로 시도
        return data
    raise RuntimeError("AV 모든 키 한도 초과")


def get_usd_krw() -> float:
    """실시간 USD/KRW 환율 조회 (yfinance → .env 기본값)"""
    # 1차: yfinance (무료, 실시간)
    try:
        import yfinance as yf
        rate = yf.Ticker("USDKRW=X").fast_info.last_price
        if rate and rate > 100:
            return round(float(rate), 2)
    except Exception:
        pass

    # 2차: .env 기본값
    return float(os.getenv("USD_KRW_RATE", "1350"))


def _to_float(value, default=0.0) -> float:
    try:
        if value in (None, ""):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _first_record(value):
    if isinstance(value, list):
        return value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _check_token_expiry(resp) -> None:
    """500 응답에서 EGW00123(토큰 만료)을 먼저 확인 후 raise_for_status 호출."""
    if resp.status_code == 500:
        try:
            body = resp.json()
            if body.get("msg_cd") == "EGW00123":
                raise KISTokenExpiredError(
                    f"KIS 토큰 만료(EGW00123): {body.get('msg1', '')} — get_access_token(force_refresh=True) 로 갱신 필요"
                )
        except (ValueError, KeyError):
            pass
    resp.raise_for_status()


def _require_kis_success(data: dict, label: str):
    if data.get("rt_cd") != "0":
        if data.get("msg_cd") == "EGW00123":
            raise KISTokenExpiredError(
                f"KIS 토큰 만료(EGW00123): {data.get('msg1', '')} — get_access_token(force_refresh=True) 로 갱신 필요"
            )
        raise RuntimeError(f"{label} 실패: {data.get('msg1') or data.get('msg_cd') or '응답 오류'}")


def _get_us_cash_snapshot(token: str) -> dict:
    """해외주식 외화예수금/주문가능금액 조회.

    실거래에서는 inquire-balance의 현금 필드가 충분하지 않아
    foreign-margin 기준 외화예수금과 일반 주문가능금액을 함께 읽는다.
    """
    if IS_PAPER_US:
        return {"cash": 0.0, "orderable_cash": 0.0, "currency": "USD"}

    profile = get_kis_market_profile("US")
    acnt_no, acnt_prdt = profile.account_no.split("-")
    def _fetch():
        resp = _kis_get(
            f"{profile.base_url}/uapi/overseas-stock/v1/trading/foreign-margin",
            headers=_headers(token, "TTTC2101R", market="US"),
            params={
                "CANO": acnt_no,
                "ACNT_PRDT_CD": acnt_prdt,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _require_kis_success(data, "해외외화예수금 조회")
        return data

    data = _retry_kis("US foreign cash", _fetch, retries=4, delay_sec=1.2)

    usd_rows = [
        row
        for row in (data.get("output", []) or [])
        if str(row.get("crcy_cd", "") or "").upper() == "USD"
    ]

    preferred_rows = [
        row for row in usd_rows
        if str(row.get("natn_name", "") or "").strip() == "미국"
    ]

    def _row_score(row: dict) -> tuple[float, float]:
        return (
            _to_float(row.get("frcr_gnrl_ord_psbl_amt"), 0.0),
            _to_float(row.get("frcr_dncl_amt1"), 0.0),
        )

    pool = preferred_rows or usd_rows
    usd_row = max(pool, key=_row_score) if pool else {}
    cash = _to_float(usd_row.get("frcr_dncl_amt1"), 0.0)
    orderable = _to_float(usd_row.get("frcr_gnrl_ord_psbl_amt"), cash)
    # For US accounts, settled foreign cash can be lower than the account value
    # while same-day unsettled sell proceeds are already reflected in orderable.
    asset_cash = max(cash, orderable)
    return {
        "cash": round(cash, 6),
        "orderable_cash": round(orderable, 6),
        "asset_cash": round(asset_cash, 6),
        "currency": "USD",
    }


def _get_balance_us_present_fallback(token: str) -> dict:
    """해외 체결기준현재잔고 폴백.

    inquire-balance가 실전에서 간헐적으로 500을 반환할 때 사용한다.
    """
    data = _get_us_present_balance_data(token)

    stocks = []
    for row in data.get("output1", []) or []:
        qty = int(_to_float(row.get("cblc_qty13"), 0))
        if qty <= 0:
            continue
        avg_price = _to_float(row.get("avg_unpr3"), 0)
        eval_price = _to_float(row.get("ovrs_now_pric1"), avg_price)
        eval_profit = _to_float(row.get("evlu_pfls_amt2"), 0)
        stocks.append(
            {
                "ticker": str(row.get("pdno", "") or "").upper(),
                "name": str(row.get("prdt_name", "") or row.get("ovrs_item_name", "") or "").strip(),
                "qty": qty,
                "avg_price": avg_price,
                "eval_price": eval_price,
                "eval_profit": eval_profit,
                "profit_rate": _to_float(row.get("evlu_pfls_rt1"), 0),
            }
        )

    cash_snapshot = _get_us_cash_snapshot(token)
    summary = _first_record(data.get("output2", {}))
    total_eval_usd = sum(s["qty"] * s["eval_price"] for s in stocks)
    total_profit = sum(_to_float(s.get("eval_profit"), 0) for s in stocks)
    if summary:
        total_eval_usd = _to_float(summary.get("frcr_evlu_amt2"), total_eval_usd)

    total_cost_usd = sum(s["qty"] * s["avg_price"] for s in stocks)
    profit_rate = (total_profit / total_cost_usd * 100.0) if total_cost_usd > 0 else 0.0
    result = {
        "stocks": stocks,
        "total_eval": round(total_eval_usd, 2),
        "cash": float(cash_snapshot.get("cash", 0.0) or 0.0),
        "orderable_cash": float(cash_snapshot.get("orderable_cash", 0.0) or 0.0),
        "asset_cash": float(cash_snapshot.get("asset_cash", cash_snapshot.get("cash", 0.0)) or 0.0),
        "total_profit": round(total_profit, 2),
        "profit_rate": round(profit_rate, 4),
        "currency": "USD",
    }
    result.update(_us_present_krw_fields(data))
    return result


def _get_us_present_balance_data(token: str) -> dict:
    profile = get_kis_market_profile("US")
    acnt_no, acnt_prdt = profile.account_no.split("-")
    tr_id = "VTRP6504R" if IS_PAPER_US else "CTRP6504R"

    def _fetch():
        resp = _kis_get(
            f"{profile.base_url}/uapi/overseas-stock/v1/trading/inquire-present-balance",
            headers=_headers(token, tr_id, market="US"),
            params={
                "CANO": acnt_no,
                "ACNT_PRDT_CD": acnt_prdt,
                "WCRC_FRCR_DVSN_CD": "02",
                "NATN_CD": "840",
                "TR_MKET_CD": "00",
                "INQR_DVSN_CD": "00",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _require_kis_success(data, "해외현재잔고 조회")
        return data

    return _retry_kis("US present balance", _fetch, retries=4, delay_sec=1.2)


def _us_present_krw_fields(data: dict) -> dict:
    summary2 = _first_record(data.get("output2", {}))
    summary3 = _first_record(data.get("output3", {}))
    if not summary3:
        return {}

    total_asset_krw = _to_float(summary3.get("tot_asst_amt"), 0.0)
    domestic_cash_krw = _to_float(summary3.get("tot_dncl_amt"), 0.0)
    cma_eval_krw = _to_float(summary3.get("cma_evlu_amt"), 0.0)
    eval_krw = _to_float(summary3.get("evlu_amt_smtl_amt"), 0.0)
    profit_krw = _to_float(summary3.get("tot_evlu_pfls_amt"), 0.0)
    purchase_krw = _to_float(summary3.get("pchs_amt_smtl_amt"), 0.0)
    market_asset_krw = max(total_asset_krw - domestic_cash_krw - cma_eval_krw, 0.0) if total_asset_krw > 0 else 0.0
    asset_cash_krw = max(market_asset_krw - eval_krw, 0.0) if market_asset_krw > 0 else 0.0
    exchange_rate = _to_float(summary2.get("frst_bltn_exrt"), 0.0)

    return {
        "kis_total_asset_krw": round(total_asset_krw, 6),
        "kis_domestic_cash_krw": round(domestic_cash_krw, 6),
        "market_asset_krw": round(market_asset_krw, 6),
        "asset_cash_krw": round(asset_cash_krw, 6),
        "total_eval_krw": round(eval_krw, 6),
        "purchase_amount_krw": round(purchase_krw, 6),
        "total_profit_krw": round(profit_krw, 6),
        "kis_exchange_rate": round(exchange_rate, 6),
    }


def _get_balance_us(token, force_refresh: bool = False) -> dict:
    """해외주식 잔고 조회 (v1_해외주식-006, TR: VTTS3012R/TTTS3012R)
    NASD로 조회하면 모의투자에서 미국 전체 잔고 반환.
    외화(USD) 기준 → KRW 환산은 호출자가 처리.
    """
    profile = get_kis_market_profile("US")
    acnt_no, acnt_prdt = profile.account_no.split("-")
    tr_id = "VTTS3012R" if IS_PAPER_US else "TTTS3012R"
    cache_key = ("US",)

    def _fetch():
        resp = _kis_get(
            f"{profile.base_url}/uapi/overseas-stock/v1/trading/inquire-balance",
            headers=_headers(token, tr_id, market="US"),
            params={
                "CANO":           acnt_no,
                "ACNT_PRDT_CD":   acnt_prdt,
                "OVRS_EXCG_CD":   "NASD",   # 모의: NASD/NYSE/AMEX 중 하나, 실전: NASD=미국전체
                "TR_CRCY_CD":     "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _require_kis_success(data, "해외잔고 조회")

        stocks = [
            {
                "ticker":       s["ovrs_pdno"],
                "name":         s.get("ovrs_item_name", ""),
                "qty":          int(float(s.get("ovrs_cblc_qty", 0))),
                "avg_price":    _to_float(s.get("pchs_avg_pric", 0)),
                "eval_price":   _to_float(s.get("now_pric2", 0)),
                "eval_profit":  _to_float(s.get("frcr_evlu_pfls_amt", 0)),
                "profit_rate":  _to_float(s.get("evlu_pfls_rt", 0)),
            }
            for s in data.get("output1", [])
            if int(float(s.get("ovrs_cblc_qty", 0))) > 0
        ]

        s2 = _first_record(data.get("output2", {}))
        total_eval_usd = sum(s["qty"] * s["eval_price"] for s in stocks)
        total_cost_usd = sum(s["qty"] * s["avg_price"] for s in stocks)
        total_profit = _to_float(s2.get("ovrs_tot_pfls"), sum(s["eval_profit"] for s in stocks))
        profit_rate = _to_float(
            s2.get("tot_pftrt"),
            (total_profit / total_cost_usd * 100.0) if total_cost_usd > 0 else 0.0,
        )

        cash_snapshot = _get_us_cash_snapshot(token)
        present_fields = {}
        try:
            present_fields = _us_present_krw_fields(_get_us_present_balance_data(token))
        except Exception as present_error:
            log.debug(f"KIS US 현재잔고 원화요약 조회 실패: {present_error}")

        result = {
            "stocks":       stocks,
            "total_eval":   round(total_eval_usd, 2),
            "cash":         float(cash_snapshot.get("cash", 0.0) or 0.0),
            "orderable_cash": float(cash_snapshot.get("orderable_cash", 0.0) or 0.0),
            "asset_cash":   float(cash_snapshot.get("asset_cash", cash_snapshot.get("cash", 0.0)) or 0.0),
            "total_profit": round(total_profit, 2),
            "profit_rate":  profit_rate,
            "currency":     "USD",
        }
        result.update(present_fields)
        return result

    if force_refresh:
        _cache_invalidate(_BALANCE_CACHE, cache_key)

    try:
        return _cache_set(_BALANCE_CACHE, cache_key, _retry_kis("US balance", _fetch, retries=4, delay_sec=1.2))
    except Exception as primary_error:
        try:
            fallback = _get_balance_us_present_fallback(token)
            log.warning(f"KIS US 잔고 현재잔고 폴백 사용: {primary_error}")
            return _cache_set(_BALANCE_CACHE, cache_key, fallback)
        except Exception:
            cached = _cache_get(_BALANCE_CACHE, cache_key)
            if cached is not None:
                _log_cache_use_throttled("balance_us", "KIS US 잔고 캐시 사용")
                return cached
            raise primary_error


def get_balance(token, market="KR", force_refresh: bool = False):
    if market == "US":
        return _get_balance_us(token, force_refresh=force_refresh)

    acnt_no, acnt_prdt = ACCOUNT_NO.split("-")
    tr_id = "VTTC8434R" if IS_PAPER else "TTTC8434R"
    cache_key = ("KR",)

    def _fetch():
        resp = _kis_get(
            f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=_headers(token, tr_id),
            params={
                "CANO": acnt_no,
                "ACNT_PRDT_CD": acnt_prdt,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "PDNO": "",
                "ORD_UNPR": "",
                "ORD_DVSN": "",
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            timeout=15,
        )
        _check_token_expiry(resp)
        data = resp.json()
        _require_kis_success(data, "국내잔고 조회")

        if "output1" in data or "output2" in data:
            stocks = [
                {
                    "ticker": s["pdno"],
                    "name": s["prdt_name"],
                    "qty": int(s["hldg_qty"]),
                    "avg_price": int(float(s["pchs_avg_pric"])),
                    "eval_price": int(s["prpr"]),
                    "eval_profit": int(s["evlu_pfls_amt"]),
                }
                for s in data.get("output1", [])
                if int(s.get("hldg_qty", 0)) > 0
            ]
            s2 = _first_record(data.get("output2", {}))
            return {
                "stocks": stocks,
                "total_eval": int(s2.get("scts_evlu_amt", 0)),
                "cash": int(s2.get("dnca_tot_amt", 0)),
                "total_profit": int(s2.get("evlu_pfls_smtl_amt", 0)),
                "profit_rate": float(s2.get("asst_icdc_erng_rt", 0)),
            }

        out = data.get("output", {})
        cash = int(out.get("ord_psbl_cash", out.get("nrcvb_buy_amt", 0)))
        return {
            "stocks": [],
            "total_eval": 0,
            "cash": cash,
            "total_profit": 0,
            "profit_rate": 0.0,
        }

    if force_refresh:
        _cache_invalidate(_BALANCE_CACHE, cache_key)

    try:
        return _cache_set(_BALANCE_CACHE, cache_key, _retry_kis("KR balance", _fetch))
    except Exception:
        cached = _cache_get(_BALANCE_CACHE, cache_key)
        if cached is not None:
            _log_cache_use_throttled("balance_kr", "KIS KR 잔고 캐시 사용")
            return cached
        raise


def _normalize_kr_daily_ccld_row(row: dict) -> dict:
    order_no = str(_pick_first(row, ["odno", "ODNO", "ord_no"], "") or "").strip()
    ticker = str(_pick_first(row, ["pdno", "PDNO", "shtn_pdno"], "") or "").strip()
    side_code = str(_pick_first(row, ["sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD"], "") or "").strip()
    side = {"01": "sell", "02": "buy"}.get(side_code, "")
    filled_qty = int(_to_float(_pick_first(row, [
        "tot_ccld_qty", "ccld_qty", "tot_ccld_qty_sum", "ft_ccld_qty", "exec_qty"
    ]), 0))
    order_qty = int(_to_float(_pick_first(row, ["ord_qty", "ORD_QTY"]), 0))
    fill_price = _to_float(_pick_first(row, [
        "avg_prvs", "avg_cntr_prc", "avg_ccld_unpr", "tot_ccld_unpr", "ccld_unpr", "ord_unpr"
    ]), 0)
    return {
        "order_no": order_no,
        "ticker": ticker,
        "side": side,
        "filled_qty": filled_qty,
        "order_qty": order_qty,
        "fill_price": fill_price,
        "order_time": str(_pick_first(row, ["ord_tmd", "ORD_TMD"], "") or "").strip(),
        "raw": row,
    }


def inquire_daily_ccld_kr(token: str,
                          start_date: str = None,
                          end_date: str = None,
                          order_no: str = "",
                          ticker: str = "",
                          side_code: str = "00",
                          filled_code: str = "00") -> list[dict]:
    acnt_no, acnt_prdt = ACCOUNT_NO.split("-")
    if start_date is None:
        start_date = datetime.now().strftime("%Y%m%d")
    if end_date is None:
        end_date = start_date

    # 공식 examples_user 기준 최신 TR ID를 먼저 사용하고, 레거시 샘플 TR ID로 한 번 더 시도한다.
    tr_ids = (
        ("VTTC0081R", "VTTC8001R")
        if IS_PAPER
        else ("TTTC0081R", "TTTC8001R")
    )

    params = {
        "CANO": acnt_no,
        "ACNT_PRDT_CD": acnt_prdt,
        "INQR_STRT_DT": start_date,
        "INQR_END_DT": end_date,
        "SLL_BUY_DVSN_CD": side_code,
        "INQR_DVSN": "00",
        "PDNO": ticker,
        "CCLD_DVSN": filled_code,
        "ORD_GNO_BRNO": "",
        "ODNO": order_no,
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
        "EXCG_ID_DVSN_CD": "KRX",
    }

    last_error = None
    for tr_id in tr_ids:
        headers = _headers(token, tr_id)
        headers["custtype"] = "P"

        def _fetch():
            resp = _kis_get(
                f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            _require_kis_success(data, "국내 주문체결조회")
            return [_normalize_kr_daily_ccld_row(row) for row in data.get("output1", [])]

        try:
            return _retry_kis(f"KR daily ccld {tr_id}", _fetch)
        except Exception as e:
            last_error = e
            log.warning(f"[KIS] 국내 주문체결조회 TR fallback {tr_id} 실패: {e}")

    raise last_error


def get_order_fill_kr(token: str, order_no: str, ticker: str = "", trade_date: str = None) -> Optional[dict]:
    if not order_no:
        return None
    query_date = trade_date or datetime.now().strftime("%Y%m%d")
    order_no_s = str(order_no).strip()
    try:
        target_int = int(order_no_s) if order_no_s else None
    except ValueError:
        target_int = None

    # VTS에서는 체결구분("01")이 비거나, 전체("00")에만 보이는 경우가 있어 둘 다 본다.
    rows = []
    seen = set()
    for filled_code in ("01", "00"):
        try:
            fetched = inquire_daily_ccld_kr(
                token,
                start_date=query_date,
                end_date=query_date,
                order_no=order_no_s,
                ticker=ticker,
                filled_code=filled_code,
            )
        except Exception:
            fetched = []
        for row in fetched:
            key = (
                str(row.get("order_no", "")).strip(),
                str(row.get("ticker", "")).strip(),
                str(row.get("order_time", "")).strip(),
                int(row.get("filled_qty", 0) or 0),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    for row in rows:
        row_no = str(row.get("order_no", "")).strip()
        if row_no == order_no_s:
            return row
        if target_int is not None and row_no:
            try:
                if int(row_no) == target_int:
                    return row
            except ValueError:
                pass
    if ticker:
        ticker_rows = [r for r in rows if str(r.get("ticker", "")).strip() == str(ticker).strip()]
        if ticker_rows:
            ticker_rows.sort(key=lambda r: (r.get("filled_qty", 0), r.get("order_time", "")), reverse=True)
            return ticker_rows[0]
    if rows:
        rows.sort(key=lambda r: (r.get("filled_qty", 0), r.get("order_time", "")), reverse=True)
        return rows[0]
    return None


def _normalize_us_inquire_ccnl_row(row: dict) -> dict:
    order_no = str(_pick_first(row, ["odno", "ODNO", "ord_no"], "") or "").strip()
    ticker = str(_pick_first(row, ["pdno", "PDNO", "ovrs_pdno"], "") or "").strip().upper()
    side_code = str(_pick_first(row, ["sll_buy_dvsn", "SLL_BUY_DVSN", "sll_buy_dvsn_cd"], "") or "").strip()
    side = {"01": "sell", "02": "buy"}.get(side_code, "")
    filled_qty = int(_to_float(_pick_first(row, [
        "ft_ccld_qty", "ccld_qty", "tot_ccld_qty", "tot_ccld_qty_sum", "exec_qty"
    ]), 0))
    order_qty = int(_to_float(_pick_first(row, ["ord_qty", "ORD_QTY"]), 0))
    fill_price = _to_float(_pick_first(row, [
        "ft_ccld_unpr3", "ft_ccld_unpr", "avg_prvs", "avg_ccld_unpr", "ccld_unpr", "ovrs_ord_unpr"
    ]), 0)
    return {
        "order_no": order_no,
        "ticker": ticker,
        "side": side,
        "filled_qty": filled_qty,
        "order_qty": order_qty,
        "fill_price": fill_price,
        "order_time": str(_pick_first(row, ["ord_tmd", "ORD_TMD", "ord_time"], "") or "").strip(),
        "raw": row,
    }


def inquire_ccnl_us(token: str,
                    start_date: str,
                    end_date: str,
                    ticker: str = "",
                    side_code: str = "00",
                    filled_code: str = "00",
                    sort_sqn: str = "DS") -> list[dict]:
    profile = get_kis_market_profile("US")
    acnt_no, acnt_prdt = profile.account_no.split("-")
    tr_id = "VTTS3035R" if IS_PAPER_US else "TTTS3035R"
    headers = _headers(token, tr_id, market="US")
    headers["custtype"] = "P"
    pdno = "" if IS_PAPER_US else (ticker or "%")
    ovrs_excg_cd = "" if IS_PAPER_US else "%"
    sll_buy_dvsn = "00" if IS_PAPER_US else side_code
    ccld_nccs_dvsn = "00" if IS_PAPER_US else filled_code
    sort_value = "DS" if IS_PAPER_US else sort_sqn

    def _fetch():
        resp = _kis_get(
            f"{profile.base_url}/uapi/overseas-stock/v1/trading/inquire-ccnl",
            headers=headers,
            params={
                "CANO": acnt_no,
                "ACNT_PRDT_CD": acnt_prdt,
                "PDNO": pdno,
                "ORD_STRT_DT": start_date,
                "ORD_END_DT": end_date,
                "SLL_BUY_DVSN": sll_buy_dvsn,
                "CCLD_NCCS_DVSN": ccld_nccs_dvsn,
                "OVRS_EXCG_CD": ovrs_excg_cd,
                "SORT_SQN": sort_value,
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": "",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _require_kis_success(data, "해외 주문체결조회")
        return [_normalize_us_inquire_ccnl_row(row) for row in data.get("output", [])]

    return _retry_kis("US inquire ccnl", _fetch)


def get_order_fill_us(token: str, order_no: str, ticker: str = "", created_at: str = "") -> Optional[dict]:
    if not order_no:
        return None
    candidates = []
    if created_at:
        try:
            base_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            candidates.append(base_dt.strftime("%Y%m%d"))
            candidates.append((base_dt - timedelta(days=1)).strftime("%Y%m%d"))
        except Exception:
            pass
    today = datetime.now().strftime("%Y%m%d")
    for dt in [today, (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")]:
        if dt not in candidates:
            candidates.append(dt)

    seen = set()
    rows = []
    for dt in candidates:
        if dt in seen:
            continue
        seen.add(dt)
        try:
            rows.extend(inquire_ccnl_us(token, start_date=dt, end_date=dt, ticker=ticker))
        except Exception:
            continue

    order_no_s = str(order_no).strip()
    try:
        target_int = int(order_no_s) if order_no_s else None
    except ValueError:
        target_int = None
    ticker_u = ticker.upper().strip()
    exact = []
    for row in rows:
        row_no = str(row.get("order_no", "")).strip()
        if ticker_u and row.get("ticker", "").upper() != ticker_u:
            continue
        matched = row_no == order_no_s
        if not matched and target_int is not None and row_no:
            try:
                matched = int(row_no) == target_int
            except ValueError:
                pass
        if matched:
            exact.append(row)
    if exact:
        exact.sort(key=lambda r: (r.get("filled_qty", 0), r.get("order_time", "")), reverse=True)
        return exact[0]

    if ticker_u:
        ticker_rows = [r for r in rows if r.get("ticker", "").upper() == ticker_u and r.get("filled_qty", 0) > 0]
        ticker_rows.sort(key=lambda r: (r.get("filled_qty", 0), r.get("order_time", "")), reverse=True)
        if ticker_rows:
            return ticker_rows[0]
    return None


def _normalize_order_result(market: str, side: str, ticker: str, qty: int, native_price: float, data: dict) -> dict:
    output = _first_record(data.get("output", {}))
    return {
        "success": data.get("rt_cd") == "0",
        "msg": data.get("msg1", "") or data.get("msg_cd", ""),
        "order_no": str(output.get("ODNO", "") or "").strip(),
        "market": market,
        "side": side,
        "ticker": ticker,
        "qty": int(qty),
        "price": float(native_price),
        "price_type": "market" if float(native_price or 0) == 0 else "limit",
        "raw": data,
    }


def _mask_order_body(body: dict) -> dict:
    masked = dict(body or {})
    cano = str(masked.get("CANO", "") or "")
    if cano:
        masked["CANO"] = f"{cano[:2]}***{cano[-2:]}" if len(cano) >= 4 else "***"
    return masked


def _response_text(resp) -> str:
    try:
        return json.dumps(resp.json(), ensure_ascii=False)
    except Exception:
        return str(getattr(resp, "text", "") or "")[:1000]


def _raise_order_http_error(label: str, resp, body: dict) -> None:
    status_code = int(getattr(resp, "status_code", 0) or 0)
    text = _response_text(resp)
    masked_body = _mask_order_body(body)
    msg = f"{label}: HTTP {status_code}; response={text}; order_body={masked_body}"
    log.error(f"[KIS order error] {msg}")
    raise KISOrderHTTPError(msg, status_code=status_code, response_text=text, order_body=masked_body)


def _parse_kr_order_time(order_time: str, base_dt: datetime) -> Optional[datetime]:
    raw = "".join(ch for ch in str(order_time or "") if ch.isdigit())
    if len(raw) < 6:
        return None
    try:
        return datetime.strptime(base_dt.strftime("%Y%m%d") + raw[:6], "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _find_recent_order_truth_kr(
    token: str,
    *,
    ticker: str,
    side: str,
    qty: int,
    submitted_at: datetime,
) -> Optional[dict]:
    side_code = "02" if side == "buy" else "01"
    rows = inquire_daily_ccld_kr(
        token,
        start_date=submitted_at.strftime("%Y%m%d"),
        end_date=submitted_at.strftime("%Y%m%d"),
        ticker=str(ticker or "").strip(),
        side_code=side_code,
        filled_code="00",
    )
    lower_bound = submitted_at - timedelta(minutes=2)
    matches = []
    for row in rows:
        if str(row.get("ticker", "")).strip() != str(ticker or "").strip():
            continue
        if row.get("side") and str(row.get("side")) != str(side):
            continue
        row_qty = int(row.get("order_qty", 0) or 0)
        if row_qty > 0 and int(qty or 0) > 0 and row_qty != int(qty or 0):
            continue
        row_dt = _parse_kr_order_time(str(row.get("order_time", "") or ""), submitted_at)
        if row_dt is not None and row_dt < lower_bound:
            continue
        matches.append((row_dt or submitted_at, row))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], str(item[1].get("order_no", ""))), reverse=True)
    return matches[0][1]


def _parse_order_time_any(order_time: str, base_dt: datetime) -> Optional[datetime]:
    raw = "".join(ch for ch in str(order_time or "") if ch.isdigit())
    if len(raw) < 6:
        return None
    for date_dt in (base_dt, base_dt - timedelta(days=1), base_dt + timedelta(days=1)):
        try:
            return datetime.strptime(date_dt.strftime("%Y%m%d") + raw[:6], "%Y%m%d%H%M%S")
        except ValueError:
            continue
    return None


def _parse_order_time_for_query_date(order_time: str, query_date: str, fallback_dt: datetime) -> Optional[datetime]:
    raw = "".join(ch for ch in str(order_time or "") if ch.isdigit())
    if len(raw) < 6:
        return None
    try:
        return datetime.strptime(str(query_date)[:8] + raw[:6], "%Y%m%d%H%M%S")
    except ValueError:
        return _parse_order_time_any(order_time, fallback_dt)


def _find_recent_order_truth_us(
    token: str,
    *,
    ticker: str,
    side: str,
    qty: int,
    submitted_at: datetime,
) -> Optional[dict]:
    side_code = "02" if side == "buy" else "01"
    query_dates = []
    for dt in (submitted_at, submitted_at - timedelta(days=1)):
        value = dt.strftime("%Y%m%d")
        if value not in query_dates:
            query_dates.append(value)
    rows: list[tuple[str, dict]] = []
    for query_date in query_dates:
        for row in inquire_ccnl_us(
            token,
            start_date=query_date,
            end_date=query_date,
            ticker=str(ticker or "").strip().upper(),
            side_code=side_code,
            filled_code="00",
        ):
            rows.append((query_date, row))
    normalized_ticker = str(ticker or "").strip().upper()
    lower_bound = submitted_at - timedelta(minutes=2)
    matches = []
    for query_date, row in rows:
        if str(row.get("ticker", "")).strip().upper() != normalized_ticker:
            continue
        if row.get("side") and str(row.get("side")) != str(side):
            continue
        row_qty = int(row.get("order_qty", 0) or 0)
        if row_qty > 0 and int(qty or 0) > 0 and row_qty != int(qty or 0):
            continue
        row_dt = _parse_order_time_for_query_date(str(row.get("order_time", "") or ""), query_date, submitted_at)
        if row_dt is not None and row_dt < lower_bound:
            continue
        matches.append((row_dt or submitted_at, row))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], str(item[1].get("order_no", ""))), reverse=True)
    return matches[0][1]


def precheck_order(
    ticker: str,
    qty: int,
    price: float,
    side: str,
    token: str,
    market: str = "KR",
    force_refresh: bool = False,
) -> dict:
    ticker = (ticker or "").strip().upper()
    qty = int(qty or 0)
    price = float(price or 0)
    if qty <= 0:
        return {"ok": False, "reason": "invalid_qty", "msg": "주문수량이 0 이하입니다.", "allowed_qty": 0}

    if market == "KR":
        bal = get_balance(token, market="KR", force_refresh=force_refresh)
        holdings = {str(s.get("ticker", "")).strip().upper(): int(s.get("qty", 0) or 0) for s in bal.get("stocks", [])}
        if side == "sell":
            held_qty = holdings.get(ticker, 0)
            return {
                "ok": held_qty >= qty,
                "reason": "ok" if held_qty >= qty else "insufficient_holding",
                "msg": "주문 가능" if held_qty >= qty else f"보유수량 부족: {held_qty}주",
                "allowed_qty": held_qty,
                "cash": float(bal.get("cash", 0) or 0),
            }
        order_value = price * qty
        cash = float(bal.get("cash", 0) or 0)
        allowed_qty = int(cash // price) if price > 0 else qty
        return {
            "ok": price > 0 and cash >= order_value,
            "reason": "ok" if price > 0 and cash >= order_value else ("invalid_price" if price <= 0 else "insufficient_cash"),
            "msg": "주문 가능" if price > 0 and cash >= order_value else ("주문단가가 0 이하입니다." if price <= 0 else f"현금 부족: {cash:,.0f}원"),
            "allowed_qty": allowed_qty,
            "cash": cash,
        }

    bal_us = get_balance(token, market="US", force_refresh=force_refresh)
    holdings_us = {str(s.get("ticker", "")).strip().upper(): int(s.get("qty", 0) or 0) for s in bal_us.get("stocks", [])}
    if side == "sell":
        held_qty = holdings_us.get(ticker, 0)
        return {
            "ok": held_qty >= qty,
            "reason": "ok" if held_qty >= qty else "insufficient_holding",
            "msg": "주문 가능" if held_qty >= qty else f"보유수량 부족: {held_qty}주",
            "allowed_qty": held_qty,
            "cash": float(bal_us.get("cash", 0) or 0),
        }

    cash_usd = float(
        bal_us.get("orderable_cash", bal_us.get("cash", 0)) or 0
    )
    order_value_usd = price * qty
    allowed_qty = int(cash_usd // max(price, 1e-9)) if price > 0 else qty
    return {
        "ok": price > 0 and cash_usd >= order_value_usd,
        "reason": "ok" if price > 0 and cash_usd >= order_value_usd else ("invalid_price" if price <= 0 else "insufficient_cash"),
        "msg": "주문 가능" if price > 0 and cash_usd >= order_value_usd else ("주문단가가 0 이하입니다." if price <= 0 else f"달러 주문가능금액 부족: ${cash_usd:,.2f}"),
        "allowed_qty": allowed_qty,
        "cash": cash_usd,
    }


def _normalize_kr_order_inputs(qty, price) -> tuple[int, int]:
    qty_i = int(qty or 0)
    price_f = float(price or 0)
    price_i = 0 if price_f <= 0 else int(round(price_f))
    return qty_i, price_i


def _build_order_body_kr(ticker, qty, price, side) -> tuple[dict, int, int]:
    acnt_no, acnt_prdt = ACCOUNT_NO.split("-")
    qty_i, price_i = _normalize_kr_order_inputs(qty, price)
    body = {
        "CANO": acnt_no,
        "ACNT_PRDT_CD": acnt_prdt,
        "PDNO": str(ticker or "").strip(),
        "ORD_DVSN": "01" if price_i == 0 else "00",
        "ORD_QTY": str(qty_i),
        "ORD_UNPR": "0" if price_i == 0 else str(price_i),
        "EXCG_ID_DVSN_CD": "KRX",
        "SLL_TYPE": "01" if side == "sell" else "",
        "CNDT_PRIC": "",
    }
    return body, qty_i, price_i


def _submit_order_kr_once(ticker, qty, price, side, token) -> dict:
    tr_map = {
        ("buy",  True):  "VTTC0012U",   # 모의투자 매수 (신TR)
        ("sell", True):  "VTTC0011U",   # 모의투자 매도 (신TR)
        ("buy",  False): "TTTC0012U",   # 실거래 매수 (신TR)
        ("sell", False): "TTTC0011U",   # 실거래 매도 (신TR)
    }
    body, qty_i, price_i = _build_order_body_kr(ticker, qty, price, side)
    headers = _headers(token, tr_map[(side, IS_PAPER)])
    headers["custtype"] = "P"   # 개인
    headers["hashkey"] = get_hashkey(body, token)
    resp = _kis_post(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash",
        headers=headers,
        json=body,
        timeout=15,
    )
    if int(getattr(resp, "status_code", 0) or 0) >= 400:
        _raise_order_http_error(f"KR order [{side} {ticker}]", resp, body)
    r = resp.json()
    return _normalize_order_result("KR", side, ticker, qty_i, price_i, r)


def _order_result_from_kr_truth(ticker, qty, price, side, row: dict) -> dict:
    order_price = float(row.get("fill_price", 0) or price or 0)
    return {
        "success": True,
        "msg": "broker_truth_recovered_after_http_500",
        "order_no": str(row.get("order_no", "") or "").strip(),
        "market": "KR",
        "side": side,
        "ticker": ticker,
        "qty": int(row.get("order_qty", 0) or qty or 0),
        "price": float(order_price),
        "price_type": "market" if float(price or 0) == 0 else "limit",
        "raw": {"broker_truth": row},
    }


def _place_order_kr(ticker, qty, price, side, token):
    ticker = str(ticker or "").strip()
    qty_i, price_i = _normalize_kr_order_inputs(qty, price)
    submitted_at = datetime.now()
    try:
        return _submit_order_kr_once(ticker, qty_i, price_i, side, token)
    except KISOrderHTTPError as first_error:
        if first_error.status_code != 500:
            raise

        try:
            truth = _find_recent_order_truth_kr(
                token,
                ticker=ticker,
                side=side,
                qty=qty_i,
                submitted_at=submitted_at,
            )
        except Exception as truth_error:
            msg = (
                f"{first_error}; broker_truth_query_failed={truth_error}; "
                "retry_skipped=state_unknown"
            )
            raise KISOrderHTTPError(
                msg,
                status_code=first_error.status_code,
                response_text=first_error.response_text,
                order_body=first_error.order_body,
            ) from truth_error

        if truth:
            log.warning(f"[KIS KR order recovery] {side} {ticker} recovered by broker truth: {truth}")
            return _order_result_from_kr_truth(ticker, qty_i, price_i, side, truth)

        log.warning(f"[KIS KR order retry] {side} {ticker} HTTP 500 with no broker truth; retrying once")
        try:
            return _submit_order_kr_once(ticker, qty_i, price_i, side, token)
        except KISOrderHTTPError as retry_error:
            msg = f"{retry_error}; retry_after_no_broker_truth_failed"
            raise KISOrderHTTPError(
                msg,
                status_code=retry_error.status_code,
                response_text=retry_error.response_text,
                order_body=retry_error.order_body,
            ) from retry_error


# 미국 거래소 코드 매핑. 미확인 종목을 기본 NASD로 보내지 않고 명시적으로 막는다.
_US_EXCHANGE_MAP = {
    "NASD": [
        "AAPL", "ADBE", "AMD", "AMZN", "AVGO", "COST", "CRM", "CSCO", "GOOG",
        "GOOGL", "INTC", "META", "MSFT", "NFLX", "NVDA", "ORCL", "PEP", "PLTR",
        "QCOM", "QQQ", "SBUX", "SMCI", "SNOW", "TSLA", "TXN", "UBER",
        "ARM", "BRZE", "CORT", "PAYS", "SRPT",
    ],
    "NYSE": ["BRK.B","JPM","BAC","WFC","GS","MS","C","USB","BLK","AXP",
             "XOM","CVX","COP","SLB","WMT","HD","MCD","NKE","PG","KO",
             "PFE","JNJ","MRK","ABT","UNH","V","MA","HIMS",
             "LLY","ABBV","CAT","GE","NOK"],
    "AMEX": ["SPY","IWM","GLD","SLV","USO"],
}


def _submit_order_us_once(ticker, qty, price, side, token) -> dict:
    profile = get_kis_market_profile("US")
    acnt_no, acnt_prdt = profile.account_no.split("-")
    qty_i = int(qty or 0)
    price_f = float(price or 0)
    tr_map = {
        ("buy",  True):  "VTTT1002U",
        ("sell", True):  "VTTT1001U",
        ("buy",  False): "TTTT1002U",
        ("sell", False): "TTTT1006U",
    }
    body = {
        "CANO":            acnt_no,
        "ACNT_PRDT_CD":    acnt_prdt,
        "OVRS_EXCG_CD":    _get_ovrs_excg_cd(ticker, token=token),
        "PDNO":            ticker.upper(),
        "ORD_QTY":         str(qty_i),
        "OVRS_ORD_UNPR":   f"{price_f:.2f}",
        "CTAC_TLNO":       "",
        "MGCO_APTM_ODNO":  "",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN":        "00",   # 모의투자는 지정가(00)만 가능
        "SLL_TYPE":        "" if side == "buy" else "00",
    }
    headers = _headers(token, tr_map[(side, IS_PAPER_US)], market="US")
    headers["custtype"] = "P"
    headers["hashkey"]  = get_hashkey(body, token, market="US")
    resp = _kis_post(
        f"{profile.base_url}/uapi/overseas-stock/v1/trading/order",
        headers=headers,
        json=body,
        timeout=15,
    )
    if int(getattr(resp, "status_code", 0) or 0) >= 400:
        _raise_order_http_error(f"US order [{side} {ticker}]", resp, body)
    r = resp.json()
    return _normalize_order_result("US", side, ticker, qty_i, price_f, r)


def _order_result_from_us_truth(ticker, qty, price, side, row: dict) -> dict:
    order_price = float(row.get("fill_price", 0) or price or 0)
    return {
        "success": True,
        "msg": "broker_truth_recovered_after_http_500",
        "order_no": str(row.get("order_no", "") or "").strip(),
        "market": "US",
        "side": side,
        "ticker": str(ticker or "").strip().upper(),
        "qty": int(row.get("order_qty", 0) or qty or 0),
        "price": float(order_price),
        "price_type": "market" if float(price or 0) == 0 else "limit",
        "raw": {"broker_truth": row},
    }


def _place_order_us(ticker, qty, price, side, token):
    ticker_u = str(ticker or "").strip().upper()
    qty_i = int(qty or 0)
    price_f = float(price or 0)
    submitted_at = datetime.now()
    try:
        return _submit_order_us_once(ticker_u, qty_i, price_f, side, token)
    except KISOrderHTTPError as first_error:
        if first_error.status_code != 500:
            raise

        try:
            truth = _find_recent_order_truth_us(
                token,
                ticker=ticker_u,
                side=side,
                qty=qty_i,
                submitted_at=submitted_at,
            )
        except Exception as truth_error:
            msg = (
                f"{first_error}; broker_truth_query_failed={truth_error}; "
                "retry_skipped=state_unknown"
            )
            raise KISOrderHTTPError(
                msg,
                status_code=first_error.status_code,
                response_text=first_error.response_text,
                order_body=first_error.order_body,
            ) from truth_error

        if truth:
            log.warning(f"[KIS US order recovery] {side} {ticker_u} recovered by broker truth: {truth}")
            return _order_result_from_us_truth(ticker_u, qty_i, price_f, side, truth)

        log.warning(f"[KIS US order retry] {side} {ticker_u} HTTP 500 with no broker truth; retrying once")
        try:
            return _submit_order_us_once(ticker_u, qty_i, price_f, side, token)
        except KISOrderHTTPError as retry_error:
            msg = f"{retry_error}; retry_after_no_broker_truth_failed"
            raise KISOrderHTTPError(
                msg,
                status_code=retry_error.status_code,
                response_text=retry_error.response_text,
                order_body=retry_error.order_body,
            ) from retry_error


def place_order(ticker, qty, price, side, token, market="KR"):
    if market == "US":
        return _place_order_us(ticker, qty, price, side, token)
    return _place_order_kr(ticker, qty, price, side, token)


def _cancel_order_kr(ticker, order_no, qty, token, price=0) -> dict:
    acnt_no, acnt_prdt = ACCOUNT_NO.split("-")
    qty_i = int(qty or 0)
    _, price_i = _normalize_kr_order_inputs(qty_i, price)
    body = {
        "CANO": acnt_no,
        "ACNT_PRDT_CD": acnt_prdt,
        "KRX_FWDG_ORD_ORGNO": "",
        "ORGN_ODNO": str(order_no or "").strip(),
        "ORD_DVSN": "00",
        "RVSE_CNCL_DVSN_CD": "02",
        "ORD_QTY": str(qty_i),
        "ORD_UNPR": "0" if price_i == 0 else str(price_i),
        "QTY_ALL_ORD_YN": "Y",
        "EXCG_ID_DVSN_CD": "KRX",
        "CNDT_PRIC": "",
    }
    headers = _headers(token, "VTTC0013U" if IS_PAPER else "TTTC0013U")
    headers["custtype"] = "P"
    headers["hashkey"] = get_hashkey(body, token)
    resp = _kis_post(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-rvsecncl",
        headers=headers,
        json=body,
        timeout=15,
    )
    if int(getattr(resp, "status_code", 0) or 0) >= 400:
        _raise_order_http_error(f"KR order cancel [{ticker} {order_no}]", resp, body)
    data = resp.json()
    _require_kis_success(data, f"KR order cancel [{ticker} {order_no}]")
    output = _first_record(data.get("output", {}))
    return {
        "success": True,
        "msg": data.get("msg1", "") or data.get("msg_cd", ""),
        "order_no": str(output.get("ODNO", "") or "").strip(),
        "original_order_no": str(order_no or "").strip(),
        "market": "KR",
        "ticker": str(ticker or "").strip(),
        "qty": qty_i,
        "raw": data,
    }


def _cancel_order_us(ticker, order_no, qty, token, price=0) -> dict:
    profile = get_kis_market_profile("US")
    acnt_no, acnt_prdt = profile.account_no.split("-")
    ticker_u = str(ticker or "").strip().upper()
    qty_i = int(qty or 0)
    price_f = float(price or 0)
    body = {
        "CANO": acnt_no,
        "ACNT_PRDT_CD": acnt_prdt,
        "OVRS_EXCG_CD": _get_ovrs_excg_cd(ticker_u, token=token),
        "PDNO": ticker_u,
        "ORGN_ODNO": str(order_no or "").strip(),
        "RVSE_CNCL_DVSN_CD": "02",
        "ORD_QTY": str(qty_i),
        "OVRS_ORD_UNPR": f"{price_f:.2f}" if price_f > 0 else "0",
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
    }
    headers = _headers(token, "VTTT1004U" if IS_PAPER_US else "TTTT1004U", market="US")
    headers["custtype"] = "P"
    headers["hashkey"] = get_hashkey(body, token, market="US")
    resp = _kis_post(
        f"{profile.base_url}/uapi/overseas-stock/v1/trading/order-rvsecncl",
        headers=headers,
        json=body,
        timeout=15,
    )
    if int(getattr(resp, "status_code", 0) or 0) >= 400:
        _raise_order_http_error(f"US order cancel [{ticker_u} {order_no}]", resp, body)
    data = resp.json()
    _require_kis_success(data, f"US order cancel [{ticker_u} {order_no}]")
    output = _first_record(data.get("output", {}))
    return {
        "success": True,
        "msg": data.get("msg1", "") or data.get("msg_cd", ""),
        "order_no": str(output.get("ODNO", "") or "").strip(),
        "original_order_no": str(order_no or "").strip(),
        "market": "US",
        "ticker": ticker_u,
        "qty": qty_i,
        "raw": data,
    }


def cancel_order(ticker, order_no, qty, token, market="KR", price=0):
    if market == "US":
        return _cancel_order_us(ticker, order_no, qty, token, price=price)
    return _cancel_order_kr(ticker, order_no, qty, token, price=price)


# ── 시장 스크리너 ──────────────────────────────────────────────────────────────

_US_FALLBACK_UNIVERSE = [
    # Core 5
    "NVDA", "TSLA", "AAPL", "GOOGL", "NFLX",
    # Tier 2 섹터 후보
    "JPM", "GS", "XOM", "CVX", "LLY", "ABBV", "CAT", "GE",
    # 보조
    "META", "AMZN", "MSFT", "AMD",
]

# 개장 전 거래량 부족 시 KR 블루칩 폴백
_KR_FALLBACK_UNIVERSE = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "005380",  # 현대차
    "051910",  # LG화학
    "035720",  # 카카오
    "000270",  # 기아
    "068270",  # 셀트리온
    "105560",  # KB금융
    "055550",  # 신한지주
    "006400",  # 삼성SDI
    "003550",  # LG
    "028260",  # 삼성물산
    "012330",  # 현대모비스
    "066570",  # LG전자
    "207940",  # 삼성바이오로직스
    "012450",  # 한화에어로스페이스
    "003490",  # 대한항공
    "096770",  # SK이노베이션
    "034730",  # SK
]

# KR 장전 스크리닝 캐시 경로 (장중 정상 결과를 저장, 다음 날 장전에 재사용)
_KR_SCREEN_CACHE_PATH = get_runtime_path("state", "kr_screen_cache.json")


def save_kr_screen_cache(candidates: list) -> None:
    """장중 유효한 KR 스크리닝 결과를 캐시에 저장 (session_close 또는 장중 정상 스크리닝 시 호출)"""
    import logging as _log
    try:
        _KR_SCREEN_CACHE_PATH.write_text(
            json.dumps({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "cached_at": __import__("time").time(),
                "candidates": candidates,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        _log.getLogger("trading_system").debug(
            f"[KR 스크리너 캐시] 저장 완료 ({len(candidates)}종목)"
        )
    except Exception as e:
        _log.getLogger("trading_system").warning(f"[KR 스크리너 캐시] 저장 실패: {e}")


def _load_kr_screen_cache() -> list:
    """전일 KR 스크리닝 캐시 로드. 오늘 또는 어제 날짜 파일만 유효."""
    try:
        if not _KR_SCREEN_CACHE_PATH.exists():
            return []
        cached = json.loads(_KR_SCREEN_CACHE_PATH.read_text(encoding="utf-8"))
        from datetime import timedelta
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if cached.get("date") not in (today, yesterday):
            return []
        return cached.get("candidates", [])
    except Exception:
        return []


def _screen_num(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value)
    except Exception:
        return default


def _kr_screen_score(candidate: dict) -> float:
    price = max(0.0, _screen_num(candidate.get("price"), 0.0))
    volume = max(0.0, _screen_num(candidate.get("volume"), 0.0))
    turnover = price * volume
    change_rate = abs(_screen_num(candidate.get("change_rate"), 0.0))
    vol_ratio = max(0.0, _screen_num(candidate.get("vol_ratio"), 0.0))
    return math.log1p(turnover) + (change_rate * 2.0) + (vol_ratio * 4.0)


def _kr_screen_brief(candidate: dict) -> dict:
    row = {
        "ticker": str(candidate.get("ticker", "") or ""),
        "name": str(candidate.get("name", "") or ""),
        "market_type": str(candidate.get("market_type", "") or ""),
        "price": _screen_num(candidate.get("price"), 0.0),
        "change_rate": _screen_num(candidate.get("change_rate"), 0.0),
        "volume": int(_screen_num(candidate.get("volume"), 0.0)),
        "vol_ratio": _screen_num(candidate.get("vol_ratio"), 0.0),
    }
    if "screen_score" in candidate:
        row["screen_score"] = round(_screen_num(candidate.get("screen_score"), 0.0), 4)
    return row


def _kr_screen_reserve_limit(top_n: int) -> int:
    limit = max(1, int(top_n or 1))
    override = os.getenv("KR_SCREEN_RESERVE_LIMIT")
    if override:
        try:
            return max(limit, int(override))
        except ValueError:
            pass
    return max(limit, min(limit * 2, 80))


def _kr_screen_kosdaq_min_ratio() -> float:
    default = 0.35
    raw = str(os.getenv("KR_SCREEN_KOSDAQ_MIN_RATIO", "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw.replace("%", ""))
    except ValueError:
        log.warning(f"[KR screener] invalid KR_SCREEN_KOSDAQ_MIN_RATIO={raw!r}; fallback={default:.2f}")
        return default
    if value > 1.0:
        value = value / 100.0
    if value < 0.0 or value > 0.8:
        clamped = max(0.0, min(0.8, value))
        log.warning(
            f"[KR screener] KR_SCREEN_KOSDAQ_MIN_RATIO={raw!r} out of range; "
            f"applied={clamped:.2f}"
        )
        return clamped
    return value


def _merge_kr_market_buckets(kospi: list[dict], kosdaq: list[dict], top_n: int) -> list[dict]:
    """Merge KOSPI/KOSDAQ screen buckets without letting one board crowd out the other."""
    limit = max(1, int(top_n or 1))
    kosdaq_min_ratio = _kr_screen_kosdaq_min_ratio()
    forced_kosdaq_min = os.getenv("KR_SCREEN_KOSDAQ_MIN")
    if forced_kosdaq_min:
        try:
            kosdaq_min = max(0, int(forced_kosdaq_min))
        except ValueError:
            log.warning(f"[KR screener] invalid KR_SCREEN_KOSDAQ_MIN={forced_kosdaq_min!r}; using ratio")
            kosdaq_min = int(round(limit * kosdaq_min_ratio))
    else:
        kosdaq_min = int(round(limit * kosdaq_min_ratio))

    def _prepared(rows: list[dict], board: str) -> list[dict]:
        out = []
        for row in rows or []:
            ticker = str(row.get("ticker", "") or "").strip()
            if not ticker:
                continue
            item = dict(row)
            item["market_type"] = str(item.get("market_type") or board).upper()
            item["screen_score"] = _kr_screen_score(item)
            out.append(item)
        out.sort(key=lambda c: c.get("screen_score", 0.0), reverse=True)
        return out

    kq_sorted = _prepared(kosdaq, "KOSDAQ")
    kp_sorted = _prepared(kospi, "KOSPI")
    selected: list[dict] = []
    seen: set[str] = set()

    def _add(row: dict) -> None:
        ticker = str(row.get("ticker", "") or "").strip()
        if ticker and ticker not in seen and len(selected) < limit:
            selected.append(row)
            seen.add(ticker)

    for row in kq_sorted[: min(kosdaq_min, len(kq_sorted))]:
        _add(row)

    combined = sorted(kp_sorted + kq_sorted, key=lambda c: c.get("screen_score", 0.0), reverse=True)
    for row in combined:
        _add(row)

    selected.sort(key=lambda c: c.get("screen_score", 0.0), reverse=True)
    log.info(
        f"[KR screener] kosdaq_min_ratio={kosdaq_min_ratio:.2f} "
        f"kosdaq_min={kosdaq_min} limit={limit} kosdaq_pool={len(kq_sorted)} kospi_pool={len(kp_sorted)}"
    )
    return selected[:limit]


def _cap_kr_screen_candidates(candidates: list[dict], top_n: int) -> list[dict]:
    kospi: list[dict] = []
    kosdaq: list[dict] = []
    for candidate in candidates or []:
        market_type = str(candidate.get("market_type") or "").upper()
        if market_type == "KOSDAQ":
            kosdaq.append(candidate)
        else:
            kospi.append(candidate)
    return _merge_kr_market_buckets(kospi, kosdaq, top_n)


def _save_kr_screen_audit(
    phase: str,
    mode: str,
    top_n: int,
    vol_cnt: str,
    buckets: dict[str, list[dict]],
    merged: list[dict],
    filtered: Optional[list[dict]] = None,
    final: Optional[list[dict]] = None,
) -> None:
    try:
        now = datetime.now()
        path = get_runtime_path("logs", "screener", f"{now.strftime('%Y%m%d')}_KR_screen.jsonl")
        payload = {
            "ts": now.isoformat(timespec="seconds"),
            "phase": phase,
            "mode": mode,
            "top_n": top_n,
            "vol_cnt": vol_cnt,
            "counts": {
                **{key: len(value or []) for key, value in (buckets or {}).items()},
                "merged": len(merged or []),
                "filtered": len(filtered or []) if filtered is not None else None,
                "final": len(final or []) if final is not None else None,
            },
            "tickers": {
                **{key: [str(c.get("ticker", "")) for c in (value or [])] for key, value in (buckets or {}).items()},
                "merged": [str(c.get("ticker", "")) for c in (merged or [])],
                "filtered": [str(c.get("ticker", "")) for c in (filtered or [])] if filtered is not None else None,
                "final": [str(c.get("ticker", "")) for c in (final or [])] if final is not None else None,
            },
            "candidates": {
                key: [_kr_screen_brief(c) for c in (value or [])[:120]]
                for key, value in (buckets or {}).items()
            },
            "merged_candidates": [_kr_screen_brief(c) for c in (merged or [])[:120]],
            "filtered_candidates": (
                [_kr_screen_brief(c) for c in (filtered or [])[:120]]
                if filtered is not None else None
            ),
            "final_candidates": (
                [_kr_screen_brief(c) for c in (final or [])[:120]]
                if final is not None else None
            ),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:
        logging.getLogger("trading_system").debug(f"[KR screener audit] save failed: {exc}")


def _kis_volume_rank(
    token: str,
    vol_cnt: str,
    top_n: int,
    market_div: str = "J",
    input_iscd: str = "0000",
) -> list:
    """KIS 거래량순위 API 호출 공통 함수.
    market_div: "J"=KRX, "NX"=NXT, "UN"=통합
    input_iscd: "0000"=전체, "0001"=KOSPI, "1001"=KOSDAQ
    """
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
    params = {
        "FID_COND_MRKT_DIV_CODE": market_div,
        "FID_COND_SCR_DIV_CODE":  "20171",
        "FID_INPUT_ISCD":         input_iscd,
        "FID_DIV_CLS_CODE":       "0",
        "FID_BLNG_CLS_CODE":      "0",
        "FID_TRGT_CLS_CODE":      "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1":      "",
        "FID_INPUT_PRICE_2":      "",
        "FID_VOL_CNT":            vol_cnt,
        "FID_INPUT_DATE_1":       "",
    }
    resp = _kis_get(
        url,
        headers=_headers(token, "FHPST01710000"),
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("output", [])
    if input_iscd == "1001":
        _mkt_type = "KOSDAQ"
    elif input_iscd == "0001":
        _mkt_type = "KOSPI"
    else:
        _mkt_type = "ALL"
    result = []
    for it in items[:top_n]:
        ticker = it.get("mksc_shrn_iscd", "").strip()
        if not ticker or not ticker.isdigit():
            continue
        try:
            result.append({
                "ticker":      ticker,
                "name":        it.get("hts_kor_isnm", ticker),
                "price":       int(it.get("stck_prpr", 0)),
                "change_rate": float(it.get("prdy_ctrt", 0)),
                "volume":      int(it.get("acml_vol", 0)),
                "vol_ratio":   float(it.get("vol_tnrt", 1.0)),
                "market_type": _mkt_type,
            })
        except (ValueError, TypeError):
            continue
    return result


def _is_kr_premarket_window() -> bool:
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    now_kr = _dt.now(ZoneInfo("Asia/Seoul"))
    return (
        (now_kr.hour == 8 and now_kr.minute >= 30)
        or (now_kr.hour == 9 and now_kr.minute <= 5)
    )


def screen_market_kr(token: str, top_n: int = 30, mode: str = "NEUTRAL") -> list:
    """
    KR 시장 스크리닝 — A+B 병행 전략

    장중(09:05~15:30):
      B: KIS volume-rank (FID_VOL_CNT=KR_SCREEN_MIN_VOLUME 환경변수, 기본 100000) → 정상 결과 → 캐시 저장 (A 준비)

    장전(08:30~09:05):
      B: KIS volume-rank (FID_VOL_CNT=0, 필터 OFF) → 동시호가·시간외 잔량 있는 종목
      A: kr_screen_cache.json (전일 장중 저장분) → B 결과 보충
      → 합쳐서 top_n 반환

    반환: [{ticker, name, price, change_rate, volume, vol_ratio}]
    """
    import logging as _log
    import time as _time
    from bot.candidate_policy import filter_tradable_candidates
    _logger = _log.getLogger("trading_system")

    def _apply_product_filter(candidates: list[dict], label: str) -> list[dict]:
        filtered, removed = filter_tradable_candidates(candidates, "KR")
        if removed:
            _logger.info(
                f"[KR 스크리너 상품필터 {label}] 제외 {len(removed)}개: "
                + ", ".join(
                    f"{c.get('ticker')}({c.get('name','')}/{c.get('blocked_reason','')})"
                    for c in removed[:12]
                )
            )
        return filtered

    reserve_n = _kr_screen_reserve_limit(top_n)
    _is_premarket = _is_kr_premarket_window()

    try:
        if _is_premarket:
            # ── B: KIS 거래량 필터 OFF → 동시호가 잔량 종목 수집 ───────────────
            live_kp = []
            live_kq = []
            try:
                live_kp = _kis_volume_rank(
                    token, vol_cnt="0", top_n=reserve_n, market_div="J", input_iscd="0001"
                )
                live_kq = _kis_volume_rank(
                    token, vol_cnt="0", top_n=reserve_n, market_div="J", input_iscd="1001"
                )
                _logger.info(
                    f"[KR 스크리너 장전-B] KIS 거래량필터OFF "
                    f"KOSPI={len(live_kp)} KOSDAQ={len(live_kq)}"
                )
                if len(live_kq) == 0:
                    _logger.warning(
                        "[KR 스크리너 KOSDAQ raw=0] "
                        f"phase=premarket market_div=J input_iscd=1001 "
                        f"vol_cnt=0 reserve_n={reserve_n} kospi_raw={len(live_kp)}"
                    )
            except Exception as e:
                _logger.warning(f"[KR 스크리너 장전-B] KIS 실패: {e}")
            live = _merge_kr_market_buckets(live_kp, live_kq, reserve_n)

            # ── A: 전일 캐시 로드 → B 보충 ────────────────────────────────────
            cached = _load_kr_screen_cache()
            _logger.info(
                f"[KR 스크리너 장전-A] 전일 캐시 {len(cached)}종목 로드"
            )

            # B 우선, A로 보충 (중복 제거)
            seen = {c["ticker"] for c in live}
            merged = list(live)
            for c in cached:
                if c["ticker"] not in seen:
                    merged.append(c)
                    seen.add(c["ticker"])

            if len(merged) < 10:
                # 최후 폴백: 하드코딩 블루칩
                for ticker in _KR_FALLBACK_UNIVERSE:
                    if ticker not in seen:
                        merged.append({
                            "ticker": ticker, "name": ticker,
                            "price": 0, "change_rate": 0.0,
                            "volume": 0, "vol_ratio": 1.0,
                            "market_type": "KOSPI",
                        })
                        seen.add(ticker)
                    if len(merged) >= reserve_n:
                        break

            _logger.info(
                f"[KR 스크리너 장전] 후보풀 {len(merged)}종목 "
                f"(B라이브={len(live)}, A캐시={len(cached)})"
            )
            filtered = _apply_product_filter(merged, "premarket")
            final = _cap_kr_screen_candidates(filtered, top_n)
            _save_kr_screen_audit(
                "premarket",
                mode,
                top_n,
                "0",
                {"kospi_raw": live_kp, "kosdaq_raw": live_kq, "cache": cached},
                merged,
                filtered,
                final,
            )
            return final

        else:
            # ── 장중: 정상 스크리닝 + 캐시 저장 ─────────────────────────────
            preset = get_screening_preset("KR", mode)
            _kr_vol_cnt = str(preset["kr_min_volume"])
            _logger.info(f"[KR 스크리너] mode={mode} → FID_VOL_CNT={_kr_vol_cnt}")
            kospi_result = _kis_volume_rank(
                token, vol_cnt=_kr_vol_cnt, top_n=reserve_n, market_div="J", input_iscd="0001"
            )

            # KOSDAQ 보강: KRX volume-rank 업종코드 1001 결과를 후보 풀에 추가한다.
            kosdaq_result = []
            try:
                kosdaq_result = _kis_volume_rank(
                    token, vol_cnt=_kr_vol_cnt, top_n=reserve_n, market_div="J", input_iscd="1001"
                )
                _logger.info(
                    f"[KR 스크리너] raw KOSPI={len(kospi_result)} "
                    f"KOSDAQ={len(kosdaq_result)}"
                )
                if len(kosdaq_result) == 0:
                    _logger.warning(
                        "[KR 스크리너 KOSDAQ raw=0] "
                        f"phase=intraday market_div=J input_iscd=1001 "
                        f"vol_cnt={_kr_vol_cnt} reserve_n={reserve_n} kospi_raw={len(kospi_result)}"
                    )
            except Exception as _e:
                _logger.warning(
                    f"[KR 스크리너 KOSDAQ raw=0] phase=intraday "
                    f"market_div=J input_iscd=1001 "
                    f"vol_cnt={_kr_vol_cnt} reserve_n={reserve_n} error={_e}"
                )

            result = _merge_kr_market_buckets(kospi_result, kosdaq_result, reserve_n)
            _logger.info(
                f"[KR 스크리너] KOSPI/KOSDAQ quota merge → {len(result)}종목 "
                f"(KOSDAQ {sum(1 for c in result if c.get('market_type') == 'KOSDAQ')}개)"
            )

            if len(result) < 10:
                # 보완: 하드코딩 폴백
                existing = {r["ticker"] for r in result}
                for ticker in _KR_FALLBACK_UNIVERSE:
                    if ticker not in existing:
                        result.append({
                            "ticker": ticker, "name": ticker,
                            "price": 0, "change_rate": 0.0,
                            "volume": 0, "vol_ratio": 1.0,
                            "market_type": "KOSPI",
                        })
                        existing.add(ticker)
                    if len(result) >= reserve_n:
                        break

            filtered = _apply_product_filter(result, "intraday")
            final = _cap_kr_screen_candidates(filtered, top_n)
            final_kosdaq_count = sum(1 for c in final if c.get("market_type") == "KOSDAQ")
            if len(final) >= 10 and final_kosdaq_count > 0:
                # 충분한 tradable 결과면 캐시 저장 (다음 날 장전 A로 사용)
                save_kr_screen_cache(final)
            elif len(final) >= 10:
                _logger.warning(
                    "[KR 스크리너 캐시] 저장 건너뜀: KOSDAQ coverage=0 "
                    f"(candidates={len(final)}, kospi_raw={len(kospi_result)}, kosdaq_raw={len(kosdaq_result)})"
                )
            _save_kr_screen_audit(
                "intraday",
                mode,
                top_n,
                _kr_vol_cnt,
                {"kospi_raw": kospi_result, "kosdaq_raw": kosdaq_result},
                result,
                filtered,
                final,
            )
            return final

    except Exception as e:
        _logger.warning(f"[KR 스크리너] API 실패 → 캐시·폴백: {e}")
        # API 완전 실패 시 캐시 → 하드코딩 순
        cached = _load_kr_screen_cache()
        if cached:
            return _apply_product_filter(cached, "cache")[:top_n]
        return _apply_product_filter([
            {"ticker": t, "name": t, "price": 0, "change_rate": 0.0, "volume": 0, "vol_ratio": 1.0}
            for t in _KR_FALLBACK_UNIVERSE
        ], "fallback")[:top_n]


_US_SCREEN_CACHE_PATH = get_runtime_path("state", "us_screen_cache.json")
_US_SCREEN_CACHE_SCHEMA = 3

# 레거시 캐시 경로 (이전 버전 호환)
_AV_CACHE_PATH = _US_SCREEN_CACHE_PATH


def _write_price_collection_priority(market: str, candidates: list, *, source: str, mode: str = "") -> None:
    if str(os.getenv("PRICE_COLLECTION_PRIORITY_WRITE", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return
    market_key = "US" if str(market or "").upper() == "US" else "KR"
    today = datetime.now().strftime("%Y%m%d")
    seen: set[str] = set()
    items: list[dict] = []
    max_items = max(1, int(os.getenv(f"{market_key}_PRICE_COLLECTION_PRIORITY_MAX", "200")))
    for row in candidates or []:
        ticker = str((row or {}).get("ticker") or "").strip()
        ticker_key = ticker.upper() if market_key == "US" else ticker
        if not ticker_key or ticker_key in seen:
            continue
        seen.add(ticker_key)
        items.append(
            {
                "rank": len(items) + 1,
                "ticker": ticker_key,
                "source": str(source or ""),
                "mode": str(mode or ""),
                "category": (row or {}).get("category", ""),
                "price": (row or {}).get("price"),
                "change_rate": (row or {}).get("change_rate"),
                "volume": (row or {}).get("volume"),
                "vol_ratio": (row or {}).get("vol_ratio"),
                "screener_quality_state": (row or {}).get("screener_quality_state", ""),
            }
        )
        if len(items) >= max_items:
            break
    if not items:
        return
    path = _US_SCREEN_CACHE_PATH.parent / f"price_collection_priority_{market_key}_{today}.json"
    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "market": market_key,
        "source": str(source or ""),
        "mode": str(mode or ""),
        "items": items,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logging.getLogger("trading_system").debug(f"[price priority] write failed: {exc}")


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except Exception:
        return default


def _extract_us_volume(item: dict) -> int:
    """US 스크리너 원본마다 volume 필드명이 달라 보수적으로 여러 키를 시도한다."""
    for key in ("volume", "avgVolume", "avgVolume3m", "averageVolume", "volumeAverage", "sharesVolume"):
        vol = _safe_int(item.get(key), 0)
        if vol > 0:
            return vol
    return 0


def _has_meaningful_candidate_volume(candidates: list) -> bool:
    return any(_safe_int(c.get("volume"), 0) > 0 for c in candidates)


def _safe_float_value(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value)
    except Exception:
        return float(default)


def _us_screen_cache_threshold(quota_total: int) -> dict:
    quota = max(0, int(quota_total or 0))
    absolute_raw = max(0, _safe_int(os.getenv("US_SCREEN_MIN_CACHE_CANDIDATES"), 30))
    ratio = _safe_float_value(os.getenv("US_SCREEN_MIN_CACHE_RATIO"), 0.60)
    ratio = max(0.0, ratio)
    absolute_floor = min(absolute_raw, quota)
    ratio_floor = min(quota, int(math.ceil(quota * ratio))) if quota > 0 else 0
    return {
        "quota_total": quota,
        "absolute_floor": absolute_floor,
        "ratio_floor": ratio_floor,
        "min_cache_count": max(absolute_floor, ratio_floor),
        "ratio": ratio,
    }


def _us_screen_market_elapsed_min() -> Optional[float]:
    try:
        from bot.session_date import KST, resolve_session_date_str
        from preopen.scheduler import regular_open_dt

        now_dt = datetime.now(KST)
        session_date = resolve_session_date_str("US", now_dt)
        opened = regular_open_dt("US", session_date)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=KST)
        else:
            opened = opened.astimezone(KST)
        return (now_dt - opened).total_seconds() / 60.0
    except Exception:
        return None


def _us_screen_quality_state(
    *,
    source: str,
    fresh_count: int,
    threshold: dict,
    raw_count_by_category: dict,
    filtered_count_by_category: dict,
    dollar_reject_count_by_category: dict,
) -> dict:
    min_cache_count = int(threshold.get("min_cache_count") or 0)
    fresh = int(fresh_count or 0)
    reasons: list[str] = []
    state = "OK"
    if min_cache_count > 0 and fresh < min_cache_count:
        state = "DEGRADED_COUNT"
        reasons.append("fresh_count_below_min_cache_count")
    category_degraded = False
    for cat, raw_count in (raw_count_by_category or {}).items():
        if int(raw_count or 0) > 0 and int((filtered_count_by_category or {}).get(cat, 0) or 0) == 0:
            category_degraded = True
            break
    if category_degraded and state == "OK":
        state = "DEGRADED_CATEGORY"
    if category_degraded:
        reasons.append("category_filtered_to_zero")
    elapsed_min = _us_screen_market_elapsed_min()
    early_window = _safe_float_value(os.getenv("US_SCREEN_EARLY_VOLUME_WINDOW_MIN"), 30.0)
    dollar_reject_total = sum(int(v or 0) for v in (dollar_reject_count_by_category or {}).values())
    early_volume_degraded = (
        elapsed_min is not None
        and 0.0 <= float(elapsed_min) <= early_window
        and dollar_reject_total > 0
    )
    if early_volume_degraded and state == "OK":
        state = "DEGRADED_EARLY_VOLUME"
    if early_volume_degraded:
        reasons.append("early_dollar_volume_rejects")
    return {
        "screener_quality_state": state,
        "screener_degraded": state != "OK",
        "screener_degraded_reason": ",".join(dict.fromkeys(reasons)),
        "raw_count_by_category": dict(raw_count_by_category or {}),
        "filtered_count_by_category": dict(filtered_count_by_category or {}),
        "dollar_volume_reject_count_by_category": dict(dollar_reject_count_by_category or {}),
        "quota_total": int(threshold.get("quota_total") or 0),
        "fresh_count": fresh,
        "min_cache_count": min_cache_count,
        "min_cache_ratio": float(threshold.get("ratio") or 0.0),
        "market_elapsed_min": round(float(elapsed_min), 2) if elapsed_min is not None else None,
        "screener_cache_used": False,
        "screener_cache_saved": False,
        "screener_cache_skipped_reason": "",
        "source": str(source or ""),
    }


def _annotate_us_screen_quality(candidates: list, quality: dict) -> list:
    annotated = []
    for row in candidates or []:
        item = dict(row or {})
        for key in (
            "screener_quality_state",
            "screener_degraded",
            "screener_degraded_reason",
            "raw_count_by_category",
            "filtered_count_by_category",
            "dollar_volume_reject_count_by_category",
            "quota_total",
            "fresh_count",
            "min_cache_count",
            "min_dollar_vol",
            "market_elapsed_min",
            "screener_cache_used",
            "screener_cache_saved",
            "screener_cache_skipped_reason",
        ):
            if key in quality:
                item[key] = quality.get(key)
        annotated.append(item)
    return annotated


def _us_screen_cache_eligible(quality: dict) -> bool:
    try:
        return int(quality.get("fresh_count") or 0) >= int(quality.get("min_cache_count") or 0)
    except Exception:
        return False


def _candidate_us_exchange_code(candidate: dict) -> Optional[str]:
    return _map_yahoo_us_exchange(
        candidate.get("exchange") or candidate.get("exchangeShortName"),
        candidate.get("fullExchangeName") or candidate.get("exchDisp"),
    )


def get_screening_preset(market: str, mode: str) -> dict:
    """
    시장 모드별 스크리닝 프리셋 반환.

    Claude는 mode만 결정하고, 실제 스크리너 파라미터는 이 함수가 확정.
    환경변수는 최종 수동 override로만 사용.

    반환 키:
      KR: kr_min_volume
      US: min_price, max_chg, min_dollar_vol, loser_max_chg,
          quota_actives, quota_gainers, quota_losers
    """
    _MODE_MAP = {
        "AGGRESSIVE": "AGGRESSIVE", "MILD_BULL": "AGGRESSIVE", "BULL": "AGGRESSIVE",
        "NEUTRAL":    "NEUTRAL",    "MILD_BEAR": "NEUTRAL",
        "DEFENSIVE":  "DEFENSIVE",  "BEAR": "DEFENSIVE", "CAUTIOUS": "DEFENSIVE", "HALT": "DEFENSIVE",
    }
    _mode = _MODE_MAP.get(str(mode).upper(), "NEUTRAL")
    _market = str(market).upper()

    if _market == "KR":
        _presets = {
            "AGGRESSIVE": {"kr_min_volume": 100_000},
            "NEUTRAL":    {"kr_min_volume": 100_000},
            "DEFENSIVE":  {"kr_min_volume": 200_000},
        }
        base = _presets.get(_mode, _presets["NEUTRAL"]).copy()
        # 환경변수 override (수동 튜닝용)
        if os.getenv("KR_SCREEN_MIN_VOLUME"):
            base["kr_min_volume"] = int(os.getenv("KR_SCREEN_MIN_VOLUME"))
        return base

    else:  # US
        _presets = {
            "AGGRESSIVE": {
                "min_price":      5.0,
                "max_chg":        25.0,
                "min_dollar_vol": 15_000_000,
                "loser_max_chg":  20.0,
                "quota_actives":  12,
                "quota_gainers":  12,
                "quota_losers":   6,
            },
            "NEUTRAL": {
                "min_price":      5.0,
                "max_chg":        25.0,
                "min_dollar_vol": 15_000_000,
                "loser_max_chg":  20.0,
                "quota_actives":  15,
                "quota_gainers":  10,
                "quota_losers":   5,
            },
            "DEFENSIVE": {
                "min_price":      5.0,
                "max_chg":        25.0,
                "min_dollar_vol": 20_000_000,
                "loser_max_chg":  15.0,
                "quota_actives":  20,
                "quota_gainers":  7,
                "quota_losers":   3,
            },
        }
        base = _presets.get(_mode, _presets["NEUTRAL"]).copy()
        # 환경변수 override
        if os.getenv("US_SCREEN_MIN_PRICE"):
            base["min_price"] = float(os.getenv("US_SCREEN_MIN_PRICE"))
        if os.getenv("US_SCREEN_MAX_CHG_PCT"):
            base["max_chg"] = float(os.getenv("US_SCREEN_MAX_CHG_PCT"))
        if os.getenv("US_SCREEN_MIN_DOLLAR_VOL"):
            base["min_dollar_vol"] = float(os.getenv("US_SCREEN_MIN_DOLLAR_VOL"))
        if os.getenv("US_LOSER_MAX_CHG_PCT"):
            base["loser_max_chg"] = float(os.getenv("US_LOSER_MAX_CHG_PCT"))
        if os.getenv("US_QUOTA_ACTIVES"):
            base["quota_actives"] = int(os.getenv("US_QUOTA_ACTIVES"))
        if os.getenv("US_QUOTA_GAINERS"):
            base["quota_gainers"] = int(os.getenv("US_QUOTA_GAINERS"))
        if os.getenv("US_QUOTA_LOSERS"):
            base["quota_losers"] = int(os.getenv("US_QUOTA_LOSERS"))
        return base


def _us_post_filter(
    candidates: list,
    category: str,
    min_price: float,
    max_abs_chg: float,
    min_dollar_vol: float,
    loser_max_chg: float,
) -> list:
    return _us_post_filter_with_stats(
        candidates,
        category,
        min_price,
        max_abs_chg,
        min_dollar_vol,
        loser_max_chg,
    )[0]


def _us_post_filter_with_stats(
    candidates: list,
    category: str,
    min_price: float,
    max_abs_chg: float,
    min_dollar_vol: float,
    loser_max_chg: float,
) -> tuple[list, dict]:
    """
    소스 무관 공통 후처리 필터 (raw 수집 후 적용).

    - 가격 하한, 등락폭 상한, 유효 티커 형식, ETF/워런트/유닛 제외
    - dollar volume 하한 (volume_missing=True인 경우 건너뜀)
    - day_losers는 loser_max_chg 추가 제한
    """
    _BAD_SFXS = {"W", "U", "R"}
    result = []
    stats = {
        "raw_count": len(candidates or []),
        "dollar_volume_reject_count": 0,
    }
    for c in candidates:
        ticker = str(c.get("ticker", "")).strip()
        if not ticker or not ticker.isalpha() or len(ticker) > 5:
            continue
        if ticker[-1] in _BAD_SFXS:
            continue
        if (
            c.get("exchange")
            or c.get("exchangeShortName")
            or c.get("fullExchangeName")
            or c.get("exchDisp")
        ) and not _candidate_us_exchange_code(c):
            continue
        price      = float(c.get("price", 0) or 0)
        change_abs = abs(float(c.get("change_rate", 0) or 0))
        volume     = int(c.get("volume", 0) or 0)
        vol_miss   = bool(c.get("volume_missing", False))

        if price < min_price:
            continue
        if change_abs > max_abs_chg:
            continue
        if category == "day_losers" and change_abs > loser_max_chg:
            continue
        if not vol_miss and min_dollar_vol > 0 and price * volume < min_dollar_vol:
            stats["dollar_volume_reject_count"] += 1
            continue
        result.append(c)
    stats["filtered_count"] = len(result)
    return result, stats


def _yf_screen_candidates() -> dict:
    """
    Yahoo Finance 내부 스크리너 API로 US 카테고리별 raw 후보 수집.

    반환: {"most_actives": [...], "day_gainers": [...], "day_losers": [...]}
    각 항목은 ticker/name/price/change_rate/volume/vol_ratio 포함.
    필터링은 screen_market_us()에서 _us_post_filter()로 일괄 처리.
    """
    import logging as _log
    _logger = _log.getLogger("trading_system")

    _BAD_SFXS = {"W", "U", "R"}
    url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; trading-bot/1.0)"}

    raw: dict = {"most_actives": [], "day_gainers": [], "day_losers": []}

    for screen_id in ("most_actives", "day_gainers", "day_losers"):
        try:
            resp = requests.get(
                url,
                params={"formatted": "false", "scrIds": screen_id, "count": 50},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            quotes = resp.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
            bucket = []
            for q in quotes:
                ticker = str(q.get("symbol", "")).strip()
                if not ticker or not ticker.isalpha() or len(ticker) > 5:
                    continue
                if ticker[-1] in _BAD_SFXS:
                    continue
                try:
                    bucket.append({
                        "ticker":      ticker,
                        "name":        q.get("shortName", ticker),
                        "price":       float(q.get("regularMarketPrice", 0)),
                        "change_rate": float(q.get("regularMarketChangePercent", 0)),
                        "volume":      int(q.get("regularMarketVolume", 0)),
                        "vol_ratio":   1.0,
                        "category":    screen_id,
                        "exchange":    q.get("exchange", ""),
                        "fullExchangeName": q.get("fullExchangeName", ""),
                        "quoteType":   q.get("quoteType", ""),
                    })
                except (ValueError, TypeError):
                    continue
            raw[screen_id] = bucket
            _logger.debug(f"[YF raw] {screen_id} {len(bucket)}종목")
        except Exception as e:
            _logger.debug(f"[YF 스크리너] {screen_id} 실패: {e}")

    return raw


def _fmp_screen_candidates() -> list:
    """
    FMP stable 엔드포인트로 US 스크리너 후보 raw 수집 (YF 실패 시 보조).

    주의: FMP stable API는 volume 필드 미포함.
    각 후보에 volume_missing=True 플래그 부여 → 호출부에서 dollar volume 필터 면제,
    최대 quota(기본 5개) 제한 적용.
    필터링은 screen_market_us()에서 _us_post_filter()로 일괄 처리.
    """
    if not FMP_KEY:
        raise RuntimeError("FMP_API_KEY 없음")

    _BAD_SFXS = {"W", "U", "R"}
    base = "https://financialmodelingprep.com/stable"
    endpoints = ["biggest-gainers", "most-actives", "biggest-losers"]
    candidates = []
    seen: set = set()

    for ep in endpoints:
        try:
            resp = _kis_get(
                f"{base}/{ep}",
                params={"apikey": FMP_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json()
            if not isinstance(items, list):
                continue
            for item in items:
                ticker = str(item.get("symbol", "")).strip()
                if not ticker or ticker in seen:
                    continue
                if not ticker.isalpha() or len(ticker) > 5:
                    continue
                if ticker[-1] in _BAD_SFXS:
                    continue
                seen.add(ticker)
                try:
                    candidates.append({
                        "ticker":         ticker,
                        "name":           item.get("name", ticker),
                        "price":          float(item.get("price", 0)),
                        "change_rate":    float(item.get("changesPercentage", 0)),
                        "volume":         0,
                        "vol_ratio":      1.0,
                        "volume_missing": True,
                        "category":       ep,
                        "exchange":       item.get("exchange", ""),
                        "exchangeShortName": item.get("exchangeShortName", ""),
                    })
                except (ValueError, TypeError):
                    continue
        except Exception:
            continue

    import logging as _log
    _log.getLogger("trading_system").info(
        f"[FMP 스크리너] raw {len(candidates)}종목 수집 (volume 미포함)"
    )
    return candidates


def screen_market_us(top_n: int = 30, mode: str = "NEUTRAL") -> list:
    """
    US 시장 스크리닝 — 모드 기반 프리셋 + 공통 post-filter + 카테고리 quota 적용.

    소스 우선순위: Yahoo Finance → FMP (최대 5개) → 하드코딩 폴백
    캐시: 당일 TTL(기본 60분) 내 재사용.
    파라미터는 get_screening_preset("US", mode)로 결정, 환경변수는 override용.

    반환: [{ticker, name, price, change_rate, volume, vol_ratio, category}]
    """
    import logging as _log
    import time as _time
    _logger = _log.getLogger("trading_system")
    today = datetime.now().strftime("%Y-%m-%d")

    # ── 모드 기반 프리셋 (환경변수 override 포함) ──────────────────────────
    preset = get_screening_preset("US", mode)
    _min_price      = preset["min_price"]
    _max_chg        = preset["max_chg"]
    _min_dollar_vol = preset["min_dollar_vol"]
    _loser_max_chg  = preset["loser_max_chg"]
    _quota = {
        "most_actives": preset["quota_actives"],
        "day_gainers":  preset["quota_gainers"],
        "day_losers":   preset["quota_losers"],
    }
    _target_n = max(1, int(top_n or 30))
    _quota_total = sum(int(v or 0) for v in _quota.values())
    if _target_n > _quota_total and _quota_total > 0:
        scale = _target_n / float(_quota_total)
        _quota = {key: max(int(value), int(round(int(value) * scale))) for key, value in _quota.items()}
        _fill_order = ("most_actives", "day_gainers", "day_losers")
        _idx = 0
        while sum(_quota.values()) < _target_n:
            _quota[_fill_order[_idx % len(_fill_order)]] += 1
            _idx += 1
    _quota_total = sum(int(v or 0) for v in _quota.values())
    _cache_threshold = _us_screen_cache_threshold(_quota_total)
    _cache_mode = str(mode).upper()
    _logger.info(
        f"[US 스크리너] mode={mode} → "
        f"actives={_quota['most_actives']} gainers={_quota['day_gainers']} losers={_quota['day_losers']} "
        f"dolvol≥${_min_dollar_vol/1e6:.0f}M max_chg≤{_max_chg}%"
    )
    _fmp_max        = int(os.getenv("US_FMP_MAX", "5"))
    _cache_preset = {
        "min_price": _min_price,
        "max_chg": _max_chg,
        "min_dollar_vol": _min_dollar_vol,
        "loser_max_chg": _loser_max_chg,
        "quota_actives": _quota["most_actives"],
        "quota_gainers": _quota["day_gainers"],
        "quota_losers": _quota["day_losers"],
        "fmp_max": _fmp_max,
        "top_n": _target_n,
        "schema": _US_SCREEN_CACHE_SCHEMA,
    }
    _CACHE_TTL_SEC  = int(os.getenv("US_SCREEN_CACHE_TTL_SEC", "1800"))

    # ── 당일 캐시 확인 ────────────────────────────────────────────────────
    if _US_SCREEN_CACHE_PATH.exists():
        try:
            cached = json.loads(_US_SCREEN_CACHE_PATH.read_text(encoding="utf-8"))
            if cached.get("date") == today and cached.get("candidates"):
                cache_age = _time.time() - cached.get("cached_at", 0)
                if cache_age <= _CACHE_TTL_SEC:
                    source = cached.get("source", "")
                    cands  = cached["candidates"]
                    cached_mode = str(cached.get("mode", "")).upper()
                    cached_preset = cached.get("preset", {})
                    cached_quality = dict(cached.get("quality") or {})
                    cached_fresh_count = _safe_int(cached_quality.get("fresh_count"), len(cands or []))
                    if cached_mode != _cache_mode or cached_preset != _cache_preset:
                        _logger.info(
                            f"[US 스크리너 캐시] mode/preset 불일치 "
                            f"(cached={cached_mode or '-'} current={_cache_mode}) → 재스크리닝"
                        )
                    elif cached_fresh_count < int(_cache_threshold.get("min_cache_count") or 0):
                        _logger.info(
                            f"[US 스크리너 캐시] 재사용 건너뜀: fresh_count={cached_fresh_count} "
                            f"min_cache_count={_cache_threshold.get('min_cache_count')} "
                            f"quota_total={_cache_threshold.get('quota_total')} "
                            f"ratio={_cache_threshold.get('ratio')} source={source or '-'} "
                            f"mode={_cache_mode} top_n={_target_n}"
                        )
                    elif source == "yf" and _has_meaningful_candidate_volume(cands):
                        _logger.debug(f"[US 스크리너 캐시] 재사용 ({cache_age/60:.0f}분 경과)")
                        cached_quality.update(
                            {
                                "screener_cache_used": True,
                                "screener_cache_saved": False,
                                "screener_cache_skipped_reason": "",
                            }
                        )
                        out = _annotate_us_screen_quality(cands[:_target_n], cached_quality)
                        _write_price_collection_priority("US", out, source=f"cache:{source or 'unknown'}", mode=mode)
                        return out
                    elif source == "fmp" and cands:
                        _logger.debug(f"[US 스크리너 캐시] 재사용 ({cache_age/60:.0f}분 경과)")
                        cached_quality.update(
                            {
                                "screener_cache_used": True,
                                "screener_cache_saved": False,
                                "screener_cache_skipped_reason": "",
                            }
                        )
                        out = _annotate_us_screen_quality(cands[:_target_n], cached_quality)
                        _write_price_collection_priority("US", out, source=f"cache:{source or 'unknown'}", mode=mode)
                        return out
                else:
                    _logger.info(
                        f"[US 스크리너 캐시] 만료 ({cache_age/60:.0f}분 > TTL {_CACHE_TTL_SEC//60}분) → 재스크리닝"
                    )
        except Exception:
            pass

    # ── 1차: Yahoo Finance — 카테고리별 raw 수집 + post-filter + quota ────
    try:
        raw_by_cat = _yf_screen_candidates()
        merged: list = []
        seen: set = set()
        cat_stats = {}
        raw_count_by_category = {}
        filtered_count_by_category = {}
        dollar_reject_count_by_category = {}
        for cat, quota in _quota.items():
            bucket = raw_by_cat.get(cat, [])
            filtered, filter_stats = _us_post_filter_with_stats(
                bucket,
                cat,
                _min_price,
                _max_chg,
                _min_dollar_vol,
                _loser_max_chg,
            )
            raw_count_by_category[cat] = int(filter_stats.get("raw_count") or 0)
            filtered_count_by_category[cat] = int(filter_stats.get("filtered_count") or 0)
            dollar_reject_count_by_category[cat] = int(filter_stats.get("dollar_volume_reject_count") or 0)
            added = 0
            for c in filtered:
                if c["ticker"] in seen or added >= quota:
                    continue
                seen.add(c["ticker"])
                merged.append(c)
                added += 1
            cat_stats[cat] = added

        if merged:
            quality = _us_screen_quality_state(
                source="yf",
                fresh_count=len(merged),
                threshold=_cache_threshold,
                raw_count_by_category=raw_count_by_category,
                filtered_count_by_category=filtered_count_by_category,
                dollar_reject_count_by_category=dollar_reject_count_by_category,
            )
            quality["min_dollar_vol"] = _min_dollar_vol
            _logger.info(
                f"[YF 스크리너] 통과={len(merged)}종목 "
                f"actives={cat_stats.get('most_actives',0)} "
                f"gainers={cat_stats.get('day_gainers',0)} "
                f"losers={cat_stats.get('day_losers',0)} "
                f"(기준: ${_min_price}+, ≤{_max_chg}%, dolvol≥${_min_dollar_vol/1e6:.0f}M)"
            )
            if _us_screen_cache_eligible(quality):
                quality["screener_cache_saved"] = True
                _US_SCREEN_CACHE_PATH.write_text(
                    json.dumps({"date": today, "candidates": merged,
                                "source": "yf", "cached_at": _time.time(),
                                "mode": _cache_mode, "preset": _cache_preset,
                                "schema": _US_SCREEN_CACHE_SCHEMA,
                                "quality": quality},
                               ensure_ascii=False),
                    encoding="utf-8",
                )
            else:
                quality["screener_cache_skipped_reason"] = "fresh_count_below_min_cache_count"
                _logger.warning(
                    f"[US 스크리너 캐시] 저장 건너뜀: fresh_count={quality.get('fresh_count')} "
                    f"min_cache_count={quality.get('min_cache_count')} "
                    f"quota_total={quality.get('quota_total')} ratio={quality.get('min_cache_ratio')} "
                    f"source=yf mode={_cache_mode} top_n={_target_n}"
                )
            out = _annotate_us_screen_quality(merged[:_target_n], quality)
            _write_price_collection_priority("US", out, source="yf", mode=mode)
            return out
    except Exception as e:
        _logger.warning(f"[YF 스크리너] 실패: {e}")

    # ── 2차: FMP fallback — volume_missing, 최대 _fmp_max개 ──────────────
    try:
        fmp_raw = _fmp_screen_candidates()
        fmp_filtered, fmp_stats = _us_post_filter_with_stats(
            fmp_raw,
            "most_actives",
            _min_price,
            _max_chg,
            _min_dollar_vol,
            _loser_max_chg,
        )
        fmp_cands = fmp_filtered[:_fmp_max]
        if fmp_cands:
            quality = _us_screen_quality_state(
                source="fmp",
                fresh_count=len(fmp_cands),
                threshold=_cache_threshold,
                raw_count_by_category={"fmp": int(fmp_stats.get("raw_count") or 0)},
                filtered_count_by_category={"fmp": int(fmp_stats.get("filtered_count") or 0)},
                dollar_reject_count_by_category={"fmp": int(fmp_stats.get("dollar_volume_reject_count") or 0)},
            )
            quality["min_dollar_vol"] = _min_dollar_vol
            _logger.info(f"[FMP 스크리너] 통과={len(fmp_cands)}종목 (최대 {_fmp_max}개, volume 미포함)")
            if _us_screen_cache_eligible(quality):
                quality["screener_cache_saved"] = True
                _US_SCREEN_CACHE_PATH.write_text(
                    json.dumps({"date": today, "candidates": fmp_cands,
                                "source": "fmp", "cached_at": _time.time(),
                                "mode": _cache_mode, "preset": _cache_preset,
                                "schema": _US_SCREEN_CACHE_SCHEMA,
                                "quality": quality},
                               ensure_ascii=False),
                    encoding="utf-8",
                )
            else:
                quality["screener_cache_skipped_reason"] = "fresh_count_below_min_cache_count"
                _logger.warning(
                    f"[US 스크리너 캐시] 저장 건너뜀: fresh_count={quality.get('fresh_count')} "
                    f"min_cache_count={quality.get('min_cache_count')} "
                    f"quota_total={quality.get('quota_total')} ratio={quality.get('min_cache_ratio')} "
                    f"source=fmp mode={_cache_mode} top_n={_target_n}"
                )
            out = _annotate_us_screen_quality(fmp_cands[:_target_n], quality)
            _write_price_collection_priority("US", out, source="fmp", mode=mode)
            return out
    except Exception as e:
        _logger.warning(f"[FMP 스크리너] 실패: {e}")

    # ── 3차: 하드코딩 폴백 ───────────────────────────────────────────────
    _logger.warning("[US 스크리너] 모든 소스 실패 → 폴백 유니버스 사용")
    fallback = [
        {"ticker": t, "name": t, "price": 0.0, "change_rate": 0.0,
         "volume": 0, "vol_ratio": 1.0, "category": "fallback"}
        for t in _US_FALLBACK_UNIVERSE
    ]
    quality = _us_screen_quality_state(
        source="fallback",
        fresh_count=len(fallback),
        threshold=_cache_threshold,
        raw_count_by_category={"fallback": len(fallback)},
        filtered_count_by_category={"fallback": len(fallback)},
        dollar_reject_count_by_category={"fallback": 0},
    )
    quality["screener_cache_skipped_reason"] = "fallback_not_cached"
    quality["min_dollar_vol"] = _min_dollar_vol
    out = _annotate_us_screen_quality(fallback, quality)
    _write_price_collection_priority("US", out, source="fallback", mode=mode)
    return out


def _aes_cbc_base64_dec(key: str, iv: str, cipher_text: str) -> str:
    """AES-256 CBC 복호화 (KIS 체결통보 암호화 해제)"""
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
        from base64 import b64decode
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
        return bytes.decode(unpad(cipher.decrypt(b64decode(cipher_text)), AES.block_size))
    except ImportError:
        log.error("[KIS WS] pycryptodome 미설치 — pip install pycryptodome")
        return ""


# KR 체결통보 컬럼 순서 (H0STCNI0/H0STCNI9, 공식 샘플 기준)
_NOTICE_COLS_KR = [
    "CUST_ID", "ACNT_NO", "ODER_NO", "OODER_NO", "SELN_BYOV_CLS", "RCTF_CLS",
    "ODER_KIND", "ODER_COND", "STCK_SHRN_ISCD", "CNTG_QTY", "CNTG_UNPR",
    "STCK_CNTG_HOUR", "RFUS_YN", "CNTG_YN", "ACPT_YN", "BRNC_NO", "ODER_QTY",
    "ACNT_NAME", "ORD_COND_PRC", "ORD_EXG_GB", "POPUP_YN", "FILLER", "CRDT_CLS",
    "CRDT_LOAN_DATE", "CNTG_ISNM40", "ODER_PRC",
]
# US 체결통보 컬럼 순서 (H0GSCNI0/H0GSCNI9, 공식 샘플 기준)
_NOTICE_COLS_US = [
    "CUST_ID", "ACNT_NO", "ODER_NO", "OODER_NO", "SELN_BYOV_CLS", "RCTF_CLS",
    "ODER_KIND2", "STCK_SHRN_ISCD", "CNTG_QTY", "CNTG_UNPR",
    "STCK_CNTG_HOUR", "RFUS_YN", "CNTG_YN", "ACPT_YN", "BRNC_NO", "ODER_QTY",
    "ACNT_NAME", "CNTG_ISNM", "ODER_COND", "DEBT_GB", "DEBT_DATE",
    "START_TM", "END_TM", "TM_DIV_TP", "CNTG_UNPR12",
]
# 하위 호환 alias
_NOTICE_COLS = _NOTICE_COLS_KR


class _Namespace:
    pass

_hdfsasp0_logged = _Namespace()


class KISWebSocket:
    def __init__(self, token, tickers, on_tick=None, on_notice=None, market="KR"):
        self.token = token
        self.tickers = tickers
        self.market = _normalize_market(market)
        self.on_tick = on_tick or (lambda d: print(f"[tick]{d}"))
        self.on_notice = on_notice  # 체결통보 콜백: on_notice(event_dict)
        self.ws = None
        self._ws_key = None
        self._notice_iv: Optional[str] = None
        self._notice_key: Optional[str] = None
        self._seen_fills: set = set()  # dedupe: (order_no, filled_qty, filled_time)
        self._hts_id: str = os.getenv("KIS_HTS_ID", "")
        self.running: bool = False
        self.started_at: str = ""
        self.last_error: str = ""

    def _get_ws_key(self):
        profile = get_kis_market_profile(self.market)
        resp = _kis_post(
            f"{profile.base_url}/oauth2/Approval",
            json={"grant_type": "client_credentials", "appkey": profile.app_key, "secretkey": profile.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["approval_key"]

    def _sub(self, ticker):
        return json.dumps(
            {
                "header": {
                    "approval_key": self._ws_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {"input": {"tr_id": "H0STCNT0", "tr_key": ticker}},
            }
        )

    def _sub_us(self, ticker: str) -> Optional[str]:
        """해외주식 실시간 현재가 구독 (실전 전용: HDFSASP0).
        tr_key 포맷: D{quote_exch}{ticker}  예) DNYSHIMS, DNASAAPL
        """
        normalized = ticker.upper()
        try:
            exch = _get_ovrs_excg_cd(normalized, token=None)
        except Exception as e:
            log.warning(f"[KIS WS] US 실시간 구독 스킵: {normalized} 거래소 코드 미확인")
            log.debug(f"[KIS WS] US 거래소 코드 resolve 실패 [{normalized}]: {e}")
            return None
        quote_exch = _US_QUOTE_CODE_MAP.get(exch, "NAS")
        rsym = f"D{quote_exch}{normalized}"
        return json.dumps(
            {
                "header": {
                    "approval_key": self._ws_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {"input": {"tr_id": "HDFSASP0", "tr_key": rsym}},
            }
        )

    def _sub_notice(self, market: str = "KR"):
        """계좌 체결통보 구독
        KR: H0STCNI9(모의) / H0STCNI0(실전)
        US: H0GSCNI9(모의) / H0GSCNI0(실전)
        """
        if market == "US":
            tr_id = "H0GSCNI9" if get_kis_market_profile("US").is_paper else "H0GSCNI0"
        else:
            tr_id = "H0STCNI9" if get_kis_market_profile("KR").is_paper else "H0STCNI0"
        return json.dumps(
            {
                "header": {
                    "approval_key": self._ws_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {"input": {"tr_id": tr_id, "tr_key": self._hts_id}},
            }
        )

    def _parse_notice(self, raw_data: str, market: str = "KR") -> Optional[dict]:
        """체결통보 데이터 파싱 (AES 복호화 후 필드 추출)"""
        cols = _NOTICE_COLS_US if market == "US" else _NOTICE_COLS_KR
        try:
            if self._notice_key and self._notice_iv:
                decrypted = _aes_cbc_base64_dec(self._notice_key, self._notice_iv, raw_data)
            else:
                decrypted = raw_data
            fields = decrypted.split("^")
            if len(fields) < len(cols):
                return None
            d = dict(zip(cols, fields))
            # CNTG_YN=2 만 체결통보 (1=접수/정정/취소)
            if d.get("CNTG_YN") != "2":
                return None
            order_no     = d.get("ODER_NO", "").strip()
            filled_qty   = int(d.get("CNTG_QTY", "0") or 0)
            # US는 CNTG_UNPR12(소수점 포함 가격) 우선
            price_field  = "CNTG_UNPR12" if market == "US" else "CNTG_UNPR"
            filled_price = float(d.get(price_field) or d.get("CNTG_UNPR", "0") or 0)
            filled_time  = d.get("STCK_CNTG_HOUR", "").strip()
            ticker       = d.get("STCK_SHRN_ISCD", "").strip()
            side         = "buy" if d.get("SELN_BYOV_CLS", "") == "2" else "sell"
            raw_hash = hashlib.sha256(decrypted.encode("utf-8", errors="ignore")).hexdigest()[:24]
            # transport-level dedupe only. Accounting idempotency is handled by the bot/fill ledger.
            key = (market, order_no, raw_hash)
            if key in self._seen_fills:
                return None
            self._seen_fills.add(key)
            return {
                "order_no":     order_no,
                "ticker":       ticker,
                "filled_qty":   filled_qty,
                "filled_price": filled_price,
                "filled_time":  filled_time,
                "side":         side,
                "market":       market,
                "raw_hash":     raw_hash,
            }
        except Exception as e:
            log.warning(f"[KIS WS] 체결통보 파싱 오류 ({market}): {e}")
            return None

    def start(self):
        import websocket

        self._ws_key = self._get_ws_key()
        profile = get_kis_market_profile(self.market)

        def on_open(ws):
            self.running = True
            if not self.started_at:
                self.started_at = datetime.now().isoformat(timespec="seconds")
            # KR 실시간 시세 구독 (KR 세션만)
            if self.market == "KR":
                for t in self.tickers:
                    ws.send(self._sub(t))
            # US 실시간 시세 구독 (US 세션 + 실전 서버만, VTS 미지원)
            if self.market == "US" and not profile.is_paper:
                subscribed = 0
                for t in self.tickers:
                    msg = self._sub_us(t)
                    if msg:
                        ws.send(msg)
                        subscribed += 1
                log.info(f"[KIS WS] US 실시간 시세 구독 {subscribed}/{len(self.tickers)}종목")
            elif self.market == "US" and profile.is_paper:
                log.info("[KIS WS] US 실시간 시세: VTS 미지원 — API 폴링 사용")
            # 체결통보 구독은 연결된 KIS profile의 market만 등록한다.
            if self.on_notice and self._hts_id:
                ws.send(self._sub_notice(self.market))
                log.info(f"[KIS WS] {self.market} 체결통보 구독 등록 ({'모의' if profile.is_paper else '실전'})")
            elif self.on_notice and not self._hts_id:
                log.warning("[KIS WS] KIS_HTS_ID 미설정 — 체결통보 구독 스킵")

        def on_message(ws, msg):
            # JSON 응답 = 구독 확인 or PINGPONG
            if msg.startswith("{"):
                try:
                    rdic = json.loads(msg)
                    body = rdic.get("body") or {}
                    output = body.get("output") or {}
                    # AES key/iv 수신 (체결통보 구독 확인 시)
                    if output.get("iv") and output.get("key"):
                        self._notice_iv  = output["iv"]
                        self._notice_key = output["key"]
                        log.info("[KIS WS] 체결통보 AES key/iv 수신 완료")
                except Exception:
                    pass
                return

            # 파이프 구분 데이터
            parts = msg.split("|")
            if len(parts) < 4:
                return
            tr_id    = parts[1] if len(parts) > 1 else ""
            raw_data = parts[3]

            # KR 체결통보
            if tr_id in ("H0STCNI9", "H0STCNI0"):
                if self.on_notice:
                    event = self._parse_notice(raw_data, market="KR")
                    if event:
                        log.info(f"[KIS WS] KR 체결통보 수신: {event}")
                        self.on_notice(event)
                return

            # US 체결통보
            if tr_id in ("H0GSCNI9", "H0GSCNI0"):
                if self.on_notice:
                    event = self._parse_notice(raw_data, market="US")
                    if event:
                        log.info(f"[KIS WS] US 체결통보 수신: {event}")
                        self.on_notice(event)
                return

            # US 실시간 시세 tick (HDFSASP0, 실전 전용)
            # RSYM 포맷: D{NAS/NYS/AMS}{ticker}  예) DNYSHIMS
            # fields: [0]RSYM [1]TICKER [2]ZDIV [3]TYMD [4]XHMS [5]KYMD [6]KHMS
            #         [7]? [8]? [9]? [10]DIFF [11]LAST [12]ASK [13]BID_SZ
            #         [14]? [15]ASK_SZ [16]? [17]BID [18]ASK2 [19]TICK_VOL
            if tr_id == "HDFSASP0":
                fields = raw_data.split("^")
                if len(fields) < 20:
                    return
                try:
                    rsym = fields[0]
                    ticker = rsym[4:] if len(rsym) > 4 else rsym  # D+3자리거래소+티커
                    price = float(fields[11])
                    volume = int(float(fields[19]))
                    self.on_tick({"ticker": ticker, "time": fields[6], "price": price, "volume": volume})
                except Exception:
                    pass
                return

            # KR 시세 tick (H0STCNT0 외 TR ID는 무시)
            if tr_id != "H0STCNT0":
                return
            fields = raw_data.split("^")
            if len(fields) < 13:
                return
            try:
                self.on_tick({"ticker": fields[0], "time": fields[1], "price": int(fields[2]), "volume": int(fields[12])})
            except Exception:
                pass

        def on_error(ws, error):
            self.last_error = str(error or "")[:240]
            self.running = False

        def on_close(ws, *args):
            self.running = False

        self.ws = websocket.WebSocketApp(
            _ws_url(self.market),
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        threading.Thread(target=self.ws.run_forever, daemon=True).start()
        self.running = True
        if not self.started_at:
            self.started_at = datetime.now().isoformat(timespec="seconds")

    def stop(self):
        try:
            if self.ws:
                self.ws.close()
        finally:
            self.running = False


if __name__ == "__main__":
    if not APP_KEY:
        print("[error] check .env")
        raise SystemExit(1)
    token = get_access_token()
    print(f"env: {'paper' if IS_PAPER else 'live'}")
    print(get_price("005930", token, market="KR"))
