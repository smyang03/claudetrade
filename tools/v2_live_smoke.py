from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _argv_value(name: str) -> str:
    for index, arg in enumerate(sys.argv):
        if arg == name and index + 1 < len(sys.argv):
            return str(sys.argv[index + 1] or "")
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
    return ""


def _load_runtime_env(runtime_mode: str = "live", env: str = "") -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    runtime_mode = str(runtime_mode or "live").lower()
    explicit_env = str(env or "")
    env_path = Path(explicit_env) if explicit_env else ROOT / (".env.live" if runtime_mode == "live" else ".env.paper")
    if not env_path.is_absolute():
        env_path = ROOT / env_path
    if not env_path.exists():
        env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)


from config.v2 import V2Config
from decision.registry import DecisionRegistry
from execution.safety_gate import SafetyContext, SafetyGate
from execution.sizing import FixedSizer
from learning.brain_snapshot import BrainSnapshotStore
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEventType
from bot.session_date import resolve_session_date_str
from runtime.risk_factory import create_risk_manager
from runtime.risk_profile import build_risk_profile


def run_live_smoke(
    *,
    market: str,
    runtime_mode: str = "live",
    root: str | Path | None = None,
    usd_krw: float | None = None,
    session_date: str | None = None,
    env: str | None = None,
) -> dict[str, Any]:
    if env is not None:
        _load_runtime_env(runtime_mode, env)
    market = str(market or "").upper()
    runtime_mode = str(runtime_mode or "").lower()
    session = str(session_date or "").strip() or resolve_session_date_str(market)
    root_path = Path(root) if root else ROOT
    config = V2Config.from_env()
    profile = build_risk_profile(market, runtime_mode, usd_krw=usd_krw, config=config)
    init_cash_krw = max(profile.fixed_order_krw * 10, 1_000_000.0)
    manager = create_risk_manager(profile, init_cash_krw=init_cash_krw)

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        store = EventStore(tmp_path / "v2_smoke.db")
        registry = DecisionRegistry(store)
        snapshot_store = BrainSnapshotStore(tmp_path / "brain_snapshots")
        snapshot = snapshot_store.create_snapshot(
            prompt_version=config.prompt_version,
            market=market,
            session_date=session,
            runtime_mode=runtime_mode,
            patterns=[],
        )

        ticker = "005930" if market == "KR" else "NVDA"
        price_krw = 25_000.0 if market == "KR" else 25.0 * profile.usd_krw
        sizing = FixedSizer(config).size(
            market=market,
            price_krw=price_krw,
            usd_krw=profile.usd_krw,
            cash_krw=manager.cash,
        )
        safety = SafetyGate(config).evaluate(
            SafetyContext(
                market=market,
                runtime_mode=runtime_mode,
                ticker=ticker,
                price_krw=price_krw,
                qty=sizing.qty,
                order_cost_krw=sizing.order_cost_krw,
                cash_krw=manager.cash,
                min_order_krw=profile.min_order_krw,
                max_daily_entries=2,
                market_open=True,
                broker_trust_level="trusted",
                last_market_data_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        if not safety.passed:
            return {
                "ok": False,
                "market": market,
                "runtime_mode": runtime_mode,
                "reason": safety.reason_code,
                "profile": profile.__dict__,
            }

        decision_id = registry.register_trade_ready(
            market=market,
            runtime_mode=runtime_mode,
            session_date=session,
            ticker=ticker,
            prompt_version=config.prompt_version,
            brain_snapshot_id=snapshot.brain_snapshot_id,
            strategy_hint="smoke",
            timing_style="momentum_timing",
            payload={"root": str(root_path), "smoke": True},
        )
        registry.record_event(
            event_type=LifecycleEventType.SAFETY_PASSED,
            market=market,
            runtime_mode=runtime_mode,
            session_date=session,
            ticker=ticker,
            decision_id=decision_id,
            prompt_version=config.prompt_version,
            brain_snapshot_id=snapshot.brain_snapshot_id,
            payload={"sizing": sizing.__dict__},
        )
        event_count = len(store.events_for_decision(decision_id))
        return {
            "ok": event_count == 2,
            "market": market,
            "runtime_mode": runtime_mode,
            "session_date": session,
            "decision_id": decision_id,
            "brain_snapshot_id": snapshot.brain_snapshot_id,
            "event_count": event_count,
            "profile": profile.__dict__,
            "sizing": sizing.__dict__,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V2 live smoke checks without broker or Claude calls.")
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="ALL")
    parser.add_argument("--runtime-mode", choices=["live", "paper"], default="live")
    parser.add_argument("--env", default="", help="Explicit env file path. Defaults to .env.live/.env.paper.")
    parser.add_argument("--session-date", default="", help="Override smoke session date. Defaults to market-aware current session date.")
    parser.add_argument("--usd-krw", type=float, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    _load_runtime_env(args.runtime_mode, args.env)

    markets = ["KR", "US"] if args.market == "ALL" else [args.market]
    results = [
        run_live_smoke(
            market=market,
            runtime_mode=args.runtime_mode,
            usd_krw=args.usd_krw,
            session_date=args.session_date or None,
            env=None,
        )
        for market in markets
    ]
    ok = all(item.get("ok") for item in results)
    if args.json:
        print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            status = "PASS" if item.get("ok") else "FAIL"
            print(f"{item.get('market')} {item.get('runtime_mode')} {item.get('session_date')} smoke: {status}")
            if not item.get("ok"):
                print(f"  reason: {item.get('reason')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
