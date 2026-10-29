# -*- coding: utf-8 -*-
"""운영 요약 계좌 총자산 — KR 보유 평가가 빠지던 결함(10-30) 회귀 방지."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import daily_ops_report as r  # noqa: E402


def test_total_includes_kr_holdings_and_uses_settled_cash():
    kr = {"cash": 965642, "d2_settlement_krw": 583188, "total_eval": 445705, "asset_total_krw": 1028893}
    us = {"kis_total_asset_krw": 4299889, "kis_domestic_cash_krw": 965642, "market_asset_krw": 3334247}
    kr["d1_settlement_krw"] = 878839
    assert r.account_total_krw(kr, us) == 445705 + 583188 + 3334247   # 10-29 23:43 실측 436.3만(KIS 값 430.0만이 아님)
    after_midnight = {**kr, "d2_settlement_krw": 878839, "asset_total_krw": 1324544}   # 자정 뒤 D+2 초기화
    assert r.account_total_krw(after_midnight, us) == 445705 + 878839 + 3334247      # 최솟값 — asset_total(132.5만)보다 보수적


def test_falls_back_to_kis_total_when_fields_missing():
    assert r.account_total_krw({}, {"kis_total_asset_krw": 4299889}) == 4299889
