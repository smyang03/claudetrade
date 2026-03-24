"""
kis_api.py
KIS API (KR) + Finnhub/FMP/yfinance (US quote/candles/screener).
"""

import os
import json
import time
import requests
import threading
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from runtime_paths import get_runtime_path

load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY", "")
APP_SECRET = os.getenv("KIS_APP_SECRET", "")
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
IS_PAPER = os.getenv("KIS_IS_PAPER", "true").lower() == "true"
AV_KEY       = os.getenv("ALPHA_VANTAGE_KEY", "")
AV_KEY_2     = os.getenv("ALPHA_VANTAGE_KEY_2", "")
FINNHUB_KEY  = os.getenv("FINNHUB_API_KEY", "")
FMP_KEY      = os.getenv("FMP_API_KEY", "").strip()

# 계좌번호 포맷 검증: "XXXXXXXXXX-XX" 형태여야 함
if ACCOUNT_NO and "-" not in ACCOUNT_NO:
    raise ValueError(
        f"KIS_ACCOUNT_NO 포맷 오류: '{ACCOUNT_NO}' — 'XXXXXXXXXX-XX' 형식으로 입력하세요."
    )

BASE_URL = os.getenv(
    "KIS_BASE_URL",
    (
    "https://openapivts.koreainvestment.com:29443"
    if IS_PAPER
    else "https://openapi.koreainvestment.com:9443"
    ),
)
WS_URL = (
    "ws://ops.koreainvestment.com:31000"
    if IS_PAPER
    else "ws://ops.koreainvestment.com:21000"
)
TOKEN_FILE = get_runtime_path("state", "kis_token.json")
KIS_HTTP_TIMEOUT = float(os.getenv("KIS_HTTP_TIMEOUT", "10"))
KIS_TOKEN_RETRY = int(os.getenv("KIS_TOKEN_RETRY", "3"))


def load_token():
    if not TOKEN_FILE.exists():
        return None
    with open(TOKEN_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if datetime.now() < datetime.fromisoformat(data["expires_at"]) - timedelta(minutes=10):
        return data
    return None


def save_token(token, expires_in):
    expires_at = (datetime.now() + timedelta(seconds=expires_in)).isoformat()
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "expires_at": expires_at}, f)


def get_access_token():
    cached = load_token()
    if cached:
        return cached["access_token"]
    if not APP_KEY or not APP_SECRET:
        raise RuntimeError("KIS_APP_KEY/KIS_APP_SECRET 값이 비어 있습니다. .env를 확인하세요.")

    last_error = None
    for attempt in range(1, max(1, KIS_TOKEN_RETRY) + 1):
        try:
            resp = requests.post(
                f"{BASE_URL}/oauth2/tokenP",
                json={"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET},
                timeout=KIS_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            save_token(data["access_token"], int(data.get("expires_in", 86400)))
            return data["access_token"]
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < KIS_TOKEN_RETRY:
                time.sleep(1.5 * attempt)
        except requests.exceptions.RequestException as e:
            last_error = e
            break

    raise RuntimeError(
        "KIS 토큰 발급 연결 실패. "
        f"URL={BASE_URL}/oauth2/tokenP, timeout={KIS_HTTP_TIMEOUT}s, retries={KIS_TOKEN_RETRY}. "
        "망/방화벽에서 KIS 도메인(210.107.75.32) 29443/9443 포트 차단 여부를 확인하고, "
        "필요 시 KIS_BASE_URL/KIS_HTTP_TIMEOUT 값을 조정하세요."
    ) from last_error


def _headers(token, tr_id=""):
    h = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }
    if tr_id:
        h["tr_id"] = tr_id
    return h


def get_hashkey(body, token):
    resp = requests.post(f"{BASE_URL}/uapi/hashkey", headers=_headers(token), json=body, timeout=10)
    resp.raise_for_status()
    return resp.json()["HASH"]


def _get_price_kr(ticker, token):
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    tr_id = "FHKST01010100"  # 시세 조회는 모의/실거래 공통 TR
    resp = requests.get(
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
            "volume": 0, "open": price, "high": price, "low": price,
        }
    except Exception:
        return {
            "ticker": ticker, "name": ticker,
            "price": 0, "change": 0, "change_rate": 0.0,
            "volume": 0, "open": 0, "high": 0, "low": 0,
        }


def get_price(ticker, token, market="KR"):
    if market == "US":
        # 1차: Finnhub (무료 무제한)
        try:
            return _get_price_us_finnhub(ticker)
        except Exception:
            pass
        # 2차: yfinance
        try:
            return _get_price_us_yf(ticker)
        except Exception:
            pass
        # 3차: Alpha Vantage (레거시 최후 폴백)
        return _get_price_us_alpha(ticker)
    try:
        return _get_price_kr(ticker, token)
    except Exception as e:
        import logging
        logging.getLogger("trading").warning(f"KIS 가격 조회 실패 [{ticker}] → yfinance 폴백: {e}")
        return _get_price_kr_yf(ticker)


def _daily_ohlcv_kr(ticker, token, lookback_days=200):
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=max(lookback_days, 30) * 2)
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
    resp = requests.get(url, headers=headers, params=params, timeout=15)
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
        # yfinance 우선 (무제한) → AV 레거시 폴백
        try:
            df = _daily_ohlcv_us_yf(ticker, lookback_days=lookback_days)
        except Exception:
            df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        if not df.empty:
            return df
        return _daily_ohlcv_us_alpha(ticker, lookback_days=lookback_days)
    return _daily_ohlcv_kr(ticker, token, lookback_days=lookback_days)


def get_index_change(market: str) -> float:
    """당일 지수 등락율 (%) — yfinance 사용 (^KS11=KOSPI, ^GSPC=S&P500)"""
    try:
        import yfinance as yf
        symbol = "^KS11" if market == "KR" else "^GSPC"
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


def get_balance(token, market="KR"):
    if market == "US":
        # US account path is broker-specific; keep this safe fallback for now.
        return {"stocks": [], "total_eval": 0, "cash": 0, "total_profit": 0, "profit_rate": 0.0}

    acnt_no, acnt_prdt = ACCOUNT_NO.split("-")
    tr_id = "VTTC8908R" if IS_PAPER else "TTTC8908R"
    resp = requests.get(
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
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
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
    s2 = data.get("output2", [{}])[0]
    return {
        "stocks": stocks,
        "total_eval": int(s2.get("scts_evlu_amt", 0)),
        "cash": int(s2.get("dnca_tot_amt", 0)),
        "total_profit": int(s2.get("evlu_pfls_smtl_amt", 0)),
        "profit_rate": float(s2.get("asst_icdc_erng_rt", 0)),
    }


def _place_order_kr(ticker, qty, price, side, token):
    acnt_no, acnt_prdt = ACCOUNT_NO.split("-")
    tr_map = {
        ("buy", True): "VTTC0802U",
        ("sell", True): "VTTC0801U",
        ("buy", False): "TTTC0802U",
        ("sell", False): "TTTC0801U",
    }
    body = {
        "CANO": acnt_no,
        "ACNT_PRDT_CD": acnt_prdt,
        "PDNO": ticker,
        "ORD_DVSN": "01" if price == 0 else "00",
        "ORD_QTY": str(qty),
        "ORD_UNPR": str(price),
    }
    headers = _headers(token, tr_map[(side, IS_PAPER)])
    headers["hashkey"] = get_hashkey(body, token)
    resp = requests.post(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash",
        headers=headers,
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    r = resp.json()
    return {"success": r.get("rt_cd") == "0", "msg": r.get("msg1", ""), "order_no": r.get("output", {}).get("ODNO", "")}


def place_order(ticker, qty, price, side, token, market="KR"):
    if market == "US":
        return {"success": False, "msg": "US live order path is not implemented", "order_no": ""}
    return _place_order_kr(ticker, qty, price, side, token)


# ── 시장 스크리너 ──────────────────────────────────────────────────────────────

_US_FALLBACK_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN",
    "GOOGL", "META", "AMD", "SMCI", "PLTR",
    "NFLX", "ORCL", "CRM", "SNOW", "UBER",
]


def screen_market_kr(token: str, top_n: int = 30) -> list:
    """
    KR 시장 스크리닝 — KIS 거래량 순위 API
    반환: [{ticker, name, price, change_rate, volume, vol_ratio}]
    장 외 시간이나 API 실패 시 빈 리스트 반환 (호출부에서 폴백 처리)
    """
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE":  "20171",
        "FID_INPUT_ISCD":         "0000",
        "FID_DIV_CLS_CODE":       "0",
        "FID_BLNG_CLS_CODE":      "0",
        "FID_TRGT_CLS_CODE":      "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1":      "",
        "FID_INPUT_PRICE_2":      "",
        "FID_VOL_CNT":            "100000",
        "FID_INPUT_DATE_1":       "",
    }
    try:
        resp = requests.get(
            url,
            headers=_headers(token, "FHPST01710000"),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("output", [])
        result = []
        for it in items[:top_n]:
            ticker = it.get("mksc_shrn_iscd", "").strip()
            if not ticker or not ticker.isdigit():
                continue
            try:
                result.append({
                    "ticker": ticker,
                    "name": it.get("hts_kor_isnm", ticker),
                    "price": int(it.get("stck_prpr", 0)),
                    "change_rate": float(it.get("prdy_ctrt", 0)),
                    "volume": int(it.get("acml_vol", 0)),
                    "vol_ratio": float(it.get("vol_tnrt", 1.0)),
                })
            except (ValueError, TypeError):
                continue
        return result
    except Exception as e:
        return []


_US_SCREEN_CACHE_PATH = get_runtime_path("state", "us_screen_cache.json")

# 레거시 캐시 경로 (이전 버전 호환)
_AV_CACHE_PATH = _US_SCREEN_CACHE_PATH


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


def _fmp_screen_candidates() -> list:
    """FMP stable 엔드포인트로 US 스크리너 후보 수집 (250회/일 무료)"""
    if not FMP_KEY:
        raise RuntimeError("FMP_API_KEY 없음")
    base = "https://financialmodelingprep.com/stable"
    endpoints = ["biggest-gainers", "most-actives", "biggest-losers"]
    candidates = []
    seen: set = set()
    for ep in endpoints:
        try:
            resp = requests.get(
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
                if not ticker or ticker in seen or not ticker.isalpha():
                    continue
                seen.add(ticker)
                try:
                    candidates.append({
                        "ticker": ticker,
                        "name": item.get("name", ticker),
                        "price": float(item.get("price", 0)),
                        "change_rate": float(item.get("changesPercentage", 0)),
                        "volume": _extract_us_volume(item),
                        "vol_ratio": 1.0,
                    })
                except (ValueError, TypeError):
                    continue
        except Exception:
            continue
    return candidates


def screen_market_us(top_n: int = 30) -> list:
    """
    US 시장 스크리닝 — FMP biggest-gainers/most-actives/biggest-losers (우선)
    - 당일 캐시 파일이 있으면 API 호출 없이 재사용
    - FMP 실패 시 AV 레거시 폴백 → 하드코딩 유니버스 최후 폴백
    반환: [{ticker, name, price, change_rate, volume, vol_ratio}]
    """
    import logging as _log
    _logger = _log.getLogger("trading_system")
    today = datetime.now().strftime("%Y-%m-%d")

    # ── 당일 캐시 확인 ────────────────────────────────────────────────────────
    if _US_SCREEN_CACHE_PATH.exists():
        try:
            cached = json.loads(_US_SCREEN_CACHE_PATH.read_text(encoding="utf-8"))
            if cached.get("date") == today and cached.get("candidates"):
                candidates = cached["candidates"]
                if cached.get("source") != "fmp" or _has_meaningful_candidate_volume(candidates):
                    return candidates[:top_n]
                _logger.warning("[US 스크리너] zero-volume FMP cache 무시 후 재조회")
        except Exception:
            pass

    # ── 1차: FMP ─────────────────────────────────────────────────────────────
    try:
        candidates = _fmp_screen_candidates()
        if candidates:
            _US_SCREEN_CACHE_PATH.write_text(
                json.dumps({"date": today, "candidates": candidates, "source": "fmp"},
                           ensure_ascii=False),
                encoding="utf-8",
            )
            return candidates[:top_n]
    except Exception as e:
        _logger.warning(f"[FMP 스크리너] 실패: {e}")

    # ── 2차: Alpha Vantage (KEY_1 소진 시 KEY_2 자동 전환) ───────────────────
    if AV_KEY or AV_KEY_2:
        try:
            data = _av_get({"function": "TOP_GAINERS_LOSERS"})
            candidates = []
            seen: set = set()
            for section in ("most_actively_traded", "top_gainers", "top_losers"):
                for item in data.get(section, []):
                    ticker = item.get("ticker", "").strip()
                    if not ticker or ticker in seen or not ticker.isalpha():
                        continue
                    seen.add(ticker)
                    try:
                        candidates.append({
                            "ticker": ticker, "name": ticker,
                            "price": float(item.get("price", 0)),
                            "change_rate": float(
                                str(item.get("change_percentage", "0")).replace("%", "")
                            ),
                            "volume": _extract_us_volume(item),
                            "vol_ratio": 1.0,
                        })
                    except (ValueError, TypeError):
                        continue
            if candidates:
                _US_SCREEN_CACHE_PATH.write_text(
                    json.dumps({"date": today, "candidates": candidates, "source": "av"},
                               ensure_ascii=False),
                    encoding="utf-8",
                )
                return candidates[:top_n]
        except Exception:
            pass

    # ── 3차: 하드코딩 폴백 유니버스 ──────────────────────────────────────────
    return [
        {"ticker": t, "name": t, "price": 0.0, "change_rate": 0.0,
         "volume": 0, "vol_ratio": 1.0}
        for t in _US_FALLBACK_UNIVERSE
    ]


class KISWebSocket:
    def __init__(self, token, tickers, on_tick=None, market="KR"):
        self.token = token
        self.tickers = tickers
        self.market = market
        self.on_tick = on_tick or (lambda d: print(f"[tick]{d}"))
        self.ws = None
        self._ws_key = None

    def _get_ws_key(self):
        resp = requests.post(
            f"{BASE_URL}/oauth2/Approval",
            json={"grant_type": "client_credentials", "appkey": APP_KEY, "secretkey": APP_SECRET},
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

    def start(self):
        if self.market != "KR":
            # US websocket routing is broker-specific; keep polling path only.
            return

        import websocket

        self._ws_key = self._get_ws_key()

        def on_open(ws):
            for t in self.tickers:
                ws.send(self._sub(t))

        def on_message(ws, msg):
            if msg.startswith("{"):
                return
            parts = msg.split("|")
            if len(parts) < 4:
                return
            fields = parts[3].split("^")
            if len(fields) < 13:
                return
            self.on_tick({"ticker": fields[0], "time": fields[1], "price": int(fields[2]), "volume": int(fields[12])})

        self.ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
        threading.Thread(target=self.ws.run_forever, daemon=True).start()

    def stop(self):
        if self.ws:
            self.ws.close()


if __name__ == "__main__":
    if not APP_KEY:
        print("[error] check .env")
        raise SystemExit(1)
    token = get_access_token()
    print(f"env: {'paper' if IS_PAPER else 'live'}")
    print(get_price("005930", token, market="KR"))
