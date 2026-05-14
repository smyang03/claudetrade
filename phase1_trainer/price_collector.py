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
import logging
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
    # ── 반도체 ────────────────────────────────────────────────────────────────
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "006400": "삼성SDI",
    "051910": "LG화학",
    "066570": "LG전자",
    "018260": "삼성에스디에스",
    # ── 반도체 장비 / 소재 ────────────────────────────────────────────────────
    "042700": "한미반도체",
    "240810": "원익IPS",
    "357780": "솔브레인",
    "102710": "이오테크닉스",
    "058470": "리노공업",
    "036540": "SFA반도체",
    "011790": "SKC",
    "078600": "대주전자재료",
    "046890": "서울반도체",
    "095610": "선진뷰티사이언스",  # 실제 확인 필요시 교체
    # ── 2차전지 ───────────────────────────────────────────────────────────────
    "373220": "LG에너지솔루션",
    "247540": "에코프로비엠",
    "086520": "에코프로",
    "047050": "포스코퓨처엠",
    "336370": "솔브레인홀딩스",
    # ── 로봇 ──────────────────────────────────────────────────────────────────
    "090360": "로보스타",
    "108380": "로보티즈",
    "277990": "유진로봇",
    "336260": "두산테스나",
    # ── 방산 / 항공우주 ───────────────────────────────────────────────────────
    "012450": "한화에어로스페이스",
    "079550": "LIG넥스원",
    "047810": "한국항공우주",
    "010140": "삼성중공업",
    # ── 자동차 ────────────────────────────────────────────────────────────────
    "005380": "현대차",
    "000270": "기아",
    "012330": "현대모비스",
    "073240": "금호타이어",
    # ── IT / 플랫폼 ───────────────────────────────────────────────────────────
    "035420": "NAVER",
    "035720": "카카오",
    "293490": "카카오뱅크",
    "036570": "엔씨소프트",
    "251270": "넷마블",
    "263750": "펄어비스",
    # ── 금융 ──────────────────────────────────────────────────────────────────
    "105560": "KB금융",
    "055550": "신한지주",
    "086790": "하나금융지주",
    "316140": "우리금융지주",
    "032830": "삼성생명",
    "006800": "미래에셋증권",
    "016360": "삼성증권",
    "071050": "한국금융지주",
    # ── 바이오 / 헬스케어 ─────────────────────────────────────────────────────
    "068270": "셀트리온",
    "207940": "삼성바이오로직스",
    "128940": "한미약품",
    "326030": "SK바이오팜",
    "000100": "유한양행",
    "006280": "녹십자",
    "196170": "알테오젠",
    "145020": "휴젤",
    # ── 통신 ──────────────────────────────────────────────────────────────────
    "017670": "SK텔레콤",
    "030200": "KT",
    "032640": "LG유플러스",
    # ── 에너지 / 소재 ─────────────────────────────────────────────────────────
    "096770": "SK이노베이션",
    "010950": "S-Oil",
    "005490": "POSCO홀딩스",
    "011780": "금호석유",
    "180640": "한화솔루션",
    "010120": "LS ELECTRIC",
    "112610": "씨에스윈드",
    "010060": "OCI홀딩스",
    # ── 조선 / 중공업 ─────────────────────────────────────────────────────────
    "009540": "HD한국조선해양",
    "042660": "한화오션",
    "034020": "두산에너빌리티",
    # ── 건설 ──────────────────────────────────────────────────────────────────
    "000720": "현대건설",
    "028260": "삼성물산",
    "047040": "대우건설",
    # ── 유통 / 소비 ───────────────────────────────────────────────────────────
    "139480": "이마트",
    "282330": "BGF리테일",
    "000080": "하이트진로",
    "271560": "오리온",
    # ── 철강 / 화학 ───────────────────────────────────────────────────────────
    "004020": "현대제철",
    "011170": "롯데케미칼",
    "017900": "고려아연",
    # ── 항공 / 운송 ───────────────────────────────────────────────────────────
    "003490": "대한항공",
    "011200": "HMM",
    # ── 엔터테인먼트 ──────────────────────────────────────────────────────────
    "035900": "JYP엔터테인먼트",
    "041510": "SM엔터테인먼트",
    "122870": "와이지엔터테인먼트",
    # ── 기타 대형주 ───────────────────────────────────────────────────────────
    "003550": "LG",
    "009830": "한화",
    "000150": "두산",
}

US_TICKERS = {
    # ── 빅테크 ────────────────────────────────────────────────────────────────
    "AAPL":  "애플",
    "MSFT":  "마이크로소프트",
    "GOOGL": "알파벳",
    "AMZN":  "아마존",
    "META":  "메타",
    "NVDA":  "엔비디아",
    "TSLA":  "테슬라",
    "NFLX":  "넷플릭스",
    "ORCL":  "오라클",
    "CRM":   "세일즈포스",
    # ── 반도체 ────────────────────────────────────────────────────────────────
    "AMD":   "AMD",
    "INTC":  "인텔",
    "QCOM":  "퀄컴",
    "AVGO":  "브로드컴",
    "MU":    "마이크론",
    "MRVL":  "마벨테크놀로지",
    "AMAT":  "어플라이드머티리얼즈",
    "LRCX":  "램리서치",
    "KLAC":  "KLA",
    "SMCI":  "슈퍼마이크로",
    "ALAB":  "아스테라랩스",
    "MCHP":  "마이크로칩테크놀로지",
    # ── AI / 데이터 ───────────────────────────────────────────────────────────
    "PLTR":  "팔란티어",
    "SOUN":  "사운드하운드AI",
    "AI":    "C3.ai",
    "PATH":  "UiPath",
    "DDOG":  "데이터독",
    "SNOW":  "스노우플레이크",
    "BBAI":  "BigBear.ai",
    # ── 클라우드 / 사이버보안 ─────────────────────────────────────────────────
    "CRWD":  "크라우드스트라이크",
    "PANW":  "팔로알토",
    "ZS":    "지스케일러",
    "OKTA":  "옥타",
    "NET":   "클라우드플레어",
    "NTNX":  "뉴타닉스",
    "FTNT":  "포티넷",
    # ── 로봇 / 자율주행 / 우주 ───────────────────────────────────────────────
    "RKLB":  "로켓랩",
    "JOBY":  "조비에비에이션",
    "ACHR":  "아처에비에이션",
    "SPCE":  "버진갤럭틱",
    # ── 바이오테크 / 헬스케어 ────────────────────────────────────────────────
    "LLY":   "일라이릴리",
    "ABBV":  "애브비",
    "JNJ":   "존슨앤드존슨",
    "MRK":   "머크",
    "PFE":   "화이자",
    "UNH":   "유나이티드헬스",
    "ISRG":  "인튜이티브서지컬",
    "MRNA":  "모더나",
    "BNTX":  "바이오엔텍",
    "REGN":  "리제네론",
    "VRTX":  "버텍스파마",
    "GILD":  "길리어드",
    "BIIB":  "바이오젠",
    "HIMS":  "힘스앤허스",
    "RXRX":  "리커전파마슈티컬즈",
    # ── 금융 / 핀테크 ─────────────────────────────────────────────────────────
    "JPM":   "JP모건",
    "GS":    "골드만삭스",
    "BAC":   "뱅크오브아메리카",
    "MS":    "모건스탠리",
    "V":     "비자",
    "MA":    "마스터카드",
    "HOOD":  "로빈후드",
    "SOFI":  "소파이",
    "COIN":  "코인베이스",
    # ── 에너지 ────────────────────────────────────────────────────────────────
    "XOM":   "엑슨모빌",
    "CVX":   "쉐브론",
    "COP":   "코노코필립스",
    "OXY":   "옥시덴탈",
    "SLB":   "슐럼버거",
    # ── 방산 / 항공우주 ───────────────────────────────────────────────────────
    "LMT":   "록히드마틴",
    "RTX":   "레이시온",
    "NOC":   "노스롭그루만",
    "BA":    "보잉",
    "GE":    "GE에어로스페이스",
    # ── 산업재 ────────────────────────────────────────────────────────────────
    "CAT":   "캐터필러",
    "HON":   "허니웰",
    "DE":    "존디어",
    # ── EV / 클린에너지 ───────────────────────────────────────────────────────
    "RIVN":  "리비안",
    "LCID":  "루시드",
    "ENPH":  "엔페이즈에너지",
    "FSLR":  "퍼스트솔라",
    "NEE":   "넥스트에라에너지",
    # ── 소비재 / 리테일 ───────────────────────────────────────────────────────
    "COST":  "코스트코",
    "MCD":   "맥도날드",
    "SBUX":  "스타벅스",
    "NKE":   "나이키",
    "DIS":   "디즈니",
    "ABNB":  "에어비앤비",
    "UBER":  "우버",
    # ── ETF (시장 레짐 파악용) ────────────────────────────────────────────────
    "SPY":   "S&P500 ETF",
    "QQQ":   "나스닥100 ETF",
    "IWM":   "러셀2000 ETF",
    "SOXL":  "반도체 3배 ETF",
    "TQQQ":  "나스닥 3배 ETF",
    "SPDN":  "S&P500 인버스 ETF",
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


def _normalize_date_window(df: pd.DataFrame, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.tz_localize(None)
    start = pd.Timestamp(start_dt)
    end = pd.Timestamp(end_dt)
    if start.tzinfo is not None:
        start = start.tz_localize(None)
    if end.tzinfo is not None:
        end = end.tz_localize(None)
    out = out[out["date"].notna()]
    out = out[(out["date"] >= start) & (out["date"] <= end)]
    return out.sort_values("date").reset_index(drop=True)


def _has_weekday_between(start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> bool:
    start = pd.Timestamp(start_dt).normalize()
    end = pd.Timestamp(end_dt).normalize()
    if start > end:
        return False
    return any(day.weekday() < 5 for day in pd.date_range(start, end, freq="D"))


def _expected_trading_days(market: str, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> tuple[list[pd.Timestamp], str]:
    start = pd.Timestamp(start_dt).normalize()
    end = pd.Timestamp(end_dt).normalize()
    if start > end:
        return [], "empty"
    market_key = str(market or "").upper()
    try:
        import exchange_calendars as ec

        calendar = ec.get_calendar("XKRX" if market_key == "KR" else "XNYS")
        sessions = calendar.sessions_in_range(start, end)
        return [pd.Timestamp(day).tz_localize(None).normalize() for day in sessions], "exchange_calendars"
    except Exception:
        return [pd.Timestamp(day).normalize() for day in pd.date_range(start, end, freq="D") if day.weekday() < 5], "weekday_fallback"


def _audit_csv_date_gaps(df: pd.DataFrame, market: str) -> dict:
    if df.empty or "date" not in df.columns:
        return {"calendar_source": "none", "gaps": [], "duplicate_dates": 0}
    dates = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None).dropna().dt.normalize()
    if dates.empty:
        return {"calendar_source": "none", "gaps": [], "duplicate_dates": 0}
    duplicate_dates = int(dates.duplicated().sum())
    expected, source = _expected_trading_days(market, dates.min(), dates.max())
    present = set(pd.Timestamp(day).normalize() for day in dates)
    gaps = [day for day in expected if day not in present]
    return {
        "calendar_source": source,
        "gaps": gaps,
        "duplicate_dates": duplicate_dates,
        "warning_only": source == "weekday_fallback",
    }


def _gap_ranges(gaps: list[pd.Timestamp]) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if not gaps:
        return []
    ordered = sorted(pd.Timestamp(day).normalize() for day in gaps)
    ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    start = prev = ordered[0]
    for day in ordered[1:]:
        if (day - prev).days <= 3:
            prev = day
            continue
        ranges.append((start, prev))
        start = prev = day
    ranges.append((start, prev))
    return ranges


def fetch_kr_daily_yfinance(ticker: str, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> pd.DataFrame:
    """yfinance 폴백 — KIS API 실패 시 사용 (KOSPI: .KS, KOSDAQ: .KQ)"""
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame()
    if pd.Timestamp(start_dt) > pd.Timestamp(end_dt):
        return pd.DataFrame()
    yfinance_logger = logging.getLogger("yfinance")
    old_level = yfinance_logger.level
    yfinance_logger.setLevel(logging.CRITICAL)
    # 대부분 KOSPI 상장 — .KS 시도 후 실패 시 .KQ
    try:
        for suffix in [".KS", ".KQ"]:
            end_fetch = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            df = yf.Ticker(f"{ticker}{suffix}").history(
                start=start_dt.strftime("%Y-%m-%d"),
                end=end_fetch,
                auto_adjust=True,
            )
            if not df.empty:
                df = df.reset_index()
                df.columns = [c.lower() for c in df.columns]
                df = df[["date", "open", "high", "low", "close", "volume"]].copy()
                return _normalize_date_window(df, start_dt, end_dt)
    finally:
        yfinance_logger.setLevel(old_level)
    return pd.DataFrame()


def collect_kr_incremental(start_dt: pd.Timestamp, end_dt: pd.Timestamp):
    """국내 주가 증분 수집 — KIS API 우선, 실패 시 yfinance 폴백

    KR_TICKERS 목록 + 기존 price/kr/ 디렉터리에 있는 CSV 종목 모두 처리.
    기존 CSV가 있으면 시작일 소급 + 최신화를 동시에 수행.
    """
    print(f"\n[국내 주가] {start_dt.date()} ~ {end_dt.date()}")

    # KR_TICKERS + 기존 CSV 병합 (이미 있는 종목도 소급 대상)
    all_tickers = dict(KR_TICKERS)
    kr_price_dir = PRICE_DIR / "kr"
    for p in kr_price_dir.glob("kr_*.csv"):
        code = p.stem[3:]  # "kr_005930" → "005930"
        if code not in all_tickers:
            all_tickers[code] = code  # 이름 모를 경우 코드로 대체

    print(f"  대상 종목: {len(all_tickers)}개 (KR_TICKERS {len(KR_TICKERS)} + 기존CSV 추가)")

    for ticker, name in all_tickers.items():
        path = PRICE_DIR / "kr" / f"kr_{ticker}.csv"
        try:
            fetch_parts = []

            if path.exists():
                existing = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
                ex_min, ex_max = existing["date"].min(), existing["date"].max()
                print(f"  [{ticker}] 기존: {ex_min.date()} ~ {ex_max.date()} ({len(existing)}일)")
                gap_audit = _audit_csv_date_gaps(existing, "KR")
                if gap_audit["duplicate_dates"]:
                    print(f"         WARN duplicate date rows: {gap_audit['duplicate_dates']}")
                for gap_start, gap_end in _gap_ranges(gap_audit["gaps"]):
                    try:
                        df_gap = fetch_kr_daily(ticker, gap_start.strftime("%Y%m%d"), gap_end.strftime("%Y%m%d"))
                    except Exception:
                        df_gap = pd.DataFrame()
                    df_gap = _normalize_date_window(df_gap, gap_start, gap_end)
                    if df_gap.empty:
                        df_gap = fetch_kr_daily_yfinance(ticker, gap_start, gap_end)
                    df_gap = _normalize_date_window(df_gap, gap_start, gap_end)
                    if not df_gap.empty:
                        fetch_parts.append(df_gap)
                        print(f"         gap {gap_start.date()} ~ {gap_end.date()} {len(df_gap)} rows added")
                    else:
                        print(
                            f"         WARN gap fetch failed {gap_start.date()} ~ {gap_end.date()} "
                            f"calendar={gap_audit['calendar_source']}"
                        )

                # < 앞 확장 (예: 500일 > 800일로 늘릴 때)
                if start_dt < ex_min:
                    s = start_dt.strftime("%Y%m%d")
                    e = (ex_min - timedelta(days=1)).strftime("%Y%m%d")
                    try:
                        df_back = fetch_kr_daily(ticker, s, e)
                    except Exception:
                        df_back = pd.DataFrame()
                    if df_back.empty:
                        df_back = fetch_kr_daily_yfinance(ticker, start_dt, ex_min - timedelta(days=1))
                    df_back = _normalize_date_window(df_back, start_dt, ex_min - timedelta(days=1))
                    if not df_back.empty:
                        fetch_parts.append(df_back)
                        print(f"         < 이전 {len(df_back)}일 추가")
                    time.sleep(0.5)

                # > 뒤 업데이트 (매일 최신화)
                if end_dt > ex_max:
                    fwd_start = ex_max + timedelta(days=1)
                    if not _has_weekday_between(fwd_start, end_dt):
                        print(f"         no weekday in update window ({fwd_start.date()} ~ {end_dt.date()}) - skip")
                    else:
                        s = fwd_start.strftime("%Y%m%d")
                        e = end_dt.strftime("%Y%m%d")
                        try:
                            df_fwd = fetch_kr_daily(ticker, s, e)
                        except Exception:
                            df_fwd = pd.DataFrame()
                        df_fwd = _normalize_date_window(df_fwd, fwd_start, end_dt)
                        if df_fwd.empty:
                            df_fwd = fetch_kr_daily_yfinance(ticker, fwd_start, end_dt)
                        df_fwd = _normalize_date_window(df_fwd, fwd_start, end_dt)
                        if not df_fwd.empty:
                            fetch_parts.append(df_fwd)
                            print(f"         > 최신 {len(df_fwd)}일 추가")
                        time.sleep(0.5)

                if not fetch_parts:
                    print(f"         이미 최신 상태 - 스킵")
                    continue

                combined = pd.concat([existing] + fetch_parts)
            else:
                # 최초 수집 — KIS 시도 후 실패 시 yfinance
                s = start_dt.strftime("%Y%m%d")
                e = end_dt.strftime("%Y%m%d")
                try:
                    combined = fetch_kr_daily(ticker, s, e)
                except Exception:
                    combined = pd.DataFrame()
                if combined.empty:
                    print(f"  [{ticker}] KIS 실패 → yfinance 폴백")
                    combined = fetch_kr_daily_yfinance(ticker, start_dt, end_dt)
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
    미국 주가 증분 수집 — yfinance 우선 (장기 히스토리)
    US_TICKERS 목록 + 기존 price/us/ 디렉터리 CSV 모두 처리.
    기존 CSV가 있으면 시작일 소급 + 최신화를 동시에 수행.
    """
    print(f"\n[미국 주가] {start_dt.date()} ~ {end_dt.date()}")

    # US_TICKERS + 기존 CSV 병합
    all_tickers = dict(US_TICKERS)
    us_price_dir = PRICE_DIR / "us"
    for p in us_price_dir.glob("us_*.csv"):
        sym = p.stem[3:]  # "us_NVDA" → "NVDA"
        if sym not in all_tickers:
            all_tickers[sym] = sym

    print(f"  대상 종목: {len(all_tickers)}개 (US_TICKERS {len(US_TICKERS)} + 기존CSV 추가)")
    print(f"  데이터 소스: yfinance (장기 히스토리)")

    for ticker, name in all_tickers.items():
        path = PRICE_DIR / "us" / f"us_{ticker}.csv"
        try:
            today_ts = pd.Timestamp(date.today())

            if path.exists():
                existing = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
                ex_min, ex_max = existing["date"].min(), existing["date"].max()
                gap_audit = _audit_csv_date_gaps(existing, "US")
                gap_ranges = _gap_ranges(gap_audit["gaps"])
                if gap_audit["duplicate_dates"]:
                    print(f"  [{ticker}] WARN duplicate date rows: {gap_audit['duplicate_dates']}")
                if gap_ranges:
                    print(f"  [{ticker}] internal gap detected ({len(gap_ranges)} ranges, calendar={gap_audit['calendar_source']})")
                    gap_parts = []
                    for gap_start, gap_end in gap_ranges:
                        df_gap = fetch_us_daily_yfinance(ticker, gap_start, gap_end)
                        df_gap = _normalize_date_window(df_gap, gap_start, gap_end)
                        if not df_gap.empty:
                            gap_parts.append(df_gap)
                    if gap_parts:
                        combined = pd.concat([existing] + gap_parts)
                        _save(path, combined, start_dt, end_dt, f"{name}({ticker})")
                        existing = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
                        ex_min, ex_max = existing["date"].min(), existing["date"].max()
                    else:
                        print(f"  WARN  {name}({ticker}) internal gap fetch failed")
                already_fresh  = ex_max >= today_ts - timedelta(days=4)
                already_covers = ex_min <= start_dt + timedelta(days=5)
                if already_fresh and already_covers:
                    print(f"  [{ticker}] 이미 최신 — 스킵")
                    continue

            print(f"  [{ticker}] yfinance 조회 중...")
            df_new = fetch_us_daily_yfinance(ticker, start_dt, end_dt)
            time.sleep(0.3)

            if not df_new.empty:
                existing_df = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame()
                combined = pd.concat([existing_df, df_new]) if not existing_df.empty else df_new
                _save(path, combined, start_dt, end_dt, f"{name}({ticker})")
            else:
                print(f"  WARN  {name}({ticker}) 데이터 없음")

        except Exception as ex:
            print(f"  NG {name}({ticker}): {ex}")



# ── 공통 저장 ─────────────────────────────────────────────────────────────────

def _save(path: Path, df: pd.DataFrame, start_dt: pd.Timestamp, end_dt: pd.Timestamp, label: str):
    """중복 제거 + 기간 필터 + CSV 저장"""
    if df.empty or "date" not in df.columns:
        print(f"  WARN  {label}: no price rows returned")
        return
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None)
    df = df[df["date"].notna()]
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


# ── 스크리너 풀 사전 수집 (Method 2) ──────────────────────────────────────────

def collect_screener_pool(market: str = "US", lookback_days: int = 90, top_n: int = 50, mode: str = "NEUTRAL"):
    """
    새벽 스케줄러용 — 스크리너 상위 N종목 OHLCV 사전 수집.

    흐름:
      1. screen_market_us() / screen_market_kr() → 당일 핫 종목 top_n
      2. 각 종목 yfinance로 lookback_days 수집
      3. CSV 저장 (이미 최신이면 스킵)

    실행:
      python price_collector.py --screener --market US
      python price_collector.py --screener --market KR
    """
    sys.path.insert(0, str(BASE_DIR))

    today = date.today()
    start_dt = pd.Timestamp(today - timedelta(days=lookback_days))
    end_dt   = pd.Timestamp(today)

    print(f"\n[스크리너 풀 사전수집] {market} | top_n={top_n} | {start_dt.date()}~{end_dt.date()}")

    # 스크리너 후보 가져오기
    if market == "US":
        try:
            from kis_api import get_screening_preset, screen_market_us
            candidates = screen_market_us(top_n=top_n, mode=mode)
            us_preset = get_screening_preset("US", mode)
        except Exception as e:
            print(f"  FMP 스크리너 실패: {e} → 폴백 유니버스 사용")
            candidates = [{"ticker": t} for t in [
                "NVDA","TSLA","AAPL","GOOGL","NFLX",
                "JPM","GS","XOM","CVX","LLY","ABBV","CAT","GE",
                "META","AMZN","MSFT","AMD","PLTR","NFLX","ORCL",
            ]]
            us_preset = {"min_price": 5.0, "min_dollar_vol": 15_000_000.0}
    else:
        # KR: KIS 토큰 필요 → 없으면 폴백
        try:
            from kis_api import screen_market_kr, get_access_token
            token = get_access_token()
            candidates = screen_market_kr(token, top_n=top_n)
        except Exception as e:
            print(f"  KIS 스크리너 실패: {e} → 폴백 유니버스 사용")
            candidates = [{"ticker": t} for t in [
                "005930","000660","035420","035720","005380","051910",
                "000270","068270","105560","055550","006400","003550",
                "028260","012330","066570","035000","018260","032830",
            ]]

    # 2차 품질 필터 — 스크리너 필터 뚫린 것 최후 차단
    tickers = []
    for c in candidates:
        t = c.get("ticker", "")
        if not t:
            continue
        if market == "US":
            # US: 알파벳만, 5자 이하, 워런트/유닛/권리주 제외
            if not t.isalpha() or len(t) > 5:
                continue
            if t[-1] in {"W", "U", "R"}:
                continue
            price = float(c.get("price", 0))
            vol = int(c.get("volume", 0) or 0)
            vol_missing = bool(c.get("volume_missing", False))
            if price < float(us_preset.get("min_price", 5.0)):
                continue
            if (
                not vol_missing
                and float(us_preset.get("min_dollar_vol", 0.0)) > 0
                and price * vol < float(us_preset.get("min_dollar_vol", 0.0))
            ):
                continue
        else:
            # KR: 6자리 숫자 코드
            if not t.isdigit() or len(t) != 6:
                continue
        tickers.append(t)
    print(f"  후보 {len(tickers)}종목: {tickers[:10]}{'...' if len(tickers)>10 else ''}")

    # US 신규 종목 거래소 코드 사전 resolve → exchange_cache.json 저장
    if market == "US":
        try:
            from kis_api import (
                _US_EXCHANGE_CACHE, _US_EXCHANGE_MAP,
                _get_ovrs_excg_cd, _save_exchange_cache,
            )
            known = set(_US_EXCHANGE_CACHE) | {t for ts in _US_EXCHANGE_MAP.values() for t in ts}
            new_tickers = [t for t in tickers if t not in known]
            if new_tickers:
                print(f"  [거래소 resolve] 신규 {len(new_tickers)}종목: {new_tickers}")
                resolved = 0
                for t in new_tickers:
                    try:
                        _get_ovrs_excg_cd(t)
                        resolved += 1
                    except Exception as e:
                        print(f"    {t}: resolve 실패 ({e})")
                    time.sleep(0.2)
                if resolved:
                    _save_exchange_cache()
                    print(f"  [거래소 resolve] {resolved}종목 저장 완료")
        except Exception as e:
            print(f"  [거래소 resolve] 건너뜀: {e}")

    collected, skipped, failed = 0, 0, 0
    for ticker in tickers:
        path = PRICE_DIR / market.lower() / f"{market.lower()}_{ticker}.csv"
        try:
            # 이미 최신 상태면 스킵
            if path.exists():
                existing = pd.read_csv(path)
                existing = existing[
                    existing["date"].notna() &
                    (existing["date"].astype(str).str.strip() != ".0")
                ].sort_values("date")
                existing["date"] = pd.to_datetime(existing["date"], format="%Y-%m-%d")
                last_date = existing["date"].max()
                if (end_dt - last_date).days <= 3:
                    skipped += 1
                    continue

            if market == "US":
                df_new = fetch_us_daily_yfinance(ticker, start_dt, end_dt)
            else:
                suffix_map = {  # KOSPI/KOSDAQ 자동 판별
                    "A": ".KS", "0": ".KS", "1": ".KS", "2": ".KS",
                    "3": ".KQ", "4": ".KQ", "5": ".KS", "6": ".KS",
                    "7": ".KS", "8": ".KS", "9": ".KS",
                }
                # yfinance KR: 6자리 숫자.KS 또는 .KQ
                import yfinance as yf
                df_new = pd.DataFrame()
                for sfx in [".KS", ".KQ"]:
                    df_new = fetch_kr_daily_yfinance(ticker, start_dt, end_dt)
                    if not df_new.empty:
                        break
                    # 직접 시도
                    try:
                        _h = yf.Ticker(f"{ticker}{sfx}").history(
                            start=start_dt.strftime("%Y-%m-%d"),
                            end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
                            auto_adjust=True,
                        )
                        if not _h.empty:
                            _h = _h.reset_index()
                            _h.columns = [c.lower() for c in _h.columns]
                            if "date" in _h.columns:
                                _h["date"] = pd.to_datetime(_h["date"]).dt.tz_localize(None)
                            df_new = _h[["date","open","high","low","close","volume"]].copy()
                            df_new = df_new.sort_values("date").reset_index(drop=True)
                            break
                    except Exception:
                        continue

            if df_new.empty:
                failed += 1
                continue

            # 기존 CSV와 머지
            if path.exists():
                existing = pd.read_csv(path)
                existing.columns = [c.lower() for c in existing.columns]
                existing["date"] = pd.to_datetime(existing["date"])
                df_new = pd.concat([existing, df_new])

            _save(path, df_new, start_dt, end_dt, f"{market}:{ticker}")
            collected += 1
            time.sleep(0.3)

        except Exception as ex:
            print(f"  NG {market}:{ticker}: {ex}")
            failed += 1

    print(f"\n  완료: 수집={collected}  스킵(최신)={skipped}  실패={failed}")
    return collected


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
    parser.add_argument("--screener", action="store_true",
                        help="스크리너 풀 사전수집 모드 (새벽 스케줄러용)")
    parser.add_argument("--top-n", type=int, default=50,
                        help="--screener 모드: 스크리너 상위 N종목 (기본: 50)")
    args = parser.parse_args()

    # ── 스크리너 풀 모드 ────────────────────────────────────────────────────────
    if args.screener:
        do_kr = not args.us_only
        do_us = not args.kr_only
        if do_us:
            collect_screener_pool("US", lookback_days=90, top_n=args.top_n)
        if do_kr:
            collect_screener_pool("KR", lookback_days=90, top_n=args.top_n)
        return

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
