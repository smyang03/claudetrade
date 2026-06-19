"""청산 판단 채점기 — 네트워크 없는 코어 로직 검증."""

from __future__ import annotations

import json
import sys
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.exit_decision_scorer as eds


def test_decision_gain_symmetric_sign():
    # HOLD는 오르면 옳음(+), SELL은 내리면 옳음(+)
    assert eds.decision_gain("HOLD", 2.0) == 2.0
    assert eds.decision_gain("HOLD", -2.0) == -2.0
    assert eds.decision_gain("SELL", -2.0) == 2.0
    assert eds.decision_gain("SELL", 2.0) == -2.0
    assert eds.decision_gain("HOLD", None) is None


def test_parse_utc_treats_naive_as_kst():
    # naive ts(운영머신 KST)는 UTC-9로 변환돼야 한다(미국장 정렬).
    dt = eds.parse_utc("2026-06-19T00:41:17")
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 15 and dt.day == 18  # 00:41 KST → 전일 15:41 UTC


def test_market_inference():
    assert eds._market_of("AVGO", {}) == "US"
    assert eds._market_of("017900", {}) == "KR"
    assert eds._market_of("AVGO", {"market": "US"}) == "US"


def test_load_decisions_excludes_guards_and_keeps_anchor(tmp_path, monkeypatch):
    log = tmp_path / "decisions_2026-06-19.jsonl"
    recs = [
        # 재량 HOLD — 채점 대상
        {"ts": "2026-06-19T00:41:00", "ticker": "AVGO", "market": "US", "decision": "HOLD",
         "current": 392.0, "decision_stage": "INTRADAY_REVIEW",
         "triage": {"exit_driver": "bounded_hold"},
         "votes": {"bull": {"confidence": 0.6}, "bear": {"confidence": 0.4}}},
        # 강제 가드(loss_cap) — 제외돼야 함
        {"ts": "2026-06-19T00:42:00", "ticker": "MRVL", "market": "US", "decision": "SELL",
         "current": 70.0, "decision_stage": "AUTO_SELL_REVIEW",
         "triage": {"exit_driver": "loss_cap"}},
        # 강제 가드(hard_stop) — 제외돼야 함
        {"ts": "2026-06-19T00:43:00", "ticker": "INTC", "market": "US", "decision": "SELL",
         "current": 20.0, "triage": {"exit_driver": "hard_stop"}},
        # anchor 없음 — 제외
        {"ts": "2026-06-19T00:44:00", "ticker": "AAPL", "market": "US", "decision": "HOLD",
         "current": 0},
    ]
    with open(log, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    monkeypatch.setattr(eds, "LOG_GLOB", str(tmp_path / "decisions_*.jsonl"))

    out = eds._load_decisions("ALL")
    tickers = {d["ticker"] for d in out}
    assert tickers == {"AVGO"}  # 가드 2건 + anchor 없음 1건 제외
    d = out[0]
    assert d["decision"] == "HOLD" and d["anchor"] == 392.0
    assert d["currency"] == "USD"
    assert d["confidence"] == 0.5  # votes 평균
    assert d["exit_driver"] == "bounded_hold"


def test_load_decisions_market_filter(tmp_path, monkeypatch):
    log = tmp_path / "decisions_2026-06-19.jsonl"
    recs = [
        {"ts": "2026-06-19T00:41:00", "ticker": "AVGO", "market": "US", "decision": "HOLD", "current": 392.0},
        {"ts": "2026-06-19T09:41:00", "ticker": "005930", "market": "KR", "decision": "HOLD", "current": 70000.0},
    ]
    with open(log, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    monkeypatch.setattr(eds, "LOG_GLOB", str(tmp_path / "decisions_*.jsonl"))

    assert {d["ticker"] for d in eds._load_decisions("US")} == {"AVGO"}
    assert {d["ticker"] for d in eds._load_decisions("KR")} == {"005930"}
    assert len(eds._load_decisions("ALL")) == 2
