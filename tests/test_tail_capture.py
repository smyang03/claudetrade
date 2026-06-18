"""통합 꼬리-capture 청산 엔진 테스트.

안전계약: 기본 OFF=비활성 / shadow=결정만(실행 호출측) / activation 전 HOLD / 증명 후 trail /
하드스톱 / 마감 carry(강함+RISK_ON+토글) vs close / regime게이트 / carry 실행 서브게이트.
"""

import os

import pytest

from runtime import tail_capture as tc


def _clear():
    for k in list(os.environ):
        if k.startswith("TAIL_CAPTURE_"):
            os.environ.pop(k, None)


@pytest.fixture(autouse=True)
def _env():
    _clear()
    yield
    _clear()


def test_default_off_inactive():
    assert tc.mode() == "off"
    assert tc.is_active() is False
    # OFF면 shadow_decision None
    assert tc.shadow_decision({"entry": 100}, 105, "US") is None


def test_pre_activation_holds():
    # MFE 2% < activation 4% → HOLD(pre_activation), trail 미발동
    d = tc.evaluate_exit(market="US", entry=100, peak=102, current=101)
    assert d["action"] == "HOLD" and d["reason"] == "pre_activation"


def test_activation_then_trail_holds_above_trail():
    # MFE 6%(증명) but 현재가 trail(peak*0.97=103) 위 → HOLD(trailing_active)
    d = tc.evaluate_exit(market="US", entry=100, peak=106, current=104)
    assert d["action"] == "HOLD" and d["active"] is True


def test_trail_exit_when_giveback_breached():
    # peak 110(MFE10%), give 3% → trail 106.7, 현재 106 <= trail → EXIT
    d = tc.evaluate_exit(market="US", entry=100, peak=110, current=106)
    assert d["action"] == "EXIT" and d["reason"] == "tail_trail"


def test_hard_stop_always():
    # 현재 97 <= entry*0.98 → 하드스톱
    d = tc.evaluate_exit(market="US", entry=100, peak=100, current=97)
    assert d["action"] == "EXIT" and d["reason"] == "hard_stop"


def test_kr_tighter_give():
    # KR give 1.5% → peak 110, trail 108.35; 현재 108 <= trail → EXIT (US였으면 106.7이라 HOLD)
    d_kr = tc.evaluate_exit(market="KR", entry=100, peak=110, current=108)
    assert d_kr["action"] == "EXIT"
    d_us = tc.evaluate_exit(market="US", entry=100, peak=110, current=108)
    assert d_us["action"] == "HOLD"


def test_preclose_carry_when_strong_riskon():
    # 마감창 + net 5%(강함) + US carry true + RISK_ON → CARRY
    d = tc.evaluate_exit(market="US", entry=100, peak=106, current=105, mins_to_close=5, regime="risk_on")
    assert d["action"] == "CARRY"


def test_preclose_close_when_weak():
    # 마감창 + net 1%(<강함3%) → CLOSE (더드 회피)
    d = tc.evaluate_exit(market="US", entry=100, peak=104, current=101, mins_to_close=5, regime="risk_on")
    assert d["action"] == "CLOSE" and d["reason"] == "preclose_not_strong"


def test_preclose_no_carry_in_riskoff():
    # 마감창 + 강함 but RISK_OFF → CARRY 안 함(베타증폭 차단) → CLOSE
    d = tc.evaluate_exit(market="US", entry=100, peak=106, current=105, mins_to_close=5, regime="risk_off")
    assert d["action"] == "CLOSE"


def test_kr_no_carry_default():
    # KR carry 기본 false → 강해도 CLOSE
    d = tc.evaluate_exit(market="KR", entry=100, peak=106, current=105, mins_to_close=5, regime="risk_on")
    assert d["action"] == "CLOSE"


def test_carry_enforce_subgate_default_false():
    # 오버나잇 캐리 *실행* 서브게이트 기본 false (결정과 별개)
    os.environ["TAIL_CAPTURE_MODE"] = "enforce"
    assert tc.carry_enforce_enabled() is False


def test_shadow_decision_uses_observed_mfe():
    os.environ["TAIL_CAPTURE_MODE"] = "shadow"
    # observed_mfe 10% → peak=110, give 3% → trail 106.7, 현재 106 → EXIT
    pos = {"entry": 100, "observed_mfe_pct": 10.0, "ticker": "X"}
    d = tc.shadow_decision(pos, 106, "US")
    assert d is not None and d["action"] == "EXIT" and d["reason"] == "tail_trail"


def test_shadow_decision_active_logs_hold():
    os.environ["TAIL_CAPTURE_MODE"] = "shadow"
    pos = {"entry": 100, "observed_mfe_pct": 6.0, "ticker": "Y"}
    d = tc.shadow_decision(pos, 104, "US")
    assert d is not None and d["action"] == "HOLD"


def test_shadow_decision_us_krw_entry_without_native_breaks():
    # 회귀: US pos["entry"]는 원화 저장. native entry 미주입이면 달러 current와 단위가 엇갈려
    # net이 -99%로 깨지고 가짜 hard_stop이 뜬다(6/18 524건 버그 재현).
    os.environ["TAIL_CAPTURE_MODE"] = "shadow"
    pos = {"entry": 812321.82, "observed_mfe_pct": 1.43, "ticker": "AMD"}
    d = tc.shadow_decision(pos, 529.0, "US")
    assert d is not None and d["action"] == "EXIT" and d["reason"] == "hard_stop"


def test_shadow_decision_us_native_entry_fixes_units():
    # 수정: 호출측이 _position_entry_native로 변환한 달러 entry를 entry_native로 넘기면
    # net이 정상(~0%)이라 가짜 hard_stop이 사라지고 HOLD(pre_activation).
    os.environ["TAIL_CAPTURE_MODE"] = "shadow"
    pos = {"entry": 812321.82, "observed_mfe_pct": 1.43, "ticker": "AMD"}
    d = tc.shadow_decision(pos, 529.0, "US", entry_native=529.0)
    assert d is not None and d["action"] == "HOLD" and d["reason"] == "pre_activation"
    assert abs(d["net_pct"]) < 1.0


# ---- Track 3-R: hold advisor carry-intent 정합 ----
from minority_report import hold_advisor as _ha


def test_carry_align_mode_default_off():
    os.environ.pop("HOLD_ADVISOR_CARRY_ALIGN_MODE", None)
    assert _ha._carry_align_mode() == "off"


def test_carry_intent_hold_true_when_profit_mfe_riskon():
    triage = {"exit_category": "HOLD"}
    pos = {"entry": 100, "current_price": 105, "peak_pnl_pct": 6.0}  # 이익+MFE6%
    assert _ha._carry_intent_hold(triage, pos, {"regime": "risk_on"}) is True


def test_carry_intent_false_when_loss():
    triage = {"exit_category": "HOLD"}
    pos = {"entry": 100, "current_price": 98, "peak_pnl_pct": 6.0}  # 손실중
    assert _ha._carry_intent_hold(triage, pos, {"regime": "risk_on"}) is False


def test_carry_intent_false_when_mfe_below_activation():
    triage = {"exit_category": "HOLD"}
    pos = {"entry": 100, "current_price": 102, "peak_pnl_pct": 2.0}  # MFE 2%<4%
    assert _ha._carry_intent_hold(triage, pos, {"regime": "risk_on"}) is False


def test_carry_intent_false_when_riskoff():
    triage = {"exit_category": "HOLD"}
    pos = {"entry": 100, "current_price": 105, "peak_pnl_pct": 6.0}
    assert _ha._carry_intent_hold(triage, pos, {"regime": "risk_off"}) is False


def test_carry_intent_false_when_not_hold():
    triage = {"exit_category": "SELL"}
    pos = {"entry": 100, "current_price": 105, "peak_pnl_pct": 6.0}
    assert _ha._carry_intent_hold(triage, pos, {"regime": "risk_on"}) is False


# ---- carry execution (오버나잇) ----

def test_should_carry_default_false_when_off():
    os.environ.pop("TAIL_CAPTURE_MODE", None)
    pos = {"entry": 100, "observed_mfe_pct": 6.0}
    assert tc.should_carry_overnight(pos, 105, "US", "risk_on") is False


def test_should_carry_true_when_all_conditions():
    os.environ["TAIL_CAPTURE_MODE"] = "enforce"
    os.environ["TAIL_CAPTURE_CARRY_ENFORCE"] = "true"
    pos = {"entry": 100, "observed_mfe_pct": 6.0}  # net 5%>=3, mfe 6%>=4
    assert tc.should_carry_overnight(pos, 105, "US", "risk_on") is True


def test_should_carry_false_when_subgate_off():
    os.environ["TAIL_CAPTURE_MODE"] = "enforce"
    os.environ["TAIL_CAPTURE_CARRY_ENFORCE"] = "false"
    pos = {"entry": 100, "observed_mfe_pct": 6.0}
    assert tc.should_carry_overnight(pos, 105, "US", "risk_on") is False


def test_should_carry_false_in_kr():
    os.environ["TAIL_CAPTURE_MODE"] = "enforce"
    os.environ["TAIL_CAPTURE_CARRY_ENFORCE"] = "true"
    pos = {"entry": 100, "observed_mfe_pct": 6.0}
    assert tc.should_carry_overnight(pos, 105, "KR", "risk_on") is False


def test_should_carry_false_in_riskoff():
    os.environ["TAIL_CAPTURE_MODE"] = "enforce"
    os.environ["TAIL_CAPTURE_CARRY_ENFORCE"] = "true"
    pos = {"entry": 100, "observed_mfe_pct": 6.0}
    assert tc.should_carry_overnight(pos, 105, "US", "risk_off") is False


def test_should_carry_false_when_weak():
    os.environ["TAIL_CAPTURE_MODE"] = "enforce"
    os.environ["TAIL_CAPTURE_CARRY_ENFORCE"] = "true"
    pos = {"entry": 100, "observed_mfe_pct": 6.0}  # net 1%<3
    assert tc.should_carry_overnight(pos, 101, "US", "risk_on") is False
