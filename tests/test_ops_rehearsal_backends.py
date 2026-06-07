from __future__ import annotations

import importlib
from pathlib import Path

from runtime.rehearsal.backends import FixtureBrokerBackend, install_rehearsal_backends, read_intents
from runtime.rehearsal.context import apply_direct_path_overrides, create_rehearsal_context
from runtime.rehearsal.fixtures import fixture_for_scenario


def test_fixture_place_order_records_marked_intent(tmp_path: Path) -> None:
    ctx = create_rehearsal_context(scenario="us_pathb_buy", runtime_root=tmp_path / "sandbox")
    fixture = fixture_for_scenario("us_pathb_buy")
    broker = FixtureBrokerBackend(ctx, fixture)
    result = broker.place_order("NVDA", 1, 123.45, "buy", market="US", path_type="claude_price")
    assert result["ok"] is True
    intents = read_intents(ctx.order_intents_path)
    assert len(intents) == 1
    intent = intents[0]
    assert intent["runtime_mode"] == "live"
    assert intent["runtime_context"] == "ops_rehearsal"
    assert intent["order_send"] is False
    assert intent["broker_call"] is False
    assert intent["claude_call"] is False
    assert intent["rehearsal"] is True
    assert intent["learning_excluded"] is True
    assert intent["do_not_learn"] is True


def test_install_rehearsal_backends_patches_original_and_consumer_globals(tmp_path: Path) -> None:
    ctx = create_rehearsal_context(scenario="us_pathb_buy", runtime_root=tmp_path / "sandbox")
    fixture = fixture_for_scenario("us_pathb_buy")
    importlib.import_module("kis_api")
    importlib.import_module("runtime.pathb_runtime")
    apply_direct_path_overrides(ctx)

    with install_rehearsal_backends(ctx, fixture):
        import kis_api
        import runtime.pathb_runtime as pathb_runtime

        assert kis_api.get_price("NVDA", market="US")["source"] == "fixture"
        assert pathb_runtime.get_price("NVDA", market="US")["source"] == "fixture"
        result = pathb_runtime.place_order("NVDA", 1, 123.45, "buy", market="US", path_type="claude_price")
        assert result["success"] is True

    intents = read_intents(ctx.order_intents_path)
    assert intents and intents[0]["ticker"] == "NVDA"
