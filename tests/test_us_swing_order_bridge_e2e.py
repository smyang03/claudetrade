from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from bot.session_date import KST
from runtime.us_swing_order_bridge import run_us_swing_handoff
from runtime.us_swing_order_handoff import ensure_handoff_schema
from tools.us_swing_shadow_runner import ensure_schema


ROOT = Path(__file__).resolve().parents[1]


def _build_db(path: Path) -> None:
    con = sqlite3.connect(path)
    ensure_schema(con)
    ensure_handoff_schema(con)
    for day_idx, signal_date in enumerate(
        ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    ):
        value = -1.0 if day_idx == 0 else 1.0
        for rank in range(1, 4):
            con.execute(
                """INSERT INTO signals(
                    signal_date,ticker,feature_date,model_version,rank,predicted_net_pct,
                    probability,created_at,status,data_quality,net_krw_pct,reference_close,
                    execution_shadow_eligible,execution_shadow_net_krw_pct,execution_shadow_policy
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    signal_date, f"M{day_idx}{rank}", signal_date, "m", rank, 1.0,
                    0.60, "now", "MATURED", "point_in_time", value, 100.0,
                    1 if rank == 1 else 0, value if rank == 1 else None, "rank1_skip_v1",
                ),
            )
    con.execute(
        """INSERT INTO signals(
            signal_date,ticker,feature_date,model_version,rank,predicted_net_pct,
            probability,created_at,status,data_quality,reference_close
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "2026-07-10", "TEST", "2026-07-09", "m", 1, 1.0,
            0.60, "now", "PENDING", "point_in_time", 100.0,
        ),
    )
    con.commit()
    con.close()


class FakeBot:
    def __init__(self, db_path: Path, *, submit_enabled: bool = True) -> None:
        self.db_path = db_path
        self.is_paper = False
        self.usd_krw_rate = 1400.0
        self.risk = SimpleNamespace(max_order_krw=2_000_000.0, positions=[])
        self.pending_orders: list[dict] = []
        self.today_judgment = {"consensus": {"mode": "CAUTIOUS"}}
        self.submit_enabled = submit_enabled
        self.submit_calls = 0
        self.last_submit_kwargs = {}
        self.unknown_calls: list[tuple] = []
        self.broker_open_orders: list[dict] = []

    def _runtime_value(self, key, default=""):
        values = {
            "US_SWING_SHADOW_DB": str(self.db_path),
            "US_SWING_POLICY_PATH": str(ROOT / "config" / "us_swing_accelerated.json"),
            "US_SWING_HISTORICAL_EVIDENCE_PATH": str(ROOT / "state" / "us_swing_historical_evidence.json"),
            "US_SWING_AUTHORITY_MODE": "micro",
            "US_SWING_ORDER_LIVE_ACK": "I_ACCEPT_LIVE_US_SWING",
        }
        return values.get(key, default)

    def _runtime_bool(self, key, default=False):
        if key == "US_SWING_ORDER_SUBMIT_ENABLED":
            return self.submit_enabled
        return default

    def _runtime_int(self, key, default=0):
        return default

    def _runtime_float(self, key, default=0.0):
        return default

    def _current_session_date_str(self, market):
        return "2026-07-10"

    def _sync_runtime_with_broker(self):
        return None

    def _token_for_market(self, market):
        return "token"

    def _same_day_reentry_state(self, ticker, market):
        return {"allowed": True}

    def _market_budget_available(self, market):
        return 2_000_000.0

    def _broker_orderable_cash_krw(self, market):
        return 2_000_000.0

    def _broker_trust_level(self, market):
        return "trusted"

    def _has_open_position(self, ticker, market):
        return False

    def _has_pending_order(self, ticker, market):
        return any(row.get("ticker") == ticker for row in self.pending_orders)

    def _broker_truth_open_buy_orders(self, market):
        return list(self.broker_open_orders)

    def _new_buy_block_state(self, market, ticker, strategy, profit_evidence=None):
        return {"allowed": True}

    def _v2_record_order_unknown(self, *args):
        self.unknown_calls.append(args)

    def _submit_micro_probe_buy_order(self, **kwargs):
        self.submit_calls += 1
        self.last_submit_kwargs = dict(kwargs)
        order_no = "ORDER-1"
        self._last_micro_probe_submit_result = {
            "status": "SUBMITTED", "order_no": order_no, "reason": "broker_accepted"
        }
        self.pending_orders.append(
            {"market": "US", "ticker": kwargs["ticker"], "order_no": order_no, "source_strategy": "us_swing_5d"}
        )
        return True


def _run(bot: FakeBot) -> dict:
    opened = datetime.now(KST) - timedelta(minutes=10)
    quote = {"name": "TEST", "price": 100.5, "open": 100.0, "prev_close": 100.0, "volume": 1000}
    with patch("runtime.us_swing_order_bridge.regular_open_dt", return_value=opened), patch(
        "runtime.us_swing_order_bridge.get_price", return_value=quote
    ):
        return run_us_swing_handoff(bot)


def test_successful_submit_is_persisted_and_second_scan_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    bot = FakeBot(db_path)

    first = _run(bot)
    second = _run(bot)

    assert first["results"][0]["submitted"] is True
    assert second["reason"] == "no_handoff_signal"
    assert bot.submit_calls == 1
    assert bot.last_submit_kwargs["tp_pct"] == 0.12
    assert bot.last_submit_kwargs["sl_pct"] == 0.25
    con = sqlite3.connect(db_path)
    assert con.execute(
        "SELECT handoff_status,handoff_order_no FROM signals WHERE ticker='TEST'"
    ).fetchone() == ("SUBMITTED", "ORDER-1")
    con.close()


def test_unknown_broker_outcome_is_terminal_and_never_retried(tmp_path: Path) -> None:
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    bot = FakeBot(db_path)

    def unknown_submit(**kwargs):
        bot.submit_calls += 1
        bot._last_micro_probe_submit_result = {
            "status": "UNKNOWN", "order_no": "", "reason": "order_exception"
        }
        return False

    bot._submit_micro_probe_buy_order = unknown_submit
    first = _run(bot)
    second = _run(bot)

    assert first["results"][0]["status"] == "ORDER_UNKNOWN"
    assert second["reason"] == "no_handoff_signal"
    assert bot.submit_calls == 1
    assert len(bot.unknown_calls) == 1


def test_restart_broker_truth_open_order_blocks_duplicate_submit(tmp_path: Path) -> None:
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    bot = FakeBot(db_path)
    bot.broker_open_orders = [{"market": "US", "ticker": "TEST", "order_no": "BROKER-OPEN"}]

    result = _run(bot)

    assert result["results"][0]["reason"] == "pending_order_exists"
    assert bot.submit_calls == 0


def test_rehearsal_never_calls_submit_path(tmp_path: Path) -> None:
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    bot = FakeBot(db_path, submit_enabled=False)

    result = _run(bot)

    assert result["results"][0]["status"] == "REHEARSAL_READY"
    assert bot.submit_calls == 0
