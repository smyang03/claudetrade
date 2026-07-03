"""반응형 red-tape 진입 게이트 테스트: on/off/band + market-scope + idx 계산.
design_red_tape_entry_gate_enforce_20260703.md §5-4. 게이트는 self 상태 무의존이라 __new__로 충분."""
from types import SimpleNamespace

from runtime.pathb_runtime import PathBRuntime


def _rt():
    return PathBRuntime.__new__(PathBRuntime)


def test_gate_off_by_default(monkeypatch):
    """토글 미설정(기본 off) → 아무리 빨강이어도 차단 안 함."""
    monkeypatch.delenv("PATHB_RED_TAPE_GATE_MODE_US", raising=False)
    assert _rt()._red_tape_entry_gate_block("US", -1.5) is False


def test_gate_enforce_blocks_below_threshold(monkeypatch):
    """enforce + idx < 임계(-0.3) → 차단."""
    monkeypatch.setenv("PATHB_RED_TAPE_GATE_MODE_US", "enforce")
    monkeypatch.delenv("PATHB_RED_TAPE_GATE_THRESHOLD_US", raising=False)  # 기본 -0.3
    assert _rt()._red_tape_entry_gate_block("US", -0.5) is True


def test_gate_band_not_blocked(monkeypatch):
    """enforce여도 -0.1~-0.3 밴드(임계 이상)는 shadow → 차단 안 함."""
    monkeypatch.setenv("PATHB_RED_TAPE_GATE_MODE_US", "enforce")
    assert _rt()._red_tape_entry_gate_block("US", -0.2) is False


def test_gate_green_not_blocked(monkeypatch):
    monkeypatch.setenv("PATHB_RED_TAPE_GATE_MODE_US", "enforce")
    assert _rt()._red_tape_entry_gate_block("US", 0.5) is False


def test_gate_none_idx(monkeypatch):
    monkeypatch.setenv("PATHB_RED_TAPE_GATE_MODE_US", "enforce")
    assert _rt()._red_tape_entry_gate_block("US", None) is False


def test_gate_kr_scope_off(monkeypatch):
    """US enforce여도 KR은 MODE_KR 미설정(off) → KR 진입 미차단(net 흑자시장 보호)."""
    monkeypatch.setenv("PATHB_RED_TAPE_GATE_MODE_US", "enforce")
    monkeypatch.delenv("PATHB_RED_TAPE_GATE_MODE_KR", raising=False)
    assert _rt()._red_tape_entry_gate_block("KR", -1.5) is False


def test_threshold_override(monkeypatch):
    """임계 override -0.1 → -0.2도 차단."""
    monkeypatch.setenv("PATHB_RED_TAPE_GATE_MODE_US", "enforce")
    monkeypatch.setenv("PATHB_RED_TAPE_GATE_THRESHOLD_US", "-0.1")
    assert _rt()._red_tape_entry_gate_block("US", -0.2) is True


def test_entry_tape_idx_session_open_reference():
    """idx = 현재값(전일종가기준 0.3) - 개장기준(1.2) = -0.9 (갭업 후 페이드)."""
    rt = _rt()
    rt.bot = SimpleNamespace(
        _index_history={"US": [1.2, 0.8, 0.3]},
        _session_open_index_change={"US": 1.2},
    )
    plan = SimpleNamespace(market="US")
    idx, now_val = rt._entry_tape_idx(plan)
    assert round(idx, 3) == -0.9
    assert now_val == 0.3


def test_entry_tape_idx_fallback_to_first_sample():
    """개장기준 None이면 deque 첫 샘플로 폴백."""
    rt = _rt()
    rt.bot = SimpleNamespace(
        _index_history={"US": [1.0, 0.5, 0.2]},
        _session_open_index_change={"US": None},
    )
    plan = SimpleNamespace(market="US")
    idx, now_val = rt._entry_tape_idx(plan)
    assert round(idx, 3) == -0.8  # 0.2 - 1.0


def test_entry_tape_idx_empty_history_returns_none():
    rt = _rt()
    rt.bot = SimpleNamespace(_index_history={}, _session_open_index_change={})
    assert rt._entry_tape_idx(SimpleNamespace(market="US")) is None
