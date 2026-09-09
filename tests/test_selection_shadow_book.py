import concurrent.futures
import sqlite3

import pytest

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
