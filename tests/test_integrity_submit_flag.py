# -*- coding: utf-8 -*-
"""integrity_check — 무청산이 "의도된 OFF"인지 "고장난 OFF"인지 가른다.

09-01 운영자 결정(커밋 `603d22f`)으로 두 sleeve의 `*_SUBMIT_ENABLED`가 false다.
그러면 신규 실체결이 없으니 CLOSED 이벤트가 안 쌓이는 게 정상인데, stale 검사가
이걸 FAIL로 올려 **종합 판정이 상시 FAIL로 켜져 있었다.** 상시 FAIL은 진짜 고장을 가린다.

가장 중요한 테스트는 마지막 두 개다 — **모르는 상태를 '의도된 OFF'로 오해하면
이번엔 반대 방향으로 눈을 감는다.** 스냅샷이 없거나 키가 빠졌으면 판단을 보류하고
기존 동작(FAIL)을 유지해야 한다.
"""
import json

import pytest

from tools import integrity_check as I


def _snap(monkeypatch, effective):
    """effective-config 스냅샷을 갈아끼운다. None이면 스냅샷 없음."""
    if effective is None:
        monkeypatch.setattr(I, "_latest_effective_config", lambda: ("", {}))
    else:
        monkeypatch.setattr(I, "_latest_effective_config",
                            lambda: ("effective_config_test_live.redacted.json",
                                     {k: str(v) for k, v in effective.items()}))


BOTH_OFF = {"US_SWING_ORDER_SUBMIT_ENABLED": "false", "KR_FALLEN_ORDER_SUBMIT_ENABLED": "false"}


def test_both_off_is_not_enabled(monkeypatch):
    _snap(monkeypatch, BOTH_OFF)
    on, why = I._submit_lanes_enabled()
    assert on is False
    assert "OFF" in why


@pytest.mark.parametrize("us,kr", [("true", "false"), ("false", "true"), ("true", "true")])
def test_any_on_stays_enabled(monkeypatch, us, kr):
    """한쪽이라도 켜져 있으면 무청산은 여전히 결함 신호다."""
    _snap(monkeypatch, {"US_SWING_ORDER_SUBMIT_ENABLED": us, "KR_FALLEN_ORDER_SUBMIT_ENABLED": kr})
    on, why = I._submit_lanes_enabled()
    assert on is True
    assert "제출 ON" in why


def test_missing_snapshot_holds_judgment(monkeypatch):
    """스냅샷이 없으면 '의도된 OFF'로 단정하지 않는다 — 기존 동작(FAIL) 유지."""
    _snap(monkeypatch, None)
    on, why = I._submit_lanes_enabled()
    assert on is True
    assert "판단보류" in why


def test_missing_key_holds_judgment(monkeypatch):
    """키 하나가 빠져도 판단보류다. 없는 키를 false로 읽으면 눈을 감는다."""
    _snap(monkeypatch, {"US_SWING_ORDER_SUBMIT_ENABLED": "false"})
    on, why = I._submit_lanes_enabled()
    assert on is True
    assert "판단보류" in why
    assert "KR_FALLEN_ORDER_SUBMIT_ENABLED" in why


def test_odd_values_are_not_treated_as_on(monkeypatch):
    """'False'/'0'/'' 같은 표기도 OFF로 읽는다(대소문자·공백 포함)."""
    _snap(monkeypatch, {"US_SWING_ORDER_SUBMIT_ENABLED": " False ",
                        "KR_FALLEN_ORDER_SUBMIT_ENABLED": "0"})
    assert I._submit_lanes_enabled()[0] is False


def _events_db(tmp_path, closed_at):
    """lifecycle_events만 있는 최소 DB."""
    import sqlite3
    p = tmp_path / "ev.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE lifecycle_events (event_type TEXT, occurred_at TEXT)")
    con.executemany("INSERT INTO lifecycle_events VALUES (?,?)",
                    [("FORWARD_MEASURED", closed_at), ("CLOSED", closed_at)])
    con.commit()
    con.close()
    return p


def _ml_db(tmp_path, synced_at):
    import sqlite3
    p = tmp_path / "ml.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE v2_learning_performance (synced_at TEXT)")
    con.execute("INSERT INTO v2_learning_performance VALUES (?)", (synced_at,))
    con.commit()
    con.close()
    return p


def _closed_row(tmp_path, monkeypatch, effective):
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    old = (now - timedelta(days=30)).isoformat()
    _snap(monkeypatch, effective)
    rows = I.check_job_freshness(_ml_db(tmp_path, old), _events_db(tmp_path, old),
                                 tmp_path / "missing_audit.db", now)
    return next(r for r in rows if r["check"].startswith("CLOSED 이벤트"))


def test_stale_closed_downgraded_when_submit_off(tmp_path, monkeypatch):
    """제출 OFF면 30일 무청산도 OK로 내린다 — 사유를 detail에 남긴다."""
    row = _closed_row(tmp_path, monkeypatch, BOTH_OFF)
    assert row["status"] == I.OK
    assert "의도" in row["detail"] and "결함 아님" in row["detail"]


def test_stale_closed_still_fails_when_submit_on(tmp_path, monkeypatch):
    """제출 ON이면 그대로 FAIL이다 — 이게 유지돼야 수리가 의미 있다."""
    row = _closed_row(tmp_path, monkeypatch,
                      {"US_SWING_ORDER_SUBMIT_ENABLED": "true",
                       "KR_FALLEN_ORDER_SUBMIT_ENABLED": "false"})
    assert row["status"] == I.FAIL


def test_other_freshness_checks_untouched(tmp_path, monkeypatch):
    """FORWARD_MEASURED·학습 sync는 제출 플래그와 무관하다 — 같이 내리면 안 된다."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    old = (now - timedelta(days=30)).isoformat()
    _snap(monkeypatch, BOTH_OFF)
    rows = I.check_job_freshness(_ml_db(tmp_path, old), _events_db(tmp_path, old),
                                 tmp_path / "missing_audit.db", now)
    others = [r for r in rows if not r["check"].startswith("CLOSED 이벤트")]
    assert others and all(r["status"] == I.FAIL for r in others)


def test_snapshot_payload_shape_is_what_we_read(tmp_path, monkeypatch):
    """_latest_effective_config가 읽는 실제 파일 모양을 고정한다(payload['effective'])."""
    cfg = tmp_path / "logs" / "config"
    cfg.mkdir(parents=True)
    (cfg / "effective_config_20260911_000000_live.redacted.json").write_text(
        json.dumps({"effective": BOTH_OFF}), encoding="utf-8")
    monkeypatch.setattr(I, "ROOT", tmp_path)
    name, eff = I._latest_effective_config()
    assert name.startswith("effective_config_")
    assert eff["US_SWING_ORDER_SUBMIT_ENABLED"] == "false"
