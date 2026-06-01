from __future__ import annotations


PERMANENT_ORDER_FAILURE_MARKERS: tuple[str, ...] = (
    "해당종목정보가 없습니다",
    "거래정지종목",
    "주문가능금액을 초과",
    "주문가능금액 초과",
    "매수가능금액 부족",
    "증거금 부족",
    "insufficient buying power",
    "insufficient funds",
    "insufficient cash",
    "exchange mapping",
    "exchange code",
    "unsupported symbol",
    "symbol not found",
    "unknown exchange",
    "ovrs_excg_cd",
)


def is_permanent_order_failure(detail: str) -> bool:
    text = str(detail or "").lower()
    return any(marker.lower() in text for marker in PERMANENT_ORDER_FAILURE_MARKERS)


def broker_reject_reason(detail: str, *, default: str = "order_rejected") -> str:
    return "permanent_order_reject" if is_permanent_order_failure(detail) else default
