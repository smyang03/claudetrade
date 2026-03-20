"""
digest_builder.py
원본 데이터 → Claude 입력용 daily_digest 생성

입력:  data/price/, data/news/, data/supplement/
출력:  data/daily_digest/YYYYMMDD_KR.json
      data/daily_digest/YYYYMMDD_US.json

Claude 입력 토큰: ~1,300 토큰/일 목표
승률 기여도 높은 데이터 우선 포함
"""

import json
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_trainer_logger, log_call
from runtime_paths import get_runtime_path

log = get_trainer_logger()

BASE_DIR    = Path(__file__).parent.parent
PRICE_DIR   = BASE_DIR / "data" / "price"
NEWS_DIR    = BASE_DIR / "data" / "news"
SUPP_DIR    = BASE_DIR / "data" / "supplement"
DIGEST_DIR  = BASE_DIR / "data" / "daily_digest"
CACHE_DIR   = BASE_DIR / "data" / "cache"

for d in [DIGEST_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def _ticker_map(market: str, universe_tickers: list[str] | None = None) -> dict:
    base = KR_TICKERS if market == "KR" else US_TICKERS
    if not universe_tickers:
        return dict(base)
    return {t: base.get(t, t) for t in universe_tickers}

KR_TICKERS = {"005930":"삼성전자","000660":"SK하이닉스","035420":"NAVER"}
US_TICKERS = {"NVDA":"엔비디아","TSLA":"테슬라","AAPL":"애플"}

FOMC_DATES = {
    "2025-01-29","2025-03-19","2025-05-07","2025-06-18",
    "2025-07-30","2025-09-17","2025-11-05","2025-12-17",
    "2026-01-28","2026-03-18","2026-05-06","2026-06-17",
}


# ── 지표 계산 (캐시 활용) ─────────────────────────────────────────────────────

def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ma5"]     = d["close"].rolling(5).mean()
    d["ma20"]    = d["close"].rolling(20).mean()
    d["ma60"]    = d["close"].rolling(60).mean()
    d["vol_avg"] = d["volume"].rolling(20).mean()
    delta = d["close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    d["rsi"] = 100 - 100 / (1 + rs)
    ema12 = d["close"].ewm(span=12).mean()
    ema26 = d["close"].ewm(span=26).mean()
    d["macd"]   = ema12 - ema26
    d["signal"] = d["macd"].ewm(span=9).mean()
    std = d["close"].rolling(20).std()
    d["bb_upper"] = d["ma20"] + 2 * std
    d["bb_lower"] = d["ma20"] - 2 * std
    d["bb_pct"]   = (d["close"] - d["bb_lower"]) / (d["bb_upper"] - d["bb_lower"]) * 100
    d["high52"]   = d["high"].rolling(252).max()
    d["low52"]    = d["low"].rolling(252).min()
    d["pos52"]    = (d["close"] - d["low52"]) / (d["high52"] - d["low52"]) * 100
    d["gap"]      = (d["open"] - d["close"].shift(1)) / d["close"].shift(1) * 100
    d["vol_ratio"]= d["volume"] / d["vol_avg"]
    d["change_pct"]= d["close"].pct_change() * 100
    return d


def load_price_with_cache(market: str, ticker: str) -> pd.DataFrame:
    """주가 로드 (캐시 우선)"""
    cache_path = CACHE_DIR / f"{market}_{ticker}_indicators.pkl"
    raw_path   = PRICE_DIR / market.lower() / f"{market.lower()}_{ticker}.csv"

    if not raw_path.exists():
        log.debug(f"주가 파일 없음: {raw_path}")
        return pd.DataFrame()

    raw_mtime   = raw_path.stat().st_mtime
    cache_valid = (cache_path.exists() and
                   cache_path.stat().st_mtime > raw_mtime)

    if cache_valid:
        return pd.read_pickle(cache_path)

    df = pd.read_csv(raw_path, parse_dates=["date"])
    df.columns = [c.lower() for c in df.columns]
    df = df.sort_values("date").reset_index(drop=True)
    df = calc_indicators(df)
    df.to_pickle(cache_path)
    log.debug(f"지표 캐시 갱신: {ticker}")
    return df


# ── 뉴스 중요도 필터 (Claude 전 단계 Python 필터) ────────────────────────────

def score_news(title: str, content: str = "") -> float:
    """
    뉴스 중요도 점수 (0~5)
    Claude에게 넘기기 전에 Python이 먼저 필터링
    → 상위 3건만 Claude에게 전달
    """
    text  = (title + " " + content).lower()
    score = 1.0  # 기본값

    # ★ 최고 중요도 (+3)
    top_kw = ["공시","계약체결","실적발표","영업이익","매출","hbm","ai칩",
               "순매수","순매도","외국인","기관","서킷브레이커","급등","급락",
               "earnings","contract","acquisition","sec filing"]
    score += sum(3.0 for kw in top_kw if kw in text)

    # ★ 높은 중요도 (+2)
    high_kw = ["반도체","수주","공급","합병","인수","분기","연간","배당",
               "nvda","tsla","삼성","하이닉스","guidance","revenue","profit"]
    score += sum(2.0 for kw in high_kw if kw in text)

    # ★ 중간 중요도 (+1)
    mid_kw = ["전망","목표주가","투자의견","analyst","upgrade","downgrade"]
    score += sum(1.0 for kw in mid_kw if kw in text)

    # ✗ 패널티 (-1)
    noise_kw = ["단순시황","증권사광고","이벤트","컬럼","오피니언"]
    score -= sum(1.0 for kw in noise_kw if kw in text)

    return max(0.0, score)


def filter_top_news(news_items: list, top_n: int = 3) -> list:
    """중요도 상위 N건만 반환"""
    scored = [(score_news(n.get("title",""), n.get("content","")), n)
              for n in news_items]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in scored[:top_n]]


# ── 보조 데이터 로드 ──────────────────────────────────────────────────────────

def load_supplement(market: str, target_date: str) -> dict:
    """외국인/기관 수급, VIX, 환율 등"""
    path = SUPP_DIR / market.lower() / f"{target_date}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_news_day(market: str, target_date: str) -> dict:
    path = NEWS_DIR / market.lower() / f"{target_date}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_prev_result(market: str, target_date: str) -> dict:
    """전일 결과 (판단 기록에서)"""
    dt   = datetime.strptime(target_date, "%Y-%m-%d")
    prev = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    # 평일 역순으로 최대 5일 탐색
    from pathlib import Path
    jdir = get_runtime_path("logs", "daily_judgment", make_parents=False)
    for i in range(1, 6):
        d = (dt - timedelta(days=i)).strftime("%Y%m%d")
        p = jdir / f"{d}_{market}.json"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
            result = rec.get("actual_result", {})
            return {
                "date":    rec.get("date", ""),
                "mode":    rec.get("consensus", {}).get("mode", ""),
                "pnl_pct": result.get("pnl_pct", 0),
                "win":     result.get("win", False),
            }
    return {}


# ── 핵심 digest 생성 ──────────────────────────────────────────────────────────

@log_call(logger=log, level="INFO")
def build_kr_digest(target_date: str, universe_tickers: list[str] | None = None) -> dict:
    """
    국내 daily_digest 생성
    목표: ~800 토큰
    """
    log.info(f"[국내 digest] {target_date}")
    supp  = load_supplement("KR", target_date)
    news  = load_news_day("KR", target_date)
    prev  = load_prev_result("KR", target_date)

    # ── Layer A: 시장 컨텍스트 (~150 토큰) ───────────────────────────────────
    layer_a = {
        "kospi": supp.get("kospi", {}),
        "usd_krw": supp.get("usd_krw", 0),
        "vkospi": supp.get("vkospi", 0),
        "foreign_futures": supp.get("foreign_futures", 0),
        "us_prev": supp.get("us_prev", {}),  # 전날 미국장
        "fomc": target_date in FOMC_DATES,
        "options_expiry": supp.get("options_expiry", False),
    }

    # ── Layer B: 종목 핵심 지표 (~300 토큰) ──────────────────────────────────
    ticker_map = _ticker_map("KR", universe_tickers)
    layer_b = {}
    for ticker, name in ticker_map.items():
        df = load_price_with_cache("KR", ticker)
        if df.empty:
            log.warning(f"주가 데이터 없음: {ticker}")
            continue

        dt_row = df[df["date"] == pd.Timestamp(target_date)]
        if dt_row.empty:
            # 해당 날짜 없으면 가장 가까운 이전 날짜
            past = df[df["date"] < pd.Timestamp(target_date)]
            if past.empty:
                continue
            dt_row = past.iloc[[-1]]

        row = dt_row.iloc[0]

        # 이동평균 배열 판단
        ma_align = "정배열" if (row.get("ma5",0) > row.get("ma20",0) > row.get("ma60",0)) else \
                   "역배열" if (row.get("ma5",0) < row.get("ma20",0) < row.get("ma60",0)) else "혼재"

        # MACD 신호
        macd_sig = "골든크로스" if row.get("macd",0) > row.get("signal",0) else "데드크로스"

        # BB 위치
        bb_pct = row.get("bb_pct", 50)
        bb_pos = "상단" if bb_pct > 80 else "하단" if bb_pct < 20 else "중간"

        # 거래량 이상
        vol_r = row.get("vol_ratio", 1.0)
        vol_signal = "폭증" if vol_r > 3 else "증가" if vol_r > 1.5 else "보통" if vol_r > 0.7 else "감소"

        layer_b[ticker] = {
            "name":        name,
            "close":       int(row.get("close", 0)),
            "change_pct":  round(float(row.get("change_pct", 0)), 2),
            "rsi":         round(float(row.get("rsi", 50)), 1),
            "macd":        macd_sig,
            "bb_pos":      bb_pos,
            "bb_pct":      round(float(bb_pct), 1),
            "ma_align":    ma_align,
            "vol_ratio":   round(float(vol_r), 2),
            "vol_signal":  vol_signal,
            "pos_52w":     round(float(row.get("pos52", 50)), 1),
            "gap_pct":     round(float(row.get("gap", 0)), 2),
            # 수급 (supplement에서)
            "foreign_flow": supp.get("flows", {}).get(ticker, {}).get("foreign", 0),
            "inst_flow":    supp.get("flows", {}).get(ticker, {}).get("institution", 0),
            "short_ratio":  supp.get("short", {}).get(ticker, 0),
            # 공시 여부
            "disclosure":  bool(news.get("disclosures", {}).get(ticker, [])),
        }

    # ── Layer C: 뉴스 선별 (~200 토큰) ───────────────────────────────────────
    # 전체 뉴스 수집
    all_news = list(news.get("market_news", []))
    for code in ticker_map:
        corp = news.get("corp_news", {}).get(code, {})
        all_news.extend(corp.get("items", []))

    # 중요도 필터링 → 상위 5건
    top_news = filter_top_news(all_news, top_n=5)
    layer_c = [{
        "title":   n.get("title", ""),
        "source":  n.get("source", ""),
        "ticker":  n.get("ticker", ""),
    } for n in top_news]

    # 공시 별도 (중요도 최상)
    disclosures = []
    for code, items in news.get("disclosures", {}).items():
        name = ticker_map.get(code, code)
        for d in items[:2]:
            disclosures.append(f"[{name}] {d.get('title','')}")

    digest = {
        "date":        target_date,
        "market":      "KR",
        "universe_tickers": list(ticker_map.keys()),
        "context":     layer_a,
        "technicals":  layer_b,
        "top_news":    layer_c,
        "disclosures": disclosures,
        "prev_result": prev,
        "built_at":    datetime.now().isoformat(),
    }

    # 저장
    path = DIGEST_DIR / f"{target_date}_KR.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(digest, f, ensure_ascii=False, indent=2)

    log.info(f"  ✅ KR digest 저장: {path.name} "
             f"(뉴스 {len(layer_c)}건, 공시 {len(disclosures)}건, "
             f"종목 {len(layer_b)}개)")
    return digest


@log_call(logger=log, level="INFO")
def build_us_digest(target_date: str, universe_tickers: list[str] | None = None) -> dict:
    """미국 daily_digest 생성"""
    log.info(f"[미국 digest] {target_date}")
    supp = load_supplement("US", target_date)
    news = load_news_day("US", target_date)
    prev = load_prev_result("US", target_date)

    is_fomc      = target_date in FOMC_DATES
    is_fomc_week = any(
        abs((datetime.strptime(target_date, "%Y-%m-%d") -
             datetime.strptime(d, "%Y-%m-%d")).days) <= 2
        for d in FOMC_DATES
    )

    layer_a = {
        "sp500":      supp.get("sp500", {}),
        "nasdaq":     supp.get("nasdaq", {}),
        "vix":        supp.get("vix", 0),
        "dxy":        supp.get("dxy", 0),
        "oil_wti":    supp.get("oil_wti", 0),
        "fomc":       is_fomc,
        "fomc_week":  is_fomc_week,
        "cpi_day":    supp.get("cpi_day", False),
        "nfp_day":    supp.get("nfp_day", False),
        "premarket":  supp.get("premarket", {}),
    }

    ticker_map = _ticker_map("US", universe_tickers)
    layer_b = {}
    for ticker, name in ticker_map.items():
        df = load_price_with_cache("US", ticker)
        if df.empty:
            continue

        dt_row = df[df["date"] == pd.Timestamp(target_date)]
        if dt_row.empty:
            past = df[df["date"] < pd.Timestamp(target_date)]
            if past.empty:
                continue
            dt_row = past.iloc[[-1]]

        row = dt_row.iloc[0]
        vol_r   = row.get("vol_ratio", 1.0)
        bb_pct  = row.get("bb_pct", 50)
        macd_sig = "골든크로스" if row.get("macd", 0) > row.get("signal", 0) else "데드크로스"

        # 변동성 돌파 목표가 (K=0.45)
        prev_range = float(row.get("high", 0)) - float(row.get("low", 0))
        vb_target  = float(row.get("open", 0)) + prev_range * 0.45

        # 감성 점수 (AV에서)
        corp_news  = news.get("corp_news", {}).get(ticker, {})
        avg_sent   = corp_news.get("avg_sentiment", 0)
        news_items = corp_news.get("items", [])

        layer_b[ticker] = {
            "name":       name,
            "close":      round(float(row.get("close", 0)), 2),
            "change_pct": round(float(row.get("change_pct", 0)), 2),
            "rsi":        round(float(row.get("rsi", 50)), 1),
            "macd":       macd_sig,
            "bb_pct":     round(float(bb_pct), 1),
            "vol_ratio":  round(float(vol_r), 2),
            "pos_52w":    round(float(row.get("pos52", 50)), 1),
            "vb_target":  round(vb_target, 2),
            "news_sentiment": round(float(avg_sent), 3),
            "sec_filing": bool([i for i in news_items if i.get("source") == "SEC EDGAR"]),
        }

    all_news = list(news.get("market_news", []))
    for t in ticker_map:
        items = news.get("corp_news", {}).get(t, {}).get("items", [])
        all_news.extend(items)
    top_news = filter_top_news(all_news, top_n=5)
    layer_c  = [{"title": n.get("title",""), "ticker": n.get("ticker","")} for n in top_news]

    digest = {
        "date":        target_date,
        "market":      "US",
        "universe_tickers": list(ticker_map.keys()),
        "context":     layer_a,
        "technicals":  layer_b,
        "top_news":    layer_c,
        "prev_result": prev,
        "built_at":    datetime.now().isoformat(),
    }

    path = DIGEST_DIR / f"{target_date}_US.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(digest, f, ensure_ascii=False, indent=2)

    log.info(f"  ✅ US digest 저장: {path.name}")
    return digest


def load_digest(market: str, target_date: str) -> dict:
    path = DIGEST_DIR / f"{target_date}_{market}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def digest_to_prompt(digest: dict) -> str:
    """
    digest → Claude 프롬프트 텍스트 변환
    목표: ~800 토큰
    """
    market = digest.get("market", "KR")
    date   = digest.get("date", "")
    ctx    = digest.get("context", {})
    tech   = digest.get("technicals", {})
    news   = digest.get("top_news", [])
    disc   = digest.get("disclosures", [])
    prev   = digest.get("prev_result", {})

    lines = [f"[{date} {market} 시장 데이터]"]

    # 시장 컨텍스트
    lines.append("\n▶ 시장 컨텍스트")
    if market == "KR":
        kospi = ctx.get("kospi", {})
        if kospi:
            lines.append(f"  코스피 {kospi.get('change_pct',0):+.2f}% | "
                         f"USD/KRW {ctx.get('usd_krw',0):,} | "
                         f"VKOSPI {ctx.get('vkospi',0):.1f}")
        if ctx.get("foreign_futures"):
            lines.append(f"  외국인 선물: {ctx['foreign_futures']:+,}억원")
        us = ctx.get("us_prev", {})
        if us:
            lines.append(f"  전날 미국장: S&P {us.get('sp500',0):+.2f}% "
                         f"나스닥 {us.get('nasdaq',0):+.2f}%")
    else:
        sp = ctx.get("sp500", {})
        nq = ctx.get("nasdaq", {})
        lines.append(f"  S&P500 {sp.get('change_pct',0):+.2f}% | "
                     f"나스닥 {nq.get('change_pct',0):+.2f}% | "
                     f"VIX {ctx.get('vix',0):.1f} | "
                     f"DXY {ctx.get('dxy',0):.1f}")

    # 이벤트
    events = []
    if ctx.get("fomc"):         events.append("🚨FOMC 발표일")
    if ctx.get("fomc_week"):    events.append("⚠️FOMC 주간")
    if ctx.get("options_expiry"): events.append("⚠️옵션만기일")
    if ctx.get("cpi_day"):      events.append("⚠️CPI 발표일")
    if events:
        lines.append(f"  이벤트: {' | '.join(events)}")

    # 종목 지표
    lines.append("\n▶ 종목 기술 지표")
    for ticker, t in tech.items():
        rsi_mark = "🔴과매도" if t["rsi"] < 30 else "🟢과매수" if t["rsi"] > 70 else ""
        vol_mark = "⚡폭증" if t["vol_ratio"] > 3 else "↑증가" if t["vol_ratio"] > 1.5 else ""
        bb_display = t['bb_pos'] if 'bb_pos' in t else f"{t['bb_pct']:.0f}%"
        lines.append(
            f"  [{t.get('name',ticker)}] {t['close']:,} "
            f"{t['change_pct']:+.2f}% | "
            f"RSI {t['rsi']}{rsi_mark} | "
            f"MACD {t['macd']} | "
            f"BB {bb_display} | "
            f"거래량 {t['vol_ratio']:.1f}배{vol_mark} | "
            f"52주위치 {t['pos_52w']:.0f}%"
        )
        # 수급 (KR만)
        ff = t.get("foreign_flow", 0)
        inst = t.get("inst_flow", 0)
        if ff or inst:
            lines.append(f"    수급: 외국인 {ff:+,}억 | 기관 {inst:+,}억 | "
                         f"공매도 {t.get('short_ratio',0):.1f}%")
        if t.get("disclosure"):
            lines.append(f"    ⭐ 공시 있음")

    # 공시
    if disc:
        lines.append("\n▶ 주요 공시")
        for d in disc[:5]:
            lines.append(f"  • {d}")

    # 뉴스
    if news:
        lines.append("\n▶ 주요 뉴스 (중요도 상위)")
        for n in news:
            tk = f"[{n['ticker']}] " if n.get("ticker") else ""
            lines.append(f"  • {tk}{n['title']}")

    # 전일 결과
    if prev:
        win = "✅" if prev.get("win") else "❌"
        lines.append(f"\n▶ 전일 결과: {prev.get('mode','-')} | "
                     f"{prev.get('pnl_pct',0):+.2f}% {win}")

    return "\n".join(lines)


if __name__ == "__main__":
    test_date = "2025-06-02"
    log.info(f"테스트: {test_date}")
    d = build_kr_digest(test_date)
    print("\n--- KR Digest Prompt ---")
    print(digest_to_prompt(d))
    tokens = len(digest_to_prompt(d).split()) * 1.3
    log.info(f"예상 토큰: ~{tokens:.0f}")
