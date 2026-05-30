from __future__ import annotations


FUTURE_LABEL_FIELDS = frozenset(
    {
        "forward_1d",
        "forward_3d",
        "forward_5d",
        "forward_30m_from_bucket",
        "forward_60m_from_bucket",
        "forward_close_from_bucket",
        "ret30",
        "ret60",
        "mfe30",
        "mfe60",
        "mae30",
        "mae60",
        "return_pct",
        "max_runup_pct",
        "max_drawdown_pct",
        "pnl_pct",
        "filled_count",
    }
)
