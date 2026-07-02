from __future__ import annotations

from typing import Any


QUALITY_VERSION = "active_lesson_quality.v1"

KNOWN_METRIC_KEYS = {
    "watch_only_missed_runup_ratio",
    "trade_ready_signal_conversion",
    "continuation_average_pnl",
    "unanimous_mismatch_count",
    "unanimous_override_count",
    "affordability_fail_count",
}

_SEVERITY_RANK = {"high": 3, "medium": 2, "info": 1, "low": 0}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fmt_pct(value: Any) -> str:
    return f"{_as_float(value):.1f}%"


def _base_fields(
    *,
    claude_actionable: bool,
    ops_flag: bool,
    action_hint: str,
    min_sample: int,
    target_prompt_scope: str = "selection",
) -> dict[str, Any]:
    return {
        "quality_version": QUALITY_VERSION,
        "claude_actionable": bool(claude_actionable),
        "ops_flag": bool(ops_flag),
        "action_hint": str(action_hint or ""),
        "min_sample": int(min_sample),
        "target_prompt_scope": str(target_prompt_scope or "selection"),
        "allowed_prompt_scopes": [str(target_prompt_scope or "selection")],
    }


def lesson_quality_fields(metric_key: str, scope: str, value: Any, sample: int) -> dict[str, Any]:
    key = str(metric_key or "").strip()
    scope_key = str(scope or "").strip().lower()

    if key == "watch_only_missed_runup_ratio":
        # 중립화(2026-07-01): max_runup_3d(peak/미실현) 기반 일방향 "적극 승격" nudge는
        # no-lookahead/생존편향 함정(못고른 승자 회수불가 확정). ops 진단용으로만 유지,
        # Claude 프롬프트 주입 차단(claude_actionable=False, hint 제거).
        return _base_fields(
            claude_actionable=False,
            ops_flag=True,
            action_hint="",
            min_sample=20,
        )
    if key == "trade_ready_signal_conversion":
        return _base_fields(
            claude_actionable=True,
            ops_flag=False,
            action_hint=(
                f"trade_ready 후보의 실제 signal_fired 전환율이 {_fmt_pct(value)}로 낮습니다. "
                "가격/거래량/섹터 확인이 부족하거나 weak veto가 있으면 trade_ready 대신 watch_only로 낮추세요."
            ),
            min_sample=20,
        )
    if key == "continuation_average_pnl":
        return _base_fields(claude_actionable=False, ops_flag=True, action_hint="", min_sample=5)
    if key in {"unanimous_mismatch_count", "unanimous_override_count"}:
        return _base_fields(claude_actionable=False, ops_flag=True, action_hint="", min_sample=1)
    if key == "affordability_fail_count":
        return _base_fields(claude_actionable=False, ops_flag=True, action_hint="", min_sample=2)
    if scope_key in {"execution", "consensus", "strategy"}:
        return _base_fields(claude_actionable=False, ops_flag=True, action_hint="", min_sample=max(1, _as_int(sample, 0)))
    return _base_fields(claude_actionable=False, ops_flag=True, action_hint="", min_sample=3)


def _evidence_score(candidate: dict[str, Any]) -> float:
    severity = str(candidate.get("severity") or "info").lower()
    confidence = _as_float(candidate.get("confidence"), 0.0)
    sample = _as_int(candidate.get("sample_count"), 0)
    return _SEVERITY_RANK.get(severity, 0) * 1000 + confidence * 100 + min(sample, 100)


def apply_lesson_conflict_guards(candidates: list[dict[str, Any]]) -> None:
    for candidate in candidates:
        candidate.pop("quality_conflict_suppressed", None)
        candidate.pop("quality_conflict_winner", None)
    by_metric = {
        str(candidate.get("metric_key") or ""): candidate
        for candidate in candidates
        if bool(candidate.get("breached"))
    }
    watch = by_metric.get("watch_only_missed_runup_ratio")
    trade_ready = by_metric.get("trade_ready_signal_conversion")
    if not watch or not trade_ready:
        return
    winner, loser = (watch, trade_ready)
    if _evidence_score(trade_ready) > _evidence_score(watch):
        winner, loser = trade_ready, watch
    loser["claude_actionable"] = False
    loser["ops_flag"] = True
    loser["action_hint"] = ""
    loser["quality_conflict_suppressed"] = True
    loser["quality_conflict_winner"] = str(winner.get("metric_key") or "")
