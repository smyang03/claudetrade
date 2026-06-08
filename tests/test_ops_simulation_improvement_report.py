from __future__ import annotations

import json
from pathlib import Path

from tools import ops_simulation_improvement_report


def _sample_report(path: Path) -> Path:
    payload = {
        "ok": True,
        "summary": {"case_count": 5},
        "results": [
            {
                "scenario": "missed_kr_wait_60m",
                "market": "KR",
                "ticker": "005930",
                "path_type": "path_a",
                "params": {
                    "source": "counterfactual_missed",
                    "candidate_key": "kr_wait",
                    "counterfactual_path": "wait_60m",
                    "observed_outcome_30m_pct": 3.0,
                    "observed_outcome_60m_pct": 6.5,
                    "observed_max_runup_60m_pct": 8.0,
                    "observed_max_drawdown_60m_pct": -1.2,
                    "price_coverage": {
                        "coverage_status": "complete",
                        "coverage_flags": [],
                        "requested_start_at": "2026-06-01T09:00:00+09:00",
                        "requested_end_at": "2026-06-01T10:00:00+09:00",
                        "actual_start_at": "2026-06-01T09:00:00+09:00",
                        "actual_end_at": "2026-06-01T10:00:00+09:00",
                        "matched_rows": 10,
                    },
                },
                "events": [{"event_type": "ENTRY_FILLED", "ts": "09:00", "price": 30000, "qty": 15}],
                "metrics": {"entered": True, "closed": False, "score": 6.2, "unrealized_pnl_pct": 6.2},
            },
            {
                "scenario": "missed_us_high_price",
                "market": "US",
                "ticker": "AVGO",
                "path_type": "claude_price",
                "params": {
                    "source": "counterfactual_missed",
                    "counterfactual_path": "immediate",
                    "fixed_order_krw": 450000,
                    "usd_krw": 1350,
                    "slippage_cap": 1.002,
                    "price_coverage": {
                        "coverage_status": "partial",
                        "coverage_flags": ["end_before_requested"],
                        "requested_start_at": "2026-06-05T03:27:00+09:00",
                        "requested_end_at": "2026-06-05T23:59:59+09:00",
                        "actual_start_at": "2026-06-05T03:28:00+09:00",
                        "actual_end_at": "2026-06-05T04:00:00+09:00",
                        "matched_rows": 12,
                    },
                },
                "events": [
                    {
                        "event_type": "ENTRY_BLOCKED",
                        "ts": "2026-06-05T03:28:00+09:00",
                        "price": 420.0,
                        "reason": "HIGH_PRICE_BUDGET_BLOCK",
                    }
                ],
                "metrics": {"entered": False, "score": -2.4, "missed_gain_pct": 2.4},
            },
            {
                "scenario": "us_pathb_control",
                "market": "US",
                "ticker": "NVDA",
                "path_type": "claude_price",
                "params": {"source": "pathb_historical"},
                "events": [{"event_type": "ENTRY_FILLED", "ts": "09:30", "price": 124.0, "qty": 2}],
                "metrics": {"entered": True, "closed": True, "score": 4.0, "realized_pnl_pct": 4.0},
            },
            {
                "scenario": "us_buy_zone_miss",
                "market": "US",
                "ticker": "RXO",
                "path_type": "claude_price",
                "params": {"source": "pathb_historical", "buy_zone_high": 21.8},
                "events": [{"event_type": "WAIT_ABOVE_BUY_ZONE", "ts": "09:31", "price": 22.1}],
                "metrics": {"entered": False, "closed": False, "score": -6.4, "missed_gain_pct": 6.4},
                "improvement_hints": [
                    {
                        "category": "profitability",
                        "priority": "medium",
                        "signal": "price missed buy zone then rallied",
                        "suggestion": "compare buy_zone width, selection timing, and pullback confirmation latency",
                        "evidence": {"missed_gain_pct": 6.4, "buy_zone_high": 21.8},
                    }
                ],
            },
            {
                "scenario": "us_exit_rebound",
                "market": "US",
                "ticker": "ARM",
                "path_type": "claude_price",
                "params": {"source": "pathb_historical", "stop_price": 360.0},
                "events": [{"event_type": "EXIT_HARD_STOP", "ts": "10:10", "price": 360.0}],
                "metrics": {"entered": True, "closed": True, "score": -4.7, "final_from_entry_pct": 4.7},
                "improvement_hints": [
                    {
                        "category": "profitability",
                        "priority": "medium",
                        "signal": "hard stop was followed by rebound",
                        "suggestion": "review stop distance, protective hold boundary, and rebound-aware exit diagnostics",
                        "evidence": {"stop_price": 360.0, "final_from_entry_pct": 4.7},
                    }
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_improvement_report_categorizes_kr_wait_us_high_price_and_coverage(tmp_path: Path) -> None:
    report_path = _sample_report(tmp_path / "simulation.json")

    payload = ops_simulation_improvement_report.build_improvement_report(
        [report_path],
        output_root=tmp_path / "analysis",
        output_dir="out",
        top=10,
    )

    assert payload["ok"] is True
    assert payload["live_writes_performed"] is False
    kr_wait = payload["categories"]["KR"]["profitability"]["wait_followup"]
    assert kr_wait["candidate_count"] == 1
    assert kr_wait["path_stats"]["wait_60m"]["avg"] == 6.2
    us_blocks = payload["categories"]["US"]["operability_profitability"]["high_price_blocks"]
    assert us_blocks["blocked_count"] == 1
    assert us_blocks["top_blocks"][0]["ticker"] == "AVGO"
    assert us_blocks["top_blocks"][0]["budget_gap_krw"] > 0
    buy_zone = payload["categories"]["US"]["profitability"]["buy_zone_misses"]
    assert buy_zone["candidate_count"] == 1
    assert buy_zone["top_candidates"][0]["ticker"] == "RXO"
    assert buy_zone["top_candidates"][0]["missed_gain_pct"] == 6.4
    exit_followup = payload["categories"]["US"]["profitability"]["exit_followup"]
    assert exit_followup["candidate_count"] == 1
    assert exit_followup["top_candidates"][0]["ticker"] == "ARM"
    assert exit_followup["signal_stats"]["hard stop was followed by rebound"]["count"] == 1
    coverage = payload["categories"]["common"]["operability_bug"]["price_coverage"]
    assert coverage["by_status"] == {"complete": 1, "partial": 1}
    assert coverage["incomplete_count"] == 1
    for output_path in payload["output_paths"].values():
        assert Path(output_path).exists()


def test_improvement_report_deduplicates_sweep_rows_in_examples(tmp_path: Path) -> None:
    base = json.loads(_sample_report(tmp_path / "simulation.json").read_text(encoding="utf-8"))
    duplicate_kr = dict(base["results"][0])
    duplicate_kr["metrics"] = dict(duplicate_kr["metrics"], score=7.4)
    duplicate_us = dict(base["results"][1])
    duplicate_us["metrics"] = dict(duplicate_us["metrics"], score=-3.0, missed_gain_pct=3.0)
    base["results"].extend([duplicate_kr, duplicate_us])
    duplicate_path = tmp_path / "duplicate_simulation.json"
    duplicate_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")

    payload = ops_simulation_improvement_report.build_improvement_report(
        [duplicate_path],
        output_root=tmp_path / "analysis",
        output_dir="dedupe",
        top=10,
    )

    kr_wait = payload["categories"]["KR"]["profitability"]["wait_followup"]
    assert kr_wait["raw_candidate_count"] == 2
    assert kr_wait["candidate_count"] == 1
    assert kr_wait["top_candidates"][0]["score"] == 7.4
    us_blocks = payload["categories"]["US"]["operability_profitability"]["high_price_blocks"]
    assert us_blocks["raw_blocked_count"] == 2
    assert us_blocks["blocked_count"] == 1
    assert us_blocks["groups"][0]["raw_count"] == 2
    coverage = payload["categories"]["common"]["operability_bug"]["price_coverage"]
    assert coverage["raw_incomplete_count"] == 2
    assert coverage["incomplete_count"] == 1


def test_improvement_report_cli_json(tmp_path: Path, capsys) -> None:
    report_path = _sample_report(tmp_path / "simulation.json")

    rc = ops_simulation_improvement_report.main(
        [
            "--report",
            str(report_path),
            "--output-root",
            str(tmp_path / "analysis"),
            "--output-dir",
            "cli",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["categories"]["KR"]["profitability"]["wait_followup"]["candidate_count"] == 1
