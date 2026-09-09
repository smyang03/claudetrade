"""Transactional, order-incapable paper accounts for selection shadowing."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


CAPITAL = 4_320_000.0
DAILY_BUDGET = 540_000.0
MASTER_SEED = 20260910
RULE_COUNTS = {"baseline_k1": 1, "random_k1": 1, "random_k3": 3}
RULE_SLOTS = {"baseline_k1": 7, "random_k1": 7, "random_k3": 21}
CONTRACT = "forward_quote_v1"


def _dt(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SelectionShadowBook:
    """Owns only a dedicated SQLite paper ledger; it has no broker interface."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    market TEXT NOT NULL, session_date TEXT NOT NULL, signal_date TEXT,
                    status TEXT NOT NULL, collected_at TEXT, completed_at TEXT,
                    payload TEXT NOT NULL, PRIMARY KEY(market, session_date));
                CREATE TABLE IF NOT EXISTS accounts (
                    market TEXT NOT NULL, rule TEXT NOT NULL, capital REAL NOT NULL,
                    cash REAL NOT NULL, peak_nav REAL NOT NULL, PRIMARY KEY(market, rule));
                CREATE TABLE IF NOT EXISTS decisions (
                    market TEXT NOT NULL, session_date TEXT NOT NULL, rule TEXT NOT NULL,
                    decided_at TEXT NOT NULL, allocation REAL NOT NULL, seed TEXT NOT NULL,
                    PRIMARY KEY(market, session_date, rule));
                CREATE TABLE IF NOT EXISTS intents (
                    id INTEGER PRIMARY KEY, market TEXT NOT NULL, session_date TEXT NOT NULL,
                    rule TEXT NOT NULL, ticker TEXT NOT NULL, allocation REAL NOT NULL,
                    decided_at TEXT NOT NULL, seed TEXT NOT NULL, status TEXT NOT NULL,
                    reason TEXT, UNIQUE(market, session_date, rule, ticker));
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY, intent_id INTEGER NOT NULL UNIQUE,
                    market TEXT NOT NULL, rule TEXT NOT NULL, ticker TEXT NOT NULL,
                    entry_session TEXT NOT NULL, entry_at TEXT NOT NULL, entry_price REAL NOT NULL,
                    qty INTEGER NOT NULL, fx REAL NOT NULL, entry_cost REAL NOT NULL,
                    entry_fee REAL NOT NULL, last_price REAL NOT NULL, last_price_at TEXT NOT NULL,
                    prior_peak_price REAL NOT NULL, pending_reason TEXT, mature_at TEXT,
                    FOREIGN KEY(intent_id) REFERENCES intents(id));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, type TEXT NOT NULL,
                    market TEXT, rule TEXT, ticker TEXT, intent_id INTEGER, at TEXT NOT NULL,
                    amount REAL, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS closed_positions (
                    id INTEGER PRIMARY KEY, position_id INTEGER NOT NULL UNIQUE, market TEXT NOT NULL,
                    rule TEXT NOT NULL, ticker TEXT NOT NULL, entry_session TEXT NOT NULL,
                    entry_at TEXT NOT NULL, exit_at TEXT NOT NULL,
                    qty INTEGER NOT NULL, entry_price REAL NOT NULL, exit_price REAL NOT NULL,
                    reason TEXT NOT NULL, pnl REAL NOT NULL, delayed INTEGER NOT NULL,
                    mature_at TEXT, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS valuations (
                    position_id INTEGER NOT NULL, session_date TEXT NOT NULL, at TEXT NOT NULL,
                    price REAL NOT NULL, fresh INTEGER NOT NULL,
                    PRIMARY KEY(position_id, session_date, at));
                CREATE TABLE IF NOT EXISTS statuses (
                    id INTEGER PRIMARY KEY, market TEXT NOT NULL, session_date TEXT NOT NULL,
                    status TEXT NOT NULL, at TEXT NOT NULL, details TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_positions_account ON positions(market, rule);
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=2.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=2000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def record_snapshot(self, snapshot: dict) -> dict:
        market, session = snapshot["market"], snapshot["session_date"]
        status = snapshot["status"].upper()
        if status not in {"READY", "EMPTY", "FAILED", "MISSING"}:
            raise ValueError("unsupported snapshot status")
        collected = _dt(snapshot["collected_at"], "collected_at")
        completed = _dt(snapshot["completed_at"], "completed_at")
        if completed < collected:
            raise ValueError("completed_at precedes collected_at")
        if status == "READY" and not snapshot.get("candidates"):
            raise ValueError("READY snapshot requires candidates")
        if status == "EMPTY" and snapshot.get("candidates"):
            raise ValueError("EMPTY snapshot cannot contain candidates")
        with self._connect() as db:
            prior = db.execute("SELECT payload,status FROM snapshots WHERE market=? AND session_date=?",
                               (market, session)).fetchone()
            if prior and prior["status"] in {"READY", "EMPTY"}:
                return json.loads(prior["payload"])
            db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?) "
                       "ON CONFLICT(market,session_date) DO UPDATE SET signal_date=excluded.signal_date,"
                       "status=excluded.status,collected_at=excluded.collected_at,"
                       "completed_at=excluded.completed_at,payload=excluded.payload",
                       (market, session, snapshot.get("signal_date"), status, snapshot["collected_at"],
                        snapshot["completed_at"], _json({**snapshot, "status": status})))
        return {**snapshot, "status": status}

    def decide(self, clock: dict) -> list[dict]:
        now = self._validate_clock(clock)
        market, session = clock["market"], clock["session_date"]
        with self._connect() as db:
            snap = db.execute("SELECT * FROM snapshots WHERE market=? AND session_date=?",
                              (market, session)).fetchone()
            if not snap or snap["status"] not in {"READY", "EMPTY"}:
                return []
            if _dt(snap["completed_at"], "completed_at") > now:
                raise ValueError("future snapshot completion")
            if snap["status"] == "EMPTY":
                return []
            payload = json.loads(snap["payload"])
            candidates = sorted(payload["candidates"], key=lambda x: (-float(x.get("dvol", 0)), x["ticker"]))
            db.execute("BEGIN IMMEDIATE")
            for rule in RULE_COUNTS:
                db.execute("INSERT OR IGNORE INTO accounts VALUES(?,?,?,?,?)",
                           (market, rule, CAPITAL, CAPITAL, CAPITAL))
                if db.execute("SELECT 1 FROM decisions WHERE market=? AND session_date=? AND rule=?",
                              (market, session, rule)).fetchone():
                    continue
                held = {r[0] for r in db.execute("SELECT ticker FROM positions WHERE market=? AND rule=?",
                                                 (market, rule))}
                pool = [c for c in candidates if c["ticker"] not in held]
                seed = hashlib.sha256(f"v1|{MASTER_SEED}|{market}|{session}|{rule}".encode()).hexdigest()
                if rule == "baseline_k1":
                    chosen = pool[:1]
                else:
                    chosen = sorted(pool, key=lambda c: hashlib.sha256(
                        f"{seed}|{c['ticker']}".encode()).hexdigest())[:RULE_COUNTS[rule]]
                allocation = DAILY_BUDGET / len(chosen) if chosen else 0.0
                db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?)",
                           (market, session, rule, clock["now"], allocation, seed))
                for candidate in chosen:
                    db.execute("INSERT INTO intents(market,session_date,rule,ticker,allocation,decided_at,seed,status)"
                               " VALUES(?,?,?,?,?,?,?,'PENDING')",
                               (market, session, rule, candidate["ticker"], allocation, clock["now"], seed))
            rows = db.execute("SELECT * FROM intents WHERE market=? AND session_date=? ORDER BY rule,ticker",
                              (market, session)).fetchall()
            return [dict(row) for row in rows]

    def required_tickers(self, market: str) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT ticker FROM intents WHERE market=? AND status='PENDING' UNION "
                              "SELECT ticker FROM positions WHERE market=? ORDER BY ticker", (market, market))
            return [row[0] for row in rows]

    def tick(self, clock: dict, quotes: dict) -> dict:
        now = self._validate_clock(clock)
        market, session = clock["market"], clock["session_date"]
        result = {"market": market, "session_date": session, "now": clock["now"],
                  "fills": [], "exits": [], "errors": []}
        # Quote validation is performed before acquiring a write transaction.
        checked = {}
        for ticker in self.required_tickers(market):
            error = self._quote_error(quotes.get(ticker), ticker, session, now)
            if error:
                result["errors"].append({"ticker": ticker, "code": "NO_VERIFIED_QUOTE", "detail": error})
            else:
                checked[ticker] = quotes[ticker]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reconciled = self._reconcile(db, market)
            if not reconciled:
                result["errors"].append({"code": "RECONCILIATION_ERROR", "market": market})
                db.execute("INSERT INTO statuses(market,session_date,status,at,details) VALUES(?,?,?,?,?)",
                           (market, session, "RECONCILIATION_ERROR", clock["now"], _json({})))
            self._mark_mature_cohorts(db, clock)
            self._process_exits(db, clock, checked, result)
            open_at, close_at = _dt(clock["open_at"], "open_at"), _dt(clock["close_at"], "close_at")
            pending = db.execute("SELECT * FROM intents WHERE market=? AND session_date=? AND status='PENDING'",
                                 (market, session)).fetchall()
            if now > open_at + timedelta(minutes=45):
                db.execute("UPDATE intents SET status='ENTRY_WINDOW_MISSED',reason='ENTRY_WINDOW_MISSED' "
                           "WHERE market=? AND session_date=? AND status='PENDING'", (market, session))
            elif reconciled and open_at + timedelta(minutes=5) <= now <= min(open_at + timedelta(minutes=45), close_at):
                for intent in pending:
                    quote = checked.get(intent["ticker"])
                    if quote and _dt(quote["requested_at"], "requested_at") >= _dt(intent["decided_at"], "decided_at"):
                        self._fill(db, intent, quote, clock["now"], result)
                    elif quote:
                        result["errors"].append({"ticker": intent["ticker"], "code": "NO_VERIFIED_QUOTE",
                                                 "detail": "quote requested before decision"})
        return result

    def _fill(self, db, intent, quote, now, result):
        fx = 1390.0 if intent["market"] == "US" else 1.0
        fee_pct = .50 if intent["market"] == "US" else .25
        unit = float(quote["price"]) * fx
        qty = math.floor(intent["allocation"] / unit)
        account = db.execute("SELECT * FROM accounts WHERE market=? AND rule=?",
                             (intent["market"], intent["rule"])).fetchone()
        if qty < 1:
            db.execute("UPDATE intents SET status='TOO_EXPENSIVE',reason='TOO_EXPENSIVE' WHERE id=?", (intent["id"],))
            return
        entry_cost = qty * unit
        entry_fee = entry_cost * fee_pct / 200
        if entry_cost + entry_fee > account["cash"]:
            db.execute("UPDATE intents SET status='INSUFFICIENT_CASH',reason='INSUFFICIENT_CASH' WHERE id=?", (intent["id"],))
            return
        db.execute("UPDATE accounts SET cash=cash-? WHERE market=? AND rule=?",
                   (entry_cost + entry_fee, intent["market"], intent["rule"]))
        db.execute("INSERT INTO events(event_key,type,market,rule,ticker,intent_id,at,amount,details) "
                   "VALUES(?, 'OPEN',?,?,?,?,?,?,?)",
                   (f"open:{intent['id']}", intent["market"], intent["rule"], intent["ticker"], intent["id"],
                    now, -(entry_cost + entry_fee), _json({"quote": quote, "qty": qty})))
        db.execute("INSERT INTO positions(intent_id,market,rule,ticker,entry_session,entry_at,entry_price,qty,fx,"
                   "entry_cost,entry_fee,last_price,last_price_at,prior_peak_price) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (intent["id"], intent["market"], intent["rule"], intent["ticker"], intent["session_date"], now,
                    quote["price"], qty, fx, entry_cost, entry_fee, quote["price"], quote["price_at"], quote["price"]))
        db.execute("UPDATE intents SET status='FILLED',reason=NULL WHERE id=?", (intent["id"],))
        result["fills"].append({"intent_id": intent["id"], "rule": intent["rule"], "ticker": intent["ticker"],
                                "qty": qty, "price": quote["price"], "at": now})

    def _process_exits(self, db, clock, checked, result):
        now, close = _dt(clock["now"], "now"), _dt(clock["close_at"], "close_at")
        dates = clock.get("session_dates") or []
        for pos in db.execute("SELECT * FROM positions WHERE market=?", (clock["market"],)).fetchall():
            held = None
            if pos["entry_session"] in dates and clock["session_date"] in dates:
                held = dates.index(clock["session_date"]) - dates.index(pos["entry_session"]) + 1
            quote = checked.get(pos["ticker"])
            if not quote:
                if close - timedelta(minutes=15) <= now <= close and held is not None and held >= 7:
                    db.execute("UPDATE positions SET pending_reason='EXIT_PENDING_QUOTE' WHERE id=?", (pos["id"],))
                continue
            price = float(quote["price"])
            db.execute("INSERT OR IGNORE INTO valuations VALUES(?,?,?,?,1)",
                       (pos["id"], clock["session_date"], clock["now"], price))
            in_close_window = close - timedelta(minutes=15) <= now <= close
            reason = "TP" if price >= pos["entry_price"] * 1.12 else None
            if not reason and in_close_window and price <= pos["entry_price"] * .75:
                reason = "SL"
            historical_peak = db.execute(
                "SELECT MAX(price) FROM valuations WHERE position_id=? AND session_date<?",
                (pos["id"], clock["session_date"])).fetchone()[0]
            if not reason and in_close_window and pos["market"] == "US" and historical_peak is not None and historical_peak >= pos["entry_price"] * 1.04 and price <= pos["entry_price"]:
                reason = "BE"
            if not reason and in_close_window and held is not None and held >= 7:
                reason = "D7"
            delayed = pos["pending_reason"] == "EXIT_PENDING_QUOTE"
            if not reason and delayed:
                reason = "D7"
            if reason:
                fee_pct = .50 if pos["market"] == "US" else .25
                receipt = pos["qty"] * price * pos["fx"] - pos["entry_cost"] * fee_pct / 200
                pnl = receipt - pos["entry_cost"] - pos["entry_fee"]
                db.execute("UPDATE accounts SET cash=cash+? WHERE market=? AND rule=?", (receipt, pos["market"], pos["rule"]))
                db.execute("INSERT INTO events(event_key,type,market,rule,ticker,intent_id,at,amount,details) VALUES(?, 'CLOSE',?,?,?,?,?,?,?)",
                           (f"close:{pos['id']}", pos["market"], pos["rule"], pos["ticker"], pos["intent_id"], clock["now"], receipt, _json({"reason": reason, "quote": quote})))
                db.execute("INSERT INTO closed_positions(position_id,market,rule,ticker,entry_session,entry_at,exit_at,qty,entry_price,exit_price,reason,pnl,delayed,mature_at,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (pos["id"], pos["market"], pos["rule"], pos["ticker"], pos["entry_session"], pos["entry_at"], clock["now"], pos["qty"], pos["entry_price"], price, reason, pnl, int(delayed), pos["mature_at"],
                            _json({"scheduled_session": dates[6] if delayed and len(dates) > 6 else None,
                                   "delay_sessions": max((held or 7) - 7, 0)})))
                db.execute("DELETE FROM positions WHERE id=?", (pos["id"],))
                result["exits"].append({"rule": pos["rule"], "ticker": pos["ticker"], "reason": reason, "price": price})
            else:
                # Only observations from completed earlier sessions can arm BE.
                prior_peak = max(pos["prior_peak_price"], price) if clock["session_date"] > pos["entry_session"] else pos["prior_peak_price"]
                db.execute("UPDATE positions SET last_price=?,last_price_at=?,prior_peak_price=? WHERE id=?",
                           (price, quote["price_at"], prior_peak, pos["id"]))

    @staticmethod
    def _mark_mature_cohorts(db, clock):
        dates = clock.get("session_dates") or []
        if clock["session_date"] not in dates:
            return
        current = dates.index(clock["session_date"])
        for table in ("positions", "closed_positions"):
            rows = db.execute(f"SELECT id,entry_session FROM {table} WHERE market=? AND mature_at IS NULL",
                              (clock["market"],)).fetchall()
            for row in rows:
                if row["entry_session"] in dates and current - dates.index(row["entry_session"]) + 1 >= 7:
                    db.execute(f"UPDATE {table} SET mature_at=? WHERE id=?", (clock["now"], row["id"]))

    def _reconcile(self, db, market):
        for account in db.execute("SELECT * FROM accounts WHERE market=?", (market,)):
            delta = db.execute("SELECT COALESCE(SUM(amount),0) FROM events WHERE market=? AND rule=?",
                               (market, account["rule"])).fetchone()[0]
            if abs(account["cash"] - (account["capital"] + delta)) > .01:
                return False
        return True

    def _validate_clock(self, clock):
        now = _dt(clock["now"], "now")
        open_at, close_at = _dt(clock["open_at"], "open_at"), _dt(clock["close_at"], "close_at")
        if not open_at < close_at or now.date().isoformat() != clock["session_date"]:
            raise ValueError("clock does not match session")
        return now

    @staticmethod
    def _quote_error(quote, ticker, session, now):
        if not quote:
            return "missing quote"
        try:
            price = float(quote["price"])
            price_at = _dt(quote["price_at"], "price_at")
            requested = _dt(quote["requested_at"], "requested_at")
            received = _dt(quote["received_at"], "received_at")
        except (KeyError, TypeError, ValueError) as exc:
            return str(exc)
        if not math.isfinite(price) or price <= 0:
            return "price must be finite and positive"
        if quote.get("session_date") != session:
            return "quote session mismatch"
        if price_at > now or requested > now or received > now:
            return "future quote timestamp"
        if now - price_at > timedelta(seconds=60):
            return "stale quote"
        if requested > received:
            return "request after receipt"
        return None

    def record_status(self, market: str, session_date: str, status: str,
                      now: str, details: dict) -> None:
        _dt(now, "now")
        with self._connect() as db:
            db.execute("INSERT INTO statuses(market,session_date,status,at,details) VALUES(?,?,?,?,?)",
                       (market, session_date, status, now, _json(details)))


def read_report(path, now: str | None = None) -> dict:
    """Read the report through SQLite read-only mode; never creates the DB."""
    path = Path(path)
    empty = {"available": False, "authority": "SHADOW_ONLY", "contract": CONTRACT,
             "accounts": [], "markets": [], "positions": [], "closed": [], "intents": [],
             "errors": [], "last_updated": None}
    if not path.is_file():
        return empty
    if now is not None:
        _dt(now, "now")
    try:
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=.25)
        db.row_factory = sqlite3.Row
        positions = [dict(r) for r in db.execute("SELECT * FROM positions ORDER BY market,rule,ticker")]
        closed = [dict(r) for r in db.execute("SELECT * FROM closed_positions ORDER BY exit_at DESC")]
        intents = [dict(r) for r in db.execute("SELECT * FROM intents ORDER BY session_date DESC,rule,ticker")]
        accounts = []
        for row in db.execute("SELECT * FROM accounts ORDER BY market,rule"):
            item = dict(row)
            held = [p for p in positions if p["market"] == row["market"] and p["rule"] == row["rule"]]
            closed_for = [p for p in closed if p["market"] == row["market"] and p["rule"] == row["rule"]]
            reserve = sum(p["entry_fee"] for p in held)
            marked = sum(p["qty"] * p["last_price"] * p["fx"] for p in held)
            nav = row["cash"] + marked - reserve
            item.update(nav=nav, return_pct=((nav / row["capital"] - 1) * 100 if held or closed_for else None),
                        mdd_pct=0.0, exposure_pct=(marked / nav * 100 if nav else 0.0),
                        closed_count=len(closed_for), open_count=len(held),
                        mature_count=sum(x.get("mature_at") is not None for x in held + closed_for),
                        stale_count=0, closed_pnl=sum(x["pnl"] for x in closed_for),
                        open_pnl=nav - row["capital"] - sum(x["pnl"] for x in closed_for))
            accounts.append(item)
        markets = [dict(r) for r in db.execute(
            "SELECT market,session_date,status snapshot_status,completed_at snapshot_completed_at FROM snapshots ORDER BY session_date DESC")]
        for market in markets:
            relevant = [i["status"] for i in intents if i["market"] == market["market"] and i["session_date"] == market["session_date"]]
            if market["snapshot_status"] in {"FAILED", "MISSING", "EMPTY"}:
                execution = "BLOCKED"
            elif any(status == "FILLED" for status in relevant):
                execution = "ACTIVE"
            elif relevant and all(status == "ENTRY_WINDOW_MISSED" for status in relevant):
                execution = "BLOCKED"
            else:
                execution = "PENDING"
            market["execution_status"] = execution
        errors = [dict(r) for r in db.execute("SELECT market,session_date,status,at,details FROM statuses WHERE status LIKE '%ERROR%' ORDER BY at DESC")]
        updated_values = [x for x in [*(p["last_price_at"] for p in positions),
                                      *(m["snapshot_completed_at"] for m in markets)] if x]
        report = {**empty, "available": True, "accounts": accounts, "markets": markets,
                  "positions": positions, "closed": closed, "intents": intents, "errors": errors,
                  "last_updated": max(updated_values) if updated_values else None}
        db.close()
        return report
    except sqlite3.Error as exc:
        return {**empty, "errors": [{"code": "REPORT_READ_ERROR", "detail": str(exc), "at": now}]}
