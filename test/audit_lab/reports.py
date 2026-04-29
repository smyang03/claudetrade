"""Report writers for audit-lab runs."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> str:
    return str(value)


def write_json_report(payload: dict, output_dir: Path, name: str = "audit_report") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return path


def write_csv_report(rows: list[dict], output_dir: Path, name: str = "audit_summary") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.csv"
    fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else ["message"]
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            writer.writerows(rows)
        else:
            writer.writerow({"message": "no rows"})
    return path


def write_markdown_report(payload: dict, output_dir: Path, name: str = "audit_report") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.md"
    summary_rows = payload.get("summary_rows", [])
    lines = [
        "# Backtest Audit Report",
        "",
        f"- generated_at: {payload.get('generated_at', datetime.now().isoformat(timespec='seconds'))}",
        f"- cost_model: {payload.get('cost_model', '')}",
        f"- entry_timing: {payload.get('entry_timing', '')}",
        "",
        "## Summary",
        "",
    ]
    if summary_rows:
        headers = ["market", "strategy", "n_trades", "win_rate", "avg_pnl_pct", "profit_factor", "max_drawdown_pct"]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in summary_rows:
            lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    else:
        lines.append("No summary rows.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_report_bundle(payload: dict, output_dir: Path, name: str | None = None) -> dict:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = name or f"audit_report_{stamp}"
    summary_name = base.replace("report", "summary")
    trades_name = base.replace("report", "trades")
    flags_name = base.replace("report", "flags")
    errors_name = base.replace("report", "errors")
    return {
        "json": str(write_json_report(payload, output_dir, base)),
        "csv": str(write_csv_report(payload.get("summary_rows", []), output_dir, summary_name)),
        "trades_csv": str(write_csv_report(payload.get("trade_rows", []), output_dir, trades_name)),
        "flags_csv": str(write_csv_report(payload.get("flag_rows", []), output_dir, flags_name)),
        "errors_csv": str(write_csv_report(payload.get("error_rows", []), output_dir, errors_name)),
        "markdown": str(write_markdown_report(payload, output_dir, base)),
    }
