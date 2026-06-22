"""postmortem 프롬프트의 V2 fresh brain 가드 회귀 테스트.

시장판단(_brain_context_for_judge)·selection(_v2_fresh_brain_selection_active)은
V2 fresh brain일 때 레거시 brain/correction_guide를 차단하는데, postmortem 경로에만
가드가 빠져 stale brain 요약(급락장 시점 correction_guide 시대 누적 요약)이 매 사후분석에
계속 주입되던 누락을 막는다. 정책은 단일 출처(analysts._v2_fresh_brain_selection_active)를
재사용한다(2026-06-23 무결성 감사 ③경로 가드 추가).
"""
from types import SimpleNamespace
from unittest.mock import patch

from minority_report import postmortem


def _clear(monkeypatch):
    monkeypatch.delenv("V2_BRAIN_POLICY", raising=False)
    monkeypatch.delenv("V2_FRESH_BRAIN_START", raising=False)


def test_helper_active_when_env_true(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("V2_FRESH_BRAIN_START", "true")
    assert postmortem._postmortem_fresh_brain_active() is True


def test_helper_inactive_by_default(monkeypatch):
    _clear(monkeypatch)
    assert postmortem._postmortem_fresh_brain_active() is False


def test_helper_active_via_policy(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("V2_BRAIN_POLICY", "fresh_v2_reference_v1")
    monkeypatch.setenv("V2_FRESH_BRAIN_START", "false")
    assert postmortem._postmortem_fresh_brain_active() is True


# --- 통합: run() 프롬프트에서 실제로 brain 요약이 빠지는지 ---

_BRAIN_MARK = "BRAINSUMMARY_MARKER_PM"


def _capture_postmortem_prompt(monkeypatch, fresh_brain: bool) -> str:
    prompts: list[str] = []

    def _fake_create(*, model, max_tokens, messages, **kwargs):
        prompts.append(messages[0]["content"])
        return SimpleNamespace(
            content=[SimpleNamespace(text='{"bull_result":"PARTIAL","bear_result":"PARTIAL",'
                                          '"neutral_result":"PARTIAL","bull_why":"x","bear_why":"x",'
                                          '"neutral_why":"x","key_lesson":"x","issue_type":"none",'
                                          '"issue_desc":"","best_trade":null,"worst_trade":null,'
                                          '"worst_trade_reason":"","market_regime":"unknown"}')],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    _clear(monkeypatch)
    monkeypatch.setenv("V2_FRESH_BRAIN_START", "true" if fresh_brain else "false")
    today_judgment = {
        "judgments": {"bull": {"stance": "BULL", "key_reason": "r"},
                      "bear": {"stance": "BEAR", "key_reason": "r"},
                      "neutral": {"stance": "NEUTRAL", "key_reason": "r"}},
        "consensus": {"mode": "NEUTRAL"},
    }
    with patch.object(postmortem.client.messages, "create", side_effect=_fake_create), \
         patch.object(postmortem.BrainDB, "generate_prompt_summary", return_value=_BRAIN_MARK), \
         patch.object(postmortem, "_recent_selection_feedback_section", return_value=""), \
         patch.object(postmortem, "_submit_postmortem_policy_candidate", lambda *a, **k: {}), \
         patch.object(postmortem, "_append_lesson_candidate", lambda *a, **k: None):
        postmortem.run(
            market="US", date="2026-06-23", today_judgment=today_judgment,
            actual_result={"market_change": 0.0, "pnl_pct": 0.0},
            digest_prompt="digest", trade_log=[], decision_event_log=[],
        )
    return "\n".join(prompts)


def test_fresh_brain_omits_brain_summary_from_postmortem_prompt(monkeypatch):
    txt = _capture_postmortem_prompt(monkeypatch, fresh_brain=True)
    assert txt, "프롬프트가 캡처되지 않았다"
    assert _BRAIN_MARK not in txt, "fresh brain인데 brain summary가 postmortem 프롬프트에 샜다"


def test_legacy_mode_injects_brain_summary_into_postmortem_prompt(monkeypatch):
    txt = _capture_postmortem_prompt(monkeypatch, fresh_brain=False)
    assert _BRAIN_MARK in txt, "레거시 모드인데 brain summary가 주입되지 않았다 (가드가 과하게 막음)"
