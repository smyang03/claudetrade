"""교훈 forward-validation 레이어 테스트.

핵심 안전계약 검증: 기본 OFF면 라이브 영향 0(get_runtime_adjustments={}), enforce일 때만
valid_apply 셀이 bounded 조정을 반환, invalid_block은 조정 없음, 조정은 bound 내 clamp.
"""

import os

import pytest

from minority_report import lesson_validation as lv


def _clear_env():
    for k in ("LESSON_VALIDATION_ENABLED", "LESSON_VALIDATION_APPLY_MODE"):
        os.environ.pop(k, None)


@pytest.fixture(autouse=True)
def _env_reset():
    _clear_env()
    yield
    _clear_env()


# ---- 순수 스코어링 ----

def test_counterfactual_gain_is_median_difference():
    # 했더라면 중앙 +2.0, 실제 중앙 +0.5 → gain +1.5
    assert lv.counterfactual_gain([1.0, 2.0, 3.0], [0.0, 0.5, 1.0]) == pytest.approx(1.5)


def test_counterfactual_gain_none_when_empty():
    assert lv.counterfactual_gain([], [1.0]) is None
    assert lv.counterfactual_gain([1.0], []) is None


def test_score_cell_valid_apply_when_gain_positive():
    wo = [3.0] * 40  # 했더라면 +3
    tr = [0.0] * 15  # 실제 0
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", wo, tr,
                         sessions_confirmed=2)
    assert cell["verdict"] == "valid_apply"
    assert cell["counterfactual_gain"] == pytest.approx(3.0)
    assert 0.0 < cell["confidence"] <= 1.0


def test_score_cell_invalid_block_when_gain_negative():
    wo = [-3.0] * 40
    tr = [0.0] * 15
    cell = lv.score_cell("watch_only_missed_runup_ratio", "KR", "STRONG_UP", wo, tr,
                         sessions_confirmed=2)
    assert cell["verdict"] == "invalid_block"


def test_score_cell_insufficient_when_below_sample_floor():
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "DOWN", [2.0] * 5, [1.0] * 2,
                         sessions_confirmed=2)
    assert cell["verdict"] == "insufficient"


def test_score_cell_pending_when_sessions_not_confirmed():
    wo = [3.0] * 40
    tr = [0.0] * 15
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", wo, tr,
                         sessions_confirmed=1, min_sessions=2)
    assert cell["verdict"] == "pending"


def test_marginal_when_gain_positive_but_would_be_below_cost():
    # gain +지만 would_be(=0.2)가 비용(0.5) 미달 → '덜 잃음'일 뿐 marginal(자동적용 안 함)
    wo = [0.2] * 40
    tr = [-2.0] * 15
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", wo, tr,
                         sessions_confirmed=2, cost_floor=0.5)
    assert cell["verdict"] == "marginal"


def test_valid_apply_requires_would_be_above_cost():
    wo = [1.0] * 40  # would_be 1.0 >= 비용 0.5 → 절대수익
    tr = [-1.0] * 15
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", wo, tr,
                         sessions_confirmed=2, cost_floor=0.5)
    assert cell["verdict"] == "valid_apply"
    assert cell["would_be_med"] == pytest.approx(1.0)


def test_marginal_produces_no_adjustment(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [0.2] * 40, [-2.0] * 15,
                         sessions_confirmed=2, cost_floor=0.5)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    assert lv.get_runtime_adjustments("US", "STRONG_UP", db_path=db) == {}


def test_sign_consistency_counts_same_sign_only():
    # overall +; sub들 [+, +, -] → 같은부호 2
    assert lv.sign_consistency_sessions([1.0, 2.0, -3.0], overall_gain=1.5) == 2
    # overall -; sub들 [-, -, +, None] → 2
    assert lv.sign_consistency_sessions([-1.0, -2.0, 3.0, None], overall_gain=-1.0) == 2
    assert lv.sign_consistency_sessions([], overall_gain=1.0) == 0


def test_flipping_cell_stays_pending(tmp_path):
    # 부호 뒤집히는 셀 = 같은부호 1회뿐 → min_sessions 미달 pending(불안정 차단)
    wo = [3.0] * 40
    tr = [0.0] * 15
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", wo, tr,
                         sessions_confirmed=1, min_sessions=2, cost_floor=0.0)
    assert cell["verdict"] == "pending"


def test_score_cell_neutral_when_gain_small():
    wo = [0.3] * 40
    tr = [0.0] * 15
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "UP", wo, tr,
                         sessions_confirmed=2, gain_threshold=1.0)
    assert cell["verdict"] == "neutral"


# ---- 라이브 훅 안전계약 ----

def test_runtime_adjustments_empty_when_disabled(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    # 기본 OFF → 반드시 {}
    assert lv.get_runtime_adjustments("US", "STRONG_UP", db_path=db) == {}


def test_runtime_adjustments_empty_in_shadow_mode(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "shadow"
    # shadow는 관측만 → 적용 훅은 여전히 {}
    assert lv.get_runtime_adjustments("US", "STRONG_UP", db_path=db) == {}


def test_runtime_adjustments_applied_only_in_enforce(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    adj = lv.get_runtime_adjustments("US", "STRONG_UP", db_path=db)
    assert "entry_priority_cutoff_adjust" in adj
    # 방향: 완화(음수) + bound 내
    assert -0.05 <= adj["entry_priority_cutoff_adjust"] < 0.0


def test_invalid_block_produces_no_adjustment(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "KR", "STRONG_UP", [-5.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    # invalid_block 셀은 조정 없음
    assert lv.get_runtime_adjustments("KR", "STRONG_UP", db_path=db) == {}


def test_adjustment_clamped_within_bound(tmp_path):
    db = str(tmp_path / "lv.db")
    # 극단 gain이어도 bound(0.05) 초과 금지
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "DOWN", [50.0] * 100, [0.0] * 100,
                         sessions_confirmed=5)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    adj = lv.get_runtime_adjustments("US", "DOWN", db_path=db)
    assert abs(adj["entry_priority_cutoff_adjust"]) <= 0.05


def test_index_momentum_regime_buckets():
    assert lv.index_momentum_regime(None) is None
    assert lv.index_momentum_regime(5.0) == "STRONG_UP"
    assert lv.index_momentum_regime(1.0) == "UP"
    assert lv.index_momentum_regime(-1.0) == "DOWN"
    assert lv.index_momentum_regime(-5.0) == "STRONG_DOWN"


def test_block_priority_suppresses_control(tmp_path):
    # 같은 control(cutoff)에 valid_apply와 invalid_block 공존 → block 우선(무조정)
    db = str(tmp_path / "lv.db")
    valid = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                          sessions_confirmed=2)
    block = lv.score_cell("trade_ready_signal_conversion", "US", "STRONG_UP", [-3.0] * 40, [0.0] * 15,
                          sessions_confirmed=2)
    lv.upsert_cells([valid, block], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    # 둘 다 entry_priority_cutoff_adjust 매핑 → block 존재로 차단
    assert lv.get_runtime_adjustments("US", "STRONG_UP", db_path=db) == {}


def test_shadow_adjustments_present_when_enabled(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "shadow"
    # shadow에선 get_shadow_adjustments는 값 있음(로깅용), 단 runtime 적용은 {}
    assert lv.get_shadow_adjustments("US", "STRONG_UP", db_path=db) != {}
    assert lv.get_runtime_adjustments("US", "STRONG_UP", db_path=db) == {}


def test_apply_to_tuner_noop_when_disabled(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    base = {"entry_priority_cutoff_adjust": 0.0}
    out = lv.apply_to_tuner_overrides(dict(base), "US", "STRONG_UP", db_path=db)
    assert out == base  # disabled → 무접촉


def test_apply_to_tuner_noop_when_regime_none(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    base = {"entry_priority_cutoff_adjust": 0.0}
    out = lv.apply_to_tuner_overrides(dict(base), "US", None, db_path=db)
    assert out == base  # regime None → no-op(안전)


def test_apply_to_tuner_shadow_does_not_change_result(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "shadow"
    base = {"entry_priority_cutoff_adjust": 0.0}
    out = lv.apply_to_tuner_overrides(dict(base), "US", "STRONG_UP", db_path=db)
    assert out == base  # shadow → result 무변경


def test_apply_to_tuner_enforce_adjusts_within_bound(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    base = {"entry_priority_cutoff_adjust": 0.0}
    out = lv.apply_to_tuner_overrides(dict(base), "US", "STRONG_UP", db_path=db)
    assert out["entry_priority_cutoff_adjust"] < 0.0
    assert abs(out["entry_priority_cutoff_adjust"]) <= 0.05


def test_regime_from_consensus_mode():
    assert lv.regime_from_consensus_mode(None) is None
    assert lv.regime_from_consensus_mode("") is None
    assert lv.regime_from_consensus_mode("MODERATE_BULL") == "risk_on"
    assert lv.regime_from_consensus_mode("MILD_BEAR") == "risk_off"
    assert lv.regime_from_consensus_mode("NEUTRAL") == "mixed"


def test_is_fresh_recent_and_stale():
    from datetime import datetime, timezone, timedelta
    recent = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    assert lv._is_fresh(recent) is True
    assert lv._is_fresh(old) is False
    assert lv._is_fresh(None) is False  # 없으면 보수적 stale


def test_stale_valid_cell_is_ignored_but_block_kept(tmp_path):
    import sqlite3
    from datetime import datetime, timezone, timedelta
    db = str(tmp_path / "lv.db")
    valid = lv.score_cell("watch_only_missed_runup_ratio", "US", "risk_on", [3.0] * 40, [0.0] * 15,
                          sessions_confirmed=2)
    block = lv.score_cell("trade_ready_signal_conversion", "US", "risk_off", [-3.0] * 40, [0.0] * 15,
                          sessions_confirmed=2)
    lv.upsert_cells([valid, block], db_path=db)
    # 두 셀 updated_at을 과거로 (stale)
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(timespec="seconds")
    con = sqlite3.connect(db)
    con.execute("UPDATE validated_lesson SET updated_at=?", (old,))
    con.commit()
    con.close()
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    # stale valid → 무시(디폴트 fallback)
    assert lv.get_runtime_adjustments("US", "risk_on", db_path=db) == {}
    # stale invalid_block → 함정방어는 유지(여전히 차단이라 조정 없음)
    assert lv.get_runtime_adjustments("US", "risk_off", db_path=db) == {}


def test_low_confidence_valid_cell_not_applied(tmp_path):
    import sqlite3
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "risk_on", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    # confidence를 임계 미만으로 강제
    con = sqlite3.connect(db)
    con.execute("UPDATE validated_lesson SET confidence=0.1")
    con.commit()
    con.close()
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    # 기본 min_confidence=0.3 > 0.1 → 적용 안 함(디폴트)
    assert lv.get_runtime_adjustments("US", "risk_on", db_path=db) == {}


def test_missing_db_returns_empty_cells():
    assert lv.get_validated_cells("US", "risk_on", db_path="data/__nonexistent_lv__.db") == []


def test_default_fallback_unmapped_control_unchanged(tmp_path):
    db = str(tmp_path / "lv.db")
    cell = lv.score_cell("watch_only_missed_runup_ratio", "US", "risk_on", [3.0] * 40, [0.0] * 15,
                         sessions_confirmed=2)
    lv.upsert_cells([cell], db_path=db)
    os.environ["LESSON_VALIDATION_ENABLED"] = "true"
    os.environ["LESSON_VALIDATION_APPLY_MODE"] = "enforce"
    # 매핑 안 된 control(size_adj)은 교훈이 건드리지 않음 → 기존값 유지
    base = {"size_adj": 0, "momentum_wait_adjust_min": 0}
    out = lv.apply_to_tuner_overrides(dict(base), "US", "risk_on", db_path=db)
    assert out["size_adj"] == 0 and out["momentum_wait_adjust_min"] == 0


def test_store_roundtrip_and_regime_indexing(tmp_path):
    db = str(tmp_path / "lv.db")
    c1 = lv.score_cell("watch_only_missed_runup_ratio", "US", "STRONG_UP", [3.0] * 40, [0.0] * 15,
                       sessions_confirmed=2)
    c2 = lv.score_cell("watch_only_missed_runup_ratio", "US", "UP", [-3.0] * 40, [1.0] * 15,
                       sessions_confirmed=2)
    lv.upsert_cells([c1, c2], db_path=db)
    su = lv.get_validated_cells("US", "STRONG_UP", db_path=db)
    up = lv.get_validated_cells("US", "UP", db_path=db)
    assert len(su) == 1 and su[0]["verdict"] == "valid_apply"
    assert len(up) == 1 and up[0]["verdict"] == "invalid_block"
