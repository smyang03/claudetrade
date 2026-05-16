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
from credit_tracker import record as credit_record, throttle_state
from minority_report.raw_call_logger import save as save_raw_call
from minority_report.active_lessons import build_active_lesson_context
from bot.candidate_policy import normalize_selection_result, selection_limits
from minority_report.prompt_contracts import (
    COMMON_DECISION_CONTRACT,
    HARD_SOFT_RULE_CONTRACT,
    PRICE_PLAN_CONTRACT,
    SELECTION_EXECUTION_PHASE_CONTRACT,
    SIZING_DECISION_CONTRACT,
)
from runtime.candidate_actions import candidate_action_prompt_contract
from runtime.selection_compact_schema import (
    compact_output_contract,
    compact_schema_enabled,
    reference_prices_from_candidates,
)

log          = get_minority_logger()
analysis_log = get_analysis_logger()
judgment_log = get_judgment_logger()
client       = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL        = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
# R1: bear/neutral은 Haiku(저비용), bull은 Sonnet(방향 conviction 품질 확보)
# R2 토론은 전원 Sonnet 유지
R1_MODEL      = os.getenv("R1_MODEL", "claude-haiku-4-5-20251001")
BULL_R1_MODEL = os.getenv("BULL_R1_MODEL", MODEL)
BEAR_R1_MODEL = os.getenv("BEAR_R1_MODEL", R1_MODEL)
NEUTRAL_R1_MODEL = os.getenv("NEUTRAL_R1_MODEL", R1_MODEL)


def _env_int_bound(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_bool_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _r1_model_for(analyst_type: str) -> str:
    role = str(analyst_type or "").strip().lower()
    if role == "bull":
        return os.getenv("BULL_R1_MODEL", BULL_R1_MODEL or R1_MODEL)
    if role == "bear":
        return os.getenv("BEAR_R1_MODEL", BEAR_R1_MODEL or R1_MODEL)
    if role == "neutral":
        return os.getenv("NEUTRAL_R1_MODEL", NEUTRAL_R1_MODEL or R1_MODEL)
    return os.getenv("R1_MODEL", R1_MODEL)


def _lesson_context_for_prompt(lesson_context: str, *, scope: str = "r1") -> tuple[str, dict]:
    text = str(lesson_context or "").strip()
    scope_key = str(scope or "r1").strip().lower()
    if not text:
        return "", {
            "scope": scope_key,
            "injected": False,
            "chars": 0,
            "max_chars": 0,
            "omitted_chars": 0,
        }
    if scope_key == "r2":
        if not _env_bool_flag("ACTIVE_LESSONS_DEBATE_ENABLED", True):
            return "", {
                "scope": scope_key,
                "injected": False,
                "chars": 0,
                "max_chars": 0,
                "omitted_chars": len(text),
                "disabled": True,
            }
        max_chars = _env_int_bound("ACTIVE_LESSONS_DEBATE_MAX_CHARS", 1200, 120, 3000)
    else:
        default_limit = _env_int_bound("ACTIVE_LESSONS_MAX_CHARS", 3000, 120, 3000)
        max_chars = _env_int_bound("ACTIVE_LESSONS_ANALYST_MAX_CHARS", default_limit, 120, 3000)
    trimmed = text if len(text) <= max_chars else text[: max_chars - 3].rstrip() + "..."
    return trimmed, {
        "scope": scope_key,
        "injected": bool(trimmed),
        "chars": len(trimmed),
        "max_chars": max_chars,
        "omitted_chars": max(0, len(text) - len(trimmed)),
    }


def _merge_lesson_context_meta(prompt_meta: dict, source_meta: Optional[dict] = None) -> dict:
    merged = dict(prompt_meta or {})
    if source_meta:
        merged["source_metadata"] = dict(source_meta)
    return merged


def _json_array_object_cap(items: list[dict], max_chars: int) -> tuple[str, list[dict], int]:
    """Serialize a JSON array without cutting any item in the middle."""

    limit = max(2, int(max_chars or 0))
    parts: list[str] = []
    included: list[dict] = []
    current_len = 2  # opening and closing brackets
    source_items = [dict(item) for item in (items or []) if isinstance(item, dict)]
    for item in source_items:
        try:
            encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str)
        next_len = current_len + len(encoded) + (1 if parts else 0)
        if next_len > limit:
            break
        parts.append(encoded)
        included.append(item)
        current_len = next_len
    omitted = max(0, len(source_items) - len(included))
    return "[" + ",".join(parts) + "]", included, omitted


def _build_tuning_feedback_contract(
    market: str,
    evidence_items: list[dict],
    active_lesson_meta: dict,
) -> tuple[str, dict]:
    if not _env_bool_flag("TUNING_FEEDBACK_CONTRACT_ENABLED", True):
        return "", {}
    market_key = str(market or "").upper()
    known_at = time.strftime("%Y-%m-%dT%H:%M:%S+09:00", time.localtime())
    max_failures = _env_int_bound("TUNING_FEEDBACK_MAX_SIMILAR_FAILURES", 3, 0, 3)
    tags: set[str] = set()
    for item in evidence_items or []:
        negative = item.get("negative_evidence") if isinstance(item.get("negative_evidence"), dict) else {}
        risk_control = item.get("risk_control_view") if isinstance(item.get("risk_control_view"), dict) else {}
        for raw in list(negative.get("risk_tags") or []) + list(risk_control.get("soft_gates") or []):
            tag = str(raw or "").strip().lower()
            if tag:
                tags.add(tag)
        if str(risk_control.get("action_ceiling") or "").upper() == "WATCH":
            tags.add("action_ceiling_watch")

    similar_failures: list[dict] = []
    if tags & {"late_chase", "stale_candidate", "action_ceiling_watch"}:
        similar_failures.append(
            {
                "pattern": "late_chase_after_watch",
                "features": "candidate_age/chase_pct elevated or action_ceiling=WATCH",
                "lesson": "do not BUY_READY without fresh ret_3m/ret_5m plus OR/VWAP/volume confirmation",
            }
        )
    if tags & {"fade", "pb_high", "gap_pullback", "or_missing", "at_high", "deep_pullback"}:
        similar_failures.append(
            {
                "pattern": "weak_rebound_after_pullback",
                "features": "fade/pullback/high-zone context present",
                "lesson": "use WATCH or PROBE_READY until setup maturity is CONFIRMED",
            }
        )
    lesson_ids = [str(item) for item in list((active_lesson_meta or {}).get("ids") or [])[:5]]
    feedback = {
        "rule_version": f"{market_key.lower()}_selection_feedback.v1",
        "known_at": known_at,
        "market": market_key,
        "usage_contract": {
            "allowed": [
                "calibrate soft gate threshold suggestions",
                "inject up to three similar failure lessons",
                "explain why WATCH is safer when live evidence is incomplete",
            ],
            "forbidden": [
                "promote BUY_READY solely from historical missed opportunities",
                "broadly loosen late_chase/fade/or_missing without live confirmation",
                "override QUARANTINE/BENCH or hard facts",
            ],
        },
        "threshold_suggestions": {
            "late_chase_max_age_min": _env_int_bound("KR_LATE_ENTRY_FULL_BUY_END_MIN", 90, 30, 240),
            "late_chase_max_price_change_pct": float(os.getenv("KR_LATE_ENTRY_MAX_CHASE_PCT", "5.0") or 5.0),
            "fresh_override_requires": ["ret_3m_pct", "ret_5m_pct", "opening_range_break_or_vwap_or_volume"],
        },
        "similar_past_failures": similar_failures[:max_failures],
        "active_lesson_ids": lesson_ids,
    }
    if not feedback["similar_past_failures"] and not lesson_ids:
        feedback["usage_contract"]["allowed"].append("no active failure lesson; keep current live evidence primary")
    max_chars = _env_int_bound("TUNING_FEEDBACK_MAX_CHARS", 1100, 300, 2400)
    section = (
        "\nTuning feedback contract (historical calibration only; live evidence remains primary):\n"
        + json.dumps(feedback, ensure_ascii=False, separators=(",", ":"))[:max_chars]
        + "\n"
    )
    return section, feedback

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


def _compact_array_section(text: str, field: str) -> str:
    marker = f'"{field}"'
    raw = str(text or "")
    start = raw.find(marker)
    if start == -1:
        return ""
    colon = raw.find(":", start + len(marker))
    bracket = raw.find("[", colon + 1)
    if colon == -1 or bracket == -1:
        return ""
    depth = 0
    in_string = False
    escape = False
    for idx in range(bracket, len(raw)):
        ch = raw[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return raw[bracket:idx + 1]
    return ""


def _recover_compact_ticker_array(text: str, field: str) -> list[str]:
    section = _compact_array_section(text, field)
    if not section:
        return []
    values: list = []
    try:
        parsed = json.loads(section)
        if isinstance(parsed, list):
            values = parsed
    except Exception:
        values = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', section)
    out: list[str] = []
    for value in values:
        ticker = str(value or "").strip()
        if ticker and ticker not in out:
            out.append(ticker)
    return out


def _recover_compact_watch_selection(text: str) -> dict:
    watchlist = _recover_compact_ticker_array(text, "wl")
    raw_trade_ready = _recover_compact_ticker_array(text, "tr")
    if not watchlist and raw_trade_ready:
        watchlist = list(raw_trade_ready)
    if not watchlist:
        return {}
    return {
        "wl": watchlist,
        "tr": [],
        "ca": [],
        "_parse_recovered": True,
        "_fallback_mode": "compact_watch_recovered",
        "_recovered_raw_trade_ready": raw_trade_ready,
    }


def _extract_json_strict(text: str) -> dict:
    """Extract JSON without partial recovery for machine-contract responses."""

    def _clean(s: str) -> str:
        s = re.sub(r",(\s*[}\]])", r"\1", s)
        s = re.sub(r"\bNaN\b", '"NaN"', s)
        s = re.sub(r"\bInfinity\b", "999", s)
        s = re.sub(r"\b-Infinity\b", "-999", s)
        s = s.replace("\u201c", '"').replace("\u201d", '"')
        s = s.replace("\u2018", "'").replace("\u2019", "'")
        s = s.replace("\uff1a", ":")
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
        return s.strip()

    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"strict_json_not_found:{raw[:80]}")
        raw = raw[start:end + 1]
    return json.loads(_clean(raw))
ALLOWED_STANCES = set(STANCES.split("|"))
ALLOWED_STRATEGIES = {"모멘텀", "평균회귀", "갭풀백", "갭+눌림", "갭눌림", "변동성돌파", "관망"}
_LAST_SELECTION_META: dict = {}


def get_last_selection_meta() -> dict:
    return dict(_LAST_SELECTION_META)


def _force_watch_only_selection_meta(meta: dict, phase: str = "preopen_watch") -> dict:
    clean = dict(meta or {})
    clean["trade_ready"] = []
    for key in (
        "recommended_strategy",
        "max_position_pct",
        "allocation_intent",
        "max_order_cap_pct",
        "risk_budget_pct",
        "size_reason",
        "price_targets",
    ):
        clean[key] = {}
    clean["_forced_watch_only_phase"] = phase
    return clean


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


def _candidate_post_open_hint(candidate: dict) -> str:
    features = candidate.get("post_open_features") if isinstance(candidate.get("post_open_features"), dict) else {}
    if not features:
        return ""
    parts = []
    state = str(features.get("momentum_state") or candidate.get("post_open_momentum_state") or "").strip()
    if state:
        parts.append(f"state={state}")
    for key, label in (
        ("ret_3m_pct", "r3"),
        ("ret_5m_pct", "r5"),
        ("ret_10m_pct", "r10"),
        ("ret_30m_pct", "r30"),
        ("pullback_from_high_pct", "pb_high"),
    ):
        value = features.get(key)
        if value is None:
            continue
        try:
            parts.append(f"{label}={float(value):+.1f}%")
        except Exception:
            pass
    if not parts:
        return ""
    return "post_open=" + ",".join(parts)


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


def _candidate_preopen_pin_hint(candidate: dict) -> str:
    tier = str(candidate.get("preopen_pin_tier", "") or "").strip().upper()
    pinned = bool(candidate.get("preopen_pinned")) or tier == "HARD"
    if not pinned and not tier:
        return ""
    parts = [f"preopen_pin={tier or 'HARD'}"]
    rank = candidate.get("shadow_preopen_rank")
    if rank is not None:
        parts.append(f"rank={rank}")
    score = candidate.get("preopen_score")
    try:
        if score is not None:
            parts.append(f"score={float(score):.2f}")
    except Exception:
        pass
    anchor = candidate.get("preopen_anchor_price") or candidate.get("anchor_price")
    try:
        if anchor is not None and float(anchor) > 0:
            parts.append(f"anchor={float(anchor):.4g}")
    except Exception:
        pass
    turnover = candidate.get("preopen_pin_turnover")
    try:
        if turnover is not None and float(turnover) > 0:
            parts.append(f"pin_turn={float(turnover)/1e6:.1f}M")
    except Exception:
        pass
    if bool(candidate.get("preopen_pin_require_confirmation")):
        parts.append("confirm=required_before_trade_ready")
    reason = str(candidate.get("preopen_pin_reason", "") or "").strip()
    if reason:
        parts.append(f"pin_reason={reason}")
    return " ".join(parts)


def _candidate_quality_hint(candidate: dict) -> str:
    if str(os.getenv("ENABLE_KR_CANDIDATE_QUALITY_PROMPT", "false")).lower() not in {"1", "true", "yes", "on"}:
        return ""
    grade = str(candidate.get("candidate_quality_grade") or "").strip()
    score = candidate.get("candidate_quality_score")
    parts = []
    if grade:
        try:
            parts.append(f"q={grade}{float(score):.0f}")
        except Exception:
            parts.append(f"q={grade}")
    for key, label in (
        ("rs_20d_vs_board", "rs20"),
        ("rs_60d_vs_board", "rs60"),
        ("turnover_vs_20d", "turn20"),
        ("volume_vs_20d", "vol20"),
    ):
        value = candidate.get(key)
        try:
            parts.append(f"{label}={float(value):+.1f}" if "rs" in label else f"{label}={float(value):.1f}x")
        except Exception:
            pass
    flow_bits = []
    for key, label in (
        ("foreign_net_qty_1d", "F1"),
        ("institution_net_qty_1d", "I1"),
        ("foreign_net_qty_5d", "F5"),
        ("institution_net_qty_5d", "I5"),
    ):
        value = candidate.get(key)
        try:
            parsed = float(value)
        except Exception:
            continue
        if parsed > 0:
            flow_bits.append(label + "+")
        elif parsed < 0:
            flow_bits.append(label + "-")
    if flow_bits:
        parts.append("flow=" + "".join(flow_bits))
    gaps = candidate.get("quality_data_gaps")
    if isinstance(gaps, list) and gaps:
        parts.append(f"qgap={len(gaps)}")
    reliability = candidate.get("trainer_cohort_reliability")
    try:
        rel = float(reliability)
        if rel >= 0.70:
            rel_label = "high"
        elif rel >= 0.45:
            rel_label = "mid"
        else:
            rel_label = "low"
        cohort_part = f"cohort={rel_label}"
        for sample_key in (
            "cohort_sample_n",
            "trainer_cohort_sample_n",
            "trainer_cohort_count",
            "cohort_count",
            "sample_count",
        ):
            sample = candidate.get(sample_key)
            try:
                if sample not in (None, ""):
                    cohort_part += f" n={int(float(sample))}"
                    break
            except Exception:
                continue
        parts.append(cohort_part)
    except Exception:
        if candidate.get("trainer_cohort_key") or candidate.get("trainer_cohort_penalty") not in (None, ""):
            parts.append("cohort=thin")
    tier = str(candidate.get("trainer_tier") or candidate.get("trainer_candidate_state") or "").strip().upper()
    if tier:
        parts.append(f"tier={tier}")
    return "quality=" + ",".join(parts) if parts else ""


def _candidate_trainer_hint(candidate: dict) -> str:
    if not _env_bool_flag("CANDIDATE_QUALITY_TRAINER_PROMPT_HINT_ENABLED", True):
        return ""
    state = str(candidate.get("trainer_candidate_state") or "").strip().upper()
    score = candidate.get("trainer_prompt_score")
    risk = candidate.get("trainer_risk_score")
    plan_a = candidate.get("trainer_plan_a_score")
    pathb = candidate.get("trainer_pathb_wait_score")
    if not state and score in (None, "") and risk in (None, ""):
        return ""
    parts = []
    if state:
        parts.append(state)
    try:
        parts.append(f"q={float(score):.0f}")
    except Exception:
        pass
    try:
        parts.append(f"pa={float(plan_a):.0f}")
    except Exception:
        pass
    try:
        parts.append(f"pb={float(pathb):.0f}")
    except Exception:
        pass
    try:
        parts.append(f"risk={float(risk):.0f}")
    except Exception:
        pass
    try:
        failed_ready_count = int(float(candidate.get("repeated_failed_ready_count") or candidate.get("stale_cycle_count") or 0))
        if failed_ready_count > 0:
            parts.append(f"repeated_failed_ready_count={failed_ready_count}")
    except Exception:
        pass
    return "trainer=" + ",".join(parts) if parts else ""


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
    hard_pin_candidates = [
        candidate
        for candidate in candidates or []
        if str((candidate or {}).get("preopen_pin_tier", "") or "").strip().upper() == "HARD"
        or bool((candidate or {}).get("preopen_pinned"))
    ]
    hard_pin_seen: set[str] = set()
    for candidate in hard_pin_candidates:
        ticker = str((candidate or {}).get("ticker", "") or "").strip().upper()
        if not ticker or ticker in hard_pin_seen:
            continue
        hard_pin_seen.add(ticker)
        chosen.append(candidate)
        if len(chosen) >= prompt_cap:
            return chosen[:prompt_cap]
    category_counts: dict[str, int] = {}
    sector_counts: dict[str, int] = {}
    overextended_count = 0
    low_liquidity_count = 0
    kosdaq_count = 0

    for candidate in candidates or []:
        ticker = str((candidate or {}).get("ticker", "") or "").strip().upper()
        if ticker and ticker in hard_pin_seen:
            continue
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


def _trainer_prompt_pool_enabled() -> bool:
    return _env_bool_flag("CANDIDATE_QUALITY_TRAINER_ENABLED", False)


def _trainer_prompt_reorder_enabled() -> bool:
    return _env_bool_flag("CANDIDATE_PROMPT_POOL_REORDER_ENABLED", False)


def _trainer_prompt_target(market: str, fallback: int) -> int:
    market_key = str(market or "").upper()
    return _env_int_bound(
        f"CANDIDATE_PROMPT_POOL_TARGET_{market_key}",
        _env_int_bound(f"{market_key}_PROMPT_POOL_CAP", fallback, 1, 100),
        1,
        100,
    )


def _trainer_prompt_hard_cap(market: str, fallback: int) -> int:
    market_key = str(market or "").upper()
    policy_default = 24 if market_key == "US" else 28
    return _env_int_bound(
        f"CANDIDATE_PROMPT_POOL_HARD_CAP_{market_key}",
        _env_int_bound(f"{market_key}_PROMPT_POOL_CAP", policy_default, 1, 100),
        1,
        100,
    )


def _build_selection_prompt_pool(candidates: list[dict], market: str, prompt_cap: int) -> tuple[list[dict], dict]:
    if not _trainer_prompt_pool_enabled():
        prompt_candidates = _curate_selection_candidates(candidates, market, prompt_cap)
        return prompt_candidates, {
            "enabled": False,
            "prompt_pool_count": len(prompt_candidates),
            "full_pool_count": len(candidates or []),
            "excluded_from_prompt": [],
            "version": "legacy_curate_selection_candidates",
        }
    try:
        from runtime.candidate_prompt_pool import build_trainer_prompt_pool

        target = _trainer_prompt_target(market, prompt_cap)
        hard_cap = _trainer_prompt_hard_cap(market, max(prompt_cap, target))
        result = build_trainer_prompt_pool(
            list(candidates or []),
            market=market,
            target=target,
            hard_cap=hard_cap,
            reorder_enabled=_trainer_prompt_reorder_enabled(),
        )
        prompt_candidates = [dict(row or {}) for row in result.get("prompt_pool") or []]
        scored_pool = list(result.get("scored_pool") or [])
        all_quarantined = (
            bool(scored_pool)
            and not prompt_candidates
            and all(str((row or {}).get("trainer_candidate_state") or "").upper() == "QUARANTINE" for row in scored_pool)
        )
        return prompt_candidates, {
            "enabled": True,
            "version": result.get("version", ""),
            "score_version": result.get("score_version", ""),
            "target": result.get("target"),
            "hard_cap": result.get("hard_cap"),
            "full_pool_count": len(result.get("full_pool") or []),
            "scored_pool_count": len(scored_pool),
            "prompt_pool_count": len(prompt_candidates),
            "excluded_from_prompt": list(result.get("excluded_from_prompt") or []),
            "metrics": dict(result.get("metrics") or {}),
            "prompt_pool": prompt_candidates,
            "scored_pool": scored_pool,
            "safe_empty_prompt_pool": bool(all_quarantined),
            "prompt_pool_empty_reason": "all_candidates_quarantined" if all_quarantined else "",
            "trainer_all_quarantined": bool(all_quarantined),
        }
    except Exception as exc:
        log.warning(f"[ticker-selection] trainer prompt pool failed {market}: {exc}")
        prompt_candidates = _curate_selection_candidates(candidates, market, prompt_cap)
        return prompt_candidates, {
            "enabled": False,
            "error": str(exc),
            "prompt_pool_count": len(prompt_candidates),
            "full_pool_count": len(candidates or []),
            "excluded_from_prompt": [{"reason": "trainer_prompt_pool_error", "detail": str(exc)}],
            "version": "legacy_after_trainer_error",
        }


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
    active_lessons_context: str = "",
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
    active_text, _active_meta = _lesson_context_for_prompt(active_lessons_context, scope="selection")
    active_section = f"\n{active_text}\n" if active_text else ""
    return f"""이전 종목선정 응답이 잘려서 다시 묻습니다. 이번에는 watchlist/reasons 복구만 수행하고 trade_ready는 빈 배열로 반환하세요.
시장: {market}
모드: {consensus_mode}
후보:
{chr(10).join(lines)}
{active_section}
{COMMON_DECISION_CONTRACT}

Required output rules:
- keep a broad watchlist: if candidates >= 15 and mode is not DEFENSIVE/HALT, return at least {watch_floor} watchlist names.
- required fields: watchlist, trade_ready, reasons
- trade_ready must be [] in this retry response.
- DO NOT include price_targets in this response. Price plans will be requested separately.
- reasons는 종목당 15자 이내로 짧게.

규칙:
- 후보 중에서만 선택
- watchlist 최대 {watch_max}개
- trade_ready는 반드시 빈 배열 []
- JSON만 반환

{{
  "watchlist":["code1","code2"],
  "trade_ready":[],
  "reasons":{{"code1":"짧은 이유"}}
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
    market_regime = str(result.get("market_regime", "unknown") or "unknown").strip()
    data_quality = str(result.get("data_quality", "unknown") or "unknown").strip()
    new_buy_permission = str(result.get("new_buy_permission", "selective") or "selective").strip()
    try:
        max_gross_exposure_pct = int(float(result.get("max_gross_exposure_pct", 0) or 0))
    except Exception:
        max_gross_exposure_pct = 0
    max_gross_exposure_pct = max(0, min(100, max_gross_exposure_pct))
    key_confirmations = result.get("key_confirmations", [])
    if not isinstance(key_confirmations, list):
        key_confirmations = []
    key_contradictions = result.get("key_contradictions", [])
    if not isinstance(key_contradictions, list):
        key_contradictions = []
    return {
        "stance": stance,
        "confidence": confidence,
        "key_reason": str(result.get("key_reason", ""))[:500],
        "full_reasoning": str(result.get("full_reasoning", ""))[:2000],
        "top_risks": top_risks,
        "suggested_strategy": suggested_strategy,
        "suggested_size_pct": suggested_size_pct,
        "market_regime": market_regime[:40],
        "data_quality": data_quality[:40],
        "new_buy_permission": new_buy_permission[:40],
        "max_gross_exposure_pct": max_gross_exposure_pct,
        "key_confirmations": [str(x)[:120] for x in key_confirmations[:5]],
        "key_contradictions": [str(x)[:120] for x in key_contradictions[:5]],
    }


def _fallback_result(error: Exception) -> dict:
    return {
        "stance": "NEUTRAL",
        "confidence": 0.3,
        "key_reason": f"오류:{str(error)[:60]}",
        "full_reasoning": "",
        "top_risks": [],
        "market_regime": "unknown",
        "data_quality": "poor",
        "new_buy_permission": "selective",
        "max_gross_exposure_pct": 0,
        "key_confirmations": [],
        "key_contradictions": [],
        "suggested_strategy": "관망",
    }


def _debate_defaults_for_stance(stance: str) -> dict:
    stance_key = str(stance or "").strip().upper()
    if stance_key == "AGGRESSIVE":
        return {"suggested_size_pct": 90.0, "new_buy_permission": "allow", "max_gross_exposure_pct": 100}
    if stance_key == "MODERATE_BULL":
        return {"suggested_size_pct": 70.0, "new_buy_permission": "allow", "max_gross_exposure_pct": 90}
    if stance_key == "MILD_BULL":
        return {"suggested_size_pct": 50.0, "new_buy_permission": "selective", "max_gross_exposure_pct": 70}
    if stance_key in {"CAUTIOUS", "NEUTRAL"}:
        return {"suggested_size_pct": 35.0, "new_buy_permission": "selective", "max_gross_exposure_pct": 50}
    if stance_key == "MILD_BEAR":
        return {"suggested_size_pct": 20.0, "new_buy_permission": "selective", "max_gross_exposure_pct": 30}
    if stance_key == "CAUTIOUS_BEAR":
        return {"suggested_size_pct": 10.0, "new_buy_permission": "block", "max_gross_exposure_pct": 15}
    return {"suggested_size_pct": 0.0, "new_buy_permission": "block", "max_gross_exposure_pct": 0}


def _merge_debate_result(my_r1: dict, result: dict) -> dict:
    clean = dict(result or {})
    stance = str(clean.get("stance", my_r1.get("stance", "NEUTRAL")) or "NEUTRAL").strip().upper()
    if stance not in ALLOWED_STANCES:
        stance = str(my_r1.get("stance", "NEUTRAL") or "NEUTRAL").strip().upper()
    clean["stance"] = stance
    try:
        clean["confidence"] = max(0.0, min(1.0, float(clean.get("confidence", my_r1.get("confidence", 0.5)) or 0.0)))
    except Exception:
        clean["confidence"] = float(my_r1.get("confidence", 0.5) or 0.5)

    changed = bool(clean.get("changed")) or stance != str(my_r1.get("stance", "") or "").strip().upper()
    defaults = _debate_defaults_for_stance(stance)
    for key in ("suggested_size_pct", "max_gross_exposure_pct"):
        if key in clean:
            try:
                clean[key] = max(0.0, min(100.0, float(clean.get(key) or 0.0)))
            except Exception:
                clean.pop(key, None)
        elif changed:
            clean[key] = defaults[key]
    if "new_buy_permission" in clean:
        permission = str(clean.get("new_buy_permission") or "").strip().lower()
        if permission not in {"allow", "selective", "block"}:
            clean.pop("new_buy_permission", None)
        else:
            clean["new_buy_permission"] = permission
    elif changed:
        clean["new_buy_permission"] = defaults["new_buy_permission"]
    if "suggested_strategy" in clean:
        strategy = str(clean.get("suggested_strategy") or "").strip()
        if strategy not in ALLOWED_STRATEGIES:
            clean.pop("suggested_strategy", None)
    return {**my_r1, **clean}


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
• 개별 종목의 위 신호 2개 이상은 해당 종목 모멘텀 근거일 뿐, 시장 MODERATE_BULL의 충분조건이 아님
• 시장 MODERATE_BULL 이상은 breadth 요약(상승 비율, GC/DC, 섹터 확산) 또는 지수/섹터 확인이 동반될 때만
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
• 제공된 breadth 요약의 상승/하락 신호 개수 대비 비교 (직접 재계산 금지)
• 과거 유사 시장 패턴과의 통계적 일치도
• 지표 간 상충 여부 (기술적 긍정 + 매크로 부정 → 불확실)
• 데이터 신뢰도 검증 (데이터 누락시 불확실성 증가)

[판단 기준]
• 상승/하락 신호 균등 → 반드시 NEUTRAL
• 한쪽으로 2:1 이상 기울 때만 MILD_BULL or MILD_BEAR
• 신호가 불명확하면 confidence 0.75 초과 금지. 지표가 한쪽으로 명확하거나 데이터 불확실성이 판단의 핵심 근거일 때만 0.85까지 허용
• 극단 판단(AGGRESSIVE, HALT) 원칙적 금지

[절대 하지 말 것]
• 확신 없이 강한 stance 선택 금지
• 한쪽 분석가 의견에 무조건 동조 금지
• 신호가 명확하지 않은데 confidence 0.7 이상 부여 금지""",
}

US_BEAR_PERSONA = """당신은 미국 주식 헤지펀드 리스크 매니저입니다.

[전문 영역 — US 리스크 축을 우선 확인]
• VIX 수준/변화: 결측이면 calm으로 해석하지 말고 data_quality 불확실성으로 처리
• HYG 하락, TNX 급등, DXY 급등 같은 credit/rate/USD 스트레스
• SPY/QQQ/IWM 및 섹터 ETF(XLK/XLF/XLE 등) 약세 확산
• breadth 악화: 상승 비율 하락, GC/DC 악화, RSI 과매수 과포화 후 둔화
• 대형주 과매수(NVDA/AAPL/GOOGL 등)는 보조 위험이지 단독 시장 판단 근거가 아님

[판단 기준]
• VIX/HYG/TNX/DXY 중 2개 이상 위험 신호 + breadth 악화 → CAUTIOUS_BEAR 이하
• VIX 25 이상 또는 HYG 급락 + 지수 약세 → DEFENSIVE 검토
• 대형주 일부 과매수만 있고 breadth가 양호하면 CAUTIOUS 이상으로 과도 하향 금지
• VIX/DXY가 N/A면 calm으로 보지 말고 data_quality를 mixed로 반영

[절대 하지 말 것]
• KR 지표(VKOSPI, 외국인 선물, USD/KRW)로 US Bear 판단을 주도하지 말 것
• 개별 기술주 1~3개의 과매수만으로 시장 전체 HALT 판단 금지
• 위험 신호가 있는데 AGGRESSIVE 판단 금지"""

BREADTH_FIRST_CONTRACT = """[시장 breadth 우선 계약 — 반드시 준수]
• 시장 mode는 먼저 breadth 요약과 지수/매크로/섹터 흐름으로 판단하세요.
• 개별 종목은 시장 판단의 보조 예시입니다. 1~3개 종목만으로 시장 mode를 결정하지 마세요.
• 제공된 GC/DC, RSI 과매수/과매도, 상승/하락 개수는 코드가 계산한 값입니다. 직접 다시 세지 말고 그대로 사용하세요.
• breadth와 개별 종목 예시가 충돌하면 breadth를 우선하세요.
• VIX/DXY/VKOSPI가 N/A 또는 결측이면 안정 신호가 아니라 data_quality 불확실성입니다.
• key_reason에는 개별 종목을 최대 3개까지만 예시로 언급하세요."""


def _persona_for(analyst_type: str, market: str = "") -> str:
    if analyst_type == "bear" and str(market or "").upper() == "US":
        return US_BEAR_PERSONA
    return PERSONAS[analyst_type]

# ── 1라운드: 독립 판단 ─────────────────────────────────────────────────────────
def call_analyst(analyst_type: str, digest_prompt: str,
                 brain_summary: str, correction: str,
                 analyst_feedback: str = "",
                 portfolio_info=None,
                 lesson_context: str = "",
                 lesson_context_meta: Optional[dict] = None,
                 market: str = "") -> dict:
    """1라운드 독립 판단 — stance/confidence/key_reason 3필드만 반환. 상세 분석은 R2에서."""
    feedback_section = f"\n[나의 과거 실적]\n{analyst_feedback}\n" if analyst_feedback else ""
    lesson_text, lesson_meta = _lesson_context_for_prompt(lesson_context, scope="r1")
    lesson_section = f"\n[recent lesson candidates]\n{lesson_text}\n" if lesson_text else ""

    _r1_model = _r1_model_for(analyst_type)

    prompt = f"""{_persona_for(analyst_type, market)}

{BREADTH_FIRST_CONTRACT}
{COMMON_DECISION_CONTRACT}
{HARD_SOFT_RULE_CONTRACT}
{feedback_section}{lesson_section}
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
JSON으로만 응답 (다른 텍스트 없이):
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"핵심 근거 한 문장 (구체적 지표 수치 포함)"}}"""

    try:
        r1_max_tokens = _env_int_bound("CLAUDE_ANALYST_R1_MAX_TOKENS", 700, 200, 2000)
        resp = client.messages.create(model=_r1_model, max_tokens=r1_max_tokens,
                                      messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip()
        result = _sanitize_analyst_result(_extract_json(raw), analyst_type)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens,
                      f"analyst_{analyst_type}_r1", model=_r1_model)
        save_raw_call(
            label=f"analyst_{analyst_type}_r1",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
            model=_r1_model,
            prompt_version="market_judgment_v4_slim",
            extra={
                "lesson_context": _merge_lesson_context_meta(lesson_meta, lesson_context_meta),
                "model_route": {
                    "analyst": analyst_type,
                    "r1_model": _r1_model,
                    "fallback_model": os.getenv("R1_MODEL", R1_MODEL),
                },
                "token_budget": {
                    "max_tokens": r1_max_tokens,
                    "stop_reason": getattr(resp, "stop_reason", ""),
                },
                "persona": {
                    "market": market,
                    "us_bear_persona": bool(str(analyst_type).lower() == "bear" and str(market or "").upper() == "US"),
                },
            },
        )
        log.info(f"[{analyst_type} R1/{_r1_model.split('-')[1]}] {result.get('stance','-')} "
                 f"conf={result.get('confidence',0):.2f} | "
                 f"{result.get('key_reason','')[:60]}")
        analysis_log.info(
            f"[analyst_r1] {analyst_type} {result.get('stance','-')}",
            extra={"extra": {
                "event": "analyst_response_r1",
                "analyst": analyst_type,
                "r1_model": _r1_model,
                "stance": result.get("stance"),
                "confidence": result.get("confidence"),
                "key_reason": result.get("key_reason"),
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
                        market: str = "",
                        lesson_context: str = "",
                        lesson_context_meta: Optional[dict] = None) -> dict:
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
    lesson_text, lesson_meta = _lesson_context_for_prompt(lesson_context, scope="r2")
    lesson_section = f"\n[recent lesson candidates]\n{lesson_text}\n" if lesson_text else ""

    prompt = f"""{_persona_for(analyst_type, market)}
{BREADTH_FIRST_CONTRACT}
{history_section}{lesson_section}
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
  "suggested_size_pct":0~100,
  "new_buy_permission":"allow|selective|block",
  "max_gross_exposure_pct":0~100,
  "suggested_strategy":"모멘텀|평균회귀|갭+눌림|변동성돌파|관망",
  "changed":true|false,
  "change_reason":"변경했다면 설득된 논거, 유지했다면 null"}}"""

    try:
        r2_max_tokens = _env_int_bound("CLAUDE_ANALYST_R2_MAX_TOKENS", 900, 300, 2500)
        resp = client.messages.create(model=MODEL, max_tokens=r2_max_tokens,
                                      messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip()
        result = _extract_json(raw)
        merged = _merge_debate_result(my_r1, result)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens,
                      f"analyst_{analyst_type}_r2", model=MODEL)
        save_raw_call(
            label=f"analyst_{analyst_type}_r2",
            prompt=prompt, raw_response=raw, parsed={**result, "_merged": merged},
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
            model=MODEL,
            prompt_version="market_debate_v3_sizing",
            extra={
                "lesson_context": _merge_lesson_context_meta(lesson_meta, lesson_context_meta),
                "model_route": {
                    "analyst": analyst_type,
                    "r2_model": MODEL,
                },
                "token_budget": {
                    "max_tokens": r2_max_tokens,
                    "stop_reason": getattr(resp, "stop_reason", ""),
                },
                "persona": {
                    "market": market,
                    "us_bear_persona": bool(str(analyst_type).lower() == "bear" and str(market or "").upper() == "US"),
                },
            },
        )

        changed = merged.get("changed", False)
        change_mark = f"→ {merged['stance']}" if changed else "유지"
        log.info(f"[{analyst_type} R2] {change_mark} "
                 f"conf={merged.get('confidence',0):.2f} | "
                 f"{merged.get('key_reason','')[:60]}")

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

    active_lesson_meta: Optional[dict] = None
    try:
        active_lessons = build_active_lesson_context(market)
        active_lesson_meta = dict(active_lessons.get("metadata") or {})
        lesson_context = str(active_lessons.get("section") or "")
        log.info(
            f"[active_lessons/judgment] {market} selected={active_lesson_meta.get('count', 0)} "
            f"injected={active_lesson_meta.get('injected', False)} "
            f"shadow={active_lesson_meta.get('shadow', True)} "
            f"chars={active_lesson_meta.get('chars', 0)}"
        )
    except Exception as e:
        active_lesson_meta = {"source": "fallback_param", "load_error": str(e)[:160]}
        log.warning(f"[active_lessons/judgment] load failed; fallback lesson_context used: {e}")

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
            lesson_context_meta=active_lesson_meta,
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
        r2[atype] = call_analyst_debate(
            atype,
            r1[atype],
            others,
            digest_prompt,
            debate_history,
            market=market,
            lesson_context=lesson_context,
            lesson_context_meta=active_lesson_meta,
        )
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
                   secondary_change_pct: Optional[float] = None,
                   execution_phase: str = "",
                   evidence_by_ticker: Optional[dict] = None) -> list:
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
            "allocation_intent": {},
            "max_order_cap_pct": {},
            "risk_budget_pct": {},
            "size_reason": {},
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
    execution_phase_norm = str(execution_phase or "").strip().lower()
    preopen_watch = (
        execution_phase_norm in {"preopen", "preopen_watch", "preopen_digest"}
        or str(consensus_mode or "").strip().upper() == "PREOPEN_WATCH"
    )

    limits = selection_limits(market)
    prompt_cap = _selection_candidate_cap(market, limits["watch_max"], limits["trade_max"])
    prompt_candidates, prompt_pool_meta = _build_selection_prompt_pool(candidates, market, prompt_cap)
    if len(candidates) > len(prompt_candidates):
        log.info(f"[ticker-selection] {market} prompt candidates trimmed: {len(candidates)} -> {len(prompt_candidates)}")
    if prompt_pool_meta.get("enabled"):
        log.info(
            f"[ticker-selection] {market} trainer_prompt_pool "
            f"full={prompt_pool_meta.get('full_pool_count')} "
            f"prompt={prompt_pool_meta.get('prompt_pool_count')} "
            f"excluded={len(prompt_pool_meta.get('excluded_from_prompt') or [])}"
        )

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
            _candidate_trainer_hint(candidate),
            _candidate_quality_hint(candidate),
            _candidate_earnings_hint(candidate),
            _candidate_preopen_pin_hint(candidate),
            _candidate_post_open_hint(candidate),
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
    evidence_map = evidence_by_ticker if isinstance(evidence_by_ticker, dict) else {}
    evidence_items: list[dict] = []
    if evidence_map:
        for candidate in prompt_candidates:
            ticker = str(candidate.get("ticker", "") or "").strip()
            if not ticker:
                continue
            keys = [ticker, ticker.upper()]
            evidence = next((evidence_map.get(k) for k in keys if isinstance(evidence_map.get(k), dict)), None)
            if not evidence:
                continue
            evidence_items.append(dict(evidence))
            if len(evidence_items) >= int(os.getenv("SELECTION_FULL_EVIDENCE_MAX", "8")):
                break
    evidence_section = ""
    evidence_omitted_count = 0
    if evidence_items:
        evidence_max_chars = _env_int_bound("SELECTION_EVIDENCE_MAX_CHARS", 3500, 128, 20000)
        evidence_json, capped_evidence_items, evidence_omitted_count = _json_array_object_cap(
            evidence_items,
            evidence_max_chars,
        )
        evidence_items = capped_evidence_items
    if evidence_items:
        evidence_section = (
            "\nRuntime evidence pack (use as facts; soft-gate override must match these values):\n"
            + evidence_json
            + "\n"
            + (
                f"Runtime evidence pack metadata: included={len(evidence_items)} omitted={evidence_omitted_count} "
                f"max_chars={evidence_max_chars}\n"
                if evidence_omitted_count else ""
            )
            + "\n"
        )
    n_cands = len([c for c in prompt_candidates if c.get("ticker")])
    watch_max = min(limits["watch_max"], n_cands)
    trade_max = min(limits["trade_max"], n_cands)
    if preopen_watch:
        trade_max = 0
    slot_plan = _selection_slot_plan(consensus_mode, market)
    slot_text = ", ".join(f"{name}:{count}" for name, count in slot_plan)
    phase_instruction = (
        f"PREOPEN WATCH ONLY for {market}: prepare a broad watch candidate list before regular-market open. "
        "This is not an executable buy decision. trade_ready must be []. "
        "Do not provide recommended_strategy, sizing, or price_targets."
        if preopen_watch
        else f"EXECUTABLE OPENING/INTRADAY SELECTION for {market}: split candidates into WATCH and TRADE_READY using current live-market context."
    )
    phase_rule_block = (
        "- PREOPEN WATCH ONLY: trade_ready must be an empty array.\n"
        "- PREOPEN WATCH ONLY: recommended_strategy, sizing fields, and price_targets must be empty objects.\n"
        "- PREOPEN WATCH ONLY: use reasons/veto to describe what must be confirmed after the regular open.\n"
        "- PREOPEN WATCH ONLY overrides any generic trade_ready or price_targets rule below."
        if preopen_watch
        else "- Opening/intraday phase: trade_ready may be non-empty only when live execution context supports a new buy."
    )

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
    active_lessons = build_active_lesson_context(market)
    active_lesson_meta = dict(active_lessons.get("metadata") or {})
    log.info(
        f"[active_lessons] {market} selected={active_lesson_meta.get('count', 0)} "
        f"injected={active_lesson_meta.get('injected', False)} "
        f"shadow={active_lesson_meta.get('shadow', True)} "
        f"chars={active_lesson_meta.get('chars', 0)}"
    )
    active_lesson_section = str(active_lessons.get("section") or "")
    fallback_lesson_text, fallback_lesson_meta = _lesson_context_for_prompt(lesson_context, scope="selection")
    lesson_section = (
        f"\n{active_lesson_section}\n"
        if active_lesson_section
        else (f"\n{fallback_lesson_text}\n" if fallback_lesson_text else "")
    )
    if fallback_lesson_text and not active_lesson_section:
        active_lesson_meta.setdefault("fallback", fallback_lesson_meta)
    tuning_feedback_section, tuning_feedback_meta = _build_tuning_feedback_contract(
        market,
        evidence_items,
        active_lesson_meta,
    )
    candidate_action_shadow_enabled = (
        not preopen_watch
        and str(os.getenv("ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW", "false")).lower() in {"1", "true", "yes", "on"}
    )
    candidate_action_live_enabled = (
        not preopen_watch
        and str(os.getenv("ENABLE_CLAUDE_CANDIDATE_ACTIONS", "false")).lower() in {"1", "true", "yes", "on"}
    )
    compact_selection_enabled = (
        not preopen_watch
        and compact_schema_enabled(False)
    )
    selection_reference_prices = reference_prices_from_candidates(prompt_candidates, market)
    candidate_action_section = candidate_action_prompt_contract(
        enabled=(candidate_action_shadow_enabled or candidate_action_live_enabled) and not compact_selection_enabled,
    )
    candidate_actions_example = (
        ',\n  "candidate_actions":[{"ticker":"code1","schema_version":"candidate_actions.v2",'
        '"action":"PROBE_READY","confidence":0.64,"freshness_verdict":"FRESH",'
        '"setup_maturity":"CONFIRMED","why_not_watch":"fresh confirmation exists",'
        '"action_ceiling_ack":"PROBE_READY","reason_code":"FRESH_CONTINUATION_PROBE",'
        '"soft_gate_overrides":[],"invalidation_condition":"breaks opening range low",'
        '"price_targets":{"buy_zone_low":73000,"buy_zone_high":73500,"sell_target":76000,'
        '"stop_loss":71000},"valid_until":"2026-05-06T09:10:00"}]'
        if candidate_action_section else ""
    )

    prompt = f"""{phase_instruction}
execution_phase: {execution_phase or 'unspecified'}
오늘 {market} 세션에서 후보를 WATCH와 TRADE_READY로 분리하세요.
합의 모드: {consensus_mode}
후보 종목:
{cand_text}
{evidence_section}

시장 컨텍스트:
{digest_prompt[:220]}{intraday_section}{brain_section}{tuner_section}{lesson_section}{selection_feedback[:700]}{tuning_feedback_section}
{COMMON_DECISION_CONTRACT}
{SELECTION_EXECUTION_PHASE_CONTRACT}
{SIZING_DECISION_CONTRACT}
{PRICE_PLAN_CONTRACT}
{HARD_SOFT_RULE_CONTRACT}
{candidate_action_section}
규칙:
{phase_rule_block}
- 후보 종목 중에서만 고르세요.
- watchlist는 선별 목록입니다. 최대 {watch_max}개, 보통 8~18개 수준으로 제한하세요.
- trade_ready는 실제 매수 권한 후보입니다. 최대 {trade_max}개이며 0개도 허용됩니다.
- trade_ready는 전략 슬롯을 나눠서 고르세요. slot guide: {slot_text}
- 저유동성, 구조화 상품, 과열, 손절폭 과대 후보는 trade_ready에서 제외하세요.
- preopen_pin=HARD 후보는 장전 우수 후보라 평가 기회를 보장한 것이며 자동 매수 후보가 아닙니다.
- preopen_pin=HARD confirm=required_before_trade_ready 후보는 anchor 대비 현재가 안정, OR/전략 신호, 개장 후 품질 확인 전에는 trade_ready로 올리지 마세요.
- Use intraday context session_phase/active_strategies/runtime gates to judge execution feasibility, not just strength.
- Treat exec= hints (or/atr/ep/fit/tclose/blackout) as real execution constraints. Strong names with poor exec hints should stay watch_only.
- Use recent selection feedback to calibrate trade_ready aggressiveness.
- Recent selection feedback is historical only. Do not promote a ticker to trade_ready solely because it moved after watch_only earlier in the same session.
- Tuning feedback is a calibration contract only. It may tighten soft gates or add similar-failure cautions, but it cannot create BUY_READY without current live evidence.
- recent selection feedback을 반영해 missed watch_only가 높은 그룹은 명확한 veto 없이 watch_only로만 두지 마세요.
- weak trade_ready가 높은 그룹은 더 강한 RS, 유동성, 장중 품질이 있을 때만 trade_ready로 올리세요.
- reasons와 veto는 짧게 쓰세요.
- recommended_strategy and max_position_pct must reflect conviction and risk, not generic defaults.
- max_order_cap_pct, allocation_intent, and risk_budget_pct must reflect conviction and risk, not generic defaults.
- max_position_pct is a legacy alias for max_order_cap_pct. It caps the system order budget and is not final portfolio weight or final quantity.
- price_targets is required for every trade_ready ticker in the primary response.
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
  "allocation_intent":{{"code1":"probe|small|normal|aggressive"}},
  "max_order_cap_pct":{{"code1":20}},
  "risk_budget_pct":{{"code1":0.35}},
  "size_reason":{{"code1":"high RS but ATR elevated"}},
  "price_targets":{{
    "code1":{{
      "reference_price":73200,
      "buy_zone_low":73000,
      "buy_zone_high":73500,
      "sell_target":76000,
      "stop_loss":71000,
      "reward_risk":1.5,
      "risk_pct":2.7,
      "reward_pct":3.4,
      "hold_days":1,
      "confidence":0.65,
      "cancel_if_open_above":74500,
      "target_basis":"VWAP reclaim + resistance",
      "invalid_if":"breaks opening range low",
      "entry_rationale":"support pullback",
      "exit_rationale":"near resistance",
      "rationale":"buy near support, sell into resistance"
    }}
  }}{candidate_actions_example}
}}"""

    if compact_selection_enabled:
        compact_watch_max = min(
            watch_max,
            _env_int_bound("CLAUDE_SELECTION_COMPACT_WATCH_MAX", min(15, watch_max), 1, max(1, watch_max)),
        )
        compact_trade_max = min(
            trade_max,
            _env_int_bound("CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX", min(5, trade_max), 0, max(0, trade_max)),
        )
        prompt = f"""{phase_instruction}
execution_phase: {execution_phase or 'unspecified'}
market: {market}
mode: {consensus_mode}

Candidates:
{cand_text}
{evidence_section}

Market context:
{digest_prompt[:220]}{intraday_section}{brain_section}{tuner_section}{lesson_section}{selection_feedback[:700]}{tuning_feedback_section}
{COMMON_DECISION_CONTRACT}
{HARD_SOFT_RULE_CONTRACT}
{compact_output_contract(watch_max=compact_watch_max, trade_max=compact_trade_max)}

Rules:
{phase_rule_block}
- Choose only from supplied candidates.
- Use live execution context and exec= hints as constraints.
- Strong names with poor execution hints should remain WATCH.
- Use recent selection feedback to calibrate trade_ready aggressiveness.
- Recent feedback and tuning feedback are calibration only, not permission to chase.
- ca[].s must be a concrete setup strategy such as momentum, gap_pullback, mean_reversion, opening_range_pullback, volatility_breakout, or continuation.
- ca[].rc, ca[].blk, and ca[].inv must be short machine codes.
- Do not output human explanations.
"""

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

    def _attach_prompt_pool_meta(meta: dict) -> dict:
        enriched = dict(meta or {})
        enriched["_candidate_quality_trainer_enabled"] = bool(prompt_pool_meta.get("enabled"))
        enriched["_candidate_quality_trainer_version"] = str(prompt_pool_meta.get("score_version") or "")
        enriched["_prompt_pool_version"] = str(prompt_pool_meta.get("version") or "")
        enriched["_full_pool_count"] = int(prompt_pool_meta.get("full_pool_count") or len(candidates or []))
        enriched["_scored_pool_count"] = int(prompt_pool_meta.get("scored_pool_count") or enriched["_full_pool_count"])
        enriched["_prompt_pool_count"] = int(prompt_pool_meta.get("prompt_pool_count") or len(prompt_candidates))
        enriched["_prompt_pool_target"] = prompt_pool_meta.get("target")
        enriched["_prompt_pool_hard_cap"] = prompt_pool_meta.get("hard_cap")
        enriched["_prompt_pool_metrics"] = dict(prompt_pool_meta.get("metrics") or {})
        enriched["_final_prompt_pool"] = list(prompt_pool_meta.get("prompt_pool") or prompt_candidates or [])
        enriched["_excluded_from_prompt"] = list(prompt_pool_meta.get("excluded_from_prompt") or [])
        enriched["_safe_empty_prompt_pool"] = bool(prompt_pool_meta.get("safe_empty_prompt_pool"))
        enriched["_prompt_pool_empty_reason"] = str(prompt_pool_meta.get("prompt_pool_empty_reason") or "")
        enriched["_trainer_all_quarantined"] = bool(prompt_pool_meta.get("trainer_all_quarantined"))
        return enriched

    fallback_meta = _attach_prompt_pool_meta(fallback_meta)
    fallback = fallback_meta["watchlist"]

    US_INVERSE_ETFS = {"TZA", "SPDN", "NVD", "SQQQ", "SDOW", "SPXU", "SH", "PSQ", "MYY"}
    US_STABLE_ANCHORS = ["T", "VZ", "XLU", "KO", "JNJ", "PG", "O", "VYM", "SCHD"]

    import time as _time
    last_err = None
    resp = None
    selection_max_tokens = 0
    for _attempt in range(3):
        try:
            throttle = throttle_state(label="select_tickers")
            if not bool(throttle.get("allowed", True)):
                raise RuntimeError(f"claude_budget_throttle:{throttle.get('tier')}")
            compressed_output = _env_bool_flag("SELECTION_OUTPUT_COMPRESSION_ENABLED", False)
            selection_max_tokens = (
                _env_int_bound("CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS", 2200, 700, 4000)
                if compressed_output or throttle.get("tier") == "warn"
                else _env_int_bound("CLAUDE_SELECTION_MAX_TOKENS", 3200, 1024, 6000)
            )
            resp = client.messages.create(
                model=MODEL,
                max_tokens=selection_max_tokens,
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
        stop_reason = str(getattr(resp, "stop_reason", "") or "")
        parse_error = False
        parse_stage = "strict_compact" if compact_selection_enabled else "legacy"
        if compact_selection_enabled and stop_reason == "max_tokens":
            result = {
                "wl": _safe_watch_fallback(prompt_candidates, market),
                "tr": [],
                "ca": [],
                "_fallback_mode": "selection_truncated",
            }
            parse_error = True
            parse_stage = "compact_truncated"
        else:
            try:
                result = _extract_json_strict(raw) if compact_selection_enabled else _extract_json(raw)
            except Exception as parse_exc:
                if compact_selection_enabled:
                    log.warning(f"[ticker-selection] {market} compact parse failed: {parse_exc}")
                    recovered_compact = _recover_compact_watch_selection(raw)
                    if recovered_compact:
                        result = recovered_compact
                        parse_stage = "compact_watch_recovered"
                    else:
                        result = {
                            "wl": _safe_watch_fallback(prompt_candidates, market),
                            "tr": [],
                            "ca": [],
                            "_fallback_mode": "selection_parse_failed",
                        }
                        parse_stage = "compact_parse_failed"
                    parse_error = True
                else:
                    raise
        selection_meta = normalize_selection_result(
            result,
            prompt_candidates,
            market,
            stop_reason=stop_reason,
            reference_prices=selection_reference_prices,
            source_prompt_id="selection_rank_v3+compact_v1" if compact_selection_enabled else "selection_rank_v3+execution_plan_v1",
        )
        selection_meta = _attach_prompt_pool_meta(selection_meta)
        if evidence_items:
            selection_meta["evidence_version"] = "selection_evidence.v1"
            selection_meta["evidence_tickers"] = [str(item.get("ticker") or "") for item in evidence_items]
            selection_meta["evidence_omitted_count"] = int(evidence_omitted_count)
        if tuning_feedback_meta:
            selection_meta["tuning_feedback"] = dict(tuning_feedback_meta)
            selection_meta["tuning_feedback_applied"] = True
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
            parse_error=parse_error,
            parse_stage=parse_stage,
            prompt_version="selection_rank_v3+compact_v1" if compact_selection_enabled else "selection_rank_v3+execution_plan_v1",
            extra={
                "active_lessons": active_lesson_meta,
                "evidence_version": "selection_evidence.v1" if evidence_items else "",
                "evidence_tickers": [str(item.get("ticker") or "") for item in evidence_items],
                "evidence_omitted_count": int(evidence_omitted_count),
                "tuning_feedback_rule_version": tuning_feedback_meta.get("rule_version", ""),
                "tuning_feedback_applied": bool(tuning_feedback_meta),
                "stop_reason": stop_reason,
                "max_tokens": selection_max_tokens,
                "compact_schema_enabled": bool(compact_selection_enabled),
                "selection_reference_prices": selection_reference_prices,
                "prompt_contract": "selection_compact.v1" if compact_selection_enabled else "selection_rank_v3+execution_plan_v1",
            },
        )
        if result.get("_fallback_mode") == "selection_partial" and not compact_selection_enabled:
            retry_candidates = _pick_selection_retry_candidates(prompt_candidates, result, market)
            if retry_candidates:
                retry_active_lessons = build_active_lesson_context(market, retry=True)
                retry_active_lesson_meta = dict(retry_active_lessons.get("metadata") or {})
                retry_prompt = _build_selection_retry_prompt(
                    market,
                    consensus_mode,
                    retry_candidates,
                    market_change_pct=market_change_pct,
                    secondary_change_pct=secondary_change_pct,
                    active_lessons_context=str(retry_active_lessons.get("section") or ""),
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
                    retry_trade_ready = list(retry_result.get("trade_ready") or []) if isinstance(retry_result.get("trade_ready"), list) else []
                    if retry_trade_ready:
                        retry_watch = list(retry_result.get("watchlist") or []) if isinstance(retry_result.get("watchlist"), list) else []
                        retry_result["watchlist"] = list(dict.fromkeys(retry_watch + retry_trade_ready))
                    retry_result["trade_ready"] = []
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
                        prompt_version="selection_retry_v2+execution_plan_v1",
                        extra={"active_lessons": retry_active_lesson_meta},
                    )
                    if not retry_result.get("_parse_recovered") and retry_meta.get("watchlist"):
                        result = retry_result
                        selection_meta = retry_meta
                        selection_meta["_selection_retry_trade_ready_ignored"] = retry_trade_ready
                        selection_meta["_trade_ready_without_price_targets_source"] = "selection_retry_disabled"
                        selection_meta["active_lessons"] = {
                            "primary": active_lesson_meta,
                            "retry": retry_active_lesson_meta,
                        }
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
        selection_meta.setdefault("active_lessons", active_lesson_meta)
        selection_meta = _attach_prompt_pool_meta(selection_meta)
        if evidence_items:
            selection_meta.setdefault("evidence_version", "selection_evidence.v1")
            selection_meta.setdefault("evidence_tickers", [str(item.get("ticker") or "") for item in evidence_items])
            selection_meta.setdefault("evidence_omitted_count", int(evidence_omitted_count))
        if tuning_feedback_meta:
            selection_meta.setdefault("tuning_feedback", dict(tuning_feedback_meta))
            selection_meta.setdefault("tuning_feedback_applied", True)
        if preopen_watch:
            selection_meta = _force_watch_only_selection_meta(selection_meta, phase="preopen_watch")
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
