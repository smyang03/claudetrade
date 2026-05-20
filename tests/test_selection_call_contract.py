from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_all_select_tickers_calls_pass_prompt_pool_override_and_evidence() -> None:
    source_path = ROOT / "trading_bot.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    required = {"evidence_by_ticker", "prompt_pool_override", "prompt_pool_meta_override"}
    calls: list[tuple[int, set[str]]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "select_tickers":
            calls.append((node.lineno, {kw.arg for kw in node.keywords if kw.arg}))

    assert calls, "select_tickers callsites were not found"
    missing = {
        lineno: sorted(required - keywords)
        for lineno, keywords in calls
        if required - keywords
    }
    assert not missing, f"select_tickers callsites missing selection contract kwargs: {missing}"

