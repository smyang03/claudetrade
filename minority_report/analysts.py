from __future__ import annotations

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
from minority_report.claude_utils import is_claude_retryable_error, claude_response_meta
from minority_report.active_lessons import build_active_lesson_context
from minority_report.consensus import is_available_judgment
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
from runtime import selection_smart_skip

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


def _compact_selection_candidate_lines(
    lines: list[str],
    *,
    max_line_chars: int,
    max_total_chars: int,
) -> tuple[str, dict]:
    kept: list[str] = []
    omitted = 0
    total = 0
    line_limit = max(80, int(max_line_chars or 0))
    total_limit = max(line_limit, int(max_total_chars or 0))
    for raw_line in lines or []:
        line = str(raw_line or "").strip()
        if not line:
            continue
        if len(line) > line_limit:
            line = line[: line_limit - 3].rstrip() + "..."
        next_total = total + len(line) + (1 if kept else 0)
        if next_total > total_limit:
            omitted += 1
            continue
        kept.append(line)
        total = next_total
    return "\n".join(kept), {
        "candidate_line_count": len(lines or []),
        "candidate_line_included_count": len(kept),
        "candidate_line_omitted_count": omitted,
        "candidate_line_max_chars": line_limit,
        "candidate_total_max_chars": total_limit,
        "candidate_prompt_chars": total,
    }


def _compact_evidence_num(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except Exception:
        return None
    if parsed != parsed:
        return None
    return round(parsed, 4)


def _compact_selection_evidence_item(item: dict) -> dict:
    source = dict(item or {})
    live = source.get("live_evidence") if isinstance(source.get("live_evidence"), dict) else {}
    post_open = live.get("post_open_confirmation") if isinstance(live.get("post_open_confirmation"), dict) else {}
    risk = live.get("risk_control_view") if isinstance(live.get("risk_control_view"), dict) else {}
    timing = live.get("execution_timing") if isinstance(live.get("execution_timing"), dict) else {}
    out: dict = {
        "t": str(source.get("ticker") or live.get("ticker") or ""),
        "ev": str(source.get("evidence_class") or source.get("selection_evidence_class") or ""),
        "ceil": str(
            source.get("selection_evidence_action_ceiling")
            or live.get("action_ceiling")
            or source.get("action_ceiling")
            or risk.get("action_ceiling")
            or ""
        ),
        "ds": str(live.get("data_state") or source.get("data_state") or ""),
    }
    missing = live.get("missing_fields") or source.get("missing_fields") or []
    if missing:
        out["miss"] = [str(value) for value in list(missing)[:5] if str(value)]
    for src, dst in (
        ("ret_3m_pct", "r3"),
        ("ret_5m_pct", "r5"),
        ("ret_10m_pct", "r10"),
        ("ret_30m_pct", "r30"),
        ("vwap_distance_pct", "vwap"),
        ("volume_ratio_open", "vol"),
        ("pullback_from_high_pct", "fh"),
        ("spread_bps", "spr"),
    ):
        value = _compact_evidence_num(post_open.get(src))
        if value is not None:
            out[dst] = value
    if post_open.get("opening_range_break") not in (None, ""):
        out["or"] = bool(post_open.get("opening_range_break"))
    if post_open.get("momentum_state"):
        out["state"] = str(post_open.get("momentum_state"))
    hard = risk.get("hard_blocks") if isinstance(risk.get("hard_blocks"), list) else []
    soft = risk.get("soft_gates") if isinstance(risk.get("soft_gates"), list) else []
    if hard:
        out["hard"] = [str(value) for value in hard[:5] if str(value)]
    if soft:
        out["soft"] = [str(value) for value in soft[:5] if str(value)]
    age = _compact_evidence_num(timing.get("candidate_age_min"))
    if age is not None:
        out["age"] = age
    return {key: value for key, value in out.items() if value not in ("", [], {}, None)}


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
    feasibility = candidate.get("strategy_feasibility")
    if isinstance(feasibility, dict) and feasibility:
        feas_strategy = fit_strategy if fit_strategy in feasibility else ""
        if not feas_strategy and len(feasibility) == 1:
            feas_strategy = next(iter(feasibility.keys()))
        if feas_strategy and isinstance(feasibility.get(feas_strategy), dict):
            detail = feasibility.get(feas_strategy) or {}
            ceiling = str(detail.get("action_ceiling") or detail.get("ceiling") or "WATCH").strip().upper()
            reason = str(detail.get("reason") or detail.get("state") or "").strip()
            if ceiling or reason:
                parts.append(f"feas={feas_strategy}:{ceiling}:{reason}")
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


def _compact_news_prompt_text(value, max_chars: int = 96) -> str:
    text = " ".join(str(value or "").replace("|", " ").split())
    return text[: max(1, int(max_chars))].strip()


def _candidate_news_hint(candidate: dict) -> str:
    try:
        count = int(float(candidate.get("news_or_earnings_count") or 0))
    except Exception:
        count = 0
    raw_sources = candidate.get("news_or_earnings_sources")
    if isinstance(raw_sources, (list, tuple, set)):
        sources = [_compact_news_prompt_text(src, 28) for src in raw_sources]
    elif raw_sources:
        sources = [_compact_news_prompt_text(raw_sources, 64)]
    else:
        sources = []
    sources = [src for src in sources if src][:3]
    title = _compact_news_prompt_text(
        candidate.get("news_or_earnings_sample_title") or candidate.get("news_sample_title"),
        96,
    )
    prompt_summary = _compact_news_prompt_text(candidate.get("news_prompt_summary"), 140)
    risk_summary = _compact_news_prompt_text(candidate.get("risk_news_summary"), 120)
    signal_type = _compact_news_prompt_text(candidate.get("news_signal_type"), 32)
    flagged = (
        bool(candidate.get("news_or_earnings_flag"))
        or count > 0
        or bool(sources)
        or bool(title)
        or bool(prompt_summary)
        or bool(risk_summary)
    )
    if not flagged:
        return ""
    parts = []
    if count > 0:
        parts.append(f"count={count}")
    elif bool(candidate.get("news_or_earnings_flag")):
        parts.append("flag=true")
    if sources:
        parts.append("src=" + ",".join(sources))
    quality = _compact_news_prompt_text(candidate.get("news_quality"), 24)
    if quality:
        parts.append("quality=" + quality)
    date_quality = _compact_news_prompt_text(candidate.get("news_date_quality"), 24)
    if date_quality and date_quality != "dated":
        parts.append("date=" + date_quality)
    if signal_type:
        parts.append("signal=" + signal_type)
    try:
        news_score = int(float(candidate.get("news_score") or 0))
    except Exception:
        news_score = 0
    if news_score > 0:
        parts.append(f"score={news_score}")
    if bool(candidate.get("news_prompt_eligible")):
        parts.append("eligible=true")
    if prompt_summary:
        parts.append("summary=" + prompt_summary)
    elif title:
        parts.append("title=" + title)
    if risk_summary:
        parts.append("risk=" + risk_summary)
    return "news=" + "|".join(parts) if parts else "news=flag"


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
    flow_quality = str(candidate.get("flow_data_quality") or candidate.get("investor_flow_quality") or "").strip()
    flow_flags = candidate.get("flow_quality_flags")
    flow_flag_set = {str(flag).strip() for flag in flow_flags if str(flag).strip()} if isinstance(flow_flags, (list, tuple, set)) else set()
    if flow_quality == "bad_zero_flow_cluster" or "kr_investor_flow_all_zero_cluster" in flow_flag_set:
        parts.append("flow=unavailable:all_zero_cluster")
    else:
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


def _candidate_discovery_hint(candidate: dict) -> str:
    role = str(candidate.get("candidate_pool_role") or "").strip().upper()
    if role != "DISCOVERY":
        return ""
    parts = ["role=DISCOVERY"]
    ceiling = str(candidate.get("discovery_action_ceiling") or "WATCH").strip().upper()
    if ceiling:
        parts.append(f"ceiling={ceiling}")
    signal = str(candidate.get("discovery_signal_family") or "").strip()
    if signal:
        parts.append(f"signal={signal}")
    reason = str(candidate.get("discovery_reason") or "").strip()
    if reason:
        parts.append(f"reason={reason}")
    return " ".join(parts)


def _candidate_evidence_hint(candidate: dict) -> str:
    evidence_class = str(candidate.get("evidence_class") or "").strip().upper()
    ceiling = str(
        candidate.get("selection_evidence_action_ceiling")
        or candidate.get("evidence_action_ceiling")
        or ""
    ).strip().upper()
    if not evidence_class and not ceiling:
        return ""
    parts = []
    if evidence_class:
        parts.append(f"ev={evidence_class}")
    if ceiling:
        parts.append(f"ceil={ceiling}")
    state = str(candidate.get("selection_evidence_data_state") or "").strip().lower()
    if state:
        parts.append(f"eds={state}")
    reason = str(candidate.get("selection_evidence_missing_reason") or "").strip()
    if reason and evidence_class in {"COMPACT_ONLY", "MISSING_OR_STALE", "PREFETCHED_PARTIAL"}:
        parts.append(f"emiss={reason[:32]}")
    return ",".join(parts)


def _candidate_identity_prefix(candidate: dict) -> str:
    ticker = str(candidate.get("ticker", "") or "").strip()
    name = str(
        candidate.get("name")
        or candidate.get("company_name")
        or candidate.get("ticker_name")
        or candidate.get("display_name")
        or ""
    ).strip()
    if not name:
        return ticker
    name = _compact_news_prompt_text(name.replace("=", " "), 80)
    return f"{ticker} name={name}" if ticker else f"name={name}"


def _candidate_compact_news_hint(candidate: dict) -> str:
    parts = []
    try:
        count = int(float(candidate.get("news_or_earnings_count") or 0))
    except Exception:
        count = 0
    if count > 0:
        parts.append(f"n={count}")
    quality = _compact_news_prompt_text(candidate.get("news_quality"), 24)
    if quality:
        parts.append("q=" + quality)
    date_quality = _compact_news_prompt_text(candidate.get("news_date_quality"), 24)
    if date_quality and date_quality != "dated":
        parts.append("date=" + date_quality)
    signal_type = _compact_news_prompt_text(candidate.get("news_signal_type"), 32)
    if signal_type:
        parts.append("sig=" + signal_type)
    try:
        news_score = int(float(candidate.get("news_score") or 0))
    except Exception:
        news_score = 0
    if news_score > 0:
        parts.append(f"score={news_score}")
    risk_summary = _compact_news_prompt_text(candidate.get("risk_news_summary"), 64)
    if risk_summary:
        parts.append("risk=" + risk_summary)
    return "newsq=" + ",".join(parts) if parts else ""


def _candidate_relative_strength_text(
    candidate: dict,
    market: str,
    rate: float,
    market_change_pct: Optional[float] = None,
    secondary_change_pct: Optional[float] = None,
) -> str:
    if market == "KR":
        market_type = candidate.get("market_type", "KOSPI")
        base_pct = (
            secondary_change_pct
            if market_type == "KOSDAQ" and secondary_change_pct is not None
            else market_change_pct
        )
        rs = rate - base_pct if base_pct is not None else None
        return f"rs={rs:+.1f}%({'KQ' if market_type == 'KOSDAQ' else 'KP'})" if rs is not None else ""

    rs_parts = []
    if market_change_pct is not None:
        rs_parts.append(f"SP{rate - market_change_pct:+.1f}%")
    if secondary_change_pct is not None:
        rs_parts.append(f"NQ{rate - secondary_change_pct:+.1f}%")
    return f"rs=({'/'.join(rs_parts)})" if rs_parts else ""


def _candidate_turnover_text(market: str, turnover: float) -> str:
    if turnover <= 0:
        return ""
    if market == "US":
        return f"turn=${turnover/1e6:.1f}M"
    return f"turn={turnover/1e8:.1f}e8KRW"


def _earnings_line_token(candidate: dict, market: str) -> str:
    if str(market or "").upper() != "US":
        return ""
    try:
        from runtime.earnings_calendar import earnings_tag
        return earnings_tag(str(candidate.get("ticker") or ""), "US")
    except Exception:
        return ""


def _format_selection_candidate_line(
    candidate: dict,
    market: str,
    ranked_turnovers: list[float],
    *,
    market_change_pct: Optional[float] = None,
    secondary_change_pct: Optional[float] = None,
    kr_premarket: bool = False,
    compact: bool = False,
) -> str:
    rate = _safe_float(candidate.get("change_rate", 0.0), 0.0)
    vr = _safe_float(candidate.get("vol_ratio", 0.0), 0.0)
    # US: vol_ratio는 1.0 placeholder라 무의미 — 실측 rel_vol(자기 20일 평균 대비, 세션 보정)로 대체 표기
    rel_vol = _safe_float(candidate.get("rel_vol_shadow", 0.0), 0.0)
    price = _safe_float(candidate.get("price", 0), 0.0)
    volume = _safe_float(candidate.get("volume", 0), 0.0)
    turnover = price * volume
    market_type = str(candidate.get("market_type", "") or "").strip()
    category = str(candidate.get("category", "") or "").strip()
    sector = str(candidate.get("sector", "") or "").strip()
    above_ma60 = candidate.get("above_ma60")
    from_high_pct = candidate.get("from_high_pct")
    liquidity_bucket = (
        str(candidate.get("liquidity_bucket", "") or "").strip()
        or _candidate_liquidity_bucket(turnover, ranked_turnovers)
    )
    from_high_bucket = (
        str(candidate.get("from_high_bucket", "") or "").strip()
        or _candidate_pullback_bucket(from_high_pct)
    )
    news_hint = _candidate_compact_news_hint(candidate) if compact else _candidate_news_hint(candidate)
    if market == "US":
        vol_token = f"rvol={rel_vol:.1f}x" if rel_vol > 0 else ""
    else:
        vol_token = f"vol={vr:.1f}x" if vr > 0 else ""
    # 좀비 후보 표기 — 6세션+ 무성과 체류 (역산: 해당 그룹 플랜 -0.71%, 승률 36%)
    freshness_token = ""
    if str(candidate.get("freshness_grade") or "") == "OLD":
        freshness_token = f"stale={candidate.get('freshness_age_sessions')}s"
    # 실적 임박 표기 (PEAD 정책: earnings_date는 즉시 노출 허용)
    earnings_token = ""
    if market == "US":
        try:
            from runtime.earnings_calendar import earnings_tag
            earnings_token = earnings_tag(str(candidate.get("ticker") or ""), market)
        except Exception:
            earnings_token = ""
    parts = [
        _candidate_identity_prefix(candidate),
        f"chg={rate:+.2f}%",
        _candidate_relative_strength_text(
            candidate,
            market,
            rate,
            market_change_pct=market_change_pct,
            secondary_change_pct=secondary_change_pct,
        ),
        "" if compact else (f"p={price:,.2f}".rstrip("0").rstrip(".") if price > 0 else ""),
        "" if compact or (market == "KR" and kr_premarket) else vol_token,
        "" if compact else _candidate_turnover_text(market, turnover),
        "" if compact else (f"board={market_type}" if market_type else ""),
        "" if compact else (f"category={category}" if category else ""),
        "" if compact else (f"sector={sector}" if sector else ""),
        f"liq={liquidity_bucket}",
        freshness_token,
        earnings_token,
        news_hint,
        _candidate_discovery_hint(candidate),
        _candidate_trainer_hint(candidate),
        _candidate_quality_hint(candidate),
        _candidate_evidence_hint(candidate),
        _candidate_earnings_hint(candidate),
        "" if compact else _candidate_preopen_pin_hint(candidate),
        _candidate_post_open_hint(candidate),
        (
            f"from_high={_safe_float(from_high_pct, 0.0):+.1f}%({from_high_bucket})"
            if from_high_pct is not None else
            "from_high=unknown"
        ),
        "ma60=above" if above_ma60 is True else ("ma60=below" if above_ma60 is False else ""),
        _candidate_execution_hint(candidate),
    ]
    return " ".join([part for part in parts if part])


def _selection_reason_identity_warnings(selection_meta: dict, candidates: list[dict], market: str) -> list[dict]:
    if str(market or "").upper() != "KR":
        return []
    reasons = selection_meta.get("reasons") if isinstance(selection_meta, dict) else {}
    if not isinstance(reasons, dict):
        return []
    names = {}
    for candidate in candidates or []:
        ticker = _prompt_ticker_key(market, (candidate or {}).get("ticker"))
        name = str((candidate or {}).get("name") or "").strip()
        if ticker and name:
            names[ticker] = name
    warnings: list[dict] = []
    for ticker, reason in reasons.items():
        key = _prompt_ticker_key(market, ticker)
        own_name = names.get(key, "")
        text = str(reason or "").strip()
        if not key or not own_name or ":" not in text:
            continue
        prefix = text.split(":", 1)[0].strip()
        if not prefix or len(prefix) > 40 or not any(ord(ch) > 127 for ch in prefix):
            continue
        if prefix == own_name or prefix in own_name or own_name in prefix:
            continue
        warnings.append(
            {
                "ticker": key,
                "official_name": own_name,
                "reason_prefix": prefix,
                "type": "reason_name_mismatch",
            }
        )
    return warnings[:20]


def _selection_candidate_cap(market: str, watch_max: int, trade_max: int) -> int:
    if market == "US":
        hard_cap = int(os.getenv("US_SELECTION_PROMPT_CAP", "40"))
    else:
        hard_cap = int(os.getenv("KR_SELECTION_PROMPT_CAP", "40"))
    return max(trade_max, hard_cap)


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
    raw_candidates = list(candidates or [])
    if len(raw_candidates) <= prompt_cap:
        _annotate_candidate_prompt_features(raw_candidates)
        regular = [candidate for candidate in raw_candidates if not bool((candidate or {}).get("same_day_stopped"))]
        stopped = [candidate for candidate in raw_candidates if bool((candidate or {}).get("same_day_stopped"))]
        return regular + stopped

    caps = _selection_prompt_diversity_caps(market)
    _annotate_candidate_prompt_features(raw_candidates)
    chosen: list[dict] = []
    deferred: list[dict] = []
    hard_pin_candidates = [
        candidate
        for candidate in raw_candidates
        if str((candidate or {}).get("preopen_pin_tier", "") or "").strip().upper() == "HARD"
        or bool((candidate or {}).get("preopen_pinned"))
    ]
    hard_pin_seen: set[str] = set()
    for candidate in hard_pin_candidates:
        ticker = str((candidate or {}).get("ticker", "") or "").strip().upper()
        if not ticker or ticker in hard_pin_seen:
            continue
        hard_pin_seen.add(ticker)
        if bool((candidate or {}).get("same_day_stopped")):
            deferred.append(candidate)
            continue
        chosen.append(candidate)
        if len(chosen) >= prompt_cap:
            return chosen[:prompt_cap]
    category_counts: dict[str, int] = {}
    sector_counts: dict[str, int] = {}
    overextended_count = 0
    low_liquidity_count = 0
    kosdaq_count = 0

    for candidate in raw_candidates:
        ticker = str((candidate or {}).get("ticker", "") or "").strip().upper()
        if ticker and ticker in hard_pin_seen:
            continue
        # 당일 손절 종목은 다른 후보로 캡을 채운 뒤에만 편입 (최후순위)
        if bool(candidate.get("same_day_stopped")):
            deferred.append(candidate)
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
    policy_default = 40
    return _env_int_bound(
        f"CANDIDATE_PROMPT_POOL_HARD_CAP_{market_key}",
        _env_int_bound(f"{market_key}_PROMPT_POOL_CAP", policy_default, 1, 100),
        1,
        100,
    )


def _prompt_overlay_mode() -> str:
    mode = str(os.getenv("PROMPT_OVERLAY_MODE", "off") or "off").strip().lower()
    return mode if mode in {"off", "shadow", "live"} else "off"


def _prompt_overlay_keep_current() -> int:
    return _env_int_bound("PROMPT_OVERLAY_KEEP_CURRENT", 15, 0, 100)


def _prompt_overlay_plan_a_max() -> int:
    return _env_int_bound("PROMPT_OVERLAY_PLAN_A_MAX", 4, 0, 100)


def _prompt_ticker_key(market: str, ticker: object) -> str:
    value = str(ticker or "").strip()
    return value.upper() if str(market or "").upper() == "US" else value


def _prompt_tickers_from_rows(rows: list[dict], market: str) -> list[str]:
    tickers: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ticker = _prompt_ticker_key(market, row.get("ticker"))
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    return tickers


def _apply_plan_a_prompt_overlay(
    prompt_candidates: list[dict],
    prompt_pool_meta: dict,
    market: str,
) -> tuple[list[dict], dict]:
    mode = _prompt_overlay_mode()
    enriched_meta = dict(prompt_pool_meta or {})
    base_overlay_meta = {
        "_prompt_overlay_requested_mode": mode,
        "_prompt_overlay_mode": "current_only",
        "_prompt_overlay_candidate_state": "current_only",
        "_overlay_plan_a_available": 0,
        "_overlay_plan_a_added": 0,
        "_overlay_added_tickers": [],
        "_overlay_removed_tickers": [],
        "_overlay_keep_current": _prompt_overlay_keep_current(),
        "_overlay_plan_a_max": _prompt_overlay_plan_a_max(),
        "_overlay_plan_b_used": False,
    }
    enriched_meta.update(base_overlay_meta)
    if mode == "off":
        return prompt_candidates, enriched_meta

    scored_pool = [dict(row or {}) for row in list(enriched_meta.get("scored_pool") or []) if isinstance(row, dict)]
    if not scored_pool:
        return prompt_candidates, enriched_meta

    try:
        from runtime.candidate_prompt_pool import build_plan_a_overlay_prompt_pool

        overlay_pool, overlay_meta = build_plan_a_overlay_prompt_pool(
            [dict(row or {}) for row in prompt_candidates or []],
            scored_pool,
            market=market,
            cap=int(enriched_meta.get("hard_cap") or len(prompt_candidates or [])),
            keep_current=base_overlay_meta["_overlay_keep_current"],
            plan_a_max=base_overlay_meta["_overlay_plan_a_max"],
        )
    except Exception as exc:
        log.warning(f"[ticker-selection] prompt overlay failed {market}: {exc}")
        enriched_meta["_prompt_overlay_error"] = str(exc)
        return prompt_candidates, enriched_meta

    overlay_added = list(overlay_meta.get("overlay_added_tickers") or [])
    overlay_removed = list(overlay_meta.get("overlay_removed_tickers") or [])
    overlay_candidate_state = str(overlay_meta.get("overlay_candidate_state") or "current_only")
    effective_mode = mode if overlay_candidate_state == "overlay_candidate" and overlay_added else "current_only"
    enriched_meta.update(
        {
            "_prompt_overlay_mode": effective_mode,
            "_prompt_overlay_candidate_state": overlay_candidate_state,
            "_overlay_plan_a_available": int(overlay_meta.get("overlay_plan_a_available") or 0),
            "_overlay_plan_a_added": int(overlay_meta.get("overlay_plan_a_added") or 0),
            "_overlay_added_tickers": overlay_added,
            "_overlay_removed_tickers": overlay_removed,
            "_overlay_keep_current": int(overlay_meta.get("overlay_keep_current") or base_overlay_meta["_overlay_keep_current"]),
            "_overlay_plan_a_max": int(overlay_meta.get("overlay_plan_a_max") or base_overlay_meta["_overlay_plan_a_max"]),
            "_overlay_plan_b_used": bool(overlay_meta.get("overlay_plan_b_used")),
        }
    )
    if mode == "shadow":
        enriched_meta["_shadow_overlay_prompt_pool"] = [dict(row or {}) for row in overlay_pool]
        enriched_meta["_shadow_overlay_tickers"] = _prompt_tickers_from_rows(overlay_pool, market)
        enriched_meta["_shadow_overlay_added_tickers"] = overlay_added
        enriched_meta["_shadow_overlay_removed_tickers"] = overlay_removed
        enriched_meta["_shadow_overlay_plan_a_available"] = int(overlay_meta.get("overlay_plan_a_available") or 0)
        enriched_meta["_shadow_overlay_plan_a_added"] = int(overlay_meta.get("overlay_plan_a_added") or 0)
        return prompt_candidates, enriched_meta

    if mode == "live" and effective_mode == "live":
        live_pool = [dict(row or {}) for row in overlay_pool]
        enriched_meta["prompt_pool"] = live_pool
        enriched_meta["prompt_pool_count"] = len(live_pool)
        metrics = dict(enriched_meta.get("metrics") or {})
        metrics["prompt_overlay"] = dict(overlay_meta)
        enriched_meta["metrics"] = metrics
        return live_pool, enriched_meta

    return prompt_candidates, enriched_meta


def _apply_discovery_prompt_overlay(
    prompt_candidates: list[dict],
    prompt_pool_meta: dict,
    market: str,
) -> tuple[list[dict], dict]:
    if not _env_bool_flag("DISCOVERY_PROMPT_ENABLED", False):
        return prompt_candidates, dict(prompt_pool_meta or {})
    try:
        from runtime.candidate_discovery_overlay import apply_discovery_overlay

        discovery_pool, discovery_meta = apply_discovery_overlay(
            [dict(row or {}) for row in prompt_candidates or []],
            dict(prompt_pool_meta or {}),
            market=market,
        )
        return [dict(row or {}) for row in discovery_pool], dict(discovery_meta or {})
    except Exception as exc:
        enriched_meta = dict(prompt_pool_meta or {})
        enriched_meta["_discovery_error"] = str(exc)
        log.warning(f"[ticker-selection] discovery overlay failed {market}: {exc}")
        return prompt_candidates, enriched_meta


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
        from runtime.candidate_freshness import annotate_candidate_freshness

        # 신선도 패널티 — 좀비(6세션+ 체류)·체결후재탕 후보를 풀 정렬에서 강등 (2026-06-11)
        try:
            freshness_summary = annotate_candidate_freshness(list(candidates or []), market)
            if freshness_summary.get("penalized"):
                log.info(
                    f"[candidate freshness] {market} 강등 {freshness_summary['penalized']}건 "
                    f"(OLD={freshness_summary['old']} 재탕={freshness_summary['retrade']} 면제={freshness_summary['exempt']})"
                )
        except Exception as _fresh_exc:
            log.warning(f"[candidate freshness] {market} 주석 실패 — 무적용: {_fresh_exc}")

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


def prepare_selection_prompt_pool(market: str, candidates: list[dict]) -> tuple[list[dict], dict]:
    """Return the exact prompt pool and metadata select_tickers() will use."""
    limits = selection_limits(market)
    prompt_cap = _selection_candidate_cap(market, limits["watch_max"], limits["trade_max"])
    prompt_candidates, prompt_pool_meta = _build_selection_prompt_pool(
        [dict(row or {}) for row in list(candidates or []) if isinstance(row, dict)],
        market,
        prompt_cap,
    )
    prompt_candidates, prompt_pool_meta = _apply_plan_a_prompt_overlay(
        prompt_candidates,
        prompt_pool_meta,
        market,
    )
    prompt_candidates, prompt_pool_meta = _apply_discovery_prompt_overlay(
        prompt_candidates,
        prompt_pool_meta,
        market,
    )
    prompt_pool_meta = dict(prompt_pool_meta or {})
    prompt_pool_meta["prompt_pool"] = [dict(row or {}) for row in list(prompt_candidates or [])]
    prompt_pool_meta["prompt_pool_count"] = len(prompt_pool_meta["prompt_pool"])
    return [dict(row or {}) for row in list(prompt_candidates or [])], prompt_pool_meta


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
    retry_cap = min(
        _env_int_bound("SELECTION_RETRY_CANDIDATE_CAP", 40, 10, 80),
        len(candidates),
    )
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
    for candidate in candidates or []:
        key = str((candidate or {}).get("ticker", "") or "").strip()
        key = key.upper() if market == "US" else key
        if key and key in candidate_map and key not in ordered:
            ordered.append(key)
        if len(ordered) >= retry_cap:
            break
    return [candidate_map[key] for key in ordered]


def _build_selection_retry_prompt(
    market: str,
    consensus_mode: str,
    retry_candidates: list[dict],
    market_change_pct: Optional[float] = None,
    secondary_change_pct: Optional[float] = None,
    active_lessons_context: str = "",
) -> str:
    ranked_turnovers = _annotate_candidate_prompt_features(retry_candidates)
    lines = [
        _format_selection_candidate_line(
            candidate,
            market,
            ranked_turnovers,
            market_change_pct=market_change_pct,
            secondary_change_pct=secondary_change_pct,
            compact=True,
        )
        for candidate in retry_candidates
    ]
    lines = [line for line in lines if line]
    watch_max = min(selection_limits(market)["watch_max"], len(retry_candidates))
    watch_floor = min(watch_max, 10 if len(retry_candidates) >= 15 else max(1, len(retry_candidates) // 2))
    active_text, _active_meta = _lesson_context_for_prompt(active_lessons_context, scope="selection")
    active_section = f"\n{active_text}\n" if active_text else ""
    retry_price_plan_omit_marker = "DO NOT include price_targets in this response"
    price_plan_rule = (
        f"- {retry_price_plan_omit_marker}. Price plans will be requested separately."
        if len(retry_candidates) < 15
        else ""
    )
    return f"""Previous ticker-selection response was truncated. 다시 묻습니다. Rebuild WATCH/reasons only.
market: {market}
mode: {consensus_mode}
candidates:
{chr(10).join(lines)}
{active_section}
{COMMON_DECISION_CONTRACT}

Rules:
- Choose only from supplied candidates.
- Candidate identity is ticker + name=. Do not invent or substitute company names.
- If you mention a company name in reasons, it must exactly match the supplied name= for that ticker.
- Keep a broad watchlist: if candidates >= 15 and mode is not DEFENSIVE/HALT, return at least {watch_floor} watchlist names.
- watchlist max {watch_max}.
- trade_ready must be [].
{price_plan_rule}
- Do not include execution plans, sizing, allocation, budgets, or strategy recommendations.
- reasons must be short.
- Return JSON only.

{{
  "watchlist":["code1","code2"],
  "trade_ready":[],
  "reasons":{{"code1":"short reason"}}
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
    sanitized: dict = {
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
    reversal_trigger = str(result.get("reversal_trigger", "") or "").strip()
    if reversal_trigger:
        sanitized["reversal_trigger"] = reversal_trigger[:200]
    return sanitized


def _fallback_result(error: Exception) -> dict:
    error_class = type(error).__name__ if error is not None else "Exception"
    return {
        "stance": "UNAVAILABLE",
        "available": False,
        "analyst_unavailable": True,
        "status": "unavailable",
        "failure_stage": "r1",
        "error_class": error_class[:80],
        "failure_reason": error_class[:80],
        "confidence": 0.0,
        "key_reason": f"analyst_unavailable:{error_class[:80]}",
        "full_reasoning": "",
        "top_risks": [],
        "market_regime": "unknown",
        "data_quality": "unavailable",
        "new_buy_permission": "block",
        "max_gross_exposure_pct": 0,
        "key_confirmations": [],
        "key_contradictions": [],
        "suggested_strategy": "unavailable",
        "suggested_size_pct": None,
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
        return {"suggested_size_pct": 10.0, "new_buy_permission": "selective", "max_gross_exposure_pct": 15}
    return {"suggested_size_pct": 0.0, "new_buy_permission": "block", "max_gross_exposure_pct": 0}


def _merge_debate_result(my_r1: dict, result: dict) -> dict:
    if not is_available_judgment(my_r1):
        return dict(my_r1 or {})
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

[역할]
• 시장 방향을 독립적으로 판단합니다 (항상 비관적일 필요 없음)
• 어떤 stance를 내든 반드시 "이 조건이 깨지면 반전" 시나리오를 reversal_trigger로 명시해야 합니다

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
• 지표 간 상충 여부 (기술적 긍정 + 매크로 부정 → 불확실)
• 데이터 신뢰도 검증 (데이터 누락 = confidence 페널티이지 NEUTRAL 근거가 아님)

[판단 기준 — 순서대로 적용]
1. breadth 상승 비율 45~55% 구간일 때만 NEUTRAL 허용
2. 긍정 신호가 부정보다 2배 이상(예: GC 우세, 섹터 상승 多, breadth 60%+) → MILD_BULL
3. 부정 신호가 긍정보다 2배 이상 → MILD_BEAR
4. 데이터 결측 30%+ → confidence를 0.10 하향 후 그래도 방향이 있으면 방향 제시
5. 극단 판단(AGGRESSIVE, HALT) 원칙적 금지

[NEUTRAL 조건 — 반드시 이 경우에만]
• breadth 상승 비율이 실제로 45~55% 구간인 경우
• key_reason에 반드시 "긍정 신호 X개 vs 부정 신호 Y개" 수치 포함

[절대 하지 말 것]
• 데이터 결측을 NEUTRAL 근거로 사용 금지 (결측은 confidence 하향만)
• 한쪽 분석가 의견에 무조건 동조 금지
• 방향이 있는데 불확실하다는 이유만으로 NEUTRAL 선택 금지""",
}

US_BEAR_PERSONA = """당신은 미국 주식 헤지펀드 리스크 매니저입니다.

[역할]
• 시장 방향을 독립적으로 판단합니다 (항상 비관적일 필요 없음)
• 어떤 stance를 내든 반드시 "이 조건이 깨지면 반전" 시나리오를 reversal_trigger로 명시해야 합니다

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
• key_reason에는 개별 종목을 최대 3개까지만 예시로 언급하세요.
• breadth 60%+ 인데 장중 실시간 지수가 하락 중일 때: breadth를 방향 판단의 기준으로 삼고, 장중 하락은 confidence 조정에만 반영하세요. 장 초반·중반의 일시 하락을 종가 방향으로 단정하지 마세요."""


def _market_interpretation_guide(market: str) -> str:
    market_key = str(market or "").upper()
    if market_key == "US":
        return """[데이터 해석 가이드 — US 전용, 반드시 준수]
• SPY/QQQ/IWM의 1d/5d 흐름과 breadth를 먼저 확인하세요.
• VIX/DXY/TNX/HYG가 N/A이면 안정 신호가 아니라 data_quality 불확실성입니다.
• VIX 상승 + HYG 약세 + TNX/DXY 급등은 risk-off 신호입니다.
• 섹터 ETF(XLK/XLF/XLE 등)는 시장 판단의 보조 근거이며, 개별 대형주 1~3개만으로 mode를 결정하지 마세요.
• premarket/after-hours 데이터는 정규장 breadth보다 신뢰도가 낮습니다. confidence 조정에 우선 반영하세요.
• FOMC/CPI/고용/대형 실적 발표 당일은 1d 방향이 발표 전후로 바뀔 수 있으므로 5d 추세와 이벤트 리스크를 함께 보세요.
• KR 전용 지표(VKOSPI, 외국인 선물, USD/KRW)를 US mode 판단의 주 근거로 쓰지 마세요."""
    return """[데이터 해석 가이드 — KR 전용, 반드시 준수]
• 코스피: "1d X% / 5d Y%" 형태 — 1d는 전일 대비, 5d는 주간 추세. 둘 다 확인할 것.
• USD/KRW: "1,465 (1d -0.8%, 5d -3.8%, 20일고점대비 -4.2%)" 형태
  - 1d 음수 = KRW 강세(위험 완화), 양수 = KRW 약세(위험)
  - 20일고점대비 -5% 이상이면 환율 위험은 단기 해소 국면
• VKOSPI 결측: 데이터 없음. 중간 불확실성(보통 수준)으로 처리. DEFENSIVE 판단 근거로 쓰지 말 것.
• 오늘 요일: 월요일이면 금요일 종가 기준임을 감안. 주말 사이 갭 가능성 포함.
• 외국인/기관 N/A: 데이터 없음. 0(순매도도 순매수도 없음)과 다름. 판단 유보.
• MACD 골든크로스(확대중): 추세 강화 신호. MACD 골든크로스(축소중): 추세 약화 주의.
• 이벤트 ⚠️ 표시(FOMC, CPI, 실적 집중 주간): confidence를 0.05~0.08 하향 후 출력. key_reason에 이벤트 리스크 인지 여부 반드시 언급. 5d 추세를 1d보다 우선 참고.
• FOMC 결과 발표 당일: 발표 전후로 1d 수치 방향이 뒤집힐 수 있음. 5d 추세 우선. 1d 단독 과신 금지."""


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
    _is_bear = str(analyst_type).lower() == "bear"
    _bear_reversal_hint = (
        ',\n  "reversal_trigger":"현재 stance가 뒤집히는 조건 한 문장 (구체적 지표 수치 포함)"'
        if _is_bear else ""
    )

    prompt = f"""{_persona_for(analyst_type, market)}

{BREADTH_FIRST_CONTRACT}
{COMMON_DECISION_CONTRACT}
{HARD_SOFT_RULE_CONTRACT}
{feedback_section}{lesson_section}
{_market_interpretation_guide(market)}

[시장 전체 메모리]
{brain_summary}

[보정 지침]
{correction}

[오늘 시장 데이터]
{digest_prompt}

위 데이터를 당신의 전문 영역 관점에서 분석하세요. 반드시 트렌드 수치(1d/5d)를 근거로 언급하세요.
JSON으로만 응답 (다른 텍스트 없이):
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"핵심 근거 한 문장 (구체적 지표 수치 포함)"{_bear_reversal_hint}}}"""

    try:
        r1_max_tokens = _env_int_bound("CLAUDE_ANALYST_R1_MAX_TOKENS", 700, 200, 2000)
        _t0 = time.perf_counter()
        resp = client.messages.create(model=_r1_model, max_tokens=r1_max_tokens,
                                      messages=[{"role": "user", "content": prompt}])
        _duration_ms = int((time.perf_counter() - _t0) * 1000)
        raw = resp.content[0].text.strip()
        result = _sanitize_analyst_result(_extract_json(raw), analyst_type)
        credit_record(
            resp.usage.input_tokens, resp.usage.output_tokens,
            f"analyst_{analyst_type}_r1", model=_r1_model,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        save_raw_call(
            label=f"analyst_{analyst_type}_r1",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
            model=_r1_model,
            duration_ms=_duration_ms,
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
    if not is_available_judgment(my_r1):
        return {**dict(my_r1 or {}), "debate_skipped": True, "debate_skip_reason": "r1_unavailable"}
    raw_others = dict(others or {})
    available_others = {
        atype: result for atype, result in raw_others.items()
        if is_available_judgment(result)
    }
    excluded_unavailable_peer_roles = [
        atype for atype in raw_others
        if atype not in available_others
    ]
    if not available_others:
        return {
            **dict(my_r1 or {}),
            "debate_skipped": True,
            "debate_skip_reason": "insufficient_available_peers",
            "available_peer_count": 0,
            "available_peer_roles": [],
            "excluded_unavailable_peer_roles": excluded_unavailable_peer_roles,
        }
    others_txt = "\n".join(
        f"• {atype.upper()} 분석가: {r['stance']} (확신도 {r.get('confidence',0):.0%})\n"
        f"  근거: {r.get('key_reason','')}"
        for atype, r in available_others.items()
    )
    peer_note = (
        f"\n[available peer analysts: {len(available_others)}] "
        f"{', '.join(available_others.keys())}\n"
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
{peer_note}{others_txt}

[오늘 시장 데이터 요약]
{digest_prompt[:800]}

토론 지침:
1. 자신의 R1 stance에 반하는 근거를 데이터에서 먼저 탐색하세요.
2. 반대 근거가 충분히 강하면 stance를 수정하고 change_reason에 명시하세요.
3. 반대 근거가 약하거나 없으면 stance를 유지하고 그 이유를 한 문장으로 설명하세요.
   예: "반론 검토 결과: VIX 안정 + breadth 양호로 하락 근거 부족 — 유지"
4. 다수 의견에 동조하기 위한 변경은 하지 마세요.
• 과거 토론 이력이 있다면, 비슷한 상황에서 의견 변경이 도움이 됐는지 참고하세요.

JSON으로만 응답:
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"최종 핵심 근거 한 문장 (구체적 지표 포함)",
  "suggested_size_pct":0~100,
  "new_buy_permission":"allow|selective|block",
  "max_gross_exposure_pct":0~100,
  "suggested_strategy":"모멘텀|평균회귀|갭+눌림|변동성돌파|관망",
  "changed":true|false,
  "change_reason":"변경했다면 설득된 논거, 유지했다면 반론 검토 결과 한 문장"}}"""

    try:
        r2_max_tokens = _env_int_bound("CLAUDE_ANALYST_R2_MAX_TOKENS", 900, 300, 2500)
        _t0 = time.perf_counter()
        resp = client.messages.create(model=MODEL, max_tokens=r2_max_tokens,
                                      messages=[{"role": "user", "content": prompt}])
        _duration_ms = int((time.perf_counter() - _t0) * 1000)
        raw = resp.content[0].text.strip()
        result = _extract_json(raw)
        merged = _merge_debate_result(my_r1, result)
        credit_record(
            resp.usage.input_tokens, resp.usage.output_tokens,
            f"analyst_{analyst_type}_r2", model=MODEL,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        save_raw_call(
            label=f"analyst_{analyst_type}_r2",
            prompt=prompt, raw_response=raw, parsed={**result, "_merged": merged},
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            duration_ms=_duration_ms,
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
                "debate_peers": {
                    "available_peer_count": len(available_others),
                    "available_peer_roles": list(available_others.keys()),
                    "excluded_unavailable_peer_roles": excluded_unavailable_peer_roles,
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
        tagged = dict(my_r1 or {})
        tagged.update({
            "r2_unavailable": True,
            "debate_skipped": True,
            "debate_skip_reason": "r2_error",
        })
        return tagged


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
        active_lessons = build_active_lesson_context(market, prompt_scope="market_judgment")
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
        if not is_available_judgment(r1.get(atype) or {}):
            r2[atype] = {
                **dict(r1.get(atype) or {}),
                "debate_skipped": True,
                "debate_skip_reason": "r1_unavailable",
            }
            continue
        others = {
            k: v for k, v in r1.items()
            if k != atype and is_available_judgment(v)
        }
        if not others:
            r2[atype] = {
                **dict(r1.get(atype) or {}),
                "debate_skipped": True,
                "debate_skip_reason": "insufficient_available_peers",
                "available_peer_count": 0,
                "available_peer_roles": [],
                "excluded_unavailable_peer_roles": [
                    k for k, v in r1.items()
                    if k != atype and not is_available_judgment(v)
                ],
            }
            continue
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

    # 토론 결과는 prompt-visible 정책 메모리이므로 기본 런타임에서는 brain.json에 직접 저장하지 않는다.
    try:
        from datetime import date as _date
        today_str = _date.today().isoformat()
        log.info(f"[토론 기록 승인대기] {today_str} {market} direct brain 저장 생략 변경={len(changes)}건")
    except Exception as e:
        log.warning(f"[토론 기록 승인대기 처리 실패] {e}")

    unavailable_roles = [
        atype for atype in ("bull", "bear", "neutral")
        if not is_available_judgment(r2.get(atype) or {})
    ]
    available_roles = [
        atype for atype in ("bull", "bear", "neutral")
        if atype not in unavailable_roles
    ]

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
            "analyst_unavailable_count": len(unavailable_roles),
            "analyst_unavailable_roles": unavailable_roles,
            "available_analyst_roles": available_roles,
        }},
    )
    return {"bull": r2["bull"], "bear": r2["bear"], "neutral": r2["neutral"],
            "_debate": {
                "r1": r1,
                "changes": changes,
                "unavailable_roles": unavailable_roles,
                "available_roles": available_roles,
            }}


def _digest_news_excerpt(digest_prompt: str, max_chars: int = 600) -> str:
    text = str(digest_prompt or "")
    if not text:
        return ""
    marker_positions = [
        pos
        for pos in (
            text.find("▶ 주요 뉴스"),
            text.find("주요 뉴스 ("),
            text.find("top_news"),
        )
        if pos >= 0
    ]
    if not marker_positions:
        return ""
    start = min(marker_positions)
    end = text.find("\n▶", start + 1)
    if end < 0:
        end = len(text)
    excerpt = text[start:end].strip()
    if not excerpt:
        return ""
    return "\nDigest news excerpt:\n" + excerpt[: max(1, int(max_chars))].strip() + "\n"


def _selection_market_session_date(market: str) -> str:
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI

    market_key = str(market or "").strip().upper()
    now_kr = _dt.now(_ZI("Asia/Seoul"))
    if market_key == "US":
        return now_kr.astimezone(_ZI("America/New_York")).date().isoformat()
    return now_kr.date().isoformat()


def select_tickers(market: str, digest_prompt: str, consensus_mode: str, candidates: list,
                   intraday_context: str = "",
                   lesson_context: str = "",
                   market_change_pct: Optional[float] = None,
                   secondary_change_pct: Optional[float] = None,
                   execution_phase: str = "",
                   evidence_by_ticker: Optional[dict] = None,
                   prompt_pool_override: Optional[list[dict]] = None,
                   prompt_pool_meta_override: Optional[dict] = None,
                   session_date: str = "") -> list:
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
    if prompt_pool_override is not None:
        source_keys = {
            _prompt_ticker_key(market, row.get("ticker"))
            for row in list(candidates or [])
            if isinstance(row, dict) and str(row.get("ticker") or "").strip()
        }
        override_rows = [
            dict(row or {})
            for row in list(prompt_pool_override or [])
            if isinstance(row, dict) and str((row or {}).get("ticker") or "").strip()
        ]
        unknown_override_tickers = [
            _prompt_ticker_key(market, row.get("ticker"))
            for row in override_rows
            if source_keys and _prompt_ticker_key(market, row.get("ticker")) not in source_keys
        ]
        if unknown_override_tickers:
            log.warning(
                f"[ticker-selection] {market} prompt override removed non-candidate tickers: "
                f"{unknown_override_tickers[:10]}"
            )
            override_rows = [
                row for row in override_rows
                if _prompt_ticker_key(market, row.get("ticker")) not in set(unknown_override_tickers)
            ]
        prompt_candidates = override_rows
        prompt_pool_meta = dict(prompt_pool_meta_override or {})
        prompt_pool_meta["prompt_pool"] = [dict(row or {}) for row in prompt_candidates]
        prompt_pool_meta["prompt_pool_count"] = len(prompt_candidates)
        prompt_pool_meta.setdefault("full_pool_count", len(candidates or []))
        prompt_pool_meta.setdefault("scored_pool_count", prompt_pool_meta.get("full_pool_count", len(candidates or [])))
        prompt_pool_meta.setdefault("version", "prompt_pool_override")
        prompt_pool_meta["_prompt_pool_override_used"] = True
        if unknown_override_tickers:
            prompt_pool_meta["_prompt_pool_override_unknown_tickers"] = unknown_override_tickers[:20]
    else:
        prompt_candidates, prompt_pool_meta = prepare_selection_prompt_pool(market, candidates)
        prompt_pool_meta = dict(prompt_pool_meta or {})
        prompt_pool_meta["_prompt_pool_override_used"] = False
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
        rel_vol = _safe_float(candidate.get("rel_vol_shadow", 0.0), 0.0)
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
            _candidate_identity_prefix(candidate),
            f"chg={rate:+.2f}%",
            rs_str,
            f"p={price:,.2f}".rstrip("0").rstrip(".") if price > 0 else "",
            "" if _kr_premarket else (
                (f"rvol={rel_vol:.1f}x" if rel_vol > 0 else "")
                if market == "US"
                else (f"vol={vr:.1f}x" if vr > 0 else "")
            ),
            (
                f"turn={turnover/1e8:.1f}억"
                if market == "KR" and turnover > 0 else
                (f"turn=${turnover/1e6:.1f}M" if market == "US" and turnover > 0 else "")
            ),
            f"board={market_type}" if market_type else "",
            f"category={category}" if category else "",
            f"sector={sector}" if sector else "",
            f"liq={liquidity_bucket}",
            (
                f"stale={candidate.get('freshness_age_sessions')}s"
                if str(candidate.get("freshness_grade") or "") == "OLD" else ""
            ),
            _earnings_line_token(candidate, market),
            _candidate_news_hint(candidate),
            _candidate_discovery_hint(candidate),
            _candidate_trainer_hint(candidate),
            _candidate_quality_hint(candidate),
            _candidate_evidence_hint(candidate),
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
    raw_evidence_map = evidence_by_ticker if isinstance(evidence_by_ticker, dict) else {}
    prompt_keys = {
        _prompt_ticker_key(market, row.get("ticker"))
        for row in list(prompt_candidates or [])
        if isinstance(row, dict) and str(row.get("ticker") or "").strip()
    }
    evidence_map: dict[str, dict] = {}
    dropped_evidence_keys: list[str] = []
    for raw_key, raw_value in raw_evidence_map.items():
        key = _prompt_ticker_key(market, raw_key)
        if not key:
            continue
        if key not in prompt_keys:
            dropped_evidence_keys.append(key)
            continue
        if isinstance(raw_value, dict):
            evidence_map[key] = dict(raw_value)
    if dropped_evidence_keys:
        prompt_pool_meta["_evidence_non_prompt_count"] = len(dropped_evidence_keys)
        prompt_pool_meta["_evidence_non_prompt_tickers_sample"] = dropped_evidence_keys[:10]
        log.debug(
            f"[ticker-selection] {market} dropped non-prompt evidence "
            f"count={len(dropped_evidence_keys)} sample={dropped_evidence_keys[:10]}"
        )
    evidence_items: list[dict] = []
    compact_evidence_shadow_items: list[dict] = []
    if evidence_map:
        for candidate in prompt_candidates:
            ticker = _prompt_ticker_key(market, candidate.get("ticker"))
            if not ticker:
                continue
            evidence = evidence_map.get(ticker)
            if not evidence:
                continue
            evidence_item = dict(evidence)
            evidence_item["ticker"] = _prompt_ticker_key(market, evidence_item.get("ticker") or ticker)
            for meta_key in (
                "evidence_class",
                "selection_evidence_action_ceiling",
                "selection_evidence_missing_reason",
                "selection_evidence_data_state",
            ):
                if candidate.get(meta_key) not in (None, ""):
                    evidence_item[meta_key] = candidate.get(meta_key)
            compact_evidence_shadow_items.append(_compact_selection_evidence_item(evidence_item))
            if _env_bool_flag("SELECTION_COMPACT_EVIDENCE_PACK_ENABLED", False):
                evidence_item = _compact_selection_evidence_item(evidence_item)
            evidence_items.append(evidence_item)
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
    evidence_rule_block = ""
    if any(str(row.get("evidence_class") or "").strip() for row in prompt_candidates if isinstance(row, dict)):
        evidence_rule_block = (
            "\n- Candidates with ev=COMPACT_ONLY or ev=MISSING_OR_STALE have max action WATCH."
            "\n- Candidates with ev=PREFETCHED_PARTIAL should remain WATCH unless supplied ceiling explicitly allows more."
            "\n- Do not put a ticker in trade_ready/tr unless its evidence ceiling allows BUY_READY or PROBE_READY."
        )

    digest_news_section = _digest_news_excerpt(digest_prompt)
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
    active_lessons = build_active_lesson_context(market, prompt_scope="selection")
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

    # C1 캐시 system 블록 — compact 분기에서만 설정, 그 외 None (단일 프롬프트 유지)
    selection_system_blocks = None

    prompt = f"""{phase_instruction}
execution_phase: {execution_phase or 'unspecified'}
오늘 {market} 세션에서 후보를 WATCH와 TRADE_READY로 분리하세요.
합의 모드: {consensus_mode}
후보 종목:
{cand_text}
{evidence_section}

시장 컨텍스트:
{digest_prompt[:220]}{digest_news_section}{intraday_section}{brain_section}{tuner_section}{lesson_section}{selection_feedback[:700]}{tuning_feedback_section}
{COMMON_DECISION_CONTRACT}
{SELECTION_EXECUTION_PHASE_CONTRACT}
{SIZING_DECISION_CONTRACT}
{PRICE_PLAN_CONTRACT}
{HARD_SOFT_RULE_CONTRACT}
{candidate_action_section}
규칙:
{phase_rule_block}
{evidence_rule_block}
- 후보 종목 중에서만 고르세요.
- rvol은 자기 20일 평균 대비 세션 진행률 보정 거래량 배수입니다 (US 전용). rvol>=2는 비정상 수급 급증이니 신선한 변화로 우선 검토하되, 추격 매수가 아니라 눌림 계획(PULLBACK_WAIT/price_targets)으로 다루세요.
- watchlist는 선별 목록입니다. 최대 {watch_max}개, 보통 8~18개 수준으로 제한하세요.
- trade_ready는 실제 매수 권한 후보입니다. 최대 {trade_max}개이며 0개도 허용됩니다.
- trade_ready는 전략 슬롯을 나눠서 고르세요. slot guide: {slot_text}
- 저유동성, 구조화 상품, 과열, 손절폭 과대 후보는 trade_ready에서 제외하세요.
- KR market: momentum 전략은 현재 누적 손실 기록 중이므로 trade_ready 금지. watchlist 관찰만 허용.
- KR market: 동일 종목이 직전 세션에서 손실(loss_cap/hard_stop)으로 청산된 경우 trade_ready 재선정 금지. watchlist 유지.
- preopen_pin=HARD 후보는 장전 우수 후보라 평가 기회를 보장한 것이며 자동 매수 후보가 아닙니다.
- preopen_pin=HARD confirm=required_before_trade_ready 후보는 anchor 대비 현재가 안정, OR/전략 신호, 개장 후 품질 확인 전에는 trade_ready로 올리지 마세요.
- Use intraday context session_phase/active_strategies/runtime gates to judge execution feasibility, not just strength.
- Treat exec= hints (or/atr/ep/fit/tclose/blackout) as real execution constraints. Strong names with poor exec hints should stay watch_only.
- Treat exec= feas=<strategy>:<ceiling>:<reason> as an execution ceiling. Do not make trade_ready above the listed ceiling for the selected strategy.
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
- price_targets는 trade_ready 종목 또는 candidate_actions에서 PULLBACK_WAIT를 선택한 종목에 대해서만 채우세요. 그 외 watch_only 종목에는 price_targets를 쓰지 마세요.
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

    if preopen_watch:
        preopen_phase_instruction = (
            f"PREOPEN WATCH ONLY for {market}: prepare a broad watch candidate list before regular-market open. "
            "This is not an executable buy decision. trade_ready must be []."
        )
        prompt = f"""{preopen_phase_instruction}
execution_phase: {execution_phase or 'preopen_watch'}
market: {market}
mode: {consensus_mode}

Candidates:
{cand_text}
{evidence_section}

Market context:
{digest_prompt[:220]}{digest_news_section}{intraday_section}{brain_section}{tuner_section}{lesson_section}{selection_feedback[:700]}{tuning_feedback_section}
{COMMON_DECISION_CONTRACT}

PREOPEN WATCH OUTPUT CONTRACT:
- Choose only from supplied candidates.
- Candidate identity is ticker + name=. Do not invent or substitute company names.
- If you mention a company name in reasons, it must exactly match the supplied name= for that ticker.
- Return WATCH candidates only. trade_ready must be [].
- Do not include execution plans, sizing, allocation, budgets, or strategy recommendations.
- Use reasons/veto to describe what must be confirmed after the regular open.
- watchlist max {watch_max}; normally return 8 to 18 names.
- reasons must be short.
- JSON only.

{{
  "watchlist":["code1","code2"],
  "trade_ready":[],
  "reasons":{{"code1":"short reason"}},
  "veto":{{"code2":"short veto"}}
}}"""

    selection_prompt_budget_meta = {
        "compact_prompt_budget_enabled": False,
        "candidate_prompt_chars": len(cand_text),
        "evidence_prompt_chars": len(evidence_section),
    }
    smart_skip_watch_cap = watch_max
    smart_skip_trade_cap = trade_max
    smart_skip_prompt_contract = "selection_preopen_watch_v1" if preopen_watch else "selection_rank_v3+execution_plan_v1"
    if compact_selection_enabled:
        compact_watch_max = min(
            watch_max,
            _env_int_bound("CLAUDE_SELECTION_COMPACT_WATCH_MAX", min(15, watch_max), 1, max(1, watch_max)),
        )
        compact_trade_max = min(
            trade_max,
            _env_int_bound("CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX", min(5, trade_max), 0, max(0, trade_max)),
        )
        smart_skip_watch_cap = compact_watch_max
        smart_skip_trade_cap = compact_trade_max
        smart_skip_prompt_contract = "selection_compact.v1"
        compact_cand_text, compact_candidate_meta = _compact_selection_candidate_lines(
            cand_lines,
            max_line_chars=_env_int_bound("CLAUDE_SELECTION_COMPACT_CANDIDATE_LINE_MAX_CHARS", 260, 120, 800),
            max_total_chars=_env_int_bound("CLAUDE_SELECTION_COMPACT_CANDIDATES_MAX_CHARS", 6000, 1000, 20000),
        )
        compact_digest_limit = _env_int_bound("CLAUDE_SELECTION_COMPACT_DIGEST_MAX_CHARS", 160, 80, 800)
        compact_feedback_limit = _env_int_bound("CLAUDE_SELECTION_COMPACT_FEEDBACK_MAX_CHARS", 500, 120, 1200)
        selection_prompt_budget_meta.update(
            {
                "compact_prompt_budget_enabled": True,
                "candidate_prompt_chars": len(compact_cand_text),
                "digest_max_chars": compact_digest_limit,
                "selection_feedback_max_chars": compact_feedback_limit,
                **compact_candidate_meta,
            }
        )
        _static_universal_rules = """- Choose only from supplied candidates.
- Use live execution context and exec= hints as constraints.
- Treat exec= feas=<strategy>:<ceiling>:<reason> as an execution ceiling for trade_ready decisions.
- Strong names with poor execution hints should remain WATCH.
- Use recent selection feedback to calibrate trade_ready aggressiveness.
- Recent feedback and tuning feedback are calibration only, not permission to chase.
- ca[].s must be a concrete setup strategy such as momentum, gap_pullback, mean_reversion, opening_range_pullback, volatility_breakout, or continuation.
- ca[].rc, ca[].blk, and ca[].inv must be short machine codes.
- Do not output human explanations.
- KR market: momentum strategy is prohibited in tr (trade_ready). WATCH only.
- KR market: tickers 078150/264850/024840 are prohibited in tr. WATCH only."""
        _static_contract_block = (
            f"{COMMON_DECISION_CONTRACT}\n"
            f"{HARD_SOFT_RULE_CONTRACT}\n"
            f"{compact_output_contract(watch_max=compact_watch_max, trade_max=compact_trade_max)}\n\n"
            f"Universal rules:\n{_static_universal_rules}"
        )
        _dynamic_prompt = f"""{phase_instruction}
execution_phase: {execution_phase or 'unspecified'}
market: {market}
mode: {consensus_mode}

Candidates:
{compact_cand_text}
{evidence_section}

Market context:
{digest_prompt[:compact_digest_limit]}{digest_news_section}{intraday_section}{brain_section}{tuner_section}{lesson_section}{selection_feedback[:compact_feedback_limit]}{tuning_feedback_section}

Market-phase rules:
{phase_rule_block}
{evidence_rule_block}
"""
        # C1 selection 프롬프트 캐시 (2026-06-11): 정적 계약/스키마/공통규칙을 system 블록으로
        # 분리해 1h TTL 캐시. off면 기존 단일 프롬프트와 동일 텍스트 구성 유지.
        if _env_bool_flag("SELECTION_PROMPT_CACHE_ENABLED", False):
            selection_system_blocks = [
                {
                    "type": "text",
                    "text": _static_contract_block,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ]
            prompt = _dynamic_prompt
        else:
            selection_system_blocks = None
            prompt = f"""{phase_instruction}
execution_phase: {execution_phase or 'unspecified'}
market: {market}
mode: {consensus_mode}

Candidates:
{compact_cand_text}
{evidence_section}

Market context:
{digest_prompt[:compact_digest_limit]}{digest_news_section}{intraday_section}{brain_section}{tuner_section}{lesson_section}{selection_feedback[:compact_feedback_limit]}{tuning_feedback_section}
{COMMON_DECISION_CONTRACT}
{HARD_SOFT_RULE_CONTRACT}
{compact_output_contract(watch_max=compact_watch_max, trade_max=compact_trade_max)}

Rules:
{phase_rule_block}
{evidence_rule_block}
{_static_universal_rules}
"""
    selection_prompt_budget_meta["prompt_chars"] = len(prompt)
    selection_prompt_budget_meta["market_context_chars"] = len(str(digest_prompt[:220] or ""))

    fallback_meta = normalize_selection_result(
        {
            "watchlist": _safe_watch_fallback(prompt_candidates, market),
            "trade_ready": [],
            "_parse_recovered": True,
            "_fallback_mode": "safe_watch",
        },
        prompt_candidates,
        market,
        allow_legacy_auto_ready=_env_bool_flag("ALLOW_LEGACY_SELECTION_AUTO_READY", False),
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
        feasibility_by_ticker = {}
        for row in enriched["_final_prompt_pool"]:
            if not isinstance(row, dict):
                continue
            ticker_key = str(row.get("ticker") or "").strip()
            if not ticker_key:
                continue
            if str(market or "").upper() == "US":
                ticker_key = ticker_key.upper()
            pack = row.get("strategy_feasibility")
            if isinstance(pack, dict) and pack:
                feasibility_by_ticker[ticker_key] = dict(pack)
        if feasibility_by_ticker:
            enriched["_strategy_feasibility_by_ticker"] = feasibility_by_ticker
        enriched["_excluded_from_prompt"] = list(prompt_pool_meta.get("excluded_from_prompt") or [])
        enriched["_safe_empty_prompt_pool"] = bool(prompt_pool_meta.get("safe_empty_prompt_pool"))
        enriched["_prompt_pool_empty_reason"] = str(prompt_pool_meta.get("prompt_pool_empty_reason") or "")
        enriched["_trainer_all_quarantined"] = bool(prompt_pool_meta.get("trainer_all_quarantined"))
        enriched["_prompt_overlay_requested_mode"] = str(prompt_pool_meta.get("_prompt_overlay_requested_mode") or "off")
        enriched["_prompt_overlay_mode"] = str(prompt_pool_meta.get("_prompt_overlay_mode") or "current_only")
        enriched["_prompt_overlay_candidate_state"] = str(prompt_pool_meta.get("_prompt_overlay_candidate_state") or "current_only")
        enriched["_overlay_plan_a_available"] = int(prompt_pool_meta.get("_overlay_plan_a_available") or 0)
        enriched["_overlay_plan_a_added"] = int(prompt_pool_meta.get("_overlay_plan_a_added") or 0)
        enriched["_overlay_added_tickers"] = list(prompt_pool_meta.get("_overlay_added_tickers") or [])
        enriched["_overlay_removed_tickers"] = list(prompt_pool_meta.get("_overlay_removed_tickers") or [])
        enriched["_overlay_keep_current"] = prompt_pool_meta.get("_overlay_keep_current")
        enriched["_overlay_plan_a_max"] = prompt_pool_meta.get("_overlay_plan_a_max")
        enriched["_overlay_plan_b_used"] = bool(prompt_pool_meta.get("_overlay_plan_b_used"))
        for key in (
            "_prompt_pool_override_used",
            "_prompt_pool_override_unknown_tickers",
            "_evidence_non_prompt_count",
            "_evidence_non_prompt_tickers_sample",
            "evidence_prefetch_source",
            "evidence_requested_tickers",
            "evidence_requested_count",
            "evidence_prompt_overlap_count",
            "evidence_prompt_overlap_ratio",
            "evidence_fetch_success_tickers",
            "evidence_fetch_success_count",
            "evidence_fetch_success_ratio",
            "evidence_pack_source",
            "evidence_pack_tickers",
            "evidence_pack_count",
            "evidence_class_counts",
            "selection_evidence_ceiling_counts",
            "selection_evidence_watch_ceiling_tickers",
            "selection_evidence_reorder_shadow_tickers",
            "prompt_exec_missing_count",
            "prompt_exec_missing_pct",
            "prompt_exec_formed_count",
            "prompt_exec_forming_count",
            "_discovery_enabled",
            "_discovery_mode",
            "_discovery_max_slots",
            "_discovery_added",
            "_discovery_added_tickers",
            "_discovery_role_by_ticker",
            "_discovery_action_ceiling_by_ticker",
            "_discovery_signal_by_ticker",
            "_discovery_reject_counts",
            "_prompt_pool_core_count",
            "_prompt_pool_discovery_count",
            "selection_trace_id",
            "visibility_contract_version",
            "_smart_skip_context",
            "_smart_skip_context_hash",
            "_smart_skip_core_hash",
            "_smart_skip_tail_hash",
        ):
            if key in prompt_pool_meta:
                enriched[key] = prompt_pool_meta.get(key)
        if prompt_pool_meta.get("_shadow_overlay_prompt_pool") is not None:
            enriched["_shadow_overlay_prompt_pool"] = list(prompt_pool_meta.get("_shadow_overlay_prompt_pool") or [])
            enriched["_shadow_overlay_tickers"] = list(prompt_pool_meta.get("_shadow_overlay_tickers") or [])
            enriched["_shadow_overlay_added_tickers"] = list(prompt_pool_meta.get("_shadow_overlay_added_tickers") or [])
            enriched["_shadow_overlay_removed_tickers"] = list(prompt_pool_meta.get("_shadow_overlay_removed_tickers") or [])
            enriched["_shadow_overlay_plan_a_available"] = int(prompt_pool_meta.get("_shadow_overlay_plan_a_available") or 0)
            enriched["_shadow_overlay_plan_a_added"] = int(prompt_pool_meta.get("_shadow_overlay_plan_a_added") or 0)
        if prompt_pool_meta.get("_prompt_overlay_error"):
            enriched["_prompt_overlay_error"] = str(prompt_pool_meta.get("_prompt_overlay_error") or "")
        return enriched

    fallback_meta = _attach_prompt_pool_meta(fallback_meta)
    fallback = fallback_meta["watchlist"]

    smart_skip_session_date = str(session_date or _selection_market_session_date(market))
    smart_skip_context = selection_smart_skip.market_context_components(
        market_change_pct=market_change_pct,
        secondary_change_pct=secondary_change_pct,
        intraday_context=intraday_context,
        session_phase=execution_phase,
        consensus_mode=consensus_mode,
    )
    smart_skip_core_hash = selection_smart_skip.prompt_pool_rank_hash(
        prompt_candidates,
        market=market,
        start=1,
        end=int(os.getenv("SELECTION_SMART_SKIP_CORE_SIZE", "25") or 25),
    )
    smart_skip_tail_hash = selection_smart_skip.prompt_pool_rank_hash(
        prompt_candidates,
        market=market,
        start=int(os.getenv("SELECTION_SMART_SKIP_CORE_SIZE", "25") or 25) + 1,
        end=40,
    )
    prompt_pool_meta["_smart_skip_context"] = dict(smart_skip_context)
    prompt_pool_meta["_smart_skip_context_hash"] = selection_smart_skip.sha256_text(
        json.dumps(smart_skip_context, ensure_ascii=False, sort_keys=True)
    )[:20]
    prompt_pool_meta["_smart_skip_core_hash"] = smart_skip_core_hash
    prompt_pool_meta["_smart_skip_tail_hash"] = smart_skip_tail_hash
    smart_skip_prompt_hash = selection_smart_skip.semantic_signature(
        market=market,
        session_date=smart_skip_session_date,
        consensus_mode=consensus_mode,
        execution_phase=execution_phase,
        candidates=prompt_candidates,
        prompt_contract=smart_skip_prompt_contract,
        watch_cap=smart_skip_watch_cap,
        trade_cap=smart_skip_trade_cap,
        session_phase=execution_phase,
        config_hash=str(prompt_pool_meta.get("config_hash") or prompt_pool_meta.get("_config_hash") or ""),
        lesson_hash=str(active_lesson_meta.get("hash") or active_lesson_meta.get("selected_hash") or ""),
        market_context=smart_skip_context,
        prompt_pool_core_hash=smart_skip_core_hash,
        prompt_pool_tail_hash=smart_skip_tail_hash,
    )
    try:
        smart_skip = selection_smart_skip.maybe_reuse(
            market=market,
            consensus_mode=consensus_mode,
            execution_phase=execution_phase,
            prompt_hash=smart_skip_prompt_hash,
            prompt_candidate_count=len(prompt_candidates),
            preopen_watch=preopen_watch,
            session_date=smart_skip_session_date,
        )
    except Exception as smart_skip_exc:
        log.debug(f"[ticker-selection] smart skip fail-open {market}: {smart_skip_exc}")
        smart_skip = {"reuse": False, "reason": "smart_skip_error"}
    if bool(smart_skip.get("reuse")):
        selection_meta = dict(smart_skip.get("selection_meta") or {})
        selection_meta = _attach_prompt_pool_meta(selection_meta)
        selection_meta["_smart_skip_reused"] = True
        selection_meta["_smart_skip_mode"] = str(smart_skip.get("mode") or "live")
        selection_meta["_smart_skip_full_claude_call_skipped"] = bool(smart_skip.get("full_claude_call_skipped", True))
        selection_meta["_smart_skip_reason"] = str(smart_skip.get("reason") or "prompt_cache_hit")
        selection_meta["_smart_skip_cached_at"] = str(smart_skip.get("cached_at") or "")
        tickers = list(dict.fromkeys(selection_meta.get("watchlist") or fallback or []))
        selection_meta["watchlist"] = tickers
        selection_meta["trade_ready"] = list(selection_meta.get("trade_ready") or [])
        reasons = dict(smart_skip.get("reasons") or selection_meta.get("reasons") or {})
        _LAST_SELECTION_META = selection_meta
        log.info(
            f"[ticker-selection] {market} smart_skip reuse "
            f"full_call_skipped=true reason={selection_meta.get('_smart_skip_reason', '')} "
            f"scope={selection_meta.get('_smart_skip_scope', '')} "
            f"watch={tickers} trade_ready={selection_meta.get('trade_ready', [])}"
        )
        analysis_log.info(
            f"[selection smart_skip] {market} watch={tickers} trade_ready={selection_meta.get('trade_ready', [])}",
            extra={
                "extra": {
                    "event": "ticker_selection_smart_skip",
                    "log_contract": "selection_smart_skip_live_reuse_v1",
                    "source": "smart_skip_live_reuse",
                    "market": market,
                    "consensus_mode": consensus_mode,
                    "selected": tickers,
                    "trade_ready": selection_meta.get("trade_ready", []),
                    "candidate_count": len(prompt_candidates),
                    "reason": str(smart_skip.get("reason") or ""),
                    "mode": str(smart_skip.get("mode") or "live"),
                    "full_claude_call_skipped": bool(smart_skip.get("full_claude_call_skipped", True)),
                    "cache_scope": str(selection_meta.get("_smart_skip_scope") or smart_skip.get("scope") or ""),
                    "cached_at": str(selection_meta.get("_smart_skip_cached_at") or smart_skip.get("cached_at") or ""),
                    "prompt_hash": str(selection_meta.get("_smart_skip_prompt_hash") or smart_skip_prompt_hash),
                }
            },
        )
        return tickers, reasons

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
            _t0 = time.perf_counter()
            _select_kwargs: dict = {
                "model": MODEL,
                "max_tokens": selection_max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if selection_system_blocks:
                _select_kwargs["system"] = selection_system_blocks
            resp = client.messages.create(**_select_kwargs)
            _select_duration_ms = int((time.perf_counter() - _t0) * 1000)
            last_err = None
            break
        except Exception as _e:
            last_err = _e
            _emsg = str(_e)
            if is_claude_retryable_error(_e) and _attempt < 2:
                _wait = 2 ** (_attempt + 1)
                log.warning(f"[ticker-selection] Claude 재시도 가능 에러 ({type(_e).__name__}) -> {_wait}s retry ({_attempt + 1}/3)")
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
            source_prompt_id=smart_skip_prompt_contract,
            allow_legacy_auto_ready=_env_bool_flag("ALLOW_LEGACY_SELECTION_AUTO_READY", False),
        )
        selection_meta = _attach_prompt_pool_meta(selection_meta)
        if evidence_items:
            compact_evidence_enabled = _env_bool_flag("SELECTION_COMPACT_EVIDENCE_PACK_ENABLED", False)
            selection_meta["evidence_version"] = (
                "selection_evidence.compact_v1" if compact_evidence_enabled else "selection_evidence.v1"
            )
            selection_meta["evidence_tickers"] = [str(item.get("ticker") or item.get("t") or "") for item in evidence_items]
            selection_meta["evidence_omitted_count"] = int(evidence_omitted_count)
            selection_meta["compact_evidence_shadow_enabled"] = True
            selection_meta["compact_evidence_shadow_count"] = len(compact_evidence_shadow_items)
            selection_meta["compact_evidence_shadow_tickers"] = [str(item.get("t") or "") for item in compact_evidence_shadow_items]
            selection_meta["compact_evidence_shadow_sample"] = compact_evidence_shadow_items[:8]
            selection_meta["compact_evidence_pack_enabled"] = bool(compact_evidence_enabled)
            if compact_evidence_enabled:
                selection_meta["compact_evidence_pack_tickers"] = [str(item.get("t") or "") for item in evidence_items]
                selection_meta["compact_evidence_pack_included_count"] = len(evidence_items)
        if tuning_feedback_meta:
            selection_meta["tuning_feedback"] = dict(tuning_feedback_meta)
            selection_meta["tuning_feedback_applied"] = True
        credit_record(
            resp.usage.input_tokens, resp.usage.output_tokens, "select_tickers", model=MODEL,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        evidence_alignment_extra = {
            key: selection_meta.get(key)
            for key in (
                "evidence_prefetch_source",
                "evidence_requested_tickers",
                "evidence_requested_count",
                "evidence_prompt_overlap_count",
                "evidence_prompt_overlap_ratio",
                "evidence_fetch_success_tickers",
                "evidence_fetch_success_count",
                "evidence_fetch_success_ratio",
                "evidence_pack_source",
                "evidence_pack_tickers",
                "evidence_pack_count",
                "evidence_class_counts",
                "selection_evidence_ceiling_counts",
                "selection_evidence_watch_ceiling_tickers",
                "compact_evidence_pack_enabled",
                "compact_evidence_pack_tickers",
                "compact_evidence_pack_included_count",
                "compact_evidence_shadow_enabled",
                "compact_evidence_shadow_count",
                "compact_evidence_shadow_tickers",
                "prompt_exec_missing_count",
                "prompt_exec_missing_pct",
                "prompt_exec_formed_count",
                "prompt_exec_forming_count",
            )
            if key in selection_meta
        }
        _resp_meta = claude_response_meta(resp)
        save_raw_call(
            label="select_tickers",
            prompt=prompt,
            raw_response=raw,
            parsed={**result, "_normalized": selection_meta},
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            duration_ms=_select_duration_ms if "_select_duration_ms" in dir() else None,
            market=market,
            model=MODEL,
            parse_error=parse_error,
            parse_stage=parse_stage,
            prompt_version="selection_rank_v3+compact_v1" if compact_selection_enabled else smart_skip_prompt_contract,
            cache_creation_input_tokens=_resp_meta["cache_creation_input_tokens"],
            cache_read_input_tokens=_resp_meta["cache_read_input_tokens"],
            request_id=_resp_meta["request_id"],
            service_tier=_resp_meta["service_tier"],
            extra={
                "active_lessons": active_lesson_meta,
                "evidence_version": str(selection_meta.get("evidence_version") or ("selection_evidence.v1" if evidence_items else "")),
                "evidence_tickers": list(selection_meta.get("evidence_tickers") or []),
                "evidence_omitted_count": int(evidence_omitted_count),
                "tuning_feedback_rule_version": tuning_feedback_meta.get("rule_version", ""),
                "tuning_feedback_applied": bool(tuning_feedback_meta),
                "stop_reason": stop_reason,
                "max_tokens": selection_max_tokens,
                "compact_schema_enabled": bool(compact_selection_enabled),
                "fallback_created_execution_authority": False,
                "prompt_budget": dict(selection_prompt_budget_meta),
                "selection_reference_prices": selection_reference_prices,
                "prompt_contract": smart_skip_prompt_contract,
                **evidence_alignment_extra,
            },
        )
        if result.get("_fallback_mode") == "selection_partial" and not compact_selection_enabled:
            retry_candidates = _pick_selection_retry_candidates(prompt_candidates, result, market)
            if retry_candidates:
                retry_active_lessons = build_active_lesson_context(market, retry=True, prompt_scope="selection")
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
                    retry_meta = normalize_selection_result(
                        retry_result,
                        retry_candidates,
                        market,
                        allow_legacy_auto_ready=_env_bool_flag("ALLOW_LEGACY_SELECTION_AUTO_READY", False),
                    )
                    credit_record(
                        retry_resp.usage.input_tokens, retry_resp.usage.output_tokens, "select_tickers_retry", model=MODEL,
                        cache_creation_input_tokens=getattr(retry_resp.usage, "cache_creation_input_tokens", 0) or 0,
                        cache_read_input_tokens=getattr(retry_resp.usage, "cache_read_input_tokens", 0) or 0,
                    )
                    retry_candidate_tickers = [
                        _prompt_ticker_key(market, row.get("ticker"))
                        for row in retry_candidates
                        if isinstance(row, dict) and _prompt_ticker_key(market, row.get("ticker"))
                    ]
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
                        extra={
                            "active_lessons": retry_active_lesson_meta,
                            "retry_candidate_count": len(retry_candidates),
                            "retry_candidate_tickers": retry_candidate_tickers,
                            "retry_prompt_chars": len(retry_prompt),
                            "retry_input_tokens": retry_resp.usage.input_tokens,
                            "retry_output_tokens": retry_resp.usage.output_tokens,
                        },
                    )
                    if not retry_result.get("_parse_recovered") and retry_meta.get("watchlist"):
                        result = retry_result
                        selection_meta = retry_meta
                        selection_meta["_selection_retry_trade_ready_ignored"] = retry_trade_ready
                        selection_meta["_selection_retry_candidate_count"] = len(retry_candidates)
                        selection_meta["_selection_retry_candidate_tickers"] = retry_candidate_tickers
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
        selection_meta.setdefault("_selection_consensus_mode", str(consensus_mode or ""))
        selection_meta.setdefault("_selection_execution_phase", str(execution_phase or ""))
        selection_meta.setdefault("_selection_prompt_contract", smart_skip_prompt_contract)
        selection_meta.setdefault("_selection_prompt_hash", smart_skip_prompt_hash)
        reason_identity_warnings = _selection_reason_identity_warnings(selection_meta, prompt_candidates, market)
        if reason_identity_warnings:
            selection_meta["_reason_identity_warnings"] = reason_identity_warnings
            log.warning(
                f"[ticker-selection] {market} reason identity mismatch "
                f"count={len(reason_identity_warnings)} sample={reason_identity_warnings[:3]}"
            )
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
        try:
            selection_smart_skip.record_full_call(
                market=market,
                consensus_mode=consensus_mode,
                execution_phase=execution_phase,
                prompt_hash=smart_skip_prompt_hash,
                prompt_candidate_count=len(prompt_candidates),
                selection_meta=selection_meta,
                reasons=reasons,
                session_date=smart_skip_session_date,
            )
        except Exception as smart_skip_record_exc:
            log.debug(f"[ticker-selection] smart skip record failed {market}: {smart_skip_record_exc}")
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
