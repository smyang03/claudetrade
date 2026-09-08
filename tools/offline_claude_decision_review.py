"""Claude 매수 judge / hold advisor 오프라인 적합성 감사.

외부 모델 API를 호출하지 않는다. 운영 원장에 이미 저장된 입력·응답·forward 결과만
읽어서 다음을 분리한다.

1. single_symbol_judge가 실제로 낸 action과 60분 후 가격 경로
2. 생성된 PULLBACK_WAIT 플랜의 live 등록/체결/손익
3. hold advisor의 현재 triage 표본과 입력 완전성
4. hold advisor JSONL outcome 라벨의 의미와 3거래일 SELL-vs-HOLD 반사실
5. Codex가 결과를 보지 않고 재판정할 수 있는 결정론적 blind case

주의:
- audit_candidate_outcomes의 return은 실제 주문 net이 아니라 후보 시점 forward 가격수익률이다.
- PULLBACK_WAIT를 후보 시점 즉시매수 수익률로 평가하면 안 된다. action별 opportunity
  surface를 보는 용도이며, 실제 플랜 성과는 v2_canonical_performance를 따로 집계한다.
- hold_advisor_exit_outcome.realized_net/hold_fwd_net은 현재 구현상 이름과 달리 gross
  수익률이다. 이 도구는 컬럼명을 그대로 읽되 보고서에서 그 사실을 명시한다.

Examples:
    python tools/offline_claude_decision_review.py --mode summary
    python tools/offline_claude_decision_review.py --mode blind --blind-count 10
    python tools/offline_claude_decision_review.py --mode reveal --case-ids J-...,H-...
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
ML_DB = ROOT / "data" / "ml" / "decisions.db"
RAW_DIR = ROOT / "logs" / "raw_calls"
HOLD_DIR = ROOT / "logs" / "hold_advisor"
KST = timezone(timedelta(hours=9))


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # 이 저장소의 raw_calls/hold_advisor naive timestamp는 실행 호스트 KST다.
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.mean(clean) if clean else None


def _median(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.median(clean) if clean else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _hash_rank(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _extract_json_after(prompt: str, marker: str) -> dict[str, Any]:
    pos = prompt.find(marker)
    if pos < 0:
        return {}
    tail = prompt[pos + len(marker) :].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(tail)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _extract_object_after_key(text: str, key: str) -> dict[str, Any]:
    """잘린 상위 JSON 안에서도 완결된 중첩 object 하나를 복원한다."""

    key_pos = text.find(f'"{key}"')
    if key_pos < 0:
        return {}
    start = text.find("{", key_pos + len(key) + 2)
    if start < 0:
        return {}
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : pos + 1])
                except ValueError:
                    return {}
                return value if isinstance(value, dict) else {}
    return {}


def _first_match(text: str, pattern: str) -> Any:
    match = re.search(pattern, text)
    return match.group(1) if match else None


def _nested(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _judge_input_from_prompt(prompt: str) -> dict[str, Any]:
    payload = _extract_json_after(prompt, "Input:\n")
    if payload:
        return payload
    # max-char truncation으로 JSON 끝이 잘린 경우에도 핵심 필드는 보존한다.
    input_text = prompt[prompt.find("Input:\n") + len("Input:\n") :] if "Input:\n" in prompt else prompt
    post_open = _extract_object_after_key(input_text, "post_open_features")
    risk = _extract_object_after_key(input_text, "risk_context")
    candidate = {
        "market": _first_match(input_text, r'"market"\s*:\s*"([^"]+)"'),
        "ticker": _first_match(input_text, r'"ticker"\s*:\s*"([^"]+)"'),
        "current_price": _first_match(input_text, r'"current_price"\s*:\s*([-+]?\d+(?:\.\d+)?)'),
        "price": _first_match(input_text, r'"price"\s*:\s*([-+]?\d+(?:\.\d+)?)'),
        "change_rate": _first_match(input_text, r'"change_rate"\s*:\s*([-+]?\d+(?:\.\d+)?)'),
        "early_judge_source": _first_match(input_text, r'"early_judge_source"\s*:\s*"([^"]+)"'),
        "source": _first_match(input_text, r'"source"\s*:\s*"([^"]+)"'),
        "trainer_candidate_state": _first_match(
            input_text, r'"trainer_candidate_state"\s*:\s*"([^"]+)"'
        ),
        "trainer_prompt_score": _first_match(
            input_text, r'"trainer_prompt_score"\s*:\s*([-+]?\d+(?:\.\d+)?)'
        ),
        "trainer_plan_a_score": _first_match(
            input_text, r'"trainer_plan_a_score"\s*:\s*([-+]?\d+(?:\.\d+)?)'
        ),
        "trainer_risk_score": _first_match(
            input_text, r'"trainer_risk_score"\s*:\s*([-+]?\d+(?:\.\d+)?)'
        ),
    }
    return {
        "market": candidate["market"],
        "ticker": candidate["ticker"],
        "candidate": candidate,
        "post_open_features": post_open,
        "risk_context": risk,
        "_partial_recovery": True,
    }


def _compact_judge_input(payload: dict[str, Any]) -> dict[str, Any]:
    candidate = _nested(payload, "candidate")
    features = _nested(payload, "post_open_features")
    if not features:
        features = _nested(candidate, "post_open_features")
    risk = _nested(payload, "risk_context")
    if not candidate and any(key in payload for key in ("ticker", "current_price", "feature_known_at")):
        candidate = payload
    current = _as_float(features.get("current_price"))
    if current is None:
        current = _as_float(candidate.get("current_price"))
    candidate_current = _as_float(candidate.get("current_price"))
    candidate_price = _as_float(candidate.get("price"))
    price_conflict_pct = None
    if current and candidate_price and current > 0:
        price_conflict_pct = (candidate_price / current - 1.0) * 100.0
    expected = _as_float(features.get("rvol_expected_cumulative_volume"))
    observed = _as_float(features.get("rvol_current_cumulative_volume"))
    rvol_profile = (observed / expected) if observed is not None and expected and expected > 0 else None
    feasibility = _nested(payload, "strategy_feasibility")
    feasibility_summary = {
        name: {
            "state": value.get("state"),
            "reason": value.get("reason"),
            "action_ceiling": value.get("action_ceiling"),
            "hard_block": value.get("hard_block"),
        }
        for name, value in feasibility.items()
        if isinstance(value, dict)
    }
    return {
        "market": payload.get("market") or candidate.get("market"),
        "ticker": candidate.get("ticker") or payload.get("ticker"),
        "candidate_source": candidate.get("source") or candidate.get("early_judge_source"),
        "candidate_reason": candidate.get("reason"),
        "partial_prompt_recovery": bool(payload.get("_partial_recovery")),
        "change_rate": _as_float(candidate.get("change_rate") or candidate.get("change_pct")),
        "trainer_state": candidate.get("trainer_candidate_state"),
        "trainer_prompt_score": _as_float(candidate.get("trainer_prompt_score")),
        "trainer_plan_a_score": _as_float(candidate.get("trainer_plan_a_score")),
        "trainer_risk_score": _as_float(candidate.get("trainer_risk_score")),
        "feature_known_at": features.get("known_at") or payload.get("feature_known_at"),
        "session_date": (
            features.get("market_session_date")
            or features.get("session_date")
            or candidate.get("session_date")
        ),
        "current_price": current,
        "candidate_current_price": candidate_current,
        "candidate_price": candidate_price,
        "candidate_price_vs_feature_pct": _round(price_conflict_pct),
        "elapsed_min": _as_float(features.get("market_open_elapsed_min")),
        "data_quality": features.get("data_quality"),
        "evidence_coverage": _as_float(features.get("evidence_coverage_ratio")),
        "momentum_state": features.get("momentum_state"),
        "ret_3m_pct": _as_float(features.get("ret_3m_pct")),
        "ret_5m_pct": _as_float(features.get("ret_5m_pct")),
        "ret_10m_pct": _as_float(features.get("ret_10m_pct")),
        "opening_range_break": features.get("opening_range_break"),
        "opening_range_high": _as_float(features.get("opening_range_high")),
        "opening_range_low": _as_float(features.get("opening_range_low")),
        "vwap": _as_float(features.get("vwap")),
        "vwap_distance_pct": _as_float(features.get("vwap_distance_pct")),
        "pullback_from_high_pct": _as_float(features.get("pullback_from_high_pct")),
        "volume_ratio_open": _as_float(features.get("volume_ratio_open")),
        "rvol_profile": _round(rvol_profile),
        "rvol_profile_status": features.get("rvol_profile_status"),
        "market_regime": risk.get("market_regime") or payload.get("market_regime"),
        "strategy_feasibility": feasibility_summary,
    }


@dataclass
class JudgeCall:
    case_id: str
    ts: datetime
    session_date: str
    market: str
    ticker: str
    action: str
    route: str
    valid: bool
    confidence: float | None
    reason: str
    parsed: dict[str, Any]
    compact_input: dict[str, Any]
    raw_path: str
    outcome_30m_pct: float | None = None
    outcome_60m_pct: float | None = None
    outcome_source: str = ""
    matched_candidate_key: str = ""
    match_delta_sec: float | None = None
    outcome_status: str = ""
    local_entry_price: float | None = None
    local_forward_30m_pct: float | None = None
    local_forward_60m_pct: float | None = None
    local_mfe_60m_pct: float | None = None
    local_mae_60m_pct: float | None = None
    local_data_source: str = ""
    plan_replay: dict[str, Any] | None = None


def load_judge_calls(start_date: str, end_date: str) -> list[JudgeCall]:
    rows: list[JudgeCall] = []
    for raw_path in sorted(RAW_DIR.glob("*_single_symbol_judge_*.json")):
        data = _read_json(raw_path)
        if not data:
            continue
        date = str(data.get("date") or "")[:10]
        if not (start_date <= date <= end_date):
            continue
        ts = _parse_dt(data.get("timestamp"))
        parsed = data.get("parsed") if isinstance(data.get("parsed"), dict) else {}
        prompt = str(data.get("prompt") or "")
        compact = _compact_judge_input(_judge_input_from_prompt(prompt))
        market = str(data.get("market") or compact.get("market") or "").upper()
        ticker = str((data.get("extra") or {}).get("ticker") or compact.get("ticker") or "").upper()
        if ts is None or not market or not ticker:
            continue
        action = str(parsed.get("action") or "").upper()
        session_date = str(compact.get("session_date") or date)[:10]
        case_id = f"J-{session_date.replace('-', '')}-{market}-{ticker}-{str(data.get('call_id') or ts.strftime('%H%M%S'))}"
        rows.append(
            JudgeCall(
                case_id=case_id,
                ts=ts,
                session_date=session_date,
                market=market,
                ticker=ticker,
                action=action,
                route=str(parsed.get("route") or ""),
                valid=bool(parsed.get("valid", not bool(data.get("parse_error")))),
                confidence=_as_float(parsed.get("confidence")),
                reason=str(parsed.get("reason") or ""),
                parsed=parsed,
                compact_input=compact,
                raw_path=str(raw_path),
            )
        )
    return rows


def attach_candidate_outcomes(calls: list[JudgeCall], audit_db: Path = AUDIT_DB) -> None:
    if not calls or not audit_db.exists():
        return
    start_date = min(row.session_date for row in calls)
    end_date = max(row.session_date for row in calls)
    con = sqlite3.connect(f"file:{audit_db}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        db_rows = con.execute(
            """
            SELECT r.candidate_key,r.market,r.session_date,r.ticker,r.known_at,r.price,
                   r.post_open_features_json,
                   MAX(CASE WHEN o.horizon_min=30 THEN o.return_pct END) outcome_30m_pct,
                   MAX(CASE WHEN o.horizon_min=60 THEN o.return_pct END) outcome_60m_pct,
                   MAX(CASE WHEN o.horizon_min=60 THEN o.source END) outcome_source,
                   MAX(CASE WHEN o.horizon_min=60 THEN o.status END) outcome_status
            FROM audit_candidate_rows r
            JOIN audit_candidate_outcomes o ON o.candidate_key=r.candidate_key
            WHERE r.session_date BETWEEN ? AND ? AND o.horizon_min IN (30,60)
            GROUP BY r.candidate_key,r.market,r.session_date,r.ticker,r.known_at,r.price,
                     r.post_open_features_json
            """,
            (start_date, end_date),
        ).fetchall()
    finally:
        con.close()
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for db_row in db_rows:
        item = dict(db_row)
        item["known_dt"] = _parse_dt(item.get("known_at"))
        try:
            features = json.loads(item.get("post_open_features_json") or "{}")
        except ValueError:
            features = {}
        item["feature_current"] = _as_float(features.get("current_price"))
        item["feature_known_dt"] = _parse_dt(features.get("known_at"))
        by_key[(str(item["market"]).upper(), str(item["session_date"]), str(item["ticker"]).upper())].append(item)

    for call in calls:
        candidates = by_key.get((call.market, call.session_date, call.ticker), [])
        feature_ts = _parse_dt(call.compact_input.get("feature_known_at")) or call.ts
        current = _as_float(call.compact_input.get("current_price"))
        scored: list[tuple[float, dict[str, Any], float]] = []
        for item in candidates:
            known = item.get("feature_known_dt") or item.get("known_dt")
            if known is None:
                continue
            delta = abs((known - feature_ts).total_seconds())
            if delta > 20 * 60:
                continue
            price = item.get("feature_current") or _as_float(item.get("price"))
            price_gap_pct = 0.0
            if current and price and current > 0:
                price_gap_pct = abs(price / current - 1.0) * 100.0
            # 1% 가격 차이는 5분 시차보다 큰 벌점. 동일 시점 중 입력 가격과 가까운 행 우선.
            score = delta + price_gap_pct * 300.0
            if item.get("outcome_60m_pct") is None:
                score += 600.0
            scored.append((score, item, delta))
        if not scored:
            continue
        _, chosen, delta = min(scored, key=lambda value: value[0])
        call.outcome_30m_pct = _as_float(chosen.get("outcome_30m_pct"))
        call.outcome_60m_pct = _as_float(chosen.get("outcome_60m_pct"))
        call.outcome_source = str(chosen.get("outcome_source") or "")
        call.outcome_status = str(chosen.get("outcome_status") or "")
        call.matched_candidate_key = str(chosen.get("candidate_key") or "")
        call.match_delta_sec = round(delta, 3)


_MINUTE_CACHE: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _minute_rows(market: str, ticker: str) -> list[dict[str, Any]]:
    cache_key = (market, ticker)
    if cache_key in _MINUTE_CACHE:
        return _MINUTE_CACHE[cache_key]
    path = ROOT / "data" / "price" / "minute" / market.lower() / f"{market.lower()}_{ticker}.csv"
    rows: list[dict[str, Any]] = []
    if path.exists():
        try:
            with path.open(encoding="utf-8-sig") as handle:
                for raw in csv.DictReader(handle):
                    ts = _parse_dt(raw.get("ts"))
                    open_price = _as_float(raw.get("open"))
                    high = _as_float(raw.get("high"))
                    low = _as_float(raw.get("low"))
                    close = _as_float(raw.get("close"))
                    if ts is None or None in (open_price, high, low, close):
                        continue
                    rows.append(
                        {
                            "ts": ts,
                            "open": open_price,
                            "high": high,
                            "low": low,
                            "close": close,
                            "source": raw.get("source") or "",
                        }
                    )
        except OSError:
            rows = []
    rows.sort(key=lambda row: row["ts"])
    _MINUTE_CACHE[cache_key] = rows
    return rows


def _session_end(call: JudgeCall) -> datetime:
    day = datetime.strptime(call.session_date, "%Y-%m-%d").replace(tzinfo=KST)
    if call.market == "US":
        return day + timedelta(days=1, hours=5)
    return day.replace(hour=15, minute=30)


def attach_local_minute_forward(calls: list[JudgeCall]) -> None:
    """실제 judge 응답 시각 이후 분봉으로 30/60분 경로를 다시 만든다."""

    for call in calls:
        bars = [
            row
            for row in _minute_rows(call.market, call.ticker)
            if call.ts <= row["ts"] <= _session_end(call)
        ]
        if not bars:
            continue
        entry_bar = bars[0]
        entry = _as_float(entry_bar.get("open")) or _as_float(entry_bar.get("close"))
        if not entry or entry <= 0:
            continue
        call.local_entry_price = entry
        call.local_data_source = str(entry_bar.get("source") or "local_minute")
        for minutes, attr in (
            (30, "local_forward_30m_pct"),
            (60, "local_forward_60m_pct"),
        ):
            target = call.ts + timedelta(minutes=minutes)
            target_bar = next((row for row in bars if row["ts"] >= target), None)
            if target_bar is not None:
                setattr(call, attr, (float(target_bar["close"]) / entry - 1.0) * 100.0)
        horizon_end = call.ts + timedelta(minutes=60)
        window = [row for row in bars if row["ts"] <= horizon_end]
        if window:
            call.local_mfe_60m_pct = (max(float(row["high"]) for row in window) / entry - 1.0) * 100.0
            call.local_mae_60m_pct = (min(float(row["low"]) for row in window) / entry - 1.0) * 100.0


def attach_pullback_plan_replay(calls: list[JudgeCall]) -> None:
    """PULLBACK_WAIT 존을 생성 시각 이후 분봉/일봉에만 통과시킨다."""

    try:
        from tools.rr_reject_causal_replay import replay
    except Exception:
        return
    for call in calls:
        if call.action != "PULLBACK_WAIT" or not call.valid:
            continue
        try:
            call.plan_replay = replay(
                call.market,
                call.ticker,
                call.session_date,
                call.ts,
                call.parsed,
            )
        except Exception:
            call.plan_replay = None


def _unique_first(calls: list[JudgeCall]) -> list[JudgeCall]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[JudgeCall] = []
    for row in sorted(calls, key=lambda item: item.ts):
        key = (row.session_date, row.market, row.ticker, row.action)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def summarize_judge(calls: list[JudgeCall]) -> dict[str, Any]:
    feature_ages = []
    price_conflicts = []
    reference_to_feature = 0
    reference_to_candidate = 0
    for row in calls:
        feature_ts = _parse_dt(row.compact_input.get("feature_known_at"))
        if feature_ts is not None:
            feature_ages.append((row.ts - feature_ts).total_seconds())
        conflict = _as_float(row.compact_input.get("candidate_price_vs_feature_pct"))
        if conflict is not None:
            price_conflicts.append(abs(conflict))
        reference = _as_float(row.parsed.get("reference_price"))
        feature_price = _as_float(row.compact_input.get("current_price"))
        candidate_price = _as_float(row.compact_input.get("candidate_price"))
        if reference and feature_price and candidate_price:
            if abs(reference - feature_price) <= abs(reference - candidate_price):
                reference_to_feature += 1
            else:
                reference_to_candidate += 1
    result: dict[str, Any] = {
        "raw_calls": len(calls),
        "valid": sum(1 for row in calls if row.valid),
        "matched_60m": sum(1 for row in calls if row.outcome_60m_pct is not None),
        "local_minute_matched_60m": sum(1 for row in calls if row.local_forward_60m_pct is not None),
        "input_freshness_and_price_contract": {
            "feature_age_available": len(feature_ages),
            "feature_age_median_sec": _round(_median(feature_ages), 1),
            "feature_age_gt_5m": sum(1 for value in feature_ages if value > 300),
            "feature_age_gt_15m": sum(1 for value in feature_ages if value > 900),
            "candidate_price_vs_feature_available": len(price_conflicts),
            "price_conflict_gt_0_5pct": sum(1 for value in price_conflicts if value > 0.5),
            "price_conflict_gt_2pct": sum(1 for value in price_conflicts if value > 2.0),
            "claude_reference_closer_to_feature": reference_to_feature,
            "claude_reference_closer_to_candidate_price": reference_to_candidate,
        },
        "by_market_action": {},
        "pullback_plan_causal_replay_unique_session_ticker": {},
    }
    grouped: dict[tuple[str, str], list[JudgeCall]] = defaultdict(list)
    for row in calls:
        grouped[(row.market, row.action)].append(row)
    for (market, action), group in sorted(grouped.items()):
        forward = [row.outcome_60m_pct for row in group if row.outcome_60m_pct is not None]
        local_forward = [
            row.local_forward_60m_pct for row in group if row.local_forward_60m_pct is not None
        ]
        unique_group = _unique_first(group)
        unique_local_forward = [
            row.local_forward_60m_pct
            for row in unique_group
            if row.local_forward_60m_pct is not None
        ]
        result["by_market_action"][f"{market}:{action}"] = {
            "n": len(group),
            "valid": sum(1 for row in group if row.valid),
            "matched_60m": len(forward),
            "mean_60m_gross_pct": _round(_mean(forward)),
            "median_60m_gross_pct": _round(_median(forward)),
            "positive_60m_rate": _round(
                sum(1 for value in forward if value is not None and value > 0) / len(forward)
                if forward
                else None
            ),
            "gt_0_5pct_60m_rate": _round(
                sum(1 for value in forward if value is not None and value > 0.5) / len(forward)
                if forward
                else None
            ),
            "local_minute_n": len(local_forward),
            "local_minute_mean_60m_gross_pct": _round(_mean(local_forward)),
            "local_minute_median_60m_gross_pct": _round(_median(local_forward)),
            "local_minute_positive_60m_rate": _round(
                sum(1 for value in local_forward if value is not None and value > 0)
                / len(local_forward)
                if local_forward
                else None
            ),
            "unique_session_ticker_n": len(unique_group),
            "unique_local_minute_n": len(unique_local_forward),
            "unique_local_minute_mean_60m_gross_pct": _round(_mean(unique_local_forward)),
            "unique_local_minute_median_60m_gross_pct": _round(_median(unique_local_forward)),
            "unique_local_minute_positive_60m_rate": _round(
                sum(1 for value in unique_local_forward if value is not None and value > 0)
                / len(unique_local_forward)
                if unique_local_forward
                else None
            ),
            "audit_outcome_status": dict(
                Counter(row.outcome_status for row in group if row.outcome_60m_pct is not None)
            ),
        }
    for market in ("KR", "US"):
        unique_pullbacks = _unique_first(
            [row for row in calls if row.market == market and row.action == "PULLBACK_WAIT" and row.valid]
        )
        replayed = [row.plan_replay for row in unique_pullbacks if isinstance(row.plan_replay, dict)]
        filled = [
            _as_float(item.get("net_pct"))
            for item in replayed
            if item.get("verdict") == "filled" and _as_float(item.get("net_pct")) is not None
        ]
        result["pullback_plan_causal_replay_unique_session_ticker"][market] = {
            "plans": len(unique_pullbacks),
            "replayed": len(replayed),
            "verdicts": dict(Counter(str(item.get("verdict") or "") for item in replayed)),
            "filled": len(filled),
            "mean_net_pct": _round(_mean(filled)),
            "median_net_pct": _round(_median(filled)),
            "win_rate": _round(
                sum(1 for value in filled if value is not None and value > 0) / len(filled)
                if filled
                else None
            ),
            "cost_contract": "KR 0.21% / US 0.50% round trip",
        }
    return result


def summarize_live_plans(ml_db: Path = ML_DB, start_date: str = "2026-07-01") -> dict[str, Any]:
    if not ml_db.exists():
        return {}
    con = sqlite3.connect(f"file:{ml_db}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT market,route,path_type,origin_action,
                   COUNT(*) n,SUM(filled) filled,SUM(closed) closed,
                   AVG(CASE WHEN closed=1 THEN pnl_pct_net END) avg_net,
                   SUM(CASE WHEN closed=1 AND pnl_pct_net>0 THEN 1 ELSE 0 END) wins
            FROM v2_canonical_performance
            WHERE runtime_mode='live' AND session_date>=?
              AND (origin_action<>'' OR path_type='claude_price')
            GROUP BY market,route,path_type,origin_action
            """,
            (start_date,),
        ).fetchall()
    finally:
        con.close()
    return {
        f"{row['market']}:{row['route']}:{row['path_type']}:{row['origin_action']}": {
            "n": int(row["n"] or 0),
            "filled": int(row["filled"] or 0),
            "closed": int(row["closed"] or 0),
            "avg_realized_net_pct": _round(_as_float(row["avg_net"])),
            "wins": int(row["wins"] or 0),
        }
        for row in rows
    }


def load_hold_decisions(start_date: str, end_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path_text in sorted(glob.glob(str(HOLD_DIR / "decisions_2026-*.jsonl"))):
        path = Path(path_text)
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            ts = _parse_dt(row.get("ts"))
            if ts is None:
                continue
            date = ts.date().isoformat()
            if not (start_date <= date <= end_date):
                continue
            row["_ts_dt"] = ts
            row["_source_path"] = str(path)
            rows.append(row)
    return sorted(rows, key=lambda row: row["_ts_dt"])


def _hold_mode(row: dict[str, Any]) -> str:
    triage = row.get("triage") if isinstance(row.get("triage"), dict) else {}
    if triage.get("hold_mode"):
        return str(triage.get("hold_mode"))
    for role in ("neutral", "bull", "bear", "triage", "challenge"):
        vote = (row.get("votes") or {}).get(role)
        if isinstance(vote, dict) and vote.get("hold_mode"):
            return str(vote.get("hold_mode"))
    return ""


def _triage_present(row: dict[str, Any]) -> bool:
    return isinstance(row.get("triage"), dict) and bool(row.get("triage"))


def summarize_hold(rows: list[dict[str, Any]]) -> dict[str, Any]:
    current = [row for row in rows if _triage_present(row)]
    outcomes = [row for row in rows if isinstance(row.get("outcome"), dict)]
    completeness = [
        _as_float((row.get("input_completeness") or {}).get("score"))
        for row in current
        if isinstance(row.get("input_completeness"), dict)
    ]
    category_driver = Counter()
    for row in current:
        triage = row.get("triage") or {}
        category_driver[f"{triage.get('exit_category','')}:{triage.get('exit_driver','')}"] += 1
    malformed_category_driver = sum(
        count
        for key, count in category_driver.items()
        if key.startswith("STOP_LOSS:") and key.split(":", 1)[1] in {"time_carry", "profit_protection"}
        or key.startswith("SELL:") and key.split(":", 1)[1] in {"loss_cap", "hard_stop", "failed_recovery", "invalid_if"}
    )
    return {
        "decisions": len(rows),
        "actions": dict(Counter(str(row.get("decision") or "") for row in rows)),
        "stages": dict(Counter(str(row.get("decision_stage") or "") for row in rows)),
        "triage_decisions": len(current),
        "triage_with_challenge_reason": sum(
            1 for row in current if str((row.get("triage") or {}).get("second_opinion_reason") or "")
        ),
        "fallbacks": sum(1 for row in rows if bool(row.get("fallback"))),
        "embedded_outcomes": len(outcomes),
        "embedded_outcome_by_action": dict(Counter(str(row.get("decision") or "") for row in outcomes)),
        "input_completeness_mean": _round(_mean(completeness)),
        "input_completeness_below_0_8": sum(1 for value in completeness if value is not None and value < 0.8),
        "category_driver": dict(category_driver),
        "category_driver_mismatch": malformed_category_driver,
        "negative_minutes_to_close": sum(
            1
            for row in rows
            if (_as_float((row.get("pathb_revenue_path_context") or {}).get("minutes_to_close")) or 0) < 0
        ),
    }


def load_hold_exit_counterfactuals(
    hold_rows: list[dict[str, Any]], ml_db: Path = ML_DB
) -> list[dict[str, Any]]:
    if not ml_db.exists():
        return []
    con = sqlite3.connect(f"file:{ml_db}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        db_rows = con.execute(
            """
            SELECT h.*,
                   p.pnl_pct actual_gross_from_performance,
                   p.pnl_pct_net actual_net_from_performance,
                   p.fee_pct_round_trip,
                   p.fx_change_pct
            FROM hold_advisor_exit_outcome h
            LEFT JOIN v2_learning_performance p ON p.path_run_id=h.path_run_id AND p.closed=1
            WHERE h.hold_fwd_net IS NOT NULL AND h.realized_net IS NOT NULL
            ORDER BY h.sell_ts
            """
        ).fetchall()
    finally:
        con.close()

    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in hold_rows:
        path_id = str((row.get("pathb_revenue_path_context") or {}).get("path_run_id") or "")
        if path_id:
            by_path[path_id].append(row)
    result: list[dict[str, Any]] = []
    for db_row in db_rows:
        item = dict(db_row)
        candidates = by_path.get(str(item.get("path_run_id") or ""), [])
        sell_ts = _parse_dt(item.get("sell_ts"))
        matched = None
        if candidates and sell_ts is not None:
            matched = min(candidates, key=lambda row: abs((row["_ts_dt"] - sell_ts).total_seconds()))
        item["decision_row"] = matched
        item["gross_sell_advantage_pct"] = _round(
            (_as_float(item.get("realized_net")) or 0.0) - (_as_float(item.get("hold_fwd_net")) or 0.0)
        )
        result.append(item)
    return result


def summarize_hold_counterfactual(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "n": len(rows),
        "matched_decision_rows": sum(1 for row in rows if row.get("decision_row")),
        "note": (
            "DB columns realized_net/hold_fwd_net are gross in current collector; "
            "hold is forced 3-session close, not bounded advisor HOLD policy."
        ),
        "by_market": {},
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("market") or "")].append(row)
    for market, group in sorted(grouped.items()):
        advantages = [_as_float(row.get("gross_sell_advantage_pct")) for row in group]
        result["by_market"][market] = {
            "n": len(group),
            "mean_realized_gross_pct": _round(_mean(_as_float(row.get("realized_net")) for row in group)),
            "mean_hold_3session_gross_pct": _round(_mean(_as_float(row.get("hold_fwd_net")) for row in group)),
            "mean_sell_advantage_gross_pct": _round(_mean(advantages)),
            "median_sell_advantage_gross_pct": _round(_median(advantages)),
            "sell_win_rate": _round(
                sum(1 for value in advantages if value is not None and value > 0) / len(advantages)
                if advantages
                else None
            ),
            "mean_actual_net_pct": _round(
                _mean(_as_float(row.get("actual_net_from_performance")) for row in group)
            ),
        }
    return result


def _compact_hold_input(row: dict[str, Any]) -> dict[str, Any]:
    context = row.get("advisor_context_v2") if isinstance(row.get("advisor_context_v2"), dict) else {}
    pathb = (
        row.get("pathb_revenue_path_context")
        if isinstance(row.get("pathb_revenue_path_context"), dict)
        else {}
    )
    return {
        "ts": row.get("ts"),
        "ticker": row.get("ticker"),
        "market": row.get("market"),
        "decision_stage": row.get("decision_stage"),
        "default_policy": row.get("default_policy"),
        "entry": _as_float(row.get("entry")),
        "current": _as_float(row.get("current")),
        "pnl_pct": _as_float(row.get("pnl_pct")),
        "tp_price": _as_float(row.get("tp_price")),
        "held_days": row.get("held_days"),
        "peak_giveback_shadow": row.get("peak_giveback_shadow") or {},
        "input_completeness": row.get("input_completeness") or {},
        "advisor_context_v2": {
            key: context.get(key)
            for key in (
                "selected_reason",
                "source_type",
                "entry_route",
                "hold_min_since_entry",
                "hard_stop_price",
                "hard_stop_distance_pct",
                "invalid_if",
                "selection_reference_target",
                "selection_reference_stop",
                "pathb_reference_target",
                "pathb_reference_stop",
                "or_formed",
                "or_high",
                "or_low",
                "entry_vs_or_high_pct",
                "entry_invalid_if",
                "plan_origin_action",
                "plan_confidence",
                "market_sharp_reversal_active",
                "rel_vol",
            )
            if context.get(key) not in (None, "")
        },
        "pathb_context": {
            key: pathb.get(key)
            for key in (
                "is_pathb",
                "origin_action",
                "exit_reason",
                "reference_target",
                "reference_stop",
                "profit_ladder_tier",
                "minutes_to_close",
            )
            if pathb.get(key) not in (None, "")
        },
    }


def build_blind_cases(
    judge_calls: list[JudgeCall],
    hold_rows: list[dict[str, Any]],
    hold_cf: list[dict[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    eligible_judge = [
        row
        for row in judge_calls
        if row.valid
        and row.outcome_60m_pct is not None
        and row.compact_input.get("current_price") is not None
    ]
    by_judge_bucket: dict[str, list[JudgeCall]] = defaultdict(list)
    for row in eligible_judge:
        bucket = f"{row.market}:{row.action}"
        by_judge_bucket[bucket].append(row)
    judge_target = max(1, count // 2)
    judge_selected: list[JudgeCall] = []
    # action/시장 다양성 우선, 결과값은 순위에 쓰지 않는다.
    for bucket in sorted(by_judge_bucket):
        ranked = sorted(by_judge_bucket[bucket], key=lambda row: _hash_rank(row.case_id))
        if ranked:
            judge_selected.append(ranked[0])
    if len(judge_selected) > judge_target:
        judge_selected = sorted(judge_selected, key=lambda row: _hash_rank("bucket|" + row.case_id))[:judge_target]
    elif len(judge_selected) < judge_target:
        selected_ids = {row.case_id for row in judge_selected}
        remaining = sorted(
            (row for row in eligible_judge if row.case_id not in selected_ids),
            key=lambda row: _hash_rank(row.case_id),
        )
        judge_selected.extend(remaining[: judge_target - len(judge_selected)])
    for row in judge_selected:
        cases.append(
            {
                "case_id": row.case_id,
                "kind": "BUY_JUDGE",
                "question": (
                    "이 시점에 BUY_READY, PULLBACK_WAIT, WAIT_RECHECK, REJECT 중 무엇이 적합한가? "
                    "PULLBACK_WAIT라면 구조적 존이 실제로 정의 가능한지도 판단."
                ),
                "input": row.compact_input,
            }
        )

    hold_target = max(1, count - len(cases))
    hold_candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for item in hold_cf:
        decision_row = item.get("decision_row")
        if not isinstance(decision_row, dict):
            continue
        case_id = (
            f"H-CF-{str(item.get('sell_date') or '').replace('-', '')}-"
            f"{str(item.get('market') or '')}-{str(item.get('ticker') or '')}-"
            f"{_hash_rank(str(item.get('sell_key') or ''))[:8]}"
        )
        hold_candidates.append((case_id, decision_row, {"source": "paired_sell_vs_3session_hold"}))
    # 실제 HOLD 결과가 붙은 레코드도 포함하되, 결과로 순위를 정하지 않는다.
    for row in hold_rows:
        if str(row.get("decision") or "").upper() not in {"HOLD", "TRAIL"}:
            continue
        if not isinstance(row.get("outcome"), dict):
            continue
        case_id = (
            f"H-LOG-{row['_ts_dt'].strftime('%Y%m%d-%H%M%S')}-"
            f"{str(row.get('market') or '')}-{str(row.get('ticker') or '')}"
        )
        hold_candidates.append((case_id, row, {"source": "embedded_hold_outcome"}))
    by_hold_market: dict[str, list[tuple[str, dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for item in hold_candidates:
        by_hold_market[str(item[1].get("market") or "")].append(item)
    hold_selected: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for market in sorted(by_hold_market):
        ranked = sorted(by_hold_market[market], key=lambda item: _hash_rank(item[0]))
        if ranked:
            hold_selected.append(ranked[0])
    selected_ids = {item[0] for item in hold_selected}
    remaining_hold = sorted(
        (item for item in hold_candidates if item[0] not in selected_ids),
        key=lambda item: _hash_rank(item[0]),
    )
    hold_selected.extend(remaining_hold[: max(0, hold_target - len(hold_selected))])
    for case_id, row, meta in hold_selected[:hold_target]:
        cases.append(
            {
                "case_id": case_id,
                "kind": "HOLD_ADVISOR",
                "question": (
                    "이 시점에 HOLD, SELL(비손절), STOP_LOSS 중 무엇이 적합한가? "
                    "HOLD라면 protective stop/무효조건/재검토 시간을 명시."
                ),
                "selection_source": meta["source"],
                "input": _compact_hold_input(row),
            }
        )
    return sorted(cases, key=lambda item: item["case_id"])


def reveal_cases(
    case_ids: set[str],
    judge_calls: list[JudgeCall],
    hold_rows: list[dict[str, Any]],
    hold_cf: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    judge_by_id = {row.case_id: row for row in judge_calls}
    for case_id in sorted(case_ids):
        if case_id in judge_by_id:
            row = judge_by_id[case_id]
            out.append(
                {
                    "case_id": case_id,
                    "kind": "BUY_JUDGE",
                    "claude": {
                        "action": row.action,
                        "route": row.route,
                        "confidence": row.confidence,
                        "reason": row.reason,
                        "plan": {
                            key: row.parsed.get(key)
                            for key in (
                                "buy_zone_low",
                                "buy_zone_high",
                                "sell_target",
                                "stop_loss",
                                "hold_days",
                                "invalid_if",
                            )
                            if row.parsed.get(key) not in (None, "")
                        },
                    },
                    "outcome": {
                        "forward_30m_gross_pct": row.outcome_30m_pct,
                        "forward_60m_gross_pct": row.outcome_60m_pct,
                        "source": row.outcome_source,
                        "status": row.outcome_status,
                        "match_delta_sec": row.match_delta_sec,
                        "local_minute_entry_price": row.local_entry_price,
                        "local_minute_forward_30m_pct": row.local_forward_30m_pct,
                        "local_minute_forward_60m_pct": row.local_forward_60m_pct,
                        "local_minute_mfe_60m_pct": row.local_mfe_60m_pct,
                        "local_minute_mae_60m_pct": row.local_mae_60m_pct,
                        "local_minute_source": row.local_data_source,
                    },
                }
            )
            continue

        matched = False
        for item in hold_cf:
            decision_row = item.get("decision_row")
            if not isinstance(decision_row, dict):
                continue
            expected = (
                f"H-CF-{str(item.get('sell_date') or '').replace('-', '')}-"
                f"{str(item.get('market') or '')}-{str(item.get('ticker') or '')}-"
                f"{_hash_rank(str(item.get('sell_key') or ''))[:8]}"
            )
            if expected != case_id:
                continue
            out.append(
                {
                    "case_id": case_id,
                    "kind": "HOLD_ADVISOR",
                    "claude": {
                        "action": decision_row.get("decision"),
                        "hold_mode": _hold_mode(decision_row),
                        "reason": next(
                            (
                                str(vote.get("reason") or "")
                                for vote in (decision_row.get("votes") or {}).values()
                                if isinstance(vote, dict) and vote.get("reason")
                            ),
                            "",
                        ),
                    },
                    "outcome": {
                        "actual_realized_gross_pct": item.get("realized_net"),
                        "actual_realized_net_pct": item.get("actual_net_from_performance"),
                        "forced_hold_3session_gross_pct": item.get("hold_fwd_net"),
                        "gross_sell_advantage_pct": item.get("gross_sell_advantage_pct"),
                        "regime": item.get("regime"),
                        "warning": (
                            "3-session forced HOLD is not the advisor's bounded HOLD policy; "
                            "gross fields are mislabeled *_net in the source table."
                        ),
                    },
                }
            )
            matched = True
            break
        if matched:
            continue
        for row in hold_rows:
            expected = (
                f"H-LOG-{row['_ts_dt'].strftime('%Y%m%d-%H%M%S')}-"
                f"{str(row.get('market') or '')}-{str(row.get('ticker') or '')}"
            )
            if expected != case_id:
                continue
            out.append(
                {
                    "case_id": case_id,
                    "kind": "HOLD_ADVISOR",
                    "claude": {
                        "action": row.get("decision"),
                        "hold_mode": _hold_mode(row),
                        "reason": next(
                            (
                                str(vote.get("reason") or "")
                                for vote in (row.get("votes") or {}).values()
                                if isinstance(vote, dict) and vote.get("reason")
                            ),
                            "",
                        ),
                    },
                    "outcome": row.get("outcome"),
                    "warning": (
                        "embedded outcome success is a bookkeeping label, not a paired "
                        "SELL-vs-HOLD causal comparison."
                    ),
                }
            )
            break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("summary", "blind", "reveal"), default="summary")
    parser.add_argument("--start-date", default="2026-06-01")
    parser.add_argument("--end-date", default="2026-07-23")
    parser.add_argument("--plan-start-date", default="2026-07-01")
    parser.add_argument("--blind-count", type=int, default=10)
    parser.add_argument("--case-ids", default="", help="comma-separated case IDs for reveal")
    parser.add_argument("--output", default="", help="optional JSON output path")
    args = parser.parse_args()

    judge_calls = load_judge_calls(args.start_date, args.end_date)
    attach_candidate_outcomes(judge_calls)
    attach_local_minute_forward(judge_calls)
    attach_pullback_plan_replay(judge_calls)
    hold_rows = load_hold_decisions(args.start_date, args.end_date)
    hold_cf = load_hold_exit_counterfactuals(hold_rows)

    if args.mode == "summary":
        payload: dict[str, Any] = {
            "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
            "api_calls_made": 0,
            "window": {"start_date": args.start_date, "end_date": args.end_date},
            "judge": summarize_judge(judge_calls),
            "live_judge_plan_performance": summarize_live_plans(start_date=args.plan_start_date),
            "hold_advisor": summarize_hold(hold_rows),
            "hold_advisor_paired_counterfactual": summarize_hold_counterfactual(hold_cf),
            "outcome_contract_findings": [
                "hold JSONL SELL success means realized pnl>0, not SELL outperforming HOLD.",
                "hold JSONL SELL hold_delta_pct is close pnl minus decision pnl, not a hold counterfactual.",
                "hold JSONL is patched only in today's file and only the latest unmatched ticker row.",
                "hold_advisor_exit_outcome realized_net is populated from pnl_pct (gross).",
                "hold_advisor_exit_outcome hold_fwd_net is entry-to-3-session-close gross, without bounded HOLD exits.",
                "audit_candidate_outcomes is candidate forward gross, not executable order net.",
            ],
        }
    elif args.mode == "blind":
        payload = {
            "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
            "api_calls_made": 0,
            "selection_rule": "stratified deterministic hash; outcome values never used for rank",
            "cases": build_blind_cases(
                judge_calls,
                hold_rows,
                hold_cf,
                max(2, int(args.blind_count)),
            ),
        }
    else:
        case_ids = {item.strip() for item in str(args.case_ids).split(",") if item.strip()}
        payload = {
            "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
            "api_calls_made": 0,
            "revealed": reveal_cases(case_ids, judge_calls, hold_rows, hold_cf),
        }

    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
