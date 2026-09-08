from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from tools import collect_independent_research_inputs as collect
from runtime.independent_research_strategies import evaluate


def frame():
    return pd.DataFrame({"Close": [100., 102., 105.], "Adj Close": [99., 101., 104.],
                         "Dividends": [1., 0., 0.], "Stock Splits": [0., 0., 0.]},
                        index=pd.to_datetime(["2026-09-03", "2026-09-04", "2026-09-08"]))


def test_adjusted_prices_observed_now_and_incomplete_bar_excluded():
    when = "2026-09-08T16:00:00+00:00"
    out = collect.normalize_prices(frame(), ["2026-09-03", "2026-09-04"], when)
    assert [r["adjusted_close"] for r in out["bars"]] == [99., 101.]
    assert all(r["known_at"] == when for r in out["bars"])
    assert out["bars"][0]["raw_close"] == 100
    assert out["bars"][0]["dividends"] == 1


@pytest.mark.parametrize("bad", ["missing_adj", "nan", "zero", "missing_session", "duplicate"])
def test_price_validation_rejects_incomplete_or_bad_provider_data(bad):
    f = frame()
    if bad == "missing_adj": f = f.drop(columns="Adj Close")
    elif bad == "nan": f.loc[f.index[0], "Adj Close"] = float("nan")
    elif bad == "zero": f.loc[f.index[0], "Close"] = 0
    elif bad == "missing_session": f = f.iloc[1:]
    elif bad == "duplicate": f = pd.concat([f, f.iloc[:1]])
    with pytest.raises(ValueError):
        collect.normalize_prices(f, ["2026-09-03", "2026-09-04"], "2026-09-08T16:00:00Z")


def test_archive_preserves_revisions_without_overwrite(tmp_path):
    first = collect.archive(tmp_path, {"observed_at": "first", "epsEstimate": 1})
    again = collect.archive(tmp_path, {"observed_at": "first", "epsEstimate": 1})
    second = collect.archive(tmp_path, {"observed_at": "later", "epsEstimate": 2})
    assert first == again and first != second
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_collect_failure_replaces_stale_success_without_exposing_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(collect, "ROOT", tmp_path)
    monkeypatch.setattr(collect, "calendar_context", lambda now: (["2026-09-03", "2026-09-04"], "2026-09-09"))
    monkeypatch.setattr(collect, "fetch_prices", lambda ticker: frame())
    def failed(now):
        raise RuntimeError("token=SHOULD_NOT_APPEAR")
    monkeypatch.setattr(collect, "fetch_fundamentals", failed)
    output = tmp_path / "input.json"
    output.write_text('{"old_success":true}', encoding="utf-8")
    snap = collect.collect(output, tmp_path / "archive")
    assert len(snap["prices"]) == 4
    assert snap["earnings"] == [] and snap["earnings_input_status"]["status"] == "BLOCKED"
    assert "SHOULD_NOT_APPEAR" not in output.read_text(encoding="utf-8")
    assert "old_success" not in json.loads(output.read_text(encoding="utf-8"))


def test_missing_guidance_is_blocked_not_no_candidate():
    now = datetime(2026, 9, 8, 16, tzinfo=timezone.utc)
    snap = {"schema_version": "independent_research_inputs_v1", "source": "fixture",
            "observed_at": now.isoformat(), "cutoff_at": now.isoformat(),
            "asof_session": "2026-09-04", "entry_session": "2026-09-09", "earnings": [],
            "earnings_input_status": {"status": "BLOCKED", "reason": "guidance_missing"}}
    result = evaluate(snap, now)["strategies"][1]
    assert result["status"] == "BLOCKED"
    assert result["rejections"] == [{"reason": "guidance_missing"}]
