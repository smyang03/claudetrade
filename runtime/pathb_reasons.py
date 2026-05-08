from __future__ import annotations


PATHB_DECISION_EXIT_REASON_MAP = {
    "CLOSED_LOSS_CAP": "loss_cap",
    "CLOSED_HARD_STOP": "hard_stop",
    "CLOSED_CLAUDE_PRICE_STOP": "claude_price_stop",
    "CLOSED_CLAUDE_PRICE_TARGET": "target",
    "CLOSED_CLAUDE_PRICE_PRE_CLOSE": "pre_close",
    "CLOSED_MFE_BREAKEVEN": "mfe_breakeven",
    "CLOSED_POLICY_RECHECK": "policy_recheck",
    "CLOSED_SESSION_END": "session_end",
}

PATHB_AUTO_CLOSE_REASON_PRIORITY = [
    "CLOSED_HARD_STOP",
    "CLOSED_LOSS_CAP",
    "CLOSED_CLAUDE_PRICE_STOP",
    "CLOSED_MFE_BREAKEVEN",
    "CLOSED_TRAILING_STOP",
    "CLOSED_CLAUDE_PRICE_PRE_CLOSE",
    "CLOSED_CLAUDE_PRICE_TARGET",
    "CLOSED_TARGET",
    "CLOSED_TAKE_PROFIT",
    "CLOSED_TIME_EXIT",
    "CLOSED_SESSION_END",
    "CLOSED_UNKNOWN",
]
PATHB_AUTO_CLOSE_REASON_PRIORITY_RANK = {
    reason: rank for rank, reason in enumerate(PATHB_AUTO_CLOSE_REASON_PRIORITY)
}
PATHB_MANUAL_CLOSE_REASONS = {"CLOSED_USER_MANUAL", "USER_MANUAL", "MANUAL"}

ORDER_UNKNOWN_SOFT_TIMEOUT_SEC_DEFAULT = 90
ORDER_UNKNOWN_HARD_TIMEOUT_SEC_DEFAULT = 300
ORDER_UNKNOWN_MIN_RECONCILE_ATTEMPTS_DEFAULT = 2


def normalize_pathb_decision_exit_reason(reason: str) -> str:
    normalized = str(reason or "").strip().upper()
    return PATHB_DECISION_EXIT_REASON_MAP.get(
        normalized,
        normalized.lower() or "pathb_closed",
    )


def pathb_close_reason_priority(reason: str) -> int:
    normalized = str(reason or "").strip().upper()
    return PATHB_AUTO_CLOSE_REASON_PRIORITY_RANK.get(normalized, len(PATHB_AUTO_CLOSE_REASON_PRIORITY))


def choose_primary_pathb_close_reason(reasons: list[str] | tuple[str, ...]) -> str:
    cleaned = [str(reason or "").strip().upper() for reason in reasons if str(reason or "").strip()]
    if not cleaned:
        return "CLOSED_UNKNOWN"
    manual = [reason for reason in cleaned if reason in PATHB_MANUAL_CLOSE_REASONS]
    if manual:
        return "CLOSED_USER_MANUAL"
    return sorted(cleaned, key=pathb_close_reason_priority)[0]
