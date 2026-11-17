# -*- coding: utf-8 -*-
"""T1 hi52 사전등록(11-18) — 쉐도우 관측 필드와 판정 로직. 네트워크 없음."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "research"))

import research_t1_hi52_forward_20261118 as H  # noqa: E402
import us_earnings_t1_shadow as S  # noqa: E402


def _px(n, last=50.0, peak=100.0):
    idx = pd.bdate_range("2025-06-02", periods=n)
    c = np.linspace(peak, last, n)
    c[10] = peak
    return pd.DataFrame({"Close": c, "Volume": np.full(n, 1e6)}, index=idx)


def test_shadow_features_add_hi52_without_changing_model_inputs():
    px = _px(300)
    entry = px.index[-1] + pd.Timedelta(days=1)
    ev = {"eps_act": 1.0, "eps_est": 0.8, "date": str(entry.date())}
    hist = pd.DataFrame({"date": pd.to_datetime([]), "eps_act": [], "eps_est": []})
    f = S._features(ev, hist, px, entry)
    assert abs(f["_hi52"] - px.Close.iloc[-1] / px.Close.iloc[-252:].max()) < 1e-9
    assert f["_hi52_n"] == 252
    short = S._features(ev, hist, _px(80), _px(80).index[-1] + pd.Timedelta(days=1))
    assert short["_hi52_n"] == 80
    assert set(S.FEATS) <= set(f)                                      # 모델 입력 14개 그대로


def test_hi52_from_closes_uses_only_pre_entry():
    c = pd.Series([10.0, 20.0, 15.0, 99.0], index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]))
    v, n = H.hi52_from_closes(c, "2026-01-07")
    assert n == 3 and abs(v - 15 / 20) < 1e-9


def test_evaluate_verdicts(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "OUT", tmp_path)
    rows = []
    days = pd.bdate_range("2026-10-01", periods=120)
    rng = np.random.default_rng(0)
    for i, d in enumerate(days):
        s = str(d.date())
        for k, low in enumerate((True, False)):
            tk = f"T{i}{k}"
            rows.append({"kind": "signal", "selected_I1": True, "session": s, "ticker": tk,
                         "obs": {"hi52": 0.3 if low else 0.8}})
            rows.append({"kind": "settle", "hold": 20, "ticker": tk, "session": s,
                         "net_excess": (0.05 if low else 0.0) + rng.normal(0, 0.02)})
    r = H.evaluate(rows)
    assert r["n_low"] == 120 and r["diff"] > 0.04 and r["verdict"] == "합격"
    for x in rows:
        if x["kind"] == "settle" and x["ticker"].endswith("0"):
            x["net_excess"] -= 0.06
    assert H.evaluate(rows)["verdict"] == "기각"
    assert H.evaluate([])["verdict"].startswith("관측")
