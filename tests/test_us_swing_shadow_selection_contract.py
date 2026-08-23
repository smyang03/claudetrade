"""shadow 원장과 실주문이 **같은 종목**을 고르는지 — 계약 정합 테스트.

2026-08-23 (Codex 리뷰 P1-3). 08-20에 거래대금 밴드·MAX 하한이 실주문에만 붙어,
같은 세션에 shadow=VOYG(rank1) / live=MXL(rank3, 밴드 1순위)로 갈라졌다. 판정 리포트가
읽는 것은 shadow 손익이므로, 우리가 실제로 쓰지 않는 전략의 성과를 30건 표본에 쌓고
있었다. 사전등록 코호트 정의 1항("실주문과 동일 계약 전체로 선정된 건만")에 정면으로
반한다.

여기서 지키는 계약:
  1. 선별이 켜지면 shadow도 밴드/MAX를 적용해 원 rank1이 아닌 종목을 고른다.
  2. 밴드가 전부 걸러내면 그날은 적격 0건이다(라이브의 SKIPPED와 대칭).
  3. 선별이 꺼져 있으면 rank1 — 이전 동작 그대로(fail-open).
  4. 선별 정책이 바뀌면 계약 지문(contract_id)이 바뀐다 — 다른 선별 구간을 한 평균에
     섞지 않기 위해서다.
"""

from __future__ import annotations

import sqlite3

import pytest

from runtime.us_swing_execution_contract import resolve_execution_contract
from runtime.us_swing_order_bridge import EnvRuntimeConfig, resolve_selection_policy
from tools.us_swing_shadow_runner import annotate_execution_shadow, ensure_schema

_POLICY = {
    "authority_caps": {"micro": {"size_multiplier": 0.1}},
    "execution_contract": {"max_hold_sessions": 5},
}


def _con_with_pool() -> sqlite3.Connection:
    """rank1은 밴드 밖(거래대금 과대), rank3이 밴드 안 — 라이브 08-20과 같은 모양."""
    con = sqlite3.connect(":memory:")
    ensure_schema(con)
    con.executemany(
        """INSERT INTO signals(signal_date,ticker,feature_date,model_version,rank,created_at,
            status,reference_close) VALUES (?,?,?,?,?,?,?,?)""",
        [
            ("2026-01-05", "BIGVOL", "2026-01-02", "m", 1, "now", "PENDING", 10.0),
            ("2026-01-05", "TINYVOL", "2026-01-02", "m", 2, "now", "PENDING", 10.0),
            ("2026-01-05", "INBAND", "2026-01-02", "m", 3, "now", "PENDING", 10.0),
        ],
    )
    # candidate_pool_all은 ensure_schema가 이미 만든다(recorded_at NOT NULL 포함).
    # 여기서 CREATE TABLE을 다시 쓰면 프로덕션과 다른 픽스처가 되어 제약을 못 본다.
    con.executemany(
        "INSERT INTO candidate_pool_all(session_date,ticker,dollar_vol,recorded_at) VALUES (?,?,?,?)",
        [
            ("2026-01-05", "BIGVOL", 2_000e6, "now"),   # 밴드 위 — 배제
            ("2026-01-05", "TINYVOL", 10e6, "now"),     # 밴드 아래 — 배제
            ("2026-01-05", "INBAND", 250e6, "now"),     # 100~500M — 통과
        ],
    )
    con.commit()
    return con


def _enable_band(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("US_SWING_DVOL_BAND_ENABLED", "true")
    monkeypatch.setenv("US_SWING_DVOL_BAND_MIN_M", "100")
    monkeypatch.setenv("US_SWING_DVOL_BAND_MAX_M", "500")
    monkeypatch.setenv("US_SWING_MAX_FLOOR_ENABLED", "false")


def test_shadow_follows_band_selection_not_raw_rank1(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_band(monkeypatch)
    con = _con_with_pool()
    shadow = annotate_execution_shadow(
        con, signal_date="2026-01-05", fx_map={"2026-01-05": 1000.0}, policy=_POLICY
    )
    assert shadow["selected"]["ticker"] == "INBAND"
    # 원 rank는 보존된다 — 귀속 태그가 rank3임을 알 수 있어야 한다.
    assert shadow["selected"]["rank"] == 3
    assert shadow["selected"]["selection_applied"] is True
    assert shadow["policy"] == "contract_selection_v1"

    rows = dict(
        con.execute("SELECT ticker,execution_shadow_eligible FROM signals").fetchall()
    )
    assert rows == {"BIGVOL": 0, "TINYVOL": 0, "INBAND": 1}


def test_shadow_records_zero_eligible_when_band_empties_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_band(monkeypatch)
    # 밴드를 아무도 통과 못 하는 구간으로 좁힌다 — 라이브는 이날 SKIPPED다.
    monkeypatch.setenv("US_SWING_DVOL_BAND_MIN_M", "600")
    monkeypatch.setenv("US_SWING_DVOL_BAND_MAX_M", "700")
    con = _con_with_pool()
    shadow = annotate_execution_shadow(
        con, signal_date="2026-01-05", fx_map={"2026-01-05": 1000.0}, policy=_POLICY
    )
    assert shadow["selected"] == {}
    eligible = [r[0] for r in con.execute("SELECT execution_shadow_eligible FROM signals")]
    assert eligible == [0, 0, 0]
    reasons = {r[0] for r in con.execute("SELECT execution_shadow_reason FROM signals")}
    assert reasons == {"dvol_band_no_candidate"}


def test_selection_disabled_keeps_rank1_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("US_SWING_DVOL_BAND_ENABLED", "false")
    monkeypatch.setenv("US_SWING_MAX_FLOOR_ENABLED", "false")
    con = _con_with_pool()
    shadow = annotate_execution_shadow(
        con, signal_date="2026-01-05", fx_map={"2026-01-05": 1000.0}, policy=_POLICY
    )
    assert shadow["selected"]["ticker"] == "BIGVOL"
    assert shadow["policy"] == "rank1_skip_v1"


def test_selection_policy_changes_contract_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    common = dict(
        policy=_POLICY,
        effective_mode="micro",
        configured_max_order_krw=760_000.0,
        base_order_budget_krw=500_000.0,
    )
    baseline = resolve_execution_contract(**common)["contract_id"]

    monkeypatch.setenv("US_SWING_DVOL_BAND_ENABLED", "false")
    monkeypatch.setenv("US_SWING_MAX_FLOOR_ENABLED", "false")
    off = resolve_execution_contract(
        **common, selection_policy=resolve_selection_policy(EnvRuntimeConfig())
    )["contract_id"]

    _enable_band(monkeypatch)
    on = resolve_execution_contract(
        **common, selection_policy=resolve_selection_policy(EnvRuntimeConfig())
    )["contract_id"]

    assert off != on, "선별을 켜고 끄면 지문이 달라져야 한다"
    # selection_policy를 안 넘기면 기존 지문 그대로 — 이력이 끊기지 않는다.
    assert baseline == resolve_execution_contract(**common)["contract_id"]
    assert baseline not in (off, on)
