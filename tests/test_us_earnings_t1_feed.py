"""T1 채점 피드 수리(09-30): 스테일 캘린더 행 제거(B) · BMO 실적 Finnhub 대체(A). 네트워크 없음."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import us_earnings_t1_shadow as s  # noqa: E402


def _evt(tmp_path, rows):
    p = tmp_path / "evt.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


def test_candidates_drop_stale_rows_from_older_fetch(tmp_path):
    p = _evt(tmp_path, [
        {"ticker": "NG", "date": "2026-09-30", "hour": "BMO", "fetched_at": "2026-09-20T00:00:00+00:00"},   # 옛 예정일
        {"ticker": "NG", "date": "2026-10-08", "hour": "BMO", "fetched_at": "2026-09-29T00:00:00+00:00"},   # 이동된 예정일
        {"ticker": "CAG", "date": "2026-09-30", "hour": "BMO", "fetched_at": "2026-09-29T00:00:00+00:00"},
        {"ticker": "CNXC", "date": "2026-09-29", "hour": "AMC", "fetched_at": "2026-09-29T00:00:00+00:00"},
    ])
    got = s._candidates(pd.Timestamp("2026-09-30"), pd.Timestamp("2026-09-29"), path=p)
    assert set(got) == {("CAG", "2026-09-30"), ("CNXC", "2026-09-29")}


def test_finnhub_actual_used_with_yf_estimate():
    cal = {("FDS", "2026-09-30"): {"epsActual": 4.5, "epsEstimate": 4.3, "hour": "bmo"}}
    info, st = s._finnhub_actual("FDS", "2026-09-30", {"eps_est": 4.35}, cal, "no_actual")
    assert st == "ok" and info["eps_act"] == 4.5 and info["eps_est"] == 4.35
    assert info["act_source"] == "finnhub_cal" and info["hour"] == "BMO"


def test_finnhub_missing_or_conflict_stays_missed():
    info, st = s._finnhub_actual("CAG", "2026-09-30", {"eps_est": 0.5}, {}, "no_actual")
    assert info is None and st == "no_actual|fh_no_actual"
    cal = {("X", "2026-09-30"): {"epsActual": 0.1, "epsEstimate": -0.2}}
    info, st = s._finnhub_actual("X", "2026-09-30", {"eps_est": 0.3}, cal, "no_actual")
    assert info is None and st.endswith("feed_conflict")
    cal = {("Y", "2026-09-30"): {"epsActual": 0.1, "epsEstimate": 0.1}}
    info, st = s._finnhub_actual("Y", "2026-09-30", {"eps_est": None}, cal, "date_not_found")
    assert info is None and st.endswith("no_yf_est")


def test_finnhub_calendar_without_key_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(s, "ROOT", tmp_path)          # .env.live 없음 → 키 없음 → 네트워크 호출 없이 빈 dict
    assert s._finnhub_calendar(pd.Timestamp("2026-09-29"), pd.Timestamp("2026-09-30")) == {}
