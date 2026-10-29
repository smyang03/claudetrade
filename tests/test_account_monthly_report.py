# -*- coding: utf-8 -*-
"""월간 계좌 리포트 집계(10-30) — 브로커 행 합산 규칙."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import account_monthly_report as a  # noqa: E402


def test_month_range_and_summary():
    assert a.month_range("2026-10") == ("2026-10", "20261001", "20261031")
    kr = [{"pdno": "229200", "prdt_name": "KODEX 코스닥150", "rlzt_pfls": "-14226", "fee": "16", "tl_tax": "0", "sll_qty": "29"},
          {"pdno": "229200", "prdt_name": "KODEX 코스닥150", "rlzt_pfls": "-16", "fee": "16", "tl_tax": "0", "sll_qty": "0"},
          {"pdno": "030000", "prdt_name": "제일기획", "rlzt_pfls": "2505", "fee": "3", "tl_tax": "192", "sll_qty": "5"}]
    us = [{"ovrs_pdno": "APLD", "ovrs_item_name": "x", "ovrs_rlzt_pfls_amt": "-27339.5", "stck_sll_tlex": "1264.3"}]
    s = a.summarize_broker(kr, us)
    assert s["KR"] == {"realized": -11737, "cost": 227, "sells": 2}
    assert s["US"]["realized"] == -27340 and s["US"]["sells"] == 1
    assert abs(s["realized_total"] - (-14226 - 16 + 2505 - 27339.5)) <= 1
    assert s["pre_cost_total"] == round(-14226 - 16 + 2505 - 27339.5 + 227 + 1264.3)
    assert list(s["by_ticker"])[0].startswith("US APLD")                    # 손실 큰 순
