"""
kis_api.py
KIS API (KR) + AlphaVantage fallback (US quote/candles).
"""

import os
import json
import requests
import threading
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY", "")
APP_SECRET = os.getenv("KIS_APP_SECRET", "")
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
IS_PAPER = os.getenv("KIS_IS_PAPER", "true").lower() == "true"
AV_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

BASE_URL = (
    "https://openapivts.koreainvestment.com:29443"
    if IS_PAPER
    else "https://openapi.koreainvestment.com:9443"
)
WS_URL = (
    "ws://ops.koreainvestment.com:31000"
    if IS_PAPER
    else "ws://ops.koreainvestment.com:21000"
)
TOKEN_FILE = "kis_token.json"


def load_token():
    if not os.path.exists(TOKEN_FILE):
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
    resp = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    save_token(data["access_token"], int(data.get("expires_in", 86400)))
    return data["access_token"]


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
    tr_id = "VTTC8434R" if IS_PAPER else "FHKST01010100"
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


def _get_price_us_alpha(ticker):
    if not AV_KEY:
        raise RuntimeError("ALPHA_VANTAGE_KEY is required for US quote")
    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": AV_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    q = resp.json().get("Global Quote", {})
    if not q:
        raise RuntimeError(f"US quote unavailable for {ticker}")
    return {
        "ticker": ticker,
        "name": ticker,
        "price": int(float(q.get("05. price", 0))),
        "change": int(float(q.get("09. change", 0))),
        "change_rate": float(q.get("10. change percent", "0%").replace("%", "")),
        "volume": int(float(q.get("06. volume", 0))),
        "open": int(float(q.get("02. open", 0))),
        "high": int(float(q.get("03. high", 0))),
        "low": int(float(q.get("04. low", 0))),
    }


def get_price(ticker, token, market="KR"):
    if market == "US":
        return _get_price_us_alpha(ticker)
    return _get_price_kr(ticker, token)


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


def _daily_ohlcv_us_alpha(ticker, lookback_days=200):
    if not AV_KEY:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "TIME_SERIES_DAILY", "symbol": ticker, "outputsize": "full", "apikey": AV_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    ts = resp.json().get("Time Series (Daily)", {})
    if not ts:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    rows = []
    for d, v in ts.items():
        rows.append(
            {
                "date": d,
                "open": float(v["1. open"]),
                "high": float(v["2. high"]),
                "low": float(v["3. low"]),
                "close": float(v["4. close"]),
                "volume": float(v["5. volume"]),
            }
        )
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.sort_values("date").tail(lookback_days).reset_index(drop=True)


def get_daily_ohlcv(ticker, token, lookback_days=200, market="KR"):
    if market == "US":
        return _daily_ohlcv_us_alpha(ticker, lookback_days=lookback_days)
    return _daily_ohlcv_kr(ticker, token, lookback_days=lookback_days)


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
