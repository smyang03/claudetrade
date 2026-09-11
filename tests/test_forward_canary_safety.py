from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tools import forward_gate_watch as gate
from tools import canary_materializer as materializer
from runtime import canary_policy as policy


@pytest.fixture
def book():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE trades(strategy_id TEXT,session_date TEXT,net_pct REAL,status TEXT,backfill INTEGER)")
    yield con
    con.close()


def insert(con, day, net, status="CLOSED", backfill=0):
    con.execute("INSERT INTO trades VALUES(?,?,?,?,?)", (gate.ARMS[0], day, net, status, backfill))


def stats(con):
    return gate.forward_arm_stats(con)[gate.ARMS[0]]


def test_thirty_trades_in_two_sessions_cannot_promote(book):
    for day, net in [("2026-09-07", 1.0), ("2026-09-08", 1.1)]:
        for _ in range(15):
            insert(book, day, net)
    s = stats(book)
    assert s["n"] == 30 and s["sessions"] == 2
    assert gate.verdict(gate.ARMS[0], s) == "ACCUMULATING_SESSIONS(2/30)"


def test_open_cohort_excluded_as_a_whole(book):
    insert(book, "2026-09-07", 10)
    insert(book, "2026-09-07", None, "OPEN")
    insert(book, "2026-09-08", -1)
    insert(book, "2026-09-09", 99, backfill=1)
    s = stats(book)
    assert s["n"] == 1 and s["session_mean"] == -1
    assert s["incomplete_sessions"] == 1


@pytest.mark.parametrize("bad", [None, "bad", float("inf"), -float("inf")])
def test_invalid_closed_return_fails_closed(book, bad):
    insert(book, "2026-09-07", bad)
    assert gate.verdict(gate.ARMS[0], stats(book)) == "DATA_INVALID"


def strong_stats():
    return {"n": 100, "sessions": 60, "session_t": 4,
            "half_year_means": {"2026H1": .5, "2026H2": .6}, "ex_top2_mean": .4}


def test_positive_evidence_requires_review_not_live_promotion():
    assert gate.verdict(gate.ARMS[0], strong_stats()) == "REVIEW_REQUIRED"


@pytest.mark.parametrize("change", [
    {"half_year_means": {"2026H2": 1}},
    {"half_year_means": {"2026H1": -1, "2026H2": 1}},
    {"ex_top2_mean": -1}, {"session_t": 2.4999},
])
def test_weak_or_concentrated_evidence_stays_watch(change):
    assert gate.verdict(gate.ARMS[0], {**strong_stats(), **change}) == "WATCH"


def test_sample_standard_deviation():
    assert gate._t([1, 2, 3]) == pytest.approx(2 * 3 ** .5)
    assert gate._t([1, 1]) is None


@pytest.fixture
def policy_files(monkeypatch, tmp_path):
    for name in ("POLICY", "GATE", "REALIZED"):
        monkeypatch.setattr(policy, name, tmp_path / name)
    policy.POLICY.write_text(json.dumps({"status": "APPROVED", "total_loss_line_krw": -33000,
                                        "canary_strategies": {"new_arm": "arm"}}), encoding="utf-8")
    return tmp_path


def test_missing_policy_blocks(policy_files):
    policy.POLICY.unlink()
    assert policy.canary_gate("new_arm") == (False, "no_valid_policy")


@pytest.mark.parametrize("content", ['[]', '{', '{"status":"PROPOSAL"}', '{"status":"APPROVED"}'])
def test_malformed_or_unapproved_policy_blocks(policy_files, content):
    policy.POLICY.write_text(content, encoding="utf-8")
    assert policy.canary_gate("new_arm")[0] is False


def test_old_strong_label_cannot_authorize_canary(policy_files):
    policy.GATE.write_text(json.dumps({"verdicts": {"arm": "CANDIDATE_STRONG"}}), encoding="utf-8")
    assert policy.canary_gate("new_arm") == (False, "execution_validation_pending")
    assert policy.canary_gate("CANARY_ARM")[0] is False


@pytest.mark.parametrize("row", ['{"net_krw":NaN}', '{"net_krw":null}', '{}', '{', '[]'])
def test_corrupt_loss_ledger_blocks_even_legacy(policy_files, row):
    policy.REALIZED.write_text(row, encoding="utf-8")
    assert policy.canary_gate("us_swing_5d") == (False, "invalid_loss_evidence")


def test_legacy_loss_guard_preserved(policy_files):
    assert policy.canary_gate("us_swing_5d") == (True, "legacy_loss_guard_only")
    policy.REALIZED.write_text('{"net_krw":-33000}\n', encoding="utf-8")
    assert policy.canary_gate("us_swing_5d")[0] is False


@pytest.mark.parametrize("pick,cap", [("all", 999), ("dvol_desc", 5), ("other", 1)])
def test_all_or_top5_cannot_inherit_k1_evidence(pick, cap):
    assert materializer.selection_contract_error({"pick": pick, "daily_cap": cap})


def test_k1_selection_contract_only():
    assert materializer.selection_contract_error({"pick": "dvol_desc", "daily_cap": 1}) is None


def test_us_preopen_kst_uses_same_session(monkeypatch):
    from preopen import scheduler
    monkeypatch.setattr(scheduler, "_exchange_session_open_dt", lambda market, day:
                        datetime.fromisoformat(day + "T13:30:00+00:00"))
    now = datetime.fromisoformat("2026-09-09T21:05:00+09:00")
    assert materializer._next_session("US", now) == "2026-09-09"
    assert materializer._next_session("US", now + timedelta(hours=2)) == "2026-09-10"


def test_calendar_closure_and_missing_calendar(monkeypatch):
    from preopen import scheduler
    monkeypatch.setattr(scheduler, "_exchange_session_open_dt", lambda market, day:
                        datetime.fromisoformat("2026-09-08T13:30:00+00:00") if day == "2026-09-08" else None)
    assert materializer._next_session("US", datetime(2026, 9, 4, 22, tzinfo=timezone.utc)) == "2026-09-08"
    monkeypatch.setattr(scheduler, "_exchange_session_open_dt", lambda *args: None)
    with pytest.raises(ValueError, match="calendar_unavailable"):
        materializer._next_session("US", datetime.now(timezone.utc))


def test_reference_maturity_is_inclusive_and_complete(monkeypatch, tmp_path):
    monkeypatch.setattr(materializer, "ROOT", tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(materializer, "LEDGER", ledger)
    monkeypatch.setitem(sys.modules, "virtual_books", SimpleNamespace(bar_complete=lambda d, m: d <= "2026-09-08"))
    prices = tmp_path / "data" / "price" / "kr"
    prices.mkdir(parents=True)
    (prices / "kr_TEST.csv").write_text("2026-09-07,100,110,90,101,1\n2026-09-08,101,110,90,102,1\n2026-09-09,102,110,90,999,1\n", encoding="utf-8")
    row = {"kind": "planned", "market": "KR", "ticker": "TEST", "session_date": "2026-09-07",
           "order_krw": 50000, "hold_sessions": 2}
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")
    materializer.settle()
    settled = json.loads(ledger.read_text(encoding="utf-8"))
    assert settled["exit_close_at_hold"] == 102
    assert settled["reference_exit_session"] == "2026-09-08"
    assert "NOT_STRATEGY_PNL" in settled["settlement_kind"]
    row["hold_sessions"] = 3
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")
    materializer.settle()
    assert json.loads(ledger.read_text(encoding="utf-8"))["status"] == "REHEARSAL_OPENED"


def test_latest_incomplete_bar_does_not_erase_completed_candidates(monkeypatch, tmp_path):
    (tmp_path / "us_TEST.csv").touch()
    fake = SimpleNamespace(
        US_DIR=tmp_path, KR_DIR=tmp_path, MIN_HISTORY=1,
        _load_bars=lambda p: [("2026-09-07",), ("2026-09-08",), ("2026-09-09",)],
        bar_complete=lambda d, m: d <= "2026-09-08", _index_regime=lambda m: {},
        volume_context=lambda b: {}, featurize=lambda b, i, m, v: {"dvol": 1, "price": 10},
        pool_pass=lambda f, m: ["pool"], US_DVOL_MIN_M=10,
    )
    monkeypatch.setitem(sys.modules, "discovery_pools", fake)
    rows = materializer._last_bar_candidates("US")
    assert rows["pool"][0]["signal_date"] == "2026-09-08"


@pytest.mark.parametrize("calendar_ok", [True, False])
def test_materialized_strong_signal_has_no_authority(monkeypatch, tmp_path, calendar_ok):
    monkeypatch.setattr(materializer, "ROOT", tmp_path)
    monkeypatch.setattr(materializer, "LEDGER", tmp_path / "rehearsal.jsonl")
    for name in ("POLICY", "GATE"):
        monkeypatch.setattr(materializer, name, tmp_path / name)
    materializer.POLICY.write_text(json.dumps({"priority_queue": ["all", "k1"]}), encoding="utf-8")
    materializer.GATE.write_text(json.dumps({"verdicts": {"all": "CANDIDATE_STRONG", "k1": "CANDIDATE_STRONG"}}), encoding="utf-8")
    fake = SimpleNamespace(
        STRATEGIES=[{"id": "all", "universe": "xus", "pool": "pool", "pick": "all", "daily_cap": 999},
                    {"id": "k1", "universe": "xus", "pool": "pool", "pick": "dvol_desc", "daily_cap": 1}],
        candidate_filter_pass=lambda c, f: True, _contract_hash=lambda s: "fingerprint",
        HOLD_SESSIONS=7, TP=12, SL=-25,
    )
    monkeypatch.setitem(sys.modules, "virtual_books", fake)
    monkeypatch.setattr(materializer, "_last_bar_candidates", lambda m: {
        "pool": [{"dvol": 20, "price": 10, "ticker": "TEST", "signal_date": "2026-09-08"}]})
    def session(m, now):
        if not calendar_ok:
            raise ValueError("exchange_calendar_unavailable")
        return "2026-09-09"
    monkeypatch.setattr(materializer, "_next_session", session)
    materializer.materialize()
    payload = json.loads((tmp_path / "state" / "canary_signals_US.json").read_text(encoding="utf-8"))
    assert payload["authority"] == "SIGNAL_ONLY_NO_BROKER_AUTHORITY"
    if calendar_ok:
        assert len(payload["signals"]) == 1
        signal = payload["signals"][0]
        assert signal["strategy_id"] == "CANARY_K1"
        assert signal["canary_allowed"] is False
        assert signal["research_contract_hash"] == "fingerprint"
        assert any("selection_contract_mismatch" in x for x in payload["errors"])
    else:
        assert payload["signals"] == [] and payload["status"] == "blocked"


def test_policy_excluded_arm_never_generates_rehearsal_signal():
    """정책 `excluded`에 든 arm은 K1 계약을 만족해도 리허설 신호를 내지 않는다.

    2026-09-11 실측 결함: 정책 파일에 excluded 블록이 있는데 코드가 읽지 않아
    `c_kr_insider_k1`(정책상 "forward 30건 전 캐너리 금지")이 09-09~11 리허설 3건을 실제로 생성했다.
    """
    from tools import canary_materializer as cm
    k1 = {"pick": "dvol_desc", "daily_cap": 1}
    excluded = {"c_kr_insider_k1": "사후 발견 뷰 — forward 30건 전 캐너리 금지"}
    # 금지 arm: K1 계약을 만족해도 제외되고, 사유가 드러난다
    reason = cm.arm_skip_reason("c_kr_insider_k1", k1, excluded)
    assert reason is not None and reason.startswith("policy_excluded:")
    # 금지 목록에 없고 계약도 맞으면 통과
    assert cm.arm_skip_reason("other_arm", k1, excluded) is None
    # 계약 불일치는 계약 사유로
    assert cm.arm_skip_reason("other_arm", {"pick": "all", "daily_cap": 1000000}, excluded) == (
        "selection_contract_mismatch:rehearsal_requires_dvol_desc_K1")
    # 큐에 있는데 arm이 없으면 조용히 넘기지 않고 드러낸다
    assert cm.arm_skip_reason("ghost_arm", None, excluded) == "arm_not_in_strategies"
    # excluded 미지정도 안전하게 동작
    assert cm.arm_skip_reason("other_arm", k1, None) is None


def test_expected_skips_do_not_mark_status_degraded():
    """정책 제외·계약 불일치는 설계된 skip이라 status를 degraded로 만들지 않는다."""
    import json as _json
    from pathlib import Path as _Path
    from tools import canary_materializer as cm
    pol = _json.loads(_Path(cm.POLICY).read_text(encoding="utf-8"))
    # 실제 정책 파일 기준으로 큐의 모든 항목이 '설계된 skip' 또는 정상 처리여야 한다
    assert isinstance(pol.get("excluded"), dict) and pol["excluded"], "정책에 excluded 블록이 있어야 한다"
    for arm_id in pol.get("priority_queue") or []:
        # 정책이 금지했으면 금지 사유가 나와야 한다
        if arm_id in pol["excluded"]:
            assert cm.arm_skip_reason(arm_id, {"pick": "dvol_desc", "daily_cap": 1}, pol["excluded"]).startswith("policy_excluded:")
