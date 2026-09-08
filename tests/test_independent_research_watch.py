import json
import sys
from datetime import datetime, timezone

import pytest

from tools import independent_research_watch as watch


def test_missing_inputs_visible_and_rerun_idempotent(monkeypatch, tmp_path):
    output, ledger = tmp_path / "report.json", tmp_path / "ledger.jsonl"
    monkeypatch.setattr(sys, "argv", ["watch", "--input", str(tmp_path / "absent.json"),
                                     "--output", str(output), "--ledger", str(ledger)])
    assert watch.main() == 0
    assert watch.main() == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["input_error"]
    assert all(s["status"] == "BLOCKED" and not s["signals"] for s in result["strategies"])
    rows = ledger.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["kind"] == "RESEARCH_OBSERVATION_NOT_TRADE"


def test_calendar_blocks_missing_first_open(monkeypatch):
    from preopen import scheduler
    from tools import canary_materializer
    close = datetime(2026, 9, 8, 20, tzinfo=timezone.utc)
    now = datetime(2026, 9, 9, 16, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler, "_exchange_session_close_dt",
                        lambda market, day: close if day == "2026-09-08" else None)
    monkeypatch.setattr(canary_materializer, "_next_session",
                        lambda market, at: "2026-09-10" if at == now else "2026-09-09")
    with pytest.raises(ValueError, match="first_entry_open_already_passed"):
        watch.validate_calendar({"asof_session": "2026-09-08", "entry_session": "2026-09-10"}, now)


def test_missed_open_allows_only_diagnostics(monkeypatch, tmp_path):
    output, ledger, src = tmp_path / "report.json", tmp_path / "ledger.jsonl", tmp_path / "input.json"
    src.write_text('{}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["watch", "--input", str(src), "--output", str(output), "--ledger", str(ledger)])
    def invalid(s, now):
        raise ValueError("first_entry_open_already_passed")
    monkeypatch.setattr(watch, "validate_calendar", invalid)
    monkeypatch.setattr(watch, "evaluate", lambda s, now: {"input_hash": "hash", "strategies": [
        {"strategy_id": "r_multiasset_trend_v1", "contract_hash": "a", "status": "RESEARCH_TARGET", "signals": [{"buy": True}], "rejections": []},
        {"strategy_id": "r_earnings_quality_drift_v1", "contract_hash": "b", "status": "RESEARCH_CANDIDATE", "signals": [{"buy": True}], "rejections": []}]})
    watch.main()
    strategies = json.loads(output.read_text(encoding="utf-8"))["strategies"]
    assert strategies[0]["status"] == "OBSERVING_ENTRY_WINDOW_CLOSED"
    assert strategies[1]["status"] == "BLOCKED"
    assert all(not s["signals"] for s in strategies)
