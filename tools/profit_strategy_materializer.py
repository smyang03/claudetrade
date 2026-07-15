from __future__ import annotations

"""Materialize point-in-time signals for the bounded profit-strategy handoff.

This process never imports the broker API and never submits an order.  It only
writes a session-scoped signal snapshot consumed by the live bot's separately
locked MICRO order bridge.
"""

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"
SECTOR_PAIRS = {
    "SOXX": "091160",
    "XLV": "227550",
    "XLF": "139220",
    "ITA": "309230",
    "LIT": "305720",
}
CORE_SOURCE_AUTHORITY = "SHADOW_ONLY_NO_ORDER_OR_LIVE_CONFIG_EFFECT"
CORE_LIVE_AUTHORITY = "MICRO_ENFORCE_OPERATOR_PROMOTED"
CORE_LIVE_ACK = "I_ACCEPT_LIVE_PROFIT_STRATEGIES"
CORE_CONTRACTS = {
    "US": {
        "strategy_id": "US_SCHG_BIL_TREND_V1",
        "assets": {"SCHG", "BIL"},
    },
    "KR": {
        "strategy_id": "KR_FACTOR_TREND_V1",
        "assets": {"275280.KS", "275300.KS"},
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _latest_core_shadow_path() -> Path | None:
    directory = ROOT / "data" / "shadow"
    rows = sorted(
        directory.glob("core_shadow_signal_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return rows[0] if rows else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def materialize_core_live_manifest(
    *,
    market: str,
    session_date: str,
    output_path: Path,
    source_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Promote one validated primary core arm into a session-scoped live artifact.

    The research artifact remains shadow-only.  This function is the explicit
    authority boundary: exact strategy/ticker contracts, source hash and the
    operator's MICRO switches must all agree before a live bridge may consume
    the resulting manifest.
    """

    market_key = str(market or "").upper()
    contract = CORE_CONTRACTS.get(market_key)
    source = source_path or _latest_core_shadow_path()
    source_payload = _read_json(source) if source else {}
    values = dict(os.environ if env is None else env)
    errors: list[str] = []
    signals: list[dict[str, Any]] = []

    if contract is None:
        errors.append("unsupported_market")
    if source is None or not source.exists():
        errors.append("core_shadow_source_missing")
    if source_payload.get("schema_version") != "core_shadow_targets_v1":
        errors.append("core_shadow_schema_mismatch")
    if source_payload.get("authority") != CORE_SOURCE_AUTHORITY:
        errors.append("core_shadow_authority_mismatch")
    if str(source_payload.get("effective_month") or "") != str(session_date)[:7]:
        errors.append("core_shadow_effective_month_mismatch")

    configured_ids = {
        item.strip().upper()
        for item in str(values.get("PROFIT_STRATEGY_ENABLED_IDS") or "").split(",")
        if item.strip()
    }
    operator_contract = {
        "authority_mode": str(values.get("PROFIT_STRATEGY_AUTHORITY_MODE") or "shadow").lower(),
        "handoff_enabled": _truthy(values.get("PROFIT_STRATEGY_ORDER_HANDOFF_ENABLED")),
        "submit_enabled": _truthy(values.get("PROFIT_STRATEGY_ORDER_SUBMIT_ENABLED")),
        "kill_switch": _truthy(values.get("PROFIT_STRATEGY_KILL_SWITCH")),
        "live_ack_verified": str(values.get("PROFIT_STRATEGY_ORDER_LIVE_ACK") or "") == CORE_LIVE_ACK,
        "enabled_ids": sorted(configured_ids),
    }
    if operator_contract["authority_mode"] != "micro":
        errors.append("operator_authority_not_micro")
    if not operator_contract["handoff_enabled"]:
        errors.append("operator_handoff_disabled")
    if not operator_contract["submit_enabled"]:
        errors.append("operator_submit_disabled")
    if operator_contract["kill_switch"]:
        errors.append("operator_kill_switch_active")
    if not operator_contract["live_ack_verified"]:
        errors.append("operator_live_ack_missing")
    if contract and contract["strategy_id"] not in configured_ids:
        errors.append("core_strategy_not_enabled")

    matching_arms = [
        dict(arm)
        for arm in source_payload.get("arms") or []
        if isinstance(arm, dict)
        and str(arm.get("market") or "").upper() == market_key
        and str(arm.get("role") or "").lower() == "primary"
        and contract
        and str(arm.get("strategy_id") or "").upper() == contract["strategy_id"]
    ]
    if len(matching_arms) != 1:
        errors.append("core_primary_arm_not_unique")
    elif contract:
        arm = matching_arms[0]
        weights = arm.get("weights") if isinstance(arm.get("weights"), dict) else {}
        total_weight = 0.0
        for asset, raw_weight in weights.items():
            asset_key = str(asset or "").upper()
            try:
                weight = float(raw_weight or 0.0)
            except Exception:
                weight = -1.0
            if asset_key not in contract["assets"]:
                errors.append(f"core_asset_not_allowed:{asset_key}")
                continue
            if not 0.0 < weight <= 1.0:
                errors.append(f"core_weight_invalid:{asset_key}")
                continue
            total_weight += weight
            signals.append({
                "strategy_id": contract["strategy_id"],
                "source_strategy": contract["strategy_id"].lower(),
                "market": market_key,
                "ticker": asset_key if market_key == "US" else asset_key.split(".", 1)[0],
                "signal_date": str(source_payload.get("signal_month") or ""),
                "entry_session_date": session_date,
                "known_at": str(source_payload.get("as_of") or ""),
                "rank": len(signals) + 1,
                "priority": weight,
                "hold_sessions": 9999,
                "weight": weight,
                "evidence": {
                    "effective_month": source_payload.get("effective_month"),
                    "target_asset": asset_key,
                    "core_policy": "monthly_target_rebalance",
                },
            })
        if total_weight > 1.000001:
            errors.append("core_total_weight_exceeds_one")
        if not signals:
            errors.append("core_positive_target_missing")

    source_hash = _sha256(source) if source and source.exists() else ""
    payload = {
        "schema_version": "profit_strategy_core_live_manifest_v1",
        "authority": CORE_LIVE_AUTHORITY if not errors else "NO_LIVE_AUTHORITY",
        "status": "healthy" if not errors else "blocked",
        "market": market_key,
        "session_date": session_date,
        "effective_month": str(source_payload.get("effective_month") or ""),
        "generated_at": _now(),
        "source_artifact": str(source.resolve()) if source else "",
        "source_sha256": source_hash,
        "source_schema_version": str(source_payload.get("schema_version") or ""),
        "source_authority": str(source_payload.get("authority") or ""),
        "operator_contract": operator_contract,
        "signals": signals if not errors else [],
        "errors": errors,
    }
    _atomic_json(output_path, payload)
    return payload


def consensus_signals(db_path: Path, *, session_date: str) -> list[dict[str, Any]]:
    """Return latest prior-session US signals whose two strategy labels agree."""

    if not db_path.exists():
        return []
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        latest = connection.execute(
            """
            SELECT MAX(date) FROM ticker_selection_log
            WHERE market='US' AND bot_mode='live' AND signal_fired=1 AND date<?
            """,
            (session_date,),
        ).fetchone()[0]
        if not latest:
            return []
        if (pd.Timestamp(session_date) - pd.Timestamp(str(latest))).days > 4:
            return []
        rows = connection.execute(
            """
            SELECT id,date,ticker,strategy_name,recommended_strategy,
                   selection_rank,entry_priority_score,change_pct
            FROM ticker_selection_log
            WHERE market='US' AND bot_mode='live' AND signal_fired=1 AND date=?
              AND TRIM(COALESCE(strategy_name,''))<>''
              AND LOWER(TRIM(strategy_name))=LOWER(TRIM(recommended_strategy))
            ORDER BY COALESCE(selection_rank,999999),
                     COALESCE(entry_priority_score,-999999) DESC,id DESC
            """,
            (latest,),
        ).fetchall()
    finally:
        connection.close()
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row["ticker"] or "").upper().strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        output.append({
            "strategy_id": "US_CONSENSUS_3D_V1",
            "source_strategy": "us_consensus_3d",
            "market": "US",
            "ticker": ticker,
            "signal_date": str(latest),
            "entry_session_date": session_date,
            "known_at": f"{latest}T23:59:59Z",
            "rank": int(row["selection_rank"] or len(output) + 1),
            "priority": float(row["entry_priority_score"] or 0.0),
            "hold_sessions": 3,
            "weight": 1.0,
            "evidence": {
                "selection_row_id": int(row["id"]),
                "strategy_name": str(row["strategy_name"] or ""),
                "recommended_strategy": str(row["recommended_strategy"] or ""),
                "change_pct": row["change_pct"],
            },
        })
    return output


def _download_sector_closes(symbol: str) -> pd.Series:
    import yfinance as yf

    frame = yf.download(symbol, period="10d", interval="1d", auto_adjust=True, progress=False, threads=False)
    if frame.empty:
        return pd.Series(dtype=float)
    close = frame["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = pd.to_numeric(close, errors="coerce").dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close[~close.index.duplicated(keep="last")].sort_index()


def sector_pulse_signals(
    *,
    session_date: str,
    close_loader: Callable[[str], pd.Series] = _download_sector_closes,
    threshold_pct: float = 2.0,
) -> list[dict[str, Any]]:
    """Select the strongest completed US sector pulse for the next KR session."""

    cutoff = pd.Timestamp(session_date)
    best: tuple[str, str, pd.Timestamp, float] | None = None
    for leader, target in SECTOR_PAIRS.items():
        closes = close_loader(leader)
        closes = closes[closes.index < cutoff]
        if len(closes) < 2:
            continue
        signal_date = pd.Timestamp(closes.index[-1])
        if (cutoff - signal_date).days > 4:
            continue
        move = (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1.0) * 100.0
        if move < float(threshold_pct):
            continue
        if best is None or move > best[3]:
            best = (leader, target, signal_date, move)
    if best is None:
        return []
    leader, target, signal_date, move = best
    return [{
        "strategy_id": "KR_US_SECTOR_PULSE_3D_V0",
        "source_strategy": "kr_us_sector_pulse_3d",
        "market": "KR",
        "ticker": target,
        "signal_date": str(signal_date.date()),
        "entry_session_date": session_date,
        "known_at": f"{signal_date.date()}T21:00:00Z",
        "rank": 1,
        "priority": float(move),
        "hold_sessions": 3,
        "weight": 1.0,
        "evidence": {
            "us_leader": leader,
            "leader_return_pct": round(float(move), 6),
            "threshold_pct": float(threshold_pct),
            "signal_provider": "yfinance_close_observation",
            "execution_price_provider": "KIS_ONLY",
        },
    }]


def materialize(*, market: str, session_date: str, output_path: Path) -> dict[str, Any]:
    market_key = str(market or "").upper()
    signals: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        if market_key == "US":
            signals.extend(consensus_signals(SELECTION_DB, session_date=session_date))
        elif market_key == "KR":
            signals.extend(sector_pulse_signals(session_date=session_date))
    except Exception as exc:
        errors.append(str(exc)[:500])
    payload = {
        "schema_version": "profit_strategy_signals_v1",
        "authority": "SIGNAL_ONLY_NO_BROKER_AUTHORITY",
        "market": market_key,
        "session_date": session_date,
        "generated_at": _now(),
        "signals": signals,
        "errors": errors,
        "status": "healthy" if not errors else "degraded",
    }
    _atomic_json(output_path, payload)
    return payload


def _load_live_operator_env() -> None:
    """Make direct/restart invocations obey the same two-source live config."""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env.live", override=True)
    except Exception:
        pass
    config_path = Path(os.getenv("V2_START_CONFIG_PATH", "config/v2_start_config.json"))
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    overrides = payload.get("env_overrides") if isinstance(payload, dict) else {}
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key:
                os.environ[str(key)] = str(value)


def materialize_current_core_manifests() -> dict[str, dict[str, Any]]:
    """Refresh both market manifests after a core tracker/restart rewrite."""
    from bot.session_date import KST, resolve_session_date_str

    now = datetime.now(KST)
    output: dict[str, dict[str, Any]] = {}
    for market in ("KR", "US"):
        session_date = resolve_session_date_str(market, now)
        output[market] = materialize_core_live_manifest(
            market=market,
            session_date=session_date,
            output_path=ROOT / "state" / f"profit_strategy_core_live_manifest_{market}.json",
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=("KR", "US"))
    parser.add_argument("--session-date", default=str(date.today()))
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--core-current-sessions",
        action="store_true",
        help="refresh validated KR/US live core manifests for their current session dates",
    )
    args = parser.parse_args()
    _load_live_operator_env()
    if args.core_current_sessions:
        manifests = materialize_current_core_manifests()
        print(json.dumps({"core_live_manifests": manifests}, ensure_ascii=False))
        return 0 if all(item.get("status") == "healthy" for item in manifests.values()) else 1
    if not args.market:
        parser.error("--market is required unless --core-current-sessions is used")
    output = Path(args.output) if args.output else ROOT / "state" / f"profit_strategy_signals_{args.market}.json"
    payload = materialize(market=args.market, session_date=args.session_date, output_path=output)
    manifest_path = ROOT / "state" / f"profit_strategy_core_live_manifest_{args.market}.json"
    manifest = materialize_core_live_manifest(
        market=args.market,
        session_date=args.session_date,
        output_path=manifest_path,
    )
    print(json.dumps({"signals": payload, "core_live_manifest": manifest}, ensure_ascii=False))
    # Challenger observations are deliberately non-authoritative.  Their
    # provider degradation stays visible in the signal payload but must not
    # mark the live core lane dead when its separately validated manifest is
    # healthy.  Only failure of the live authority artifact is process-fatal.
    return 0 if manifest["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
