from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from test.audit_lab.cost_model import CostModel
from test.audit_lab.critical_flags import build_alert_plan, evaluate_critical_flags, should_request_claude_audit
from test.audit_lab.data_quality import validate_ohlcv_frame
from test.audit_lab.db import init_database, table_names
from test.audit_lab.event_engine import calc_stats, run_ticker_backtest
from test.audit_lab.regime_replay import ReplayRegimeClassifier, classify_regime
from test.audit_lab.reports import write_report_bundle
from test.audit_lab.universe import UniverseMember, build_live_universe
from test.audit_lab.walk_forward import run_walk_forward_on_frame, walk_forward_windows
from test.audit_lab.yfinance_collector import YFinanceDailyCollector


def _frame(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": date, "open": open_, "high": high, "low": low, "close": close, "volume": 1000}
            for date, open_, high, low, close in rows
        ]
    )


class AuditLabPhase1Test(unittest.TestCase):
    def test_next_open_entry_and_cost_adjusted_take_profit(self) -> None:
        df = _frame(
            [
                ("2024-01-01", 100, 101, 99, 100),
                ("2024-01-02", 100, 106, 99, 105),
                ("2024-01-03", 105, 106, 103, 104),
            ]
        )
        trades = run_ticker_backtest(
            df,
            market="KR",
            ticker="000000",
            strategy="unit",
            params={"tp_pct": 0.05, "sl_pct": 0.02, "max_hold": 3},
            signal_func=lambda _df, i, _params: i == 0,
            cost_model=CostModel(name="unit", buy_bps=10, sell_bps=10, sell_tax_bps=0, slippage_bps=0),
            respect_disabled_combos=False,
        )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entry_date"], "2024-01-02")
        self.assertEqual(trades[0]["reason"], "take_profit")
        self.assertAlmostEqual(trades[0]["gross_pnl_pct"], 5.0)
        self.assertAlmostEqual(trades[0]["net_pnl_pct"], 4.8)

    def test_same_bar_stop_and_target_uses_stop_loss(self) -> None:
        df = _frame(
            [
                ("2024-01-01", 100, 101, 99, 100),
                ("2024-01-02", 100, 106, 97, 103),
                ("2024-01-03", 103, 104, 102, 103),
            ]
        )
        trades = run_ticker_backtest(
            df,
            market="KR",
            ticker="000000",
            strategy="unit",
            params={"tp_pct": 0.05, "sl_pct": 0.02, "max_hold": 3},
            signal_func=lambda _df, i, _params: i == 0,
            cost_model=CostModel(name="none"),
            respect_disabled_combos=False,
        )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["reason"], "stop_loss_same_bar")
        self.assertAlmostEqual(trades[0]["net_pnl_pct"], -2.0)

    def test_entry_day_exit_can_be_deferred_for_audit_experiment(self) -> None:
        df = _frame(
            [
                ("2024-01-01", 100, 101, 99, 100),
                ("2024-01-02", 100, 106, 97, 103),
                ("2024-01-03", 103, 104, 102, 103),
            ]
        )
        trades = run_ticker_backtest(
            df,
            market="KR",
            ticker="000000",
            strategy="unit",
            params={"tp_pct": 0.05, "sl_pct": 0.02, "max_hold": 1},
            signal_func=lambda _df, i, _params: i == 0,
            cost_model=CostModel(name="none"),
            entry_day_exit_policy="defer",
            respect_disabled_combos=False,
        )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["reason"], "max_hold")
        self.assertEqual(trades[0]["held_days"], 1)
        self.assertAlmostEqual(trades[0]["net_pnl_pct"], 3.0)

    def test_stats_and_report_bundle(self) -> None:
        trades = [
            {"net_pnl_pct": 2.0, "reason": "take_profit"},
            {"net_pnl_pct": -1.0, "reason": "stop_loss"},
            {"net_pnl_pct": 1.0, "reason": "max_hold"},
        ]
        stats = calc_stats(trades)
        self.assertEqual(stats["n_trades"], 3)
        self.assertAlmostEqual(stats["win_rate"], 66.667)
        self.assertEqual(stats["profit_factor"], 3.0)

        with tempfile.TemporaryDirectory() as tmp:
            paths = write_report_bundle(
                {
                    "cost_model": "unit",
                    "entry_timing": "next_open",
                    "summary_rows": [{"market": "KR", "strategy": "unit", **stats}],
                },
                Path(tmp),
                name="phase1_unit",
            )
            for path in paths.values():
                self.assertTrue(Path(path).exists())


class AuditLabPhase2Test(unittest.TestCase):
    def test_regime_classifier_uses_previous_close_by_default(self) -> None:
        df = pd.DataFrame(
            [
                {"date": "2024-01-01", "close": 100, "ma60": 100},
                {"date": "2024-01-02", "close": 112, "ma60": 100},
                {"date": "2024-01-03", "close": 91, "ma60": 100},
            ]
        )
        classifier = ReplayRegimeClassifier.from_price_frame(df)

        self.assertEqual(classifier.mode_for("2024-01-01"), "NEUTRAL")
        self.assertEqual(classifier.mode_for("2024-01-02"), "NEUTRAL")
        self.assertEqual(classifier.mode_for("2024-01-03"), "AGGRESSIVE")

    def test_current_close_timing_is_explicit(self) -> None:
        df = pd.DataFrame([{"date": "2024-01-01", "close": 91, "ma60": 100}])
        classifier = ReplayRegimeClassifier.from_price_frame(df, timing="current_close")

        self.assertEqual(classifier.mode_for("2024-01-01"), "CAUTIOUS_BEAR")

    def test_regime_thresholds(self) -> None:
        self.assertEqual(classify_regime(111, 100), "AGGRESSIVE")
        self.assertEqual(classify_regime(106, 100), "MODERATE_BULL")
        self.assertEqual(classify_regime(96, 100), "MILD_BEAR")
        self.assertEqual(classify_regime(91, 100), "CAUTIOUS_BEAR")


class AuditLabPhase3Test(unittest.TestCase):
    def test_walk_forward_window_generation(self) -> None:
        windows = walk_forward_windows(start="2020-01-01", end="2024-12-31", train_years=3, test_years=1)

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].train_start, "2020-01-01")
        self.assertEqual(windows[0].test_start, "2023-01-01")
        self.assertEqual(windows[1].test_end, "2024-12-31")

    def test_walk_forward_runs_fixed_params_without_optimization(self) -> None:
        dates = pd.date_range("2020-01-01", "2024-12-31", freq="7D")
        df = pd.DataFrame(
            {
                "date": dates,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000,
            }
        )
        rows = run_walk_forward_on_frame(
            df,
            market="KR",
            ticker="000000",
            strategy="unit",
            start="2020-01-01",
            end="2024-12-31",
            params={"tp_pct": 0.10, "sl_pct": 0.10, "max_hold": 1},
            signal_func=lambda _df, i, _params: i % 8 == 0,
            cost_model=CostModel(name="none"),
        )

        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]["optimized"])
        self.assertIn("train_stats", rows[0])
        self.assertIn("test_stats", rows[0])
        self.assertGreater(rows[0]["train_stats"]["n_trades"], 0)
        self.assertGreater(rows[0]["test_stats"]["n_trades"], 0)


class AuditLabPhase4Test(unittest.TestCase):
    def test_critical_flags_trigger_local_alert_plan_only(self) -> None:
        flags = evaluate_critical_flags(
            {"n_trades": 8, "profit_factor": 0.82, "max_drawdown_pct": -31.0},
            walk_forward_rows=[{"pf_ratio_test_to_train": 0.52}],
            min_trades=30,
        )
        codes = {flag["code"] for flag in flags}
        alert = build_alert_plan(flags)

        self.assertIn("LOW_SAMPLE", codes)
        self.assertIn("COST_ADJUSTED_PF_BELOW_1", codes)
        self.assertIn("DEEP_DRAWDOWN", codes)
        self.assertIn("WALK_FORWARD_DEGRADATION", codes)
        self.assertIn("CRITICAL_CLUSTER", codes)
        self.assertTrue(should_request_claude_audit(flags))
        self.assertTrue(alert["local_alert_required"])
        self.assertFalse(alert["telegram_send_allowed"])
        self.assertFalse(alert["claude_call_allowed"])
        self.assertFalse(alert["live_change_allowed"])

    def test_no_flags_allows_local_pass(self) -> None:
        flags = evaluate_critical_flags({"n_trades": 40, "profit_factor": 1.4, "max_drawdown_pct": -8.0}, min_trades=30)
        alert = build_alert_plan(flags)

        self.assertEqual(flags, [])
        self.assertFalse(alert["local_alert_required"])
        self.assertTrue(alert["live_change_allowed"])


class AuditLabMarketDataTest(unittest.TestCase):
    def test_cost_model_constants_are_fixed(self) -> None:
        self.assertAlmostEqual(CostModel.from_name("KR", "realistic").round_trip_bps, 41.0)
        self.assertAlmostEqual(CostModel.from_name("US", "realistic").round_trip_bps, 10.0)
        self.assertAlmostEqual(CostModel.from_name("KR", "basic").round_trip_bps, 3.0)
        self.assertAlmostEqual(CostModel.from_name("US", "basic").round_trip_bps, 0.0)

    def test_db_schema_initializes_expected_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market_data.sqlite"
            init_database(db_path)
            names = table_names(db_path)

        self.assertIn("collection_runs", names)
        self.assertIn("symbol_master", names)
        self.assertIn("ohlcv_manifest", names)
        self.assertIn("data_quality_issues", names)
        self.assertIn("backtest_trades", names)

    def test_live_universe_union_tags_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            (root / "data" / "universe" / "KR").mkdir(parents=True)
            (root / "kis_api.py").write_text(
                "_KR_FALLBACK_UNIVERSE = ['000660', '005930']\n_US_FALLBACK_UNIVERSE = ['AMD']\n",
                encoding="utf-8",
            )
            (root / "state" / "shared_judgment_KR_20260424.json").write_text(
                json.dumps({"digest_raw": {"universe_tickers": ["005930", "111111"]}}),
                encoding="utf-8",
            )
            (root / "data" / "universe" / "KR" / "2026-04-24.json").write_text(
                json.dumps({"tickers": ["111111", "222222"]}),
                encoding="utf-8",
            )

            members = build_live_universe("KR", root=root, recent_files=3)
            by_symbol = {member.raw_symbol: member for member in members}

        self.assertEqual(by_symbol["005930"].universe_group, "core")
        self.assertIn("fallback", by_symbol["000660"].sources)
        self.assertEqual(by_symbol["111111"].universe_group, "multi_source")
        self.assertEqual(by_symbol["222222"].universe_group, "dynamic_only")

    def test_quality_validator_flags_invalid_ohlc(self) -> None:
        df = pd.DataFrame(
            [
                {"date": "2024-01-01", "open": 100, "high": 99, "low": 98, "close": 100, "volume": 1000},
                {"date": "2024-01-02", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000},
            ]
        )
        report = validate_ohlcv_frame(df, symbol="BAD", market="US")

        self.assertEqual(report.quality_grade, "FAIL")
        self.assertIn("ohlc_invalid", {issue["issue_type"] for issue in report.issues})

    def test_yfinance_collector_retries_empty_frames_and_records_db(self) -> None:
        calls: list[str] = []

        def downloader(**kwargs) -> pd.DataFrame:
            calls.append(kwargs["symbol"])
            if len(calls) < 3:
                return pd.DataFrame()
            return pd.DataFrame(
                [
                    {"date": "2020-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
                    {"date": "2021-01-01", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1000},
                    {"date": "2022-01-01", "open": 102, "high": 104, "low": 101, "close": 103, "volume": 1000},
                    {"date": "2023-01-01", "open": 103, "high": 105, "low": 102, "close": 104, "volume": 1000},
                ]
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            collector = YFinanceDailyCollector(
                data_dir=root / "data",
                db_path=root / "market_data.sqlite",
                downloader=downloader,
                sleep_seconds=0,
                max_retries=3,
                storage_format="csv",
            )
            results = collector.collect(
                [UniverseMember(market="US", raw_symbol="AAPL", universe_group="core", sources=("core",))],
                run_id="CR_UNIT",
            )
            db_path = root / "market_data.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                manifest_count = conn.execute("SELECT COUNT(*) FROM ohlcv_manifest").fetchone()[0]
                resolution = conn.execute("SELECT status FROM symbol_resolution ORDER BY id DESC LIMIT 1").fetchone()[0]
            finally:
                conn.close()
            file_exists = Path(results[0].file_path).exists()

        self.assertEqual(len(calls), 3)
        self.assertEqual(results[0].status, "ok")
        self.assertTrue(file_exists)
        self.assertEqual(manifest_count, 1)
        self.assertEqual(resolution, "ok")


if __name__ == "__main__":
    unittest.main()
