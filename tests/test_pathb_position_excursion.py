"""Phase 1c: PathB 포지션 excursion(MFE/MAE) 관측 기록 테스트.

청산 트리거/profit_ladder 입력(peak_pnl_pct)을 건드리지 않고 observed_* 키에만
기록하는지, exit_meta가 observed를 우선 쓰는지 검증한다.
"""

import types
import unittest

from runtime.pathb_runtime import PathBRuntime


class PositionExcursionTests(unittest.TestCase):
    def setUp(self):
        # __init__을 우회해 _update_position_excursion / _pathb_exit_meta 단위만 검증한다.
        self.rt = PathBRuntime.__new__(PathBRuntime)

    def test_tracks_peak_low_without_touching_ladder_input(self):
        pos = {"entry": 100.0}
        self.rt._update_position_excursion(pos, 110.0, "KR")
        self.rt._update_position_excursion(pos, 95.0, "KR")
        self.rt._update_position_excursion(pos, 105.0, "KR")
        self.assertAlmostEqual(pos["observed_peak_price"], 110.0)
        self.assertAlmostEqual(pos["observed_low_price"], 95.0)
        self.assertAlmostEqual(pos["observed_mfe_pct"], 10.0)
        self.assertAlmostEqual(pos["observed_mae_pct"], -5.0)
        # profit_ladder가 읽는 입력은 절대 건드리지 않는다(보호 계약).
        self.assertNotIn("peak_pnl_pct", pos)
        self.assertNotIn("trough_pnl_pct", pos)

    def test_zero_or_negative_price_ignored(self):
        pos = {"entry": 100.0}
        self.rt._update_position_excursion(pos, 0.0, "KR")
        self.rt._update_position_excursion(pos, -5.0, "KR")
        self.assertNotIn("observed_peak_price", pos)

    def test_missing_entry_still_records_prices(self):
        pos = {}
        self.rt._update_position_excursion(pos, 50.0, "KR")
        # entry를 모르면 mfe/mae는 못 내지만 peak/low 추적은 유지된다.
        self.assertAlmostEqual(pos["observed_peak_price"], 50.0)
        self.assertNotIn("observed_mfe_pct", pos)

    def test_exit_meta_prefers_observed_over_legacy_peak(self):
        self.rt.bot = types.SimpleNamespace(risk=None)
        pos = {
            "sl": 0,
            "observed_mfe_pct": 10.0,
            "observed_mae_pct": -4.0,
            "peak_pnl_pct": 3.0,  # 레거시 입력(ladder용) — meta에는 유지되되 position_mfe는 observed 사용
        }
        meta = self.rt._pathb_exit_meta(pos, "US", "CLOSED_PROFIT_LADDER")
        self.assertAlmostEqual(meta["position_mfe_pct"], 10.0)
        self.assertAlmostEqual(meta["position_mae_pct"], -4.0)
        self.assertAlmostEqual(meta["peak_pnl_pct"], 3.0)

    def test_exit_meta_falls_back_to_legacy_when_no_observed(self):
        self.rt.bot = types.SimpleNamespace(risk=None)
        pos = {"sl": 0, "peak_pnl_pct": 2.5, "trough_pnl_pct": -1.5}
        meta = self.rt._pathb_exit_meta(pos, "US", "CLOSED_PROFIT_LADDER")
        self.assertAlmostEqual(meta["position_mfe_pct"], 2.5)
        self.assertAlmostEqual(meta["position_mae_pct"], -1.5)

    def test_exit_meta_recovers_from_durable_when_pos_none(self):
        # 청산 finalize 시 pos가 sync로 이미 제거된(None) 케이스: plan_json 영속값에서 복원.
        self.rt.bot = types.SimpleNamespace(risk=None)
        durable = {"observed_mfe_pct": 7.2, "observed_mae_pct": -1.1}
        meta = self.rt._pathb_exit_meta(None, "US", "CLOSED_CLAUDE_PRICE_PRE_CLOSE", durable=durable)
        self.assertAlmostEqual(meta["position_mfe_pct"], 7.2)
        self.assertAlmostEqual(meta["position_mae_pct"], -1.1)

    def test_exit_meta_pos_none_no_durable_does_not_crash(self):
        self.rt.bot = types.SimpleNamespace(risk=None)
        meta = self.rt._pathb_exit_meta(None, "US", "CLOSED_PROFIT_LADDER")
        self.assertEqual(meta["position_mfe_pct"], 0.0)
        self.assertEqual(meta["position_mae_pct"], 0.0)

    def test_exit_meta_prefers_live_pos_over_durable(self):
        self.rt.bot = types.SimpleNamespace(risk=None)
        pos = {"sl": 0, "observed_mfe_pct": 10.0, "observed_mae_pct": -4.0}
        durable = {"observed_mfe_pct": 1.0, "observed_mae_pct": -9.0}
        meta = self.rt._pathb_exit_meta(pos, "US", "CLOSED_PROFIT_LADDER", durable=durable)
        self.assertAlmostEqual(meta["position_mfe_pct"], 10.0)
        self.assertAlmostEqual(meta["position_mae_pct"], -4.0)


class _FakeStore:
    def __init__(self):
        self.calls = []

    def update_path_run(self, path_run_id, *, plan=None, merge_plan=False):
        self.calls.append((path_run_id, dict(plan or {}), merge_plan))


class PositionExcursionDurablePersistTests(unittest.TestCase):
    def setUp(self):
        self.rt = PathBRuntime.__new__(PathBRuntime)
        self.rt.store = _FakeStore()

    def test_persists_to_plan_json_on_new_extreme(self):
        pos = {"entry": 100.0, "pathb_path_run_id": "run-1"}
        self.rt._update_position_excursion(pos, 110.0, "KR")
        self.assertEqual(len(self.rt.store.calls), 1)
        path_run_id, plan, merge = self.rt.store.calls[0]
        self.assertEqual(path_run_id, "run-1")
        self.assertTrue(merge)
        self.assertAlmostEqual(plan["observed_peak_price"], 110.0)
        self.assertAlmostEqual(plan["observed_mfe_pct"], 10.0)

    def test_no_persist_without_path_run_id(self):
        pos = {"entry": 100.0}  # pathb_path_run_id 없음(브로커 주입 등)
        self.rt._update_position_excursion(pos, 110.0, "KR")
        self.assertEqual(self.rt.store.calls, [])

    def test_persists_only_when_extreme_changes(self):
        pos = {"entry": 100.0, "pathb_path_run_id": "run-1"}
        self.rt._update_position_excursion(pos, 110.0, "KR")  # 최초: 고점·저점 세팅 → 영속화
        self.rt._update_position_excursion(pos, 95.0, "KR")   # 새 저점 → 영속화
        self.rt._update_position_excursion(pos, 100.0, "KR")  # 고점·저점 불변 → 영속화 없음
        self.assertEqual(len(self.rt.store.calls), 2)

    def test_persist_failure_does_not_break_tracking(self):
        class _BoomStore:
            def update_path_run(self, *a, **k):
                raise RuntimeError("db locked")

        self.rt.store = _BoomStore()
        pos = {"entry": 100.0, "pathb_path_run_id": "run-1"}
        self.rt._update_position_excursion(pos, 120.0, "KR")  # 예외 삼키고 추적 유지
        self.assertAlmostEqual(pos["observed_mfe_pct"], 20.0)


if __name__ == "__main__":
    unittest.main()
