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

load_dotenv()

log = get_collector_logger()

# ── 설정 ──────────────────────────────────────────────────────────────────────

AV_KEY      = os.getenv("ALPHA_VANTAGE_KEY", "")
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")

NEWS_DIR = Path(__file__).parent.parent / "data" / "news" / "us"
NEWS_DIR.mkdir(parents=True, exist_ok=True)

TARGET_TICKERS = {
    "NVDA": "엔비디아",
    "TSLA": "테슬라",
    "AAPL": "애플",
    "MSFT": "마이크로소프트",
    "META": "메타",
    "SPY":  "S&P500 ETF",
    "QQQ":  "나스닥100 ETF",
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
        results.append({
            "source":  "Finnhub",
            "date":    target_date,
            "title":   item.get("headline", ""),
            "content": item.get("summary", "")[:500],
            "url":     item.get("url", ""),
            "sentiment_score": 0.0,
            "sentiment_label": "Neutral",
            "relevance": 1.0,
        })

    log.debug(f"Finnhub [{ticker}] {target_date}: {len(results)}건")
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
            "date":      target_date,
            "title":     f"[{src.get('form_type','')}] {src.get('display_names','')}",
            "content":   src.get("file_date", ""),
            "url":       f"https://www.sec.gov{src.get('file_date','')}",
            "form_type": src.get("form_type", ""),
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
    for ticker in ["SPY", "QQQ"]:
        try:
            if AV_KEY:
                news = fetch_av_news(ticker, target_date)
                results.extend(news[:5])
                time.sleep(12)  # AV API 제한
            elif FINNHUB_KEY:
                news = fetch_finnhub_news(ticker, target_date)
                results.extend(news[:5])
                time.sleep(1)
        except Exception as e:
            log.warning(f"시장 뉴스 [{ticker}]: {e}")
    return results


# ── 하루치 전체 수집 ──────────────────────────────────────────────────────────

@log_call(logger=log, level="INFO")
def collect_day(target_date: str) -> dict:
    """
    특정 날짜 미국 뉴스/공시 전체 수집
    target_date: 'YYYY-MM-DD'
    """
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
    corp_tickers = {k: v for k, v in TARGET_TICKERS.items()
                    if k not in ("SPY", "QQQ")}

    for ticker, name in corp_tickers.items():
        items = []

        # Alpha Vantage 우선
        if AV_KEY:
            try:
                av_news = fetch_av_news(ticker, target_date)
                items.extend(av_news)
                log.debug(f"  AV [{ticker}] {len(av_news)}건")
                time.sleep(12)  # 5회/분 제한
            except Exception as e:
                log.warning(f"  AV 실패 [{ticker}]: {e}")

        # Finnhub 백업
        if FINNHUB_KEY and len(items) < 3:
            try:
                fh_news = fetch_finnhub_news(ticker, target_date)
                items.extend(fh_news)
                time.sleep(1)
            except Exception as e:
                log.debug(f"  Finnhub 실패 [{ticker}]: {e}")

        # SEC 공시
        try:
            sec = fetch_sec_filings(ticker, target_date)
            if sec:
                items.extend(sec)
                log.info(f"  SEC 공시 [{ticker}] {len(sec)}건")
            time.sleep(0.5)
        except Exception as e:
            log.debug(f"  SEC 실패 [{ticker}]: {e}")

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

    # 저장
    save_path = NEWS_DIR / f"{target_date}.json"
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
