"""
price_collector.py
국내: KIS API 일봉
미국: Alpha Vantage 일봉

[Alpha Vantage 무료 키]
  - 25회/일 제한, KST 09:00 초기화 (UTC 00:00)
  - outputsize=full > 1회 호출로 전체 히스토리 반환 (날짜 필터는 로컬)
  - 5개 US 종목 전체 수집 = 5회 호출 > 300일이든 800일이든 동일

[사용법]
  python price_collector.py                       # 기본 500일
  python price_collector.py --lookback 800        # 800일
  python price_collector.py --start 2023-01-01    # 특정 시작일
  python price_collector.py --start 2023-01-01 --end 2024-12-31
  python price_collector.py --update              # 오늘까지 누락분만 추가
  python price_collector.py --update --kr-only    # 국내만 최신화
  python price_collector.py --update --us-only    # 미국만 최신화

[저장 경로] (digest_builder.py 기준)
  data/price/kr/kr_{ticker}.csv
  data/price/us/us_{ticker}.csv
"""

import os
import sys
import time
import argparse
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, date
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

BASE_DIR  = Path(__file__).parent.parent
PRICE_DIR = BASE_DIR / "data" / "price"
(PRICE_DIR / "kr").mkdir(parents=True, exist_ok=True)
(PRICE_DIR / "us").mkdir(parents=True, exist_ok=True)

# ── 수집 대상 종목 ────────────────────────────────────────────────────────────

KR_TICKERS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "005380": "현대차",
    "000270": "기아",
    "051910": "LG화학",
    "006400": "삼성SDI",
    "035720": "카카오",
    "068270": "셀트리온",
    "028260": "삼성물산",
    "012330": "현대모비스",
    "003550": "LG",
}

US_TICKERS = {
    "NVDA":  "엔비디아",
    "TSLA":  "테슬라",
    "AAPL":  "애플",
    "MSFT":  "마이크로소프트",
    "AMZN":  "아마존",
    "GOOGL": "알파벳",
    "META":  "메타",
    "AMD":   "AMD",
    "SMCI":  "슈퍼마이크로",
    "PLTR":  "팔란티어",
    "NFLX":  "넷플릭스",
}


# ── KIS 토큰 ──────────────────────────────────────────────────────────────────

_token_cache: dict = {}

def get_kis_token() -> str:
    if _token_cache.get("token") and _token_cache.get("expires"):
        if datetime.now() < _token_cache["expires"]:
            return _token_cache["token"]
    resp = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"]   = data["access_token"]
    _token_cache["expires"] = datetime.now() + timedelta(hours=23)
    return _token_cache["token"]


# ── 국내 주가 수집 ────────────────────────────────────────────────────────────

def _fetch_kr_daily_once(ticker: str, start_yyyymmdd: str, end_yyyymmdd: str) -> pd.DataFrame:
    """KIS API 국내 일봉 단일 호출 (최대 100행 반환)"""
    token = get_kis_token()
    resp = requests.get(
        f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        headers={
            "Content-Type":  "application/json",
            "authorization": f"Bearer {token}",
            "appkey":        KIS_APP_KEY,
            "appsecret":     KIS_APP_SECRET,
            "tr_id":         "FHKST03010100",
        },
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":         ticker,
            "FID_INPUT_DATE_1":       start_yyyymmdd,
            "FID_INPUT_DATE_2":       end_yyyymmdd,
            "FID_PERIOD_DIV_CODE":    "D",
            "FID_ORG_ADJ_PRC":       "0",
        },
        timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json().get("output2", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={
        "stck_bsop_date": "date",
        "stck_oprc":      "open",
        "stck_hgpr":      "high",
        "stck_lwpr":      "low",
        "stck_clpr":      "close",
        "acml_vol":       "volume",
        "prdy_vrss":      "change",
        "prdy_ctrt":      "change_pct",
    })
    cols = ["date", "open", "high", "low", "close", "volume", "change", "change_pct"]
    df = df[[c for c in cols if c in df.columns]].copy()
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.sort_values("date").reset_index(drop=True)


def fetch_kr_daily(ticker: str, start_yyyymmdd: str, end_yyyymmdd: str) -> pd.DataFrame:
    """
    KIS API 국내 일봉 — 100일 단위로 페이지네이션하여 긴 기간 수집
    KIS API는 1회 호출당 최대 100행 반환
    """
    start_dt = datetime.strptime(start_yyyymmdd, "%Y%m%d")
    end_dt   = datetime.strptime(end_yyyymmdd,   "%Y%m%d")
    parts    = []

    chunk_end = end_dt
    while chunk_end >= start_dt:
        chunk_start = max(start_dt, chunk_end - timedelta(days=140))  # 140일 요청 → ~100 거래일
        s = chunk_start.strftime("%Y%m%d")
        e = chunk_end.strftime("%Y%m%d")
        df_chunk = _fetch_kr_daily_once(ticker, s, e)
        if df_chunk.empty:
            break
        parts.append(df_chunk)
        # 다음 청크: 현재 청크 최초일 하루 전까지
        chunk_end = df_chunk["date"].min() - timedelta(days=1)
        if chunk_end < start_dt:
            break
        time.sleep(0.3)

    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df[(df["date"] >= pd.Timestamp(start_dt)) & (df["date"] <= pd.Timestamp(end_dt))]


def collect_kr_incremental(start_dt: pd.Timestamp, end_dt: pd.Timestamp):
    """국내 주가 증분 수집 — 누락 구간만 KIS API 호출"""
    print(f"\n[국내 주가] {start_dt.date()} ~ {end_dt.date()}")

    for ticker, name in KR_TICKERS.items():
        path = PRICE_DIR / "kr" / f"kr_{ticker}.csv"
        try:
            fetch_parts = []

            if path.exists():
                existing = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
                ex_min, ex_max = existing["date"].min(), existing["date"].max()
                print(f"  [{ticker}] 기존: {ex_min.date()} ~ {ex_max.date()} ({len(existing)}일)")

                # < 앞 확장 (예: 500일 > 800일로 늘릴 때)
                if start_dt < ex_min:
                    s = start_dt.strftime("%Y%m%d")
                    e = (ex_min - timedelta(days=1)).strftime("%Y%m%d")
                    df_back = fetch_kr_daily(ticker, s, e)
                    if not df_back.empty:
                        fetch_parts.append(df_back)
                        print(f"         < 이전 {len(df_back)}일 추가")
                    time.sleep(0.5)

                # > 뒤 업데이트 (매일 최신화)
                if end_dt > ex_max:
                    s = (ex_max + timedelta(days=1)).strftime("%Y%m%d")
                    e = end_dt.strftime("%Y%m%d")
                    df_fwd = fetch_kr_daily(ticker, s, e)
                    if not df_fwd.empty:
                        fetch_parts.append(df_fwd)
                        print(f"         > 최신 {len(df_fwd)}일 추가")
                    time.sleep(0.5)

                if not fetch_parts:
                    print(f"         이미 최신 상태 - 스킵")
                    continue

                combined = pd.concat([existing] + fetch_parts)
            else:
                # 최초 수집
                s = start_dt.strftime("%Y%m%d")
                e = end_dt.strftime("%Y%m%d")
                combined = fetch_kr_daily(ticker, s, e)
                time.sleep(0.5)

            _save(path, combined, start_dt, end_dt, f"{name}({ticker})")

        except Exception as ex:
            print(f"  NG {name}({ticker}): {ex}")


# ── 미국 주가 수집 ────────────────────────────────────────────────────────────

def fetch_us_daily_compact(ticker: str) -> pd.DataFrame:
    """
    Alpha Vantage TIME_SERIES_DAILY (outputsize=compact = 최근 100일)
    무료 키: 25회/일, KST 09:00 초기화
    outputsize=full은 유료 전용 → compact 사용
    100일 이상 필요한 경우 기존 CSV와 누적 머지로 확장
    """
    if not AV_KEY:
        print("  WARN  ALPHA_VANTAGE_KEY 없음")
        return pd.DataFrame()

    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={
            "function":   "TIME_SERIES_DAILY",
            "symbol":     ticker,
            "outputsize": "compact",
            "apikey":     AV_KEY,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    ts = data.get("Time Series (Daily)", {})
    if not ts:
        note = data.get("Note") or data.get("Information") or str(data)
        raise ValueError(f"Alpha Vantage 응답 오류: {note}")

    rows = [
        {
            "date":   d,
            "open":   float(v["1. open"]),
            "high":   float(v["2. high"]),
            "low":    float(v["3. low"]),
            "close":  float(v["4. close"]),
            "volume": float(v["5. volume"]),
        }
        for d, v in ts.items()
    ]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def fetch_us_daily_yfinance(ticker: str, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> pd.DataFrame:
    """
    yfinance 폴백 — Alpha Vantage 실패 시 또는 장기 히스토리 수집 시 사용
    pip install yfinance 필요
    """
    try:
        import yfinance as yf
    except ImportError:
        print("  yfinance 미설치: pip install yfinance")
        return pd.DataFrame()

    end_fetch = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.Ticker(ticker).history(
        start=start_dt.strftime("%Y-%m-%d"),
        end=end_fetch,
        auto_adjust=True,
    )
    if df.empty:
        return pd.DataFrame()

    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    return df.sort_values("date").reset_index(drop=True)


def collect_us_incremental(start_dt: pd.Timestamp, end_dt: pd.Timestamp):
    """
    미국 주가 증분 수집
    1순위: Alpha Vantage (AV_KEY 있고 최근 100일이면)
    2순위: yfinance 폴백 (키 없거나 장기 수집 필요 시)
    """
    print(f"\n[미국 주가] {start_dt.date()} ~ {end_dt.date()}")
    need_long = (end_dt - start_dt).days > 100

    if not AV_KEY:
        print("  INFO  ALPHA_VANTAGE_KEY 없음 → yfinance 사용")
    elif need_long:
        print(f"  INFO  {(end_dt-start_dt).days}일 > 100일 → yfinance 사용 (AV compact 한계)")
    else:
        print("  INFO  Alpha Vantage 사용 (25회/일, KST 09:00 초기화)")

    av_calls = 0
    for ticker, name in US_TICKERS.items():
        path = PRICE_DIR / "us" / f"us_{ticker}.csv"
        try:
            today_ts = pd.Timestamp(date.today())

            if path.exists():
                existing = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
                ex_min, ex_max = existing["date"].min(), existing["date"].max()
                already_fresh  = ex_max >= today_ts - timedelta(days=4)
                already_covers = ex_min <= start_dt + timedelta(days=5)
                if already_fresh and already_covers:
                    _save(path, existing, start_dt, end_dt, f"{name}({ticker})")
                    print(f"         이미 최신 — 스킵")
                    continue

            # yfinance 우선 (장기 or AV 키 없을 때)
            if not AV_KEY or need_long:
                print(f"  [{ticker}] yfinance 조회 중...")
                df_new = fetch_us_daily_yfinance(ticker, start_dt, end_dt)
                time.sleep(0.5)
            else:
                print(f"  [{ticker}] Alpha Vantage 조회 중... ({av_calls+1}번째)")
                try:
                    df_new = fetch_us_daily_compact(ticker)
                    av_calls += 1
                except Exception as av_err:
                    print(f"     AV 실패({av_err}) → yfinance 폴백")
                    df_new = fetch_us_daily_yfinance(ticker, start_dt, end_dt)
                if av_calls < len(US_TICKERS):
                    time.sleep(12)

            if not df_new.empty:
                existing_df = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame()
                combined = pd.concat([existing_df, df_new]) if not existing_df.empty else df_new
                _save(path, combined, start_dt, end_dt, f"{name}({ticker})")
            else:
                print(f"  WARN  {name}({ticker}) 데이터 없음")

        except Exception as ex:
            print(f"  NG {name}({ticker}): {ex}")

    if av_calls:
        print(f"\n  총 Alpha Vantage 호출: {av_calls}회 / 25회 한도")


# ── 공통 저장 ─────────────────────────────────────────────────────────────────

def _save(path: Path, df: pd.DataFrame, start_dt: pd.Timestamp, end_dt: pd.Timestamp, label: str):
    """중복 제거 + 기간 필터 + CSV 저장"""
    df = (df
          .drop_duplicates("date")
          .sort_values("date")
          .reset_index(drop=True))
    df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
    if df.empty:
        print(f"  WARN  {label}: 해당 기간 데이터 없음")
        return
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  OK {label}: {len(df)}일 ({df['date'].min().date()} ~ {df['date'].max().date()})")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="주가 데이터 수집기 (증분)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python price_collector.py                    # 기본 500일
  python price_collector.py --lookback 800     # 800일
  python price_collector.py --start 2023-01-01
  python price_collector.py --start 2023-01-01 --end 2024-12-31
  python price_collector.py --update           # 누락분만 추가
  python price_collector.py --update --kr-only
        """,
    )
    parser.add_argument("--lookback", type=int, default=500,
                        help="오늘 기준 수집 일수 (기본: 500일)")
    parser.add_argument("--start", type=str, default=None,
                        help="시작 날짜 YYYY-MM-DD (지정 시 --lookback 무시)")
    parser.add_argument("--end", type=str, default=None,
                        help="종료 날짜 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--update", action="store_true",
                        help="최신화 모드: 누락분만 추가 (기간 설정 무시)")
    parser.add_argument("--kr-only", action="store_true", help="국내만 수집")
    parser.add_argument("--us-only", action="store_true", help="미국만 수집")
    args = parser.parse_args()

    today   = date.today()
    end_dt  = pd.Timestamp(args.end) if args.end else pd.Timestamp(today)

    if args.update:
        # 최신화: 이미 있는 데이터 기준으로 오늘까지만 추가
        # start_dt는 안전하게 넓게 잡되, _incremental 함수가 기존 CSV 보고 판단
        start_dt = pd.Timestamp(today - timedelta(days=args.lookback))
        print("=== 최신화 모드 (누락분만 추가) ===")
    elif args.start:
        start_dt = pd.Timestamp(args.start)
    else:
        start_dt = pd.Timestamp(today - timedelta(days=args.lookback))

    print("=" * 60)
    print(f"  주가 데이터 수집")
    print(f"  기간: {start_dt.date()} ~ {end_dt.date()} ({(end_dt - start_dt).days}일)")
    print(f"  저장: {PRICE_DIR}")
    print("=" * 60)

    do_kr = not args.us_only
    do_us = not args.kr_only

    if do_kr:
        collect_kr_incremental(start_dt, end_dt)
    if do_us:
        collect_us_incremental(start_dt, end_dt)

    print("\n" + "=" * 60)
    print("  수집 완료! 현재 저장된 파일:")
    for p in sorted(PRICE_DIR.rglob("*.csv")):
        try:
            df = pd.read_csv(p)
            print(f"  {p.relative_to(PRICE_DIR)}: {len(df)}행")
        except Exception:
            pass
    print("=" * 60)


if __name__ == "__main__":
    main()
