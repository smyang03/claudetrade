from __future__ import annotations

from datetime import date

from tools import kr_ipo_alert as A


def _rec(name, sub, band="10,000~13,000", uw="한국투자증권", fixed="-"):
    return {"corp_name": name, "raw": [name, sub, fixed, band, "", uw, ""], "subscription": sub,
            "band": band, "underwriter": uw, "spac": "스팩" in name}


def test_parse_subscription_formats():
    assert A.parse_subscription("2026.12.14~12.15") == (date(2026, 12, 14), date(2026, 12, 15))
    assert A.parse_subscription("2026.12.31~01.02") == (date(2026, 12, 31), date(2027, 1, 2))
    assert A.parse_subscription("2026.12.14~2026.12.15") == (date(2026, 12, 14), date(2026, 12, 15))
    assert A.parse_subscription("미정") == (None, None)


def test_build_alerts_d1_kis_flag_and_spac_excluded():
    today = date(2026, 12, 11)  # 금 → 다음 영업일 12-14(월)
    sched = [
        _rec("데스틴파워", "2026.12.14~12.15", "8,000~11,000"),
        _rec("KTE", "2026.12.14~12.15", "19,000~23,000", uw="NH투자증권"),
        _rec("우리투자스팩1호", "2026.12.14~12.15", "2,000~2,000", uw="우리투자증권"),
        _rec("먼회사", "2026.12.21~12.22"),
    ]
    alerts = A.build_alerts(sched, [], today, {})
    names = {a["name"]: a for a in alerts}
    assert set(names) == {"데스틴파워", "KTE"}
    assert names["데스틴파워"]["kind"] == "sub_d1" and names["데스틴파워"]["kis"] is True
    assert names["KTE"]["kis"] is False
    assert names["데스틴파워"]["margin_krw"] == 55000  # 11,000 × 10 × 0.5


def test_build_alerts_d0_only_when_d1_not_sent_and_idempotent():
    today = date(2026, 12, 14)
    sched = [_rec("데스틴파워", "2026.12.14~12.15"), _rec("인터엑스", "2026.12.14~12.15")]
    sent = {"데스틴파워|sub_d1|2026-12-14": "2026-12-11"}
    alerts = A.build_alerts(sched, [], today, sent)
    assert [a["name"] for a in alerts] == ["인터엑스"]
    assert alerts[0]["kind"] == "sub_d0"
    sent[alerts[0]["key"]] = "2026-12-14"
    assert A.build_alerts(sched, [], today, sent) == []


def test_listing_alert_and_message():
    today = date(2026, 11, 11)
    upcoming = [{"corp_name": "넥스아이", "listing_date": "2026-11-12", "offer_price": 8000.0, "spac": False},
                {"corp_name": "OO스팩", "listing_date": "2026-11-12", "offer_price": 2000.0, "spac": True}]
    alerts = A.build_alerts([], upcoming, today, {})
    assert [a["kind"] for a in alerts] == ["list_d1"]
    msg = A.format_message(alerts, "regime")
    assert "넥스아이" in msg and "시초가" in msg


def test_fixed_price_preferred_over_band():
    rec = _rec("X", "2026.12.14~12.15", fixed="12,000")
    price, text = A.price_info(rec)
    assert price == 12000 and "확정" in text
