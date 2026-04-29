"""Critical flag evaluation for audit-lab reports.

This module only creates local flags and alert plans. It does not send
Telegram messages and does not call Claude.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CriticalFlag:
    code: str
    severity: str
    message: str
    metric: float | int | str | None = None
    threshold: float | int | str | None = None


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def evaluate_critical_flags(
    stats: dict,
    *,
    walk_forward_rows: list[dict] | None = None,
    min_trades: int = 30,
    decision_min_trades: int = 100,
    live_recent_stats: dict | None = None,
    long_term_stats: dict | None = None,
) -> list[dict]:
    flags: list[CriticalFlag] = []
    n_trades = int(stats.get("n_trades", 0) or 0)
    pf = _finite_number(stats.get("profit_factor"))
    max_dd = _finite_number(stats.get("max_drawdown_pct"))

    if n_trades < min_trades:
        flags.append(
            CriticalFlag(
                code="LOW_SAMPLE",
                severity="medium",
                message="거래 표본이 부족해 수익성 판단 신뢰도가 낮음",
                metric=n_trades,
                threshold=min_trades,
            )
        )
    if n_trades < decision_min_trades:
        flags.append(
            CriticalFlag(
                code="LOW_SAMPLE_DECISION_BLOCKED",
                severity="medium",
                message="거래 표본 100건 미만으로 실전 반영 판단 금지",
                metric=n_trades,
                threshold=decision_min_trades,
            )
        )
    if pf is not None and pf < 1.0:
        flags.append(
            CriticalFlag(
                code="COST_ADJUSTED_PF_BELOW_1",
                severity="critical",
                message="거래비용 반영 후 수익비가 1 미만",
                metric=round(pf, 4),
                threshold=1.0,
            )
        )
    if max_dd is not None and max_dd <= -25.0:
        flags.append(
            CriticalFlag(
                code="DEEP_DRAWDOWN",
                severity="critical",
                message="최대 낙폭이 허용 기준보다 큼",
                metric=round(max_dd, 4),
                threshold=-25.0,
            )
        )

    for row in walk_forward_rows or []:
        ratio = _finite_number(row.get("pf_ratio_test_to_train"))
        if ratio is not None and ratio < 0.70:
            flags.append(
                CriticalFlag(
                    code="WALK_FORWARD_DEGRADATION",
                    severity="critical",
                    message="검증 구간 PF가 학습 구간 대비 70% 미만",
                    metric=round(ratio, 4),
                    threshold=0.70,
                )
            )
            break

    recent_pf = _finite_number((live_recent_stats or {}).get("profit_factor"))
    long_pf = _finite_number((long_term_stats or {}).get("profit_factor"))
    if recent_pf is not None and long_pf is not None and long_pf > 0:
        divergence = abs(recent_pf - long_pf) / long_pf
        if divergence > 0.30:
            flags.append(
                CriticalFlag(
                    code="LIVE_BACKTEST_PF_DIVERGENCE",
                    severity="critical",
                    message="최근 실전 PF와 장기 백테스트 PF 괴리가 30% 초과",
                    metric=round(divergence * 100.0, 3),
                    threshold=30.0,
                )
            )

    critical_count = sum(1 for flag in flags if flag.severity == "critical")
    if critical_count >= 3:
        flags.append(
            CriticalFlag(
                code="CRITICAL_CLUSTER",
                severity="critical",
                message="critical 플래그가 3개 이상 동시에 발생",
                metric=critical_count,
                threshold=3,
            )
        )
    return [asdict(flag) for flag in flags]


def should_request_claude_audit(flags: list[dict]) -> bool:
    trigger_codes = {
        "COST_ADJUSTED_PF_BELOW_1",
        "WALK_FORWARD_DEGRADATION",
        "LIVE_BACKTEST_PF_DIVERGENCE",
        "CRITICAL_CLUSTER",
    }
    return any(flag.get("severity") == "critical" and flag.get("code") in trigger_codes for flag in flags)


def build_alert_plan(flags: list[dict]) -> dict:
    critical = [flag for flag in flags if flag.get("severity") == "critical"]
    return {
        "local_alert_required": bool(critical),
        "telegram_send_allowed": False,
        "claude_call_allowed": False,
        "claude_audit_candidate": should_request_claude_audit(flags),
        "human_review_required": bool(critical),
        "review_sla": "다음 거래일 시작 전 검토",
        "live_change_allowed": False if critical else True,
        "critical_count": len(critical),
        "messages": [flag.get("message", "") for flag in critical],
    }
