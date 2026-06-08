from __future__ import annotations

from pathlib import Path

from runtime.rehearsal.context import create_rehearsal_context
from runtime.rehearsal.simulation import (
    all_simulation_scenarios,
    load_price_tape,
    load_simulation_batch,
    run_guarded_simulation,
    run_simulation_case,
    run_simulation_suite,
    simulation_case_from_tape,
    simulation_case_for_scenario,
)


def _signals(result: dict) -> set[str]:
    return {str(hint.get("signal") or "") for hint in result.get("improvement_hints") or []}


def test_buy_zone_replay_enters_and_exits_with_positive_pnl() -> None:
    result = run_simulation_case(simulation_case_for_scenario("us_pathb_buy_zone_replay"))
    assert result["metrics"]["entered"] is True
    assert result["metrics"]["closed"] is True
    assert result["metrics"]["realized_pnl_pct"] > 0
    assert any(event["event_type"] == "ENTRY_FILLED" for event in result["events"])
    assert any(event["event_type"] == "EXIT_TARGET" for event in result["events"])


def test_missed_buy_zone_reports_profitability_hint() -> None:
    result = run_simulation_case(simulation_case_for_scenario("us_pathb_missed_buy_zone"))
    assert result["metrics"]["entered"] is False
    assert result["metrics"]["missed_gain_pct"] >= 3.0
    assert "price missed buy zone then rallied" in _signals(result)


def test_stop_then_rebound_reports_stop_review_hint() -> None:
    result = run_simulation_case(simulation_case_for_scenario("us_pathb_stop_then_rebound"))
    assert result["metrics"]["closed"] is True
    assert result["metrics"]["realized_pnl_pct"] < 0
    assert "hard stop was followed by rebound" in _signals(result)


def test_operability_blocks_are_reported_separately() -> None:
    broker = run_simulation_case(simulation_case_for_scenario("broker_truth_fail_closed"))
    unknown = run_simulation_case(simulation_case_for_scenario("order_unknown_halts_entry"))
    order_size = run_simulation_case(simulation_case_for_scenario("kr_patha_order_size_gate"))

    broker_reasons = {event.get("reason") for event in broker["events"]}
    unknown_reasons = {event.get("reason") for event in unknown["events"]}
    size_reasons = {event.get("reason") for event in order_size["events"]}

    assert "BLOCKED_BROKER_TRUTH" in broker_reasons
    assert "ORDER_UNKNOWN_UNRESOLVED" in unknown_reasons
    assert "ORDER_SIZE_TOO_SMALL_GATE" in size_reasons
    assert "broker truth blocked entry" in _signals(broker)
    assert "ORDER_UNKNOWN halted entry" in _signals(unknown)
    assert "order size too small" in _signals(order_size)


def test_parameter_sweep_ranks_profitable_threshold_over_blocked_case() -> None:
    case = simulation_case_for_scenario("us_pathb_buy_zone_replay")
    report = run_simulation_suite([case], sweep={"confidence_threshold": [0.5, 0.9]})
    assert report["summary"]["case_count"] == 2
    assert report["best"]["sweep"] == {"confidence_threshold": 0.5}
    assert report["worst"]["sweep"] == {"confidence_threshold": 0.9}
    assert report["summary"]["block_reasons"]["BLOCKED_CONFIDENCE"] == 1
    assert "TARGET_HIT" not in report["summary"]["block_reasons"]


def test_price_tape_csv_can_build_custom_case(tmp_path: Path) -> None:
    tape_path = tmp_path / "nvda_tape.csv"
    tape_path.write_text(
        "ts,close\n"
        "09:30,124.0\n"
        "09:35,123.0\n"
        "10:00,130.5\n",
        encoding="utf-8",
    )
    tape = load_price_tape(tape_path)
    case = simulation_case_from_tape(
        name="nvda_custom",
        market="US",
        ticker="NVDA",
        price_tape=tape,
        params={"buy_zone_low": 122.0, "buy_zone_high": 125.0, "target_price": 130.0},
    )
    result = run_simulation_case(case)
    assert result["scenario"] == "nvda_custom"
    assert result["metrics"]["entered"] is True
    assert result["metrics"]["closed"] is True


def test_batch_file_loads_scenario_and_inline_tape(tmp_path: Path) -> None:
    batch_path = tmp_path / "batch.json"
    batch_path.write_text(
        """
{
  "overrides": {"confidence_threshold": 0.5},
  "sweep": {"profit_ladder_giveback_pct": [1.0, 2.0]},
  "cases": [
    {"scenario": "us_pathb_buy_zone_replay", "name": "builtin"},
    {
      "name": "inline_kr",
      "market": "KR",
      "ticker": "005930",
      "price_tape": [
        {"ts": "09:00", "price": 30000},
        {"ts": "09:10", "price": 31500},
        {"ts": "10:00", "price": 33500}
      ],
      "params": {"buy_zone_low": 29500, "buy_zone_high": 30500, "target_price": 33000}
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    cases, overrides, sweep = load_simulation_batch(batch_path)
    assert [case.name for case in cases] == ["builtin", "inline_kr"]
    assert overrides["confidence_threshold"] == 0.5
    assert sweep["profit_ladder_giveback_pct"] == [1.0, 2.0]
    report = run_simulation_suite(cases, overrides=overrides, sweep=sweep)
    assert report["summary"]["case_count"] == 4


def test_guarded_simulation_writes_report_only_in_sandbox(tmp_path: Path) -> None:
    ctx = create_rehearsal_context(scenario="ops_simulation_test", runtime_root=tmp_path / "sandbox")
    cases = [simulation_case_for_scenario(name) for name in all_simulation_scenarios()]
    report = run_guarded_simulation(ctx, cases, csv_path=Path("reports/custom.csv"))
    assert report["ok"] is True
    assert report["runtime_mode"] == "live"
    assert report["network_guard"]["call_count"] == 0
    assert report["protected_static_files"]["changed_count"] == 0
    assert "state/brain.json" in report["protected_static_files"]["after"]["files"]
    assert Path(report["report_path"]).resolve().is_relative_to(ctx.sandbox_root)
    assert Path(report["csv_path"]).resolve().is_relative_to(ctx.sandbox_root)
    assert Path(report["report_path"]).exists()
    assert Path(report["csv_path"]).exists()
