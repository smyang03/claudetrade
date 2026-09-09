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
                    id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL, session_date TEXT NOT NULL,
                    rule TEXT NOT NULL, ticker TEXT NOT NULL, allocation REAL NOT NULL,
                    decided_at TEXT NOT NULL, seed TEXT NOT NULL, status TEXT NOT NULL,
                    reason TEXT, UNIQUE(market, session_date, rule, ticker));
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, intent_id INTEGER NOT NULL UNIQUE,
                    market TEXT NOT NULL, rule TEXT NOT NULL, ticker TEXT NOT NULL,
                    entry_session TEXT NOT NULL, entry_at TEXT NOT NULL, entry_price REAL NOT NULL,
                    qty INTEGER NOT NULL, fx REAL NOT NULL, entry_cost REAL NOT NULL,
                    entry_fee REAL NOT NULL, last_price REAL NOT NULL, last_price_at TEXT NOT NULL,
                    prior_peak_price REAL NOT NULL, pending_reason TEXT, mature_at TEXT,
                    entry_source TEXT NOT NULL DEFAULT '', entry_requested_at TEXT NOT NULL DEFAULT '',
                    entry_received_at TEXT NOT NULL DEFAULT '', entry_price_kind TEXT NOT NULL DEFAULT '',
                    last_source TEXT NOT NULL DEFAULT '', last_requested_at TEXT NOT NULL DEFAULT '',
                    last_received_at TEXT NOT NULL DEFAULT '', last_price_kind TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(intent_id) REFERENCES intents(id));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE, type TEXT NOT NULL,
                    market TEXT, rule TEXT, ticker TEXT, intent_id INTEGER, at TEXT NOT NULL,
                    amount REAL, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS closed_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER NOT NULL UNIQUE, market TEXT NOT NULL,
                    rule TEXT NOT NULL, ticker TEXT NOT NULL, entry_session TEXT NOT NULL,
                    entry_at TEXT NOT NULL, exit_at TEXT NOT NULL,
                    qty INTEGER NOT NULL, entry_price REAL NOT NULL, exit_price REAL NOT NULL,
                    reason TEXT NOT NULL, pnl REAL NOT NULL, delayed INTEGER NOT NULL,
                    mature_at TEXT, exit_source TEXT NOT NULL DEFAULT '', exit_requested_at TEXT NOT NULL DEFAULT '',
                    exit_received_at TEXT NOT NULL DEFAULT '', exit_price_kind TEXT NOT NULL DEFAULT '', details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS valuations (
                    position_id INTEGER NOT NULL, session_date TEXT NOT NULL, at TEXT NOT NULL,
                    price REAL NOT NULL, fresh INTEGER NOT NULL,
                    PRIMARY KEY(position_id, session_date, at));
                CREATE TABLE IF NOT EXISTS account_valuations (
                    market TEXT NOT NULL, rule TEXT NOT NULL, at TEXT NOT NULL,
                    cash REAL NOT NULL, nav REAL NOT NULL, exposure REAL NOT NULL,
                    PRIMARY KEY(market, rule, at));
                CREATE TABLE IF NOT EXISTS statuses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL, session_date TEXT NOT NULL,
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
        if status not in {"READY", "EMPTY", "FAILED", "MISSING", "INPUT_INCOMPLETE", "ERROR"}:
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
            db.execute("BEGIN IMMEDIATE")
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
                created = db.execute("INSERT OR IGNORE INTO accounts VALUES(?,?,?,?,?)",
                                     (market, rule, CAPITAL, CAPITAL, CAPITAL)).rowcount
                if created:
                    db.execute("INSERT INTO account_valuations VALUES(?,?,?,?,?,?)",
                               (market, rule, clock["now"], CAPITAL, CAPITAL, 0.0))
                if db.execute("SELECT 1 FROM decisions WHERE market=? AND session_date=? AND rule=?",
                              (market, session, rule)).fetchone():
                    continue
                held = {r[0] for r in db.execute("SELECT ticker FROM positions WHERE market=? AND rule=?",
                                                 (market, rule))}
                pool = [c for c in candidates if c["ticker"] not in held]
                seed = hashlib.sha256(f"v1|{MASTER_SEED}|{market}|{session}|{rule}".encode()).hexdigest()
                available_slots = max(RULE_SLOTS[rule] - len(held), 0)
                choose_count = min(RULE_COUNTS[rule], available_slots)
                if rule == "baseline_k1":
                    chosen = pool[:choose_count]
                else:
                    chosen = sorted(pool, key=lambda c: hashlib.sha256(
                        f"{seed}|{c['ticker']}".encode()).hexdigest())[:choose_count]
                account_cash = db.execute("SELECT cash FROM accounts WHERE market=? AND rule=?",
                                          (market, rule)).fetchone()[0]
                same_day_proceeds = db.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM events WHERE market=? AND rule=? "
                    "AND type='CLOSE' AND substr(at,1,10)=?",
                    (market, rule, session)).fetchone()[0]
                spendable_cash = max(account_cash - same_day_proceeds, 0.0)
                frozen_budget = min(DAILY_BUDGET, spendable_cash)
                allocation = frozen_budget / len(chosen) if chosen else 0.0
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
                if pending:
                    db.execute("INSERT INTO statuses(market,session_date,status,at,details) VALUES(?,?,?,?,?)",
                               (market, session, "ENTRY_WINDOW_MISSED", clock["now"],
                                _json({"intent_count": len(pending)})))
            elif reconciled and open_at + timedelta(minutes=5) <= now <= min(open_at + timedelta(minutes=45), close_at):
                for intent in pending:
                    quote = checked.get(intent["ticker"])
                    decided_at = _dt(intent["decided_at"], "decided_at")
                    if (quote and _dt(quote["requested_at"], "requested_at") >= decided_at
                            and _dt(quote["price_at"], "price_at") >= decided_at):
                        self._fill(db, intent, quote, clock["now"], result)
                    elif quote:
                        result["errors"].append({"ticker": intent["ticker"], "code": "NO_VERIFIED_QUOTE",
                                                 "detail": "quote observed or requested before decision"})
            for error in result["errors"]:
                if error["code"] != "RECONCILIATION_ERROR":
                    db.execute("INSERT INTO statuses(market,session_date,status,at,details) VALUES(?,?,?,?,?)",
                               (market, session, error["code"], clock["now"], _json(error)))
            self._record_account_valuations(db, market, clock["now"])
            db.execute("INSERT INTO statuses(market,session_date,status,at,details) VALUES(?,?,?,?,?)",
                       (market, session, "HEARTBEAT", clock["now"], _json({"fills": len(result["fills"]),
                                                                            "exits": len(result["exits"]),
                                                                            "errors": len(result["errors"])})))
        return result

    @staticmethod
    def _record_account_valuations(db, market, at):
        for account in db.execute("SELECT * FROM accounts WHERE market=?", (market,)).fetchall():
            positions = db.execute("SELECT * FROM positions WHERE market=? AND rule=?",
                                   (market, account["rule"])).fetchall()
            marked = sum(p["qty"] * p["last_price"] * p["fx"] for p in positions)
            reserve = sum(p["entry_fee"] for p in positions)
            nav = account["cash"] + marked - reserve
            db.execute("INSERT OR REPLACE INTO account_valuations VALUES(?,?,?,?,?,?)",
                       (market, account["rule"], at, account["cash"], nav, marked))
            db.execute("UPDATE accounts SET peak_nav=MAX(peak_nav,?) WHERE market=? AND rule=?",
                       (nav, market, account["rule"]))

    def _fill(self, db, intent, quote, now, result):
        fx = 1390.0 if intent["market"] == "US" else 1.0
        fee_pct = .50 if intent["market"] == "US" else .25
        unit = float(quote["price"]) * fx
        qty = math.floor(intent["allocation"] / unit)
        account = db.execute("SELECT * FROM accounts WHERE market=? AND rule=?",
                             (intent["market"], intent["rule"])).fetchone()
        position_count = db.execute("SELECT COUNT(*) FROM positions WHERE market=? AND rule=?",
                                    (intent["market"], intent["rule"])).fetchone()[0]
        if position_count >= RULE_SLOTS[intent["rule"]]:
            db.execute("UPDATE intents SET status='SLOT_LIMIT',reason='SLOT_LIMIT' WHERE id=?", (intent["id"],))
            return
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
                   "entry_cost,entry_fee,last_price,last_price_at,prior_peak_price,entry_source,entry_requested_at,"
                   "entry_received_at,entry_price_kind,last_source,last_requested_at,last_received_at,last_price_kind) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (intent["id"], intent["market"], intent["rule"], intent["ticker"], intent["session_date"], now,
                    quote["price"], qty, fx, entry_cost, entry_fee, quote["price"], quote["price_at"], quote["price"],
                    quote["source"], quote["requested_at"], quote["received_at"], quote["price_kind"],
                    quote["source"], quote["requested_at"], quote["received_at"], quote["price_kind"]))
        db.execute("UPDATE intents SET status='FILLED',reason=NULL WHERE id=?", (intent["id"],))
        result["fills"].append({"intent_id": intent["id"], "rule": intent["rule"], "ticker": intent["ticker"],
                                "qty": qty, "price": quote["price"], "at": now})

    def _process_exits(self, db, clock, checked, result):
        now, close = _dt(clock["now"], "now"), _dt(clock["close_at"], "close_at")
        open_at = _dt(clock["open_at"], "open_at")
        if not open_at <= now <= close:
            return
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
            observed_at = _dt(quote["price_at"], "price_at")
            if not open_at <= observed_at <= close:
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
            overdue = held is not None and held > 7
            if not reason and (delayed or overdue):
                reason = "D7"
                delayed = True
            if reason:
                fee_pct = .50 if pos["market"] == "US" else .25
                receipt = pos["qty"] * price * pos["fx"] - pos["entry_cost"] * fee_pct / 200
                pnl = receipt - pos["entry_cost"] - pos["entry_fee"]
                db.execute("UPDATE accounts SET cash=cash+? WHERE market=? AND rule=?", (receipt, pos["market"], pos["rule"]))
                db.execute("INSERT INTO events(event_key,type,market,rule,ticker,intent_id,at,amount,details) VALUES(?, 'CLOSE',?,?,?,?,?,?,?)",
                           (f"close:{pos['id']}", pos["market"], pos["rule"], pos["ticker"], pos["intent_id"],
                            clock["now"], receipt, _json({"reason": reason, "quote": quote, "qty": pos["qty"]})))
                entry_index = dates.index(pos["entry_session"]) if pos["entry_session"] in dates else None
                scheduled = dates[entry_index + 6] if entry_index is not None and entry_index + 6 < len(dates) else None
                db.execute("INSERT INTO closed_positions(position_id,market,rule,ticker,entry_session,entry_at,exit_at,qty,entry_price,exit_price,reason,pnl,delayed,mature_at,exit_source,exit_requested_at,exit_received_at,exit_price_kind,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (pos["id"], pos["market"], pos["rule"], pos["ticker"], pos["entry_session"], pos["entry_at"], clock["now"], pos["qty"], pos["entry_price"], price, reason, pnl, int(delayed), pos["mature_at"],
                            quote["source"], quote["requested_at"], quote["received_at"], quote["price_kind"],
                            _json({"scheduled_session": scheduled if delayed else None,
                                   "delay_sessions": max((held or 7) - 7, 0)})))
                db.execute("DELETE FROM positions WHERE id=?", (pos["id"],))
                result["exits"].append({"rule": pos["rule"], "ticker": pos["ticker"], "reason": reason, "price": price})
            else:
                # Only observations from completed earlier sessions can arm BE.
                prior_peak = max(pos["prior_peak_price"], price) if clock["session_date"] > pos["entry_session"] else pos["prior_peak_price"]
                db.execute("UPDATE positions SET last_price=?,last_price_at=?,prior_peak_price=?,last_source=?,"
                           "last_requested_at=?,last_received_at=?,last_price_kind=? WHERE id=?",
                           (price, quote["price_at"], prior_peak, quote["source"], quote["requested_at"],
                            quote["received_at"], quote["price_kind"], pos["id"]))

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
                elapsed = current - dates.index(row["entry_session"]) + 1 if row["entry_session"] in dates else 0
                window_complete = elapsed > 7 or (elapsed == 7 and _dt(clock["now"], "now") >= _dt(clock["close_at"], "close_at"))
                if window_complete:
                    db.execute(f"UPDATE {table} SET mature_at=? WHERE id=?", (clock["now"], row["id"]))

    def _reconcile(self, db, market):
        for account in db.execute("SELECT * FROM accounts WHERE market=?", (market,)):
            delta = db.execute("SELECT COALESCE(SUM(amount),0) FROM events WHERE market=? AND rule=?",
                               (market, account["rule"])).fetchone()[0]
            if abs(account["cash"] - (account["capital"] + delta)) > .01:
                return False
        intents = db.execute("SELECT * FROM intents WHERE market=?", (market,)).fetchall()
        for intent in intents:
            opens = db.execute("SELECT * FROM events WHERE event_key=? AND type='OPEN'",
                               (f"open:{intent['id']}",)).fetchall()
            position = db.execute("SELECT * FROM positions WHERE intent_id=?", (intent["id"],)).fetchone()
            closes = db.execute("SELECT * FROM events WHERE intent_id=? AND type='CLOSE'",
                                (intent["id"],)).fetchall()
            if intent["status"] == "FILLED":
                if len(opens) != 1:
                    return False
                identity = (intent["market"], intent["rule"], intent["ticker"], intent["id"])
                if (opens[0]["market"], opens[0]["rule"], opens[0]["ticker"], opens[0]["intent_id"]) != identity:
                    return False
                try:
                    open_qty = json.loads(opens[0]["details"])["qty"]
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    return False
                if type(open_qty) is not int or open_qty <= 0:
                    return False
                if position is not None:
                    if closes or (position["market"], position["rule"], position["ticker"],
                                  position["intent_id"]) != identity or type(position["qty"]) is not int or position["qty"] != open_qty:
                        return False
                else:
                    if len(closes) != 1 or not closes[0]["event_key"].startswith("close:"):
                        return False
                    try:
                        position_id = int(closes[0]["event_key"].split(":", 1)[1])
                    except (ValueError, IndexError):
                        return False
                    closed = db.execute("SELECT * FROM closed_positions WHERE position_id=?",
                                        (position_id,)).fetchone()
                    if closed is None:
                        return False
                    try:
                        close_qty = json.loads(closes[0]["details"])["qty"]
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        return False
                    if type(close_qty) is not int or close_qty <= 0:
                        return False
                    if ((closes[0]["market"], closes[0]["rule"], closes[0]["ticker"],
                         closes[0]["intent_id"]) != identity
                            or (closed["market"], closed["rule"], closed["ticker"]) != identity[:3]
                            or type(closed["qty"]) is not int or closed["qty"] != open_qty or close_qty != open_qty):
                        return False
            elif position is not None or opens or closes:
                return False
        for position in db.execute("SELECT * FROM positions WHERE market=?", (market,)):
            if not db.execute("SELECT 1 FROM intents WHERE id=? AND status='FILLED'",
                              (position["intent_id"],)).fetchone():
                return False
        for closed in db.execute("SELECT * FROM closed_positions WHERE market=?", (market,)):
            close = db.execute("SELECT * FROM events WHERE event_key=? AND type='CLOSE'",
                               (f"close:{closed['position_id']}",)).fetchone()
            if close is None or close["market"] != closed["market"] or close["rule"] != closed["rule"] or close["ticker"] != closed["ticker"]:
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
    supplied_now = _dt(now, "now") if now is not None else None
    try:
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=.25)
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        positions = [dict(r) for r in db.execute("SELECT * FROM positions ORDER BY market,rule,ticker")]
        closed = [dict(r) for r in db.execute("SELECT * FROM closed_positions ORDER BY exit_at DESC")]
        intents = [dict(r) for r in db.execute("SELECT * FROM intents ORDER BY session_date DESC,rule,ticker")]
        open_events = {r["intent_id"]: r for r in db.execute(
            "SELECT intent_id,at,details FROM events WHERE type='OPEN'")}
        for intent in intents:
            event = open_events.get(intent["id"])
            quote = json.loads(event["details"]).get("quote", {}) if event else {}
            intent.update(fill_at=event["at"] if event else None,
                          fill_price=quote.get("price"), fill_qty=(json.loads(event["details"]).get("qty") if event else None),
                          fill_source=quote.get("source"), fill_requested_at=quote.get("requested_at"),
                          fill_received_at=quote.get("received_at"), fill_price_kind=quote.get("price_kind"))
        snapshots = [dict(r) for r in db.execute(
            "SELECT market,session_date,status snapshot_status,completed_at snapshot_completed_at FROM snapshots")]
        statuses = [dict(r) for r in db.execute(
            "SELECT market,session_date,status,at,details FROM statuses ORDER BY at DESC,id DESC")]
        timestamp_values = [x for x in [*(p["last_price_at"] for p in positions),
                                         *(s["snapshot_completed_at"] for s in snapshots),
                                         *(s["at"] for s in statuses)] if x]
        last_updated = max(timestamp_values, key=lambda value: _dt(value, "persisted timestamp")) if timestamp_values else None
        effective_now = supplied_now or (_dt(last_updated, "last_updated") if last_updated else None)
        accounts = []
        for row in db.execute("SELECT * FROM accounts ORDER BY market,rule"):
            item = dict(row)
            held = [p for p in positions if p["market"] == row["market"] and p["rule"] == row["rule"]]
            closed_for = [p for p in closed if p["market"] == row["market"] and p["rule"] == row["rule"]]
            reserve = sum(p["entry_fee"] for p in held)
            marked = sum(p["qty"] * p["last_price"] * p["fx"] for p in held)
            nav = row["cash"] + marked - reserve
            nav_history = [v[0] for v in db.execute(
                "SELECT nav FROM account_valuations WHERE market=? AND rule=? ORDER BY at",
                (row["market"], row["rule"]))]
            peak = row["capital"]
            mdd = 0.0
            for historical_nav in nav_history:
                peak = max(peak, historical_nav)
                mdd = min(mdd, (historical_nav / peak - 1) * 100)
            stale_count = 0
            if effective_now is not None:
                stale_count = sum(effective_now - _dt(p["last_price_at"], "last_price_at") > timedelta(seconds=60)
                                  for p in held)
            item.update(nav=nav, return_pct=((nav / row["capital"] - 1) * 100 if held or closed_for else None),
                        mdd_pct=mdd, exposure_pct=(marked / nav * 100 if nav else 0.0),
                        closed_count=len(closed_for), open_count=len(held),
                        mature_count=sum(x.get("mature_at") is not None for x in held + closed_for),
                        stale_count=stale_count, closed_pnl=sum(x["pnl"] for x in closed_for),
                        open_pnl=nav - row["capital"] - sum(x["pnl"] for x in closed_for))
            accounts.append(item)
        keys = sorted({(r["market"], r["session_date"]) for r in snapshots + statuses},
                      key=lambda value: (value[1], value[0]), reverse=True)
        markets = []
        for market_name, session_date in keys:
            snap = next((r for r in snapshots if r["market"] == market_name and r["session_date"] == session_date), None)
            diagnostic = next((r for r in statuses if r["market"] == market_name and
                               r["session_date"] == session_date and r["status"] != "HEARTBEAT"), None)
            heartbeat = next((r for r in statuses if r["market"] == market_name and
                              r["session_date"] == session_date and r["status"] == "HEARTBEAT"), None)
            market = {"market": market_name, "session_date": session_date,
                      "snapshot_status": snap["snapshot_status"] if snap else "MISSING",
                      "snapshot_completed_at": snap["snapshot_completed_at"] if snap else None}
            relevant = [i["status"] for i in intents if i["market"] == market["market"] and i["session_date"] == market["session_date"]]
            if market["snapshot_status"] in {"FAILED", "MISSING", "EMPTY", "INPUT_INCOMPLETE", "ERROR"}:
                execution = "BLOCKED"
            elif any(status == "FILLED" for status in relevant):
                execution = "ACTIVE"
            elif relevant and all(status == "ENTRY_WINDOW_MISSED" for status in relevant):
                execution = "BLOCKED"
            elif any(status == "PENDING" for status in relevant):
                execution = "PENDING"
            elif diagnostic:
                execution = "BLOCKED"
            else:
                execution = "PENDING"
            market["execution_status"] = execution
            market["heartbeat_at"] = heartbeat["at"] if heartbeat else None
            market["latest_status"] = diagnostic["status"] if diagnostic else None
            market["latest_status_at"] = diagnostic["at"] if diagnostic else None
            market["latest_status_details"] = json.loads(diagnostic["details"]) if diagnostic else None
            markets.append(market)
        errors = [{**row, "details": json.loads(row["details"])} for row in statuses
                  if row["status"] != "HEARTBEAT"]
        report = {**empty, "available": True, "accounts": accounts, "markets": markets,
                  "positions": positions, "closed": closed, "intents": intents, "errors": errors,
                  "last_updated": last_updated}
        db.close()
        return report
    except sqlite3.Error as exc:
        return {**empty, "errors": [{"code": "REPORT_READ_ERROR", "detail": str(exc), "at": now}]}
