from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from runtime import independent_research_strategies as research

NOW = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)


def snapshot():
    dates = []
    day = datetime(2026, 8, 31)
    while len(dates) < 253:
        if day.weekday() < 5:
            dates.append(day.date().isoformat())
        day -= timedelta(days=1)
    dates.reverse()
    bars = [{"session": d, "known_at": d + "T21:00:00+00:00",
             "adjusted_close": 100 + i * .2 + (i % 2) * .05} for i, d in enumerate(dates)]
    event = {"event_id": "TEST:2026Q2", "ticker": "TEST", "published_at": "2026-08-31T11:00:00Z",
             "estimate_observed_at": "2026-08-30T10:00:00Z", "actual_observed_at": "2026-08-31T12:00:00Z",
             "source_document": "fixture://earnings", "eps_basis": "comparable_recurring_diluted",
             "eps_actual": 1.2, "eps_estimate": 1, "revenue_actual": 105, "revenue_estimate": 100,
             "previous_guidance_observed_at": "2026-08-01T10:00:00Z", "guidance_observed_at": "2026-08-31T12:00:00Z",
             "guidance_period": "FY2026", "previous_guidance_period": "FY2026",
             "guidance_metric": "revenue_USD", "previous_guidance_metric": "revenue_USD",
             "guidance_mid": 110, "previous_guidance_mid": 100,
             "reaction_bar": {"session": "2026-08-31", "closed_at": "2026-08-31T20:00:00Z",
                              "open": 100, "close": 102, "previous_close": 99, "volume": 200,
                              "prior20_mean_volume": 100, "prior20_mean_dollar_volume": 30_000_000}}
    return {"schema_version": "independent_research_inputs_v1", "source": "test-fixture",
            "observed_at": "2026-09-01T09:00:00Z", "cutoff_at": "2026-08-31T21:00:00Z",
            "asof_session": "2026-08-31", "entry_session": "2026-09-01",
            "prices": {t: {"price_basis": "split_and_distribution_adjusted", "bars": deepcopy(bars)}
                       for t in research.CONTRACTS["r_multiasset_trend_v1"]["universe"]},
            "earnings": [event]}


def result(s, index=0):
    return research.evaluate(s, NOW)["strategies"][index]


def test_both_rules_generate_research_only():
    out = research.evaluate(snapshot(), NOW)
    assert out["authority"] == "RESEARCH_ONLY" and out["live_eligible"] is False
    assert [s["status"] for s in out["strategies"]] == ["RESEARCH_TARGET", "RESEARCH_CANDIDATE"]
    for row in out["strategies"]:
        assert row["live_eligible"] is False and row["execution_validation"] == "NOT_IMPLEMENTED"


def test_missing_input_blocks_both():
    assert all(s["status"] == "BLOCKED" for s in research.evaluate({}, NOW)["strategies"])


@pytest.mark.parametrize("field,value", [("observed_at", "2026-09-02T10:00:00Z"),
    ("cutoff_at", "2026-08-20T10:00:00Z"), ("observed_at", "2026-09-01T09:00:00"),
    ("entry_session", "2026-08-31"), ("schema_version", "other")])
def test_snapshot_time_and_schema_fail_closed(field, value):
    s = snapshot(); s[field] = value
    assert result(s)["status"] == "BLOCKED"


@pytest.mark.parametrize("defect", ["missing", "basis", "short", "future", "nan", "duplicate"])
def test_trend_bad_data_blocks_whole_allocation(defect):
    s = snapshot(); item = s["prices"]["GLD"]
    if defect == "missing": del s["prices"]["GLD"]
    elif defect == "basis": item["price_basis"] = "raw_close"
    elif defect == "short": item["bars"] = item["bars"][-200:]
    elif defect == "future": item["bars"][-1]["known_at"] = "2026-09-02T10:00:00Z"
    elif defect == "nan": item["bars"][-1]["adjusted_close"] = float("nan")
    elif defect == "duplicate": item["bars"][-1]["session"] = item["bars"][-2]["session"]
    assert result(s)["status"] == "BLOCKED"
    assert result(s, 1)["status"] == "RESEARCH_CANDIDATE"


def test_trend_failed_asset_stays_cash_not_redistributed():
    s = snapshot()
    for i, bar in enumerate(s["prices"]["GLD"]["bars"]):
        bar["adjusted_close"] = 200 - i * .2 + (i % 2) * .05
    r = result(s)
    assert r["target_weights"]["GLD"] == 0
    assert max(r["target_weights"].values()) <= .25
    assert r["cash_weight"] >= .25


def test_midmonth_is_diagnostics_not_rebalance():
    s = snapshot(); s["entry_session"] = "2026-08-31"; s["asof_session"] = "2026-08-28"
    # use a consistent as-of history without making a 252-bar truncated input
    for item in s["prices"].values():
        for bar in item["bars"]:
            bar["session"] = (datetime.fromisoformat(bar["session"]) - timedelta(days=3)).date().isoformat()
    assert result(s)["status"] == "OBSERVING"
    assert not result(s)["signals"]


@pytest.mark.parametrize("field,value", [
    ("estimate_observed_at", "2026-08-31T13:00:00Z"), ("eps_actual", None),
    ("eps_estimate", -1), ("revenue_actual", 100), ("eps_basis", "unknown"),
    ("guidance_mid", 100), ("guidance_period", "FY2027"), ("source_document", ""),
    ("actual_observed_at", "2026-09-02T10:00:00Z"),
])
def test_earnings_does_not_substitute_price_gap_for_fundamentals(field, value):
    s = snapshot(); s["earnings"][0][field] = value
    assert result(s, 1)["signals"] == []


def test_earnings_deterministic_k1_and_duplicates():
    s = snapshot(); second = deepcopy(s["earnings"][0])
    second.update(event_id="AAA:2026Q2", ticker="AAA")
    s["earnings"] += [second, deepcopy(second)]
    r = result(s, 1)
    assert len(r["signals"]) == 1 and r["signals"][0]["ticker"] == "AAA"
    assert r["candidate_count"] == 2 and len(r["rejections"]) == 1


def test_contract_hash_changes_with_contract(monkeypatch):
    sid = "r_multiasset_trend_v1"; old = research.fingerprint(sid)
    monkeypatch.setitem(research.CONTRACTS[sid], "max_asset_weight", .1)
    assert old != research.fingerprint(sid)
