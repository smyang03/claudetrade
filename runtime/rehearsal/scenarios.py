from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

from runtime.rehearsal.backends import install_rehearsal_backends, read_intents
from runtime.rehearsal.context import (
    RehearsalContext,
    RehearsalGuardError,
    apply_direct_path_overrides,
    assert_repo_live_state_unchanged,
    assert_runtime_objects_sandboxed,
    install_no_network_guard,
    install_write_guard,
    snapshot_repo_live_state,
)
from runtime.rehearsal.fixtures import RehearsalScenarioFixture, all_scenarios, fixture_for_scenario


def _ensure_live_argv() -> bool:
    if "--live" in sys.argv:
        return False
    sys.argv.append("--live")
    return True


def _import_runtime_modules() -> Any:
    for name in (
        "runtime_paths",
        "kis_api",
        "ticker_selection_db",
        "intraday_strategy_db",
        "strategy.param_tuner",
        "minority_report.raw_call_logger",
        "minority_report.analysts",
        "minority_report.hold_advisor",
        "minority_report.quick_exit_check",
        "minority_report.tuner",
        "minority_report.postmortem",
        "runtime.broker_truth_snapshot",
        "runtime.intraday_minute_cache",
        "runtime.pathb_runtime",
        "telegram_reporter",
        "telegram_commander",
    ):
        importlib.import_module(name)
    return importlib.import_module("trading_bot")


def _event(context: RehearsalContext, event_type: str, **payload: Any) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "rehearsal": True,
        "runtime_context": context.runtime_context,
        "runtime_mode": "live",
        "learning_excluded": True,
        "do_not_learn": True,
        "order_send": False,
        "broker_call": False,
        "claude_call": False,
        **payload,
    }


def _validate_intents(intents: list[dict[str, Any]]) -> None:
    for row in intents:
        if row.get("order_send") is not False:
            raise RehearsalGuardError("order intent contains order_send != false")
        if row.get("broker_call") is not False or row.get("claude_call") is not False:
            raise RehearsalGuardError("order intent contains broker_call/claude_call != false")
        for marker in ("rehearsal", "learning_excluded", "do_not_learn"):
            if row.get(marker) is not True:
                raise RehearsalGuardError(f"order intent missing marker: {marker}")


def _guard_summary(guard_state: dict[str, Any], *, sample_size: int = 25) -> dict[str, Any]:
    calls = list(guard_state.get("calls") or [])
    return {
        "call_count": len(calls),
        "calls_sample": calls[:sample_size],
    }


def _execute_scenario(
    context: RehearsalContext,
    fixture: RehearsalScenarioFixture,
    broker: Any,
    bot: Any,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    name = fixture.name

    if name == "kr_patha_buy":
        price = float(broker.get_price("005930", market="KR")["price"])
        pre = broker.precheck_order("005930", 1, price, "buy", market="KR")
        if pre.get("ok"):
            broker.place_order("005930", 1, price, "buy", market="KR")
            events.append(_event(context, "SAFETY_PASSED", market="KR", ticker="005930", side="buy"))
        else:
            events.append(_event(context, "ORDER_BLOCKED", market="KR", ticker="005930", reason=pre.get("reason")))

    elif name == "us_pathb_buy":
        price = float(broker.get_price("NVDA", market="US")["price"])
        pre = broker.precheck_order("NVDA", 1, price, "buy", market="US")
        if pre.get("ok"):
            broker.place_order("NVDA", 1, price, "buy", market="US", path_type="claude_price")
            events.append(_event(context, "PATHB_BUY_INTENT", market="US", ticker="NVDA", path_type="claude_price"))
        else:
            events.append(_event(context, "PATHB_BUY_BLOCKED", market="US", ticker="NVDA", reason=pre.get("reason")))

    elif name == "us_pathb_sell_target":
        price = float(broker.get_price("NVDA", market="US")["price"])
        broker.place_order("NVDA", 1, price, "sell", market="US", path_type="claude_price")
        events.append(
            _event(
                context,
                "PATHB_SELL_INTENT",
                market="US",
                ticker="NVDA",
                path_type="claude_price",
                close_reason="CLOSED_CLAUDE_PRICE_TARGET",
            )
        )

    elif name == "broker_truth_fail_closed":
        truth = broker.broker_truth_snapshot("US")
        if truth.get("ok"):
            raise RehearsalGuardError("broker_truth_fail_closed fixture unexpectedly returned ok")
        events.append(_event(context, "PATHB_BUY_BLOCKED", market="US", ticker="NVDA", reason="BLOCKED_BROKER_TRUTH"))

    elif name == "order_unknown_reconcile":
        events.append(_event(context, "PATHB_BUY_BLOCKED", market="US", ticker="NVDA", reason="ORDER_UNKNOWN_UNRESOLVED"))

    else:
        raise RehearsalGuardError(f"unknown rehearsal scenario: {name}")

    return events


def run_rehearsal_scenario(context: RehearsalContext, fixture: RehearsalScenarioFixture | None = None) -> dict[str, Any]:
    fixture = fixture or fixture_for_scenario(context.scenario)
    before = snapshot_repo_live_state()
    appended_live_arg = _ensure_live_argv()
    try:
        with install_no_network_guard(context) as network_guard:
            with install_write_guard(context) as write_guard:
                trading_bot = _import_runtime_modules()
                path_overrides = apply_direct_path_overrides(context)
                with install_rehearsal_backends(context, fixture) as backend_state:
                    for stale in (context.order_intents_path, context.report_path):
                        try:
                            if stale.exists():
                                stale.unlink()
                        except FileNotFoundError:
                            pass
                    bot = trading_bot.TradingBot(is_paper=False)
                    assert_runtime_objects_sandboxed(bot, context)
                    events = _execute_scenario(context, fixture, backend_state["broker"], bot)
                    intents = read_intents(context.order_intents_path)
                    _validate_intents(intents)
                    min_intents = int(fixture.expected.get("min_order_intents", 1))
                    if len(intents) < min_intents:
                        raise RehearsalGuardError(
                            f"scenario {fixture.name} produced {len(intents)} intents, expected at least {min_intents}"
                        )
                    summary = {
                        "ok": True,
                        "scenario": fixture.name,
                        "sandbox_root": str(context.sandbox_root),
                        "runtime_mode": "live",
                        "is_paper": bool(getattr(bot, "is_paper", True)),
                        "order_intents": intents,
                        "events": events,
                        "path_overrides": {k: str(v) for k, v in path_overrides.items()},
                        "write_guard": _guard_summary(write_guard),
                        "network_guard": _guard_summary(network_guard),
                        "network_guard_calls": list(network_guard.get("calls") or []),
                    }
                    context.report_path.parent.mkdir(parents=True, exist_ok=True)
                    context.report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        assert_repo_live_state_unchanged(before)
        return summary
    finally:
        if appended_live_arg:
            try:
                sys.argv.remove("--live")
            except ValueError:
                pass


def run_many(context_factory: Any, scenario_names: list[str] | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name in scenario_names or all_scenarios():
        context = context_factory(name)
        results.append(run_rehearsal_scenario(context, fixture_for_scenario(name)))
    return results


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
