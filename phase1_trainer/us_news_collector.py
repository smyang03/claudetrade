"""
us_news_collector.py - 미국 뉴스/공시 수집

소스:
  1. Alpha Vantage News API  (무료 25회/일)
  2. SEC EDGAR               (완전 무료, 공시)
  3. Fed Reserve             (FOMC 일정/결정)
  4. Finnhub API             (무료, 뉴스 백업)

수집 데이터:
  - 날짜별 종목 뉴스 헤드라인
  - SEC 공시 (10-Q, 8-K 등 실적/이벤트)
  - FOMC 발표일 여부
  - 저장: data/news/us/YYYYMMDD.json

.env:
  ALPHA_VANTAGE_KEY=...  ← alphavantage.co 무료 발급
  FINNHUB_KEY=...        ← finnhub.io 무료 발급 (선택)
"""

from __future__ import annotations

import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_collector_logger, log_retry, log_call, ProgressLogger
from kis_api import get_access_token, _headers, _kis_get, _get_us_quote_codes, get_kis_market_profile
from runtime_paths import get_runtime_path

load_dotenv()

log = get_collector_logger()

# ── 설정 ──────────────────────────────────────────────────────────────────────

AV_KEY      = os.getenv("ALPHA_VANTAGE_KEY", "")
FINNHUB_KEY = (
    os.getenv("FINNHUB_API_KEY", "").strip()
    or os.getenv("FINNHUB_KEY", "").strip()
)

# Alpha Vantage 무료 한도 초과 시 당일 재시도 중단
_AV_EXHAUSTED_DATE = ""

NEWS_DIR = Path(__file__).parent.parent / "data" / "news" / "us"
NEWS_DIR.mkdir(parents=True, exist_ok=True)

TARGET_TICKERS = {
    # Core 5
    "NVDA":  "엔비디아",
    "TSLA":  "테슬라",
    "AAPL":  "애플",
    "GOOGL": "알파벳",
    "NFLX":  "넷플릭스",
    # Tier 2 섹터 플레이 후보 (뉴스만 수집, 매일 분석 아님)
    "JPM":   "JP모건",
    "GS":    "골드만삭스",
    "XOM":   "엑슨모빌",
    "CVX":   "쉐브론",
    "LLY":   "일라이릴리",
    "ABBV":  "애브비",
    "CAT":   "캐터필러",
    "GE":    "GE에어로스페이스",
    # 시장 지수
    "SPY":   "S&P500 ETF",
    "QQQ":   "나스닥100 ETF",
}


def _normalize_us_targets(targets: dict[str, str] | None) -> dict[str, str]:
    raw_targets = targets if targets is not None else TARGET_TICKERS
    normalized: dict[str, str] = {}
    for ticker, name in raw_targets.items():
        symbol = str(ticker or "").strip().upper()
        if symbol and symbol not in normalized:
            normalized[symbol] = str(name or symbol).strip() or symbol
    return normalized


def _read_news_file(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_existing_if_reusable(path: Path, target_tickers: list[str], force: bool) -> dict | None:
    if force or not path.exists():
        return None
    data = _read_news_file(path)
    if not data:
        return None
    existing_tickers = data.get("target_tickers") or list((data.get("corp_news") or {}).keys())
    existing_tickers = [str(t).upper() for t in existing_tickers]
    if not target_tickers or existing_tickers == target_tickers:
        log.info(f"[SKIP] US news file exists and force=False: {path.name}")
        return data
    log.warning(
        f"US news file target mismatch; recollecting {path.name} "
        f"existing={len(existing_tickers)} requested={len(target_tickers)}"
    )
    return None


def _provider_key(source: str) -> str:
    text = str(source or "").strip()
    if text.startswith("KIS"):
        return "KIS"
    if text.startswith("Finnhub"):
        return "Finnhub"
    if text.startswith("SEC"):
        return "SEC EDGAR"
    if text.startswith("AlphaVantage"):
        return "AlphaVantage"
    return text or "unknown"


def _attach_collection_metadata(result: dict, targets: dict[str, str], target_source: str) -> None:
    corp_news = result.get("corp_news") or {}
    provider_counts: dict[str, int] = {}
    for corp in corp_news.values():
        for item in corp.get("items", []) or []:
            key = _provider_key(item.get("source", ""))
            provider_counts[key] = provider_counts.get(key, 0) + 1
    for item in result.get("market_news", []) or []:
        key = _provider_key(item.get("source", ""))
        provider_counts[key] = provider_counts.get(key, 0) + 1
    target_tickers = list(targets.keys())
    missing = [
        ticker for ticker in target_tickers
        if int((corp_news.get(ticker) or {}).get("count", 0) or 0) <= 0
    ]
    covered = len(target_tickers) - len(missing)
    result["target_source"] = target_source
    result["target_count"] = len(target_tickers)
    result["target_tickers"] = target_tickers
    result["provider_counts"] = provider_counts
    result["news_coverage"] = {
        "covered_ticker_count": covered,
        "missing_tickers": missing,
        "coverage_ratio": round(covered / len(target_tickers), 4) if target_tickers else 0.0,
    }

# FOMC 발표일 (2024~2026 주요 날짜 하드코딩 + API로 보완)
FOMC_DATES = {
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-17",
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
}

# 주요 경제지표 발표일 체크 키워드
MACRO_KEYWORDS = [
    "CPI", "PPI", "NFP", "nonfarm", "payroll", "GDP",
    "interest rate", "Fed", "FOMC", "inflation",
    "unemployment", "retail sales", "PMI"
]


# ── Alpha Vantage 뉴스 ────────────────────────────────────────────────────────

@log_retry(max_retries=3, delay=15.0, logger=log)
def fetch_av_news(ticker: str, target_date: str) -> list[dict]:
    """
    Alpha Vantage 뉴스 감성 API
    무료: 25회/일, 5회/분 → 12초 간격 필요
    """
    if not AV_KEY:
        log.debug("ALPHA_VANTAGE_KEY 없음")
        return []

    # 날짜 범위 (당일 전체)
    dt       = datetime.strptime(target_date, "%Y-%m-%d")
    time_from = dt.strftime("%Y%m%dT0000")
    time_to   = dt.strftime("%Y%m%dT2359")

    url = "https://www.alphavantage.co/query"
    params = {
        "function":  "NEWS_SENTIMENT",
        "tickers":   ticker,
        "time_from": time_from,
        "time_to":   time_to,
        "limit":     "20",
        "apikey":    AV_KEY,
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    if "Note" in data or "Information" in data:
        msg = data.get("Note") or data.get("Information")
        log.warning(f"AV API 제한: {msg}")
        raise Exception(f"AV API 제한: {msg}")

    items   = data.get("feed", [])
    results = []
    for item in items:
        # 해당 티커 감성 점수 추출
        ticker_sentiment = next(
            (t for t in item.get("ticker_sentiment", [])
             if t.get("ticker") == ticker), {}
        )
        results.append({
            "source":          f"AlphaVantage",
            "date":            target_date,
            "title":           item.get("title", ""),
            "content":         item.get("summary", "")[:500],
            "url":             item.get("url", ""),
            "sentiment_score": float(ticker_sentiment.get("ticker_sentiment_score", 0)),
            "sentiment_label": ticker_sentiment.get("ticker_sentiment_label", "Neutral"),
            "relevance":       float(ticker_sentiment.get("relevance_score", 0)),
        })

    log.debug(f"AV 뉴스 [{ticker}] {target_date}: {len(results)}건")
    return results


def _av_exhausted_path() -> Path:
    return Path(get_runtime_path("state", "av_exhausted_date.txt"))


def _av_runtime_day() -> str:
    """AV 일일 한도는 뉴스 target_date가 아니라 실제 호출한 달력일 기준이다.
    (AV rate limit은 API 콜 시점 기준 — 멀티데이 백필에서 target_date마다 재호출하던 버그 차단)"""
    return datetime.now().strftime("%Y-%m-%d")


def _is_av_exhausted(target_date: str) -> bool:
    """AV 일일 소진 여부. 한도는 호출일(달력일) 기준으로 판단한다 — 멀티데이 백필에서
    target_date가 날짜마다 달라 이미 소진된 AV를 매 날짜 재호출하던 헛호출을 막는다.
    전역(메모리)은 collector가 데이터 사이클마다 새 프로세스로 떠 리셋되므로 state 파일로 영속화한다."""
    global _AV_EXHAUSTED_DATE
    today = _av_runtime_day()
    if _AV_EXHAUSTED_DATE == today:
        return True
    try:
        p = _av_exhausted_path()
        if p.exists() and p.read_text(encoding="utf-8").strip() == today:
            _AV_EXHAUSTED_DATE = today
            return True
    except Exception:
        pass
    return False


def _mark_av_exhausted(target_date: str):
    global _AV_EXHAUSTED_DATE
    today = _av_runtime_day()
    _AV_EXHAUSTED_DATE = today
    try:
        p = _av_exhausted_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(today, encoding="utf-8")
    except Exception:
        pass
    log.warning(f"AV 일일 한도 소진 감지 — {today}(호출일) 남은 세션 동안 AV 호출 중단")


# ── Finnhub 뉴스 (백업) ───────────────────────────────────────────────────────

@log_retry(max_retries=3, delay=2.0, logger=log)
def fetch_finnhub_news(ticker: str, target_date: str) -> list[dict]:
    """
    Finnhub 뉴스 API (무료 60회/분)
    AV 제한 시 백업으로 사용
    """
    if not FINNHUB_KEY:
        return []

    dt       = datetime.strptime(target_date, "%Y-%m-%d")
    next_day = (dt + timedelta(days=1)).strftime("%Y-%m-%d")

    url    = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": ticker,
        "from":   target_date,
        "to":     next_day,
        "token":  FINNHUB_KEY,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    items = resp.json()

    results = []
    for item in items[:15]:
        published_at = ""
        try:
            published_at = datetime.fromtimestamp(int(item.get("datetime", 0))).isoformat()
        except Exception:
            published_at = target_date
        results.append({
            "source":  "Finnhub",
            "provider": "Finnhub",
            "date":    target_date,
            "published_at": published_at,
            "title":   item.get("headline", ""),
            "content": item.get("summary", "")[:500],
            "url":     item.get("url", ""),
            "ticker": ticker.upper(),
            "sentiment_score": 0.0,
            "sentiment_label": "Neutral",
            "relevance": 1.0,
        })

    log.debug(f"Finnhub [{ticker}] {target_date}: {len(results)}건")
    return results


@log_retry(max_retries=2, delay=1.0, logger=log)
def fetch_kis_news(ticker: str, target_date: str) -> list[dict]:
    """
    KIS 해외 뉴스 종합(제목)
    Finnhub 대체/보강용. 외부 뉴스 도메인 차단 환경에서도 동작 가능성이 높음.
    """
    profile = get_kis_market_profile("US")
    token = get_access_token(market="US")
    _, quote_exch = _get_us_quote_codes(ticker, token)
    resp = _kis_get(
        f"{profile.base_url}/uapi/overseas-price/v1/quotations/news-title",
        headers=_headers(token, "HHPSTH60100C1", market="US"),
        params={
            "INFO_GB": "",
            "CLASS_CD": "",
            "NATION_CD": "US",
            "EXCHANGE_CD": quote_exch,
            "SYMB": ticker.upper(),
            "DATA_DT": target_date.replace("-", ""),
            "DATA_TM": "",
            "CTS": "",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    items = data.get("outblock1", []) or data.get("output", []) or []

    target_ymd = target_date.replace("-", "")
    requested_symbol = ticker.upper()
    results = []
    seen_titles = set()
    for item in items:
        published_date = target_date
        data_dt = str(item.get("data_dt") or "").strip()
        if len(data_dt) == 8 and data_dt.isdigit():
            if data_dt != target_ymd:
                continue
            published_date = f"{data_dt[:4]}-{data_dt[4:6]}-{data_dt[6:8]}"
        raw_symbol = str(
            item.get("symb")
            or item.get("SYMB")
            or item.get("symbol")
            or item.get("rsym")
            or ""
        ).strip().upper()
        if raw_symbol and raw_symbol != requested_symbol:
            continue
        title = str(item.get("title", "") or item.get("hts_pbnt_titl_cntt", "") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        published_time = str(item.get("data_tm") or "").strip()
        published_at = published_date
        if len(published_time) == 6 and published_time.isdigit():
            published_at = f"{published_date}T{published_time[:2]}:{published_time[2:4]}:{published_time[4:6]}+09:00"
        results.append({
            "source": "KIS",
            "provider": item.get("source", "") or "KIS",
            "date": published_date,
            "published_at": published_at,
            "title": title,
            "content": "",
            "url": "",
            "ticker": raw_symbol or requested_symbol,
            "news_id": item.get("news_key", ""),
            "sentiment_score": 0.0,
            "sentiment_label": "Neutral",
            "relevance": 1.0,
        })
        if len(results) >= 15:
            break

    log.debug(f"KIS [{ticker}] {target_date}: {len(results)}건")
    return results


@log_retry(max_retries=2, delay=1.0, logger=log)
def fetch_kis_market_news(target_date: str) -> list[dict]:
    """
    KIS 해외 브로커 뉴스(제목)
    시장 전반 개요 뉴스 보강용.
    """
    profile = get_kis_market_profile("US")
    token = get_access_token(market="US")
    resp = _kis_get(
        f"{profile.base_url}/uapi/overseas-price/v1/quotations/brknews-title",
        headers=_headers(token, "FHKST01011801", market="US"),
        params={
            "FID_NEWS_OFER_ENTP_CODE": "0",
            "FID_COND_SCR_DIV_CODE": "11801",
            "FID_COND_MRKT_CLS_CODE": "",
            "FID_INPUT_ISCD": "",
            "FID_TITL_CNTT": "",
            "FID_INPUT_DATE_1": target_date.replace("-", ""),
            "FID_INPUT_HOUR_1": "",
            "FID_RANK_SORT_CLS_CODE": "",
            "FID_INPUT_SRNO": "",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    items = data.get("output", []) or []

    results = []
    for item in items[:20]:
        title = item.get("hts_pbnt_titl_cntt", "")
        if not title:
            continue
        results.append({
            "source": "KIS",
            "provider": item.get("source", "") or "KIS",
            "date": target_date,
            "title": title,
            "content": "",
            "url": "",
            "sentiment_score": 0.0,
            "sentiment_label": "Neutral",
            "relevance": 1.0,
        })

    log.debug(f"KIS market [{target_date}]: {len(results)}건")
    return results


# ── SEC EDGAR 공시 수집 ───────────────────────────────────────────────────────

@log_retry(max_retries=3, delay=2.0, logger=log)
def fetch_sec_filings(ticker: str, target_date: str) -> list[dict]:
    """
    SEC EDGAR 최근 공시 조회
    완전 무료, 인증 불필요
    """
    headers = {"User-Agent": "trading-bot contact@example.com"}

    # 티커 → CIK 변환
    cik_url  = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt={target_date}&enddt={target_date}&forms=8-K,10-Q,10-K"
    try:
        resp = requests.get(cik_url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.debug(f"SEC 검색 실패 [{ticker}]: {e}")
        return []

    hits    = data.get("hits", {}).get("hits", [])
    results = []
    for hit in hits[:5]:
        src = hit.get("_source", {})
        results.append({
            "source":    "SEC EDGAR",
            "provider":  "SEC EDGAR",
            "date":      target_date,
            "title":     f"[{src.get('form_type','')}] {src.get('display_names','')}",
            "content":   src.get("file_date", ""),
            "url":       f"https://www.sec.gov{src.get('file_date','')}",
            "form_type": src.get("form_type", ""),
            "ticker":    ticker.upper(),
        })

    log.debug(f"SEC [{ticker}] {target_date}: {len(results)}건")
    return results


# ── 거시 경제 이벤트 확인 ─────────────────────────────────────────────────────

def check_macro_events(target_date: str) -> dict:
    """
    당일 주요 거시 이벤트 확인
    반환: {fomc: bool, cpi: bool, other: []}
    """
    events = {
        "fomc":        target_date in FOMC_DATES,
        "fomc_week":   any(
            (datetime.strptime(target_date, "%Y-%m-%d") -
             datetime.strptime(d, "%Y-%m-%d")).days in range(-2, 3)
            for d in FOMC_DATES
            if abs((datetime.strptime(target_date, "%Y-%m-%d") -
                    datetime.strptime(d, "%Y-%m-%d")).days) <= 2
        ),
        "other":       [],
    }

    if events["fomc"]:
        log.info(f"⚠️  {target_date} FOMC 발표일!")
    elif events["fomc_week"]:
        log.info(f"⚠️  {target_date} FOMC 주간 (변동성 주의)")

    return events


# ── 시장 전반 뉴스 (SPY/QQQ 기반) ────────────────────────────────────────────

def fetch_market_overview(target_date: str) -> list[dict]:
    """미국 시장 전반 뉴스 (SPY, QQQ)"""
    results = []
    try:
        results.extend(fetch_kis_market_news(target_date)[:10])
    except Exception as e:
        log.debug(f"KIS 시장 뉴스 실패: {e}")

    for ticker in ["SPY", "QQQ"]:
        try:
            if FINNHUB_KEY:
                news = fetch_finnhub_news(ticker, target_date)
                results.extend(news[:5])
                time.sleep(1)
            elif len(results) < 3:
                news = fetch_kis_news(ticker, target_date)
                results.extend(news[:5])
            elif AV_KEY and not _is_av_exhausted(target_date):
                news = fetch_av_news(ticker, target_date)
                results.extend(news[:5])
                time.sleep(12)  # AV API 제한
        except Exception as e:
            if "AV API 제한" in str(e):
                _mark_av_exhausted(target_date)
            log.warning(f"시장 뉴스 [{ticker}]: {e}")
    return results


# ── 하루치 전체 수집 ──────────────────────────────────────────────────────────

@log_call(logger=log, level="INFO")
def collect_day(
    target_date: str,
    targets: dict[str, str] | None = None,
    *,
    force: bool = True,
    target_source: str | None = None,
) -> dict:
    """
    특정 날짜 미국 뉴스/공시 전체 수집
    target_date: 'YYYY-MM-DD'
    """
    normalized_targets = _normalize_us_targets(targets)
    target_source = target_source or ("explicit_targets" if targets is not None else "fallback_target_tickers")
    corp_tickers = {
        k: v for k, v in normalized_targets.items()
        if k not in ("SPY", "QQQ")
    }
    save_path = NEWS_DIR / f"{target_date}.json"
    existing = _load_existing_if_reusable(save_path, list(corp_tickers.keys()), force)
    if existing is not None:
        return existing

    # 주말 체크
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    if dt.weekday() >= 5:
        log.info(f"[SKIP] {target_date} 주말")
        return {}

    log.info(f"━━━ {target_date} 미국 뉴스 수집 시작 ━━━")
    result = {
        "date":          target_date,
        "macro_events":  check_macro_events(target_date),
        "market_news":   [],
        "corp_news":     {},
        "collected_at":  datetime.now().isoformat(),
    }

    # 1. 시장 전반 뉴스
    result["market_news"] = fetch_market_overview(target_date)
    log.info(f"  시장뉴스: {len(result['market_news'])}건")

    # 2. 종목별 수집 (SPY/QQQ 제외)
    for ticker, name in corp_tickers.items():
        items = []

        # Finnhub 우선
        if FINNHUB_KEY:
            try:
                fh_news = fetch_finnhub_news(ticker, target_date)
                items.extend(fh_news)
                time.sleep(1)
            except Exception as e:
                log.debug(f"  Finnhub 실패 [{ticker}]: {e}")

        # KIS 뉴스 보강
        if len(items) < 3:
            try:
                kis_news = fetch_kis_news(ticker, target_date)
                items.extend(kis_news)
                time.sleep(0.2)
            except Exception as e:
                log.debug(f"  KIS 뉴스 실패 [{ticker}]: {e}")

        # SEC 공시
        try:
            sec = fetch_sec_filings(ticker, target_date)
            if sec:
                items.extend(sec)
                log.info(f"  SEC 공시 [{ticker}] {len(sec)}건")
            time.sleep(0.5)
        except Exception as e:
            log.debug(f"  SEC 실패 [{ticker}]: {e}")

        # Alpha Vantage 보조
        if AV_KEY and len(items) < 3 and not _is_av_exhausted(target_date):
            try:
                av_news = fetch_av_news(ticker, target_date)
                items.extend(av_news)
                log.debug(f"  AV [{ticker}] {len(av_news)}건")
                time.sleep(12)  # 5회/분 제한
            except Exception as e:
                if "AV API 제한" in str(e):
                    _mark_av_exhausted(target_date)
                log.warning(f"  AV 실패 [{ticker}]: {e}")

        result["corp_news"][ticker] = {
            "name":  name,
            "items": items,
            "count": len(items),
            "avg_sentiment": (
                sum(i.get("sentiment_score", 0) for i in items) / len(items)
                if items else 0.0
            ),
        }
        log.info(f"  [{name}({ticker})] {len(items)}건")

    _attach_collection_metadata(result, corp_tickers, target_source)

    # 저장
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total = sum(v["count"] for v in result["corp_news"].values())
    log.info(
        f"━━━ {target_date} 완료 | 시장 {len(result['market_news'])}건 "
        f"| 종목 {total}건 | "
        f"FOMC={result['macro_events']['fomc']} | "
        f"저장: {save_path.name} ━━━"
    )
    return result


# ── 기간 전체 수집 ────────────────────────────────────────────────────────────

def collect_range(start: str, end: str):
    """
    기간 전체 수집
    AV 무료 키 제한 고려 → 종목당 12초 대기
    하루치 약 60초 소요
    """
    log.info(f"{'='*55}")
    log.info(f"  미국 뉴스 기간 수집: {start} ~ {end}")
    log.info(f"{'='*55}")

    start_dt = datetime.strptime(start, "%Y-%m-%d").date()
    end_dt   = datetime.strptime(end,   "%Y-%m-%d").date()
    current  = start_dt

    biz_days = []
    while current <= end_dt:
        if current.weekday() < 5:
            biz_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    est_minutes = len(biz_days) * 1.5  # 하루 약 1.5분
    log.info(f"총 {len(biz_days)}개 영업일 | 예상 소요: {est_minutes:.0f}분")

    prog = ProgressLogger(
        total=len(biz_days), name="미국뉴스수집",
        logger=log, interval=5
    )

    success = 0
    for day_str in biz_days:
        save_path = NEWS_DIR / f"{day_str}.json"
        if save_path.exists():
            log.debug(f"[SKIP] {day_str}")
            prog.step(day_str, success=True)
            success += 1
            continue

        try:
            collect_day(day_str)
            success += 1
            prog.step(day_str, success=True)
        except Exception as e:
            log.error(f"[FAIL] {day_str}: {e}")
            prog.step(day_str, success=False)

        time.sleep(3)  # 날짜 간 대기

    prog.done()
    log.info(f"수집 완료: {success}/{len(biz_days)}일")


# ── 로드 유틸 ─────────────────────────────────────────────────────────────────

def load_day(target_date: str) -> dict:
    path = NEWS_DIR / f"{target_date}.json"
    if not path.exists():
        return {
            "date": target_date,
            "macro_events": {"fomc": False, "fomc_week": False},
            "market_news": [],
            "corp_news": {},
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_day_summary(target_date: str) -> str:
    """Claude에게 전달할 미국 뉴스 요약"""
    data   = load_day(target_date)
    lines  = [f"[{target_date} 미국 시장 뉴스]"]

    # 거시 이벤트
    macro = data.get("macro_events", {})
    if macro.get("fomc"):
        lines.append("\n🚨 오늘 FOMC 금리 결정 발표일 (극도 주의)")
    elif macro.get("fomc_week"):
        lines.append("\n⚠️  FOMC 주간 (변동성 주의)")

    # 시장 뉴스
    if data.get("market_news"):
        lines.append("\n▶ 시장 전반")
        for n in data["market_news"][:5]:
            sentiment = ""
            score = n.get("sentiment_score", 0)
            if score > 0.15:   sentiment = " 📈"
            elif score < -0.15: sentiment = " 📉"
            lines.append(f"  • {n['title']}{sentiment}")

    # 종목별
    lines.append("\n▶ 종목별")
    for ticker, corp in data.get("corp_news", {}).items():
        if corp.get("count", 0) == 0:
            continue
        avg_s = corp.get("avg_sentiment", 0)
        mood  = "📈" if avg_s > 0.15 else ("📉" if avg_s < -0.15 else "➡️")
        lines.append(f"  [{corp['name']}({ticker})] {mood} 감성 {avg_s:+.2f}")
        for item in corp["items"][:3]:
            content = f" - {item['content'][:80]}" if item.get("content") else ""
            lines.append(f"    • {item['title']}{content}")

    return "\n".join(lines)


def check_api_keys():
    log.info("미국 뉴스 API 키 상태")
    log.info(f"  ALPHA_VANTAGE_KEY: {'✅ 있음' if AV_KEY else '❌ 없음 (alphavantage.co 발급)'}")
    log.info(f"  FINNHUB_KEY:       {'✅ 있음' if FINNHUB_KEY else '⚠️  없음 (finnhub.io 발급, 선택)'}")
    log.info(f"  SEC EDGAR:         ✅ 인증 불필요")
    log.info(f"  FOMC 일정:         ✅ 하드코딩됨")
    log.info(f"  저장 경로:         {NEWS_DIR}")


if __name__ == "__main__":
    check_api_keys()

    test_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    log.info(f"\n테스트 수집: {test_date}")
    collect_day(test_date)

    log.info("\n요약 텍스트:")
    print(get_day_summary(test_date))
