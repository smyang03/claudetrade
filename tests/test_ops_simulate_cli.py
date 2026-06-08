from __future__ import annotations

import json
from pathlib import Path

from tools import ops_simulate


def test_ops_simulate_cli_json_runs_single_scenario(tmp_path: Path, capsys) -> None:
    rc = ops_simulate.main(
        [
            "--scenario",
            "us_pathb_buy_zone_replay",
            "--runtime-root",
            str(tmp_path / "sim"),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"]["case_count"] == 1
    assert payload["best"]["scenario"] == "us_pathb_buy_zone_replay"
    assert payload["protected_static_files"]["changed_count"] == 0
    assert Path(payload["report_path"]).resolve().is_relative_to((tmp_path / "sim").resolve())
    assert Path(payload["csv_path"]).exists()


def test_ops_simulate_cli_sweep_reports_best_case(tmp_path: Path, capsys) -> None:
    rc = ops_simulate.main(
        [
            "--scenario",
            "us_pathb_buy_zone_replay",
            "--runtime-root",
            str(tmp_path / "sim_sweep"),
            "--sweep",
            "confidence_threshold=0.5,0.9",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["case_count"] == 2
    assert payload["best"]["sweep"] == {"confidence_threshold": 0.5}
    assert payload["worst"]["sweep"] == {"confidence_threshold": 0.9}


def test_ops_simulate_cli_tape_file_and_custom_outputs(tmp_path: Path, capsys) -> None:
    tape = tmp_path / "tape.csv"
    tape.write_text(
        "ts,price\n"
        "09:30,124\n"
        "09:35,123\n"
        "10:00,130.5\n",
        encoding="utf-8",
    )
    rc = ops_simulate.main(
        [
            "--tape-file",
            str(tape),
            "--market",
            "US",
            "--ticker",
            "NVDA",
            "--runtime-root",
            str(tmp_path / "sim_tape"),
            "--report-out",
            "reports/custom.json",
            "--csv-out",
            "reports/custom.csv",
            "--set",
            "buy_zone_low=122",
            "--set",
            "buy_zone_high=125",
            "--set",
            "target_price=130",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["case_count"] == 1
    assert payload["best"]["scenario"] == "tape"
    assert Path(payload["report_path"]).name == "custom.json"
    assert Path(payload["csv_path"]).name == "custom.csv"
    assert Path(payload["csv_path"]).exists()


def test_ops_simulate_cli_batch_file(tmp_path: Path, capsys) -> None:
    batch = tmp_path / "batch.json"
    batch.write_text(
        """
{
  "cases": [
    {"scenario": "us_pathb_buy_zone_replay", "name": "builtin"},
    {
      "name": "inline_case",
      "market": "US",
      "ticker": "NVDA",
      "price_tape": [
        {"ts": "09:30", "price": 124},
        {"ts": "10:00", "price": 130.5}
      ],
      "params": {"buy_zone_low": 123, "buy_zone_high": 125, "target_price": 130}
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    rc = ops_simulate.main(
        [
            "--batch-file",
            str(batch),
            "--runtime-root",
            str(tmp_path / "sim_batch"),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["case_count"] == 2
    assert {row["scenario"] for row in payload["ranking"]} == {"builtin", "inline_case"}
