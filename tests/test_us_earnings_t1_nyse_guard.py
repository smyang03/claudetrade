# -*- coding: utf-8 -*-
"""T1 NYSE LOO 한도 가드·거부 감지(11-16). YSS 11-13: NYSE가 NBBO +20% 넘는 한도를 거부. 네트워크·주문 없음."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import us_earnings_t1_live as T  # noqa: E402


def test_loo_limit_nyse_gapdown_uses_nbbo_guard():
    # YSS 재현: 전일 6.60 → 한도 8.25는 장전 5.85의 +41% → 거부됐다. 가드 후 5.85×1.15 = 6.73(+15%, 20% 안쪽)
    lim, rule = T.loo_limit(6.60, "NYSE", 5.85)
    assert rule == "nbbo_guard" and lim == round(5.85 * T.NYSE_NBBO_CAP, 2)
    assert lim / 5.85 - 1 < 0.20


def test_loo_limit_nyse_gapup_keeps_base_and_nasdaq_untouched():
    assert T.loo_limit(32.09, "NYSE", 36.0) == (round(32.09 * 1.25, 2), "base")      # KRMN 갭업: 기본이 더 낮음
    assert T.loo_limit(3.34, "NASD", 3.10) == (round(3.34 * 1.25, 2), "base")        # VRRM: NASDAQ은 가드 없음
    assert T.loo_limit(6.60, "NYSE", None) == (8.25, "nbbo_missing")                 # 호가 없으면 기본(기록만)
    assert T.loo_limit(6.60, "AMEX", 5.85)[1] == "nbbo_guard"


def test_reconcile_marks_reject_and_alerts(tmp_path, monkeypatch):
    led = tmp_path / "led.jsonl"
    led.write_text(json.dumps({"kind": "order", "session": "2026-11-13", "ticker": "YSS", "side": "buy", "qty": 15,
                               "limit": 8.25, "order_no": "0031109224", "success": True, "at": "2026-11-13T14:20:03+00:00"}) + "\n"
                   + json.dumps({"kind": "position", "ticker": "YSS", "status": "OPEN", "qty": 15}) + "\n", encoding="utf-8")
    monkeypatch.setattr(T, "LEDGER", led)
    monkeypatch.setattr(T, "_today_et", lambda: "2026-11-16")

    class _Kis:
        @staticmethod
        def get_order_fill_us(token, order_no, ticker, at):
            return {"filled_qty": 0, "fill_price": 8.25, "raw": {"prcs_stat_name": "거부",
                    "rjct_rson_name": "LimitPrice {8.25 > 40.78% Limit 20.00% thru nbbo } nbbo:5.85"}}

    sent = []
    monkeypatch.setattr(T, "_kis", lambda: (_Kis, "tok"))
    monkeypatch.setattr(T, "_telegram", lambda m: sent.append(m) or True)
    import types
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=lambda *a, **k: []))
    assert T.cmd_reconcile() == 0
    pos = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()][-1]
    assert pos["status"] == "CLOSED" and pos["close_reason"] == "loo_rejected" and "20.00%" in pos["reject_reason"]
    assert len(sent) == 1 and "YSS" in sent[0]
