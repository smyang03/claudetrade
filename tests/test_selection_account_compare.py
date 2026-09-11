"""Account comparison regressions: literal cash flows, not trade-mean returns."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
from dataclasses import replace
from datetime import date, timedelta

import pytest


def test_comparison_tool_is_available():
    assert importlib.util.find_spec("tools.selection_account_compare") is not None


@pytest.fixture
def sc():
    name = "tools.selection_account_compare"
    assert importlib.util.find_spec(name) is not None, "account comparison tool is missing"
    return importlib.import_module(name)


DAYS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
        "2026-09-07", "2026-09-08", "2026-09-09"]


def candidate(sc, ticker="A", start=0, closes=None, rank=100, ibs=60, disc=-5):
    closes = closes or [100] * (7 - start)
    win = [(day, 100, max(100, close), min(100, close), close, 1000)
           for day, close in zip(DAYS[start:], closes)]
    return sc.Candidate(ticker, "2026-08-31" if start == 0 else DAYS[start - 1],
                        DAYS[start], rank, win, ibs, disc)


def config(sc, **kwargs):
    return sc.Config(market="KR", capital=1000, daily_budget=500, **kwargs)


def test_open_loss_is_in_nav_and_both_halves_of_cost_are_reserved(sc):
    c = candidate(sc, closes=[90])
    result = sc.simulate([c], DAYS[:1], config(sc), "baseline")
    # Four shares: cash 599.5, gross mark 360, reserved exit fee .5.
    assert result["nav"] == pytest.approx(959)
    assert result["return_pct"] == pytest.approx(-4.1)
    assert result["open_count"] == 1
    assert result["closed_count"] == 0
    assert result["trades"][0]["net_pct"] == pytest.approx(-10.25)


def test_cash_constraint_and_no_reuse_of_today_close_proceeds(sc):
    a = candidate(sc, closes=[100, 120])
    b = candidate(sc, "B", start=1)
    cfg = sc.Config(market="KR", capital=100.125, daily_budget=100.125)
    result = sc.simulate([a, b], DAYS[:2], cfg, "baseline")
    assert len(result["trades"]) == 1
    assert result["skips"]["unaffordable"] == 1
    assert result["nav"] == pytest.approx(111.875)
    assert min(p["cash"] for p in result["equity"]) >= 0


def test_same_ticker_is_not_bought_twice_while_open(sc):
    result = sc.simulate([candidate(sc), candidate(sc, start=1)], DAYS[:2], config(sc), "baseline")
    assert len(result["trades"]) == 1
    assert result["skips"]["already_held"] == 1


def test_basket_uses_same_daily_budget_when_only_one_candidate(sc):
    cs = [candidate(sc)]
    a = sc.simulate(cs, DAYS, config(sc), "baseline")
    b = sc.simulate(cs, DAYS, config(sc), "random3", seed=4)
    assert a["nav"] == b["nav"]
    assert a["entry_notional"] == b["entry_notional"] == 400


def test_ranking_does_not_substitute_cheaper_stock_after_unaffordable_pick(sc):
    expensive = replace(candidate(sc), win=[(DAYS[0], 2000, 2000, 2000, 2000, 10)])
    cheap = candidate(sc, "B", rank=50)
    result = sc.simulate([expensive, cheap], DAYS[:1], config(sc), "baseline")
    assert result["trades"] == []
    assert result["skips"]["unaffordable"] == 1


def test_risk_filter_missing_features_fails_closed_and_is_diagnostic(sc):
    cs = [candidate(sc, ibs=None), candidate(sc, "B", ibs=70, disc=-20)]
    result = sc.simulate(cs, DAYS, config(sc), "risk")
    assert result["trades"] == []
    assert result["skips"]["risk_rejected"] == 2


def test_random_seed_is_reproducible_and_input_order_independent(sc):
    cs = [candidate(sc, str(i), rank=i) for i in range(8)]
    a = sc.simulate(cs, DAYS, config(sc), "random1", seed=3)
    b = sc.simulate(list(reversed(cs)), DAYS, config(sc), "random1", seed=3)
    assert a == b


def test_matched_days_control_cannot_buy_on_other_days(sc):
    cs = [candidate(sc), candidate(sc, "B", start=1)]
    result = sc.simulate(cs, DAYS, config(sc), "random1", allowed_days={DAYS[1]})
    assert [t["entry_date"] for t in result["trades"]] == [DAYS[1]]


def test_closed_early_winner_is_not_a_mature_cohort_alone(sc):
    result = sc.simulate([candidate(sc, closes=[120])], DAYS[:1], config(sc), "baseline")
    assert result["closed_count"] == 1
    assert result["mature_cohort_count"] == 0


def test_entry_day_high_cannot_trigger_take_profit(sc):
    c = replace(candidate(sc), win=[(DAYS[0], 100, 150, 99, 101, 10)])
    result = sc.simulate([c], DAYS[:1], config(sc), "baseline")
    assert result["open_count"] == 1
    assert result["trades"][0]["net_pct"] == pytest.approx(.75)


def test_d7_is_seven_sessions_inclusive(sc):
    result = sc.simulate([candidate(sc)], DAYS, config(sc), "baseline")
    assert result["trades"][0]["exit_date"] == DAYS[6]
    assert result["trades"][0]["exit_reason"] == "D_MAT"
    assert result["nav"] == pytest.approx(999)


def test_us_break_even_uses_previous_peak_not_same_day_high(sc):
    win = [(DAYS[0], 100, 100, 100, 100, 10),
           (DAYS[1], 100, 105, 99, 99, 10),
           (DAYS[2], 99, 101, 98, 99, 10)]
    c = replace(candidate(sc), win=win)
    cfg = sc.Config(market="US", capital=1000, daily_budget=500, fx=1)
    result = sc.simulate([c], DAYS[:3], cfg, "baseline")
    assert result["trades"][0]["exit_date"] == DAYS[2]
    assert result["trades"][0]["net_pct"] == pytest.approx(-1.5)


def test_stale_open_mark_is_visible_and_never_claimed_current(sc):
    result = sc.simulate([candidate(sc, closes=[90])], DAYS[:3], config(sc), "baseline")
    assert result["stale_open_count"] == 1
    assert result["trades"][0]["mark_date"] == DAYS[0]
    assert result["nav"] == pytest.approx(959)


def test_duplicate_candidate_fails_instead_of_double_weighting_random(sc):
    with pytest.raises(ValueError, match="duplicate"):
        sc.simulate([candidate(sc), candidate(sc)], DAYS, config(sc), "random1")


def test_asof_cannot_see_future_exit(sc):
    result = sc.simulate([candidate(sc, closes=[100, 120])], DAYS[:1], config(sc), "baseline")
    assert result["closed_count"] == 0
    assert result["nav"] == pytest.approx(999)


@pytest.mark.parametrize("capital,budget,fx", [(0, 100, 1), (100, -1, 1), (100, 10, 0)])
def test_invalid_capital_parameters_are_rejected(sc, capital, budget, fx):
    with pytest.raises(ValueError):
        sc.Config(market="US", capital=capital, daily_budget=budget, fx=fx)


def test_report_never_promotes_retrospective_or_loss_making_result(sc):
    report = sc.compare([candidate(sc)], DAYS, config(sc), repeats=10, include_risk=True)
    assert report["decision"]["status"] == "RESEARCH_ONLY"
    assert report["decision"]["forward_eligible_trades"] == 0
    assert report["decision"]["live_authorized"] is False
    assert "random1_on_risk_days" in report["strategies"]


def test_snapshot_round_trip_and_refuse_overwrite(sc, tmp_path):
    cs = [candidate(sc)]
    cfg = config(sc)
    report = sc.compare(cs, DAYS, cfg, repeats=3)
    directory = tmp_path / "run"
    sc.save_run(directory, cs, DAYS, cfg, {"counts": {}}, report)
    restored, calendar, restored_cfg, audit = sc.read_snapshot(directory / "inputs.json")
    assert sc.compare(restored, calendar, restored_cfg, repeats=3) == report
    assert json.loads((directory / "report.json").read_text())["decision"]["live_authorized"] is False
    with pytest.raises(FileExistsError):
        sc.save_run(directory, cs, DAYS, cfg, audit, report)


def test_kr_entry_uses_market_next_session_not_next_available_stock_bar(sc):
    assert sc.entry_date("KR", DAYS[0], DAYS) == DAYS[1]
    assert sc.entry_date("US", DAYS[0], DAYS) == DAYS[0]
    assert sc.entry_date("KR", DAYS[-1], DAYS) is None


def test_features_use_only_prior_day_and_reject_short_history(sc):
    bars = [(f"2026-08-{i:02}", 100, 110, 90, 100, 1000) for i in range(1, 23)]
    bars += [(DAYS[0], 100, 999, 1, 900, 10)]
    f = sc.prior_features(bars, DAYS[0])
    assert f["ibs"] == 50
    assert f["ma20_disc"] == 0
    assert f["dvol"] == 100000
    assert sc.prior_features(bars[:2], DAYS[0]) is None


def test_sql_reader_is_readonly_and_missing_database_not_created(sc, tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        sc.read_rows(missing, "SELECT 1")
    assert not missing.exists()
    db = tmp_path / "source.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE t (x INTEGER)")
    with pytest.raises(sqlite3.OperationalError):
        sc.read_rows(db, "INSERT INTO t VALUES (1)")


def build_source(tmp_path, market="KR"):
    import csv
    price_dir = tmp_path / "data" / "price" / market.lower()
    price_dir.mkdir(parents=True)
    history = [(date(2026, 7, 1) + timedelta(days=i)).isoformat() for i in range(24)]
    sessions = history + DAYS
    def prices(ticker, days):
        with (price_dir / f"{market.lower()}_{ticker}.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "open", "high", "low", "close", "volume"])
            writer.writerows((d, 100, 110, 90, 100, 2_000_000) for d in days)
    prices("069500" if market == "KR" else "SPY", sessions)
    prices("000001" if market == "KR" else "AAA", sessions)
    return history, prices


def test_loader_missing_kr_entry_is_not_shifted_to_next_stock_bar(sc, tmp_path):
    history, prices = build_source(tmp_path)
    prices("000001", history + DAYS[1:])
    db = tmp_path / "data" / "shadow" / "virtual_books.db"
    db.parent.mkdir()
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE trades(strategy_id TEXT,session_date TEXT,ticker TEXT,backfill INTEGER)")
        con.execute("INSERT INTO trades VALUES('xkr_fallen3',?,'000001',0)", (history[-1],))
    before = db.read_bytes()
    cs, calendar, audit = sc.load_dataset(tmp_path, "KR", DAYS[0], DAYS[-1])
    assert cs[0].entry_date == DAYS[0]
    assert cs[0].win == []
    assert audit["counts"]["missing_entry_bar"] == 1
    assert audit["coverage_complete"] is False
    result = sc.simulate(cs, calendar, config(sc), "baseline")
    assert result["trades"] == []
    assert db.read_bytes() == before


def test_loader_missing_candidate_does_not_silently_report_complete(sc, tmp_path):
    history, _ = build_source(tmp_path)
    db = tmp_path / "data" / "shadow" / "virtual_books.db"
    db.parent.mkdir()
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE trades(strategy_id TEXT,session_date TEXT,ticker TEXT,backfill INTEGER)")
        con.execute("INSERT INTO trades VALUES('xkr_fallen3',?,'999999',0)", (history[-1],))
    cs, _, audit = sc.load_dataset(tmp_path, "KR", DAYS[0], DAYS[-1])
    assert cs == []
    assert audit["counts"]["source_rows"] == 1
    assert audit["counts"]["missing_or_invalid_price"] == 1
    assert audit["issues"][0]["ticker"] == "999999"
    assert audit["forward_eligible_trades"] == 0


def test_price_reader_ignores_future_rows_but_rejects_invalid_past_ohlc(sc, tmp_path):
    p = tmp_path / "prices.csv"
    p.write_text("date,open,high,low,close,volume\n2026-09-01,100,110,90,100,10\n2026-09-02,bad,row\n")
    assert len(sc.read_bars(p, DAYS[0])) == 1
    p.write_text("date,open,high,low,close,volume\n2026-09-01,100,0,90,100,10\n")
    with pytest.raises(ValueError):
        sc.read_bars(p, DAYS[0])


def test_input_snapshot_tampering_is_detected(sc, tmp_path):
    cs, cfg = [candidate(sc)], config(sc)
    out = tmp_path / "run"
    sc.save_run(out, cs, DAYS, cfg, {"counts": {}}, sc.compare(cs, DAYS, cfg, repeats=2))
    path = out / "inputs.json"
    data = json.loads(path.read_text())
    data["payload"]["config"]["capital"] = 2_000_000
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="hash"):
        sc.read_snapshot(path)


def test_nav_reconciles_closed_and_open_pnl_for_basket(sc):
    cs = [candidate(sc, closes=[120]), candidate(sc, "B", closes=[80])]
    result = sc.simulate(cs, DAYS[:1], config(sc), "random3")
    assert result["closed_count"] == result["open_count"] == 1
    assert result["nav"] - 1000 == pytest.approx(result["closed_pnl"] + result["open_pnl"])


def test_snapshot_cli_replays_same_report_and_no_source_db(sc, tmp_path, capsys):
    cs, cfg = [candidate(sc)], config(sc)
    audit = {"counts": {}, "coverage_complete": True, "notes": []}
    report = sc.compare(cs, DAYS, cfg, repeats=2)
    report["audit"] = audit
    original, replay = tmp_path / "original", tmp_path / "replay"
    sc.save_run(original, cs, DAYS, cfg, audit, report)
    assert sc.main(["--snapshot", str(original / "inputs.json"), "--repeats", "2",
                    "--output-dir", str(replay)]) == 0
    assert json.loads((original / "report.json").read_text()) == json.loads((replay / "report.json").read_text())
    assert "RESEARCH ONLY" in capsys.readouterr().out


def kr_db(tmp_path, history, tickers):
    db = tmp_path / "data" / "shadow" / "virtual_books.db"
    db.parent.mkdir()
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE trades(strategy_id TEXT,session_date TEXT,ticker TEXT,backfill INTEGER)")
        con.executemany("INSERT INTO trades VALUES('xkr_fallen3',?,?,0)", [(history[-1], t) for t in tickers])
    return db


def test_bad_future_bar_does_not_remove_top_pick_before_entry(sc, tmp_path):
    history, prices = build_source(tmp_path)
    prices("000002", history + DAYS)
    path = tmp_path / "data" / "price" / "kr" / "kr_000001.csv"
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace(f"{DAYS[2]},100,110,90,100", f"{DAYS[2]},100,0,90,100")
    path.write_text(text, encoding="utf-8-sig")
    kr_db(tmp_path, history, ["000001", "000002"])
    cs, calendar, audit = sc.load_dataset(tmp_path, "KR", DAYS[0], DAYS[-1])
    assert len(cs) == 2
    result = sc.simulate(cs, calendar, config(sc), "baseline")
    assert result["trades"][0]["ticker"] == "000001"
    assert result["trades"][0]["mark_date"] == DAYS[1]
    assert result["stale_open_count"] == 1
    assert audit["counts"]["price_path_gap"] == 1


def test_absent_source_sessions_cannot_claim_complete_coverage(sc, tmp_path):
    history, _ = build_source(tmp_path)
    kr_db(tmp_path, history, [])
    _, _, audit = sc.load_dataset(tmp_path, "KR", DAYS[0], DAYS[-1])
    assert audit["coverage_complete"] is False
    assert audit["source_sessions_unverified"] == DAYS


def test_feature_window_gap_is_not_silently_treated_as_daily_return(sc, tmp_path):
    history, prices = build_source(tmp_path)
    prices("000001", history[:10] + history[11:] + DAYS)
    kr_db(tmp_path, history, ["000001"])
    cs, _, audit = sc.load_dataset(tmp_path, "KR", DAYS[0], DAYS[-1])
    assert cs == []
    assert audit["counts"]["feature_window_gap"] == 1
