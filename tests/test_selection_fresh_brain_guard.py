"""selection 프롬프트의 V2 fresh brain 가드 회귀 테스트.

시장판단 경로(_brain_context_for_judge)는 V2 fresh brain일 때 레거시 brain/
correction_guide를 주입하지 않는데, selection 경로(select_tickers)에만 그 가드가
빠져 stale correction_guide(예: 급락장 "MILD_BULL 차단" 지침)가 강세장에도 계속
주입되던 버그를 막는다. trading_bot._v2_fresh_brain_policy_enabled()와 정책이
일치하는지, 그리고 selection 프롬프트가 가드에 따라 brain 블록을 비우는지 검증한다.
"""
import os
from types import SimpleNamespace
from unittest.mock import patch

from minority_report import analysts


def _clear(monkeypatch):
    monkeypatch.delenv("V2_BRAIN_POLICY", raising=False)
    monkeypatch.delenv("V2_FRESH_BRAIN_START", raising=False)


def test_fresh_brain_selection_active_when_env_true(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("V2_FRESH_BRAIN_START", "true")
    assert analysts._v2_fresh_brain_selection_active() is True


def test_fresh_brain_selection_inactive_when_env_false(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("V2_FRESH_BRAIN_START", "false")
    assert analysts._v2_fresh_brain_selection_active() is False


def test_fresh_brain_selection_default_off(monkeypatch):
    _clear(monkeypatch)
    assert analysts._v2_fresh_brain_selection_active() is False


def test_fresh_brain_selection_active_via_policy(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("V2_BRAIN_POLICY", "fresh")
    # 명시적으로 START=false여도 policy가 우선해 활성
    monkeypatch.setenv("V2_FRESH_BRAIN_START", "false")
    assert analysts._v2_fresh_brain_selection_active() is True


def test_policy_matches_trading_bot_truthy_set(monkeypatch):
    # trading_bot._env_bool과 동일 truthy 집합을 사용하는지 (정책 일치 보장)
    _clear(monkeypatch)
    for truthy in ("1", "true", "yes", "y", "on", "TRUE", "On"):
        monkeypatch.setenv("V2_FRESH_BRAIN_START", truthy)
        assert analysts._v2_fresh_brain_selection_active() is True, truthy
    for falsy in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("V2_FRESH_BRAIN_START", falsy)
        assert analysts._v2_fresh_brain_selection_active() is False, falsy


# --- 통합: select_tickers 프롬프트에서 실제로 brain 블록이 빠지는지 ---

_BRAIN_MARK = "BRAINSUMMARY_MARKER_X"
_CORR_MARK = "CORRECTION_MARKER_X"


def _capture_selection_prompt(monkeypatch, fresh_brain: bool) -> str:
    prompts: list[str] = []

    def _fake_create(*, model, max_tokens, messages, **kwargs):
        prompts.append(messages[0]["content"])
        return SimpleNamespace(
            content=[SimpleNamespace(text='{"watchlist": [], "trade_ready": [], "reasons": {}}')],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    _clear(monkeypatch)
    monkeypatch.setenv("V2_FRESH_BRAIN_START", "true" if fresh_brain else "false")
    monkeypatch.setenv("ACTIVE_LESSONS_ENABLED", "false")
    monkeypatch.setenv("CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED", "false")
    with patch.object(analysts.client.messages, "create", side_effect=_fake_create), \
         patch.object(analysts, "_extract_json",
                      return_value={"watchlist": [], "trade_ready": [], "reasons": {}}), \
         patch.object(analysts, "credit_record", lambda *a, **k: None), \
         patch.object(analysts, "save_raw_call", lambda **k: None), \
         patch("claude_memory.brain.generate_prompt_summary", return_value=_BRAIN_MARK), \
         patch("claude_memory.brain.load",
               return_value={"correction_guide": {"US": {"note": _CORR_MARK}}}):
        analysts.select_tickers(
            market="US",
            digest_prompt="market digest",
            consensus_mode="NEUTRAL",
            candidates=[{"ticker": "AAPL", "price": 100.0, "volume": 1000, "change_rate": 1.0}],
            market_change_pct=0.0,
            secondary_change_pct=0.0,
        )
    return "\n".join(prompts)


def test_fresh_brain_omits_brain_and_correction_from_selection_prompt(monkeypatch):
    txt = _capture_selection_prompt(monkeypatch, fresh_brain=True)
    assert _CORR_MARK not in txt, "fresh brain인데 correction_guide가 selection 프롬프트에 샜다"
    assert _BRAIN_MARK not in txt, "fresh brain인데 brain summary가 selection 프롬프트에 샜다"


def test_legacy_mode_injects_brain_or_correction_into_selection_prompt(monkeypatch):
    txt = _capture_selection_prompt(monkeypatch, fresh_brain=False)
    assert (_CORR_MARK in txt) or (_BRAIN_MARK in txt), \
        "레거시 모드인데 brain/correction이 주입되지 않았다 (가드가 과하게 막음)"
