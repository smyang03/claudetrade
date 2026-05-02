"""
supplement_collector.py - 보조 데이터 수집
외국인/기관 수급, VIX/VKOSPI, 환율, 공매도, 프리마켓

국내: KIS API (외국인/기관 수급)
미국: Alpha Vantage (VIX, 환율)
"""
import os, json, time, requests
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional
from dotenv import load_dotenv
import sys
sys.path.insert(0,str(Path(__file__).parent.parent))
from logger import get_collector_logger, log_retry
load_dotenv()

log      = get_collector_logger()
AV_KEY   = os.getenv("ALPHA_VANTAGE_KEY","")
KIS_KEY  = os.getenv("KIS_APP_KEY","")
KIS_SEC  = os.getenv("KIS_APP_SECRET","")
IS_PAPER = os.getenv("KIS_IS_PAPER","true").lower()=="true"
KIS_BASE = ("https://openapivts.koreainvestment.com:29443" if IS_PAPER
            else "https://openapi.koreainvestment.com:9443")

SUPP_DIR = Path(__file__).parent.parent/"data"/"supplement"
SUPP_DIR.mkdir(parents=True,exist_ok=True)
(SUPP_DIR/"kr").mkdir(exist_ok=True)
(SUPP_DIR/"us").mkdir(exist_ok=True)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _positive_float_or_none(value, *, minimum: float = 0.0) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")) or parsed <= minimum:
        return None
    return parsed


def _metric_result(value=None, source: str = "", fallback_used: bool = False, error: str = "") -> dict:
    return {
        "value": _positive_float_or_none(value),
        "source": source,
        "fallback_used": bool(fallback_used),
        "error": str(error or "")[:160],
    }


def _apply_metric(data: dict, field: str, result: dict) -> None:
    value = result.get("value")
    data[field] = value
    data.setdefault("sources", {})[field] = result.get("source", "")
    data.setdefault("fallback_used", {})[field] = bool(result.get("fallback_used", False))
    if value is None:
        flags = data.setdefault("data_quality_flags", [])
        flag = f"{field}_missing"
        if flag not in flags:
            flags.append(flag)
    if result.get("error"):
        data.setdefault("collection_errors", {})[field] = result.get("error")


def _yf_close_for_date(symbol: str, target_date: str) -> Optional[float]:
    try:
        import yfinance as yf
        start_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
        end_dt = start_dt + timedelta(days=1)
        hist = yf.Ticker(symbol).history(start=start_dt.isoformat(), end=end_dt.isoformat())
        if hist.empty:
            hist = yf.Ticker(symbol).history(period="5d")
        if hist.empty:
            return None
        return _positive_float_or_none(hist["Close"].iloc[-1])
    except Exception:
        return None

def _kis_token():
    resp=requests.post(f"{KIS_BASE}/oauth2/tokenP",
        json={"grant_type":"client_credentials","appkey":KIS_KEY,"appsecret":KIS_SEC})
    resp.raise_for_status()
    return resp.json()["access_token"]

@log_retry(max_retries=3, delay=2.0, logger=log)
def fetch_investor_flow_kr(ticker: str, target_date: str, token: str) -> dict:
    """KIS API - 외국인/기관 수급 (투자자별 매매동향)"""
    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-investor"
    params = {"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":ticker,
              "FID_INPUT_DATE_1":target_date.replace("-",""),
              "FID_INPUT_DATE_2":target_date.replace("-",""),
              "FID_PERIOD_DIV_CODE":"D"}
    headers = {"Content-Type":"application/json",f"authorization":f"Bearer {token}",
               "appkey":KIS_KEY,"appsecret":KIS_SEC,"tr_id":"FHKST01010900"}
    try:
        resp = requests.get(url,headers=headers,params=params,timeout=15)
        resp.raise_for_status()
        output = resp.json().get("output",[{}])
        if not output: return {}
        row = output[0]
        def _toint(v, default=0):
            try:
                return int(v) if v != "" else default
            except (TypeError, ValueError):
                return default
        return {
            "foreign":      _toint(row.get("frgn_ntby_qty")),
            "institution":  _toint(row.get("orgn_ntby_qty")),
            "individual":   _toint(row.get("indv_ntby_qty")),
            "foreign_5d":   0,
        }
    except Exception as e:
        log.debug(f"수급 조회 실패 [{ticker}]: {e}")
        return {}

@log_retry(max_retries=3, delay=12.0, logger=log)
def fetch_vix_detail(target_date: str) -> dict:
    """Alpha Vantage VIX with yfinance fallback."""
    fallback_error = ""
    if AV_KEY:
        try:
            url = "https://www.alphavantage.co/query"
            params = {
                "function": "TIME_SERIES_DAILY",
                "symbol": "VIX",
                "apikey": AV_KEY,
                "outputsize": "compact",
            }
            resp = requests.get(url, params=params, timeout=20)
            ts = resp.json().get("Time Series (Daily)", {})
            row = ts.get(target_date, {})
            value = _positive_float_or_none(row.get("4. close"))
            if value is not None:
                return _metric_result(value, "alpha_vantage:VIX")
        except Exception as e:
            fallback_error = str(e)

    value = _yf_close_for_date("^VIX", target_date)
    return _metric_result(value, "yfinance:^VIX", fallback_used=bool(AV_KEY), error=fallback_error)


def fetch_vix(target_date: str) -> Optional[float]:
    return fetch_vix_detail(target_date).get("value")


@log_retry(max_retries=3, delay=12.0, logger=log)
def fetch_usd_krw_detail(target_date: str) -> dict:
    """USD/KRW via Alpha Vantage with yfinance fallback."""
    fallback_error = ""
    if AV_KEY:
        try:
            url = "https://www.alphavantage.co/query"
            params = {
                "function": "FX_DAILY",
                "from_symbol": "USD",
                "to_symbol": "KRW",
                "apikey": AV_KEY,
                "outputsize": "compact",
            }
            resp = requests.get(url, params=params, timeout=20)
            ts = resp.json().get("Time Series FX (Daily)", {})
            row = ts.get(target_date, {})
            rate = _positive_float_or_none(row.get("4. close"), minimum=100)
            if rate is not None:
                return _metric_result(rate, "alpha_vantage:FX_DAILY")
        except Exception as e:
            fallback_error = str(e)

    rate = _yf_close_for_date("USDKRW=X", target_date)
    if rate is not None and rate > 100:
        return _metric_result(
            round(rate, 2),
            "yfinance:USDKRW=X",
            fallback_used=bool(AV_KEY),
            error=fallback_error,
        )
    source = "alpha_vantage,yfinance:USDKRW=X" if AV_KEY else "yfinance:USDKRW=X"
    return _metric_result(None, source, fallback_used=bool(AV_KEY), error=fallback_error)


def fetch_usd_krw(target_date: str) -> Optional[float]:
    return fetch_usd_krw_detail(target_date).get("value")


@log_retry(max_retries=3, delay=12.0, logger=log)
def fetch_dxy_detail(target_date: str) -> dict:
    """DXY daily close via yfinance."""
    value = _yf_close_for_date("DX-Y.NYB", target_date)
    return _metric_result(value, "yfinance:DX-Y.NYB")


def fetch_dxy(target_date: str) -> Optional[float]:
    return fetch_dxy_detail(target_date).get("value")


@log_retry(max_retries=3, delay=12.0, logger=log)
def fetch_vkospi_detail(target_date: str) -> dict:
    """VKOSPI daily close via yfinance symbols."""
    for symbol in ("^KS200VOL", "^VKOSPI"):
        value = _yf_close_for_date(symbol, target_date)
        if value is not None:
            return _metric_result(value, f"yfinance:{symbol}", fallback_used=(symbol != "^KS200VOL"))
    return _metric_result(None, "yfinance:^KS200VOL,^VKOSPI", fallback_used=True)


def fetch_vkospi(target_date: str) -> Optional[float]:
    return fetch_vkospi_detail(target_date).get("value")

def collect_kr_supplement(target_date: str):
    path = SUPP_DIR/"kr"/f"{target_date}.json"
    if path.exists():
        log.debug(f"[SKIP] KR supplement {target_date}")
        return
    log.info(f"[KR supplement] {target_date}")
    data = {
        "date": target_date,
        "collected_at": _now_iso(),
        "flows": {},
        "usd_krw": None,
        "vkospi": None,
        "data_quality_flags": [],
        "sources": {},
        "fallback_used": {},
        "collection_errors": {},
    }
    KR_FLOW_TICKERS = [
        "005930","000660","035420","005380","000270",
        "051910","006400","035720","068270","028260","012330","003550",
    ]
    try:
        token = _kis_token()
        for ticker in KR_FLOW_TICKERS:
            flow = fetch_investor_flow_kr(ticker, target_date, token)
            data["flows"][ticker] = flow
            time.sleep(0.5)
    except Exception as e:
        log.warning(f"KIS 수급 실패: {e}")
    try:
        _apply_metric(data, "usd_krw", fetch_usd_krw_detail(target_date))
        time.sleep(12)
    except Exception as e:
        _apply_metric(data, "usd_krw", _metric_result(None, "collector_exception", error=str(e)))
    try:
        _apply_metric(data, "vkospi", fetch_vkospi_detail(target_date))
    except Exception as e:
        _apply_metric(data, "vkospi", _metric_result(None, "collector_exception", error=str(e)))
    with open(path,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
    log.info(f"  ✅ KR supplement 저장: {path.name}")

def collect_us_supplement(target_date: str):
    path = SUPP_DIR/"us"/f"{target_date}.json"
    if path.exists(): return
    log.info(f"[US supplement] {target_date}")
    data = {
        "date": target_date,
        "collected_at": _now_iso(),
        "vix": None,
        "dxy": None,
        "oil_wti": None,
        "fomc": False,
        "cpi_day": False,
        "nfp_day": False,
        "data_quality_flags": [],
        "sources": {},
        "fallback_used": {},
        "collection_errors": {},
    }
    try:
        _apply_metric(data, "vix", fetch_vix_detail(target_date))
        time.sleep(12)
    except Exception as e:
        _apply_metric(data, "vix", _metric_result(None, "collector_exception", error=str(e)))
    try:
        _apply_metric(data, "dxy", fetch_dxy_detail(target_date))
    except Exception as e:
        _apply_metric(data, "dxy", _metric_result(None, "collector_exception", error=str(e)))
    _apply_metric(data, "oil_wti", _metric_result(None, "not_configured"))
    with open(path,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
    log.info(f"  ✅ US supplement 저장: {path.name}")

def collect_range(start: str, end: str, market: str = "ALL"):
    from datetime import datetime, timedelta
    s = datetime.strptime(start,"%Y-%m-%d").date()
    e = datetime.strptime(end,"%Y-%m-%d").date()
    days = []
    cur = s
    while cur <= e:
        if cur.weekday()<5: days.append(cur.strftime("%Y-%m-%d"))
        cur+=timedelta(days=1)
    log.info(f"supplement 수집: {len(days)}일 {market}")
    for d in days:
        if market in ("KR","ALL"): collect_kr_supplement(d)
        if market in ("US","ALL"): collect_us_supplement(d)
        time.sleep(1)

if __name__=="__main__":
    test = (date.today()-timedelta(days=1)).strftime("%Y-%m-%d")
    collect_kr_supplement(test)
    collect_us_supplement(test)
