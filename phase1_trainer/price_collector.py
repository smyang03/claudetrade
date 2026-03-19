"""
price_collector.py
국내: KIS API 일봉 (최대 600일)
미국: Alpha Vantage 일봉 (무료 500회/일)

사전 준비:
  pip install requests python-dotenv pandas

.env:
  KIS_APP_KEY=...
  KIS_APP_SECRET=...
  KIS_IS_PAPER=true
  ALPHA_VANTAGE_KEY=...   ← https://alphavantage.co 무료 발급
"""

import os
import time
import json
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── 설정 ──────────────────────────────────────────────────────────────────────

KIS_APP_KEY    = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
IS_PAPER       = os.getenv("KIS_IS_PAPER", "true").lower() == "true"
AV_KEY         = os.getenv("ALPHA_VANTAGE_KEY", "")

KIS_BASE = (
    "https://openapivts.koreainvestment.com:29443"
    if IS_PAPER else
    "https://openapi.koreainvestment.com:9443"
)

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── 학습 대상 종목 ────────────────────────────────────────────────────────────

KR_TICKERS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "005380": "현대차",
    "051910": "LG화학",
}

US_TICKERS = {
    "NVDA": "엔비디아",
    "TSLA": "테슬라",
    "AAPL": "애플",
    "MSFT": "마이크로소프트",
    "META": "메타",
}

# 지수 (참조용)
KR_INDEX = "0001"   # 코스피
US_INDEX = "SPY"    # S&P500 ETF


# ── KIS 토큰 ──────────────────────────────────────────────────────────────────

_token_cache = {}

def get_kis_token() -> str:
    if _token_cache.get("token") and _token_cache.get("expires"):
        if datetime.now() < _token_cache["expires"]:
            return _token_cache["token"]

    url  = f"{KIS_BASE}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey":     KIS_APP_KEY,
        "appsecret":  KIS_APP_SECRET,
    }
    resp = requests.post(url, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"]   = data["access_token"]
    _token_cache["expires"] = datetime.now() + timedelta(hours=23)
    return _token_cache["token"]


# ── 국내 주가 수집 (KIS 일봉) ─────────────────────────────────────────────────

def fetch_kr_daily(ticker: str, start: str, end: str) -> pd.DataFrame:
    """
    KIS API 국내주식 기간별시세 (일봉)
    start/end: 'YYYYMMDD'
    """
    token  = get_kis_token()
    url    = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    tr_id  = "FHKST03010100"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD":         ticker,
        "FID_INPUT_DATE_1":       start,
        "FID_INPUT_DATE_2":       end,
        "FID_PERIOD_DIV_CODE":    "D",
        "FID_ORG_ADJ_PRC":       "0",
    }
    headers = {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        KIS_APP_KEY,
        "appsecret":     KIS_APP_SECRET,
        "tr_id":         tr_id,
    }
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    rows = data.get("output2", [])
    if not rows:
        print(f"  [{ticker}] 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "stck_bsop_date": "date",
        "stck_oprc":      "open",
        "stck_hgpr":      "high",
        "stck_lwpr":      "low",
        "stck_clpr":      "close",
        "acml_vol":       "volume",
        "prdy_vrss":      "change",
        "prdy_ctrt":      "change_pct",
    })
    cols = ["date","open","high","low","close","volume","change","change_pct"]
    df   = df[[c for c in cols if c in df.columns]].copy()
    for c in ["open","high","low","close","volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values("date").reset_index(drop=True)
    return df


def collect_kr_prices(start: str, end: str):
    """국내 전 종목 수집 후 CSV 저장"""
    print(f"\n[국내 주가 수집] {start} ~ {end}")
    for ticker, name in KR_TICKERS.items():
        try:
            df = fetch_kr_daily(ticker, start.replace("-",""), end.replace("-",""))
            if len(df) > 0:
                path = DATA_DIR / f"kr_{ticker}.csv"
                df.to_csv(path, index=False, encoding="utf-8-sig")
                print(f"  ✅ {name}({ticker}): {len(df)}일 → {path.name}")
            time.sleep(0.5)   # API 호출 간격
        except Exception as e:
            print(f"  ❌ {name}({ticker}): {e}")


# ── 미국 주가 수집 (Alpha Vantage) ───────────────────────────────────────────

def fetch_us_daily(ticker: str, outputsize: str = "full") -> pd.DataFrame:
    """
    Alpha Vantage TIME_SERIES_DAILY
    outputsize: 'compact'(100일) | 'full'(20년)
    무료 키: 25회/일 제한
    """
    url = "https://www.alphavantage.co/query"
    params = {
        "function":   "TIME_SERIES_DAILY",
        "symbol":     ticker,
        "outputsize": outputsize,
        "apikey":     AV_KEY,
        "datatype":   "json",
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    if "Time Series (Daily)" not in data:
        msg = data.get("Note") or data.get("Information") or "알 수 없는 오류"
        raise ValueError(f"Alpha Vantage 오류: {msg}")

    ts  = data["Time Series (Daily)"]
    rows = []
    for date_str, vals in ts.items():
        rows.append({
            "date":   date_str,
            "open":   float(vals["1. open"]),
            "high":   float(vals["2. high"]),
            "low":    float(vals["3. low"]),
            "close":  float(vals["4. close"]),
            "volume": float(vals["5. volume"]),
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def collect_us_prices(start: str, end: str):
    """미국 전 종목 수집 후 CSV 저장 (Alpha Vantage 무료 키: 25회/일)"""
    print(f"\n[미국 주가 수집] {start} ~ {end}")

    if not AV_KEY:
        print("  ⚠️  ALPHA_VANTAGE_KEY 없음 → .env 파일에 추가 필요")
        print("      무료 발급: https://alphavantage.co/support/#api-key")
        return

    start_dt = pd.to_datetime(start)
    end_dt   = pd.to_datetime(end)

    for ticker, name in US_TICKERS.items():
        try:
            df = fetch_us_daily(ticker, outputsize="full")
            # 기간 필터
            df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
            if len(df) > 0:
                path = DATA_DIR / f"us_{ticker}.csv"
                df.to_csv(path, index=False, encoding="utf-8-sig")
                print(f"  ✅ {name}({ticker}): {len(df)}일 → {path.name}")
            # 무료 API 호출 제한: 12초 대기 (5회/분)
            print(f"     API 대기 중 (12초)...")
            time.sleep(12)
        except Exception as e:
            print(f"  ❌ {name}({ticker}): {e}")
            time.sleep(12)


# ── 코스피 지수 수집 ──────────────────────────────────────────────────────────

def fetch_kospi_daily(start: str, end: str) -> pd.DataFrame:
    """코스피 지수 일봉"""
    token  = get_kis_token()
    url    = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD":         "0001",
        "FID_INPUT_DATE_1":       start,
        "FID_INPUT_DATE_2":       end,
        "FID_PERIOD_DIV_CODE":    "D",
    }
    headers = {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {get_kis_token()}",
        "appkey":        KIS_APP_KEY,
        "appsecret":     KIS_APP_SECRET,
        "tr_id":         "FHKUP03500100",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("output2", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "bsop_date": "date",
        "bstp_nmix_oprc": "open",
        "bstp_nmix_hgpr": "high",
        "bstp_nmix_lwpr": "low",
        "bstp_nmix_prpr": "close",
        "acml_vol":       "volume",
        "bstp_nmix_prdy_ctrt": "change_pct",
    })
    cols = ["date","open","high","low","close","volume","change_pct"]
    df   = df[[c for c in cols if c in df.columns]].copy()
    for c in ["open","high","low","close","volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values("date").reset_index(drop=True)
    return df


# ── 전체 수집 실행 ────────────────────────────────────────────────────────────

def collect_all(kr_start="2024-10-01", kr_end="2026-03-19",
                us_start="2025-01-01", us_end="2026-03-19"):
    """
    Phase 1 학습용 전체 데이터 수집
    국내: 2024-10-01 ~ 현재 (하락+반등+강세)
    미국: 2025-01-01 ~ 현재 (조정+강세+횡보+현재)
    """
    print("=" * 55)
    print("   Phase 1 학습용 주가 데이터 수집 시작")
    print("=" * 55)

    # 국내
    collect_kr_prices(kr_start, kr_end)

    # 코스피 지수
    try:
        print("\n[코스피 지수 수집]")
        df_kospi = fetch_kospi_daily(
            kr_start.replace("-",""), kr_end.replace("-","")
        )
        if len(df_kospi) > 0:
            path = DATA_DIR / "kr_kospi.csv"
            df_kospi.to_csv(path, index=False, encoding="utf-8-sig")
            print(f"  ✅ 코스피: {len(df_kospi)}일")
    except Exception as e:
        print(f"  ❌ 코스피 지수: {e}")

    # 미국
    collect_us_prices(us_start, us_end)

    print("\n" + "=" * 55)
    print("   수집 완료!")
    print(f"   저장 위치: {DATA_DIR}")
    files = list(DATA_DIR.glob("*.csv"))
    print(f"   파일 수: {len(files)}개")
    for f in sorted(files):
        df = pd.read_csv(f)
        print(f"   {f.name}: {len(df)}행")
    print("=" * 55)


# ── 데이터 로드 유틸 ──────────────────────────────────────────────────────────

def load_kr(ticker: str) -> pd.DataFrame:
    path = DATA_DIR / f"kr_{ticker}.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} 없음. collect_all() 먼저 실행하세요.")
    df = pd.read_csv(path, parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_us(ticker: str) -> pd.DataFrame:
    path = DATA_DIR / f"us_{ticker}.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} 없음. collect_all() 먼저 실행하세요.")
    df = pd.read_csv(path, parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_kospi() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "kr_kospi.csv", parse_dates=["date"])


if __name__ == "__main__":
    # 실행 시 전체 수집
    collect_all(
        kr_start="2024-10-01",
        kr_end="2026-03-19",
        us_start="2025-01-01",
        us_end="2026-03-19",
    )
