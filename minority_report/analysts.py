"""minority_report/analysts.py - Bull/Bear/Neutral 3명 Claude 판단

개선사항:
  1. 페르소나 강화  - 각 분석가 전문 영역·금지 행동 명시
  2. 개별 적중률 피드백 - 자신의 과거 실적만 분리해서 수신
  3. 2라운드 토론  - 1차 판단 후 상대 의견 보고 최종 수정
"""
import os, json, re, time, sys
from typing import Optional
import anthropic
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_analysis_logger, get_judgment_logger, get_minority_logger
from credit_tracker import record as credit_record
from minority_report.raw_call_logger import save as save_raw_call
from bot.candidate_policy import normalize_selection_result, selection_limits

log          = get_minority_logger()
analysis_log = get_analysis_logger()
judgment_log = get_judgment_logger()
client       = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL        = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
# R1 분석가: 비용 절감을 위해 Haiku 사용 (R2 토론은 Sonnet 유지)
# R1_MODEL 환경변수로 오버라이드 가능 (기본 Haiku 4.5)
R1_MODEL     = os.getenv("R1_MODEL", "claude-haiku-4-5-20251001")


def _env_int_bound(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))

STANCES = "AGGRESSIVE|MODERATE_BULL|MILD_BULL|CAUTIOUS|NEUTRAL|MILD_BEAR|CAUTIOUS_BEAR|DEFENSIVE|HALT"
_SELECTION_RECOVERY_FIELDS = (
    "watchlist",
    "trade_ready",
    "reasons",
    "veto",
    "recommended_strategy",
    "risk_tags",
    "max_position_pct",
    "price_targets",
    "tickers",
)


def _selection_field_section(text: str, field: str) -> str:
    start = text.find(f'"{field}"')
    if start == -1:
        return ""
    tail = text[start:]
    next_positions = []
    for name in _SELECTION_RECOVERY_FIELDS:
        if name == field:
            continue
        idx = tail.find(f'"{name}"')
        if idx > 0:
            next_positions.append(idx)
    end = min(next_positions) if next_positions else len(tail)
    return tail[:end]


def _recover_partial_ticker_array(section: str) -> list[str]:
    if not section:
        return []
    return list(dict.fromkeys(re.findall(r'"([A-Z0-9]{2,10})"', section)))


def _recover_partial_reason_map(section: str) -> dict[str, str]:
    if not section:
        return {}
    pairs = re.findall(r'"([A-Z0-9]{2,10})"\s*:\s*"([^"]{1,80})"', section)
    return {k: v.strip() for k, v in pairs}


def _recover_partial_selection_json(s: str) -> dict:
    watchlist = _recover_partial_ticker_array(_selection_field_section(s, "watchlist"))
    legacy_tickers = _recover_partial_ticker_array(_selection_field_section(s, "tickers"))
    trade_ready = _recover_partial_ticker_array(_selection_field_section(s, "trade_ready"))
    reasons = _recover_partial_reason_map(_selection_field_section(s, "reasons"))
    veto = _recover_partial_reason_map(_selection_field_section(s, "veto"))
    watchlist = watchlist or legacy_tickers
    if not (watchlist or trade_ready or reasons or veto):
        return {}
    recovered = {
        "_parse_recovered": True,
        "_fallback_mode": "selection_partial",
    }
    if watchlist:
        recovered["watchlist"] = watchlist
    if trade_ready:
        recovered["trade_ready"] = trade_ready
    if reasons:
        recovered["reasons"] = reasons
    if veto:
        recovered["veto"] = veto
    return recovered


def _extract_json(text: str) -> dict:
    """Claude 응답에서 JSON 추출 — 형식 무관하게 견고하게 파싱"""
    def _fix(s: str) -> str:
        # trailing comma
        s = re.sub(r",(\s*[}\]])", r"\1", s)
        # JSON 비표준 수치 리터럴 (nan, inf)
        s = re.sub(r'\bNaN\b',       '"NaN"',  s)
        s = re.sub(r'\bInfinity\b',  '999',    s)
        s = re.sub(r'\b-Infinity\b', '-999',   s)
        s = re.sub(r'\bnan\b',       '0',      s)
        s = re.sub(r'\binf\b',       '999',    s)
        s = re.sub(r'\b-inf\b',      '-999',   s)
        # 전각 따옴표/콜론 → ASCII
        s = s.replace('\u201c', '"').replace('\u201d', '"')
        s = s.replace('\u2018', "'").replace('\u2019', "'")
        s = s.replace('\uff1a', ':')
        # string value 내 literal 개행·탭 제거 (키-값 구분자 오파싱 방지)
        s = re.sub(r'(?<=":)(\s*"[^"]*?)\n([^"]*?")', lambda m: m.group(0).replace('\n', ' '), s)
        s = s.replace('\r\n', ' ').replace('\r', ' ')
        # 제어문자 제거 (null바이트 등)
        s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
        return s

    def _try_parse(s: str) -> dict:
        try:
            return json.loads(_fix(s))
        except json.JSONDecodeError:
            pass
        recovered = _recover_partial_selection_json(s)
        if recovered:
            log.warning(
                "[_extract_json] JSON 파싱 실패 — partial 복구: "
                f"watch={recovered.get('watchlist', [])[:20]} "
                f"trade={recovered.get('trade_ready', [])[:12]} "
                f"reasons={len(recovered.get('reasons', {}))}개"
            )
            return recovered
        # tickers 배열 추출
        reasons_start = s.find('"reasons"')
        tickers_section = s[:reasons_start] if reasons_start != -1 else s
        tickers = list(dict.fromkeys(re.findall(r'"([A-Z0-9]{2,10})"', tickers_section)))
        # reasons 개별 key:value 쌍 regex 추출 시도
        reasons = {}
        if reasons_start != -1:
            reasons_section = s[reasons_start:]
            pairs = re.findall(r'"([A-Z0-9]{2,10})"\s*:\s*"([^"]{1,60})"', reasons_section)
            reasons = {k: v for k, v in pairs}
        if tickers:
            log.warning(f"[_extract_json] JSON 파싱 실패 — regex 복구: tickers={tickers[:20]} reasons={len(reasons)}개")
            return {
                "tickers": tickers[:20],
                "reasons": reasons,
                "_parse_recovered": True,
                "_fallback_mode": "ticker_regex",
            }
        raise ValueError("tickers 추출 불가")

    # 1차: 닫힌 ```json ... ``` 블록
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return _try_parse(m.group(1))
    # 2차: { ... } 정상 추출
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return _try_parse(text[start:end + 1])
    # 3차: 응답이 max_tokens로 잘린 경우 — 열린 { 뒤 내용으로 필드 regex 복구
    if start != -1:
        partial = text[start:]
        stance_m = re.search(r'"stance"\s*:\s*"([A-Z_]+)"', partial)
        conf_m   = re.search(r'"confidence"\s*:\s*([0-9.]+)', partial)
        reason_m = re.search(r'"key_reason"\s*:\s*"([^"]{1,200})"', partial)
        if stance_m:
            log.warning(f"[_extract_json] 잘린 응답 regex 복구: stance={stance_m.group(1)}")
            return {
                "stance":     stance_m.group(1),
                "confidence": float(conf_m.group(1)) if conf_m else 0.5,
                "key_reason": reason_m.group(1) if reason_m else "응답 잘림",
            }
    raise ValueError(f"JSON 추출 실패: {text[:200]}")
ALLOWED_STANCES = set(STANCES.split("|"))
ALLOWED_STRATEGIES = {"모멘텀", "평균회귀", "갭풀백", "갭+눌림", "갭눌림", "변동성돌파", "관망"}
_LAST_SELECTION_META: dict = {}


def get_last_selection_meta() -> dict:
    return dict(_LAST_SELECTION_META)


def _extract_json(text: str) -> dict:
    """Claude 응답에서 JSON을 추출한다. 잘린 selection 응답은 부분 복구를 시도한다."""

    def _fix(s: str) -> str:
        s = re.sub(r",(\s*[}\]])", r"\1", s)
        s = re.sub(r"\bNaN\b", '"NaN"', s)
        s = re.sub(r"\bInfinity\b", "999", s)
        s = re.sub(r"\b-Infinity\b", "-999", s)
        s = re.sub(r"\bnan\b", "0", s)
        s = re.sub(r"\binf\b", "999", s)
        s = re.sub(r"\b-inf\b", "-999", s)
        s = s.replace("\u201c", '"').replace("\u201d", '"')
        s = s.replace("\u2018", "'").replace("\u2019", "'")
        s = s.replace("\uff1a", ":")
        s = re.sub(r'(?<=":)(\s*"[^"]*?)\n([^"]*?")', lambda m: m.group(0).replace("\n", " "), s)
        s = s.replace("\r\n", " ").replace("\r", " ")
        s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
        return s

    def _try_parse(s: str) -> dict:
        try:
            return json.loads(_fix(s))
        except json.JSONDecodeError:
            pass

        recovered = _recover_partial_selection_json(s)
        if recovered:
            log.warning(
                "[_extract_json] JSON 파싱 실패 — partial 복구: "
                f"watch={recovered.get('watchlist', [])[:20]} "
                f"trade={recovered.get('trade_ready', [])[:12]} "
                f"reasons={len(recovered.get('reasons', {}))}개"
            )
            return recovered

        reasons_start = s.find('"reasons"')
        tickers_section = s[:reasons_start] if reasons_start != -1 else s
        tickers = list(dict.fromkeys(re.findall(r'"([A-Z0-9]{2,10})"', tickers_section)))
        reasons = {}
        if reasons_start != -1:
            reasons_section = s[reasons_start:]
            pairs = re.findall(r'"([A-Z0-9]{2,10})"\s*:\s*"([^"]{1,60})"', reasons_section)
            reasons = {k: v for k, v in pairs}
        if tickers:
            log.warning(f"[_extract_json] JSON 파싱 실패 — regex 복구: tickers={tickers[:20]} reasons={len(reasons)}개")
            return {
                "tickers": tickers[:20],
                "reasons": reasons,
                "_parse_recovered": True,
                "_fallback_mode": "ticker_regex",
            }
        raise ValueError("tickers 추출 불가")

    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return _try_parse(m.group(1))

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return _try_parse(text[start:end + 1])

    if start != -1:
        partial = text[start:]
        recovered = _recover_partial_selection_json(partial)
        if recovered:
            log.warning(
                "[_extract_json] 잘린 selection 응답 복구: "
                f"watch={recovered.get('watchlist', [])[:20]} "
                f"trade={recovered.get('trade_ready', [])[:12]}"
            )
            return recovered
        stance_m = re.search(r'"stance"\s*:\s*"([A-Z_]+)"', partial)
        conf_m = re.search(r'"confidence"\s*:\s*([0-9.]+)', partial)
        reason_m = re.search(r'"key_reason"\s*:\s*"([^"]{1,200})"', partial)
        if stance_m:
            log.warning(f"[_extract_json] 잘린 응답 regex 복구: stance={stance_m.group(1)}")
            return {
                "stance": stance_m.group(1),
                "confidence": float(conf_m.group(1)) if conf_m else 0.5,
                "key_reason": reason_m.group(1) if reason_m else "응답 잘림",
            }
    raise ValueError(f"JSON 추출 실패: {text[:200]}")


def _recent_selection_feedback_section(market: str) -> str:
    try:
        import ticker_selection_db as _tsdb

        recent_feedback = _tsdb.format_recent_selection_feedback(market, days=20)
        if recent_feedback:
            return (
                "\nrecent selection feedback (historical calibration only; "
                "not a same-session chase signal):\n"
                + recent_feedback[:900]
                + "\n"
            )
    except Exception as _e:
        log.debug(f"[ticker-selection] selection feedback skipped: {_e}")
    return ""


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _candidate_liquidity_bucket(turnover: float, ranked_turnovers: list[float]) -> str:
    if turnover <= 0 or not ranked_turnovers:
        return "unknown"
    if len(ranked_turnovers) == 1:
        return "high"
    higher = sum(1 for value in ranked_turnovers if value > turnover)
    percentile = 1.0 - (higher / len(ranked_turnovers))
    if percentile >= 0.67:
        return "high"
    if percentile >= 0.34:
        return "mid"
    return "low"


def _candidate_pullback_bucket(from_high_pct) -> str:
    if from_high_pct is None:
        return "unknown"
    value = _safe_float(from_high_pct, 0.0)
    if value <= -5.0:
        return "deep"
    if value <= -2.0:
        return "pullback"
    if value <= -0.5:
        return "near_high"
    return "at_high"


def _annotate_candidate_prompt_features(candidates: list[dict]) -> list[float]:
    ranked_turnovers: list[float] = []
    for candidate in candidates or []:
        price = _safe_float(candidate.get("price", 0), 0.0)
        volume = _safe_float(candidate.get("volume", 0), 0.0)
        turnover = price * volume
        if turnover > 0:
            ranked_turnovers.append(turnover)
    ranked_turnovers.sort()

    for candidate in candidates or []:
        price = _safe_float(candidate.get("price", 0), 0.0)
        volume = _safe_float(candidate.get("volume", 0), 0.0)
        turnover = price * volume
        if not candidate.get("liquidity_bucket"):
            candidate["liquidity_bucket"] = _candidate_liquidity_bucket(turnover, ranked_turnovers)
        if not candidate.get("from_high_bucket"):
            candidate["from_high_bucket"] = _candidate_pullback_bucket(candidate.get("from_high_pct"))
    return ranked_turnovers


def _mode_family(mode: str) -> str:
    mode = str(mode or "").upper()
    if mode in {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL"}:
        return "RISK_ON"
    if not mode or mode in {"CAUTIOUS", "NEUTRAL"}:
        return "BALANCED"
    return "RISK_OFF"


def _selection_slot_plan(consensus_mode: str, market: str = "") -> list[tuple[str, int]]:
    family = _mode_family(consensus_mode)
    if family == "RISK_ON":
        slots = [
            ("momentum", 2),
            ("gap_pullback", 2),
            ("opening_range_pullback", 1),
            ("mean_reversion", 1),
        ]
    elif family == "BALANCED":
        slots = [
            ("gap_pullback", 2),
            ("opening_range_pullback", 1),
            ("mean_reversion", 1),
            ("momentum", 1),
        ]
    else:
        slots = [("mean_reversion", 1)]
    if str(market).upper() == "KR":
        adjusted = []
        for name, count in slots:
            if name == "momentum":
                count = 1 if family == "RISK_ON" else 0
            if count > 0:
                adjusted.append((name, count))
        return adjusted
    return slots


def _candidate_execution_hint(candidate: dict) -> str:
    or_state = str(candidate.get("or_state", "") or "").strip()
    atr_stage = str(candidate.get("atr_stage", "") or "").strip()
    atr_pct = candidate.get("atr_pct")
    ep_bucket = str(candidate.get("entry_priority_bucket", "") or "").strip()
    fit_strategy = str(candidate.get("execution_fit_strategy", "") or "").strip()
    minutes_to_close = candidate.get("minutes_to_close")
    blackout = candidate.get("entry_blackout_now")

    parts = []
    if or_state:
        parts.append(f"or={or_state}")
    if atr_stage:
        if atr_pct is not None:
            parts.append(f"atr={atr_pct:.1f}%({atr_stage})")
        else:
            parts.append(f"atr={atr_stage}")
    if ep_bucket:
        parts.append(f"ep={ep_bucket}")
    if fit_strategy:
        parts.append(f"fit={fit_strategy}")
    selection_bias = str(candidate.get("selection_bias", "") or "").strip()
    if selection_bias:
        parts.append(f"bias={selection_bias}")
    if minutes_to_close is not None:
        parts.append(f"tclose={float(minutes_to_close):.0f}m")
    if blackout:
        parts.append("blackout=now")
    return "exec=" + ",".join(parts) if parts else ""


def _candidate_earnings_hint(candidate: dict) -> str:
    earnings_window = str(candidate.get("earnings_window", "") or "").strip()
    earnings_date = str(candidate.get("earnings_date", "") or "").strip()
    prompt_applied = bool(candidate.get("prompt_applied"))
    surprise_sign = str(candidate.get("surprise_sign", "") or "").strip()
    surprise_strength = str(candidate.get("surprise_strength", "") or "").strip()

    parts = []
    if earnings_window and earnings_window != "none":
        parts.append(f"earn={earnings_window}")
    elif earnings_date:
        parts.append(f"earn_date={earnings_date}")

    if prompt_applied and surprise_sign and surprise_sign != "unknown":
        parts.append(f"surprise={surprise_sign}/{surprise_strength or 'unknown'}")
    return " ".join(parts)


def _selection_candidate_cap(market: str, watch_max: int, trade_max: int) -> int:
    if market == "US":
        hard_cap = int(os.getenv("US_SELECTION_PROMPT_CAP", "24"))
        watch_margin = 4
    else:
        hard_cap = int(os.getenv("KR_SELECTION_PROMPT_CAP", "28"))
        watch_margin = 8
    target = max(trade_max + 8, min(watch_max + watch_margin, hard_cap))
    return max(trade_max, min(target, hard_cap))


def _selection_prompt_diversity_caps(market: str) -> dict[str, int]:
    if market == "US":
        return {
            "category": 3,
            "sector": 3,
            "overextended": 3,
            "low_liquidity": 2,
        }
    return {
        "category": 2,
        "sector": 2,
        "overextended": 2,
        "low_liquidity": 1,
        "kosdaq": 5,
    }


def _curate_selection_candidates(candidates: list[dict], market: str, prompt_cap: int) -> list[dict]:
    if len(candidates or []) <= prompt_cap:
        _annotate_candidate_prompt_features(candidates or [])
        return list(candidates or [])

    caps = _selection_prompt_diversity_caps(market)
    _annotate_candidate_prompt_features(candidates or [])
    chosen: list[dict] = []
    deferred: list[dict] = []
    category_counts: dict[str, int] = {}
    sector_counts: dict[str, int] = {}
    overextended_count = 0
    low_liquidity_count = 0
    kosdaq_count = 0

    for candidate in candidates or []:
        category = str(candidate.get("category", "") or "").strip().lower()
        sector = str(candidate.get("sector", "") or "").strip().lower()
        liquidity_bucket = str(candidate.get("liquidity_bucket", "") or "").strip().lower()
        from_high_bucket = str(candidate.get("from_high_bucket", "") or "").strip().lower()
        market_type = str(candidate.get("market_type", "") or "").strip().upper()

        blocked = False
        if category and category_counts.get(category, 0) >= caps.get("category", prompt_cap):
            blocked = True
        if sector and sector_counts.get(sector, 0) >= caps.get("sector", prompt_cap):
            blocked = True
        if from_high_bucket in {"at_high", "near_high"} and overextended_count >= caps.get("overextended", prompt_cap):
            blocked = True
        if liquidity_bucket == "low" and low_liquidity_count >= caps.get("low_liquidity", prompt_cap):
            blocked = True
        if market == "KR" and market_type == "KOSDAQ" and kosdaq_count >= caps.get("kosdaq", prompt_cap):
            blocked = True

        if blocked:
            deferred.append(candidate)
            continue

        chosen.append(candidate)
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
        if sector:
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if from_high_bucket in {"at_high", "near_high"}:
            overextended_count += 1
        if liquidity_bucket == "low":
            low_liquidity_count += 1
        if market == "KR" and market_type == "KOSDAQ":
            kosdaq_count += 1
        if len(chosen) >= prompt_cap:
            break

    if len(chosen) < prompt_cap:
        for candidate in deferred:
            chosen.append(candidate)
            if len(chosen) >= prompt_cap:
                break

    return chosen[:prompt_cap]


def _safe_watch_fallback(candidates: list[dict], market: str) -> list[str]:
    limits = selection_limits(market)
    safe_max = min(limits["watch_max"], 12 if market == "US" else 8)
    watch: list[str] = []
    for candidate in candidates or []:
        ticker = str(candidate.get("ticker", "") or "").strip()
        if not ticker:
            continue
        ticker = ticker.upper() if market == "US" else ticker
        if ticker not in watch:
            watch.append(ticker)
        if len(watch) >= safe_max:
            break
    return watch


def _pick_selection_retry_candidates(candidates: list[dict], result: dict, market: str) -> list[dict]:
    retry_cap = min(selection_limits(market)["watch_max"], len(candidates))
    candidate_map: dict[str, dict] = {}
    for candidate in candidates or []:
        ticker = str(candidate.get("ticker", "") or "").strip()
        if not ticker:
            continue
        key = ticker.upper() if market == "US" else ticker
        candidate_map[key] = candidate

    ordered: list[str] = []
    for source in (
        result.get("trade_ready", []),
        result.get("watchlist", []),
        result.get("tickers", []),
        _safe_watch_fallback(candidates, market),
    ):
        if not isinstance(source, list):
            continue
        for ticker in source:
            key = str(ticker or "").strip()
            key = key.upper() if market == "US" else key
            if key in candidate_map and key not in ordered:
                ordered.append(key)
            if len(ordered) >= retry_cap:
                break
        if len(ordered) >= retry_cap:
            break

    if not ordered:
        return list(candidates[:retry_cap])
    return [candidate_map[key] for key in ordered]


def _build_selection_retry_prompt(
    market: str,
    consensus_mode: str,
    retry_candidates: list[dict],
    market_change_pct: Optional[float] = None,
    secondary_change_pct: Optional[float] = None,
) -> str:
    lines = []
    for candidate in retry_candidates:
        rate = _safe_float(candidate.get("change_rate", 0.0), 0.0)
        turnover = _safe_float(candidate.get("price", 0), 0.0) * _safe_float(candidate.get("volume", 0), 0.0)
        liq = str(candidate.get("liquidity_bucket", "") or "").strip() or "unknown"
        pullback = str(candidate.get("from_high_bucket", "") or "").strip() or _candidate_pullback_bucket(candidate.get("from_high_pct"))
        ma60 = candidate.get("above_ma60")
        if market == "KR":
            market_type = candidate.get("market_type", "KOSPI")
            base_pct = (
                secondary_change_pct
                if market_type == "KOSDAQ" and secondary_change_pct is not None
                else market_change_pct
            )
            rs = rate - base_pct if base_pct is not None else None
            rs_text = f"rs={rs:+.1f}%" if rs is not None else ""
        else:
            rs_parts = []
            if market_change_pct is not None:
                rs_parts.append(f"SP{rate - market_change_pct:+.1f}%")
            if secondary_change_pct is not None:
                rs_parts.append(f"NQ{rate - secondary_change_pct:+.1f}%")
            rs_text = f"rs=({'/'.join(rs_parts)})" if rs_parts else ""
        line = " ".join(
            part for part in [
                str(candidate.get("ticker", "") or "").strip(),
                f"chg={rate:+.2f}%",
                rs_text,
                f"liq={liq}",
                f"pullback={pullback}",
                "ma60=above" if ma60 is True else ("ma60=below" if ma60 is False else ""),
                (
                    f"turn=${turnover/1e6:.1f}M"
                    if market == "US" and turnover > 0 else
                    (f"turn={turnover/1e8:.1f}억" if market == "KR" and turnover > 0 else "")
                ),
            ] if part
        )
        lines.append(line)

    watch_max = min(selection_limits(market)["watch_max"], len(retry_candidates))
    trade_max = min(selection_limits(market)["trade_max"], len(retry_candidates))
    watch_floor = min(watch_max, 10 if len(retry_candidates) >= 15 else max(1, len(retry_candidates) // 2))
    return f"""이전 종목선정 응답이 잘려서 다시 묻습니다. 이번에는 아주 짧게 답하세요.
시장: {market}
모드: {consensus_mode}
후보:
{chr(10).join(lines)}

Required output rules:
- keep a broad watchlist: if candidates >= 15 and mode is not DEFENSIVE/HALT, return at least {watch_floor} watchlist names.
- required fields: watchlist, trade_ready, reasons, price_targets
- price_targets is required for every trade_ready ticker. Do not add price_targets for watch_only tickers.
- price_targets prices must be native market prices: KR=KRW, US=USD.
- price_targets fields: buy_zone_low, buy_zone_high, sell_target, stop_loss, hold_days, confidence, cancel_if_open_above, entry_rationale, exit_rationale, rationale.
- price target rationale fields must be short: entry_rationale, exit_rationale, rationale <= 40 chars each.

규칙:
- 후보 중에서만 선택
- watchlist 최대 {watch_max}개
- trade_ready 최대 {trade_max}개
- JSON만 반환
- 필수 필드만 반환: watchlist, trade_ready, reasons, price_targets
- reasons는 짧게
- price_targets 안의 entry_rationale, exit_rationale, rationale도 각각 40자 이내로 짧게

{{
  "watchlist":["code1","code2"],
  "trade_ready":["code1"],
  "reasons":{{"code1":"짧은 이유"}},
  "price_targets":{{
    "code1":{{
      "buy_zone_low":73000,
      "buy_zone_high":73500,
      "sell_target":76000,
      "stop_loss":71000,
      "hold_days":1,
      "confidence":0.65,
      "cancel_if_open_above":74500,
      "entry_rationale":"support pullback",
      "exit_rationale":"near resistance",
      "rationale":"buy near support"
    }}
  }}
}}"""


def _sanitize_analyst_result(result: dict, analyst_type: str) -> dict:
    stance = str(result.get("stance", "NEUTRAL")).strip().upper()
    if stance not in ALLOWED_STANCES:
        log.warning(f"[{analyst_type}] invalid stance={stance} -> NEUTRAL")
        stance = "NEUTRAL"
    try:
        confidence = float(result.get("confidence", 0.3))
    except Exception:
        confidence = 0.3
    confidence = max(0.0, min(1.0, confidence))
    top_risks = result.get("top_risks", [])
    if not isinstance(top_risks, list):
        top_risks = []
    top_risks = [str(x) for x in top_risks[:5]]
    suggested_strategy = str(result.get("suggested_strategy", "관망")).strip()
    if suggested_strategy not in ALLOWED_STRATEGIES:
        suggested_strategy = "관망"
    suggested_size_pct = result.get("suggested_size_pct")
    try:
        suggested_size_pct = max(0.0, min(100.0, float(suggested_size_pct)))
    except Exception:
        suggested_size_pct = None
    return {
        "stance": stance,
        "confidence": confidence,
        "key_reason": str(result.get("key_reason", ""))[:500],
        "full_reasoning": str(result.get("full_reasoning", ""))[:2000],
        "top_risks": top_risks,
        "suggested_strategy": suggested_strategy,
        "suggested_size_pct": suggested_size_pct,
    }


def _fallback_result(error: Exception) -> dict:
    return {
        "stance": "NEUTRAL",
        "confidence": 0.3,
        "key_reason": f"오류:{str(error)[:60]}",
        "full_reasoning": "",
        "top_risks": [],
        "suggested_strategy": "관망",
    }


# ── 강화된 페르소나 ────────────────────────────────────────────────────────────
PERSONAS = {
    "bull": """당신은 15년 경력의 성장주 모멘텀 트레이더입니다.

[전문 영역 — 이 지표들을 우선 확인]
• RSI 과매도(30 이하) 반등 신호
• MACD 골든크로스 or 히스토그램 상향 전환
• 거래량 평균 대비 1.5배 이상 급증
• 볼린저밴드 하단 터치 후 반등
• 52주 신고가 근접 (5% 이내)

[판단 기준]
• 위 신호 2개 이상 → MODERATE_BULL 이상
• 신호 1개 + 시장 분위기 양호 → MILD_BULL
• 기술적 신호 없음 → NEUTRAL 이하

[절대 하지 말 것]
• 환율·VIX만을 이유로 하락 판단 금지 (매크로는 참고만)
• HALT 판단은 시장 전체 서킷브레이커 상황에서만
• 근거 없이 confidence 0.5 이하 부여 금지""",

    "bear": """당신은 헤지펀드 출신 리스크 매니저입니다.

[전문 영역 — 이 지표들을 우선 확인]
• VKOSPI 20 이상 or 전일 대비 급등 (결측이면 중간 불확실성으로 처리)
• USD/KRW 당일 변화 방향: 1d 상승(KRW 약세) = 위험, 1d 하락(KRW 강세) = 위험 완화
  - 절대 수준이 아닌 추세로 판단: 20일고점대비 -5% 이상 하락이면 환율 위험 해소 중
• 외국인 순매도 지속 (3일 이상) — N/A는 판단 유보
• 신용잔고 증가 + 지수 하락 (역배열 신호)
• 거래량 급감 + 상승 종목 수 감소

[판단 기준]
• 위험 신호 1개 → CAUTIOUS_BEAR 이하
• VKOSPI 25 이상 or 환율 당일 +1.5% 이상 급등 → 기본값 DEFENSIVE
• 복수 위험 신호 동시 발생 → HALT 검토
• 위험 신호 없음 → MILD_BEAR 이상 가능
• 월요일이고 금요일 코스피가 하락 마감이었어도, 환율/VKOSPI 안정이면 과도한 하락 판단 금지

[절대 하지 말 것]
• 기술적 반등 신호만으로 BULL 판단 금지
• 위험 신호가 있는데 NEUTRAL 이상 판단 금지
• 근거 없이 AGGRESSIVE 판단 금지
• USD/KRW 절대 수준이 높다는 이유만으로 하락 판단 금지 (추세 방향을 보라)""",

    "neutral": """당신은 퀀트 통계 분석가입니다.

[전문 영역 — 이 관점에서 분석]
• 상승/하락 신호 개수 대비 비교 (몇 대 몇인가)
• 과거 유사 시장 패턴과의 통계적 일치도
• 지표 간 상충 여부 (기술적 긍정 + 매크로 부정 → 불확실)
• 데이터 신뢰도 검증 (데이터 누락시 불확실성 증가)

[판단 기준]
• 상승/하락 신호 균등 → 반드시 NEUTRAL
• 한쪽으로 2:1 이상 기울 때만 MILD_BULL or MILD_BEAR
• confidence는 절대 0.75 초과 금지 (불확실성은 항상 존재)
• 극단 판단(AGGRESSIVE, HALT) 원칙적 금지

[절대 하지 말 것]
• 확신 없이 강한 stance 선택 금지
• 한쪽 분석가 의견에 무조건 동조 금지
• 신호가 명확하지 않은데 confidence 0.7 이상 부여 금지""",
}

# ── 1라운드: 독립 판단 ─────────────────────────────────────────────────────────
def call_analyst(analyst_type: str, digest_prompt: str,
                 brain_summary: str, correction: str,
                 analyst_feedback: str = "",
                 portfolio_info=None,
                 lesson_context: str = "",
                 market: str = "") -> dict:
    """1라운드 독립 판단"""
    feedback_section = f"\n[나의 과거 실적]\n{analyst_feedback}\n" if analyst_feedback else ""

    # 포트폴리오 현황 섹션
    if portfolio_info:
        cash        = portfolio_info.get("cash", 0)
        total       = portfolio_info.get("total_equity", cash)
        max_order   = portfolio_info.get("max_order_krw", 0)
        n_pos       = portfolio_info.get("n_positions", 0)
        max_pos     = portfolio_info.get("max_positions", 3)
        portfolio_section = (
            f"\n[포트폴리오 현황]\n"
            f"• 가용 현금: {cash:,.0f}원\n"
            f"• 총 자산: {total:,.0f}원\n"
            f"• 1회 최대 주문: {max_order:,.0f}원\n"
            f"• 현재 보유 종목: {n_pos}/{max_pos}개\n"
            f"• 잔여 슬롯: {max(0, max_pos - n_pos)}개\n"
        )
    else:
        portfolio_section = ""

    lesson_section = f"\n[recent lesson candidates]\n{lesson_context[:500]}\n" if lesson_context else ""

    prompt = f"""{PERSONAS[analyst_type]}
{feedback_section}{portfolio_section}{lesson_section}
[데이터 해석 가이드 — 반드시 준수]
• 코스피: "1d X% / 5d Y%" 형태 — 1d는 전일 대비, 5d는 주간 추세. 둘 다 확인할 것.
• USD/KRW: "1,465 (1d -0.8%, 5d -3.8%, 20일고점대비 -4.2%)" 형태
  - 1d 음수 = KRW 강세(위험 완화), 양수 = KRW 약세(위험)
  - 20일고점대비 -5% 이상이면 환율 위험은 단기 해소 국면
• VKOSPI 결측: 데이터 없음. 중간 불확실성(보통 수준)으로 처리. DEFENSIVE 판단 근거로 쓰지 말 것.
• 오늘 요일: 월요일이면 금요일 종가 기준임을 감안. 주말 사이 갭 가능성 포함.
• 외국인/기관 N/A: 데이터 없음. 0(순매도도 순매수도 없음)과 다름. 판단 유보.
• MACD 골든크로스(확대중): 추세 강화 신호. MACD 골든크로스(축소중): 추세 약화 주의.

[시장 전체 메모리]
{brain_summary}

[보정 지침]
{correction}

[오늘 시장 데이터]
{digest_prompt}

위 데이터를 당신의 전문 영역 관점에서 분석하세요. 반드시 트렌드 수치(1d/5d)를 근거로 언급하세요.
포트폴리오 현황을 참고하여 이 시장 상황에서 1회 최대 주문금액 대비 몇 %를 투자할지 제안하세요.
JSON으로만 응답 (다른 텍스트 없이):
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"핵심 근거 한 문장 (구체적 지표 수치 포함)",
  "full_reasoning":"상세 분석 2~3문장",
  "top_risks":["위험1","위험2"],
  "suggested_strategy":"모멘텀|평균회귀|갭+눌림|변동성돌파|관망",
  "suggested_size_pct":0~100}}"""

    try:
        resp = client.messages.create(model=R1_MODEL, max_tokens=2048,
                                      messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip()
        result = _sanitize_analyst_result(_extract_json(raw), analyst_type)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens,
                      f"analyst_{analyst_type}_r1", model=R1_MODEL)
        save_raw_call(
            label=f"analyst_{analyst_type}_r1",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
            model=R1_MODEL,
        )
        log.info(f"[{analyst_type} R1] {result.get('stance','-')} "
                 f"conf={result.get('confidence',0):.2f} | "
                 f"{result.get('key_reason','')[:60]}")
        analysis_log.info(
            f"[analyst_r1] {analyst_type} {result.get('stance','-')}",
            extra={"extra": {
                "event": "analyst_response_r1",
                "analyst": analyst_type,
                "stance": result.get("stance"),
                "confidence": result.get("confidence"),
                "key_reason": result.get("key_reason"),
                "top_risks": result.get("top_risks", []),
                "suggested_strategy": result.get("suggested_strategy"),
            }},
        )
        return result
    except Exception as e:
        log.error(f"[{analyst_type} R1] 오류: {e}")
        return _fallback_result(e)


# ── 2라운드: 토론 후 최종 판단 ────────────────────────────────────────────────
def call_analyst_debate(analyst_type: str, my_r1: dict,
                        others: dict, digest_prompt: str,
                        debate_history: str = "",
                        market: str = "") -> dict:
    """
    2라운드: 다른 분석가 의견 + 과거 토론 이력 보고 최종 판단 수정
    others: {analyst_type: r1_result, ...} (자신 제외)
    debate_history: brain.get_debate_summary() 결과
    """
    others_txt = "\n".join(
        f"• {atype.upper()} 분석가: {r['stance']} (확신도 {r.get('confidence',0):.0%})\n"
        f"  근거: {r.get('key_reason','')}"
        for atype, r in others.items()
    )

    history_section = f"\n[과거 토론 이력]\n{debate_history}\n" if debate_history else ""

    prompt = f"""{PERSONAS[analyst_type]}
{history_section}
[당신의 1라운드 판단]
• stance: {my_r1['stance']}
• 확신도: {my_r1.get('confidence', 0):.0%}
• 근거: {my_r1.get('key_reason', '')}

[다른 분석가들의 1라운드 판단]
{others_txt}

[오늘 시장 데이터 요약]
{digest_prompt[:800]}

토론 지침:
• 다른 분석가의 논거를 당신의 전문 영역 관점에서 평가하세요.
• 과거 토론 이력이 있다면, 비슷한 상황에서 의견 변경이 도움이 됐는지 참고하세요.
• 설득력 있는 논거라면 stance를 조정하세요. 그렇지 않으면 유지하세요.
• 단순히 다수에 동조하기 위한 변경은 하지 마세요.

JSON으로만 응답:
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"최종 핵심 근거 한 문장 (구체적 지표 포함)",
  "changed":true|false,
  "change_reason":"변경했다면 설득된 논거, 유지했다면 null"}}"""

    try:
        resp = client.messages.create(model=MODEL, max_tokens=512,
                                      messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip()
        result = _extract_json(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens,
                      f"analyst_{analyst_type}_r2", model=MODEL)
        save_raw_call(
            label=f"analyst_{analyst_type}_r2",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
            model=MODEL,
        )

        changed = result.get("changed", False)
        change_mark = f"→ {result['stance']}" if changed else "유지"
        log.info(f"[{analyst_type} R2] {change_mark} "
                 f"conf={result.get('confidence',0):.2f} | "
                 f"{result.get('key_reason','')[:60]}")

        # r1 데이터 병합 (full_reasoning, top_risks, suggested_strategy 보존)
        merged = {**my_r1, **result}
        return merged
    except Exception as e:
        log.error(f"[{analyst_type} R2] 오류: {e}")
        return my_r1  # 오류 시 1라운드 결과 그대로


# ── 3명 판단 통합 (2라운드 토론 포함) ────────────────────────────────────────
def get_three_judgments(digest_prompt: str, brain_summary: str,
                        correction: str, delay: float = 1.5,
                        market: str = "KR",
                        lesson_context: str = "",
                        portfolio_info=None) -> dict:
    """
    1라운드 독립 판단 → 2라운드 토론 → 최종 판단
    portfolio_info: {"cash", "total_equity", "max_order_krw", "n_positions", "max_positions"}
    """
    from claude_memory import brain as BrainDB

    # ── 1라운드: 개별 적중률 피드백 포함 독립 판단 ──────────────────────────
    log.info("━━ Round 1: 독립 판단 ━━")
    r1 = {}
    for atype in ("bull", "bear", "neutral"):
        try:
            feedback = BrainDB.generate_analyst_summary(market, atype)
        except Exception:
            feedback = ""
        r1[atype] = call_analyst(
            atype,
            digest_prompt,
            brain_summary,
            correction,
            feedback,
            portfolio_info,
            lesson_context=lesson_context,
            market=market,
        )
        time.sleep(delay)

    log.info(f"R1 완료 | Bull:{r1['bull']['stance']} "
             f"Bear:{r1['bear']['stance']} Neut:{r1['neutral']['stance']}")

    # ── 2라운드: 과거 토론 이력 + 상대 의견 보고 최종 수정 ───────────────────
    log.info("━━ Round 2: 토론 ━━")
    try:
        debate_history = BrainDB.get_debate_summary(market, n=5)
    except Exception:
        debate_history = ""

    r2 = {}
    for atype in ("bull", "bear", "neutral"):
        others = {k: v for k, v in r1.items() if k != atype}
        r2[atype] = call_analyst_debate(atype, r1[atype], others,
                                        digest_prompt, debate_history, market=market)
        time.sleep(delay)

    log.info(f"R2 완료 | Bull:{r2['bull']['stance']} "
             f"Bear:{r2['bear']['stance']} Neut:{r2['neutral']['stance']}")

    # 변경 여부 로깅
    changes = []
    for atype in ("bull", "bear", "neutral"):
        if r2[atype].get("changed") or r1[atype]["stance"] != r2[atype]["stance"]:
            reason = r2[atype].get("change_reason", "") or ""
            log.info(f"  [{atype}] 의견 변경: {r1[atype]['stance']} → {r2[atype]['stance']} "
                     f"| {reason[:60]}")
            changes.append({
                "analyst":   atype,
                "r1_stance": r1[atype]["stance"],
                "r2_stance": r2[atype]["stance"],
                "reason":    reason[:120],
            })
        else:
            log.info(f"  [{atype}] 의견 유지: {r2[atype]['stance']}")

    # 토론 결과를 brain.json에 저장
    try:
        from datetime import date as _date
        today_str = _date.today().isoformat()
        BrainDB.save_debate_result(market, today_str, r1, r2)
        log.info(f"[토론 기록 저장] {today_str} {market} 변경={len(changes)}건")
    except Exception as e:
        log.warning(f"[토론 기록 저장 실패] {e}")

    judgment_log.info(
        f"[judgments_final] Bull:{r2['bull']['stance']} "
        f"Bear:{r2['bear']['stance']} Neutral:{r2['neutral']['stance']}",
        extra={"extra": {
            "event":   "three_judgments",
            "round1":  r1,
            "round2":  r2,
            "changes": changes,
            "bull":    r2["bull"],
            "bear":    r2["bear"],
            "neutral": r2["neutral"],
        }},
    )
    return {"bull": r2["bull"], "bear": r2["bear"], "neutral": r2["neutral"],
            "_debate": {"r1": r1, "changes": changes}}


def select_tickers(market: str, digest_prompt: str, consensus_mode: str, candidates: list,
                   intraday_context: str = "",
                   lesson_context: str = "",
                   market_change_pct: Optional[float] = None,
                   secondary_change_pct: Optional[float] = None) -> list:
    """Claude가 WATCH와 TRADE_READY를 분리 선택한다."""
    global _LAST_SELECTION_META
    if not candidates:
        log.warning("[ticker-selection] no candidates -> defaults")
        defaults = {
            "KR": ["005930", "000660", "035420", "005380", "051910", "068270", "207940", "012450"],
            "US": ["NVDA", "TSLA", "AAPL", "GOOGL", "NFLX", "AMD", "INTC", "PLTR"],
        }
        fallback = defaults.get(market, [])
        _LAST_SELECTION_META = {
            "watchlist": fallback,
            "trade_ready": [],
            "reasons": {},
            "veto": {},
            "risk_tags": {},
            "recommended_strategy": {},
            "max_position_pct": {},
        }
        return fallback, {}

    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI

    _now_kr = _dt.now(_ZI("Asia/Seoul"))
    _kr_premarket = (
        market == "KR"
        and (
            (_now_kr.hour == 8 and _now_kr.minute >= 30)
            or (_now_kr.hour == 9 and _now_kr.minute <= 5)
        )
    )

    limits = selection_limits(market)
    prompt_cap = _selection_candidate_cap(market, limits["watch_max"], limits["trade_max"])
    prompt_candidates = _curate_selection_candidates(candidates, market, prompt_cap)
    if len(candidates) > len(prompt_candidates):
        log.info(f"[ticker-selection] {market} prompt candidates trimmed: {len(candidates)} -> {len(prompt_candidates)}")

    ranked_turnovers = _annotate_candidate_prompt_features(prompt_candidates)
    cand_lines = []
    for candidate in prompt_candidates:
        rate = _safe_float(candidate.get("change_rate", 0.0), 0.0)
        vr = _safe_float(candidate.get("vol_ratio", 0.0), 0.0)
        price = _safe_float(candidate.get("price", 0), 0.0)
        volume = _safe_float(candidate.get("volume", 0), 0.0)
        turnover = price * volume

        if market == "KR":
            market_type = candidate.get("market_type", "KOSPI")
            base_pct = (
                secondary_change_pct
                if market_type == "KOSDAQ" and secondary_change_pct is not None
                else market_change_pct
            )
            rs = rate - base_pct if base_pct is not None else None
            rs_str = f"rs={rs:+.1f}%({'KQ' if market_type == 'KOSDAQ' else 'KP'})" if rs is not None else ""
        else:
            rs_parts = []
            if market_change_pct is not None:
                rs_parts.append(f"SP{rate - market_change_pct:+.1f}%")
            if secondary_change_pct is not None:
                rs_parts.append(f"NQ{rate - secondary_change_pct:+.1f}%")
            rs_str = f"rs=({'/'.join(rs_parts)})" if rs_parts else ""

        market_type = str(candidate.get("market_type", "") or "").strip()
        category = str(candidate.get("category", "") or "").strip()
        sector = str(candidate.get("sector", "") or "").strip()
        above_ma60 = candidate.get("above_ma60")
        from_high_pct = candidate.get("from_high_pct")
        liquidity_bucket = str(candidate.get("liquidity_bucket", "") or "").strip() or _candidate_liquidity_bucket(turnover, ranked_turnovers)
        from_high_bucket = str(candidate.get("from_high_bucket", "") or "").strip() or _candidate_pullback_bucket(from_high_pct)
        parts = [
            str(candidate.get("ticker", "") or "").strip(),
            f"chg={rate:+.2f}%",
            rs_str,
            f"p={price:,.2f}".rstrip("0").rstrip(".") if price > 0 else "",
            "" if _kr_premarket else (f"vol={vr:.1f}x" if vr > 0 else ""),
            (
                f"turn={turnover/1e8:.1f}억"
                if market == "KR" and turnover > 0 else
                (f"turn=${turnover/1e6:.1f}M" if market == "US" and turnover > 0 else "")
            ),
            f"board={market_type}" if market_type else "",
            f"category={category}" if category else "",
            f"sector={sector}" if sector else "",
            f"liq={liquidity_bucket}",
            _candidate_earnings_hint(candidate),
            (
                f"from_high={_safe_float(from_high_pct, 0.0):+.1f}%({from_high_bucket})"
                if from_high_pct is not None else
                "from_high=unknown"
            ),
            "ma60=above" if above_ma60 is True else ("ma60=below" if above_ma60 is False else ""),
            _candidate_execution_hint(candidate),
        ]
        cand_lines.append(" ".join([part for part in parts if part]))

    cand_text = "\n".join(cand_lines)
    n_cands = len([c for c in prompt_candidates if c.get("ticker")])
    watch_max = min(limits["watch_max"], n_cands)
    trade_max = min(limits["trade_max"], n_cands)
    slot_plan = _selection_slot_plan(consensus_mode, market)
    slot_text = ", ".join(f"{name}:{count}" for name, count in slot_plan)

    intraday_section = f"\n장중 컨텍스트:\n{intraday_context[:400]}\n" if intraday_context else ""
    brain_section = ""
    try:
        from claude_memory import brain as BrainDB
        brain_summary = BrainDB.generate_prompt_summary(market)
        correction = json.dumps(
            BrainDB.load().get("correction_guide", {}).get(market, {}),
            ensure_ascii=False,
        )
        if brain_summary or correction != "{}":
            brain_section = (
                "\n학습/교정 요약:\n"
                f"{brain_summary[:700]}\n"
                f"correction_guide: {correction[:450]}\n"
            )
    except Exception as _e:
        log.debug(f"[ticker-selection] brain context skipped: {_e}")

    tuner_section = ""
    try:
        from strategy import param_tuner as _param_tuner
        recent = _param_tuner.get_recent_history(market, days=5)[:8]
        if recent:
            slim = [
                {
                    "strategy": row.get("strategy"),
                    "entries": row.get("entries"),
                    "wins": row.get("wins"),
                    "losses": row.get("losses"),
                    "avg_pnl_pct": row.get("avg_pnl", row.get("avg_pnl_pct")),
                }
                for row in recent
            ]
            tuner_section = "\n최근 전략 성과:\n" + json.dumps(slim, ensure_ascii=False)[:600] + "\n"
    except Exception as _e:
        log.debug(f"[ticker-selection] tuner context skipped: {_e}")

    selection_feedback = _recent_selection_feedback_section(market)
    lesson_section = f"\n{lesson_context[:500]}\n" if lesson_context else ""
    prompt = f"""오늘 {market} 세션에서 후보를 WATCH와 TRADE_READY로 분리하세요.
합의 모드: {consensus_mode}
후보 종목:
{cand_text}

시장 컨텍스트:
{digest_prompt[:220]}{intraday_section}{brain_section}{tuner_section}{lesson_section}{selection_feedback[:700]}
규칙:
- 후보 종목 중에서만 고르세요.
- watchlist는 선별 목록입니다. 최대 {watch_max}개, 보통 8~18개 수준으로 제한하세요.
- trade_ready는 실제 매수 권한 후보입니다. 최대 {trade_max}개이며 0개도 허용됩니다.
- trade_ready는 전략 슬롯을 나눠서 고르세요. slot guide: {slot_text}
- 저유동성, 구조화 상품, 과열, 손절폭 과대 후보는 trade_ready에서 제외하세요.
- Use intraday context session_phase/active_strategies/runtime gates to judge execution feasibility, not just strength.
- Treat exec= hints (or/atr/ep/fit/tclose/blackout) as real execution constraints. Strong names with poor exec hints should stay watch_only.
- Use recent selection feedback to calibrate trade_ready aggressiveness.
- Recent selection feedback is historical only. Do not promote a ticker to trade_ready solely because it moved after watch_only earlier in the same session.
- recent selection feedback을 반영해 missed watch_only가 높은 그룹은 명확한 veto 없이 watch_only로만 두지 마세요.
- weak trade_ready가 높은 그룹은 더 강한 RS, 유동성, 장중 품질이 있을 때만 trade_ready로 올리세요.
- reasons와 veto는 짧게 쓰세요.
- recommended_strategy and max_position_pct must reflect conviction and risk, not generic defaults.
- recommended_strategy, risk_tags, max_position_pct는 trade_ready 종목에 대해서만 채우세요.
- price_targets는 trade_ready 종목에 대해서만 채우세요. watch_only 종목에는 price_targets를 쓰지 마세요.
- price_targets 가격 단위는 시장 native 가격입니다. KR은 KRW, US는 USD입니다.
- 각 price target에는 buy_zone_low, buy_zone_high, sell_target, stop_loss, hold_days, confidence, cancel_if_open_above, entry_rationale, exit_rationale, rationale만 포함하세요.
- Long 매수 기준 sell_target은 buy_zone_high보다 높고 stop_loss는 buy_zone_low보다 낮아야 합니다.
- 응답이 길어질 것 같으면 watchlist를 줄이고 watch_only 종목의 optional field는 생략하세요.
- JSON만 반환하세요.

{{
  "watchlist":["code1","code2"],
  "trade_ready":["code1"],
  "reasons":{{"code1":"선정 이유"}},
  "veto":{{"code2":"제외 이유"}},
  "recommended_strategy":{{"code1":"momentum|gap_pullback|mean_reversion|opening_range_pullback|observe"}},
  "risk_tags":{{"code1":["tag1"]}},
  "max_position_pct":{{"code1":20}},
  "price_targets":{{
    "code1":{{
      "buy_zone_low":73000,
      "buy_zone_high":73500,
      "sell_target":76000,
      "stop_loss":71000,
      "hold_days":1,
      "confidence":0.65,
      "cancel_if_open_above":74500,
      "entry_rationale":"support pullback",
      "exit_rationale":"near resistance",
      "rationale":"buy near support, sell into resistance"
    }}
  }}
}}"""

    fallback_meta = normalize_selection_result(
        {
            "watchlist": _safe_watch_fallback(prompt_candidates, market),
            "trade_ready": [],
            "_parse_recovered": True,
            "_fallback_mode": "safe_watch",
        },
        prompt_candidates,
        market,
    )
    fallback = fallback_meta["watchlist"]

    US_INVERSE_ETFS = {"TZA", "SPDN", "NVD", "SQQQ", "SDOW", "SPXU", "SH", "PSQ", "MYY"}
    US_STABLE_ANCHORS = ["T", "VZ", "XLU", "KO", "JNJ", "PG", "O", "VYM", "SCHD"]

    import time as _time
    last_err = None
    resp = None
    for _attempt in range(3):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=_env_int_bound("CLAUDE_SELECTION_MAX_TOKENS", 3200, 1024, 6000),
                messages=[{"role": "user", "content": prompt}],
            )
            last_err = None
            break
        except Exception as _e:
            last_err = _e
            _emsg = str(_e)
            if ("529" in _emsg or "overloaded" in _emsg.lower()) and _attempt < 2:
                _wait = 2 ** (_attempt + 1)
                log.warning(f"[ticker-selection] Claude overloaded -> { _wait }s retry ({_attempt + 1}/3)")
                _time.sleep(_wait)
            else:
                break

    try:
        if last_err is not None:
            raise last_err
        raw = resp.content[0].text.strip()
        result = _extract_json(raw)
        selection_meta = normalize_selection_result(result, prompt_candidates, market)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, "select_tickers", model=MODEL)
        save_raw_call(
            label="select_tickers",
            prompt=prompt,
            raw_response=raw,
            parsed={**result, "_normalized": selection_meta},
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            market=market,
            model=MODEL,
        )
        if result.get("_fallback_mode") == "selection_partial":
            retry_candidates = _pick_selection_retry_candidates(prompt_candidates, result, market)
            if retry_candidates:
                retry_prompt = _build_selection_retry_prompt(
                    market,
                    consensus_mode,
                    retry_candidates,
                    market_change_pct=market_change_pct,
                    secondary_change_pct=secondary_change_pct,
                )
                log.info(
                    f"[ticker-selection] {market} partial recovery -> lightweight retry "
                    f"({len(retry_candidates)} candidates)"
                )
                try:
                    retry_resp = client.messages.create(
                        model=MODEL,
                        max_tokens=_env_int_bound("CLAUDE_SELECTION_RETRY_MAX_TOKENS", 1800, 700, 4000),
                        messages=[{"role": "user", "content": retry_prompt}],
                    )
                    retry_raw = retry_resp.content[0].text.strip()
                    retry_result = _extract_json(retry_raw)
                    retry_meta = normalize_selection_result(retry_result, retry_candidates, market)
                    credit_record(retry_resp.usage.input_tokens, retry_resp.usage.output_tokens, "select_tickers_retry", model=MODEL)
                    save_raw_call(
                        label="select_tickers_retry",
                        prompt=retry_prompt,
                        raw_response=retry_raw,
                        parsed={**retry_result, "_normalized": retry_meta},
                        input_tokens=retry_resp.usage.input_tokens,
                        output_tokens=retry_resp.usage.output_tokens,
                        market=market,
                        model=MODEL,
                    )
                    if not retry_result.get("_parse_recovered") and retry_meta.get("watchlist"):
                        result = retry_result
                        selection_meta = retry_meta
                        log.info(
                            f"[ticker-selection] {market} lightweight retry accepted: "
                            f"watch={retry_meta.get('watchlist', [])} "
                            f"trade_ready={retry_meta.get('trade_ready', [])}"
                        )
                    else:
                        log.warning(
                            f"[ticker-selection] {market} lightweight retry unresolved -> "
                            "keeping partial recovery result"
                        )
                except Exception as retry_exc:
                    log.warning(f"[ticker-selection] {market} lightweight retry failed: {retry_exc}")
        tickers = selection_meta["watchlist"]
        if not tickers:
            raise ValueError("no valid tickers")

        reasons = selection_meta.get("reasons", {})
        if market == "US" and consensus_mode in ("DEFENSIVE", "HALT"):
            non_inverse = [ticker for ticker in tickers if ticker not in US_INVERSE_ETFS]
            if not non_inverse:
                valid = {str(c.get("ticker", "")).upper() for c in prompt_candidates if c.get("ticker")}
                stable_in_candidates = [ticker for ticker in US_STABLE_ANCHORS if ticker in valid]
                if stable_in_candidates:
                    tickers = tickers[:1] + stable_in_candidates[: max(0, watch_max - 1)]
                    selection_meta["watchlist"] = tickers
                    selection_meta["trade_ready"] = [ticker for ticker in selection_meta["trade_ready"] if ticker in tickers]
                    log.info(f"[ticker-selection] DEFENSIVE/HALT anchors added: {tickers}")

        _LAST_SELECTION_META = selection_meta
        log.info(
            f"[ticker-selection] {market} watch={tickers} "
            f"trade_ready={selection_meta.get('trade_ready', [])}"
        )
        analysis_log.info(
            f"[selection] {market} watch={tickers} trade_ready={selection_meta.get('trade_ready', [])}",
            extra={
                "extra": {
                    "event": "ticker_selection",
                    "market": market,
                    "consensus_mode": consensus_mode,
                    "selected": tickers,
                    "trade_ready": selection_meta.get("trade_ready", []),
                    "veto": selection_meta.get("veto", {}),
                    "risk_tags": selection_meta.get("risk_tags", {}),
                    "candidate_count": len(prompt_candidates),
                    "reasons": reasons,
                }
            },
        )
        return tickers, reasons
    except Exception as e:
        _LAST_SELECTION_META = fallback_meta
        log.error(f"[ticker-selection] error: {e} -> fallback")
        return fallback, fallback_meta.get("reasons", {})
