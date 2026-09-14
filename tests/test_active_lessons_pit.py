# -*- coding: utf-8 -*-
"""active_lessons — point-in-time 가드 (2026-09-14).

09-14 감사에서 잡은 잠재 함정: `generated_at`이 recency 점수에만 쓰이고 **컷오프로는
쓰이지 않았다.** 라이브에서는 `today == 분석일`이라 안 터지지만, 과거 재생 경로가 하나라도
생기면 **미래 교훈이 프롬프트에 들어간다**(TradingAgents #1300과 같은 모양).

우리는 같은 계열을 08-22·08-25·09-03 세 번 놓쳤고 **전부 사후 발견**이었다. 문서로는
못 막힌다는 게 실측이라 코드에 박았다.

가장 중요한 테스트 둘:
· `as_of`를 안 주면 **라이브 동작이 그대로**여야 한다(기본값이 오늘).
· 날짜를 못 읽는 교훈은 **통과**해야 한다 — 가드가 기능을 죽이면 더 나쁘다.
"""
from datetime import date, timedelta

import pytest

from minority_report import active_lessons as A


def _item(gen_at, *, source="lesson_candidates", text="교훈 본문 충분히 긴 텍스트"):
    return {"id": f"x_{gen_at}", "market": "KR", "source": source, "scope": "selection",
            "target_prompt_scope": "selection", "allowed_prompt_scopes": ["selection"],
            "text": text, "severity": "info", "confidence": 0.5, "sample_count": 10,
            "generated_at": gen_at}


D = date(2026, 5, 20)


@pytest.mark.parametrize("gen_at,future", [
    ("2026-05-19", False),                 # 과거 — 통과
    ("2026-05-20", False),                 # 같은 날 — 통과(그날 장 마감 후 생성분 포함)
    ("2026-05-21", True),                  # 하루 뒤 — 미래
    ("2026-12-31", True),                  # 한참 뒤 — 미래
    ("2026-05-21T09:30:00+09:00", True),   # 타임스탬프 형태도 앞 10자로 자른다
])
def test_future_detection(gen_at, future):
    assert A._is_future_item(_item(gen_at), D) is future


@pytest.mark.parametrize("bad", ["", None, "어제", "2026-13-99", "20260521"])
def test_unparseable_date_passes(bad):
    """날짜를 못 읽으면 통과시킨다 — 막으면 라이브에서 교훈이 조용히 사라진다."""
    assert A._is_future_item(_item(bad), D) is False


def test_date_field_fallback():
    """brain 항목은 generated_at 대신 date를 쓴다 — 둘 다 봐야 한다."""
    assert A._is_future_item({"date": "2026-05-21"}, D) is True
    assert A._is_future_item({"date": "2026-05-19"}, D) is False


def _wire(monkeypatch, items):
    """수집기 두 개를 고정 목록으로 대체한다.

    승인 게이트(2026-06-12 "운영자 승인 전 교훈은 프롬프트 주입 금지")도 꺼야 한다.
    그게 내 필터보다 **뒤에서** 전부 막아버려서, 안 끄면 이 테스트가 PIT 가드가 아니라
    승인 게이트를 재검증하게 된다.
    """
    from minority_report import lesson_approvals
    monkeypatch.setattr(lesson_approvals, "approval_required", lambda: False)
    monkeypatch.setattr(A, "_collect_lesson_candidate_items",
                        lambda market, today, ignored, *, prompt_scope: list(items))
    monkeypatch.setattr(A, "_collect_brain_items",
                        lambda market, today, ignored, *, prompt_scope: [])


def test_select_items_drops_future_when_as_of_given(monkeypatch):
    past, future = _item("2026-05-19", text="과거 교훈 본문"), _item("2026-05-21", text="미래 교훈 본문")
    _wire(monkeypatch, [past, future])
    selected, ignored = A._select_items("KR", 10, prompt_scope="selection", as_of=D)
    texts = [x["text"] for x in selected]
    assert "과거 교훈 본문" in texts
    assert "미래 교훈 본문" not in texts
    assert any(x["reason"] == "future_lesson" for x in ignored)


def test_select_items_default_is_today_unchanged(monkeypatch):
    """as_of 미지정 = 기존 동작. 오늘 이전 교훈은 전부 남는다."""
    old = _item((date.today() - timedelta(days=5)).isoformat(), text="오래된 교훈 본문")
    today_item = _item(date.today().isoformat(), text="오늘 교훈 본문")
    _wire(monkeypatch, [old, today_item])
    selected, ignored = A._select_items("KR", 10, prompt_scope="selection")
    texts = [x["text"] for x in selected]
    assert "오래된 교훈 본문" in texts and "오늘 교훈 본문" in texts
    assert not any(x["reason"] == "future_lesson" for x in ignored)


def test_tomorrow_lesson_dropped_even_on_live_default(monkeypatch):
    """기본값(오늘)에서도 내일 날짜 교훈은 막는다 — 시계 오차·오기입 방어."""
    tomorrow = _item((date.today() + timedelta(days=1)).isoformat(), text="내일 교훈 본문")
    _wire(monkeypatch, [tomorrow])
    selected, ignored = A._select_items("KR", 10, prompt_scope="selection")
    assert selected == []
    assert any(x["reason"] == "future_lesson" for x in ignored)


def test_build_context_accepts_as_of(monkeypatch):
    """공개 API가 as_of를 받아 _select_items까지 넘긴다."""
    seen = {}

    def fake(market, max_items, *, prompt_scope, as_of=None):
        seen["as_of"] = as_of
        return [], []

    monkeypatch.setattr(A, "_select_items", fake)
    monkeypatch.setenv("ACTIVE_LESSONS_ENABLED", "1")
    A.build_active_lesson_context("KR", as_of=D)
    assert seen["as_of"] == D


def test_build_context_default_as_of_is_none(monkeypatch):
    """안 넘기면 None이 가서 _select_items가 오늘로 해석한다 — 라이브 동작 불변."""
    seen = {}

    def fake(market, max_items, *, prompt_scope, as_of=None):
        seen["as_of"] = as_of
        return [], []

    monkeypatch.setattr(A, "_select_items", fake)
    monkeypatch.setenv("ACTIVE_LESSONS_ENABLED", "1")
    A.build_active_lesson_context("KR")
    assert seen["as_of"] is None
