import concurrent.futures
import json
import sqlite3

import pytest

import runtime.selection_shadow_book as shadow_book_module
from runtime.selection_shadow_book import SelectionShadowBook, read_report


def snapshot(status="READY", candidates=None, completed_at="2026-09-10T09:04:01+09:00"):
    return {
        "market": "KR", "session_date": "2026-09-10", "signal_date": "2026-09-09",
        "collected_at": "2026-09-10T09:04:00+09:00", "completed_at": completed_at,
        "status": status,
        "candidates": candidates if candidates is not None else [
            {"ticker": "005930", "dvol": 3_000_000_000.0, "features": {}}
        ],
        "source_rows": [], "excluded": [], "provenance": {},
    }


def clock(now="2026-09-10T09:06:00+09:00", session_dates=None):
    return {
        "market": "KR", "session_date": "2026-09-10", "now": now,
        "open_at": "2026-09-10T09:00:00+09:00", "close_at": "2026-09-10T15:30:00+09:00",
        "session_dates": session_dates or ["2026-09-09", "2026-09-10"],
    }


def quote(price=100_000, price_at="2026-09-10T09:05:59+09:00",
          requested_at="2026-09-10T09:05:58+09:00", session_date="2026-09-10"):
    return {"price": price, "price_at": price_at, "requested_at": requested_at,
            "received_at": "2026-09-10T09:06:00+09:00", "session_date": session_date,
            "source": "fixture", "price_kind": "LAST_PRICE_PAPER"}


def prepared_book(tmp_path, candidates=None):
    book = SelectionShadowBook(tmp_path / "book.db")
    book.record_snapshot(snapshot(candidates=candidates))
    book.decide(clock("2026-09-10T09:05:57+09:00"))
    return book


def test_three_rules_have_independent_cash_positions_and_fee_math(tmp_path):
    book = prepared_book(tmp_path)
    result = book.tick(clock(), {"005930": quote()})
    assert len(result["fills"]) == 3
    report = read_report(tmp_path / "book.db", clock()["now"])
    assert len(report["positions"]) == 3
    assert {(p["rule"], p["qty"]) for p in report["positions"]} == {
        ("baseline_k1", 5), ("random_k1", 5), ("random_k3", 5)}
    for account in report["accounts"]:
        assert account["cash"] == pytest.approx(3_819_375)
        assert account["nav"] == pytest.approx(4_318_750)
    assert sum(p["entry_fee"] for p in report["positions"]) == pytest.approx(1_875)


def test_snapshot_success_is_immutable_but_failure_can_retry_and_empty_is_distinct(tmp_path):
    book = SelectionShadowBook(tmp_path / "book.db")
    assert book.record_snapshot(snapshot(status="FAILED"))["status"] == "FAILED"
    assert book.record_snapshot(snapshot(status="EMPTY", candidates=[]))["status"] == "EMPTY"
    locked = book.record_snapshot(snapshot(candidates=[{"ticker": "OTHER", "dvol": 9, "features": {}}]))
    assert locked["status"] == "EMPTY"
    assert book.decide(clock()) == []
    assert read_report(tmp_path / "book.db")["markets"][0]["snapshot_status"] == "EMPTY"


def test_decisions_are_deterministic_order_independent_and_expensive_picks_not_substituted(tmp_path):
    candidates = [{"ticker": x, "dvol": d, "features": {}} for x, d in
                  [("B", 20), ("A", 20), ("C", 10)]]
    selected = []
    for index, rows in enumerate((candidates, list(reversed(candidates)))):
        book = SelectionShadowBook(tmp_path / f"b{index}.db")
        book.record_snapshot(snapshot(candidates=rows))
        selected.append([(x["rule"], x["ticker"], x["allocation"]) for x in book.decide(clock())])
    assert selected[0] == selected[1]
    assert ("baseline_k1", "A", 540_000.0) in selected[0]
    book = prepared_book(tmp_path / "expensive", [{"ticker": "X", "dvol": 10, "features": {}},
                                                   {"ticker": "Y", "dvol": 9, "features": {}}])
    book.tick(clock(), {"X": quote(600_000), "Y": quote(1)})
    report = read_report(tmp_path / "expensive" / "book.db")
    baseline = [i for i in report["intents"] if i["rule"] == "baseline_k1"][0]
    assert baseline["ticker"] == "X" and baseline["status"] == "TOO_EXPENSIVE"


@pytest.mark.parametrize("bad_quote", [
    quote(price=-1), quote(price_at="2026-09-10T09:00:00+09:00"),
    quote(price_at="2026-09-10T09:07:00+09:00"), quote(requested_at="2026-09-10T09:03:00+09:00"),
    quote(session_date="2026-09-09"),
])
def test_bad_stale_future_or_predecision_quote_fails_closed(tmp_path, bad_quote):
    book = prepared_book(tmp_path)
    result = book.tick(clock(), {"005930": bad_quote})
    assert not result["fills"]
    assert result["errors"]


def test_naive_clock_rejected_and_entry_cutoff_never_backfills(tmp_path):
    book = prepared_book(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        book.tick(clock("2026-09-10T09:06:00"), {"005930": quote()})
    result = book.tick(clock("2026-09-10T09:46:00+09:00"), {"005930": quote(
        price_at="2026-09-10T09:45:59+09:00", requested_at="2026-09-10T09:45:58+09:00")})
    assert not result["fills"]
    assert {i["status"] for i in read_report(tmp_path / "book.db")["intents"]} == {"ENTRY_WINDOW_MISSED"}


def test_duplicate_and_concurrent_ticks_fill_each_intent_once(tmp_path):
    book = prepared_book(tmp_path)
    args = (clock(), {"005930": quote()})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: book.tick(*args), range(2)))
    book.tick(*args)
    report = read_report(tmp_path / "book.db")
    assert len(report["positions"]) == 3
    assert all(a["cash"] == pytest.approx(3_819_375) for a in report["accounts"])


def test_fill_transaction_rolls_back_when_position_insert_aborts(tmp_path):
    book = prepared_book(tmp_path)
    with sqlite3.connect(tmp_path / "book.db") as db:
        db.execute("CREATE TRIGGER abort_open BEFORE INSERT ON positions BEGIN SELECT RAISE(ABORT, 'boom'); END")
    with pytest.raises(sqlite3.IntegrityError, match="boom"):
        book.tick(clock(), {"005930": quote()})
    report = read_report(tmp_path / "book.db")
    assert not report["positions"]
    assert all(a["cash"] == 4_320_000 for a in report["accounts"])
    assert not [e for e in report.get("events", []) if e["type"] == "OPEN"]


def test_report_absent_is_read_only(tmp_path):
    path = tmp_path / "missing" / "book.db"
    report = read_report(path)
    assert report["available"] is False
    assert not path.exists() and not path.parent.exists()


def test_same_day_close_proceeds_do_not_fund_frozen_allocations(tmp_path):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    book.tick(clock("2026-09-10T10:00:00+09:00"), {"005930": quote(
        112_000, "2026-09-10T09:59:59+09:00", "2026-09-10T09:59:58+09:00")})
    # The day's frozen decisions remain the only intents despite sale proceeds.
    assert len(read_report(tmp_path / "book.db")["intents"]) == 3


def test_prior_session_peak_required_for_us_be_and_d7_waits_for_fresh_quote(tmp_path):
    book = SelectionShadowBook(tmp_path / "book.db")
    s = snapshot(candidates=[{"ticker": "ABC", "dvol": 10, "features": {}}])
    s["market"] = "US"
    s["collected_at"] = "2026-09-10T09:04:00-04:00"; s["completed_at"] = "2026-09-10T09:04:01-04:00"
    c = clock("2026-09-10T09:06:00-04:00"); c.update(market="US", open_at="2026-09-10T09:00:00-04:00", close_at="2026-09-10T15:30:00-04:00")
    book.record_snapshot(s); book.decide(dict(c, now="2026-09-10T09:05:57-04:00"))
    q = quote(100, "2026-09-10T09:05:59-04:00", "2026-09-10T09:05:58-04:00"); q["received_at"]="2026-09-10T09:06:00-04:00"
    book.tick(c, {"ABC": q})
    # Same-day +4% cannot arm break-even.
    noon = dict(c, now="2026-09-10T12:00:00-04:00")
    high = quote(104, "2026-09-10T11:59:59-04:00", "2026-09-10T11:59:58-04:00"); high["received_at"]=noon["now"]
    book.tick(noon, {"ABC": high})
    close = dict(c, now="2026-09-10T15:20:00-04:00")
    low = quote(99, "2026-09-10T15:19:59-04:00", "2026-09-10T15:19:58-04:00"); low["received_at"]=close["now"]
    book.tick(close, {"ABC": low})
    assert len(read_report(tmp_path / "book.db")["positions"]) == 3


def test_reconciliation_error_blocks_entries_but_is_reported(tmp_path):
    book = prepared_book(tmp_path)
    with sqlite3.connect(tmp_path / "book.db") as db:
        db.execute("UPDATE accounts SET cash = cash - 1 WHERE rule='baseline_k1'")
    result = book.tick(clock(), {"005930": quote()})
    assert result["errors"]
    report = read_report(tmp_path / "book.db")
    assert report["errors"] and len(report["positions"]) == 0


def test_d7_missing_quote_becomes_pending_then_exits_on_next_fresh_regular_quote(tmp_path):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    seven = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15",
             "2026-09-16", "2026-09-17", "2026-09-18"]
    due = {"market": "KR", "session_date": "2026-09-18", "now": "2026-09-18T15:20:00+09:00",
           "open_at": "2026-09-18T09:00:00+09:00", "close_at": "2026-09-18T15:30:00+09:00",
           "session_dates": seven}
    result = book.tick(due, {})
    assert not result["exits"]
    assert {p["pending_reason"] for p in read_report(tmp_path / "book.db")["positions"]} == {"EXIT_PENDING_QUOTE"}
    delayed = dict(due, session_date="2026-09-21", now="2026-09-21T09:01:00+09:00",
                   open_at="2026-09-21T09:00:00+09:00", close_at="2026-09-21T15:30:00+09:00",
                   session_dates=seven + ["2026-09-21"])
    q = quote(101_000, "2026-09-21T09:00:59+09:00", "2026-09-21T09:00:58+09:00", "2026-09-21")
    q["received_at"] = delayed["now"]
    result = book.tick(delayed, {"005930": q})
    assert {e["reason"] for e in result["exits"]} == {"D7"}
    assert {c["delayed"] for c in read_report(tmp_path / "book.db")["closed"]} == {1}


def test_closed_early_cohort_becomes_mature_after_seven_verified_sessions(tmp_path):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    tp_clock = clock("2026-09-10T10:00:00+09:00")
    tp = quote(112_000, "2026-09-10T09:59:59+09:00", "2026-09-10T09:59:58+09:00")
    tp["received_at"] = tp_clock["now"]
    book.tick(tp_clock, {"005930": tp})
    assert all(a["mature_count"] == 0 for a in read_report(tmp_path / "book.db")["accounts"])
    sessions = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15",
                "2026-09-16", "2026-09-17", "2026-09-18"]
    observation = {"market": "KR", "session_date": "2026-09-18", "now": "2026-09-18T12:00:00+09:00",
                   "open_at": "2026-09-18T09:00:00+09:00", "close_at": "2026-09-18T15:30:00+09:00",
                   "session_dates": sessions}
    book.tick(observation, {})
    assert all(a["mature_count"] == 0 for a in read_report(tmp_path / "book.db")["accounts"])
    book.tick(dict(observation, now="2026-09-18T15:30:00+09:00"), {})
    assert all(a["mature_count"] == 1 for a in read_report(tmp_path / "book.db")["accounts"])


def test_report_uses_persisted_nav_history_for_drawdown_and_quote_age_staleness(tmp_path):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    down_clock = clock("2026-09-10T10:00:00+09:00")
    down = quote(90_000, "2026-09-10T09:59:59+09:00", "2026-09-10T09:59:58+09:00")
    down["received_at"] = down_clock["now"]
    book.tick(down_clock, {"005930": down})
    fresh = read_report(tmp_path / "book.db", "2026-09-10T10:00:30+09:00")
    for account in fresh["accounts"]:
        assert account["nav"] == pytest.approx(4_268_750)
        assert account["mdd_pct"] == pytest.approx(-1.18634259259)
        assert account["stale_count"] == 0
    stale = read_report(tmp_path / "book.db", "2026-09-10T10:01:01+09:00")
    assert all(account["stale_count"] == 1 for account in stale["accounts"])
    with sqlite3.connect(tmp_path / "book.db") as db:
        assert db.execute("SELECT COUNT(*) FROM account_valuations").fetchone()[0] >= 9


def test_report_exposes_status_and_heartbeat_without_snapshot_or_fill(tmp_path):
    book = SelectionShadowBook(tmp_path / "book.db")
    book.record_status("KR", "2026-09-10", "NO_VERIFIED_QUOTE",
                       "2026-09-10T09:06:00+09:00", {"ticker": "005930"})
    book.record_status("KR", "2026-09-10", "HEARTBEAT",
                       "2026-09-10T09:06:15+09:00", {"phase": "tick"})
    report = read_report(tmp_path / "book.db", "2026-09-10T09:06:20+09:00")
    assert report["last_updated"] == "2026-09-10T09:06:15+09:00"
    assert report["markets"] == [{
        "market": "KR", "session_date": "2026-09-10", "snapshot_status": "MISSING",
        "snapshot_completed_at": None, "execution_status": "BLOCKED",
        "heartbeat_at": "2026-09-10T09:06:15+09:00",
        "latest_status": "NO_VERIFIED_QUOTE", "latest_status_at": "2026-09-10T09:06:00+09:00",
        "latest_status_details": {"ticker": "005930"},
    }]
    assert report["errors"][0]["status"] == "NO_VERIFIED_QUOTE"


def test_position_identity_is_not_reused_after_close(tmp_path):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    tp_clock = clock("2026-09-10T10:00:00+09:00")
    tp = quote(112_000, "2026-09-10T09:59:59+09:00", "2026-09-10T09:59:58+09:00")
    tp["received_at"] = tp_clock["now"]
    book.tick(tp_clock, {"005930": tp})
    s = snapshot(candidates=[{"ticker": "000660", "dvol": 5, "features": {}}],
                 completed_at="2026-09-11T09:04:01+09:00")
    s.update(session_date="2026-09-11", signal_date="2026-09-10",
             collected_at="2026-09-11T09:04:00+09:00")
    c = dict(clock("2026-09-11T09:05:57+09:00"), session_date="2026-09-11",
             open_at="2026-09-11T09:00:00+09:00", close_at="2026-09-11T15:30:00+09:00",
             session_dates=["2026-09-10", "2026-09-11"])
    book.record_snapshot(s); book.decide(c)
    q = quote(100_000, "2026-09-11T09:05:59+09:00", "2026-09-11T09:05:58+09:00", "2026-09-11")
    q["received_at"] = "2026-09-11T09:06:00+09:00"
    book.tick(dict(c, now=q["received_at"]), {"000660": q})
    with sqlite3.connect(tmp_path / "book.db") as db:
        ids = [r[0] for r in db.execute("SELECT position_id FROM closed_positions UNION SELECT id FROM positions")]
    assert len(ids) == len(set(ids)) == 6


def test_decision_freezes_available_slots_and_pre_exit_cash(tmp_path):
    book = SelectionShadowBook(tmp_path / "book.db")
    book.record_snapshot(snapshot())
    with sqlite3.connect(tmp_path / "book.db") as db:
        db.execute("INSERT INTO accounts VALUES('KR','baseline_k1',4320000,100000,4320000)")
        for n in range(7):
            intent = db.execute("INSERT INTO intents(market,session_date,rule,ticker,allocation,decided_at,seed,status) VALUES('KR',?, 'baseline_k1',?,1,?,'seed','FILLED')",
                                (f"2026-09-0{n+1}", f"H{n}", f"2026-09-0{n+1}T09:06:00+09:00")).lastrowid
            db.execute("INSERT INTO events(event_key,type,market,rule,ticker,intent_id,at,amount,details) VALUES(?, 'OPEN','KR','baseline_k1',?,?,?,0,?)",
                       (f"open:{intent}", f"H{n}", intent, f"2026-09-0{n+1}T09:06:00+09:00", _event_details(1)))
            db.execute("INSERT INTO positions(intent_id,market,rule,ticker,entry_session,entry_at,entry_price,qty,fx,entry_cost,entry_fee,last_price,last_price_at,prior_peak_price) VALUES(?,'KR','baseline_k1',?,?,?,1,1,1,1,0,1,?,1)",
                       (intent, f"H{n}", f"2026-09-0{n+1}", f"2026-09-0{n+1}T09:06:00+09:00", f"2026-09-0{n+1}T09:06:00+09:00"))
    decisions = book.decide(clock("2026-09-10T09:05:57+09:00"))
    assert not [d for d in decisions if d["rule"] == "baseline_k1"]
    # A different uncapped rule freezes the cash it actually has, not future sale proceeds.
    with sqlite3.connect(tmp_path / "book.db") as db:
        db.execute("UPDATE accounts SET cash=100000 WHERE market='KR' AND rule='random_k1'")
        db.execute("DELETE FROM decisions WHERE market='KR' AND session_date='2026-09-10' AND rule='random_k1'")
        db.execute("DELETE FROM intents WHERE market='KR' AND session_date='2026-09-10' AND rule='random_k1'")
    frozen = [d for d in book.decide(clock("2026-09-10T09:05:58+09:00")) if d["rule"] == "random_k1"]
    assert frozen[0]["allocation"] == 100_000


def test_late_decision_excludes_same_day_close_proceeds_from_frozen_budget(tmp_path):
    book = SelectionShadowBook(tmp_path / "book.db")
    book.record_snapshot(snapshot())
    with sqlite3.connect(tmp_path / "book.db") as db:
        db.execute("INSERT INTO accounts VALUES('KR','baseline_k1',4320000,600000,4320000)")
        db.execute("INSERT INTO events(event_key,type,market,rule,ticker,intent_id,at,amount,details) "
                   "VALUES('close:prior','CLOSE','KR','baseline_k1','OLD',NULL,"
                   "'2026-09-10T09:03:00+09:00',500000,'{}')")
    selected = [d for d in book.decide(clock("2026-09-10T09:05:57+09:00"))
                if d["rule"] == "baseline_k1"]
    assert selected[0]["allocation"] == 100_000


def _event_details(qty):
    return json.dumps({"qty": qty, "quote": {"source": "fixture"}})


def test_exit_requires_regular_session_observation(tmp_path):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    after = clock("2026-09-10T16:00:00+09:00")
    q = quote(120_000, "2026-09-10T15:59:59+09:00", "2026-09-10T15:59:58+09:00")
    q["received_at"] = after["now"]
    assert not book.tick(after, {"005930": q})["exits"]
    early = clock("2026-09-10T09:00:30+09:00")
    premarket = quote(120_000, "2026-09-10T08:59:59+09:00", "2026-09-10T09:00:00+09:00")
    premarket["received_at"] = early["now"]
    assert not book.tick(early, {"005930": premarket})["exits"]


def test_fill_rechecks_slot_cap_after_frozen_decision(tmp_path, monkeypatch):
    book = prepared_book(tmp_path)
    monkeypatch.setitem(shadow_book_module.RULE_SLOTS, "baseline_k1", 0)
    book.tick(clock(), {"005930": quote()})
    baseline = [i for i in read_report(tmp_path / "book.db")["intents"]
                if i["rule"] == "baseline_k1"]
    assert baseline[0]["status"] == "SLOT_LIMIT"


def test_entry_rejects_price_observed_before_decision_even_if_requested_after(tmp_path):
    book = prepared_book(tmp_path)
    q = quote(100_000, "2026-09-10T09:05:56+09:00", "2026-09-10T09:05:58+09:00")
    result = book.tick(clock(), {"005930": q})
    assert not result["fills"]
    assert all(i["status"] == "PENDING" for i in read_report(tmp_path / "book.db")["intents"])


def test_worker_downtime_past_d7_exits_and_records_entry_relative_schedule(tmp_path):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    dates = ["2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15",
             "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]
    c = {"market": "KR", "session_date": "2026-09-21", "now": "2026-09-21T09:01:00+09:00",
         "open_at": "2026-09-21T09:00:00+09:00", "close_at": "2026-09-21T15:30:00+09:00", "session_dates": dates}
    q = quote(101_000, "2026-09-21T09:00:59+09:00", "2026-09-21T09:00:58+09:00", "2026-09-21")
    q["received_at"] = c["now"]
    assert {x["reason"] for x in book.tick(c, {"005930": q})["exits"]} == {"D7"}
    details = [json.loads(x["details"]) for x in read_report(tmp_path / "book.db")["closed"]]
    assert {x["scheduled_session"] for x in details} == {"2026-09-18"}


def test_concurrent_successful_snapshot_writers_observe_same_winner(tmp_path):
    book = SelectionShadowBook(tmp_path / "book.db")
    rows = [snapshot(candidates=[{"ticker": ticker, "dvol": 1, "features": {}}]) for ticker in ("A", "B")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(book.record_snapshot, rows))
    assert results[0] == results[1]


def test_structural_reconciliation_blocks_missing_position_and_changed_quantity(tmp_path):
    for name, mutation in (("missing", "DELETE FROM positions WHERE rule='baseline_k1'"),
                           ("qty", "UPDATE positions SET qty=qty+1 WHERE rule='baseline_k1'")):
        book = prepared_book(tmp_path / name)
        book.tick(clock(), {"005930": quote()})
        with sqlite3.connect(tmp_path / name / "book.db") as db:
            db.execute(mutation)
            db.execute("UPDATE intents SET status='PENDING' WHERE rule='random_k3'")
        result = book.tick(clock("2026-09-10T09:07:00+09:00"), {})
        assert any(e["code"] == "RECONCILIATION_ERROR" for e in result["errors"])


@pytest.mark.parametrize("mutation", [
    "UPDATE closed_positions SET qty=qty+1 WHERE rule='baseline_k1'",
    "UPDATE closed_positions SET ticker='CORRUPT' WHERE rule='baseline_k1'",
    "UPDATE closed_positions SET rule='corrupt' WHERE rule='baseline_k1'",
    "UPDATE closed_positions SET market='US' WHERE rule='baseline_k1'",
    "UPDATE events SET ticker='CORRUPT' WHERE event_key='open:1'",
    "UPDATE events SET ticker='CORRUPT' WHERE event_key='close:1'",
    "UPDATE events SET rule='corrupt' WHERE event_key='close:1'",
    "UPDATE events SET market='US' WHERE event_key='close:1'",
    "UPDATE events SET intent_id=999999 WHERE event_key='close:1'",
])
def test_reconciliation_rejects_closed_position_and_event_identity_or_quantity_mismatch(tmp_path, mutation):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    tp_clock = clock("2026-09-10T10:00:00+09:00")
    tp = quote(120_000, "2026-09-10T09:59:59+09:00", "2026-09-10T09:59:58+09:00")
    tp["received_at"] = tp_clock["now"]
    book.tick(tp_clock, {"005930": tp})
    with sqlite3.connect(tmp_path / "book.db") as db:
        db.execute(mutation)
    result = book.tick(clock("2026-09-10T10:01:00+09:00"), {})
    assert any(error["code"] == "RECONCILIATION_ERROR" for error in result["errors"])


@pytest.mark.parametrize("event_key", ["open:1", "close:1"])
@pytest.mark.parametrize("corrupt_qty", [999, 5.5, "5", True, None])
def test_close_event_records_quantity_and_reconciliation_rejects_its_mutation(tmp_path, event_key, corrupt_qty):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    tp_clock = clock("2026-09-10T10:00:00+09:00")
    tp = quote(120_000, "2026-09-10T09:59:59+09:00", "2026-09-10T09:59:58+09:00")
    tp["received_at"] = tp_clock["now"]
    book.tick(tp_clock, {"005930": tp})
    with sqlite3.connect(tmp_path / "book.db") as db:
        details = json.loads(db.execute("SELECT details FROM events WHERE event_key='close:1'").fetchone()[0])
        assert details["qty"] == 5
        details = json.loads(db.execute("SELECT details FROM events WHERE event_key=?", (event_key,)).fetchone()[0])
        details["qty"] = corrupt_qty
        db.execute("UPDATE events SET details=? WHERE event_key=?", (json.dumps(details), event_key))
    result = book.tick(clock("2026-09-10T10:01:00+09:00"), {})
    assert any(error["code"] == "RECONCILIATION_ERROR" for error in result["errors"])


@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("corrupt_qty", [0, -5, 5.5, True])
def test_reconciliation_rejects_matching_invalid_quantities(tmp_path, closed, corrupt_qty):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    if closed:
        tp_clock = clock("2026-09-10T10:00:00+09:00")
        tp = quote(120_000, "2026-09-10T09:59:59+09:00", "2026-09-10T09:59:58+09:00")
        tp["received_at"] = tp_clock["now"]
        assert len(book.tick(tp_clock, {"005930": tp})["exits"]) == 3
    with sqlite3.connect(tmp_path / "book.db") as db:
        for event_key, raw_details in db.execute("SELECT event_key,details FROM events WHERE intent_id=1").fetchall():
            details = json.loads(raw_details)
            details["qty"] = corrupt_qty
            db.execute("UPDATE events SET details=? WHERE event_key=?", (json.dumps(details), event_key))
        table = "closed_positions" if closed else "positions"
        db.execute(f"UPDATE {table} SET qty=? WHERE rule='baseline_k1'", (corrupt_qty,))
    result = book.tick(clock("2026-09-10T10:01:00+09:00"), {})
    assert any(error["code"] == "RECONCILIATION_ERROR" for error in result["errors"])


def test_other_market_closed_corruption_does_not_block_healthy_market(tmp_path):
    book = SelectionShadowBook(tmp_path / "book.db")
    us_snapshot = snapshot(candidates=[{"ticker": "ABC", "dvol": 10, "features": {}}])
    us_snapshot.update(market="US", collected_at="2026-09-10T09:04:00-04:00",
                       completed_at="2026-09-10T09:04:01-04:00")
    us_clock = {"market": "US", "session_date": "2026-09-10", "now": "2026-09-10T09:05:57-04:00",
                "open_at": "2026-09-10T09:00:00-04:00", "close_at": "2026-09-10T15:30:00-04:00",
                "session_dates": ["2026-09-10"]}
    book.record_snapshot(us_snapshot); book.decide(us_clock)
    us_entry = quote(100, "2026-09-10T09:05:59-04:00", "2026-09-10T09:05:58-04:00")
    us_entry.update(received_at="2026-09-10T09:06:00-04:00")
    book.tick(dict(us_clock, now=us_entry["received_at"]), {"ABC": us_entry})
    us_tp = quote(120, "2026-09-10T09:59:59-04:00", "2026-09-10T09:59:58-04:00")
    us_tp.update(received_at="2026-09-10T10:00:00-04:00")
    book.tick(dict(us_clock, now=us_tp["received_at"]), {"ABC": us_tp})
    with sqlite3.connect(tmp_path / "book.db") as db:
        db.execute("UPDATE closed_positions SET ticker='CORRUPT' WHERE market='US' AND rule='baseline_k1'")
    assert any(error["code"] == "RECONCILIATION_ERROR" for error in
               book.tick(dict(us_clock, now="2026-09-10T10:01:00-04:00"), {})["errors"])
    book.record_snapshot(snapshot())
    book.decide(clock("2026-09-10T09:05:57+09:00"))
    result = book.tick(clock(), {"005930": quote()})
    assert len(result["fills"]) == 3
    assert not any(error["code"] == "RECONCILIATION_ERROR" for error in result["errors"])


def test_repeated_decide_does_not_manufacture_initial_capital_valuations(tmp_path):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    book.decide(clock("2026-09-10T09:07:00+09:00"))
    with sqlite3.connect(tmp_path / "book.db") as db:
        assert db.execute("SELECT COUNT(*) FROM account_valuations WHERE nav=4320000").fetchone()[0] == 3


def test_report_exposes_sanitized_execution_provenance(tmp_path):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    report = read_report(tmp_path / "book.db")
    for position in report["positions"]:
        assert position["entry_source"] == "fixture"
        assert position["entry_requested_at"] == "2026-09-10T09:05:58+09:00"
        assert position["entry_received_at"] == "2026-09-10T09:06:00+09:00"
        assert position["entry_price_kind"] == "LAST_PRICE_PAPER"
    assert all(intent["fill_source"] == "fixture" for intent in report["intents"])


def test_report_reads_one_consistent_sqlite_snapshot_during_concurrent_commit(tmp_path, monkeypatch):
    book = prepared_book(tmp_path)
    book.tick(clock(), {"005930": quote()})
    writer = sqlite3.connect(tmp_path / "book.db")
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE accounts SET cash=4320000")
    writer.execute("DELETE FROM positions")
    actual_connect = sqlite3.connect
    committed = False

    class HookConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal committed
            cursor = super().execute(sql, parameters)
            if not committed and sql.startswith("SELECT * FROM positions"):
                writer.commit()
                committed = True
            return cursor

    def hooked_connect(*args, **kwargs):
        if kwargs.get("uri"):
            kwargs["factory"] = HookConnection
        return actual_connect(*args, **kwargs)

    monkeypatch.setattr(shadow_book_module.sqlite3, "connect", hooked_connect)
    report = read_report(tmp_path / "book.db")
    writer.close()
    assert all(account["nav"] == pytest.approx(4_318_750) for account in report["accounts"])
