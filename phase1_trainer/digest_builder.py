"""
digest_builder.py
원본 데이터 → Claude 입력용 daily_digest 생성

입력:  data/price/, data/news/, data/supplement/
출력:  data/daily_digest/YYYYMMDD_KR.json
      data/daily_digest/YYYYMMDD_US.json

Claude 입력 토큰: ~1,300 토큰/일 목표
승률 기여도 높은 데이터 우선 포함
"""

from typing import Optional, List
import json
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_trainer_logger, log_call
from runtime_paths import get_runtime_path
try:
    from bot.session_date import KST, resolve_session_date_str
except Exception:  # pragma: no cover
    KST = None
    resolve_session_date_str = None

try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    _YF_OK = False

log = get_trainer_logger()

BASE_DIR    = Path(__file__).parent.parent
PRICE_DIR   = BASE_DIR / "data" / "price"
NEWS_DIR    = BASE_DIR / "data" / "news"
SUPP_DIR    = BASE_DIR / "data" / "supplement"
DIGEST_DIR  = BASE_DIR / "data" / "daily_digest"
CACHE_DIR   = BASE_DIR / "data" / "cache"

for d in [DIGEST_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# 인버스 ETF — 약세 모드에서만 유니버스에 포함
_INVERSE_TICKERS = {"114800", "SQQQ"}


def _ticker_map(market: str, universe_tickers: Optional[List[str]] = None,
                include_inverse: bool = False) -> dict:
    base = KR_TICKERS if market == "KR" else US_TICKERS
    if universe_tickers:
        result = {t: base.get(t, t) for t in universe_tickers}
    else:
        result = dict(base)
    if not include_inverse:
        result = {k: v for k, v in result.items() if k not in _INVERSE_TICKERS}
    return result


def _external_data_production_ready() -> bool:
    try:
        from phase1_trainer.external_data_store import DEFAULT_DB_PATH, ExternalDataStore

        return bool(ExternalDataStore(DEFAULT_DB_PATH).readiness_summary(initialize=False).get("production_ready"))
    except Exception:
        return False


def _append_flag(flags: list[str], flag: str) -> None:
    if flag and flag not in flags:
        flags.append(flag)

KR_TICKERS = {
    "005930": "삼성전자",
    "068270": "셀트리온",
    "035420": "NAVER",
    "035720": "카카오",
    "005380": "현대차",
    "051910": "LG화학",
    # 인버스 ETF (약세장 헤지용)
    "114800": "KODEX인버스",
}

US_TICKERS = {
    # Core 5 — 섹터 다변화된 핵심 유니버스
    "NVDA":  "엔비디아",
    "TSLA":  "테슬라",
    "AAPL":  "애플",
    "GOOGL": "알파벳",
    "NFLX":  "넷플릭스",
    # 인버스 ETF (약세장 헤지용)
    "SQQQ": "나스닥3×인버스",
}

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
    win52 = min(252, max(20, len(d)))
    d["high52"]   = d["high"].rolling(win52, min_periods=5).max()
    d["low52"]    = d["low"].rolling(win52, min_periods=5).min()
    denom52 = (d["high52"] - d["low52"]).replace(0, float("nan"))
    d["pos52"]    = (d["close"] - d["low52"]) / denom52 * 100
    d["gap"]      = (d["open"] - d["close"].shift(1)) / d["close"].shift(1) * 100
    d["vol_ratio"]= d["volume"] / d["vol_avg"]
    d["change_pct"]= d["close"].pct_change(fill_method=None) * 100
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
        try:
            return pd.read_pickle(cache_path)
        except Exception:
            cache_path.unlink(missing_ok=True)

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
    → 상위 N건만 Claude에게 전달
    """
    # 제목 위주 채점 — content는 첫 200자만 (긴 본문으로 인한 score 인플레이션 방지)
    text  = (title + " " + content[:200]).lower()
    score = 1.0  # 기본값

    # ★ 최고 중요도 (+3) — KR + EN
    top_kw = [
        "공시","계약체결","실적발표","영업이익","매출","hbm","ai칩",
        "순매수","순매도","외국인","기관","서킷브레이커","급등","급락",
        "earnings","contract","acquisition","sec filing","beat","miss",
        "guidance cut","guidance raise","layoff","buyback","dividend",
        "fda approval","recall","merger","bankruptcy","fraud","investigation",
    ]
    score += sum(3.0 for kw in top_kw if kw in text)

    # ★ 높은 중요도 (+2) — KR + EN
    high_kw = [
        "반도체","수주","공급","합병","인수","분기","연간","배당",
        "nvda","tsla","aapl","msft","meta","삼성","하이닉스",
        "guidance","revenue","profit","eps","q1","q2","q3","q4",
        "partnership","deal","launch","shipment","supply chain",
    ]
    score += sum(2.0 for kw in high_kw if kw in text)

    # ★ 중간 중요도 (+1) — KR + EN
    mid_kw = [
        "전망","목표주가","투자의견",
        "analyst","upgrade","downgrade","price target","buy","sell","hold",
        "market share","forecast","outlook","rally","decline","surge","plunge",
    ]
    score += sum(1.0 for kw in mid_kw if kw in text)

    # ✗ 패널티 (-2) — 기관 포트폴리오 공시 (저가치: 펀드 보유 변경 등)
    fund_kw = [
        "buys shares of","has $","stake in","position in",
        "increases holdings","largest position","largest holding",
        "stake increased","stake decreased","buys stake","sells stake",
        "wealth management","asset management llc","investment advisory",
        "investment management","securities wealth","financial llc",
    ]
    score -= sum(2.0 for kw in fund_kw if kw in text)

    # ✗ 패널티 (-1) — 기타 저품질
    noise_kw = ["단순시황","증권사광고","이벤트","컬럼","오피니언"]
    score -= sum(1.0 for kw in noise_kw if kw in text)

    return max(0.0, score)


def filter_top_news(news_items: list, top_n: int = 3) -> list:
    """중요도 상위 N건만 반환. 동일 제목 중복 제거."""
    seen_titles = set()
    deduped = []
    for n in news_items:
        title = n.get("title", "")
        key = title[:60].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(n)
    scored = [(score_news(n.get("title",""), n.get("content","")), n)
              for n in deduped]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in scored[:top_n]]


# ── 보조 데이터 로드 ──────────────────────────────────────────────────────────

def _yf_change(symbol: str, period: str = "5d") -> float:
    """yfinance로 전일 대비 등락률 계산"""
    if not _YF_OK:
        return 0.0
    try:
        import logging as _logging
        _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
        df = yf.Ticker(symbol).history(period=period)
        if len(df) < 2:
            return 0.0
        prev = float(df["Close"].iloc[-2])
        last = float(df["Close"].iloc[-1])
        return round((last - prev) / prev * 100, 2) if prev else 0.0
    except Exception:
        return 0.0


def _yf_last(symbol: str, period: str = "5d") -> float:
    """yfinance로 최근 종가"""
    if not _YF_OK:
        return 0.0
    try:
        import logging as _logging
        _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
        df = yf.Ticker(symbol).history(period=period)
        return round(float(df["Close"].iloc[-1]), 2) if not df.empty else 0.0
    except Exception:
        return 0.0


def _yf_multi_change(symbol: str) -> dict:
    """1d / 5d / 20일고점대비 변화율 계산 — 추세 컨텍스트용"""
    if not _YF_OK:
        return {}
    try:
        import logging as _logging
        _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
        df = yf.Ticker(symbol).history(period="30d")
        if df.empty or len(df) < 2:
            return {}
        closes = df["Close"].dropna()
        last = float(closes.iloc[-1])
        def _pct(n: int):
            if len(closes) > n:
                prev = float(closes.iloc[-(n + 1)])
                return round((last - prev) / prev * 100, 2) if prev else None
            return None
        high_20d = float(closes.tail(20).max()) if len(closes) >= 5 else None
        from_high = round((last - high_20d) / high_20d * 100, 2) if high_20d else None
        return {
            "change_1d":         _pct(1),
            "change_5d":         _pct(5),
            "from_20d_high_pct": from_high,
        }
    except Exception:
        return {}


def detect_market_regime(sp500_change: float, vix: float, trend_5d: float = 0.0) -> str:
    """
    시장 레짐 자동 감지.
    Returns: "trending_bull" | "trending_bear" | "ranging" | "high_vol" | "crash"
    """
    if vix >= 35:
        return "crash"
    if vix >= 25:
        return "high_vol"
    if abs(sp500_change) > 1.5:
        return "trending_bull" if sp500_change > 0 else "trending_bear"
    if trend_5d > 0.3:
        return "trending_bull"
    if trend_5d < -0.3:
        return "trending_bear"
    return "ranging"


def _yf_5d_trend(symbol: str) -> float:
    """최근 5일 종가 기준 추세 기울기 (% / day)"""
    if not _YF_OK:
        return 0.0
    try:
        import logging as _logging
        _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
        df = yf.Ticker(symbol).history(period="10d")
        closes = df["Close"].dropna().values
        if len(closes) < 5:
            return 0.0
        closes = closes[-5:]
        # 선형 기울기 (pct/day)
        xs = np.arange(len(closes), dtype=float)
        slope = float(np.polyfit(xs, closes / closes[0] * 100, 1)[0])
        return round(slope, 3)
    except Exception:
        return 0.0


def _yf_premarket(symbol: str) -> float:
    """프리마켓 등락률 (전일 종가 대비 %)"""
    if not _YF_OK:
        return 0.0
    try:
        import logging as _logging
        _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
        tk = yf.Ticker(symbol)
        info = tk.info
        pre = info.get("preMarketPrice") or info.get("regularMarketPreviousClose")
        prev = info.get("previousClose") or info.get("regularMarketPreviousClose")
        if pre and prev and prev > 0:
            return round((pre - prev) / prev * 100, 2)
    except Exception:
        pass
    return 0.0


def _yf_earnings_date(symbol: str) -> str:
    """다음 실적 발표일 (YYYY-MM-DD 또는 '')"""
    if not _YF_OK:
        return ""
    try:
        import logging as _logging
        _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
        cal = yf.Ticker(symbol).calendar
        if cal is None:
            return ""
        # calendar는 dict 또는 DataFrame
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                if hasattr(ed, "__iter__") and not isinstance(ed, str):
                    ed = list(ed)[0]
                return str(ed)[:10] if ed else ""
        elif hasattr(cal, "columns"):
            # DataFrame 형태
            if "Earnings Date" in cal.columns:
                return str(cal["Earnings Date"].iloc[0])[:10]
    except Exception:
        pass
    return ""


_PEAD_PROMPT_INCLUDE_SURPRISE = False
_PEAD_SHADOW_REQUIRED_TRADING_DAYS = 5
_PEAD_MANUAL_REVIEW_CHECKS = (
    "tier_null_rate_checked",
    "surprise_sample_10_checked",
    "prompt_leak_zero_checked",
)


def _pead_shadow_state_path() -> Path:
    return get_runtime_path("state", "pead_shadow_state.json")


def _default_pead_market_state(market: str, target_date: str) -> dict:
    return {
        "market": str(market or "").upper(),
        "shadow_start_date": target_date,
        "last_observed_date": "",
        "trading_days_observed": 0,
        "required_trading_days": _PEAD_SHADOW_REQUIRED_TRADING_DAYS,
        "prompt_surprise_enabled": False,
        "enabled_at": None,
        "manual_review_passed": False,
        "manual_review": {
            "tier_null_rate_checked": False,
            "surprise_sample_10_checked": False,
            "prompt_leak_zero_checked": False,
            "reviewer": "",
            "reviewed_at": "",
            "notes": "",
        },
        "last_shadow_summary": {},
        "updated_at": "",
    }


def _load_pead_shadow_state() -> dict:
    path = _pead_shadow_state_path()
    if not path.exists():
        return {"markets": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"markets": {}}
    if not isinstance(data, dict):
        return {"markets": {}}
    if "markets" not in data:
        market = str(data.get("market") or "").upper()
        if market:
            return {"markets": {market: data}}
        return {"markets": {}}
    if not isinstance(data.get("markets"), dict):
        data["markets"] = {}
    return data


def _save_pead_shadow_state(state: dict) -> None:
    path = _pead_shadow_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _business_days_observed(start_date: str, target_date: str) -> int:
    try:
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(target_date).normalize()
    except Exception:
        return 0
    if pd.isna(start) or pd.isna(end) or end < start:
        return 0
    return len(pd.bdate_range(start, end))


def _pead_manual_review_complete(market_state: dict) -> bool:
    review = market_state.get("manual_review") or {}
    return all(bool(review.get(key)) for key in _PEAD_MANUAL_REVIEW_CHECKS)


def _summarize_pead_shadow_rows(rows: list[dict]) -> dict:
    by_tier: dict[str, dict] = {}
    for row in rows or []:
        tier = str(row.get("confidence_tier") or "low").lower()
        bucket = by_tier.setdefault(
            tier,
            {
                "rows": 0,
                "reported_eps_null": 0,
                "eps_estimate_null": 0,
                "surprise_known": 0,
                "prompt_applied": 0,
            },
        )
        bucket["rows"] += 1
        if row.get("reported_eps") is None:
            bucket["reported_eps_null"] += 1
        if row.get("eps_estimate") is None:
            bucket["eps_estimate_null"] += 1
        if str(row.get("surprise_sign") or "unknown") != "unknown":
            bucket["surprise_known"] += 1
        if bool(row.get("prompt_applied")):
            bucket["prompt_applied"] += 1

    for bucket in by_tier.values():
        rows_count = max(1, int(bucket.get("rows") or 0))
        bucket["reported_eps_null_rate"] = round(bucket["reported_eps_null"] / rows_count, 4)
        bucket["eps_estimate_null_rate"] = round(bucket["eps_estimate_null"] / rows_count, 4)
    return {
        "rows": len(rows or []),
        "by_tier": by_tier,
    }


def _record_pead_shadow_observation(market: str, target_date: str, rows: list[dict]) -> dict:
    market_key = str(market or "").upper()
    state = _load_pead_shadow_state()
    markets = state.setdefault("markets", {})
    market_state = markets.get(market_key)
    if not isinstance(market_state, dict):
        market_state = _default_pead_market_state(market_key, target_date)

    if not market_state.get("shadow_start_date"):
        market_state["shadow_start_date"] = target_date
    market_state.setdefault("required_trading_days", _PEAD_SHADOW_REQUIRED_TRADING_DAYS)
    market_state.setdefault("prompt_surprise_enabled", False)
    market_state.setdefault("manual_review", _default_pead_market_state(market_key, target_date)["manual_review"])

    observed = _business_days_observed(str(market_state.get("shadow_start_date") or target_date), target_date)
    market_state["last_observed_date"] = target_date
    market_state["trading_days_observed"] = max(int(market_state.get("trading_days_observed") or 0), observed)
    market_state["manual_review_passed"] = _pead_manual_review_complete(market_state)
    market_state["last_shadow_summary"] = _summarize_pead_shadow_rows(rows)
    market_state["updated_at"] = datetime.now().isoformat(timespec="seconds")

    markets[market_key] = market_state
    _save_pead_shadow_state(state)
    return market_state


def _pead_surprise_prompt_allowed(market: str, target_date: str) -> bool:
    if not _PEAD_PROMPT_INCLUDE_SURPRISE:
        return False
    market_key = str(market or "").upper()
    market_state = (_load_pead_shadow_state().get("markets") or {}).get(market_key) or {}
    if not market_state:
        return False

    required = int(market_state.get("required_trading_days") or _PEAD_SHADOW_REQUIRED_TRADING_DAYS)
    observed = max(
        int(market_state.get("trading_days_observed") or 0),
        _business_days_observed(str(market_state.get("shadow_start_date") or ""), target_date),
    )
    if observed < required:
        return False
    if not bool(market_state.get("prompt_surprise_enabled")):
        return False
    return _pead_manual_review_complete(market_state)


def _safe_float_or_none(value) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed


def _positive_float_or_none(value) -> Optional[float]:
    parsed = _safe_float_or_none(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        parsed = float(value)
        if np.isnan(parsed) or np.isinf(parsed):
            return None
        return parsed
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _write_json_safe(path: Path, payload: dict) -> dict:
    safe_payload = _json_safe(payload)
    path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return safe_payload


_POSITIVE_SUPPLEMENT_METRICS = {"vix", "dxy", "vkospi", "usd_krw", "oil_wti", "tnx"}


def _merge_live_context_with_supp(live: dict, supp: dict) -> dict:
    merged = dict(live or {})
    for key, value in (supp or {}).items():
        if key in _POSITIVE_SUPPLEMENT_METRICS and _positive_float_or_none(value) is None and key in merged:
            continue
        merged[key] = value
    return merged


def _is_current_session_build(market: str, target_date: str) -> bool:
    try:
        if resolve_session_date_str is not None:
            now_dt = datetime.now(KST) if KST is not None else None
            return str(target_date) == resolve_session_date_str(market, now_dt)
    except Exception:
        pass
    return str(target_date) == datetime.now().strftime("%Y-%m-%d")


def _clean_data_quality_flags(flags, context: dict) -> list[str]:
    cleaned: list[str] = []
    for raw_flag in flags or []:
        flag = str(raw_flag)
        if flag.endswith("_missing"):
            metric = flag[: -len("_missing")]
            if metric in _POSITIVE_SUPPLEMENT_METRICS and _positive_float_or_none(context.get(metric)) is not None:
                continue
        if flag not in cleaned:
            cleaned.append(flag)
    return cleaned


def _metric_prompt_value(value, label: str, digits: int = 1) -> str:
    parsed = _positive_float_or_none(value)
    if parsed is None:
        return f"{label} N/A (결측)"
    return f"{label} {parsed:.{digits}f}"


def _prompt_numeric(value, fmt: str, default: str = "N/A") -> str:
    parsed = _safe_float_or_none(value)
    if parsed is None:
        return default
    return format(parsed, fmt)


def _iter_breadth_rows(items) -> list[dict]:
    rows: list[dict] = []
    if isinstance(items, dict):
        iterable = items.items()
        for ticker, payload in iterable:
            if not isinstance(payload, dict):
                continue
            row = dict(payload)
            row.setdefault("ticker", str(ticker))
            rows.append(row)
    elif isinstance(items, list):
        for payload in items:
            if not isinstance(payload, dict):
                continue
            row = dict(payload)
            if row.get("ticker"):
                rows.append(row)
    return rows


def _item_change_pct(item: dict) -> Optional[float]:
    for key in ("change_pct", "change_rate", "change"):
        parsed = _safe_float_or_none(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _macd_bucket(value) -> str:
    text = str(value or "").lower()
    if "골든" in text or "golden" in text or text in {"gc", "bullish"}:
        return "golden"
    if "데드" in text or "dead" in text or text in {"dc", "bearish"}:
        return "dead"
    return ""


def _ticker_example(item: dict) -> dict:
    ticker = str(item.get("ticker", "") or "").strip()
    change = _item_change_pct(item)
    example = {
        "ticker": ticker,
        "name": str(item.get("name", ticker) or ticker),
        "change_pct": round(change, 2) if change is not None else None,
    }
    rsi = _safe_float_or_none(item.get("rsi"))
    if rsi is not None:
        example["rsi"] = round(rsi, 1)
    macd = str(item.get("macd", "") or "")
    if macd:
        example["macd"] = macd
    vol_ratio = _safe_float_or_none(item.get("vol_ratio"))
    if vol_ratio is not None:
        example["vol_ratio"] = round(vol_ratio, 2)
    return example


def build_breadth_summary(market: str, items, context: Optional[dict] = None) -> dict:
    """Deterministic market breadth summary for Claude prompts."""
    rows = _iter_breadth_rows(items)
    total = len(rows)
    changes = [(row, _item_change_pct(row)) for row in rows]
    valid_changes = [(row, chg) for row, chg in changes if chg is not None]
    advancers = sum(1 for _, chg in valid_changes if chg > 0)
    decliners = sum(1 for _, chg in valid_changes if chg < 0)
    unchanged = sum(1 for _, chg in valid_changes if chg == 0)

    golden = 0
    dead = 0
    overbought = 0
    oversold = 0
    volume_spike = 0
    volume_extreme = 0
    near_52w_high = 0
    at_52w_high = 0
    above_ma60 = 0
    below_ma60 = 0
    earnings_pre = 0
    earnings_post = 0
    sector_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}

    for row in rows:
        macd = _macd_bucket(row.get("macd"))
        if macd == "golden":
            golden += 1
        elif macd == "dead":
            dead += 1

        rsi = _safe_float_or_none(row.get("rsi"))
        if rsi is not None:
            if rsi > 70:
                overbought += 1
            if rsi < 30:
                oversold += 1

        vol_ratio = _safe_float_or_none(row.get("vol_ratio"))
        if vol_ratio is not None:
            if vol_ratio >= 1.5:
                volume_spike += 1
            if vol_ratio >= 3.0:
                volume_extreme += 1

        pos_52w = _safe_float_or_none(row.get("pos_52w"))
        from_high = _safe_float_or_none(row.get("from_high_pct"))
        if pos_52w is not None:
            if pos_52w >= 95:
                near_52w_high += 1
            if pos_52w >= 99:
                at_52w_high += 1
        elif from_high is not None:
            if from_high >= -5:
                near_52w_high += 1
            if from_high >= -1:
                at_52w_high += 1

        ma60 = row.get("above_ma60")
        if ma60 is True or ma60 == 1:
            above_ma60 += 1
        elif ma60 is False or ma60 == 0:
            below_ma60 += 1

        earnings_window = str(row.get("earnings_window", "") or "").lower()
        if earnings_window.startswith("pre") or earnings_window == "today":
            earnings_pre += 1
        elif earnings_window.startswith("post"):
            earnings_post += 1

        sector = str(row.get("sector", "") or "").strip()
        category = str(row.get("category", "") or "").strip()
        if sector:
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1

    sorted_valid = sorted(valid_changes, key=lambda pair: pair[1], reverse=True)
    top_positive = [_ticker_example(row) for row, _ in sorted_valid[:5]]
    top_negative = [_ticker_example(row) for row, _ in sorted_valid[-5:]][::-1]

    ctx = context or {}
    data_quality_flags: list[str] = []
    def _add_quality_flag(flag: str) -> None:
        if flag not in data_quality_flags:
            data_quality_flags.append(flag)

    for flag in ctx.get("data_quality_flags") or []:
        _add_quality_flag(str(flag))
    if str(market or "").upper() == "US":
        if _positive_float_or_none(ctx.get("vix")) is None:
            _add_quality_flag("vix_missing")
        if _positive_float_or_none(ctx.get("dxy")) is None:
            _add_quality_flag("dxy_missing")
    else:
        if _positive_float_or_none(ctx.get("vkospi")) is None:
            _add_quality_flag("vkospi_missing")

    advance_ratio = round(advancers / total, 3) if total else 0.0
    decline_ratio = round(decliners / total, 3) if total else 0.0
    summary = {
        "market": str(market or "").upper(),
        "source": "digest_or_screen",
        "universe_count": total,
        "valid_change_count": len(valid_changes),
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "advance_ratio": advance_ratio,
        "decline_ratio": decline_ratio,
        "golden_cross": golden,
        "dead_cross": dead,
        "rsi_overbought": overbought,
        "rsi_oversold": oversold,
        "volume_spike": volume_spike,
        "volume_extreme": volume_extreme,
        "near_52w_high": near_52w_high,
        "at_52w_high": at_52w_high,
        "above_ma60": above_ma60,
        "below_ma60": below_ma60,
        "earnings_pre": earnings_pre,
        "earnings_post": earnings_post,
        "top_positive": top_positive,
        "top_negative": top_negative,
        "sector_counts": dict(sorted(sector_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]),
        "category_counts": dict(sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]),
        "sector_changes": ctx.get("sectors") or ctx.get("kr_sectors") or {},
        "data_quality_flags": data_quality_flags,
    }
    return _json_safe(summary)


def _format_breadth_summary(summary: dict) -> list[str]:
    if not isinstance(summary, dict) or not summary.get("universe_count"):
        return ["  breadth N/A (요약 없음)"]
    total = int(summary.get("universe_count") or 0)
    valid = int(summary.get("valid_change_count") or 0)
    adv = int(summary.get("advancers") or 0)
    dec = int(summary.get("decliners") or 0)
    flat = int(summary.get("unchanged") or 0)
    adv_ratio = float(summary.get("advance_ratio") or 0) * 100
    lines = [
        f"  universe {total}개 / 변화율 유효 {valid}개",
        f"  상승/하락/보합: {adv}/{dec}/{flat} ({adv_ratio:.0f}% 상승)",
        f"  GC/DC: {int(summary.get('golden_cross') or 0)}/{int(summary.get('dead_cross') or 0)}",
        (
            f"  RSI 과매수/과매도: {int(summary.get('rsi_overbought') or 0)}/"
            f"{int(summary.get('rsi_oversold') or 0)}"
        ),
        (
            f"  거래량 급증/폭증: {int(summary.get('volume_spike') or 0)}/"
            f"{int(summary.get('volume_extreme') or 0)}"
        ),
        (
            f"  52주 고점근접/신고가권: {int(summary.get('near_52w_high') or 0)}/"
            f"{int(summary.get('at_52w_high') or 0)}"
        ),
    ]
    sector_changes = summary.get("sector_changes") or {}
    if isinstance(sector_changes, dict):
        sec = " | ".join(
            f"{k} {float(v):+.2f}%"
            for k, v in sector_changes.items()
            if _safe_float_or_none(v) not in (None, 0.0)
        )
        if sec:
            lines.append(f"  섹터/ETF: {sec}")
    flags = summary.get("data_quality_flags") or []
    if flags:
        lines.append(f"  데이터 품질: {', '.join(str(f) for f in flags)}")
    top_pos = summary.get("top_positive") or []
    top_neg = summary.get("top_negative") or []
    if top_pos:
        lines.append("  상승 예시: " + ", ".join(_format_example(e) for e in top_pos[:3]))
    if top_neg:
        lines.append("  하락 예시: " + ", ".join(_format_example(e) for e in top_neg[:3]))
    return lines


def _format_example(example: dict) -> str:
    ticker = str(example.get("ticker", "") or "").strip()
    name = str(example.get("name", ticker) or ticker)
    change = example.get("change_pct")
    label = ticker if not name or name == ticker else f"{name}({ticker})"
    if change is None:
        return label
    return f"{label} {float(change):+.2f}%"


def _prompt_ticker_label(ticker: str, item: dict) -> str:
    ticker = str(ticker or item.get("ticker", "") or "").strip()
    name = str(item.get("name", ticker) or ticker).strip()
    if not ticker:
        return name or "-"
    if ticker.upper() == "RSI":
        return f"{name}(ticker=RSI)"
    if name and name != ticker:
        return f"{name}(ticker={ticker})"
    return f"ticker={ticker}"


def _normalize_date_str(value) -> str:
    if value is None:
        return ""
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return ""
    if pd.isna(ts):
        return ""
    return str(ts.date())


def _business_day_distance(base_date: str, event_date: str) -> Optional[int]:
    try:
        start = pd.Timestamp(base_date).normalize()
        end = pd.Timestamp(event_date).normalize()
    except Exception:
        return None
    if pd.isna(start) or pd.isna(end):
        return None
    if start == end:
        return 0
    if start < end:
        return len(pd.bdate_range(start, end)) - 1
    return -(len(pd.bdate_range(end, start)) - 1)


def _classify_earnings_window(target_date: str, event_date: str) -> str:
    diff = _business_day_distance(target_date, event_date)
    if diff is None:
        return "none"
    if diff == 0:
        return "today"
    if 0 < diff <= 5:
        return "pre"
    days_since = abs(diff)
    if diff < 0 and days_since <= 1:
        return "post_1"
    if diff < 0 and days_since <= 3:
        return "post_3"
    if diff < 0 and days_since <= 10:
        return "post_10"
    return "none"


def _classify_surprise(actual: Optional[float], estimate: Optional[float]) -> tuple[str, str]:
    if actual is None or estimate is None or estimate == 0:
        return "unknown", "unknown"
    ratio = (actual - estimate) / abs(estimate)
    if ratio > 0.03:
        sign = "beat"
    elif ratio < -0.03:
        sign = "miss"
    else:
        sign = "inline"
    magnitude = abs(ratio)
    if magnitude < 0.05:
        strength = "small"
    elif magnitude < 0.15:
        strength = "medium"
    else:
        strength = "large"
    return sign, strength


def _ticker_symbol_candidates(market: str, ticker: str) -> list[str]:
    raw = str(ticker or "").strip().upper()
    if not raw:
        return []
    if market == "KR" and raw.isdigit():
        return [f"{raw}.KS", f"{raw}.KQ", raw]
    return [raw]


def _load_yf_earnings_events(symbol: str) -> list[dict]:
    if not _YF_OK:
        return []
    try:
        import logging as _logging
        _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
        table = getattr(yf.Ticker(symbol), "earnings_dates", None)
        if table is None or not hasattr(table, "iterrows") or table.empty:
            return []
        events: list[dict] = []
        columns = {str(col).strip().lower(): col for col in table.columns}
        estimate_col = columns.get("eps estimate")
        reported_col = columns.get("reported eps")
        for idx, row in table.iterrows():
            event_date = _normalize_date_str(idx)
            if not event_date:
                continue
            events.append(
                {
                    "event_date": event_date,
                    "eps_estimate": _safe_float_or_none(row.get(estimate_col)) if estimate_col else None,
                    "reported_eps": _safe_float_or_none(row.get(reported_col)) if reported_col else None,
                }
            )
        events.sort(key=lambda item: item["event_date"])
        return events
    except Exception:
        return []


def _pick_relevant_earnings_event(target_date: str, events: list[dict]) -> Optional[dict]:
    if not events:
        return None
    target_ts = pd.Timestamp(target_date).normalize()
    post_candidates: list[tuple[int, dict]] = []
    pre_candidates: list[tuple[int, dict]] = []
    for event in events:
        event_date = str(event.get("event_date", "") or "")
        window = _classify_earnings_window(target_date, event_date)
        if window == "none":
            continue
        event_ts = pd.Timestamp(event_date).normalize()
        distance = abs(_business_day_distance(target_date, event_date) or 0)
        if event_ts <= target_ts:
            post_candidates.append((distance, event))
        else:
            pre_candidates.append((distance, event))
    if post_candidates:
        post_candidates.sort(key=lambda item: item[0])
        return post_candidates[0][1]
    if pre_candidates:
        pre_candidates.sort(key=lambda item: item[0])
        return pre_candidates[0][1]
    return None


def _build_earnings_event_payload(
    market: str,
    ticker: str,
    target_date: str,
    include_surprise: bool = False,
) -> dict:
    payload = {
        "earnings_date": "",
        "earnings_window": "none",
        "confidence_tier": "low",
        "surprise_sign": "unknown",
        "surprise_strength": "unknown",
        "reported_eps": None,
        "eps_estimate": None,
        "pead_bias": "neutral",
        "prompt_applied": False,
        "source_symbol": "",
    }

    fallback_date = ""
    for symbol in _ticker_symbol_candidates(market, ticker):
        if not fallback_date:
            fallback_date = _yf_earnings_date(symbol)
        events = _load_yf_earnings_events(symbol)
        event = _pick_relevant_earnings_event(target_date, events)
        if event is None:
            continue
        event_date = str(event.get("event_date", "") or "")
        payload["earnings_date"] = event_date
        payload["earnings_window"] = _classify_earnings_window(target_date, event_date)
        payload["source_symbol"] = symbol
        payload["confidence_tier"] = "medium"
        if include_surprise:
            actual = _safe_float_or_none(event.get("reported_eps"))
            estimate = _safe_float_or_none(event.get("eps_estimate"))
            payload["reported_eps"] = actual
            payload["eps_estimate"] = estimate
            if actual is not None and estimate is not None:
                payload["surprise_sign"], payload["surprise_strength"] = _classify_surprise(actual, estimate)
                payload["confidence_tier"] = "high"
        break

    if not payload["earnings_date"] and fallback_date:
        payload["earnings_date"] = fallback_date
        payload["earnings_window"] = _classify_earnings_window(target_date, fallback_date)
        payload["confidence_tier"] = "medium"

    window = payload["earnings_window"]
    sign = payload["surprise_sign"]
    if window == "pre":
        payload["pead_bias"] = "pre_earnings_risk"
    elif window.startswith("post_") or window == "today":
        if sign == "beat":
            payload["pead_bias"] = "positive_drift"
        elif sign == "miss":
            payload["pead_bias"] = "negative_drift"
    return payload


def _persist_pead_shadow_rows(market: str, target_date: str, rows: list[dict]) -> None:
    if not rows:
        return
    path = get_runtime_path("logs", "pead", f"{target_date}_{market}_shadow.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    _record_pead_shadow_observation(market, target_date, rows)


def fetch_live_context_us() -> dict:
    """supplement 파일 없을 때 yfinance로 실시간 시장 지표 수집"""
    if not _YF_OK:
        return {}
    log.info("  [live context] yfinance로 US 시장 지표 수집 중...")
    ctx = {
        "sp500":   {"change_pct": _yf_change("^GSPC"), "close": _yf_last("^GSPC")},
        "nasdaq":  {"change_pct": _yf_change("^IXIC"), "close": _yf_last("^IXIC")},
        "vix":     _yf_last("^VIX"),
        "dxy":     _yf_last("DX-Y.NYB"),
        "usd_krw": _yf_last("KRW=X"),
        "oil_wti": _yf_last("CL=F"),
        # 채권 / 신용
        "tnx":     _yf_last("^TNX"),   # 10년 국채금리
        "hyg":     {"change_pct": _yf_change("HYG"), "close": _yf_last("HYG")},  # 하이일드 ETF
        # 섹터 ETF 등락
        "sectors": {
            "XLK": _yf_change("XLK"),   # 기술
            "XLF": _yf_change("XLF"),   # 금융
            "XLE": _yf_change("XLE"),   # 에너지
            "XLY": _yf_change("XLY"),   # 경기소비재
            "XLV": _yf_change("XLV"),   # 헬스케어
            "XLI": _yf_change("XLI"),   # 산업재
            "XLC": _yf_change("XLC"),   # 통신서비스
        },
    }
    return ctx


def fetch_live_context_kr() -> dict:
    """supplement 파일 없을 때 yfinance로 실시간 KR 시장 지표 수집"""
    if not _YF_OK:
        return {}
    log.info("  [live context] yfinance로 KR 시장 지표 수집 중...")
    # TIGER 섹터 ETF 등락률 (KR Tier2 트리거)
    kr_sector_etfs = {
        "091160.KS": "반도체",   # TIGER 반도체
        "227550.KS": "헬스케어", # TIGER 헬스케어
        "139220.KS": "금융",     # TIGER KRX금융
        "309230.KS": "방산",     # TIGER 방산&우주
        "305720.KS": "2차전지",  # TIGER 2차전지테마
    }
    kr_sectors = {}
    for etf_code, sector_name in kr_sector_etfs.items():
        try:
            chg = _yf_change(etf_code)
            if chg != 0:
                kr_sectors[etf_code] = chg
        except Exception:
            pass

    _vkospi_raw = _yf_last("^KS200VOL") or _yf_last("^VKOSPI") or None
    _usd_krw_val = _yf_last("KRW=X") or None
    return {
        "kospi":          {"change_pct": _yf_change("^KS11"), "close": _yf_last("^KS11")},
        "kosdaq":         {"change_pct": _yf_change("^KQ11"), "close": _yf_last("^KQ11")},
        "kospi_trend":    _yf_multi_change("^KS11"),
        "usd_krw":        _usd_krw_val,
        "usd_krw_trend":  _yf_multi_change("KRW=X") if _usd_krw_val else {},
        "vkospi":         _vkospi_raw,
        "kr_sectors":     kr_sectors,
    }


def get_market_vol_trend(market: str = "KR") -> str:
    """오늘 시장 거래량을 20일 평균과 비교해 "high"/"normal"/"low" 반환.

    장중 호출 시 세션 진행률로 외삽(extrapolation)해 구조적 저평가를 보정.
    - KR 세션: 09:00~15:30 KST (390분)
    - US 세션: 09:30~16:00 ET  (390분)
    - 세션 시작 후 30분 미만이면 "normal" 반환 (초반 노이즈 회피)

    yfinance 실패 시 "normal" 반환 (safe default).
    """
    if not _YF_OK:
        return "normal"
    symbol = "^KS11" if market == "KR" else "^GSPC"
    try:
        import yfinance as _yf
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        hist = _yf.Ticker(symbol).history(period="25d")
        if hist.empty or len(hist) < 5:
            return "normal"
        avg_vol   = float(hist["Volume"].iloc[:-1].mean())
        today_vol = float(hist["Volume"].iloc[-1])
        if avg_vol <= 0:
            return "normal"

        # ── 세션 진행률 계산 ────────────────────────────────────────────────
        _SESSION_MINUTES = 390  # KR/US 모두 6.5h
        if market == "KR":
            from zoneinfo import ZoneInfo as _ZI
            _now = _dt.now(_ZI("Asia/Seoul"))
            _sess_start = _now.replace(hour=9, minute=0, second=0, microsecond=0)
            _sess_end   = _now.replace(hour=15, minute=30, second=0, microsecond=0)
        else:
            try:
                from zoneinfo import ZoneInfo as _ZI
                _now = _dt.now(_ZI("America/New_York"))
            except Exception:
                _now = _dt.now(_tz.utc).replace(tzinfo=None)
            _sess_start = _now.replace(hour=9, minute=30, second=0, microsecond=0)
            _sess_end   = _now.replace(hour=16, minute=0, second=0, microsecond=0)

        _elapsed = (_now - _sess_start).total_seconds() / 60
        _total   = (_sess_end - _sess_start).total_seconds() / 60

        if _elapsed < 30:
            return "normal"  # 개장 초반 — 외삽 노이즈 큼

        if 0 < _elapsed < _total:
            # 세션 진행 중: 전체 일간 거래량으로 외삽
            today_vol = today_vol / (_elapsed / _total)

        ratio = today_vol / avg_vol
        if ratio >= 1.5:
            return "high"
        if ratio <= 0.7:
            return "low"
        return "normal"
    except Exception:
        return "normal"


def build_intraday_advisor_context(market: str = "KR") -> dict:
    """hold_advisor용 장중 실시간 시장 컨텍스트.

    Returns
    -------
    {"ok": bool, "text": str}
      ok=True  : 실제 데이터 조회 성공
      ok=False : 조회 실패 (text는 빈 문자열)
    """
    try:
        if market == "KR":
            ctx = fetch_live_context_kr()
            if not ctx:
                return {"ok": False, "text": ""}
            kospi  = ctx.get("kospi", {})
            kosdaq = ctx.get("kosdaq", {})
            usd    = ctx.get("usd_krw")
            trend  = ctx.get("usd_krw_trend", {})
            vkospi = ctx.get("vkospi")
            ktrend = ctx.get("kospi_trend", {})

            _k5d = ktrend.get("change_5d")
            _k5d_str = f" / 5d {_k5d:+.1f}%" if _k5d is not None else ""
            _kospi_chg = _safe_float_or_none(kospi.get('change_pct'))
            _kosdaq_chg = _safe_float_or_none(kosdaq.get('change_pct'))
            kospi_str  = (f"코스피 {kospi.get('close', 0):,.0f} "
                          f"({_kospi_chg:+.2f}%{_k5d_str})" if _kospi_chg is not None else f"코스피 {kospi.get('close', 0):,.0f} (N/A%{_k5d_str})")
            kosdaq_str = (f"코스닥 {kosdaq.get('close', 0):,.0f} "
                          f"({_kosdaq_chg:+.2f}%)" if _kosdaq_chg is not None else f"코스닥 {kosdaq.get('close', 0):,.0f} (N/A%)")
            usd_str = ""
            if usd:
                usd_str = f"USD/KRW {usd:,.0f}"
                if trend.get("change_1d") is not None:
                    usd_str += f" (1d {trend['change_1d']:+.1f}%"
                    if trend.get("from_20d_high_pct") is not None:
                        usd_str += f", 20일고점대비 {trend['from_20d_high_pct']:+.1f}%"
                    usd_str += ")"
            vk_str = f"VKOSPI {vkospi:.1f}" if vkospi else "VKOSPI 결측"
            parts = [kospi_str, kosdaq_str]
            if usd_str:
                parts.append(usd_str)
            parts.append(vk_str)
            return {"ok": True, "text": " | ".join(parts)}

        if market == "US":
            ctx = fetch_live_context_us()
            if not ctx:
                return {"ok": False, "text": ""}
            sp500  = ctx.get("sp500", {})
            nasdaq = ctx.get("nasdaq", {})
            vix    = ctx.get("vix")
            _sp_chg = _safe_float_or_none(sp500.get('change_pct'))
            _nq_chg = _safe_float_or_none(nasdaq.get('change_pct'))
            sp_str = (f"S&P500 {sp500.get('close', 0):,.0f} ({_sp_chg:+.2f}%)" if _sp_chg is not None else f"S&P500 {sp500.get('close', 0):,.0f} (N/A%)")
            nq_str = (f"NASDAQ {nasdaq.get('close', 0):,.0f} ({_nq_chg:+.2f}%)" if _nq_chg is not None else f"NASDAQ {nasdaq.get('close', 0):,.0f} (N/A%)")
            vix_str = _metric_prompt_value(vix, "VIX")
            return {"ok": True, "text": f"{sp_str} | {nq_str} | {vix_str}"}

    except Exception:
        pass

    return {"ok": False, "text": ""}


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
    # 오늘 파일 없으면 최근 5일 내 가장 최근 파일로 폴백
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    news_dir = NEWS_DIR / market.lower()
    for i in range(1, 6):
        fallback = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
        fp = news_dir / f"{fallback}.json"
        if fp.exists():
            log.info(f"  [뉴스] {target_date} 파일 없음 → {fallback} 폴백 사용")
            with open(fp, encoding="utf-8") as f:
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
def build_kr_digest(target_date: str, universe_tickers: Optional[List[str]] = None) -> dict:
    """
    국내 daily_digest 생성
    목표: ~800 토큰
    """
    log.info(f"[국내 digest] {target_date}")
    supp  = load_supplement("KR", target_date)
    news  = load_news_day("KR", target_date)
    prev  = load_prev_result("KR", target_date)

    # supplement 없으면 live yfinance fallback
    if not supp.get("kospi") and _is_current_session_build("KR", target_date):
        live = fetch_live_context_kr()
        supp = _merge_live_context_with_supp(live, supp)  # supp valid values win

    # ── Layer A: 시장 컨텍스트 (~150 토큰) ───────────────────────────────────
    _vkospi_val = _positive_float_or_none(supp.get("vkospi"))
    _usd_krw_val = _positive_float_or_none(supp.get("usd_krw"))
    layer_a = {
        "kospi":           supp.get("kospi", {}),
        "kosdaq":          supp.get("kosdaq", {}),
        "kospi_trend":     supp.get("kospi_trend", {}),
        "usd_krw":         _usd_krw_val,
        "usd_krw_trend":   supp.get("usd_krw_trend", {}),
        "vkospi":          _vkospi_val,
        "foreign_futures": supp.get("foreign_futures", 0),
        "us_prev":         supp.get("us_prev", {}),
        "fomc":            target_date in FOMC_DATES,
        "options_expiry":  supp.get("options_expiry", False),
        "kr_sectors":      supp.get("kr_sectors", {}),
        "data_quality_flags": supp.get("data_quality_flags", []),
        "data_sources":    supp.get("sources", {}),
        "fallback_used":   supp.get("fallback_used", {}),
        "day_of_week":     datetime.strptime(target_date, "%Y-%m-%d").strftime("%A"),
    }
    layer_a["data_quality_flags"] = _clean_data_quality_flags(layer_a.get("data_quality_flags"), layer_a)

    # ── Layer B: 종목 핵심 지표 (~300 토큰) ──────────────────────────────────
    # VKOSPI 20+ 이면 인버스 ETF 포함 (결측(None)은 0으로 처리)
    _vkospi = float(_vkospi_val or 0)
    ticker_map = _ticker_map("KR", universe_tickers, include_inverse=_vkospi >= 20)
    if not news.get("market_news"):
        _append_flag(layer_a["data_quality_flags"], "kr_market_news_missing")
    corp_news = news.get("corp_news") if isinstance(news.get("corp_news"), dict) else {}
    covered_corp = sum(1 for ticker in ticker_map if int((corp_news.get(ticker) or {}).get("count", 0) or 0) > 0)
    if ticker_map and covered_corp / len(ticker_map) < 0.5:
        _append_flag(layer_a["data_quality_flags"], "kr_corp_news_coverage_low")
    if not _external_data_production_ready():
        _append_flag(layer_a["data_quality_flags"], "external_data_empty")
    layer_b = {}

    for ticker, name in ticker_map.items():
        df = load_price_with_cache("KR", ticker)
        if df.empty:
            log.warning(f"주가 데이터 없음: {ticker}")
            continue

        # date 컬럼 타입 강제 통일 (str/mixed 모두 대응)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")

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

        # BB 위치 — None/NaN 안전 처리
        bb_pct = _safe_float_or_none(row.get("bb_pct"))
        bb_pos = ("상단" if bb_pct > 80 else "하단" if bb_pct < 20 else "중간") if bb_pct is not None else "N/A"

        # 거래량 이상 — None/NaN 안전 처리
        vol_r = _safe_float_or_none(row.get("vol_ratio"))
        vol_signal = ("폭증" if vol_r > 3 else "증가" if vol_r > 1.5 else "보통" if vol_r > 0.7 else "감소") if vol_r is not None else "N/A"

        def _safe_int(v, default=0):
            try:
                f = float(v)
                return default if f != f else int(f)  # NaN check
            except (TypeError, ValueError):
                return default

        def _safe_float(v, default=0.0):
            try:
                f = float(v)
                return default if f != f else f
            except (TypeError, ValueError):
                return default

        _flows = supp.get("flows", {}).get(ticker, {})
        _ff   = _flows.get("foreign")    # None = 결측 (0과 구분)
        _inst = _flows.get("institution")
        _short = supp.get("short", {}).get(ticker)  # None = 결측

        # MACD 히스토그램 방향 (확장/축소)
        _macd_hist = _safe_float(row.get("macd", 0)) - _safe_float(row.get("signal", 0))
        _prev_rows = df[df["date"] < pd.Timestamp(target_date)]
        if len(_prev_rows) >= 2:
            _prev_r = _prev_rows.iloc[-2]
            _prev_hist = _safe_float(_prev_r.get("macd", 0)) - _safe_float(_prev_r.get("signal", 0))
            macd_expanding = abs(_macd_hist) > abs(_prev_hist)
        else:
            macd_expanding = None

        earnings_meta = _build_earnings_event_payload("KR", ticker, target_date, include_surprise=False)

        layer_b[ticker] = {
            "name":           name,
            "close":          _safe_int(row.get("close", 0)),
            "change_pct":     round(_safe_float(row.get("change_pct", 0)), 2),
            "rsi":            round(_safe_float(row.get("rsi", 50), 50), 1),
            "macd":           macd_sig,
            "macd_expanding": macd_expanding,  # True=확대, False=축소, None=알수없음
            "bb_pos":         bb_pos,
            "bb_pct":         round(_safe_float(bb_pct, 50), 1),
            "ma_align":       ma_align,
            "vol_ratio":      round(_safe_float(vol_r, 1.0), 2),
            "vol_signal":     vol_signal,
            "pos_52w":        round(_safe_float(row.get("pos52", 50), 50), 1),
            "gap_pct":        round(_safe_float(row.get("gap", 0)), 2),
            "foreign_flow":   _ff,    # None = 데이터 없음
            "inst_flow":      _inst,  # None = 데이터 없음
            "short_ratio":    _short, # None = 데이터 없음
            "disclosure":     bool(news.get("disclosures", {}).get(ticker, [])),
            "earnings_date":  earnings_meta.get("earnings_date", ""),
            "earnings_window": earnings_meta.get("earnings_window", "none"),
            "confidence_tier": earnings_meta.get("confidence_tier", "low"),
            "pead_bias":      earnings_meta.get("pead_bias", "neutral"),
            "prompt_applied": False,
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
        "breadth_summary": build_breadth_summary("KR", layer_b, layer_a),
        "top_news":    layer_c,
        "disclosures": disclosures,
        "prev_result": prev,
        "built_at":    datetime.now().isoformat(),
    }

    # 저장
    path = DIGEST_DIR / f"{target_date}_KR.json"
    digest = _write_json_safe(path, digest)

    log.info(f"  ✅ KR digest 저장: {path.name} "
             f"(뉴스 {len(layer_c)}건, 공시 {len(disclosures)}건, "
             f"종목 {len(layer_b)}개)")
    return digest


@log_call(logger=log, level="INFO")
def build_us_digest(target_date: str, universe_tickers: Optional[List[str]] = None) -> dict:
    """미국 daily_digest 생성"""
    log.info(f"[미국 digest] {target_date}")
    supp = load_supplement("US", target_date)
    news = load_news_day("US", target_date)
    prev = load_prev_result("US", target_date)

    # supplement 없으면 live yfinance fallback
    if not supp.get("sp500") and _is_current_session_build("US", target_date):
        live = fetch_live_context_us()
        supp = _merge_live_context_with_supp(live, supp)  # supp valid values win

    is_fomc      = target_date in FOMC_DATES
    is_fomc_week = any(
        abs((datetime.strptime(target_date, "%Y-%m-%d") -
             datetime.strptime(d, "%Y-%m-%d")).days) <= 2
        for d in FOMC_DATES
    )

    _us_vix = _positive_float_or_none(supp.get("vix"))
    _us_dxy = _positive_float_or_none(supp.get("dxy"))
    _us_usd_krw = _positive_float_or_none(supp.get("usd_krw"))
    _us_oil_wti = _positive_float_or_none(supp.get("oil_wti"))
    _us_tnx = _positive_float_or_none(supp.get("tnx"))
    layer_a = {
        "sp500":      supp.get("sp500", {}),
        "nasdaq":     supp.get("nasdaq", {}),
        "vix":        _us_vix,
        "dxy":        _us_dxy,
        "usd_krw":    _us_usd_krw,
        "oil_wti":    _us_oil_wti,
        # 채권 / 신용 (시장 위험 지표)
        "tnx":        _us_tnx,                    # 10년 국채금리 (%)
        "hyg":        supp.get("hyg", {}),         # 하이일드 ETF 등락
        # 섹터 ETF 등락 (시장 흐름 파악)
        "sectors":    supp.get("sectors", {}),
        # 시장 레짐 자동 감지
        "regime":     detect_market_regime(
            sp500_change=supp.get("sp500", {}).get("change_pct", 0) if isinstance(supp.get("sp500"), dict) else 0,
            vix=float(_us_vix or 0),
        ),
        "fomc":       is_fomc,
        "fomc_week":  is_fomc_week,
        "cpi_day":    supp.get("cpi_day", False),
        "nfp_day":    supp.get("nfp_day", False),
        "premarket":  supp.get("premarket", {}),
        "data_quality_flags": supp.get("data_quality_flags", []),
        "data_sources": supp.get("sources", {}),
        "fallback_used": supp.get("fallback_used", {}),
    }
    layer_a["data_quality_flags"] = _clean_data_quality_flags(layer_a.get("data_quality_flags"), layer_a)

    # VIX 25+ 이상이면 인버스 ETF 포함 (약세 헤지 구간)
    _vix = _us_vix or 0
    ticker_map = _ticker_map("US", universe_tickers, include_inverse=float(_vix) >= 25)
    if not _external_data_production_ready():
        _append_flag(layer_a["data_quality_flags"], "external_data_empty")
    layer_b = {}
    shadow_rows = []
    pead_prompt_allowed = _pead_surprise_prompt_allowed("US", target_date)
    for ticker, name in ticker_map.items():
        df = load_price_with_cache("US", ticker)
        if df.empty:
            continue

        # date 컬럼 타입 강제 통일 (str/mixed 모두 대응)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")

        dt_row = df[df["date"] == pd.Timestamp(target_date)]
        if dt_row.empty:
            past = df[df["date"] < pd.Timestamp(target_date)]
            if past.empty:
                continue
            dt_row = past.iloc[[-1]]

        row = dt_row.iloc[0]
        vol_r    = _safe_float_or_none(row.get("vol_ratio"))
        bb_pct   = _safe_float_or_none(row.get("bb_pct"))
        macd_sig = "골든크로스" if row.get("macd", 0) > row.get("signal", 0) else "데드크로스"

        # 변동성 돌파 목표가 (K=0.45)
        prev_range = float(row.get("high", 0)) - float(row.get("low", 0))
        vb_target  = float(row.get("open", 0)) + prev_range * 0.45

        # ATR (14일, 가격 대비 %)
        close_val = float(row.get("close", 0))
        atr_pct = 0.0
        if "atr" in df.columns and close_val > 0:
            atr_pct = round(float(row.get("atr", 0)) / close_val * 100, 2)
        elif close_val > 0 and len(df) >= 14:
            # ATR 직접 계산 (없을 경우)
            idx = df.index[df["date"] == dt_row.iloc[0]["date"]]
            if len(idx):
                sl = df.loc[max(0, idx[0]-13): idx[0]]
                tr = (sl["high"] - sl["low"]).mean()
                atr_pct = round(float(tr) / close_val * 100, 2)

        # 5일 추세 방향 (가격 파일 기반 - yfinance 호출 없이)
        past_5d = df[df["date"] < pd.Timestamp(target_date)].tail(5)
        trend_5d = 0.0
        if len(past_5d) >= 2:
            xs = np.arange(len(past_5d), dtype=float)
            c0 = float(past_5d["close"].iloc[0])
            if c0 > 0:
                ys = past_5d["close"].values / c0 * 100
                trend_5d = round(float(np.polyfit(xs, ys, 1)[0]), 3)

        # 프리마켓 등락 (supplement 있으면 우선)
        premarket_val = (supp.get("premarket", {}).get(ticker)
                         or _yf_premarket(ticker))

        # 실적 발표일 (yfinance calendar)
        earnings_meta = _build_earnings_event_payload("US", ticker, target_date, include_surprise=True)
        prompt_applied = bool(
            pead_prompt_allowed
            and earnings_meta.get("confidence_tier") == "high"
            and earnings_meta.get("surprise_sign") not in ("", "unknown", None)
        )

        # 감성 점수 (AV에서)
        corp_news  = news.get("corp_news", {}).get(ticker, {})
        avg_sent   = corp_news.get("avg_sentiment", 0)
        news_items = corp_news.get("items", [])
        change_pct_val = _safe_float_or_none(row.get("change_pct"))
        rsi_val = _safe_float_or_none(row.get("rsi"))
        bb_pct_val = _safe_float_or_none(bb_pct)
        vol_ratio_val = _safe_float_or_none(vol_r)
        pos_52w_val = _safe_float_or_none(row.get("pos52"))
        avg_sent_val = _safe_float_or_none(avg_sent)

        layer_b[ticker] = {
            "name":           name,
            "close":          round(close_val, 2),
            "change_pct":     round(change_pct_val if change_pct_val is not None else 0.0, 2),
            "rsi":            round(rsi_val, 1) if rsi_val is not None else None,
            "macd":           macd_sig,
            "bb_pct":         round(bb_pct_val, 1) if bb_pct_val is not None else None,
            "vol_ratio":      round(vol_ratio_val, 2) if vol_ratio_val is not None else None,
            "pos_52w":        round(pos_52w_val, 1) if pos_52w_val is not None else None,
            "vb_target":      round(vb_target, 2),
            "atr_pct":        atr_pct,       # 변동성 지표
            "trend_5d":       trend_5d,      # 5일 추세 기울기 (%/day)
            "premarket_pct":  premarket_val, # 프리마켓 등락 (%)
            "earnings_date":  earnings_meta.get("earnings_date", ""),
            "earnings_window": earnings_meta.get("earnings_window", "none"),
            "confidence_tier": earnings_meta.get("confidence_tier", "low"),
            "surprise_sign":  earnings_meta.get("surprise_sign", "unknown"),
            "surprise_strength": earnings_meta.get("surprise_strength", "unknown"),
            "reported_eps":   earnings_meta.get("reported_eps"),
            "eps_estimate":   earnings_meta.get("eps_estimate"),
            "pead_bias":      earnings_meta.get("pead_bias", "neutral"),
            "prompt_applied": prompt_applied,
            "news_sentiment": round(avg_sent_val, 3) if avg_sent_val is not None else None,
            "sec_filing":     bool([i for i in news_items if i.get("source") == "SEC EDGAR"]),
        }
        shadow_rows.append(
            {
                "ticker": ticker,
                "target_date": target_date,
                "earnings_date": earnings_meta.get("earnings_date", ""),
                "earnings_window": earnings_meta.get("earnings_window", "none"),
                "reported_eps": earnings_meta.get("reported_eps"),
                "eps_estimate": earnings_meta.get("eps_estimate"),
                "surprise_sign": earnings_meta.get("surprise_sign", "unknown"),
                "surprise_strength": earnings_meta.get("surprise_strength", "unknown"),
                "confidence_tier": earnings_meta.get("confidence_tier", "low"),
                "pead_bias": earnings_meta.get("pead_bias", "neutral"),
                "prompt_applied": prompt_applied,
                "source_symbol": earnings_meta.get("source_symbol", ""),
            }
        )

    all_news = list(news.get("market_news", []))
    for t in ticker_map:
        items = news.get("corp_news", {}).get(t, {}).get("items", [])
        # ticker 태그 없으면 주입 (AlphaVantage 포맷)
        for item in items:
            if not item.get("ticker"):
                item = {**item, "ticker": t}
            all_news.append(item)
    top_news = filter_top_news(all_news, top_n=5)
    layer_c  = [{"title": n.get("title",""), "ticker": n.get("ticker","")} for n in top_news]

    digest = {
        "date":        target_date,
        "market":      "US",
        "universe_tickers": list(ticker_map.keys()),
        "context":     layer_a,
        "technicals":  layer_b,
        "breadth_summary": build_breadth_summary("US", layer_b, layer_a),
        "top_news":    layer_c,
        "prev_result": prev,
        "built_at":    datetime.now().isoformat(),
    }

    path = DIGEST_DIR / f"{target_date}_US.json"
    digest = _write_json_safe(path, digest)
    _persist_pead_shadow_rows("US", target_date, shadow_rows)

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
    breadth = digest.get("breadth_summary", {})
    news   = digest.get("top_news", [])
    disc   = digest.get("disclosures", [])
    prev   = digest.get("prev_result", {})

    lines = [
        f"[{date} {market} 시장 데이터]",
        "시장 mode 판단은 breadth 요약과 지수/매크로를 우선하고, 개별 종목은 보조 예시로만 사용.",
    ]

    # 시장 컨텍스트
    lines.append("\n▶ 시장 컨텍스트")
    if market == "KR":
        kospi = ctx.get("kospi", {})
        if kospi:
            # 코스피 1d + 5d 추세
            kospi_5d = ctx.get("kospi_trend", {}).get("change_5d")
            _kp_chg = _safe_float_or_none(kospi.get('change_pct'))
            kospi_str = f"코스피 1d {_kp_chg:+.2f}%" if _kp_chg is not None else "코스피 1d N/A%"
            if kospi_5d is not None:
                kospi_str += f" / 5d {kospi_5d:+.2f}%"

            # 환율 추세 포함
            usd_krw = ctx.get("usd_krw")
            usd_trend = ctx.get("usd_krw_trend", {})
            if usd_krw:
                trend_parts = []
                if usd_trend.get("change_1d") is not None:
                    trend_parts.append(f"1d {usd_trend['change_1d']:+.1f}%")
                if usd_trend.get("change_5d") is not None:
                    trend_parts.append(f"5d {usd_trend['change_5d']:+.1f}%")
                if usd_trend.get("from_20d_high_pct") is not None:
                    trend_parts.append(f"20일고점대비 {usd_trend['from_20d_high_pct']:+.1f}%")
                trend_str = f" ({', '.join(trend_parts)})" if trend_parts else ""
                usd_str = f"USD/KRW {usd_krw:,.0f}{trend_str}"
            else:
                usd_str = "USD/KRW N/A"

            # VKOSPI 결측 처리
            vkospi = ctx.get("vkospi")
            vkospi_str = f"VKOSPI {vkospi:.1f}" if vkospi is not None else "VKOSPI 결측"

            lines.append(f"  {kospi_str} | {usd_str} | {vkospi_str}")

        # 요일 컨텍스트
        day_of_week = ctx.get("day_of_week", "")
        if day_of_week:
            _day_ko = {
                "Monday": "월요일", "Tuesday": "화요일", "Wednesday": "수요일",
                "Thursday": "목요일", "Friday": "금요일",
                "Saturday": "토요일", "Sunday": "일요일",
            }
            lines.append(f"  오늘: {_day_ko.get(day_of_week, day_of_week)}")

        if ctx.get("foreign_futures"):
            lines.append(f"  외국인 선물: {ctx['foreign_futures']:+,}억원")
        us = ctx.get("us_prev", {})
        if us:
            lines.append(f"  전날 미국장: S&P {us.get('sp500',0):+.2f}% "
                         f"나스닥 {us.get('nasdaq',0):+.2f}%")
    else:
        sp = ctx.get("sp500", {})
        nq = ctx.get("nasdaq", {})
        vix_str = _metric_prompt_value(ctx.get("vix"), "VIX")
        dxy_str = _metric_prompt_value(ctx.get("dxy"), "DXY")
        _sp_chg2 = _safe_float_or_none(sp.get('change_pct'))
        _nq_chg2 = _safe_float_or_none(nq.get('change_pct'))
        _sp_part = f"S&P500 {_sp_chg2:+.2f}%" if _sp_chg2 is not None else "S&P500 N/A%"
        _nq_part = f"나스닥 {_nq_chg2:+.2f}%" if _nq_chg2 is not None else "나스닥 N/A%"
        lines.append(f"  {_sp_part} | {_nq_part} | {vix_str} | {dxy_str}")
        # 채권 / 신용
        tnx = ctx.get("tnx", 0)
        hyg = ctx.get("hyg", {})
        if tnx:
            _hyg_chg = _safe_float_or_none(hyg.get('change_pct')) if hyg else None
            hyg_str = f" | HYG {_hyg_chg:+.2f}%" if (_hyg_chg is not None) else ""
            lines.append(f"  10년 국채금리 {tnx:.2f}%{hyg_str}")
        # 섹터 ETF
        sectors = ctx.get("sectors", {})
        if sectors:
            sec_str = " | ".join(
                f"{k} {v:+.2f}%" for k, v in sectors.items() if v != 0
            )
            if sec_str:
                lines.append(f"  섹터: {sec_str}")
        # 시장 레짐
        regime = ctx.get("regime", "")
        _regime_ko = {
            "trending_bull": "상승추세", "trending_bear": "하락추세",
            "ranging": "횡보", "high_vol": "고변동성", "crash": "폭락국면",
        }
        if regime:
            lines.append(f"  시장레짐: {_regime_ko.get(regime, regime)}")

    # 이벤트
    events = []
    if ctx.get("fomc"):         events.append("🚨FOMC 발표일")
    if ctx.get("fomc_week"):    events.append("⚠️FOMC 주간")
    if ctx.get("options_expiry"): events.append("⚠️옵션만기일")
    if ctx.get("cpi_day"):      events.append("⚠️CPI 발표일")
    if events:
        lines.append(f"  이벤트: {' | '.join(events)}")

    # 시장 breadth 요약
    lines.append("\n▶ 시장 breadth 요약")
    lines.extend(_format_breadth_summary(breadth))

    # 종목 지표
    lines.append("\n▶ 종목 기술 지표")
    for ticker, t in tech.items():
        rsi = _safe_float_or_none(t.get("rsi"))
        vol_ratio = _safe_float_or_none(t.get("vol_ratio"))
        bb_pct = _safe_float_or_none(t.get("bb_pct"))
        pos_52w = _safe_float_or_none(t.get("pos_52w"))
        rsi_mark = "🔴과매도" if rsi is not None and rsi < 30 else "🟢과매수" if rsi is not None and rsi > 70 else ""
        vol_mark = "⚡폭증" if vol_ratio is not None and vol_ratio > 3 else "↑증가" if vol_ratio is not None and vol_ratio > 1.5 else ""
        bb_display = t.get("bb_pos") if t.get("bb_pos") not in (None, "") else (_prompt_numeric(bb_pct, ".0f", "N/A") + ("%" if bb_pct is not None else ""))
        close_display = _prompt_numeric(t.get("close"), ",.2f")
        change_display = _prompt_numeric(t.get("change_pct"), "+.2f", "+0.00")
        rsi_display = _prompt_numeric(rsi, ".1f")
        vol_display = _prompt_numeric(vol_ratio, ".1f")
        pos_display = _prompt_numeric(pos_52w, ".0f")
        label = _prompt_ticker_label(ticker, t)
        lines.append(
            f"  [{label}] {close_display} "
            f"{change_display}% | "
            f"RSI {rsi_display}{rsi_mark} | "
            f"MACD {t.get('macd', 'N/A')} | "
            f"BB {bb_display} | "
            f"거래량 {vol_display}배{vol_mark} | "
            f"52주위치 {pos_display}%"
        )
        # 수급 (KR만) — None=결측, 0=실제0으로 구분
        ff   = t.get("foreign_flow")
        inst = t.get("inst_flow")
        short = t.get("short_ratio")
        if ff is not None or inst is not None:
            ff_str   = f"외국인 {ff:+,}억"   if ff   is not None else "외국인 N/A"
            inst_str = f"기관 {inst:+,}억"   if inst  is not None else "기관 N/A"
            short_str = f" | 공매도 {short:.1f}%" if short is not None else ""
            lines.append(f"    수급: {ff_str} | {inst_str}{short_str}")
        # MACD 방향 보강
        macd_exp = t.get("macd_expanding")
        if macd_exp is not None:
            exp_str = "확대중" if macd_exp else "축소중"
            lines[-1] = lines[-1]  # 종목 라인 유지
            # MACD 라인에 방향 추가 (기존 라인 수정)
            for i in range(len(lines) - 1, -1, -1):
                macd_text = str(t.get("macd", "N/A"))
                if f"MACD {macd_text}" in lines[i]:
                    lines[i] = lines[i].replace(
                        f"MACD {macd_text}",
                        f"MACD {macd_text}({exp_str})"
                    )
                    break
        if t.get("disclosure"):
            lines.append(f"    ⭐ 공시 있음")
        # US 추가 지표
        atr = t.get("atr_pct")
        tr5 = t.get("trend_5d")
        pm  = t.get("premarket_pct")
        ed  = t.get("earnings_date", "")
        ew  = t.get("earnings_window", "")
        prompt_applied = bool(t.get("prompt_applied"))
        surprise_sign = str(t.get("surprise_sign", "") or "")
        surprise_strength = str(t.get("surprise_strength", "") or "")
        extras = []
        atr_val = _safe_float_or_none(atr)
        tr5_val = _safe_float_or_none(tr5)
        pm_val = _safe_float_or_none(pm)
        if atr_val is not None and atr_val > 0: extras.append(f"ATR {atr_val:.1f}%")
        if tr5_val is not None and tr5_val != 0: extras.append(f"5일추세 {tr5_val:+.2f}%/일")
        if pm_val is not None and pm_val != 0: extras.append(f"프리마켓 {pm_val:+.2f}%")
        if ed:            extras.append(f"실적 {ed}")
        if ew and ew != "none": extras.append(f"실적창 {ew}")
        if prompt_applied and surprise_sign and surprise_sign != "unknown":
            extras.append(f"surprise {surprise_sign}/{surprise_strength or 'unknown'}")
        if extras:
            lines.append(f"    {' | '.join(extras)}")

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
