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

    def _broker_truth_market_snapshot(self, market, *, force=False, ttl_sec=None):
        return {"missing": False, "stale": False, "error": "", "open_orders": list(self.broker_open_orders)}

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
    ), patch(
        "runtime.us_swing_order_bridge.get_runtime_path",
        side_effect=lambda *parts, **_: bot.db_path.parent.joinpath(*parts),
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
    status = (tmp_path / "state" / "us_swing_execution_status.json").read_text(encoding="utf-8")
    assert '"schema_version": "us_swing_execution_status_v1"' in status
    assert '"status": "SKIPPED"' in status
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


def test_stale_broker_snapshot_fails_closed(tmp_path: Path) -> None:
    # 스냅샷이 stale이면 "주문 있음"으로 간주해 제출을 막는다 (2026-08-02 fail-closed)
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    bot = FakeBot(db_path)
    bot._broker_truth_market_snapshot = (
        lambda market, *, force=False, ttl_sec=None: {
            "missing": False, "stale": True, "error": "", "open_orders": []
        }
    )

    result = _run(bot)

    assert result["results"][0]["reason"] == "pending_order_exists"
    assert bot.submit_calls == 0


def test_stale_snapshot_recovers_via_forced_refresh(tmp_path: Path) -> None:
    # TTL 경과(stale)만으로 매수가 죽지 않게: 강제 갱신이 성공하면 제출이 진행된다
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    bot = FakeBot(db_path)

    def snapshot(market, *, force=False, ttl_sec=None):
        if force:
            return {"missing": False, "stale": False, "error": "", "open_orders": []}
        return {"missing": False, "stale": True, "error": "", "open_orders": []}

    bot._broker_truth_market_snapshot = snapshot
    result = _run(bot)

    assert result["results"][0]["submitted"] is True
    assert bot.submit_calls == 1


def test_broker_snapshot_exception_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    bot = FakeBot(db_path)

    def boom(market, *, force=False, ttl_sec=None):
        raise RuntimeError("snapshot unavailable")

    bot._broker_truth_market_snapshot = boom
    result = _run(bot)

    assert result["results"][0]["reason"] == "pending_order_exists"
    assert bot.submit_calls == 0


def _run_with_operator_override_authority(bot: FakeBot) -> dict:
    # 라이브 경로 재현: forward 블로커 + 운영자 ACK로 _operator_micro_override가 발동한
    # authority(슬롯 3/일1건). e2e 기본 경로는 evidence 기반 authority(슬롯 1)라 별도 패치.
    base_authority = {
        "configured_mode": "micro",
        "eligible_mode": "shadow",
        "effective_mode": "shadow",
        "allowed_to_emit_orders": False,
        "blockers": ["forward_matured_insufficient"],
        "warnings": [],
    }

    original_runtime_value = bot._runtime_value

    def runtime_value(key, default=""):
        if key == "US_SWING_OPERATOR_MICRO_OVERRIDE_ACK":
            return "I_ACCEPT_MICRO_WITHOUT_FORWARD"
        return original_runtime_value(key, default)

    bot._runtime_value = runtime_value
    with patch(
        "runtime.us_swing_order_bridge.resolve_handoff_authority",
        return_value=base_authority,
    ):
        return _run(bot)


def test_five_open_us_swing_slots_block_sixth_entry(tmp_path: Path) -> None:
    # 2026-08-20 운영자 결정(B안) 슬롯 5: 다섯 포지션 보유 중이면 여섯 번째 진입은 차단된다.
    # (기존 슬롯 3 계약에서 개정 — D5 보유 x 일1건의 정상상태 동시보유가 5개라
    #  슬롯 3이 진입률을 0.6건/일로 깎고 있었다.)
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    bot = FakeBot(db_path)
    bot.risk.positions = [
        {"market": "US", "ticker": f"HOLD{i}", "source_strategy": "us_swing_5d"}
        for i in range(5)
    ]

    result = _run_with_operator_override_authority(bot)

    assert result["results"][0]["reason"] == "strategy_open_slot_cap_reached"
    assert bot.submit_calls == 0


def test_four_open_slots_still_allow_entry_under_slot_cap_five(tmp_path: Path) -> None:
    # 슬롯 5 계약: 네 개 보유 중이면 다섯 번째는 통과해야 한다(구 계약이면 여기서 막혔다).
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    bot = FakeBot(db_path)
    bot.risk.positions = [
        {"market": "US", "ticker": f"HOLD{i}", "source_strategy": "us_swing_5d"}
        for i in range(4)
    ]

    result = _run_with_operator_override_authority(bot)

    assert result["results"][0]["submitted"] is True
    assert bot.submit_calls == 1


class _BandMaxBot(FakeBot):
    """밴드+MAX 게이트를 켠 봇 — skip 사유 귀속 테스트용."""

    def _runtime_bool(self, key, default=False):
        if key in ("US_SWING_DVOL_BAND_ENABLED", "US_SWING_MAX_FLOOR_ENABLED"):
            return True
        return super()._runtime_bool(key, default)

    def _runtime_float(self, key, default=0.0):
        if key == "US_SWING_MAX_FLOOR_PCT":
            return 8.0
        return super()._runtime_float(key, default)


def test_max_floor_empty_reports_max_floor_reason(tmp_path: Path) -> None:
    # 2026-09-01 실측: KRMN이 밴드 안(175M)이었는데 MAX가 비우자 사유가
    # dvol_band_no_candidate로 기록됐다(shadow 원장은 max_floor_no_candidate).
    # 실제로 비운 게이트가 사유여야 두 원장의 skip 통계가 갈라지지 않는다.
    db_path = tmp_path / "swing.db"
    _build_db(db_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO candidate_pool_all(session_date, ticker, dollar_vol, eligible, in_pool,"
        " recorded_at) VALUES (?,?,?,?,?,?)",
        ("2026-07-10", "TEST", 200e6, 1, 1, "now"),
    )
    con.commit()
    con.close()
    # 픽스처 CSV는 프로덕션과 같이 BOM을 단다(test_us_swing_max_floor 교훈).
    price_dir = tmp_path / "data" / "price" / "us"
    price_dir.mkdir(parents=True, exist_ok=True)
    with (price_dir / "us_TEST.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fh.write("date,open,high,low,close,volume\n")
        for i in range(25):
            close = 100 + i * 0.1  # 하루 최대 상승 ~0.1% << 하한 8%
            fh.write(f"2026-06-{i + 1:02d},{close},{close},{close},{close},1000\n")
    bot = _BandMaxBot(db_path, submit_enabled=False)

    result = _run(bot)

    assert result["status"] == "SKIPPED"
    assert result["reason"] == "max_floor_no_candidate"
    assert bot.submit_calls == 0
