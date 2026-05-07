from __future__ import annotations


PATHB_DECISION_EXIT_REASON_MAP = {
    "CLOSED_LOSS_CAP": "loss_cap",
    "CLOSED_HARD_STOP": "hard_stop",
    "CLOSED_CLAUDE_PRICE_STOP": "claude_price_stop",
    "CLOSED_CLAUDE_PRICE_TARGET": "target",
    "CLOSED_CLAUDE_PRICE_PRE_CLOSE": "pre_close",
    "CLOSED_POLICY_RECHECK": "policy_recheck",
    "CLOSED_SESSION_END": "session_end",
}


def normalize_pathb_decision_exit_reason(reason: str) -> str:
    normalized = str(reason or "").strip().upper()
    return PATHB_DECISION_EXIT_REASON_MAP.get(
        normalized,
        normalized.lower() or "pathb_closed",
    )
