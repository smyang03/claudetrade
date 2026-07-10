from __future__ import annotations

import json
from pathlib import Path

from tools.us_swing_execution_evidence import build_evidence


def test_execution_evidence_can_allow_micro_while_blocking_probe(tmp_path: Path) -> None:
    rank1 = {
        "sessions": 293, "mean_net_pct": 1.4, "profit_factor": 1.36,
        "block_lcb_pct": -0.17, "ex_top3_days_pct": 0.9,
        "by_year": {
            "2025": {"mean_net_pct": 0.05, "profit_factor": 1.01},
            "2026": {"mean_net_pct": 6.3, "profit_factor": 3.2},
        },
    }
    top3 = {**rank1, "block_lcb_pct": -0.37}
    path = tmp_path / "counterfactual.json"
    path.write_text(json.dumps({
        "subsets": {"rank1": {"tp12_sl25": rank1}, "top3": {"tp12_sl25": top3}}
    }), encoding="utf-8")

    evidence = build_evidence(path)

    assert evidence["modes"]["micro"]["passed"] is True
    assert evidence["modes"]["probe"]["passed"] is False
    assert evidence["modes"]["probe"]["reasons"] == ["block_lcb_failed"]
