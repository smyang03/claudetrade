# -*- coding: utf-8 -*-
"""공모주 상장일 시초가 자동 매도 — 식별 규칙(오매도 방지). 네트워크·주문 없음."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import kr_ipo_autosell as A  # noqa: E402

IPO = [
    {"corp_name": "디티에스(구.동양티에스)", "listing_date": "2026-11-20", "offer_price": 18500.0, "spac": False},
    {"corp_name": "에스에프씨 (유가)", "listing_date": "2026-11-20", "offer_price": None, "spac": False},
    {"corp_name": "케이비제30호스팩", "listing_date": "2026-11-20", "offer_price": 2000.0, "spac": True},
    {"corp_name": "아델", "listing_date": "2026-12-02", "offer_price": None, "spac": False},
]


def test_norm_name_strips_parens_and_spaces():
    assert A.norm_name("디티에스(구.동양티에스)") == A.norm_name("디티에스")
    assert A.norm_name("에스에프씨 (유가)") == "에스에프씨"


def test_listings_exclude_spac_and_other_days():
    got = [r["corp_name"] for r in A.listings_on(date(2026, 11, 20), IPO)]
    assert got == ["디티에스(구.동양티에스)", "에스에프씨 (유가)"]


def test_match_requires_name_offer_price_and_not_other_lane_or_core():
    lst = A.listings_on(date(2026, 11, 20), IPO)
    stocks = [
        {"ticker": "111111", "name": "디티에스", "qty": 1, "avg_price": 18500},     # 공모주 균등 1주 → 판다
        {"ticker": "222222", "name": "에스에프씨", "qty": 2, "avg_price": 31000},  # 공모가 미상 → 이름만으로
        {"ticker": "333333", "name": "디티에스", "qty": 5, "avg_price": 25000},    # 공모가와 단가 다름(장내 매수) → 안 판다
        {"ticker": "275280", "name": "디티에스", "qty": 1, "avg_price": 18500},    # 코어 → 안 판다
        {"ticker": "444444", "name": "에스에프씨", "qty": 1, "avg_price": 31000},  # 다른 레인 보유 → 안 판다
        {"ticker": "555555", "name": "아델", "qty": 1, "avg_price": 9000},          # 상장일 아님
    ]
    got = {t["ticker"] for t in A.match_targets(stocks, lst, excluded={"444444"})}
    assert got == {"111111", "222222"}


def test_due_retries_only_past_listing_open_positions():
    rows = [{"kind": "position", "ticker": "111111", "status": "OPEN", "listing_date": "2026-11-19"},
            {"kind": "position", "ticker": "222222", "status": "OPEN", "listing_date": "2026-11-20"},
            {"kind": "position", "ticker": "333333", "status": "CLOSED", "listing_date": "2026-11-18"}]
    assert [p["ticker"] for p in A.due_retries(rows, date(2026, 11, 20))] == ["111111"]


def test_close_pnl_after_tax():
    assert A.close_pnl(10000, 20000, 1) == round(10000 - 30000 * A.FEE_RATE - 20000 * A.SELL_TAX)
