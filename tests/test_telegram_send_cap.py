# -*- coding: utf-8 -*-
"""텔레그램 일일 발송 상한 (2026-09-02 텔레그램 정리)."""
from __future__ import annotations

import json
from pathlib import Path

import telegram_reporter as tg


def _arm(monkeypatch, tmp_path: Path, cap: int):
    posted: list[dict] = []

    class _Resp:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(tg, "TOKEN", "t")
    monkeypatch.setattr(tg, "CHAT_ID", "c")
    monkeypatch.setattr(tg, "DAILY_SEND_CAP", cap)
    monkeypatch.setattr(tg, "_SEND_COUNTER_PATH", tmp_path / "counter.json")
    monkeypatch.setattr(tg, "_cap_warned_date", "")
    monkeypatch.setattr(tg.requests, "post", lambda url, json=None, timeout=0: posted.append(json) or _Resp())
    return posted


def test_cap_drops_noncritical_after_limit(monkeypatch, tmp_path):
    posted = _arm(monkeypatch, tmp_path, cap=2)
    assert tg.send("a") and tg.send("b")
    assert tg.send("c") is False           # 3번째는 드롭
    assert tg.send("d", critical=True)     # 긴급은 우회
    assert [p["text"] for p in posted] == ["a", "b", "d"]
    data = json.loads((tmp_path / "counter.json").read_text(encoding="utf-8"))
    assert data["count"] == 4              # 드롭도 카운트(감사용)


def test_counter_resets_on_new_date(monkeypatch, tmp_path):
    posted = _arm(monkeypatch, tmp_path, cap=1)
    (tmp_path / "counter.json").write_text(json.dumps({"date": "2000-01-01", "count": 99}), encoding="utf-8")
    assert tg.send("fresh")                # 날짜 바뀌면 0부터
    assert len(posted) == 1


def test_rehearsal_alert_format():
    text = tg.rehearsal_pick_alert("US", "ABCD", 3, 12.34, reason="submit_disabled_virtual_mode")
    assert "REHEARSAL" in text and "실주문 없음" in text and "ABCD" in text and "3주" in text
