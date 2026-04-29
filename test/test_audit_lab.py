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
from test.audit_lab.db import connect, init_database, persist_backtest_result, table_names, upsert_ohlcv_manifest, upsert_symbol_master
from test.audit_lab.event_engine import calc_stats, run_ticker_backtest
from test.audit_lab.intraday_collector import YFinanceIntradayCollector
from test.audit_lab.intraday_diagnostics import run_intraday_entry_diagnostics
from test.audit_lab.intraday_entry_models import find_intraday_entry
from test.audit_lab.intraday_file_importer import discover_intraday_files, import_intraday_files
from test.audit_lab.intraday_probe import probe_intraday_capability
from test.audit_lab.intraday_simulator import run_ticker_intraday_entry_backtest
from test.audit_lab.intraday_targets import (
    allowed_intraday_universe_groups,
    build_intraday_target_rows,
    unique_target_symbols_by_market,
    write_intraday_target_files,
)
from test.audit_lab.market_data_adapter import available_collected_tickers, load_collected_price_frame
from test.audit_lab.network_diag import diagnose_network
from test.audit_lab.regime_replay import ReplayRegimeClassifier, classify_regime
from test.audit_lab.reports import write_report_bundle
from test.audit_lab.strategy_policy import allowed_universe_groups
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
        self.assertIn("LOW_SAMPLE_DECISION_BLOCKED", codes)
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
        flags = evaluate_critical_flags({"n_trades": 140, "profit_factor": 1.4, "max_drawdown_pct": -8.0}, min_trades=30)
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

    def test_intraday_quality_uses_intraday_duration_thresholds(self) -> None:
        dates = pd.date_range("2024-01-01 09:30:00", periods=1200, freq="75min")
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
        report = validate_ohlcv_frame(df, symbol="AAPL", market="US", timeframe="5m")

        self.assertEqual(report.quality_grade, "A")

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


class AuditLabCollectedRunnerTest(unittest.TestCase):
    def test_gap_filter_skips_large_next_open_gap(self) -> None:
        df = _frame(
            [
                ("2024-01-01", 100, 101, 99, 100),
                ("2024-01-02", 110, 111, 109, 110),
                ("2024-01-03", 110, 111, 109, 110),
            ]
        )
        trades = run_ticker_backtest(
            df,
            market="KR",
            ticker="000000",
            strategy="unit",
            params={"tp_pct": 0.05, "sl_pct": 0.02, "max_hold": 2},
            signal_func=lambda _df, i, _params: i == 0,
            cost_model=CostModel(name="none"),
            entry_model="gap_filter",
            max_entry_gap_pct=1.5,
            respect_disabled_combos=False,
        )

        self.assertEqual(trades, [])

    def test_pullback_limit_enters_when_next_day_low_touches_limit(self) -> None:
        df = _frame(
            [
                ("2024-01-01", 100, 101, 99, 100),
                ("2024-01-02", 101, 102, 99, 100),
                ("2024-01-03", 100, 101, 99, 100),
            ]
        )
        trades = run_ticker_backtest(
            df,
            market="KR",
            ticker="000000",
            strategy="unit",
            params={"tp_pct": 0.50, "sl_pct": 0.50, "max_hold": 1},
            signal_func=lambda _df, i, _params: i == 0,
            cost_model=CostModel(name="none"),
            entry_model="pullback_limit",
            pullback_limit_pct=-0.5,
            respect_disabled_combos=False,
        )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entry_model"], "pullback_limit")
        self.assertAlmostEqual(trades[0]["entry_price"], 99.5)

    def test_confirmation_next_open_waits_for_survival_candle(self) -> None:
        df = _frame(
            [
                ("2024-01-01", 100, 101, 99, 100),
                ("2024-01-02", 100, 104, 99, 103),
                ("2024-01-03", 103, 107, 102, 106),
                ("2024-01-04", 106, 108, 105, 107),
            ]
        )
        trades = run_ticker_backtest(
            df,
            market="KR",
            ticker="000000",
            strategy="unit",
            params={"tp_pct": 0.03, "sl_pct": 0.02, "max_hold": 2},
            signal_func=lambda _df, i, _params: i == 0,
            cost_model=CostModel(name="none"),
            entry_model="confirmation_next_open",
            respect_disabled_combos=False,
        )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entry_timing"], "next_open_confirmed")
        self.assertEqual(trades[0]["entry_date"], "2024-01-03")

    def test_confirmation_next_open_skips_failed_survival_candle(self) -> None:
        df = _frame(
            [
                ("2024-01-01", 100, 101, 99, 100),
                ("2024-01-02", 100, 103, 96, 102),
                ("2024-01-03", 102, 104, 101, 103),
            ]
        )
        trades = run_ticker_backtest(
            df,
            market="KR",
            ticker="000000",
            strategy="unit",
            params={"tp_pct": 0.03, "sl_pct": 0.02, "max_hold": 2},
            signal_func=lambda _df, i, _params: i == 0,
            cost_model=CostModel(name="none"),
            entry_model="confirmation_next_open",
            respect_disabled_combos=False,
        )

        self.assertEqual(trades, [])

    def test_collected_adapter_filters_quality_and_loads_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "market_data.sqlite"
            csv_path = root / "AAPL.csv"
            dates = pd.date_range("2020-01-01", periods=90, freq="D")
            pd.DataFrame(
                {
                    "date": dates,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1000,
                }
            ).to_csv(csv_path, index=False)
            init_database(db_path)
            with connect(db_path) as conn:
                upsert_symbol_master(
                    conn,
                    symbol="AAPL",
                    raw_symbol="AAPL",
                    market="US",
                    universe_group="core",
                    universe_sources=["unit"],
                )
                upsert_ohlcv_manifest(
                    conn,
                    {
                        "symbol": "AAPL",
                        "market": "US",
                        "timeframe": "daily",
                        "file_path": str(csv_path),
                        "storage_format": "csv",
                        "row_count": 90,
                        "start_date": "2020-01-01",
                        "end_date": "2020-03-30",
                        "missing_rate": 0.0,
                        "quality_grade": "A",
                        "run_id": "CR_UNIT",
                    },
                )

            tickers = available_collected_tickers("US", db_path=db_path, min_quality="C")
            frame = load_collected_price_frame("US", "AAPL", db_path=db_path)

        self.assertEqual(tickers, ["AAPL"])
        self.assertFalse(frame.empty)
        self.assertIn("ma60", frame.columns)

    def test_backtest_result_persists_to_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market_data.sqlite"
            init_database(db_path)
            trade = {
                "market": "US",
                "ticker": "AAPL",
                "strategy": "unit",
                "entry_model": "next_open",
                "universe_group": "core",
                "analysis_window": "official_2018",
                "signal_date": "2024-01-01",
                "signal_price": 100.0,
                "entry_date": "2024-01-02",
                "entry_price": 101.0,
                "exit_date": "2024-01-03",
                "exit_price": 103.0,
                "gross_pnl_pct": 1.980198,
                "net_pnl_pct": 1.880198,
                "held_days": 1,
                "reason": "max_hold",
                "mode": "NEUTRAL",
                "entry_gap_pct": 1.0,
                "entry_day_sl_breach": 0,
                "entry_timing": "next_open",
                "cost_bps": 10.0,
            }
            metric = {
                "run_id": "BT_UNIT",
                "market": "US",
                "strategy": "unit",
                "entry_model": "next_open",
                "analysis_window": "official_2018",
                "data_source": "unit",
                "universe_group": "ALL",
                "regime": "ALL",
                "n_trades": 1,
                "win_rate": 100.0,
                "avg_pnl_pct": 1.880198,
                "profit_factor": 1.0,
                "max_drawdown_pct": 0.0,
                "trade_sharpe": 0.0,
            }
            with connect(db_path) as conn:
                persist_backtest_result(
                    conn,
                    run_info={
                        "run_id": "BT_UNIT",
                        "market": "US",
                        "strategy": "unit",
                        "data_start": "2024-01-01",
                        "data_end": "2024-01-03",
                        "cost_model": "realistic",
                        "entry_model": "next_open",
                        "params": {"analysis_window": "official_2018"},
                    },
                    trades=[trade],
                    metrics=[metric],
                    flags=[{"code": "LOW_SAMPLE", "severity": "medium", "metric": 1, "threshold": 30}],
                )
                counts = {
                    "runs": conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0],
                    "trades": conn.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0],
                    "metrics": conn.execute("SELECT COUNT(*) FROM strategy_metrics").fetchone()[0],
                    "flags": conn.execute("SELECT COUNT(*) FROM critical_flags").fetchone()[0],
                }

        self.assertEqual(counts, {"runs": 1, "trades": 1, "metrics": 1, "flags": 1})

    def test_intraday_probe_uses_mock_downloader(self) -> None:
        def downloader(**_kwargs) -> pd.DataFrame:
            dates = pd.date_range("2024-01-01 09:30:00", periods=3, freq="5min")
            return pd.DataFrame(
                {
                    "date": dates,
                    "open": [100, 101, 102],
                    "high": [101, 102, 103],
                    "low": [99, 100, 101],
                    "close": [100.5, 101.5, 102.5],
                    "volume": [1000, 1100, 1200],
                }
            )

        rows = probe_intraday_capability(["AAPL"], intervals=["5m"], downloader=downloader)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(rows[0]["rows"], 3)

    def test_profit_guard_policy_allows_only_selected_groups(self) -> None:
        groups = allowed_universe_groups(
            "profit_guard_v1",
            market="US",
            strategy="mean_reversion",
            entry_model="gap_filter",
        )
        excluded = allowed_universe_groups(
            "profit_guard_v1",
            market="KR",
            strategy="mean_reversion",
            entry_model="gap_filter",
        )

        self.assertEqual(groups, ("fallback",))
        self.assertEqual(excluded, ())

    def test_profit_guard_v2_is_strict_us_mean_reversion_only(self) -> None:
        allowed = allowed_universe_groups(
            "profit_guard_v2",
            market="US",
            strategy="mean_reversion",
            entry_model="confirmation_next_open",
        )
        excluded = allowed_universe_groups(
            "profit_guard_v2",
            market="KR",
            strategy="momentum",
            entry_model="gap_filter",
        )

        self.assertEqual(allowed, ("fallback", "dynamic_only"))
        self.assertEqual(excluded, ())


class AuditLabIntradayEntryTest(unittest.TestCase):
    def _intraday_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"date": "2024-01-02 09:30:00", "open": 100, "high": 100.5, "low": 99.7, "close": 100.0, "volume": 1000},
                {"date": "2024-01-02 09:35:00", "open": 100, "high": 101.0, "low": 99.8, "close": 100.8, "volume": 1200},
                {"date": "2024-01-02 10:00:00", "open": 100.8, "high": 102.5, "low": 100.5, "close": 102.2, "volume": 1800},
                {"date": "2024-01-02 10:05:00", "open": 102.2, "high": 106.0, "low": 102.0, "close": 105.5, "volume": 2000},
            ]
        )

    def test_opening_range_reclaim_entry_triggers_after_opening_box(self) -> None:
        entry = find_intraday_entry(
            self._intraday_frame(),
            model="opening_range_reclaim",
            entry_date="2024-01-02",
            signal_close=100.0,
            stop_loss_pct=0.02,
            opening_minutes=30,
            deadline_minutes=120,
            max_gap_pct=1.5,
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry.model, "opening_range_reclaim")
        self.assertEqual(entry.minutes_from_open, 30)
        self.assertAlmostEqual(entry.entry_price, 102.2)

    def test_intraday_entry_skips_opening_stop_breach(self) -> None:
        bad = pd.DataFrame(
            [
                {"date": "2024-01-02 09:30:00", "open": 100, "high": 101, "low": 97, "close": 100, "volume": 1000},
                {"date": "2024-01-02 10:00:00", "open": 100, "high": 103, "low": 99, "close": 102, "volume": 1000},
            ]
        )
        entry = find_intraday_entry(
            bad,
            model="opening_range_reclaim",
            entry_date="2024-01-02",
            signal_close=100.0,
            stop_loss_pct=0.02,
        )

        self.assertIsNone(entry)

    def test_vwap_reclaim_entry_triggers(self) -> None:
        entry = find_intraday_entry(
            self._intraday_frame(),
            model="vwap_reclaim",
            entry_date="2024-01-02",
            signal_close=100.0,
            stop_loss_pct=0.02,
            opening_minutes=30,
            deadline_minutes=120,
            max_gap_pct=1.5,
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry.model, "vwap_reclaim")

    def test_intraday_entry_matches_timezone_aware_yfinance_timestamps(self) -> None:
        intraday = self._intraday_frame().copy()
        intraday["date"] = pd.to_datetime(intraday["date"]).dt.tz_localize("UTC")

        entry = find_intraday_entry(
            intraday,
            model="opening_range_reclaim",
            entry_date="2024-01-02",
            signal_close=100.0,
            stop_loss_pct=0.02,
            opening_minutes=30,
            deadline_minutes=120,
            max_gap_pct=1.5,
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry.entry_timestamp, "2024-01-02T10:00:00+00:00")

    def test_intraday_simulator_uses_intraday_entry_and_exit(self) -> None:
        daily = _frame(
            [
                ("2024-01-01", 100, 101, 99, 100),
                ("2024-01-02", 100, 106, 99, 104),
                ("2024-01-03", 104, 105, 103, 104),
                ("2024-01-04", 104, 105, 103, 104),
            ]
        )
        trades = run_ticker_intraday_entry_backtest(
            daily,
            self._intraday_frame(),
            market="US",
            ticker="AAPL",
            strategy="unit",
            params={"tp_pct": 0.03, "sl_pct": 0.02, "max_hold": 2},
            signal_func=lambda _df, i, _params: i == 0,
            cost_model=CostModel(name="none"),
            intraday_entry_model="opening_range_reclaim",
        )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entry_timing"], "intraday")
        self.assertEqual(trades[0]["reason"], "take_profit")
        self.assertEqual(trades[0]["entry_timestamp"], "2024-01-02T10:00:00")

    def test_intraday_collector_records_manifest(self) -> None:
        def downloader(**_kwargs) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"date": "2024-01-02 09:30:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
                    {"date": "2024-01-02 09:35:00", "open": 100.5, "high": 101.5, "low": 100, "close": 101, "volume": 1100},
                ]
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            collector = YFinanceIntradayCollector(
                data_dir=root / "data",
                db_path=root / "market_data.sqlite",
                downloader=downloader,
                sleep_seconds=0,
                max_retries=1,
                storage_format="csv",
            )
            results = collector.collect(market="US", symbols=["AAPL"], interval="5m", run_id="CI_UNIT")
            conn = sqlite3.connect(root / "market_data.sqlite")
            try:
                row = conn.execute(
                    "SELECT timeframe, file_path FROM ohlcv_manifest WHERE symbol='AAPL' AND market='US'"
                ).fetchone()
            finally:
                conn.close()
            file_exists = Path(row[1]).exists()

        self.assertEqual(results[0].status, "ok")
        self.assertEqual(row[0], "5m")
        self.assertTrue(file_exists)

    def test_intraday_file_importer_registers_external_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            source_dir.mkdir()
            csv_path = source_dir / "AAPL.csv"
            pd.DataFrame(
                [
                    {"date": "2024-01-02 09:30:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
                    {"date": "2024-01-02 09:35:00", "open": 100.5, "high": 102, "low": 100, "close": 101.5, "volume": 1100},
                ]
            ).to_csv(csv_path, index=False)
            db_path = root / "market_data.sqlite"
            files = discover_intraday_files(source_dir)
            results = import_intraday_files(
                files,
                market="US",
                timeframe="5m",
                db_path=db_path,
                data_dir=root / "data",
                storage_format="csv",
                run_id="II_UNIT",
            )
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT symbol, timeframe, row_count, file_path FROM ohlcv_manifest WHERE symbol='AAPL'"
                ).fetchone()
                run_status = conn.execute("SELECT status FROM collection_runs WHERE run_id='II_UNIT'").fetchone()[0]
            finally:
                conn.close()
            imported_exists = Path(row[3]).exists()

        self.assertEqual(len(files), 1)
        self.assertEqual(results[0].status, "ok")
        self.assertEqual(row[0], "AAPL")
        self.assertEqual(row[1], "5m")
        self.assertEqual(row[2], 2)
        self.assertEqual(run_status, "done")
        self.assertTrue(imported_exists)

    def test_intraday_target_export_uses_profit_guard_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "market_data.sqlite"
            init_database(db_path)
            symbols = {
                "AAPL": "fallback",
                "RKLB": "dynamic_only",
                "MSFT": "core",
            }
            with connect(db_path) as conn:
                for symbol, group in symbols.items():
                    csv_path = root / f"{symbol}.csv"
                    pd.DataFrame(
                        [
                            {"date": "2024-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
                            {"date": "2024-01-02", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 1000},
                        ]
                    ).to_csv(csv_path, index=False)
                    upsert_symbol_master(
                        conn,
                        symbol=symbol,
                        raw_symbol=symbol,
                        market="US",
                        universe_group=group,
                        universe_sources=[group],
                    )
                    upsert_ohlcv_manifest(
                        conn,
                        {
                            "symbol": symbol,
                            "market": "US",
                            "timeframe": "daily",
                            "file_path": str(csv_path),
                            "storage_format": "csv",
                            "row_count": 2,
                            "start_date": "2024-01-01",
                            "end_date": "2024-01-02",
                            "missing_rate": 0.0,
                            "quality_grade": "A",
                            "run_id": "CR_TARGET",
                        },
                    )

            rows = build_intraday_target_rows(
                policy_name="profit_guard_v2",
                market="US",
                strategy="mean_reversion",
                db_path=db_path,
                min_quality="C",
            )
            unique = unique_target_symbols_by_market(rows)
            paths = write_intraday_target_files(rows, output_dir=root / "targets", name="unit_targets")
            json_exists = Path(paths["json"]).exists()
            symbols_exists = Path(paths["us_symbols_txt"]).exists()

        self.assertEqual(allowed_intraday_universe_groups("profit_guard_v2", market="US", strategy="mean_reversion"), ("fallback", "dynamic_only"))
        self.assertEqual(unique, {"US": ["AAPL", "RKLB"]})
        self.assertEqual(len(rows), 3)
        self.assertNotIn("MSFT", {row["symbol"] for row in rows})
        self.assertTrue(json_exists)
        self.assertTrue(symbols_exists)

    def test_intraday_diagnostics_reports_missing_data_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = run_intraday_entry_diagnostics(
                market="US",
                strategy="mean_reversion",
                tickers=["AAPL"],
                intraday_entry_model="vwap_reclaim",
                db_path=Path(tmp) / "empty.sqlite",
            )

        self.assertEqual(payload["signal_count"], 0)
        self.assertEqual(payload["entry_count"], 0)
        self.assertEqual(payload["error_rows"][0]["reason"], "NO_DAILY_DATA")

    def test_network_diag_detects_win10013_policy_block(self) -> None:
        def getter(_url: str, *, timeout: int) -> object:
            raise ConnectionError("[WinError 10013] blocked")

        result = diagnose_network(("https://example.invalid",), getter=getter)

        self.assertFalse(result["all_ok"])
        self.assertTrue(result["blocked_by_os_policy"])


if __name__ == "__main__":
    unittest.main()
