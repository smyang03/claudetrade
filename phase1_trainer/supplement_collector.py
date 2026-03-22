"""
supplement_collector.py - 보조 데이터 수집
외국인/기관 수급, VIX/VKOSPI, 환율, 공매도, 프리마켓

국내: KIS API (외국인/기관 수급)
미국: Alpha Vantage (VIX, 환율)
"""
import os, json, time, requests
from pathlib import Path
from datetime import datetime, date, timedelta
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
        return {
            "foreign":      int(row.get("frgn_ntby_qty",0)),
            "institution":  int(row.get("orgn_ntby_qty",0)),
            "individual":   int(row.get("indv_ntby_qty",0)),
            "foreign_5d":   0,
        }
    except Exception as e:
        log.debug(f"수급 조회 실패 [{ticker}]: {e}")
        return {}

@log_retry(max_retries=3, delay=12.0, logger=log)
def fetch_vix(target_date: str) -> float:
    """Alpha Vantage - VIX"""
    if not AV_KEY: return 0.0
    url = "https://www.alphavantage.co/query"
    params = {"function":"TIME_SERIES_DAILY","symbol":"VIX",
              "apikey":AV_KEY,"outputsize":"compact"}
    try:
        resp = requests.get(url,params=params,timeout=20)
        data = resp.json()
        ts   = data.get("Time Series (Daily)",{})
        row  = ts.get(target_date,{})
        return float(row.get("4. close",0))
    except: return 0.0

@log_retry(max_retries=3, delay=12.0, logger=log)
def fetch_usd_krw(target_date: str) -> float:
    """환율 USD/KRW (AlphaVantage 우선 → yfinance 폴백)"""
    # 1차: AlphaVantage FX_DAILY (역사 데이터)
    if AV_KEY:
        try:
            url = "https://www.alphavantage.co/query"
            params = {"function": "FX_DAILY", "from_symbol": "USD", "to_symbol": "KRW",
                      "apikey": AV_KEY, "outputsize": "compact"}
            resp = requests.get(url, params=params, timeout=20)
            ts   = resp.json().get("Time Series FX (Daily)", {})
            row  = ts.get(target_date, {})
            rate = float(row.get("4. close", 0))
            if rate > 100:
                return rate
        except Exception:
            pass

    # 2차: yfinance (최근 데이터 / 무료)
    try:
        import yfinance as yf
        hist = yf.Ticker("USDKRW=X").history(start=target_date, end=target_date)
        if hist.empty:
            # 당일 데이터가 없으면 최근 1일 조회
            hist = yf.Ticker("USDKRW=X").history(period="5d")
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            if rate > 100:
                return round(rate, 2)
    except Exception:
        pass

    return 0.0

def collect_kr_supplement(target_date: str):
    path = SUPP_DIR/"kr"/f"{target_date}.json"
    if path.exists():
        log.debug(f"[SKIP] KR supplement {target_date}")
        return
    log.info(f"[KR supplement] {target_date}")
    data = {"date": target_date, "flows": {}, "usd_krw": 0, "vkospi": 0}
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
        data["usd_krw"] = fetch_usd_krw(target_date)
        time.sleep(12)
    except: pass
    with open(path,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
    log.info(f"  ✅ KR supplement 저장: {path.name}")

def collect_us_supplement(target_date: str):
    path = SUPP_DIR/"us"/f"{target_date}.json"
    if path.exists(): return
    log.info(f"[US supplement] {target_date}")
    data = {"date":target_date,"vix":0,"dxy":0,"oil_wti":0,
            "fomc":False,"cpi_day":False,"nfp_day":False}
    try:
        data["vix"] = fetch_vix(target_date)
        time.sleep(12)
    except: pass
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
