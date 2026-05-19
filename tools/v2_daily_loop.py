from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.v2 import DEFAULT_V2_CONFIG
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from research.v2_policy_optimizer import OptimizerConfig, build_policy_optimization_report
from research.v2_simulation_report import build_simulation_report
from review.daily_review import DailyReviewWriter
from tools.sync_v2_learning_performance import sync_v2_learning_performance
from tools.v2_forward_measurer import measure_forward_pending


START_CONFIG_PATH = ROOT / "config" / "v2_start_config.json"
REPORT_DIR = ROOT / "data" / "v2_reports"


def load_start_config(path: str | Path = START_CONFIG_PATH) -> dict[str, Any]:
    config_path = Path(path)
    return json.loads(config_path.read_text(encoding="utf-8"))


def run_daily_loop(
    *,
    session_date: str | None = None,
    runtime_mode: str = "live",
    market: str = "KR",
    config_path: str | Path = START_CONFIG_PATH,
    dry_run: bool = False,
    run_simulation: bool = True,
    run_optimizer: bool = True,
    store: EventStore | None = None,
    root: str | Path = ROOT,
    output_dir: str | Path = REPORT_DIR,
) -> dict[str, Any]:
    root_path = Path(root)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_start_config(config_path)
    markets = _markets_from_args(market, cfg)
    session = session_date or date.today().isoformat()
    event_store = store or EventStore()

    forward = reserve_forward_pending(
        event_store,
        session_date=session,
        runtime_mode=runtime_mode,
        markets=markets,
        dry_run=dry_run,
    )
    forward_measured = measure_forward_pending(
        event_store,
        session_date=session,
        runtime_mode=runtime_mode,
        markets=markets,
        dry_run=dry_run,
    )
    learning_sync: dict[str, Any] = {}
    for mkt in markets:
        learning_sync[mkt] = sync_v2_learning_performance(
            event_db=event_store.path,
            ml_db=root_path / "data" / "ml" / "decisions.db",
            market=mkt,
            runtime_mode=runtime_mode,
            dry_run=dry_run,
        )
    reviews: dict[str, Any] = {}
    for mkt in markets:
        writer = DailyReviewWriter(event_store)
        if dry_run:
            reviews[mkt] = {"summary": writer.build_summary(session_date=session, runtime_mode=runtime_mode, market=mkt)}
        else:
            reviews[mkt] = writer.write(session_date=session, runtime_mode=runtime_mode, market=mkt)

    simulation_paths = build_simulation_report(root_path) if run_simulation else {}
    optimizer_paths = (
        build_policy_optimization_report(
            root_path,
            config=OptimizerConfig(min_trades=60, min_validation_trades=20),
        )
        if run_optimizer
        else {}
    )
    previous = _latest_daily_loop_payload(out_dir)
    config_diff = diff_start_config(previous.get("start_config") if previous else None, cfg)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "session_date": session,
        "runtime_mode": runtime_mode,
        "markets": markets,
        "dry_run": dry_run,
        "basis": "V2 end-of-day loop; no Claude calls; no order execution; no broker order calls",
        "start_config": cfg,
        "config_diff_vs_previous_loop": config_diff,
        "forward_pending": forward,
        "forward_measured": forward_measured,
        "learning_sync": learning_sync,
        "daily_reviews": reviews,
        "simulation_report": simulation_paths,
        "policy_optimization_report": optimizer_paths,
        "checks": build_checks(cfg, forward, forward_measured),
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"v2_daily_loop_{stamp}.json"
    md_path = out_dir / f"v2_daily_loop_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    payload["paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def reserve_forward_pending(
    store: EventStore,
    *,
    session_date: str,
    runtime_mode: str,
    markets: list[str],
    dry_run: bool = False,
) -> dict[str, Any]:
    decisions = _decisions_for_session(
        store,
        session_date=session_date,
        runtime_mode=runtime_mode,
        markets=markets,
    )
    reserved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for decision in decisions:
        decision_id = str(decision.get("decision_id") or "")
        events = store.events_for_decision(decision_id)
        event_types = {str(event.get("event_type") or "") for event in events}
        if _forward_measurement_complete(events):
            skipped.append({"decision_id": decision_id, "reason": "already_measured"})
            continue
        if "FORWARD_PENDING_DATA" in event_types:
            skipped.append({"decision_id": decision_id, "reason": "already_pending"})
            continue
        item = {
            "decision_id": decision_id,
            "market": decision.get("market"),
            "ticker": decision.get("ticker"),
            "due_horizons": ["1d", "3d", "5d"],
        }
        reserved.append(item)
        if not dry_run:
            store.append(
                LifecycleEvent(
                    event_type="FORWARD_PENDING_DATA",
                    market=str(decision.get("market") or ""),
                    runtime_mode=str(decision.get("runtime_mode") or runtime_mode),
                    session_date=str(decision.get("session_date") or session_date),
                    ticker=str(decision.get("ticker") or ""),
                    decision_id=decision_id,
                    prompt_version=str(decision.get("prompt_version") or DEFAULT_V2_CONFIG.prompt_version),
                    brain_snapshot_id=str(decision.get("brain_snapshot_id") or "brain_pending"),
                    payload={
                        "due_horizons": ["1d", "3d", "5d"],
                        "reason": "daily_loop_forward_reservation",
                    },
                )
            )
    return {
        "decision_count": len(decisions),
        "reserved_count": len(reserved),
        "skipped_count": len(skipped),
        "reserved": reserved,
        "skipped": skipped,
    }


def diff_start_config(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if not previous:
        return {"status": "NO_PREVIOUS_CONFIG", "changed": []}
    changed = []
    keys = sorted(set(previous.keys()) | set(current.keys()))
    for key in keys:
        if previous.get(key) != current.get(key):
            changed.append({"key": key, "previous": previous.get(key), "current": current.get(key)})
    return {"status": "CHANGED" if changed else "UNCHANGED", "changed": changed}


def build_checks(
    config: dict[str, Any],
    forward: dict[str, Any],
    forward_measured: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    enabled = [str(item).upper() for item in config.get("enabled_markets", [])]
    disabled = [str(item).upper() for item in config.get("disabled_markets", [])]
    env_overrides = config.get("env_overrides") if isinstance(config.get("env_overrides"), dict) else {}
    kr_fixed_order = _safe_int(_effective_config_value(config, env_overrides, "KR_FIXED_ORDER_KRW"), -1)
    us_fixed_order = _safe_int(_effective_config_value(config, env_overrides, "US_FIXED_ORDER_KRW"), -1)
    kr_min_order = _safe_int(_effective_config_value(config, env_overrides, "KR_MIN_ORDER_KRW"), -1)
    us_min_order = _safe_int(_effective_config_value(config, env_overrides, "US_MIN_ORDER_KRW"), -1)
    kr_max_positions = _safe_int(_effective_config_value(config, env_overrides, "KR_MAX_POSITIONS"), -1)
    us_max_positions = _safe_int(_effective_config_value(config, env_overrides, "US_MAX_POSITIONS"), -1)
    max_daily_entries = _safe_int(_effective_config_value(config, env_overrides, "V2_MAX_DAILY_ENTRIES"), -1)
    kr_daily_cap = _safe_int(_effective_config_value(config, env_overrides, "KR_DAILY_ENTRY_CAP"), max_daily_entries)
    us_daily_cap = _safe_int(_effective_config_value(config, env_overrides, "US_DAILY_ENTRY_CAP"), max_daily_entries)
    pathb_max_positions = _safe_int(_effective_config_value(config, env_overrides, "PATHB_MAX_POSITIONS"), -1)
    pathb_max_daily_entries = _safe_int(_effective_config_value(config, env_overrides, "PATHB_MAX_DAILY_ENTRIES"), -1)
    return [
        {"name": "kr_us_enabled", "ok": set(enabled) == {"KR", "US"} and not disabled},
        {"name": "kr_order_size_configured", "ok": kr_fixed_order > 0 and kr_min_order > 0 and kr_fixed_order >= kr_min_order},
        {"name": "us_order_size_configured", "ok": us_fixed_order > 0 and us_min_order > 0 and us_fixed_order >= us_min_order},
        {
            "name": "us_fx_dynamic_not_static_usd",
            "ok": "US_FIXED_ORDER_USD" not in config and "US_FIXED_ORDER_USD" not in env_overrides,
        },
        {"name": "kr_us_max_positions_configured", "ok": kr_max_positions >= 1 and us_max_positions >= 1},
        {"name": "daily_entry_limit_configured", "ok": max_daily_entries >= 1 or kr_daily_cap >= 1 or us_daily_cap >= 1},
        {"name": "pathb_limits_configured", "ok": pathb_max_positions >= 1 and pathb_max_daily_entries >= 1},
        {"name": "fresh_brain", "ok": str(config.get("brain_policy") or "") == "fresh_v2_reference_v1"},
        {"name": "same_close_research_only", "ok": str(config.get("same_close_policy") or "") == "research_only_disallowed_for_live"},
        {"name": "forward_queue_checked", "ok": "decision_count" in forward},
        {"name": "forward_measurement_checked", "ok": forward_measured is not None and "measured_count" in forward_measured},
    ]


def _effective_config_value(config: dict[str, Any], env_overrides: dict[str, Any], key: str) -> Any:
    if key in env_overrides:
        return env_overrides.get(key)
    return config.get(key)


def _decisions_for_session(
    store: EventStore,
    *,
    session_date: str,
    runtime_mode: str,
    markets: list[str],
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in markets)
    sql = (
        "SELECT * FROM v2_decisions "
        f"WHERE session_date=? AND runtime_mode=? AND market IN ({placeholders}) "
        "ORDER BY created_at, decision_id"
    )
    params: list[Any] = [session_date, runtime_mode, *markets]
    with store.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    raw_payload = data.pop("payload_json", "{}")
    try:
        data["payload"] = json.loads(raw_payload or "{}")
    except json.JSONDecodeError:
        data["payload"] = {}
    return data


def _forward_measurement_complete(events: list[dict[str, Any]]) -> bool:
    due: set[int] = set()
    measured: set[int] = set()
    for event in events:
        if str(event.get("event_type") or "") != "FORWARD_PENDING_DATA":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        due.update(_parse_horizon_values(payload.get("due_horizons") or []))
    for event in events:
        if str(event.get("event_type") or "") != "FORWARD_MEASURED":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if not due:
            due.update(_parse_horizon_values(payload.get("due_horizons") or []))
        measured.update(_parse_horizon_values(payload.get("all_measured_horizons") or payload.get("measured_horizons") or []))
    if not due:
        due = {1, 3, 5}
    return bool(measured) and due.issubset(measured)


def _parse_horizon_values(values: Any) -> set[int]:
    parsed: set[int] = set()
    if isinstance(values, (str, int)):
        values = [values]
    for item in values or []:
        text = str(item).strip().lower().removesuffix("d")
        try:
            parsed.add(int(text))
        except ValueError:
            continue
    return parsed


def _markets_from_args(market: str, config: dict[str, Any]) -> list[str]:
    market_value = str(market or "KR").upper()
    if market_value == "ALL":
        return [str(item).upper() for item in config.get("enabled_markets", ["KR"])]
    return [market_value]


def _latest_daily_loop_payload(output_dir: Path) -> dict[str, Any] | None:
    paths = sorted(output_dir.glob("v2_daily_loop_*.json"), reverse=True)
    for path in paths:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# V2 Daily Loop",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- session_date: {payload['session_date']}",
        f"- runtime_mode: {payload['runtime_mode']}",
        f"- markets: {', '.join(payload['markets'])}",
        f"- dry_run: {payload['dry_run']}",
        "",
        "## Checks",
        "",
    ]
    for item in payload.get("checks", []):
        lines.append(f"- {'PASS' if item.get('ok') else 'FAIL'} {item.get('name')}")
    forward = payload.get("forward_pending") or {}
    forward_measured = payload.get("forward_measured") or {}
    learning_sync = payload.get("learning_sync") or {}
    lines.extend(
        [
            "",
            "## Forward Pending",
            "",
            f"- decisions: {forward.get('decision_count', 0)}",
            f"- reserved: {forward.get('reserved_count', 0)}",
            f"- skipped: {forward.get('skipped_count', 0)}",
            "",
            "## Forward Measured",
            "",
            f"- decisions: {forward_measured.get('decision_count', 0)}",
            f"- measured: {forward_measured.get('measured_count', 0)}",
            f"- pending data: {forward_measured.get('pending_data_count', 0)}",
            f"- missing CSV: {forward_measured.get('missing_csv_count', 0)}",
            "",
            "## Learning Sync",
            "",
        ]
    )
    for mkt, sync in learning_sync.items():
        lines.append(f"### {mkt}")
        lines.append(f"- selected: {sync.get('selected', 0)}")
        lines.append(f"- written: {sync.get('written', 0)}")
        lines.append(f"- filled: {sync.get('filled', 0)}")
        lines.append(f"- closed: {sync.get('closed', 0)}")
        lines.append(f"- learning_allowed: {sync.get('learning_allowed', 0)}")
        lines.append(f"- forward_complete: {sync.get('forward_complete', 0)}")
        lines.append("")
    lines.extend(
        [
            "## Reports",
            "",
            f"- simulation: {(payload.get('simulation_report') or {}).get('markdown')}",
            f"- optimizer: {(payload.get('policy_optimization_report') or {}).get('markdown')}",
            "",
            "## Config Diff",
            "",
            f"- status: {(payload.get('config_diff_vs_previous_loop') or {}).get('status')}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the minimal V2 end-of-day loop without Claude or order calls.")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--runtime-mode", choices=["live", "paper"], default="live")
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="KR")
    parser.add_argument("--config", default=str(START_CONFIG_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-simulation", action="store_true")
    parser.add_argument("--skip-optimizer", action="store_true")
    args = parser.parse_args()

    payload = run_daily_loop(
        session_date=args.session_date,
        runtime_mode=args.runtime_mode,
        market=args.market,
        config_path=args.config,
        dry_run=args.dry_run,
        run_simulation=not args.skip_simulation,
        run_optimizer=not args.skip_optimizer,
    )
    print(f"json: {payload['paths']['json']}")
    print(f"markdown: {payload['paths']['markdown']}")
    return 0 if all(item.get("ok") for item in payload.get("checks", [])) else 1


if __name__ == "__main__":
    raise SystemExit(main())
